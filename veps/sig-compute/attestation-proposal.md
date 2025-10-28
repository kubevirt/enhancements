# VEP #107: Enable attestation for Confidential VMs in Intel TDX

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
This proposal introduces attestation for Confidential VMs in Intel TDX.
Attestation is the mechanism used by a Confidential VM to attest that it is
running on confidential hardware (HW). For Intel TDX, attestation requires to
deploy the Quote Generation Service (QGS) on the host that communicates with
QEMU through a UNIX socket when a confidential wants to generate a blob. The
VEP does not include managing or installing QGS components on the host but
instead only proposes adding the required mechanisms to KubeVirt to utilize an
existing QGS device socket. Bear in mind that SEV-SNP does not require a
host-side service for the blob generation.

## Motivation
Organizations are now asking for workloads to include the strongest
confidential computing security guarantees. Together with confidential VMs, we
need to provide a way for confidential VMs to be attested. By doing so,
confidential VMs can certify that they are running on confidential HW.

## Goals
- Enable attestation for confidential VMs on Intel TDX

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
    confidentialCompute:
      tdx:
        attestation:
          enforced: true
          qgsSocketPath: "/var/run/tdx-qgs/qgs.socket"
```

We add a new top-level section in configuration for all TEE-related
configuration items named confidentialCompute. We add a new section for tdx.
The attestation section contains two attributes: enforced and qgsSocketPath.
When enforced is true, KubeVirt shall require the socket to exist or it fails.
When enforced is false, KubeVirt shall try to mount the UNIX socket only if it
exists. If the socket does not exist, KubeVirt shall not fail. qgsSocketPath is
an optional attribute that indicates the path to the UNIX socket. We implement
a device plugin that mounts the UNIX socket depending on the enforced
attribute.

We introduce the TDX device plugin, i.e., `devices.kubevirt.io/tdx`, which runs
on nodes and handles both TDX hardware capability advertisement and QGS socket
management. This plugin replaces the current node-labeller for TDX detection.
It relies on Kubernetes native extended resource scheduling for placement. The
plugin checks for TDX hardware support in the same way node-labeller used to
do. If it is supported, it advertises it as a resource:
`devices.kubevirt.io/tdx`. During resource allocation the plugin probes for the
QGS socket at the configured path. If the socket exists, it mounts it into the
virt-launcher pod as a volume.

The plugin's health is based on the cluster-wide
confidentialCompute.tdx.attestation.enforced configuration:

* If attestation is enforced: The plugin marks itself as healthy only if both
  TDX hardware and the QGS socket are present. If the socket is missing, it
  becomes unhealthy and enters a retry loop. This acts as a scheduling gate:
  TDX VMs requesting the resource won't allocate until QGS is deployed.
* If attestation is not enforced: The plugin is always healthy if TDX hardware
  is available. QGS mounting is opportunistic - if the socket exists, it's
  mounted; otherwise, the VM starts without attestation.

TDX VMs automatically request the tdx resource in their pod spec to make sure
that these are scheduled on the TDX capable nodes.

## API Examples
The below YAML snippets provide an example of how to enable attestation in the
KubeVirt CR. In the default configuration, enforced is false and the
qgsSocketPath is "/var/run/tdx-qgs/qgs.socket".

```xml
configuration:
    confidentialCompute:
      tdx:
        attestation:
          enforced: false
```

The below YAML snippets provide an example of how to enforce the enabling of
attestation.

```xml
configuration:
    confidentialCompute:
      tdx:
        attestation:
          enforced: true
          qgsSocketPath: "/var/run/qgs.socket"
```

## Alternatives
- Request the `devices.kubevirt.io/qgs` resource in the VMI spec. We chose a
  simpler approach where attestation is set up cluster-wide.
- Use DRA instead of a device plugin.

## Scalability

## Update/Rollback Compatibility
- All new fields are optional and disabled by default
- This should not impact existing VMs

## Functional Testing Approach
- e2e tests to check that the blob from the guest point of view is correctly
  generated.
- Since TDX & SEV-SNP do not have support for nested virtualization this will
  require bare metal hardware to conduct e2e testing.

## Implementation Phases
The initial phase of implementation will focus on integrating basic
functionality. For example, a socket device plugin would be required to mount
the UNIX socket into the virt-launcher. The initial phase also includes the e2e
tests.

## Feature lifecycle Phases

### Alpha
The feature will be implemented in Alpha. We do not know if it will be possible
to have e2e tests in Alpha due to lack of TDX hardware. We expect the feature
to be merged without the e2e tests.

### Beta
We expect e2e tests in Beta. We expect the API to be stable. We need to decide
if we keep the path to the UNIX socket as an attribute or we remove it in favor
of a default location. We need to re-evaluate whether there is any use case for
having per-VMI enforcements.

### GA
Remove feature gate.
