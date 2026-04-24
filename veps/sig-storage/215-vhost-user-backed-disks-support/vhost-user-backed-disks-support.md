# VEP #215: vhost-user backed disks support

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This enhancement proposes first-class upstream support for PVC-backed vhost-user disks in KubeVirt.

This proposal models vhost-user as a `VolumeSource`. The intent is to keep the storage identity and lifecycle at the volume layer, while still presenting the disk to the guest as a regular virtio block device whose dataplane is provided over a Unix socket (for example, `vhost-user-blk`).

This VEP covers both regular attachment and declarative hotplug for VM disks backed by a PVC and exposed to QEMU through vhost-user. Live migration behavior is expected to follow the same PVC-backed volume semantics KubeVirt already relies on for other storage types.

## Motivation

KubeVirt currently has strong support for file and block disk sources, but support for vhost-user-backed disks is not a first-class, upstreamed feature.

In practice, users rely on downstream/custom integrations, annotation contracts, and attach-path overrides. This makes behavior hard to validate and hard to evolve upstream. At [Nebius](https://nebius.com/) we encountered this directly while implementing [NBS](https://github.com/ydb-platform/nbs/tree/main/doc)-backed vhost-user disks.

Upstream support is needed to:

- Provide a stable API contract for vhost-user-backed disk sources.
- Remove reliance on vendor-specific logic in core flows.
- Standardize runtime behavior and validation for vhost-user disk attachment.
- Create a clean baseline that can support PVC-backed vhost-user integrations.

## Goals

- Add first-class API semantics for PVC-backed vhost-user disks.
- Represent vhost-user as a `VolumeSource`.
- Map the API to libvirt/QEMU domain configuration in a well-defined way.
- Introduce validation rules for supported vhost-user disk options.
- Support declarative hotplug of vhost-user volumes through the existing declarative volume hotplug model.
- Guard the feature behind a feature gate during Alpha and Beta.
- Keep non-vhost disk behavior unchanged.

## Non Goals

- Standardizing backend-specific control planes or CSI endpoint lifecycle behavior.
- Requiring a specific sidecar implementation.
- Redesigning the whole disk subsystem.
- Supporting standalone vhost-user sources with no PVC/PV behind them.
- Adding vhost-user-specific live migration orchestration or provider contracts beyond existing PVC-backed volume semantics.

## Definition of Users

- KubeVirt contributors implementing storage/runtime features.
- Cluster operators enabling advanced disk backends.
- Platform teams integrating socket-backed block backends.
- VM users requiring vhost-user-backed disk devices.

## User Stories

1. As a cluster operator, I want to define a PVC-backed vhost-user disk using an explicit KubeVirt API contract.
2. As a VM user, I want this disk to appear as a regular virtio disk in guest while backend connectivity is handled through vhost-user.
3. As a contributor, I want API validation and converter behavior to be explicit and testable.
4. As an operator, I want the same volume source to work for declarative hotplug so VM spec changes can add or remove vhost-user volumes consistently with other declarative hotplug storage types.

## Repos

- `kubevirt/enhancements`
  - VEP tracker and VEP doc.
- `kubevirt/kubevirt`
  - API validation, feature gate, converter/launcher/handler/controller changes, unit/integration tests.
- `kubevirt/user-guide`
  - User-facing docs, constraints, and examples.

## Design

### Design principles

- Define vhost-user disk semantics as first-class API, not annotation conventions.
- Keep storage identity and lifecycle at the volume layer.
- Keep guest-facing disk model aligned with existing virtio expectations.
- Keep runtime mapping deterministic and observable.

### Scope split

Primary scope (this VEP):

- General upstream support for PVC-backed vhost-user disks.
- API representation of vhost-user source, socket location, number of queues, reconnect timeout, and declarative hotplug intent.
- Runtime conversion to libvirt `type='vhostuser'` disk XML.
- Declarative hotplug support using the existing spec-driven hotplug flow.
- Validation and tests for both regular and declarative-hotplug behavior.

Secondary scope (follow-on work):

- Validation of live migration behavior using the existing PVC-backed volume model.

### Proposed model

The proposed API uses a dedicated `VolumeSource`:

- `volumes[].vhostUser.claimName`: PVC backing the vhost-user disk.
- `volumes[].vhostUser.socket.path`: path to the Unix socket, relative to the root of the mounted PVC.
- `volumes[].vhostUser.reconnectTimeoutSeconds`: how long QEMU should keep retrying reconnects.
- `volumes[].vhostUser.queues`: optional queue count.
- `volumes[].vhostUser.hotpluggable`: indicates that the volume may participate in declarative hotplug flows.

The matching disk remains a regular virtio disk attachment:

- `disks[].name`: matches the volume name.
- `disks[].disk.bus`: must be `virtio`.

This means the API contract is effectively:

- volume source kind: `vhostUser`
- PVC identity: `claimName`
- socket location relative to volume root: `socket.path`
- reconnect timeout: `reconnectTimeoutSeconds`
- optional queue count: `queues`
- declarative hotplug intent: `hotpluggable`

The runtime mapping should produce:

- disk `type='vhostuser'`
- source `type='unix'`
- socket path resolved internally from the mounted PVC root and `socket.path`
- reconnect stanza with reconnect enabled
- target bus `virtio`

### Go API sketch

The API is expected to look conceptually like this:

```go
type VolumeSource struct {
    // ... existing fields omitted
    VhostUser *VhostUserVolumeSource `json:"vhostUser,omitempty"`
}

type VhostUserVolumeSource struct {
    ClaimName               string          `json:"claimName"`
    Socket                  VhostUserSocket `json:"socket"`
    Queues                  *uint           `json:"queues,omitempty"`
    ReconnectTimeoutSeconds *uint           `json:"reconnectTimeoutSeconds,omitempty"`
    Hotpluggable            bool            `json:"hotpluggable,omitempty"`
}

type VhostUserSocket struct {
    Path string `json:"path"`
}
```

The matching disk continues to use existing disk structs. No vhost-user-specific field is added to `Disk`; instead, vhost-user behavior is inferred from the matching volume source and constrained by disk validation rules.

For declarative hotplug, `hotpluggable` follows the same role that it already has for PVC/DataVolume-backed storage: it marks the volume as eligible for spec-driven add/remove handling when `DeclarativeHotplugVolumes` feature gate is enabled.

### Disk field applicability and validation

A vhost-user volume still requires a matching entry in `spec.domain.devices.disks`, but only a subset of existing disk configuration is meaningful for this transport.

Required constraints:

- `disks[].name` must match the `volumes[].name`.
- `disks[].disk.bus` must be `virtio`.
- the disk must use the regular `disk` target, not `lun` or `cdrom`.

Fields that do not apply to vhost-user disks should be rejected by validation rather than silently ignored. This includes at least:

- `cache`
- `io`
- `errorPolicy`
- `blockSize`

If additional disk options are found to be unsupported by the libvirt/QEMU vhost-user path, they should be treated the same way: explicitly documented and rejected during admission.

For declarative hotplug, validation must also ensure that a hotpluggable `vhostUser` volume is PVC-backed, uses a matching `virtio` disk entry, and only participates in the declarative VM-spec-driven hotplug path.

### Why `VolumeSource`

The design uses `VolumeSource.vhostUser`.

This aligns with the implementation constraints and intended storage model:

- the feature assumes there is a PVC/PV backing the disk,
- the socket path is resolved relative to the mounted PVC root inside `virt-launcher`, and
- KubeVirt already reasons about storage identity and lifecycle primarily at the volume layer.

This is also a good fit for migration semantics because it keeps vhost-user disks anchored to the same PVC-backed storage model KubeVirt already uses for other volumes.

It is also compatible with the declarative hotplug direction in KubeVirt, because the source of truth remains `spec.volumes` and the hotplug controller can reason about `vhostUser` volumes the same way it already reasons about other PVC-backed hotpluggable sources once the source type is added to the declarative hotplug checks.

### Feature gates

Proposed gate name for this feature: `VhostUserVolumes`.

- Alpha/Beta: `VhostUserVolumes` gate required.
- Declarative hotplug behavior additionally depends on `DeclarativeHotplugVolumes` while that feature remains gated upstream.
- GA: `VhostUserVolumes` gate removed.

### Reconnect semantics

The API exposes only `reconnectTimeoutSeconds`.

At runtime, reconnect is always enabled in the emitted libvirt XML. The API does not currently include a separate boolean to disable reconnect.

### Socket path semantics

`socket.path` is interpreted relative to the root of the mounted PVC backing the volume. KubeVirt resolves that relative path to the launcher-visible path internally when rendering the libvirt domain.

Validation must reject absolute paths, symlinks, and path traversal outside the mounted volume root, so the contract remains volume-relative and does not permit references to arbitrary filesystem locations.

### Shared memory requirement

`vhost-user-blk` requires the QEMU process and the external vhost-user backend to access the same guest memory. Without shared guest memory, libvirt rejects the domain configuration with an error similar to `'vhostuser' requires shared memory`.

For VMIs that use vhost-user disks, KubeVirt configures the domain memory backing as shared and memfd-backed:

```xml
<memoryBacking>
  <source type='memfd'/>
  <access mode='shared'/>
</memoryBacking>
```

### Declarative hotplug note

This VEP includes declarative hotplug support for `vhostUser` volumes. The intent is that VM or VMI spec changes can add or remove a hotpluggable `vhostUser` volume using the same declarative reconciliation model as other hotpluggable storage sources.

This requires extending the declarative hotplug source checks to recognize `VolumeSource.vhostUser`, teaching the hotplug controller to treat `claimName` as the backing PVC identity, and carrying the same disk-side validation rules into hotplug reconciliation.

### Migration note

This proposal does not introduce any vhost-user-specific migration API, controller flow, or readiness contract.

Migration is expected to follow the same rules KubeVirt already applies to PVC-backed volumes. If the backing PVC is considered live-migratable by existing KubeVirt checks, and the storage provider exposes the configured `socket.path` when the volume is mounted in the target launcher pod, then migration should work without additional KubeVirt-side orchestration.

The socket lifecycle remains the responsibility of the storage provider or CSI integration, not KubeVirt. This VEP therefore does not define provider internals for migration; it only relies on the existing expectation that a mounted PVC exposes the data and endpoints required by the workload.

## API Examples

The examples below are illustrative and not final schema.

### Example 1: VMI with a PVC-backed vhost-user disk

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vm-vhost-user
spec:
  domain:
    devices:
      disks:
      - name: data0
        disk:
          bus: virtio
  volumes:
  - name: data0
    vhostUser:
      claimName: pvc-data0
      socket:
        path: nbs.sock
      reconnectTimeoutSeconds: 1
      queues: 4
```

### Example 2: VM with a declaratively hotpluggable vhost-user volume

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm-vhost-user-hotplug
spec:
  runStrategy: Always
  template:
    spec:
      domain:
        devices:
          disks:
          - name: data0
            disk:
              bus: virtio
      volumes:
      - name: data0
        vhostUser:
          claimName: pvc-data0
          socket:
            path: nbs.sock
          reconnectTimeoutSeconds: 1
          hotpluggable: true
```

When `DeclarativeHotplugVolumes` is enabled, adding or removing this volume from the VM template is expected to follow the same declarative reconciliation model used for other hotpluggable storage volumes.

### Example 3: Resulting libvirt disk intent

```xml
<disk type='vhostuser' device='disk'>
  <driver name='qemu' type='raw' queues='4'/>
  <source type='unix' path='/run/kubevirt-private/vmi-disks/data0/nbs.sock'>
    <reconnect enabled='yes' timeout='1'/>
  </source>
  <target bus='virtio' dev='vdb'/>
</disk>
```

## Alternatives

### Alternative A: keep annotation-driven behavior

- Pros: low immediate effort in downstream distributions.
- Cons: weak API discoverability, harder validation, not an upstream-quality contract.

### Alternative B: disk-level backend fields

- Pros: smaller API delta and easy to explain as a disk transport override.
- Cons: storage identity and lifecycle remain anchored elsewhere, which makes the PVC-backed socket contract and future declarative hotplug and migration semantics harder to express cleanly.
- Rejected in favor of `VolumeSource.vhostUser`.

### Alternative C: standalone vhost-user source with no PVC/PV

- Pros: more generic on paper.
- Cons: does not match the implementation assumptions in this proposal, which require PVC-backed storage and socket resolution relative to the mounted volume root.
- Deferred unless there is a concrete upstream use case that cannot be represented with the PVC-backed model.

## Scalability

No new control-plane objects are required.

Scalability considerations:

- Control-plane load remains aligned with current disk reconciliation flows.
- Runtime overhead is proportional to number of attached vhost-user disks.
- Queue configuration directly affects CPU consumption and throughput characteristics.

Testing should include multiple disks per VMI and queue scaling scenarios.

## Update/Rollback Compatibility

- Feature gate protects rollout in Alpha/Beta.
- Existing disk types and behavior remain unchanged when gate is disabled.

Upgrade:

- Gate can be enabled without impacting existing workloads.
- New API fields are opt-in.

Rollback:

- New requests are rejected when gate is disabled.
- Existing running workloads should continue to run, with operational guidance documented for restart behavior.

API stability:

- Alpha: schema can evolve based on review.
- Beta: additive-compatible changes only.
- GA: stable API semantics.

## Functional Testing Approach

### Unit tests

- API validation for `VolumeSource.vhostUser`, disk bus requirements, queue count, relative socket path, and reconnect timeout.
- Converter mapping to `vhostuser` disk XML.
- Relative socket path resolution from mounted PVC root.
- Shared memory enablement for VMIs using vhost-user disks.

### Integration tests

- Non-hotplug lifecycle with a PVC-backed vhost-user disk.
- VM start/stop/restart behavior with a vhost-user-backed disk.
- Declarative hotplug add/remove flows for hotpluggable `vhostUser` volumes.
- Reconnect behavior when socket endpoints are recreated during declarative hotplug flows.
- Multi-disk scenarios and queue count variations.
- Live migration coverage for RWX PVC-backed vhost-user volumes under the existing migratability rules.

For integration with existing KubeVirt CI, a lightweight [NBS](https://github.com/ydb-platform/nbs) setup could be used as a storage provider.

## Implementation History

[TBD?]

## Graduation Requirements

### Alpha

- [ ] Feature gate `VhostUserVolumes` guards all new behavior.
- [ ] API fields for PVC-backed vhost-user disks are defined and validated.
- [ ] Converter/runtime support for vhost-user-backed disks is merged.
- [ ] Unit tests for API validation, XML generation, PVC mount wiring, and socket path resolution are merged.
- [ ] User docs for feature-gated base support are merged.

### Beta

- [ ] API shape is stable, with only additive changes needed.
- [ ] Integration tests are merged, including:
  - [ ] Full guest IO e2e's.
  - [ ] Hotplug e2e's.
  - [ ] Live migration e2e's.
- [ ] Known critical issues are resolved or documented with mitigations.

### GA

- [ ] Feature gate removed.
- [ ] API and behavior are finalized and backward-compatible.
- [ ] Full documentation and operational guidance are complete.
- [ ] Long-running confidence and community adoption evidence are available.
- [ ] Relevant SIGs sign off on graduation.
