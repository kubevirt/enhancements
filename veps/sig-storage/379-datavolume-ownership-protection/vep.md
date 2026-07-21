# VEP #379: DataVolume Ownership Protection Against Name Collisions

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10.0
- This VEP targets beta for version: v1.11.0
- This VEP targets GA for version: v1.12.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes strict isolation of DataVolume (DV) ownership between standalone DataVolume and template-based DataVolume.
Currently KubeVirt has no prevention against conflict of these two usages on a single DataVolume,
which could result in data loss and/or VM corruption.  The VEP address this issue.

## Motivation

Consider that you create a standalone DV and then create a VM with template-based DV for the same name, the standalone DV is overwritten by the template-based one.
Moreover when you delete the VM, the standalone DV is removed by cascading deletion.
So if the standalone DV has some business related data, it's critical data loss.

Consider another case where two VMs both have template-based DV for the same name, the DV will be overwritten when the second VM initializes the DV.
So the first VM sees data lost, leading to some random results including VM destruction (for example if the shared DV is used as system drive).

The two types of usages of DVs are very different from the lifecycle's viewpoint and not designed to be used in any combined manner.
Although this issue seems to happen only due to operational errors or within sandbox environments, it can be seen as critical by enterprise users running mission-critical workloads, and potentially raising concerns about KubeVirt's quality.

## Goals

Free us from the risk of data loss and any random results caused by the issue, by preventing DataVolume Overwrite and Deletion caused by the issue.

## Non Goals

N/A

## Definition of Users

- **VM administrator**: Create/Delete VMs and DataVolumes
- **VM user**: Use and store their data in DataVolume

## User Stories

- As a VM administrator, I want to manage VMs and DataVolumes safely without accidentally overwriting/deleting existing DataVolumes, even when I reuse YAML files for VM creation and fail to change DataVolume name in the old YAML files.
- As a VM user, I want to use DataVolumes reliably, without losing data by misoperation of VM administrator.

## Repos

- https://github.com/kubevirt/kubevirt

## Design

### API change

No API change

### Behavior change

There are two points to consider about current implementation.

1. The usages of DataVolumes (standalone and template-based) can be distinguished by their `ownerReferences` which is set to `nil` for standalone DataVolume and to its managing VM for template-based DataVolume.
   For template-based DataVolumes, `ownerReferences` field is populated when the DataVolume is adopted via `ClaimMatchedDataVolumes()` in the VM reconciliation loop.
   This function lacks checking whether a given DataVolume is standalone or template-based, so the standalone DataVolume with conflicting name can be wrongly adopted by the claiming VM, which is the root cause of the issue.

2. Reconciliation loop of VM controller also lacks the check to detect the name collisions of DataVolume, so no one prevents the VM from starting and initializing.

In this VEP, we introduce a conditional logic to address these points: preventing adoption for standalone DataVolumes (for point 1), and making `handleDataVolumes()` fail with a new status message indicating that the VM is not able to startup due to DV name collision with a preexisting DV in the cluster (for point 2).
The additional logic for 2 can work reliabily by adding logic for 1 together.

This updated ownership semantics enables reliable, owner-based validation, effectively identifying conflicts with both standalone DataVolumes and template-based DataVolumes owned by other VMs.
Since cascading deletion during VM termination relies on `ownerReferences`, this fix also eliminates the critical issue where pre-existing DataVolumes can be accidentally destroyed alongside the VM.

It is important to note that this behavior fix is following the same pattern kubernetes uses for Pods that use inline (or ephemeral) PVCs. If there's a name collision the Pod will refuse to start.
See the KEP that introduced this https://github.com/kubernetes/enhancements/blob/master/keps/sig-storage/1698-generic-ephemeral-volumes/README.md#troubleshooting.

## API Examples

N/A

## Alternatives

N/A

## Scalability

Scalability impact is minimal because the change just slightly reduces unnecessary API Update/Patch requests.

## Update/Rollback Compatibility

If race with upgrade, the risk of data loss still remains, but that's not worse than staying in the old version.
If race with rollback, a DataVolume created in the new version can be affected by the issue when running in the old version, but no additional issue due to the rollback.
There is no significant issue with coexistance of new versioned nodes and old versioned nodes, because the whole workflow of creating/deleting VMs is independent across nodes.

## Functional Testing Approach

E2E tests covering the following scenarios to ensure the feature gate functions as expected:

- Verify that a pre-existing, standalone DataVolume is not adopted by a newly created VirtualMachine
  even if its name matches a template in the VM's dataVolumeTemplates.
  When the VM is subsequently deleted, ensure the standalone DataVolume is NOT removed by the Kubernetes Garbage Collector.
- Verify that a DataVolume already owned by an existing VirtualMachine does not have its ownership modified
  when another VirtualMachine attempts to use the same name in its dataVolumeTemplates.
  When the second VM is deleted, ensure the original DataVolume remains intact and attached to its rightful owner.

## Implementation History

06-22-2026: Initial implementation PR for review. PR: https://github.com/kubevirt/kubevirt/pull/18219

## Graduation Requirements

### Alpha
- [ ] DataVolume checking logic for ownership protection.
- [ ] Unit tests for the changed code
- [ ] Feature gate `DataVolumeOwnershipProtection` guards all code changes (disabled by default)

### Beta
- [ ] Set feature gate `DataVolumeOwnershipProtection` enabled by default.
- [ ] e2e tests for the new logic.

### GA
- [ ] Feature gate removed.
