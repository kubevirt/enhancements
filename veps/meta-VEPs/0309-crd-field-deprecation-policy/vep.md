# VEP #309: CRD Field Deprecation and Removal Policy

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/309) (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt currently has no formal policy governing how CRD fields are
deprecated, migrated, and removed across releases. All KubeVirt CRDs use
`NoneConverter`, which means the API server performs no field transformation
between API versions — it only applies structural schema pruning. This creates
a risk of silent data loss when deprecated fields are removed from CRD schemas,
particularly when the deprecated field has a structured replacement that
requires conversion logic to populate.

This VEP proposes establishing a policy for CRD field deprecation and removal
that ensures stored data is not silently lost during storage version migration,
regardless of the upgrade path taken by consumers.

The Kubernetes project maintains a
[deprecation policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)
and [API change guidelines](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md)
that govern how built-in API fields are deprecated and removed. These policies
rely on mechanisms unavailable to CRD authors — specifically, internal type
representations, star-topology conversion functions, and protobuf serialization
with tombstone tag reservation. This VEP adapts the principles behind those
upstream policies to the constraints of CRDs using `NoneConverter`, where the
API server performs no field transformation and relies solely on structural
schema pruning.

## Motivation

KubeVirt uses `Strategy: NoneConverter` for every multi-version CRD:

```go
// pkg/virt-operator/resource/generate/components/crds.go
Conversion: &extv1.CustomResourceConversion{
    Strategy: extv1.NoneConverter,
},
```

With `NoneConverter`, the API server does not call a conversion webhook when
serving or storing objects. When the storage version changes and the
[kube-storage-version-migrator](https://github.com/kubernetes-sigs/kube-storage-version-migrator)
rewrites objects in etcd, it performs a no-op GET + PUT through the API server.
The API server applies the structural schema of the target version, silently
pruning any fields not defined in that schema.

For fields that are simply removed with no replacement, this is expected. But
for fields that are **restructured** — where an old field is replaced by a new
field with a different shape — pruning causes permanent data loss: the old
field is dropped and the new field is never populated.

KubeVirt has application-level conversion code in virt-controller (e.g. the
ControllerRevision upgrade handler in `pkg/instancetype/upgrade/`) that can
transform objects between API versions at runtime. However, this code only runs
when virt-controller reconciles individual objects — it does not run during
storage version migration. Standalone CRs (preferences, snapshots, clones) are
not covered by this path at all.

### Impact on the broader ecosystem

KubeVirt is consumed by multiple downstream distributions, each with their own
upgrade lifecycle policies. Some distributions support sequential upgrades
only; others offer skip-level upgrade paths (e.g. N to N+2 or N to N+3) that
span multiple KubeVirt minor releases.

Today, there is no structured way for downstream consumers to determine
whether a given upgrade path is safe with respect to CRD field migrations.
A distribution maintainer evaluating whether their users can safely skip from
KubeVirt 1.X to 1.X+3 would need to manually audit every intermediate
release's CRD schema changes, identify which fields were removed, determine
whether migration logic existed, and check whether that migration logic still
exists in the target version. This information is not documented anywhere —
it is scattered across PRs, Go doc comments, and tribal knowledge.

A clear upstream policy with documented migration lifecycles would allow
downstream distributions to programmatically determine which intermediate
versions contain required migration steps, enabling them to build skip-level
upgrade support on top of the upstream policy without requiring the upstream
project to adopt any specific upgrade window.

### Concrete examples of at-risk field transitions

**PreferredUseEfi / PreferredUseSecureBoot → PreferredEfi** (instancetype
preferences):

```go
// Deprecated: Will be removed with v1beta2 or v1
DeprecatedPreferredUseEfi *bool `json:"preferredUseEfi,omitempty"`

// Deprecated: Will be removed with v1beta2 or v1
DeprecatedPreferredUseSecureBoot *bool `json:"preferredUseSecureBoot,omitempty"`

PreferredEfi *v1.EFI `json:"preferredEfi,omitempty"`
```

When these deprecated booleans are removed from the schema, the storage version
migrator will prune them. Since preferences are standalone CRs (not embedded in
ControllerRevisions), no application-level conversion applies. The replacement
`preferredEfi` field is never populated. VMs referencing the affected preference
would silently lose their EFI boot configuration.

**Snapshot Indications → SourceIndications**:

```go
// Deprecated: Use SourceIndications instead. This field will be removed in a future version.
Indications []Indication `json:"indications,omitempty"`

SourceIndications []SourceIndication `json:"sourceIndications,omitempty"`
```

The old flat string list would be pruned without populating the structured
replacement.

### Demonstrated impact

A [runnable demo](https://github.com/lyarwood/kube-storage-version-migrator/tree/demo/demo)
reproduces this data loss against a kind cluster with raw etcd diffs showing
the before and after state. The demo covers:

1. Basic field pruning during storage version migration
2. Field removal within a single API version
3. Compounded field loss during multi-version skip-level upgrades
4. Conversion webhook data preservation vs skip-level data loss

## Goals

- Establish a policy for how CRD fields must be deprecated and removed
- Ensure that removing a deprecated field from a CRD schema never causes
  silent data loss for stored objects
- Define what migration steps must occur before a field can be removed
- Document the interaction between `NoneConverter`, the storage version
  migrator, and field deprecation
- Align with the principles of the upstream Kubernetes
  [deprecation policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)
  and [API change guidelines](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md),
  adapted for CRDs

## Non Goals

- Mandate a specific technical mechanism (conversion webhooks vs
  virt-operator migration vs other approaches)
- Change KubeVirt's supported upgrade path (currently N to N+1)
- Address downstream distribution-specific upgrade policies (e.g. N to N+3)
- Introduce conversion webhooks as part of this VEP (a separate VEP could
  propose that)

## Definition of Users

- **KubeVirt API authors**: Developers adding, deprecating, or removing fields
  from KubeVirt CRD schemas
- **KubeVirt consumers**: Anyone running KubeVirt who upgrades between
  releases, including distributions, platform operators, and end users
- **SIG leads and approvers**: Reviewers who need clear criteria for
  evaluating API changes that involve field deprecation

## User Stories

- As a KubeVirt API author, I want clear rules for how to deprecate and
  remove a CRD field so that I don't accidentally cause data loss for users

- As a KubeVirt consumer upgrading from version N to N+1, I want assurance
  that no stored data is silently lost during the upgrade

- As a KubeVirt consumer who has skipped one or more releases, I want to
  understand what data loss risks exist and what mitigation steps are required

- As a SIG lead reviewing a PR that removes a deprecated field, I want a
  checklist to verify that the removal is safe for all stored objects

- As a downstream distribution maintainer, I want a clear record of which
  releases contain migration steps and which remove fields, so that I can
  determine which intermediate versions my upgrade tooling must pass through
  and build skip-level upgrade support on top of the upstream policy

## Repos

- kubevirt/kubevirt
- kubevirt/api
- kubevirt/enhancements

## Design

### The problem with the current approach

Today, the implicit process for field deprecation is:

1. Add a replacement field alongside the deprecated field
2. Mark the old field as deprecated in Go doc comments
3. At some future release, remove the old field from the CRD schema

Step 3 is where data loss occurs. When the schema changes, the storage version
migrator rewrites all objects. The API server prunes the old field (not in the
schema) and does not populate the new field (no conversion logic runs during
migration). The data in the old field is permanently lost.

The application-level conversion in virt-controller only helps for objects that
virt-controller actively reconciles (e.g. ControllerRevision upgrades for
instancetype references). Standalone CRs — preferences, snapshots, clones,
backups — have no such path.

### Relationship to upstream Kubernetes compatibility rules

The Kubernetes
[API change guidelines](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md#on-compatibility)
define six backward compatibility rules. Rule 4 requires that "it must be
possible to round-trip your change (convert to different API versions and back)
with no loss of information." The Kubernetes
[deprecation policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)
formalizes this as Rule #2: "API objects must be able to round-trip between API
versions in a given release without information loss."

For built-in Kubernetes types, round-trip fidelity is enforced by conversion
functions that transform objects between versioned representations via an
internal (hub) type. When a field is deprecated in one version, the conversion
function maps it to the replacement field in the internal type, preserving data
across versions.

CRDs using `NoneConverter` have no internal type and no conversion functions.
The API server stores and serves objects using structural schema pruning alone.
This means that the round-trip guarantee is violated whenever a field is removed
from the schema of the stored version: objects written with the old field cannot
be read back with their data intact. The data loss described in this VEP is, in
upstream terms, a round-trip fidelity violation.

The upstream
[deprecation policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#fields-of-rest-resources)
for fields of REST resources (Rule #5a) states that individual fields may be
deprecated with appropriate documentation, and Rule #5b states that deprecated
fields that were previously required must become optional. Rule #1 states that
"once an API element has been added to an API group at a particular version, it
can not be removed from that version or have its behavior significantly
changed." For CRDs, this translates to: a field must not be removed from any
served API version's schema until all stored objects have been migrated away
from that field.

### Proposed policy

Before a deprecated field can be removed from a CRD schema, **all stored
objects must have been migrated to the replacement field**. This migration must
happen through a mechanism that operates on every instance of the CR, not just
objects that virt-controller happens to reconcile.

Concretely, the field removal process would be:

1. **Release N**: Add the replacement field. Mark the old field as deprecated.
   Both fields coexist in the schema.

2. **Release N+1** (or later): virt-operator, during its upgrade reconciliation
   (before applying the updated CRD), reads all instances of the affected CR
   and writes the replacement field from the old field for any object that has
   the old field set but not the new one. This is an explicit migration step
   that runs once during upgrade.

3. **Release N+2** (or later, at least one release after step 2): Remove the
   old field from the CRD schema. By this point, all stored objects already
   have the replacement field populated (done in step 2), so the storage
   version migrator's pruning of the old field does not lose data.

The minimum gap between step 2 and step 3 (one release) ensures that any
consumer doing a sequential N+1 upgrade has gone through the migration step
before the field is removed. 

### How to mark a field as deprecated

When deprecating a CRD field (Release N in the policy above), the following
markers are required. These align with the upstream Kubernetes convention
described in
[Rule #5a](https://kubernetes.io/docs/reference/using-api/deprecation-policy/#fields-of-rest-resources)
and the
[API change guidelines](https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md).

1. **Go doc comment**: Add a `// Deprecated:` comment to the Go struct field.
   The comment must state the replacement field and the earliest version in
   which the old field may be removed:

   ```go
   // Deprecated: Use PreferredEfi instead. This field will be removed no
   // earlier than KubeVirt v1.X+2. Added in v1.W, deprecated in v1.X.
   DeprecatedPreferredUseEfi *bool `json:"preferredUseEfi,omitempty"`
   ```

2. **Go field name prefix**: Prefix the Go struct field name with `Deprecated`
   (e.g., `DeprecatedPreferredUseEfi`). This is a KubeVirt convention that
   makes deprecated fields visually distinct in code. The JSON serialization
   name (`json:"preferredUseEfi"`) must NOT change, as changing it would break
   existing stored objects and clients.

3. **Admission webhook warning**: Add an admission warning that is returned
   when a user creates or updates an object that sets the deprecated field.
   This follows the pattern already established by `warnDeprecatedAPIs` in the
   validating webhook admitters and aligns with the upstream Kubernetes practice
   of returning `Warning` headers for deprecated API usage (introduced in
   Kubernetes v1.19). Example warning text:

   > Field .spec.firmware.preferredUseEfi is deprecated; use
   > .spec.firmware.preferredEfi instead. This field will be removed in
   > KubeVirt v1.X+2.

4. **OpenAPI description**: Include the deprecation notice in the field's
   OpenAPI/Swagger description so that it appears in API discovery and
   generated documentation. This is achieved via the existing swagger doc
   generation from Go doc comments.

**Note on JSON Schema limitations:** The CRD structural schema
(`JSONSchemaProps`) does not support a per-field `deprecated` boolean. The
`Deprecated` flag on CRD versions applies to entire API versions, not
individual fields. Per-field deprecation can only be communicated through
description text and admission warnings.

**Note on tombstone markers:** The upstream Kubernetes codebase uses
`// +k8s:deprecated=fieldname,protobuf=N` tombstone comments to reserve field
names and protobuf tag numbers after a field is removed. KubeVirt CRD types do
not use protobuf serialization. For CRDs, the relevant concern is JSON field
name reservation, not protobuf tag reservation. When a field is removed from
the Go struct (Release N+2), a tombstone comment should be left in its place to
prevent the JSON field name from being reused:

```go
// Deprecated: preferredUseEfi was removed in v1.X+2. Do not reuse this
// JSON field name. Replacement: PreferredEfi.
```

This is a lightweight adaptation of the upstream tombstone convention that
addresses the JSON-relevant concern (field name reuse would cause
deserialization conflicts with old stored data that has not yet been cleaned
up) without the protobuf tag portion that does not apply.

### Deprecation timeline

The Kubernetes
[deprecation policy](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)
specifies minimum API lifetimes by stability level:

- **GA**: API elements may not be removed within a major version
- **Beta**: Deprecated after no more than 9 months or 3 minor releases
  (whichever is longer)
- **Alpha**: May be removed in any release without prior notice

For CRD fields, the stability level of the field inherits from the stability
level of the CRD API version it belongs to. A field in a `v1beta1` CRD version
is a beta field; a field in a `v1` CRD version is a GA field.

This VEP's 3-release process (add replacement in N, migrate in N+1, remove in
N+2) provides a minimum of 2 releases between deprecation and removal. For
KubeVirt's current quarterly release cadence, this amounts to approximately 6
months — shorter than the upstream 9-month minimum for beta APIs.

The VEP does not mandate a minimum calendar duration because the critical
safety guarantee is the migration step, not the passage of time. The 3-release
minimum ensures that:

1. Users have at least one full release cycle to observe deprecation warnings
   and update their usage
2. The migration step has shipped and been tested in production before the
   field is removed
3. Sequential N-to-N+1 upgraders always pass through the migration release

SIGs may choose to extend the deprecation window beyond the 3-release minimum
for widely-used fields, particularly for fields in GA API versions. The review
checklist should document the rationale for the chosen timeline.

### What this means for non-sequential upgrades

This policy does not guarantee safety for consumers who skip the release
containing the migration step (step 2). A consumer upgrading from release N
directly to release N+2 would hit the field removal without the migration
having run.

This is a conscious scope limitation. KubeVirt's supported upgrade path is
N to N+1. Consumers requiring broader skip-level support would need to either:

- Ensure their upgrade tooling runs through each intermediate virt-operator
  version sequentially (even if the underlying Kubernetes cluster is
  skip-level)
- Carry additional migration logic in their distribution

However, a well-defined upstream policy is what **enables** downstream
distributions to build skip-level support on top. Without a clear policy,
downstreams cannot determine which releases contain migration steps, which
releases remove fields, or which intermediate versions are safe to skip. The
information simply does not exist in a structured form.

With this policy in place, each field removal PR documents which release
contains the migration step. A downstream distribution can use this
information to align its own lifecycle policies — for example, by ensuring
that its supported upgrade windows always include the migration release, or
by accumulating migration steps from multiple releases into a single operator
version. The upstream project does not need to adopt any specific skip-level
window for this to work; it only needs to make the migration lifecycle
**explicit and documented** rather than implicit and silent.

### Differences from upstream Kubernetes API deprecation

The following table summarizes how upstream Kubernetes deprecation mechanisms
map to the CRD context:

| Upstream mechanism | Built-in API types | KubeVirt CRDs (`NoneConverter`) |
|---|---|---|
| Conversion functions (hub-and-spoke) | Internal type with bidirectional conversion | Not available; virt-operator migration substitutes |
| Round-trip fidelity tests | Fuzz-based tests via internal type | Round-trip tests exist but do not cover cross-version conversion (no conversion exists) |
| `+k8s:deprecated=field,protobuf=N` tombstone | Reserves JSON name and protobuf tag | Adapted: reserve JSON name only (CRD types do not use protobuf) |
| Per-field `deprecated` in JSON Schema | N/A (built-in types use OpenAPI generated from code) | Not supported by CRD structural schema (`JSONSchemaProps` has no per-field `deprecated`) |
| Warning header on deprecated API version | Automatic via apiserver | Available via CRD version `deprecated: true` + `deprecationWarning` field |
| Warning on deprecated field usage | Admission webhook warnings | Available via validating/mutating webhook `Warnings` response field |
| Storage version advancement ([Rule #4b](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)) | Enforced by apiserver | Must be enforced by virt-operator: do not change storage version in the same release a new version is introduced |
| Minimum deprecation window | 9 months / 3 releases for beta | 3-release process (2 releases between deprecation and removal) |

Key constraints:

- **No per-field schema annotation**: The CRD `JSONSchemaProps` does not
  support a `deprecated` boolean on individual properties. Field-level
  deprecation can only be signaled through description text and admission
  warnings, not through the schema itself.
- **No protobuf for CRD types**: Most KubeVirt CRD types do not use protobuf
  serialization. The `+k8s:deprecated` tombstone's `protobuf=N` component is
  not applicable. Only JSON field name reservation matters.
- **No conversion functions**: The fundamental reason this VEP exists. Without
  conversion functions, the API server cannot transform deprecated fields to
  their replacements during storage migration. The virt-operator migration step
  in this VEP substitutes for what conversion functions provide in upstream.

### Review checklist for field removal PRs

Any PR that removes a field from a CRD schema must:

- [ ] Confirm that a replacement field exists (if the field is being
      restructured, not just deleted)
- [ ] Confirm that a migration step was added in a prior release that
      populates the replacement field from the old field for all stored objects
- [ ] Confirm that the migration step runs in virt-operator during upgrade,
      before the CRD schema is applied
- [ ] Confirm that at least one release has shipped with the migration step
      before the field is removed
- [ ] Confirm that an admission warning was added in the deprecation release
      (Release N) that notifies users when the deprecated field is set
- [ ] Confirm that a tombstone comment is left in the Go struct at the
      location of the removed field, reserving the JSON field name from reuse
- [ ] Confirm that the `// Deprecated:` Go doc comment included the removal
      target version and was present for at least the minimum deprecation window
- [ ] Document in the PR description which release contains the migration and
      which releases cannot be safely skipped
- [ ] Update the KubeVirt release notes to note the field removal

## API Examples

No API changes are proposed by this VEP. This is a process and policy
proposal.

### Example: safe field removal timeline

| Release | Action | Schema | Migration |
|---|---|---|---|
| v1.X | Add `preferredEfi`, deprecate `preferredUseEfi` | Both fields present | None needed |
| v1.X+1 | Add migration in virt-operator | Both fields present | virt-operator populates `preferredEfi` from `preferredUseEfi` for all stored preferences during upgrade |
| v1.X+2 | Remove `preferredUseEfi` from schema | Only `preferredEfi` | Storage version migrator prunes `preferredUseEfi` — safe because `preferredEfi` was already populated in v1.X+1 |

### Example: field removal PR checklist

```markdown
## Field Removal: preferredUseEfi

- [x] Replacement field: `preferredEfi` (added in v1.4.0)
- [x] Migration step: virt-operator populates `preferredEfi` from
      `preferredUseEfi` during upgrade (added in v1.X+1, PR #NNNNN)
- [x] Migration shipped in: v1.X+1
- [x] Minimum one release gap: v1.X+2 >= v1.X+1 + 1 ✓
- [x] Admission warning added in v1.X for use of `preferredUseEfi` (PR #MMMMM)
- [x] Tombstone comment left in Go struct reserving JSON name `preferredUseEfi`
- [x] `// Deprecated:` comment present since v1.X with removal target v1.X+2
- [x] Release notes updated
- Note: consumers upgrading from v1.X or earlier directly to v1.X+2
  will skip the migration. Sequential upgrade through v1.X+1 is required.
```

## Alternatives

### Alternative 1: Introduce conversion webhooks

Replace `NoneConverter` with `Webhook` conversion for multi-version CRDs.
The webhook would handle field transformation during API server reads and
writes, including during storage version migration.

**Pros:**
- Field transformation happens transparently during migration
- No explicit migration step needed in virt-operator
- Standard Kubernetes mechanism for CRD version conversion

**Cons:**
- Significant operational complexity (TLS certificates, webhook deployment,
  availability requirements)
- Webhook must be available during API server startup — failure modes are
  severe (CRD becomes unservable)
- Webhook code must be carried for as long as old stored objects may exist,
  which depends on the supported upgrade window
- Does not solve the lifecycle question — the webhook still needs to know
  about all transitions from all supported source versions

**Decision:** Not rejected, but out of scope for this VEP. A separate VEP
could propose conversion webhooks. This VEP focuses on the policy layer
regardless of mechanism.

### Alternative 2: Never remove deprecated fields from CRD schemas

Keep deprecated fields in the OpenAPI schema indefinitely. The Go struct
can rename them with a `Deprecated` prefix, but the JSON serialization name
stays in the schema. No data loss because the field is never pruned.

**Pros:**
- Zero risk of data loss
- No migration logic needed
- No conversion webhooks needed
- Works for any upgrade path, including arbitrary skip-level

**Cons:**
- Schema grows indefinitely
- Confusing for users who see deprecated fields in API discovery
- Old fields may conflict with new fields or cause unexpected behavior if
  both are set
- Does not encourage API evolution

**Decision:** This is effectively the current state for some fields (e.g.
`preferredUseEfi` is deprecated but still in the schema). Could be adopted
as formal policy, but it constrains API evolution and may not be acceptable
long-term.

### Alternative 3: No policy — leave it to individual PR authors

Continue the current approach where field removal is handled ad-hoc in
individual PRs.

**Pros:**
- No process overhead
- Maximum flexibility for API authors

**Cons:**
- Silent data loss risk (the motivation for this VEP)
- No consistency across SIGs
- No review checklist for approvers
- Data loss is discovered by users in production, not during review

**Decision:** Rejected. The current approach has already produced multiple
at-risk field transitions.

## Scalability

No scalability impact. The virt-operator migration step iterates over all
instances of a CR type once during upgrade. For most KubeVirt CRD types, the
object count is small (preferences, snapshots, clones are typically in the
hundreds, not millions).

## Update/Rollback Compatibility

This VEP improves update compatibility by ensuring field removals are safe.

**Upgrade:** The migration step in virt-operator runs during upgrade before the
CRD schema is applied. If the migration fails, the upgrade should be blocked
(CRD schema not applied, old field still present).

**Rollback:** If rolling back to a version before the migration step, both
fields would be present in the schema. Objects that were migrated (new field
populated) would still have the old field until the storage version migrator
prunes it. No data loss in either direction.

## Functional Testing Approach

- E2E tests for each field migration step: create objects with old field only,
  upgrade, verify new field is populated
- E2E tests for field removal: verify that objects with only the new field
  survive storage version migration without data loss
- Upgrade tests that verify the migration step runs before the CRD schema
  change is applied

## Implementation History

- 2026-05: Initial investigation and demo of storage version migration data
  loss risks. [Demo and analysis](https://github.com/lyarwood/kube-storage-version-migrator/tree/demo/demo).

## Graduation Requirements

This is a policy/process VEP. Graduation represents adoption of the policy
across KubeVirt SIGs.

### Alpha

- [ ] Policy documented and agreed upon by all SIGs
- [ ] Review checklist added to PR template for API changes
- [ ] Existing at-risk field transitions identified and tracked
- [ ] Admission warnings implemented for all currently-deprecated CRD fields
      that have structured replacements

### Beta

- [ ] At least one field migration has been implemented following the policy
- [ ] Migration framework in virt-operator exists for field-level CR migration
- [ ] All existing deprecated fields with structured replacements have
      migration steps planned

### GA

- [ ] All SIGs consistently apply the policy in PR reviews
- [ ] No new field removals have been merged without following the policy
- [ ] Documentation updated in the KubeVirt contributor guide
