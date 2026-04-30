# VEP #NNNN: Drop the preferred prefix from VirtualMachinePreference spec attributes

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Every field within the `VirtualMachinePreference` spec is prefixed with
`preferred` (e.g. `preferredDiskBus`, `preferredAutoattachGraphicsDevice`,
`preferredCPUTopology`). This prefix is redundant — these fields already live
inside a resource called `VirtualMachinePreference` and are by definition
preferences. The prefix adds verbosity without clarity and should be removed.

Removing the prefix is a non-trivial API migration because KubeVirt CRDs
currently use `Strategy: NoneConverter` — meaning there is no Kubernetes
conversion webhook in place. Without a conversion webhook, removing fields from
a CRD schema causes silent, permanent data loss during storage version
migration due to the API server's schema pruning behavior.

This VEP proposes introducing `v1beta2` for the instancetype and preference
CRDs that drops the `preferred` prefix, along with the necessary conversion
infrastructure to safely migrate existing objects.

## Motivation

The `preferred` prefix was introduced when the preference API was first
designed to make it clear that these values are "preferred" defaults rather
than hard requirements (as opposed to instancetype fields which are strictly
applied). While well-intentioned, in practice:

1. **Redundancy**: The resource is already named `VirtualMachinePreference` —
   every field within it is by definition a preference. Writing
   `preferredDiskBus` inside a preference object is equivalent to writing
   `preferencePreferredDiskBus`.

2. **Verbosity**: The prefix adds 9 characters to every field name across ~45
   fields, making the API unnecessarily verbose and harder to read.

3. **Inconsistency**: Some fields already break the pattern
   (`preferSpreadSocketToCoreRatio` uses `prefer` instead of `preferred`),
   revealing that the convention is not consistently applied.

4. **Friction for new fields**: As seen in the discussion on
   [VEP 285](https://github.com/kubevirt/enhancements/pull/286), contributors
   adding new preference fields must decide whether to continue with the
   `preferred` prefix despite recognizing it as unnecessary, creating an
   awkward choice between consistency with a flawed convention and doing the
   right thing.

## Goals

- Remove the `preferred` prefix from all `VirtualMachinePreference` spec
  fields in a new API version.
- Ensure existing `VirtualMachinePreference` and
  `VirtualMachineClusterPreference` objects are safely migrated without data
  loss.
- Provide a clear deprecation and migration path that accounts for skip-level
  upgrade scenarios.

## Non Goals

- Restructuring the preference sub-specs (e.g. merging `DevicePreferences` and
  `FeaturePreferences`). This VEP only addresses the naming prefix.
- Changing the semantics of how preferences are applied to VMs.
- Addressing the broader question of whether certain fields belong in
  instancetypes vs preferences (e.g. the `launchSecurity` discussion in
  VEP 285 is orthogonal).

## Definition of Users

- **Cluster administrators** who manage `VirtualMachineClusterPreference`
  objects and need to understand the migration path during upgrades.
- **VM owners** who create `VirtualMachinePreference` objects and will interact
  with the new field names.
- **Tooling authors** who generate or parse preference objects (e.g.
  `common-instancetypes`, `virtctl`).

## User Stories

- As a user, I want preference field names that are concise and
  non-redundant so that my preference manifests are easier to read and write.
- As a cluster administrator, I want existing preference objects to be
  automatically migrated during upgrade so that no manual intervention or data
  loss occurs.
- As a contributor, I want a clear convention for naming new preference fields
  without needing to perpetuate a known-redundant prefix.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/api](https://github.com/kubevirt/api)
- [kubevirt/common-instancetypes](https://github.com/kubevirt/common-instancetypes)
- [kubevirt/containerized-data-importer](https://github.com/kubevirt/containerized-data-importer) (if preference references exist)

## Design

### The problem with field removal today

Investigation using the
[kube-storage-version-migrator](https://github.com/kubernetes-sigs/kube-storage-version-migrator)
has demonstrated the following behaviors when CRD fields are removed:

1. **Schema pruning on read**: When the API server serves an object through a
   version endpoint whose schema does not include a field, that field is
   silently stripped from the response — even though it still exists in etcd.

2. **Permanent loss during migration**: The storage version migrator performs a
   no-op GET + PUT for every object. Because the GET already returns pruned
   data, the PUT writes back an object with the removed fields permanently
   gone from etcd.

3. **No conversion without a webhook**: KubeVirt CRDs use
   `Strategy: NoneConverter`. Without a conversion webhook, renamed fields
   cannot be automatically populated from their old counterparts — the
   migrator can only prune, never convert.

4. **Skip-level upgrade compounding**: Users who skip intermediate versions
   lose all fields removed across those versions in a single migration pass,
   with no opportunity for intermediate conversion webhooks to run (even if
   they had existed).

This is the same pattern already identified for the `preferredUseEfi` →
`preferredEfi` transition, where removing the deprecated boolean fields
without a conversion webhook would cause silent loss of EFI boot
configuration.

### Proposed approach

#### Phase 1: Introduce a conversion webhook

Before any fields can be safely renamed, KubeVirt must have conversion webhook
infrastructure in place for the instancetype and preference CRDs. This is a
prerequisite for any field rename or removal.

- Implement a conversion webhook for `VirtualMachineInstancetype`,
  `VirtualMachineClusterInstancetype`, `VirtualMachinePreference`, and
  `VirtualMachineClusterPreference`.
- Change the CRD conversion strategy from `NoneConverter` to `Webhook`.
- The webhook initially performs identity conversion (no field changes), to
  validate the infrastructure works correctly.

This phase also unblocks the safe removal of the already-deprecated
`preferredUseEfi` and `preferredUseSecureBoot` fields, which face the same
conversion problem.

#### Phase 2: Introduce v1beta2 with renamed fields

Introduce `v1beta2` that drops the `preferred` prefix from all fields.

Field mapping examples:

| v1beta1 (old) | v1beta2 (new) |
|---|---|
| `preferredDiskBus` | `diskBus` |
| `preferredCPUTopology` | `cpuTopology` |
| `preferredAutoattachGraphicsDevice` | `autoattachGraphicsDevice` |
| `preferredMachineType` | `machineType` |
| `preferredStorageClassName` | `storageClassName` |
| `preferredEfi` | `efi` |
| `preferredClockOffset` | `clockOffset` |
| `preferredInterfaceModel` | `interfaceModel` |
| `preferSpreadSocketToCoreRatio` | `spreadSocketToCoreRatio` |

The conversion webhook handles bidirectional mapping between old and new field
names.

Both API versions are served simultaneously. `v1beta2` becomes the storage
version. The storage version migrator rewrites all objects, and because the
conversion webhook is in place, old field values are correctly mapped to new
field names during the migration — no data loss.

#### Phase 3: Deprecate and remove v1beta1

Following the standard Kubernetes API deprecation policy:

- `v1beta1` is marked as deprecated with a served-but-not-storage status.
- After a suitable deprecation window (at least two releases), `v1beta1` is
  removed entirely.
- The conversion webhook can be simplified once `v1beta1` is no longer served.

### Conversion webhook field mapping

The conversion webhook must handle the following field renames across all
preference sub-specs. The full list of ~45 fields and their mappings is
mechanical — every `preferred`-prefixed field maps to the same name with the
prefix removed.

For `PreferSpreadSocketToCoreRatio`, the `prefer` prefix (not `preferred`) is
also dropped, becoming `SpreadSocketToCoreRatio`.

The already-deprecated fields (`DeprecatedPreferredUseEfi`,
`DeprecatedPreferredUseSecureBoot`) should not be carried forward into
`v1beta2`. The conversion webhook should map these to the `Efi` struct field
with the appropriate value. This cleanup can be bundled with the prefix removal
or done as a prerequisite in Phase 1.

### Impact on ControllerRevisions

`VirtualMachinePreference` objects referenced by VMs are captured as
`ControllerRevision` snapshots. These revisions contain the raw API object
at the version it was created with.

The existing application-level conversion code in
`pkg/instancetype/compatibility/` must be extended to handle the new field
names. Since ControllerRevisions are not served through the preference CRD
endpoints, the conversion webhook does not apply to them — they require
Go-level decoding and conversion at runtime, as is already done today for
v1alpha1 → v1beta1.

## API Examples

Current (v1beta1):
```yaml
apiVersion: instancetype.kubevirt.io/v1beta1
kind: VirtualMachinePreference
metadata:
  name: my-preference
spec:
  cpu:
    preferredCPUTopology: Spread
  devices:
    preferredDiskBus: virtio
    preferredInterfaceModel: virtio
    preferredAutoattachGraphicsDevice: true
    preferredAutoattachSerialConsole: true
    preferredRng: {}
    preferredTPM: {}
    preferredBlockMultiQueue: true
    preferredNetworkInterfaceMultiQueue: true
  firmware:
    preferredEfi:
      secureBoot: true
  machine:
    preferredMachineType: q35
```

Proposed (v1beta2):
```yaml
apiVersion: instancetype.kubevirt.io/v1beta2
kind: VirtualMachinePreference
metadata:
  name: my-preference
spec:
  cpu:
    cpuTopology: Spread
  devices:
    diskBus: virtio
    interfaceModel: virtio
    autoattachGraphicsDevice: true
    autoattachSerialConsole: true
    rng: {}
    tpm: {}
    blockMultiQueue: true
    networkInterfaceMultiQueue: true
  firmware:
    efi:
      secureBoot: true
  machine:
    machineType: q35
```

## Alternatives

### Do nothing

Continue with the `preferred` prefix. New fields (like those proposed in
VEP 285) would continue to use it for consistency. This is the lowest-risk
option but perpetuates the API wart indefinitely and would carry forward into
any future `v1` promotion.

### Drop the prefix only for new fields

Stop adding the `preferred` prefix to new fields while leaving existing fields
unchanged. This creates an inconsistent API where some fields have the prefix
and others do not, which is arguably worse than the current state. The field
naming would become arbitrary rather than following a clear (if redundant)
convention.

### Introduce field aliases

Add alias fields without the prefix that map to the same underlying storage.
This avoids the need for a new API version but doubles the API surface, adds
validation complexity (mutual exclusivity), and the old names would still
exist. This is more complex than a clean version bump.

## Scalability

No scalability impact. The conversion webhook adds a small amount of latency
to preference object requests during the transition period, but preference
objects are low-volume resources (typically tens to hundreds per cluster, not
thousands).

## Update/Rollback Compatibility

This is the central concern of this VEP. The conversion webhook is the key
mechanism that ensures update and rollback safety:

- **Upgrade**: The conversion webhook translates v1beta1 field names to
  v1beta2. Storage migration rewrites objects through the webhook, preserving
  all data.
- **Rollback**: While both versions are served, rolling back to a version that
  only understands v1beta1 is safe because the webhook can convert in both
  directions. After v1beta1 is removed, rollback to a pre-webhook version is
  no longer supported (consistent with standard Kubernetes API versioning
  policy).
- **Skip-level upgrades**: Because the conversion webhook is present in the
  serving version, skip-level upgrades are safe — the webhook handles
  conversion regardless of which intermediate versions were skipped. This is
  a direct improvement over the current `NoneConverter` approach, which has
  been [demonstrated](https://github.com/kubernetes-sigs/kube-storage-version-migrator)
  to cause data loss in skip-level scenarios.

## Functional Testing Approach

- Unit tests for the conversion webhook covering all ~45 field mappings in
  both directions (v1beta1 → v1beta2 and v1beta2 → v1beta1).
- Integration tests that create preference objects in v1beta1, trigger
  storage migration, and verify all fields are preserved in v1beta2.
- Upgrade tests that create preferences in v1beta1, perform an upgrade, and
  verify the objects are accessible and correct through v1beta2.
- ControllerRevision compatibility tests verifying that VMs referencing old
  preference revisions continue to work after upgrade.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha

- [ ] Conversion webhook infrastructure implemented for preference CRDs
- [ ] `v1beta2` introduced with renamed fields (prefix dropped)
- [ ] Bidirectional conversion between old and new field names
- [ ] ControllerRevision compatibility code updated
- [ ] Unit tests for all field mappings

### Beta

- [ ] Storage version switched to `v1beta2`
- [ ] `v1beta1` marked as deprecated
- [ ] Storage migration verified to preserve all field values
- [ ] Skip-level upgrade testing completed
- [ ] `common-instancetypes` updated to use new field names
- [ ] Documentation updated

### GA

- [ ] `v1beta1` removed after deprecation window
- [ ] Conversion webhook simplified (no longer needs bidirectional conversion with `v1beta1`)
- [ ] All downstream tooling confirmed migrated
