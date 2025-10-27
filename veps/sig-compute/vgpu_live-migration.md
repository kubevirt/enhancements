# VEP #109: Implement vGPU Enabled Live Migration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [] (R) Target version is explicitly mentioned and approved
- [] (R) Graduation criteria filled

## Overview

This is a proposal to allow live migrations in KubeVirt to work for VMs with a single vGPU, exposed by mdev, between two nodes in the same cluster with identical GPUs.

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

**KubeVirt Administrators:** Users who have cluster wide privileges to trigger APIs to manage a cluster.
**KubeVirt Owner:** VM workload owners who want high availability for their VMs.

## User Stories

As a KubeVirt admin/owner, I want to be able to live migrate my VMs that have a vGPU.

## Repos

https://github.com/kubevirt/kubevirt

## Design

In order to live migrate a vGPU, we must first check that the host-driver versions for the GPU are the same on each node for compatability. Then, the destination XML needs to be updated with the mdev UUID that KubeVirt allocates on the target virt-launcher. To do this, we use a QEMU hook that interacts with the target `virt-launcher` pod. Live migrations have multiple phases, and the QEMU hook is called at each phase. The hook will have conditional statements to only execute at the "prepare begin" phase. Hooks receive the phase and XML from stdin, and the updated XML is sent to stdout.

Once the destination XML contains the correct fields, the live migration can begin. Libvirt/QEMU already support vGPU live migration for mdev (since Libvirt 8.6.0 and QEMU 8.1.0) and will do the actual migration, so no further work is needed by KubeVirt to migrate the vGPU. Some migration configs at the Libvirt/QEMU level, such as the migration method or downtime limit, may be necessary however.

A recent PR [16212](https://github.com/kubevirt/kubevirt/pull/16212) introduces a feature gate in KubeVirt, TargetSideMigrationHooks, to register and write QEMU hooks for the target virt-launcher. We will use this new infrastructure for the QEMU hook, and vGPU live migration will only be available with the TargetSideMigrationHooks feature gate enabled.

**Security:** Communication between the `virt-launcher` pod and QEMU hook should be limited to the unix socket, and the unix socket should be exclusively read and written to by the QEMU hook and `virt-launcher` pod.

**Failed migrations:** Cleanup will be performed by existing code and by code introduced in [16212](https://github.com/kubevirt/kubevirt/pull/16212).

## API Examples

No changes to API.

## Alternatives

Instead of relying on a QEMU hook, a Libvirt API could be introduced to allow KubeVirt to update the destination XML at the start of migration via callbacks. However, previous discussions asking for this API haven’t made progress.

## Scalability

Each unix socket will be unique to allow concurrent live migrations. Linux can support a very large number of open sockets at the same time, so it should be possible to live migrate a large number of VMs concurrently without significant performance issues. KubeVirt also imposes its own limitations on the number of live migrations on a node and cluster-wide level.

## Update/Rollback Compatibility

N/A

## Functional Testing Approach

* Unit tests: Verify that the VM is able to live migrate with the vGPU given the proper conditions.
* E2E: Verify that the target `virt-launcher` pod and QEMU hook interact as expected.
* [Optional] Also verify that this works with the NVIDIA GPU Operator.

## Implementation History

N/A

## Graduation Requirements

### Alpha

* Implement all functionality and testing.
* Need to monitor migration progress.
* Figure out how to handle any data loss during the migration.

### Beta

* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in Beta.

### GA

* Needs [VEP 141](https://github.com/kubevirt/enhancements/issues/141) to be in GA.
