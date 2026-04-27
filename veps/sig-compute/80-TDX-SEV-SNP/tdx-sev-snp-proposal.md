# VEP #80: Enable TDX and SEV-SNP

## VEP Status Metadata

### Target releases (WorkloadEncryptionSEV)

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.7
- This VEP targets beta for version: v1.9
- This VEP targets GA for version:

### Target releases (WorkloadEncryptionTDX)

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.7
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview
This proposal deals with the enablement of the following technologies: Intel
TDX & AMD Secure Encrypted Virtualization with Secure Nested Paging (SEV-SNP)
in Kubevirt. This enablement includes the support for creating confidential VMs
by using TDX & SEV-SNP by encrypting VM memory. For AMD, SEV is currently
supported in KubeVirt, and this VEP proposes to build on top of this feature
and integrates SEV-SNP support.

## Motivation
Organizations are now asking for workloads to include the strongest
confidential computing security guarantees.

For AMD, KubeVirt supports SEV and SEV-ES for memory encryption, but lacks
SEV-SNP enhanced memory integrity protection.

For Intel, Add support for TDX technology for in-use memory encryption of
confidential guests.

## Goals
- Enable TDX VM Deployment: Provide users the ability to deploy VMs that are
  utilizing Intel TDX technology.
- Enable SEV-SNP VM Deployment: Provide users the ability to deploy VMs that
  are utilizing AMD SEV-SNP technology.
- Extend the existing SEV Feature: The SEV feature is currently in the alpha
  phase. Extending it with SEV-SNP is the next logical step.
- Extend Existing SEV Infrastructure: Use the existing SEV node labeling,
  scheduling, and domain generation infrastructure.

## Non Goals
- TDX & SEV-SNP Live Migration: Live migrations are currently not supported by
  either technologies. Goal: mark confidential-computing-enabled VMs as
  non-migratable.
- All TDX & SEV-SNP libvirt XML configurations those would be use cases situations.
- Custom Attestation Services: Only focus on the enabling the capabilities
  through the standard interfaces and not building a custom attestation
  infrastructure.
- SEV migration: No automatic upgrade path from SEV/SEV-ES to SEV-SNP, this
  will be required to be done by explicit user configuration.
- Confidential Devices Support.

## Definition of Users
- Cluster Admins: Responsible for enabling and managing SEV-SNP & TDX
  capabilities in Kubernetes Clusters.
- Developers: Deploys confidential workloads that require SEV-SNP & TDX.

## User Stories
- As a Cluster Admin, I want to enable TDX on cluster nodes so that Developers
  can deploy Confidential Compute workloads with TDX enabled.
- As a Cluster Admin, I need to ensure that my clusters have the correct
  labeling that shows the correct TDX labels are showing up on my nodes.
- As a Developer, I want to deploy VMs on a platform with TDX protection to
  meet compliance requirements for memory integrity.
- As a Cluster Admin, I want to enable SEV-SNP on cluster nodes so that
  Developers can deploy Confidential Compute workloads with SEV-SNP enabled.
- As a Cluster Admin, I need to ensure that my clusters have the correct
  labeling that shows the correct SEV and SEV-SNP labels are showing up on my
  nodes.
- As a Developer, I want to deploy VMs on a platform with SEV-SNP protection to
  meet compliance requirements for memory integrity.
- As a Developer, I want to deploy VMs without needing to understand the
  low-level confidential computing technologies.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Common Features
Both designs rely on labelling those nodes capable of running CVMs
(Confidential Virtual Machines). In addition to labeling, the node registers
its available "key ID" resources, which determines how many CVMs can be created
in the node. The scheduler consumes one key ID for each CVM. The number of key
IDs is limited per node.  This is applicable to both TDX and SNP and the
available resources are available under `/sys/fs/cgroup/misc.capacity`.

### TDX Design
A new TDX Feature Gate should be added first. Extend the node labeller to
detect and label nodes capable of running TDX VMs, a corresponding node
selector can be added to VM pods so they are scheduled correctly. TDX
parameters need to be specified via the `launchSecurity` element of the Libvirt
domain xml:
```xml
   <domain>
     ...
     <launchSecurity type='tdx'>
       <policy>0x10000001</policy>
       <mrConfigId>xxx</mrConfigId>
       <mrOwner>xxx</mrOwner>
       <mrOwnerConfig>xxx</mrOwnerConfig>
       <quoteGenerationSocket path="/var/run/tdx-qgs/qgs.socket"/>
     </launchSecurity>
     ...
   </domain>
```
All parameters are optional. Please refer to
https://gitlab.com/libvirt/libvirt/-/blob/master/docs/formatdomain.rst?ref_type=heads#launch-security
for the explanation of the parameters. From the implementation perspective,
only extend the VMI spec to enable the basics in the proposal currently:

```xml
   spec:
     domain:
       launchSecurity:
         tdx: {}
```
Let's hold off on adding more options to the VMI spec for now. We need further
discussion on the use cases and how they would apply to VM creation.

### AMD Design

AMD SEV-SNP maps to a new type in the LibVirt XML of type “sev-snp” with its
own attributes and elements that are similar to the type “sev”.

```xml
   <domain>
     ...
     <launchSecurity type='sev-snp'>
       <cbitpos>47</cbitpos>
       <policy>0x00030000</policy>
       <reducedPhysBits>1</reducedPhysBits>
	<guestVisibileWorkarounds>...</guestVisibleWorkaround>
	<idBlock>...</idBlock>
	<idAuth>...</idAuth>
	<hostData>...</hostData>
     </launchSecurity>
     ...
   </domain>
```

A new structure should be created for adding SEV-SNP on the `LaunchSecurity`
field.
```go
type SEVSNP struct {
  // 64-bit SEV-SNP Guest Policy
  policy: "0x3000" # Default Policy
}
```
Similar to TDX, all elements for type=’sev-snp’, this will be a basic enablement
until further use cases arise to provide additional settings to configure a 
Confidential VM. The default policy option will have a default value that QEMU specifies.
The node labeller will detect SEV-SNP capabilities from the LibVirt domain capabilities then
apply the label to the node, while the node selector renderer will be extended to include
SEV-SNP scheduling.

### Security Considerations
- The infrastructure provider and the VMI author are untrusted. For example,
  the infrastructure provider is always capable of injecting random cloud-init
  stuff behind our back. So if an instance of a secret application runs inside
  a CVM, via cloud-init, an infrastructure provider or a hacker could simply
  execute random commands inside this VM.

## API Examples
The bellow yaml snippets provide examples of how to request TDX feature in the
VMI spec.

### API Examples for Intel TDX

- TDX VM with default options

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  ...
spec:
  domain:
    launchSecurity:
      tdx: {}
  ...
```

### API Examples for AMD

- Basic SEV-SNP with default options

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: sev-snp-vm
spec:
  domain:
    launchSecurity:
      snp: {}
  ...
```

## Alternatives

## Scalability

## Update/Rollback Compatibility
- All new fields are optional and disabled by default
- This should not impact existing VMs
- AMD SEV Compatibility:
  - The AMD SEV-SNP feature sits behind the existing SEV feature gate without
    breaking changes
  - Node labels are added, no labels are removed.

## Functional Testing Approach
- Unit testing to detect TDX & SEV-SNP from the libvirt capabilities.
- Since TDX & SEV-SNP do not have support for nested virtualization this will
  require bare metal hardware to conduct e2e testing.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

- v1.7: Alpha implementation of TDX and SEV-SNP support.
- v1.9: Beta implementation of SEV-SNP support. 

## Graduation Requirements

<!--
The requirements for graduating to each stage.
-->

### Alpha
- [x] Feature gate guards all code changes
- [x] Initial implementation supporting TDX and SEV-SNP VM creation and deployment
- [ ] e2e TDX tests (may be deferred to Beta due to lack of TDX/SEV-SNP hardware in CI)
- [x] e2e SEV-SNP tests 

### Beta
- [ ] Hardware available for CI
- [ ] CI lanes created for TDX and SEV-SNP
- [ ] e2e tests passing

### GA
- [ ] Remove feature gates
