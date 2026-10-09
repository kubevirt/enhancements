# VEP #492: Replace ghost record cache with VMI-based launcher discovery

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

virt-handler discovers virt-launcher pods on its node by reading an on-disk ghost record cache. This cache is persisted as checkpoint files and is the
primary mechanism for surviving virt-handler restarts. When the cache is stale (a launcher started while virt-handler was down), virt-handler does not know
about the launcher, reports the VMI as Failed, and kills it. This has caused multiple customer-facing bugs during upgrades.

This VEP replaces the ghost record cache with discovery through virt-handler's existing VMI informers. Each VMI's `Status.ActivePods` already contains the
pod UIDs of all launcher pods for that VMI, and virt-handler already derives socket paths from these UIDs in its launcher client code. The ghost record store
is a redundant, error-prone cache of information that is already available through the API server. On startup, virt-handler's VMI informers perform a full
list, giving it every VMI on the node, including ones that appeared while it was down. The on-disk checkpoint files are removed entirely.

This VEP depends on [VEP-491](../491-launcher-socket-authentication/vep.md) (feature gate `LauncherSocketAuthentication`). Without authentication, dynamic
discovery could allow a malicious pod to be trusted.

## Motivation

virt-handler's ghost record cache is an on-disk checkpoint store (`GhostRecordStore`) that maps VMI namespace/name to socket file paths and pod UIDs. It is
populated when virt-handler first connects to a launcher and persists across restarts. The cache has three fundamental problems:

1. **Stale on restart.** A virt-launcher that starts while virt-handler is down (during upgrade, crash, or DaemonSet rollout) is not in the cache.
   virt-handler does not discover it, the VMI has no domain, and virt-handler marks it as Failed. Multiple bugs have been filed where running VMs are killed
   during upgrades because of this.

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

virt-handler already runs two VMI informers that give it all VMIs on the node:
[`vmiSourceInformer`](https://github.com/kubevirt/kubevirt/blob/main/pkg/controller/virtinformers.go) (VMIs where this node is the source) and
`vmiTargetInformer` (VMIs where this node is a migration target). These informers perform a full list from the API server on startup, so they catch every
VMI, including ones that were scheduled to this node while virt-handler was down.

Each VMI's [`Status.ActivePods`](https://kubevirt.io/api-reference/main/definitions.html#_v1_virtualmachineinstancestatus) is a
`map[types.UID]string` populated by virt-controller when it creates a launcher pod. virt-handler already uses this map to find launcher sockets in its
[`findSocketOnHost`](https://github.com/kubevirt/kubevirt/blob/main/pkg/virt-handler/cmd-client/client.go) function, which iterates `ActivePods`, derives
the socket path from each pod UID, and checks whether the socket exists on disk:

```
/pods/<podUID>/volumes/kubernetes.io~empty-dir/sockets/launcher-sock
```

This means the ghost record store is a redundant cache of information that is already available through the VMI informers. Removing it and using `ActivePods`
directly eliminates the staleness problem without adding any new informer or API server pressure.

This approach is consistent with KubeVirt's architecture: the
[reviewer guide](https://github.com/kubevirt/kubevirt/blob/main/docs/reviewer-guide.md) explicitly says to avoid adding informers to node-level components
like virt-handler, and that "virt-handler shouldn't need permissions to list all Pods because viewing pods is virt-controller's responsibility." Using the
VMI informer's `ActivePods` follows both principles: no new informer, and pod discovery stays in virt-controller's domain.

For each discovered socket:

1. Authenticate it via `CmdAuth.Authenticate` + TokenReview ([VEP-491](../491-launcher-socket-authentication/vep.md)).
2. Call `CmdInfo.Info` for version negotiation.
3. Call `Cmd.GetDomain` to retrieve the domain state.
4. Emit the domain event into the domain informer's event channel.

This replaces `listSockets(GhostRecordGlobalStore.list())` in `handleResync`.

### Removing the ghost record store

The following are removed:

- `GhostRecordStore` struct and `GhostRecordGlobalStore` global
- `ghostRecord` type
- `InitializeGhostRecordCache` function
- `Add`, `Delete`, `Exists`, `LastKnownUID`, `list`, `findBySocket` methods
- The `IterableCheckpointManager` and all on-disk checkpoint read/write for ghost records
- `listSockets` function (replaced by VMI store iteration)

The `LauncherClientInfoByVMI` in-memory cache is kept. It caches open gRPC connections and is rebuilt from live VMI data on startup.

### Startup sequence

The new virt-handler startup sequence:

1. VMI informers sync (already happens today, performs a full list from the API server).
2. For each VMI with `ActivePods` entries on this node, derive socket paths from pod UIDs.
3. For each socket: authenticate, connect, retrieve domain state.
4. Start the domain-watcher resync loop.
5. Begin processing VMI events.

Steps 2-3 replace the ghost record cache initialization that currently runs in `InitializeGhostRecordCache`.

### Resync loop

`handleResync` currently calls `listSockets(GhostRecordGlobalStore.list())`. After this VEP, it instead iterates VMIs from the VMI store and derives
socket paths from `ActivePods`. This guarantees that every running launcher is discovered, including ones that started while virt-handler was down,
because the VMI informer's initial list catches all VMIs on the node.

### Live migration

During live migration, both the source and target launcher pods appear in `VMI.Status.ActivePods`. The source node's `vmiSourceInformer` and the target
node's `vmiTargetInformer` each see the VMI. Each virt-handler instance discovers only the local pod (the remote pod's socket path does not exist locally),
which is the same behavior as today.

### Handling launcher disappearance

When a VMI is deleted or its `ActivePods` is updated, the VMI informer fires an update/delete event. virt-handler closes the cached client connection and
cleans up state. This replaces the ghost record deletion path.

The `handleStaleSocketConnections` watchdog remains for detecting sockets that become unresponsive without a corresponding VMI state change (e.g. process
crash inside the container).

### Feature gate

Ghost record removal is guarded by the same `LauncherSocketAuthentication` feature gate that enables socket authentication
([VEP-491](../491-launcher-socket-authentication/vep.md)). Authentication and ghost record removal are two sides of the same coin: authentication makes
dynamic discovery safe, and dynamic discovery eliminates the need for ghost records. There is no useful intermediate state where authentication is on but
ghost records are still used.

When the gate is disabled (default), the ghost record cache is used (current behavior). When enabled, virt-handler authenticates every socket and discovers
launchers from the VMI store instead of ghost records.

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

No new informers, no new list/watch, no new RBAC. The VMI informers already exist and are already synced on startup. The only added cost per launcher is
the TokenReview call from [VEP-491](../491-launcher-socket-authentication/vep.md) (~1-5ms per call).

On a node with N VMs, startup performs N socket connections and N TokenReview calls. This is comparable to the current ghost record startup (N checkpoint
file reads + N socket connections), with the TokenReview calls being the only new cost.

No new CRDs, no new controllers.

## Update/Rollback Compatibility

**Upgrade:** The feature gate is off by default. Upgrading KubeVirt adds the VMI-based discovery code but does not activate it. Ghost records continue to
work. Enabling the feature gate switches to VMI-based discovery. On the first startup with the gate enabled, ghost record checkpoint files are ignored (not
read). They can be cleaned up by a one-time migration that removes the checkpoint directory.

**Rollback:** Disabling the feature gate reverts to ghost record discovery. Since ghost records were not being written while the gate was enabled, the cache
will be empty on rollback. virt-handler will rebuild it from scratch by connecting to the launchers it discovers through its normal VMI reconciliation loop.
There is a brief window where virt-handler has no cached state, but this is equivalent to a fresh virt-handler start on a node, which is already a
supported scenario.

## Functional Testing Approach

### Unit

- VMI store iteration discovers all launcher sockets on the node via `ActivePods`.
- Socket path is correctly derived from pod UID.
- Launcher that started while virt-handler was down is discovered on startup (VMI informer initial list catches it).
- VMI deletion triggers client cleanup.
- `handleResync` uses VMI store iteration, not ghost records.

### Functional / e2e

- Start a VMI, kill virt-handler, wait for the VMI's domain to be running, start virt-handler. VMI must remain Running.
- Start a VMI while virt-handler is down. When virt-handler starts, the VMI must be discovered and reported as Running.
- Upgrade KubeVirt (which restarts virt-handler). All running VMIs must survive with no disruption.
- No ghost record checkpoint files are written to disk when the feature gate is enabled.

## Implementation History

N/A

## Graduation Requirements

### Alpha

- [ ] Changes guarded by `LauncherSocketAuthentication` feature gate (shared with [VEP-491](../491-launcher-socket-authentication/vep.md))
- [ ] `handleResync` discovers launchers from VMI store's `ActivePods` instead of ghost records
- [ ] Ghost record store is not read or written when gate is enabled
- [ ] Startup discovers launchers that started while virt-handler was down
- [ ] Unit tests listed above pass
- [ ] Functional tests listed above pass

### Beta

- [ ] Ghost record checkpoint files are cleaned up on first startup with gate enabled
- [ ] No VMI disruptions reported in alpha
- [ ] `GhostRecordStore` code removed (not just bypassed)

#### On-By-Default Readiness

- Zero reports of VMIs killed on upgrade with the gate enabled
- Startup time measured and documented for nodes with 100+ VMs

### GA

- [ ] No outstanding functional or reliability gaps
- [ ] Feature has been on-by-default for at least one minor release with no regressions
- [ ] Ghost record related code fully removed from the codebase
