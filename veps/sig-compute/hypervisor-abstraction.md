# VEP #97: Hypervisor Abstraction Layer

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal introduces a Hypervisor Abstraction Layer for KubeVirt, enabling the platform to integrate multiple hypervisor backends through a set of consistent, well-defined interfaces—while preserving the current KVM-first behavior as the default. In this VEP, the term “Hypervisor” denotes the hardware‑level virtualization engine—such as KVM or any component offering similar functionality—that provides CPU, memory, and interrupt virtualization beneath the VMM layer (e.g., QEMU).

The initial scope focuses on key areas critical to hypervisor integration, including:

- Device exposition and selection
- Adjustments to Libvirt domain XML
- Spec validation and mutation logic
- Runtime modifications to VirtualMachineInstances (VMIs)

By limiting the scope to these foundational aspects, the design provides a flexible base that can evolve over time without requiring a disruptive refactor of existing components.

## Motivation

- KubeVirt currently hard-codes KVM/QEMU assumptions throughout virt-launcher, virt-handler, virt-controller, and node preparation scripts.
- Platform teams that rely on alternative accelerators or hypervisor stacks face invasive forks to replace `/dev/kvm`, libvirt domain types, or runtime adjustments (e.g., assigning housekeeping threads to a particular cgroup).
- A scoped abstraction keeps the project approachable for contributors while unlocking new hardware backends.

## Goals

- Document the cluster-wide hypervisor configuration and per-component extension points (defaults, converter, webhooks, node labeller) so downstream implementations can extend behavior without invasive changes to existing components.
- Resolve the hypervisor to be used for a VMI early and feed it through the virt-launcher converter so hypervisor-specific behavior stays localized.
- Support both admission-time validation and mutation so administrators can enforce guardrails while still customizing VMIs for a given hypervisor.
- Let VMs request hypervisor-specific allocatable device resources, avoiding new scheduling primitives.
- Make it simple for downstreams to implement new hypervisors by following a documented contract.

## Non Goals

- Support multiple hypervisors/accelerators on the same KubeVirt deployment.
- Extension of `VMI` CRD to introduce `Hypervisor` field. Since multiple hypervisors/accelerators in the same KubeVirt deployment is not in scope, VMIs do not need to explicitly specify the target hypervisor.
- Redesign the VirtualMachineInstance API schema beyond additive fields.
- Replace existing Hyper-V enlightenment features or other architecture-specific helpers.
- Mandate new observability requirements; telemetry hooks remain optional.

## Definition of Users

- Cluster administrators who need to bootstrap KubeVirt on hardware that exposes alternative virtualization devices.
- VM owners who would like to run a virtual machines using a non-KVM hypervisor with its differentiated capabilities
- Platform vendors integrating proprietary or emerging hypervisor stacks with KubeVirt.
- Upstream maintainers of core virt-launcher, virt-controller, and virt-handler.
- Hypervisor-specific experts maintaining hypervisor-specific logic and validations.

## User Stories

1. As a cluster administrator, I would like to deploy KubeVirt on a cluster with non-KVM hypervisor nodes, and have non-KVM VMs schedule only on nodes that expose its required devices.
2. As a platform engineer, I can supply hypervisor-specific VMI spec mutations and libvirt domain adjustments without forking the virt-launcher converter.
3. As a core maintainer, I can maintain and develop the core of KubeVirt without deep knowledge of all specific hypervisors. 
4. As a hypervisor-specific expert I know exactly where to add hypervisor-specific validation, testing and documentation when a new hypervisor is introduced, letting me develop quickly and independently.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (this VEP)

## Design

### Hypervisor Extension Points

Cluster configuration (`spec.configuration.hypervisor`) declares the list of supported hypervisors for the KubeVirt installation, and each control-plane package exposes focused extension contracts so downstream implementations only touch the areas they actually need:

- **Validation webhooks (`pkg/virt-api/webhooks/validating-webhook/admitters/hypervisor/`)** – We introduce a Validator interface that will define validation functions for core KubeVirt resources that have hypervisor-specific constraints, namely VM and VMI. Each hypervisor will provide its own concrete Validator to enforce rules and constraints relevant to its capabilities. Because much of the VM/VMI validation logic applies is expected to be hypervisor-agnostic, we will extract this shared logic into a base validation layer, making it available to all hypervisor‑specific validators.

  ```go
  type Validator interface {
      // Validate spec of VirtualMachine
      ValidateVirtualMachineSpec(field *k8sfield.Path, spec *v1.VirtualMachineSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause

      // Validate spec of VirtualMachineInstance
      ValidateVirtualMachineInstanceSpec(field *k8sfield.Path, spec *v1.VirtualMachineInstanceSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause
      
      // Validate hot-plug updates to VMI. For example, this would encapsulate functionality in the ValidateHotplugDiskConfiguration function.
      ValidateHotplug(oldVmi *v1.VirtualMachineInstance, newVmi *v1.VirtualMachineInstance, cc *virtconfig.ClusterConfig) []metav1.StatusCause
  }
  ```

 - **Defaults provider registry (`pkg/defaults/providers/`)** – Introduces a single `DefaultsProvider` interface applied in layered order (Base → Hypervisor → Architecture → Hypervisor+Architecture → Finalization). Providers are registered under composite keys like `kvm/amd64` or `mshv/arm64`. Each provider implements:
   ```go
    type DefaultsProvider interface {
        SetVirtualMachineDefaults(vm *v1.VirtualMachine, clusterConfig *virtconfig.ClusterConfig, virtClient kubecli.KubevirtClient)
        SetDefaultVirtualMachineInstance(clusterConfig *virtconfig.ClusterConfig, vmi *v1.VirtualMachineInstance) error
        SetDefaultVirtualMachineInstanceSpec(clusterConfig *virtconfig.ClusterConfig, spec *v1.VirtualMachineInstanceSpec) error
    }
   ```
   Only zero-value fields are set at each layer; `FinalizeVMI` handles derived/status data (CPU topology snapshot, memory status, hotplug sizing, feature dependency resolution). Existing public functions delegate to the resolved provider for backwards compatibility.
- **Runtime interfaces (`pkg/virt-handler/runtime/`)** – Provides interfaces for tuning a running VM or interacting with the virtualization stack outside of Libvirt. 
  1.  `DomainTuner` interface for tuning a running virtual machine.
      ```go
      type DomainTuner interface {
          // Adjust memlock limit for QEMU process
          AdjustResources(vmi *v1.VirtualMachineInstance, additionalOverheadRatio *string) error
          // Assign housekeeping CPU threads to housekeeping cgroup
          HandleHousekeeping(vmi *v1.VirtualMachineInstance, domain *api.Domain) error
      }
      ```

  2. `HypervisorCapabilityExtractor` interface for querying the hypervisor device for its capabilities.
      ```go
      type HypervisorCapabilityExtractor interface {
        GetHypervFeatures() []string
      }
      ```

  3. `VirtLauncherResourceRenderer` for computing resource requirement of the `virt-launcher` pod.
      ```go
      type VirtLauncherResourceRenderer interface {
        GetMemoryOverhead(vmi *v1.VirtualMachineInstance, arch string, additionalOverheadRatio *string) resource.Quantity
      }
      ```
          

- **Converter library (`pkg/virt-launcher/virtwrap/converter/`)** – Adds a new `Converter` interface, which contains the main function to convert VMI to Libvirt domain.

  ```golang
  type Converter interface {
    Convert_v1_VirtualMachineInstance_To_api_Domain(vmi *v1.VirtualMachineInstance, domain *api.Domain, c *ConverterContext) (err error)
  }
  ```

  The `BaseConverter` struct that implements the above interface would contain common functionality for VMI to domain conversion. Each hypervisor will be able to implement their own converter, e.g., `MshvConverter`, embed the `BaseConverter` and override certain functions of the `BaseConverter` with their own.

- **Node labeller** – Adds parameters `--virt-type` and `--hypervisor-device` to the `cmd/virt-launcher/node-labeller/node-labeller.sh` script, so that it can correctly query Libvirt for node and domain capabilities.

This split preserves the “implement once, reuse everywhere” story without routing everything through a monolithic interface. New hypervisors can land incrementally—start with defaults and webhooks, add converter support, then extend node labelling—while keeping the contract for each area explicit and testable.

### Selection

- `virt-config` loads cluster-wide defaults from an additive `hypervisor` field on the `KubeVirt` CR. The `hypervisor` field is a list of hypervisors that can be supported on the cluster. 

  **Single-hypervisor Constraint:** In the current VEP, we will enforce that the number of elements in this list is less than or equal to 1, i.e., to enforce only a single hypervisor for the entire cluster. A future VEP will consider adding support for multiple hypervisors on the same cluster. The `name` in each hypervisor configuration entry selects the cluster-wide hypervisor implementation. Supporting multiple hypervisors in the same cluster will also necessitate the addition of a per-VMI field `hypervisor` to denote on which hypervisor the VMI has to be created.
  
  A dedicated feature gate, `ConfigurableHypervisor`, guards the new functionality:

    ```yaml
    spec:
      configuration:
        hypervisor:
        - name: kvm
          hypervisorDevice: kvm
          virtType: kvm
        developerConfiguration:
          featureGates:
            - ConfigurableHypervisor
    ```

  **Backwards compability:** If the list of `hypervisor` is empty or is not specified at all in the `KubevirtConfiguration` CR, the cluster will fallback to the default KVM hypervisor for all VMIs. The same fallback behavior will be enforced if the feature gate `ConfigurableHypervisor` is not active.

- `virt-controller` reads the configured hypervisor from `ClusterConfig` when generating launcher manifests and threads that ID through the `ConverterContext` so downstream components can act consistently.
- Each package's registry uses the configured name to locate its implementation, avoiding a monolithic factory while keeping selection logic consistent.

### Integration with Defaults, Runtime, Converter and Validating Webhooks

1. The proposed `Validator` interface's hypervisor-specific implementation would be resolved and invoked from within the `Admit` function of the concerned `Admitter` implementations - e.g., `VMsAdmitter`, `VMICreateAdmitter`, `VMIUpdateAdmitter`, `VMIRSAdmitter`, etc.
2. `pkg/defaults` pulls the `DefaultsExtension` associated with the configured hypervisor to mutate the VMI and set the appropriate default values for the specific hypervisor supported on the cluster.
3. `virt-controller` reads the configured hypervisor from `ClusterConfig`, and adds a K8s device request for the appropriate hypervisor device to the `virt-launcher` pod definition so that it can be scheduled on the node with that hypervisor device. Furthermore, it adds the hypervisor information to the command-line of the `virt-launcher`, which the `virt-launcher` pod then uses to instantiate the right implementation of the `HypervisorConverter` for converting VMI spec to Libvirt domain XML.
4. The `virt-controller` resolves the `VirtLauncherResourceRenderer` implementation to run `GetMemoryOverhead`, keeping pod-level resource calculations in sync with the mutated spec. The `DomainTuner` interface is used by virt-handler to run the `AdjustResources` function for memlock sizing and ancillary bookkeeping.
5. When virt-launcher converts the VMI, it instantiates both the `Converter` and the Hypervisor Runtime interfaces. The hypervisor-specific instance of the `Converter` interface leverages common conversion functions as well as hypervisor-specific functions to convert VMI to Libvirt domain.
6. The virt-launcher component will continue to leverage existing helpers for common functionality, such as setLaunchSecurity, disk configuration, and network setup. Hypervisor-specific extensions to the converter logic will extend the base implementation of these helpers to introduce specialized logic.

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
    hypervisor:
    - name: mshv
      hypervisorDevice: mshv
      virtType: hyperv
    developerConfiguration:
      featureGates:
        - ConfigurableHypervisor
    imagePullPolicy: Always
  imagePullPolicy: Always
```

With this configuration in place, every VMI reconciled by the control plane inherits the `mshv` behavior automatically — no per-object annotations are required.

## Reference Implementation

This section presents a reference implementation of how the aforementioned interfaces will be implemented for different hypervisors. The core design principle used in the reference implementations is to provide a **base implementation** for each interface - which would contain hypervisor-agnostic logic - while providing hooks to specify hypervisor-specific logic for that interface's functions. The implementation examples provided are subject to change and should not be interpreted as binding. Their purpose is to facilitate early discussion and gather feedback on the proposed design direction.

### Reference Implementation of Multi-Hypervisor support in Converter

- The existing `pkg/virt-launcher/virtwrap/converter` package becomes a reusable library with a new `hypervisor` subpackage. This contains the aforementioned `HypervisorConverter` interface that exposes the main functions for converting VMI spec to Libvirt domain XML.

  ```golang
  type Converter interface {
    Convert_v1_VirtualMachineInstance_To_api_Domain(vmi *v1.VirtualMachineInstance, domain *api.Domain, c *ConverterContext) (err error)
  }
  ```

- Shared translation helpers for disks, NICs, CPU topology, and security settings live in `converter/base-converter.go` as part of the `BaseConverter`.

  ```go
  type BaseConverter struct {
    // common configurators
    domainConfigurator  configurator
    networkConfigurator configurator
    tpmConfigurator     configurator
    // list of extra configurators to be used
    // by hypervisor-specific converters to extend BaseConverter
    extraConfigurators []configurator

    // converter functionality that is encapsulated in functions rather than configurators
    serialDeviceConverter func(vmi *v1.VirtualMachineInstance, domain *api.Domain, c *ConverterContext) error
  }

  // Initialization of BaseConverter sets the configurators with 
  // their default values
  func NewBaseConverter(c *ConverterContext) *BaseConverter {
    return &BaseConverter{
      domainConfigurator: metadata.DomainConfigurator{},
      networkConfigurator: network.NewDomainConfigurator(
        network.WithDomainAttachmentByInterfaceName(c.DomainAttachmentByInterfaceName),
        network.WithUseLaunchSecuritySEV(c.UseLaunchSecuritySEV),
        network.WithUseLaunchSecurityPV(c.UseLaunchSecurityPV),
      ),
      tpmConfigurator: compute.TPMDomainConfigurator{},

      serialDeviceConverter: baseSerialDeviceConverter,
    }
  }
  ```

  When instantiating a hypervisor-specific converter, the base configurators can be overridden or extended. Furthermore, conversion logic that lives in a function and not in a configurator can also be overridden.

  ```go
  func NewMshvConverter(c *ConverterContext) *MshvConverter {
    baseConverter := NewBaseConverter(c)

    // Override the base domain configurator
    baseConverter.domainConfigurator = MshvDomainConfigurator{}
    // Add an extra configurator
    baseConverter.extraConfigurators = append(baseConverter.extraConfigurators, ExtraMshvConfigurator{})

    // Custom serial attachment function to override the base
    baseConverter.serialDeviceConverter = customMshvSerialDeviceConverter

    return &MshvConverter{
      baseConverter: baseConverter,
    }
  }
  ```

#### Rationale behind the use of a `BaseConverter` struct

The `BaseConverter` module of KubeVirt was introduced to do two important things:

- Provide the common steps for building a Domain XML from VMI spec. We expect the implementation of the `Convert_v1_VirtualMachineInstance_To_api_Domain` function would live within the `BaseConverter` implementation, with all the configurators necessary to create different parts of the Domain XML as its fields. That way, when Converter is being written for a new hypervisor, the contributor not need to know all the necessary conversions needed to build a Domain XML, just which ones to override.

- Provide the common (hypervisor-agnostic) implementation for each configurator. For hypervisor-specific logic, the hypervisor contributor would have to specify custom configurator implementations only for specific configurators of the `BaseConverter`.  

Therefore the use of a `BaseConverter` implementation with explicit configurators would make the life of a hypervisor contributor much simpler when it comes to implementing the `Converter` interface. We propose the same pattern in the `Defaults` and `Validation` modules, we will be discussed below.

### Reference Implementation of Hypervisor-Specific Defaults

The defaults system is refactored to support multi-axis overrides (hypervisor, architecture, combined) without expanding large `switch` statements. The goals of the refactoring are to ensure that the custom defaults provider for a specific hypervisor should be able to re-use as much of the common defaults provider functionality as possible, while still being able to override certain parts of the common defaults provider.

Following is the `DefaultsProvider` interface as discussed earlier in the design.
```go
type DefaultsProvider interface {
  SetVirtualMachineDefaults(vm *v1.VirtualMachine, clusterConfig *virtconfig.ClusterConfig, virtClient kubecli.KubevirtClient)
	SetDefaultVirtualMachineInstance(clusterConfig *virtconfig.ClusterConfig, vmi *v1.VirtualMachineInstance) error
	SetDefaultVirtualMachineInstanceSpec(clusterConfig *virtconfig.ClusterConfig, spec *v1.VirtualMachineInstanceSpec) error
}
```

The `BaseDefaults` implementation of the `DefaultsProvider` interface contains the common (base) implementation of each of the above interface functions. The `BaseDefaults` struct has several function-type fields, with each field meant to store the logic for setting the default value of a particular section of VMI spec.

```go
type BaseDefaults struct {
  // Architecture-specific Defaults Setter 
	amd64DefaultsSetter   arch_defaults.ArchDefaults
	arm64DefaultsSetter   arch_defaults.ArchDefaults
	s390x64DefaultsSetter arch_defaults.ArchDefaults

  // Function-type fields for setting default vals for parts of the VMI/VM
	defaultVMMachineTypeSetter                func(vm *v1.VirtualMachine, clusterConfig *virtconfig.ClusterConfig)
	defaultVMIMachineTypeSetter               func(clusterConfig *virtconfig.ClusterConfig, vmi *v1.VirtualMachineInstanceSpec)
	currentCPUTopologyStatusSetter            func(vmi *v1.VirtualMachineInstance)
	guestMemoryStatusSetter                   func(vmi *v1.VirtualMachineInstance)
	defaultHypervFeatureDependenciesSetter    func(spec *v1.VirtualMachineInstanceSpec)
	defaultEvictionStrategySetter             func(clusterConfig *virtconfig.ClusterConfig, spec *v1.VirtualMachineInstanceSpec)
  ...
}
```

When an instance of the `BaseDefaults` struct is created, each function field for setting default values is initialized with its default implementation.

A hypervisor-specific implementation of `DefaultsProvider` interface would leverage struct embedding to override specific functions in the default implementation, while reusing the rest. 

```go
type MSHVDefaults struct {
	base_defaults.BaseDefaults
}

// To instantiate the MSHVDefaults struct
baseDefaults := base_defaults.NewBaseDefaults(
  arch_defaults.NewAmd64ArchDefaults(),
  arch_defaults.NewArm64ArchDefaults(),
  arch_defaults.NewS390xArchDefaults(),
)

// Override the default provider function for a specific VMI field
baseDefaults.defaultEvictionStrategySetter = MshvCustomDefaultEvictionStrategySetter

// Create MSHVDefaults with the overridden BaseDefaults
mshvDefaults := mshv_defaults.MSHVDefaults{
  *baseDefaults
}
```

Similarly, if a particular hypervisor-specific defaults provider, e.g., `MSHVDefaults` needs to override an architecture-specific default provider, it can do it in the same way as above.


### Hypervisor-Specific Validations

`pkg/virt-api/webhooks/validating-webhook/admitters/hypervisor/` provides per-hypervisor implementation of the `Validator` interface. Each implementation enforces compatibility (required devices, unsupported feature combinations) after defaults have populated in the spec.

#### Key points

- Validation is distinct from defaulting: validators never set user-facing defaults (that is handled by `DefaultsProvider`).
- Hypervisor-specific rejection messages surface early (webhook) instead of deferring to runtime/libvirt errors.
- Unit tests cover both acceptance of valid specs and explicit rejection of incompatible feature / device combos.

#### Proposed Code Structure

The aforementioned `Validator` interface would be implemented by the `BaseValidator` that contains validation functionality common across hypervisors and architectures.

```go
type BaseValidator struct {}

func (b *BaseValidator) ValidateVirtualMachineInstanceSpec (field *k8sfield.Path, spec *v1.VirtualMachineInstanceSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause {
    var causes []metav1.StatusCause
    ... // more validation functions
    causes = append(causes, b.validateNUMA(field, spec)...)
    causes = append(causes, b.validateGuestMemoryLimit(field, spec)...)
    ... // more validation functions
}

func (b *BaseValidator) validateNUMA(field *k8sfield.Path, spec *v1.VirtualMachineInstanceSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause {
    // generic (hypervisor/arch-agnostic) logic for validation of VMI's NUMA spec
}
```

Each hypervisor-specific implementation of the validator would embed the `BaseValidator` to inherit the common validation logic. A skeleton implementation for MSHV-specific Validator implementation is shown below.

```go
type MshvValidator struct { *BaseValidator }

func (m *MshvValidator) ValidateVirtualMachineInstanceSpec (field *k8sfield.Path, spec *v1.VirtualMachineInstanceSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause {
    var causes []metav1.StatusCause
    // Execute common validation logic
    causes = append(causes, m.BaseValidator.ValidateVirtualMachineInstanceSpec(field, spec, config))
    // Run hypervisor-specific check
    causes = append(causes, m.validateCPUModel(field, spec, config))
    // Run checks specific to the hypervisor-arch pair
    arch := spec.Architecture
    if arch == "" {
      arch = "amd64" // Or from the clusterConfig 
    }
    switch arch {
    case "amd64":
      causes = append(causes, m.validateVmiForAmd64(spec))
    case "arm64":
      causes = append(causes, m.validateVmiForArm64(spec))
    case "s390x":
      causes = append(causes, m.validateVmiForS390x(spec))
    return causes
}

// Validation function catering to an MSHV-specific constraint on VMI CPU
func (m *MshvValidator) validateCPUModel (field *k8sfield.Path, spec *v1.VirtualMachineInstanceSpec, config *virtconfig.ClusterConfig) []metav1.StatusCause {
    var causes []metav1.StatusCause
    // For MSHV hypervisor, the guest's CPU model has to be "qemu64-v1"
    if spec.Domain.CPU.model != "qemu64-v1" {
        // append validation failure cause to causes
    }
    return causes
}
```

#### Code Organization

The following directory will be created to host the `Validator` interface and the hypervisor-specific implementations: `pkg/virt-api/webhooks/validating-webhook/validators`.

- `Validator` interface would be defined in `pkg/virt-api/webhooks/validating-webhook/validators/validator.go`.
- Implementation of `Validator` interface for `KVM` would like in `pkg/virt-api/webhooks/validating-webhook/validators/kvm/kvm.go`.
- Architecture-specific validation logic would reside in per-architecture files of the form `pkg/virt-api/webhooks/validating-webhook/validators/kvm/{amd64,arm64,s390x}.go`.

### Scheduling and Device Management

- `virt-controller` reads the `DeviceRequests` declared by the defaults extension to determine the device plugin resources (for example, `devices.kubevirt.io/mshv` plus an auxiliary firmware device) to request. Kubernetes schedules VMI pods only on nodes that advertise the required quantities; entries flagged `Optional: true` may be skipped when the resource is absent.
- `virt-handler`'s device manager uses the same list when spawning its permanent `GenericDevicePlugin` instances, so the existing lifecycle for `/dev/kvm` seamlessly extends to `/dev/mshv` or composite requirements.
- The node-labeller sidecar in `virt-handler` is invoked with the resolved hypervisor device and virt-type. It probes only the specific hypervisor device and sets the correct `virt-type` to query Libvirt capabilities. With this design, new hypervisors can add support for node-labeller without needing to update the container image.

### Observability Hooks

- Monitoring can leverage existing metrics that expose allocatable device resources (e.g., `devices_kubevirt_io_*`). No new mandatory metrics are introduced.


### Adding a Hypervisor Implementation

1. **Defaults Provider** – `pkg/defaults/providers/sample.go` (or `pkg/defaults/providers/sample_amd64.go` when arch-specific):

   ```go
   // sample.go
   type SampleDefaults struct{ *BaseDefaults }

   func (d *SampleDefaults) ApplyVMDefaults(vm *v1.VirtualMachine, cc *virtconfig.ClusterConfig, client kubecli.KubevirtClient) {
       d.BaseDefaults.ApplyVMDefaults(vm, cc, client) // call embedded base first
       // sample hypervisor-wide VM template tweaks (if any)
   }

   func (d *SampleDefaults) ApplyVMISpecDefaults(spec *v1.VirtualMachineInstanceSpec, cc *virtconfig.ClusterConfig) error {
       if err := d.BaseDefaults.ApplyVMISpecDefaults(spec, cc); err != nil { return err }
       // set zero-value fields only (e.g. disk bus, firmware) for sample hypervisor
       return nil
   }

   func NewSampleDefaults() *SampleDefaults {
      // Create the base defaults
      baseDefaults := base_defaults.NewBaseDefaults(
        arch_defaults.NewAmd64ArchDefaults(),
        arch_defaults.NewArm64ArchDefaults(),
        arch_defaults.NewS390xArchDefaults(),
      )

      // Override the default provider function for a specific VMI field
      baseDefaults.defaultEvictionStrategySetter = SampleCustomDefaultEvictionStrategySetter

      // Create custom defaults provider with the overridden BaseDefaults
      sampleDefaults := SampleDefaults{
        *baseDefaults
      }
   }
   ```

2. **Runtime** – Add `pkg/virt-handler/runtime/sample.go` implementing the hypervisor runtime interfaces interface so controllers, handlers, and virt-launcher share runtime hooks.

   ```go
   type sampleRuntime struct{}

   func (sampleRuntime) AdjustResources(_ *v1.VirtualMachineInstance, _ *string) error {
     // reuse existing helpers; return nil when no adjustments are needed
     return nil
   }

   func (sampleRuntime) GetMemoryOverhead(vmi *v1.VirtualMachineInstance, arch string, ratio *string) resource.Quantity {
     return baseMemoryOverhead(vmi, arch, ratio)
   }

   func (sampleRuntime) HandleHousekeeping(_ *v1.VirtualMachineInstance, dom *api.Domain) error {
     // assign housekeeping CPU threads to appropriate cgroup
     return nil
   }
   ```

3. **Converter** – Add `pkg/virt-launcher/virtwrap/converter/sample.go` implementing the `Converter` interface embedding the BaseConverter (overriding only the methods that differ from the `BaseConverter`).

    ```go
    type SampleConverter struct {
      baseConverter *BaseConverter
    }

    func NewSampleConverter(c *ConverterContext) *SampleConverter {
      baseConverter := NewBaseConverter(c)

      // Override the base domain configurator
      baseConverter.domainConfigurator = SampleDomainConfigurator{}
      // Add an extra configurator
      baseConverter.extraConfigurators = append(baseConverter.extraConfigurators, ExtraSampleConfigurator{})

      // Custom serial attachment function
      baseConverter.serialDeviceConverter = customSampleSerialDeviceConverter

      return &SampleConverter{
        baseConverter: baseConverter,
      }
    }
    ```

4. **Admission** – Create `pkg/virt-api/webhooks/validating-webhook/admitters/hypervisor/sample.go` that exports `MutateVMI` and `Validate` functions and register them in the webhook registry.

## Alternative Designs Considered

### Plugin model for hypervisor backend integration

The alternative design that we evaluated was a plugin model for hypervisor integration. In this approach, KubeVirt would define a set of core interfaces (similar to the ones described above), but instead of implementing these interfaces in-tree, KubeVirt would provide the mechanism for dynamically loading external implementations at runtime. Each hypervisor backend (e.g., KVM, MSHV, or future hypervisors) could supply its own plugin, packaged and maintained in a separate repository. These plugins would register with KubeVirt components (virt-launcher, virt-handler, etc.) through a well-defined contract.

While the plugin-based approach offers strong decoupling and extensibility, it requires significant refactoring of the KubeVirt codebase. Hypervisor-specific logic is currently invoked from multiple components (e.g., virt-launcher, virt-handler, API), and introducing a dynamic plugin mechanism would involve redesigning these interactions and adding lifecycle management for external modules.

For the initial implementation of multi-hypervisor support, we chose an in-tree design to achieve a working solution faster. This approach allows us to validate the abstraction layer, experiment with real-world scenarios, and identify what works and what does not. With these learnings, we will be better positioned to propose a robust plugin-based architecture for multi-hypervisor support in the future.

### Alternatives to KubeVirt CR API change

This VEP proposes to add the `HypervisorConfiguration` field to the `KubevirtConfiguration` CRD. The rationale behind this choice was to allow the cluster admin to declare outright which hypervisor they want KubeVirt to target. Furthermore, it follows the `ArchConfiguration` field in `KubevirtConfiguration` CRD. In addition, we also considered the following alternatives to configure KubeVirt to target a specific hypervisor:

- **Use a separate CRD**: Devise a `KubeVirtHypervisorConfiguration` CRD. If it is found in the `kubevirt` namespace, use it. If not, fall back to `KVM`. This is an approach that meets our requirements, but we prefer to add the `HypervisorConfiguration` field following the precedent of `ArchConfiguration`. Adding a new CRD would also require us to introduce a new controller for it, which will not be needed when the hypervisor configuration is a part of the `KubevirtConfiguration` CRD as we can reuse its existing controller.

- **Querying Worker Nodes**: Iterate over worker nodes and look for a telltale device. If no special devices are found, fall back to KVM. In this approach, since the target hypervisor is not declared outright for the cluster, it would require a `Hypervisor` field in the `VMI` CRD. In this VEP, we do not have extension of `VMI` CRD in scope.

- **Build-time switch**: Use build-tags to compile KubeVirt with support for a particular hypervisor. We did not choose this approach because it would require KubeVirt to build multiple versions of its components to support different hypervisors. A runtime configuration is preferable, especially given the logic for multi-hypervisor support is already in-tree.

## Future Enhancements

Full abstraction with—multi-device descriptors, richer domain defaults, hypervisor-driven validation, and alternate libvirt transports.

- Extend `virt-handler` node-labeller to surface per-hypervisor capability labels so schedulers and operators can audit readiness without relying on device resources alone.

## Scalability

- Hypervisor selection resolves once per reconcile, and each component performs a constant-time lookup in its registry, keeping control-loop complexity independent of how many implementations ship.
- Converter mutators execute in a deterministic order to avoid combinatorial growth in conditionals.

## Update/Rollback Compatibility

- The feature is additive. Clusters without hypervisor configuration continue to use KVM exclusively.
- Rolling back to a version without the abstraction reverts to the existing KVM default once the new configuration field is removed or ignored.

## Functional Testing Approach

The goal of testing for multi-hypervisor support is to ensure that for each in-tree hypervisor implementation, all supported KubeVirt features are verified.

- Unit tests for each hypervisor implementation for validating functionality concerning each extension point with comprehensive coverage for all new code.
- Integration tests should test the following for each in-tree hypervisor:
  - Validate if `virt-handler`'s device plugin can detect correct hypervisor device on the node and label node with correct allocatable and capacity.
  - Validate `virt-controller`'s rendering of `virt-launcher` pod spec.
  - Validate node-labeller's ability to add expected node labels for each hypervisor.
  - Validate runtime adjustments to VMI, such as memlock limit adjustment and housekeeping thread management.

### Integration with existing Prow-based CI testing

To ensure robust validation of the proposed in-tree Microsoft Hypervisor (MSHV) integration, we recommend incorporating MSHV-specific tests into KubeVirt’s existing CI workflows. The following changes are proposed:

- Introduce dedicated testing lanes for MSHV on AMD64, aligned with each SIG (e.g., sig-compute) and Kubernetes version.
- Enhance the Prow provisioner to support provisioning Azure-based Kubernetes clusters, enabling deployment and testing of KubeVirt distributions backed by MSHV.
- Optimize CI resource usage by scheduling non-KVM hypervisor tests during the second phase of CI execution—triggered after the /lgtm label is applied—when comprehensive validation runs are performed.

## Implementation Phases

1. Refactor KubeVirt to introduce the above interfaces for the Hypervisor extension points. Implement the interface for KVM only, such that KubeVirt continues to be able to create and manage KVM-based VMs.

2. Add the `HypervisorConfiguration` CR to KubeVirt API and the `hypervisor` field to the `KubevirtConfiguration` CR. Add the `ConfigurableHypervisor` feature gate.

3. For each interface, add the MSHV implementation that is gated by the `ConfigurableHypervisor` feature gate. Test the MSHV implementation of interfaces using unit tests, and basic VM lifecycle management testing using functional tests.

4. Expand functional tests for MSHV to cover all supported features. Integrate testing lanes for MSHV platform into KubeVirt CI/CD infrastructure and ensure that the MSHV implementation is regularly tested.


## Graduation Requirements

### Alpha

- Feature gate protects the configurable hypervisor functionality.
- Validation webhook for KubeVirt CR enforce that hypervisor configuration contains at most 1 entry, thereby enforcing only 1 supported hypervisor in the cluster.
- Update of the `HypervisorConfiguration` value in KubeVirt CR after a KubeVirt deployment is running will not result in any change to the deployment. This setting is only to be used at the deployment time.
- Cluster-wide hypervisor configuration implemented and consumed by the different KubeVirt components - admission webhooks, virt-handler and converter.
- Basic functional tests for VMI scheduling, domain creation and lifecycle should be added for MSHV hypervisor.
- All existing test lanes that run test cases against KVM hypervisor should pass, ensuring backwards compatibility.

### Beta

- Basic VM lifecycle should be working correctly on the MSHV hypervisor.
- We should have an explicit list of KubeVirt features that are working correctly with the MSHV hypervisor, and a list of features that are not. The feature support on MSHV should be well documented as well as registered in the Feature Discovery mechanism proposed by the VEP [VEP 97.1 hypervisor feature discovery](https://github.com/kubevirt/enhancements/pull/122).
- KubeVirt should support multiple hypervisors in the same cluster. A VMI-level hypervisor field should be introduced to let the user choose which hypervisor to use for creating the VMI.
- The KubeVirt CI/CD infrastructure should already be running test lanes against the MSHV hypervisor and tests should be passing.
- Upgrade and rollback testing should be successfully executed in CI.


### GA

- Clear documentation should exists that describes the architecture of multi-hypervisor support in KubeVirt and walks through the contributor workflow for adding support for a new hypervisor.
- The API change introduced to KubeVirt for multi-hypervisor support would be compatible with an out-of-tree model. Even if multi-hypervisor support is released as GA with the in-tree model, transitioning to an out-of-tree (aka plugin) model should not require further API changes.