# VEP #491: Authenticate virt-launcher command sockets

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version: deliberately unset, see [Beta promotion](#beta-promotion)
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

Today the resync loop only polls sockets listed in an on-disk ghost record cache that virt-handler wrote itself, after a successful connection. A
companion VEP (492-ghost-record-removal) replaces that cache with `VMI.Status.ActivePods`, so virt-handler will start dialing sockets it has never seen
before. Authentication is a prerequisite for that.

This VEP adds authentication using [projected ServiceAccount tokens](https://kubernetes.io/docs/concepts/storage/projected-volumes/#serviceaccounttoken) and
the [TokenReview API](https://kubernetes.io/docs/reference/kubernetes-api/authentication-resources/token-review-v1/). When virt-handler connects to a launcher
socket, the launcher presents a cryptographically verifiable token that virt-handler validates through the API server before trusting any data from it.

> **Upgrade requirement, read this first.** A virt-launcher that does not serve `CmdAuth` is rejected. There is no fallback, by design. virt-launcher pods
> are not recreated by a KubeVirt upgrade, so a VM running since before the upgrade keeps a launcher that cannot authenticate. Enabling this feature gate
> on a cluster with such VMs leaves them running but unmanaged by virt-handler until they are live-migrated or restarted. Check
> `KubeVirt.status.outdatedVirtualMachineInstanceWorkloads` before enabling it, and see
> [Backward compatibility](#backward-compatibility) for what this means when the gate goes on by default at beta.

## Motivation

The command channel has no notion of peer identity. virt-handler dials a Unix socket under the kubelet pods directory and treats whatever answers as the
virt-launcher for a given VMI. Domain state, shutdown and migration all follow from that peer's answers.

What limits the blast radius today is not a security check but an accident of discovery: resync only polls sockets already recorded on disk. Removing
that cache, which the companion VEP does for its own reliability reasons, removes the incidental filter with it.

`ActivePods` is a better source of truth than a file on disk, but it is not proof of what is listening: it says a pod with that UID belongs to the VMI,
not that the process on that pod's socket is a real virt-launcher. Authentication closes that gap by verifying the peer on every connection instead of
inferring trust from how the socket was found.

## Goals

- virt-handler authenticates every virt-launcher socket before trusting domain state from it, on every connection (domain `List`, resync, VM sync, migration).
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
- As a cluster admin, I need the handler-launcher channel to be authenticated so that removing the ghost record cache (to make launcher discovery independent
  of virt-handler's local disk state) does not widen the spoofing surface.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### Projected ServiceAccount tokens

Every virt-launcher pod receives a [projected ServiceAccount token](https://kubernetes.io/docs/concepts/storage/projected-volumes/#serviceaccounttoken) volume
with:
- **Audience:** `kubevirt.io/cmd-auth`, scoped to this use case, so the token cannot be replayed against the Kubernetes API or other services
- **Expiration:** 1 hour (auto-refreshed by the kubelet)
- **Path:** `/var/run/secrets/tokens/cmd-auth-token`

The volume and the `CmdAuth` gRPC service are shipped unconditionally, regardless of the feature gate. This ensures that after one upgrade cycle, every running
virt-launcher already has the authentication capability. The feature gate controls only whether virt-handler enforces authentication (see Feature gate below).

This is a standard Kubernetes projected volume and works regardless of the
[`AutomountServiceAccountToken`](https://kubernetes.io/docs/tasks/configure-pod-container/configure-service-account/#opt-out-of-api-credential-automounting)
setting (which is `false` by default for virt-launcher pods).

The volume requires a non-empty `serviceAccountName`, which virt-launcher pods do not set unless the VMI asks for a service account volume. Rather than
rely on the API server's admission defaulting for a volume it requires, the template sets `serviceAccountName: default` itself. This is user-visible in
the pod spec, but `AutomountServiceAccountToken` stays `false`, so no API credentials are mounted, and the token is audience-scoped, so it grants no API
permissions whatever is bound to that ServiceAccount.

### CmdAuth gRPC service

A new `CmdAuth` gRPC service is registered on the virt-launcher's existing gRPC server alongside `Cmd` and `CmdInfo`. It exposes a single `Authenticate` RPC
that reads the projected token from disk and returns it.

### Authentication flow

When virt-handler connects to a launcher socket (during resync, VM sync, or any other operation):

1. Dial the socket and call `CmdAuth.Authenticate`.
2. The virt-launcher returns its projected SA token.
3. virt-handler calls the [TokenReview API](https://kubernetes.io/docs/reference/kubernetes-api/authentication-resources/token-review-v1/) with the token and
   the expected audience `kubevirt.io/cmd-auth`.
4. The API server validates the token signature, expiration, and audience. virt-handler requires `status.authenticated` and `status.audiences` to contain
   `kubevirt.io/cmd-auth`. Passing `spec.audiences` already makes the API server reject a token issued for another audience, so the explicit check only
   guards against a future caller dropping that field.
5. virt-handler extracts
   [`authentication.kubernetes.io/pod-uid`](https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/#additional-metadata-in-pod-bound-tokens)
   from `status.user.extra` and verifies it matches the pod UID in the socket path. This ensures the token belongs to the same pod that owns the socket. Tokens
   without a pod UID are rejected.
6. virt-handler verifies that the pod UID appears in `VMI.Status.ActivePods` for the associated VMI. This confirms that virt-controller created this pod as a
   legitimate launcher for this specific VMI. The VMI is identified from the ghost record (today) or from the VMI store via `ActivePods` (after ghost record
   removal, [VEP-492](../492-ghost-record-removal/vep.md)).
7. If any check fails, virt-handler rejects the socket and skips all further RPCs on it.

Step 6 fails closed: if virt-handler cannot resolve the owning VMI, the socket is rejected. A valid token only proves some pod minted it.

That makes the check depend on a populated VMI store. `factory.Start` and `domainSharedInformer.Run` run concurrently today, which is safe only because
ghost records come from disk. With the gate on, an early `List` would reject every socket and sync an empty domain cache, so virt-handler waits for the
VMI informer before starting the domain informer. [VEP-492](../492-ghost-record-removal/vep.md) needs the same ordering, but the requirement starts here.

When the feature gate is enabled, authentication runs before any `GetDomain` call on:

- the domain informer's initial `List`
- the domain-watcher resync loop
- the VM controller's `GetLauncherClient`

`handleResync` only emits `Modified` for sockets that authenticate and return a domain. It does not remove a domain that `List` already inserted, so `List`
has to authenticate too. A ghost record whose socket file is already gone does not dial. It still returns a deletion-marked domain, because there is no peer
to authenticate. [VEP-492](../492-ghost-record-removal/vep.md) replaces ghost-record enumeration with `ActivePods`, and that `List` uses the same check.

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
`VMI.Status.ActivePods`. virt-controller builds that map from pods in the VMI's namespace that carry a controller `ownerReference` to the VMI, so a pod
created by an unrelated tenant can never appear in another tenant's VMI.

This also naturally handles live migration: both the source and target launcher pods appear in `VMI.Status.ActivePods`, so both pass the check.

**Residual case.** Since `ActivePods` comes from owner references, a user can point a pod of their own at their own VMI and pass all three checks. Owner
references are namespace-scoped, so this is confined to VMIs the attacker already owns and can delete outright. It is not a cross-tenant vector, which is
what this VEP defends against. Requiring virt-controller's labels on top would add no boundary, since those are attacker-settable too.

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

The authentication **capability** (the `CmdAuth` gRPC service on virt-launcher and the projected token volume on launcher pods) is shipped unconditionally,
so that every launcher pod created from the alpha release onwards is ready for enforcement without any cluster configuration.

Splitting capability from enforcement this way is the core of the design. It is what lets the gate be turned on later without a compatibility shim: by the
time anyone enables enforcement, the launchers it will talk to already speak `CmdAuth`. The one thing that must not happen is enforcement growing an
escape hatch for peers that do not, which is covered under Backward compatibility.

### RBAC

virt-handler's ClusterRole gains one additional rule:

```yaml
- apiGroups: ["authentication.k8s.io"]
  resources: ["tokenreviews"]
  verbs: ["create"]
```

### Backward compatibility

The `CmdAuth` service and the projected token volume are ungated, so every launcher pod created from this release onwards has both.

**Pre-existing launchers do not.** virt-launcher pods are not a DaemonSet and are not recreated by an upgrade. A VMI running before the upgrade keeps its
old pod, with the old binary and no token volume, until it is restarted or live-migrated. Automated workload updates are opt-in
(`spec.workloadUpdateStrategy.workloadUpdateMethods` defaults to an empty list), so outdated launchers can persist indefinitely. KubeVirt already tracks
them through the `kubevirt.io/outdatedLauncherImage` label, `KubeVirt.status.outdatedVirtualMachineInstanceWorkloads`, and the
`kubevirt_vmi_number_of_outdated` metric. **That count reaching zero is the precondition for enabling the gate.**

#### No fallback for launchers without `CmdAuth`

Such a launcher answers `Authenticate` with gRPC `Unimplemented`, which is a hard rejection like any other failure.

An authentication check the peer can decline by not implementing it is not a check: any pod could reach the fallback by never registering `CmdAuth`,
which is the impersonation ghost record removal would otherwise open up. Falling back to `ActivePods` membership would not help either, since a pod a
user created in their own namespace can satisfy it. The token is what separates a real virt-launcher from a pod that merely belongs to the VMI, so there
is nothing weaker to fall back to. Shipping the capability ungated, one release ahead of enforcement, is what makes a fallback unnecessary.

#### Enabling the gate too early

Each VMI still on an outdated launcher has its socket rejected, so no domain in the cache and `GetLauncherClient` failing on every sync. The VMI stays
`Running` rather than Failed, because `calculateVmPhaseForStatusReason` needs `IsLauncherClientUnresponsive`, which reads `FindSocket` and still finds a
live socket, and the authentication error is returned unwrapped so it is not an `IrrecoverableError`. The guest is untouched.

The VM therefore keeps running but goes unmanaged: no state reporting, no graceful shutdown, no migration. Live-migrating or restarting it fixes this.
Fail-closed and recoverable by a normal operation is the right trade for a security control.

#### Beta promotion

Per [VEP-229](../../meta-VEPs/229-beta-features-on-by-default/vep.md), beta gates are on by default from v1.9 and that default is not configurable, so
promoting this gate enables enforcement everywhere that upgrades. There is no opt-in beta. The plan is timing and communication, not a code path:

1. **Stay alpha for several releases.** The beta target is deliberately unset above. Each release shrinks the population of pre-`CmdAuth` launchers
   through normal VM churn.
2. **Announce before the flip.** Mailing list and community meeting announcements that upgrading to version X requires all running VMs on a launcher
   from version Y or later, with the metric above as the check.
3. **Document the escape hatch.** An explicit `spec.configuration.developerConfiguration.disabledFeatureGates` entry beats the beta default (`IsEnabled`
   precedence is GA, explicit enable, explicit disable, then beta). An admin caught out can disable the gate, roll the VMs, and re-enable.

HCO-based distributions explicitly disable on-by-default non-GA gates, so the exposure is non-HCO deployments. The residual risk is accepted knowingly:
a cluster that runs a VM across several releases, never enables workload updates, and ignores the announcements will find it unmanaged. The alternative
is a bypassable feature.

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

Each virt-launcher gets a short-lived certificate, and virt-handler validates the certificate chain.

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

**Upgrade:** `CmdAuth` and the token volume are added to all **newly created** launcher pods. The gate is off by default, so the upgrade changes no
behaviour for running VMs. Upgrading alone does not make the gate safe to enable, since already-running VMIs keep their old pods: wait for
`KubeVirt.status.outdatedVirtualMachineInstanceWorkloads` to reach zero.

**Rollback:** Disabling the feature gate or rolling back KubeVirt stops authentication checks. The projected volume on already-running pods is harmless (an
unused file). The `CmdAuth` service on the launcher is inert when no client calls it.

## Functional Testing Approach

### Unit

`AuthenticateSocket`:
- Succeeds for an authenticated, pod-bound token whose UID is in `ActivePods`.
- Rejects an unauthenticated token, an `Authenticate` RPC error, a pod UID that does not match the socket path, a token with no pod UID, and
  `status.audiences` without `kubevirt.io/cmd-auth`.
- Rejects a pod UID absent from `ActivePods`, and fails closed when `ActivePods` is nil or empty.
- Rejects gRPC `Unimplemented` even when the pod UID is in `ActivePods`: no peer without `CmdAuth` is ever accepted.
- Is a no-op when the k8s client is nil (gate off).

Other:
- virt-handler waits for the VMI informer to sync before starting the domain informer when the gate is enabled.
- Domain `List` omits a socket that fails authentication, and still reports a deletion-marked domain when the socket file is already gone.
- `AuthServer.Authenticate` returns the token from the projected volume path.
- Virt-launcher pod template always includes the projected SA token volume and mount (unconditional, not gated).
- Virt-launcher pod template sets `serviceAccountName` to `default` when the VMI names none, and leaves `AutomountServiceAccountToken` false.

### Functional / e2e

- A running VMI's launcher is authenticated on every virt-handler connection (domain `List`, resync, and VM sync).
- A pod that creates a socket at the expected path and does not serve `CmdAuth` (or serves a fake token) is rejected.
- virt-handler restart with the feature gate enabled correctly resyncs all legitimate VMIs.
- Upgrade with a VMI running from the previous release, then enable the gate. The VMI stays `Running` and its guest is unaffected, its socket is rejected,
  and it is not marked Failed. Live-migrating it onto a current launcher restores management.
- `KubeVirt.status.outdatedVirtualMachineInstanceWorkloads` reaches zero after all VMIs are migrated or restarted post-upgrade, and the gate enabled at
  that point causes no disruption.

## Implementation History

N/A

## Graduation Requirements

### Alpha

- [ ] `CmdAuth` gRPC service registered on virt-launcher (unconditional)
- [ ] Projected SA token volume added to virt-launcher pod template (unconditional)
- [ ] `LauncherSocketAuthentication` feature gate controls virt-handler enforcement only
- [ ] virt-handler authenticates sockets on every connection path when gate is enabled (domain `List`, domain-watcher resync, and VM controller sync)
- [ ] TokenReview RBAC added to virt-handler ClusterRole
- [ ] A socket that does not serve `CmdAuth` is rejected, with no fallback path
- [ ] Unit tests pass
- [ ] Functional tests pass

### Beta

- [ ] Token verification result caching for performance
- [ ] User guide documents the feature gate, its security properties, and the zero-outdated-workloads precondition for enabling it
- [ ] Enough releases have passed since alpha that launchers predating `CmdAuth` are rare (see Backward compatibility)
- [ ] Mailing list and community meeting announcements sent ahead of the beta release, stating the minimum launcher version required to upgrade
- [ ] Release notes for the beta release carry the same requirement and the `disabledFeatureGates` escape hatch

#### On-By-Default Readiness

- No reports of authentication failures against legitimate virt-launchers in alpha
- TokenReview latency measured and documented for large node counts
- Confirmed that a cluster with outdated launchers degrades as documented and recovers by migration or restart
- Confirmed that an explicit `disabledFeatureGates` entry overrides beta-on-by-default

### GA

- [ ] No outstanding functional, security, or test gaps
- [ ] Feature has been on-by-default for at least one minor release with no flakes or regressions
- [ ] Ghost record removal (companion VEP) shipped and stable under the same gate
