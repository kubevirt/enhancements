# VEP #3: Adding Link State Management for vNICs

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

The purpose of this enhancement is to introduce support for link state management (up/down) for virtual NICs in KubeVirt.
This feature will enable VM owners to dynamically control the link state. It aligns KubeVirt with traditional 
virtualization platforms and enhances its utility in environments requiring precise network state control.

By introducing link state management, KubeVirt will:
- Provide VM-level controls to configure the link state of individual interfaces.
- Report link state info via the VirtualMachineInterface status.

This proposal outlines the necessary changes to KubeVirt, including API changes.

## Motivation

Currently, KubeVirt does not support modifying or reporting the link state of virtual network interfaces.
This limitation can lead to challenges in implementing advanced networking scenarios, such as:
- Simulating link failures for testing and debugging.
- Managing multi-network setups, where some interfaces need to be intentionally disabled.

## Goals

1. Allow users to configure the link state (up/down) of individual interfaces through the KubeVirt API.
2. Enable link state configuration at both VM creation and runtime.
3. Report the current domain link state of each VM interface.

## Non Goals

1. Affect network plumbing on the host - the setup process will remain unchanged.
2. Manage and report the link state of SR-IOV NICs.
3. Report the link state of the underlying network provider.
4. Report the link state of interfaces inside the guest operating system.

## User Stories

As a VM owner I would like to be able to:

- Start the VM with one or more interfaces with their link set to `down`.
- Toggle the link state of one or more interfaces of a running VM.
- Hot plug an interface with a link state set to `down`.
- Control the link state of interfaces connected to primary and secondary networks.

- I want my interfaces link states to persist following a migration.
- I want the domain link state to be reported per VMI.

## Repos

kubevirt/kubevirt.

## Design

### API Addition
Currently, the `Interface` struct has the `State` field (currently used for hot-unplug):
```go
type Interface struct {
    ...
    State InterfaceState `json:"state,omitempty"`
    ...
}
```

Type `InterfaceState` currently has a single allowed value:  
```go
type InterfaceState string

const (
	InterfaceStateAbsent InterfaceState = "absent"
)
```

The following values will be added to specify the required link state:
```go
const (
    InterfaceStateLinkUp   InterfaceState = "up"
    InterfaceStateLinkDown InterfaceState = "down"
)
```

An empty value will be considered as `up`.

The network validator will be adjusted to allow the two new values for all interfaces regardless of whether they are 
connected to primary or secondary networks, or whether they use a core binding or a binding plugin.

> [!IMPORTANT]
> Controlling the link state of SR-IOV NICs will not be supported as we cannot control their link state via the domain.
> 
> Setting the desired link state of an SR-IOV NIC will not be permitted (enforced by the validating webhooks), as it is not currently possible setting it in the domain. 

> [!WARNING]  
> When HTTP / TCP readiness and/or liveness probes are specified on the VM, setting the primary interface's link state to `down` will
> cause the VM:
> 1. To be marked as not ready (readiness probe)
> 2. To be restarted, as the kubelet will kill the virt-launcher pod (liveness probe)

virt-launcher's `Converter` component will be adjusted to take the interface State field into account when creating a new domain XML.
In case the field's value is `InterfaceStateLinkDown`, the interface will be created as follows:
```xml
<interface>
  ...
  <link state='down'/>
  ...
</interface>
```

> [!NOTE]
> For additional details please see libvirt's [documentation](https://libvirt.org/formatdomain.html#modifying-virtual-link-state).

virt-launcher's `LibvirtDomainManager.SyncVMI` will be extended to support updating the interfaces link state while the VM is running.

The new logic will:
1. Fetch the domain.
2. For each interface in the VMI spec:
 - Compare the actual link state against the desired link state.
 - Adjust the interface entry in the domain in case there is a need to take action.

virt-launcher's NIC hotplug logic will be adjusted to support hot-plugging a NIC while its link state is down.

### Network Binding Plugins Support

When using the functionality purposed by this enhancement with network binding plugins using a sidecar container -
it is up to the plugin's vendor to support this functionality - as they control the interface section in the domain XML.

### Link State Reporting

A new field will be added to the VMI interface status that will be used for reporting the interface's current link state (up/down):
```go
type VirtualMachineInstanceNetworkInterface struct {
	...
	LinkState string `json:"linkState,omitempty"`
	...
}
```

virt-handler will be responsible for reporting this field's value, based on domain spec information received from virt-launcher.
An empty specification (as exists today) would be considered as `up`.

SR-IOV NICs' link state will not be reported, as it could change without KubeVirt awareness.

> [!NOTE]
> The linkState information will be reported for interfaces which are present in the VirtualMachineInstance's spec.
> Interfaces defined inside the guest will not have their link state reported.
>
> Changing the link state of an interface inside the guest or in the underlying network provider - will not affect this report:
> 
> 1. KubeVirt is not aware of network provider internal state.
> 2. qemu guest agent does not report the interface link states inside the guest. 


## API Examples
### Controlling The Link State
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: default
              state: down
              masquerade: { }
      networks:
        - name: default
          pod: { }
```
### Link State Reporting
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vm
spec:
  domain:
    devices:
      interfaces:
      - name: default
        state: down
        masquerade: { }
  networks:
  - name: default
    pod: { }
status:
  interfaces:
    - name: default
      linkState: down
```

## Scalability

This enhancement does not affect scalability.

## Update/Rollback Compatibility

As this functionality will require the cooperation of the virt-launcher component, it could only be supported by
up-to-date virt-launcher pods.

## Functional Testing Approach

The following e2e scenarios will be added:
- A VM started with interface with a link state set to `down`, then changed to `up`.
- Setting a link state of a running VM to `down`, then `up`.
- Hot-plugging an interface connected to a secondary network with link state set to `down`, then changed to `up`.
- Migrating a VM that has an interface with a link state set to `down`, then changed to `up`.

## Implementation Phases

1. Addition of the new `InterfaceState` values.
2. Adjusting the network validator to accept the new `InterfaceState` values.
3. Adjusting virt-launcher:
   - On creation
   - When VM is running
   - On hotplug
4. Addition of e2e tests.
5. Addition of the `LinkState` field to `VirtualMachineInstanceNetworkInterface`.
6. Adjusting virt-handler's reporting logic.
7. Adjusting e2e tests.

## Feature lifecycle Phases

### GA (v1.5.0)
1. The enhancement proposes extending an existing field, which has a validation infrastructure, and is propagated all the
way to the virt-launcher level.
2. The mechanisms used to set the link state are already used by the virt-launcher.
3. Reporting the link state in the interface status is a natural addition to the reported details.

Both the API and functionality changes are small and straight-forward with no special implications that may be considered
as a risk.
