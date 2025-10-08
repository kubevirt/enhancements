# VEP #95: Hypervisor Abstraction Layer

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Introduce a Hypervisor Abstraction Layer that lets KubeVirt plug in multiple hypervisor backends through a consistent contract, while keeping today's KVM-first behavior unchanged. To start, we scope the contract to device selection, domain tweaks, validation and mutation so as to provide a flexible base that can evolve without introducing a major refactor. 

## Motivation

- KubeVirt currently hard-codes KVM/QEMU assumptions throughout virt-launcher, virt-handler, virt-controller, and node preparation scripts.
- Platform teams that rely on alternative accelerators or hypervisor stacks face invasive forks to replace `/dev/kvm`, libvirt domain types, or resource scheduling hints.
- A scoped abstraction keeps the project approachable for contributors while unlocking new hardware backends.

## Goals

- Provide an explicit `Hypervisor` interface that supplies device requirements to schedule against plus domain defaults and mutators, avoiding invasive changes to existing components.
- Resolve the active hypervisor early and feed it through the virt-launcher converter so hypervisor-specific behavior stays localized.
- Let components request allocatable device resources declared by the hypervisor configuration, avoiding new scheduling primitives.
- Make it simple for downstreams to implement new hypervisors by following a documented contract.

## Non Goals

- Deliver a full implementation of any specific new hypervisor backend.
- Redesign the VirtualMachineInstance API schema beyond additive fields.
- Mandate new observability requirements; telemetry hooks remain optional.

## Definition of Users

- Cluster administrators who need to bootstrap KubeVirt on hardware that exposes alternative virtualization devices.
- Platform vendors integrating proprietary or emerging hypervisor stacks with KubeVirt.
- Upstream contributors maintaining virt-launcher, virt-controller, and virt-handler.

## User Stories

1. As a cluster administrator, I can declare a non-KVM hypervisor as the cluster default, and VMI pods schedule only on nodes that expose its required devices.
2. As a platform engineer, I can supply hypervisor-specific libvirt defaults and mutators without forking the virt-launcher domain converter.
3. As an upstream maintainer, I know exactly where to add validation, testing, and documentation when a new hypervisor is introduced.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (this VEP)

## Design

### Hypervisor Interface

```go
// pkg/hypervisor/hypervisor.go

type DeviceRequirement struct {
  Resource string
  Count    int64
  Optional bool
}

type DomainProfile struct {
  Type string
  XMLNS string
  Mutators []DomainMutator
}

type DomainMutator interface {
  Apply(*api.Domain, *v1.VirtualMachineInstance) error
}

type ValidationResult = field.ErrorList

type Hypervisor interface {
  Devices(*v1.VirtualMachineInstance) []DeviceRequirement
  DomainDefaults(*v1.VirtualMachineInstance) (DomainProfile, error)
  ValidateVMI(*v1.VirtualMachineInstance) ValidationResult
  MutateVMI(*v1.VirtualMachineInstance)
}
```

The `Hypervisor` interface is intentionally small. It returns static information about required host devices (including optional or multi-quantity entries), libvirt defaults, and mutators that should run during domain conversion. `DeviceRequirement` identifies each resource name, the quantity to request, and whether the dependency is optional.

### Selection and Overrides

- `virt-config` loads cluster-wide defaults from an additive `hypervisorConfiguration` field on the `KubeVirt` CR. The `name` selects the cluster-wide default hypervisor implementation. A dedicated feature gate, `ConfigurableHypervisor`, guards the new functionality:

```yaml
spec:
  configuration:
    hypervisorConfiguration:
      name: kvm
    developerConfiguration:
      featureGates:
        - ConfigurableHypervisor
```

- VMIs can opt in to a specific hypervisor via the annotation `hypervisor.kubevirt.io/name: sample-hypervisor`.
- `virt-controller` resolves the hypervisor for each VMI when generating launcher manifests and embeds the ID in pod annotations and the `ConverterContext`.
- The implementation ships with a factory (`hypervisor.NewHypervisor`) that instantiates the appropriate provider based on the resolved name, keeping selection logic centralized without a global registry.

### Integration with Defaults and Converter

1. `virt-controller` and `virt-handler` populate the `ConverterContext` with the resolved hypervisor ID before calling into virtwrap.
2. `virtwrap/api` defaults request `DomainDefaults` from the active hypervisor to stamp the baseline domain type, XML namespace, and attach mutators.
3. The converter executes the returned mutators immediately after defaults, interleaving hypervisor-specific XML edits with existing architecture helpers (CPU topology, devices, timers).
4. Subsequent converter phases remain untouched.

### Scheduling and Device Management

- `virt-controller` calls `Devices()` to determine the device plugin resources (for example, `devices.kubevirt.io/mshv` plus an auxiliary firmware device) to request. Kubernetes schedules VMI pods only on nodes that advertise the required quantities; entries flagged `Optional: true` may be skipped when the resource is absent.
- `virt-handler`'s device manager uses the same list when spawning its permanent `GenericDevicePlugin` instances, so the existing lifecycle for `/dev/kvm` seamlessly extends to `/dev/mshv` or composite requirements.
- Node labelling remains optional telemetry. Operators can surface informative labels, but functionality relies solely on allocatable resources.

### Validation & Mutation

- Hypervisors can use `ValidateVMI` to enforce requirements. The validating webhooks inside `virt-api` resolve the active hypervisor for every VirtualMachine and VirtualMachineInstance admission request using the same precedence rules as `virt-controller`.
- The mutating webhook shares the same resolution flow and invokes `MutateVMI` early in the admission chain, giving providers a chance to normalize the spec before Kubernetes persists the object.
- In tandem, these hooks enable opinionated defaults for each hypervisor while still rejecting incompatible specs, delivering flexibility without sacrificing guardrails.

### Observability Hooks

- The chosen hypervisor ID is attached to launcher pods as `hypervisor.kubevirt.io/name` for dashboards and debugging.
- Monitoring can leverage existing metrics that expose allocatable device resources (e.g., `devices_kubevirt_io_*`). No new mandatory metrics are introduced.

## API Examples

### Cluster Configuration

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    hypervisorConfiguration:
      name: sample
    developerConfiguration:
      featureGates:
        - ConfigurableHypervisor
    imagePullPolicy: Always
  imagePullPolicy: Always
```

### VMI Annotation Override

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: demo-vmi
  annotations:
    hypervisor.kubevirt.io/name: sample-hypervisor
spec:
  domain:
    cpu:
      cores: 4
    devices:
      disks:
        - name: containerdisk
          disk:
            bus: virtio
  volumes:
    - name: containerdisk
      containerDisk:
        image: kubevirt/fedora-cloud-container-disk-demo:latest
```

### Adding a Hypervisor Implementation

```go
func (h *SampleHypervisor) Devices(_ *v1.VirtualMachineInstance) []hypervisor.DeviceRequirement {
  return []hypervisor.DeviceRequirement{{
    Resource: "devices.kubevirt.io/sample",
    Count:    1,
  }}
}

func (h *SampleHypervisor) DomainDefaults(_ *v1.VirtualMachineInstance) (hypervisor.DomainProfile, error) {
  profile := hypervisor.DomainProfile{
    Type: "sample",
    Mutators: []hypervisor.DomainMutator{sampleDomainMutator{}},
  }
  return profile, nil
}

type sampleDomainTypeMutator struct{}

func (sampleDomainTypeMutator) Apply(domain *api.Domain, _ *v1.VirtualMachineInstance) error {
  if domain == nil {
    return nil
  }
  domain.Spec.Type = "sample"
  return nil
}

// Wire the implementation into the factory, typically by extending
// hypervisor.NewHypervisor with a new case:

func NewHypervisor(name string) hypervisor.Hypervisor {
  switch strings.ToLower(name) {
  case "sample":
    return &SampleHypervisor{}
  default:
    return &KVMHypervisor{}
  }
}
```

## Alternatives

1. **Status quo** – Continue duplicating KVM assumptions everywhere. This blocks new hypervisors and increases maintenance burden.
2. **Deep plugin model** – Move domain generation to separate binaries per hypervisor. Rejected for complexity and duplication of KubeVirt control-plane logic.
3. **Libvirt-only configuration** – Attempt to encode all variability via libvirt XML fragments in CRDs. Lacks validation, testing, and integration with device management.

### Future Enhancements

Full abstraction with—multi-device descriptors, richer domain defaults, distribution of hypervisor-specific logic amongst component libraries.

## Scalability

- Hypervisor selection is resolved via a constant-time factory call during existing reconciles, keeping control-loop complexity independent of how many implementations ship.
- Converter mutators execute in a deterministic order to avoid combinatorial growth in conditionals.

## Update/Rollback Compatibility

- The feature is additive. Clusters without hypervisor configuration continue to use KVM exclusively.
- Rolling back to a version without the abstraction leaves hypervisor-specific annotations unused but harmless.

## Functional Testing Approach

- Unit tests for each hypervisor implementation verifying domain defaults and mutators.
- Integration tests covering virt-controller manifest rendering and device manager plugin registration with other hypervisors enabled.
- End-to-end lanes that launch VMIs under at least two hypervisors (e.g., KVM plus a stub hypervisor) to confirm scheduling and domain generation.

## Implementation History

- 2025-10-XX: Initial VEP draft.

## Graduation Requirements

### Alpha

- Feature gate covers configurable hypervisor
- Cluster-wide hypervisor configuration and VMI annotation implemented.
- Basic functional tests for alternative hypervisor scheduling and domain generation.

### Beta

- Monitoring and observability hooks consumed by community dashboards.
- Upgrade/rollback testing executed in CI.

### GA

- Documentation reflects hypervisor lifecycle and contributor workflow.
