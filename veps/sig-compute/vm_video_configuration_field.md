# VEP #35: Add a Video configuration field for VMs

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

# Overview
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
- 
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

## Feature Stage
We are targeting this feature to be introduced in **Alpha**.

- During Alpha:
   - The `video` field will be **optional**.
   - Users can opt into the new functionality without affecting existing behavior.

Depending on adoption and feedback, we aim to promote the feature to **Beta** in a subsequent release, followed by **GA** once it is stable and widely used.