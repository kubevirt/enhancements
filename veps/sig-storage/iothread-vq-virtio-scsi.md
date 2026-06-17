# VEP #342: IOThread Virtqueue Mapping for virtio-scsi

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

<!--
Provide a brief overview of the topic
-->

IOThread Virtqueue Mapping allows for multiple dedicated I/O threads to process an individual disk's virtqueues to improve I/O performance. Prior to libvirt 11.2.0 (QEMU 10.0), iothread-vq-mapping was exclusively supported for virtio-blk devices. This enhancement aims to extend the virtio-scsi controller to leverage the libvirt changes to enable multi I/O threads.

## Motivation

<!--
Why this enhancement is important
-->

While virtio-blk offers a higher I/O performance compared to virtio-scsi, it lacks in disk scalability since each disk must be directly mapped to an individual PCI slot. For VM workloads that require a large amount of disks, users can leverage virtio-scsi which only uses a single PCI slot for a central controller that then manages all the virtio-scsi disks.

However, currently the SCSI Controller only gets allocated (at most) a single IOThread, so every virtio-scsi device competes for this single thread. For VMs that contain potentially hundreds of SCSI disks, all performing intensive I/O operations, this can quickly become a CPU bottleneck.

## Goals

<!--
The desired outcome
-->

* Leverage existing `IOThreadsPolicies` API (shared, auto, supplementalPool), to extend the SCSI Controller to expose dedicated IOThreads for virtio-scsi devices.
* Add opt-in feature gate.
* Complete performance testing to verify that virtio-scsi disks benefit from the inclusion of iothread-vq-mapping, similar to what was seen for virtio-blk.

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

* Changes to existing IOThread mapping for virtio-blk
* Changes to existing `IOThreadsPolicy` API

## Definition of Users

<!--
Who is this feature set intended for
-->

Cluster users running VMs that contain multiple virtio-scsi disks performing heavy I/O operations.

## User Stories

<!--
List of user stories this design aims to solve
-->

* As a user I would like to dedicate multiple IOThreads for my virtio-scsi disks by leveraging iothread-vq-mapping in order to gain a performance increase during heavy I/O operations.

## Repos

<!--
List of repose this design impacts
-->

[kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

The SCSI Controller needs to be extended to accept a list of IOThreads so that the resulting controller's domain xml can include the dedicated threads and their associated ids. From here, libvirt will automatically map the provided threads to the virtqueues.

```xml
<controller type='scsi' index='0' model='virtio-scsi'>
  <driver queues='4'>
    <iothreads>
      <iothread id='1'/>
      <iothread id='2'/>
      <iothread id='3'/>
    </iothreads>
  </driver>
</controller>
```

The challenge here is that the SCSI controller can only assign threads to virtqueues (which currently is set to the number of vCPUs in a VMI) and not to individual disks like with virtio-blk. We can leverage the existing `IOThreadsPolicy` API to enable a multi-threaded SCSI controller that can process the virtqueues in parallel however, we cannot guarantee the same 1:1 thread to disk mapping which slightly changes how the policies will behave with SCSI disks.


The following is an overview of how the SCSI controller would consume the following policies.

### Policy: shared
Each disk shares the same IOThread. With these proposed changes, we would assign this same thread to be also shared by the controller.

### Policy: auto
Each disk (excluding ones that request dedicatedIO) get assigned a thread in round robin order from the list of available iothreads. The total number of threads is equal to the amount of disks in a VMI, capped at 2 * (# of vCPUs). Since the SCSI controller cannot assign individual threads to its disks, we would instead allocate all of the "auto threads" (threads not reserved for dedicatedIO disks) to the controller. 

### Policy: supplementalPool
Each disk gets access to a pool of threads. Same as with auto policy, the SCSI controller can only allocate threads to the virtqueues, so the entire supplemental thread pool would become shared with the SCSI controller.

Note: the SCSI controller will create and manage virtqueues equal to the number of vCPUs for a given VMI. For `auto` and `supplementalPool` policies, if the number of threads passed in to the SCSI controller exceeds the number of virtqueues, we will take the `min(totalThreads, vcpus)` as to not allocate more threads than the controller needs.


## API Examples

<!--
Tangible API examples used for discussion
-->

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  labels:
    special: test-vmi
  name: test-vmi
spec:
  domain:
    ioThreadsPolicy: auto
    cpu:
      cores: 2
    devices:
      disks:
      - disk:
          bus: virtio
        name: testdisk1
      - disk:
          bus: virtio
        name: testdisk2
      - disk:
          bus: scsi
        name: scsi-disk
```

Disks will be assigned IOThreads like this:

```
testdisk1: 1
testdisk2: 2
scsi-disk: [1, 2]
```

Note: this is an example of the VMI having more auto threads (3) than virtqueues (2), so the SCSI controller here will only be allocated 2 total threads.

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

VMs that specify `ioThreadsPolicy: supplementalPool` with virtio-scsi and blk devices will result in this supplementalPool being a shared thread pool, there could be an argument for there being a distinct pool for just the scsi controller.


## Scalability

<!--
Overview of how the design scales)
-->

No new API so scalability should not be an issue.

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

The feature is additive and will be behind a feature gate. On upgrade, the existing `IOThreadsPolicy` logic will continue to work exclusively for virtio-blk devices until feature gate is enabled.

On rollback, disabling the feature gate reverts the ability to perform iothread-vq-mapping for virtio-scsi and will fallback to using a single threaded SCSI controller.


## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

* Unit tests to validate domain xml structure of new scsi controller
* Extend existing e2e hotplug test to verify hotplugging virtio-scsi disks when an `IOThreadsPolicy` is set

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha
- [ ] Add new feature gate 
- [ ] Updates to virtio-scsi controller
- [ ] 

### Beta
- [ ] Successful performance testing

### GA
