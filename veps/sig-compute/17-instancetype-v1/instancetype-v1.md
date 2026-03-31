# VEP #17: Instancetype API v1 Graduation and Custom Sizing

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v0.56.0
- This VEP targets beta for version: v1.9 (first in v1.0.0)
- This VEP targets GA for version: v1.12.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone /
release*.

- [x] (R) Enhancement issue created, which links to VEP dir in
  [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP graduates the `instancetype.kubevirt.io` API group from `v1beta1` to
`v1` through an intermediate `v1beta2` version, introducing two key changes:

1. **Custom Sizing**: Instancetypes become non-conflicting defaults.
   VM-specified values take precedence over instancetype values, similar to how
   `VirtualMachinePreference` works today. This replaces the current strict
   conflict semantics where any overlap between a VM spec and an instancetype
   causes rejection.

2. **Optional CPU/Memory**: The `CPU` and `Memory` fields become optional in the
   instancetype spec, enabling device-only instancetypes (e.g., GPU passthrough
   configurations without mandating compute resources).

The migration follows a 5-release cycle, ensuring at least one release between
changing the storage version and removing the old API version to allow
[`kube-storage-version-migrator`](https://github.com/kubernetes-sigs/kube-storage-version-migrator)
to rewrite stored objects:

- **v1.9.0**: Introduce `v1beta2`, deprecate `v1beta1`
- **v1.10.0**: Change storage to `v1beta2`, introduce `v1`, deprecate `v1beta2`
- **v1.11.0**: Remove `v1beta1`
- **v1.12.0**: Change storage to `v1`
- **v1.13.0**: Remove `v1beta2`

This graduation plan is in accordance with Rule #4b of the [Kubernetes Deprecation Policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#deprecating-parts-of-the-api)
and section "Backward compatibility gotchas" of the [Kubernetes API Changes Guide](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md#backward-compatibility-gotchas).

## Motivation

The current instancetype model is rigid: referencing an instancetype means
accepting all of its values without any customization. If a VM specifies any
field that the instancetype also defines - CPU count, memory, GPUs, host
devices, etc. - the VM is rejected with a conflict error.

This "take it or leave it" design prevents legitimate use cases:

- A user wants an instancetype's GPU passthrough configuration but needs more
  memory for their workload.
- An admin defines instancetypes as baseline configurations, but teams need to
  adjust CPU count for specific applications.
- Cloud providers want to offer instancetypes as starting points that users can
  customize, similar to how cloud VM flavors often allow resource adjustments.
- An instancetype should be able to represent a device profile (GPUs, host
  devices, launch security) without being forced to also mandate CPU and memory
  values.

Meanwhile, `VirtualMachinePreference` already implements the "defaults with
overrides" pattern - preferences apply only when the VM has not specified a
value, and they never cause conflicts. Aligning instancetype behavior with this
model makes the API more consistent, flexible, and user-friendly.

## Goals

- Graduate `instancetype.kubevirt.io` from `v1beta1` to `v1` through an
  intermediate `v1beta2`
- Change instancetype application semantics: VM-specified values take precedence
  over instancetype defaults (custom sizing)
- Make `CPU` and `Memory` optional in the instancetype spec to enable
  device-only instancetypes
- Remove deprecated fields from the `v1beta2`/`v1` API surface
- Maintain backward compatibility: `v1beta1` instancetypes retain strict
  conflict semantics until `v1beta1` is removed
- Ensure a VM always ends up with valid memory configuration through existing
  validation

## Non Goals

- Changing the `VirtualMachinePreference` API semantics (preferences already
  work as defaults)
- Introducing sizing ranges or min/max constraints (could be a follow-up
  enhancement)
- Merging instancetypes and preferences into a single resource type

## Definition of Users

- **VM User**: Creates VirtualMachines, references instancetypes, and may want
  to override specific fields for workload-specific needs.
- **Cluster Admin**: Defines instancetypes as organizational standards or cloud
  offerings that serve as sensible defaults.
- **Automation/CI**: Programmatically creates VMs using instancetypes as
  baselines with workload-specific overrides.

## User Stories

- As a VM user, I want to reference a GPU-focused instancetype but specify my
  own CPU and memory requirements, so I get the right device configuration
  without being forced into a fixed compute size.

- As a VM user, I want existing VMs that reference instancetypes without any
  overrides to continue working identically after upgrading.

- As a platform admin, I want to define instancetypes that provide sensible
  defaults for CPU, memory, and devices, knowing that users can customize sizing
  if needed.

- As a platform admin, I want to create device-only instancetypes (GPUs, host
  devices, launch security) without being forced to specify CPU and memory
  values.

- As a VM user, I want the system to ensure my VM always has valid memory
  configuration, whether that comes from the instancetype, my own specification,
  or a combination of both.

- As a VM user, I want to gradually migrate from `v1beta1` to `v1` with clear
  deprecation signals and no surprises in behavior until I opt into the new API
  version.

## Repos

- `kubevirt/kubevirt`
- `kubevirt/common-instancetypes`

## Design

### Part 1: API Version Migration

The migration follows a 5-release cycle with the storage version always being
the oldest available API version. At least one release separates changing the
storage version from removing the old API version, giving operators time to run
[`kube-storage-version-migrator`](https://github.com/kubernetes-sigs/kube-storage-version-migrator)
to rewrite stored objects. This matches the approach used for the `kubevirt.io`
v1alpha3 -> v1 migration (storage changed in v1.0.0, v1alpha3 removed in
v1.2.0).

#### v1.9.0 - Introduce `v1beta2`, deprecate `v1beta1`

A new API version `instancetype.kubevirt.io/v1beta2` is introduced with the
following changes from `v1beta1`:

**Schema changes:**

- `VirtualMachineInstancetypeSpec.CPU` changes from `CPUInstancetype` (required)
  to `*CPUInstancetype` (optional pointer)
- `VirtualMachineInstancetypeSpec.Memory` changes from `MemoryInstancetype`
  (required) to `*MemoryInstancetype` (optional pointer)

**Deprecated fields removed in `v1beta2`:**

- `FirmwarePreferences.DeprecatedPreferredUseEfi` - use
  `FirmwarePreferences.PreferredEfi` instead
- `FirmwarePreferences.DeprecatedPreferredUseSecureBoot` - use
  `FirmwarePreferences.PreferredEfi` with SecureBoot instead
- Deprecated `PreferredCPUTopology` enum values: `preferCores`, `preferSockets`,
  `preferThreads`, `preferSpread`, `preferAny` - use the short forms `cores`,
  `sockets`, `threads`, `spread`, `any` instead

**Version serving strategy:**

- `v1beta1`: served, **storage version** (oldest available - ensures safe
  downgrade to pre-v1.9.0), deprecated
- `v1beta2`: served, preferred for new objects

Conversion webhooks handle `v1beta1` <-> `v1beta2` translation:

- `v1beta1` -> `v1beta2`: Required `CPU`/`Memory` fields wrapped in pointers.
  Deprecated `PreferredCPUTopology` values mapped to short forms. Deprecated
  firmware fields mapped to `PreferredEfi`.
- `v1beta2` -> `v1beta1`: Optional `CPU`/`Memory` unwrapped if present. If
  `CPU` or `Memory` is nil (device-only instancetype), the conversion fails
  with an error since `v1beta1` cannot represent device-only instancetypes.
  No mapping needed for `PreferredCPUTopology` - the short forms (`cores`,
  `sockets`, `threads`, `spread`, `any`) are already valid `v1beta1` values
  alongside the deprecated ones.

#### v1.10.0 - Change storage to `v1beta2`, introduce `v1`

Storage version moves from `v1beta1` to `v1beta2`. `v1beta1` remains served to
allow `kube-storage-version-migrator` to rewrite all stored objects from
`v1beta1` to `v1beta2`. Operators should deploy `kube-storage-version-migrator`
to complete the migration before upgrading to v1.11.0.

A new API version `instancetype.kubevirt.io/v1` is also introduced with the
same schema as `v1beta2`.

**Version serving strategy:**

- `v1beta1`: served, deprecated
- `v1beta2`: served, **storage version**, deprecated
- `v1`: served, preferred for new objects

Conversion webhooks handle `v1beta2` <-> `v1` translation (trivial since schemas
are identical).

#### v1.11.0 - Remove `v1beta1`

`v1beta1` is removed after the migration window.

**Version serving strategy:**

- `v1beta1`: **removed**
- `v1beta2`: served, **storage version** (oldest available), deprecated
- `v1`: served, preferred for new objects

#### v1.12.0 - Change storage to `v1`

Storage version moves from `v1beta2` to `v1`. `v1beta2` remains served to allow
`kube-storage-version-migrator` to rewrite all stored objects from `v1beta2` to
`v1`. Operators should complete migration before upgrading to v1.13.0.

**Version serving strategy:**

- `v1beta2`: served, deprecated
- `v1`: served, **storage version**

#### v1.13.0 - Remove `v1beta2`

**Version serving strategy:**

- `v1`: served, **storage version** (only version)

### Part 2: Custom Sizing

#### Behavioral Change

Starting with `v1beta2`, the instancetype application logic changes from
**conflict-on-overlap** to **skip-if-set**:

| Scenario                                            | v1beta1 behavior                   | v1beta2/v1 behavior                        |
|-----------------------------------------------------|------------------------------------|--------------------------------------------|
| VM specifies a field that instancetype also defines | **Conflict error** - VM is rejected | **VM wins** - instancetype value is skipped |
| VM does not specify a field instancetype defines    | Instancetype value is applied       | Instancetype value is applied (same)        |
| Neither VM nor instancetype provides memory         | N/A (instancetype always provides)  | **Validation error** - VM is rejected       |

ControllerRevisions are upgraded to the current storage version on use once the
old API version is no longer served. For example, `v1beta1` ControllerRevisions
are upgraded to `v1beta2` starting with v1.11.0 (when `v1beta1` is removed),
not v1.10.0 (when the storage version changes). This means existing VMs retain
strict conflict semantics through v1.10.0, and transition to skip-if-set only
when `v1beta1` is fully removed.

#### Implementation

All apply functions in `pkg/instancetype/apply/` follow the same transformation:

**Before (v1beta1 - conflict):**

```go
func applyCPU(
    baseConflict *conflict.Conflict,
    instancetypeSpec *v1beta1.VirtualMachineInstancetypeSpec,
    preferenceSpec *v1beta1.VirtualMachinePreferenceSpec,
    vmiSpec *virtv1.VirtualMachineInstanceSpec,
) conflict.Conflicts {
    if vmiSpec.Domain.CPU != nil && vmiSpec.Domain.CPU.Guest != 0 {
        return conflict.Conflicts{baseConflict.NewChild("domain", "cpu", "guest")}
    }
    // ... set from instancetype
}
```

**After (v1beta2/v1 - skip-if-set):**

```go
func applyCPU(
    instancetypeSpec *v1beta2.VirtualMachineInstancetypeSpec,
    preferenceSpec *v1beta2.VirtualMachinePreferenceSpec,
    vmiSpec *virtv1.VirtualMachineInstanceSpec,
) {
    if instancetypeSpec.CPU == nil {
        return
    }
    if vmiSpec.Domain.CPU == nil || vmiSpec.Domain.CPU.Guest == 0 {
        // apply from instancetype
    }
    // Same pattern for Model, DedicatedCPUPlacement, NUMA, etc.
}
```

The affected apply functions and their fields:

| File                | Fields (skip if VM specifies)                                                                                                                                                                                                                      |
|---------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cpu.go`            | `domain.cpu.guest` (sockets/cores/threads), `domain.cpu.model`, `domain.cpu.dedicatedCPUPlacement`, `domain.cpu.numa`, `domain.cpu.isolateEmulatorThread`, `domain.cpu.realtime`, `domain.resources.requests[cpu]`, `domain.resources.limits[cpu]` |
| `memory.go`         | `domain.memory.guest`, `domain.memory.hugepages`, `domain.memory.maxGuest`, `domain.resources.requests[memory]`, `domain.resources.limits[memory]`                                                                                                 |
| `gpu.go`            | `domain.devices.gpus`                                                                                                                                                                                                                              |
| `hostdevices.go`    | `domain.devices.hostDevices`                                                                                                                                                                                                                       |
| `iothreads.go`      | `domain.ioThreads`                                                                                                                                                                                                                                 |
| `iothreadpolicy.go` | `domain.ioThreadsPolicy`                                                                                                                                                                                                                           |
| `launchsecurity.go` | `domain.launchSecurity`                                                                                                                                                                                                                            |
| `nodeselector.go`   | `nodeSelector`                                                                                                                                                                                                                                     |
| `scheduler.go`      | `schedulerName`                                                                                                                                                                                                                                    |
| `annotations.go`    | `annotations` (per-key)                                                                                                                                                                                                                            |

The orchestrator in `vmi.go` no longer collects or returns `conflict.Conflicts`
for v1beta2/v1 instancetypes.

#### Validation

The existing VMI admission webhooks already validate that memory is present
(via `ValidateVirtualMachineInstanceMandatoryFields`). If neither the
instancetype nor the VM provides memory, the existing admitters reject the VM
for missing required fields - the same as they would for any VM created without
an instancetype. No new validation is needed for memory.

CPU topology is not explicitly required by existing admitters, but a VMI without
any CPU configuration would use defaults (1 vCPU). This is acceptable behavior
for device-only instancetypes where the user does not specify CPU.

## API Examples

### Example 1: Classic usage (no overrides)

Identical behavior to today. A VM references an instancetype without specifying
any overlapping fields:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta2
kind: VirtualMachineInstancetype
metadata:
  name: standard-medium
spec:
  cpu:
    guest: 4
  memory:
    guest: 8Gi
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  instancetype:
    name: standard-medium
  template:
    spec:
      domain:
        devices:
          disks:
            - name: rootdisk
              disk:
                bus: virtio
      volumes:
        - name: rootdisk
          containerDisk:
            image: registry.example.com/my-image
# Result: VM gets 4 vCPUs, 8Gi RAM from instancetype. Identical to v1beta1 behavior.
```

### Example 2: Custom sizing - override memory

A user references a GPU instancetype but needs more memory for their ML
workload:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta2
kind: VirtualMachineInstancetype
metadata:
  name: gpu-workstation
spec:
  cpu:
    guest: 8
    dedicatedCPUPlacement: true
  memory:
    guest: 16Gi
  gpus:
    - name: gpu1
      deviceName: nvidia.com/A100
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-ml-vm
spec:
  instancetype:
    name: gpu-workstation
  template:
    spec:
      domain:
        memory:
          guest: 64Gi    # Override: need more memory for ML workload
        devices:
          disks:
            - name: rootdisk
              disk:
                bus: virtio
      volumes:
        - name: rootdisk
          containerDisk:
            image: registry.example.com/ml-image
# Result: 8 vCPUs (from instancetype), 64Gi RAM (VM override),
# A100 GPU (from instancetype), dedicatedCPUPlacement (from instancetype).
#
# With v1beta1, this VM would have been REJECTED with a conflict on domain.memory.guest.
```

### Example 3: Device-only instancetype

An instancetype that only defines device configuration, with no CPU or memory:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta2
kind: VirtualMachineInstancetype
metadata:
  name: gpu-passthrough
spec:
  gpus:
    - name: gpu1
      deviceName: nvidia.com/T4
  launchSecurity:
    sev: { }
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: secure-gpu-vm
spec:
  instancetype:
    name: gpu-passthrough
  template:
    spec:
      domain:
        cpu:
          cores: 2
          sockets: 1
          threads: 1
        memory:
          guest: 4Gi
        devices:
          disks:
            - name: rootdisk
              disk:
                bus: virtio
      volumes:
        - name: rootdisk
          containerDisk:
            image: registry.example.com/my-image
# Result: 2 vCPUs, 4Gi RAM (from VM), T4 GPU and SEV (from instancetype).
# This is NOT possible with v1beta1, which requires CPU and Memory in the instancetype.
```

### Example 4: Override everything

The instancetype serves as documentation of the baseline. The VM overrides all
compute resources:

Using `standard-medium` from Example 1 (4 vCPUs, 8Gi):

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: fully-custom-vm
spec:
  instancetype:
    name: standard-medium
  template:
    spec:
      domain:
        cpu:
          cores: 16
          sockets: 1
          threads: 2
        memory:
          guest: 32Gi
# Result: 32 vCPUs (16 cores * 2 threads), 32Gi RAM - all from VM spec.
# Instancetype values are entirely overridden.
```

### Example 5: Validation failure - missing memory

Neither the instancetype nor the VM provides memory:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta2
kind: VirtualMachineInstancetype
metadata:
  name: gpu-only
spec:
  gpus:
    - name: gpu1
      deviceName: nvidia.com/T4
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: invalid-vm
spec:
  instancetype:
    name: gpu-only
  template:
    spec:
      domain:
        devices:
          disks:
            - name: rootdisk
              disk:
                bus: virtio
# Result: REJECTED by existing VMI validation.
# Error: "no memory requested, at least one of 'domain.memory.guest',
#  'domain.memory.hugepages.pageSize' or 'domain.resources.requests.memory'
#  must be set"
```

### Example 6: v1beta1 backward compatibility

Existing `v1beta1` instancetypes retain strict conflict semantics through
v1.10.0. ControllerRevisions are only upgraded when `v1beta1` is removed in
v1.11.0:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta1
kind: VirtualMachineInstancetype
metadata:
  name: legacy-instance
spec:
  cpu:
    guest: 2
  memory:
    guest: 4Gi
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  instancetype:
    name: legacy-instance
  template:
    spec:
      domain:
        memory:
          guest: 8Gi  # Conflict!
# v1.9.0 - v1.10.0: REJECTED with conflict error on domain.memory.guest.
# v1.11.0+: ControllerRevision upgraded to v1beta2 on use, VM is accepted
#   with 8Gi memory from the VM spec.
```

## Alternatives

### Alternative 1: Explicit override opt-in on the VM

Add a field like `spec.instancetype.allowOverrides: true` to
`InstancetypeMatcher` on the VM. When true, VM values win. When false (default),
conflicts are raised as today.

This was rejected because it adds API complexity and requires users to know
about the opt-in. It also does not address device-only instancetypes where
CPU/Memory are simply not relevant.

### Alternative 2: Per-field overridable annotations

Mark individual fields as overridable in the instancetype spec, e.g.,
`overridable: true` on each field.

This was rejected because it creates a very complex API surface that is
difficult to understand and validate.

### Alternative 3: Skip v1beta2, graduate directly to v1

Go straight from `v1beta1` to `v1` with the behavioral change.

This was rejected because for a behavioral change a proper deprecation cycle
is recommended. Users could be relying on the strict conflict semantics and
might need time to adapt, although in reality the impact should be minimal
and backward compatible. The intermediate `v1beta2` version provides this
transition period.

## Scalability

No scalability impact. The changes are purely in admission/apply logic. No
additional API calls, watches, or ControllerRevisions are introduced. The
skip-if-set logic is computationally simpler than the conflict-detection logic
it replaces.

## Update/Rollback Compatibility

### Upgrade path

- **v1.8.x -> v1.9.0**: `v1beta2` becomes available. Existing `v1beta1`
  instancetypes continue working with strict conflict semantics. No behavioral
  change unless users create or convert instancetypes to `v1beta2`. Storage
  version remains `v1beta1`.
- **v1.9.x -> v1.10.0**: Storage version changes to `v1beta2`. `v1` introduced.
  All three versions served. Operators should deploy
  `kube-storage-version-migrator` to rewrite stored objects from `v1beta1` to
  `v1beta2`.
- **v1.10.x -> v1.11.0**: `v1beta1` removed (migration must be complete).
  ControllerRevisions with `v1beta1` are upgraded to `v1beta2` on next use,
  transitioning to skip-if-set semantics.
- **v1.11.x -> v1.12.0**: Storage version changes to `v1`. Both `v1beta2` and
  `v1` still served. Operators should run `kube-storage-version-migrator` to
  rewrite stored objects from `v1beta2` to `v1`.
- **v1.12.x -> v1.13.0**: `v1beta2` removed (migration must be complete). Only
  `v1` remains.

### Rollback considerations

- **v1.9.0 -> v1.8.x**: Safe. Storage version is `v1beta1`, so the older version
  can read all stored objects. VMs that were using custom sizing with `v1beta2`
  instancetypes will need to have conflicting fields removed or the instancetype
  reference dropped.
- **v1.10.0 -> v1.9.x**: Safe. Storage version is `v1beta2`, readable by v1.9.x
  which serves `v1beta2`.
- **v1.11.0 -> v1.10.x**: Safe. Storage version is `v1beta2`, readable by
  v1.10.x. Note: `v1beta1` was removed in v1.11.0, so rollback only works if
  `kube-storage-version-migrator` completed the v1beta1 -> v1beta2 migration
  before the upgrade to v1.11.0.
- **v1.12.0 -> v1.11.x**: Safe. Storage version is `v1`, readable by v1.11.x
  which serves `v1`.
- **v1.13.0 -> v1.12.x**: Safe. Storage version is `v1`, readable by v1.12.x.

### ControllerRevision compatibility

ControllerRevisions are upgraded to the current storage version on use once the
old API version is no longer served. Existing `v1beta1` ControllerRevisions
retain strict conflict semantics through v1.10.0. Starting with v1.11.0 (when
`v1beta1` is removed), they are converted to `v1beta2` on next use and the
`ControllerRevisionObjectVersionLabel` is updated, transitioning them to
skip-if-set semantics.

## Functional Testing Approach

### Unit tests

- Each apply function (`cpu.go`, `memory.go`, `gpu.go`, etc.) must have tests
  verifying that VM-specified values are preserved and instancetype values fill
  in only unset fields when using `v1beta2`/`v1` instancetypes.
- Each apply function must retain tests verifying conflict behavior for
  `v1beta1` instancetypes.

### Functional tests

- Create a VM referencing a `v1beta2` instancetype with overridden memory.
  Verify the VMI gets the VM's memory value and the instancetype's other values.
- Create a VM referencing a device-only `v1beta2` instancetype (no CPU/Memory)
  with CPU/Memory on the VM spec. Verify success.
- Create a VM referencing a device-only instancetype without CPU/Memory on the
  VM spec. Verify rejection.
- Create a VM referencing a `v1beta1` instancetype with overlapping fields.
  Verify conflict rejection (backward compatibility).

### Upgrade tests

- Create VMs with `v1beta1` instancetypes pre-upgrade. Verify they continue
  working after upgrade to v1.9.0 with strict conflict semantics.
- Verify ControllerRevisions are upgraded to the current storage version on use.

### Conversion webhook tests

- Create instancetypes via `v1beta1` API, read back via `v1beta2` API. Verify
  correct field mapping (required -> optional pointer).
- Create instancetypes via `v1beta2` API, read back via `v1beta1` API. Verify
  correct field mapping (optional pointer -> required). Verify conversion fails
  for device-only instancetypes (nil CPU/Memory).
- Verify deprecated fields are correctly mapped during conversion.

## Graduation Requirements

### v1.9.0

- [ ] `v1beta2` API types defined with optional `CPU`/`Memory`
- [ ] Deprecated fields removed from `v1beta2` types
- [ ] Conversion webhooks between `v1beta1` <-> `v1beta2`
- [ ] All apply functions updated with skip-if-set logic for `v1beta2`
  instancetypes
- [ ] Apply functions retain conflict logic for `v1beta1` instancetypes
- [ ] Unit and functional tests for custom sizing
- [ ] `v1beta1` marked as deprecated in API documentation
- [ ] `common-instancetypes` updated to use `v1beta2`
- [ ] Storage version: `v1beta1`

### v1.10.0

- [ ] Storage version changed to `v1beta2`
- [ ] `v1` API types defined (same schema as `v1beta2`)
- [ ] Conversion webhooks between `v1beta2` <-> `v1`
- [ ] `v1beta2` marked as deprecated in API documentation
- [ ] Documentation recommends deploying `kube-storage-version-migrator`
- [ ] Documentation updated with custom sizing examples
- [ ] `common-instancetypes` updated to use `v1`

### v1.11.0

- [ ] `v1beta1` API version removed
- [ ] ControllerRevisions upgraded to `v1beta2` on use
- [ ] Storage version: `v1beta2`

### v1.12.0

- [ ] Storage version changed to `v1`
- [ ] Documentation recommends deploying `kube-storage-version-migrator`

### v1.13.0

- [ ] `v1beta2` API version removed
- [ ] Only `v1` served and stored
- [ ] Storage version: `v1`
