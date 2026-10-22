# VEP #340: launchsecurity: Add LaunchData resource for TDX and SNP

## VEP Status Metadata

### Target releases (LaunchData)

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

Confidential VMs (CVMs) require per-VM configuration values to be injected
before boot so the guest can use them during remote attestation. These values
are incorporated into hardware measurement registers and become part of the
attestation report, allowing the guest to prove that the host provided the
expected configuration.

This proposal introduces a new `LaunchData` Custom Resource (CR) that carries the
launch-time attestation values for a VMI. This feature is behind the
InjectLaunchData Feature Gate.

The design separates two concerns:

1. **Immutable intent** in the VMI spec (`launchDataRef` field).
2. **Complete-on-create launch-data** in the `LaunchData` CR (spec immutable after creation, referenced from VMI spec).

In this VEP, we focus on the init-data
[initData](https://github.com/confidential-containers/trustee/blob/main/kbs/docs/initdata.md) that defines the key data structure and algorithms to inject any well-defined data from untrusted host into TEE. Other fields may be required in the future, e.g., vcek, idBlock, idAuth, authorKey, policy, guestVisibleWorkarounds.

## Motivation

Organizations deploying confidential VMs need the strongest possible
attestation guarantees. A key use case is encrypted disk provisioning: an
external operator injects a reference to a secret stored in a Key Broker
Service (KBS) into the guest before boot. The guest then attests to the KBS
and retrieves the secret only if the attestation passes. The injected values
become part of the hardware measurement, ensuring the host cannot tamper with
them undetected.

## Goals

- Expose TDX and SEV-SNP LaunchData fields through the CR
- Provide a declarative, Kubernetes-native mechanism for LaunchData injection
  that is compatible with GitOps workflows.

## Non Goals

- Implement the external operator that creates `LaunchData` resources.
- Verify the cryptographic relationship between the digest and the InitData bytes.

## Definition of Users

- Cluster Administrators: Configure RBAC permissions for the `LaunchData`
  resource.

- VM Users: Secret delivery.

## User Stories

- As a VM user, I want to tell the infrastructure to inject LaunchData into my
  confidential VM.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

The design separates immutable declarative intent (VMI spec) and complete-on-create launch-data (`LaunchData` CR).

### API extensions

New fields added to the VMI spec. These are set at creation time and never
mutated:

```go
type TDX struct {
    LaunchDataRef string `json:"LaunchDataRef,omitempty"`
}

type SEVSNP struct {
    LaunchDataRef string `json:"LaunchDataRef,omitempty"`
}
```
### LaunchData CRD

A new namespace-scoped Custom Resource that carries the launch-time values:

```go
type LaunchData struct {
  metav1.TypeMeta   `json:",inline"`
  metav1.ObjectMeta `json:"metadata,omitempty"`
  Spec   LaunchDataSpec `json:"spec"`
}

type LaunchDataSpec struct {
    // InitData carries the init-data fields for this VEP.
    // Future revisions may add other launch-time fields (e.g., vcek, idBlock).
    InitData *InitDataSpec `json:"initData,omitempty"`
}

type InitDataSpec struct {
    // TDX: base64-encoded 48-byte digest. Mutually exclusive with HostData.
    MRConfigId string `json:"mrConfigId,omitempty"`
    // SNP: base64-encoded 32-byte digest. Mutually exclusive with MRConfigId.
    HostData string `json:"hostData,omitempty"`
    // Init-Data bytes delivered via SMBIOS Type 11.
    OEMStrings []string `json:"oemStrings"`
}
```
`LaunchData` update after creation is rejected. The CR is destroyed when the VM is destroyed.

### VMI Status Condition

A new condition type is added to the VMI API:

```go
VirtualMachineInstanceInitDataReady VirtualMachineInstanceConditionType = "LaunchDataReady"
```

virt-controller reports this condition on the VMI status:

- `LaunchDataReady=False` with reason `LaunchDataNotFound` while the referenced
  `LaunchData` CR does not exist.
- `LaunchDataReady=True` with reason `LaunchDataReady` once the `LaunchData` CR
  exists with a valid spec and virt-controller proceeds to create the
  virt-launcher pod.

### Security Considerations

- The `LaunchData` resource is guarded by RBAC. Only components with the
  appropriate ClusterRole can create `LaunchData` resources. RBAC restricts
  which components can supply launch-data (access control).
- For InitData fields, e.g., `mrConfigId` and `hostData`, are measured by the hardware (TDX
  or SEV-SNP respectively). Any tampering is detectable by the guest during
  remote attestation. The host infrastructure provider cannot modify these
  values without invalidating the attestation report.
- The `oemStrings` field carries a reference (e.g., a KBS resource path), not
  the actual secret. The secret is only released by the KBS after the guest
  passes attestation.
- InitData contains measurement digests and resource paths, not
  cryptographic keys.
- A user with permission to create VMIs can
  set `launchDataRef` to reference a `LaunchData` CR created for a different VM.
  KubeVirt does not validate ownership of the referenced `LaunchData`. If the
  referenced CR still exists, the new VM boots with the same attestation values
  and may pass attestation, potentially gaining access to secrets in the KBS.
  Current mitigations are: (1) the external operator rejecting reuse of
  `LaunchData` CRs owned by a different VMI, and (2) the `ownerReference`
  garbage collection that removes the `LaunchData` when the original VMI is
  deleted, limiting the window of exposure. Cluster administrators should
  restrict `LaunchData` RBAC (especially `get` and `list`) to trusted
  components only. A future revision may add a `SubjectAccessReview` check in
  the admission webhook to verify that the VMI creator has RBAC `get` permission
  on the referenced `LaunchData`.

## API Examples

### TDX

Values are provisioned at runtime, the VMI is created with `LaunchDataRef`.
KubeVirt blocks VM startup until `LaunchData` CR is available.

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
            launchDataRef: my-tdx-vm-launchdata
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

LaunchData CR:

```yaml
apiVersion: kubevirt.io/v1
kind: LaunchData
metadata:
  name: my-tdx-vm-launchdata
  namespace: default
spec:
  initData:
    mrConfigId: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    oemStrings:
      - "kbs:///default/uuid/root"
```

### SNP

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
            launchDataRef: my-snp-vm-launchdata
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

LaunchData CR:

```yaml
apiVersion: kubevirt.io/v1
kind: LaunchData
metadata:
  name: my-snp-vm-launchdata
  namespace: default
spec:
  initData:
    hostData: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    oemStrings:
      - "kbs:///default/uuid/root"
```

## Alternatives

### Subresource-based injection

**Drawbacks:**
- Mutating the VMI spec at runtime causes GitOps configuration drift.
- Imperative one-shot PUT calls are not aligned with Kubernetes declarative
  patterns.
- JSON Patch with `test` operations for concurrency control is fragile.
- Field ownership between the user (static values) and the operator (dynamic
  values) is ambiguous when both live in the same spec field.

### Hook sidecar

A hook sidecar intercepts the libvirt domain XML at VMI creation time and
modifies it directly to inject `mrConfigId`/`hostData` and OEM strings.

**Drawbacks:** bypasses KubeVirt's API, not auditable, no RBAC, fragile
against KubeVirt upgrades that change the XML structure, requires a sidecar
container per VMI.

### KubeVirt-internal provisioner

KubeVirt itself could contact the provisioner service instead of relying on
an external operator.

**Drawbacks:** couples KubeVirt to a specific provisioner implementation and
contradicts the existing SEV model where KubeVirt only provides plumbing.

## Scalability

virt-controller adds one watch for `LaunchData` objects. It does not create the
virt-launcher pod until the referenced `LaunchData` exists with a valid spec.
The controller does not poll; it reacts to `LaunchData` creation events.

## Update/Rollback Compatibility

- The new VMI spec fields launchDataRef are optional and default to empty/nil.
  Existing VMI specs are unaffected.
- The `LaunchData` CRD does not exist in older versions. Rollback removes the
  CRD and its objects.
- The feature is guarded by the `InjectLaunchData` feature gate.

## Functional Testing Approach

- Unit tests for the `LaunchData`: Duplicate `LaunchData` rejection, write-once
  enforcement, invalid base64, wrong byte length, `MRConfigId`/`HostData`
  mutual exclusivity.
- Unit tests for the blocking logic that waits for the `LaunchData` CR to exist
  before starting the VM.
- E2E test: create a TDX/SNP VMI with `launchDataRef`, create a `LaunchData` CR,
  verify the VMI transitions from `Scheduled` to `Running`.

## Trust Model and Security Contract for InitData

### Normative InitData Chain

The complete trust chain for Init-Data follows the
[CoCo Init-Data specification](https://confidentialcontainers.org/docs/features/initdata/#integrity-and-attestation):

1. **InitData bytes** — The provisioner constructs the InitData TOML payload
   (containing policy, AA/CDH configuration, or KBS resource paths).

2. **Canonical digest** — The provisioner computes the digest using the
   algorithm declared in the InitData TOML (`sha384` for TDX, `sha256` for
   SNP).

3. **Launch register** — KubeVirt writes the digest to the hardware
   launch register via libvirt and delivers the InitData bytes via SMBIOS Type
   11 (`oemStrings`). **This is KubeVirt's sole role: atomic transport and
   commitment.** KubeVirt does not verify the relationship between the digest
   and the delivered bytes.

4. **Guest verify-before-parse** — The guest Attestation Agent computes
   `hash(SMBIOS Type 11 data)` and compares it with the value in its own
   hardware report (`TDREPORT.MR_CONFIG_ID` or SNP attestation report
   `HOSTDATA`). If mismatch, the guest MUST abort. The guest MUST NOT parse or
   act on the InitData before this verification succeeds.

5. **Attestation Service claim** — During remote attestation, the Attestation
   Service verifies the hardware evidence and includes the InitData digest as a
   verified claim in the attestation token.

6. **Relying-party / KBS policy authorization** — The KBS evaluates the
   verified claims (including the specific `mrConfigId`/`hostData` value)
   against its resource release policy. Only if the policy authorizes the
   digest does the KBS release the requested secret.

KubeVirt operates exclusively at step 3. It does not interpret the InitData
content, does not verify digests, and MUST NOT be treated as an authority that
establishes attestation success.

### InitData Representation

`mrConfigId` MUST be the SHA-384 digest of the InitData bytes delivered via
`oemStrings`. For SEV-SNP, `hostData` MUST be the SHA-256 digest of the same
payload.

### Negative Cases

- If the guest detects `hash(SMBIOS) != TDREPORT.mrConfigId` then MUST abort.
- If the KBS does not recognize the `mrConfigId` then MUST NOT release the key.
- If an `InitData` CR is created with a `mrConfigId` that does not correspond
  to the `oemStrings` then KubeVirt does not detect this (out of scope); the
  guest and KBS reject it at attestation time.
- If no `InitData` CR is created or validation fails then the VMI remains in
  `Pending` phase indefinitely; it never boots without valid InitData.

## Graduation Requirements

### Alpha

The feature will be implemented in Alpha.

### Beta

We expect e2e tests in Beta. We expect the API to be stable.

### GA
Remove feature gate
