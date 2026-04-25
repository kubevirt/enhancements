# VEP #35: Add a Video configuration field for VMs

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.6.0
- This VEP targets beta for version: v1.7.0
- This VEP targets GA for version: v1.9.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview
- We want to allow VM-owners to explicitly set the video device type when needed.

## Motivation
In KubeVirt, the default video device type for AMD64 architectures is VGA for BIOS-based VMs and Bochs for EFI-based VMs.
For Arm and s390x architectures, we have transitioned to using the `virtio` video device.

In other words, the video device that is chosen for VMs is implicit and unclear.
Different video devices have pros and cons and fit to different use-cases.
For example, a guest with `virtio` drivers installed could enjoy from a `virtio` device which is generally faster and optimized for virtualization.
Legacy guests, or guests with no `virtio` drivers, can use VGA for compatibility, and so on.

With this proposal, the VM creator could adapt the video deceive to their needs.

## Goals
- Allowing VM-owners to choose `virtio` or any other desired video device to override the existing default one.

## Definition of Users
- VM owners who need to select a specific video device for their virtual machines to match their workload requirements.

## User Stories
- As an VM owner, I want my VM to provide high-resolution display, so that I can use high resolution
  when I access the VM display via an in-guest graphical remote display mirroring access method like an in guest VNC server

- As a Windows 11 on ARM user, I like to install my VM using the ramfb display adapter,
  and replace it by the `virtio` adapter after I installed the `virtio` driver

- As a VM creator with legacy guest or guest with no `virtio` drivers, I want to rely on VGA.
- As a VM creator running a guest with `virtio` drivers, I want to enjoy from an optimized virtualization-aware video device which is generally faster and lighter on resources.


## Repos
- [KubeVirt](https://github.com/kubevirt/kubevirt)
- [common-templates](https://github.com/kubevirt/common-templates)
- [common-instancetypes](https://github.com/kubevirt/common-instancetypes)


## Design

### Preferred Option: Add `Video` Configuration Field to VM API [This approach was chosen to implement]

#### Description
Introduce a structured Video field under spec.template.spec.domain.devices with the following optional attributes:
```yaml
video:
  type: virtio         # Optional: virtio, bochs, vga, etc.
```
This approach extends the API to provide declarative control over video settings.

#### Integration with Existing Behavior:
* If `autoattachGraphicsDevice` is set to `false`, no video devices will be attached, and the video field must not be present.
* If `video.type` is provided, it will override the default video device model.
* Webhook will validate that `video` is only set if `autoattachGraphicsDevice` is true or unset, and is valid with the relevant arch.
* Architecture-specific logic will continue to provide sensible defaults unless overridden.

The same hierarchy will apply for other architectures.

#### Pros and Cons

**Pros:**
- **Granular Control:** Enables precise configuration for each VM.

**Cons:**
- **New API:** Introducing additional fields that need maintenance and testing.

## Alternatives Considered:

### Option 1: Default to Virtio Instead of Bochs for UEFI VMs - [Ruled out due to possible compatibility issues]
#### Description
Instead of relying on `Bochs` as the default video device for `AMD64` architectures, 
KubeVirt would transition to `virtio` as the new default across all architectures.
This change would provide higher resolutions, and improved compatibility with modern operating systems, especially Windows VMs.

**Pros:**
- **Reduced Configuration:** Users won't need to manually switch to virtio for better video performance.

**Cons:**
- **Backward Compatibility:** we need to adjust a fall-back logic to maintain full backward-computability
- **pre-requisite driver** `virtio` driver must be installed for `virtio` video to enable the benefits over bochs
- The hard coded `virtio` would not enable the windows on arm user story

### Option 2: Annotation-Based Configuration
#### Description
This approach proposes introducing an annotation, kubevirt.io/video-device,
which allows users to specify the video device type at the VM level without modifying the core API.

**Pros:**
- **No API Changes:** Avoids modifying the VM schema.
- **Flexible:** Can be easily removed or changed in future releases without API versioning concerns.

**Cons:**
- **Lack of Visibility and Transparency:** Using an annotation for domain-level configuration makes it harder for users 
to discover and understand its impact. 
Unlike structured API fields, annotations are not well-documented in API references or validation mechanisms.

- **Potential for Unintended Overrides:** Since annotations are loosely enforced,
- they could create inconsistencies if users unknowingly override expected defaults.

### API Examples

#### Video Device Configured Explicitly with an API
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
   name: vm
spec:
   template:
      spec:
         domain:
            devices:
               video:
                  type: virtio
```

#### Video Device Configured With Annotation
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
   name: vm
annotations:
  kubevirt.io/video-device: "virtio"
```

## Scalability
Overhead of the virt-launcher/qemu will be measured and adjusted accordingly.

## Update/Rollback Compatibility
Currently, the virt-launcher arch-converter manages the default video type.
We only provide a field for the user to specify explicitly if they want something else like virtio, so backward compatibility isn't affected.

## Functional Testing Approach
* Create a VM with video.Type set to `virtio` and expect launch successfully.

## Implementation History

- 2025-05-08: Initial VEP merged. PR: https://github.com/kubevirt/enhancements/pull/36
- 2025-06-17: Alpha implementation merged (feature gated under `VideoConfig`). PR: https://github.com/kubevirt/kubevirt/pull/14673
- 2025-09-04: VEP updated with Beta and GA graduation criteria. PR: https://github.com/kubevirt/enhancements/pull/87
- 2025-10-28: Promoted `VideoConfig` feature gate from Alpha to Beta. PR: https://github.com/kubevirt/kubevirt/pull/15939

## Graduation Requirements

### Alpha

- [x] Feature gate `VideoConfig` guards all code changes
- [x] The `video` field is optional
- [x] Users can opt into the new functionality without affecting existing behavior

### Beta

- [x] Document supported video models per architecture, including multi-head
  support where applicable
- [x] Test that Windows guests (including Windows 11 on ARM) can take advantage
  of the `virtio` adapter to achieve higher resolutions
- [x] Explore and measure the overhead of each video model to provide guidance
  for users
- [x] Add the missing documentation from Alpha into the USER_GUIDE, with
  practical examples and usage notes
- [x] Support matrix documented (see below)

### GA

- [ ] The `video` configuration field is considered stable and fully supported
- [ ] Feature gate `VideoConfig` is removed and functionality is enabled by default
- [ ] **API Stability:** The `video` field under `spec.template.spec.domain.devices` is part of the stable VM API. Any changes follow Kubernetes API deprecation policies.
- [ ] **Default Behavior:** Architecture-specific defaults (e.g., VGA/Bochs for AMD64, virtio for Arm/s390x) remain in place if no `video` field is specified. Explicit configuration always overrides defaults.
- [ ] **Validation & Documentation:** All supported video device models are documented per architecture. The API rejects unsupported combinations (e.g., devices not available for the VM's architecture). User guides provide compatibility matrices and best-practice recommendations.
- [ ] **Backward Compatibility:** Existing VMs that do not specify a `video` field continue to behave as before. No migration or updates are required.
- [ ] **Testing:** The feature is covered by unit tests validating correct video device assignment and rejection of unsupported models per architecture.

## Support Matrix

The following matrix summarizes the current default and supported video devices across architectures, firmware types, and guest OS categories.  
This will serve as a reference for VM creators when selecting a video device.

| Architecture | Firmware (BIOS/EFI) | Guest OS Type   | Default Video Device | Override Options                                                            | Notes                                                                             |
|--------------|---------------------|-----------------|----------------------|-----------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| AMD64        | BIOS                | Linux / Windows | VGA                  | vga, cirrus, virtio (requires drivers), virtio-vga (fallback), ramfb, bochs | VGA is chosen for legacy compatibility.                                           |
| AMD64        | EFI                 | Linux / Windows | Bochs                | vga, cirrus, virtio (requires drivers), virtio-vga (fallback), ramfb, bochs | Modern OSs can use virtio for higher resolutions.                                 |
| ARM64        | BIOS / EFI          | Linux           | Virtio               | virtio, ramfb                                                               | No distinction between BIOS/EFI. ramfb is mainly used for installation workflows. |
| ARM64        | BIOS / EFI          | Windows 11      | Virtio               | virtio, ramfb                                                               | ramfb is often required for installer; switch to virtio after drivers are loaded. |
| s390x        | BIOS / EFI          | Linux           | Virtio               | virtio                                                                      | Only virtio is supported; ramfb not available.                                    |

### Key Points
- **AMD64**
    - **BIOS VMs** default to **VGA**.
    - **EFI VMs** default to **Bochs**.
    - Both can be switched to `virtio` if drivers are present.
    - Without drivers, fallback is `virtio-vga` to preserve compatibility.

- **ARM64**
    - Default is **virtio** regardless of BIOS/EFI.
    - `ramfb` can be used for installer workflows (e.g. Windows on ARM), then replaced by virtio.

- **s390x**
    - Default is **virtio**.
    - `ramfb` is **not supported**.
