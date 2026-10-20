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

The `InitData` Custom Resource is the mechanism for injecting attestation
values into confidential VMs before boot. This proposal introduces the
**provisioner-operator**: a separate project under the kubevirt organization
that bridges KubeVirt and an attestation provisioning service to automate the
`InitData` lifecycle for confidential VMs using Intel TDX and AMD SEV-SNP.

The operator watches for VMIs that reference an `InitData` CR via
`initDataRef` in their launch security configuration. When the referenced
`InitData` does not yet exist, the operator contacts a provisioning service
to obtain per-VM secret references, constructs the InitData payload and its
cryptographic digest, and creates the `InitData` CR. On VMI deletion, the
operator cleans up the provisioning record in the backend service.

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
- When a VM is deleted, the provisioning record must be cleaned up so the
  secret is no longer accessible.

Without this operator, each organization deploying confidential VMs on
KubeVirt would need to build its own automation to perform these steps.

## Goals

- Automate the `InitData` CR lifecycle for VMIs that reference an `InitData`
  via `initDataRef`.
- Support Intel TDX and AMD SEV-SNP with a single operator instance.
- Clean up provisioning records when a VMI is deleted.
- Provide RBAC-scoped access to the `InitData` resource.

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
  `InitData` resource.

- VM Users: Create VMIs with `initDataRef` in their launch security
  configuration. The provisioning flow is transparent.

## User Stories

- As a cluster administrator, I want to deploy an operator that automatically
  provisions InitData for confidential VMs so that VM users do not need to
  manage attestation manually.
- As a VM user, I want to create a confidential VM with `initDataRef` and have
  the InitData provisioned automatically before the VM boots.
- As a cluster administrator, I want provisioning records to be cleaned up when
  VMs are deleted so that secrets are not left accessible.

## Repos

- [kubevirt/provisioner-operator](https://github.com/kubevirt/provisioner-operator)
  (proposed, currently at
  [MatiasVara/provisioner-operator](https://github.com/MatiasVara/provisioner-operator))

## Design

### Architecture

The operator sits between KubeVirt and a provisioning service. It has two
interfaces:

1. **Kubernetes API**: watches VMIs, creates `InitData` CRs, manages
   finalizers.
2. **Provisioning service API**: requests per-VM secret allocation and
   cleanup.

```
┌────────────────────────────────────────────────────────────────┐
│                      Kubernetes Cluster                        │
│                                                                │
│  ┌───────────────────┐       ┌───────────────────────────┐    │
│  │ provisioner-      │ watch │  VirtualMachineInstance    │    │
│  │ operator          │──────>│  initDataRef: <name>       │    │
│  │                   │       └───────────────────────────┘    │
│  │                   │                                        │
│  │                   │       ┌───────────────────────────┐    │
│  │                   │create │  InitData CR               │    │
│  │                   │──────>│  (ownerRef → VMI)          │    │
│  └─────────┬─────────┘       └───────────────────────────┘    │
│            │                                                   │
│            │ POST /provision                                   │
│            │ DELETE /provision/{ns}/{name}                      │
│  ┌─────────▼─────────┐                                        │
│  │ Provisioning       │                                        │
│  │ service (Trustee)  │                                        │
│  └───────────────────┘                                        │
└────────────────────────────────────────────────────────────────┘
```

### Reconcile flow

The operator watches VMIs across the configured namespace. When it detects a
VMI with `initDataRef` set (TDX or SNP) and the referenced `InitData` CR does
not yet exist, it performs the following steps:
1. Verify the provisioning service is healthy.
2. Request a provisioning record from the service, providing the VMI namespace
   and name.
3. Receive a per-VM resource path (where the secret is stored in the KBS).
4. Construct the InitData TOML document containing the KBS URL and resource
   path, following the CoCo InitData specification.
5. Compute the digest:
   - TDX: SHA-384 of the InitData bytes → `mrConfigId` (48 bytes, base64).
   - SNP: SHA-256 of the InitData bytes → `hostData` (32 bytes, base64).
6. Encode the InitData TOML as a base64 OEM string.
7. Create an `InitData` CR with the name specified in `initDataRef` and
   `ownerReference` to the VMI, containing `mrConfigId` or `hostData`, and
   `oemStrings`.
8. Add a finalizer to the VMI.

If the referenced `InitData` CR already exists, the operator skips
provisioning. This allows the `InitData` to be created manually or by another
component — the operator only acts when the referenced resource is missing.

Once KubeVirt's InitData controller validates and commits the `InitData` CR,
the VM boots with the values embedded in the hardware measurement registers
and SMBIOS Type 11.

### Cleanup flow

When a VMI with the operator's finalizer is deleted:

1. The operator calls the provisioning service's cleanup endpoint to revoke
   the provisioning record.
2. The operator removes its finalizer from the VMI.
3. Kubernetes garbage-collects the `InitData` CR via its `ownerReference`.

### Interaction with the provisioning service

The operator communicates with the provisioning service (CoCo Trustee
provisioner plugin as reference implementation) via REST:

| Operation | Method | Endpoint | Purpose |
|-----------|--------|----------|---------|
| Health check | `GET` | `/healthz` | Verify service availability before provisioning |
| Provision | `POST` | `/kbs/v0/provisioner/provision` | Allocate a per-VM secret and obtain the resource path |
| Cleanup | `DELETE` | `/kbs/v0/provisioner/provision/{namespace}/{name}` | Revoke the provisioning record on VMI deletion |

The provision response contains a `resource_path` (KBS resource locator) and
a `uuid` (deterministic identifier derived from namespace + name). The
operator uses these to construct the InitData TOML.

### InitData construction

The operator constructs the InitData TOML following the
[CoCo specification](https://confidentialcontainers.org/docs/features/initdata/):

```toml
version = "0.1.0"
algorithm = "sha384"

[data]
"aa.toml" = '''
[token_configs]
[token_configs.kbs]
url = "<KBS_URL>"
'''

"cdh.toml" = '''
[kbc]
name = "cc_kbc"
url = "<KBS_URL>"
'''
```

The digest is then computed from the TOML bytes using the declared algorithm.
The full TOML is base64-encoded and delivered as an OEM string so the guest
can retrieve the KBS URL and configuration at boot.

### Configuration

The operator is configured via environment variables:

| Variable | Description |
|----------|-------------|
| `KBS_URL` | Base URL of the provisioning service |
| `WATCH_NAMESPACE` | Kubernetes namespace to watch for VMIs |

### RBAC

The operator requires the following permissions:

| Resource | Verbs | Purpose |
|----------|-------|---------|
| `virtualmachineinstances` | `get`, `list`, `watch`, `patch` | Watch VMIs and manage finalizers |
| `initdata` | `create`, `get`, `list`, `watch` | Create InitData CRs |

The operator's ServiceAccount is the only identity with `create` permission on
`InitData` resources, ensuring that only the operator can supply launch-data.

## API Examples

### TDX with provisioner-operator

The VM user creates a VMI with `initDataRef` pointing to the name of the
`InitData` CR that the operator will create. The operator handles everything
else:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-tdx-vm
spec:
  template:
    spec:
      domain:
        launchSecurity:
          tdx:
            initDataRef: my-tdx-vm-initdata
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

The operator detects the VMI, contacts Trustee, and creates:

```yaml
apiVersion: kubevirt.io/v1
kind: InitData
metadata:
  name: my-tdx-vm-initdata
  namespace: default
  ownerReferences:
    - apiVersion: kubevirt.io/v1
      kind: VirtualMachineInstance
      name: my-tdx-vm
      uid: "a1b2c3d4-..."
spec:
  mrConfigId: "<base64-encoded SHA-384 of InitData TOML>"
  oemStrings:
    - "<base64-encoded InitData TOML>"
```

### SNP with provisioner-operator

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-snp-vm
spec:
  template:
    spec:
      domain:
        launchSecurity:
          sevSnp:
            initDataRef: my-snp-vm-initdata
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

The operator creates the `InitData` CR with `hostData` (SHA-256) instead of
`mrConfigId`.

## Security Considerations

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
- The operator runs with the minimum RBAC permissions required. Only the
  operator's ServiceAccount can create `InitData` resources.
- The operator does not run in a privileged security context.
- The InitData CR write-once property (enforced by KubeVirt per VEP #340)
  prevents overwriting committed values.

## Relationship to Other Proposals

| Proposal | Relationship |
|----------|-------------|
| VEP #340 (InitData resource) | This operator is the external operator that VEP #340 declares out of scope. It creates `InitData` CRs. |
| VEP #80 (TDX and SEV-SNP enablement) | Provides the launch security fields that this operator watches for. |
| PR #410 (Attestation operator) | Complementary proposal with broader scope (reference value generation, endorsement management). This operator focuses on InitData lifecycle. |

## Alternatives

### Hook sidecar

A hook sidecar contacts the provisioning service and patches the libvirt
domain XML directly.

**Drawbacks:** bypasses KubeVirt's API, not auditable, no RBAC, fragile
against KubeVirt upgrades, requires a sidecar per VMI, no cleanup on
deletion.

### Manual InitData creation

The cluster administrator manually creates `InitData` CRs for each VMI.

**Drawbacks:** error-prone, does not scale, requires the administrator to
compute digests manually, no automated cleanup.

## Scalability

The operator adds one watch on VMIs and one `InitData` creation per VMI in
the configured namespace. The provisioning service call adds one HTTP
round-trip per VMI creation and one per VMI deletion. The operator does not
poll; it reacts to VMI events.

## Update/Rollback Compatibility

- The operator is deployed independently from KubeVirt. Upgrading or rolling
  back the operator does not affect running VMs.
- The operator depends on the `InitData` CRD from VEP #340. If KubeVirt is
  rolled back to a version without the CRD, the operator cannot create
  `InitData` resources but existing VMs are unaffected.
- The finalizer ensures cleanup happens even if the operator is temporarily
  unavailable; Kubernetes retries deletion until the finalizer is removed.

## Functional Testing Approach

- Unit tests for InitData TOML construction and digest computation
  (SHA-384 for TDX, SHA-256 for SNP).
- Unit tests for the reconcile logic: VMI with `initDataRef` triggers
  provisioning, VMI without `initDataRef` is ignored, VMI with existing
  committed InitData is skipped.
- Unit tests for the cleanup flow: finalizer removal after provisioning
  service cleanup.
- Integration test with a mock provisioning service: verify end-to-end
  flow from VMI creation to InitData CR creation.
- E2E test with Trustee: verify a TDX/SNP VMI boots successfully after
  the operator provisions InitData.

## Graduation Requirements

### Alpha

The operator will be published as a separate repository under the kubevirt
organization. Basic authentication (bearer token) to
the provisioning service.

### Beta

mTLS authentication option. E2E tests.

### GA

