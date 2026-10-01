# VEP #144: Memory Lock RLimit configuration

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This enhancement proposes a new VMI field to set unlimited memlock
limits to the virtqemud processes for those instances that request it.
This allows running VMIs that require non-default memory lock limits
without extending the existing list of conditions under which the limit
should be modified, no matter the logic supporting a device or workload
lives inside the core KubeVirt source or it is provided via a plugin.

Currently, the unlimited memlock limit is applied to the virtqemud
process as long as the virtual machine is SEV, RT or is attached a VFIO
device. In other words, this VEP does not want to change the current
limit, but let arbitrary virtual machines to opt-in.

## Motivation

Depending on the devices attached to a VM (SR-IOV, vDPA, ...) or the
type of VM (real time, SEV, ...) VM memory will be locked by QEMU or the
devices themselves to ensure proper functioning. The amount of memory
that a process can lock is limited by the environment's memory lock
RLimit. Libvirt updates environment memory lock limits if they are below
expectations (see [source code][libvirt-vdpa-memlock-ratio]). Those
expectations depend on the guest's memory size, the number of devices
that require locking memory, the size of other memory regions that
could be also locked (such as the IOMMU region) and whether iommufd is
used or not.

Currently, KubeVirt (`virt-handler`) sets the memory lock limits of the
`virt-launcher` to `'unlimited'` under a specific set of
[conditions][kubevirt-memlock-limit-conditions]: When SRIOV/VFIO devices
are attached to, or it is a real time or SEV VM. However, these are not
the only cases in which a virtual machine might need to lock memory.
Moreover, implementations outside of the core KubeVirt source code, such
as hook sidecars or network binding plugins, do not have a way to make
sure VMs won't fail to boot due to memory lock limits not being set
appropriately.

A mechanism to allow running virtual machines with non-trivial memory
lock RLimit requirements is needed.

Initially, the approach consisted of an API field, `ReservedOverhead`,
that was released under a feature gate in Alpha stage in release v1.8.
This enhancement tracked the development of such feature. This allowed
other components, such as admission webhooks, specify those
requirements without affecting internal KubeVirt source code. Its tight
coupling to pod-sizing was the reason why the original design was pushed
back in favor of this, proposed in this renewed VEP.

Currently, some existing mechanisms that are related to this enhancement
exist, but each with their own limitations:
1. `AdditionalGuestMemoryOverheadRatio`: It is a cluster-wide
   configuration, meaning that it would affect all VMIs. It increases
   `virt-launcher` pods' memory overhead, based on the guest memory and
   the configured ratio. This does not translate into modified memory
   lock limits, as they are modified under very specific
   [conditions][kubevirt-memlock-limit-conditions] and it affects the
   amount of VMIs that can be scheduled in nodes.
2. `ComputeResourceOverhead`: This only impacts VMIs that are plugged
   interfaces of a network binding plugin kind. However, it is a static
   value, meaning that it wouldn't grow based on guest memory size. It
   increases pods' memory overhead, which impacts on schedulable VMIs.
   And finally, it does not trigger memory lock RLimit configuration.
3. `ReservedOverhead`: From v1.8 on, the VMI spec API would let users
   define memlock limit requirements under the `ReservedOverheadMemlock`
   feature gate. This, however, did not decouple the memory lock
   configuration from pod memory sizing, put the responsibility of
   computing the limits on the user, who did need to learn about
   internals, and showed not to be scalable so as to support hotplug
   operations of different kind. It was finally decided to remove this
   field from the API in favour of another mechanism.

In summary, KubeVirt does not support a mechanism to let users run
workloads that need locked memory without those...
- ...being a really specific set of cases (SR-IOV, SEV, RT...).
- ...being decoupled from pod memory sizing and so VM schedulability.
- ...involving the user in knowing about libvirt's memory lock limit
  configuration internals.

[libvirt-vdpa-memlock-ratio]: https://gitlab.com/libvirt/libvirt/-/blob/v11.9.0/src/qemu/qemu_domain.c#L8362-8384
[kubevirt-memlock-limit-conditions]: https://github.com/kubevirt/kubevirt/blob/v1.7.0/pkg/virt-handler/isolation/detector.go#L95

## Goals

- Support running any virtual machine with non-trivial memory lock
  RLimit requirements not under just a specific set of cases.
- Make it an opt-in mechanism that easy to configure, yet does not
  impact all KubeVirt-managed virtual machines.
- Adjust memory lock limits without impacting VMI scheduling capacity
  (i.e. increasing pod memory resource limits).
- Remove the existing `ReservedOverhead` API field, which tried
  addressing some of the points above in previous releases.

## Non Goals

- Modifying the overall memory overhead calculation.
- Providing a general mechanism to adjust `virt-launcher` pod memory
  limits.
- Implementing a KubeVirt-Libvirt memory lock RLimit negotiation
  mechanism.
- Change the `'unlimited'` memlock rlimit currently applied to those
  VMIs that meet the conditions listed above.

## Definition of Users

- Users of any VM that might require locking guest memory.
- Kubevirt developers that work on a KubeVirt feature that may require
  the VM or any of its devices to lock guest memory.
- Network binding plugin, hook-sidecar or other type of KubeVirt addon
  developers.

## User Stories

- As a user, I want to run VMs that need to lock guest memory, requiring
  out of the ordinary memory lock RLimits on `virt-launcher` pods and
  qemu processes, no matter if support for those is explicitly
  implemented in the core KubeVirt source code or not.
- As a user, I want to run any workload without requiring in-depth
  knowledge of if they might require locking guest's memory and how to
  compute the memory lock limits by myself.
- As a user, I want to run any workload that might require locking
  guests's memory without implying any security implications related to
  possible memory pressure attacks exploited by malicious device or
  hypervisor implementations, or guests.
- As a KubeVirt developer, I want a general mechanism that allows
  running any VM that require arbitrary memlock RLimits without needing
  to add use-case specific logic.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

This VEP proposes a new API field under `spec.domain.memory.memLock`.
This will hold a pointer to an empty struct. If it is `nil`, it won't
change the default memlock limits, unless the default set of conditions
are met. If not `nil`, even if the struct is empty, it will set the
memlock limits of the `virtqemud` and `qemu-*` processes running in the
virt-launcer pod to `unlimited`.

This way, users that know that their VM need special memory lock limit
requirements can demand them, without affecting pod's memory size, and
so, pod schedulability.

The empty struct was chosen over a `Required`/`NotRequired` sort of
enumeration for easier field extension in the future.

This approach is protected against memory pressure attacks that might be
exercised by malicious device implementation or guests by the launcher
pod's memory resource limits. Even if the memory lock limit is
`'unlimited'`, it accounts virtual memory locked. In case a malicious
workload tries locking more physical memory than the one expected by
KubeVirt and set into the launcher's memory resource limits, the kubelet
would bring it down. Setting those limits is vital to be protected
against that vector, for example, explicitly declaring
`spec.domain.resources.limits.memory`.

As this VEP proposes an alternative way of dealing with VM memory lock
limits, it also requires removing the KubeVirt's Alpha VMI field
`spec.domain.memory.reservedOverhead` added in v1.8 under the
`ReservedOverheadMemlock` feature gate. In favor of
`spec.domain.memory.memLock` and the `ExplicitMemLock` feature gate.

This VEP affects when all supported hypervisors modify the memlock
limits ([kvm][kvm-adjustresources], [mshv][mshv-adjustresources])
in their `AdjustResources` implementations.

[kvm-adjustresources]: https://github.com/kubevirt/kubevirt/blob/e93df5812d69db267055f9c67f2b122ee0f5ab4d/pkg/hypervisor/kvm/runtime.go#L57-L105
[mshv-adjustresources]: https://github.com/kubevirt/kubevirt/blob/e93df5812d69db267055f9c67f2b122ee0f5ab4d/pkg/hypervisor/mshv/runtime.go#L54-L113

## API Examples

A virtual machine requesting `unlimited` memlock limits by setting the
appropriate field so it won't be parsed as `nil`:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  ...
spec:
  template:
    spec:
      domain:
        memory:
          memLock: {}
  ...
```

## Alternatives

### ReservedOverherhead API field
In a previous iteration of this VEP an API field that went for
`ReservedOverhead` was introduced in v1.8/Alpha under the
`ReservedOverheadMemlock` feature gate. For VMs it was located under
`spec.template.domain.memory.reservedOverhead`. It held two subfields:
`addedOverhead`, controlling the amount of memory to account and
`memLock`, controlling whether memory needed to be locked or not.

This however brought some drawbacks, such as the user needing to know
about libvirt's memory lock limit computation internals, it did not
decouple the pod memory size from the memory lock limits, and it did not
scale properly in hotplug/VM-update operations, when backed up by
mutating admission webhooks.

An example of a VM with this field configured:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  ...
spec:
  template:
    spec:
      domain:
        memory:
          reservedOverhead:
            addedOverhead: 1G
            memLock: "Required"
  ...
```

With this configuration, the memory RLimits of the `virt-launcher` pod
and its qemu process would be updated to match the VM memory + `1G`.

Mutating admission webhooks could create or increase that field if some
conditions (up to each webhook) were met.

### Libvirt exposes memlock Rlimit requirements through API
Another option would be to outsource memlock limit calculation to
libvirt. KubeVirt would define the domainXML, request the memory lock
limit required to start the VM to libvirt, then finally, start the VM.

**Pros**:
- Does not implement any logic regarding memory lock RLimit calculation
  in KubeVirt.
- The approach works no matter the device or VM type.
- Does not set an unlimited limit, but keeps it right where libvirt
  expects to be necessary.

**Cons**:
- Libvirt lacks an API exposing that information.
- KubeVirt lacks the VM lifecycle stage between domain definition and VM
  startup, needed to sync this requirement.

### Network binding plugin specific field for memory lock configuration

This was the first proposal of this VEP.

**Description**: Extend the network binding plugin interface so it also
accepts a boolean flag that determines whether a specific network
binding plugin has out of the ordinary memlock RLimit configuration
requirements. Based on that, `virt-handler` would compute the new
RLimits and apply them if needed. The formula computing the RLimits
would be reverse engineered from what libvirt calculates.

**Pros**:
- It doesn't require each network binding plugin to implement mutating
  admission webhooks if they have specific memlock RLimit requirements.

**Cons**:
- KubeVirt holds the logic computing the memory lock RLimits needed for
  any sort of network binding plugin that might need from it.
- The formula to compute the memlock RLimits is general for every
  network binding plugin and is reverse engineered from what libvirt
  expects.
- Does not scale well to other use-cases apart from network binding
  plugins that might need to specify new memlock RLimits.

### Sidecar Hook for Memory Lock configuration

**Description**: Extend the sidecar hooks with another hook that would
return the expected memory lock limits for a certain use-case. The
`virt-launcher` would call the hooks of all configured sidecars and come
up with the resulting memory lock limit requirements during start-up.
The `virt-launcher` would expose this information for the
`virt-handler`.  The `virt-handler` would finally apply those
requirements.

**Pros**:
- It leads into a more general solution as it permits each use-case to
  define their own specific memory lock limit computation hook.
- It is a solution that benefits sidecars, which also include network
  binding plugins as the outcome of a broader solution

**Cons**:
- More complex implementation requiring extra launcher-handler
  integration.
- This implementation would rely on each possible user to implement a
  sidecar for memory lock requirement configuration.

### Defining and tracking an annotation on VirtualMachines

**Description**: Define an annotation key such as
`kubevirt.io/memlock-rlimit` that `virt-handler` would track on
`VirtualMachine`s, and apply on the `virt-launcher`. Admission webhooks
registered by network binding plugins or for other reasons could modify
the annotation accordingly.

**Pros**:
- It serves network binding plugins as well as any other possible device
  or VM configuration that could need to modify `virt-launcher` memory
  lock RLimits.
- Easier to change in the future as it is not bound to the VM spec.

**Cons**:
- It would not be so clear for users, even if documented properly, as it
  would be out of the API.
- It wouldn't be together with other memory specifics in the VM spec.

## Scalability

This approach decouples virt-launcher memory sizing from memlock limits.
In other words, it stops treating limits on virtual locked memory as
physical memory limits. This allows running workloads that need to lock
the VM memory several times (resulting on a high accounted virtual
locked memory) while keeping restricted physical memory limits on
virt-launcher pods, which improves VM schedulability on nodes.

There is already some logic that covers SR-IOV, RT, SEV machines' memory
lock limit requirements. The existing logic is ad-hoc for each use case.
This implementation removes these conditions in favour of the global
`'unlimited'` limit.

This implementation does not need time to compute limits based on
conditions, number of attached devices, or others. However, it might
introduce VMI registration delays if mutating admission webhooks are
deployed in the cluster to add the `memLock` field automatically to
those VMIs that need it.

## Update/Rollback Compatibility

- SEV and RT VMs or those with with SR-IOV devices, already have their
  memlock rlimits configured to `unlimited`, so they are not affected
  during update/rollback.
- It removes a field from VM domain memory specs: `ReservedOverhead`.
  However, this was under a feature gate and in Alpha stage in v1.8 and
  v1.9. So it does not affect the stable, or beta KubeVirt API.
  Moreover, VMs with those fields set can be easily accomodated in new
  version virt-launcher pods as the limits will be higher.
- On rollback, VMIs with memory lock requirements that were not
  supported previously will fail to start. However, SR-IOV, SEV and RT
  virtual machines won't fail, as they were supported by a different
  mechanism in former versions.

## Functional Testing Approach

Tests should cover that `virt-handler` configures `'unlimited'` memlock
rlimits in the `virt-launcher` pods of those VMIs that requested it.

The following e2e scenarios should be considered:
- VM creation: a VM that requires special memlock RLimits can start if
  the `spec.domain.memory.memLock` field is set to `{}` (i.e. not
  `nil`).
- VM migration: Memory lock RLimits are set to `'unlimited'` in the
  destination `virt-launcher` and qemu process when a VM with a
  `spec.domain.memory.memLock` field set to `{}` (not `nil`) is
  migrated.


## Implementation History

- 2026/08/12: Alternative approach setting unlimited memory lock limits
  in virt-launcher pods whose VMI requested it is proposed.
- 2026/07/02: Alternative approach setting unlimited memory lock limits
  to all virt-launcher pods is proposed.
- 2026/06/03: Beta graduation definitely pushed back (see sig/compute
  meting).
- 2026/04/29: Plans to graduate the feature into beta in v1.9 are
  accepted.
- 2026/03/04: Feature lands Alpha stage in v1.8
  https://github.com/kubevirt/kubevirt/pull/16956
- 2026/02/27: Feature implementation lands the main branch
  https://github.com/kubevirt/kubevirt/pull/16384
- 2025/12/10: Working on the draft implementation for the new design.
- 2025/11/21: Draft implementation of the first proposed VEP:
  https://github.com/kubevirt/kubevirt/pull/16197

## Graduation Requirements

### Alpha
- v1.10:
    * Remove the previous `spec.domain.memory.reservedOverhead` struct.
    * Remove the `ReservedOverheadMemlock` feature gate (Alpha).
    * Add `spec.domain.memory.memLock` enum field.
    * Add the `ExplicitMemLock` feature gate (Alpha) to protect the new
      field and feature.

### Beta
- v1.11:
    * E2E tests.
    * It is used by other repositories.

### GA
- v1.12:
    * Documentation.
    * Remove `'ExplicitMemLock'` feature gate.
