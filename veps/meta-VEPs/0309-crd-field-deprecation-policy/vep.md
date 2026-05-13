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
- [ ] Document in the PR description which release contains the migration and
      which releases cannot be safely skipped

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

### Beta

- [ ] At least one field migration has been implemented following the policy
- [ ] Migration framework in virt-operator exists for field-level CR migration
- [ ] All existing deprecated fields with structured replacements have
      migration steps planned

### GA

- [ ] All SIGs consistently apply the policy in PR reviews
- [ ] No new field removals have been merged without following the policy
- [ ] Documentation updated in the KubeVirt contributor guide
