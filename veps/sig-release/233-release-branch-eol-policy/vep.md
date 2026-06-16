# VEP #233: Enforce Release Branch End-of-Life Policy

## VEP Status Metadata

### Target releases

<!--
This VEP is a process/policy change and does not follow the standard
Alpha/Beta/GA feature graduation model. The target version below reflects
when the policy is announced and takes effect.
-->

- This VEP targets announcement for version: v1.9
- This VEP targets enforcement for version: v1.10

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue [#233](https://github.com/kubevirt/enhancements/issues/233) created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

KubeVirt currently maintains an unbounded number of release branches with no
enforced End-of-Life (EOL) policy. The existing documentation in
[docs/release.md](https://github.com/kubevirt/kubevirt/blob/main/docs/release.md)
states that EOL is tied to the Kubernetes support period but explicitly notes
that *"The EOL of a KubeVirt release is currently not enforced"*. This has led
to a situation where CVE fixes, dependency updates, and other backports must be
applied to an ever-growing number of branches, consuming significant
contributor time and CI resources.

This VEP proposes adopting a two-tier EOL policy that combines the
[Kubernetes patch release support period](https://kubernetes.io/releases/patch-releases/#support-period)
with structured downstream-aligned extended maintenance:

- **Tier 1 — Full support**: The **3 most recent minor releases** receive
  active upstream maintenance including CVE fixes, bug fixes, and dependency
  updates with full CI. This matches the Kubernetes model.
- **Tier 2 — Extended maintenance**: The **next 2 older releases** remain open
  for merges but do not receive proactive upstream backports. Downstream
  vendors or other interested parties may propose cherry-picks and are
  responsible for funding CI on these branches.

## Motivation

As of March 2026, the `kubevirt/kubevirt` repository has **59 release
branches** spanning from `release-0.4` to `release-1.8`. While the
[sig-release support matrix](https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md)
only formally tracks v1.2 through v1.7 (with v1.2-v1.4 already marked EOL),
in practice backports continue to land on branches well beyond the documented
support window.

Recent CVE responses illustrate how the lack of an enforced EOL compounds
the maintenance burden:

### CVE-2023-39325 / CVE-2023-44487 — HTTP/2 Rapid Reset (Oct 2023, High)

This high-severity vulnerability in `golang.org/x/net` and
`google.golang.org/grpc` required backports to **7 branches**: `release-0.49`,
`release-0.53`, `release-0.58`, `release-0.59`, `release-1.0`, `release-1.1`,
and `release-1.2`
([#10622](https://github.com/kubevirt/kubevirt/pull/10622),
[#10604](https://github.com/kubevirt/kubevirt/pull/10604),
[#10677](https://github.com/kubevirt/kubevirt/pull/10677),
[#10676](https://github.com/kubevirt/kubevirt/pull/10676),
[#10675](https://github.com/kubevirt/kubevirt/pull/10675),
[#10674](https://github.com/kubevirt/kubevirt/pull/10674),
[#10673](https://github.com/kubevirt/kubevirt/pull/10673)).
Under the proposed two-tier policy, 5 branches (0.58, 0.59, 1.0, 1.1, 1.2)
would have been in scope (3 Tier 1 + 2 Tier 2), with `release-0.49` and
`release-0.53` at full EOL. Notably, `release-0.49` targeted Kubernetes 1.24,
which had already been EOL for months when the fix landed.

### CVE-2024-21626 — runc Container Escape (Jan 2024, Critical)

This critical container escape vulnerability required backports to
**4 branches**: `release-0.58`, `release-0.59`, `release-1.0`, and
`release-1.1`
([#13253](https://github.com/kubevirt/kubevirt/pull/13253),
[#13254](https://github.com/kubevirt/kubevirt/pull/13254),
[#13255](https://github.com/kubevirt/kubevirt/pull/13255),
[#13256](https://github.com/kubevirt/kubevirt/pull/13256)).
`release-0.58` targeted Kubernetes 1.26, which was well past its own EOL when
the fix was merged.

### CVE-2025-22869 — golang.org/x/crypto SSH Handshake Panic (2025, High)

A panic-triggering vulnerability in the Go SSH handshake was backported to
`release-1.3` and `release-1.4`
([#14633](https://github.com/kubevirt/kubevirt/pull/14633),
[#14629](https://github.com/kubevirt/kubevirt/pull/14629)),
both of which are already marked EOL in the sig-release support matrix.

### CVE-2025-47913 — golang.org/x/crypto Denial of Service (2025, High)

A denial-of-service vulnerability in Go's crypto library was backported to
`release-1.5` and `release-1.6`
([#16887](https://github.com/kubevirt/kubevirt/pull/16887),
[#16870](https://github.com/kubevirt/kubevirt/pull/16870)).

### CVE Backport Scope Comparison

| CVE | Date | Severity | Branches Backported | Under 3-Release-Only | Under Two-Tier (T1 + T2) |
|-----|------|----------|--------------------:|---------------------:|-------------------------:|
| CVE-2023-39325 | Oct 2023 | High | 7 | 3 | 5 (3 + 2) |
| CVE-2024-21626 | Jan 2024 | Critical | 4 | 3 | 4 (3 + 1) |
| CVE-2025-22869 | 2025 | High | 2 | 2 | 2 (0 + 2) |
| CVE-2025-47913 | 2025 | High | 2 | 2 | 2 (1 + 1) |

Under a 3-release-only policy, branches beyond the most recent 3 are closed
and downstream vendors must carry backports in private forks. The two-tier
model keeps the upstream community obligation bounded to 3 releases (Tier 1)
while providing open Tier 2 branches where vendors can land backports
upstream. Note that Tier 2 backports are at the vendor's discretion — the
upstream community is not obligated to initiate or review them.

The pattern is clear: without an enforced EOL, each CVE response requires
work across an unpredictable and growing set of branches. This creates
several compounding problems:

1. **Unsustainable contributor burden**: Each CVE or critical fix must be
   backported, reviewed, and merged across many branches. The HTTP/2 rapid
   reset fix alone required 7 separate cherry-pick PRs, each needing review
   and CI validation.
2. **Unbounded CI cost**: CI lanes must be maintained and executed for every
   active release branch. Old branches frequently break due to infrastructure
   drift, consuming debugging time unrelated to the fix itself.
3. **False sense of security**: Users on ancient releases receive sporadic CVE
   fixes but no guarantee of comprehensive coverage, creating a false sense
   that the release is fully maintained.
4. **No upgrade incentive**: Without a hard EOL, downstream consumers have no
   forcing function to upgrade to supported releases.
5. **Inconsistency with stated policy**: The documentation already ties EOL to
   Kubernetes support periods, but the lack of enforcement makes the policy
   meaningless in practice. Fixes are landing on branches targeting Kubernetes
   versions that Kubernetes itself stopped supporting long ago.

## Goals

- Define a hard EOL policy for KubeVirt release branches with a fixed support
  window
- Align the KubeVirt release lifecycle with the Kubernetes release support
  model
- Reduce the number of actively maintained release branches to a bounded,
  predictable set
- Establish automated enforcement of EOL through CI and branch protection
- Provide a clear transition plan for the existing backlog of unmaintained
  branches

## Non Goals

- Changing the KubeVirt release cadence (3 releases per year, trailing
  Kubernetes by ~2 months)
- Modifying the backport policy for branches that are within the support window
- Changing the Kubernetes version compatibility matrix
- Deleting old release branches or git tags (historical artifacts are preserved)
- Defining a long-term support (LTS) release model (this could be a future
  VEP if there is demand)

## Definition of Users

- **KubeVirt release maintainers**: Contributors who cut releases, manage CI
  lanes, and process backport PRs
- **KubeVirt contributors**: Developers who must create and shepherd cherry-pick
  PRs for bug fixes and CVEs
- **Downstream distributors**: Organizations (e.g., Red Hat, SUSE) that
  consume KubeVirt releases and may maintain their own extended support windows
  independently
- **End users**: Operators running KubeVirt clusters who need to understand
  which versions receive security and bug fixes

## User Stories

- As a **release maintainer**, I want a bounded number of release branches to
  maintain so that CVE response does not require cherry-picks to 10+ branches.
- As a **contributor**, I want a clear policy on which branches accept
  backports so that I do not waste time preparing cherry-picks for unsupported
  releases.
- As a **downstream distributor**, I want a predictable upstream EOL schedule
  so that I can plan my own support lifecycle accordingly.
- As a **downstream distributor**, I want a structured upstream path to land
  backports for releases beyond the upstream full-support window so that I do
  not have to maintain private forks during my product's extended support
  period.
- As an **end user**, I want to know exactly which KubeVirt versions are
  supported so that I can plan upgrades within the support window.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) — release branch
  management, branch protection, documentation updates
- [kubevirt/sig-release](https://github.com/kubevirt/sig-release) — support
  matrix updates, release schedule documentation
- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) — CI
  lane lifecycle automation, Prow job configuration

## Design

### Support Window

KubeVirt will adopt a **two-tier support model**:

- **Tier 1 — Full support (3 most recent releases)**: The upstream community
  actively backports CVEs, bug fixes, and dependency updates. Full CI is
  maintained by the upstream project. This matches the
  [Kubernetes patch release support period](https://kubernetes.io/releases/patch-releases/#support-period)
  and provides approximately **12-15 months** of full support per release.
- **Tier 2 — Extended maintenance (next 2 older releases)**: The branch
  remains open for merges but does not receive proactive upstream backports.
  Downstream vendors or other interested parties may propose cherry-picks. CI
  for Tier 2 branches is maintained by the parties who need them (e.g., via
  sponsored CI lanes in `kubevirt/project-infra`). The upstream community is
  not obligated to initiate or review backports for Tier 2 branches.

With 3 minor releases per year (~every 15 weeks), Tier 1 provides ~12-15
months of full upstream support. Tier 2 extends the window by an additional
~8-10 months, giving downstream distributors a structured upstream target for
backports during their extended support windows.

This two-tier model is motivated by the structural gap between upstream
release cadence and downstream product support windows. Downstream products
built on KubeVirt typically maintain support windows of 18-24 months (or
longer for EUS releases), exceeding the ~12-15 month Tier 1 window by 9-15
months depending on the release type. Without Tier 2, downstream teams must
independently carry backports in private forks during this gap period.

| Aspect | Current Policy | Proposed Policy |
|--------|---------------|-----------------|
| Supported releases | Unbounded ("not enforced") | 3 Tier 1 + 2 Tier 2 |
| Max active branches | 8-13+ for CVEs | 5-6 (3 T1 + 2-3 T2, plus main) |
| EOL enforcement | Soft/optional | Hard cutoff after Tier 2 |
| Upstream CVE backport scope | All "willing" branches | 3 releases (Tier 1) |
| Tier 2 CVE backports | N/A | At vendor discretion |
| Full support window | Indefinite | ~12-15 months (Tier 1) |
| Extended maintenance window | N/A | ~20-25 months total (Tier 1 + Tier 2) |

### Tier Lifecycle

A release progresses through the following states:

1. **Tier 1 (Full support)**: From GA until the 3rd subsequent minor release
   reaches GA.
2. **Tier 2 (Extended maintenance)**: From Tier 1 EOL until either:
   - No downstream vendor declares an active support window for the release, or
   - The 5th subsequent minor release reaches GA (maximum Tier 2 duration of
     2 additional release cycles)
3. **Full EOL**: The branch is archived and no further changes are accepted.

| Event | Tier 1 | Tier 2 |
|-------|--------|--------|
| v1.7 GA | v1.7, v1.6, v1.5 | v1.4, v1.3 |
| v1.8 GA | v1.8, v1.7, v1.6 | v1.5, v1.4 — **v1.3 reaches full EOL** |
| v1.9 GA | v1.9, v1.8, v1.7 | v1.6, v1.5 — **v1.4 reaches full EOL** |

### Tier 1 to Tier 2 Transition Actions

When a release moves from Tier 1 to Tier 2:

1. **Support matrix update**: The release is marked as Tier 2 in the
   [sig-release support matrix](https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md).
2. **CI transition**: Upstream-funded Prow jobs are removed. Downstream
   vendors that need CI for the branch are responsible for configuring and
   funding their own lanes in `kubevirt/project-infra`.
3. **Announcement**: The Tier 2 transition is announced on the `kubevirt-dev`
   mailing list as part of the new release announcement.
4. **Backport policy change**: The branch remains open for merges, but
   cherry-pick PRs are no longer initiated by the upstream community.
   Downstream vendors may propose cherry-picks following the standard
   backporting process.

### Full EOL Actions

When a release reaches full EOL (exits Tier 2):

1. **Branch protection**: The release branch is marked read-only. No further
   PRs will be merged.
2. **CI removal**: All remaining Prow jobs (including vendor-sponsored lanes)
   for the release branch are removed from `kubevirt/project-infra`.
3. **Support matrix update**: The release is marked as EOL in the sig-release
   support matrix.
4. **Backport rejection**: Cherry-pick PRs targeting EOL branches are
   auto-closed by a bot with a standard message directing users to upgrade.
5. **Announcement**: Full EOL is announced on the `kubevirt-dev` mailing list.

### CVE Handling

CVE responses differ by tier:

- **Tier 1 branches**: The upstream community actively backports CVE fixes as
  part of the standard release process.
- **Tier 2 branches**: Downstream vendors or interested parties may propose
  CVE backports as cherry-picks. The upstream community is not obligated to
  initiate these backports, but may review and merge them following the
  standard process. CVE advisories will note that Tier 2 branches may receive
  fixes depending on vendor activity.
- **Full EOL branches**: No backports will be made, regardless of severity.
  CVE advisories will recommend upgrading from EOL releases.

### Tier 2 Branch Governance

Tier 2 branches have the following governance rules:

- **Cherry-pick proposals**: Any interested party may propose cherry-picks to
  Tier 2 branches following the standard backporting process documented in
  [docs/release-branch-backporting.md](https://github.com/kubevirt/kubevirt/blob/main/docs/release-branch-backporting.md).
- **Review responsibility**: The proposing party is responsible for finding
  reviewers. Upstream maintainers may review Tier 2 cherry-picks but are not
  obligated to do so. Downstream organizations are encouraged to designate
  reviewers from their own contributors.
- **CI requirements**: At least one CI lane must be active and passing for a
  Tier 2 branch to accept merges. The CI lane must be funded and maintained by
  the parties that need the branch. Vendor-sponsored CI lanes can be
  configured in `kubevirt/project-infra` following the existing sponsored lane
  process.
- **Branch closure**: A Tier 2 branch is closed (moved to full EOL) when
  either no downstream vendor declares an active support window or the maximum
  Tier 2 duration is reached. Vendors should declare their support windows by
  updating the sig-release support matrix or notifying SIG Release.

### Downstream Extended Support

Tier 2 provides a structured upstream path for downstream vendors to land
backports without maintaining private forks. This eliminates the gap between
upstream EOL and downstream end-of-maintenance that would exist under a
3-release-only policy.

Downstream distributors that need support beyond the Tier 2 window are free
to maintain their own forks and backport patches independently. The upstream
project's full EOL does not prevent downstream organizations from continuing
to ship patches for older releases in their own products.

### Transition Plan

To avoid disruption, the following transition plan is proposed:

1. **Announce the policy** on `kubevirt-dev@googlegroups.com`, slack channels,
   social media, website, and in the release notes for the next GA release after
   this VEP is accepted.
2. **Grace period**: Provide one full release cycle of notice. For example, if
   announced with v1.9 GA, the policy takes effect at v1.10 GA.
3. **Initial tier assignment**: At the policy effective date, all releases
   older than N-4 (where N is the current release) are marked full EOL.
   Releases N-3 and N-4 enter Tier 2. As of v1.10 GA, this would mean v1.10,
   v1.9, v1.8 are Tier 1; v1.7, v1.6 are Tier 2; and v1.5 and earlier are
   full EOL.
4. **Archive old branches**: For branches that have been de facto dead for
   years (release-0.4 through release-0.57), immediately:
   - Remove any remaining CI configuration
   - Apply branch protection rules to prevent merges
   - No need to delete branches; they serve as historical record

### Documentation Updates

The following documentation changes are required:

- **`docs/release.md`**: Replace the soft EOL language with the hard policy
  defined in this VEP. Remove the sentence *"The EOL of a KubeVirt release is
  currently not enforced."*
- **`docs/release-branch-backporting.md`**: Add a section clarifying that
  backports are only accepted for releases within the Tier 1 or Tier 2
  support window, and document the Tier 2 cherry-pick process.
- **sig-release support matrix**: Ensure releases are clearly marked with
  their current tier (Tier 1, Tier 2, or EOL) and the support window policy
  is documented.

## API Examples

N/A — this VEP is a process and policy change, not an API change.

## Alternatives

### Alternative 1: Support 4 most recent releases

Supporting 4 releases instead of 3 would provide a longer ~16-20 month window.
This was considered but rejected because:

- It still represents a significant improvement over the current unbounded
  state
- It would maintain 4 active branches instead of 3, increasing maintenance
  burden by 33%
- The 3-release model directly mirrors Kubernetes and is well-understood by the
  community
- Downstream distributors who need longer windows can maintain their own
  support independently

### Alternative 2: Time-based EOL (e.g., 12 months from GA)

A fixed calendar-based EOL (e.g., 12 months from GA) was considered. This was
rejected because:

- It decouples from the Kubernetes lifecycle, which is the stated basis for
  KubeVirt's support model
- Release schedule variability (holidays, delays) could create confusing EOL
  dates
- The 3-release model is simpler to understand and communicate

### Alternative 3: Maintain the status quo with better tooling

Improving automation to reduce the per-branch cost of backports was considered
as an alternative to reducing the number of branches. This was rejected
because:

- Automation cannot eliminate the review burden — humans must still review each
  cherry-pick for correctness
- CI cost scales linearly with branch count regardless of automation
- Old branches accumulate infrastructure incompatibilities that no amount of
  tooling can prevent
- The fundamental problem is policy, not tooling

### Alternative 4: 3-release-only with no extended maintenance

Under this simpler model, only the **3 most recent minor releases** receive
any upstream support. When a release reaches EOL (the 3rd subsequent release
reaches GA), the branch is immediately archived — marked read-only, CI
removed, cherry-picks auto-closed. Downstream distributors that need longer
support windows must maintain their own forks and backport patches
independently.

This was the original primary proposal for this VEP. It was not adopted
because:

- The gap between upstream EOL and downstream end-of-maintenance is
  structural, not incidental. With downstream support windows of 18-24 months
  (or longer for EUS releases) and an upstream Tier 1 window of ~12-15
  months, downstream teams face a **9-15 month gap** where they must
  independently carry backports in private forks with no upstream branch to
  target
- This gap repeats for every release going forward and is a direct
  consequence of the mismatch between upstream release cadence and downstream
  product lifecycle
- Forcing all downstream maintenance into private forks reduces transparency
  and prevents the broader community from benefiting from vendor-contributed
  backports
- The two-tier model addresses these concerns while keeping the upstream
  community's obligation bounded to the same 3 releases

The 3-release-only model remains viable if the community determines that the
complexity of Tier 2 branch governance outweighs the benefits of structured
extended maintenance.

### Alternative 5: LTS releases

Designating certain releases as Long-Term Support (LTS) with an extended
window (e.g., 2 years) was considered. This was deferred as a potential future
VEP because:

- It adds complexity to the release process
- It requires dedicated maintainer commitment for LTS branches
- The immediate problem is the lack of *any* enforced EOL, not the need for
  longer support on specific releases
- An LTS model could be layered on top of this policy in a future VEP if
  demand exists

## Scalability

This VEP improves scalability by bounding the number of active release
branches. The maintenance and CI cost of release branches scales linearly with
the number of branches, so reducing from 8-13+ active branches to 5-6 (3
Tier 1 + 2-3 Tier 2) represents a proportional reduction in resource
consumption. The upstream community's active maintenance obligation is further
bounded to 3 Tier 1 branches, with Tier 2 CI costs shifted to the downstream
vendors that need them.

## Update/Rollback Compatibility

This VEP does not affect KubeVirt update or rollback mechanisms. The existing
update compatibility guarantees (N-1 to N upgrades) are unchanged.

Users on EOL releases will need to perform a rolling upgrade through supported
versions to reach a current release. This is already the expected upgrade path
and is documented in [docs/updates.md](https://github.com/kubevirt/kubevirt/blob/main/docs/updates.md).

## Functional Testing Approach

No functional testing changes are required for the policy itself. The
enforcement mechanisms should be validated as follows:

- **Tier 2 transition**: Verify that upstream CI lanes are removed and
  vendor-sponsored lanes can be configured for Tier 2 branches
- **Tier 2 merges**: Verify that cherry-pick PRs can be merged to Tier 2
  branches when vendor-sponsored CI is passing
- **Branch protection**: Verify that PRs to full-EOL branches cannot be
  merged
- **CI removal**: Verify that no Prow jobs are configured for full-EOL
  branches
- **Bot behavior**: Verify that cherry-pick PRs to full-EOL branches receive
  the standard auto-close message
- **Support matrix**: Verify that the sig-release matrix accurately reflects
  Tier 1, Tier 2, and EOL status after each new GA release

## Implementation History

<!--
To be filled in as the VEP progresses.
-->

## Graduation Requirements

This is a process VEP and does not follow the standard Alpha/Beta/GA feature
graduation model. Instead, the following milestones apply:

### Phase 1: Policy Adoption

- [ ] VEP accepted by SIG Release and community vote
- [ ] Documentation updated in `kubevirt/kubevirt` (`docs/release.md`,
      `docs/release-branch-backporting.md`)
- [ ] Policy announced on `kubevirt-dev` mailing list
- [ ] Grace period begins (one release cycle)

### Phase 2: Enforcement

- [ ] Branch protection applied to all pre-existing full-EOL branches
- [ ] CI lanes removed for full-EOL branches in `kubevirt/project-infra`
- [ ] Auto-close bot configured for cherry-pick PRs targeting full-EOL
      branches
- [ ] sig-release support matrix updated with Tier 1, Tier 2, and EOL status
- [ ] Vendor-sponsored CI lane process documented for Tier 2 branches

### Phase 3: Steady State

- [ ] Tier lifecycle automated as part of the release procedure
- [ ] Each new GA release automatically triggers Tier 1 → Tier 2 transition
      for the N-3 release
- [ ] Each new GA release evaluates full EOL for Tier 2 branches with no
      active vendor support window
- [ ] Tier 2 branch governance documented in release procedure
- [ ] Vendor CI sponsorship process documented and operational
