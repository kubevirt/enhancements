# VEP #340: launchsecurity: Add InitData resource for TDX and SNP

## VEP Status Metadata

### Target releases (InitData)

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
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

1. **Immutable intent** in the VMI spec (`attestation` marker or static values).
2. **One-shot launch-data commitment** in the `InitData` CR (write-once, bound to VMI UID).

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

- Expose TDX and SEV-SNP InitData fields declaratively (static mode) or at
  runtime via a dedicated resource (external mode).
- Provide a declarative, Kubernetes-native mechanism for InitData commitment
  that is compatible with GitOps workflows.

## Non Goals

- Implement the external operator that creates `InitData` resources.
- Verify the cryptographic relationship between the digest and the InitData bytes.

## Definition of Users

- Cluster Administrators: Deploy and manage the external operator that
  automates the attestation provisioning flow. Configure RBAC permissions for
  the `InitData` resource.

- VM Users: secret delivery flow to work transparently once the cluster
  administrator has configured the external operator.

## User Stories

- As a VM user, I want to configure InitData registers in the VM definition so
  that they are injected into the VM at boot time.
- As a VM user, I want to tell the infrastructure to inject InitData into my
  confidential VM.
- As a cluster administrator, I want to configure an external operator
  that automates the attestation provisioning flow so that VM users do not
  need to manage the injection manually.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

The design separates immutable declarative intent (VMI spec) and one-shot
launch-data commitment (`InitData` CR).

### API extensions (VMI spec — immutable)

New fields added to the VMI spec. These are set at creation time and never
mutated:

```go
type TDX struct {
    // Static mode: base64-encoded 48-byte value set at creation time.
    // Mutually exclusive with Attestation.
    MRConfigId  string          `json:"mrConfigId,omitempty"`
    // External mode: signals that an InitData CR will provide the values.
    // Mutually exclusive with MRConfigId.
    Attestation *TDXAttestation `json:"attestation,omitempty"`
}

type SEVSNP struct {
    // Static mode: base64-encoded 32-byte value set at creation time.
    // Mutually exclusive with Attestation.
    HostData    string              `json:"hostData,omitempty"`
    // External mode: signals that an InitData CR will provide the values.
    // Mutually exclusive with HostData.
    Attestation *SEVSNPAttestation  `json:"attestation,omitempty"`
}

type Firmware struct {
    // ... existing fields ...
    // Static OEM strings delivered via SMBIOS Type 11 (static mode only).
    OEMStrings []string `json:"oemStrings,omitempty"`
}
```

The `Attestation` field is a mode selector: when present, it signals that the
VMI requires an `InitData` CR before boot. Without `Attestation`, the values
are provided directly in the spec (static mode). The two modes are **mutually
exclusive** and enforced at admission time.

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
    // Immutable reference binding this InitData to a specific VMI instance.
    VMIRef VMIReference `json:"vmiRef"`
    // TDX: base64-encoded 48-byte digest. Mutually exclusive with HostData.
    MRConfigId string `json:"mrConfigId,omitempty"`
    // SNP: base64-encoded 32-byte digest. Mutually exclusive with MRConfigId.
    HostData string `json:"hostData,omitempty"`
    // Init-Data bytes delivered via SMBIOS Type 11.
    OEMStrings []string `json:"oemStrings"`
}

type VMIReference struct {
    Name string    `json:"name"`
    UID  types.UID `json:"uid"`
}

type InitDataStatus struct {
    Conditions []metav1.Condition `json:"conditions,omitempty"`
    Phase      InitDataPhase      `json:"phase,omitempty"`
}

type InitDataPhase string

const (
    InitDataPending   InitDataPhase = "Pending"
    InitDataCommitted InitDataPhase = "Committed"
    InitDataFailed    InitDataPhase = "Failed"
)
```

Properties:
- The `InitData` CR has an `ownerReference` pointing to the VMI, enabling
  automatic garbage collection when the VMI is deleted.
- **Write-once**: updates to `.spec` are rejected after the `InitData` has
  reached `Committed`.
- **One-per-VMI**: creation of a second `InitData` are rejected for the same
  VMI UID.
- **UID binding**: `vmiRef.uid` must match the target VMI's UID; this prevents
  stale or replayed `InitData` objects from being applied to a different VMI
  instance with the same name.

### Validation Rules

- `Attestation` + `MRConfigId`/`HostData` present in spec: VMI - rejected (mutually exclusive modes)
- `InitData` with `vmiRef.uid` not matching the VMI : InitData - rejected
- `InitData` for a VMI without `Attestation` : InitData - rejected
- `InitData` update to `.spec` after `Phase = Committed` : InitData - rejected (write-once)
- Second `InitData` for the same VMI UID : InitData - rejected

### Security Considerations

- The `InitData` resource is guarded by RBAC. Only components with the
  appropriate ClusterRole can create `InitData` resources. RBAC restricts
  which components can supply launch-data (access control). Authorization of
  the injected values themselves is enforced by the KBS policy at attestation
  time.
- The committed `mrConfigId` and `hostData` are measured by the hardware (TDX
  or SEV-SNP respectively). Any tampering is detectable by the guest during
  remote attestation. The host infrastructure provider cannot modify these
  values after commitment without invalidating the attestation report.
- The `oemStrings` field carries a reference (e.g., a KBS resource path), not
  the actual secret. The secret is only released by the Key Broker Service
  after the guest passes attestation.
- The design does not store secrets in Kubernetes objects. The `InitData` CR
  contains measurement digests and resource paths, not cryptographic keys.
- The write-once property and UID binding ensure that an `InitData` cannot be
  replayed or reused for a different VMI instance.

## API Examples

### TDX with static mrConfigId

When `mrConfigId` is known at VM creation time, it can be set directly in
the YAML. No `attestation` field and no `InitData` CR is needed:

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
            mrConfigId: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        firmware:
          oemStrings:
            - "kbs:///default/uuid/root"
          bootloader:
            efi:
              secureBoot: false
```

### SNP with static hostData

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
            hostData: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        firmware:
          oemStrings:
            - "kbs:///default/uuid/root"
          bootloader:
            efi:
              secureBoot: false
```

### TDX with external operator (InitData CR)

When the values are provisioned at runtime by an external operator, the VMI
is created with `attestation`. KubeVirt blocks VM startup until a committed
`InitData` CR is available; no `startStrategy: Paused` is required.

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
            attestation: {}
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
  vmiRef:
    name: my-tdx-vm
    uid: "a1b2c3d4-e5f6-..."
  mrConfigId: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  oemStrings:
    - "kbs:///default/uuid/root"
```

### SNP with external operator (InitData CR)

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
            attestation: {}
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
  vmiRef:
    name: my-snp-vm
    uid: "f7e8d9c0-b1a2-..."
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

The `InitData` controller adds one watch and one reconcile per VMI creation
in external mode. The controller does not poll; it reacts to `InitData` creation events.

## Update/Rollback Compatibility

- The new VMI spec fields (`mrConfigId`, `hostData`, `oemStrings`,
  `attestation`) are optional and default to empty/nil. Existing VMI specs are
  unaffected.
- The `InitData` CRD does not exist in older versions. Rollback removes the
  CRD; existing `InitData` objects are garbage-collected with their owning
  VMIs.
- The feature is guarded by the `InjectInitData` feature gate.
- VMs that do not set `attestation` (static mode or no launch security) behave
  identically to before.

## Functional Testing Approach

- Unit tests for the `InitData`: UID mismatch rejection, duplicate `InitData`
  rejection, write-once enforcement, invalid base64, wrong byte length,
  `MRConfigId`/`HostData` mutual exclusivity.
- Unit tests for the blocking logic that waits for a committed `InitData` CR before starting the VM.
- Tests verifying that `attestation` + `mrConfigId` is rejected.
- E2E test: create a TDX/SNP VMI with `attestation`, create an `InitData` CR,
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
