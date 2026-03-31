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

## Design

### API Versioning Plan

The `v1beta1` API version is structurally identical to `v1alpha1`. No fields
are added, removed, or renamed. This means no conversion webhook is required
and the CRDs can use the `None` conversion strategy.

The following CRDs are affected:

- `VirtualMachineTemplate`
- `VirtualMachineTemplateRequest`

The graduation follows a three-release plan to transition from `v1alpha1` to
`v1beta1`:

#### v1.9.0 — Introduce v1beta1

- Both `v1alpha1` and `v1beta1` are served
- Storage version remains `v1alpha1`
- `v1alpha1` is marked as deprecated

#### v1.10.0 — Move storage version to v1beta1

- Storage version moves to `v1beta1`
- `v1alpha1` continues to be served but remains deprecated

#### v1.11.0 — Remove v1alpha1

- `v1alpha1` is no longer served
- Only `v1beta1` is served as the storage version

### Changes to virt-template

The `v1beta1` types will be added to the `kubevirt/virt-template-api`
repository as a copy of the existing `v1alpha1` types under a new
`template.kubevirt.io/v1beta1` package. The CRD manifests will be updated
to serve both versions and mark `v1alpha1` as deprecated.

### Changes to kubevirt/kubevirt

The vendored manifest bundle for virt-template will include the updated CRD
definitions with both API versions.

## Update/Rollback Compatibility

| Transition | Notes |
|---|---|
| v1.8.x to v1.9.0 | Transparent. Storage stays `v1alpha1`, `v1beta1` becomes available. |
| v1.9.0 to v1.8.x | Safe rollback. Storage is still `v1alpha1`. |
| v1.9.x to v1.10.0 | Storage moves to `v1beta1`. Storage migration required. |
| v1.10.x to v1.11.0 | `v1alpha1` removed. All clients must use `v1beta1`. |

## Functional Testing Approach

- Verify that both `v1alpha1` and `v1beta1` API versions are served and
  return correct data for all affected CRDs
- Verify that objects created via one version can be read via the other

## Implementation Phases (for v1beta1 in v1.9.0)

- Phase 1: Add `v1beta1` types to `kubevirt/virt-template-api` (copy of
  `v1alpha1`) and update CRD manifests for multi-version serving
- Phase 2: Update `kubevirt/virt-template` controllers and webhooks to handle
  both API versions

## Graduation Requirements

### Beta (v1.9.0)

- `v1beta1` API version served alongside `v1alpha1`
- `v1alpha1` marked as deprecated
- No breaking API changes from `v1alpha1`
- Functional tests covering multi-version serving
- Documentation updated to reference `v1beta1` as the preferred version
