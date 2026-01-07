# VEP #165: ContainerPath Volumes for KubeVirt VirtualMachines

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal introduces `ContainerPath` volumes for KubeVirt VirtualMachines, allowing users to expose paths in the virt-launcher pod to the VirtualMachine via virtiofs. This feature provides a mechanism to expose data that external systems (cloud provider webhooks, platform infrastructure) implicitly inject into pods, eliminating the need for additional mutating webhooks or custom volume handling.

## Motivation

KubeVirt VirtualMachines currently lack a general mechanism to expose mounted volumes from the pod filesystem to the guest VM. While KubeVirt supports referencing Kubernetes Secrets and ConfigMaps by name (creating new mounts for the VM), there is no way to expose volumes that are already mounted into the pod at specific paths.

The primary motivation is to support data that is **implicitly injected into pods by external systems** (mutating webhooks, platform infrastructure) without user intervention. This creates a "chicken and egg" problem: the data appears in the pod automatically, but there's no way to expose it to the VM without additional custom webhooks or configuration.

This limitation creates challenges in several scenarios:

- **Cloud Provider Service Account Tokens**: AWS IAM Roles for Service Accounts (IRSA) injects credentials at `/var/run/secrets/eks.amazonaws.com/serviceaccount` via a mutating webhook, requiring additional webhooks like [irsa-mutation-webhook](https://github.com/kubevirt/irsa-mutation-webhook) to make these available to VMs
- **GKE Workload Identity**: Similar to IRSA, GKE injects service account credentials into pods at specific paths via mutating webhooks
- **Azure Workload Identity**: Azure AD pod identity tokens are mounted at standard paths in pods via mutating webhooks
- **Trusted Execution Environments (TEE)**: Confidential computing scenarios often require attestation tokens and runtime-injected secrets that are mounted into pods by platform infrastructure via CSI drivers or init containers

Currently, these scenarios require custom solutions, typically involving mutating webhooks that transform pod specifications or duplicate volume mounts. A general-purpose escape hatch for exposing implicitly-injected pod paths to VMs would eliminate this complexity.

## Goals

- Provide a declarative API for exposing mounted volumes from the virt-launcher pod to the VirtualMachine guest
- Support virtiofs as the transport mechanism for maximum compatibility and performance
- Enable read-only access initially to support secret injection use cases securely
- Eliminate the need for mutating webhooks for common cloud provider identity patterns
- Maintain consistency with existing KubeVirt volume handling patterns (similar to Secret volumes)
- Support only filesystem-based access (no disk device exposure)

## Non Goals

- **Replacing Existing Volume Types**: For cases where you want to reference a Kubernetes Secret or ConfigMap object directly by name, continue using the existing Secret and ConfigMap volume types. ContainerPath is for exposing already-mounted paths in the pod
- **Explicit Volume Configuration (e.g., CSI Secrets)**: The primary focus is on **implicitly-injected data** by webhooks and platform infrastructure. While ContainerPath can technically expose explicitly-configured volumes (like CSI Secrets Store volumes), these scenarios may be better served by dedicated volume types in the future since users already control the pod configuration
- **Write Support**: Initial implementation will focus on read-only access. Write support may be considered in future iterations based on user demand and security implications
- **Disk Device Support**: ContainerPath volumes will only be exposed as filesystems via virtiofs, not as block devices or disk images
- **Arbitrary Host Path Access**: This feature does not provide access to the host filesystem, only paths within the virt-launcher pod
- **Automatic Discovery**: Users must explicitly configure ContainerPath volumes; there is no automatic detection or mounting of pod paths
- **Backwards Compatibility with Legacy Webhook Solutions**: Existing webhook-based solutions will continue to work but are not a primary consideration for this design

## Definition of Users

- **Platform Engineers**: Infrastructure teams deploying KubeVirt in cloud environments with managed identity services (AWS IRSA, GKE Workload Identity, Azure Workload Identity)
- **Application Developers**: Developers building cloud-native applications that need to access cloud provider SDKs and services from within VMs
- **Security Teams**: Teams implementing confidential computing and TEE-based workloads requiring attestation tokens

## User Stories

- As a platform engineer deploying KubeVirt on EKS, I want my VirtualMachines to automatically have access to AWS credentials injected via IRSA without maintaining a separate mutating webhook
- As a platform engineer on GKE, I want my VirtualMachines to seamlessly access GCP services using Workload Identity credentials mounted in the pod
- As an application developer, I want my VM-based application to use the AWS SDK with IRSA credentials that are automatically mounted at the expected path
- As a security team member implementing confidential computing, I want to expose attestation tokens and runtime secrets that are injected into my pod to the trusted VM guest

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

The design introduces a new volume source type, `containerPath`, which can be used in the `spec.template.spec.volumes` section of a VirtualMachine or VirtualMachineInstance. This volume source references a path within the virt-launcher pod filesystem that corresponds to a mounted volume.

### Key Design Principles

1. **Volume-Mount-Only**: ContainerPath volumes must reference paths that correspond to volumeMounts in the virt-launcher compute container. This provides a clear security boundary - only explicitly mounted volumes can be exposed, not arbitrary filesystem paths
2. **Filesystem-Only**: ContainerPath volumes are exposed exclusively via virtiofs, not as disk devices
3. **Read-Only Default**: Initial implementation supports only read-only access for security
4. **Explicit Configuration**: Users must explicitly declare containerPath volumes; no automatic mounting
5. **Pod-Scoped**: Paths are resolved within the virt-launcher pod, not the host
6. **Consistency with Secrets**: Implementation follows the same patterns as Secret volume handling in KubeVirt

### API Design

A new `ContainerPathVolumeSource` will be added to the KubeVirt API:

```go
type ContainerPathVolumeSource struct {
    // Path is the path within the virt-launcher pod to expose to the VM.
    // The path must correspond to a volumeMount in the virt-launcher compute container.
    // This ensures only explicitly mounted volumes can be exposed, not arbitrary filesystem paths.
    // Required
    Path string `json:"path"`

    // ReadOnly specifies whether the volume should be mounted read-only
    // Optional, defaults to true
    // +optional
    ReadOnly *bool `json:"readOnly,omitempty"`
}
```

The volume source is added to the existing `VolumeSource` union type:

```go
type VolumeSource struct {
    // ... existing volume sources ...

    // ContainerPath represents a path in the virt-launcher pod
    // +optional
    ContainerPath *ContainerPathVolumeSource `json:"containerPath,omitempty"`
}
```

### Implementation Details

1. **VolumeMount Validation**: The virt-controller will validate that the specified path corresponds to a volumeMount in the virt-launcher compute container spec. This is checked at VM creation/update time:
   - The path is normalized (resolving any `.` or `..` components)
   - The normalized path must exactly match a volumeMount's mountPath, OR
   - The normalized path must be a subpath of a volumeMount's mountPath (for nested directories within a mount)
   - Paths containing `..` components that would escape the volumeMount boundary are rejected (e.g., `/mnt/secret/../../etc/passwd`)
   - This ensures only explicitly declared volumes can be exposed, providing a clear security boundary
2. **Volume Resolution**: The virt-launcher pod will validate that the specified path exists and is accessible at runtime
3. **Virtiofs Integration**: The path will be exposed to the VM using the existing virtiofs infrastructure, similar to how Secret volumes are handled
4. **Mount Options**: The volume will be mounted read-only by default, with the read-only constraint enforced at both the virtiofs and libvirt domain levels
5. **Additional Validation**: API validation will also ensure:
   - Path is an absolute path
   - Volume name is unique within the VM spec
6. **Security**: Access is limited to volumeMounts in the virt-launcher pod, preventing exposure of arbitrary filesystem paths or host filesystem access

### Relationship to Existing Features

- **Secret Volumes**: ContainerPath follows the same filesystem-based exposure pattern
- **ConfigMap Volumes**: Similar read-only semantics
- **Virtiofs**: Leverages existing virtiofs infrastructure for all filesystem sharing
- **Hotplug**: ContainerPath volumes will not support hotplug in the initial implementation

## API Examples

### AWS IRSA Example

First, create a ServiceAccount with the IRSA annotation:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: aws-irsa-sa
  namespace: default
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/my-role
```

Then create a VirtualMachine that uses this ServiceAccount and exposes the injected credentials:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: aws-vm
  namespace: default
spec:
  running: true
  template:
    spec:
      domain:
        devices:
          filesystems:
          - name: aws-irsa-sa
            virtiofs: {}
          - name: aws-token
            virtiofs: {}
        resources:
          requests:
            memory: 1Gi
      volumes:
      - name: aws-irsa-sa
        serviceAccount:
          serviceAccountName: aws-irsa-sa
      - name: aws-token
        containerPath:
          path: /var/run/secrets/eks.amazonaws.com/serviceaccount
          readOnly: true
```

When the virt-launcher pod is created, the EKS Pod Identity Webhook will inject AWS credentials at `/var/run/secrets/eks.amazonaws.com/serviceaccount`, which the ContainerPath volume then exposes to the VM guest.

### GKE Workload Identity Example

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: gke-vm
spec:
  running: true
  template:
    spec:
      domain:
        devices:
          filesystems:
          - name: gcp-token
            virtiofs: {}
        resources:
          requests:
            memory: 1Gi
      volumes:
      - name: gcp-token
        containerPath:
          path: /var/run/secrets/tokens/gcp-ksa
          readOnly: true
```

### Confidential Computing / TEE Example

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: confidential-vm
spec:
  running: true
  template:
    spec:
      domain:
        devices:
          filesystems:
          - name: attestation
            virtiofs: {}
          - name: runtime-secrets
            virtiofs: {}
        resources:
          requests:
            memory: 2Gi
      volumes:
      - name: attestation
        containerPath:
          path: /run/attestation
          readOnly: true
      - name: runtime-secrets
        containerPath:
          path: /run/secrets/runtime
          readOnly: true
```

## Alternatives

### Alternative 1: Use Existing Secret and ConfigMap Volume Types

Use the existing Secret and ConfigMap volume types to expose this data.

**Pros:**
- No new API needed
- Reuses existing, well-tested volume types

**Cons:**
- Secret and ConfigMap volumes reference Kubernetes objects **by name**, not mounted paths
- Cloud provider credentials (IRSA, Workload Identity) are injected as **projected volumes** by mutating webhooks, not as Secret objects - the tokens are dynamically generated via the TokenRequest API and aren't stored in etcd
- Even if you could copy credential data into Secrets, it would require syncing and create staleness issues (tokens expire and rotate)
- Creates duplicate mounts (once by Kubernetes into the pod, again by KubeVirt from the object)
- Doesn't solve the core problem: data is implicitly injected by external systems without user control

**Conclusion:** Rejected because these volume types work by referencing objects by name, not by referencing already-mounted paths in the pod

### Alternative 2: Extend Secret and ConfigMap Volume Sources

Instead of a new volume source, extend Secret and ConfigMap to support arbitrary paths.

**Pros:**
- Reuses existing volume types
- No new API surface

**Cons:**
- Semantically incorrect (not all paths are secrets or configmaps)
- Would require significant API changes to existing stable types
- Doesn't support non-Kubernetes-object paths (e.g., runtime-injected credentials)
- Creates confusion about what Secret/ConfigMap volumes represent

**Conclusion:** Rejected due to semantic mismatch and API complexity

### Alternative 3: Mutating Webhooks (Status Quo)

Continue requiring mutating webhooks for each use case.

**Pros:**
- No KubeVirt code changes required
- Maximum flexibility for webhook implementations

**Cons:**
- Requires deploying and maintaining separate webhook infrastructure
- Each cloud provider/scenario needs custom webhook
- Webhook failures can block VM creation
- Poor user experience
- Operational complexity

**Conclusion:** Rejected as the problem this VEP aims to solve

### Alternative 4: Allow Any Arbitrary Path (No VolumeMount Constraint)

Allow ContainerPath to reference any path in the virt-launcher pod, with a blacklist of sensitive system paths (`/proc`, `/sys`, `/dev`, etc.).

**Pros:**
- Maximum flexibility
- Simpler API - no need to understand volumeMounts

**Cons:**
- Much larger attack surface - users could attempt to expose any filesystem path
- Requires complex path validation and blacklist maintenance
- Difficult to reason about security - what other paths should be blacklisted?
- Path traversal concerns require extensive validation
- Doesn't align with Kubernetes' volume-based model
- Security reviewers must consider the entire filesystem, not just declared volumes
- Potential for accidental exposure of sensitive data (logs, configs, etc.)

**Conclusion:** Rejected in favor of volumeMount-only constraint for better security posture and clearer boundaries

### Alternative 5: Sidecar Container with Shared Volume

Use a sidecar container to copy data to a shared emptyDir volume.

**Pros:**
- No KubeVirt changes needed
- Works today

**Cons:**
- Requires sidecar for every VM
- Data staleness issues (copies may be out of date)
- Increased resource usage
- Complex to implement for dynamic tokens

**Conclusion:** Rejected due to operational complexity and staleness issues

## Scalability

ContainerPath volumes have minimal scalability impact:

- **No Additional Pods**: Unlike hotplug volumes, ContainerPath volumes don't require attachment pods
- **Virtiofs Overhead**: Shares the same virtiofs infrastructure as Secret and ConfigMap volumes, so overhead is equivalent
- **Path Resolution**: Minimal CPU/memory overhead for path resolution and validation
- **Storage**: No additional storage required; paths reference existing pod data

The primary scalability consideration is the number of virtiofs mounts per VM, which is already limited by the virtiofs infrastructure and is shared across all filesystem volume types.

## Update/Rollback Compatibility

**Upgrade:**
- New API field `containerPath` is additive and optional
- Existing VMs without containerPath volumes are unaffected
- VMs with containerPath volumes require virt-launcher and libvirt components that support the feature
- Feature gate `ContainerPathVolumes` will guard the functionality during alpha/beta phases

**Rollback:**
- Rolling back to a version without ContainerPath support will cause VMs using this feature to fail validation
- Existing running VMs will continue to run but cannot be updated
- Users must remove containerPath volumes from VM specs before rollback to ensure VM lifecycle operations work

**VM Migration:**
- ContainerPath volumes will migrate normally as they reference pod-local paths
- Both source and destination pods must support containerPath volumes

## Functional Testing Approach

**Note on Cloud Provider Testing**: While the primary use cases involve cloud provider credential injection (IRSA, Workload Identity), the core functionality can be fully tested without cloud provider infrastructure. Tests simulate credential injection by pre-populating directories in the pod, which is functionally equivalent to what cloud provider webhooks do.

### Unit Tests
- API validation for ContainerPathVolumeSource
  - Valid absolute paths that match volumeMounts
  - Invalid relative paths
  - Paths that don't correspond to any volumeMount (should be rejected)
  - Subpaths within a volumeMount (should be accepted, e.g., `/mnt/secret/token`)
  - Path traversal attempts (should be rejected, e.g., `/mnt/secret/../../etc/passwd`)
- VolumeMount validation logic
  - Path normalization and `..` component detection
  - Validation that normalized path stays within volumeMount boundaries
- Volume resolution logic
- Virtiofs mount configuration generation

### Functional Tests
- VM with single containerPath volume boots successfully
- VM can read files from containerPath-mounted filesystem
- Multiple containerPath volumes in single VM
- Read-only enforcement (write operations fail)
- VM with containerPath volume can be stopped and restarted
- Path validation errors are properly reported

### Integration Tests
- Simulated credential injection: Pre-populate paths in pod, verify VM can read them via containerPath (tests IRSA/Workload Identity pattern without requiring actual cloud infrastructure)
- Service account token pattern: VM reads Kubernetes SA token from standard `/var/run/secrets/kubernetes.io/serviceaccount` path
- Non-existent path results in appropriate error
- VM migration with containerPath volumes
- Large directory trees exposed via containerPath
- Dynamic file updates: Files updated in pod are visible in VM (tests token rotation scenarios)

### Security Tests
- Attempt to reference paths that don't correspond to volumeMounts is rejected (e.g., `/etc/passwd`, `/var/log`)
- Attempt to reference system paths like `/proc`, `/sys`, `/dev` is rejected (these should never be volumeMounts)
- Read-only volumes cannot be written to
- Subpaths within a valid volumeMount are accepted (e.g., if `/mnt/secret` is a volumeMount, `/mnt/secret/token` should work)
- Path traversal attempts to escape a valid volumeMount are rejected:
  - Example: `/mnt/secret/../../etc/passwd` where `/mnt/secret` is a valid volumeMount - should be rejected
  - Example: `/var/run/secrets/eks.amazonaws.com/serviceaccount/../../../etc/passwd` - should be rejected
  - Validation should detect and block any `..` components that would escape the volumeMount boundary

## Implementation History

_To be filled in as implementation progresses_

## Graduation Requirements

### Alpha

- [ ] Feature gate `ContainerPathVolumes` guards all functionality
- [ ] API validation ensures:
  - Absolute paths only
  - Paths correspond to volumeMounts in the virt-launcher compute container
  - Unique volume names
- [ ] Basic implementation supporting read-only access via virtiofs
- [ ] Unit tests covering API validation, volumeMount validation, and core logic
- [ ] Functional tests covering basic use cases
- [ ] Documentation for API fields and basic usage examples, including the volumeMount constraint

### Beta

- [ ] Feature gate enabled by default
- [ ] Comprehensive functional test coverage including:
  - Simulated credential injection patterns (mimics IRSA/Workload Identity without requiring cloud infrastructure)
  - Service account token access
  - Multiple containerPath volumes per VM
  - Dynamic file updates visible in VM (token rotation scenarios)
- [ ] Migration support validated
- [ ] Performance testing shows acceptable overhead
- [ ] User documentation with real-world examples for AWS IRSA, GKE Workload Identity, and Azure scenarios
- [ ] At least 2 releases of alpha testing with user feedback incorporated
- [ ] Security review completed
- [ ] (Optional) Community validation of IRSA/Workload Identity patterns by users with cloud provider access

### GA

- [ ] Feature gate removed
- [ ] Production usage by at least 3 organizations
- [ ] No critical bugs reported in beta phase for at least 2 releases
- [ ] Comprehensive user guide and troubleshooting documentation
- [ ] (Optional) Write support added if user demand and security review support it
- [ ] (Optional) Hotplug support if there is demonstrated need

## Security Considerations

### VolumeMount-Only Constraint

The primary security control is that **ContainerPath volumes can only reference paths that correspond to volumeMounts** in the virt-launcher compute container. This design provides significant security benefits:

1. **Clear Security Boundary**: Only explicitly declared volumes in the pod spec can be exposed. No arbitrary filesystem paths, no accidental exposure of sensitive system paths
2. **Self-Documenting**: Security reviewers can look at the volumeMounts list to understand what data could potentially be exposed to VMs
3. **Simplified Path Traversal Validation**: Path validation only needs to ensure paths stay within volumeMount boundaries. Paths are normalized and checked to prevent escape attempts (e.g., `/mnt/secret/../../etc/passwd`)
4. **Aligns with Kubernetes Principles**: Everything is volumes, following standard Kubernetes resource patterns
5. **Reduced Attack Surface**: Attackers cannot attempt to expose arbitrary paths like `/var/log`, `/etc`, or other non-volume directories

### Additional Path Restrictions

ContainerPath volumes have additional restrictions:

1. **Pod-Scoped Only**: Paths are resolved within the virt-launcher pod container, not the host
2. **Absolute Paths Required**: Relative paths are rejected to prevent confusion
3. **VolumeMount Validation**: Paths must correspond to a volumeMount in the compute container (exact match or subpath)
4. **Path Traversal Prevention**: Paths are normalized to resolve `.` and `..` components, and the normalized path must remain within the volumeMount boundary. Attempts to escape via `..` are rejected (e.g., `/mnt/secret/../../etc/passwd` is invalid even if `/mnt/secret` is a valid volumeMount)

### Read-Only Default

The initial implementation enforces read-only access to:
- Prevent VMs from modifying pod filesystem state
- Protect credential files from being overwritten
- Reduce risk of privilege escalation attacks

### Audit Logging

ContainerPath volume usage should be logged for audit purposes:
- Volume source configuration (path)
- Mount operations
- Access denials

### Future Write Support Considerations

If write support is added in the future, additional protections will be required:
- Explicit write permission flag (opt-in)
- Additional validation of writable paths
- Consideration of SELinux/AppArmor contexts
- Clear documentation of security implications

## Use Case Deep Dive

### AWS IRSA (IAM Roles for Service Accounts)

**Background**: AWS EKS IRSA allows pods to assume IAM roles without long-lived credentials. The workflow:
1. Administrator creates a Kubernetes ServiceAccount with the annotation `eks.amazonaws.com/role-arn: arn:aws:iam::ACCOUNT:role/ROLE_NAME`
2. When a pod uses this ServiceAccount, the **EKS Pod Identity Webhook** (a mutating admission webhook) intercepts pod creation
3. The webhook automatically injects into the pod:
   - Environment variables: `AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_REGION`
   - A projected volume mounted at `/var/run/secrets/eks.amazonaws.com/serviceaccount/` containing a web identity token
4. AWS SDKs automatically detect these environment variables and exchange the token for temporary IAM credentials

**Current Problem**: The webhook injects credentials into the virt-launcher pod, but:
- The VM guest doesn't have the environment variables
- The VM guest can't access the mounted token directory
- Applications running in VMs can't authenticate to AWS services
- Currently requires [irsa-mutation-webhook](https://github.com/kubevirt/irsa-mutation-webhook) to duplicate the credentials into the VM

**Solution with ContainerPath**:
1. The virt-launcher pod gets IRSA credentials injected at `/var/run/secrets/eks.amazonaws.com/serviceaccount/`
2. A ContainerPath volume exposes this path to the VM guest via virtiofs
3. Applications in the VM can read the token and environment variables from the mounted filesystem
4. The AWS SDK in the VM works automatically (with proper environment variable configuration)

**Benefit**: Removes need for custom mutating webhooks, simplifies deployment, and provides a declarative configuration

### GKE Workload Identity

Similar to AWS IRSA, GKE's Workload Identity feature injects GCP service account credentials into pods at `/var/run/secrets/tokens/gcp-ksa`. ContainerPath volumes enable VMs to access these credentials without custom solutions.

### Azure Workload Identity

Azure AD pod-managed identity injects tokens into pods. ContainerPath volumes provide a generic mechanism for VMs to access these tokens.

### Confidential Computing / TEE

Confidential computing platforms (e.g., Azure Confidential Computing, AWS Nitro Enclaves integration, GCP Confidential VMs) often inject attestation tokens and runtime secrets into pods via init containers or CSI drivers. These secrets are mounted at platform-specific paths and need to be accessible to trusted workloads running in VMs.

### HashiCorp Vault Agent Injector

The [HashiCorp Vault Agent Injector](https://developer.hashicorp.com/vault/docs/platform/k8s/injector) mutating webhook automatically adds a vault-agent sidecar container and a shared emptyDir volume to pods. The sidecar authenticates to Vault and writes secrets to the emptyDir at a configurable path (typically `/vault/secrets/`), which application containers read from. ContainerPath volumes can expose these emptyDir-backed secret paths to VMs, allowing VM workloads to access dynamically-injected Vault secrets. This pattern works because the emptyDir is a volumeMount in the virt-launcher pod, satisfying the volumeMount-only constraint.

## Questions and Answers

**Q: Why not just use Secret or ConfigMap volumes?**
A: KubeVirt's Secret and ConfigMap volume types reference Kubernetes objects **by name**, and KubeVirt creates new mounts for the VM from those objects. ContainerPath is specifically for **implicitly-injected data** where external systems (webhooks, platform infrastructure) automatically mount data into your pod without your direct control:
- **Cloud provider credentials** injected by mutating webhooks (IRSA, Workload Identity, Azure Workload Identity)
- **TEE attestation tokens** injected by confidential computing platforms
- **Platform-injected secrets** mounted by infrastructure components you don't control

**When to use each:**
- Use Secret/ConfigMap volumes: When you want to reference a Kubernetes object by name and have KubeVirt mount it
- Use ContainerPath volumes: When data is implicitly injected into your pod by external systems and you want to expose it to the VM

**Q: Aren't IRSA/Workload Identity tokens just Kubernetes Secrets that we could reference by name?**
A: No. IRSA and similar systems use **projected volumes** with serviceAccountToken sources, not Secret objects. The tokens are:
- Generated dynamically by the Kubernetes TokenRequest API (not stored in etcd)
- Short-lived and automatically rotated
- Never stored as Secret objects that you could reference by name
- Injected into the pod by mutating webhooks as projected volume mounts

Even if you could create a Secret to hold the token, you'd need to constantly sync it and deal with rotation/expiration. ContainerPath lets you directly expose the dynamic mount that already exists in the pod.

**Q: Can I expose any path in the pod, like `/var/log` or `/etc`?**
A: No. ContainerPath volumes can **only reference paths that correspond to volumeMounts** in the virt-launcher compute container. This means:
- The path must match a volumeMount's mountPath (exact match or subpath)
- Arbitrary filesystem paths that aren't volumes cannot be exposed
- This provides a clear security boundary - only explicitly declared volumes can be shared with VMs

**Q: Can this access the host filesystem?**
A: No. Paths are resolved within the virt-launcher pod container filesystem only, and must correspond to volumeMounts.

**Q: What happens if the path doesn't exist or doesn't correspond to a volumeMount?**
A: VM creation/update will fail with a validation error indicating the path does not correspond to a valid volumeMount.

**Q: Can I use this for large data directories?**
A: While technically possible, ContainerPath is optimized for small configuration and credential files. For large datasets, use dedicated volume types (PVC, DataVolume, etc.).

**Q: Will this support hotplug?**
A: Not in the initial implementation. Hotplug support may be added in the future if there is user demand.

**Q: What about write access?**
A: Initial implementation is read-only. Write support may be added in future versions after security review and demonstrated use cases.

**Q: How is this different from HostPath volumes?**
A: HostPath volumes (if supported) would access the node's host filesystem. ContainerPath accesses the pod's container filesystem. This is a critical security distinction.

**Q: Can I use environment variable expansion in the path?**
A: No. The path must be a literal string. Environment variable expansion would add complexity and potential security issues.
