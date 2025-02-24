# VEP #29: NIC Hotplug / Hotunplug For Namespace Admins Following VMIM Hardening

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

Currently, the NIC hotplug / hotunplug feature depends on a user-initiated migration following each NIC hotplug / hotunplug
operation.
The migration is required in order to attach / detach secondary interfaces to / from the target virt-launcher pod.

> [!NOTE]
> SR-IOV hotplug operation requires a migration in order for the
> [SR-IOV device plugin](https://github.com/k8snetworkplumbingwg/sriov-network-device-plugin) to be invoked when the
> target pod is created.

PR [kubevirt/kubevirt#13497](https://github.com/kubevirt/kubevirt/pull/13497) removed Namespace administrators'
permissions to create VirtualMachineInstanceMigration objects - thus breaking the NIC hotplug / hotunplug feature
for them in v1.5.

> [!NOTE]
> KubeVirt also supports in-place hotplug / hotunplug operations (without performing a migration) 
> by integrating with the [dynamic-networks-controller](https://github.com/k8snetworkplumbingwg/multus-dynamic-networks-controller).
>
> This method does not support SR-IOV NICs hotplug.

## Motivation

In previous KubeVirt versions, a project admin could hotplug / unplug NICs.
We want to assure that this feature will continue working in v1.5 and future versions.

## Goals

Project admins could perform NIC hotplug / unplug operations without involving the cluster-admin for every hotplug /
hotunplug operation.

## Non Goals

## Definition of Users

- Namespace administrators

## User Stories

- As a namespace admin I would like to be able to hotplug / unplug a secondary interface without involving the cluster
  admin.

## Repos

- kubevirt/kubevirt

## Design

### Integrate with LiveUpdate Rollout Strategy

The CPU and memory hotplug features use the `LiveUpdate` `RolloutStrategy` which automatically initiates a migration
of the VM when certain spec fields are changed.

Extend the feature to automatically initiate a live migration in case of NIC hotplug / hotunplug request.

#### API Additions

In order to mark the VMI for automatic migration, a new allowed value will be introduced to `VirtualMachineInstanceConditionType`:

```go
VirtualMachineInstanceVNICChange VirtualMachineInstanceConditionType = "HotVNICChange"
```

In order to keep supporting in-place hotplug / hotunplug, a cluster-wide knob will be added to the `NetworkConfiguration` struct:

```go
type NetworkConfiguration struct {
	...
	EnableInPlaceVNICChange *bool `json:"enableInPlaceVNICChange,omitempty"`
	...
}
```

A user will set it to `true` in order to let KubeVirt know that the dynamic-networks-controller is deployed.
KubeVirt will use this configuration to enable in-place hotplug / hotunplug for supported network bindings (currently `bridge`).
Doing so will enable NIC hotplug / unplug for non-migratable VMs and will provide a better user experience.

#### VM Controller
The VM controller is responsible for patching VMI objects as a result of a change in the VM object.
The VM controller contains a dedicated network VM controller, responsible for NIC hotplug / hotunplug.
The network VM controller is being executed independently of the LiveUpdate rollout strategy feature.

The network VM controller sync will be invoked under the same conditions the CPU and memory handlers are being executed.
As a result, the logic responsible for setting the `RestartRequired` condition on the VM will be updated.

#### Network VM controller
The network VM controller will return early in the following cases:
- The VMI has the `HotVNICChange` condition set to `true` - meaning a hotplug / hotunplug operation is already underway.
- The VMI is currently migrating.

#### VMI Controller
The VMI controller is responsible for two flows:
1. Updating the VMI.Status field.
2. Patching the virt-launcher pod when it is ready with an updated Multus network attachment annotation (used for in-place hotplug).

The logic responsible for calculating the VMI network status will be adjusted to mark the VMI for automatic migration by adding the `HotVNICChange` condition to the VMI.

The logic of whether to add the condition will depend on the value of the newly introduced `EnableInPlaceVNICChange` knob:

Default behavior (without in-place hotplug / hotunplug):
The condition will be added in case at least a single secondary NIC was hotplugged / hotunplugged.

With in-place hotplug / hotunplug:
The condition will be added in case at least a single secondary NIC using SR-IOV binding was hotplugged / hotunplugged.

The same logic will also be responsible for removing the condition when it is unneeded.

#### Workload Update Controller

Similarly to the CPU and memory hotplug features, a VMI that has the `HotVNICChange` condition set to `true`:
1. Shall be considered for automatic migration.
2. Shall not be considered for migration abortion.

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

<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

Users will have to configure their KubeVirt configuration similarly to CPU and memory hotplug in order to use the automatic
migration.
The manual migration will no longer achieve the desired affect.
Users using the dynamic-networks-controller will have to adjust their KubeVirt configuration.

## Functional Testing Approach

The migration-based hotplug / hotunplug tests will be adjusted to remove the manual VMIM object creation.
The in-place hotplug / hotunplug tests will be adjusted to assert that a migration did not occur.

## Implementation Phases

1. Introduction of the new API.
2. VMI controller changes.
3. Workload update controller changes.
4. Adjusting the e2e tests to work without dynamic-networks-controller.
5. VM controller changes.
6. Network VM controller changes.
7. Adjusting the e2e tests to work with the dynamic-networks-controller.

## Feature lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

### Beta

### GA
Since the LiveUpdate rollout strategy and NIC hotplug / hotunplug features are already GAed, their integration does not
pose a significant risk.
