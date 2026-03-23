# VEP #227: Enable Secure Boot for ARM64 Guests

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Enable UEFI Secure Boot for ARM64 (aarch64) guest virtual machines in
KubeVirt. ARM64 guests currently default to EFI boot with Secure Boot
explicitly disabled. The underlying stack (QEMU 10.0+, libvirt, edk2) is
gaining support for Secure Boot on aarch64 through a new `uefi-vars` device
mechanism that is fundamentally different from the x86_64 SMM-based approach.
This VEP proposes leveraging the libvirt firmware auto-selection infrastructure
introduced by [VEP #241](https://github.com/kubevirt/enhancements/issues/241)
to enable this capability.

## Motivation

Secure Boot is an important security feature that verifies the integrity of
boot components before execution, protecting against bootkits and rootkits.
While KubeVirt has supported Secure Boot for x86_64 guests for some time,
ARM64 guests have been unable to use it due to architectural differences in
how Secure Boot is implemented.

The underlying infrastructure is now ready:

- **QEMU 10.0+** introduced the `uefi-vars-sysbus` device, which implements
  UEFI variable operations on the host side, enabling Secure Boot on ARM
  without requiring EL3 (TrustZone) emulation with KVM.
- **libvirt** has added firmware descriptor files for aarch64 Secure Boot
  using the `uefi-vars` device and supports firmware auto-selection for
  aarch64 with Secure Boot features.
- **edk2** ships aarch64 firmware builds (`QEMU_EFI.qemuvars.fd`) that work
  with the `uefi-vars` device and support Secure Boot variable protection.
  The required firmware files (`QEMU_EFI.qemuvars.fd` and Secure Boot
  variable templates) are available from upstream edk2 stable tag
  `edk2-stable202502` onwards. Distro packages are landing in CentOS Stream
  10 via `edk2-20260221-1.el10`.

KubeVirt should expose this capability to users so that ARM64 workloads
requiring Secure Boot (e.g. for compliance, OS requirements, or supply chain
security) can be deployed.

## Goals

- Allow users to request Secure Boot for ARM64 guest VMs using the existing
  `spec.domain.firmware.bootloader.efi.secureBoot` API field.
- Introduce an `ARM64SecureBoot` feature gate so that cluster admins can
  explicitly opt in to this capability when they have confirmed their
  underlying stack (QEMU 10.0+, libvirt with `uefi-vars` support, edk2
  firmware) is capable.
- Leverage the libvirt firmware auto-selection infrastructure from
  [VEP #241](https://github.com/kubevirt/enhancements/issues/241) for ARM64
  Secure Boot, delegating firmware and device configuration to libvirt.
- Make the SMM validation architecture-aware, since SMM is an x86-only concept
  and is not required for ARM64 Secure Boot.
- Ensure backward compatibility: ARM64 guests without Secure Boot continue to
  work as before.

## Non Goals

- Supporting Secure Boot with confidential computing (SEV/SNP/TDX) on ARM64
  (these technologies are x86-only today).
- Providing ARM64-specific attestation or key enrollment APIs beyond what the
  firmware provides by default.
- Automatic detection of hypervisor stack capabilities (QEMU version, libvirt
  features, firmware availability). This is a broader concern that applies to
  multiple features and will be addressed separately.

## Definition of Users

- **VM Authors**: Users who create and manage ARM64 VMs and want to enable
  Secure Boot for security or compliance reasons.
- **Cluster Admins**: Administrators who manage KubeVirt clusters with ARM64
  worker nodes and enable the `ARM64SecureBoot` feature gate when the
  virt-launcher image includes the required QEMU, libvirt, and firmware
  versions.

## User Stories

- As a VM Author, I want to deploy ARM64 VMs with UEFI Secure Boot enabled so
  that my workloads meet security compliance requirements.
- As a VM Author, I want to use the same `secureBoot` API field for ARM64 as I
  do for x86_64, so that my VM definitions are portable across architectures.
- As a Cluster Admin, I want to enable the `ARM64SecureBoot` feature gate when
  I have confirmed that my ARM64 nodes have the required QEMU, libvirt, and
  firmware versions, so that VM Authors can use Secure Boot on ARM64.
- As a VM Author using instance types, I want Secure Boot preferences to apply
  correctly to ARM64 VMs.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Dependency on VEP #241: Firmware Auto-Selection

This VEP builds on the libvirt firmware auto-selection infrastructure
introduced by
[VEP #241](https://github.com/kubevirt/enhancements/issues/241). VEP #241
adds the domain schema types (`Firmware` attribute, `FirmwareInfo`,
`FirmwareFeature`), the `UsesFirmwareAutoSelection` field in
`EFIConfiguration`, and the firmware auto-selection code path in the domain
converter — initially for x86_64 EFI Secure Boot.

This VEP extends that same infrastructure to ARM64 Secure Boot, which uses
the same `<os firmware='efi'>` XML mechanism but resolves to a fundamentally
different firmware configuration (ROM loader + `uefi-vars` device instead of
pflash).

### Background: ARM64 vs x86_64 Secure Boot Architecture

On x86_64, Secure Boot relies on SMM (System Management Mode) to protect UEFI
authenticated variables from unauthorized guest modification. The firmware is
loaded via two pflash devices (code + variables), and SMM provides the
isolation layer.

On ARM64, SMM does not exist. Instead, the new approach uses the QEMU
`uefi-vars-sysbus` device, which implements UEFI variable operations on the
host side. This device:

- Stores UEFI variables in a JSON file on the host
- Protects authenticated variables (including Secure Boot state) without
  requiring ARM EL3/TrustZone emulation
- Works with KVM (unlike the EL3 approach which is TCG-only)
- Uses a ROM-type loader instead of pflash

The resulting libvirt domain XML is structurally different:

| Aspect | x86_64 | ARM64 |
|---|---|---|
| Loader type | `pflash` | `rom` |
| Variable storage | `<nvram>` (binary pflash) | `<varstore>` (JSON via uefi-vars device) |
| QEMU mechanism | Two pflash drives | `-bios` + `uefi-vars-sysbus` device |
| SMM required | Yes | No |
| Minimum QEMU | Long-standing | 10.0+ |

### Leveraging Libvirt Firmware Auto-Selection

Despite the architectural differences, both x86_64 and ARM64 Secure Boot use
the same libvirt firmware auto-selection XML on the KubeVirt side. When a user
requests Secure Boot on an ARM64 VMI, KubeVirt generates the same XML
structure as VEP #241 uses for x86_64:

```xml
<os firmware='efi'>
  <type arch='aarch64' machine='virt'>hvm</type>
  <firmware>
    <feature enabled='yes' name='secure-boot'/>
    <feature enabled='yes' name='enrolled-keys'/>
  </firmware>
</os>
```

Libvirt then matches this against available firmware descriptor JSON files on
the host (e.g. `90-edk2-aarch64-qemuvars-sb-enrolled.json`) and automatically
configures the correct loader, varstore, and QEMU device. The resulting domain
XML produced by libvirt will look like:

```xml
<os firmware='efi'>
  <type arch='aarch64' machine='virt'>hvm</type>
  <firmware>
    <feature enabled='yes' name='enrolled-keys'/>
    <feature enabled='yes' name='secure-boot'/>
  </firmware>
  <loader type='rom' format='raw'>/usr/share/edk2/aarch64/QEMU_EFI.qemuvars.fd</loader>
  <varstore template='/usr/share/edk2/aarch64/vars.secboot.json'
            path='/var/lib/libvirt/qemu/varstore/guest.json'/>
</os>
```

Note that despite using the same input XML, libvirt resolves ARM64 to a ROM
loader with a `<varstore>` element (using the `uefi-vars` device) rather than
the pflash `<loader>`/`<nvram>` that x86_64 receives. This is handled
entirely by libvirt's firmware descriptor matching — KubeVirt does not need
to differentiate.

This approach:

- Avoids KubeVirt needing to know about `uefi-vars-sysbus` device internals
- Handles distribution-specific firmware paths via firmware descriptor files
- Unifies the Secure Boot code path across x86_64 and ARM64 — both use the
  same `UsesFirmwareAutoSelection` mechanism from VEP #241
- Is the approach libvirt's own documentation recommends for aarch64

### Feature Gate

A new `ARM64SecureBoot` feature gate will guard this functionality. This is
separate from the `FirmwareAutoSelection` feature gate introduced by VEP #241
because it has additional requirements beyond firmware auto-selection:

- QEMU 10.0+ (for the `uefi-vars-sysbus` device)
- libvirt with `<varstore>` and firmware auto-selection support for aarch64
- edk2-aarch64 firmware with `QEMU_EFI.qemuvars.fd` and Secure Boot variable
  templates

Since QEMU, libvirt, and edk2 firmware are all shipped within the
virt-launcher container image, the stack requirements are determined by the
base OS used to build the image. Currently, the required versions are only
available in **CentOS Stream 10** and derivatives. Virt-launcher images
built on CentOS Stream 9 or earlier will not include the necessary
components.

The project can already build CentOS Stream 10 based virt-launcher images,
though CentOS Stream 10 is not yet formally supported as a base OS.
Formal CentOS Stream 10 support is being tracked separately in
[VEP #210: CentOS Stream 10 Support](https://github.com/kubevirt/enhancements/issues/210).
This feature's graduation is therefore dependent on the progress of VEP
#210 — ARM64 Secure Boot can only graduate to GA once CentOS Stream 10
based images are the default or formally supported.

KubeVirt does not yet have a general mechanism for per-feature hypervisor
version requirements or for gating features based on the contents of the
virt-launcher image. The feature gate serves as an explicit admin opt-in,
allowing the feature to be introduced in Alpha using CentOS Stream 10
based virt-launcher images before they are formally supported. Once CentOS
Stream 10 images are standard, the feature gate can be promoted and
eventually removed.

The feature gate will be registered in
`pkg/virt-config/featuregate/active.go`:

```go
// Owner: @lyarwood
// Alpha: v1.X.0
//
// ARM64SecureBoot enables UEFI Secure Boot for ARM64 guests using
// libvirt firmware auto-selection and the QEMU uefi-vars device.
// Requires QEMU 10.0+, libvirt with uefi-vars/varstore support,
// and edk2-aarch64 firmware with Secure Boot templates.
ARM64SecureBoot = "ARM64SecureBoot"
```

When the feature gate is not enabled:

- ARM64 VMIs requesting `secureBoot: true` will be rejected by the
  validating webhook with a clear error indicating the feature gate must be
  enabled.
- Existing ARM64 EFI boot behavior (SecureBoot defaulting to false) is
  unchanged.

### Implementation Changes

#### 1. Feature Gate Definition (`pkg/virt-config/featuregate/active.go`)

Add the `ARM64SecureBoot` constant and register it as Alpha.

#### 2. Domain Converter (`pkg/virt-launcher/virtwrap/converter/compute/os.go`)

Extend the firmware auto-selection code path introduced by VEP #241 to
handle ARM64. The domain schema types (`Firmware`, `FirmwareInfo`,
`FirmwareFeature`) and the `UsesFirmwareAutoSelection` field in
`EFIConfiguration` are already available from VEP #241. The converter logic
for ARM64 Secure Boot uses the same `UsesFirmwareAutoSelection` path —
the only difference is the architecture in the `<type>` element, which
libvirt uses to select the correct firmware descriptor.

When `arch == "aarch64"` and `secureBoot == true`:
  - Set `UsesFirmwareAutoSelection = true` in the `EFIConfiguration`
  - The existing auto-selection code path sets `domain.Spec.OS.Firmware`
    and `domain.Spec.OS.FirmwareInfo` with `secure-boot` and
    `enrolled-keys` features
  - Do NOT set `BootLoader` or `NVRam` (libvirt handles these)

When `arch == "aarch64"` and `secureBoot == false`:
  - Continue using the existing explicit `<loader>`/`<nvram>` approach with
    AAVMF firmware (no behavior change)

#### 3. Validation Webhook (`pkg/virt-api/webhooks/validating-webhook/admitters/vmi-create-admitter.go`)

Two validation changes are needed:

**Feature gate check**: When an ARM64 VMI requests `secureBoot: true`, the
webhook must verify that the `ARM64SecureBoot` feature gate is enabled. If
not, reject with a clear error:

```go
if secureBootEnabled(spec.Firmware) && spec.Architecture == "arm64" &&
    !config.ARM64SecureBootEnabled() {
    causes = append(causes, metav1.StatusCause{
        Type:    metav1.CauseTypeFieldValueInvalid,
        Message: "ARM64SecureBoot feature gate is not enabled in kubevirt-config",
        Field:   field.Child("domain", "firmware", "bootloader", "efi", "secureBoot").String(),
    })
}
```

**SMM validation**: Make the existing "SecureBoot requires SMM" check
architecture-aware, since SMM is an x86 concept and does not apply to
ARM64. ARM64 Secure Boot protection is provided by the `uefi-vars` device
on the host side:

```go
if secureBootEnabled(spec.Firmware) && !smmFeatureEnabled(spec.Features) && spec.Architecture != "arm64" {
    causes = append(causes, metav1.StatusCause{
        // ...
    })
}
```

#### 4. EFI Environment Detection (`pkg/virt-launcher/virtwrap/efi/efi.go`)

The current `DetectEFIEnvironment()` function for ARM64 only checks for basic
AAVMF firmware. When using firmware auto-selection for ARM64 Secure Boot,
KubeVirt delegates firmware discovery to libvirt rather than probing for
firmware files directly. Since QEMU, libvirt, and firmware are all shipped
within the virt-launcher image, the availability of Secure Boot support is
determined at image build time. The feature gate guards against enabling the
feature when the virt-launcher image does not yet include the required
components. If libvirt cannot find a matching firmware descriptor at VM
start time, it will return a clear error that surfaces to the user.

#### 5. Manager (`pkg/virt-launcher/virtwrap/manager.go`)

Extend the firmware auto-selection condition from VEP #241 to include ARM64.
The existing VEP #241 logic sets `UsesFirmwareAutoSelection = true` for
standard Secure Boot when `FirmwareAutoSelection` is enabled. ARM64
additionally requires the `ARM64SecureBoot` feature gate — both gates must
be enabled for ARM64 Secure Boot to use firmware auto-selection:

```go
if secureBoot && vmType == efi.None && config.FirmwareAutoSelectionEnabled() {
    if arch == "aarch64" && !config.ARM64SecureBootEnabled() {
        // ARM64 Secure Boot requires both feature gates
        // Reject via webhook, not here
    } else {
        efiConf = &convertertypes.EFIConfiguration{
            SecureLoader:              true,
            UsesFirmwareAutoSelection: true,
        }
    }
} else {
    // Existing explicit-path logic for all other cases,
    // or when the FirmwareAutoSelection feature gate is disabled.
}
```

#### 6. ARM64 Defaults (`pkg/defaults/arm64.go`)

No change required. The default of `SecureBoot = false` for ARM64 remains
appropriate. Users must explicitly opt in to Secure Boot.

## API Examples

### ARM64 VM with Secure Boot Enabled

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: arm64-secureboot-vm
spec:
  architecture: arm64
  domain:
    firmware:
      bootloader:
        efi:
          secureBoot: true
    resources:
      requests:
        memory: 1Gi
    devices:
      disks:
        - name: disk0
          disk:
            bus: virtio
  volumes:
    - name: disk0
      containerDisk:
        image: registry.example.com/arm64-uefi-image:latest
```

### ARM64 VM without Secure Boot (unchanged, existing behavior)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: arm64-vm
spec:
  architecture: arm64
  domain:
    firmware:
      bootloader:
        efi: {}
    resources:
      requests:
        memory: 1Gi
```

### Instance Type Preference with Secure Boot

```yaml
apiVersion: instancetype.kubevirt.io/v1beta1
kind: VirtualMachinePreference
metadata:
  name: arm64-secureboot-preference
spec:
  firmware:
    preferredEfi:
      secureBoot: true
```

## Alternatives

### Alternative A: Explicit uefi-vars Device Configuration

Instead of using libvirt firmware auto-selection, KubeVirt could directly
generate the ROM loader and `uefi-vars-sysbus` device XML:

```xml
<os>
  <type arch='aarch64' machine='virt'>hvm</type>
  <loader type='rom'>/usr/share/edk2/aarch64/QEMU_EFI.qemuvars.fd</loader>
  <varstore template='/usr/share/edk2/aarch64/vars.secboot.json'
            path='/var/lib/libvirt/qemu/varstore/guest.json'/>
</os>
```

**Rejected because:** This duplicates firmware selection logic that libvirt
already handles, hardcodes distribution-specific paths, and requires KubeVirt
to know about the `uefi-vars-sysbus` device internals. It's also more fragile
if firmware packaging changes. VEP #241 has established firmware
auto-selection as the preferred approach for Secure Boot.

### Alternative B: Add AAVMF Secure Boot Firmware Variants

Follow the same pattern as x86_64 by adding `AAVMF_CODE.secboot.fd` /
`AAVMF_VARS.secboot.fd` constants and using pflash.

**Rejected because:** ARM64 Secure Boot does not use pflash-based variable
protection. The `uefi-vars` device approach is fundamentally different, and
there are no pflash-based secure boot firmware builds for aarch64. This
approach would not work.

## Scalability

No scalability impact. The change affects per-VM domain XML generation only.

## Update/Rollback Compatibility

- All changes are additive and opt-in.
- ARM64 VMs without `secureBoot: true` continue to work exactly as before.
- The new libvirt domain XML path (`<os firmware='efi'>`) is only used for
  ARM64 Secure Boot, not for any existing configurations.
- Rolling back KubeVirt will cause ARM64 Secure Boot VMs to fail validation
  (since the older version doesn't support it). Non-Secure-Boot ARM64 VMs
  are unaffected.

## Functional Testing Approach

- **Unit tests**: Verify domain XML generation for ARM64 with and without
  Secure Boot, including the firmware auto-selection XML structure.
- **Unit tests**: Verify that the SMM validation correctly allows ARM64
  Secure Boot without SMM and continues to require SMM for x86_64.
- **Unit tests**: Verify ARM64 defaults remain unchanged (SecureBoot=false).
- **E2E tests**: On ARM64 CI infrastructure with QEMU 10.0+ and appropriate
  firmware packages, boot an ARM64 VM with Secure Boot enabled and verify
  that Secure Boot is active inside the guest (e.g. via
  `mokutil --sb-state`).

## Implementation History

## Graduation Requirements

### Alpha

- [ ] VEP #241 (Firmware Auto-Selection) merged and `FirmwareAutoSelection`
      feature gate at least Alpha
- [ ] `ARM64SecureBoot` feature gate added and registered as Alpha
- [ ] Validation webhook rejects ARM64 `secureBoot: true` when feature gate is
      disabled
- [ ] Validation webhook allows ARM64 Secure Boot without SMM when feature
      gate is enabled
- [ ] Domain converter generates firmware auto-selection XML for ARM64 Secure
      Boot (reusing VEP #241 infrastructure)
- [ ] Unit tests for all changed code paths
- [ ] Documentation updated to mention ARM64 Secure Boot support, the feature
      gate, and host requirements (QEMU 10.0+, libvirt with varstore support,
      edk2-aarch64 firmware)

### Beta

- [ ] `ARM64SecureBoot` feature gate promoted to Beta
- [ ] ARM64 CI lane with Secure Boot testing
- [ ] E2E tests verifying Secure Boot is active inside guest
- [ ] Verified with at least two Linux distributions as guests

### GA

- [ ] CentOS Stream 10 formally supported as virt-launcher base OS
      ([VEP #210](https://github.com/kubevirt/enhancements/issues/210))
- [ ] `ARM64SecureBoot` feature gate removed, feature always enabled
- [ ] Stable for at least 2 minor releases
- [ ] No outstanding bugs related to ARM64 Secure Boot
