# VEP #97.1: Hypervisor Abstraction - Capability Registry for Hypervisor Feature Discovery

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Introduce a centralized capability registry that provides a declarative matrix for expressing feature constraints across hypervisor and architecture combinations. The registry enables validation, test filtering, and clear error messages without duplicating logic across components. 

**Note:** This VEP was extracted from VEP 97 (Hypervisor Abstraction Layer) to allow independent discussion and implementation of the capability registry design. The registry serves as the feature discovery mechanism for the broader hypervisor abstraction framework described in VEP 97. 

## Motivation

As KubeVirt expands to support multiple hypervisors (KVM and MSHV) on multiple architectures, not all features have equal support on different platforms. Some features are architecture-specific (e.g., AMD SEV workload encryption), while others may be specific to a certain hypervisor, while still others untested or have known compatibility issues on certain architectures. Currently, there is no systematic way to track and communicate hypervisor/architecture-specific feature support.

- There is no mechanism for the selection of functional tests based on the capabilities supported by the platform, based on hypervisor /architecture combination.
- Error messages for unsupported features lack context, documentation links, and version information.
- Adding a new hypervisor requires updating the validation logic for multiple VMI features.
- No clear mechanism to declare when features need explicit validation or are unsupported without creating scattered conditional code.
- Users are unsure whether a certain Feature Gate is supported on a particular hypervisor or architecture. The webhooks for validating feature gates changes only check for deprecation and not functionality support.

## Goals

- Provide a mechanism to declare when features/capabilities are fully supported, experimental or unsupported on specific platforms. It should be possible to declare feature support  for a certain hypervisor or architecture or their combination.

- Enable automatic validation by querying the capability registry instead of scattered conditional logic. The registry-based validation should integrate easily into the validation webhooks logic to validate VMI specs on the target platform.

- Enable test filtering so that the functional tests run on a given platform only test functionality that is supported on that platform.

- Generate rich error messages with explanations, documentation links, and version information when features are unsupported.


## Non Goals

- The registry is not meant to serve as a catalog of features offered by KubeVirt. Only those features that need explicit control (e.g., validation or are unsupported) are added to the registry. 

- It should not be required to register every feature as a capability (unregistered features should be implicitly allowed and handled by normal code paths).

## Definition of Users

- Upstream contributors adding new features or hypervisor support.
- Platform engineers integrating proprietary hypervisor stacks.
- Test infrastructure maintainers ensuring tests run only where features are supported.
- End users receiving clear error messages when requesting unsupported features.

## User Stories

1. As a contributor adding a new hypervisor, I can declare which features need validation or should be blocked, and have validation work automatically without scattered conditional logic.
2. As a test maintainer, I can decorate tests with capability requirements for features that need platform-specific validation. When testing on a particular platform, I can use label filters to run only those tests which invoke functionality  that is supported on that platform.
3. As an end user, I receive clear error messages with documentation links when I request features that are explicitly unsupported on my platform.
4. As a platform engineer, I can declare constraints (e.g., "VGA unsupported on ARM64") once and have validation, test filtering, and error messages handled automatically.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (this VEP)

## Design

### Core Types

```go
type CapabilityKey string // e.g., "graphics.vga", "firmware.secureboot.uefi"
type SupportLevel int

const (
	Unregistered SupportLevel = iota // Not registered (default zero value)
	Unsupported                      // Explicitly blocked on this platform
	Experimental                     // Requires feature gate
	Deprecated                       // Supported but discouraged
)

type Capability struct {
	// function to check if this capability is required by a given VMI
	IsRequiredBy func(vmi *v1.VirtualMachineInstanceSpec) bool
}

// struct to store the extent to which a given capability is supported
type CapabilitySupport struct {
	Level   SupportLevel
	Message string // User-facing explanation
	GatedBy string // Optional: feature gate name
}
```

### Capability Definitions

All capabilities are defined once as typed constants in `pkg/capabilities/definitions.go`:

```go
// Capability constants - each represents a feature that may need validation or blocking
const (
	CapVsock        CapabilityKey = "domain.devices.vsock"
	CapPanicDevices CapabilityKey = "domain.devices.panicDevices"
	// ... all capabilities declared as constants
)

// Define CapVsock capability
var CapVsockDef = Capability{
	IsRequiredBy: func(vmiSpec *v1.VirtualMachineInstanceSpec) bool {
		return vmiSpec.Domain.Devices.AutoattachVSOCK != nil && *vmiSpec.Domain.Devices.AutoattachVSOCK
	},
}

// Define PanicDevices capability
var CapPanicDevicesDef = Capability{
	IsRequiredBy: func(vmiSpec *v1.VirtualMachineInstanceSpec) bool {
		return len(vmiSpec.Domain.Devices.PanicDevices) > 0
	},
}

// Define a struct to hold a map from capability keys to their definitions
var CapabilityDefinitions = map[CapabilityKey]Capability{
	CapVsock:        CapVsockDef,
	CapPanicDevices: CapPanicDevicesDef,
	// Add other capabilities here as they are defined
}
```

### Registration Pattern

Capabilities are fully defined when hypervisors register their constraints via a builder pattern. The `Capability` struct populated by the builder contains all necessary metadata (`Level`, `Message`, `GatedBy`).

Hypervisor+architecture combinations register their constraints using a fluent builder API:

```go
// pkg/capabilities/registry.go - Matrix registration with builder pattern
type Capabilities struct {
    matrix map[string]map[CapabilityKey]CapabilitySupport // "hypervisor/arch" or "hypervisor" -> capabilities
}

type CapabilitiesBuilder struct {
    matrix      *Capabilities
    platformKey string  // Composite key: "hypervisor/arch" or "hypervisor"
}

type CapabilityBuilder struct {
  builder *CapabilitiesBuilder
  cap     CapabilityKey
}

func Register(hypervisor, arch string) *CapabilitiesBuilder {
    // Returns builder for registering capabilities
}

// Fluent methods for registering capability constraints
func (b *CapabilitiesBuilder) Experimental(cap CapabilityKey, gate string) *CapabilitiesBuilder {
    // Mark capability as experimental with feature gate
}

func (b *CapabilitiesBuilder) Unsupported(caps ...CapabilityKey) *CapabilitiesBuilder {
    // Explicitly mark as unsupported (for clarity/documentation)
}

// When richer metadata would otherwise repeat the capability key, `Cap(...)` returns a
// capability-specific builder that keeps the fluent chain focused on a single key.
func (b *CapabilitiesBuilder) Cap(cap CapabilityKey) *CapabilityBuilder {
  // Start capability-specific fluent builder to avoid repeating the key
}

func (e *CapabilityBuilder) Experimental(gate string) *CapabilityBuilder {
  // Mark capability as experimental with feature gate and continue chaining
}

func (e *CapabilityBuilder) Unsupported() *CapabilityBuilder {
  // Explicitly mark capability as unsupported and continue chaining metadata helpers
}

func (e *CapabilityBuilder) WithMessage(msg string) *CapabilityBuilder {
  // Attach custom message while staying on the capability-specific builder
}

func (e *CapabilityBuilder) WithDocLink(link string) *CapabilityBuilder {
  // Attach documentation link without repeating the capability key
}

func (e *CapabilityBuilder) WithSince(version string) *CapabilityBuilder {
  // Record the release where constraint was registered without repeating the key
}

func (e *CapabilityBuilder) Done() *CapabilitiesBuilder {
  // Return to the parent builder after finishing metadata customization
}
```

### When to Register Capabilities

The capability registry is **opt-in for validation**. Only register capabilities when you need to:

1. **Block a feature on specific platforms**: Register as `Unsupported` to generate validation errors
   - Example: VGA graphics on ARM64 architectures
   - Example: Certain firmware features on proprietary hypervisors

2. **Mark a feature as experimental**: Register as `Experimental` with a feature gate requirement
   - Example: CPU hotplug while still under development
   - Example: New network multiqueue features

3. **Mark a feature as deprecated**: Register as `Deprecated` to warn users
   - Feature still works but users should migrate away
   - Helps communicate deprecation timeline

4. **Enable test filtering**: Register to control which tests run on which platforms
   - Only those tests run which are supported or experimental on the target platform
   - Reduces noise in CI for platform-specific features

**What not to register**: Features where support is unknown or still being discovered. If a feature hasn't shown platform-specific issues and doesn't require explicit blocking, leave it unregistered. Let natural test failures and runtime errors reveal compatibility issues, then register capabilities as constraints are discovered. This keeps the registry focused on known validation needs rather than attempting to preemptively catalog all features.

### Registration Examples

Here are examples showing how hypervisors register capability constraints:

```go
// Usage in init() - hypervisor implementations register support:
func init() {
    // Register base KVM constraints (applies to all KVM archs unless overridden)
    Register("kvm", "").  // empty arch = hypervisor-wide default
      Experimental(CapCPUHotplug)
    
    // Architecture-specific constraints for KVM
    Register("kvm", "arm64").
      Cap(CapGraphicsVGA).
        Unsupported().
        WithMessage("VGA graphics not supported on ARM64 architecture").
        WithDocLink("https://kubevirt.io/user-guide/graphics#compatibility").
        Done()
    
    // VGA is unregistered on amd64/s390x - implicitly allowed
}
```

### Resolution Strategy

The registry resolves capabilities using a layered fallback strategy, checking from most specific to least specific:

```go
// Query with automatic fallback: hypervisor/arch -> hypervisor -> base
func Get(hypervisor, arch string, cap CapabilityKey) Capability {
    // Try exact match: kvm/amd64
    if c, ok := lookup(hypervisor+"/"+arch, cap); ok { return c }
    
    // Try hypervisor-wide: kvm
    if c, ok := lookup(hypervisor, cap); ok { return c }
    
    // Not registered - return zero value (caller checks cap.Level)
    return Capability{}
}
```

Resolution precedence (most specific wins):
1. **Hypervisor+Architecture** (e.g., `kvm/amd64`) - most specific
2. **Hypervisor-wide** (e.g., `kvm`) - applies to all architectures unless overridden
3. **Not registered** - returns zero-value `Capability{}` (where `Level` is 0, which equals `Unregistered`)


### Integration Points

#### Validation Webhooks

The validating webhook integrates with the capability registry by:

1. Mapping VMI spec features to capability keys (e.g., `video.type: vga` → `graphics.vga`)
2. Querying the registry for each capability
3. Applying validation logic based on the returned `Capability` data (support level, message, doc links)

The registry provides the data; consumers apply their own validation policy. This pattern replaces scattered validation files (`arm64.go`, `s390x.go`) with centralized data queries.


#### Test Filtering

Tests use capability decorators to filter which tests to run on a given platform:

```go
// tests/decorators/capabilities.go
var RequiresVGAGraphics = decorators.RequiresCapability(capabilities.CapGraphicsVGA)

It("should support VGA graphics", RequiresVGAGraphics, func() {
    vmi := libvmifact.NewCirros()
    vmi.Spec.Domain.Devices.Video = &v1.Video{Type: "vga"}
})
```

Behavior: The test filtering mechanism should take as input the target hypervisor and architecture pair and return the set of capability-based test labels that should be used for  filtering tests for running. All tests requiring functionality that is `Unsupported` on the target platform would be marked as negative.

Example: VGA tests run on KVM/amd64 (unregistered, implicitly allowed) but skip on KVM/arm64 (registered as unsupported).

For determining the names of the unsupported capabilities on the target platform, we introduce a binary under `cmd/` named `kubevirt-capability-check`, which has the following usage:

```bash
$ ./_out/cmd/kubevirt-capability-check/kubevirt-capability-check --help
Usage of ./_out/cmd/kubevirt-capability-check/kubevirt-capability-check:
      --arch string            Target architecture (e.g., 'amd64', 'arm64', 's390x')
      --hypervisor string      Target hypervisor (e.g., 'kvm', 'mshv')
      --list-all               List all capabilities regardless of support level
      --output string          Output format: 'keys' (capability keys only), 'detailed' (keys with messages), 'json' (default "keys")
      --support-level string   Support level to filter by (Unsupported, Experimental, Deprecated, Unregistered) (default "Unsupported")
  -v, --v int                  log level for V logs (default 2)
```

This binary queries the same capability registry as the validation logic does.

#### Node Labeling

The node labeller can optionally expose capability labels on nodes for scheduling and audit purposes:

```go
// Example node labels generated from capability registry (only for registered capabilities)
kubevirt.io/capability.graphics.vga: "unsupported"  // Only on arm64
kubevirt.io/capability.cpu.hotplug: "experimental"
```

This allows users and operators to query node capabilities without inspecting the hypervisor configuration.

### Benefits

- **Opt-in validation**: Only register when validation/blocking is needed—unregistered capabilities allowed by default
- **Centralized constraints**: Single source of truth replaces scattered conditional logic across `arm64.go`, `s390x.go`, etc.
- **Natural inheritance**: Hypervisor-wide constraints with architecture-specific overrides
- **Type-safe**: Constants prevent typos, enable IDE autocomplete
- **Rich error messages**: Automatic user-friendly messages with doc links
- **Automatic test filtering**: Filter out tests which invoke explicitly unsupported capabilities

## API Examples

### Querying Capabilities to Validate VMI spec

```go
// Retrieve the capability support information for the given hypervisor and architecture
supports := capabilities.GetAll(hypervisor, arch)

// Validate the capabilities in the spec against the supported capabilities
for capKey, capSupport := range supports {
  capabilityDef := capabilities.CapabilityDefinitions[capKey]

  // Only trigger validation logic if the capability is used by the VMI
  if capabilityDef.IsRequiredBy(vmiSpec) {
    switch capSupport.Level {
    case capabilities.Unsupported:
      // Unsupported capabilities are straightaway rejected
      causes = append(causes, metav1.StatusCause{
        Type:    metav1.CauseTypeFieldValueNotSupported,
        Message: capSupport.Message,
        Field:   field.String(),
      })
    case capabilities.Experimental:
      // For Experimental capabilities,
      // check if the corresponding feature gate is enabled
      if capSupport.GatedBy != "" && !config.IsFeatureGateEnabled(capSupport.GatedBy) {
        causes = append(causes, metav1.StatusCause{
          Type:    metav1.CauseTypeFieldValueNotSupported,
          Message: capSupport.Message + fmt.Sprintf(". But feature gate '%s' is not enabled", capSupport.GatedBy),
          Field:   field.String(),
        })
      }
    }
  }
}
```

### Error Message Example

```
VGA graphics not supported on this platform. See: https://kubevirt.io/user-guide/graphics#compatibility
```

### Filtering tests for running on a given platform

Run the `kubevirt-capability-check` binary to compute which capabilities are unsupported on the target platform so that those tests that require those unsupported capabilities are not run.

```bash
$ unsupportedCaps=$()./_out/cmd/kubevirt-capability-check/kubevirt-capability-check \
    --arch s390x \
    --hypervisor kvm)
$ echo $unsupportedCaps
domain.devices.panicDevices
$ set +H
$ for cap in $unsupportedCaps; do
    labelFilter="${labelFilter}&&(!requires-capability-${cap})"
    done
$ echo $labelFilter
&&(!requires-capability-domain.devices.panicDevices)
$ set -H

```

## Undesired alternatives

1. **Status quo** – Continue scattered architecture-specific validation files. This increases maintenance burden and makes adding hypervisors difficult.
2. **Per-hypervisor capability files** – Create `pkg/capabilities/hypervisor/kvm/amd64.go` etc. Rejected due to file sprawl and duplication.
3. **Runtime-only discovery** – Query libvirt capabilities at runtime instead of static registry. Rejected because it requires running nodes and can't be used for validation or test planning.

## Scalability

- Capability lookups are constant-time map operations.
- Registry is populated once during init, no runtime registration overhead.
- Memory footprint scales linearly with number of hypervisor+arch+capability combinations (expected to be small).

## Update/Rollback Compatibility

- The capability registry is additive—coexists with existing validation logic during migration.
- Rolling back removes capability-based validation but doesn't break existing functionality.

## Functional Testing Approach

- Unit tests for capability registry ensuring correct lookup precedence and fallback behavior.
- Integration tests verifying validation webhooks reject unsupported features with correct error messages.
- Integration tests confirming test decorators correctly skip tests on unsupported platforms.
- Minimal end-to-end tests covering a happy path and a negative path to demonstrate the wiring; the bulk of the coverage lives in unit and integration tests that exercise resolution and webhook behavior.

## Implementation History

- 2025-Oct-7: Initial VEP #95 draft.
- 2025-Oct-31: Initial drafet of VEP #95.1 (Extracted feature discovery from VEP #95).

## Graduation Requirements

### Alpha

- Core capability registry implemented with types and registration API.
- Known constraints registered for KVM (VGA unsupported on arm64, experimental features with gates).
- Basic validation webhook integration (unregistered capabilities allowed by default).
- Test decorator framework for capability-based filtering.
- Unit tests

### Beta

- All architecture-specific validation files replaced with capability queries.
- Test filtering integrated into CI pipelines.
- Documentation for when and how to register capabilities.
- Clear contributor guide distinguishing validation capabilities from exhaustive feature cataloging.

### GA

- Capability registry covers known validation and blocking scenarios across hypervisors.
- Multiple hypervisor implementations registered (KVM + at least one alternative).
- Node labeling optional feature for capability discovery.
- Comprehensive documentation emphasizing opt-in validation model.
