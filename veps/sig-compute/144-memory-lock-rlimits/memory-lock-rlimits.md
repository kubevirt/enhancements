# VEP #144: Memory Lock RLimit configuration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

This enhancement introduces a new field to the `VirtualMachine` spec to
specify memory lock RLimit configuration requirements. The new field
will be placed into `spec.domain.memory.reservedOverhead`. It will
contain a structure holding an enum and a kubernetes
`resource.Quantity`. The enum will indicate if the memory lock RLimit
needs to be updated, and the `Quantity` will indicate if there's some
extra memory apart from the VM's that could be locked.

Those values can be updated by other external mechanisms, such as
mutating admission webhooks, based on other VMI spec configurations.
Those values will be considered by the `virt-handler` to appropriately
configure the memlock RLimits of the `virt-launcher` pod and qemu
process.

## Motivation

Depending on the devices attached to a VM (SR-IOV, vDPA, ...) or the
type of VM (real time, SEV, ...) VM memory will be locked by QEMU or the
devices themselves to ensure proper functioning. The amount of memory
that a process can lock is limited by the environment's memory lock
RLimit. Libvirt updates environment memory lock limits if they are below
expectations (see [source code][libvirt-vdpa-memlock-ratio]). Those
expectations depend on the guest's memory size, the number of devices
that require locking memory, and the size of other memory regions that
could be also locked (such as the IOMMU region).

Currently, KubeVirt (`virt-handler`) adjusts the memory lock limits of
the `virt-launcher` under a specific set of
[conditions][kubevirt-memlock-limit-conditions]: When SRIOV/VFIO devices
are attached to, or it is a real time or SEV VM.

However, the cases in which the VM might need to lock memory are not
limited to those listed above. In other words, this mechanism might need
to be extended in the future. Moreover, it might be that the devices
requiring to lock VM memory are implemented outside the core kubevirt
source code, such as network binding plugins.

A more general mechanism that applies memory lock RLimits of the
`virt-launcher` based on requirements is needed. Letting users and other
components, such as admission webhooks, to specify those requirements
will help to make the existing mechanism adapt to other use-cases.

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

With the existing approaches, kubevirt lacks a general mechanism for
other agents to specify their memory lock RLimit requirements. For that
reason, some network binding plugins (like vDPA) fail to function, or
require workarounds that do not scale or impact cluster-wide VMI
scheduling.

[libvirt-vdpa-memlock-ratio]: https://gitlab.com/libvirt/libvirt/-/blob/v11.9.0/src/qemu/qemu_domain.c#L8362-8384
[kubevirt-memlock-limit-conditions]: https://github.com/kubevirt/kubevirt/blob/v1.7.0/pkg/virt-handler/isolation/detector.go#L95

## Goals

- Support a mechanism to specify memory lock RLimits.
- Make this mechanism general to possible use cases that need to specify
  their memory lock RLimits.
- Adjust `virt-launcher` pod and qemu process memory lock RLimits based
  on the requirements specified through that mechanism.
- Adjust memory lock limits without impacting VMI scheduling capacity
  (i.e. increasing pod memory resource limits).

## Non Goals

- Modifying the overall memory overhead calculation.
- Changing how memory lock limits are computed for already considered
  conditions (e.g. SR-IOV).
- Providing a general mechanism to adjust `virt-launcher` pod memory
  limits.

## Definition of Users

- Users of VMs that might require out of the ordinary memory lock RLimit
  configurations.
- Kubevirt developers that might need to specify memory lock RLimit
  requirements for the use-case they are working on.
- Network binding plugin developers.

## User Stories

- As a user, I want to adjust memory lock RLimits of `virt-launcher`
  pods and qemu processes to allow the usage of virtualization
  technologies that need it.
- As a user, I want the memory lock RLimits mechanism to be adjustable
  automatically by other components such as admission webhooks.
- As a KubeVirt developer, I want a general mechanism that allows
  specifying memlock RLimits without needing to add use-case specific
  logic.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

This VEP introduces a new optional field to `VirtualMachine` that will
specify the memlock RLimit needs of a VM.

The new field will be `spec.domain.memory.reservedOverhead`. It will
contain two optional subfields:
- `requiresLock` (`string`): An enum indicating whether or not to extend
  the exiting memory lock RLimits of the `virt-launcher` pod and its
  qemu process.
- `value` ([`Quantity`][k8s-resource-quantity]): An optional field to
  let users specify how much memory could be locked apart from the VM's
  memory size. Note this could be larger than the guest's maximum memory
  and even larger than the maximum memory allowed for the
  `virt-launcher`.

If `requiresLock: "true"`, the logic that adjusts `virt-launcher` pod's
memory lock RLimits will be triggered. By default (when `value` is empty
or zero), the RLimits will be updated to match expected minimum value of
it. This base will equal the sum of VM's base memory size and whatever
other needs that KubeVirt might consider internally (see
[`GetMemoryOverhead`][kbvirt-getMemOverhead] logic).  Whatever value
that `value` has will be added to that base.

Note that if the expected memory RLimit is $2 * Mem_{VM}$, `value` only
needs to carry $Mem_{VM}$, as the base RLimit will already consider the
memory size of the VM once.

Users could set those fields by themselves. Mutating admission webhooks
could also watch for creation of VMs and compute memlock RLimit
requirements based on their own specific needs and conditions. This way,
RLimit calculation logic and the conditions in which they apply are
outsourced to admission webhook implementations, each supporting
different devices/conditions' needs.

As there might be multiple admission webhooks that want to update the
memlock RLimit, webhook implementations will need to check if the field
already exists, and if so, add their computed memlock RLimits to the
previously existing value. As mutating admission webhooks are executed
sequentially, this does not imply race conditions.

This field will be consumed by the `virt-handler` and will apply the
requirements onto the `virt-launcher` pod and its qemu process
accordingly.

There are certain cases in which the `virt-handler` adjusts memlock
RLimits already. When this new mechanism is introduced, `virt-handler`
will track the annotation, and if any, it will add it to the result of
the calculation existing for VFIO, SEV and RT VMs.

[k8s-resource-quantity]: https://github.com/kubernetes/apimachinery/blob/173776a0582da70432e19d16eca025c451600ae8/pkg/api/resource/quantity.go#L102
[kbvirt-getMemOverhead]: https://github.com/kubevirt/kubevirt/blob/50b9b4d0cf1caaf56147411a1e6765134e0e82b9/pkg/virt-controller/services/renderresources.go#L425

## API Examples

Users can specify that a `VirtualMachine` could require to lock memory
through `spec.domain.memory.reservedOverhead.requiresLock`. If any other
region apart from the VM memory could be locked too, the user can
specify it by setting `spec.domain.memory.reservedOverhead.value`.

An example with both fields set:

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
          value:
            value: 1G
            requiresLock: "true"
  ...
```

With this configuration, the memory RLimits of the `virt-launcher` pod
and its qemu process would be updated to match the VM memory + `1G`.

Mutating admission webhooks can create or increase that field if some
conditions (up to each webhook) are met.

## Alternatives

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

There is already some logic that covers SR-IOV, RT, SEV machines' memory
lock limit requirements. The existing logic is ad-hoc for each use case.
This implementation accepts that these conditions already live in the
KubeVirt codebase. Even if it could also cover those use-cases it
doesn't intend to remove them from the code-base, at least at the
current stage.

Apart from that, users could set these fields by themselves, but we
expect this general mechanism to be consumed mainly by mutating
admission webhooks not to require the user to run some of the
mathematical operations needed to come up with proper `value` values.

Note that mutating admission webhooks do introduce certain latency
during pod admission. This means that in case the number of devices and
conditions that require special memlock RLimits is large enough, this
could introduce a considerable latency to `VirtualMachine` admission.

## Update/Rollback Compatibility

- It adds a new optional field, meaning it is backwards compatible.
- On rollback, VMIs with `spec.domain.memory.reservedOverhead` fields
  will fail to start if they are migrated or the `virt-launcher` is
  destroyed and created again.

## Functional Testing Approach

Unit tests should cover that `virt-handler` specifies the right memory
lock RLimit value for different `spec.domain.memory.reservedOverhead`
values:
- The field does not exist or is empty.
- It exists with `requiresLock: "true"` but an empty `value`.
- It exists with `requiresLock: "true"` and a valid `value`.
- It exists with `requiresLock: "false"` and a valid `value`.
- Other conditions such as SEV/RT/SR-IOV are met and there are other
  requirements present in `reservedOverhead`.

The following e2e scenarios should be considered:
- VM creation: a VM that requires special memlock RLimits can start if
  the `spec.domain.memory.reservedOverhead` field containing a proper
  configuration is present.
- VM migration: Memory lock RLimits are configured properly in the
  destination `virt-launcher` and qemu process when a VM with a
  `spec.domain.memory.reservedOverhead` field is migrated thus, it can
  keep running in the destination.
- VM memory hotplug: guests with explicit memlock RLimit needs exposed
  by `spec.domain.memory.reservedOverhead` and expected memory
  hot-/un-plug operations can start up successfuly and keep running
  after memory hot-/un-plug.

## Implementation History

- 2025/12/10: Working on the draft implementation for the new design.
- 2025/11/21: Draft implementation of the first proposed VEP:
  https://github.com/kubevirt/kubevirt/pull/16197

## Graduation Requirements

### Alpha
- Make `virt-handler` track the value of the new fields in
  `spec.domain.memory.reservedOverhead` and apply the memory lock limits
  to `virt-launcher` and QEMU processes accordingly under a feature
  gate.
- Add feature gate `MemLockRLimitConfiguration`.
- e2e testing implementation.
- Documentation.

### Beta
- Notify that other memory extension mechanisms will be replaced by this
  one in the future.

### GA
- Remove the `MemLockRLimitConfiguration` feature gate.
- Deprecate other memory extension mechanisms.
