# VEP #340: launchsecurity: add injectInitdata subresource for TDX and SNP

## VEP Status Metadata

### Target releases

This VEP does not have graduation phases guarded by a feature gate.
- Ship target version:


### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

Confidential VMs (CVMs) require per-VM configuration values to be injected
before boot so the guest can use them during remote attestation. These values
are incorporated into hardware measurement registers and become part of the
attestation report, allowing the guest to prove that the host provided the
expected configuration.

This proposal adds two new KubeVirt subresource endpoints that allow an
external operator to inject these values into a VMI while it is in the
`Scheduled` phase, before QEMU starts:

- `PUT virtualmachineinstances/{name}/tdx/injectInitdata` — for Intel TDX
- `PUT virtualmachineinstances/{name}/snp/injectInitdata` — for AMD SEV-SNP

The design follows the existing SEV attestation pattern where `virt-handler`
blocks VM startup until the required attestation data is provided via a
subresource, and the VM is explicitly unpaused by the external operator
after injection.

In this VEP, Initdata wording refers to
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

Currently, KubeVirt supports this flow for SEV via the
`sev/fetchCertChain` and `sev/setupSession` subresources. However, Intel TDX
and AMD SEV-SNP have no equivalent mechanism. Users resort to hook sidecars
that modify the libvirt domain XML directly, bypassing KubeVirt's API and
losing auditability, RBAC control, and compatibility with future KubeVirt
versions.

This proposal fills the gap by extending the VMI API with the fields and
subresources needed for TDX and SEV-SNP attestation-based secret delivery.

## Goals

- Expose TDX and SEV-SNP initdata fields in the VMI API so users can
  configure attestation-relevant registers (`mrConfigId`, `hostData`,
  `oemStrings`) declaratively or at runtime.
- Provide a KubeVirt-native mechanism for external operators to inject
  initdata into a VMI after scheduling and before boot.
- Extend the existing SEV attestation pattern to TDX and SEV-SNP.

## Non Goals

- This proposal does not implement the external operator that calls the
  subresource. The operator is a separate component outside of KubeVirt.
- This proposal does not define how the guest uses the injected values after
  boot (that is guest-side logic, e.g., `systemd-repart`, `systemd-cryptsetup`).

## Definition of Users

- Cluster Administrators: Deploy and manage the external operator that
  automates the attestation provisioning flow. Configure RBAC permissions for
  the `injectInitdata` subresource.

- VM Users: secret delivery flow to work transparently once the cluster
  administrator has configured the external operator.

## User Stories

- As a VM user, I want to configure initdata registers like `mrConfigId`,
  `hostData`, and `oemStrings` in the VM definition so that they are
  injected into the VM at boot time.
- As a VM user, I want to tell the Kubernetes infrastructure to inject a
  secret key into my confidential VM.
- As a cluster administrator, I want to configure an external operator
  that automates the attestation provisioning flow so that VM users do not
  need to manage the injection manually.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

The design follows the existing SEV attestation pattern in KubeVirt. The key
components are:

### API extensions

New fields added to the VMI spec:

```go
// In schema.go
type TDX struct {
    MRConfigId  string          `json:"mrConfigId,omitempty"`
    Attestation *TDXAttestation `json:"attestation,omitempty"`
}

type SEVSNP struct {
    HostData    string              `json:"hostData,omitempty"`
    Attestation *SEVSNPAttestation  `json:"attestation,omitempty"`
}

type Firmware struct {
    // ... existing fields ...
    OEMStrings []string `json:"oemStrings,omitempty"`
}
```

The `Attestation` field is a marker: when present, it signals that the VMI
requires initdata injection before boot. Without `Attestation`, the registers
can be set directly from the YAML. `mrConfigId`/`hostData` start empty and are
filled via the subresource. `oemStrings` carries a reference (e.g., a KBS
resource path) that the guest reads from SMBIOS Type 11 at boot.

### Subresource endpoints

Two new `PUT` endpoints registered in `virt-api`:

| Endpoint | Platform | Payload |
|---|---|---|
| `PUT .../virtualmachineinstances/{name}/tdx/injectInitdata` | Intel TDX | `{"mrConfigId": "<base64>", "oemStrings": [...]}` |
| `PUT .../virtualmachineinstances/{name}/snp/injectInitdata` | AMD SEV-SNP | `{"hostData": "<base64>", "oemStrings": [...]}` |

Both endpoints:
1. Verify the VMI is in `Scheduled` phase.
2. Verify the `Attestation` field is set.
3. Verify the initdata field (`mrConfigId`/`hostData`) is not already set.
4. Validate the base64 value decodes to the correct length (48 bytes for TDX,
   32 bytes for SNP).
5. Apply a JSON patch to the VMI spec.

### virt-handler blocking

When `TDX.Attestation` (or `SEVSNP.Attestation`) is set, `virt-handler`
blocks VM synchronization until `mrConfigId` (or `hostData`) is populated.
This prevents QEMU from starting before the external operator has injected
the values. The check mirrors the existing `shouldWaitForSEVAttestation`
function.

### Libvirt XML translation

The new fields are translated to libvirt domain XML in the converter layer:

- `TDX.MRConfigId` -> `<launchSecurity type="tdx"><mrConfigId>...</mrConfigId></launchSecurity>`
- `SEVSNP.HostData` -> `<launchSecurity type="sev-snp"><hostData>...</hostData></launchSecurity>`
- `Firmware.OEMStrings` -> `<sysinfo type="smbios"><oemStrings><entry>...</entry></oemStrings></sysinfo>`

### Start strategy

The VMI must be created with `startStrategy: Paused`. This ensures QEMU
boots in paused state, giving the external operator a window to:
1. Call the provisioner to obtain the values.
2. Inject them via `injectInitdata`.
3. Wait for the VMI to reach `Running` phase (QEMU started but paused).
4. Call `unpause` to resume guest execution.

This is validated at admission time: if `Attestation` is set but
`startStrategy` is not `Paused`, the VMI is rejected.

### Security Considerations

- The `injectInitdata` subresource is guarded by RBAC. Only components with
  the appropriate ClusterRole can call the endpoint. This prevents unauthorized
  injection of attestation values.
- The injected `mrConfigId` and `hostData` are measured by the hardware (TDX
  or SEV-SNP respectively). Any tampering is detectable by the guest during
  remote attestation. The host infrastructure provider cannot modify these
  values after injection without invalidating the attestation report.
- The `oemStrings` field carries a reference (e.g., a KBS resource path), not
  the actual secret. The secret is only released by the Key Broker Service
  after the guest passes attestation.
- The design does not store secrets in Kubernetes objects. The VMI spec only
  contains measurement IDs and resource paths.

## API Examples

### TDX with static mrConfigId

When `mrConfigId` is known at VM creation time, it can be set directly in
the YAML. No `attestation` field, no `startStrategy: Paused`, and no
subresource call is needed:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        launchSecurity:
          tdx:
            mrConfigId: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

### SNP with static hostData

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        launchSecurity:
          sevSnp:
            hostData: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

### TDX with dynamic injection (external operator)

When the values are provisioned at runtime by an external operator, the VMI
is created with `attestation` and `startStrategy: Paused`. The operator
calls the subresource after the VMI reaches the `Scheduled` phase:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      startStrategy: Paused
      domain:
        launchSecurity:
          tdx:
            attestation: {}
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

```bash
curl -X PUT \
  /apis/subresources.kubevirt.io/v1/namespaces/default/virtualmachineinstances/my-vm/tdx/injectInitdata \
  -H "Content-Type: application/json" \
  -d '{
    "mrConfigId": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "oemStrings": ["kbs:///default/uuid/root"]
  }'
```

### SNP with dynamic injection (external operator)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      startStrategy: Paused
      domain:
        launchSecurity:
          sevSnp:
            attestation: {}
        firmware:
          bootloader:
            efi:
              secureBoot: false
```

```bash
curl -X PUT \
  /apis/subresources.kubevirt.io/v1/namespaces/default/virtualmachineinstances/my-vm/snp/injectInitdata \
  -H "Content-Type: application/json" \
  -d '{
    "hostData": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    "oemStrings": ["kbs:///default/uuid/root"]
  }'
```

## Alternatives

### Hook sidecar

The current approach used before this proposal. A hook sidecar intercepts
the libvirt domain XML at VMI creation time and modifies it directly to inject
`mrConfigId`/`hostData` and OEM strings.

**Drawbacks:** bypasses KubeVirt's API, not auditable, no RBAC, fragile
against KubeVirt upgrades that change the XML structure, requires a sidecar
container per VMI.

### KubeVirt-internal provisioner

KubeVirt itself could contact the provisioner service instead of relying on
an external operator.

**Drawbacks:** couples KubeVirt to a specific provisioner implementation and
contradicts the existing SEV model where KubeVirt only provides plumbing.

## Scalability

The subresource adds one `PUT` call per VMI creation (or two if counting
`unpause`). This is comparable to the existing SEV attestation flow and does
not introduce new watchers or polling loops in KubeVirt itself. The external
operator's scalability is its own concern.

## Update/Rollback Compatibility

- The new fields (`mrConfigId`, `hostData`, `oemStrings`, `attestation`) are
  optional and default to empty/nil. Existing VMI specs are unaffected.
- The subresource endpoints are guarded by the existing feature gates
  `WorkloadEncryptionTDX` and `WorkloadEncryptionSEVSNP`. Disabling the
  feature gate removes access to the endpoints without affecting existing VMs.
- Rollback to a version without this feature is safe: the new fields are
  ignored by older versions and VMs that do not set `attestation` behave
  identically to before.

## Functional Testing Approach

- Unit tests for the `TDXInjectInitdataHandler` and `SNPInjectInitdataHandler`:
  valid injection, duplicate injection rejection, wrong phase rejection,
  invalid base64, wrong byte length.
- Unit tests for `shouldWaitForTDXAttestation` and
  `shouldWaitForSNPAttestation` in `virt-handler`.
- Unit tests for libvirt XML converter verifying `mrConfigId`, `hostData`, and
  `oemStrings` appear in the generated XML.
- Admission webhook tests verifying that `attestation` without
  `startStrategy: Paused` is rejected.
- E2E test with a sample attester: create a TDX/SNP VMI with `attestation`,
  call the subresource, verify the VMI transitions through
  `Scheduled` → `Running` → unpaused.

## Graduation Requirements

### Alpha

The feature will be implemented in Alpha. We do not know if it will be possible
to have e2e tests in Alpha due to lack of TDX hardware. We expect the feature
to be merged without the e2e tests.

### Beta

We expect e2e tests in Beta. We expect the API to be stable.

### GA
