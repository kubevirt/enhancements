# VEP #NNNN: Your short, descriptive title

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Currently, KubeVirt uses noop init containers to fetch and report the digest of `containerDisk` and kernel boot images. This ensures that during live migration, the VM uses the exact same files from the original container disk/kernel boot image on the target pod, even if the image tag has changed.

With Kubernetes [ImageVolumeWithDigest feature](https://github.com/kubernetes/enhancements/issues/5365), the volume status exposes the image digest. This allows KubeVirt to avoid the workaround.

This VEP proposes eventually removing the noop init containers and relying directly on the digest from the ImageVolume volume status.This behavior will be protected by a KubeVirt feature gate with the same name as the Kubernetes feature gate.

## Motivation

- Simplify `virt-launcher` pod manifests by removing unnecessary init containers.
- Reduce CPU and memory usage for `virt-launcher` pods.
- Improve `virt-launcher` startup time by eliminating these init containers.
- Simplify maintenance of `virt-controller` code.
- Align live migration with native Kubernetes functionality, improving reliability.

## Goals

- Remove noop init containers used for digest fetching.
- Use the digest from the ImageVolume volume status for live migration.
- Maintain backward compatibility for existing VMs until the new approach is fully adopted.


## Non Goals

- No changes to VM or VMI API fields.
- No additional functionality beyond digest reporting and live migration support.

## Definition of Users

- KubeVirt users running VMs with `containerDisk` or custom kernel/initrd images.
- KubeVirt developers maintaining `virt-launcher` and `virt-handler`.

## User Stories


**Simplified VM Pods**  
As a KubeVirt user, I want `virt-launcher` pods without extra init containers to reduce resource usage and simplify the pod.

**Easier Maintenance**  
As a developer, I want to remove workaround code so that `virt-launcher` logic is easier to understand and maintain.

## Repos

- `kubevirt/kubevirt`

## Design

- Update live migration logic to rely on the digest from the volume status if the `ImageVolumeWithDigest` feature gate is enabled. 
- Remove noop init containers previously used for digest fetching. Once the feature reaches GA, all noop init containers code will be removed entirely.

## API Examples

No changes to VM or VMI API fields are required.


## Alternatives

- Keep the noop init containers

## Scalability

- Reduces resource consumption per `virt-launcher` pod.


## Update/Rollback Compatibility

- Older `virt-launcher` pods continue to function.


## Functional Testing Approach

- check that digest is added to target virt-launchers pods without init containers on the source virt-launchers

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

**Alpha**
- Implementation reads digest from ImageVolume status and use it during live migration.

**Beta**

**GA**

**POST-GA**
- Once it is safe remove all noop init containers and old workaround code.
