# VEP#144: Network Binding Plugin Memory Lock Requirements

This VEP extends the original network binding plugin design
[document][kubevirt-network-binding-plugin], available at the
kubevirt/community repository.

[kubevirt-network-binding-plugin]: https://github.com/kubevirt/community/blob/9c41536e982072a9b5843571c9d62a8d9bcea448/design-proposals/network-binding-plugin/network-binding-plugin.md

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This document describes a mechanism for network binding plugins to
declare their memory lock resource limit requirements. Network binding
plugins can now specify whether they need to lock memory or not when
registered, allowing the virt-handler to configure memory lock limits
appropriately for virt-launcher pods and qemu processes according to the
needs of network bindings attached.

## Motivation

Some network interfaces, such as vDPA, need to lock guest memory to
function properly. Similar to SR-IOV, libvirt tries to update
environment memory lock limits if they are below expectations (see
[source code][libvirt-vdpa-memlock-ratio]). Those expectations depend on
the guest's memory size and the number of interfaces that require
locking memory.

Currently, KubeVirt adjusts memory lock limits for SR-IOV, but lacks
a mechanism to adjust it for network binding plugins that need it.
There are some existing mechanisms that are related to this enhancement,
each with their own limitations:
1. `AdditionalGuestMemoryOverheadRatio`: It is a cluster-wide
   configuration, meaning that it would affect all VMIs. It increases
   virt-launcher pods' memory overhead, based on the guest memory and
   the configured ratio. This does not translate into modified memory
   lock limits, as they are modified under very specific
   [conditions][kubevirt-memlock-limit-conditions] and it affects the
   amount of VMIs that can be scheduled in nodes.
2. `ComputeResourceOverhead`: This only impacts VMIs that are plugged
   interfaces of a network binding plugin kind. However, the
   configuration is static, meaning that it does not scale based on
   guest memory sizes. It increases pods' memory overhead, which impacts
   on schedulable VMIs. And finally, it does not trigger memory lock
   rlimit configuration.

With the existing approaches, network binding plugins requiring specific
memory lock requirements (like vDPA) fail to function, or require
workarounds that do not scale or impact cluster-wide VMI scheduling.

[libvirt-vdpa-memlock-ratio]: https://gitlab.com/libvirt/libvirt/-/blob/v11.9.0/src/qemu/qemu_domain.c#L8362-8384
[kubevirt-memlock-limit-conditions]: https://github.com/kubevirt/kubevirt/blob/50b9b4d0cf1caaf56147411a1e6765134e0e82b9/pkg/virt-handler/isolation/detector.go#L95

## Goals

- Support configuring memory lock requirements of network binding plugins.
- Adjust virt-launcher pod and qemu process memory lock rlimits based on
  guest memory size, number of network interfaces of each kind and
  network bindings' needs.
- Adjust memory lock limits without impacting VMI scheduling capacity
  (i.e. increasing pod memory resource limits).

## Non Goals

- Modifying the overall memory overhead calculation.
- Changing how memory lock limits are computed for built-in bindings
  (e.g. SR-IOV).
- Providing a general mechanism to adjust virt-launcher pod memory
  limits.
- Providing a general mechanism to adjust memory lock rlimits other than
  for network binding plugins.

## Definition of Users

- Cluster administrators deploying network binding plugins that require
  locking memory (such as vDPA).

## User Stories

- As a network binding plugin user, I want KubeVirt to adjust memory
  lock rlimits of virt-launcher pods and qemu processes for those
  bindings that need it to function properly.

- As a cluster administrator, I want to specify which network binding
  plugins have special memory lock requirements.

- As a cluster administrator, I want memory lock rlimits to be adjusted
  based on network binding plugins' needs without affecting cluster VMI
  scheduling capacity.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

This solution introduces a new optional field called `AdjustMemoryLockLimits`
to `InterfaceBindingPlugin`. This is a boolean flag indicating whether
the network binding plugin requires to lock guest memory or not.

virt-handler takes this into account to adjust virt-launcher pods'
memory lock rlimits, only if any of the network binding plugins attached to
the VMI being scheduled requires it. If they do, it predicts the memory
lock rlimit based on the following formula:

```math
ML_{rlimit} = M_{guest} * (\sum_{i=0}^{M-1}A_i) + O_{fixed}
```

Where:
- $ML_{rlimit}$ is the resulting memory lock rlimit.
- $M_{guest}$ is the VMI base memory (see
  [ref][kubevirt-AdjustResources-vmiBaseMemory]).
- $i$ represents network binding plugin interfaces attached to a VMI.
- $M$ is the total amount of network binding plugin interfaces attached
  to a VMI.
- $A_i$ is 1 if `AdjustMemoryLockLimits` is true for the network
  binding plugin of interface $i$ and 0 otherwise.
- $O_{fixed}$ is a fixed offset set to $1024*1024$ kB, to mimic
  [libvirt's expectations][libvirt-vdpa-memlock-ratio].

And finally, it applies it to the virt-launcher pod rlimits and qemu
process as it is already done for SR-IOV interfaces.

[kubevirt-AdjustResources-vmiBaseMemory]: https://github.com/kubevirt/kubevirt/blob/v1.7.0/pkg/virt-handler/isolation/detector.go#L124-L130

## API Examples

Register a network binding plugin that requires to lock guest memory:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    network:
      binding:
        vdpa:
          sidecarImage: quay.io/kubevirt/network-vdpa-binding
          downwardAPI: device-info
          adjustMemoryLockLimits: true
```

The way network binding plugins are attached to VMs is not impacted.

## Alternatives

### Sidecar Hook for Memory Lock configuration

**Description**: Extend the sidecar hooks with another hook that would
return the expected memory lock limits for a certain use-case. The
virt-launcher would call the hooks of all configured sidecars and come
up with the resulting memory lock limit requirements during start-up.
The virt-launcher would expose this information for the virt-handler.
The virt-handler would finally apply those requirements.

**Pros**:
- It leads into a more general solution as it permits each use-case
  to define their own specific memory lock limit computation hook.
- It is a solution that benefits sidecars, which also include network
  binding plugins as the outcome of a broader solution

**Cons**:
- More complex implementation requiring extra launcher-handler
  integration.
- This implementation would rely on each possible user to implement a
  sidecar for memory lock requirement configuration.


### Extending the VMI spec

**Description**: Extend the VMI spec hooks with a dedicated field for
memory lock requirement configuration. Admission webhooks registered by
network binding plugins or for other reasons could modify the field
accordingly, exposing the field to the user, but not requiring the user
to set it explicitly.

**Pros**:
- It serves network binding plugins as well as any other possible device
  or VM configuration that could need to modify virt-launcher memory
  lock rlimits.

**Cons**:
- Exposes the configuration field to the user. (Note: could a similar
  implementation based on annotations be a better fit?).


## Scalability

There is already some logic that covers SR-IOV, RT, SEV machines' memory
lock limit requirements. The existing logic is ad-hoc for each use case.
Similar to network devices, other devices and situations could have
their own memory lock requirements. Implementing this as part of the
network binding plugin interface would leave other devices' needs aside,
meaning they would need to implement similar (duplicate/redundant) APIs
for each.


## Update/Rollback Compatibility

- It adds a new optional field, meaning it is backwards compatible.
- It does not modify VM/VMI specs.
- On rollback, VMIs using network binding plugins with
  `AdjustMemoryLockLimits: true` will fail to start if they are migrated
  or virt-launcher is re-launched.

## Functional Testing Approach

The following e2e scenarios should be considered:
- VM creation: Memory lock rlimits are configured properly for
  virt-launcher pods running VMIs with `N` network binding interfaces
  that require locking memory.
- VM migration: Memory lock rlimits are configured properly in the
  destination virt-launcher and qemu process when a VMI with network
  binding plugin interfaces with special memory lock requirements are
  migrated.

## Implementation History

- Draft implementation: https://github.com/kubevirt/kubevirt/pull/16197
    * NOTE: it includes a more complex API rather than the one described
      here (simplifying it is WIP).

## Graduation Requirements

### GA
Proposed v1.8, TBD
