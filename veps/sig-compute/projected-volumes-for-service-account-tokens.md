# VEP #94: Extend Volume API to Support Projected Volumes for Service Account Tokens

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.
- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
This VEP proposes extending KubeVirt's Volume API to support Projected Volumes,
similar to Kubernetes, enabling flexible mounting of service account
tokens with custom audiences and paths into virtual machines via virtiofs for
token rotations. This addresses an existing limitation in dynamic
authentication scenarios like AWS IRSA.

## Motivation
KubeVirt currently mounts service accounts at a hardcoded path which works fine
for standard Kubernetes API tokens but fails for advanced projections like
those in AWS IRSA. There are mounted at
`/var/run/secrets/eks.amazonaws.com/serviceaccount` with audience
`sts.amazonaws.com`. This prevents VMs from accessing rotated tokens and forces
users to invent custom workarounds like mutation webhooks or sidecars, adding
complexity, overhead and security risks. 

KubeVirt exposes virtual machines through a Kubernetes-native API with a
similar look and feel to Pods, simplifying the user experience and making VM
management consistent with existing Kubernetes workloads. Supporting Projected
Volumes extends this approach by enabling declarative and flexible handling of
projected service account tokens inside VMs. This directly benefits use cases
in environments like EKS and other multi-cloud setups.

Discussions in issue #13311 highlight the need for an upstream fix, as
workarounds are temporary and not integrated.

## Goals
- Extend VolumeSource with Projected field, supporting ServiceAccountTokenProjection.
- Enable dynamic token propagation via virtiofs.
- Introduce feature gate for controlled rollout.
- Ensure backward compatibility and integration tests.

## Non Goals
- Full support for all Kubernetes projection types (focus on service accounts).
- Support for non-virtiofs dynamic updates.

## Definition of Users
VM owners and workload developers running KubeVirt in cloud environments, such
as EKS, multi-cloud who need secure, dynamically rotated tokens inside their
VMs for authentication with external services. Cluster administrators may be
indirectly impacted but are not the primary users.

## User Stories
- As a VM owner running workloads on EKS, I want to mount IRSA tokens with
  custom audiences into my VM so that applications inside the VM can
authenticate securely without custom webhooks or sidecars.
- As a workload developer, I want projected service account tokens to be
  mounted declaratively through the VMI spec, so that I can configure
authentication in a Kubernetes-native way without extra operational overhead.
- As a VM user, I want tokens inside my VM to rotate automatically without
  requiring VM restarts, so my applications continue to run securely without
interruption.

## Repos
- kubevirt/kubevirt (core API and virt-launcher)

## Design
Add `Projected` to `VolumeSource`, with `ServiceAccountTokenProjection` for
audience, expiration, and path. Virt-launcher detects projections and shares
via virtiofs. Feature gate: ProjectedVolumes.

## API Examples
```yaml
volumes:
- name: irsa-volume
  projected:
    sources:
    - serviceAccountToken:
        audience: "sts.amazonaws.com"
        expirationSeconds: 3600
        path: "token"
```

## Alternatives
- Mutation Webhooks: Inject sidecars or modify VM definitions at admission time
  to deliver tokens. While effective, this introduces an external dependency
  and is not integrated into core KubeVirt. (based on repo: kubevirt/irsa-mutation-webhook)

- Sidecar Injections (based on PR #14568): Run custom containers inside the
  virt-launcher pod to provide token files to the VM. This offers flexibility
  but increases pod complexity, operational burden, and maintenance costs and not supported directly supported.

- Recursive Secret Scanning: Extend virt-launcher to recursively scan
  /var/run/secrets and expose contents to the VM. This raises significant
  security risks by unintentionally exposing unrelated secrets.

- QEMU Guest Agent Extensions: Extend the guest agent protocol to push
  projected tokens into the VM filesystem. This avoids virtiofs but requires
  custom controllers for token rotation and introduces additional moving parts.

- Userspace NFS Server: Run a lightweight NFS server and mount it into the VM
  to deliver projected files. This adds dependencies on NFS, and is not aligned
  with Kubernetes-native volume semantics.

These are workarounds; the proposed API extension provides a declarative, upstream solution.

## Scalability
Per-VM handling; scales with existing volume limits. Minimal overhead as
projections are pod-level.

## Update/Rollback Compatibility
- New field is optional; existing VMIs remain unaffected.
- On downgrade, projected volumes fall back to errors or no-op.

## Functional Testing Approach
- Unit tests: API validation, struct marshaling.
- Integration tests: Deploy VMI with projected token, verify mount and rotation in guest.

## Implementation History
October 01, 2025: VEP drafted.

## Graduation Requirements
### Alpha
- Feature gate enabled.
- Initial implementation for service account projections.

### Beta
- Full IRSA support.

### GA
- Documentation complete.
- Featuregate removal.
