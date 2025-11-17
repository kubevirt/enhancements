# Overview
Implement support for IBM Z Secure Execution VMs in kubevirt

## Motivation
To enable customers to deploy secure workloads on their IBM Z machines using kubevirt.

## Goals
To enable kubevirt to run Secure Execution VMs on s390x

## Non Goals
Changing how existing VMs run.

## Definition of Users
- IBM Z users wanting to secure their new VMs using Secure Execution
- IBM Z users wanting to migrate existing Secure Execution VMs to kubevirt

## User Stories
(list of user stories this design aims to solve)

## Repos
https://github.com/kubevirt/kubevirt
https://github.com/kubevirt/api

# Design

The feature should be behind a Feature Gate at first, similiar to AMD SEV.
For implementing this feature, the api behaviour would need to be changed to allow for setting an empty `launchSecurity: {}`.

For launching Secure Execution VMs, all devices need to have `iommu=on` set to allow them to be visible to the guest, as in Secure Execution Mode the hypervisor is prevented from accessing the guest memory. Otherwise Secure Execution is initiaded from the guest image during boot, not the hypervisor.

To ensure that VMs are scheduled only on nodes supporting Secure Execution, node-labeller should add a label to these nodes. This can be done by parsing the output of `virsh domcapabilities`.
When a Secure Execution VM is created, the SE label will then be added to the virt-launcher pod so that it will be scheduled on a node which has the feature enabled.

Due to the access restriction of the guest memory, live migration is not possible. Therefore the `NonMigratableCondition` will be added to the VMs.

## API Examples
```
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  ...
spec:
  domain:
    launchSecurity: {}
  ...
```

## Scalability
It should scale like normal VMs.
The only restriction would be for Secure Execution VMs, as they ´can't run on IBM Z machines that they have not been encrypted with.
To move a Secure Execution VM to a new IBM Z machine, it's public key would first need to be added to the keys used to encrypt the kernel+initramfs+parmfile.

## Security
This design improves Security by allowing users to protect their IBM Z Workloads with Secure Execution.

## Update/Rollback Compatibility
This feature would not affect existing/new VMs as long as they do not depent on it.

## Functional Testing Approach
- As much as possible should the feature be tested in unit-tests
- Upstream tests would be difficult and likely require additional hardware, as Secure Execution does not work with nested virtualization and requires being run on LPAR (Bare Metal).
