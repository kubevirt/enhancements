# VEP #35: Add a Video configuration field for VMs

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

# Overview
We want to allow VM-owners to explicitly set the video device type when needed.

## Motivation
In KubeVirt, the default video device type for AMD64 architectures is VGA for BIOS-based VMs and Bochs for EFI-based VMs.
For Arm and s390x architectures, we have transitioned to using the virtio video device.
The use of VGA/Bochs can impose limitations on video functionality, such as restricted display resolution settings, particularly for Windows virtual machines.
However, maintaining compatibility with older guest operating systems (e.g., RHEL 5/6) that rely on VGA/Bochs remains important.
To balance modernization with backward compatibility, KubeVirt continues to use VGA for legacy BIOS-based workloads.

## Goals
Ensuring VM-owners won't have a limited resolution experience with their VMs

## Definition of Users
VM owners who require specific video resolutions that are unavailable when using VGA or Bochs as the video device.

## User Stories
As an AMD64 VM owner, I want my VM to provide high-resolution display settings when accessed via VNC
or other remote access methods, so that I have a better user experience when working with graphical applications.

## Repos
- [KubeVirt](https://github.com/kubevirt/kubevirt)
- [common-templates](https://github.com/kubevirt/common-templates)
- [common-instancetypes](https://github.com/kubevirt/common-instancetypes)


## Design

### Option 1: Default to Virtio Instead of Bochs for UEFI VMs
#### Description
Instead of relying on `Bochs` as the default video device for `AMD64` architectures, 
KubeVirt would transition to `virtio` as the new default across all architectures.
This change would provide better video performance, higher resolutions,
and improved compatibility with modern operating systems,especially Windows VMs.

**Pros:**
- **Reduced Configuration:** Users won't need to manually switch to virtio for better video performance.

**Cons:**
- **Backward Compatibility:** we might need to adjust a fall-back logic to maintain full backward-computability

### Option 2 VM-Level Configuration Field

#### Description
Add a `Video` struct under `spec.template.spec.domain.devices` in the VM template schema.
This struct will include a single field, `type`, to specify the desired video device type.

The current behavior relies on the `autoattachGraphicsDevice` field, where:
1. If `autoattachGraphicsDevice` is not specified or is set to `true`, the current logic in the virt-launcher determines the video device type.
2. If `autoattachGraphicsDevice` is set to `false`, no graphics or video devices are attached.

The proposed change ensures that users can explicitly set the `type` for the video device **only if `autoattachGraphicsDevice` is not explicitly set to `false`**. This constraint will be enforced via the validation webhook and ensures that the new field does not conflict with existing configurations.

The architecture-specific logic will continue to manage the video device type configurations.

#### Implementation Logic
1. If `autoattachGraphicsDevice` is set to `false`, the `Video` field must not exist. This will be validated via the webhook.

At the `addGraphicsDevice` method:
2. If `autoattachGraphicsDevice` is set to `false`, no graphics or video devices are attached.
3. If `Video` is explicitly specified in the VM spec, the video type from the spec is used.
4. If `autoattachGraphicsDevice` is not specified or is `true`:
   - Use Bochs for EFI guests on AMD64.
   - Use VGA for BIOS guests on AMD64.

The same hierarchy will apply for other architectures.

#### Pros and Cons

**Pros:**
- **Granular Control:** Enables precise configuration for each VM.

**Cons:**
- **New API:** Introducing additional fields that need maintenance and testing.

### Option 3: Annotation-Based Configuration
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
Overhead of the virt-launcher/qemu may be impacted.

## Update/Rollback Compatibility
Currently, the virt-launcher arch-converter manages the default video type.
We only provide a field for the user to specify explicitly if they want something else like virtio, so backward compatibility isn't affected.

## Functional Testing Approach
* Create a VM with video.type set to `virtio`, Validate that the guest contains virtio drivers, and expect launch successfully.

# Implementation Phases
1. **Phase 1: Introduce New Field to Allow Users to Explicitly Set Their Desired Video Type**
    - Add logic to the validation webhook to prevent the creation of VMs where `Video` is specified and `autoattachGraphicsDevice` is explicitly set to `false`.
    - Add logic in the virt launcher to check the field's existence at `addGraphicsDevice`, before setting video device (virtio/vga).
    - Write unit and functional tests for the new behavior.

2. **Phase 2 Documentation**
    - Update KubeVirt documentation to include examples of configuring the video device type.
