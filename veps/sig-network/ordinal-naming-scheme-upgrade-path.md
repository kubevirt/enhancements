# VEP 111: Upgrade Path for VMs Using Ordinal Naming for Secondary Networks

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

KubeVirt leverages the [Multus](https://github.com/k8snetworkplumbingwg/multus-cni) CNI to connect VMs to secondary
networks.
It does this by templating virt-launcher pods with the `k8s.v1.cni.cncf.io/networks` annotation, which Multus uses to
name and attach interfaces.
While KubeVirt now uses a predictable hashed naming scheme for these interfaces, this was not always the case.
In versions prior to v1.0.0, KubeVirt used an ordinal naming scheme (e.g., net1, net2).
To ensure network connectivity survives live migration and version upgrades, KubeVirt preserves this old naming scheme
for any VM that is already using it. For additional detail, please see [The domain mismatch issue](#the-domain-mismatch-issue) bellow.
This design choice, while necessary for compatibility, has left existing older VMs "stuck" on a legacy configuration.
This document outlines a strategy to upgrade these specific VMs, transitioning them from the deprecated ordinal naming
scheme to the current naming scheme without disrupting connectivity.

## Motivation

The legacy ordinal naming scheme creates two significant problems:

1. Increased Maintenance Overhead: Supporting this deprecated scheme complicates the networking codebase, increasing
   testing permutations and long-term engineering effort.
2. Feature Incompatibility: It is fundamentally incompatible with NIC hot-unplug, effectively blocking this feature for
   all VMs that still use it.

This proposal aims to resolve both issues by migrating all affected VMs to the modern naming standard.

## Goals

- Implement an upgrade mechanism from ordinal to hashed naming that avoids a VM restart.
- Reduce overall complexity and improve code maintainability.
- Enable NIC hot-unplug for VMs created on KubeVirt versions older than v1.0.

## Definition of Users

- KubeVirt maintainers
- KubeVirt VM owners

## User Stories

- As a KubeVirt maintainer, I want to remove support for the legacy ordinal naming scheme so that I can reduce code
  complexity and the ongoing maintenance burden.
- As a KubeVirt VM owner, I want an upgrade path to make my older VMs compatible with NIC hot-unplug, as they currently
  use the legacy ordinal naming scheme which blocks this feature.

## Repos

kubevirt/kubevirt

## Design

This design details how to migrate a VM from the legacy ordinal naming scheme to the modern hashed scheme during a
standard live migration, without requiring a VM reboot.
The design leverages the domain mutation hook on the target virt-launcher pod, that was introduced by [VEP#141](https://github.com/kubevirt/enhancements/pull/142).

### Target Pod Network Configuration

The migration controller preserves the ordinal naming scheme in the target pod's `k8s.v1.cni.cncf.io/networks` annotation
if it is present in the source pod.

A new feature gate, `PodSecondaryInterfaceNamingUpgrade`, will be introduced.
If the feature gate is active, the target virt-launcher pod will always be created with the hashed naming scheme
in the networks annotation, irrespective of the source pod's state.

> [!NOTE]
> The virt-handler does not require logic changes, as it identifies the network naming scheme by reading info directly
> from the operating system.

### virt-launcher Network Setup

The virt-launcher performs a network setup process for new VMs or migration targets.
This process includes a [discovery](https://github.com/kubevirt/kubevirt/blob/aad8d79490ca6ca00849e471eb04be74dd1bf6fb/pkg/network/setup/podnic.go#L51)
phase that attempts to find pod interfaces based on their hashed naming scheme, falling back to the older naming scheme
if necessary. Following this change, the fallback can be removed, as VMI and Migration controllers will always create virt-launcher
pods with the hashed naming scheme.

### The Domain Mismatch Issue

This change creates a critical mismatch for tap-based bindings, as for tap-based bindings, KubeVirt derives the names of
tap devices from its associated pod interface name.
The migrating domain XML will reference tap device names based on the source pod's ordinal scheme (e.g., `tap1`),
which will not match the tap device names on the target pod, as they are derived from the new hashed scheme (e.g.,
`tap914f438d88d`).

The mismatch will be resolved by leveraging the domain mutation hook mechanism that will allow us to adjust the domain XML 
on the target virt-launcher pod before the VM is defined.

If the feature gate is enabled, a network domain mutator will be registered to the hook mechanism.
The network domain mutator will scan the domain for interfaces connected to secondary networks that utilize tap-based bindings.
It will then convert any ordinal names found in the target fields to hashed names.

#### Example
Input:

```xml
<!-- Interface connected to pod network -->
<interface type='ethernet'>
  <mac address='02:51:6f:30:80:10'/>
  <target dev='tap0' managed='no'/>
  <model type='virtio-non-transitional'/>
  <mtu size='1430'/>
  <alias name='ua-default'/>
  <rom enabled='no'/>
  <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
</interface>
<!-- Interface connected to secondary network -->
<interface type='ethernet'>
  <mac address='02:51:6f:30:80:11'/>
  <target dev='tap1' managed='no'/>
  <model type='virtio-non-transitional'/>
  <mtu size='1400'/>
  <alias name='ua-sec'/>
  <rom enabled='no'/>
  <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
</interface>
```

Output:
```xml
<!-- Interface connected to pod network -->
<interface type='ethernet'>
  <mac address='02:51:6f:30:80:10'/>
  <target dev='tap0' managed='no'/>
  <model type='virtio-non-transitional'/>
  <mtu size='1430'/>
  <alias name='ua-default'/>
  <rom enabled='no'/>
  <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
</interface>
<!-- Interface connected to secondary network -->
<interface type='ethernet'>
  <mac address='02:51:6f:30:80:11'/>
  <target dev='tapadd93534eeb' managed='no'/>
  <model type='virtio-non-transitional'/>
  <mtu size='1400'/>
  <alias name='ua-sec'/>
  <rom enabled='no'/>
  <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
</interface>
```

### VMI Interface Status Update

Following a successful migration, the VMI controller will reconcile the new virt-launcher pod.
As part of this process, it will update the `VMI.Status.Interfaces[].PodInterfaceName` fields to match the pod's
actual (and now hashed) interface names.

This status update completes the migration to the modern naming scheme and unblocks NIC hot-unplug functionality for the
VM.

## API Examples

N/A

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Scalability

This proposal does not affect scalability.

## Update/Rollback Compatibility

This logic is designed to allow upgrade for VMs defined prior to v1.0 and should not affect newer VMs.

## Functional Testing Approach

The network domain mutator will undergo unit testing.
However, validating this upgrade path via e2e testing presents significant challenges.
It requires provisioning a VM in KubeVirt v0.59 and sequentially performing upgrades for every subsequent version.
Furthermore, because KubeVirt releases are tightly coupled with specific Kubernetes versions, 
the underlying cluster would need to be upgraded in tandem.
Given this complexity and resource overhead, running these tests in upstream CI is not feasible.

## Implementation History

## Graduation Requirements

## Beta

This logic will be introduced in v1.8 with a feature gate in the Beta stage, in order to provide a "kill switch" for
the mechanism.

## Graduation

In case no major issues will be reported, the feature gate will be graduated in v1.9.
The network domain mutator will be removed in v1.11 concurrently with the remaining logic for the ordinal naming scheme.
