# VEP #58: AMD SEV-SNP enablement for Confidential Compute

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->
This enhancement is about adding support for the AMD Secure Encrypted Virtualization with Secure Nested Paging (SEV-SNP) in Kubevirt. SEV-SNP provides enhanced security for virtual machines by encrypting VM memory and protecting against memory integrity attacks, this builds upon the SEV/SEV-ES capabilities. SEV is currently supported in KubeVirt, and this VEP proposes to build on top of this existing feature to integrate SEV-SNP support.

## Motivation

<!--
Why this enhancement is important
-->
KubeVirt supports SEV and SEV-ES for memory encryption, but lacks SEV-SNP enahnced memory integrity protection. Organizations are now asking for workloads to include the strongest confidential computing secruity guarantees.

## Goals

<!--
The desired outcomeo
-->
- **Enable SEV-SNP VM Deployment**: Provide users the ability to deploy VMs that are utilizing AMD SEV-SNP technology. 
- **Extend the existing SEV Feature**: Currently the SEV sits behind the `WorkloadEncryptionSEV` adding SEV-SNP to feature is the next logical step. 
- **Extend Existing SEV Infrastructure**: Use the existing SEV node labeling, schedulin, and domain generation infrastructure.

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->
- **Intel TDX Support**: This proposal is targeted only to AMD SEV and enabling Intel TDX support shold targeted in a seperate VEP.
- **Custom Attestation Services**: Only focus on the enabling the capabilities through the standard interfaces and not building a custom attestation infrastructure
- **SEV migration**: No automatic upgrade path from SEV/SEV-ES to SEV-SNP, this will be required to done by explicit user configuration.


## Definition of Users

<!--
Who is this feature set intended for
-->
- **Cluster Admins**: Responsible for enabling and managing SEV-SNP capabilities in Kubernetes Clusters.
- **Developers**: Deploys confidential workloads that require SEV-SNP.

## User Stories

<!--
List of user stories this design aims to solve
-->

- As a Cluster Admin, I want to enable SEV-SNP on cluster nodes so that tenants can deploy Confidential Compute workloads with SEV-SNP enabled.
- As a Cluster Admin, I need to ensure that my clusters have the correct labeling that shows the correct SEV and SEV-SNP labels are showing up on my nodes.

- As a Developer, I want to deploy VMs on a platform with SEV-SNP protection to meet compliance requirements for memory integrity.
- As a Developer, I want to deploy VMs without needing to understand the without understanding the low level AMD technology.


## Repos

<!--
List of repose this design impacts
-->
https://github.com/kubevirt/kubevirt

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->
The SEV-SNP feature extend the exisitn SEV features by adding a `secureNestedPaging` boolean flag to the current Launch Security API. When enabled the libvirt XML will contain `<launcSecurity type='sev-snp'>` using the default QEMU policy. the implementation will use existing SEV feature gate and extend the node labeling, by labeling nodes with `sev-snp.node.kubevirt.io`. The node labeller will detect SEV-SNP capabilities from libvirt domain capabilities then apply the label on the node, while the node selector renderer will be extended to include SEV-SNP scheudling. The feature will alo generate the domain XML to contain the required fields like `cbitpos`. 

## API Examples

<!--
Tangible API examples used for discussion
-->
- Basic SEV-SNP VM configuration

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: sev-snp-vm
spec:
  domain:
    launchSecurity:
      sev:
        secureNestedPaging: true  # Enable SEV-SNP, uses QEMU default policy
...
```

- Node labels added.

```yaml
apiVersion: v1
kind: Node
metadata:
  labels:
    sev-snp.node.kubevirt.io: "true"  # New SEV-SNP label
```

- The following is a sample of the output Libvirt XML.

```xml
<domain type='kvm'>
  <launchSecurity type='sev-snp'>
    <cbitpos>47</cbitpos>
    <reducedPhysBits>1</reducedPhysBits>
    <!-- Policy uses QEMU default (0x30000) -->
    <!-- Advanced SEV-SNP fields not implemented in initial version -->
  </launchSecurity>
</domain>
```

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Scalability

<!--
Overview of how the design scales)
-->
N/A

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->
- All new fields are fields should be optional
- This should not impact existing VMs
- This feature sits behind the existing SEV feature gate without breaking changes
- Node labels are added, no labels are removed.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->
- Unit testing to detect SEV-SNP from the libvirt capabilities.
- Since SEV does not have support for nested virtualization this will require bare metal hardware to conduct e2e testing.

## Implementation Phases

<!--
How/if this design will get broken up into multiple phases)
-->
- Initially this feature should support the basic enablement of the Launch Security in the libvirt XML. The next phase will be to get the ability to have the user provide the launch security policy.

## Feature lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- Basic SEV-SNP VM creation and scheduling works

### Beta

### GA
