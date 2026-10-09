# VEP #492: Replace ghost record cache with pod informer discovery

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

virt-handler discovers virt-launcher pods on its node by reading an on-disk ghost record cache. This cache is persisted as checkpoint files and is the primary
mechanism for surviving virt-handler restarts. When the cache is stale (a launcher started while virt-handler was down), virt-handler does not know about the
launcher, reports the VMI as Failed, and kills it. This has caused multiple customer-facing bugs during upgrades.

This VEP replaces the ghost record cache with discovery through the Kubernetes pod informer. On startup, virt-handler lists virt-launcher pods on its node from
the API server, connects to their sockets, and rebuilds its in-memory state. The on-disk checkpoint files are removed entirely.

This VEP depends on VEP 491-launcher-socket-authentication (feature gate `LauncherSocketAuthentication`). Without authentication, dynamic discovery would allow
a malicious pod with the right labels and volume layout to be discovered and trusted.

## Motivation

virt-handler's ghost record cache is an on-disk checkpoint store (`GhostRecordStore`) that maps VMI namespace/name to socket file paths and pod UIDs. It is
populated when virt-handler first connects to a launcher and persists across restarts. The cache has three fundamental problems:

1. **Stale on restart.** A virt-launcher that starts while virt-handler is down (during upgrade, crash, or DaemonSet rollout) is not in the cache. virt-handler
   does not discover it, the VMI has no domain, and virt-handler marks it as Failed. Multiple bugs have been filed where running VMs are killed during upgrades
   because of this.

2. **Stale on cleanup failure.** If virt-handler crashes between deleting a VMI and cleaning up the ghost record, the stale record persists. On restart,
   virt-handler may try to connect to a socket that no longer exists, or worse, to one that has been reused by a different pod.

3. **Disk dependency.** The cache relies on a writable host filesystem. Filesystem corruption, permission changes, or disk pressure can prevent virt-handler
   from reading or writing records, breaking the entire discovery mechanism.

The acceptance criteria is: "virt-handler never reads VMI-related info from disk."

## Goals

- virt-handler discovers virt-launcher pods on its node from the Kubernetes API (pod informer), not from on-disk checkpoints.
- A virt-launcher that starts while virt-handler is down is discovered on startup without any data loss.
- The `GhostRecordStore` and its on-disk checkpoint files are removed.
- The source of truth for "which launchers exist on this node" is the container runtime and the API server, not a local cache.

## Non Goals

- **Authenticating discovered launchers.** That is handled by the prerequisite VEP (491-launcher-socket-authentication). This VEP assumes authentication is in
  place.
- **Changing the gRPC command channel protocol.** The existing `Cmd`, `CmdInfo`, and `CmdAuth` services remain unchanged.
- **Removing the launcher client cache.** The in-memory `LauncherClientInfoByVMI` cache (which holds open gRPC connections) is retained. Only the on-disk ghost
  record persistence is removed.
- **Removing other on-disk caches.** virt-handler also persists container-disk mount info and hotplug disk state on disk. Those are separate concerns with
  different lifecycles and are out of scope.

## Definition of Users

- **VM owners** whose VMs must survive virt-handler restarts and upgrades without disruption.
- **Cluster administrators** performing KubeVirt upgrades who need zero VM downtime.

## User Stories

- As a VM owner, I need my VM to keep running when KubeVirt is upgraded and virt-handler restarts, even if my VM started while virt-handler was down.
- As a cluster admin, I need virt-handler to discover all launchers on the node after restart without depending on disk state that might be stale or corrupted.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### Discovery via pod informer

virt-handler already watches pods on its node via the pod informer. On startup, it can list all pods with the label `kubevirt.io=virt-launcher` on its node and
derive the socket path from each pod's UID:

```
/pods/<podUID>/volumes/kubernetes.io~empty-dir/sockets/launcher-sock
```

For each discovered socket:

1. Authenticate it via `CmdAuth.Authenticate` + TokenReview (VEP 491-launcher-socket-authentication).
2. Call `CmdInfo.Info` for version negotiation.
3. Call `Cmd.GetDomain` to retrieve the domain state.
4. Emit the domain event into the informer's event channel.

This replaces `listSockets(GhostRecordGlobalStore.list())` in `handleResync`.

### Removing the ghost record store

The following are removed:

- `GhostRecordStore` struct and `GhostRecordGlobalStore` global
- `ghostRecord` type
- `InitializeGhostRecordCache` function
- `Add`, `Delete`, `Exists`, `LastKnownUID`, `list`, `findBySocket` methods
- The `IterableCheckpointManager` and all on-disk checkpoint read/write for ghost records
- `listSockets` function (replaced by pod informer listing)

The `LauncherClientInfoByVMI` in-memory cache is kept. It caches open gRPC connections and is rebuilt from live pod data on startup.

### Startup sequence

The new virt-handler startup sequence:

1. Start pod informer, wait for initial sync.
2. List all `kubevirt.io=virt-launcher` pods on this node.
3. For each pod: derive socket path, authenticate, connect, retrieve domain state.
4. Start the domain-watcher resync loop.
5. Begin processing VMI events.

Steps 2-3 replace the ghost record cache initialization that currently runs in `InitializeGhostRecordCache`.

### Resync loop

`handleResync` currently calls `listSockets(GhostRecordGlobalStore.list())`. After this VEP, it instead queries the pod informer for current virt-launcher pods
on the node and derives socket paths from their UIDs. This guarantees that every running launcher is discovered, including ones that started while virt-handler
was down.

### Handling launcher disappearance

When a virt-launcher pod is deleted, the pod informer fires a delete event. virt-handler closes the cached client connection and cleans up the VMI state. This
replaces the ghost record deletion path.

The `handleStaleSocketConnections` watchdog remains for detecting sockets that become unresponsive without a corresponding pod deletion event (e.g. process
crash inside the container).

### Feature gate

Ghost record removal is guarded by the same `LauncherSocketAuthentication` feature gate that enables socket authentication (VEP
491-launcher-socket-authentication). Authentication and ghost record removal are two sides of the same coin: authentication makes dynamic discovery safe, and
dynamic discovery eliminates the need for ghost records. There is no useful intermediate state where authentication is on but ghost records are still used.

When the gate is disabled (default), the ghost record cache is used (current behavior). When enabled, virt-handler authenticates every socket and discovers
launchers from the pod informer instead of ghost records.

## Alternatives

### Keep ghost records, add freshness checks

On startup, compare ghost records against the pod informer. If a ghost record has no matching pod, discard it. If a pod has no ghost record, add one.

**Not chosen.** This reduces the staleness window but does not eliminate the on-disk dependency or the filesystem failure modes. It also adds complexity (two
sources of truth that must be reconciled).

### CRI-based discovery

Query the container runtime (CRI) directly to find virt-launcher containers on the node.

**Deferred.** CRI gives the most accurate picture of what is actually running, but requires a CRI socket connection and parsing runtime metadata. The pod
informer is simpler and already available in virt-handler. CRI-based validation can be added later as a cross-check.

### Watch host filesystem for socket creation

Use inotify/fsnotify to watch the kubelet pods directory for new `launcher-sock` files.

**Not chosen.** Fragile (filesystem watching misses events under load), does not work across filesystem boundaries, and duplicates what the pod informer already
provides.

## Does it belong to core KubeVirt?

Yes. Ghost record management is entirely internal to virt-handler, a core component. The discovery mechanism cannot be externalized.

## Scalability

| Interaction | Component | When | Cost |
| --- | --- | --- | --- |
| Pod informer list | virt-handler to API server | Startup | Already exists (no new list/watch) |
| Socket connect + auth | virt-handler to virt-launcher | Startup, per pod | ~5-10ms per launcher |

On a node with N VMs, startup performs N socket connections and N TokenReview calls. This is comparable to the current ghost record startup (N checkpoint file
reads + N socket connections), with the TokenReview calls being the only added cost (covered by the authentication VEP).

No new CRDs, no new controllers, no new watches.

## Update/Rollback Compatibility

**Upgrade:** The feature gate is off by default. Upgrading KubeVirt adds the pod informer discovery code but does not activate it. Ghost records continue to
work. Enabling the feature gate switches to informer-based discovery. On the first startup with the gate enabled, ghost record checkpoint files are ignored (not
read). They can be cleaned up by a one-time migration that removes the checkpoint directory.

**Rollback:** Disabling the feature gate reverts to ghost record discovery. Since ghost records were not being written while the gate was enabled, the cache
will be empty on rollback. virt-handler will rebuild it from scratch by connecting to the launchers it discovers through its normal VMI reconciliation loop.
There is a brief window where virt-handler has no cached state, but this is equivalent to a fresh virt-handler start on a node, which is already a supported
scenario.

## Functional Testing Approach

### Unit

- Pod informer lists all virt-launcher pods on the node.
- Socket path is correctly derived from pod UID.
- Launcher that started while virt-handler was down is discovered on startup.
- Deleted pod triggers client cleanup.
- `handleResync` uses pod informer listing, not ghost records.

### Functional / e2e

- Start a VMI, kill virt-handler, wait for the VMI's domain to be running, start virt-handler. VMI must remain Running.
- Start a VMI while virt-handler is down. When virt-handler starts, the VMI must be discovered and reported as Running.
- Upgrade KubeVirt (which restarts virt-handler). All running VMIs must survive with no disruption.
- No ghost record checkpoint files are written to disk when the feature gate is enabled.

## Implementation History

N/A

## Graduation Requirements

### Alpha

- [ ] Changes guarded by `LauncherSocketAuthentication` feature gate (shared with VEP 491-launcher-socket-authentication)
- [ ] `handleResync` discovers launchers from pod informer
- [ ] Ghost record store is not read or written when gate is enabled
- [ ] Startup discovers launchers that started while virt-handler was down
- [ ] Unit tests listed above pass

### Beta

- [ ] Functional tests listed above pass, including upgrade scenario
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
