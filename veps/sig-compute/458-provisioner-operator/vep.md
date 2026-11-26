# VEP #458: Provisioner Operator for Confidential VM

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

The `LaunchData` Custom Resource (VEP #340) is the mechanism for injecting
attestation values into confidential VMs before boot. This proposal introduces
the **provisioner-operator**: a separate project under the kubevirt
organization that bridges KubeVirt and an attestation provisioning service to
automate the `LaunchData` lifecycle for confidential VMs using Intel TDX and
AMD SEV-SNP.

The operator watches VMIs that reference a `LaunchData` CR via
`launchDataRef` in their launch security configuration, and the VMs that own
them. When the referenced
`LaunchData` does not yet exist, the operator contacts a provisioning service
to obtain per-VM secret references, constructs the InitData payload and its
cryptographic digest, and creates the `LaunchData` CR owned by the VMI's
`VirtualMachine`. On VM deletion, the operator cleans up the provisioning
record in the backend service.

## Motivation

VEP #340 provides KubeVirt with the plumbing to deliver InitData to
confidential VMs. However, constructing the InitData payload and its
associated digest requires domain-specific knowledge:

- The provisioning service assigns a per-VM resource path where the secret
  (e.g., a LUKS key) is stored.
- The operator constructs an InitData TOML document containing the KBS URL
  and resource path, following the
  [CoCo InitData specification](https://confidentialcontainers.org/docs/features/initdata/).
- The digest (`mrConfigId` for TDX, `hostData` for SNP) must be computed
  from the InitData bytes using the correct algorithm.
- When a VM is deleted, the provisioning record must be cleaned.

## Goals

- Automate the `LaunchData` CR lifecycle for VMs whose VMIs reference a
  `LaunchData` via `launchDataRef` for Intel TDX and AMD SEV-SNP.

## Non Goals

- Deploy or manage the provisioning service (Trustee) lifecycle. That is the
  responsibility of the trustee-operator or the cluster administrator.
- Implement evidence verification or key brokering. Verification is delegated
  to the attestation service; key release is delegated to the KBS.
- Manage guest-side components. Guests use existing CoCo guest-components
  (attestation-agent, confidential-data-hub).
- Live migration attestation semantics.

## Definition of Users

- Cluster Administrators: Deploy the provisioner-operator and configure its
  connection to the provisioning service. Configure RBAC permissions for the
  `LaunchData` resource.

- VM Users: Create VMs with `launchDataRef` in their launch security
  configuration. The provisioning flow is transparent.

## User Stories

- As a cluster administrator, I want to deploy an operator that automatically
  provisions InitData for confidential VMs so that VM users do not need to
  manage attestation manually.
- As a VM user, I want to create a confidential VM with `launchDataRef` and
  have the InitData provisioned automatically before the VM boots.

## Repos

- [kubevirt/provisioner-operator](https://github.com/kubevirt/provisioner-operator)
  (proposed, currently at
  [MatiasVara/provisioner-operator](https://github.com/MatiasVara/provisioner-operator))

## Design

### Architecture

The operator sits between KubeVirt and a provisioning service. It has two
interfaces:

1. **Kubernetes API**: watches VMIs and VMs, creates `LaunchData` CRs, manages
   finalizers on VMs.
2. **Provisioning service API**: requests per-VM secret allocation and
   revocation.

```
┌────────────────────────────────────────────────────────────────┐
│                      Kubernetes Cluster                        │
│                                                                │
│  ┌───────────────────┐       ┌───────────────────────────┐    │
│  │ provisioner-      │ watch │  VirtualMachineInstance    │    │
│  │ operator          │──────>│  launchDataRef: <name>     │    │
│  │                   │       └───────────────────────────┘    │
│  │                   │                                        │
│  │                   │       ┌───────────────────────────┐    │
│  │                   │ watch │  VirtualMachine            │    │
│  │                   │──────>│  (finalizer)               │    │
│  │                   │       └───────────────────────────┘    │
│  │                   │                                        │
│  │                   │       ┌───────────────────────────┐    │
│  │                   │create │  LaunchData CR             │    │
│  │                   │──────>│  (ownerRef → VM)           │    │
│  └─────────┬─────────┘       └───────────────────────────┘    │
│            │                                                   │
│            │ provision / revoke                                │
│  ┌─────────▼─────────┐                                        │
│  │ Provisioning       │                                        │
│  │ service (Trustee)  │                                        │
│  └───────────────────┘                                        │
└────────────────────────────────────────────────────────────────┘
```

### Provisioning

The operator watches VMIs in the configured namespace. A VMI is in scope when
it sets `launchDataRef` and is owned by a `VirtualMachine`, identified by the
VMI's controller `ownerReference`, which carries the VM's `metadata.uid`. Other
VMIs are ignored; KubeVirt rejects `launchDataRef` on VMIs without an owning
VM (VEP #340).

For a VMI in scope whose referenced `LaunchData` does not exist, the operator:

- registers the VM for cleanup, with a finalizer, before any request to the
  provisioning service, so that a VM deleted while provisioning is in progress
  still has its record revoked;
- obtains a per-VM resource path from the provisioning service;
- builds an InitData document that carries the KBS URL and that resource path,
  following the
  [CoCo InitData specification](https://confidentialcontainers.org/docs/features/initdata/);
- creates the `LaunchData` named by `launchDataRef`, with the InitData document
  in `oemStrings`, the digest of that document for the platform of the VMI, and
  a controller `ownerReference` to the VM that includes its UID.

| Platform | `LaunchData` field | Digest of the InitData document |
|----------|--------------------|---------------------------------|
| Intel TDX | `mrConfigId` | SHA-384 (48 bytes) |
| AMD SEV-SNP | `hostData` | SHA-256 (32 bytes) |

The `algorithm` declared in the InitData document matches the digest, so that
the guest can verify the document against its hardware report (see the trust
model in VEP #340).

Once virt-controller verifies that the `LaunchData` is owned by the VMI's VM
(by UID) and carries the digest for the platform, the VM boots with the values
embedded in the hardware measurement registers and SMBIOS Type 11.

### Existing LaunchData

The operator never modifies a `LaunchData`; KubeVirt makes its spec and its
owner immutable (VEP #340). When the referenced `LaunchData` already exists:

- **Owned by the same VM (same UID):** nothing is done. This is the case after
  a stop/start, since the VM and its UID do not change. It also allows the
  `LaunchData` to be created by other means, as long as it is owned by the VM.
- **Owned by a different VM UID:** it is not reused. It belongs to a deleted VM
  with the same name that garbage collection has not removed yet, or to another
  VM that uses the same name; the operator provisions once it is gone.
- **Not owned by a VM:** KubeVirt cannot launch a VMI with it and it cannot be
  rebound, so the operator reports it and does not provision.

### Cleanup

When a VM registered for cleanup is deleted, the operator revokes its
provisioning record and then removes its own finalizer, leaving any other
finalizers in place. If revocation fails, the finalizer stays and revocation is
retried, so the VM is not deleted while its secret remains accessible.
Kubernetes garbage collection deletes the `LaunchData` through its
`ownerReference` to the VM.

Stopping a VM deletes its VMI but not the VM, so neither the provisioning
record nor the `LaunchData` is removed, and the VM reuses them on its next
start.

### Provisioning service contract

The operator relies on two operations of the provisioning service:

| Operation | Input | Result |
|-----------|-------|--------|
| Provision | VM namespace and name | KBS resource path where the per-VM secret is stored |
| Revoke | VM namespace and name | The provisioning record is removed; a record that no longer exists counts as revoked |

The Trustee provisioner plugin is the reference implementation.

### RBAC

The operator requires the following permissions:

| Resource | Verbs | Purpose |
|----------|-------|---------|
| `virtualmachineinstances` | `get`, `list`, `watch` | Watch VMIs that reference a `LaunchData` |
| `virtualmachines` | `get`, `list`, `watch`, `patch` | Watch VM deletion and manage finalizers |
| `launchdatas` | `create`, `get`, `list`, `watch` | Create `LaunchData` CRs |

The `ownerReference` does not set `blockOwnerDeletion`, so no permission on
`virtualmachines/finalizers` is needed.

Besides namespace administrators (KubeVirt's `admin` ClusterRole), the
operator's ServiceAccount is the only identity with `create` permission on
`LaunchData` resources; KubeVirt's `edit` ClusterRole only grants read access
to them.

### Security Considerations

- The operator does not store secrets. The provisioning service holds the
  actual secrets; the operator only receives resource paths and constructs
  the InitData TOML from them.
- Communication between the operator and the provisioning service should be
  authenticated. Two options are considered:
  - **Bearer token**: The operator presents a shared token (stored as a
    Kubernetes Secret) in the `Authorization` header. Simple but requires
    manual rotation.
  - **Mutual TLS (mTLS)**: Both the operator and the provisioning service
    present certificates signed by a shared CA. Stronger authentication.
    `cert-manager` can automate rotation.
- The operator runs with the minimum RBAC permissions required.
- The operator does not run in a privileged security context.
- The `LaunchData` spec is immutable and its `ownerReference` to the VM cannot
  be changed after creation (enforced by KubeVirt per VEP #340). This prevents
  overwriting committed values and reusing them for another VM, including one
  recreated with the same name.

### Reference implementation

The endpoints of the reference provisioning service, the exact InitData
document, and the operator configuration are documented in the
[operator repository](https://github.com/MatiasVara/provisioner-operator).

## API Examples

The operator introduces no API of its own. See VEP #340 for examples of a
`VirtualMachine` with `launchDataRef` and of the `LaunchData` the operator
creates for it.

## Alternatives

### Hook sidecar

A hook sidecar contacts the provisioning service and patches the libvirt
domain XML directly.

**Drawbacks:** bypasses KubeVirt's API, not auditable, no RBAC, fragile
against KubeVirt upgrades, requires a sidecar per VMI, no cleanup on
deletion.

### Manual LaunchData creation

The cluster administrator manually creates `LaunchData` CRs for each VM.

**Drawbacks:** error-prone, does not scale, requires the administrator to
compute digests manually and to set the `ownerReference` to each VM's UID, no
automated cleanup of provisioning records.

## Does it belong to core KubeVirt?

The provisioner operator shall be hosted in the KubeVirt organization, but
shall not be part of the core KubeVirt repository.

## Scalability

The operator adds one watch on VMIs, one watch on VMs, and one `LaunchData`
creation per VM in the configured namespace. It sends one request to the
provisioning service per VM provisioning and one per VM deletion; stopping
and starting a VM does not provision again. The operator does not poll; it
reacts to VMI and VM events.

## Update/Rollback Compatibility

- The operator is deployed independently from KubeVirt. Upgrading or rolling
  back the operator does not affect running VMs.
- The operator depends on the `LaunchData` CRD from VEP #340. If KubeVirt is
  rolled back to a version without the CRD, the operator cannot create
  `LaunchData` resources but existing VMs are unaffected.
- The finalizer ensures cleanup happens even if the operator is temporarily
  unavailable; Kubernetes retries deletion until the finalizer is removed.

## Functional Testing Approach

- Unit tests for InitData TOML construction and digest computation
  (SHA-384 for TDX, SHA-256 for SNP).
- Unit tests for the reconcile logic: a VMI with `launchDataRef` triggers
  provisioning, a VMI without `launchDataRef` or without an owning VM is
  ignored, an existing `LaunchData` owned by the same VM UID is skipped, and a
  `LaunchData` owned by a different UID is not reused.
- Unit tests for the created `LaunchData`: it carries a controller
  `ownerReference` to the VM with the VM's UID.
- Unit tests for the cleanup flow: on VM deletion, the provisioning service
  cleanup is called and only the operator's finalizer is removed; deleting the
  VMI on stop does not trigger cleanup.
- Integration test with a mock provisioning service: verify end-to-end
  flow from VM creation to `LaunchData` CR creation.
- E2E test with Trustee: verify a TDX/SNP VM boots successfully after the
  operator provisions its `LaunchData`, and boots again after a stop/start
  without provisioning again.

## Implementation History

## Graduation Requirements

### Alpha

The operator will be published as a separate repository under the kubevirt
organization. Basic authentication (bearer token) to
the provisioning service.

### Beta

mTLS authentication option. E2E tests.

#### On-By-Default Readiness

Not applicable: the operator is an optional component deployed separately
from KubeVirt, and it depends on KubeVirt's `InjectLaunchData` feature gate
(VEP #340).

### GA

