# VEP #134: Remove dependency between NUMA passthrough and Huge pages

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes to remove a specific validation check within KubeVirt's admission logic that currently rejects a Virtual Machine Instance (VMI) when a NUMA passthrough is enabled, but the VMI has not explicitly requested HugePages. This dependency is technically unsound (introduced in https://github.com/kubevirt/kubevirt/pull/5846), as NUMA is a topology optimization for memory locality, independent of the memory type (standard pages vs. HugePages). Original purpose for NUMA passthrough was to enable run of VMs with SAP Hana with Huge pages enabled. Removing this rejection allows compute intensive VMs to utilize standard memory pages while still benefiting from proper NUMA awareness.

## Motivation

The current validation logic treats HugePages as a prerequisite for NUMA passthrough. This is incorrect because:

1. **Independent Optimizations:** A user may need vNUMA for a large VMI but choose standard pages for reasons like host resource constraints, simpler memory management, or non-memory-intensive workloads.  
2. **Unnecessary Constraint:** The dependency forces users to adopt the complexity of HugePages allocation only to enable NUMA passthrough, even when not required by the workload itself.

Removing this rejection barrier will enhance the flexibility of KubeVirt for enterprise workloads that require NUMA-aware scheduling but not necessarily HugePages.

## Goals

- **Remove the specific validation logic** in the VMI admission webhook that performs a check for the presence of the hugepages field when vNUMA is detected as enabled.  
- Allow a VMI to be successfully created if it is configured with NUMA passthrough feature but relies on standard memory pages.


## Non-Goals

1. Change how HugePages are allocated or enforced when they are explicitly requested by the VMI.  
2. Change the internal logic that generates the NUMA passthrough.

## Definition of Users

- Developers: Deploys compute intensive VMs that require NUMA.

## User Stories

- As a Developer I would like to create a VM with NUMA passhthrough feature without requesting the cluster administrator to enable huge pages on the nodes.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

The core change will be applied to the VMI admission webhook and virt-launcher converter ([virt-api](https://github.com/kubevirt/kubevirt/blob/main/pkg/virt-api/webhooks/validating-webhook/admitters/vmi-create-admitter.go#L722) and [virt-launcher](https://github.com/kubevirt/kubevirt/blob/main/pkg/virt-launcher/virtwrap/converter/vcpu/vcpu.go#L613))
The current logic contains a condition similar to the following, which results in rejection:
IF NUMA passthrough is enabled AND VMI.spec.domain.memory.hugepages is NOT set:

* **Action:** Reject VMI creation with a validation error (e.g. "spec.domain.memory.hugepages must be requested when NUMA topology strategy is set in spec.domain.cpu.numa.guestMappingPassthrough" for field "spec.domain.cpu.numa.guestMappingPassthrough".").

### **Proposed Validation Logic Change**

The VEP proposes to entirely remove or modify the condition that enforces this dependency.
IF NUMA passthrough is enabled AND VMI.spec.domain.memory.hugepages is NOT set:
* **Action:** **Do nothing** and proceed with other validations. The VMI should be allowed to be created.
This modification ensures that the presence of NUMA passthrough awareness is independently validated from the memory backing type.

## API Examples

<!--
Tangible API examples used for discussion
-->

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Scalability

N/A

## Update/Rollback Compatibility

It should not impact update/rollback compatibility.

## Functional Testing Approach

- Unit Test: Modify the unit tests for the VMI Admission Webhook to specifically check a VMI definition where NUMA passthrough is enabled, but no hugepages configuration is present. The test must now assert that the validation passes instead of failing.  

## Implementation History

### GA
- [ ] Remove condition that enforces this dependency of NUMA passthrough to Huge pages
