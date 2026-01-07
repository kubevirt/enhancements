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
QEMU through a UNIX socket when a confidential VM wants to generate a blob. The
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
- As a developer, I want to continue to be able to launch TDX VMs with out
  setting up attestation for testing purposes.
- As a cluster admin, I want to be to fail-fast by adding an addition check to
  verify that QGS is setup correctly (i.e. the socket exists) before deploying
  before deploying my TDX VMs.

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

We add a new top-level section in configuration for all TEE(Trusted Execution
Environment)-related configuration items named `confidentialCompute`. We add a
new section for tdx. The attestation section contains two attributes:
`enforced` and `qgsSocketPath`. When `enforced` is true, KubeVirt shall enforce
that the QGS socket exists before scheduling any TDX VMs. The enforcement
mechanism is discussed later. Next, `qgsSocketPath` is an optional attribute
that indicates the path to the UNIX socket on the host.

We introduce the TDX device plugin, using the resource `devices.kubevirt.io/tdx`,
which runs on each TDX capable node and handles both TDX hardware capability
advertisement and QGS socket mounting. This plugin replaces the current
node-labeller for TDX detection. It relies on Kubernetes native extended
resource scheduling for placement. The plugin checks for TDX hardware support
by checking for the existance of a `tdx` key in `/sys/fs/cgroup/misc.capacity`
(which is added by the kernel given TDX support). Moreover, the value associated
with the `tdx` key inside `misc.capacity` also reports the maximum number of
concurrent TDs supported by the hardware. Therefore, this file is also used to
derive the device capacity of the device plugin.

It should be noted that all mounts are unconditional. Mount points are created
even if the QGS socket does not exist which allows attestation to function once
the QGS socket is created even as the VM is running.

The plugin's resource health is based on the cluster-wide
confidentialCompute.tdx.attestation.enforced configuration:

* If attestation is enforced: The resource is healthy only if the QGS socket is
  present. If the socket is missing, it becomes unhealthy and enters a retry
  loop. This acts as a scheduling gate by ensuring that: (a) TDX VMs requesting
  the resource won't allocate until QGS is deployed. (b) If there are multiple
  nodes in the cluster supporting TDX hardware, but only some have attestation
  support via the QGS socket then the scheduler will only deploy TDX VMs to
  nodes that have the QGS socket configured.
* If attestation is not enforced: The resource is always healthy if TDX
  hardware is available. QGS mounting is opportunistic - if the socket exists,
  it's mounted; otherwise, the VM starts without attestation.

TDX VMs automatically request the tdx resource in their VM spec to make sure
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
- Use a device plugin or an NRI plugin served by the QGS pod itself
- Use a hostPath mount patching handled by a mutating webhook

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
the UNIX socket into the virt-launcher.

## Feature lifecycle Phases

### Alpha
The feature will be implemented in Alpha. We do not know if it will be possible
to have e2e tests in Alpha due to lack of TDX hardware. We expect the feature
to be merged without the e2e tests.

### Beta
We expect e2e tests in Beta. We expect the API to be stable. We need to decide
if we keep the path to the UNIX socket as an attribute or we remove it in favor
of a default location. Finally we need to re-evaluate whether there is any use
case for enforcing the existance of QGS on the KubeVirt side (i.e use of the
`attestation.enforced` field) and if so whether it makes sense to extend these
enforcements to support per-VMI enforcements (as opposed to only cluster-wide).

### GA
Remove feature gate.
