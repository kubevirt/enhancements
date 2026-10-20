# VEP #491: Authenticate virt-launcher command sockets

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

The handler-launcher command channel is an unauthenticated gRPC connection over a Unix domain socket. virt-handler trusts whatever process responds on a
launcher socket without verifying its identity.

Today, virt-handler limits which sockets it talks to by consulting an on-disk ghost record cache. A companion VEP (492-ghost-record-removal) proposes removing
that cache and discovering launchers dynamically through the Kubernetes pod informer. Once ghost records are gone, any pod on the node that looks like a
virt-launcher could be discovered and trusted. Authentication of the command channel is therefore a prerequisite for safe ghost record removal.

This VEP adds authentication using [projected ServiceAccount tokens](https://kubernetes.io/docs/concepts/storage/projected-volumes/#serviceaccounttoken) and
the [TokenReview API](https://kubernetes.io/docs/reference/kubernetes-api/authentication-resources/token-review-v1/). When virt-handler connects to a launcher
socket, the launcher presents a cryptographically verifiable token that virt-handler validates through the API server before trusting any data from it.

## Motivation

virt-handler relies on an on-disk ghost record cache to know which virt-launcher sockets to connect to. When virt-handler restarts (e.g. during an upgrade), a
launcher that started while virt-handler was down is not in the cache. virt-handler does not see it, reports the VMI as Failed, and kills it. Multiple bugs
trace back to this, where running VMs are killed during KubeVirt upgrades because the ghost record cache is stale.

Removing the ghost record cache requires a new, dynamic discovery mechanism (see the companion VEP). Dynamic discovery means virt-handler will encounter sockets
it has never seen before. Without authentication, a malicious pod with the right labels and volume layout could be discovered and its fake domain data would be
trusted, letting it shut down real VMs.

Authentication closes this gap: regardless of how virt-handler discovers a socket, it verifies the launcher's identity before trusting any data from it.

## Goals

- virt-handler authenticates every virt-launcher socket before trusting domain state from it, on every connection (resync, VM sync, migration).
- A pod that is not a legitimate virt-launcher is detected and rejected. virt-handler never calls `GetDomain` or any other RPC on an unauthenticated socket.
- The mechanism works with user-configurable [ServiceAccounts](https://kubernetes.io/docs/concepts/security/service-accounts/) (virt-launcher pods may use
  different SAs across namespaces).
- The mechanism is generic enough to be reused for other handler-launcher channels (notify pipe, future services).

## Non Goals

- **Removing the ghost record cache.** That is the subject of the companion VEP (492-ghost-record-removal). This VEP only adds the authentication layer that
  makes removal safe.
- **mTLS for the command channel.** mTLS would provide encryption and mutual authentication but requires certificate management. Token-based authentication
  solves the impersonation problem without that overhead. mTLS can be layered on later.
- **Authenticating the notify pipe.** Deferred to a follow-up. The notify pipe has the same trust problem but a different communication pattern (event push, not
  request-response).
- **Encrypting the command channel.** The channel is node-local over Unix sockets. The threat is impersonation, not eavesdropping.

## Definition of Users

- **VM owners** whose VMs must not be disrupted by malicious or misconfigured pods on the same node.
- **Cluster administrators** who run multi-tenant clusters and need assurance that the handler-launcher channel is tamper-proof.

## User Stories

- As a VM owner, I need KubeVirt to never shut down my VM because a malicious pod on the same node spoofed launcher responses.
- As a cluster admin, I need the handler-launcher channel to be authenticated so that removing the ghost record cache (to fix the upgrade reliability bugs) does
  not introduce a spoofing vector.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### Projected ServiceAccount tokens

Every virt-launcher pod receives a [projected ServiceAccount token](https://kubernetes.io/docs/concepts/storage/projected-volumes/#serviceaccounttoken) volume
with:
- **Audience:** `kubevirt.io/cmd-auth` (scoped to this use case; the token cannot be replayed against the Kubernetes API or other services)
- **Expiration:** 1 hour (auto-refreshed by the kubelet)
- **Path:** `/var/run/secrets/tokens/cmd-auth-token`

The volume and the `CmdAuth` gRPC service are shipped unconditionally, regardless of the feature gate. This ensures that after one upgrade cycle, every running
virt-launcher already has the authentication capability. The feature gate controls only whether virt-handler enforces authentication (see Feature gate below).

This is a standard Kubernetes projected volume and works regardless of the
[`AutomountServiceAccountToken`](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/#opt-out-of-api-credential-automounting)
setting (which is `false` by default for virt-launcher pods).

### CmdAuth gRPC service

A new `CmdAuth` gRPC service is registered on the virt-launcher's existing gRPC server alongside `Cmd` and `CmdInfo`. It exposes a single `Authenticate` RPC
that reads the projected token from disk and returns it.

### Authentication flow

When virt-handler connects to a launcher socket (during resync, VM sync, or any other operation):

1. Dial the socket and call `CmdAuth.Authenticate`.
2. The virt-launcher returns its projected SA token.
3. virt-handler calls the [TokenReview API](https://kubernetes.io/docs/reference/kubernetes-api/authentication-resources/token-review-v1/) with the token and
   the expected audience `kubevirt.io/cmd-auth`.
4. The API server validates the token signature, expiration, and audience. The response includes `status.authenticated`, plus the pod's ServiceAccount,
   namespace, name, and UID in
   [`status.user`](https://kubernetes.io/docs/reference/kubernetes-api/authentication-resources/token-review-v1/#TokenReviewStatus).
5. virt-handler extracts
   [`authentication.kubernetes.io/pod-uid`](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/#additional-metadata-in-pod-bound-tokens)
   from `status.user.extra` and verifies it matches the pod UID in the socket path. This ensures the token belongs to the same pod that owns the socket. Tokens
   without a pod UID are rejected.
6. virt-handler verifies that the pod UID appears in `VMI.Status.ActivePods` for the associated VMI. This confirms that virt-controller created this pod as a
   legitimate launcher for this specific VMI. The VMI is identified from the ghost record (today) or the pod's labels (after ghost record removal).
7. If any check fails, virt-handler rejects the socket and skips all further RPCs on it.

Authentication is enforced on both connection paths: the domain-watcher resync loop and the VM controller's `GetLauncherClient`.

### Why a fake pod fails

A malicious pod that places a socket at the expected path will fail authentication at multiple levels:

**Pod UID binding.** Any pod can project its own SA token with the `kubevirt.io/cmd-auth` audience, and that token will pass the `status.authenticated` check.
However, the TokenReview response includes
[`authentication.kubernetes.io/pod-uid`](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/#additional-metadata-in-pod-bound-tokens)
in `status.user.extra`, which is the UID of the pod that owns the token. virt-handler compares this to the pod UID in the socket path
(`/pods/<podUID>/volumes/.../launcher-sock`). A pod impersonating a different launcher fails here because its token carries a different UID. Tokens without a
pod UID are also rejected.

**ActivePods check.** Even a pod at its own legitimate path, using the same ServiceAccount as virt-launcher, will fail the ActivePods check. In Kubernetes, any
pod in a namespace can use any ServiceAccount in that namespace, so the SA name alone is not sufficient. virt-handler verifies that the pod UID appears in
`VMI.Status.ActivePods`, which is populated only by virt-controller when it creates a launcher pod. A rogue pod's UID will never appear in any VMI's ActivePods
map.

This also naturally handles live migration: both the source and target launcher pods appear in `VMI.Status.ActivePods`, so both pass the check.

### Why projected SA tokens over shared secrets

| Property | Shared HMAC secret | Projected SA token |
| --- | --- | --- |
| Per-pod identity | No (group membership only) | Yes (SA, namespace, pod name, UID) |
| Compromise blast radius | Total (one leak compromises all) | Single pod (token expires) |
| Secret management | Manual creation, rotation, RBAC | Kubernetes handles lifecycle |
| Additional infrastructure | New Secret resource | None (kubelet feature) |
| Revocation | Requires secret rotation | Automatic on pod deletion |

### Feature gate

The `LauncherSocketAuthentication` feature gate (Alpha, off by default) controls only virt-handler's enforcement: whether it calls `AuthenticateSocket` and
rejects unauthenticated sockets. This gate also covers the companion VEP (492-ghost-record-removal): authentication and ghost record removal are shipped under
a single gate because there is no useful intermediate state.

The authentication **capability** (the `CmdAuth` gRPC service on virt-launcher and the projected token volume on launcher pods) is shipped unconditionally so
that after one upgrade cycle all launchers are ready for enforcement. This eliminates the need for graceful degradation when the gate is promoted to
on-by-default at beta: by that point, no launcher in the cluster lacks `CmdAuth` or the token.

### RBAC

virt-handler's ClusterRole gains one additional rule:

```yaml
- apiGroups: ["authentication.k8s.io"]
  resources: ["tokenreviews"]
  verbs: ["create"]
```

### Backward compatibility

The `CmdAuth` service and the projected token volume are shipped unconditionally (not gated). During a rolling update to the version that introduces this
feature, old virt-launcher binaries (still running from the previous release) do not have `CmdAuth` or the token. This is safe because:

1. The feature gate is off by default in alpha. virt-handler does not attempt authentication.
2. All virt-launcher pods are recreated during the DaemonSet rollout or through live migration, so by the time the upgrade completes, every launcher has the new
   binary and the token volume.

When the gate is promoted to on-by-default at beta (the next release), every launcher in the cluster already has the capability. No graceful degradation is
needed. virt-handler can hard-fail any socket that does not authenticate.

## Alternatives

### Shared HMAC secret (challenge-response)

A cluster-wide Kubernetes Secret injected into virt-launchers as an environment variable. virt-handler sends a random challenge, the launcher computes
HMAC-SHA256 with the shared secret, virt-handler verifies.

**Not chosen.** Only proves group membership, not per-pod identity. A single leaked secret compromises every launcher on every node. See the comparison table in
Design.

### `SO_PEERCRED` (Unix socket peer credentials)

`getsockopt(SO_PEERCRED)` returns the peer's PID, UID, and GID. From the PID, the cgroup can be inspected to determine which pod owns the process.

**Not chosen.** Linux-specific, fragile (depends on cgroup layout), and does not provide cryptographic proof of identity. Does not generalize to non-Unix
transports if the channel ever moves to vsock or TCP.

### mTLS with per-pod certificates

Each virt-launcher gets a short-lived certificate; virt-handler validates the certificate chain.

**Deferred.** Introduces significant complexity (certificate issuance, rotation, trust-root distribution). Projected SA tokens achieve the same authentication
goal with zero additional infrastructure.

### Validating webhook blocking non-KubeVirt virt-launchers

A cluster-wide [validating webhook](https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/) that intercepts all pod
creations and rejects pods named `virt-launcher-*` unless created by virt-controller.

**Not chosen.** The webhook would intercept every pod creation in the cluster, adding latency to all workloads (webhooks are synchronous in the API server
request path). With `failurePolicy: Fail`, a webhook outage blocks all pod creation cluster-wide. With `Ignore`, the protection disappears when the cluster is
under stress. More fundamentally, the exploit vector is socket-path-based, not name-based: a pod with hostPath access to the socket directory can spoof a
launcher without being named `virt-launcher-*`. Finally, the webhook does not authenticate the runtime communication channel, so a compromised process inside a
legitimate launcher pod would bypass it entirely.

## Does it belong to core KubeVirt?

Yes. This secures the internal handler-launcher communication path, which is a core architectural boundary:
- The gRPC server runs inside virt-launcher (core binary).
- The client runs inside virt-handler (core binary).
- The projected volume must be added to the pod template in virt-controller (core code path).

## Scalability

| Interaction | Component | When | Cost |
| --- | --- | --- | --- |
| `CmdAuth.Authenticate` RPC | virt-handler to virt-launcher | Socket connection | ~1ms (local Unix socket) |
| `TokenReview` API call | virt-handler to API server | Per socket, per connection | ~1-5ms per call |

On virt-handler restart with N running VMs, there are N TokenReview calls. For clusters with hundreds of VMs per node, this adds a few hundred milliseconds to
the startup cycle. Verified identities can be cached (keyed by pod UID) and only re-verified when a new socket is encountered or a UID changes.

No new list/watch, no new CRDs, no new controllers.

## Update/Rollback Compatibility

**Upgrade:** Upgrading KubeVirt unconditionally adds the `CmdAuth` service and the projected token volume to all new virt-launcher pods. The feature gate (off
by default) controls only whether virt-handler enforces authentication. After the upgrade completes, every launcher has the capability. The gate can be enabled
at any time after that, or left for the next release when it becomes on-by-default.

**Rollback:** Disabling the feature gate or rolling back KubeVirt stops authentication checks. The projected volume on already-running pods is harmless (an
unused file). The `CmdAuth` service on the launcher is inert when no client calls it.

## Functional Testing Approach

### Unit

- `AuthenticateSocket` succeeds when TokenReview returns authenticated.
- `AuthenticateSocket` fails when TokenReview returns not authenticated.
- `AuthenticateSocket` fails when the `Authenticate` RPC returns an error.
- `AuthenticateSocket` is a no-op when the k8s client is nil (feature gate off).
- `AuthServer.Authenticate` returns the token from the projected volume path.
- Virt-launcher pod template always includes the projected SA token volume and mount (unconditional, not gated).

### Functional / e2e

- A running VMI's launcher is authenticated on every virt-handler connection (resync and VM sync).
- A pod that creates a socket at the expected path and does not serve `CmdAuth` (or serves a fake token) is rejected.
- virt-handler restart with the feature gate enabled correctly resyncs all legitimate VMIs.

## Implementation History

N/A

## Graduation Requirements

### Alpha

- [ ] `CmdAuth` gRPC service registered on virt-launcher (unconditional)
- [ ] Projected SA token volume added to virt-launcher pod template (unconditional)
- [ ] `LauncherSocketAuthentication` feature gate controls virt-handler enforcement only
- [ ] virt-handler authenticates sockets on every connection path when gate is enabled (domain-watcher resync and VM controller sync)
- [ ] TokenReview RBAC added to virt-handler ClusterRole
- [ ] Unit tests pass
- [ ] Functional tests pass

### Beta

- [ ] Token verification result caching for performance
- [ ] User guide documents the feature gate and its security properties

#### On-By-Default Readiness

- No reports of authentication failures against legitimate virt-launchers in alpha
- TokenReview latency measured and documented for large node counts
- No graceful degradation needed: all launchers have `CmdAuth` and the token volume since the alpha release

### GA

- [ ] No outstanding functional, security, or test gaps
- [ ] Feature has been on-by-default for at least one minor release with no flakes or regressions
- [ ] Ghost record removal (companion VEP) shipped and stable under the same gate
