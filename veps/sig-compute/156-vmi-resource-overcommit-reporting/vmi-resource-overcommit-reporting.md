# VEP #156: Expose CPU and Memory Overcommitment Ratios on VMI Status

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements].
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Currently, KubeVirt does not expose CPU and memory overcommitment information for Virtual Machine Instances.  
Users and administrators cannot see the ratios a VMI was started with, which can lead to unexpected performance issues or instability.

As background context, the VMI object historically always populated both the memory request and the guest memory fields:

- **Memory request** – memory requested by **virt-launcher** for the VM, **excluding the virtualization overhead**.
- **Guest memory** – memory visible to the VM, i.e., what the **guest operating system sees and can use**.

After [kubevirt/kubevirt#15681](https://github.com/kubevirt/kubevirt/pull/15681) was merged, enabling overcommitment recalculation after migrations, this is no longer guaranteed, and a VMI may be populated with only one of these fields.  
Because of this, these fields **cannot be relied on to determine the current overcommitment** of a VMI.

Exposing the CPU and memory overcommitment ratios provides essential visibility for managing workloads and for external systems, such as autoscalers or quota controllers, that rely on this information.

## Motivation

Incorrect CPU or memory overcommitment ratios can negatively affect VMI performance and stability.  
By exposing these ratios:

- Users can understand actual resource usage and adjust workloads accordingly.
- External systems, such as autoscalers or quota mechanisms, can use the data to operate more effectively.
- Monitoring or alerting systems can track overcommitment ratios and notify users if VMIs are at risk of performance issues.

This improves observability, reduces the risk of performance problems, and enables better integration with external management tools.

## Goals

- Expose CPU and memory overcommitment ratios in the `status` of each VMI.

## Non Goals

- This enhancement does not modify VMI spec or allocation behavior.
- No changes to VM runtime behavior are included.

## Definition of Users

- KubeVirt users who run VMIs in clusters with overcommitment.
- Cluster administrators using AAQ quotas or billing based on overcommitment.
- Autoscaler implementations that rely on overcommitment data.
- KubeVirt developers needing observability into overcommitted resources.

## User Stories

### Observability
As a KubeVirt user, I want to see CPU and memory overcommitment ratios per VMI, so I can identify potential performance issues early.

### Autoscaling
As a cluster administrator, I want autoscalers to use overcommitment data, so that the system can proactively add 
resources or move VMIs before problems happen.

### Quota Enforcement and Billing
As a cluster administrator, I want overcommitment ratios to be reflected in AAQ quotas and billing, so that resource 
usage is accurately counted and billed.

## Repos

https://github.com/kubevirt/kubevirt

## Design

- Introduce new fields in `VMI.status` to report CPU and memory overcommitment ratios.
- Values will be populated by `virt-controller`.
- Ratios are calculated when the VMI starts and stored in the status.
- Values are updated again after a live migration completes, reflecting the VMI’s current resource allocation.

## API Examples

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vmi
spec:
  domain:
    cpu:
      cores: 2
    resources:
      requests:
        memory: 4Gi
      limits:
        memory: 6Gi
status:
...
  memory:
    memoryOvercommitRatio: 1.5
...
  currentCPUTopology:
    cpuAllocationRatio: 10.0
...
```
## Alternatives

An alternative would be to derive CPU and memory overcommitment from the `virt-launcher` pod resource requirements.

This is not ideal because `virt-launcher` resources include KubeVirt infrastructure overhead, such as dedicated CPUs 
and additional memory. 
This overhead can change between versions and depends on internal implementation details, making it difficult or 
impractical for users to calculate accurate overcommitment ratios without relying on the KubeVirt codebase.

# Scalability

This change adds a small amount of data to the VMI status and has a negligible impact on scalability. No additional pods, containers, or heavy operations are introduced.

# Update/Rollback Compatibility

During an upgrade, the new fields will be added to the VMI status when the feature gate is enabled. 

# Functional Testing Approach

- Enable the `VMIResourceMetrics` feature gate in functional tests.
- Verify that `VMI.status.resources.memoryOvercommitRatio` and `cpuAllocationRatio` are correctly calculated and reported at VMI startup.
- Confirm that the values are updated correctly after live migration.

# Implementation History

# Graduation Requirements

### Alpha

- Implement feature, enable feature gate by default in tests.
- Add functional tests to verify that `memoryOvercommitRatio` and `cpuAllocationRatio` are correctly reported at VMI startup and after live migration.

### Beta
### GA

