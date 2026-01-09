# VEP #172: Cross-Architecture Virtualization

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

This VEP proposes introducing a `CrossArchitectureVirtualization` feature gate to
enable KubeVirt to run virtual machines for guest architectures that differ
from the host architecture. The feature supports two emulation modes,
auto-detected at runtime:

1. **Software emulation** via QEMU's TCG (Tiny Code Generator): available on
   any host with the appropriate cross-architecture QEMU system binary
   installed. Suitable for development, testing, and CI/CD workloads (10-100x
   performance overhead).

2. **Hardware-accelerated virtualization** via KVM extensions: available when
   the host platform provides hardware support for cross-architecture
   virtualization (e.g., SAE for ARM64 guests on s390x hosts).
   Performance characteristics are not yet known and will be determined
   once the upstream stack stabilizes and hardware is available for
   benchmarking.

The emulation mode is determined automatically based on libvirt capabilities —
users enable the feature gate and create cross-architecture VMs without needing
to specify which emulation backend to use. When hardware acceleration is
available it is preferred; otherwise software emulation is used as a fallback.

Currently, KubeVirt's `useEmulation` configuration only supports software
emulation as a fallback for guests running the same architecture as the host
when `/dev/kvm` is unavailable. This enhancement extends cross-architecture
execution as a first-class capability.

## Motivation

Cloud providers and development teams often need to run workloads on different
architectures for testing, CI/CD pipelines, or supporting heterogeneous
environments. Currently, KubeVirt requires matching architectures between the
host and guest, limiting its flexibility in multi-architecture scenarios.

Cross-architecture emulation would enable:

- Development and testing of ARM64 workloads on AMD64 infrastructure (and vice versa)
- Migration scenarios where temporary cross-arch execution is needed
- Cost optimization by using available hardware regardless of architecture
- Enhanced CI/CD capabilities for multi-architecture container images
- Running ARM64 workloads on s390x infrastructure using hardware-accelerated
  cross-architecture virtualization
- Consolidating workloads across architectures on a single platform

## Goals

- Enable VMs to run on nodes with different architectures using either software
  or hardware-accelerated virtualization
- Auto-detect hardware cross-architecture KVM support via libvirt capabilities
  and prefer it over software emulation
- Operate independently of `useEmulation`, allowing native KVM VMs and
  cross-arch emulated VMs to coexist
- Ensure proper architecture detection and QEMU binary selection
- Set VMI conditions that distinguish between hardware-accelerated and
  software-emulated cross-architecture execution
- Maintain backward compatibility with existing emulation behavior

## Non Goals

- Performance optimization of software emulation (expected to be significantly
  slower)
- Supporting all possible architecture combinations (initial focus on amd64,
  arm64, s390x)
- Migration of running VMs across architectures
- Automatic installation or management of QEMU binaries on nodes
- Kernel or QEMU development for hardware acceleration support (upstream
  responsibility)

## Definition of Users

This feature is intended for:

- Developers requiring multi-architecture testing environments
- CI/CD systems building and testing multi-architecture workloads
- Organizations with heterogeneous cluster architectures
- Users performing architecture transition planning
- Organizations running s390x infrastructure that want to execute ARM64
  workloads using hardware-accelerated cross-architecture virtualization

## User Stories

1. As a developer, I want to test my ARM64 VM image on an AMD64 development cluster without requiring dedicated ARM64 hardware. I would like my ARM-specific VM.yaml (with `spec.architecture: arm64`) to schedule on AMD64 workers and run using software emulation.

2. As a CI/CD engineer, I want to run automated tests for multiple architectures on a single Kubernetes cluster to simplify infrastructure.

3. As a platform engineer, I want to ensure that cross-architecture VMs are only scheduled when the required QEMU binaries are available on the nodes.

4. As a platform engineer managing s390x infrastructure, I want to run ARM64
   VMs on my s390x nodes using hardware acceleration so that I can consolidate
   workloads without the performance penalty of software emulation.

5. As a workload owner, I want KubeVirt to automatically detect whether my
   cross-architecture VM can use hardware acceleration or must fall back to
   software emulation, so I don't need to manage this distinction manually.

6. As a cluster administrator, I want to see VMI conditions that clearly
   distinguish between hardware-accelerated and software-emulated
   cross-architecture VMs so I can monitor my cluster's performance
   characteristics.

## Repos

- kubevirt/kubevirt
- kubevirt/enhancements
- kubevirt/user-guide

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
4. When they differ AND the `CrossArchitectureVirtualization` feature gate
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
The configurator auto-detects whether hardware acceleration is available via
libvirt capabilities and prefers it over software emulation.

**`pkg/virt-launcher/virtwrap/converter/kvm/configurator.go`**:

```go
type KvmDomainConfigurator struct {
    allowEmulation                 bool
    kvmAvailable                   bool
    allowCrossArchEmulation        bool     // feature gate state
    hostArchitecture               string   // host architecture
    kvmSupportedGuestArchitectures []string // from libvirt capabilities
}

func (k KvmDomainConfigurator) Configure(vmi *v1.VirtualMachineInstance, domain *api.Domain) error {
    crossArchEmulation := k.isCrossArchEmulation(vmi)

    if crossArchEmulation {
        if !k.allowCrossArchEmulation {
            return fmt.Errorf("cross-architecture emulation requires the CrossArchitectureVirtualization feature gate")
        }

        path := emulatorPath(vmi.Spec.Architecture)
        if path == "" {
            return fmt.Errorf("unsupported guest architecture for cross-architecture emulation: %s", vmi.Spec.Architecture)
        }
        if err := emulatorBinaryExists(path); err != nil {
            return err
        }
        domain.Spec.Devices.Emulator = path

        // Prefer hardware acceleration when available
        if k.kvmSupportsGuestArch(vmi.Spec.Architecture) {
            logger.Infof("Cross-architecture hardware-accelerated virtualization: host=%s, guest=%s. Using KVM.",
                k.hostArchitecture, vmi.Spec.Architecture)
            // domain.Spec.Type remains "kvm" (default)
            return nil
        }

        // Fall back to software emulation
        logger.Infof("Cross-architecture software emulation: host=%s, guest=%s. Using TCG.",
            k.hostArchitecture, vmi.Spec.Architecture)
        domain.Spec.Type = "qemu"
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

func (k KvmDomainConfigurator) kvmSupportsGuestArch(guestArch string) bool {
    for _, arch := range k.kvmSupportedGuestArchitectures {
        if arch == guestArch {
            return true
        }
    }
    return false
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

The `CPUDomainConfigurator` receives a `crossArchEmulation` flag and a
`crossArchHardwareAccelerated` flag. The CPU model handling differs between
software emulation and hardware-accelerated virtualization:

**Software emulation (TCG)**: `host-passthrough` and `host-model` CPU modes
are incompatible — the host CPU cannot be passed through to a guest of a
different architecture. The configurator overrides these modes to use `max`,
which is QEMU's best-effort CPU model that exposes all features the emulator
can support:

```go
if c.crossArchEmulation && !c.crossArchHardwareAccelerated &&
    (model == v1.CPUModeHostModel || model == v1.CPUModeHostPassthrough) {
    domain.Spec.CPU.Mode = "custom"
    domain.Spec.CPU.Model = "max"
}
```

This also applies to the default CPU mode fallback (when no CPU model is
specified), which normally defaults to `host-model`.

**Hardware-accelerated virtualization (KVM)**: Hardware-accelerated
cross-architecture virtualization may support `host-passthrough` or
`host-model` CPU modes. New s390 instructions are introduced to query
available arm64 features and populate the arm64 ID register contents. The
CPU model override is **skipped** when hardware acceleration is active. The
exact CPU model behavior depends on the hardware platform and will be refined
as implementations stabilize.

##### Graphics Device

The `GraphicsDomainConfigurator` receives a `crossArchEmulation` flag and a
`crossArchHardwareAccelerated` flag. The behavior differs by emulation mode:

**Software emulation (TCG)**: Graphics devices are disabled entirely. The
cross-arch QEMU system binary (e.g., `qemu-system-aarch64-core`) is a minimal
package that may not include video device modules (e.g., virtio-gpu), causing
libvirt to reject the domain XML. Serial console remains available for guest
access.

```go
if g.crossArchEmulation && !g.crossArchHardwareAccelerated {
    return nil // Skip video and VNC configuration
}
```

**Hardware-accelerated virtualization (KVM)**: The full QEMU binary may be required (or a
different set of device modules), so the graphics restriction may not apply.
This will be validated during implementation of hardware acceleration support.

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

#### 5. Hardware Cross-Architecture KVM Detection

The list of KVM-supported guest architectures is derived from libvirt's
capabilities XML. A guest architecture has hardware KVM support when it
appears with `<domain type='kvm'>` in the capabilities for a guest
architecture that differs from the host.

This detection occurs in virt-handler (which already queries libvirt
capabilities for node labelling) and is propagated to virt-launcher via the
converter context.

**`pkg/virt-handler/node-labeller/`** (capabilities parsing):

```go
func kvmSupportedGuestArchitectures(caps *libvirtxml.Caps) []string {
    hostArch := caps.Host.CPU.Arch
    var supported []string
    for _, guest := range caps.Guests {
        if guest.Arch.Name == hostArch {
            continue // Same-arch, not cross-arch
        }
        for _, domain := range guest.Arch.Domains {
            if domain.Type == "kvm" {
                supported = append(supported, guest.Arch.Name)
                break
            }
        }
    }
    return supported
}
```

The detected architectures are advertised as node labels:

```
kubevirt.io/cross-arch-kvm-aarch64=true
```

This allows the scheduler to prefer nodes with hardware cross-arch support
over nodes that can only offer software emulation.

On most hosts today this function will return an empty list — hardware
cross-architecture KVM support is not yet generally available.

#### 6. Node Capability Labels

In addition to the `kubevirt.io/cross-arch-kvm-<arch>` labels for hardware
acceleration, virt-handler advertises a general capability label for each
guest architecture a node can run:

```
kubevirt.io/vm-arch-<arch>=true
```

For example, an amd64 node with `qemu-system-aarch64` available in
virt-launcher would have:

```
kubevirt.io/vm-arch-amd64=true     # native
kubevirt.io/vm-arch-aarch64=true   # cross-arch software emulation
```

An s390x node with SAE hardware support would have:

```
kubevirt.io/vm-arch-s390x=true     # native
kubevirt.io/vm-arch-aarch64=true   # cross-arch hardware acceleration
kubevirt.io/cross-arch-kvm-aarch64=true  # hardware KVM indicator
```

virt-handler sets these labels based on:

1. **Native architecture**: Always set (e.g., `kubevirt.io/vm-arch-amd64=true`
   on amd64 nodes)
2. **Libvirt capabilities**: Guest architectures reported in libvirt's
   capabilities XML (both `<domain type='kvm'>` and `<domain type='qemu'>`)
   indicate available cross-architecture support

These labels serve as a **hard scheduling constraint** (see [Pod Scheduling
and Node Selectors](#pod-scheduling-and-node-selectors)) to prevent VMs from
scheduling on nodes that cannot run them. Without this constraint, cross-arch
VMs would schedule on any node due to the relaxed `kubernetes.io/arch`
selector, leading to a permanent CrashLoop when the required emulation
support is unavailable.

#### 7. Domain XML Output

When cross-architecture software emulation is configured, the resulting domain
XML would include:

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

When cross-architecture hardware-accelerated virtualization is configured:

```xml
<domain type='kvm'>
  <name>test-vm</name>
  <devices>
    <emulator>/usr/bin/qemu-system-aarch64</emulator>
    <!-- other devices -->
  </devices>
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
`CrossArchitectureVirtualization` feature gate. It is intentionally
decoupled from the existing `useEmulation` configuration to allow native KVM
VMs and cross-arch emulated VMs to coexist on the same cluster.

The `useEmulation` field only controls same-architecture emulation (fallback
when `/dev/kvm` is unavailable). It has no effect on cross-architecture
scenarios.

The feature gate behavior is as follows:

| Feature Gate Enabled | useEmulation | Host Arch | Guest Arch | KVM Cross-Arch | Result |
|---------------------|--------------|-----------|------------|----------------|--------|
| No | false | amd64 | amd64 | N/A | Native KVM execution |
| No | true | amd64 | amd64 | No | Same-arch emulation (existing behavior) |
| No | false | amd64 | arm64 | N/A | **ERROR** - cross-arch not allowed |
| Yes | false | amd64 | arm64 | No | Cross-arch software emulation (TCG) |
| Yes | false | s390x | arm64 | Yes | Cross-arch KVM-accelerated virtualization (SAE) |
| Yes | false | s390x | arm64 | No | Cross-arch software emulation (TCG fallback) |
| Yes | false | amd64 | amd64 | N/A | Native KVM execution |
| Yes | true | amd64 | amd64 | No | Same-arch emulation (existing behavior) |

**Key Points**:

- The feature gate alone is sufficient for cross-arch emulation — `useEmulation` is **not** required
- `useEmulation` only controls same-arch emulation when KVM is unavailable
- Native KVM VMs and cross-arch emulated VMs can coexist on the same cluster
- When the feature gate is disabled, attempting to run a cross-arch VM returns an error at domain conversion time
- When the feature gate is enabled, the scheduler **prefers** nodes matching the guest architecture (native execution with KVM) and only falls back to cross-arch emulation when no matching nodes are available
- When hardware acceleration is available, it is automatically preferred over software emulation

### VMI Conditions

virt-handler sets a condition on the VMI status to indicate the cross-arch
emulation mode. The condition is set by detecting the domain type and comparing
the guest architecture to the host's `runtime.GOARCH`. The condition is removed
if the VM is no longer emulated (e.g., after migration to a native-arch node).

**Software emulation**:

```yaml
status:
  conditions:
  - type: SoftwareEmulation
    status: "True"
    reason: "CrossArchitectureEmulation"
    message: "VM running with software emulation (host=amd64, guest=arm64)"
```

**Hardware-accelerated virtualization**:

```yaml
status:
  conditions:
  - type: HardwareEmulation
    status: "True"
    reason: "CrossArchitectureKVM"
    message: "VM running with hardware-accelerated cross-architecture emulation (host=s390x, guest=arm64)"
```

These conditions are mutually exclusive.

### QEMU Binary Requirements

#### virt-launcher Image Requirements

Cross-architecture QEMU system binaries must be included in the virt-launcher
container image, since QEMU runs inside the virt-launcher pod rather than
directly on the host node. The only host-level dependency is kernel modules
(e.g., KVM). The required binaries are:

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

#### Image Size Considerations

Adding multiple QEMU system binaries increases the virt-launcher image size
significantly (100-200MB per architecture). The approach for managing this:

- **Alpha**: Include cross-arch QEMU binaries directly in the virt-launcher
  image build
- **Beta/Future**: Explore splitting into architecture-specific sidecars or
  initContainers to avoid bloating the base image for clusters that do not
  use cross-architecture emulation

### Validation

#### Domain Conversion Time

Validation occurs in the converter when creating the domain specification:

1. **Feature Gate Check** (`converter/kvm/configurator.go`): If guest arch ≠ host arch and feature gate is disabled, return error
2. **Emulator Binary Check**: Before setting the emulator path, verify the binary exists using `emulatorBinaryExists()` (a package-level function variable for testability)
3. **Architecture Compatibility**: Validate that the machine type is compatible with the target guest architecture (handled by existing machine type validation)

#### Unsupported Device Handling

Some devices configured in the VMI spec may not be supported under
cross-architecture software emulation due to the minimal QEMU binary
lacking certain device modules. The current handling:

- **Graphics devices**: Silently disabled by the `GraphicsDomainConfigurator`
  when software emulation is active (see [Graphics Device](#graphics-device)).
  Serial console remains available for guest access.
- **Other devices**: Device support depends on what the cross-arch QEMU binary
  provides. Unsupported devices will cause libvirt to reject the domain XML
  at startup, surfaced as a VMI event.

For Alpha, the approach is to silently adapt where possible (e.g., graphics)
and fail clearly when a device is genuinely unavailable. User-guide
documentation will list known device limitations per emulation mode.

#### User Experience

When validation fails:

- **Feature gate disabled**: VMI events show: `"cross-architecture emulation requires the CrossArchitectureVirtualization feature gate"`
- **Binary missing**: VMI fails with: `"required emulator binary not found: /usr/bin/qemu-system-aarch64"`
- **Unsupported architecture**: VMI fails with: `"unsupported guest architecture for cross-architecture emulation: <arch>"`
- **Unsupported device**: libvirt rejects the domain XML; the error is
  surfaced as a VMI event indicating which device is unavailable

### Machine Type Handling

The VMI can specify a machine type explicitly via `spec.domain.machine.type`. For cross-architecture emulation:

- **Explicit machine type**: User must specify an architecture-appropriate type (e.g., `virt` for ARM64, `q35` for AMD64)
- **Auto-detection**: If not specified, KubeVirt will use the architecture-specific default from the arch converter:
  - AMD64: `q35` (default from converterAMD64)
  - ARM64: `virt` (default from converterARM64)
  - s390x: `s390-ccw-virtio` (default from converterS390X)

The architecture converter (pkg/virt-launcher/virtwrap/converter/arch/) is instantiated based on the **guest** architecture (from `vmi.Spec.Architecture`), not the host architecture, ensuring correct defaults.

### Pod Scheduling and Node Selectors

When `CrossArchitectureVirtualization` is enabled, the virt-controller must
adjust the virt-launcher pod's scheduling constraints to prefer nodes that
natively match the guest architecture while still allowing scheduling on nodes
with a different architecture for cross-arch emulation.

Without this adjustment, `NodeSelectorRenderer` sets `kubernetes.io/arch` as a
**required** node selector matching the guest architecture (e.g., `arm64`),
which would prevent scheduling on nodes with a different architecture —
defeating the purpose of cross-architecture emulation.

The implementation uses a combination of hard constraints and soft
preferences:

1. **Remove the hard `kubernetes.io/arch` node selector** so the pod is not
   restricted to nodes matching the guest architecture
2. **Add a hard `kubevirt.io/vm-arch-<guest-arch>` node selector** so the pod
   can only schedule on nodes that are capable of running VMs for the guest
   architecture (native, hardware cross-arch, or software emulation). Without
   this constraint, VMs would schedule on incapable nodes and enter a
   permanent CrashLoop
3. **Add a preferred node affinity** for the guest architecture with maximum
   weight (100) so the scheduler *prefers* native-architecture nodes
4. **Add a preferred node affinity** for hardware cross-arch KVM support with
   weight 50, using the `kubevirt.io/cross-arch-kvm-<arch>` node label, so
   hardware-accelerated cross-arch nodes are preferred over software-only nodes

This ensures that VMs only schedule on capable nodes, and the scheduler
prefers: native architecture (KVM) > hardware cross-arch (KVM/SAE) >
software cross-arch (TCG). When no capable nodes exist, the VM remains
Pending rather than CrashLooping.

**`pkg/virt-controller/services/template.go`** (`newNodeSelectorRenderer`):

When the feature gate is enabled, the `kubernetes.io/arch` node selector is
**replaced** with a `kubevirt.io/vm-arch-<guest-arch>` node selector:

```go
architecture := vmi.Spec.Architecture
if t.clusterConfig.CrossArchitectureVirtualizationEnabled() {
    architecture = ""
}

return NewNodeSelectorRenderer(
    vmi.Spec.NodeSelector,
    t.clusterConfig.GetNodeSelectors(),
    architecture,
    opts...,
)
```

The `kubevirt.io/vm-arch-<guest-arch>` label is added as a hard node selector
on the pod:

```go
if t.clusterConfig.CrossArchitectureVirtualizationEnabled() && vmi.Spec.Architecture != "" {
    pod.Spec.NodeSelector["kubevirt.io/vm-arch-"+vmi.Spec.Architecture] = "true"
}
```

**`pkg/virt-controller/services/template.go`** (preferred affinities):

Preferred node affinities are added after the pod is built:

```go
if t.clusterConfig.CrossArchitectureVirtualizationEnabled() {
    // Weight 100: strongly prefer native architecture nodes
    setPreferredArchitectureAffinity(vmi.Spec.Architecture, &pod, 100)

    // Weight 50: prefer nodes with hardware cross-arch KVM support
    setPreferredCrossArchKVMAffinity(vmi.Spec.Architecture, &pod, 50)
}
```

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
if t.clusterConfig.CrossArchitectureVirtualizationEnabled() {
    command = append(command, "--allow-cross-arch-emulation")
}
```

**`cmd/virt-launcher/virt-launcher.go`**:

The `--allow-cross-arch-emulation` flag is parsed and propagated through to the
`ConverterContext` and `KvmDomainConfigurator` via the domain manager
initialization chain.

### Hardware-Accelerated Virtualization

#### SAE (Start Arm Execution)

The initial hardware-accelerated cross-architecture emulation target is s390x
hosts running ARM64 (aarch64) guests using the Start Arm Execution (SAE)
instruction.

On 2026-04-02, IBM and ARM [announced a strategic collaboration][ibm-arm] to
expand virtualization technologies that allow ARM-based software environments
to operate within IBM's enterprise computing platforms. The corresponding
[kernel patch series][kvm-patches] implements the KVM infrastructure for this
capability.

[ibm-arm]: https://newsroom.ibm.com/2026-04-02-ibm-announces-strategic-collaboration-with-arm-to-shape-the-future-of-enterprise-computing
[kvm-patches]: https://lore.kernel.org/all/20260402042125.3948963-1-seiden@linux.ibm.com/

Key characteristics of SAE:

- **Analogous to SIE**: Just as SIE (Start Interpretive Execution) enables
  s390x guest VMs with hardware acceleration, SAE enables ARM64 guest VMs
- **EL2 implementation**: The s390x KVM host acts as an ARM64 EL2
  (hypervisor) for EL1/EL0 (OS/application) ARM64 guests
- **Dual KVM modules**: The kernel loads both `kvm-s390` (for s390x guests)
  and `kvm-arm64` (for ARM64 guests) simultaneously
- **Shared arm64 KVM code**: The kernel's ARM64 KVM implementation is
  refactored into architecture-agnostic code (`virt/kvm/arm64/`) that both
  native ARM64 hosts and s390x SAE hosts can use

#### Kernel and Libvirt Requirements

The following are required for hardware-accelerated cross-architecture
emulation (based on the [v1 patch series][kvm-patches]):

- `CONFIG_KVM_ARM64` enabled on s390x (new Kconfig option)
- `kvm-arm64` kernel module loaded
- SAE hardware capability reported via `hwcap`
- Minimum kernel version: TBD (patches are v1 as of 2026-04-02)
- Libvirt with SAE capabilities reporting (minimum version TBD)

#### Libvirt Capabilities Detection

When hardware cross-arch KVM support is present, libvirt reports it in its
capabilities XML:

```xml
<capabilities>
  <host>
    <cpu>
      <arch>s390x</arch>
    </cpu>
  </host>
  <guest>
    <os_type>hvm</os_type>
    <arch name='aarch64'>
      <wordsize>64</wordsize>
      <domain type='kvm'/>    <!-- Hardware cross-arch support -->
      <domain type='qemu'/>   <!-- Software emulation also available -->
      <machine>virt</machine>
    </arch>
  </guest>
</capabilities>
```

The key indicator is `<domain type='kvm'>` under a guest architecture that
differs from the host architecture. When only `<domain type='qemu'>` is present
for a cross-architecture guest, only software emulation is available.

#### Supported Architecture Combinations

Initially, hardware-accelerated cross-architecture emulation is limited to
what the hardware supports:

| Host Architecture | Guest Architecture | Hardware Support |
|---|---|---|
| s390x | arm64 | SAE instruction |

Future hardware may extend this table. The implementation is designed to be
generic — any architecture combination reported by libvirt as having
`<domain type='kvm'>` support will be detected automatically.

#### Open Questions

The following questions will be resolved as the upstream kernel, QEMU, and
libvirt support for hardware-accelerated cross-architecture virtualization
stabilizes.

The v1 kernel patch series provides the groundwork: SAE instruction support,
basic arm64 KVM module on s390, VM/vCPU lifecycle, vCPU IOCTLs, and a basic
page fault handler. Upcoming patch series will introduce system-register
handling, interrupt support, hypercalls, and additional features such as PMU.

1. **CPU model**: New s390 instructions query available arm64 features and
   populate arm64 ID register contents. Does this support `host-passthrough`
   in QEMU/libvirt, or does it present a fixed ARM64 CPU model?
2. **Device model**: What virtio devices are supported? Is the full ARM64
   device model available, or a subset?
3. **KVM device path**: The v1 patch series extends the KVM core to support a
   configurable device name (needed for two KVM devices on one architecture).
   How does QEMU/libvirt discover the arm64 KVM device on s390x?
4. **System registers and interrupts**: System-register handling and interrupt
   support are not yet implemented (planned for future patch series). What is
   the timeline?
5. **Live migration**: Can an ARM64 VM be migrated between two s390x SAE-
   capable hosts? What about migration between an s390x SAE host and a
   native ARM64 host?
6. **QEMU requirements**: Does QEMU need SAE-specific changes, or does it
   work transparently via the KVM API with the existing `qemu-system-aarch64`
   binary?

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
      - CrossArchitectureVirtualization
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

**Note on container runtime warnings**: When pulling a container image whose
platform metadata does not match the node's architecture (e.g., pulling an
`aarch64` image on an `amd64` node), the container runtime (CRI-O/containerd)
will log a warning. This is cosmetic — the pull succeeds and the disk image
inside the container is architecture-correct for the guest VM. This is a known
limitation at Alpha and will be documented as expected behavior for
cross-architecture containerDisk images.

### Create an ARM64 VMI on s390x (Hardware Acceleration)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: arm64-on-z
spec:
  architecture: arm64
  domain:
    machine:
      type: virt
    cpu:
      model: host-passthrough  # Supported with hardware acceleration
    devices:
      disks:
      - name: containerdisk
        disk:
          bus: virtio
    resources:
      requests:
        memory: 4Gi
  volumes:
  - name: containerdisk
    containerDisk:
      image: quay.io/containerdisks/fedora:40-aarch64
```

### Query VMI Status for Emulation Information

When a VM is running with cross-architecture emulation, virt-handler sets a
condition on the VMI status. The condition type indicates the emulation mode:

**Software emulation**:

```yaml
status:
  conditions:
  - type: Ready
    status: "True"
  - type: SoftwareEmulation
    status: "True"
    reason: "CrossArchitectureEmulation"
    message: "VM running with software emulation (host=amd64, guest=arm64)"
```

**Hardware-accelerated virtualization**:

```yaml
status:
  conditions:
  - type: Ready
    status: "True"
  - type: HardwareEmulation
    status: "True"
    reason: "CrossArchitectureKVM"
    message: "VM running with hardware-accelerated cross-architecture emulation (host=s390x, guest=arm64)"
```

## Alternatives

1. **Require matching architectures** - Current behavior, limits flexibility
2. **Use separate arch-specific clusters** - Increases infrastructure complexity and cost
3. **Use nested virtualization with multi-arch VMs** - Complex, poor performance
4. **Wait for hardware multi-arch support** - Not available on most platforms today
5. **Separate feature gates for software and hardware virtualization** - Adds
   unnecessary configuration complexity; the emulation mode is an
   implementation detail that can be auto-detected at runtime

## Scalability

### Performance Implications

| Metric | Native KVM | Hardware Cross-Arch | Software Cross-Arch (TCG) |
|--------|-----------|---------------------|---------------------------|
| CPU | Baseline | TBD | 10-100x slower |
| Memory | Baseline | TBD | Similar |
| I/O | Baseline | TBD | Moderate overhead |
| Boot time | Baseline | TBD | 5-20x slower |

### Use Case Suitability

| Use Case | Software Emulation | Hardware-Accelerated |
|----------|-------------------|-------------------|
| Development/Testing | Yes (primary use case) | Yes |
| CI/CD pipelines | Yes | Yes |
| Production workloads | No | TBD |
| Low-throughput apps | Maybe | Yes |
| Real-time systems | No | Maybe (depends on interrupt latency) |

Software emulation is designed for development, testing, and low-throughput
scenarios. Hardware-accelerated virtualization performance characteristics are TBD.

## Update/Rollback Compatibility

### Update Scenarios

- **Enabling feature gate**: Existing VMs on matching architectures are unaffected; new cross-arch VMs can be created
- **Disabling feature gate**: Existing running cross-arch VMs continue running; new cross-arch VMs fail to start
- **Changing useEmulation**: Only affects same-arch emulation; cross-arch VMs are unaffected
- **Kernel upgrade adding hardware cross-arch support**: virt-handler detects
  new capabilities automatically via libvirt; new node labels are applied; new
  VMs benefit without cluster reconfiguration

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
5. **KVM guest arch detection**: Test `kvmSupportsGuestArch()` with mocked
   libvirt capabilities
6. **Configurator decision logic**: Test the three-way decision (hardware →
   software → error) with various capability combinations
7. **Node label generation**: Test `kvmSupportedGuestArchitectures()` parsing

### Integration Tests

1. **Cross-architecture domain conversion**: Create VMI with arm64 spec on amd64 test environment, verify domain XML includes correct emulator path
2. **Feature gate enforcement**: Verify cross-arch VMI fails when gate disabled
3. **Same-arch fallback**: Verify existing same-arch emulation still works
4. **Domain conversion with hardware acceleration**: Verify domain XML has
   `type='kvm'` and cross-arch emulator path when hardware support is detected
5. **Fallback to software emulation**: Verify domain uses `type='qemu'` when
   hardware support is absent

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

3. **VMI conditions**: Verify correct condition type (`SoftwareEmulation` vs
   `HardwareEmulation`) is set based on emulation mode

4. **Hardware acceleration** (requires SAE-capable hardware):
   - ARM64 VM on s390x with SAE: verify KVM domain type, correct emulator
     binary
   - Verify `HardwareEmulation` condition is set
   - Node scheduling: verify preference ordering (native > hardware
     cross-arch > software cross-arch)

5. **Performance baseline** (informational):
   - Measure boot time for native vs emulated
   - Document expected performance characteristics

### Test Infrastructure Requirements

- Nodes with cross-arch QEMU binaries installed
- Multi-arch container disk images for testing
- Ability to toggle feature gate in test environment
- s390x CI infrastructure for hardware acceleration tests (once SAE hardware
  is generally available)

## Implementation Phases

### Phase 1: Core Infrastructure (Alpha)

1. Extend `KvmDomainConfigurator` with cross-arch detection, emulator
   path selection, and hardware acceleration auto-detection
2. Update `arch.Converter` instantiation in `manager.go` to use guest
   architecture when cross-arch emulation is active
3. Fix memory overhead caller in `kvm/runtime.go` to pass guest architecture
   instead of `runtime.GOARCH`
4. Add feature gate `CrossArchitectureVirtualization`
5. Basic validation (binary existence checks)
6. Replace `kubernetes.io/arch` hard node selector with
   `kubevirt.io/vm-arch-<arch>` hard node selector and preferred node
   affinities when feature gate is enabled, ensuring VMs only schedule on
   capable nodes while preferring native architecture
7. Thread `--allow-cross-arch-emulation` flag from virt-controller to
   virt-launcher
8. Implement libvirt capabilities parsing for cross-arch KVM detection and
   `kubevirt.io/cross-arch-kvm-<arch>` node labels (effectively a no-op on
   current hardware, but establishes the detection infrastructure)

### Phase 2: AMD64 ↔ ARM64 Software Emulation (Alpha)

1. Support AMD64 hosts running ARM64 guests (KVM backend only)
2. Support ARM64 hosts running AMD64 guests (KVM backend only)
3. Validate all architecture-dependent configurators produce correct domain
   XML for cross-arch guests
4. Documentation for manual QEMU binary installation

### Phase 3: s390x Software Emulation and Hardware Acceleration (Beta)

1. Add s390x guest support on AMD64/ARM64 hosts (software emulation)
2. Add s390x host support for other guest architectures (software emulation)
3. Validate hardware acceleration path on SAE-capable hardware (if available)
4. CPU model and device model refinement for hardware acceleration
5. Performance benchmarking (SAE vs native vs TCG)
6. Comprehensive test matrix for all combinations

### Phase 4: Production Readiness (GA)

1. Performance benchmarking and documentation
2. Production readiness review
3. Explore automated QEMU binary distribution
4. Multi-release stability confirmation for hardware acceleration path
5. Clear support matrix (hardware, kernel, libvirt, QEMU versions)

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
- 2026-04-07: Merged hardware-accelerated cross-architecture emulation (VEP
  #173) into this VEP. Renamed feature gate from
  `MultiArchitectureSoftwareEmulation` to `CrossArchitectureVirtualization`
  (renamed from `MultiArchitectureEmulation`) to
  accommodate both software (TCG) and hardware (KVM/SAE) emulation modes under
  a single feature gate. Added auto-detection of hardware cross-arch KVM
  support via libvirt capabilities, three-tier node scheduling preference,
  `HardwareEmulation` VMI condition, and SAE background.

## Graduation Requirements

### Alpha

- [ ] Feature gate `CrossArchitectureVirtualization` guards all code changes
- [ ] `KvmDomainConfigurator` extended with cross-arch detection, emulator
      path selection, and hardware acceleration auto-detection
- [ ] `arch.Converter` instantiated for guest architecture in cross-arch
      scenarios
- [ ] Memory overhead calculations use guest architecture instead of
      `runtime.GOARCH`
- [ ] `kubernetes.io/arch` hard node selector replaced with
      `kubevirt.io/vm-arch-<arch>` hard node selector and preferred node
      affinities when feature gate is enabled, ensuring VMs only schedule on
      capable nodes while preferring native architecture
- [ ] `--allow-cross-arch-emulation` flag threaded from virt-controller to
      virt-launcher
- [ ] EFI firmware path re-detection using guest architecture for cross-arch
      scenarios
- [ ] CPU model override (`host-passthrough`/`host-model` → `max`) for TCG
      emulation
- [ ] Graphics device disabled for cross-arch software emulation (minimal
      QEMU binary lacks video device support)
- [ ] `SoftwareEmulation` VMI condition set by virt-handler when cross-arch
      software emulation is active
- [ ] `kubevirt.io/vm-arch-<arch>` node labels set by virt-handler for all
      guest architectures a node can run (native + cross-arch)
- [ ] Libvirt capabilities parsing for cross-arch KVM detection and
      `kubevirt.io/cross-arch-kvm-<arch>` node labels (infrastructure only —
      no hardware expected at Alpha)
- [ ] Support for AMD64 hosts running ARM64 guests (KVM backend)
- [ ] Support for ARM64 hosts running AMD64 guests (KVM backend)
- [ ] Basic functional tests (unit + integration + E2E)
- [ ] Documentation of performance limitations and use cases
- [ ] Cross-arch QEMU binaries included in virt-launcher image
- [ ] Validation that required QEMU binaries exist
- [ ] User-facing documentation in kubevirt/user-guide covering feature gate
      enablement, supported architecture combinations, QEMU binary
      requirements, performance limitations, and example VMI specs

### Beta

- [ ] Support for s390x in cross-architecture scenarios (all combinations,
      software emulation)
- [ ] Hardware acceleration path validated on SAE-capable hardware (if
      available)
- [ ] `HardwareEmulation` VMI condition set when hardware cross-arch
      emulation is active
- [ ] Three-tier node scheduling preference (native > hardware cross-arch >
      software cross-arch) validated end-to-end
- [ ] CPU model and graphics device handling refined for hardware
      acceleration
- [ ] Comprehensive test coverage across architecture pairs (E2E tests)
- [ ] Performance benchmarking and documented performance characteristics
- [ ] At least one release in Alpha with no major issues
- [ ] User feedback incorporated from Alpha release
- [ ] Consider automated QEMU binary distribution approach
- [ ] Consider per-VM emulation policy (e.g., a VMI-level field to disable
      cross-arch emulation, prefer hardware acceleration, or require hardware
      acceleration for specific workloads)

### GA

- [ ] Multiple releases in Beta with no major issues
- [ ] Clear documentation on supported architecture combinations
- [ ] Established best practices and use case guidance
- [ ] Production readiness review (acknowledging performance limitations for
      software emulation)
- [ ] Decision on QEMU binary distribution strategy (manual vs bundled vs
      sidecar)
- [ ] Hardware acceleration production deployment validated (if hardware
      available)
- [ ] Clear support matrix for hardware acceleration (hardware, kernel,
      libvirt, QEMU versions)
