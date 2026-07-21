# VEP #278: Expose Instancetype Resources in VirtualMachine Status

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: N/A
- This VEP targets beta for version: N/A
- This VEP targets GA for version: v1.9.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [x] (R) GA target version is explicitly mentioned and approved

## Overview

When a VirtualMachine references an instancetype, the guest-visible resources
(vCPU topology and memory) provided by that instancetype and any referenced
preference are not directly visible in the VirtualMachine status. Users and
tooling must either start the VM, fetch the instancetype and preference objects
separately, or call the expand-spec subresource to determine what resources a
VM will receive.

This VEP proposes adding a `resources` field to `vm.status.instancetypeRef`
that exposes the resolved CPU topology (cores, sockets, threads) and guest
memory, making this information immediately available without additional API
calls.

## Motivation

Instancetypes are the recommended way to define VM resource profiles in
KubeVirt. However, once a VM references an instancetype, the actual resources
it provides are opaque in the VM status. This creates friction for:

- UI/dashboard tooling that needs to display VM resources without extra API calls.
- Monitoring and capacity planning systems that need to aggregate resource
  usage across VMs.
- Users who want to verify what resources their VM will receive without
  starting it or making additional API calls.

The expand-spec subresource can answer these questions but requires an
additional API call and returns the entire expanded spec, which is heavyweight
for simply determining CPU and memory values. Starting the VM just to inspect
resources is impractical for offline capacity planning and pre-flight
validation workflows.

## Goals

- Expose the resolved CPU topology (cores, sockets, threads) and guest memory
  from a referenced instancetype in `vm.status.instancetypeRef.resources`.
- Update these values automatically when the instancetype or preference
  reference changes (e.g., when switching to a different instancetype while the
  VM is stopped or via live update).

## Non Goals

- This enhancement does not modify VM spec or allocation behavior.
- This does not expose the full preference spec — only its influence on the
  resolved CPU topology (cores, sockets, threads) is reflected in the output.
- This does not replace the expand-spec subresource, which provides a
  fully-rendered VM spec for other purposes.

## Definition of Users

- KubeVirt users who reference instancetypes and preferences in their VMs and want to see the
  provided resources at a glance.
- UI and dashboard developers who display VM resource information.
- Cluster administrators performing capacity planning or resource auditing.
- Monitoring and automation systems that aggregate VM resource data.

## User Stories

### Dashboard Resource Display

As a UI developer, I want to display the CPU and memory provided by a VM's
instancetype without making additional API calls to fetch the instancetype
object or calling expand-spec.

**Example:**

```
$ kubectl get vms/test -o json | jq .status.instancetypeRef
{
  "controllerRevisionRef": {
    "name": "test-u1.medium-..."
  },
  "name": "u1.medium",
  "resources": {
    "cpu": {
      "cores": 1,
      "sockets": 1,
      "threads": 1
    },
    "memory": "4Gi"
  }
}
```

### Capacity Planning

As a cluster administrator, I want to aggregate CPU and memory resources across
all VMs using instancetypes without fetching each instancetype object
individually. With `vm.status.instancetypeRef.resources`, I can query VM
statuses directly.

### Instancetype Change Verification

As a user, after switching a VM from `u1.medium` to `u1.large` — whether by
editing the spec while the VM is stopped or via live update — I want to verify
that the new resources are reflected in the VM status.

When a `RestartRequired` condition is set (e.g., after a live update that
requires a restart to take effect), the resources field reflects the
*currently active* instancetype, not the pending one, so users can distinguish
between what the VM is running now and what it will run after restart.

## Repos

- https://github.com/kubevirt/kubevirt

## Design

A new `InstancetypeStatusResources` struct is added containing `CPU
CPUTopology` and `Memory resource.Quantity` fields. This is added to
`InstancetypeStatusRef` as an optional `resources` field.

The `populateInstancetypeStatusResources` method in the revision store handler
resolves the CPU topology by applying the instancetype and preference to a copy
of the VM (since preferences influence the topology breakdown of cores,
sockets, and threads from the total vCPU count). The resolved values are then
stored in the status.

The resources field is:
- Populated when a ControllerRevision is first created or when the status is
  first synced for a VM with an existing revision.
- Cleared and repopulated when the instancetype or preference reference
  changes (e.g., name, kind, or specific CR reference change triggers a new
  ControllerRevision).

No feature gate is required since this is a read-only, optional status field
addition with no behavioral changes. The alpha/beta/GA graduation stages track
API stability commitments rather than gating the feature on and off.

## API Examples

### New types

```go
type InstancetypeStatusResources struct {
    CPU    CPUTopology       `json:"cpu"`
    Memory resource.Quantity `json:"memory"`
}

type InstancetypeStatusRef struct {
    // ... existing fields ...

    // Resources provides a way for users to see which resources
    // are provided by the referenced instance type without the need to run the
    // VM, fetch the instance type or call expand-spec
    //
    // +optional
    Resources *InstancetypeStatusResources `json:"resources,omitempty"`
}
```

### VirtualMachine status with instancetype resources

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  instancetype:
    name: u1.large
    kind: VirtualMachineClusterInstancetype
status:
  instancetypeRef:
    name: u1.large
    kind: VirtualMachineClusterInstancetype
    controllerRevisionRef:
      name: my-vm-u1.large-...
    resources:
      cpu:
        cores: 1
        sockets: 2
        threads: 1
      memory: 8Gi
```

### After switching instancetype (stopped VM or live update)

```yaml
# Before: u1.medium (1 socket, 4Gi)
# After:  u1.large  (2 sockets, 8Gi)
status:
  instancetypeRef:
    name: u1.large
    controllerRevisionRef:
      name: my-vm-u1.large-...
    resources:
      cpu:
        cores: 1
        sockets: 2
        threads: 1
      memory: 8Gi
```

### RestartRequired: pending instancetype change

When a live update requires a restart, the resources field reflects the
currently active instancetype until the restart occurs:

```yaml
status:
  conditions:
    - type: RestartRequired
      status: "True"
  instancetypeRef:
    name: u1.large            # pending, not yet active
    controllerRevisionRef:
      name: my-vm-u1.large-...
    resources:
      cpu:                    # reflects the CURRENT instancetype (u1.medium)
        cores: 1
        sockets: 1
        threads: 1
      memory: 4Gi
```

## Alternatives

### Using expand-spec subresource

Users can call the expand-spec subresource to see the fully-rendered VM spec
including instancetype-applied resources.

**Cons:**
- Requires an additional API call per VM.
- Returns the entire expanded spec, which is heavyweight for just CPU/memory.
- Not suitable for bulk queries or dashboard aggregation.
- Does not persist the information in the VM status for offline VMs.

### Exposing raw instancetype spec fields

Instead of the resolved topology, expose the raw `CPU.Guest` count and
`Memory.Guest` from the instancetype spec.

**Cons:**
- Does not reflect the actual CPU topology (cores/sockets/threads) which
  depends on preference application.
- Users would still need to understand how preferences influence topology to
  interpret the values correctly.

### Adding resources to vm.status directly

Instead of nesting under `instancetypeRef`, add a top-level `resources` field
to `vm.status`.

**Cons:**
- Conflates instancetype-provided resources with potentially user-defined
  resources.
- The information is directly related to the instancetype reference and
  belongs alongside it.

## Scalability

This change adds a small struct (3 uint32 fields and 1 Quantity) to the
VirtualMachine status. The overhead is negligible per VM. The resources are
populated during the existing ControllerRevision sync flow, so no additional
reconcile loops or watches are introduced.

## Update/Rollback Compatibility

The new `resources` field is optional and added to the VM status. During an
upgrade, the field will be populated for VMs with instancetype references on
the next reconcile. During a rollback, the field will be ignored by older
versions and eventually cleared on the next status update by the older
controller.

## Functional Testing Approach

- Unit tests verify that `populateInstancetypeStatusResources` correctly
  resolves and populates CPU topology and memory for both namespaced and
  cluster-scoped instancetypes.
- Unit tests verify the field is cleared and repopulated when the instancetype
  reference changes.
- E2E tests verify the resources are populated for a stopped VM with a
  cluster instancetype and a namespaced instancetype.
- E2E tests verify the resources are updated after switching instancetype on a
  stopped VM.
- E2E tests verify the resources are updated after a live instancetype hotplug
  (switching from one instancetype to another).
- E2E tests verify that when a `RestartRequired` condition is set, the
  resources field reflects the currently active instancetype, not the pending
  one.

## Implementation History

- 2025-11-17: Initial implementation PR opened. PR: https://github.com/kubevirt/kubevirt/pull/16130

## Graduation Requirements

### GA (v1.9)

- [ ] Implementation populates `resources` in `vm.status.instancetypeRef`
- [ ] Unit tests cover namespaced and cluster-scoped instancetypes
- [ ] E2E tests cover basic flow, stopped VM, and live update/hotplug scenarios
- [ ] E2E tests cover `RestartRequired` condition behavior
- [ ] VEP reviewed and approved by sig-compute
- [ ] Documentation updated with examples of the resources status field
