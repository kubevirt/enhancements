# VEP #241: Use Libvirt Firmware Auto-Selection for EFI Secure Boot

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

Replace KubeVirt's hardcoded OVMF firmware loader paths for EFI Secure Boot
with libvirt's firmware auto-selection feature. Instead of specifying an
explicit `<loader>` path in the domain XML, KubeVirt will use
`<os firmware='efi'>` with `<firmware><feature>` elements, delegating
firmware loader selection to libvirt based on JSON descriptor files shipped
by the `edk2-ovmf` package. The NVRAM path remains explicitly set by
KubeVirt to preserve filename convention and persistent storage
compatibility.

## Motivation

KubeVirt currently hardcodes firmware file names (e.g. `OVMF_CODE.secboot.fd`,
`OVMF_VARS.secboot.fd`) and constructs explicit `<loader>` / `<nvram>` paths
in the domain XML for EFI Secure Boot. This approach has several drawbacks:

1. **Fragile path assumptions**: Firmware file names and locations are
   distribution-specific and change over time. For example, edk2 packaging has
   shifted from `/usr/share/OVMF/` to `/usr/share/edk2/ovmf/` and is moving
   from raw to qcow2 firmware formats.

2. **Duplicated logic**: KubeVirt reimplements firmware selection logic
   (matching secure boot capability, SMM requirements, architecture) that
   libvirt already handles via its firmware descriptor system.

3. **Missed optimizations**: Newer firmware builds (e.g. qcow2-format OVMF)
   offer reduced memory usage and faster startup, but KubeVirt cannot
   automatically pick them up without code changes.

4. **Inconsistency with ARM64**: VEP #227 (ARM64 Secure Boot) already uses
   firmware auto-selection for ARM64 guests. Using the same mechanism for
   x86_64 unifies the code path.

Libvirt's firmware auto-selection feature (available since libvirt 7.2.0) was
designed to solve exactly this problem. The `edk2-ovmf` package in CentOS
Stream 9 already ships the required JSON firmware descriptors that declare
secure boot, enrolled keys, and SMM capabilities.

## Goals

- Use libvirt firmware auto-selection for standard EFI Secure Boot on x86_64.
- Eliminate hardcoded OVMF firmware paths for the Secure Boot case.
- Ensure functional equivalence with the current explicit-path approach.
- Unify the Secure Boot firmware selection mechanism across x86_64 and ARM64.

## Non Goals

- Changing the user-facing API (`spec.domain.firmware.bootloader.efi`).
- Adopting firmware auto-selection for non-Secure-Boot EFI or confidential
  computing VM types (SEV/SEV-ES, SEV-SNP, TDX). See the
  [Why Only Secure Boot?](#why-only-secure-boot) section below for detailed
  rationale.
- Removing the explicit firmware path detection code in `efi.go` entirely
  (it remains needed for the excluded cases above).

## Definition of Users

- **VM Authors**: No change in behavior. The same `secureBoot: true` API
  field produces the same Secure Boot functionality.
- **Cluster Admins**: Should be aware that the internal domain XML changes,
  which could affect custom monitoring or tooling that parses domain XML.
- **KubeVirt Developers**: Benefit from a simpler, more maintainable firmware
  selection code path.

## User Stories

- As a VM Author, I want EFI Secure Boot to work the same way as before with
  no changes to my VM definitions.
- As a KubeVirt Developer, I want the firmware selection logic to be delegated
  to libvirt so that KubeVirt doesn't need to track distribution-specific
  firmware file names and paths.
- As a Cluster Admin, I want KubeVirt to automatically use the best available
  firmware format (raw or qcow2) without requiring KubeVirt code changes when
  the edk2 package is updated.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Current Behavior (Explicit Paths)

For EFI Secure Boot, KubeVirt currently:

1. Probes for firmware files on the filesystem (`OVMF_CODE.secboot.fd`,
   `OVMF_VARS.secboot.fd`) via `DetectEFIEnvironment()` in `efi.go`.
2. Constructs explicit `<loader>` and `<nvram>` XML with those paths.
3. Manages the NVRAM file path at a KubeVirt-controlled location.

Resulting domain XML:

```xml
<os>
  <type arch='x86_64' machine='pc-q35-rhel10.2.0'>hvm</type>
  <loader readonly='yes' secure='yes' type='pflash'>/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd</loader>
  <nvram template='/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd'>/var/run/kubevirt-private/libvirt/qemu/nvram/vmi_VARS.fd</nvram>
</os>
```

### Proposed Behavior (Firmware Auto-Selection)

For standard EFI Secure Boot (no SEV/SNP/TDX), KubeVirt will instead:

1. Set `<os firmware='efi'>` with a `<firmware><feature>` element requesting
   `enrolled-keys`.
2. Let libvirt match these requirements against its firmware descriptor files.
3. Let libvirt resolve the loader path and NVRAM template.
4. Continue to explicitly set the `<nvram>` path via `PathForNVram()` to
   preserve NVRAM filename convention and directory compatibility (see
   [NVRAM Path Handling](#nvram-path-handling) below).

Resulting domain XML (as submitted to libvirt):

```xml
<os firmware='efi'>
  <type arch='x86_64' machine='pc-q35-rhel10.2.0'>hvm</type>
  <firmware>
    <feature enabled='yes' name='enrolled-keys'/>
  </firmware>
  <nvram format='raw'>/var/run/kubevirt-private/libvirt/qemu/nvram/vmi_VARS.fd</nvram>
</os>
```

Libvirt resolves this against the `30-edk2-ovmf-x64-sb-enrolled.json`
firmware descriptor from the `edk2-ovmf` package, which declares:

```json
{
  "description": "OVMF with SB+SMM, SB enabled, MS certs enrolled",
  "features": ["acpi-s3", "enrolled-keys", "requires-smm", "secure-boot", "verbose-dynamic"],
  "mapping": {
    "executable": { "filename": "/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd" },
    "nvram-template": { "filename": "/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd" }
  }
}
```

The resulting active domain XML (after libvirt processes it) contains the
same loader and NVRAM paths as before, but they were resolved by libvirt
rather than hardcoded by KubeVirt.

### Why Only Secure Boot?

This VEP intentionally limits the scope to standard EFI Secure Boot. The
other EFI modes are excluded for specific technical reasons:

#### Non-Secure-Boot EFI

KubeVirt's `DetectEFIEnvironment()` has fallback logic that cannot be
replicated through firmware auto-selection:

```go
if code == "" {
    // The combination (EFICodeSecureBoot + EFIVars) is valid
    // for booting in EFI mode with SecureBoot disabled
    code = codeWithSB
}
```

If `OVMF_CODE.fd` is not present but `OVMF_CODE.secboot.fd` is, KubeVirt
uses the Secure Boot code ROM with Secure Boot disabled in the loader
(`secure='no'`). This is a valid configuration that libvirt's auto-selection
would not produce — it would instead fail to find a matching non-Secure-Boot
descriptor.

Additionally, the firmware descriptor `50-edk2-ovmf-x64-nosb.json` declares
`amd-sev` and `amd-sev-es` features. Requesting `<feature enabled='no'
name='secure-boot'/>` could still match this SEV-capable descriptor when a
non-SEV firmware is intended, introducing ambiguity.

The current explicit-path approach works well for non-Secure-Boot EFI and
is not affected by the firmware naming/format changes that motivate this VEP
(since `OVMF_CODE.fd` / `OVMF_VARS.fd` are stable names).

#### SEV/SEV-ES

KubeVirt uses `OVMF_CODE.cc.fd` (the confidential-computing-specific OVMF
build) for SEV/SEV-ES guests. This firmware is purpose-built for confidential
computing with SEV-specific initialization and lacks SMM support.

The CentOS Stream 9 `edk2-ovmf` package does **not** ship a firmware
descriptor for `OVMF_CODE.cc.fd`. The only descriptor with `amd-sev` /
`amd-sev-es` features is `50-edk2-ovmf-x64-nosb.json`, which maps to the
generic `OVMF_CODE.fd` — a different firmware binary. Using auto-selection
for SEV would silently select the wrong firmware.

#### SEV-SNP and TDX

These use stateless firmware (`type='rom'`, no NVRAM):
- SEV-SNP: `OVMF.amdsev.fd`
- TDX: `OVMF.inteltdx.fd` / `OVMF.inteltdx.secboot.fd`

While firmware descriptors exist for these (`60-edk2-ovmf-x64-amdsev.json`,
`60-edk2-ovmf-x64-inteltdx.json`), these VM types have additional device
and launch security configuration requirements beyond firmware selection.
Changing the firmware selection mechanism for confidential computing VMs
carries higher risk with minimal benefit, since the firmware file names
are stable and purpose-specific.

These cases could be explored in a future follow-up VEP once the Secure Boot
auto-selection is proven stable.

### Scope of Change

| VM Configuration | Current | Proposed |
|---|---|---|
| EFI + Secure Boot (standard) | Explicit paths | **Firmware auto-selection** |
| EFI without Secure Boot | Explicit paths | Explicit paths (unchanged) |
| EFI + SEV/SEV-ES | Explicit paths | Explicit paths (unchanged) |
| EFI + SEV-SNP | Explicit paths (stateless) | Explicit paths (unchanged) |
| EFI + TDX | Explicit paths (stateless) | Explicit paths (unchanged) |

### NVRAM Path Handling

With firmware auto-selection, if no explicit `<nvram>` element is provided,
libvirt determines the NVRAM path based on its `nvram_dir` configuration in
`qemu.conf` and derives the filename from the domain name. This creates two
divergences from KubeVirt's current behavior that must be addressed.

#### NVRAM Directory

KubeVirt explicitly sets the NVRAM path via `PathForNVram()`:

- Root VMs: `/var/lib/libvirt/qemu/nvram/<vmi>_VARS.fd`
- Non-root VMs: `/var/run/kubevirt-private/libvirt/qemu/nvram/<vmi>_VARS.fd`

For root VMs, libvirt's default `nvram_dir` (`/var/lib/libvirt/qemu/nvram/`)
matches. For non-root VMs, the default diverges from KubeVirt's expected
path. The non-root `qemu.conf` copy at
`/var/run/kubevirt-private/libvirt/qemu.conf` must be updated to set
`nvram_dir = "/var/run/kubevirt-private/libvirt/qemu/nvram"` explicitly,
ensuring the NVRAM file lands inside the backend storage mount used by
`rendervolumes.go` for persistent EFI.

#### NVRAM Filename Convention

KubeVirt constructs NVRAM filenames as `<vmi.Name>_VARS.fd` (using only the
VMI name), but libvirt's domain name follows the `<namespace>_<vmi-name>`
convention. If libvirt generates the filename from the domain name, a VM
that previously had NVRAM created via explicit paths would get a new file
from template instead of reusing its existing one:

- Existing NVRAM (explicit path): `my-vm_VARS.fd`
- Auto-selected NVRAM (domain name): `default_my-vm_VARS.fd`

This would silently reset the guest's EFI state (enrolled Secure Boot keys,
boot order, etc.) on the first restart after the feature gate is enabled.

#### Solution: Explicit NVRAM Path with Auto-Selected Loader

To avoid both divergences, KubeVirt will continue to explicitly set the
`<nvram>` element in the domain XML with the path constructed by
`PathForNVram()`, even when using firmware auto-selection for the loader.
Libvirt supports combining `<os firmware='efi'>` with an explicit `<nvram>`
path — the auto-selection applies to the `<loader>` while the NVRAM path
remains under KubeVirt's control:

```xml
<os firmware='efi'>
  <type arch='x86_64' machine='pc-q35-rhel10.2.0'>hvm</type>
  <firmware>
    <feature enabled='yes' name='enrolled-keys'/>
  </firmware>
  <nvram format='raw'>/var/run/kubevirt-private/libvirt/qemu/nvram/my-vm_VARS.fd</nvram>
</os>
```

The `format='raw'` attribute ensures that libvirt treats the existing NVRAM
file as raw format even if the auto-selected firmware descriptor prefers
qcow2. Without this, an upgrade could silently corrupt or reset the NVRAM
contents if the descriptor's preferred format doesn't match the existing
file.

This ensures:

- The NVRAM file location is unchanged regardless of feature gate state.
- Existing VMs retain their EFI state across restarts when the feature gate
  is enabled.
- The persistent EFI backend storage mount at `PathForNVram()` continues to
  work correctly for both root and non-root VMs.

### Feature Gate

A new `FirmwareAutoSelection` feature gate guards this change. When
disabled (default during Alpha), the existing explicit-path behavior is
used for all EFI configurations. When enabled, standard EFI Secure Boot
(not SEV/SNP/TDX) uses firmware auto-selection.

The feature gate state is propagated to virt-launcher via a
`--firmware-auto-selection` CLI flag, following the established pattern
used by `--libvirt-hook-server-and-client` and `--upgrade-ordinal-ifaces`.
The `virt-controller` checks the feature gate and conditionally appends
the flag to the virt-launcher command in `template.go`.

The flag is checked in `manager.go` when constructing the
`EFIConfiguration`. If the flag is not set or the VM uses confidential
computing, the existing `DetectEFIEnvironment()` path is used unchanged.

```go
if secureBoot && vmType == efi.None && l.firmwareAutoSelectionEnabled {
    log.Log.Infof("Using firmware auto-selection for EFI Secure Boot")
    efiConf = &convertertypes.EFIConfiguration{
        SecureLoader:              true,
        UsesFirmwareAutoSelection: true,
    }
} else {
    // Existing explicit-path logic for all other cases,
    // or when the flag is not set.
}
```

### Code Changes

1. **`pkg/virt-config/featuregate/active.go`**: Register
   `FirmwareAutoSelection` feature gate (Alpha, default disabled).

2. **`pkg/virt-config/feature-gates.go`**: Add
   `FirmwareAutoSelectionEnabled()` method to `ClusterConfig`.

3. **`pkg/virt-controller/services/template.go`**: Conditionally append
   `--firmware-auto-selection` to the virt-launcher command when the
   feature gate is enabled.

4. **`cmd/virt-launcher/virt-launcher.go`**: Add `--firmware-auto-selection`
   pflag; pass the value to `NewLibvirtDomainManager()`.

5. **`pkg/virt-launcher/virtwrap/api/schema.go`**: Add `Firmware` attribute
   and `FirmwareInfo`/`FirmwareFeature` types to the `OS` struct.

6. **`pkg/virt-launcher/virtwrap/converter/types/converter-context.go`** and
   **`pkg/virt-launcher/virtwrap/converter/compute/os.go`**: Add
   `UsesFirmwareAutoSelection` field to `EFIConfiguration`.

7. **`pkg/virt-launcher/virtwrap/converter/compute/os.go`**: When
   `UsesFirmwareAutoSelection` is true, set firmware auto-selection XML
   instead of explicit loader. Continue to explicitly set the `<nvram>`
   path via `PathForNVram()` to preserve filename convention and directory
   compatibility.

8. **`pkg/virt-launcher/virtwrap/manager.go`**: Add
   `firmwareAutoSelectionEnabled` field to `LibvirtDomainManager`; check
   the field when constructing `EFIConfiguration` for standard Secure Boot.

9. **`pkg/virt-launcher/virtwrap/converter/converter.go`**: Pass through the
   new field in `convertEFIConfiguration()`.

## API Examples

No user-facing API changes. The existing API is unchanged:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
spec:
  domain:
    firmware:
      bootloader:
        efi:
          secureBoot: true
```

## Alternatives

### Alternative A: Keep Explicit Paths

Continue using hardcoded firmware file names. This works today but becomes
increasingly fragile as edk2 packaging evolves (e.g. qcow2 format, new file
naming conventions).

**Rejected because:** It duplicates logic libvirt already handles, and the
ARM64 Secure Boot VEP (#227) has already established firmware auto-selection
as the preferred approach.

### Alternative B: Auto-Selection for All EFI Modes

Adopt firmware auto-selection for all EFI configurations (including
non-Secure-Boot and confidential computing). This would be a larger change
with more risk — see [Why Only Secure Boot?](#why-only-secure-boot) for
the specific technical blockers for each excluded case.

**Deferred:** Could be pursued as a follow-up once the Secure Boot case is
proven stable and missing firmware descriptors (e.g. for `OVMF_CODE.cc.fd`)
are added to `edk2-ovmf`. See also [Future Work](#future-work) below for
how CentOS Stream 10 edk2 updates could expand the scope.

### Alternative C: No Feature Gate

Since this is an internal implementation change with no user-facing API
impact, skip the feature gate and ship it directly.

**Considered but not recommended:** While the change is functionally
equivalent, the NVRAM path handling difference and potential impact on
migration/persistence workflows warrant a safety net during initial rollout.

## Scalability

No scalability impact. The change affects per-VM domain XML generation only.
Libvirt's firmware descriptor matching is a fast in-memory operation.

## Update/Rollback Compatibility

- **Upgrade**: New VMs will use firmware auto-selection. Existing running VMs
  are not affected until they are restarted.
- **Rollback**: If KubeVirt is rolled back to a version without this change,
  VMs will revert to using explicit firmware paths on next start. This is
  safe because the NVRAM content is the same regardless of how the firmware
  was selected.
- **NVRAM continuity**: KubeVirt continues to set the NVRAM file path
  explicitly via `PathForNVram()`, so enabling or disabling the feature gate
  does not change the NVRAM file location. Existing VMs retain their EFI
  state (enrolled Secure Boot keys, boot order, etc.) across restarts
  regardless of the feature gate setting.
- **Migration**: The source and target virt-launcher versions should match
  (as is already required). Both sides will use the same firmware selection
  method.

## Functional Testing Approach

- **Unit tests**: Verify domain XML generation with firmware auto-selection
  enabled produces correct `<os firmware='efi'>` with feature elements.
- **Unit tests**: Verify non-Secure-Boot EFI and SEV/SNP/TDX continue to
  use explicit paths.
- **E2E tests**: Existing Secure Boot e2e tests validate that Secure Boot
  is active inside the guest — these should pass without modification,
  confirming functional equivalence.

## Implementation History

- 2026-03-23: VEP PR opened (https://github.com/kubevirt/enhancements/pull/242)
- 2026-04-02: Implementation PR opened (https://github.com/kubevirt/kubevirt/pull/17263)

## Graduation Requirements

### Alpha

- [ ] Feature gate `FirmwareAutoSelection` guards the change
- [ ] Standard EFI Secure Boot uses firmware auto-selection when gate enabled
- [ ] Non-Secure-Boot EFI and SEV/SNP/TDX unchanged
- [ ] NVRAM path explicitly set in domain XML to preserve filename convention
- [ ] NVRAM path compatibility verified for both root and non-root VMs
- [ ] Unit tests for new code paths
- [ ] Existing Secure Boot e2e tests pass

### Beta

- [ ] Feature gate enabled by default
- [ ] Migration between virt-launcher versions verified
- [ ] Persistent EFI NVRAM compatibility verified
- [ ] No regressions reported during Alpha

### GA

- [ ] Feature gate removed, auto-selection always used for Secure Boot
- [ ] Stable for at least 2 minor releases

## Future Work

### CentOS Stream 10 and the `uefi-vars` Path

CentOS Stream 10's latest `edk2-ovmf` build (`20251114-5.el10`) introduces
two new firmware descriptors that use the `uefi-vars` device — the same
mechanism used for ARM64 Secure Boot in VEP #227:

- `90-edk2-ovmf-qemuvars-x64-sb-enrolled.json` — Secure Boot with MS certs,
  using `OVMF.qemuvars.fd` + `vars.secboot.json`
- `91-edk2-ovmf-qemuvars-x64-sb.json` — Secure Boot capable but disabled,
  using `OVMF.qemuvars.fd` + `vars.blank.json`

These descriptors declare the `host-uefi-vars` feature, use ROM-type loading
(not pflash), and store UEFI variables in a JSON varstore on the host —
eliminating the pflash NVRAM file entirely. With the VEP #210 (CentOS Stream
10 Support) transition, this opens the door to:

1. **Unifying x86_64 and ARM64 Secure Boot** onto the same `uefi-vars`
   mechanism, eliminating pflash-based NVRAM management for Secure Boot VMs.
2. **Non-Secure-Boot EFI via auto-selection** using `91-*-qemuvars-sb.json`,
   sidestepping the `OVMF_CODE.fd` fallback logic concern described in this
   VEP's [Why Only Secure Boot?](#why-only-secure-boot) section.

### Remaining Blockers

The following cases still cannot use firmware auto-selection regardless of
CentOS Stream version, and would need upstream edk2 packaging changes:

- **SEV/SEV-ES**: `OVMF_CODE.cc.fd` (the confidential computing OVMF build)
  is shipped in the RPM but has no firmware descriptor. Auto-selection would
  silently pick the wrong firmware (`OVMF_CODE.fd` via `50-*-nosb.json`).
  This requires a new descriptor to be added to the `edk2-ovmf` package
  upstream.

These extensions are out of scope for this VEP and should be pursued as
separate follow-up proposals once the base auto-selection approach is proven
stable.
