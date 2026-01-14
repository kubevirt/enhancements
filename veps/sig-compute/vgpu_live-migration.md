# VEP #109: Implement vGPU Enabled Live Migration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [] (R) Target version is explicitly mentioned and approved
- [X] (R) Graduation criteria filled

## Overview

This is a proposal to allow live migrations in KubeVirt to work for VMs with a single NVIDIA vGPU, exposed by mdev, between two nodes in the same cluster with identical GPUs and GPU drivers.

## Motivation

GPU usage is increasing with more and more companies running AI workloads, so companies are now requesting live migration to support GPU enabled VMs.

## Goals

* Address a common live migration problem where the target needs to update the destination Libvirt XML. In the case of mdevs, it needs to update the mdev UUID in the XML.
* Support single vGPU enabled live migrations for both nodes that are using the Nvidia GPU Operator and clusters that are using KubeVirt’s generic device plugin for mdev.
* Support single vGPU enabled live migrations with minimal data lost due to high dirty rates.

## Non Goals

* Do not want to change the live migration workflow for non vGPU enabled VMs.
* Do not support live migration for passthrough or SRIOV vGPU
* Do not support cross-cluster live migrations
* Do not support live migrations for VMs with multiple vGPUs

## Definition of Users

* **KubeVirt Administrators:** Users who have cluster wide privileges to trigger APIs to manage a cluster.
* **KubeVirt Owner:** VM workload owners who want high availability for their VMs.

## User Stories

As a KubeVirt admin/owner, I want to be able to live migrate my VMs that have an NVIDIA vGPU.

## Repos

https://github.com/kubevirt/kubevirt

## Design

For Alpha, GPU driver versions on all worker nodes must be identical. The migration will not be successful if there is a version mismatch, so users must ensure this. This will be addressed and updated during Beta.

[VEP 141](https://github.com/kubevirt/enhancements/issues/141) introduces a feature gate in KubeVirt, TargetSideMigrationHooks, to register and write QEMU hooks for the target `virt-launcher`. We will use this new infrastructure to mutate the domain XML with the updated mdev UUID, which will be the one assigned to the target `virt-launcher` by `gpu.CreateHostDevices()` in `manager.go`. VGPU live migration will only be available with the TargetSideMigrationHooks feature gate enabled. 

Once the destination XML contains the correct fields, the live migration can begin. Libvirt/QEMU already support vGPU live migration for mdev (since Libvirt 8.6.0 and QEMU 8.1.0) and will do the actual migration, so no further work is needed by KubeVirt to migrate the vGPU. Some migration configs at the Libvirt/QEMU level, such as the migration method or downtime limit, may be necessary however.

### Example 
XML snippet before hook:
```
<hostdev mode='subsystem' type='mdev' managed='no' model='vfio-pci' display='on' ramfb='on'>
      <source>
        <address uuid='bb4a98d8-60c1-40c6-b39b-866b1e82bd8c'/>
      </source>
      <alias name='ua-gpu-gpu1'/>
      <address type='pci' domain='0x0000' bus='0x09' slot='0x00' function='0x0'/>
    </hostdev>
```

XML snippet after hook (address uuid updated):
```
<hostdev mode='subsystem' type='mdev' managed='no' model='vfio-pci' display='on' ramfb='on'>
      <source>
        <address uuid='05b59010-d19c-47d2-9477-33b4579edc90'/>
      </source>
      <alias name='ua-gpu-gpu1'/>
      <address type='pci' domain='0x0000' bus='0x09' slot='0x00' function='0x0'/>
    </hostdev>
```

**Failed migrations:** Cleanup will be performed by existing code and by code introduced in [16212](https://github.com/kubevirt/kubevirt/pull/16212).

## API Examples

N/A

## Alternatives

Instead of relying on a QEMU hook, a Libvirt API could be introduced to allow KubeVirt to update the destination XML at the start of migration via callbacks. However, previous discussions asking for this API haven’t made progress.

## Scalability

The unix socket used will be `/var/run/kubevirt/migration-hook-socket` introduced in PR [16212](https://github.com/kubevirt/kubevirt/pull/16212). A target `virt-launcher` pod will have at most one of this socket open at a time, so it should be possible to live migrate a large number of VMs concurrently without significant performance issues. KubeVirt also imposes its own limitations on the number of live migrations on a node and cluster-wide level.

## Update/Rollback Compatibility

* Needs TargetSideMigrationHooks feature gate from PR [16212](https://github.com/kubevirt/kubevirt/pull/16212) to be enabled
* Will be safe during upgrades as long as the newer node's mdev uuids don't change unexpectedly.

## Functional Testing Approach

* Unit tests: Verify that the VM is able to live migrate with the vGPU given the proper conditions.
* [Optional] Also verify that this works with the NVIDIA GPU Operator.

## Implementation History

N/A

## Graduation Requirements

### Alpha

* Implement basic functionality and testing.
* Limitations
    * Users must ensure all worker nodes have identical GPU driver versions since KubeVirt will not take this into account when scheduling the migration
    * KubeVirt is unable to estimate the maximum period for the migration. Use a hard limit that is equal to the existing   calculated values (which ignore gpu info)
* Figure out how to handle any data loss during the migration.

### Beta

* No longer require users to ensure all worker nodes have identical GPU driver versions. KubeVirt will take driver version into account when scheduling the migration
* Find a way to estimate the maximum period for the migration 
* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in Beta.

### GA

* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in GA.
