# VEP #NNNN: Your short, descriptive title

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Recently, memory and CPU hotplug was added to KubeVirt.
This allows users to add memory and CPU to a running VM on the fly.

When a VM gets hotplugged, the underlying virt-launcher pod's resources need to be modified accordingly.
Traditionally in Kubernetes, pods are immutable. Once a pod is created, its resource requests and limits cannot be changed.
Therefore, the hotplug feature was implemented by live-migrating the VM which would result in a new virt-launcher pod
with the updated resources.

On the hotplug's original [design proposal](https://github.com/kubevirt/community/blob/main/design-proposals/cpu-hotplug.md#goals)
(that pre-dated the VEP process) it's written:
> Implementation should be achievable today, with Kubernetes APIs that are at least in beta.
> Unfortunately, at the time of writing, the Kubernetes vertical pod scaling API is only alpha.

Fortunately, the in-place pod resize feature was [graduated to beta](https://github.com/kubernetes/enhancements/blob/61abddca34caac56d22b7db48734b7040dc68b43/keps/sig-node/1287-in-place-update-pod-resources/kep.yaml#L40)
in Kubernetes 1.33.
Therefore, Kubevirt should aim to move away from live-migrating the VM on hotplug and instead use the in-place pod resize feature.

## Motivation

With the in-place pod resize feature, the kubelet (through the CRI) can update the pod's resource requests and limits.
This will allow us to avoid live-migrating the VM on hotplug, which saves a lot of resources, reduces downtime and risk
and improves the user experience.

The change should be as transparent to the user as possible, as this is essentially an implementation detail.

## Goals

* Implement in-place pod resize for CPU and memory hotplug.
* Use in-pod resize as a default strategy for hotplug.
* Support hotplug for non-migratable VMs.

## Non Goals

* The user to explicitly decide whether to use in-place pod resize or live-migration (as migration doesn't really makes sense anymore).

## Definition of Users

* VM owners.
* Admins / namespace owners.

## User Stories

* As a user, I want to hotplug CPU and memory to my VM without having to live-migrate it.
* As a user, I want to hotplug CPU and memory to my non-migratable VM.
* As a user, I want to hotplug CPU and memory to my VM in order to hotplug a host device that demands more resources.
* As an admin, I want to save cluster resources and improve performance by avoiding live-migrations.
* As a namespace owner with a ResourceQuota, I want to be able to hotplug CPU and memory to my VMs without having to worry about the quota being exceeded.

## Repos

kubevirt/kubevirt

## Design

Currently, whenever a VM is hotplugged, virt-controller updates a condition that triggers the workload updater controller
which leads to a live-migration of the VM.

With this VEP is implemented, the controller would simply change the pod's resources and wait for them to be applied by kubelet.
In turn, the workload updater controller would avoid live-migrating on this situation.

## API Examples

No API changes are expected.

## Alternatives

An alternative to completely dropping the live-migration update method is to keep it as a secondary option that needs
to be explicitly enabled by the user. This could potentially help in situations where the pod cannot increase its resources
due to node constraints or other reasons.

## Scalability

This should improve scalability dramatically as it reduces the number of live-migrations that need to be performed during
hotplugs.

## Update/Rollback Compatibility

No update/rollback compatibility issues are expected.

## Functional Testing Approach

Hotplug tests should be updated to test the in-place pod resize feature.

## Implementation Phases

<!--
How/if this design will get broken up into multiple phases)
-->

## Feature lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- [ ] Implement in-place pod resize for CPU and memory hotplugs.

### Beta
- [ ] Turn this feature on by default.

### GA
- [ ] Ensure tests are constantly green.