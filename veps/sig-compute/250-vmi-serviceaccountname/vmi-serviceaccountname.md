# VEP #250: Add serviceAccountName to VirtualMachineInstance Spec

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: v1.10
- This VEP targets GA for version: v1.11

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds a `serviceAccountName` field to `VirtualMachineInstanceSpec`, decoupling the concern of "which service account the virt-launcher pod runs as" from "whether the service account token is exposed to the VM as a disk/filesystem." Today these two concerns are conflated in the `serviceAccount` volume source, forcing users to expose a service account token volume to the VM even when they only need the pod to run under a specific identity.

## Motivation

KubeVirt's current mechanism for specifying which service account a VM runs as is the `serviceAccount` volume source. When a user specifies this volume, KubeVirt does two things:

1. Sets `pod.spec.serviceAccountName` on the virt-launcher pod
2. Mounts the service account token as an ISO disk or virtiofs filesystem exposed to the VM guest

This coupling made sense when the primary use case was "I want the VM to access the Kubernetes API using a specific service account." However, with the introduction of ContainerPath volumes (VEP #165) and the growing adoption of cloud provider workload identity systems (AWS IRSA, Azure Workload Identity), a new pattern has emerged: users need the virt-launcher pod to run as a specific service account so that external mutating webhooks inject the correct credentials, but they **do not want** the Kubernetes service account token exposed to the VM.

For example, with AWS IRSA:

1. User creates a ServiceAccount annotated with `eks.amazonaws.com/role-arn`
2. User wants the virt-launcher pod to run as this ServiceAccount so the EKS Pod Identity Webhook injects AWS credentials
3. User wants to expose the injected AWS credentials to the VM via ContainerPath
4. User does **not** want the Kubernetes service account token exposed as a disk to the VM

Currently, the user is forced to configure:

```yaml
volumes:
- name: aws-irsa-sa
  serviceAccount:
    serviceAccountName: aws-irsa-sa  # Required just to set pod identity
- name: aws-token
  containerPath:
    path: /var/run/secrets/eks.amazonaws.com/serviceaccount
    readOnly: true
```

The `serviceAccount` volume creates an unnecessary disk/filesystem in the VM that the user must configure but does not want. With `spec.serviceAccountName`, the user can simply write:

```yaml
serviceAccountName: aws-irsa-sa
volumes:
- name: aws-token
  containerPath:
    path: /var/run/secrets/eks.amazonaws.com/serviceaccount
    readOnly: true
```

Cloud provider credential injection is the most prominent example, but there are other reasons to run a pod as a specific service account without wanting the token exposed to the VM:

- **Image pull secrets**: ServiceAccounts can have `imagePullSecrets` attached, allowing the virt-launcher pod to pull from private registries without the token being relevant to the VM workload
- **Service mesh authorization**: Istio AuthorizationPolicy and similar mesh configurations use service account identity to control traffic between workloads
- **Admission and policy enforcement**: OPA/Gatekeeper, Kyverno, or Pod Security Admission policies that scope permissions by service account
- **External secret injection**: Vault Agent Injector and similar tools use service account identity to determine which secrets to inject into the pod
- **Audit attribution**: Service account identity appears in Kubernetes audit logs for traceability

In all of these cases, the service account is used as a **pod-level identity** rather than a credential to expose inside the VM. The current API forces users to conflate these concerns.

This mirrors Kubernetes Pods, which have always had `spec.serviceAccountName` as a first-class field separate from volume configuration.

## Goals

- Add `serviceAccountName` to `VirtualMachineInstanceSpec` to set the virt-launcher pod's service account identity without requiring a volume
- Maintain backward compatibility with existing `serviceAccount` volume usage
- Align KubeVirt's API with Kubernetes Pod API conventions
- Simplify the user experience for workload identity patterns (IRSA, Azure Workload Identity)

## Non Goals

- **Removing the `serviceAccount` volume type**: The existing volume type remains useful when users explicitly want the token exposed to the VM
- **Changing the behavior of existing `serviceAccount` volumes**: Existing VMs using `serviceAccount` volumes continue to work identically
- **Adding `automountServiceAccountToken` to VMI spec**: This proposal focuses solely on `serviceAccountName`. Whether to expose the token to the VM is handled by the presence or absence of a `serviceAccount` volume
- **Token projection configuration**: Advanced token projection settings (audiences, expiration) are out of scope for this proposal

## Definition of Users

- **Platform Engineers**: Teams deploying KubeVirt in cloud environments that use workload identity systems (AWS IRSA, Azure Workload Identity)
- **Application Developers**: Developers running VMs that need cloud provider credentials but don't need direct Kubernetes API access from within the VM
- **Kubernetes Administrators**: Teams that use service account identity for policy enforcement, service mesh authorization, image pull secrets, or audit attribution
- **Security-Conscious Operators**: Administrators who want to follow the principle of least privilege by not exposing Kubernetes service account tokens to VMs unnecessarily

## User Stories

- As a platform engineer on EKS, I want my VirtualMachine's virt-launcher pod to run as a specific ServiceAccount for IRSA credential injection, without exposing the Kubernetes service account token to the VM guest
- As an application developer, I want to specify which ServiceAccount my VM runs as without having to configure an unwanted service account token volume
- As a security-conscious operator, I want to avoid exposing Kubernetes service account tokens to VMs that only need cloud provider credentials, reducing the attack surface inside the VM
- As a KubeVirt user migrating from Pods, I expect `serviceAccountName` to work similarly to how it works on Pods, as a simple field on the spec rather than requiring a volume

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### New API Field

A new `serviceAccountName` field is added to `VirtualMachineInstanceSpec`:

```go
type VirtualMachineInstanceSpec struct {
    // ...existing fields...

    // ServiceAccountName is the name of the ServiceAccount to use to run the
    // virt-launcher pod. This sets pod.spec.serviceAccountName but does NOT
    // automatically expose the service account token to the VM guest.
    // To expose the token to the VM, use a serviceAccount volume.
    // +optional
    ServiceAccountName string `json:"serviceAccountName,omitempty"`
}
```

### Interaction with ServiceAccount Volumes

When both `spec.serviceAccountName` and a `serviceAccount` volume are specified, they **must reference the same service account**. If they differ, the VM will be rejected with a validation error. This prevents confusing configurations where the pod identity and the exposed token come from different service accounts.

| `spec.serviceAccountName` | `serviceAccount` volume | Pod serviceAccountName | Token exposed to VM |
|---|---|---|---|
| Set to "my-sa" | Not present | "my-sa" | No |
| Not set | Present with "my-sa" | "my-sa" | Yes |
| Set to "my-sa" | Present with "my-sa" | "my-sa" | Yes |
| Set to "my-sa" | Present with "other-sa" | **Validation error** | N/A |
| Not set | Not present | "" (default) | No |

### Pod Template Rendering

The virt-controller's pod template rendering logic is updated:

```go
func serviceAccountName(vmi *v1.VirtualMachineInstance) string {
    // spec.serviceAccountName takes precedence as the source of truth
    if vmi.Spec.ServiceAccountName != "" {
        return vmi.Spec.ServiceAccountName
    }
    // Fall back to serviceAccount volume for backward compatibility
    for _, volume := range vmi.Spec.Volumes {
        if volume.ServiceAccount != nil {
            return volume.ServiceAccount.ServiceAccountName
        }
    }
    return ""
}
```

The pod's `automountServiceAccountToken` is set to `true` only when a `serviceAccount` volume is present. When only `spec.serviceAccountName` is set (no `serviceAccount` volume), `automountServiceAccountToken` remains `false`. Cloud provider webhooks (IRSA, Azure Workload Identity) inject their own projected volumes independently of this setting, so they work regardless.

### Validation

1. If both `spec.serviceAccountName` and a `serviceAccount` volume are present, the service account names must match
2. `spec.serviceAccountName`, if set, must be a valid Kubernetes service account name (DNS subdomain)
3. At most one `serviceAccount` volume is allowed (existing validation, unchanged)

### Migration and Backward Compatibility

- Existing VMs using `serviceAccount` volumes continue to work without modification
- No migration is needed; both mechanisms coexist
- The `serviceAccount` volume is **not** deprecated by this proposal. It remains the correct choice when you want the token exposed to the VM

## API Examples

### Cloud Provider Workload Identity (Recommended Pattern)

Use `spec.serviceAccountName` with ContainerPath volumes for cloud provider credential injection:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: aws-vm
spec:
  runStrategy: Always
  template:
    spec:
      serviceAccountName: aws-irsa-sa
      domain:
        devices:
          filesystems:
          - name: aws-token
            virtiofs: {}
          resources:
            requests:
              memory: 1Gi
      volumes:
      - name: aws-token
        containerPath:
          path: /var/run/secrets/eks.amazonaws.com/serviceaccount
          readOnly: true
```

### VM Needing Kubernetes API Access (Existing Pattern, Unchanged)

Continue using the `serviceAccount` volume when the VM needs the Kubernetes service account token:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: k8s-api-vm
spec:
  runStrategy: Always
  template:
    spec:
      domain:
        devices:
          filesystems:
          - name: sa-token
            virtiofs: {}
          resources:
            requests:
              memory: 1Gi
      volumes:
      - name: sa-token
        serviceAccount:
          serviceAccountName: my-sa
```

### Simple Pod Identity Without Any Token Exposure

For VMs that just need to run under a specific identity without exposing any tokens:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: identity-only-vm
spec:
  runStrategy: Always
  template:
    spec:
      serviceAccountName: my-sa
      domain:
        resources:
          requests:
            memory: 1Gi
```

## Alternatives

### Alternative 1: Keep ServiceAccount Volume As-Is (Status Quo)

Continue requiring users to create a `serviceAccount` volume even when they don't want the token exposed.

**Pros:**
- No API changes needed
- Existing documentation and examples remain valid

**Cons:**
- Forces users to expose an unnecessary service account token volume to the VM
- Creates unwanted disk/filesystem devices in the VM
- Semantically confusing: users configure a volume they don't want just to set pod identity
- Doesn't align with Kubernetes Pod API conventions
- Particularly awkward with ContainerPath volumes where the user explicitly wants different credentials

**Conclusion:** Rejected because it conflates pod identity with token exposure

### Alternative 2: New ServiceAccountToken Volume Type + Deprecate ServiceAccount Volume

Create a new `serviceAccountToken` volume type that only handles token exposure and deprecate the existing `serviceAccount` volume.

```go
type ServiceAccountTokenVolumeSource struct {
    // Audience for the projected token
    Audience string `json:"audience,omitempty"`
    // ExpirationSeconds for the projected token
    ExpirationSeconds *int64 `json:"expirationSeconds,omitempty"`
}
```

Combined with `spec.serviceAccountName` for pod identity.

**Pros:**
- Cleaner separation of concerns
- More flexible token configuration (audience, expiration)
- Aligns with Kubernetes projected volume token sources

**Cons:**
- Requires deprecating an existing stable API, which is disruptive
- Two volume types (`serviceAccount` and `serviceAccountToken`) would coexist during deprecation
- More complex migration path
- Over-engineers the solution for the immediate problem

**Conclusion:** Could be a future enhancement but is too disruptive for the immediate need. The simpler `spec.serviceAccountName` addition solves the pressing problem without breaking changes.

### Alternative 3: Add Optional Flag to ServiceAccount Volume to Skip Token Exposure

Add a field like `exposeToken: false` to the existing `ServiceAccountVolumeSource`:

```go
type ServiceAccountVolumeSource struct {
    ServiceAccountName string `json:"serviceAccountName,omitempty"`
    // ExposeToken controls whether the service account token is exposed
    // to the VM. Defaults to true for backward compatibility.
    // +optional
    ExposeToken *bool `json:"exposeToken,omitempty"`
}
```

**Pros:**
- Minimal API surface change
- No new fields on VMI spec

**Cons:**
- Semantically odd: a "volume" that doesn't create a volume
- The `serviceAccount` volume would still appear in the volumes list even though it creates nothing
- Users must still configure a disk/filesystem entry that maps to nothing
- Doesn't align with the Kubernetes Pod API pattern

**Conclusion:** Rejected because a volume that creates no volume is confusing

### Alternative 4: Make ServiceAccountName Optional in ServiceAccount Volume

Allow `serviceAccount` volume without `serviceAccountName`, deriving it from a new `spec.serviceAccountName`.

**Pros:**
- Reuses existing volume structure

**Cons:**
- Still requires a volume entry just to expose the token
- Doesn't solve the problem of not wanting the token at all
- Confusing interaction between spec-level and volume-level service account names

**Conclusion:** Rejected because it doesn't address the core issue of unwanted token exposure

## Scalability

This change has no scalability impact:

- **No Additional Resources**: Adding `serviceAccountName` to the spec adds no pods, volumes, or compute overhead
- **Reduced Overhead When Used Alone**: When used without a `serviceAccount` volume, it actually reduces overhead by not creating an unnecessary ISO disk or virtiofs mount
- **Identical Pod Rendering**: The pod template rendering logic is effectively the same, just reading the service account name from a different field

## Update/Rollback Compatibility

**Upgrade:**
- New `serviceAccountName` field is additive and optional
- Existing VMs using `serviceAccount` volumes are unaffected
- No migration needed

**Rollback:**
- Rolling back to a version without `serviceAccountName` will cause VMs that use only `spec.serviceAccountName` (without a `serviceAccount` volume) to lose their pod identity setting
- VMs will default to the namespace's default service account
- Users should add `serviceAccount` volumes before rollback if pod identity is required
- VMs using `serviceAccount` volumes (with or without `spec.serviceAccountName`) are unaffected by rollback

**VM Migration:**
- `serviceAccountName` is part of the VMI spec, not runtime state, so migration is unaffected
- Both source and target virt-controllers must support the field for it to take effect

## Functional Testing Approach

### Unit Tests
- Validation: `spec.serviceAccountName` alone sets pod service account correctly
- Validation: `spec.serviceAccountName` matching `serviceAccount` volume is accepted
- Validation: `spec.serviceAccountName` conflicting with `serviceAccount` volume is rejected
- Validation: `spec.serviceAccountName` must be a valid DNS subdomain name
- Pod rendering: `automountServiceAccountToken` is true when `serviceAccountName` is set
- Pod rendering: no service account token disk is created when only `serviceAccountName` is set (no `serviceAccount` volume)
- Backward compatibility: existing `serviceAccount` volume behavior unchanged

### Functional Tests
- VM with only `spec.serviceAccountName` boots successfully with correct pod identity
- VM with `spec.serviceAccountName` and matching `serviceAccount` volume works correctly
- VM with mismatched `spec.serviceAccountName` and `serviceAccount` volume is rejected
- VM with only `serviceAccount` volume (no `spec.serviceAccountName`) works as before
- VM with `spec.serviceAccountName` and ContainerPath volume accesses webhook-injected credentials

### Integration Tests
- Simulated IRSA pattern: VM with `serviceAccountName` + ContainerPath accesses injected credentials
- VM migration with `spec.serviceAccountName` set

## Implementation History

_To be filled in as implementation progresses_

## Graduation Requirements

### Alpha

- [ ] Feature gate `VMIServiceAccountName` guards the new `serviceAccountName` field
- [ ] API validation for the new field and its interaction with `serviceAccount` volumes
- [ ] Pod template rendering updated to use `spec.serviceAccountName`
- [ ] Unit tests covering validation and pod rendering
- [ ] Functional tests covering basic use cases
- [ ] Documentation for the new field

### Beta

- [ ] Feature gate `VMIServiceAccountName` enabled by default
- [ ] End-to-end testing with ContainerPath volumes and simulated workload identity patterns
- [ ] At least 2 releases of alpha testing with user feedback
- [ ] Migration documentation for users moving from `serviceAccount` volume to `spec.serviceAccountName`

### GA

- [ ] Feature gate `VMIServiceAccountName` removed
- [ ] Production usage confirmed
- [ ] No critical bugs in beta for at least 2 releases
