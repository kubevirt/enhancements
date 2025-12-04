# VEP #109: Implement vGPU Enabled Live Migration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [] (R) Target version is explicitly mentioned and approved
- [] (R) Graduation criteria filled

## Overview

This is a proposal to allow live migrations in Kubevirt to work for VMs with a single NVIDIA vGPU. The live migration will be supported between two nodes with identical, Kubevirt compatible GPUs that use mdev.

## Motivation

GPU usage is increasing with more and more companies running AI workloads, so companies are now requesting live migration to support GPU enabled VMs.

## Goals

* Address a common live migration problem where the target needs to update the destination Libvirt XML. In this case, it needs to update the mdev UUID in the XML.
* Support single vGPU enabled live migrations for both clusters that are using the Nvidia GPU Operator and clusters that are using Kubevirt’s generic device plugin for mdev.
* Support single vGPU enabled live migrations with minimal data lost due to high dirty rates.

## Non Goals

Do not want to change the live migration workflow for non vGPU enabled VMs.

## Definition of Users

**Kubernetes Administrators:** Users who have cluster wide privileges to trigger APIs to manage a cluster.

## User Stories

As a cluster admin, I want to be able to live migrate my VMs that have a vGPU.

## Repos

https://github.com/kubevirt/kubevirt

## Design

In order to live migrate a vGPU, the destination XML needs to be updated with the mdev UUID that Kubevirt allocates on the target virt-launcher. To do this, we introduce a QEMU hook that interacts with the target `virt-launcher` pod. Live migrations have multiple phases, and the QEMU hook is called at each phase. The hook will have conditional statements to only execute at
the "prepare begin" phase. Hooks receive the phase and XML from stdin, and the updated XML is sent to stdout.

Once the destination XML contains the correct fields, the live migration can begin. Libvirt/QEMU already support vGPU live migration for mdev and will do the actual migration, so no further work is needed by KubeVirt to migrate the vGPU. Some migration configs at the Libvirt/QEMU level, such as the migration method or downtime limit, may be necessary however.

This will be the flow:

1.  **`virt-launcher` (Target):** Target `virt-launcher` creates a vGPU `api.HostDevice`, containing some fields including the mdev UUID.
2.  **`virt-launcher` (Target):** Target `virt-launcher` listens on the `"/tmp/kube-migration-hook-socket-{unique_id}"` unix socket. At this point, the socket hasn’t been opened yet, so use a Go func so that the rest of the migration code can execute.
3.  **QEMU hook:** During the "migrate begin" phase on the target, the QEMU hook will open the `"/tmp/kube-migration-hook-socket-{unique_id}"` unix socket and pass the destination XML.
4.  **`virt-launcher` (Target):** Target `virt-launcher` accepts the connection and receives the destination XML. It then updates the XML with the target’s mdev UUID and writes the XML to the socket.
5.  **QEMU hook:** The hook receives the updated XML and outputs to stdout to complete the hook.

**Security:** Communication between the `virt-launcher` pod and QEMU hook should be limited to the unix socket, and the unix socket should be exclusively read and written to by the QEMU hook and `virt-launcher` pod.

**Failed migrations:** If the migration fails, the unix socket should close, and cleanup will be performed by existing code.

## API Examples

No changes to API.

## Alternatives

Instead of relying on a QEMU hook, a Libvirt API could be introduced to allow Kubevirt to update the destination XML at the start of migration via callbacks. However, previous discussions asking for this API haven’t made progress.

## Scalability

Each unix socket will be unique to allow concurrent live migrations. Linux can support a very large number of open sockets at the same time, so it should be possible to live migrate a large number of VMs concurrently without significant performance issues.

## Update/Rollback Compatibility

N/A

## Functional Testing Approach

* Unit tests: Verify that the VM is able to live migrate with the vGPU given the proper conditions.
* E2E: Verify that the target `virt-launcher` pod and QEMU hook interact as expected.
* Also verify that this works with the NVIDIA GPU Operator.

## Implementation History

N/A

## Graduation Requirements

### Alpha

Implement all functionality and testing.

### Beta

### GA
