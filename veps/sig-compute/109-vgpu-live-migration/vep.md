# VEP #109: Implement vGPU Enabled Live Migration

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This is a proposal to allow live migrations in KubeVirt to work for VMs with a single NVIDIA vGPU, exposed by mdev, between two nodes in the same cluster with identical GPUs, matching ECC memory configurations, and compatible GPU drivers.

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

### NVIDIA vGPU Migration Requirements

KubeVirt live migration relies on NVIDIA's underlying support, which has the following requirements as documented in [vGPU Migration Support (RHEL with KVM release notes)](https://docs.nvidia.com/vgpu/latest/grid-vgpu-release-notes-red-hat-el-kvm/index.html#vgpu-migration-support):
* **Virtual GPU Manager:** Must be identical across source and destination on RHEL KVM 9.4. From 9.6 onward, different versions are supported (unless NVIDIA states otherwise in release notes).
* **ECC Memory:** Must be configured identically (both enabled or disabled) on source and destination GPUs.
* **Guest Drivers:** Must be compatible with the host Virtual GPU Manager.
* **Limitations:** Migration will fail for GPUs with a GPU System Processor (GSP) or if the guest has CUDA unified memory, debuggers, or profilers enabled.

For Alpha, KubeVirt requires identical Virtual GPU Manager versions and ECC settings across all nodes to satisfy the strictest NVIDIA requirements. This simplifies scheduling and will be addressed during Beta.

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
    * Users must ensure all worker nodes have identical NVIDIA Virtual GPU Manager versions and matching GPU ECC configurations.
    * Users must comply with NVIDIA's other migration limitations (e.g., no GSP GPUs, no CUDA unified memory in the guest).
    * KubeVirt is unable to estimate the maximum period for the migration. Use a hard limit that is equal to the existing   calculated values (which ignore gpu info)
* Figure out how to handle any data loss during the migration.

### Beta

* KubeVirt will take hypervisor OS, Virtual GPU Manager version, and ECC configuration into account when scheduling migrations, relaxing the identical version requirement where NVIDIA allows (e.g., RHEL KVM 9.6+).
* Find a way to estimate the maximum period for the migration 
* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in Beta.

### GA

* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in GA.
