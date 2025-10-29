# VEP #16: Use ImageVolume to mount OCI images as volumes in virt-launcher

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

KubeVirt allows running virtual machines using a disk provided by an image, 
a feature known as "container disk".

For more information, refer to this [link](https://kubevirt.io/user-guide/storage/disks_and_volumes/#containerdisk).

Additionally, KubeVirt supports configuring custom kernel and initrd binaries using 
external image. 

More details can be found [here](https://kubevirt.io/user-guide/user_workloads/boot_from_external_source/#booting-from-external-source).

At the time these features were developed, Kubernetes did not have a native method to mount OCI images as volumes in pods. 
As a result, the current implementation includes some hacky operations and workarounds, such as:

- Creating init container to copy the container disk binary.
- Connecting to a socket to determine when virt-handler has finished mounting files to virt-launcher.
- Adding a container to keep the files in the launcher pod.
- Bind-mounting the files from the external images to the compute container’s file system.
- unmounting files when it is no longer needed.
- and more

This enhancement aims to replace the current workaround used for container disks and 
kernel boots with Kubernetes native image volume feature.

More details regarding ImageVolume can be found [here](https://kubernetes.io/docs/tasks/configure-pod-container/image-volumes/).

## Motivation

This enhancement offers several benefits:

- Improves the performance of virt-launcher startup by removing the need for init containers to copy binaries for container
  disks , and avoids the need for communication between the handler and the launcher for bind mounts.
- Simplifies the code and logic by removing a large amount of code and unit tests for the current workaround, and shifts
  the support responsibility to Kubernetes, making it easier to maintain and reducing potential bugs surface.
- Increases security by removing the need for virt-handler to bind mount disk files into the virt-launcher's compute container.
- Simplifies virt-launcher pod manifest by removing unnecessary init containers (such as container-disk-binary, volumecontainerdisk-init,
  volumekernel-boot-volume-init) and containers (volumecontainerdisk, volumekernel-boot-volume).
- Lowers the dependency between virt-handler and virt-launcher, moving us closer to making the virt-launcher image standalone and able to
  run outside of Kubernetes.
- We would no longer depend on the presence of virt-handler to unmount the disks, allowing Kubelet to successfully remove the Pod.

## Goals

- Remove all the current code that handles container disks and kernel boot with the existing workaround.
- Allow users to test virtual machines with the new image volume feature by enabling a feature gate.
- Create a safe upgrade path that supports old virtual machines and eventually moves them to use the 
image volume feature without user intervention.
- Make sure virt-launcher containers don’t get extra privileges, keeping the system secure.

## Non Goals

- This enhancement will not introduce changes to API fields or the API in general.
- Users will not need to be aware of the underlying implementation details of container disks or kernel boot.
- No additional functionality will be added to the existing API.
- The current behavior, from users perspective, will remain unchanged.

## Definition of Users

- Anyone who creates virtual machines with container disk or kernel boot.
- KubeVirt developers who need to develop features and fix bugs based on the current workaround.

## User Stories

### Faster Start-Up
As a KubeVirt user, I want faster virtual machine start-ups, so that I can save time and improve system performance.

### Easier Development for New Contributors
As a new KubeVirt contributor, I want a simpler codebase, so that I can quickly understand how things work and contribute 
to fixing bugs and adding features.

### Better Security
As a KubeVirt user, I want to remove privileged operations like bind mounts from virt-handler.

## Repos

https://github.com/kubevirt/kubevirt

## Design

This design introduces a new method for handling container disks and kernel boot. When a Virtual Machine with a container 
disk or kernel boot is created, the virt-launcher pod will include an imageVolume in its specification, replacing the 
current method that uses additional init containers and sidecar containers.

Virt-handler will no longer be part of this process, and there will be no need to manage bind mounts or search for sockets. 
From the launcher pod’s perspective, it will access the disk or kernel boot files directly from a predefined image path in 
the volume mounts of virt-launcher.

Once the feature gate is graduated, we will ensure the old code is removed from virt-controller and virt-launcher. 
For virt-handler, we will remove the code for initiating new VMs with container disks but will keep the code needed to 
support existing VMs. Once we are confident that all virtual machines are using the new method and there are no upgrade 
risks, we will completely remove the old code for supporting VMs from virt-handler.

## API Examples

As stated in the No Goals section, no API changes are included.

## Alternatives

One alternative to remove the current workaround was explored in [this PR](https://github.com/kubevirt/kubevirt/pull/11845). 
It suggested using shared namespaces between the launcher containers to avoid bind mounts. However, 
this approach is more complicated and may compromise the isolation of the compute container where the domain runs, as discussed 
in [here](https://github.com/kubevirt/kubevirt/pull/11845#issuecomment-2413506306).

Another option would be to introduce a new API field to expose the new implementation. However, this would require adding 
new fields to both the VM and VMI resources and would mean maintaining support for both the old and new implementations 
for a long time. This would involve updating the CRDs versions, making the upgrade path more complex to support old VMs\VMIs, 
and removing the old fields. Manual intervention would be required to migrate VMIs to the new approach, and developers 
would need to adjust many functional tests to the new API. Additionally, this approach would make users aware of implementation 
details, complicating the API.

The issue with this approach is similar to the problem with the vm.spec.running field, which we’ve been unable to remove
due to the breaking change it would cause. This demonstrates why adding new API fields to manage the same behavior is not 
ideal. 


## Scalability

This solution can improve KubeVirt VM scalability by reducing the need for extra containers and privileged operations per VM. 
It will also lower the resources requests and limits by removing the additional containers and their requirements\limits.

## Update/Rollback Compatibility

To make the upgrade path seamless for users, when the feature is graduated, we will temporary continue to support the old 
method in virt-handler for older launcher pods. We will then migrate the old launcher pods to the new imageVolume method 
using live migration. This will update the launcher pod spec to use imageVolumes instead of the old sidecar and init 
containers, and will also update the image to support the new spec.

## Functional Testing Approach

To ensure the new logic works correctly, we will set up a periodic test lane that runs relevant tests with the feature gate 
enabled. This will allow us to investigate, debug, and confirm that the existing and expected behaviors are functioning 
as expected until the feature graduates. This will help us build more confidence in the new implementation.

## Implementation Phases

- [ ] Add the new implementation, protected by a feature gate called ImageVolume that is disabled by default.
- [ ] Add functional tests to exercise live migration for VMIs using the old method and transition to the new implementation, 
include scenarios involving custom kernel boot / initrd and containerdisk.
- [ ] Add a periodic lane to run sig-compute tests once a week with the ImageVolume feature gate enabled.
- [ ] Once the feature is fully graduated, remove all code related to the old implementation in virt-handler, virt-controller, and virt-launcher,
  while still supporting the old virt-launcher in virt-handler.
- [ ] During the upgrade to a version where the feature gate is graduated, ensure all launcher pods that are associated with a live-migratable VMI
  are updated through the live migration process.
- [ ] After three releases, when we are confident that no launcher pods are using the old logic, remove the remaining code in virt-handler.

## Feature lifecycle Phases

### Alpha
- ImageVolume feature gate to be marked as beta in Kubernetes.
- Implement the new method, protected by a feature gate, Make sure all tests pass successfully with the new approach.

### Beta
- Wait for the imageVolume feature gate to be graduated to GA in Kubernetes.
- Ensure that live migration completes successfully by preserving the original container-disk image if the tag is overwritten.

### GA

### Post-GA
- Remove all code for the old method in virt-handler, virt-controller, and virt-launcher, but continue supporting the
  old virt-launchers in virt-handler.
- Remove any remaining code for the old method in virt-handler once it is safe from upgrade perspective.