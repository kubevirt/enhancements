# VEP #156: Expose  Memory Overhead on VMI Status

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements].
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Currently, KubeVirt does not expose memory overhead information for Virtual Machine Instances.  
Users and administrators cannot see per-VMI details, such as the virtualization overhead or the added/reduced memory from what was requested in the guest.

As background context, the VMI object historically always populated both the memory request and the guest memory fields:

- **Memory request** – memory requested by **virt-launcher** for the VM, **excluding the virtualization overhead**.
- **Guest memory** – memory visible to the VM, i.e., what the **guest operating system sees and can use**.

After [kubevirt/kubevirt#15681](https://github.com/kubevirt/kubevirt/pull/15681) was merged, enabling overcommitment recalculation after migrations, this is no longer guaranteed, and a VMI may be populated with only one of these fields.  
Because of this, these fields **cannot be relied on to determine the current overcommitment** of a VMI.

Exposing the memory overhead allows users to see how the actual resources differ from the requested values. These values can also be used for debugging, troubleshooting, and other purposes.

## Motivation

Incorrect memory adjustments can negatively affect VMI performance and stability.
By exposing overhead information:

- Users can understand actual resource allocation and adjust workloads when needed.
- External systems, such as autoscalers, monitoring tools, or alerting systems, can track resource overhead and alert or act when VMIs are at risk due to overcommitment or undercommitment.
- Quotas can use this data to account for virtual resources, considering any memory added or subtracted from the requested values.

## Goals

- Expose memory overhead in the `status` of each VMI.

## Non Goals

- This enhancement does not modify VMI spec or allocation behavior.
- No changes to VM runtime behavior are included.

## Definition of Users

- KubeVirt users who run VMIs and want to see how much memory is added or subtracted from the requested values.
- Cluster administrators using AAQ quotas or billing, considering the added or removed resources.
- Autoscaler implementations that rely on data about added or reduced resources.
- KubeVirt developers who need observability into resource adjustments and overhead.

## User Stories

### Quota and Billing Use Case
As a cluster administrator, I want AAQ quotas to count only the VMI workload, ignoring added overhead, so users are billed only for their requests, like in other virtualization platforms.

**Example:**  
A VMI requests 1 GiB memory. With a 200% overcommitment ratio, the launcher pod requests:
- 500 MiB (workload)
- 300 MiB (overhead)
- Total: 800 MiB

For billing, only the workload (500 MiB) should be counted. Looking at the pod request alone (800 MiB) is not enough, because it includes overhead and the ratio, which can change over time.

Exposing the overhead in the VMI status makes it easy to calculate the actual workload by subtracting the overhead.

### Configuration Change Tracking
As a cluster administrator, I want to know the per-VMI allocation at creation time, even if global ratios change later.  

**Example:**  
- VMI started with memory overcommitment 200%.
- Later, global memory overcommitment changed to 150%.  
- Existing VMI still shows the same allocated memory.  
- This allows administrators to calculate the initial overcommitment ratio per VMI and identify which VMIs use old ratios, so they can decide which VMIs to stop, migrate, or adjust.  

### Developer Configuration Verification
As a KubeVirt developer, I want to see the per-VMI overhead, so I can verify how configuration changes affect actual resource requests.  

**Example:**  
- I want to verify that the added memory overhead for my VMI configuration is correctly applied.  
The current status shows:  
  ```yaml
  memoryOverhead: 512Mi

## Repos

https://github.com/kubevirt/kubevirt

## Design

- Introduce new field in `VMI.status` to report memory overhead.
- Values will be populated by `virt-controller`.
- Overhead values are calculated when the VMI starts and stored in the status.
- Values are updated again after a live migration completes, reflecting the VMI's current resource allocation.

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
    memoryOverhead: 300Mi
...
```

## Alternatives

### Using Overcommitment Ratios

An alternative is to expose `memoryOvercommitRatio instead of overhead values.

This approach was rejected because:
- Ratios are values that result in memory requests, but they are hard to understand and it's not clear how they generate the requests or the links between them.
- Exposing overhead values directly supports a broader range of use cases (see [VEP #144: Adjust virt-launcher memory lock requirements](https://github.com/kubevirt/enhancements/issues/144)).

### Using a Metric

The overhead could be exposed via the `kubevirt_vmi_launcher_memory_overhead_bytes` metric.

**Cons:**
- Less intuitive for quotas and controllers, which need the overhead in the VMI status to know the current value.
- Metrics may lag, so controllers might see outdated values.

### Imperative Function / Sub-resource

The overhead could be exposed through an imperative function or a VMI sub-resource.

**Cons:**
- More complex to implement and maintain.
- Requires additional API access and RBAC.
- Less intuitive for quotas and controllers, which need the overhead in the VMI status to know the current value.

# Scalability

This change adds a small amount of data to the VMI status and has a negligible impact on scalability. No additional pods, containers, or heavy operations are introduced.

# Update/Rollback Compatibility

During an upgrade, the new fields will be added to the VMI status when the feature gate is enabled. 

# Functional Testing Approach

- Enable the `VMIResourceMetrics` feature gate in functional tests.
- Verify that `VMI.status.memory.memoryOverhead` and `VMI.status.currentCPUTopology.cpuOverhead` are correctly calculated and reported at VMI startup.
- Confirm that the values are updated correctly after live migration.

# Implementation History

# Graduation Requirements

### Alpha

- Implement feature, enable feature gate by default in tests.
- Add functional tests to verify that `memoryOverhead` and `cpuOverhead` are correctly reported at VMI startup and after live migration.

### Beta
- revisit the API

### GA

