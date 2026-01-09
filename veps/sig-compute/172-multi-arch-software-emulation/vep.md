# VEP #172: Multi-Architecture Software Emulation

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

This VEP proposes introducing a `MultiArchitectureSoftwareEmulation` feature gate to enable KubeVirt to run virtual machines using software emulation for guest architectures that differ from the host architecture. Currently, KubeVirt's `useEmulation` configuration only supports software emulation as a fallback for guests running the same architecture as the host when `/dev/kvm` is unavailable. This enhancement would extend that capability to allow cross-architecture emulation using QEMU's TCG (Tiny Code Generator) emulator.

## Motivation

Cloud providers and development teams often need to run workloads on different architectures for testing, CI/CD pipelines, or supporting heterogeneous environments. Currently, KubeVirt requires matching architectures between the host and guest, limiting its flexibility in multi-architecture scenarios.

Cross-architecture emulation would enable:

- Development and testing of ARM64 workloads on AMD64 infrastructure (and vice versa)
- Migration scenarios where temporary cross-arch execution is needed
- Cost optimization by using available hardware regardless of architecture
- Enhanced CI/CD capabilities for multi-architecture container images

## Goals

- Enable VMs to run on nodes with different architectures using QEMU software emulation
- Leverage existing `useEmulation` configuration infrastructure
- Ensure proper architecture detection and QEMU binary selection
- Maintain backward compatibility with existing emulation behavior

## Non Goals

- Hardware-accelerated cross-architecture emulation
- Performance optimization of software emulation (expected to be significantly slower)
- Supporting all possible architecture combinations (initial focus on amd64, arm64, s390x)
- Migration of running VMs across architectures
- Automatic installation or management of QEMU binaries on nodes

## Definition of Users

This feature is intended for:

- Developers requiring multi-architecture testing environments
- CI/CD systems building and testing multi-architecture workloads
- Organizations with heterogeneous cluster architectures
- Users performing architecture transition planning

## User Stories

1. As a developer, I want to test my ARM64 VM image on an AMD64 development cluster without requiring dedicated ARM64 hardware. I would like my ARM-specific VM.yaml (with `spec.architecture: arm64`) to schedule on AMD64 workers and run using software emulation.

2. As a CI/CD engineer, I want to run automated tests for multiple architectures on a single Kubernetes cluster to simplify infrastructure.

3. As a platform engineer, I want to ensure that cross-architecture VMs are only scheduled when the required QEMU binaries are available on the nodes.

## Repos

- kubevirt/kubevirt
- kubevirt/enhancements

## Design

### Current Emulation Behavior

Currently, KubeVirt's emulation support is implemented in the `HypervisorDomainConfigurator`:

[pkg/virt-launcher/virtwrap/converter/compute/hypervisor.go#L45-L57](https://github.com/kubevirt/kubevirt/blob/0ce3444ae73e94527b79d1ab30379007767c57e4/pkg/virt-launcher/virtwrap/converter/compute/hypervisor.go#L45-L57)

```go
func (h HypervisorDomainConfigurator) Configure(vmi *v1.VirtualMachineInstance, domain *api.Domain) error {
    if !h.kvmAvailable {
        if h.allowEmulation {
            logger := log.DefaultLogger()
            logger.Infof("kvm not present. Using software emulation.")
            domain.Spec.Type = "qemu"
        } else {
            return fmt.Errorf("kvm not present")
        }
    }
    return nil
}
```

This only handles same-architecture emulation when `/dev/kvm` is unavailable. The domain type is set to "qemu" which uses TCG emulation, but the QEMU binary is always the host architecture's binary (implicitly `/usr/bin/qemu-system-<host-arch>`).

### Architecture Detection

The VMI spec already includes an `Architecture` field (v1.VirtualMachineInstance.Spec.Architecture) which specifies the desired guest architecture. The implementation would:

1. Detect the host architecture using `runtime.GOARCH` (already available in the converter context via `c.Architecture.GetArchitecture()`)
2. Read the desired guest architecture from `vmi.Spec.Architecture`
3. Compare the two architectures
4. When they differ AND the `MultiArchitectureSoftwareEmulation` feature gate is enabled AND `useEmulation: true`, configure QEMU for cross-architecture emulation

### Implementation Changes

#### 1. Hypervisor Configurator Enhancement

The `HypervisorDomainConfigurator` would be extended to:

```go
type HypervisorDomainConfigurator struct {
    allowEmulation                bool
    kvmAvailable                  bool
    allowCrossArchEmulation       bool  // NEW: feature gate state
    hostArchitecture              string // NEW: host architecture
}

func (h HypervisorDomainConfigurator) Configure(vmi *v1.VirtualMachineInstance, domain *api.Domain) error {
    guestArch := vmi.Spec.Architecture
    crossArchEmulation := guestArch != h.hostArchitecture

    if !h.kvmAvailable || crossArchEmulation {
        if !h.allowEmulation {
            return fmt.Errorf("kvm not present or cross-arch requested, but emulation not allowed")
        }

        if crossArchEmulation && !h.allowCrossArchEmulation {
            return fmt.Errorf("cross-architecture emulation requires MultiArchitectureSoftwareEmulation feature gate")
        }

        logger := log.DefaultLogger()
        if crossArchEmulation {
            logger.Infof("Cross-architecture emulation: host=%s, guest=%s. Using software emulation.",
                h.hostArchitecture, guestArch)
        } else {
            logger.Infof("kvm not present. Using software emulation.")
        }

        domain.Spec.Type = "qemu"

        // Set explicit emulator path for cross-arch scenarios
        if crossArchEmulation {
            domain.Spec.Devices.Emulator = getEmulatorPath(guestArch)
        }
    }

    return nil
}

func getEmulatorPath(arch string) string {
    // Maps guest architecture to QEMU binary
    switch arch {
    case "arm64", "aarch64":
        return "/usr/bin/qemu-system-aarch64"
    case "amd64", "x86_64":
        return "/usr/bin/qemu-system-x86_64"
    case "s390x":
        return "/usr/bin/qemu-system-s390x"
    default:
        return ""  // Empty means use default for domain type
    }
}
```

#### 2. Domain XML Output

When cross-architecture emulation is configured, the resulting domain XML would include:

```xml
<domain type='qemu'>
  <name>test-vm</name>
  <devices>
    <emulator>/usr/bin/qemu-system-aarch64</emulator>
    <!-- other devices -->
  </devices>
  <!-- Note: TCG is the default acceleration when KVM is unavailable -->
</domain>
```

For same-architecture emulation (existing behavior):

```xml
<domain type='qemu'>
  <name>test-vm</name>
  <!-- emulator path omitted, uses default for host architecture -->
</domain>
```

### Feature Gate Configuration

The feature gate behavior is as follows:

| Feature Gate Enabled | useEmulation | Host Arch | Guest Arch | Result |
|---------------------|--------------|-----------|------------|--------|
| No | true | amd64 | amd64 | Same-arch emulation (existing behavior) |
| No | true | amd64 | arm64 | **ERROR** - cross-arch not allowed |
| Yes | false | amd64 | arm64 | **ERROR** - emulation disabled |
| Yes | true | amd64 | arm64 | Cross-arch emulation via TCG |
| Yes | true | amd64 | amd64 | Same-arch emulation (if KVM unavailable) |

**Key Points**:

- Both `useEmulation: true` AND the feature gate must be enabled for cross-arch emulation
- The feature gate alone does NOT enable emulation; it only permits cross-arch scenarios
- When the feature gate is disabled, attempting to schedule a cross-arch VM returns an error at domain conversion time

### QEMU Binary Requirements

#### Node Prerequisites

Nodes that will run cross-architecture VMs must have the appropriate QEMU system binaries installed:

- For ARM64 guests: `/usr/bin/qemu-system-aarch64`
- For AMD64 guests: `/usr/bin/qemu-system-x86_64`
- For s390x guests: `/usr/bin/qemu-system-s390x`

These binaries are typically provided by distro packages:

- **RHEL/CentOS/Fedora**: `qemu-system-aarch64`, `qemu-system-x86`, `qemu-system-s390x`
- **Debian/Ubuntu**: `qemu-system-arm`, `qemu-system-x86`, `qemu-system-s390x`

#### Container Images

The virt-launcher container image must include the necessary QEMU binaries. This requires:

1. **Multi-architecture virt-launcher builds**: The image must be built with cross-arch QEMU binaries
2. **Image size considerations**: Adding multiple QEMU system binaries will increase image size significantly (100-200MB per architecture)

**Implementation approach**:

- Initial Alpha: Document manual installation of QEMU packages on nodes (using MachineConfig on OpenShift, DaemonSets on vanilla K8s)
- Beta: Explore including common arch binaries in virt-launcher image
- Future: Potentially split into architecture-specific sidecars or initContainers

### Validation

#### Domain Conversion Time

Validation occurs in the converter when creating the domain specification:

1. **Feature Gate Check** (hypervisor.go): If guest arch ≠ host arch and feature gate is disabled, return error
2. **Emulator Binary Check** (NEW): Before setting the emulator path, verify the binary exists:

```go
func validateEmulatorBinary(path string) error {
    if path == "" {
        return nil  // Using default, skip validation
    }
    if _, err := os.Stat(path); err != nil {
        return fmt.Errorf("required emulator binary not found: %s", path)
    }
    return nil
}
```

1. **Architecture Compatibility**: Validate that the machine type is compatible with the target guest architecture (handled by existing machine type validation)

#### User Experience

When validation fails:

- **Feature gate disabled**: VMI events show: `"Cross-architecture emulation not enabled. Enable MultiArchitectureSoftwareEmulation feature gate and useEmulation configuration."`
- **Binary missing**: VMI fails with: `"Required emulator binary /usr/bin/qemu-system-aarch64 not found on node"`
- **Emulation disabled**: VMI fails with: `"kvm not present or cross-arch requested, but emulation not allowed"`

### Machine Type Handling

The VMI can specify a machine type explicitly via `spec.domain.machine.type`. For cross-architecture emulation:

- **Explicit machine type**: User must specify an architecture-appropriate type (e.g., `virt` for ARM64, `q35` for AMD64)
- **Auto-detection**: If not specified, KubeVirt will use the architecture-specific default from the arch converter:
  - AMD64: `q35` (default from converterAMD64)
  - ARM64: `virt` (default from converterARM64)
  - s390x: `s390-ccw-virtio` (default from converterS390X)

The architecture converter (pkg/virt-launcher/virtwrap/converter/arch/) is instantiated based on the **guest** architecture (from `vmi.Spec.Architecture`), not the host architecture, ensuring correct defaults.

### Relationship to Deprecated MultiArchitecture Feature

The deprecated `MultiArchitecture` feature gate (v1.8.0) served a different purpose:

- **Old feature**: Allowed VMIs to **schedule** to nodes matching `spec.architecture` (using node labels/selectors)
- **This feature**: Allows VMIs to **run** on nodes with non-matching architecture via emulation

These are complementary concerns:

- Without old feature: All VMs schedule based on standard K8s scheduling
- With this feature: VMs can run even when scheduled to "wrong" architecture (with performance penalty)

## API Examples

### Enable Cluster-Wide Cross-Architecture Emulation

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      useEmulation: true
      featureGates:
      - MultiArchitectureSoftwareEmulation
```

### Create an ARM64 VMI (runs on any architecture when feature enabled)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: arm64-vmi
spec:
  architecture: arm64  # Specifies guest architecture
  domain:
    machine:
      type: virt  # ARM64-appropriate machine type
    devices:
      disks:
      - name: containerdisk
        disk:
          bus: virtio
    resources:
      requests:
        memory: 1Gi
  volumes:
  - name: containerdisk
    containerDisk:
      # Important: Use architecture-specific image
      image: quay.io/containerdisks/fedora:40-aarch64
```

**Note on ContainerDisk Images**: The user must specify architecture-appropriate container images. KubeVirt does not automatically select multi-arch images. For ARM64 VMs, use ARM64 container disk images (e.g., `:*-aarch64` tags).

### Query VMI Status for Emulation Information

```yaml
status:
  conditions:
  - type: Ready
    status: "True"
  # New condition indicating emulation mode
  - type: Emulated
    status: "True"
    reason: "CrossArchitectureEmulation"
    message: "VM running with software emulation (host=amd64, guest=arm64)"
```

## Alternatives

1. **Require matching architectures** - Current behavior, limits flexibility
2. **Use separate arch-specific clusters** - Increases infrastructure complexity and cost
3. **Use nested virtualization with multi-arch VMs** - Complex, poor performance
4. **Wait for hardware multi-arch support** - Not available on most platforms

## Scalability

### Performance Implications

- **Emulation overhead**: Software emulation is significantly slower than native execution (typically 10-100x slower depending on workload)
- **Resource consumption**: Higher CPU usage on the host due to emulation overhead
- **Not recommended for production workloads** or performance-sensitive applications

### Use Case Suitability

| Use Case | Suitable? | Notes |
|----------|-----------|-------|
| Development/Testing | Yes | Primary use case |
| CI/CD pipelines | Yes | Acceptable for build/test |
| Low-throughput apps | Maybe | Depends on tolerance |
| Production workloads | No | Use native architecture |
| Real-time systems | No | Emulation adds latency |

The feature is designed for development, testing, and low-throughput scenarios rather than production scale.

## Update/Rollback Compatibility

### Update Scenarios

- **Enabling feature gate**: Existing VMs on matching architectures are unaffected; new cross-arch VMs can be created
- **Disabling feature gate**: Existing running cross-arch VMs continue running; new cross-arch VMs fail to start
- **Changing useEmulation**: Affects VM creation but not running VMs

### Rollback

- Safe to disable feature gate at any time
- Cross-arch VMs running when gate is disabled will continue until stopped
- No API changes required beyond the feature gate
- No persistent state changes

### Upgrade Path

- Alpha → Beta: No breaking changes expected
- Beta → GA: No breaking changes expected
- QEMU binary requirements may change; document in release notes

## Functional Testing Approach

### Unit Tests

1. **Architecture detection**: Test correct identification of host vs guest architecture mismatch
2. **QEMU binary selection**: Test `getEmulatorPath()` for all supported architectures
3. **Feature gate logic**: Test error conditions when gate is disabled
4. **Emulator validation**: Test binary existence checks

### Integration Tests

1. **Cross-architecture domain conversion**: Create VMI with arm64 spec on amd64 test environment, verify domain XML includes correct emulator path
2. **Feature gate enforcement**: Verify cross-arch VMI fails when gate disabled
3. **Same-arch fallback**: Verify existing same-arch emulation still works

### E2E Tests

1. **Cross-arch VM lifecycle** (requires multi-arch test cluster):
   - Create ARM64 VM on AMD64 host when feature gate enabled
   - Verify QEMU process uses `qemu-system-aarch64` binary
   - Verify domain XML contains correct emulator path
   - Test VM lifecycle (start, stop, delete)
   - Verify VM actually runs (guest agent ping, basic commands)

2. **Rejection scenarios**:
   - Verify cross-arch VMI rejected when feature gate disabled
   - Verify error when QEMU binary missing
   - Verify error when emulation disabled but cross-arch requested

3. **Performance baseline** (informational):
   - Measure boot time for native vs emulated
   - Document expected performance characteristics

### Test Infrastructure Requirements

- Nodes with cross-arch QEMU binaries installed
- Multi-arch container disk images for testing
- Ability to toggle feature gate in test environment

## Implementation Phases

The implementation will follow a phased approach aligned with the graduation criteria:

### Phase 1: Core Infrastructure (Alpha)

1. Extend `HypervisorDomainConfigurator` with cross-arch detection
2. Implement QEMU binary path selection
3. Add feature gate `MultiArchitectureSoftwareEmulation`
4. Basic validation (binary existence checks)

### Phase 2: AMD64 ↔ ARM64 Support (Alpha)

1. Support AMD64 hosts running ARM64 guests
2. Support ARM64 hosts running AMD64 guests
3. Documentation for manual QEMU binary installation

### Phase 3: s390x Support (Beta)

1. Add s390x guest support on AMD64/ARM64 hosts
2. Add s390x host support for other guest architectures
3. Comprehensive test matrix for all combinations

### Phase 4: Productization (Beta → GA)

1. Performance benchmarking and documentation
2. Production readiness review
3. Explore automated QEMU binary distribution

## Implementation History

- 2025-11-17: Initial VEP proposed
- 2026-01-09: VEP expanded with detailed design and moved to sig-compute

## Graduation Requirements

### Alpha

- [ ] Feature gate guards all code changes
- [ ] Support for AMD64 hosts running ARM64 guests
- [ ] Support for ARM64 hosts running AMD64 guests
- [ ] Basic functional tests (unit + integration)
- [ ] Documentation of performance limitations and use cases
- [ ] Validation that required QEMU binaries exist
- [ ] Documentation on manual QEMU binary installation for nodes

### Beta

- [ ] Support for s390x in cross-architecture scenarios (all combinations)
- [ ] Comprehensive test coverage across architecture pairs (E2E tests)
- [ ] Performance benchmarking and documented performance characteristics
- [ ] At least one release in Alpha with no major issues
- [ ] User feedback incorporated from Alpha release
- [ ] Consider automated QEMU binary distribution approach

### GA

- [ ] Multiple releases in Beta with no major issues
- [ ] Clear documentation on supported architecture combinations
- [ ] Established best practices and use case guidance
- [ ] Production readiness review (acknowledging performance limitations)
- [ ] Decision on QEMU binary distribution strategy (manual vs bundled vs sidecar)
