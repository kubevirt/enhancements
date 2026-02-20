# VEP #215: vhost-user backed disks support

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This enhancement proposes first-class upstream support for vhost-user backed disks in KubeVirt.

The primary objective is to add a clear, upstream API and runtime contract for disk devices whose dataplane is provided over a Unix socket (for example, `vhost-user-blk`).

Hotplug support is in scope as an extension of this base capability, but it is not the primary motivation of this VEP. The first-class outcome is a general disk model that works for regular disk attachment and can be extended to hotplug in a consistent way.

## Motivation

KubeVirt currently has strong support for file and block disk sources, but support for vhost-user-backed disks is not a first-class, upstreamed feature.

In practice, users rely on downstream/custom integrations, annotation contracts, and attach-path overrides. This makes behavior hard to validate and hard to evolve upstream. At [Nebius](https://nebius.com/) we encountered this directly while implementing [NBS](https://github.com/ydb-platform/nbs/tree/main/doc)-backed vhost-user disks and hotplug behavior.

Upstream support is needed to:

- Provide a stable API contract for vhost-user disk sources.
- Remove reliance on vendor-specific logic in core flows.
- Standardize runtime behavior and validation for vhost-user disk attachment.
- Create a clean baseline that can support both static and hotplug workflows.

## Goals

- Add first-class API semantics for vhost-user-backed disks.
- Map the API to libvirt/QEMU domain configuration in a well-defined way.
- Introduce validation rules for supported vhost-user disk options.
- Guard the feature behind a feature gate during Alpha and Beta.
- Keep non-vhost disk behavior unchanged.
- Provide a clean path to support vhost-user disk hotplug as a secondary step.

## Non Goals

- Standardizing backend-specific control planes or CSI endpoint lifecycle behavior.
- Requiring a specific sidecar implementation.
- Redesigning the whole disk subsystem.
- Solving all hotplug-specific corner cases in the first implementation increment.

## Definition of Users

- KubeVirt contributors implementing storage/runtime features.
- Cluster operators enabling advanced disk backends.
- Platform teams integrating socket-backed block backends.
- VM users requiring vhost-user-backed disk devices.

## User Stories

1. As a cluster operator, I want to define a vhost-user-backed disk using an explicit KubeVirt API contract.
2. As a VM user, I want this disk to appear as a regular virtio disk in guest while backend connectivity is handled through vhost-user.
3. As a contributor, I want API validation and converter behavior to be explicit and testable.
4. As an operator, I want hotplug behavior to follow naturally once base vhost-user disk semantics are upstreamed.

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
- Keep guest-facing disk model aligned with existing virtio expectations.
- Keep runtime mapping deterministic and observable.
- Make hotplug support an incremental extension over the same disk model.

### Scope split

Primary scope (this VEP):

- General upstream support for vhost-user-backed disks.
- API representation of vhost-user source and reconnect policy.
- Runtime conversion to libvirt `type='vhostuser'` disk XML.
- Validation and tests for core non-hotplug behavior.

Secondary scope (follow-on within same VEP phases):

- Support vhost-user-backed disk hotplug using the same API model.
- Add/extend attach-path behavior where hotplug-specific handling is required.

### Proposed model

The API should represent, at minimum:

- backend kind: `vhostUserBlk` (name to be finalized in review)
- unix socket source path
- reconnect policy
- optional queue count

The runtime mapping should produce:

- disk `type='vhostuser'`
- source `type='unix'` and socket path
- reconnect stanza
- target bus `virtio`

### Feature gate

Proposed gate name: `VhostUserDisks`.

- Alpha/Beta: gate required.
- GA: gate removed.

### Hotplug note

Hotplug is secondary in this VEP, but we already have a concrete implementation path proving the model:

- Hotplug requests can use the same vhost-user disk semantics as regular disks (bus/backend/reconnect/queues), with API and CLI validation adapted accordingly.
- `virt-controller` helper pod rendering must preserve mounts for already-attached filesystem hotplug PVCs so helper pod restarts do not silently drop active disks.
- `virt-handler` must propagate socket-backed disks as directory mounts (not single socket-file bind mounts) to avoid stale inode pinning and allow reconnect after backend socket recreation.
- `virt-launcher` attach-time conversion must produce `vhostuser` disk XML for hotplug deltas, while `SyncVMI` source identity must include path-based sources to avoid repeated attach loops.
- Attach-time handling must account for `vhost-user-blk` limitations (for example cache/IOMode/error-policy behavior) consistently with non-hotplug vhost-user disks.

So hotplug should reuse the same first-class vhost-user disk contract, with only hotplug-specific orchestration hardening on top.

## API Examples

The examples below are illustrative and not final schema.

### Example 1: VMI with a vhost-user-backed disk (non-hotplug)

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
        backend:
          type: vhostUserBlk
          unixSocket:
            path: /var/run/kubevirt-private/vhost-user/data0.sock
            reconnect:
              enabled: true
              timeoutSeconds: 1
          queues: 4
  volumes:
  - name: data0
    persistentVolumeClaim:
      claimName: pvc-data0
```

### Example 2: Optional AddVolume request using same backend semantics

```yaml
apiVersion: subresources.kubevirt.io/v1
kind: AddVolumeOptions
name: hp-data0
disk:
  disk:
    bus: virtio
  backend:
    type: vhostUserBlk
    unixSocket:
      path: /var/run/kubevirt/hotplug-disks/hp-data0/nbs.sock
      reconnect:
        enabled: true
        timeoutSeconds: 1
    queues: 4
volumeSource:
  persistentVolumeClaim:
    claimName: pvc-hp-data0
    hotpluggable: true
```

### Example 3: Resulting libvirt disk intent

```xml
<disk type='vhostuser' device='disk'>
  <driver name='qemu' type='raw' queues='4'/>
  <source type='unix' path='/var/run/kubevirt-private/vhost-user/data0.sock'>
    <reconnect enabled='yes' timeout='1'/>
  </source>
  <target bus='virtio' dev='vdb'/>
</disk>
```

## Alternatives

### Alternative A: keep annotation-driven behavior

- Pros: low immediate effort in downstream distributions.
- Cons: weak API discoverability, harder validation, not an upstream-quality contract.

### Alternative B: support hotplug-only path first

- Pros: addresses one user-visible workflow quickly.
- Cons: leaves the underlying disk model implicit and fragmented.
- Rejected because base support should be primary and hotplug should build on it.

### Alternative C: backend-specific one-off fields

- Pros: quick for one backend.
- Cons: poor extensibility.

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
- Existing running workloads should continue to run, with operational guidance documented for restart/reattach behavior.

API stability:

- Alpha: schema can evolve based on review.
- Beta: additive-compatible changes only.
- GA: stable API semantics.

## Functional Testing Approach

### Unit tests

- API validation for backend/bus/queue/reconnect fields.
- Converter mapping to `vhostuser` disk XML.
- Runtime source identity/path handling.

### Integration tests

- Non-hotplug lifecycle with vhost-user-backed disk.
- VM start/stop/restart behavior with vhost-user-backed disk.
- Multi-disk scenarios and queue count variations.

### Hotplug-focused tests (secondary)

- Add/remove volume using same backend model.
- Reconnect behavior when socket endpoints are recreated.
- Helper pod restart and mount propagation reliability.

## Implementation History

Nebius downstream work that informed this proposal:

- 2024-08-12: Schema support for vhost-user disk source.
- 2024-08-13: Initial hotplug attach-path vhost-user conversion.
- 2024-08-13: Source identity fix for path-based sources.
- 2024-08-13: Socket-aware readiness check.
- 2024-08-13: Restrict conversion to selected disks.
- 2024-09-05: Refactor vendor logic into separate package.
- 2024-09-05: Directory mount model for reconnect reliability.
- 2024-10-16: API admission expanded for virtio hotplug disk path.
- 2025-04-27: Per-disk queue tuning.

## Graduation Requirements

### Alpha

- [ ] Feature gate `VhostUserDisks` guards all new behavior.
- [ ] API fields for vhost-user-backed disks are defined and validated.
- [ ] Converter/runtime support for non-hotplug vhost-user-backed disks is merged.
- [ ] Unit tests for API + converter behavior are merged.
- [ ] Initial integration tests for non-hotplug lifecycle are merged.
- [ ] User docs for feature-gated base support are merged.

### Beta

- [ ] API shape is stable, with only additive changes needed.
- [ ] Reliability and scaling tests are expanded and stable.
- [ ] Secondary hotplug integration (if included in this phase) is validated by e2e tests.
- [ ] Known critical issues are resolved or documented with mitigations.

### GA

- [ ] Feature gate removed.
- [ ] API and behavior are finalized and backward-compatible.
- [ ] Full documentation and operational guidance are complete.
- [ ] Long-running confidence and community adoption evidence are available.
- [ ] Relevant SIGs sign off on graduation.
