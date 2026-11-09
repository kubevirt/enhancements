# VEP #492: Replace ghost record cache with VMI-based launcher discovery

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version: must match [VEP-491](../491-launcher-socket-authentication/vep.md), which shares the feature gate
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

virt-handler discovers virt-launcher pods on its node by reading an on-disk ghost record cache. This cache is persisted as checkpoint files and is the
primary mechanism the domain informer uses to find launcher sockets after a restart. When the cache is stale (a launcher started while virt-handler was down),
`handleResync` does not see that launcher, so the domain informer has no entry for it until VMI sync reconnects through `ActivePods`. A missing ghost record
does not by itself mark the VMI Failed. The VMI goes Failed only when its socket is gone or marked unresponsive. The cache is still a second, disk-backed
source of truth that can miss launchers or point at a stale socket.

This VEP replaces the ghost record cache with discovery through virt-handler's existing VMI informers. Each VMI's `Status.ActivePods` already contains the
pod UIDs of all launcher pods for that VMI, and virt-handler already derives socket paths from these UIDs in its launcher client code. The ghost record store
is a redundant, error-prone cache of information that is already available through the API server. On startup, virt-handler's VMI informers perform a full
list, giving it every VMI on the node, including ones that appeared while it was down. The on-disk checkpoint files are removed entirely.

This VEP depends on [VEP-491](../491-launcher-socket-authentication/vep.md) and shares its `LauncherSocketAuthentication` feature gate, because neither
is useful alone: ghost records are the only thing keeping discovery narrow today, and authentication has little to protect until discovery stops
depending on a file virt-handler wrote itself.

Specifically, it depends on VEP-491 having **no fallback** for peers that do not serve `CmdAuth`. Discovery here establishes only that a pod UID belongs
to the VMI, and `ActivePods` is built from controller owner references, so a pod a user created in their own namespace can be in it. If enforcement
could be declined by a peer that just does not implement `CmdAuth`, this VEP would widen the impersonation surface rather than leave it unchanged.

## Motivation

virt-handler's ghost record cache is an on-disk checkpoint store (`GhostRecordStore`) that maps VMI namespace/name to socket file paths and VMI UIDs. It is
populated when virt-handler first connects to a launcher and persists across restarts. The cache has three fundamental problems:

1. **Stale on restart.** A virt-launcher that starts while virt-handler is down is not in the cache, so `handleResync` cannot see it until some other
   path connects. This does not by itself mark the VMI Failed: `calculateVmPhaseForStatusReason` needs no domain *and*
   `IsLauncherClientUnresponsive`, which reads `ActivePods`, not ghost records. But the cache is still the wrong source of truth for domain polling,
   since resync cannot see a launcher it has not recorded and a stale record can point it at the wrong socket.

2. **Stale on cleanup failure.** If virt-handler crashes between deleting a VMI and cleaning up the ghost record, the stale record persists. On restart,
   virt-handler may try to connect to a socket that no longer exists, or worse, to one that has been reused by a different pod.

3. **Disk dependency.** The cache relies on a writable host filesystem. Filesystem corruption, permission changes, or disk pressure can prevent virt-handler
   from reading or writing records, breaking the entire discovery mechanism.

The acceptance criteria is: "virt-handler never reads VMI-related info from disk."

## Goals

- virt-handler discovers virt-launcher pods on its node from its existing VMI informers and `VMI.Status.ActivePods`, not from on-disk checkpoints.
- A virt-launcher that starts while virt-handler is down is discovered on startup without any data loss.
- The `GhostRecordStore` and its on-disk checkpoint files are removed.
- The source of truth for "which launchers exist on this node" is the API server, not a local cache.

## Non Goals

- **Authenticating discovered launchers.** That is handled by the prerequisite [VEP-491](../491-launcher-socket-authentication/vep.md). This VEP assumes
  authentication is in place.
- **Changing the gRPC command channel protocol.** The existing `Cmd`, `CmdInfo`, and `CmdAuth` services remain unchanged.
- **Removing the launcher client cache.** The in-memory `LauncherClientInfoByVMI` cache (which holds open gRPC connections) is retained. Only the on-disk
  ghost record persistence is removed.
- **Removing other on-disk caches.** virt-handler also persists container-disk mount info and hotplug disk state on disk. Those are separate concerns with
  different lifecycles and are out of scope.

## Definition of Users

- **VM owners** whose VMs must survive virt-handler restarts and upgrades without disruption.
- **Cluster administrators** performing KubeVirt upgrades who need zero VM downtime.

## User Stories

- As a VM owner, I need my VM to keep running when KubeVirt is upgraded and virt-handler restarts, even if my VM started while virt-handler was down.
- As a cluster admin, I need virt-handler to discover all launchers on the node after restart without depending on disk state that might be stale or
  corrupted.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### Discovery via VMI informers and ActivePods

The domain-watcher's resync loop currently discovers sockets by iterating ghost records. This VEP replaces that with iteration over the VMI store's
`ActivePods`.

The VMI store passed to the domain-watcher is the global `vmiInformer` store, which contains all VMIs in the cluster. This is the same store already
passed to the domain informer today. The store is not filtered to the local node because the domain informer predates the source/target filtered
informers. Two filters narrow it to local sockets:

1. **Node name.** `ActivePods` is a `map[types.UID]string` whose value is `pod.Spec.NodeName`. Entries whose value is not this node are skipped without
   touching the filesystem. This is the same filter `findSocketOnHost` already applies.
2. **Socket existence.** For the remaining entries the socket path is derived from the pod UID, and only paths that exist on disk are included. This
   catches a launcher whose socket is not up yet, and a pod whose `ActivePods` entry is ahead of or behind reality.

For Alpha this is acceptable. For Beta, the domain-watcher should be refactored to accept the filtered `vmiSourceInformer` and `vmiTargetInformer` stores
to avoid iterating VMIs on other nodes entirely.

[`Status.ActivePods`](https://kubevirt.io/api-reference/main/definitions.html#_v1_virtualmachineinstancestatus) maps launcher pod UID to node name.
virt-controller rebuilds it on every VMI reconcile from pods in the VMI's namespace carrying a controller `ownerReference` to the VMI, skipping pods
that are down. virt-handler already applies exactly these two filters in
[`findSocketOnHost`](https://github.com/kubevirt/kubevirt/blob/main/pkg/virt-handler/cmd-client/client.go), deriving:

```
/pods/<podUID>/volumes/kubernetes.io~empty-dir/sockets/launcher-sock
```

The ghost record store is therefore a redundant cache of what the VMI informers already have, and dropping it adds no informer or API server pressure.

This is consistent with KubeVirt's architecture: the
[reviewer guide](https://github.com/kubevirt/kubevirt/blob/main/docs/reviewer-guide.md) explicitly says to avoid adding informers to node-level components
like virt-handler, and that "virt-handler shouldn't need permissions to list all Pods because viewing pods is virt-controller's responsibility." Using the
VMI informer's `ActivePods` follows both principles: no new informer, and pod discovery stays in virt-controller's domain.

The domain informer's initial `List` and every resync both authenticate a socket before calling `GetDomain`. For each discovered local socket:

1. Authenticate it via `CmdAuth.Authenticate` + TokenReview ([VEP-491](../491-launcher-socket-authentication/vep.md)).
2. Call `CmdInfo.Info` for version negotiation.
3. Call `Cmd.GetDomain` to retrieve the domain state.
4. Emit the domain event into the domain informer's event channel.

`List` runs only after the VMI informer has synced, so `ActivePods` is populated. A socket that fails authentication is omitted. `handleResync` only emits
`Modified` for sockets that authenticate and return a domain, so it cannot evict a domain that `List` already added. That is why `List` authenticates too.
A missing socket file is skipped (the pod is on another node, or the launcher has not created the socket yet).

### More than one local socket per VMI

A ghost record holds a single `SocketFile` per VMI, so resync structurally polled at most one socket. `ActivePods` iteration does not: a migration
target landing on the source node, or a new launcher whose socket is up before virt-controller prunes the old pod, gives two live local sockets for one
VMI. Domains are keyed by namespace/name, so emitting both would make them overwrite each other and flap every resync. `findSocketOnHost` avoids this by
erroring out, which a discovery loop that must keep reporting the VMI cannot do.

The rule is **at most one domain event per VMI per resync**, picking in order:

1. The socket of the launcher client already cached in `LauncherClientInfoByVMI`, so the domain-watcher and the VM controller stay on the same peer.
2. Otherwise, the only candidate whose `GetDomain` reports an existing domain.

If both report a domain and no client is cached, virt-handler reports neither and warns, the same call `findSocketOnHost` makes today. Resolving it
needs source/target awareness the domain-watcher lacks: `migrationState.targetPod` is a pod *name* and socket paths are keyed by pod UID, so mapping
them needs a pod informer, which this VEP rules out. The case is reachable only after a virt-handler restart during a same-node migration and resolves
itself once the VM controller establishes a client. Narrowing it further is an Alpha follow-up, not a blocker.

This replaces `listSockets(GhostRecordGlobalStore.list())` in both `handleResync` and `handleStaleSocketConnections`.

### API server dependency

Ghost records gave the domain-watcher a node-local path to discover sockets. Replacing them with the VMI informer adds an API server dependency, but
not a new one: virt-handler already blocks on
[`cache.WaitForCacheSync`](https://github.com/kubevirt/kubevirt/blob/main/cmd/virt-handler/virt-handler.go) at startup, and the VMI reconcile loop
cannot act on detected domains without the VMI informer anyway. After startup `handleResync` reads the in-memory store, so an outage does not affect
discovery. The only per-socket API call is the TokenReview from [VEP-491](../491-launcher-socket-authentication/vep.md), on `List` and every resync in
Alpha, cached in Beta.

Second, `ActivePods` is written by virt-controller, so a launcher created while virt-controller is wedged is invisible until it catches up. Also not
new: `GetLauncherClient` resolves sockets through `FindSocket`, which reads the same field, so such a VMI is already unmanageable on main. What changes
is that the domain-watcher shares that dependency instead of keeping an independent disk-backed view, which is the point: one source of truth that can
lag by a reconcile, instead of two that can disagree.

### Error handling and recoverability

Authentication failures (TokenReview timeout, API server blip, token not yet projected) are treated as recoverable. `List` and `handleResync` omit that
socket and retry on the next resync. They do not emit a domain for it. An authentication failure does not by itself mark the VMI Failed. The VMI goes Failed
only through the existing unresponsive-socket path (socket gone, or socket marked unresponsive). This matters because a transient API server outage during
TokenReview must not kill running VMs.

If a socket authenticates but `GetDomain` then fails, the domain is reported with status `Unknown`, which `sync()` skips, same as a failed dial today.
If the socket file is not there yet, it is skipped and retried on the next resync.

On the VM sync path this has a concrete requirement. `GetLauncherClient` wraps permanent failures with `IrrecoverableError`, and since
[kubevirt#17847](https://github.com/kubevirt/kubevirt/pull/17847) `sync()` treats that as domain-not-alive. An authentication failure against a socket
that is present and responding must therefore be returned unwrapped, so it is retried rather than read as "this launcher is gone". Only the existing
`ErrNoSocketFound` case stays irrecoverable.

### TokenReview caching (Beta)

virt-handler should cache authentication results keyed by pod UID, reusing a result while the pod UID is unchanged and the token has not expired. Only
the first resync after startup then pays N TokenReview calls. This extends `LauncherClientInfoByVMI`, which already holds open gRPC connections per
VMI, so a cached client skips both authentication and connection setup. [VEP-491](../491-launcher-socket-authentication/vep.md) targets the same for
Beta.

### Removing the ghost record store

The following are removed:

- `GhostRecordStore` struct and `GhostRecordGlobalStore` global
- `ghostRecord` type
- `InitializeGhostRecordCache` function
- `Add`, `Delete`, `Exists`, `LastKnownUID`, `list`, `findBySocket` methods
- The `IterableCheckpointManager` and all on-disk checkpoint read/write for ghost records
- `listSockets` function (replaced by VMI store iteration)

Both `handleResync` and `handleStaleSocketConnections` currently call `listSockets(GhostRecordGlobalStore.list())`. Both are replaced with VMI store
iteration over `ActivePods`. For `handleStaleSocketConnections`, the `findBySocket` lookup (used to build a synthetic domain object for deletion events) is
replaced by looking up the VMI directly from the socket path's pod UID via `ActivePods`.

#### The `LastKnownUID` fallback

One caller is not a discovery path. `VirtualMachineController.execute` resolves the VMI UID from the VMI object, then from the domain cache, then from
`LastKnownUID`, that last step covering a VMI gone from the informer whose domain cache entry has no UID either.

This fallback is dropped rather than replaced, since with no VMI in the store there is nothing to consult. The two earlier steps cover the realistic
cases: a VMI being deleted is still in the informer while its finalizer is held, and any domain virt-handler has seen carries the UID. The uncovered
case is virt-handler restarting after the VMI is already gone from the API server with no domain event received, where cleanup now falls to the
existing orphan paths. This is a deliberate narrowing, given the acceptance criterion, and is called out so it is reviewed rather than discovered.
Confirming no cleanup path is actually lost is an Alpha requirement.

The `LauncherClientInfoByVMI` in-memory cache is kept. It caches open gRPC connections and is rebuilt from live VMI data on startup.

### Startup sequence

The new virt-handler startup sequence, when the feature gate is enabled:

1. Start the VMI informers and wait for the initial list to sync. The domain informer is not started yet.
2. Start the domain informer. Its `List` reads the populated VMI store, derives socket paths from `ActivePods`, authenticates each local socket, and then
   calls `GetDomain`.
3. The resync loop keeps authenticating on each tick.
4. Begin processing VMI events.

Today `factory.Start` and `domainSharedInformer.Run` are concurrent. That is safe only because ghost records are loaded from disk before either starts.
It is not safe once `List` reads the VMI store: an early `List` can sync an empty domain cache. Step 2 replaces the ghost record cache initialization that
currently runs in `InitializeGhostRecordCache`.

### Live migration

During live migration, both the source and target launcher pods appear in `VMI.Status.ActivePods`. The source node's `vmiSourceInformer` and the target
node's `vmiTargetInformer` each see the VMI. Each virt-handler instance discovers only the local pod (the remote pod's socket path does not exist locally),
which is the same behavior as today.

### Handling launcher disappearance

When a VMI is deleted or its `ActivePods` changes, the VMI informer fires an event and virt-handler drops the cached client for the removed pod UID.
During migration the source pod is deleted while the target runs elsewhere, so the source virt-handler drops only its local client and tears nothing
down, since the VMI is still alive.

This is a behavioural change. With ghost records, an absent socket file made `getDomainFromRecord` return a synthetic domain with `DeletionTimestamp`
set, signalling the reconcile loop to clean up. Now absent sockets are simply omitted, and disappearance is detected through the VMI informer rather
than by polling the filesystem.

The `handleStaleSocketConnections` watchdog remains for detecting sockets that become unresponsive without a corresponding VMI state change (e.g. process
crash inside the container). It iterates the same VMI store instead of ghost records. When a stale socket is found via VMI-based discovery, the domain is
constructed from the VMI object (namespace, name, UID) rather than from a ghost record.

### Feature gate

Ghost record removal shares the `LauncherSocketAuthentication` gate with [VEP-491](../491-launcher-socket-authentication/vep.md). There is no useful
intermediate state: authentication makes dynamic discovery safe, and dynamic discovery removes the need for ghost records. Disabled (the default) keeps
the ghost record cache and today's behaviour. Enabled, virt-handler authenticates every socket and discovers launchers from the VMI store.

Sharing a gate means sharing a graduation schedule. Beta is on by default
([VEP-229](../../meta-VEPs/229-beta-features-on-by-default/vep.md)), so promoting this gate also turns on authentication enforcement everywhere, and
VEP-491 leaves its beta target unset until launchers predating `CmdAuth` are rare. **Neither VEP's beta target can be filled in without the other.**

## Alternatives

### Add a pod informer to virt-handler

Add a node-scoped pod informer filtered by `kubevirt.io=virt-launcher` and `spec.nodeName`.

**Not chosen.** The [reviewer guide](https://github.com/kubevirt/kubevirt/blob/main/docs/reviewer-guide.md) explicitly says to avoid adding informers to
node-level components like virt-handler because of API server pressure at scale. It also says "virt-handler shouldn't need permissions to list all Pods
because viewing pods is virt-controller's responsibility." Using `VMI.Status.ActivePods` from the existing VMI informer avoids both problems.

### Keep ghost records, add freshness checks

On startup, compare ghost records against the VMI informer. If a ghost record has no matching VMI, discard it. If a VMI has no ghost record, add one.

**Not chosen.** This reduces the staleness window but does not eliminate the on-disk dependency or the filesystem failure modes. It also adds complexity
(two sources of truth that must be reconciled).

### CRI-based discovery

Query the container runtime (CRI) directly to find virt-launcher containers on the node.

**Deferred.** CRI gives the most accurate picture of what is actually running, but requires a CRI socket connection and parsing runtime metadata. The VMI
informer is simpler and already available. CRI-based validation can be added later as a cross-check.

### Watch host filesystem for socket creation

Use inotify/fsnotify to watch the kubelet pods directory for new `launcher-sock` files.

**Not chosen.** Fragile (filesystem watching misses events under load), does not work across filesystem boundaries, and duplicates what the VMI informer
already provides.

## Does it belong to core KubeVirt?

Yes. Ghost record management is entirely internal to virt-handler, a core component. The discovery mechanism cannot be externalized.

## Scalability

No new informers and no new list/watch. TokenReview needs `tokenreviews/create` on virt-handler's ClusterRole. That rule is added by
[VEP-491](../491-launcher-socket-authentication/vep.md). This VEP adds no RBAC of its own.

In Alpha, TokenReview runs on the initial `List` and every resync, for every local socket (~1-5ms each), so a node with N VMs pays N calls per cycle
until the Beta cache lands. The ghost-record equivalent was N checkpoint reads plus N socket connections.

**VMI store scope.** Alpha uses the global `vmiInformer` store (all VMIs in the cluster), filtering to local pods by the node name in `ActivePods`, so
each resync costs O(total VMIs) in-memory comparisons plus one `os.Stat` per *local* pod. Acceptable for Alpha. Beta should pass the node-filtered
`vmiSourceInformer` and `vmiTargetInformer` stores instead, reducing the iteration to O(VMIs on this node).

No new CRDs, no new controllers.

## Update/Rollback Compatibility

**Upgrade:** The gate is off by default, so upgrading adds the code without activating it. Enabling it switches to VMI-based discovery, and virt-handler
deletes the ghost record checkpoint files on the first startup that sees the gate on. Those files are current at that moment: the deletion is about
what happens next, since nothing writes or prunes them while the gate is on, so they would drift. Deleting them up front means a later rollback starts
from an empty cache rather than a snapshot of the past. The directory is dedicated to ghost records (`<virt-private-dir>/ghost-records`), so
container-disk and hotplug checkpoints are untouched.

**Rollback:** Disabling the gate reverts to ghost record discovery with an empty cache, which virt-handler rebuilds as its reconcile loop reconnects to
launchers. That is equivalent to a fresh virt-handler start on a node, an already-supported scenario. Ghost record code is retained through Beta for
this path and removed at GA.

## Functional Testing Approach

### Unit

- VMI store iteration discovers all launcher sockets on the node via `ActivePods`.
- Socket path is correctly derived from pod UID.
- Launcher that started while virt-handler was down is discovered on startup (VMI informer initial list catches it).
- VMI deletion triggers client cleanup (drop local client, not VMI teardown).
- `handleResync` uses VMI store iteration, not ghost records.
- `handleStaleSocketConnections` uses VMI store iteration, not ghost records.
- Domain `List` authenticates, and a socket that fails authentication is omitted rather than inserted.
- Domain `List` runs only after the VMI informer has synced.
- Authentication failure is recoverable (retried on next resync, does not by itself mark the VMI Failed).
- `ActivePods` entries for pods on other nodes are skipped by node name, with no `os.Stat` on their socket paths.
- A VMI with two local sockets produces exactly one domain event: the cached client's socket when there is one, the only socket reporting a domain
  otherwise, and none plus a warning when both report a domain and no client is cached.

### Functional / e2e

- Start a VMI, kill virt-handler, wait for the VMI's domain to be running, start virt-handler. VMI must remain Running.
- Start a VMI while virt-handler is down. When virt-handler starts, the VMI must be discovered and reported as Running.
- Upgrade KubeVirt (which restarts virt-handler). All running VMIs must survive with no disruption.
- No ghost record checkpoint files are written to disk when the feature gate is enabled.
- Enable then disable the feature gate. virt-handler rebuilds ghost records from scratch without killing VMs.
- Delete a VMI while virt-handler is down, then start virt-handler. The launcher pod and its resources are cleaned up even with no `LastKnownUID`
  fallback available.

## Implementation History

N/A

## Graduation Requirements

### Alpha

- [ ] Changes guarded by `LauncherSocketAuthentication` feature gate (shared with [VEP-491](../491-launcher-socket-authentication/vep.md))
- [ ] `handleResync` and `handleStaleSocketConnections` discover launchers from VMI store's `ActivePods` instead of ghost records
- [ ] Ghost record checkpoint files are deleted when gate is enabled
- [ ] Startup discovers launchers that started while virt-handler was down
- [ ] Domain `List` waits for the VMI informer to sync, then authenticates each local socket before `GetDomain`
- [ ] At most one domain event per VMI per resync when a VMI has more than one local socket
- [ ] Confirmed that dropping the `LastKnownUID` fallback loses no cleanup path for a VMI deleted while virt-handler was down
- [ ] Unit tests listed above pass
- [ ] Functional tests listed above pass

### Beta

- [ ] No VMI disruptions reported in alpha
- [ ] Ghost record code retained for fallback when gate is disabled
- [ ] Domain-watcher refactored to accept node-filtered VMI stores (vmiSourceInformer + vmiTargetInformer) instead of the global vmiInformer store
- [ ] TokenReview results cached by pod UID (consistent with [VEP-491](../491-launcher-socket-authentication/vep.md) Beta)

#### On-By-Default Readiness

- Zero reports of VMIs killed on upgrade with the gate enabled
- Startup time measured and documented for nodes with 100+ VMs

### GA

- [ ] No outstanding functional or reliability gaps
- [ ] Feature has been on-by-default for at least one minor release with no regressions
- [ ] Ghost record code removed from the codebase (gate locked on)
