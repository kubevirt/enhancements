# VEP #80: Enable TDX and SEV-SNP

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

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

## Implementation Phases
### Intel TDX Phases:
The initial phase of implementation will focus on integrating basic
functionality in kubevirt thus allowing the creation and deployment of
confidential VMs using the TDX technology. The initial phase also includes the
e2e tests. The subsequent phase will involve adding any missing use cases
aligned with other confidential VM technologies.

### AMD SEV-SNP Phases:
The initial phase of implementation will focus on integrating basic
functionality. The initial phase also includes the e2e tests. The subsequent
phase will involve adding any missing use cases and necessary checks to prevent
the creation of improperly configured VMs (e.g., preventing users from setting
KernelHashes without configuring kernel booting).

## Feature lifecycle Phases

### Alpha
The feature will be implemented in Alpha. We do not know if it will be possible
to have e2e tests in Alpha due to lack of TDX hardware. We expect the feature
to be merged without the e2e tests.

### Beta
The requirements for graduating to Beta include having hardware available, CI
lanes created, and e2e tests that can run.

### GA
Remove feature gate
