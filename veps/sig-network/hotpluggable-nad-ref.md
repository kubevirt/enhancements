# VEP #140: Live Update NetworkAttachmentDefinition Reference

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Target version is explicitly mentioned and approved
- [X] (R) Graduation criteria filled

## Overview

VMs using secondary networks refer to a NetworkAttachmentDefinition object's namespace and name as part of their network specification. 
Currently, if user wishes to change the network a VM is connected to, they modify the VM specification and must restart the VM. 
This proposal introduces the ability to update the NAD reference (networkName) of a network on a running VM through Live Migration. 
This allows the host network connection to change without any reboot.

## Motivation

Currently, in order to move VMs from one network to another, the user either needs to hotplug a new interface and unplug the old one or updated the NAD reference and reboot the VM.
Restart would impact the workload and hotplugging an interface would mean a change to the VM interface properties like MAC address.
This proposal would support it without reboot and without a change in the guest interfaces.

## Goals

- Change an existing NAD reference (spec.networks[].multus.networkName) of secondary networks using bridge binding on a running VM, and by extension the pod network plumbing.

## Non Goals

- Migrating between CNI types
- Changing the network binding/plugin
- Maintaining seamless network connectivity
- Changing NAD reference on non-migratable VMs
- Changing the guest's network configuration in case it have to change following the underlying network change.
- Limiting migration retries because of missing Network Attachment Definition

## Definition of Users

- VM owners

## User Stories

- As a VM owner, I should be able to re-assign VMs to a new VLAN ID, without having to reboot my VMs.

## Repos

kubevirt/kubevirt

## Design

The VEP proposes to allow changing the NAD reference (`networkName`) and trigger a migration as a result, rather than requiring restart of the VM. 
A new Feature gate `LiveUpdateNADRef` will be introduced to enable this feature.

### Adjusting when the RestartRequired condition is added
Currently, during the Sync cycle, the VM controller in virt-controller checks if a `RestartRequired` condition should be added.
This in turn calls various sub-logics that check for changes in areas like disk, volume, network, memory etc and decide if that requires a restart. The sub-logic for network
would be updated to not require the `RestartRequired` condition if just the NAD reference (`networkName`) field has been changed.

### Copying network specs from VM to VMI
During the same Sync cycle, a logic deals checks if interfaces are being hotplugged or modified
and copies the interfaces specs from VM to VMI specs accordingly. This would be adjusted to sync changes made to the `networkName` field as well.

### Adding the correct migration conditions
During the updateStatus cycle, the VMI controller in virt-controller evaluates the automatic migration request. Changes to interfaces and networks can trigger 3 states - immediate/pending/no migration.
In case of hotplug/unplug of interfaces with supported bindings it requests either immediate migration condition (for SR-IOV interfaces) or pending migration (which is a delayed migration - 
providing enough time for the [Dynamic Networks Controller](https://github.com/k8snetworkplumbingwg/multus-dynamic-networks-controller), if present to do an `InPlace` hotplug). We would follow a similar approach for swapping the NAD reference.
So, this would be updated to request an immediate migration in case the interface connected to the changed network uses bridge binding.
In case the VMI is live-migratable, as before, the WorkloadUpdateController will initiate a migration. Since the new NAD reference is now present on the VMI specs, the target pod's multus annotation 
will refer to the new NAD.

### Compatibility with clusters that uses Multus Dynamic networks Controller
#### Option 1
In order to support in-place hotplug and hot unplug using the Dynamic Networks Controller (DNC) the virt-controller updates the 
multus annotation of the pod based on the difference between the VMI interface status and the specs. DNC then calculates the diff between the new and old annotation, attaches any new network and then removes deleted networks. 
So trying to change the annotation from `'[{"name":"nad-with-vlan10","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]'` to `'[{"name":"nad-with-vlan20","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]'` will not work, as DNC will try to attach `nad-with-vlan20`
without removing `nad-with-vlan10` causing a conflict in mac and interface id and thus failing the attachment.

To prevent that, the reconciliation logic in virt-controller will be adjusted to not patch the annotation when only the NAD reference of an existing interface is updated, 
in order to not break the existing hot-plug / hot-unplug flows.

##### Pros
- Users can change the network of their VMs without a reboot, even on clusters with Dynamic Networks Controller installed.

##### Cons
- In Place swapping of NAD referenced will not be possible even with Dynamic Networks Controller installed.

#### Option 2
Instead of restricting update of source pod's network annotation, the logic in Dynamic Networks Controller would be changed to enable swapping networks (instead of adding and then removing).
The rest of the design changes remain the same.

##### Pros
- In Place swapping of NAD referenced will be possible with Dynamic Networks Controller installed.
- Changing the NAD reference of non-migratable VMs when bridge binding is used - would be possible.

##### Cons
- Dynamic Networks Controller is a third party component, so amending its logic requires additional effort. Also, not all deployments use this component


## API Examples

The user updates the `networkName` in the VirtualMachine spec.

### nad-with-vlan10
```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition 
metadata:
  name: nad-with-vlan10 
spec:
  config: '{
      ...
      "name": "nad-with-vlan10",
      "type": "bridge",
      "bridge": "br1",
      "vlan": 10,
    }'
```
### nad-with-vlan20
```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition 
metadata:
  name: nad-with-vlan20 
spec:
  config: '{
      ...
      "name": "nad-with-vlan20",
      "type": "bridge",
      "bridge": "br2",
      "vlan": 20,
    }'
```
### Original VM
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: test-vm
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
          - name: bridge-net
            bridge: {} 
      networks:
      - name: bridge-net
        multus:
          networkName: nad-with-vlan10
```
### Updated VM
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: test-vm
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
          - name: bridge-net
            bridge: {} 
      networks:
      - name: bridge-net
        multus:
          networkName: nad-with-vlan20
```

## Scalability

This enhancement relies on the existing KubeVirt capabilities and does not introduce new scalability constraints.

## Update/Rollback Compatibility

This enhancement relies on the existing KubeVirt capabilities and does not introduce new update/rollback constraints.

## Functional Testing Approach

The following scenarios are to be added to the e2e tests:
- Change the NAD reference on a VM and verify that the VM is now connected to the new network and has connectivity.

## Graduation Requirements

### Beta v1.8
- [X]  Implementation for bridge binding scenario 
- [X]  E2E testing
- [X]  Upstream documentation

### GA v1.9
- [X] Positive feedback from the community
- [X] Support for additional bindings are considered
