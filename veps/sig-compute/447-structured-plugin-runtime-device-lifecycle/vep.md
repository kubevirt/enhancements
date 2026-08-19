# VEP #447: Runtime Domain Reconciliation for Structured Plugins

Owners:

- TBD

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

[VEP 190](../190-kubevirt-structured-plugins/vep.md) introduces structured plugins with admission, domain, and node hooks. Its domain hook is invoked while KubeVirt constructs a domain for initial definition or migration. It is not invoked while KubeVirt reconciles an already-running domain.

This VEP adds **runtime domain reconciliation** to the existing advanced domain hook. KubeVirt passes both its generated desired domain and the current live domain to the hook. The plugin returns the desired domain after reconciling any external resources required by its devices. KubeVirt validates the runtime delta and remains the only component that attaches or detaches devices through libvirt.

The initial use case is an out-of-tree vhost-user block provider. Such a provider can call an endpoint API, create a node-local Unix socket, and add a vhost-user disk without representing the disk as a PersistentVolume, PersistentVolumeClaim, DataVolume, or KubeVirt volume.

## Motivation

KubeVirt's existing disk hotplug flow is tied to PVC and DataVolume sources. It creates an attachment pod, mounts the volume into `virt-launcher`, waits for a filesystem or block-device path, and then attaches the corresponding libvirt disk.

Some providers expose a different lifecycle. For example, an NBS-style vhost-user backed provider may:

1. call `StartEndpoint` for a remote disk;
2. wait for a vhost-user socket in a directory shared with `virt-launcher`;
3. attach a `<disk type='vhostuser'>` device to QEMU;
4. detach the device; and
5. call `StopEndpoint` after QEMU releases the socket.

VEP 190 can implement this lifecycle before VM startup, but its domain hook is not called during running-domain synchronization. Its existing node hooks also cannot return device XML. Consequently, a plugin cannot currently add or remove a device from a running domain without introducing an in-tree volume source or calling libvirt independently.

This proposal emerged from a related discussion in the earlier [VEP 215](https://github.com/kubevirt/enhancements/pull/218).

## Goals

- Invoke an advanced structured domain hook while reconciling a running VMI.
- Provide the hook with both generated desired domain XML and current live domain XML.
- Allow an advanced hook to add and remove plugin-owned vhost-user disks.
- Allow the plugin to prepare, retain, and clean provider resources idempotently.
- Keep KubeVirt as the only component that calls libvirt.
- Bypass PVCs, DataVolumes, KubeVirt volume sources, and hotplug attachment pods.
- Recover from interrupted attach, detach, and cleanup using desired state, live XML, and plugin-owned resource inventory.
- Support live migration when the plugin opts in and the runtime disk set is converged and frozen.
- Reuse VEP 190 conditions, timeouts, failure strategies, admission integration, and migration contexts.

## Non Goals

- Defining an NBS-specific or generic plugin volume source in the VMI API.
- Extending `virtctl addvolume` with plugin-defined sources.
- Building a generic runtime-device API for every libvirt device kind.
- Adding runtime-device operation state to VMI status.
- Adding new node hook points for per-device attach or detach.
- Allowing plugin sidecars to call libvirt.
- Adding containers, mounts, devices, or immutable domain properties to an existing launcher pod.
- Updating an attached plugin-owned disk in place.
- Applying hotplug changes while live migration is active.

## Definition of Users

- **Plugin developer:** implements an out-of-tree domain extension and provider-resource lifecycle.
- **Cluster administrator:** installs and authorizes the plugin and its pod or node access.
- **VM owner:** requests provider disks through a plugin-specific API.
- **KubeVirt developer:** maintains generic validation and libvirt synchronization without provider-specific code.

## User Stories

- As a VM owner, I can attach and detach a provider disk from a running VM without creating a PVC.
- As a plugin developer, I can prepare a vhost-user endpoint before KubeVirt attaches its disk.
- As a plugin developer, I can retain an endpoint until QEMU releases it and eventually remove an orphan after a restart.
- As a cluster administrator, I can preserve live migration for plugins that support target endpoint preparation.
- As a KubeVirt developer, I can retain exclusive ownership of libvirt mutations and validate the plugin's runtime changes.

## Repos

- `kubevirt/kubevirt`

## Design

### Existing Domain Hook Extension

The existing advanced `MutateDomain` RPC is reused. Its request gains an optional `live_domain` field:

```protobuf
message MutateDomainRequest {
    string domain_type = 1;
    bytes domain = 2;
    bytes vmi = 3;
    SidecarContext sidecar_context = 4;
    bytes live_domain = 5;
}
```

`domain` contains the desired domain generated by KubeVirt before this hook runs. `live_domain` contains the active libvirt domain and is populated for runtime reconciliation. Both use the same lossless libvirt domain representation required by VEP 190.

`InvocationContext` gains:

```go
InvocationContextRuntime InvocationContext = "Runtime"
```

The response remains unchanged:

```protobuf
message MutateDomainResponse {
    bytes domain = 1;
}
```

The returned domain is the plugin-mutated desired domain. It is not applied directly. KubeVirt validates its delta and performs supported live changes through libvirt.

### Plugin CRD

An advanced sidecar domain hook opts into runtime invocation through an optional `runtime` block:

```yaml
domainHooks:
  - sidecar:
      socketPath: /var/run/kubevirt-plugin/nbs-vhost/hook.sock
    runtime:
      enabled: true
      migrationPolicy: RecreateOnTarget
    failureStrategy: Fail
    timeout: 30s
```

`migrationPolicy` has two values:

- `Block`: VMIs with live devices owned by the hook are not live migratable.
- `RecreateOnTarget`: the hook can recreate equivalent resources and XML on a migration target.

`Block` is the default.

Runtime invocation is limited to advanced sidecar hooks in alpha. Such hooks may need provider calls, inventory inspection, retries, and multiple validation steps that cannot be expressed by a pure CEL transformation.

Only one runtime-enabled domain hook may select a VMI in alpha. Other boot-only domain hooks may still apply.

### Plugin Device Ownership

Every runtime-managed disk has a stable plugin-scoped ID. The hook represents ownership through a deterministic alias:

```text
plugin-<plugin-name>-<device-id>
```

The exact encoding and maximum length are implementation details, but it must:

- be stable across boot, runtime reconciliation, and migration;
- identify the owning plugin;
- be unique within the VMI; and
- be recoverable from active domain XML.

KubeVirt validates or normalizes the corresponding libvirt alias. A runtime hook may add, retain, or remove only disks in its alias namespace. It may not modify KubeVirt-owned disks or disks owned by another hook.

### Runtime Reconciliation

When `virt-launcher` synchronizes a running VMI, it performs the following steps under its existing per-domain serialization:

1. Generate the base desired domain from the VMI.
2. Read the current active domain XML.
3. Invoke the runtime-enabled advanced domain hook with context `Runtime`.
4. Validate the returned domain.
5. Compare plugin-owned disks in the returned domain with plugin-owned disks in the active domain.
6. Detach disks absent from the returned domain.
7. Attach disks newly present in the returned domain.
8. Confirm the observed live state and requeue reconciliation when necessary.

Runtime disk synchronization is separate from the existing PVC hotplug classifier. A plugin-owned vhost-user disk does not need a path under KubeVirt's hotplug volume directory.

Alpha supports only addition and removal of:

```xml
<disk type='vhostuser' device='disk'>
  ...
</disk>
```

The target bus and other supported fields are validated by KubeVirt. Changing XML for an attached device while retaining the same alias is rejected. The device must be removed and added again.

KubeVirt remains the sole libvirt writer. The plugin sidecar does not receive a libvirt socket and must not invoke `virsh`, `AttachDeviceFlags`, or `DetachDeviceFlags`.

### Provider-resource Reconciliation

The advanced sidecar may reconcile external resources before returning its desired domain. It derives two sets:

- **desired devices:** plugin devices requested by the VMI metadata or another plugin-owned projection;
- **live devices:** plugin-owned aliases found in `live_domain`.

The plugin maintains provider resources for their union:

```text
required resources = resources(desired devices union live devices)
orphaned resources = owned inventory minus required resources
```

For a vhost-user storage provider:

| Desired | Live | Plugin action |
|---|---|---|
| no | no | Stop and remove any owned orphan endpoint |
| yes | no | Start or verify the endpoint before returning the disk |
| yes | yes | Retain and verify the endpoint |
| no | yes | Retain the endpoint while KubeVirt detaches the disk |

Provider reconciliation must be idempotent. If a desired endpoint is not ready, the hook returns an error and KubeVirt does not attempt the attach.

Provider inventory cannot exist only in sidecar process memory. Resources must be enumerable or deterministically attributable to at least the plugin, VMI UID, device ID, and node. This allows the plugin to recover after its sidecar or node service restarts.

The plugin may call the provider directly from its sidecar or use its own internal node service. That internal protocol is outside KubeVirt's API. Existing VEP 190 node hooks such as `PreVMStart` and `PostVMStop` remain available for whole-VM setup and cleanup; this VEP adds no node hook points.

The plugin must also garbage-collect resources for VMI UIDs that no longer exist. A forced VMI or pod deletion cannot guarantee that the domain hook or `PostVMStop` runs.

### Attach Sequence

For a newly desired disk:

1. The hook observes `desired=yes, live=no`.
2. It calls the provider's start or ensure operation.
3. It waits until the vhost-user socket is usable by QEMU.
4. It returns desired domain XML containing the disk.
5. KubeVirt validates and attaches the disk.
6. The next reconciliation observes `desired=yes, live=yes`.

If endpoint preparation succeeds but the hook or attach fails, the next reconciliation either retries the attach or removes the endpoint after the device is no longer desired. No KubeVirt operation journal is required.

### Detach Sequence

For a removed disk:

1. The hook observes `desired=no, live=yes`.
2. It retains the endpoint and returns desired XML without the disk.
3. KubeVirt requests live detach.
4. Until active XML no longer contains the disk, subsequent hook calls continue to observe `live=yes` and retain the endpoint.
5. After libvirt confirms removal, the next reconciliation observes `desired=no, live=no`.
6. The hook calls the provider's stop operation and removes the socket.

Libvirt detach may be asynchronous. A successful detach API call is not treated as proof that QEMU released the device; active XML or the libvirt device-removed event determines the live set.

If KubeVirt or the sidecar restarts after detach but before cleanup, periodic reconciliation finds the endpoint in plugin inventory but the device in neither desired nor live state, and removes it.

### Failure Semantics

Runtime hooks require `failureStrategy: Fail` in alpha.

If the runtime hook times out or fails:

- KubeVirt does not interpret the unmutated base domain as a request to remove plugin devices;
- no plugin-owned attach or detach is attempted;
- existing live plugin devices remain attached; and
- normal work-queue reconciliation retries the operation.

Provider operations must be idempotent because a timeout does not prove that the provider did not complete the request.

If KubeVirt receives invalid XML or a mutation outside the plugin's permitted alias namespace, it rejects the complete runtime mutation before changing libvirt.

### Launcher Preparation

A plugin must select the VMI before its launcher pod is created. Admission remains responsible for injecting:

- the advanced domain-hook sidecar;
- the shared hook socket;
- any host socket directory;
- required network access; and
- immutable domain prerequisites such as shared `memfd` memory backing.

An arbitrary running VMI cannot begin using a plugin when doing so requires pod or immutable domain changes. A hotplug-capable VMI may start with the plugin enabled and zero provider disks.

### Live Migration

Runtime hotplug and live migration are serialized, but plugin-owned disks do not inherently make a VMI non-migratable.

Migration is allowed with `migrationPolicy: RecreateOnTarget` when:

- the desired plugin disk set equals the source live plugin disk set;
- no attach or detach is pending;
- device IDs and aliases are stable;
- the plugin prevents or queues desired-state changes while migration is active; and
- the provider supports the required source/target access overlap or handoff.

The migration sequence is:

1. Immediately before migration, KubeVirt verifies that runtime reconciliation is converged.
2. The plugin freezes its desired disk projection for the migration.
3. On the target, the existing `MigrationTarget` domain-hook context starts or verifies target endpoints and returns XML with target-local socket paths.
4. Source QEMU continues using source endpoints while migration is active.
5. Target QEMU starts with the same logical disks and aliases.
6. After success, source whole-VM cleanup or plugin garbage collection removes source endpoints.
7. After failure or cancellation, target whole-VM cleanup or plugin garbage collection removes target endpoints.
8. Runtime reconciliation resumes after migration reaches a terminal state.

The existing VEP 190 migration hook points may be used for additional whole-node preparation and verification. This VEP does not add migration hook points.

With `migrationPolicy: Block`, or when convergence and provider requirements are not satisfied, KubeVirt reports the VMI as not live migratable.

### Observability

KubeVirt emits events and metrics for:

- runtime hook failures and timeouts;
- rejected runtime mutations;
- successful and failed plugin-disk attaches;
- successful and failed plugin-disk detaches; and
- migration blocked by an unconverged or unsupported runtime hook.

Provider-specific progress, endpoint IDs, and cleanup errors belong in the plugin's own CRD status, logs, and metrics. They are not duplicated into a generic VMI runtime-device status.

## API Examples

### Plugin

```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: nbs-vhost
spec:
  condition: 'has(vmi.metadata.annotations) && "nbs.example.io/enabled" in vmi.metadata.annotations'
  failureStrategy: Fail

  domainHooks:
    - sidecar:
        socketPath: /var/run/kubevirt-plugin/nbs-vhost/hook.sock
      runtime:
        enabled: true
        migrationPolicy: RecreateOnTarget
      failureStrategy: Fail
      timeout: 30s

  nodeHooks:
    - socket: /var/run/kubevirt/plugins/nbs-vhost.sock
      permittedHooks:
        - PostVMStop
      failureStrategy: Fail
      timeout: 45s

  mutatingAdmissionWebhooks:
    - name: nbs-vhost
```

`PostVMStop` is optional fast cleanup. The plugin remains responsible for inventory-based garbage collection if it is not delivered.

### Provider-owned Desired State

```yaml
apiVersion: storage.example.io/v1alpha1
kind: VhostDiskAttachment
metadata:
  name: database-disks
spec:
  vmiName: database
  disks:
    - id: data
      diskID: volume-7f31
      target: vdb
      queues: 4
```

The provider controller validates this object and projects the relevant state into VMI metadata. KubeVirt does not read the provider CRD or require RBAC for it.

### Runtime Reconciliation Example

```text
attachment updated
    |
    v
VMI metadata updated
    |
    v
virt-launcher SyncVMI
    |
    +--> read desired domain and live domain
    |
    +--> MutateDomain(context=Runtime)
    |       |
    |       +--> StartEndpoint / retain / StopEndpoint
    |       +--> return desired domain XML
    |
    +--> validate plugin-owned disk delta
    +--> libvirt attach or detach
    +--> requeue until converged
```

## Alternatives

### Separate Runtime-device Framework

A separate hook type could return a typed device collection and coordinate with a new node resource hook through a plan/apply protocol. This provides a broad foundation for arbitrary devices, but duplicates parts of the existing domain-hook mechanism and significantly increases the KubeVirt API and controller surface before additional use cases are demonstrated.

### Four Device Lifecycle Node Hooks

`PreDeviceAttach`, `PostDeviceAttach`, `PreDeviceDetach`, and `PostDeviceDetach` express operation edges clearly. However, they still require a durable operation journal or plugin inventory to recover a callback lost after detach. They also do not return device XML. The proposed runtime domain hook provides both desired XML and level-triggered recovery with less KubeVirt API.

### KubeVirt-owned Runtime-device Status

KubeVirt could persist every operation phase in VMI status. This gives generic progress reporting but creates a public API and distributed transaction state for provider resources that KubeVirt does not understand. The proposal instead requires provider-owned inventory and idempotent reconciliation.

### Let the Plugin Call Libvirt

Giving the sidecar access to libvirt avoids changes to `SyncVMI`, but creates multiple unsynchronized domain writers and bypasses KubeVirt validation, locking, status, shutdown, and migration coordination. This is rejected.

### Reuse PVC-based Hotplug

A provider that naturally exposes a Kubernetes volume should continue to use CSI and the existing PVC hotplug flow. Requiring an endpoint-oriented provider to manufacture a PVC solely to enter that flow adds an unrelated abstraction and an attachment pod.

### Add an In-tree Provider Volume Source

An in-tree source gives KubeVirt full knowledge of one provider but couples the KubeVirt API and release cycle to that provider. It does not satisfy the structured-plugin goal.

## Scalability

Each selected runtime hook adds one sidecar RPC to relevant `SyncVMI` reconciliations. Unchanged desired/live sets do not produce libvirt operations, although the plugin must periodically inspect its inventory to collect orphans.

Runtime mutations and device counts are bounded by KubeVirt limits. No per-device VMI status is added. Metrics must not use plugin device IDs, VMI names, or namespaces as labels.

The sidecar must make no-change reconciliation inexpensive. KubeVirt applies normal timeout and work-queue backoff behavior to a failing hook.

## Update/Rollback Compatibility

The API is guarded by the existing alpha `Plugins` feature gate. Older KubeVirt versions reject or ignore the new `runtime` CRD field according to normal CRD compatibility rules and do not invoke `Runtime`.

During upgrade, live plugin disks are recoverable from deterministic aliases in active XML. Provider resources are recoverable from plugin-owned inventory. The hook protocol follows VEP 190 version negotiation.

Before disabling the feature or downgrading to a version without runtime support, operators must remove runtime disks or stop affected VMIs. Disabling the feature does not implicitly detach disks from running QEMU processes.

The plugin should block its own removal while provider inventory is nonempty. Forced plugin removal may require VM shutdown and manual provider cleanup.

## Functional Testing Approach

### Unit Tests

- CRD validation for runtime enablement and migration policy.
- Protobuf compatibility for the optional `live_domain` field.
- Runtime alias ownership and collision validation.
- Desired/live disk diff and lossless vhost-user XML tests.
- Rejection of non-disk, non-vhost-user, and in-place runtime mutations.
- Failure tests proving hook errors never become implicit detach requests.
- Provider desired/live union and orphan-detection tests in the reference plugin.
- Migration convergence and policy tests.

### Component Tests

- Attach and asynchronous detach through fake libvirt.
- Hook timeout after provider success followed by idempotent retry.
- `virt-launcher` restart before and after attach or detach.
- Sidecar restart with provider inventory reconstruction.
- Stale VMI updates converging on the next reconciliation.
- Invalid or out-of-namespace XML rejected before libvirt mutation.

### End-to-end Tests

- Attach and detach a vhost-user test disk without a PVC or attachment pod.
- Perform guest I/O through the attached disk.
- Verify the endpoint exists before attach and remains until confirmed detach.
- Crash after detach but before cleanup and verify orphan collection.
- Delete or stop a VMI and verify whole-VM garbage collection.
- Migrate a converged disk set successfully.
- Reject migration while attach or detach is pending.
- Reject or defer desired-state changes during migration.
- Verify source cleanup after success and target cleanup after failure.

## Implementation History

TBD

## Graduation Requirements

### Alpha

- [ ] All changes are guarded by the `Plugins` feature gate.
- [ ] Advanced domain hooks support the `Runtime` invocation context and `live_domain`.
- [ ] Runtime mutation supports plugin-owned vhost-user disk addition and removal.
- [ ] KubeVirt exclusively performs libvirt attach and detach.
- [ ] Hook failure cannot implicitly detach a live plugin disk.
- [ ] Device aliases provide stable ownership across restart and migration.
- [ ] The reference plugin reconstructs inventory and removes orphaned endpoints.
- [ ] Concurrent hotplug and migration is rejected or deferred.
- [ ] At least one reference plugin demonstrates no-PVC attach and detach.

### Beta

- [ ] API feedback from at least two runtime domain-hook implementations is incorporated.
- [ ] Upgrade and rollback are tested with attached plugin disks.
- [ ] Successful, failed, and cancelled migrations are covered.
- [ ] Metrics, events, and troubleshooting documentation are complete.
- [ ] Scale and soak testing covers supported VM and disk counts.
- [ ] No unresolved security issues exist in runtime XML validation or sidecar authorization.

#### On-By-Default Readiness

- [ ] VMIs without runtime hooks observe no behavior or performance regression.
- [ ] A failing hook cannot remove a working device.
- [ ] Provider resource leaks are detectable and recoverable.
- [ ] Shutdown and migration cleanup behavior is deterministic.

### GA

- [ ] Runtime domain-hook API and compatibility rules are stable.
- [ ] At least two releases of beta feedback have been addressed.
- [ ] Upgrade, downgrade, migration, and recovery tests consistently pass.
