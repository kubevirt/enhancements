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
To ensure network connectivity survived live migration and version upgrades, KubeVirt preserves this old naming scheme
for any VM that already used it.
This design choice, while necessary for compatibility, has left existing older VMs "stuck" on a legacy configuration.
This document outlines a strategy to upgrade these specific VMs, transitioning them from the deprecated ordinal naming
scheme to the current hashed scheme without disrupting connectivity.

## Motivation

The legacy ordinal naming scheme creates two significant problems:

1. Increased Maintenance Overhead: Supporting this deprecated scheme complicates the networking codebase, increasing
   testing permutations and long-term engineering effort.
2. Feature Incompatibility: It is fundamentally incompatible with NIC hot-unplug, effectively blocking this feature for
   all VMs that still use it.

This proposal aims to resolve both issues by migrating all affected VMs to the modern naming standard.

## Goals

- Implement an upgrade mechanism from ordinal to hashed naming that avoids a VM restart

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

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

### Target Pod Network Configuration

Currently, the target pod's `k8s.v1.cni.cncf.io/networks` annotation preserves the ordinal naming scheme in case it
exists in the source pod.
The target virt-launcher pod will always be created using the hashed naming scheme in its networks annotation,
regardless of the source pod's configuration.

> [!NOTE]
> The virt-handler is unaffected, as it identifies the network naming scheme by reading info directly from the operating
> system.

Since the virt-handler identifies the network naming scheme directly from the info read form the operating system, there
is no need to change it.

### The Domain Mismatch Issue

This change creates a critical mismatch for tap-based bindings, as for tap-based bindings, KubeVirt derives the names of
tap devices from its associated pod interface name.
The migrating domain XML will reference tap device names based on the source pod's ordinal scheme (e.g., `tap1`),
which
will not match the tap device names on the target pod, as they are derived from the new hashed scheme (e.g.,
`tap914f438d88d`).

The mismatch will be resolved by leveraging libvirt's hook mechanism to intercept and mutate the domain XML on the
target virt-launcher pod before the VM is defined.

This will be achieved using a client-server model over a Unix socket, where virt-launcher acts as the server and the
hook script acts as the client.

Proposed Flow:

1. The target virt-launcher process starts, creates a Unix domain socket, and begins listening for incoming connections.
2. As part of the migration, libvirt executes the hook script on the target pod, passing the original domain XML to the
   script's stdin.
3. The hook script connects to the virt-launcher's Unix socket and passes the domain XML from its stdin to the socket.
4. The virt-launcher server receives the XML, parses it, scans for legacy configurations (like ordinal interface names),
   and performs the necessary mutations.
5. virt-launcher sends the mutated domain XML back to the hook script over the same socket connection.
6. The hook script reads the mutated XML from the socket and writes it to its stdout.
7. Libvirt reads the stdout from the hook script and uses this final, modified XML to define the VM on the target.

This process would allow us to perform similar changes in the future that do not change the VM's ABI.

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

<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

The upgrade mechanism and all code supporting the legacy ordinal naming scheme will be deprecated and removed after
three minor versions.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation History

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

### Alpha

### Beta

### GA
