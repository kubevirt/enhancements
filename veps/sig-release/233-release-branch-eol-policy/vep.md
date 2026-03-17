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

This VEP proposes adopting a hard EOL policy modeled on the
[Kubernetes patch release support period](https://kubernetes.io/releases/patch-releases/#support-period),
limiting active maintenance to the **3 most recent minor releases**.

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
Under a 3-release policy, only 3 branches (1.0, 1.1, 1.2) would have been
in scope. Notably, `release-0.49` targeted Kubernetes 1.24, which had already
been EOL for months when the fix landed.

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

| CVE | Date | Severity | Branches Backported | Under 3-Release Policy |
|-----|------|----------|--------------------:|---------------------:|
| CVE-2023-39325 | Oct 2023 | High | 7 | 3 |
| CVE-2024-21626 | Jan 2024 | Critical | 4 | 3 |
| CVE-2025-22869 | 2025 | High | 2 | 2 |
| CVE-2025-47913 | 2025 | High | 2 | 2 |

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

KubeVirt will support the **3 most recent minor releases**. With 3 minor
releases per year (~every 15 weeks), this provides a support window of
approximately **12-15 months** per release — closely matching the Kubernetes
support period of ~14 months.

| Aspect | Current Policy | Proposed Policy |
|--------|---------------|-----------------|
| Supported releases | Unbounded ("not enforced") | 3 most recent minor releases |
| Max active branches | 8-13+ for CVEs | 3 (plus main) |
| EOL enforcement | Soft/optional | Hard cutoff |
| CVE backport scope | All "willing" branches | 3 releases |
| Support window | Indefinite | ~12-15 months |

### EOL Trigger

A release reaches EOL when the **third subsequent minor release reaches GA**.
For example:

| Event | Supported Releases |
|-------|-------------------|
| v1.7 GA | v1.7, v1.6, v1.5 |
| v1.8 GA | v1.8, v1.7, v1.6 — **v1.5 reaches EOL** |
| v1.9 GA | v1.9, v1.8, v1.7 — **v1.6 reaches EOL** |

### EOL Actions

When a release reaches EOL, the following actions are taken:

1. **Branch protection**: The release branch is marked read-only. No further
   PRs will be merged.
2. **CI removal**: All Prow presubmit and periodic jobs for the release branch
   are removed from `kubevirt/project-infra`.
3. **Support matrix update**: The release is marked as EOL in the
   [sig-release support matrix](https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md).
4. **Backport rejection**: Cherry-pick PRs targeting EOL branches are
   auto-closed by a bot with a standard message directing users to upgrade.
5. **Announcement**: EOL is announced on the `kubevirt-dev` mailing list as
   part of the new release announcement.

### CVE Handling for EOL Releases

CVE reports against EOL releases will be handled as follows:

- If the CVE affects a supported release, it will be fixed in supported
  releases only.
- The CVE advisory will note which releases contain the fix and recommend
  upgrading from EOL releases.
- No backports to EOL branches will be made, regardless of severity.

This is consistent with how Kubernetes and most major open-source projects
handle CVEs in EOL releases.

### Downstream Extended Support

Downstream distributors who need longer support windows are free to maintain
their own forks and backport patches independently. The upstream project's EOL
does not prevent downstream organizations from continuing to ship patches for
older releases in their own products.

### Transition Plan

To avoid disruption, the following transition plan is proposed:

1. **Announce the policy** on `kubevirt-dev@googlegroups.com`, slack channels,
   social media, website, and in the release notes for the next GA release after
   this VEP is accepted.
2. **Grace period**: Provide one full release cycle of notice. For example, if
   announced with v1.9 GA, the policy takes effect at v1.10 GA.
3. **Initial EOL batch**: At the policy effective date, all releases older than
   N-2 (where N is the current release) are marked EOL simultaneously. As of
   v1.10 GA, this would mean v1.7 and earlier are EOL.
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
  backports are only accepted for releases within the support window.
- **sig-release support matrix**: Ensure EOL releases are clearly marked and
  the support window policy is documented.

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

### Alternative 4: Two-tier support with downstream-aligned extended maintenance

Downstream products built on KubeVirt may have maintenance support windows
(~18 months or more) that exceed the proposed 3-release upstream window (~12-15
months). The gap between upstream EOL and downstream end-of-maintenance is
typically 3-6 months, during which the downstream team must independently carry
CVE fixes and backports without an upstream branch to target.

A two-tier model could address this:

- **Tier 1 — Full support (3 most recent releases)**: The upstream community
  actively backports CVEs, bug fixes, and dependency updates. Full CI is
  maintained. This is the upstream community commitment and matches the
  Kubernetes model.
- **Tier 2 — Extended maintenance (next 2 releases)**: The branch remains open
  for merges but does not receive proactive upstream backports. Downstream
  teams or other interested parties may propose cherry-picks, and CI for these
  branches is maintained by the parties who need them (e.g., via sponsored CI
  lanes in `kubevirt/project-infra`). The upstream community is not obligated
  to initiate or review backports for Tier 2 branches.

This gives downstream distributors a place to land backports upstream rather
than carrying them in a private fork, while keeping the upstream community's
commitment bounded to 3 releases.

This alternative was not adopted as the primary proposal because:

- It increases the maximum number of active branches from 3 to 5, adding
  complexity even if the obligation for the extra branches is shifted
  downstream
- It introduces ambiguity about review responsibilities — who approves
  cherry-picks to Tier 2 branches if the upstream community is not obligated?
- CI cost for Tier 2 branches must be explicitly funded and maintained by
  downstream consumers, which requires coordination outside the upstream
  project
- The 3-release model is simpler to adopt as a first step; a Tier 2 extension
  could be added later if downstream demand justifies it

However, if the community determines that the downstream maintenance gap is
too large, this two-tier model is the recommended compromise over simply
extending full support to 5 releases, as it preserves the bounded upstream
commitment while providing a structured path for downstream needs.

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
the number of branches, so reducing from 8-13+ active branches to 3 represents
a proportional reduction in resource consumption.

## Update/Rollback Compatibility

This VEP does not affect KubeVirt update or rollback mechanisms. The existing
update compatibility guarantees (N-1 to N upgrades) are unchanged.

Users on EOL releases will need to perform a rolling upgrade through supported
versions to reach a current release. This is already the expected upgrade path
and is documented in [docs/updates.md](https://github.com/kubevirt/kubevirt/blob/main/docs/updates.md).

## Functional Testing Approach

No functional testing changes are required for the policy itself. The
enforcement mechanisms should be validated as follows:

- **Branch protection**: Verify that PRs to EOL branches cannot be merged
- **CI removal**: Verify that no Prow jobs are configured for EOL branches
- **Bot behavior**: Verify that cherry-pick PRs to EOL branches receive the
  standard auto-close message
- **Support matrix**: Verify that the sig-release matrix accurately reflects
  EOL status after each new GA release

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

- [ ] Branch protection applied to all pre-existing EOL branches
- [ ] CI lanes removed for EOL branches in `kubevirt/project-infra`
- [ ] Auto-close bot configured for cherry-pick PRs targeting EOL branches
- [ ] sig-release support matrix updated

### Phase 3: Steady State

- [ ] EOL enforcement automated as part of the release procedure
- [ ] Each new GA release automatically triggers EOL actions for the N-3
      release
