# VEP #29: Integrate NIC Hotplug with LiveUpdate Rollout Strategy

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

Currently, the NIC [hotplug / hotunplug feature](https://github.com/kubevirt/community/blob/a4eb40ce2c0372a49c39e8289fb3d18ca3f30512/design-proposals/nic-hotplug/nic-hotplug.md)
depends on a user-initiated migration following each NIC hotplug / hotunplug operation.
The migration is required in order to attach / detach secondary interfaces to / from the target virt-launcher pod.

> [!NOTE]
> KubeVirt also supports in-place hotplug / hotunplug operations (without performing a migration)
> by integrating with the [dynamic-networks-controller](https://github.com/k8snetworkplumbingwg/multus-dynamic-networks-controller).
>
> This method does not support hot-plugging SR-IOV NICs, as SR-IOV hotplug operation requires a migration in order for the
> [SR-IOV device plugin](https://github.com/k8snetworkplumbingwg/sriov-network-device-plugin) to be invoked when the
> target pod is created.

PR [kubevirt/kubevirt#13497](https://github.com/kubevirt/kubevirt/pull/13497) removed Namespace administrators'
permissions to create VirtualMachineInstanceMigration objects - thus breaking the NIC hotplug / hotunplug feature
for them in v1.5.

## Motivation

In previous KubeVirt versions, a namespace admin could hotplug and unplug NICs.
We want to ensure that this feature will keep working in v1.5 and future versions and enhance it so the migration will be
automatically performed - without user intervention.

## Goals

- Integrate the NIC hotplug / hotunplug with the LiveUpdate rollout strategy for consistent user experience.
- Enable namespace admins to perform NIC hotplug / unplug operations independently - without involving the cluster-admin 
for each hotplug / hotunplug operation.

## Non Goals

## Definition of Users

- Namespace administrators

## User Stories

As a namespace admin I would like to be able to:
- Hotplug / unplug a secondary interface without involving the cluster admin.
- Hotplug a new secondary network interface that uses bridge binding.
- Hotunplug a secondary network interface that uses bridge binding.
- Hotplug a new secondary network interface of type SR-IOV.
- Use the staging rollout strategy so any hotplug / hotunplug operation will take effect only after restarting a VM.

## Repos

- kubevirt/kubevirt

## Design

### Integrate with LiveUpdate Rollout Strategy

The CPU and memory hotplug features use the `LiveUpdate` [rollout strategy](https://kubevirt.io/user-guide/user_workloads/vm_rollout_strategies/)
which automatically initiates a migration of the VM when certain spec fields are changed.

Extend the feature to automatically initiate a live migration in case of NIC hotplug / hotunplug request.

#### API Additions

Currently, the CPU, memory and volume hotplug / hotunplug flows have a dedicated condition on the VMI object to mark the VMI
for automatic migration:
- HotVCPUChange
- HotMemoryChange
- VolumesChange

Since we do not wish to introduce another resource-specific condition, a new generic allowed value will be added to `VirtualMachineInstanceConditionType`:

```go
VirtualMachineInstanceMigrationRequired VirtualMachineInstanceConditionType = "MigrationRequired"
```

Existing hotplug / hotunplug flows could be adjusted to use it.

#### Migration Based Hotplug Flow

1. The VM owner adds one or more new secondary vNICs and matching networks to a running VM.
2. The VM controller patches the VMI object with the additional relevant vNICs and networks.
3. The VMI controller:
   
   - Patches the virt-launcher pod's Multus network attachment annotation (`k8s.v1.cni.cncf.io/networks`) - since the dynamic-networks-controller is not present, this will not have an affect.
   - Updates the VMI interface status.
   - Adds the `MigrationRequired` condition to the VMI with its status set to `False` and a timestamp.
4. On a future iteration of the reconciliation loop, the VMI controller will update the `MigrationRequired` condition status to `True` (Following no change in the interface statuses for more than TBD seconds after the initial timestamp set on the condition).
5. The Workload update controller will identify the VMI as a candidate for automatic migration and will create a VMIM object for it.
6. The Migration controller will remove the `MigrationRequired` condition once the migration is complete.
7. The vNICs will be hot-plugged following the migration - same as today. 

#### Migration Based Hotunplug Flow

1. The VM owner sets the state one or more existing secondary vNICs to `absent`.
2. The VM controller patches the VMI object with the relevant interfaces and networks.
3. The VMI controller:

    - Patches the virt-launcher pod's Multus network attachment annotation (`k8s.v1.cni.cncf.io/networks`) - since the dynamic-networks-controller is not present, this will not have an affect.
    - Updates the VMI interface status.
    - Adds the `MigrationRequired` condition to the VMI with its status set to `False` and a timestamp.
4. On a future iteration of the reconciliation loop, the VMI controller will update the `MigrationRequired` condition status to `True` (Following no change in the interface statuses for more than TBD seconds after the initial timestamp set on the condition).
5. The Workload update controller will identify the VMI as a candidate for automatic migration and will create a VMIM object for it.
6. The Migration controller will remove the `MigrationRequired` condition once the migration is complete.
7. The vNICs will be hot-unplugged following the migration - same as today.

#### In-Place Hotplug / Hotunplug Flow

1. The VM owner adds one or more new secondary vNICs with bridge binding and matching networks, or sets their state to `absent` to / on a running VM.
2. The VM controller patches the VMI object with the additional relevant vNICs and networks.
3. The VMI controller:

    - Patches the virt-launcher pod's Multus network attachment annotation (`k8s.v1.cni.cncf.io/networks`).
    - Updates the VMI interface status.
    - Adds the `MigrationRequired` condition to the VMI with the `False` with its status set to `False` and a timestamp.
4. The dynamic-networks-controller will observe the change to the Multus network attachment annotation and will invoke Multus.
5. On a future iteration of the reconciliation loop, the VMI controller will observe a change in the interface statuses and will remove the `MigrationRequired` condition.
6. The virt-handler will set up / remove the necessary plumbing.
7. The virt-launcher will attach / detach the relevant interfaces to / from the domain.

> [!NOTE]
> In case one or more SR-IOV NICs are hotplugged - the VMI controller will immediately mark the VMI for automatic migration
> by adding the `MigrationRequired` condition with the `True` status.
> This is because the dynamic-networks-controller cannot handle hot-plug of SR-IOV devices.
> 
> SR-IOV hotunplug is currently unsupported.

> [!NOTE]
> The time it takes for:
> 1. The dynamic-networks-controller to observe the change and invoke Multus.
> 2. Multus to invoke its delegate CNIs.
> 3. Delegate CNIs to perform their job.
> 4. Multus to report its status on the pod's annotations.
> 5. VMI controller to observe and report the interface statuses.
> 
> Is unbounded and may be longer than the timeout defined before an automated migration is requested.
> It may cause the VMI to migrate even if an in-place operation took place.


#### Pros

- Improves the current user experience
- Will work for hotplugging interfaces using bridge and SR-IOV bindings
- Keeps the integration with the dynamic-networks-controller

#### Cons
 - Will not work for non-migratable VMs
 - Performs a migration for every hotplug / hotunplug
 - Requires an API change

## API Examples

<!--
Tangible API examples used for discussion
-->

## Alternatives

### Using the dynamic-networks-controller

The dynamic-networks-controller is a controller, running as a [DaemonSet](https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/),
listening to changes on Pods and invoking the [Multus v4 CNI](https://github.com/k8snetworkplumbingwg/multus-cni).

It enables NIC hotplug / hotunplug operations in place, e.g. without creating another pod.
A known limitation of the controller above is that it cannot hotplug interfaces that require invocation of
[Kubernetes device plugins](https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/),
for example: SR-IOV network interfaces.

#### Pros

- Improved user experience
- No need for migration when attaching/detaching interfaces using bridge binding
- Works on non-migratable VMs
- Does not require changes to kubevirt/kubevirt

#### Cons

- The dynamic-networks-controller is an external component that needs to be deployed and managed
- Not applicable for hotplugging SR-IOV interfaces

### Providing Namespace Admins the required RBAC Rules

Cluster admins could provide Namespace admins the permissions to create VMIM objects.
The documentation will be updated to reflect the fact that there are additional RBAC rules missing for namespace
admins to be able to perform NIC hotplug / unplug.

#### Pros

- Does not require code changes

#### Cons

- Losses the benefits of limiting VMIM creation to cluster admins
- Requires cluster-admins to take action

## Scalability

N/A.

## Update/Rollback Compatibility

Users will have to configure their KubeVirt configuration similarly to CPU and memory hotplug in order to enable the automatic
migration.

## Functional Testing Approach

The migration-based hotplug / hotunplug tests will be adjusted to remove the manual VMIM object creation.
The in-place hotplug / hotunplug tests will be adjusted to assert that a migration did not occur.

## Implementation Phases

1. Support automatic migration on NIC hotplug / hotunplug (without dynamic-networks-controller).

## Feature lifecycle Phases

### GA
Planned for v1.6.
Since the LiveUpdate rollout strategy and NIC hotplug / hotunplug features are already GAed, their integration does not
pose a significant risk.

This integration could also be considered as a bug fix to restore namespace admins' ability to perform NIC hotplug / hotunplug.
