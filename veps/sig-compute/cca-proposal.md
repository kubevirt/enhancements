# VEP #129: Enable Arm CCA

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal deals with the enablement of Arm Confidential Compute Architecture (CCA) in KubeVirt.
This enablement includes the support for creating confidential VMs by using CCA by encrypting VM memory.

## Motivation

Organizations are now asking for workloads to include the strongest confidential computing security guarantees.

Add support for CCA technology for in-use memory encryption of confidential guests on Arm.

## Goals

- Enable CCA VM Deployment: Provide users the ability to deploy VMs that are utilizing CCA technology

## Non Goals

- CCA Live Migration: Live migration is currently not supported by this technology. Goal: mark confidential-computing-enabled VMs as non-migratable.
- Exhaustive support for all CCA configurations in the libvirt XML: We will focus on the essential parameters required for basic enablement.
- Custom Attestation Services: Only focus on the enabling the capabilities through the standard interfaces and not building a custom attestation infrastructure.
- Confidential Devices Support.

## Definition of Users

- Cluster Admins: Responsible for enabling and managing CCA capabilities in Kubernetes Clusters.
- Developers: Deploys confidential workloads that require CCA.

## User Stories

- As a Cluster Admin, I want to enable CCA on cluster nodes so that Developers can deploy Confidential Compute workloads with CCA enabled.
- As a Cluster Admin, I need to ensure that my clusters have the correct labeling that shows the correct CCA labels are showing up on my nodes.
- As a Developer, I want to deploy VMs on a platform with CCA protection to meet compliance requirements for memory integrity.
- As a Developer, I want to deploy VMs without needing to understand the low-level confidential computing technologies.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

A new CCA Feature Gate should be added first. Extend the node labeller to detect and label nodes capable of running CCA VMs, a corresponding node selector can be added to VM pods so they are scheduled correctly.
CCA parameters need to be specified via the `launchSecurity` element of the libvirt domain xml.

```xml
<domain>
  ...
  <launchSecurity type='cca' measurement-log='yes'>
    <measurement-algo>sha256</measurement-algo>
    <personalization-value>...</personalization-value>
  </launchSecurity>
  ...
</domain>
```

All parameters are optional. Please refer to
https://git.codelinaro.org/kazuhiro_abe/libvirt/-/blob/905ce0d1054e14cf304605a6a214ee5c96094439/docs/formatdomain.rst#launch-security
for the explanation of the parameters.

*Note: The content at the provided link describes a specification that is not yet finalized and is subject to change.*

A new structure should be created for adding CCA on the `LaunchSecurity` field.

```go
type CCA struct {
    MeasurementAlgo string `json:"measurementAlgo,omitempty"`
    MeasurementLog string `json:"measurementLog,omitempty"`
    PersonalizationValue string `json:"personalizationValue,omitempty"`
}
```

## API Examples

The below yaml snippets provide examples of how to request CCA feature in the VMI spec.

- Basic CCA VM with default options

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  ...
spec:
  domain:
    launchSecurity:
      cca: {}
  ...
```

## Alternatives

## Scalability

## Update/Rollback Compatibility

- All new fields are optional and disabled by default.
- This should not impact existing VMs.

## Functional Testing Approach

- Unit testing to detect CCA from the libvirt capabilities.
- Since CCA does not have support for nested virtualization, this will require bare metal hardware to conduct e2e testing.

## Implementation History

The initial phase of implementation will focus on integrating basic functionality in KubeVirt thus allowing the creation and deployment of confidential VMs using the CCA technology.
The subsequent phase will involve adding any missing use cases aligned with other confidential VM technologies.

## Graduation Requirements

### Alpha

The feature will be implemented in Alpha.
We do not know if it will be possible to have e2e tests in Alpha due to lack of CCA hardware.
We expect the feature to be merged without the e2e tests.

### Beta

The requirements for graduating to Beta include having hardware available, CI lanes created, and e2e tests that can run.

### GA

Remove feature gate
