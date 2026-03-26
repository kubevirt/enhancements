# VEP #76 - Part 2: VirtualMachine Templates Beta Graduation

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.8
- This VEP targets beta for version: v1.9
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir
  in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Following [the initial proposal](./vmtemplate-proposal.md), this document
describes the graduation of the `template.kubevirt.io` API from Alpha to Beta.

The `VirtualMachineTemplate` and `VirtualMachineTemplateRequest` CRDs are
currently served as `template.kubevirt.io/v1alpha1` and are in Alpha phase. This
change introduces `template.kubevirt.io/v1beta1` and promotes the feature phase
from Alpha to Beta. The aim is to target KubeVirt release v1.9.0.

The import and export capabilities for VirtualMachine templates are described
in companion proposal files and are not covered by this document.

## Goals

- Graduate `template.kubevirt.io` from `v1alpha1` to `v1beta1`
- Add `templateLabels` and `ttlSecondsAfterFinished` fields to the VMTR spec
- Advance the `Template` feature gate to beta (enabled by default)
- Tie VMTR lifecycle to the created VMT via owner references

## Non Goals

- Import and export of VirtualMachine templates (covered in companion proposals)
- Graduating to GA
- Changes to the `VirtualMachineTemplate` spec

## Repos

- `kubevirt/kubevirt`
- `kubevirt/virt-template`
- `kubevirt/virt-template-api`

## Design

### API Changes

The following additive changes are introduced to the
`VirtualMachineTemplateRequest` (VMTR) spec:

- `templateLabels` (`map[string]string`, optional) - labels to set on
  the created VMT.
- `ttlSecondsAfterFinished` (`*int32`, optional) - number of seconds
  after successful completion before the VMTR is automatically deleted.
  Modeled after the Kubernetes
  [TTL-after-finished controller](https://kubernetes.io/docs/concepts/workloads/controllers/ttlafterfinished/).
  Failed VMTRs are never cleaned up by the TTL controller, so they
  remain available for debugging. If unset, the VMTR is not automatically
  deleted and must be removed manually or through the owner reference
  cleanup described below.

```yaml
apiVersion: template.kubevirt.io/v1beta1
kind: VirtualMachineTemplateRequest
metadata:
  name: my-request
  namespace: my-template-namespace
spec:
  virtualMachineRef:
    name: my-vm
    namespace: my-vm-namespace
  templateName: my-template
  templateLabels:
    example.com/os: linux
    example.com/workload: server
  ttlSecondsAfterFinished: 3600
```

All other fields across `VirtualMachineTemplate` and
`VirtualMachineTemplateRequest` remain unchanged from `v1alpha1`.

The new fields are added to both the `v1alpha1` and `v1beta1` CRD schemas.
This is necessary because Kubernetes prunes unknown fields from stored objects
based on the storage version's schema. Since `v1alpha1` remains the storage
version in v1.9.0, omitting the fields from its schema would cause data loss
for objects created via `v1beta1`. Adding them to both schemas avoids the need
for a conversion webhook and allows the CRDs to continue using the `None`
conversion strategy.

### API Versioning Plan

The graduation follows a three-release plan to transition from `v1alpha1` to
`v1beta1`. While
[Rule #4a](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#deprecating-parts-of-the-api)
of the Kubernetes Deprecation Policy allows alpha API versions to be removed
in any release, we follow the same conservative approach used for other
KubeVirt API migrations to give operators time to run
[`kube-storage-version-migrator`](https://github.com/kubernetes-sigs/kube-storage-version-migrator).
Per [Rule #4b](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#deprecating-parts-of-the-api),
the preferred and storage versions advance together, only after a release that
supports both versions.

#### v1.9.0 - Introduce v1beta1

- Both `v1alpha1` and `v1beta1` are served
- `v1alpha1` remains storage version and preferred (ensures safe rollback)
- `v1alpha1` is marked as deprecated

#### v1.10.0 - Move storage version to v1beta1

- Storage version and preferred version move to `v1beta1`
- `v1alpha1` continues to be served but remains deprecated
- Operators should deploy `kube-storage-version-migrator` to rewrite stored
  objects from `v1alpha1` to `v1beta1`

#### v1.11.0 - Remove v1alpha1

- `v1alpha1` is no longer served
- Only `v1beta1` is served as the storage version

### Changes to virt-template

The `v1beta1` types will be added to the `kubevirt/virt-template-api`
repository under a new `template.kubevirt.io/v1beta1` package. The
`templateLabels` and `ttlSecondsAfterFinished` fields are added to the
`VirtualMachineTemplateRequest` type in both `v1alpha1` and `v1beta1`
packages. All other types are copies of their `v1alpha1` counterparts. The
CRD manifests will be updated to serve both versions and mark `v1alpha1` as
deprecated.

The virt-template-controller will be updated to read `templateLabels` from the
VMTR spec and set them on the created VMT. It will also implement the TTL
cleanup logic for successfully completed VMTRs.

Additionally, after creating the VMT, the controller will set an owner
reference on the VMTR pointing to the VMT. This ties the lifecycle of the
VMTR to the VMT: when the VMT is deleted, Kubernetes garbage collection
automatically cleans up the VMTR. Deleting a VMTR does not affect the VMT.

### Changes to kubevirt/kubevirt

The `Template` feature gate is advanced to beta (enabled by default). The
vendored manifest bundle for virt-template will include the updated CRD
definitions with both API versions.

## Update/Rollback Compatibility

| Transition | Notes |
|---|---|
| v1.8.x to v1.9.0 | Transparent. Storage and preferred stay `v1alpha1`, `v1beta1` becomes available. |
| v1.9.0 to v1.8.x | Safe rollback. Storage is still `v1alpha1`. |
| v1.9.x to v1.10.0 | Storage and preferred move to `v1beta1`. Operators should deploy `kube-storage-version-migrator`. |
| v1.10.0 to v1.9.x | Safe. v1.9.x serves both versions and can read `v1beta1`-stored objects. |
| v1.10.x to v1.11.0 | `v1alpha1` removed. Migration must be complete. All clients must use `v1beta1`. |

## Functional Testing Approach

- Verify that both `v1alpha1` and `v1beta1` API versions are served and
  return correct data for all affected CRDs
- Verify that objects created via one version can be read via the other
- Verify that `templateLabels` defined on a VMTR are set on the VMT
  created by that request
- Verify that a VMTR without `templateLabels` creates a VMT without
  additional labels (backwards compatibility)
- Verify that deleting a VMT also deletes the VMTR that created it
- Verify that deleting a VMTR does not delete the VMT
- Verify that a succeeded VMTR with `ttlSecondsAfterFinished` is deleted
  after the specified duration
- Verify that a failed VMTR with `ttlSecondsAfterFinished` is not deleted
- Verify that a VMTR without `ttlSecondsAfterFinished` is not automatically
  deleted

## Graduation Requirements

### Beta (v1.9.0)

- `Template` feature gate in `kubevirt/kubevirt` advanced to beta (enabled by
  default)
- `v1beta1` API version served alongside `v1alpha1`
- `v1alpha1` marked as deprecated
- No breaking API changes from `v1alpha1` (additive only)
- `templateLabels` field on VMTR implemented and tested
- `ttlSecondsAfterFinished` cleanup for succeeded VMTRs implemented and tested
- VMTR lifecycle tied to VMT via owner references
- Functional tests covering multi-version serving
- Storage version: `v1alpha1`, preferred version: `v1alpha1`
- Documentation updated to recommend `v1beta1` for new objects

### v1.10.0

- Storage version: `v1beta1`, preferred version: `v1beta1`
- Documentation recommends deploying `kube-storage-version-migrator`

### v1.11.0

- `v1alpha1` API version removed
- Only `v1beta1` served and stored
