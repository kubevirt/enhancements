# VEP #340: launchsecurity: Add InitData resource for TDX and SNP

## VEP Status Metadata

### Target releases (InitData)

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

This proposal introduces a new `InitData` Custom Resource (CR) that carries
the launch-time attestation values for a VMI.

The design cleanly separates two concerns:

1. **Immutable intent** in the VMI spec (`initDataRef` field).
2. **One-shot launch-data commitment** in the `InitData` CR (write-once, referenced from VMI spec).

In this VEP, InitData wording refers to
[initData](https://github.com/confidential-containers/trustee/blob/main/kbs/docs/initdata.md).
The Initdata Specification defines the key data structure and algorithms to
inject any well-defined data from untrusted host into TEE.

## Motivation

Organizations deploying confidential VMs need the strongest possible
attestation guarantees. A key use case is encrypted disk provisioning: an
external operator injects a reference to a secret stored in a Key Broker
Service (KBS) into the guest before boot. The guest then attests to the KBS
and retrieves the secret only if the attestation passes. The injected values
become part of the hardware measurement, ensuring the host cannot tamper with
them undetected.

## Goals

- Expose TDX and SEV-SNP InitData fields through the CR
- Provide a declarative, Kubernetes-native mechanism for InitData commitment
  that is compatible with GitOps workflows.

## Non Goals

- Implement the external operator that creates `InitData` resources.
- Verify the cryptographic relationship between the digest and the InitData bytes.

## Definition of Users

- Cluster Administrators: Configure RBAC permissions for the `InitData`
  resource.

- VM Users: Secret delivery.

## User Stories

- As a VM user, I want to tell the infrastructure to inject InitData into my
  confidential VM.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

The design separates immutable declarative intent (VMI spec) and one-shot
launch-data commitment (`InitData` CR).

### API extensions

New fields added to the VMI spec. These are set at creation time and never
mutated:

```go
type TDX struct {
    InitDataRef string `json:"initDataRef,omitempty"`
}

type SEVSNP struct {
    InitDataRef string `json:"initDataRef,omitempty"`
}
```
### InitData CRD

A new namespace-scoped Custom Resource that carries the launch-time values:

```go
type InitData struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec   InitDataSpec   `json:"spec"`
    Status InitDataStatus `json:"status,omitempty"`
}

type InitDataSpec struct {
    // TDX: base64-encoded 48-byte digest. Mutually exclusive with HostData.
    MRConfigId string `json:"mrConfigId,omitempty"`
    // SNP: base64-encoded 32-byte digest. Mutually exclusive with MRConfigId.
    HostData string `json:"hostData,omitempty"`
    // Init-Data bytes delivered via SMBIOS Type 11.
    OEMStrings []string `json:"oemStrings"`
}

type InitDataStatus struct {
    Conditions []metav1.Condition `json:"conditions,omitempty"`
}

```

Properties:
- The `InitData` CR has an `ownerReference` pointing to the VMI, enabling
  automatic garbage collection when the VMI is deleted.
- **Write-once**: updates to `.spec` are rejected after the `InitData` has
  reached `Committed`.

### Validation Rules

- `InitData` update to `.spec` after committed condition is True : InitData -
  rejected (write-once)

### Security Considerations

- The `InitData` resource is guarded by RBAC. Only components with the
  appropriate ClusterRole can create `InitData` resources. RBAC restricts
  which components can supply launch-data (access control).
- The committed `mrConfigId` and `hostData` are measured by the hardware (TDX
  or SEV-SNP respectively). Any tampering is detectable by the guest during
  remote attestation. The host infrastructure provider cannot modify these
  values after commitment without invalidating the attestation report.
- The `oemStrings` field carries a reference (e.g., a KBS resource path), not
  the actual secret. The secret is only released by the KBS after the guest
  passes attestation.
- The `InitData` CR contains measurement digests and resource paths, not
  cryptographic keys.

## API Examples

### TDX

Values are provisioned at runtime, the VMI is created with `initDataRef`.
KubeVirt blocks VM startup until a committed `InitData` CR is available.

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

InitData CR:

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
      uid: "a1b2c3d4-e5f6-..."
spec:
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
            initDataRef: my-snp-vm-initdata
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

InitData CR:

```yaml
apiVersion: kubevirt.io/v1
kind: InitData
metadata:
  name: my-snp-vm-initdata
  namespace: default
  ownerReferences:
    - apiVersion: kubevirt.io/v1
      kind: VirtualMachineInstance
      name: my-snp-vm
      uid: "f7e8d9c0-b1a2-..."
spec:
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

The `InitData` controller adds one watch and one reconcile per VMI creation.
The controller does not poll; it reacts to `InitData` creation events.

## Update/Rollback Compatibility

- The new VMI spec fields initDataRef are optional and default to empty/nil.
  Existing VMI specs are unaffected.
- The `InitData` CRD does not exist in older versions. Rollback removes the
  CRD; existing `InitData` objects are garbage-collected with their owning
  VMIs.
- The feature is guarded by the `InjectInitData` feature gate.

## Functional Testing Approach

- Unit tests for the `InitData`: Duplicate `InitData` rejection, write-once
  enforcement, invalid base64, wrong byte length, `MRConfigId`/`HostData`
  mutual exclusivity.
- Unit tests for the blocking logic that waits for a committed `InitData` CR
  before starting the VM.
- E2E test: create a TDX/SNP VMI with `initDataRef`, create an `InitData` CR,
  verify the VMI transitions from `Scheduled` to `Running` only after the
  `InitData` reaches `Committed`.

## Trust Model and Security Contract

### Normative InitData Chain

The complete trust chain for Init-Data follows the
[CoCo Init-Data specification](https://confidentialcontainers.org/docs/features/initdata/#integrity-and-attestation):

1. **InitData bytes** — The provisioner constructs the InitData TOML payload
   (containing policy, AA/CDH configuration, or KBS resource paths).

2. **Canonical digest** — The provisioner computes the digest using the
   algorithm declared in the InitData TOML (`sha384` for TDX, `sha256` for
   SNP).

3. **Launch register commitment** — KubeVirt writes the digest to the hardware
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
  `Scheduled` phase indefinitely; it never boots without valid InitData.

## Graduation Requirements

### Alpha

The feature will be implemented in Alpha.

### Beta

We expect e2e tests in Beta. We expect the API to be stable.

### GA
Remove feature gate
