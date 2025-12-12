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

- Change an existing NAD reference (spec.networks[].multus.networkName) on a running VM, and by extension the pod network plumbing.

## Non Goals

- Migrating between incompatible CNI types
- Maintain seamless network connectivity

## Definition of Users

- VM owners

## User Stories

- As a VM owner, I should be able to re-assign VMs to a new VLAN ID, without having to reboot my VMs.

## Repos

kubevirt/kubevirt

## Design

The VEP proposes to allow changing the NAD reference (`networkName`) and trigger a migration as a result, rather than requiring restart.

#### Adjusting when the `RestartRequired` condition is added
Currently, during the Sync cycle, the VM controller in virt-controller checks if a `RestartRequired` condition should be added.
This in turn calls various sub-logics that check for changes in areas like disk, volume, network, memory etc and decide if that requires a restart. The sub-logic for network
would be updated to not require the `RestartRequired` condition if just the NAD reference (`networkName`) field has been changed.

#### Copying network specs from VM to VMI
During the same Sync cycle, a logic deals checks if interfaces are being hotplugged or modified
and copies the interfaces specs from VM to VMI specs accordingly. This would be adjusted to sync changes made to the `networkName` field as well.

#### Adding the correct migration conditions
During the updateStatus cycle, the VMI controller in virt-controller evaluates the migration state. Changes to interfaces and networks can trigger 3 states - immediate/pending/no migration.
In case of hotplug/unplug of interfaces with supported bindings it requests either immediate migration condition (for SR-IOV interfaces) or pending migration (which is a delayed migration - providing enough time for the `Dynamic Networks Controller`, if present to do an `InPlace` hotplug).
This would be updated to request an immediate migration in case the interface connected to the changed network uses either SR-IOV or bridge binding.

#### Compatibility with clusters using Dynamic Networks Controller
##### Restricting update of source pod's network annotation
The sync cycle of the VMI controller in virt-controller has a logic to reconcile the pod multus network annotation (`k8s.v1.cni.cncf.io/networks`) with the network and interface specs of the VMI.
It calculates the multus annotation based on the difference between the VMI interface status and the specs. In case this calculated annotation is not the same as that on the pod,
it overwrites the annotation. So if the current annotation on the pod was `k8s.v1.cni.cncf.io/networks:[{"name":"nad-with-vlan10","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]`
and the networkName in the VMI spec changes `nad-with-vlan20`, the pod will get annotation `k8s.v1.cni.cncf.io/networks:[{"name":"nad-with-vlan20","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]` after reconciliation.
In clusters with Dynamic Networks Controller (DNC) installed this can cause a problem.
DNC first calculates the diff between the desired and current `k8s.v1.cni.cncf.io/networks` annotation of the pod, attaches any new network and then removes deleted networks. So trying to change the annotation from
`'[{"name":"nad-with-vlan10","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]'` to `'[{"name":"nad-with-vlan20","namespace":"default","mac":"02:87:f5:51:4c:5d","interface":"poda1363d52898"}]'` will not work, as DNC will try to attach `nad-with-vlan20`
without removing `nad-with-vlan10` causing a conflict in mac and interface id and thus failing the attachment.

This reconciliation logic will be adjusted to not patch the annotation in case the current and desired values differ only by the NAD reference.

#### Pros
- Users can change the network of their VMs without a reboot.

#### Cons
- In Place swapping of NAD referenced will not be possible even with Dynamic Networks Controller installed.

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

## Scalability

This enhancement relies on the existing Live Migration capabilities and does not introduce new scalability constraints.

## Update/Rollback Compatibility

This enhancement relies on the existing Live Migration capabilities and does not introduce new update/rollback constraints.

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

### Alpha v1.8
- [ ]  Protect all changes with feature gate `liveUpdateNADRef` 
- [ ]  Gather user feedback 

### Beta v1.9
- [ ] Feature gate stays
- [ ] New VEP is written in case unaddress scenarios are identified (and decided to be addressed) from user feedback

### GA v1.10

- [ ] Feature gate is removed