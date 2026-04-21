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

Hotplug support and full live migration support are not part of the initial contract proposed here. The first-class outcome targeted by this VEP is a clear API and runtime contract for non-hotplug VM disks backed by a PVC and exposed to QEMU through vhost-user.

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
- Guard the feature behind a feature gate during Alpha and Beta.
- Keep non-vhost disk behavior unchanged.

## Non Goals

- Standardizing backend-specific control planes or CSI endpoint lifecycle behavior.
- Requiring a specific sidecar implementation.
- Redesigning the whole disk subsystem.
- Supporting standalone vhost-user sources with no PVC/PV behind them.
- Delivering full live migration orchestration for vhost-user disks in the initial implementation.
- Supporting hotplugging of such volumes in the first implementation increment.

## Definition of Users

- KubeVirt contributors implementing storage/runtime features.
- Cluster operators enabling advanced disk backends.
- Platform teams integrating socket-backed block backends.
- VM users requiring vhost-user-backed disk devices.

## User Stories

1. As a cluster operator, I want to define a PVC-backed vhost-user disk using an explicit KubeVirt API contract.
2. As a VM user, I want this disk to appear as a regular virtio disk in guest while backend connectivity is handled through vhost-user.
3. As a contributor, I want API validation and converter behavior to be explicit and testable.

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
- Avoid exposing KubeVirt-private mount paths in the user-facing API.

### Scope split

Primary scope (this VEP):

- General upstream support for PVC-backed vhost-user disks.
- API representation of vhost-user source, socket location, number of queues, and reconnect timeout.
- Runtime conversion to libvirt `type='vhostuser'` disk XML.
- Validation and tests for core non-hotplug behavior.

Secondary scope (follow-on work):

- Hotplug support using the same volume-based API model.
- Live migration support with explicit target-side backend/socket handoff semantics.

### Proposed model

The proposed API uses a dedicated `VolumeSource`:

- `volumes[].vhostUser.claimName`: PVC backing the vhost-user disk.
- `volumes[].vhostUser.type`: backend kind. Currently only `blk` is supported.
- `volumes[].vhostUser.socket.path`: path to the Unix socket, relative to the root of the mounted PVC.
- `volumes[].vhostUser.reconnectTimeoutSeconds`: how long QEMU should keep retrying reconnects.
- `volumes[].vhostUser.queues`: optional queue count.

The matching disk remains a regular virtio disk attachment:

- `disks[].name`: matches the volume name.
- `disks[].disk.bus`: must be `virtio`.

This means the API contract is effectively:

- volume source kind: `vhostUser`
- PVC identity: `claimName`
- socket location relative to volume root: `socket.path`
- reconnect timeout: `reconnectTimeoutSeconds`
- optional queue count: `queues`

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
    Type                    VhostUserDiskType `json:"type,omitempty"`
    Socket                  VhostUserSocket `json:"socket"`
    Queues                  *uint           `json:"queues,omitempty"`
    ReconnectTimeoutSeconds *uint           `json:"reconnectTimeoutSeconds,omitempty"`
}

type VhostUserSocket struct {
    Path string `json:"path"`
}
```

The matching disk continues to use existing disk structs. No vhost-user-specific field is added to `Disk`; instead, vhost-user behavior is inferred from the matching volume source and constrained by disk validation rules.

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

### Why `VolumeSource`

The design uses `VolumeSource.vhostUser`.

This aligns with the implementation constraints and intended storage model:

- the feature assumes there is a PVC/PV backing the disk,
- the socket path is resolved relative to the mounted PVC root inside `virt-launcher`, and
- KubeVirt already reasons about storage identity and lifecycle primarily at the volume layer.

This is also a good fit for future work around migration semantics, even though this proposal does not yet implement dedicated live migration support.

### Feature gate

Proposed gate name: `VhostUserDisks`.

- Alpha/Beta: gate required.
- GA: gate removed.

### Reconnect semantics

The API exposes only `reconnectTimeoutSeconds`.

At runtime, reconnect is always enabled in the emitted libvirt XML. The API does not currently include a separate boolean to disable reconnect.

### Socket path semantics

The API does not expose a pod-visible absolute socket path.

Instead, `socket.path` is interpreted relative to the root of the mounted PVC backing the volume. KubeVirt resolves that relative path to the launcher-visible path internally when rendering the libvirt domain.

This avoids leaking KubeVirt-private mount paths such as `/run/kubevirt-private/...` into user manifests.

### Shared memory requirement

`vhost-user-blk` requires shared guest memory.

The PoC therefore enables shared `memfd`-backed memory for VMIs that use vhost-user disks, similarly to the existing virtiofs shared-memory handling.

### Migration note

This proposal does not add dedicated live migration support for vhost-user disks.

It reuses existing volume-based migratability checks, but it does not yet define or implement the target-side backend/socket handoff needed for vhost-user as a fully supported migratable storage type.

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
      type: blk
      socket:
        path: nbs.sock
      reconnectTimeoutSeconds: 1
      queues: 4
```

### Example 2: Resulting libvirt disk intent

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
- Cons: storage identity and lifecycle remain anchored elsewhere, which makes the PVC-backed socket contract and future migration semantics harder to express cleanly.
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
- Multi-disk scenarios and queue count variations.

### Deferred tests

- Live migration with explicit vhost-user backend handoff.
- Hotplug add/remove volume flows using the same model.
- Reconnect behavior when socket endpoints are recreated during hotplug flows.

## Implementation History

[TBD]

## Graduation Requirements

### Alpha

- [ ] Feature gate `VhostUserDisks` guards all new behavior.
- [ ] API fields for PVC-backed vhost-user disks are defined and validated.
- [ ] Converter/runtime support for non-hotplug vhost-user-backed disks is merged.
- [ ] Unit tests for API + converter behavior are merged.
- [ ] Initial integration tests for non-hotplug lifecycle are merged.
- [ ] User docs for feature-gated base support are merged.

### Beta

- [ ] API shape is stable, with only additive changes needed.
- [ ] Reliability and scaling tests are expanded and stable.
- [ ] Known critical issues are resolved or documented with mitigations.

### GA

- [ ] Feature gate removed.
- [ ] API and behavior are finalized and backward-compatible.
- [ ] Full documentation and operational guidance are complete.
- [ ] Long-running confidence and community adoption evidence are available.
- [ ] Relevant SIGs sign off on graduation.
