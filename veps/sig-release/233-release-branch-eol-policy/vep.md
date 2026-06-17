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

Additionally, the same core maintainers who develop features on `main` are
responsible for reviewing backports across all release branches. This creates
reviewer fatigue and concentrates the maintenance burden on a small group of
individuals — a problem that grows with each new release regardless of how many
old branches are archived.

This VEP proposes a **dedicated release branch governance model** where all
release branches, from the point they are cut, are owned by maintainer groups
separate from the core `main` branch maintainers. Release branches remain
active as long as their dedicated maintainers opt in to keep them alive, and
transition to EOL when no maintainer is willing to continue. This is modeled
on [OpenStack's stable branch policy](https://docs.openstack.org/project-team-guide/stable-branches.html),
which has operated at this scale (15+ project teams, dozens of repos) for over
a decade.

## Motivation

As of March 2026, the `kubevirt/kubevirt` repository has **59 release
branches** spanning from `release-0.4` to `release-1.8`. While the
[sig-release support matrix](https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md)
only formally tracks v1.2 through v1.7 (with v1.2-v1.4 already marked EOL),
in practice backports continue to land on branches well beyond the documented
support window.

### CVE Backport Burden

Recent CVE responses illustrate how the lack of an enforced EOL compounds
the maintenance burden:

#### CVE-2023-39325 / CVE-2023-44487 — HTTP/2 Rapid Reset (Oct 2023, High)

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
Under a maintainer-driven model, responsibility for these backports would
have been distributed across dedicated release branch maintainers rather than
falling on core `main` branch reviewers. Notably, `release-0.49` targeted
Kubernetes 1.24, which had already been EOL for months when the fix landed —
a branch that would likely have reached EOL earlier if maintainer opt-in were
required to keep it alive.

#### CVE-2024-21626 — runc Container Escape (Jan 2024, Critical)

This critical container escape vulnerability required backports to
**4 branches**: `release-0.58`, `release-0.59`, `release-1.0`, and
`release-1.1`
([#13253](https://github.com/kubevirt/kubevirt/pull/13253),
[#13254](https://github.com/kubevirt/kubevirt/pull/13254),
[#13255](https://github.com/kubevirt/kubevirt/pull/13255),
[#13256](https://github.com/kubevirt/kubevirt/pull/13256)).
`release-0.58` targeted Kubernetes 1.26, which was well past its own EOL when
the fix was merged.

#### CVE-2025-22869 — golang.org/x/crypto SSH Handshake Panic (2025, High)

A panic-triggering vulnerability in the Go SSH handshake was backported to
`release-1.3` and `release-1.4`
([#14633](https://github.com/kubevirt/kubevirt/pull/14633),
[#14629](https://github.com/kubevirt/kubevirt/pull/14629)),
both of which are already marked EOL in the sig-release support matrix.

#### CVE-2025-47913 — golang.org/x/crypto Denial of Service (2025, High)

A denial-of-service vulnerability in Go's crypto library was backported to
`release-1.5` and `release-1.6`
([#16887](https://github.com/kubevirt/kubevirt/pull/16887),
[#16870](https://github.com/kubevirt/kubevirt/pull/16870)).

#### CVE Backport Summary

| CVE | Date | Severity | Branches Backported |
|-----|------|----------|--------------------:|
| CVE-2023-39325 | Oct 2023 | High | 7 |
| CVE-2024-21626 | Jan 2024 | Critical | 4 |
| CVE-2025-22869 | 2025 | High | 2 |
| CVE-2025-47913 | 2025 | High | 2 |

Under this VEP's governance model, the review and CI burden for these
backports would be carried by dedicated release branch maintainers rather
than core `main` branch reviewers. Branches without active maintainers
would have already transitioned to EOL, naturally reducing the scope.

### Reviewer Burden

The unbounded branch count is only part of the problem. The same core
maintainers who review feature work on `main` also review every backport
cherry-pick across all release branches. This creates compounding reviewer
fatigue:

- Approximately **half of all upstream PRs are backports** — from various
  contributors and downstream vendors — creating significant reviewer and CI
  load
- Individual core maintainers report spending **up to half of their review
  time** on backports rather than feature development
- Backports are often a source of long-running fixes where old branches are
  discovered to be non-functional
- Fixes for customer problems are sometimes raised upstream but never
  backported, with missing bug tracking and no follow-up
- Backports are sometimes raised for CVEs that don't actually affect upstream
- Edge cases with forked dependencies (e.g.
  [kubevirt#17692](https://github.com/kubevirt/kubevirt/pull/17692)) require
  time-consuming manual verification by maintainers

### Summary

The pattern is clear: without separating release branch maintenance from core
development and without a mechanism to EOL branches that lack active
maintainers, the project faces:

1. **Unsustainable contributor burden**: Each CVE or critical fix must be
   backported, reviewed, and merged across many branches. The HTTP/2 rapid
   reset fix alone required 7 separate cherry-pick PRs, each needing review
   and CI validation.
2. **Unbounded CI cost**: CI lanes must be maintained and executed for every
   active release branch. Old branches frequently break due to infrastructure
   drift, consuming debugging time unrelated to the fix itself.
3. **Reviewer fatigue**: Core maintainers context-switch between feature
   reviews on `main` and backport reviews across release branches, with
   backports consuming up to half their review bandwidth.
4. **False sense of security**: Users on ancient releases receive sporadic CVE
   fixes but no guarantee of comprehensive coverage, creating a false sense
   that the release is fully maintained.
5. **No upgrade incentive**: Without a hard EOL, downstream consumers have no
   forcing function to upgrade to supported releases.
6. **Inconsistency with stated policy**: The documentation already ties EOL to
   Kubernetes support periods, but the lack of enforcement makes the policy
   meaningless in practice. Fixes are landing on branches targeting Kubernetes
   versions that Kubernetes itself stopped supporting long ago.

## Goals

- Separate release branch maintenance from core `main` branch development by
  establishing dedicated release branch maintainer groups
- Define a maintainer-driven EOL policy where branches transition to EOL when
  no dedicated maintainer opts in to keep them alive
- Establish automated enforcement of EOL through CI and branch protection
- Provide a clear transition plan for the existing backlog of unmaintained
  branches
- Create a contributor onboarding path through release branch review toward
  full SIG approver status on `main`

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
- **Release branch maintainers**: Dedicated reviewers/approvers responsible for
  backport review and CI health on release branches, separate from core `main`
  branch maintainers
- **KubeVirt contributors**: Developers who must create and shepherd cherry-pick
  PRs for bug fixes and CVEs
- **Downstream distributors**: Organizations that consume KubeVirt releases
  and may maintain their own extended support windows independently
- **End users**: Operators running KubeVirt clusters who need to understand
  which versions receive security and bug fixes

## User Stories

- As a **release maintainer**, I want release branches to have dedicated
  maintainers and a clear EOL process so that unmaintained branches are
  retired and do not accumulate indefinitely.
- As a **core maintainer**, I want release branch review to be handled by a
  dedicated group so that I can focus my review time on feature development on
  `main`.
- As a **release branch maintainer**, I want clear ownership of specific
  release branches so that I know what I am responsible for and can build
  expertise in the backporting process.
- As a **new contributor**, I want a structured path into the project where I
  can start with lower-risk backport reviews and work toward full SIG approver
  status on `main`.
- As a **contributor**, I want a clear policy on which branches accept
  backports so that I do not waste time preparing cherry-picks for EOL
  releases.
- As a **downstream distributor**, I want release branches to remain active
  upstream as long as I am willing to provide maintainers, so that I do not
  need to maintain private forks unnecessarily.
- As an **end user**, I want to know exactly which KubeVirt versions have
  active maintainers so that I can plan upgrades accordingly.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) — release branch
  management, branch protection, OWNERS/OWNERS_ALIASES updates, documentation
  updates
- [kubevirt/sig-release](https://github.com/kubevirt/sig-release) — support
  matrix updates, release schedule documentation
- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) — CI
  lane lifecycle automation, Prow job configuration

## Design

### Branch Lifecycle

A release branch progresses through three phases:

| Phase | Ownership | CI | Upstream Obligation |
|-------|-----------|-----|---------------------|
| **Active** | Dedicated release branch maintainers | Full Prow presubmit + periodic | Maintainers actively review backports and monitor CI |
| **Maintainer opt-in review** | Same, subject to re-confirmation | Maintained by opt-in party | Branch stays alive only if a maintainer explicitly opts in |
| **End of Life** | None | Removed | Branch archived, no further merges |

| Aspect | Current Policy | Proposed Policy |
|--------|---------------|-----------------|
| Supported releases | Unbounded ("not enforced") | Maintainer-driven: active as long as someone opts in |
| EOL enforcement | Soft/optional | Hard: absence of maintainers = automatic EOL |
| Release branch ownership | Core maintainers (same as main) | Dedicated release branch maintainers |
| CVE backport scope | All "willing" branches | Active branches with opted-in maintainers |

### EOL Trigger

A release branch transitions to EOL when **no dedicated maintainer opts in to
keep it alive**. This is evaluated through an explicit opt-in process modeled
on OpenStack's approach:

1. An **EOL proposal PR** is opened against `kubevirt/sig-release` (or
   `kubevirt/enhancements`) proposing the branch for EOL.
2. The PR remains open for a **minimum of one month** to give maintainers,
   downstream vendors, and other interested parties time to respond.
3. A release branch maintainer or SIG liaison may **object** (by commenting or
   requesting changes) to keep the branch alive. Objecting requires a
   commitment to continue maintaining the branch — including CI health and
   backport review.
4. If no one objects within the window, the branch transitions to EOL.

This opt-in model ensures that branches are never archived while someone is
actively willing to maintain them, while also providing a natural pressure
valve against unbounded branch accumulation — branches without maintainers
are automatically retired.

### EOL Actions

When a release branch transitions to EOL:

1. **Branch protection**: The release branch is marked read-only. No further
   PRs will be merged.
2. **CI removal**: All Prow presubmit and periodic jobs for the release branch
   are removed from `kubevirt/project-infra`.
3. **Support matrix update**: The release is marked as EOL in the
   [sig-release support matrix](https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md).
4. **Backport rejection**: Cherry-pick PRs targeting EOL branches are
   auto-closed by a bot with a standard message directing users to upgrade.
5. **Announcement**: EOL is announced on the `kubevirt-dev` mailing list.

### CVE Handling for EOL Releases

CVE reports against EOL releases will be handled as follows:

- If the CVE affects an active release, it will be fixed in active releases
  only.
- The CVE advisory will note which releases contain the fix and recommend
  upgrading from EOL releases.
- No backports to EOL branches will be made, regardless of severity.

This is consistent with how Kubernetes and most major open-source projects
handle CVEs in EOL releases.

### Release Branch Governance

This VEP introduces a governance model for release branches that separates
backport review from core `main` branch development. The model is based on
[OpenStack's stable branch policy](https://docs.openstack.org/project-team-guide/stable-branches.html),
where stable branch maintenance is explicitly separated from feature
development from the moment a branch is cut.

All release branches are owned by dedicated maintainer groups from the moment
they are cut. Core `main` branch maintainers focus on feature development and
review changes on `main`; the release branch maintainer groups review backports
on every release branch. This addresses the full scope of the reviewer burden
— the most recent release branches are typically the busiest for backports, so
offloading only old branches would leave the core of the problem untouched.

Release branch maintainer group membership does not require being a core
`main` branch approver — it is a separate group managed independently.
However, the release branch maintainer groups operate under the oversight of
the project's core approvers and formal maintainers, who are responsible for
defining the backporting policy, mentoring release branch maintainers, and
providing technical guidance on complex or uncertain backports. This ensures
that release branch quality remains aligned with the project's standards even
though the day-to-day review work is handled by a different group.

This structure also provides a natural onboarding path for new contributors:
reviewing backports (lower-risk changes with clear correctness criteria) under
the mentorship of core maintainers builds codebase familiarity and review
experience, making release branch maintainers strong candidates for future
SIG approver status on `main`.

When a release branch is cut, the OWNERS and OWNERS_ALIASES files on that
branch are updated as part of the release procedure to grant the dedicated
maintainer group `/approve` and `/lgtm` rights. The exact team structure and
review requirements are implementation details to be determined during the
graduation process.

### Transition Plan

To avoid disruption, the following transition plan is proposed:

1. **Announce the policy** on `kubevirt-dev@googlegroups.com`, slack channels,
   social media, website, and in the release notes for the next GA release after
   this VEP is accepted.
2. **Grace period**: Provide one full release cycle of notice. For example, if
   announced with v1.9 GA, the policy takes effect at v1.10 GA.
3. **Establish release branch maintainer groups**: During the grace period,
   populate OWNERS_ALIASES with initial release branch maintainer groups and
   onboard members.
4. **Archive dead branches**: For branches that have been de facto dead for
   years (release-0.4 through release-0.57), immediately:
   - Remove any remaining CI configuration
   - Apply branch protection rules to prevent merges
   - No need to delete branches; they serve as historical record
5. **Opt-in review for remaining old branches**: Open EOL proposal PRs for
   all branches that lack active maintainers. Any branch where no maintainer
   opts in within the one-month window transitions to EOL.
6. **First governed branch cut**: The first release branch cut after policy
   adoption uses the new OWNERS model, with the dedicated maintainer group
   receiving `/approve` and `/lgtm` rights from the point of cut.

### Documentation Updates

The following documentation changes are required:

- **`docs/release.md`**: Replace the soft EOL language with the maintainer-
  driven EOL policy defined in this VEP. Remove the sentence *"The EOL of a
  KubeVirt release is currently not enforced."* Add documentation on release
  branch maintainer groups, the OWNERS transition at branch cut, and the
  opt-in EOL process.
- **`docs/release-branch-backporting.md`**: Add a section clarifying that
  backports are only accepted for releases with active maintainers. Document
  the review requirements for release branch maintainers.
- **sig-release support matrix**: Ensure EOL releases are clearly marked and
  the support policy is documented.
- **OWNERS_ALIASES**: Add `release-branch-maintainers` alias with membership
  lists.

## API Examples

N/A — this VEP is a process and policy change, not an API change.

## Alternatives

### Alternative 1: Hard N-release EOL cutoff (e.g., 3 or 4 releases)

A fixed release-count-based EOL (e.g., "only the 3 most recent releases are
supported") was considered. This was rejected because:

- It imposes an arbitrary cutoff that does not account for whether someone is
  willing and able to maintain the branch
- It forces downstream distributors with longer support windows into private
  forks even when the upstream branch is still viable
- The maintainer-driven opt-in model achieves the same goal of bounding branch
  count — branches without maintainers reach EOL naturally — without the
  rigidity of a fixed number

### Alternative 2: Time-based EOL (e.g., 12 months from GA)

A fixed calendar-based EOL was considered. This was rejected for similar
reasons to the N-release cutoff:

- It decouples from whether anyone is actually maintaining the branch
- Release schedule variability could create confusing EOL dates
- The opt-in model is more flexible while still providing a natural pressure
  valve against unbounded branch accumulation

### Alternative 3: Maintain the status quo with better tooling

Improving automation to reduce the per-branch cost of backports was considered
as an alternative to changing governance. This was rejected because:

- Automation cannot eliminate the review burden — humans must still review each
  cherry-pick for correctness
- CI cost scales linearly with branch count regardless of automation
- Old branches accumulate infrastructure incompatibilities that no amount of
  tooling can prevent
- The fundamental problem is governance, not tooling

### Alternative 4: EOL policy without dedicated maintainer groups

An EOL-only policy (with or without a fixed release cutoff) while retaining
the current model where core `main` branch maintainers review all release
branch backports. This was rejected because:

- It does not address reviewer fatigue — the same small group still reviews
  all backports on all active branches
- The most recent release branches are the busiest for backports, so archiving
  old branches alone does not meaningfully reduce the core maintainer burden
- It leaves the question of review responsibility for release branches
  ambiguous, which was a recurring concern in downstream discussions

### Alternative 5: Open-ended best-effort branches

Under this model, older branches would remain open for merges on a best-effort,
no-obligation basis. This was rejected because:

- It is functionally close to the current situation
- Without CI, review obligations, or structured process, "open for merges"
  provides little practical value — cherry-picks with no CI cannot be
  validated, and merges without review risk regressions
- It creates a false sense of support
- Review responsibility is ambiguous

### Alternative 6: Vendor-maintained public forks (midstream model)

An OpenShift-style approach where vendors maintain public forks (e.g.,
`openshift/kubevirt`) for extended branches was considered. This avoids
governance changes in the upstream repo but was not adopted because:

- It requires standing up parallel CI infrastructure in the fork
- It introduces duplication between upstream and fork for branches that are
  still active in both places
- Downstream tooling friction was a significant concern raised in discussions
- The dedicated maintainer group model achieves the same separation of
  responsibility while keeping everything upstream

### Alternative 7: LTS releases

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

This VEP improves scalability by distributing the maintenance workload across
dedicated teams and introducing a natural mechanism to retire branches that
lack active maintainers. Separating release branch review from core `main`
branch development allows the two activities to scale independently. The
opt-in EOL model provides a pressure valve: branches without maintainers are
retired automatically, preventing unbounded branch accumulation without
imposing an arbitrary cap.

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
- **OWNERS**: Verify that release branch OWNERS files correctly reference the
  dedicated maintainer group aliases after branch cut

## Implementation History

<!--
To be filled in as the VEP progresses.
-->

## Graduation Requirements

This is a process VEP and does not follow the standard Alpha/Beta/GA feature
graduation model. Instead, the following milestones apply:

### Phase 1: Policy Adoption and Governance Setup

- [ ] VEP accepted by SIG Release and community vote
- [ ] Release branch maintainer groups established and populated
- [ ] `release-branch-maintainers` alias added to OWNERS_ALIASES
- [ ] Documentation updated in `kubevirt/kubevirt` (`docs/release.md`,
      `docs/release-branch-backporting.md`)
- [ ] Policy announced on `kubevirt-dev` mailing list
- [ ] Grace period begins (one release cycle)

### Phase 2: Enforcement

- [ ] Dead branches (release-0.4 through release-0.57) archived
- [ ] EOL proposal PRs opened for remaining old branches without maintainers
- [ ] Branch protection applied to all branches that complete EOL
- [ ] CI lanes removed for EOL branches in `kubevirt/project-infra`
- [ ] Auto-close bot configured for cherry-pick PRs targeting EOL branches
- [ ] sig-release support matrix updated
- [ ] First release branch cut under the new OWNERS model completed

### Phase 3: Steady State

- [ ] EOL opt-in review process operational for each release cycle
- [ ] OWNERS transition at branch cut automated as part of the release
      procedure
- [ ] Release branch maintainer onboarding process documented and operational
