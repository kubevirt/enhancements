# VEP #340: launchsecurity: Add LaunchData resource for TDX and SNP

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

Confidential VMs (CVMs) require per-VM configuration values to be injected
before boot so the guest can use them during remote attestation. These values
are incorporated into hardware measurement registers and become part of the
attestation report, allowing the guest to prove that the host provided the
expected configuration.

This proposal introduces a new `LaunchData` Custom Resource (CR) that carries the
launch-time attestation values for a VMI. This feature is behind the
InjectLaunchData Feature Gate.

The design separates two concerns:

1. **Immutable intent** in the VMI spec (`launchDataRef` on `launchSecurity`).
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
- Support for Live Migration.

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

A new field is added to `LaunchSecurity` in the VMI spec. It is set at
creation time and never mutated.

```go
type LaunchSecurity struct {
    // Name of the LaunchData CR that carries the launch-time values for this VMI.
    // When set, virt-controller blocks virt-launcher pod creation until a LaunchData CR
    // with this name exists in the VMI namespace.
    // +optional
    LaunchDataRef string `json:"launchDataRef,omitempty"`
    TDX           *TDX    `json:"tdx,omitempty"`
    SEVSNP        *SEVSNP `json:"sevSnp,omitempty"`
}
```
### LaunchData CRD

A new namespace-scoped Custom Resource that carries the launch-time values:

```go
type LaunchData struct {
  metav1.TypeMeta   `json:",inline"`
  // metadata.ownerReferences binds the LaunchData to its VirtualMachine.
  metav1.ObjectMeta `json:"metadata,omitempty"`
  Spec   LaunchDataSpec `json:"spec"`
}

type LaunchDataSpec struct {
    // InitData carries the init-data fields for this VEP.
    // Future revisions may add other launch-time fields (e.g., vcek, idBlock).
    InitData InitDataSpec `json:"initData"`
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
`LaunchData` spec update after creation is rejected.

A `LaunchData` belongs to exactly one `VirtualMachine`. The producer of the CR
expresses this with a standard `metadata.ownerReferences` entry with
`controller: true` that points to the owning VM, including its
`metadata.uid`. Binding to the UID lets a VM reuse its `LaunchData` across
stop/start, while a VM that is deleted and recreated with the same name gets a
new UID and cannot use it. The `LaunchData` is therefore created after the VM
exists. Kubernetes garbage collection deletes the `LaunchData` when its owning
VM is deleted; KubeVirt does not implement any custom cleanup.

Admission rejects a `LaunchData` that is not usable at launch. The CRD schema
rejects a create when `initData` is missing, because the field is required.
virt-api rejects the object when `initData` has neither `mrConfigId` nor
`hostData`, and when both digests are set. Exactly one digest is accepted. The
webhook does not look at the VMI platform: TDX and SNP each use one of those
fields, and which one is chosen by the producer of the CR.

virt-api also protects the ownership binding. On create, it rejects a
`LaunchData` that does not have exactly one `ownerReference` with
`controller: true`, or whose controller `ownerReference` does not reference a
`VirtualMachine`. On update, it rejects any change to the controller
`ownerReference`, including adding one, so a `LaunchData` cannot be rebound to
another VM. Removing it is allowed, since garbage collection removes it when
the VM is deleted with orphan propagation; a `LaunchData` without an owner can
no longer be used to launch any VMI.

The `oemStrings` field uses SMBIOS Type 11 (OEM Strings) as a generic
transport mechanism to deliver InitData bytes to the guest. This approach
was chosen because it does not require modifications to the guest image —
any guest with a standard SMBIOS reader can retrieve the values. Note that
on the x86 architecture, the total size of all SMBIOS tables is limited to
65535 bytes, which constrains the maximum payload that can be delivered
through this channel.

### VMI Status Condition

A new condition type is added to the VMI API:

```go
VirtualMachineInstanceLaunchDataReady VirtualMachineInstanceConditionType = "LaunchDataReady"
```

virt-controller reports this condition on the VMI status:

- `LaunchDataReady=False` with reason `LaunchDataNotFound` while the referenced
  `LaunchData` CR does not exist.
- `LaunchDataReady=True` with reason `LaunchDataReady` once the `LaunchData` CR
  exists, it is owned by the VM that owns the VMI and the corresponding digest
  is set. virt-controller then proceeds to create the virt-launcher pod.

virt-controller checks that the controller `ownerReference` of the
`LaunchData` references the VMI's owning VM by UID, and also that the
corresponding digest is set, e.g., mrConfigId for TDX. It performs the UID
check itself rather than relying on garbage collection having completed:
garbage collection is asynchronous, so a `LaunchData` whose VM was deleted can
still exist when a new VM with the same name is created, and only the UID
tells them apart. Admission rejects an object that is missing `initData` or
does not carry exactly one digest. Admission also rejects a VMI that sets
`launchDataRef` when it is standalone, i.e., not owned by a `VirtualMachine`.

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
- To prevent a `LaunchData` from being used by a VM other than the one it was
  provisioned for, including a VM recreated with the same name,
  virt-controller checks that the controller `ownerReference` of the
  `LaunchData` matches the UID of the VMI's owning VM. virt-api rejects any
  later change to that `ownerReference`, so the binding cannot be redirected
  to another VM.

### Feature Gate Enforcement

The `InjectLaunchData` feature gate is enforced at two points:

- **virt-api (admission webhook):** When a VMI is created with a
  `launchDataRef` field and the feature gate is disabled, the webhook rejects
  the creation request. This prevents users from creating VMIs that reference
  `LaunchData` CRs when the feature is not enabled.
- **virt-controller (core logic):** Before blocking pod creation to wait for a
  `LaunchData` CR, virt-controller checks whether the feature gate is enabled.
  If disabled, the controller rejects pod creation.

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
          launchDataRef: my-tdx-vm-launchdata
          tdx: {}
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
  ownerReferences:
  - apiVersion: kubevirt.io/v1
    kind: VirtualMachine
    name: my-tdx-vm
    uid: 6f1c2a3e-8b4d-4e5f-9a0b-1c2d3e4f5a6b  # metadata.uid of my-tdx-vm
    controller: true
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
          launchDataRef: my-snp-vm-launchdata
          sevSnp: {}
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
  ownerReferences:
  - apiVersion: kubevirt.io/v1
    kind: VirtualMachine
    name: my-snp-vm
    uid: 2b7e9d41-0c3f-4a6e-b8d2-5f1a7c9e3d0b  # metadata.uid of my-snp-vm
    controller: true
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

## Does it belong to core KubeVirt?

This feature must live in core KubeVirt because it requires integration at
multiple internal layers that are not accessible to external components.
The `LaunchData` CRD is defined and registered by KubeVirt itself,
providing a Kubernetes-native interface through which external operators
can supply launch-time values. Both the CRD definition and the
consumption of those values are handled within KubeVirt internals. The launch
register values (`mrConfigId` and `hostData`) are written to the libvirt
domain XML by the virt-launcher converter during VM creation, which is an
internal code path that cannot be reached by an external controller or a
hook sidecar. The pod creation blocking logic must run inside
virt-controller since no external component can gate the virt-launcher pod
lifecycle. The environment variables that carry the InitData values are
injected into the pod template at render time, which is also internal to
virt-controller.

## Scalability

virt-controller adds one watch for `LaunchData` objects. It does not create the
virt-launcher pod until the referenced `LaunchData` exists, its controller
`ownerReference` matches the UID of the VM that owns the VMI, and the digest
for that platform is set:
`mrConfigId` for TDX, `hostData` for SEV-SNP. If that field is not set, the
controller does not create the pod. It does not validate the digest content;
admission has already accepted the object. The controller does not poll; it
reacts to `LaunchData` creation events.

## Update/Rollback Compatibility

- The new VMI spec field `launchDataRef` on `launchSecurity` is optional and
  defaults to empty. Existing VMI specs are unaffected.
- The `LaunchData` CRD does not exist in older versions. Rollback removes the
  CRD and its objects.
- The feature is guarded by the `InjectLaunchData` feature gate.

## Functional Testing Approach

- Unit test: the CRD schema rejects a `LaunchData` that does not include
  `initData`.
- Unit tests: virt-api rejects a `LaunchData` when `initData` has no digest,
  when both `mrConfigId` and `hostData` are set, when a digest is not valid
  base64, or when a digest has the wrong length. virt-api also rejects an update
  of the spec of an existing `LaunchData`.
- Unit tests: virt-api rejects a `LaunchData` create without exactly one
  controller `ownerReference` to a `VirtualMachine`. It rejects an update that
  changes or adds the controller `ownerReference`, and accepts one that removes
  it.
- Unit tests: virt-api rejects the creation of a VMI that sets `launchDataRef`
  and has no `ownerReference` to a `VirtualMachine` with `controller: true`,
  and accepts it when the VMI is owned by a `VirtualMachine`.
- Unit test: virt-controller does not create the virt-launcher pod while the
  referenced `LaunchData` does not exist.
- Unit test: virt-controller does not create the virt-launcher pod when the
  controller `ownerReference` of the `LaunchData` does not match the UID of the
  VMI's owning VM, e.g., when the VM was deleted and recreated with the same
  name.
- Unit test: virt-controller creates the virt-launcher pod when the `LaunchData`
  exists, is owned by the VMI's owning VM with a matching UID, and the digest
  for that platform is set.
- Unit test: virt-controller does not create the virt-launcher pod when the VM
  is TDX and only `hostData` is set, or when the VM is SEV-SNP and only
  `mrConfigId` is set.

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
- If no `InitData` CR is created the VMI remains in `Pending` phase
  indefinitely; it never boots without valid InitData.

## Graduation Requirements

### Alpha

The feature will be implemented in Alpha. We expect e2e test.

### Beta

We expect the API to be stable.

### GA
Remove feature gate
