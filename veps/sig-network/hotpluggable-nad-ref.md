# VEP #140: Live Update NetworkAttachmentDefinition Reference

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

VMs using secondary networks refer to a NetworkAttachmentDefinition object's namespace and/or name as part of their network specification. 
Currently, the whole network specification, including this reference - is immutable for the lifecycle of a VM.
If a user wishes to change the network a VM is connected to (example: switching VLANs), they must modify the VM specification and restart the VM. 
This proposal introduces the ability to update the NAD reference (networkName) of a network on a running VM through Live Migration. 
This allows the host network connection to change without any reboot.

## Motivation

Currently, in order to move VMs from one network to another, the user either needs to hotplug a new interface and unplug the old one or updated the NAD reference and reboot the VM.
We want to implement a way to do this without reboot and without a change in the guest interfaces.

## Goals

- Change an existing NAD reference (`spec.networks[].multus.networkName`) on a running VM.

## Non Goals

- Migrating between incompatible CNI types
- Verifying that the NAD corresponding to the new NAD reference exists
- Handling failed migration
- Maintain seamless network connectivity

## Definition of Users

- Cluster admins
- VM owners

## User Stories

- As a Cluster Admin, I should be able to re-assign VMs to a new VLAN ID, without having to reboot my VMs.
- As a VM Owner I do not want to lose my VMs in case of a failed migration

## Repos

kubevirt/kubevirt

## Design

The VEP proposes to trigger migration on changing the NAD reference (`networkName`), rather than requiring restart.

#### Adjusting when the `RestartRequired` flag is added
Currently, during the Sync cycle, the VM controller in virt-controller checks if a `RestartRequired` flag should be added.
This in turn calls various sub-logics that check for changes in areas like disk, volume, network, memory etc and decide if that requires a restart. The sub-logic for network
that compares the interface and network specs of the VMI (currently running instance) to the VM (changed by the user) would be updated to not return the restart flag if just the NAD reference (`networkName`) field has been changed

#### Copying network specs from VM to VMI
The same Sync cycle then copies various specs like network, firmware etc from the VM to the VMI.
The logic that deals with copying the interfaces/network specs checks if interfaces are being hotplugged or modified
and copies the interfaces from VM to VMI specs accordingly. This would be modified to copy the network specs as well.

#### Adding the correct migration flags
During the, the VMI controller in virt-controller evaluates the migration state. Changes to interfaces and networks can trigger 3 states - immediate/pending/no migration.
In case of hotplug/unplug of interfaces with supported bindings it returns either immediate migration flag (for SIOV interfaces) or pending migration (which is a delayed migration - providing enough time for the `Dynamic Network Controller` to do an `InPlace` hotplug if applicable).
This would be updated to trigger immediate migration in case the new NAD reference is of SRIOV binding and pending migration in case it is of bridge binding type.
In case of SRIOV the migration controller as usual will create the target pod with the new specs 
In case of bridge binding the migration condition false initially due to the pending migration returned.
After the VM controller's reconciliation has copied the change in networkName to the VMI, the VMI controllers sync cycle will update
the `k8s.v1.cni.cncf.io/networks` annotation of the existing pod. Dynamic Network Controller will catch this change and perform the necessary actions to update the pod network.


#### Pros
- Users can change the network of their VMs without a reboot

#### Cons
- Unlike the other migrations we support now (where failed migration due to things like resource constraints will eventually succeed when the resources free up), in case the new NAD does not exist the migration will fail. 
The reconciliation loop will keep trying the migration forever.

## API Examples

The user updates the `networkName` in the VirtualMachine spec.

### nad-with-vlan10
```
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition 
metadata:
  name: nad-with-vlan10 
  namespace: default
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
```
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition 
metadata:
  name: nad-with-vlan20 
  namespace: default
spec:
  config: '{
      ...
      "name": "nad-with-vlan20",
      "type": "bridge",
      "bridge": "br1",
      "vlan": 20,
    }'
```
### Original VM
```
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
```
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

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Scalability

This enhancement relies on the existing Live Migration capabilities and does not introduce new scalability constraints.

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

## Functional Testing Approach

The following scenarios are to be added to the e2e tests:
- Change the NAD reference on a VM and verify that the VM is now connected to the new network and has connectivity.
- Attempt to change the reference to a non-existent NAD. Verify migration is blocked without killing the VM.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha v1.9
- [ ]  Protect all changes with feature gate `liveUpdateNADRef` 
- [ ]  Gather user feedback 

### Beta v1.10
- [ ] Feature gate stays
- [ ] New VEP is written in case unaddress scenarios are identified (and decided to be addressed) from user feedback

### GA v1.11

- [ ] Feature gate is removed