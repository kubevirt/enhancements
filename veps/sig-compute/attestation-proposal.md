# VEP #107: Enable attestation for Confidential VMs

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
This proposal deals with the enablement of attestation for Confidential VMs.
Attestation is the mechanism used by a Confidential VM to attest that it is
running on confidential HW (hardware). This proposal focuses on the deployment of QGS
(Quote Generation Service) in the host. This service is required for the blob
generation on confidential guests using Intel TDX. Bear in mind that SEV-SNP
does not require something in the host for the blob generation.

## Motivation
Organizations are now asking for workloads to include the strongest
confidential computing security guarantees. Together with confidential VMs, we
need to provide a way for confidential VMs to be attested. By doing so,
confidential VMs can certify that they are running on confidential HW.

## Goals
- Enable Attestation for confidential VMs on Intel TDX

## Non Goals
- Deployment of the Quote Generation Service (QGS)

## Definition of Users
- VM users: Deploy confidential workloads that require attestation.

## User Stories
- As a VM user, I want to deploy VMs on a platform with TDX protection and be
  able to attest these VMs.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design
From the implementation perspective, we extend the KubeVirt CR spec to enable
attestation:

```xml
configuration:
    attestation:
      enabled: true
```

This indicates that QGS has been deployed on the node. The virt-controller is
notified to request the qgs resource during the creation of the virt-handler
pod.  

## API Examples
The bellow yaml snippets provide an example of how to enable attestation in the Kubevirt CR.

```xml
configuration:
    attestation: {}
```

The bellow yaml snippets provide an example of how to disable attestation in the Kubevirt CR.

```xml
configuration:
    attestation:
      enabled: false
```

## Alternatives
- Request the `devices.kubevirt.io/qgs` resource in the VMI spec. We chose a
  simpler approach where attestation is set up cluster-wide. At the moment,
  there is no use case where confidential mode and attestation are not both
  used together. If a use case appears where only confidential mode is needed,
  we might consider enabling it at the VM level instead.

## Scalability

## Update/Rollback Compatibility
- All new fields are optional and disabled by default
- This should not impact existing VMs

## Functional Testing Approach
- e2e tests to check that the blob from the guest pov is correctly generated.
- Since TDX & SEV-SNP do not have support for nested virtualization this will
  require bare metal hardware to conduct e2e testing.

## Implementation Phases
The initial phase of implementation will focus on integrating basic
functionality. For example, a socket device plugin would be required to mount
the unix socket into the virt-launcher. The initial phase also includes the e2e
tests.

## Feature lifecycle Phases

### Alpha
The feature will be implemented in Alpha. We do not know if it will be possible
to have e2e tests in Alpha due to lack of TDX hardware. We expect the feature
to be merged without the e2e tests.

### Beta
We expect e2e tests in Beta. We expect the API to be stable.

### GA
