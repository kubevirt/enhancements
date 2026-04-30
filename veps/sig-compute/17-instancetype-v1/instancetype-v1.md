# VEP #17: Instancetype API v1 Graduation

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v0.56.0
- This VEP targets beta for version: v1.0.0
- This VEP targets GA for version: v1.9.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone /
release*.

- [x] (R) Enhancement issue created, which links to VEP dir in
  [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [x] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP graduates the `instancetype.kubevirt.io` API group from `v1beta1` to
`v1`. The instancetype API has been beta since v1.0.0, has proven stable in
production across multiple releases, and is ready for GA. No behavioral or
schema changes are introduced - this is a straightforward API version promotion
with removal of previously deprecated fields.

The migration follows a 4-release cycle:

- **v1.9.0**: Introduce `v1`, deprecate `v1beta1`
- **v1.10.0**: Change storage and preferred version to `v1`
- **v1.11.0**: `v1beta1` continues to be served (deprecation window)
- **v1.12.0**: Remove `v1beta1`

This graduation plan follows
[Rule #4a](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#deprecating-parts-of-the-api)
(beta versions served for 3 releases after deprecation) and
[Rule #4b](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#deprecating-parts-of-the-api)
(preferred/storage must not advance until both versions are served) of the
[Kubernetes Deprecation Policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/),
as well as the
[Kubernetes API Changes Guide](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md#backward-compatibility-gotchas)
(at least one release between storage version change and old version removal
for [`kube-storage-version-migrator`](https://github.com/kubernetes-sigs/kube-storage-version-migrator)).

## Motivation

Graduating to `v1` signals API stability and allows the project to remove the
beta version over time.

## Goals

- Graduate `instancetype.kubevirt.io` from `v1beta1` to `v1`
- Remove previously deprecated fields from the `v1` API surface
- Maintain full backward compatibility throughout the migration

## Non Goals

- Changing instancetype application semantics (conflict behavior stays as-is)
- Making `CPU` or `Memory` optional in the instancetype spec
- Surfacing applied resources in VM status
- Merging instancetypes and preferences into a single resource type

## Definition of Users

- **VM User**: Creates VirtualMachines and references instancetypes.
- **Cluster Admin**: Defines instancetypes as organizational standards or cloud
  offerings.

## User Stories

- As a VM user, I want the instancetype API to be stable (`v1`), so I can rely
  on it without worrying about breaking changes in future releases.

- As a VM user, I want to gradually migrate from `v1beta1` to `v1` with clear
  deprecation signals and no surprises in behavior.

- As a cluster admin, I want a clear migration timeline so I can plan upgrades
  and run storage migration tooling at the right time.

## Repos

- `kubevirt/kubevirt`
- `kubevirt/common-instancetypes`

## Design

### API Version Migration

The migration follows the 4-release cycle described in the overview.

#### Deprecated fields removed in `v1`

- `FirmwarePreferences.DeprecatedPreferredUseEfi` - use
  `FirmwarePreferences.PreferredEfi` instead
- `FirmwarePreferences.DeprecatedPreferredUseSecureBoot` - use
  `FirmwarePreferences.PreferredEfi` with SecureBoot instead
- Deprecated `PreferredCPUTopology` enum values: `preferCores`, `preferSockets`,
  `preferThreads`, `preferSpread`, `preferAny` - use the short forms `cores`,
  `sockets`, `threads`, `spread`, `any` instead

#### Conversion webhooks

Conversion webhooks handle `v1beta1` <-> `v1` translation:

- `v1beta1` -> `v1`: Deprecated `PreferredCPUTopology` values mapped to short
  forms. Deprecated firmware fields mapped to `PreferredEfi`.
- `v1` -> `v1beta1`: No mapping needed. The short `PreferredCPUTopology` forms
  (`cores`, `sockets`, `threads`, `spread`, `any`) are already valid `v1beta1`
  values alongside the deprecated ones. Likewise, `PreferredEfi` already exists
  in the `v1beta1` schema alongside the deprecated firmware fields, so no
  back-conversion to `DeprecatedPreferredUseEfi` or
  `DeprecatedPreferredUseSecureBoot` is required.

### ControllerRevision Handling

ControllerRevisions storing instancetype snapshots are not managed by
`kube-storage-version-migrator` since they contain serialized objects as opaque
data. Starting with v1.10.0 (when storage moves to `v1`), ControllerRevisions
are re-encoded from `v1beta1` to `v1` during VM reconciliation in
virt-controller. This gives two full releases
(v1.10.0 and v1.11.0) for ControllerRevisions to be gradually re-encoded
before `v1beta1` is removed in v1.12.0.

## API Examples

The `v1` API is identical to `v1beta1` minus the deprecated fields. The only
change for users is the API version string:

```yaml
apiVersion: instancetype.kubevirt.io/v1
kind: VirtualMachineInstancetype
metadata:
  name: my-instancetype
spec:
  cpu:
    guest: 4
  memory:
    guest: 8Gi
```

## Alternatives

### Graduate with behavioral changes (custom sizing)

Change instancetype semantics during graduation so that VM-specified values
take precedence over instancetype defaults, making instancetypes act as
non-conflicting defaults similar to preferences.

This was rejected because instancetypes should have clear semantics over how
many CPUs and how much memory they provide. Users who only need defaults can
use preferences without referencing an instancetype at all.
For full customizability, `VirtualMachineTemplates` can be used.

## Scalability

No scalability impact. The changes are purely in API version serving and
conversion. No additional API calls, watches, or ControllerRevisions are
introduced.

## Update/Rollback Compatibility

| Transition | Notes |
|---|---|
| v1.8.x to v1.9.0 | Transparent. `v1` becomes available alongside `v1beta1`. Storage and preferred stay `v1beta1`. |
| v1.9.0 to v1.8.x | Safe rollback. Storage is still `v1beta1`. |
| v1.9.x to v1.10.0 | Storage and preferred move to `v1`. Operators should deploy `kube-storage-version-migrator`. ControllerRevisions re-encoded to `v1` during VM reconciliation. |
| v1.10.0 to v1.9.x | Safe rollback. v1.9.x serves both versions and can read `v1`-stored objects. |
| v1.10.x to v1.11.0 | No changes. `v1beta1` continues to be served (deprecation window). |
| v1.11.0 to v1.10.x | Safe rollback. No changes between these versions. |
| v1.11.x to v1.12.0 | `v1beta1` removed. Migration must be complete. |
| v1.12.0 to v1.11.x | Safe only if `kube-storage-version-migrator` completed the `v1beta1` to `v1` migration before the upgrade to v1.12.0. |

Rollback to the immediately preceding release is safe at each phase. Rolling
back more than one version is not guaranteed. For example, rolling back from
v1.10.0 to v1.8.x would break because v1.8.x does not serve `v1` and cannot
read objects stored as `v1`.

## Functional Testing Approach

### Conversion webhook tests

- Create instancetypes via `v1beta1` API, read back via `v1` API. Verify
  correct field mapping (deprecated fields mapped to replacements).
- Create instancetypes via `v1` API, read back via `v1beta1` API. Verify
  fields are preserved.
- Round-trip conversion: verify `v1beta1` -> `v1` -> `v1beta1` produces
  equivalent objects.

### Functional tests

- Create a VM referencing a `v1` instancetype. Verify the VMI gets the
  expected resources.
- Create a VM referencing a `v1beta1` instancetype during the migration period.
  Verify it continues to work identically.

### Upgrade tests

- Create VMs with `v1beta1` instancetypes pre-upgrade. Verify they continue
  working after upgrade to v1.9.0 with identical behavior.
- Verify ControllerRevisions are re-encoded to `v1` during VM reconciliation
  after storage version changes in v1.10.0.

## Graduation Requirements

### v1.9.0

- [ ] `v1` API types defined (same schema as `v1beta1`, deprecated fields
  removed)
- [ ] Conversion webhooks between `v1beta1` <-> `v1`
- [ ] `v1beta1` marked as deprecated in API documentation
- [ ] `common-instancetypes` updated to use `v1` (`v1` is already served)
- [ ] Unit and functional tests for conversion and backward compatibility
- [ ] Storage version: `v1beta1`, preferred version: `v1beta1`

### v1.10.0

- [ ] Storage version: `v1`, preferred version: `v1`
- [ ] ControllerRevisions re-encoded to `v1` during VM reconciliation
- [ ] Documentation recommends deploying `kube-storage-version-migrator`

### v1.12.0

- [ ] `v1beta1` API version removed
- [ ] Only `v1` served and stored
