# VEP #144: Memory Lock rlimit configuration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This enhancement introduces a new field to `VirtualMachine` annotations
to specify memory lock rlimit configuration requirements. That
annotation can be updated by other external mechanisms, such as mutating
admission webhooks, based on the VMI spec and specific conditions. The
value of that annotation field will be checked by the `virt-handler` to
appropriately configure the memlock rlimits of the `virt-launcher` and
qemu process.

## Motivation

Depending on the devices attached to a VM (SR-IOV, vDPA, ...) or the
type of VM (real time, SEV, ...) VM memory will be locked by QEMU or the
devices themselves to ensure proper functioning. The amount of memory
that a process can lock is limited by the environment's memory lock
rlimit. Libvirt updates environment memory lock limits if they are below
expectations (see [source code][libvirt-vdpa-memlock-ratio]). Those
expectations depend on the guest's memory size and the number of devices
that require locking memory.

Currently, KubeVirt (`virt-handler`) adjusts the memory lock limits of the
`virt-launcher` under a specific set of
[conditions][kubevirt-memlock-limit-conditions]: When SRIOV/VFIO devices
are attached to, or it is a real time or SEV VM.

However, the cases in which the VM might need to lock memory are not
limited to those listed above. In other words, this mechanism might need
to be extended in the future. Moreover, it might be that the
implementation for devices that require locking memory lives outside the
core kubevirt source code, such as network binding plugins.

A more general mechanism that applies memory lock rlimits of the
`virt-launcher` based on requirements is needed. In addition, a
mechanism that allows specifying those rlimits by other components that
live outside the core kubevirt source code is needed. That way, some
cases, such as the vDPA network binding plugin, could specify their
needs through a mutating admission webhook, for example.

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
   And finally, it does not trigger memory lock rlimit configuration.

With the existing approaches, kubevirt lacks a general mechanism for
other agents to specify their memory lock rlimit requirements. For that
reason, network binding plugins requiring specific memory lock
requirements (like vDPA) fail to function, or require workarounds that
do not scale or impact cluster-wide VMI scheduling.

[libvirt-vdpa-memlock-ratio]: https://gitlab.com/libvirt/libvirt/-/blob/v11.9.0/src/qemu/qemu_domain.c#L8362-8384
[kubevirt-memlock-limit-conditions]: https://github.com/kubevirt/kubevirt/blob/v1.7.0/pkg/virt-handler/isolation/detector.go#L95

## Goals

- Support mechanism to specify memory lock rlimits.
- Make this mechanism general to possible use cases that need to specify
  their memory lock rlimits.
- Adjust `virt-launcher` pod and qemu process memory lock rlimits based
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

- Users of VMs that might require out of the ordinary memory lock rlimit
  configurations.
- Kubevirt developers that might need to specify memory lock rlimit
  requirements for the use-case they are working on.
- Network binding plugin developers.

## User Stories

- As a network binding plugin user, I want a mechanism to adjust memory
  lock rlimits of `virt-launcher` pods and qemu processes based on VMI
  configuration.
- As a KubeVirt developer, I want a general mechanism that allows
  specifying the memlock rlimit requirements of a future use-case that
  might need it.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

This solution introduces a new optional annotation to `VirtualMachine`s,
`kubevirt.io/memlock-rlimit`. The annotation will be used to specify the
memlock rlimit needs of a VM. The value of that annotation key will
contain a string that can be unmarshalled into a [`Quantity`
object][k8s-resource-quantity] from
`k8s.io/apimachinery/pkg/api/resource`. Mutating admission webhooks
could watch creation of VMs, check whether a set of conditions are met
and compute their own memlock rlimit requirements based on the VM's
configuration.

This way, rlimit calculation logic and the conditions in which they
apply are outsourced to admission webhook implementations, each
supporting different devices/conditions' needs.

As there might be multiple admission webhooks that want to update the
memlock rlimit, webhook implementations will need to check if such
annotation exists, and if so, add their computed memlock rlimits to the
previously existing value. As mutating admission webhooks are executed
sequentially, this does not imply race conditions.

This annotation will be tracked by the `virt-handler` and will apply the
requirements onto the `virt-launcher` pod and the qemu process
accordingly.

There already are certain cases in which the `virt-handler` adjusts
memlock rlimits. When this new mechanism is introduced, `virt-handler`
will track the annotation, and if any, it will add it to the result of
the calculation existing for VFIO, SEV and RT VMs.

[k8s-resource-quantity]: https://github.com/kubernetes/apimachinery/blob/173776a0582da70432e19d16eca025c451600ae8/pkg/api/resource/quantity.go#L102

## API Examples

`VirtualMachine`s could include the `kubevirt.io/memlock-rlimit`
annotation with a valid resource value:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  annotations:
    kubevirt.io/latest-observed-api-version: v1
    kubevirt.io/storage-observed-api-version: v1
    kubevirt.io/memlock-rlimit: "2G"
  ...
spec:
  ...
```

Mutating admission webhooks can create or increase that field if some
conditions (up to each webhook) are met.

## Alternatives

### Network binding plugin specific field for memory lock configuration

This was the first proposal of this VEP.

**Description**: Extend the network binding plugin interface so it also
accepts a boolean flag that determines whether a specific network
binding plugin has out of the ordinary memlock rlimit configuration
requirements. Based on that, `virt-handler` would compute the new
rlimits and apply them if needed. The formula computing the rlimits
would be reverse engineered from what libvirt calculates.

**Pros**:
- It doesn't require each network binding plugin to implement mutating
  admission webhooks if they have specific memlock rlimit requirements.

**Cons**:
- KubeVirt holds the logic computing the memory lock rlimits needed for
  any sort of network binding plugin that might need from it.
- The formula to compute the memlock rlimits is general for every
  network binding plugin and is reverse engineered from what libvirt
  expects.
- Does not scale well to other use-cases apart from network binding
  plugins that might need to specify new memlock rlimits.

### Sidecar Hook for Memory Lock configuration

**Description**: Extend the sidecar hooks with another hook that would
return the expected memory lock limits for a certain use-case. The
`virt-launcher` would call the hooks of all configured sidecars and come
up with the resulting memory lock limit requirements during start-up.
The `virt-launcher` would expose this information for the `virt-handler`.
The `virt-handler` would finally apply those requirements.

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

**Description**: Extend the VMI spec with a dedicated field for
memory lock requirement configuration. Admission webhooks registered by
network binding plugins or for other reasons could modify the field
accordingly, exposing the field to the user, but not requiring the user
to set it explicitly.

**Pros**:
- It serves network binding plugins as well as any other possible device
  or VM configuration that could need to modify `virt-launcher` memory
  lock rlimits.

**Cons**:
- Harder to change in the future.

## Scalability

There is already some logic that covers SR-IOV, RT, SEV machines' memory
lock limit requirements. The existing logic is ad-hoc for each use case.
This implementation accepts that these conditions already live in the
KubeVirt codebase. Even if it could also cover those use-cases it
doesn't intend to remove them from the code-base, at least at the
current stage.

Apart from that, this should provide a general mechanism so that memory
lock rlimits can be applied according to needs based on conditions and
requirements of each VM's configuration that are outsourced to mutating
admission webhooks.

Note that mutating admission webhooks do introduce certain latency
during pod admission. This means that in case the number of devices and
conditions that require special memlock rlimits is large enough, this
could introduce a considerable latency to `VirtualMachine` admission.

## Update/Rollback Compatibility

- It adds a new optional field, meaning it is backwards compatible.
- It does not modify VM/VMI specs, but adds a new annotation.
- On rollback, VMIs with the `kubevirt.io/memlock-rlimit` annotation
  will fail to start if they are migrated or the `virt-launcher` is
  destroyed and created again.

## Functional Testing Approach

Unit tests should cover that `virt-handler` specifies the right memory
lock rlimit value when the `kubevirt.io/memlock-rlimit` annotation is
present. For cases such as:
- Annotation does not exist.
- There is an annotation with a proper value.
- A SR-IOV device exists and there is an annotation with a value.

The following e2e scenarios should be considered:
- VM creation: a VM that requires special memlock rlimits can start if
  the `kubevirt.io/memlock-rlimit` annotation containing a proper value
  was added.
- VM migration: Memory lock rlimits are configured properly in the
  destination `virt-launcher` and qemu process when a VMIs with a
  `kubevirt.io/memlock-rlimit` annotation are migrated.
- VM memory hotplug: guests with explicit memlock rlimit needs exposed
  by `kubevirt.io/memlock-rlimit` and expected memory hot-/un-plug
  operations can start up successfuly.

## Implementation History

- 2025/12/10: Working on the draft implementation for the new design.
- 2025/11/21: Draft implementation of the first proposed VEP:
              https://github.com/kubevirt/kubevirt/pull/16197

## Graduation Requirements

### Alpha

- Make `virt-handler` track the value of annotation
  `kubevirt.io/memlock-rlimit` and apply the memory lock limits to
  `virt-launcher` and QEMU processes accordingly under a feature gate.
- Add feature gate `MemLockRLimitConfiguration`

### Beta

### GA

- e2e testing is implemented and passing.
- Documentation.
- Not under a feature gate.
