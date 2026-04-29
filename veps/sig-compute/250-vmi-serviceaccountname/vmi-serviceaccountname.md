# VEP #250: Add serviceAccountName to VirtualMachineInstance Spec

## VEP Status Metadata

### Target releases

- This VEP targets version: v1.9

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved

## Overview

This proposal adds a `serviceAccountName` field to `VirtualMachineInstanceSpec`, decoupling the concern of "which service account the virt-launcher pod runs as" from "whether the service account token is exposed to the VM as a disk/filesystem." Today these two concerns are conflated in the `serviceAccount` volume source, forcing users to expose a service account token volume to the VM even when they only need the pod to run under a specific identity.

## Motivation

KubeVirt's current mechanism for specifying which service account a VM runs as is the `serviceAccount` volume source. When a user specifies this volume, KubeVirt does two things:

1. Sets `pod.spec.serviceAccountName` on the virt-launcher pod
2. Mounts the service account token as an ISO disk or virtiofs filesystem exposed to the VM guest

This coupling made sense when the primary use case was "I want the VM to access the Kubernetes API using a specific service account." However, with the growing adoption of cloud provider workload identity systems (AWS IRSA, Azure Workload Identity), a new pattern has emerged: users need the virt-launcher pod to run as a specific service account so that external mutating webhooks inject the correct credentials, but they **do not want** the Kubernetes service account token exposed to the VM. This pattern pairs naturally with mechanisms like ContainerPath volumes (VEP #165) that can expose webhook-injected credentials to the VM, though this proposal does not depend on ContainerPath.

For example, with AWS IRSA:

1. User creates a ServiceAccount annotated with `eks.amazonaws.com/role-arn`
2. User wants the virt-launcher pod to run as this ServiceAccount so the EKS Pod Identity Webhook injects AWS credentials
3. User wants to expose the injected AWS credentials to the VM
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

Cloud provider credential injection is the most prominent example, but there are other reasons a workload needs a specific service account identity without wanting the token exposed to the VM:

- **Image pull secrets**: ServiceAccounts can have `imagePullSecrets` attached, allowing the virt-launcher pod to pull from private registries without the token being relevant to the VM workload
- **Service mesh authorization**: Istio AuthorizationPolicy and similar mesh configurations use service account identity to control traffic between workloads
- **Admission and policy enforcement**: OPA/Gatekeeper, Kyverno, or Pod Security Admission policies that scope permissions by service account
- **External secret injection**: Vault Agent Injector and similar tools use service account identity to determine which secrets to inject into the pod
- **Audit attribution**: Service account identity appears in Kubernetes audit logs for traceability

In all of these cases, the service account functions as a **workload identity**. It determines how the workload authenticates to external systems and how infrastructure components authorize it. This is a workload-level concern, not a pod implementation detail. KubeVirt already recognizes this by exposing service account control through the `serviceAccount` volume. Adding `serviceAccountName` as a standalone field doesn't introduce a new abstraction leak; it corrects an existing one by separating the identity concern from the credential concern.

## Goals

- Allow users to control the VM's service account identity without requiring a service account volume
- Maintain backward compatibility with existing `serviceAccount` volume usage
- Simplify the user experience for specifying workload identity

## Non Goals

- **Removing the `serviceAccount` volume type**: The existing volume type remains useful when users explicitly want the token exposed to the VM
- **Changing the behavior of existing `serviceAccount` volumes**: Existing VMs using `serviceAccount` volumes continue to work identically
- **Adding `automountServiceAccountToken` to VMI spec**: This proposal focuses solely on `serviceAccountName`. Whether to expose the token to the VM is handled by the presence or absence of a `serviceAccount` volume
- **Token projection configuration**: Advanced token projection settings (audiences, expiration) are out of scope for this proposal

## Definition of Users

- **Platform Engineers**: Teams deploying KubeVirt in cloud environments that use workload identity systems (AWS IRSA, Azure Workload Identity)
- **Namespace Admins**: Administrators who define and manage VMs that need cloud provider credentials but don't need direct Kubernetes API access from within the VM
- **Cluster Admins**: Teams that use service account identity for policy enforcement, service mesh authorization, image pull secrets, or audit attribution
- **Security-Conscious Operators**: Administrators who want to follow the principle of least privilege by not exposing Kubernetes service account tokens to VMs unnecessarily

## User Stories

- As a platform engineer, I want my VirtualMachine to run as a specific ServiceAccount for cloud provider credential injection, without exposing the Kubernetes service account token to the VM guest
- As a namespace admin, I want to specify which ServiceAccount my VM runs as without having to configure an unwanted service account token volume
- As a security-conscious operator, I want to set a custom service account for my VM without exposing unnecessary Kubernetes service account tokens, reducing the attack surface inside the VM

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

### Immutability

Changing `serviceAccountName` requires recreating the VMI. For VMIs managed by a VirtualMachine object, this means a VM restart.

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

Use `spec.serviceAccountName` for cloud provider credential injection. This example uses ContainerPath volumes (VEP #165) to expose the webhook-injected credentials to the VM:

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

### Simple Workload Identity Without Any Token Exposure

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
- Particularly awkward when the user explicitly wants different credentials (e.g., cloud provider credentials)

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

### Alternative 5: Group-Level Service Account Management

Manage service account assignment at a group level (e.g., per namespace or label selector) rather than per-VM.

**Pros:**
- Could reduce per-VM configuration for homogeneous workloads

**Cons:**
- Doesn't match real-world usage patterns: different VMs in the same namespace often need different cloud provider roles (e.g., one VM needs S3 access, another needs DynamoDB). Cloud provider workload identity (IRSA, Azure Workload Identity) maps 1:1 between ServiceAccount and IAM role, so per-VM granularity is required
- The existing `serviceAccount` volume already works per-VM, so per-VM granularity is established precedent
- A group-level mechanism would still need per-VM overrides, adding complexity without eliminating the need for a per-VM field

**Conclusion:** Rejected because per-VM granularity is required for cloud provider workload identity patterns, and group-level management would still need per-VM overrides

### Alternative 6: MutatingAdmissionPolicy

Deploy a [MutatingAdmissionPolicy](https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/) that mutates the virt-launcher pod's service account. KubeVirt could optionally assist by exposing this through a VM/VMI subresource.

**Pros:**
- No VMI API changes needed
- Leverages standard Kubernetes extension mechanisms

**Cons:**
- MutatingAdmissionPolicy is a cluster-scoped resource, so only cluster admins can create them. Namespace admins who want to set a service account on their VMs would need to request a cluster admin to create or modify an admission policy, creating an unnecessary operational dependency
- Users don't create virt-launcher pods directly — virt-controller does. A policy would need to match pods created by virt-controller and correlate them back to the originating VMI, which is fragile and indirect
- Breaks discoverability: nothing in the VM/VMI spec would indicate what service account the workload runs as. Operators would have to inspect admission policies separately to understand the effective configuration
- Adds operational complexity: users must manage admission policies alongside their VMs for a basic workload identity concern
- The `serviceAccount` volume already establishes precedent that service account control belongs in the VMI spec

**Conclusion:** Rejected because it requires cluster admin privileges for a namespace-level concern, breaks discoverability of the VM's effective configuration, and adds unnecessary operational complexity

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

## Functional Testing Approach

### Unit Tests
- Validation: `spec.serviceAccountName` alone sets pod service account correctly
- Validation: `spec.serviceAccountName` matching `serviceAccount` volume is accepted
- Validation: `spec.serviceAccountName` conflicting with `serviceAccount` volume is rejected
- Validation: `spec.serviceAccountName` must be a valid DNS subdomain name
- Pod rendering: `automountServiceAccountToken` is false when only `serviceAccountName` is set (no `serviceAccount` volume)
- Pod rendering: `automountServiceAccountToken` is true when a `serviceAccount` volume is present
- Pod rendering: no service account token disk is created when only `serviceAccountName` is set (no `serviceAccount` volume)
- Backward compatibility: existing `serviceAccount` volume behavior unchanged (existing tests cover this, listed for completeness)

### Functional Tests
- VM with only `spec.serviceAccountName` boots successfully with correct pod identity
- VM with `spec.serviceAccountName` accesses webhook-injected credentials (simulated IRSA pattern)
- VM migration with `spec.serviceAccountName` set

## Implementation History

_To be filled in as implementation progresses_

## Graduation Requirements

This feature does not require a feature gate because it introduces no new functionality — it provides a new, more ergonomic way to configure existing behavior (setting `pod.spec.serviceAccountName` on the virt-launcher pod). The `serviceAccount` volume already enables this, and `spec.serviceAccountName` simply decouples pod identity from token exposure.

### Requirements

- [ ] API validation for the new field and its interaction with `serviceAccount` volumes
- [ ] Pod template rendering updated to use `spec.serviceAccountName`
- [ ] Unit tests covering validation and pod rendering
- [ ] Functional tests covering basic use cases
- [ ] End-to-end testing with simulated workload identity patterns
- [ ] Documentation for the new field
- [ ] Migration documentation for users moving from `serviceAccount` volume to `spec.serviceAccountName`
