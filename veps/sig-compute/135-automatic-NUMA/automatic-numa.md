# VEP #135: Automatic VM NUMA passthrough Enablement Based on Node Topology

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal proposes an automated mechanism to enable NUMA passthrough within a VirtualMachineInstance (VMI) when the VMI's requested resources (vCPUs or memory) have exceeded the capacity of a single physical NUMA node on the host. This feature will improve the performance of large VMs by reducing cross-NUMA latency and making the underlying topology transparent to the guest VM, without requiring manual user configuration. The behavior will be controllable via a KubeVirt configuration flag.

## Motivation

Currently, users running large VMs on multi-NUMA host machines must manually configure the NUMA passthrough in the VMI spec. Failing to do so, it may result in poor performance due to the operating system inside the VM not being aware of the underlying physical NUMA boundaries, which leads to frequent cross-NUMA access.

This manual process faces the following drawbacks:

1. **Error-prone**: Users might forget the necessary NUMA passthrough configuration.  
2. **Sub-optimal**: KubeVirt should strive to automatically provide the best performance configuration for the resources requested.

Automating this configuration will provide performance benefits and a better out-of-the-box experience for users running large workloads.

## Goals

- Introduce an KubeVirt configuration field to globally enable or disable this automatic NUMA enablement feature.  
- Implement a mechanism in node-labeller to discover the host's physical NUMA topology (specifically, the maximum memory and CPU count of a single NUMA node).  
- Report the discovered NUMA topology information on the Kubernetes Node object (via annotations or labels).  
- Implement admission logic to automatically enable NUMA passthrough functionality if its resource request exceeds the reported single-NUMA node capacity, provided the feature is enabled.  

## Non-Goals

1. Implementing full NUMA-aware scheduling (e.g., ensuring a VMI's NUMA nodes are pinned to distinct physical NUMA nodes). This is a more complex scheduling/placement problem. This VEP focuses only on enabling the NUMA passthrough in the VM.  
2. Automatic NUMA node to pNUMA node affinity/pinning. This requires more complex configuration and coordination (e.g., with the CPU Manager) and is outside the scope of this proposal.

## Definition of Users

- Cluster Admins: Responsible for enabling configuration for automatic NUMA passthrough enablement in Kubernetes Clusters.
- Developers: Deploys VMs which will have NUMA passthrough enabled.

## User Stories
- As a Cluster Admin, I want a configuration option to globally enable or disable automatic NUMA passthrough enablement for VMs, so I can control the performance features available to users.
- As a Developer deploying a large VM (e.g. 64 vCPUs, 256Gi memory), I want the NUMA passthrough feature to be automatically enabled for my VM, so I don't have to manually tune the spec for best performance.
- As a Cluster Admin, I want `node-labeller` to reliably report the max number of cpu and size of memory in single NUMA, so that the admission controller can make correct decisions about NUMA enablement.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

A new configuration field will be introduced in the `KubeVirtConfiguration` to act as a switch for this feature.

```
spec:
  configuration:
    developerConfiguration:
      # Defaults to false
      autoNUMAEnablement: true
```

1. **Topology Discovery**: a node-labeller will be responsible for gathering information about the node's physical NUMA topology from libvirt.
2. **Reporting**: node-labeller will aggregate this information and apply it as a structured annotation on the corresponding Kubernetes Node object.  
* **Annotation Example**:  
  On the Node object

```
metadata:
  annotations:
    “kubevirt.io/max-single-numa-cpus”: “20”
    “kubevirt.io/max-single-numa-memory”: “128Gi" 
```

A KubeVirt component (e.g., the VMI Mutating Webhook) will intercept VMI creation requests.

1. **Check Configuration**: The webhook first checks if `KubeVirtConfiguration.developerConfiguration.autoNUMAEnablement` is set to `true`. If not, it skips the rest of the logic.  
2. **Determine Resource Requirements**: The webhook reads the VMI's requested memory and CPU count, i.e., `VMI.spec.domain.resources.requests`.  
3. **Fetch Node Information**: The webhook uses the Node information reported by the Topology Discovery phase. Since the VMI may not be scheduled yet, the webhook must assume a worst-case scenario or rely on the reported node capacity from the cluster's smallest NUMA nodes.  
Then if `VMI_Requested_Memory > smallest-max-single-numa-memory` **OR** `VMI_Requested_CPU > smallest-max-single-numa-cpus`, then NUMA enablement is triggered.

## API Examples

## Alternatives

We might enable NUMA passthrough for all VMs (not only VMs which are requesting more resources than a NUMA node can provide) when the autoNUMAEnablement configuration is enabled. This might add some VM overhead for enabling the feature.

## Scalability
N/A

## Update/Rollback Compatibility

- The autoNUMAEnablement will be disabled by default in the Alpha version.

## Functional Testing Approach

- Unit testing to detect NUMA topology from the libvirt capabilities.
- Unit testing to check if a VM requesting more resources than is a minimal quantity in a cluster has NUMA passthrough enabled.

## Implementation History

## Graduation Requirements

### Alpha

The `KubeVirtConfiguration.developerConfiguration.autoNUMAEnablement` field is introduced.  
Node-labeller successfully discovers and reports a minimal NUMA topology (Max CPU/Memory per node) on the Node object.  
The Mutating Webhook successfully mutates the VMI spec when the feature is enabled and the resource requirements are met.  
End-to-end integration tests are implemented.

### Beta

* Address feedback from the community and internal teams.  
* The feature is stable and has been proven in large-scale environments.  

### GA

Enable this feature by default.