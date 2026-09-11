# VEP #45: In-Place Pod Resize for CPU/Memory Hotplug

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Table of contents

- [Release Signoff Checklist](#release-signoff-checklist)
- [Overview](#overview)
- [Motivation](#motivation)
- [Goals](#goals)
- [Non Goals](#non-goals)
- [Definition of Users](#definition-of-users)
- [User Stories](#user-stories)
- [Repos](#repos)
- [Design](#design)
  - [Feature Gate](#feature-gate)
  - [Decision Tree](#decision-tree)
  - [In-Place Resize Flow](#in-place-resize-flow)
  - [Container Resize Policy](#container-resize-policy)
  - [Component Responsibilities](#component-responsibilities)
  - [Preventing Workload-Updater Races](#preventing-workload-updater-races)
  - [VMI Conditions](#vmi-conditions)
  - [Failure Handling](#failure-handling)
  - [Infeasible Resize](#infeasible-resize)
- [API Examples](#api-examples)
- [Alternatives](#alternatives)
- [Risk and Limitations](#risk-and-limitations)
  - [Dedicated CPUs](#dedicated-cpus)
  - [Hugepages (Memory Only)](#hugepages-memory-only)
  - [Pre-existing Issue: CPU Hotplug for Non-Migratable VMs](#pre-existing-issue-cpu-hotplug-for-non-migratable-vms)
- [Scalability](#scalability)
- [Update/Rollback Compatibility](#updaterollback-compatibility)
- [Functional Testing Approach](#functional-testing-approach)
- [Implementation History](#implementation-history)
- [Graduation Requirements](#graduation-requirements)
  - [Alpha](#alpha)
  - [Beta](#beta)
  - [GA](#ga)

## Overview

KubeVirt supports CPU and memory hotplug for running VMs. Currently, every hotplug
operation requires a live migration, because Kubernetes pods were traditionally
immutable - pod resource requests and limits could not be changed after creation.
The migration creates a new virt-launcher pod with the updated resources.

Kubernetes in-place pod resize [graduated to GA](https://kubernetes.io/blog/2025/12/19/kubernetes-v1-35-in-place-pod-resize-ga/) in Kubernetes 1.35.
This allows modifying a pod's CPU and memory requests/limits without recreating
the pod. The kubelet updates the container's cgroup limits directly.

This VEP proposes using in-place pod resize to eliminate the migration requirement
for hotplug. The hotplug flow becomes two phases:

1. **Pod-level resize**: The VM controller patches the virt-launcher pod's resources,
   triggering Kubernetes to update cgroup limits.
2. **Guest-level hotplug**: virt-handler applies the change inside the VM via libvirt
   (onlining vCPUs for CPU, hotplugging virtio-mem devices for memory).

When in-place resize is not possible (dedicated CPUs, hugepages for memory), the
existing migration-based path remains as a fallback.

## Motivation

The current migration-based hotplug has several drawbacks:

- **Resource overhead**: Live migration temporarily doubles resource consumption (source
  and target pods run simultaneously).
- **Downtime risk**: Migration involves a brief stun-time during cutover and can fail,
  leaving the VM in an intermediate state.
- **Non-migratable VMs excluded**: VMs with passthrough devices (GPUs, vFPGAs, etc.)
  cannot migrate, so they cannot hotplug CPU or memory at all today.
- **Quota pressure**: The temporary second pod may exceed namespace ResourceQuotas,
  blocking the hotplug entirely.
- **Complexity**: The migration path involves multiple controllers (VM controller,
  workload-updater, migration controller, virt-handler on both source and target nodes),
  making the hotplug flow harder to reason about and debug.

In-place resize eliminates all of these issues for the common case.

## Goals

- Use Kubernetes in-place pod resize for CPU and memory hotplug under the `LiveUpdate`
  rollout strategy.
- Make in-place resize the preferred method; fall back to migration only when in-place
  is not feasible.
- Enable hotplug for non-migratable VMs (e.g., VMs with passthrough devices).
- Keep the change transparent to users - no new API fields required.

## Non Goals

- Exposing a user-facing toggle to choose between in-place resize and migration.
  In-place is always preferred when possible; migration is an automatic fallback.
- Supporting in-place resize for dedicated-CPU VMs. Kubernetes does not support in-place
  resize with static CPU/memory manager policies. This is being tracked upstream in
  [KEP #5294](https://github.com/kubernetes/enhancements/issues/5294) and may be
  addressed in a future VEP iteration.
- Resource scale-down. CPU socket removal and memory hotunplug are not supported by
  KubeVirt today. This VEP does not change that - in-place resize follows the same
  scale-up-only constraint.

## Definition of Users

- **VM owners**: Users who create and manage VMs and want to adjust resources without
  disruption.
- **Cluster admins**: Operators who want to reduce migration overhead and enable
  hotplug for a broader set of VM configurations.

## User Stories

- As a VM owner, I want to hotplug CPU and memory without triggering a live migration,
  so that my VM experiences no stun-time or migration risk.
- As a VM owner with a non-migratable VM (e.g., GPU passthrough), I want to hotplug
  CPU and memory, which is not possible today.
- As a VM owner, I want to hotplug memory to support a newly attached passthrough device
  that requires additional resources.
- As a cluster admin, I want hotplug operations to avoid the resource overhead of
  temporary migration pods.
- As a namespace owner with a ResourceQuota, I want hotplug to work without temporarily
  exceeding my quota.

## Repos

- kubevirt/kubevirt

## Design

### Feature Gate

A new feature gate `InPlaceHotplug` controls the feature. When disabled, the existing
migration-based hotplug path is used unchanged.

### Decision Tree

When a VM spec change triggers a hotplug under the `LiveUpdate` rollout strategy:

```
VM spec change (CPU sockets or memory)
  |
  +-- LiveUpdate rollout strategy disabled?
  |     \-- Set RestartRequired condition (existing behavior)
  |
  +-- Existing hotplug blockers apply?
  |     (NUMA passthrough, realtime, SEV/TDX, ARM64, etc.)
  |     \-- Set RestartRequired condition (existing behavior)
  |
  +-- InPlaceHotplug feature gate disabled?
  |     \-- Use existing migration-based path (no behavior change)
  |
  +-- Dedicated CPUs (dedicatedCpuPlacement: true)?
  |     \-- Fall back to migration-based path
  |         (K8s does not support in-place resize with static CPU manager)
  |
  +-- Memory change on hugepages VM?
  |     \-- Fall back to migration-based path for memory
  |         (K8s does not support in-place resize of [hugepage resources](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/1287-in-place-update-pod-resources#non-goals))
  |         Note: CPU hotplug on hugepages VMs can still use in-place resize.
  |
  \-- Otherwise:
        \-- Use in-place resize path (described below)
```

### In-Place Resize Flow

The flow has two phases - pod-level resize and guest-level hotplug - coordinated
across two components:

**Phase 1 - Pod-level resize (virt-controller's VM controller)**:

1. The VM controller detects a CPU or memory change in the VM spec (existing logic
   in `handleCPUChangeRequest` / `handleMemoryHotplugRequest`).
2. The VM controller patches the VMI spec with the new resource values (existing).
3. **New**: The VM controller patches the virt-launcher pod's compute container
   resources (requests and limits) by adding the resource delta. For CPU this is
   straightforward (additional millicores based on the new socket count). For memory,
   the delta includes both the guest memory increase and any proportional overhead
   (e.g., page table overhead that scales with memory size). This triggers Kubernetes
   in-place pod resize, which updates the container's cgroup limits.
4. **New**: The VM controller watches the pod's status conditions for resize completion.
   Kubernetes surfaces resize state via `PodResizePending` and `PodResizeInProgress`
   conditions. The VM controller reflects this on the VMI via the new
   `PodResourceResizeInProgress` condition (see [VMI Conditions](#vmi-conditions) below).
5. **New**: Once the pod resize completes, the VM controller clears the
   `PodResourceResizeInProgress` condition, signaling readiness for guest-level hotplug.

If the user changes both CPU and memory simultaneously, both resources are updated
in a single pod patch. Kubernetes handles multi-resource resize atomically per
container.

**Phase 2 - Guest-level hotplug (virt-handler)**:

1. virt-handler detects the VMI condition indicating pod resize is complete.
2. virt-handler applies the guest-level change via libvirt:
   - **CPU**: Calls `SyncVirtualMachineCPUs` to online the new vCPUs.
   - **Memory**: Hotplugs a virtio-mem device with the additional memory.
3. virt-handler updates `vmi.Status.CurrentCPUTopology` or memory status and clears
   the hotplug conditions.

Today, guest-level hotplug runs exclusively during migration finalization
(`migration-target.go:hotplugCPU` / `hotplugMemory`). This VEP introduces a new
code path in virt-handler that performs the same guest-level operations outside of
migration context. The logic (libvirt calls, status updates, condition management)
is the same - it is extracted from the migration-specific code into a reusable path
that can be invoked either during migration finalization or after in-place pod resize.

### Container Resize Policy

Kubernetes allows containers to specify a `resizePolicy` per resource type, controlling
whether a container restart is required on resize. The default policy is `NotRequired`
(resize without restart), which is what we need - the virt-launcher container must not
be restarted, as that would terminate the VM.

Since the default is correct, no explicit `resizePolicy` configuration is needed on
the compute container. However, if an external admission webhook or policy engine
modifies the resize policy to `RestartContainer`, the resize would restart
virt-launcher and kill the VM. This should be documented as an operational
consideration.

### Component Responsibilities

| Component | Current Role | New Role (In-Place Path) |
|-----------|-------------|--------------------------|
| virt-controller's VM controller | Patches VMI spec | Patches VMI spec + pod resources, watches pod resize |
| Workload-updater | Creates migrations for hotplug | Not involved in in-place path |
| virt-handler | Applies guest hotplug during migration | Applies guest hotplug without migration |

The workload-updater remains responsible for migration-based hotplug (dedicated CPUs,
hugepages memory) and is not involved in the in-place path.

### Preventing Workload-Updater Races

In the current architecture, the VMI controller (not the VM controller) sets
`VCPUChange` / `MemoryChange` conditions when it detects a spec vs. status mismatch.
The workload-updater watches these conditions and creates migrations. This creates a
potential race: the VM controller patches the VMI spec for in-place resize, and the
VMI controller independently sets conditions that trigger the workload-updater to
create an unwanted migration.

To prevent this, the in-place resize path must signal to the workload-updater that
a migration is not needed. The recommended approach is for the VM controller to set
an annotation on the VMI (e.g., `kubevirt.io/in-place-resize-in-progress`) before
patching the pod. The workload-updater checks for this annotation and skips migration
creation when it is present. The annotation is removed after guest-level hotplug
completes.

### VMI Conditions

Existing conditions are reused, and one new condition is introduced:

| Condition | Existing? | Purpose |
|-----------|-----------|---------|
| `VCPUChange` | Yes | Indicates a CPU hotplug is in progress |
| `MemoryChange` | Yes | Indicates a memory hotplug is in progress |
| `PodResourceResizeInProgress` | **New** | Indicates Kubernetes pod resize is in progress |

The new `PodResourceResizeInProgress` condition provides observability into the
multi-stage process. For example, if `VCPUChange` is True but
`PodResourceResizeInProgress` is False, the pod resize has completed and
guest-level hotplug is pending.

### Failure Handling

**Pod resize failure**: If Kubernetes cannot resize the pod (e.g., node resource
pressure), the pod's status will reflect `PodResizePending` with a reason. The VM
controller should surface this in a VMI condition and retry.

**Guest hotplug failure**: If the pod resize succeeds but guest-level hotplug fails
(libvirt error), virt-handler should:

1. Retry with exponential backoff.
2. Emit events and update conditions to reflect the failure.
3. **Not roll back** pod resources. The user's declared intent is the larger size.
   Rolling back would break declarative reconciliation - the VM spec still requests
   the larger resources, and the controller would immediately try to resize again.
   The pod reserving more resources than the guest uses is safe (slightly wasteful,
   but correct). The user can revert the VM spec to trigger a downward adjustment if
   needed.

For context: the current migration-based path does not have explicit guest hotplug
failure handling. Guest hotplug runs during `finalizeMigration()`, so a failure
fails the migration, which the migration controller retries. The in-place path
needs its own retry logic since there is no migration to retry.

### Infeasible Resize

When Kubernetes marks a pod resize as `Infeasible` (the current node cannot accommodate
the new resource request), the VM controller surfaces a `ResizeInfeasible` condition
on the VMI. The user can then either revert the VM spec or manually trigger a
migration to a node with more capacity.

This approach follows the principle of least surprise: the user opted for `LiveUpdate`
expecting non-disruptive changes, and auto-migrating would introduce unexpected
stun-time. This is especially important for latency-sensitive workloads.

An alternative would be to automatically fall back to migration, but this risks
surprising users who specifically chose in-place resize to avoid disruption. A future
iteration could add an opt-in annotation for automatic fallback.

## API Examples

No API changes are required. The feature is controlled by the `InPlaceHotplug` feature
gate and operates transparently under the existing `LiveUpdate` rollout strategy.

Existing API fields used:
- `spec.domain.cpu.maxSockets` - upper bound for CPU hotplug (used for libvirt
  pre-configuration)
- `spec.domain.memory.maxGuest` - upper bound for memory hotplug
- `VMRolloutStrategy: LiveUpdate` - enables hotplug

Note: Kubernetes ResourceQuota admission checks apply to in-place pod resize patches.
If the namespace quota does not have room for the increased resources, the patch will
be rejected. However, unlike the migration path, in-place resize only needs quota
headroom for the delta (not for an entire second pod), which is a significant
improvement.

## Alternatives

### Keep migration as the only hotplug method

The current approach works but has all the drawbacks listed in the Motivation section.
With Kubernetes in-place resize at GA, there is no technical reason to continue
requiring migration for the common case.

### New rollout strategy instead of enhancing LiveUpdate

Instead of making in-place resize the default under `LiveUpdate`, a new strategy
(e.g., `InPlace`) could be introduced. This was rejected because:

- It adds API surface without clear benefit. In-place resize is strictly better than
  migration when it is feasible.
- It would require users to change their VM specs to opt in.
- The migration fallback for dedicated CPUs means `LiveUpdate` already implies
  "use the best available method."

## Risk and Limitations

### Dedicated CPUs

Kubernetes in-place pod resize does not support VMs with dedicated CPU placement
(static CPU/memory manager policies). The resize will be marked as infeasible by
the kubelet.

**Mitigation**: Fall back to the existing migration-based hotplug path for VMs with
`dedicatedCpuPlacement: true`. This is detected upfront in the VM controller's
decision tree, avoiding unnecessary resize attempts.

Upstream progress: [KEP #5294](https://github.com/kubernetes/enhancements/issues/5294)
is working to support in-place resize with static CPU and memory policies. Once
available in Kubernetes, this limitation can be removed in a future VEP iteration.

### Hugepages (Memory Only)

Kubernetes does not support in-place resize of hugepage resources (`hugepages-2Mi`,
`hugepages-1Gi`). VMs using hugepages can still use in-place resize for CPU hotplug,
but memory hotplug must fall back to migration.

### Pre-existing Issue: CPU Hotplug for Non-Migratable VMs

Today, CPU hotplug for non-migratable VMs (e.g., GPU passthrough) is silently
ineffective. The VM controller patches the VMI spec without checking migratability,
setting the `VCPUChange` condition. However, guest-level CPU hotplug only runs during
migration finalization (`migration-target.go:hotplugCPU`). Since the VM cannot migrate,
the condition persists indefinitely and the guest never receives the new vCPUs.

This VEP resolves this issue for non-dedicated-CPU VMs by providing an in-place
hotplug path that does not require migration.

## Scalability

In-place resize significantly improves scalability:

- Eliminates temporary migration pods, halving the peak resource consumption during
  hotplug.
- Removes the migration scheduling step, reducing load on the Kubernetes scheduler.
- Enables parallel hotplug operations without being constrained by cluster-wide
  migration limits.

## Update/Rollback Compatibility

- **Upgrade**: The feature is gated behind `InPlaceHotplug`. When disabled, behavior
  is identical to the current migration-based path.
- **Rollback**: Disabling the feature gate reverts to migration-based hotplug. No
  persistent state changes are introduced that would prevent rollback.

## Functional Testing Approach

Extend existing CPU/memory hotplug tests to run with `InPlaceHotplug` enabled,
verifying no migration is triggered and guest-level changes apply. Add tests for
non-migratable VM hotplug, migration fallback paths (dedicated CPUs, hugepages),
and failure scenarios (infeasible resize, guest hotplug failure).

## Implementation History

<!--
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha

- [ ] Feature-gated in-place resize for CPU and memory hotplug, with migration
      fallback for dedicated CPUs and hugepages.
- [ ] Hotplug support for non-migratable VMs.
- [ ] Functional tests.

### Beta

TBD

### GA

TBD
