# VEP #172: Multi-Architecture Software Emulation

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

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
- Operate independently of `useEmulation`, allowing native KVM VMs and
  cross-arch emulated VMs to coexist
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

Since v1.8.0, KubeVirt's hypervisor support has been refactored into a
multi-hypervisor abstraction layer. Per-hypervisor domain configurators are
located under `pkg/virt-launcher/virtwrap/converter/` and are instantiated
inline within `Convert_v1_VirtualMachineInstance_To_api_Domain()` in
`pkg/virt-launcher/virtwrap/converter/converter.go`.

**KVM backend** (`pkg/virt-launcher/virtwrap/converter/kvm/configurator.go`):

```go
func (k KvmDomainConfigurator) Configure(vmi *v1.VirtualMachineInstance, domain *api.Domain) error {
    if !k.kvmAvailable {
        if k.allowEmulation {
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

This only handles same-architecture emulation when `/dev/kvm` is unavailable.
The domain type is set to "qemu" which uses TCG emulation, but the QEMU
binary is always the host architecture's binary (implicitly
`/usr/bin/qemu-system-<host-arch>`).

### Architecture Detection

The VMI spec already includes an `Architecture` field
(`v1.VirtualMachineInstance.Spec.Architecture`) which specifies the desired
guest architecture. The converter context provides the host architecture via
`c.Architecture.GetArchitecture()`, which is derived from `runtime.GOARCH`.

However, the current codebase has a fundamental limitation: the architecture
converter (`arch.Converter`) is **always instantiated for the host
architecture** via `arch.NewConverter(runtime.GOARCH)` in `manager.go`. This
means all 23+ domain configurators in the builder chain receive host
architecture behavior, not guest architecture behavior. This affects machine
type defaults, device selection, memory overhead calculations, CPU features,
and more.

The implementation must therefore:

1. Detect the host architecture using `runtime.GOARCH`
2. Read the desired guest architecture from `vmi.Spec.Architecture`
3. Compare the two architectures
4. When they differ AND the `MultiArchitectureSoftwareEmulation` feature gate
   is enabled:
   - Instantiate the `arch.Converter` for the **guest** architecture instead
     of the host architecture
   - Configure QEMU for cross-architecture emulation in the domain
     configurator
   - Pass the guest architecture to memory overhead calculations (which
     currently hardcode `runtime.GOARCH`)

### Implementation Changes

#### 1. KVM Domain Configurator Enhancement

The cross-architecture emulation logic is added to `KvmDomainConfigurator`.

**`pkg/virt-launcher/virtwrap/converter/kvm/configurator.go`**:

```go
type KvmDomainConfigurator struct {
    allowEmulation                bool
    kvmAvailable                  bool
    allowCrossArchEmulation       bool   // NEW: feature gate state
    hostArchitecture              string // NEW: host architecture
}

func (k KvmDomainConfigurator) Configure(vmi *v1.VirtualMachineInstance, domain *api.Domain) error {
    crossArchEmulation := k.isCrossArchEmulation(vmi)

    // Cross-arch emulation is controlled solely by the feature gate,
    // independent of useEmulation. This allows native KVM VMs and
    // cross-arch emulated VMs to coexist on the same cluster.
    if crossArchEmulation {
        if !k.allowCrossArchEmulation {
            return fmt.Errorf("cross-architecture emulation requires the MultiArchitectureSoftwareEmulation feature gate")
        }

        logger.Infof("Cross-architecture emulation: host=%s, guest=%s. Using software emulation.",
            k.hostArchitecture, vmi.Spec.Architecture)

        domain.Spec.Type = "qemu"
        domain.Spec.Devices.Emulator = emulatorPath(vmi.Spec.Architecture)
        return nil
    }

    // Same-arch emulation: only used when KVM is unavailable
    if !k.kvmAvailable {
        if k.allowEmulation {
            logger.Infof("kvm not present. Using software emulation.")
            domain.Spec.Type = "qemu"
        } else {
            return fmt.Errorf("kvm not present")
        }
    }

    return nil
}

func emulatorPath(arch string) string {
    switch arch {
    case "arm64", "aarch64":
        return "/usr/bin/qemu-system-aarch64"
    case "amd64", "x86_64":
        return "/usr/bin/qemu-system-x86_64"
    case "s390x":
        return "/usr/bin/qemu-system-s390x"
    default:
        return ""
    }
}
```

#### 2. Architecture Converter Pipeline

The current `arch.Converter` is instantiated in `manager.go` using
`arch.NewConverter(runtime.GOARCH)`, which means all configurators in the
domain builder chain receive host architecture behavior. For cross-arch
emulation, the converter must be instantiated for the **guest** architecture.

**`pkg/virt-launcher/virtwrap/manager.go`** (architecture converter
creation):

```go
// Current: always uses host architecture
archConverter := arch.NewConverter(runtime.GOARCH)

// Proposed: use guest architecture when cross-arch emulation is active
guestArch := vmi.Spec.Architecture
if guestArch != "" && guestArch != runtime.GOARCH && crossArchEmulationEnabled {
    archConverter = arch.NewConverter(guestArch)
}
```

This ensures that all downstream configurators (CPU, graphics, controllers,
OS, etc.) produce architecture-appropriate domain XML for the guest, not the
host. The `arch.Converter` interface implementations (`converterAMD64`,
`converterARM64`, `converterS390X`) already encapsulate all
architecture-specific decisions — the change is simply selecting the right
implementation.

**Affected configurators** (non-exhaustive — all configurators that call
`c.Architecture` methods):

| Configurator | Architecture-dependent behavior |
|---|---|
| `CPUDomainConfigurator` | CPU hotplug support, MPX validation, CPU model override |
| `GraphicsDomainConfigurator` | Video device selection, disabled for cross-arch |
| `ControllersDomainConfigurator` | USB/SCSI controller models |
| `HypervisorFeaturesDomainConfigurator` | VMPort configuration |
| `OSDomainConfigurator` | SMBIOS, firmware selection |
| `WatchdogDomainConfigurator` | Architecture-specific models |
| `InputDeviceDomainConfigurator` | Input device types |

##### CPU Model Override

The `CPUDomainConfigurator` receives a `crossArchEmulation` flag. When
cross-architecture emulation is active,
`host-passthrough` and `host-model` CPU modes are incompatible — the host CPU
cannot be passed through to a guest of a different architecture. The
configurator overrides these modes to use `max`, which is QEMU's best-effort
CPU model that exposes all features the emulator can support:

```go
if c.crossArchEmulation && (model == v1.CPUModeHostModel || model == v1.CPUModeHostPassthrough) {
    domain.Spec.CPU.Mode = "custom"
    domain.Spec.CPU.Model = "max"
}
```

This also applies to the default CPU mode fallback (when no CPU model is
specified), which normally defaults to `host-model`.

##### Graphics Device

The `GraphicsDomainConfigurator` receives a `crossArchEmulation` flag. When
cross-architecture emulation is active, graphics devices are disabled entirely.
The cross-arch QEMU system binary (e.g., `qemu-system-aarch64-core`) is a
minimal package that may not include video device modules (e.g., virtio-gpu),
causing libvirt to reject the domain XML. Serial console remains available for
guest access.

```go
if g.crossArchEmulation {
    return nil // Skip video and VNC configuration
}
```

#### 3. Memory Overhead Calculation

The `LauncherHypervisorResources.GetMemoryOverhead()` method (implemented in
`pkg/hypervisor/kvm/hypervisorbackend.go`) accepts a `cpuArch` parameter
for architecture-specific overhead (e.g., 128 MiB pflash for ARM64 UEFI).
However, the caller in `kvm/runtime.go` always passes `runtime.GOARCH`:

```go
// Current: hardcoded host architecture
memlockSize = k.GetMemoryOverhead(vmi, runtime.GOARCH, config.AdditionalGuestMemoryOverheadRatio)

// Proposed: use guest architecture
guestArch := vmi.Spec.Architecture
if guestArch == "" {
    guestArch = runtime.GOARCH
}
memlockSize = k.GetMemoryOverhead(vmi, guestArch, config.AdditionalGuestMemoryOverheadRatio)
```

This ensures architecture-specific overheads (ARM64 UEFI pflash, s390x CCW
devices, etc.) are correctly accounted for when running cross-arch guests.

#### 4. EFI Firmware Path Detection

The EFI environment is normally detected once at virt-launcher startup using
the host architecture (`runtime.GOARCH`) to determine which firmware filenames
to look for (e.g., `OVMF_CODE.fd` for x86_64, `AAVMF_CODE.fd` for ARM64).
However, the OVMF path passed to virt-launcher is already guest-architecture
specific (set by `GetOVMFPath(vmi.Spec.Architecture)` in virt-controller).

For cross-architecture emulation, the EFI environment must be re-detected
using the **guest** architecture so the correct firmware filenames are resolved.
Without this, an ARM64 guest on an x86_64 host would look for `OVMF_CODE.fd`
in `/usr/share/AAVMF/` (which only contains `AAVMF_CODE.fd`), causing an
"EFI OVMF roms missing" error.

**`pkg/virt-launcher/virtwrap/manager.go`** (`generateConverterContext`):

```go
efiEnv := l.efiEnvironment
if l.allowCrossArchEmulation && vmi.Spec.Architecture != "" && vmi.Spec.Architecture != runtime.GOARCH {
    efiEnv = efi.DetectEFIEnvironment(vmi.Spec.Architecture, l.ovmfPath)
}
```

#### 5. Domain XML Output

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

Cross-architecture emulation is controlled **solely** by the
`MultiArchitectureSoftwareEmulation` feature gate. It is intentionally
decoupled from the existing `useEmulation` configuration to allow native KVM
VMs and cross-arch emulated VMs to coexist on the same cluster.

The `useEmulation` field only controls same-architecture emulation (fallback
when `/dev/kvm` is unavailable). It has no effect on cross-architecture
scenarios.

The feature gate behavior is as follows:

| Feature Gate Enabled | useEmulation | Host Arch | Guest Arch | KVM Available | Result |
|---------------------|--------------|-----------|------------|---------------|--------|
| No | false | amd64 | amd64 | Yes | Native KVM execution |
| No | true | amd64 | amd64 | No | Same-arch emulation (existing behavior) |
| No | false | amd64 | arm64 | Yes | **ERROR** - cross-arch not allowed |
| Yes | false | amd64 | arm64 | Yes | Cross-arch emulation via TCG |
| Yes | false | amd64 | amd64 | Yes | Native KVM execution |
| Yes | true | amd64 | amd64 | No | Same-arch emulation (existing behavior) |

**Key Points**:

- The feature gate alone is sufficient for cross-arch emulation — `useEmulation` is **not** required
- `useEmulation` only controls same-arch emulation when KVM is unavailable
- Native KVM VMs and cross-arch emulated VMs can coexist on the same cluster
- When the feature gate is disabled, attempting to run a cross-arch VM returns an error at domain conversion time
- When the feature gate is enabled, the scheduler **prefers** nodes matching the guest architecture (native execution with KVM) and only falls back to cross-arch emulation when no matching nodes are available

### QEMU Binary Requirements

#### Node Prerequisites

Nodes that will run cross-architecture VMs must have the appropriate QEMU system binaries installed:

- For ARM64 guests: `/usr/bin/qemu-system-aarch64`
- For AMD64 guests: `/usr/bin/qemu-system-x86_64`
- For s390x guests: `/usr/bin/qemu-system-s390x`

These binaries are typically provided by distro packages:

- **RHEL/CentOS/Fedora**: `qemu-system-aarch64-core`, `qemu-system-x86-core`, `qemu-system-s390x-core`
- **Debian/Ubuntu**: `qemu-system-arm`, `qemu-system-x86`, `qemu-system-s390x`

**Note on EL distro availability**: RHEL and CentOS Stream do not currently
ship cross-architecture QEMU system emulator packages in their default
repositories. For example, `qemu-system-aarch64-core` is not available in
CentOS Stream 9 BaseOS or AppStream for x86_64. These packages are currently
only available via the
[@virtmaint-sig/virt-preview](https://copr.fedorainfracloud.org/coprs/g/virtmaint-sig/virt-preview/)
COPR repository. This is a known blocker for productization — the packages
would need to be included in the base EL distribution or a supported
repository before this feature can graduate beyond Alpha.

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

1. **Feature Gate Check** (`converter/kvm/configurator.go`): If guest arch ≠ host arch and feature gate is disabled, return error
2. **Emulator Binary Check**: Before setting the emulator path, verify the binary exists using `emulatorBinaryExists()` (a package-level function variable for testability)
3. **Architecture Compatibility**: Validate that the machine type is compatible with the target guest architecture (handled by existing machine type validation)

#### User Experience

When validation fails:

- **Feature gate disabled**: VMI events show: `"cross-architecture emulation requires the MultiArchitectureSoftwareEmulation feature gate"`
- **Binary missing**: VMI fails with: `"required emulator binary not found: /usr/bin/qemu-system-aarch64"`
- **Unsupported architecture**: VMI fails with: `"unsupported guest architecture for cross-architecture emulation: <arch>"`

### Machine Type Handling

The VMI can specify a machine type explicitly via `spec.domain.machine.type`. For cross-architecture emulation:

- **Explicit machine type**: User must specify an architecture-appropriate type (e.g., `virt` for ARM64, `q35` for AMD64)
- **Auto-detection**: If not specified, KubeVirt will use the architecture-specific default from the arch converter:
  - AMD64: `q35` (default from converterAMD64)
  - ARM64: `virt` (default from converterARM64)
  - s390x: `s390-ccw-virtio` (default from converterS390X)

The architecture converter (pkg/virt-launcher/virtwrap/converter/arch/) is instantiated based on the **guest** architecture (from `vmi.Spec.Architecture`), not the host architecture, ensuring correct defaults.

### Pod Scheduling and Node Selectors

When `MultiArchitectureSoftwareEmulation` is enabled, the virt-controller must
adjust the virt-launcher pod's scheduling constraints to prefer nodes that
natively match the guest architecture while still allowing scheduling on nodes
with a different architecture for cross-arch emulation.

Without this adjustment, `NodeSelectorRenderer` sets `kubernetes.io/arch` as a
**required** node selector matching the guest architecture (e.g., `arm64`),
which would prevent scheduling on nodes with a different architecture —
defeating the purpose of cross-architecture emulation.

The implementation uses a two-part approach:

1. **Remove the hard `kubernetes.io/arch` node selector** so the pod *can*
   schedule on any node
2. **Add a preferred node affinity** for the guest architecture with maximum
   weight (100) so the scheduler *prefers* native-architecture nodes

This ensures that when nodes matching the guest architecture exist in the
cluster, the scheduler will place the VM there for native execution (with KVM).
Cross-architecture emulation via TCG is only used as a fallback when no
matching nodes are available.

**`pkg/virt-controller/services/template.go`** (`newNodeSelectorRenderer`):

When the feature gate is enabled, the `kubernetes.io/arch` node selector is
**omitted** from the hard requirement:

```go
architecture := vmi.Spec.Architecture
if t.clusterConfig.MultiArchitectureSoftwareEmulationEnabled() {
    architecture = ""
}

return NewNodeSelectorRenderer(
    vmi.Spec.NodeSelector,
    t.clusterConfig.GetNodeSelectors(),
    architecture,
    opts...,
)
```

**`pkg/virt-controller/services/template.go`** (preferred affinity):

A preferred node affinity is added after the pod is built, so the scheduler
strongly prefers nodes with matching architecture:

```go
if t.clusterConfig.MultiArchitectureSoftwareEmulationEnabled() {
    setPreferredArchitectureAffinity(vmi.Spec.Architecture, &pod)
}
```

This adds a `PreferredDuringSchedulingIgnoredDuringExecution` term with weight
100 for `kubernetes.io/arch` matching the VMI's `spec.architecture`. The
maximum weight ensures this preference takes priority over other scheduling
preferences.

The `machine-type.node.kubevirt.io/*` node selector is **preserved**. When the
cross-architecture QEMU binaries are installed on a node (e.g.,
`qemu-system-aarch64` on an amd64 host), libvirt reports the guest
architecture's machine types in its capabilities XML. The virt-handler node
labeller already discovers machine types from **all** guest architectures in
the capabilities (`getMachines()` iterates over `capabilities.Guests`), so
cross-arch machine types like `virt` will be advertised on the node
automatically. This ensures that the machine type node selector continues to
serve as a valid scheduling constraint — only nodes with the required QEMU
binaries installed will advertise the cross-arch machine types.

### virt-launcher Cross-Architecture Flag

The cross-architecture emulation state is threaded from virt-controller to
virt-launcher via a CLI flag on the compute container:

**`pkg/virt-controller/services/template.go`** (command construction):

```go
if t.clusterConfig.MultiArchitectureSoftwareEmulationEnabled() {
    command = append(command, "--allow-cross-arch-emulation")
}
```

**`cmd/virt-launcher/virt-launcher.go`**:

The `--allow-cross-arch-emulation` flag is parsed and propagated through to the
`ConverterContext` and `KvmDomainConfigurator` via the domain manager
initialization chain.

### Relationship to Deprecated MultiArchitecture Feature

The deprecated `MultiArchitecture` feature gate (v1.8.0) served a different purpose:

- **Old feature**: Allowed VMIs to **schedule** to nodes matching `spec.architecture` (using node labels/selectors)
- **This feature**: Allows VMIs to **run** on nodes with non-matching architecture via emulation

These are complementary concerns:

- Without old feature: All VMs schedule based on standard K8s scheduling
- With this feature: VMs can run even when scheduled to "wrong" architecture (with performance penalty)

### Relationship to Multi-Hypervisor Support (VEP #97)

KubeVirt v1.8.0 introduced a hypervisor abstraction layer (VEP #97) that
decouples the domain conversion pipeline from KVM, enabling alternative
hypervisor backends such as MSHV (HyperV-Direct). The per-hypervisor domain
configurators now live under `pkg/virt-launcher/virtwrap/converter/` (e.g.,
`converter/kvm/` and `converter/mshv/`) and are instantiated inline within
`Convert_v1_VirtualMachineInstance_To_api_Domain()` in `converter.go`. The
cross-architecture emulation changes in this VEP are scoped to the KVM
backend only, since KVM is the only backend that supports multiple host
architectures. Hypervisor selection is based on the host's available device,
and cross-arch emulation is handled within the `KvmDomainConfigurator`.

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
      featureGates:
      - MultiArchitectureSoftwareEmulation
```

**Note**: `useEmulation` is **not** required for cross-architecture emulation.
The feature gate alone is sufficient. `useEmulation` only controls same-arch
emulation when KVM is unavailable and can remain disabled (the default).

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

When a VM is running with cross-architecture emulation, virt-handler sets a
`SoftwareEmulation` condition on the VMI status. This is set by detecting that
the domain type is `qemu` and the guest architecture differs from the host's
`runtime.GOARCH`. The condition is removed if the VM is no longer emulated
(e.g., after migration to a native-arch node).

```yaml
status:
  conditions:
  - type: Ready
    status: "True"
  # Condition set by virt-handler when cross-arch emulation is active
  - type: SoftwareEmulation
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
- **Changing useEmulation**: Only affects same-arch emulation; cross-arch VMs are unaffected

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
2. **QEMU binary selection**: Test `emulatorPath()` for all supported architectures
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

1. Extend `KvmDomainConfigurator` with cross-arch detection and emulator
   path selection
2. Update `arch.Converter` instantiation in `manager.go` to use guest
   architecture when cross-arch emulation is active
3. Fix memory overhead caller in `kvm/runtime.go` to pass guest architecture
   instead of `runtime.GOARCH`
4. Add feature gate `MultiArchitectureSoftwareEmulation`
5. Basic validation (binary existence checks)
6. Replace `kubernetes.io/arch` hard node selector with a preferred node
   affinity when feature gate is enabled, so the scheduler prefers native
   architecture nodes but allows cross-arch emulation as a fallback
7. Thread `--allow-cross-arch-emulation` flag from virt-controller to
   virt-launcher

### Phase 2: AMD64 ↔ ARM64 Support (Alpha)

1. Support AMD64 hosts running ARM64 guests (KVM backend only)
2. Support ARM64 hosts running AMD64 guests (KVM backend only)
3. Validate all architecture-dependent configurators produce correct domain
   XML for cross-arch guests
4. Documentation for manual QEMU binary installation

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
- 2026-03-17: Updated design to align with multi-hypervisor abstraction layer
  (VEP #97) landed in v1.8.0. Replaced references to removed
  `HypervisorDomainConfigurator` with `KvmDomainConfigurator`. Added sections
  on architecture converter pipeline, memory overhead calculation, and
  relationship to multi-hypervisor support. Added sections on pod scheduling
  and node selector adjustments (replace `kubernetes.io/arch` hard node
  selector with preferred node affinity when feature gate is enabled, so
  scheduler prefers native architecture nodes with cross-arch emulation as
  fallback; machine-type selectors preserved since virt-handler already
  reports cross-arch machine types via libvirt capabilities) and virt-launcher
  CLI flag threading. Added functional test for cross-architecture emulation.
- 2026-03-18: Added EFI firmware path re-detection for cross-arch guests (guest
  architecture determines firmware filenames). Added CPU model override
  (`host-passthrough`/`host-model` → `max`) for TCG emulation. Added graphics
  device disabling for cross-arch emulation (minimal QEMU binary lacks
  virtio-gpu). Successfully tested ARM64 guest on AMD64 host with full boot
  and guest agent connectivity.
- 2026-04-02: Updated file paths to reflect upstream refactoring that moved
  `KvmDomainConfigurator` from `pkg/hypervisor/kvm/` to
  `pkg/virt-launcher/virtwrap/converter/kvm/configurator.go` and inlined the
  domain builder factory into `converter.go`. Added VEP Status Metadata
  section with v1.9.0 alpha target.

## Graduation Requirements

### Alpha

- [ ] Feature gate guards all code changes
- [ ] `KvmDomainConfigurator` extended with cross-arch detection and emulator
      path selection
- [ ] `arch.Converter` instantiated for guest architecture in cross-arch
      scenarios
- [ ] Memory overhead calculations use guest architecture instead of
      `runtime.GOARCH`
- [ ] `kubernetes.io/arch` hard node selector replaced with preferred node
      affinity when feature gate is enabled, preferring native architecture
      nodes with cross-arch emulation as a fallback
- [ ] `--allow-cross-arch-emulation` flag threaded from virt-controller to
      virt-launcher
- [ ] EFI firmware path re-detection using guest architecture for cross-arch
      scenarios
- [ ] CPU model override (`host-passthrough`/`host-model` → `max`) for TCG
      emulation
- [ ] Graphics device disabled for cross-arch emulation (minimal QEMU binary
      lacks video device support)
- [ ] `SoftwareEmulation` VMI condition set by virt-handler when cross-arch
      emulation is active
- [ ] Support for AMD64 hosts running ARM64 guests (KVM backend)
- [ ] Support for ARM64 hosts running AMD64 guests (KVM backend)
- [ ] Basic functional tests (unit + integration + E2E)
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
