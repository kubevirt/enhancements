# VEP #277: Persistent Reservation

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.0.0
- This VEP targets beta for version:
- This VEP targets GA for version: v1.9.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This is a retroactive VEP for future references/extensions.  
The Persistent Reservation feature has existed since years and predates the VEP workflow.

## Motivation

SCSI protocol offers dedicated commands in order to reserve and control access to the LUNs.  
This can be used to prevent data corruption if the disk is shared by multiple VMs (or more in general processes).

This is about allowing the use of the above with kubevirt VMs.

## Goals

- Enable the use of SCSI persistent reservation with kubevirt VMs

## Definition of Users

- VM owners

## User Stories

- As a VM owner, I would like to use SCSI persistent reservations to allow restricting access to block devices to specific initiators

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

The SCSI persistent reservation is handled by the qemu-pr-helper. The pr-helper is a privileged daemon that can be either started by libvirt directly or managed externally.  
In case of KubeVirt, the qemu-pr-helper needs to be started **externally** because it requires high privileges in order to perform the persistent SCSI reservation. Afterward, the pr-helper socket is accessed by the unprivileged virt-launcher pod for enabling the SCSI persistent reservation.

The implementation leverages the device plugin framework;  
New device plugin for mounting the pr-socket inside the virt-launcher container if it requests the resource `devices.kubevirt.io/pr-helper` and is enabled by the reservations field in the VMI declaration.

```yaml
    devices:
      disks:
      - name: mypvcdisk
        lun:
          reservations: true
```

When the configurable for the feature is toggled, an additional container with the qemu-pr-helper is deployed/removed inside the virt-handler pod.

## API Examples

N/A

## Update/Rollback Compatibility

- This is upgrade compatible.

## Functional Testing Approach

- Unit tests
- E2E tests

## Implementation History

PersistentReservation has become stable over the releases (v1.0.0)
But was never intended to be rolled out on a default installation
(it involves significant overhead & is a niche use case, Windows Server Failover Clustering and the like)

This requires a rather unorthodox GA plan:
- GA
- Introduce configurable defaulting to "off"

## Graduation Requirements

### Alpha

### Beta

### GA

- [ ] Automatic node anti-affinity for VMs using S3PR on the same volume

## References

1. https://kubevirt.io/user-guide/storage/disks_and_volumes/#persistent-reservation
2. https://qemu-project.gitlab.io/qemu/tools/qemu-pr-helper.html
3. https://qemu-project.gitlab.io/qemu/system/pr-manager.html
4. https://github.com/kubevirt/kubevirt/pull/9177
