# VEP #172: Selective Code Freeze Enforcement via Tide Configuration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in
  [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP codifies a selective code freeze for `kubevirt/kubevirt`: during the freeze, Tide requires a
specific set of labels for merging PRs into `main`. Eligible work includes tracked enhancements,
critical-urgent bug fixes, and critical-urgent flake fixes. The implemented gate targets `main`; this VEP
also proposes temporarily excluding the next expected release branch from ordinary merging until thaw.
Older release branches remain unaffected.

The core mechanism has already been implemented in `kubevirt/project-infra`. This draft describes the
[merged configuration as of October 27, 2026][tide-config], reconciles the proposal with that implementation,
and identifies remaining process decisions. Merged configuration is not independent confirmation of the
configuration currently loaded in the Prow cluster.

## Motivation

Before technical enforcement, code freeze relied on reviewers and approvers policing release eligibility.
That was error-prone under release pressure and difficult for first-time or occasional contributors to
discover. A documented Tide policy makes the process explicit and consistently enforces merge eligibility.

The freeze concentrates CI and review bandwidth on tracked release work and release stabilization without
blocking critical fixes. Keeping `main` selective until release branching can also reduce the need to
review and test the same change on both `main` and a release branch during that interval.

## Goals

- Document and enforce the implemented label-based eligibility rules for `main` during code freeze.
- Provide an auditable config-PR mechanism for freeze activation and code thaw.
- Protect the next expected release branch through the branching-to-thaw interval without freezing older releases.
- Preserve dedicated paths for tracked enhancements and critical bug/flake fixes.
- Explain release timing, label recovery, exception requests, and release-team responsibilities.
- Distinguish implemented behavior from process improvements that still require agreement.

## Non Goals

- Changing VEP proposal, review, approval, or enhancement-freeze deadlines.
- Replacing the existing [exception-request process][exceptions].
- Freezing older release branches or other repositories, or retaining the new release branch's exclusion after thaw.
- Changing label automation or introducing milestone-based gating as part of the baseline implementation.
- Stopping PR review or presubmit testing for work that cannot merge during the freeze.
- Replacing normal CI, review, DCO, or blocking-label requirements.

## Definition of Users

- **Release team**: Publishes the schedule, coordinates exception decisions with maintainers and SIGs,
  submits freeze/thaw config PRs, and verifies the deployed behavior.
- **VEP owners / contributors**: Reference tracked enhancement issues, monitor labeling, and request
  extensions when required by the enhancement process. A matching label does not itself grant an extension.
- **General contributors**: Can continue development and review, but need an eligible route to merge
  into frozen `main`; ordinary work waits for thaw.
- **Reviewers and approvers**: Assess correctness and release suitability, including whether critical
  classifications are justified. Tide checks labels, not the rationale for applying them.
- **Restricted-label team**: Members of `restricted-labels-kubevirt` can use Prow's manual
  `/label approved-vep` command to recover a missing label on genuinely tracked enhancement work.

## User Stories

- As a **release team member**, I want a small, reviewable config change to activate or lift the freeze.
- As a **VEP owner**, I want eligible enhancement work to remain mergeable while understanding that
  technical eligibility does not replace release-process approval or an extension.
- As a **contributor**, I want to understand why my PR is blocked and how to request an exception or
  correct a missing label.
- As a **maintainer**, I want critical bug and flake fixes to merge without mislabeling them as VEP work.
- As a **reviewer**, I want a clear distinction between merge eligibility and merge priority.

## Repos

- [kubevirt/project-infra](https://github.com/kubevirt/project-infra): Implemented Tide queries and
  freeze/thaw switch; follow-up configuration changes only if additional policy is agreed.
- [kubevirt/sig-release](https://github.com/kubevirt/sig-release): Release schedules and follow-up tracker
  updates for the checklist described below; proposed release-tool integration for the freeze configuration.
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements): This VEP and existing enhancement policy;
  follow-up alignment where current exception/deadline wording needs clarification.
- [kubevirt/community](https://github.com/kubevirt/community): Follow-up contributor-guide documentation.
- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt): Existing approved-VEP labeling automation;
  changes only if a separate automation or milestone proposal is adopted.

## Design

### Implemented Merge Eligibility

Tide queries are alternative admission routes: labels within a query are required together, but a PR
does not need to satisfy every query. During freeze, the ordinary `kubevirt/kubevirt` query excludes `main`.
The [dedicated queries][tide-config] permit the following:

| Target                            | Required labels                                              | Purpose                                    |
|-----------------------------------|--------------------------------------------------------------|--------------------------------------------|
| `main`                            | `lgtm`, `approved`, `approved-vep`                           | Tracked enhancement work                   |
| `main`                            | `lgtm`, `approved`, `kind/bug`, `priority/critical-urgent`   | Critical bug fixes                         |
| `main`                            | `lgtm`, `approved`, `kind/flake`, `priority/critical-urgent` | Critical flake fixes                       |
| Any branch except `main`          | `lgtm`, `approved`                                           | Ordinary merging outside the frozen branch |
| Any branch, author `kubevirt-bot` | `skip-review`                                                | Existing trusted automation route          |

The first four routes exclude PRs with any of these labels:

```text
do-not-merge
do-not-merge/hold
do-not-merge/invalid-owners-file
do-not-merge/work-in-progress
do-not-merge/release-note-label-needed
do-not-merge/invalid-commit-message
needs-rebase
dco-signoff: no
```

The bot route has the same exclusions except `do-not-merge/release-note-label-needed`. It does not require
`lgtm` or `approved` in its query. `skip-review` alone is not a human-authored PR exemption: the author
restriction is essential. Required CI checks still apply to eligible PRs.

Neither `kind/bug`, `kind/flake`, nor `priority/critical-urgent` alone grants freeze admission. Other
repositories and release branches retain their own merge requirements in this implementation snapshot.
The proposed next-release-branch exclusion below changes only the upcoming release branch's eligibility
during freeze; it was not implemented by the release-branch presubmit PR.

### Freeze Activation and Code Thaw

The dedicated queries remain configured across release cycles. Activation does not require extracting
the repository from a shared query again. The release team submits a PR enabling the following exclusion
on the ordinary query, as in [project-infra#5529][freeze-110]:

```yaml
# Fragment of the ordinary kubevirt/kubevirt query; other fields remain unchanged.
repos:
  - kubevirt/kubevirt
excludedBranches:
  - main
labels:
  - lgtm
  - approved
```

For thaw, remove or comment out `excludedBranches` on this query, allowing the ordinary route to match
`main` again. Keep the dedicated enhancement and critical-fix queries. The [v1.9 thaw][thaw-19] used this
approach; the [v1.10 thaw PR][thaw-110] is still unmerged as of this draft's snapshot.

### Proposed Next-Release-Branch Protection

In the [release-1.10 presubmit configuration PR #5573][release-config-110], merged October 29, 2026,
[follow-up feedback][next-branch-feedback] proposed adding the next expected release branch to the same
exclusion when activating each freeze. For example, when starting the v1.11 freeze:

```yaml
# Fragment of the ordinary query during the proposed v1.11 freeze.
repos:
  - kubevirt/kubevirt
excludedBranches:
  - main
  - release-1.11
labels:
  - lgtm
  - approved
```

Use literal branch names: [Tide turns these entries][tide-query] into GitHub `base` search qualifiers, not regular
expressions. Do not use `^release-1.11$` or a wildcard covering all release branches. Replace the previous
cycle's branch entry at each activation rather than accumulating exclusions for older releases.

Prepare and deploy the exclusion before the new branch is created, or before PRs can merge into it if it
already exists. This closes the ordinary merge route during the interval between branching and verified
thaw. The [release-tool suggestion][freeze-tool-feedback] is to generate the updated exclusion alongside
the release configuration changes in one project-infra PR. This automation is proposed, not implemented
by #5573, which added/updated presubmit jobs rather than Tide queries.

With the existing dedicated queries still restricted to `main`, this proposal blocks all human-authored
PRs from Tide on the upcoming release branch until thaw, even if they carry `approved-vep` or critical-fix
labels. Selective admission on `main` is unchanged. The independent `kubevirt-bot`/`skip-review` route
remains available on both branches, subject to its existing checks and blockers. Older release branches
retain ordinary merging. These are Tide eligibility rules, not a guarantee against manual/admin merges.

At thaw, remove or comment out the entire `excludedBranches` mapping and both entries, restoring ordinary
merging on `main` and the new release branch. Freeze activates/uncomments the section; thaw
deactivates/comments it out. If critical fixes must merge to the new branch before thaw, extending the
dedicated queries to that branch requires an explicit policy/configuration change; labels alone cannot do it.

### Deployment Verification

Do not blindly revert all changes made during the freeze: later fixes, such as critical-flake admission,
must not be lost. Also check that no other broad query admits ordinary PRs on either excluded branch
while freeze is active.

Merged config is deployed through the existing [config-updater mapping][config-updater]. Tide's configured
sync period is two minutes, but deployment and reconciliation take time; this is not a guaranteed
two-minute activation deadline. Verify behavior before announcing activation or thaw as complete.

### Release Timing and Branching

Each release schedule must state enhancement freeze, code freeze, expected thaw, branching, and RC dates.
The [v1.10 schedule][schedule-110] plans code freeze for October 21 and thaw for October 28, 2026: a
one-week planned interval, not a universal duration. The activation config actually merged on October 22;
record actual activation separately from scheduled dates. An open thaw PR is not evidence of completed thaw.

The v1.10 schedule groups release-branch creation, `rc.0`, and restored normal merging at the thaw milestone.
This VEP proposes making the operational ordering explicit: create and verify the release branch and its
required CI configuration before lifting the gate on `main`. The release team should record the branch
point and RC tag, then verify thaw. This ordering is a proposed checklist requirement, not a claim that
the schedule already specifies it precisely. Under the next-release-branch proposal, keep the new branch
excluded from ordinary Tide merging throughout this preparation, then thaw both branches together.

The generic [release tracker][release-tracker] still places branching and `rc.0` under Code Freeze and
has no separate thaw milestone. Align it with the release-specific schedule rather than maintaining
conflicting instructions.

Existing [enhancement policy][exceptions] requires exception requests for work continuing past the relevant
deadlines. VEP approval and tracking should be established before freeze; late approval or unfinished work
still follows that process. Tide only sees the resulting labels: it does not check approval dates,
extension decisions, or exception expiry. The final relationship between the approved-VEP admission route
and extension requirements must be agreed and reflected in the enhancement documentation.

Reviewers also proposed delaying branching further, potentially branching from a selected/promoted RC to
reduce duplicate CI and backport reviews. The frozen-`main` interval reduces that duplication before the
branch is cut, but the broader RC-based model is deferred. It is not part of the documented v1.10 procedure,
which creates the branch at thaw and tags RCs and GA on the release branch. Adopting a different branch
point requires a separate release-procedure decision and CI/backport plan before changing thaw prerequisites.

### Merge Priority Is Not Admission

The [current priority order][tide-priority] is:

1. `kind/flake` and `priority/critical-urgent`
2. `kind/failing-test` and `priority/critical-urgent`
3. `kind/bug` and `priority/critical-urgent`
4. `approved-vep`

Priority orders work already eligible to merge; it does not grant freeze admission. In particular,
`kind/failing-test` plus critical urgency has no independent freeze query. Such a PR needs another valid
route. Adding a failing-test route would be a policy/configuration change, not documentation of current behavior.

Prioritizing VEPs without blocking ordinary work was proposed in review. Priority alone does not exclude
non-release work during freeze, so this VEP retains selective admission as well as the existing priority
ordering. Outside freeze, ordinary PRs can merge while these priorities remain configured. An independent
VEP merge pool was proposed in [project-infra#5193][merge-pool] but closed unmerged; it is not implemented.

### Label Assignment and Recovery

The existing [workflow][vep-workflow] runs on PR `opened`, `edited`, `reopened`, and `synchronize` events.
Its [script][vep-script] checks PR-body enhancement issue references against organization project 22,
accepting issues with status `Tracked` or `At risk`, and adds/removes `approved-vep`. Link the enhancement
tracking issue, not just the VEP design PR. Tide trusts this label; it does not independently consult the board.

For a missing or unexpected label:

1. Check the PR-body tracking-issue reference, project membership/status, and workflow result.
2. Correct the reference or board entry through the owning SIG/release team and re-evaluate labeling on a
   supported PR event. Board changes alone do not trigger this workflow.
3. If automation is incorrect or unavailable, ask a `restricted-labels-kubevirt` member to assess whether
   `/label approved-vep` is justified for genuinely tracked work; not every approver belongs to that team.
4. Fix the underlying mismatch: later automation can undo a manual label change. Do not use this recovery
   path to misclassify unrelated fixes as enhancement work.

The [Prow restriction][restricted-labels] applies to manual label commands, not all GitHub UI/API access.
The critical-priority, bug, and flake labels are not restricted to the release team by this configuration.
Neither the presence of those labels nor a successful Tide query proves release-team exception approval.

### Exception Handling and Remaining Decisions

Use the existing [kubevirt-dev exception process][exceptions], including justification, additional time
needed, and impact if denied. This VEP proposes linking the request and decision from the affected PR and
recording the deciding maintainers, owning SIG, release-team coordination, and any deadline or scope limit.
The approval authority/quorum and handling of expired exceptions still require explicit agreement.

Critical bug and flake fixes use their implemented routes after the appropriate review and classification;
they do not need `approved-vep`. For other exceptions, approval on the mailing list alone cannot satisfy
Tide. There is currently no generic `freeze-exception` query. Do not add unrelated labels merely to bypass
the gate. Agree a technical route before promising that an otherwise ineligible exception can merge.

Before this VEP is accepted, resolve and document:

- Which enhancement work requires an extension despite satisfying the approved-VEP query, and who grants it.
- Who authorizes critical classifications and other exceptions, and whether Prow label restrictions need
  strengthening to match that authority.
- Whether a restricted `freeze-exception` route is needed for exceptional work outside existing routes.
- Whether critical failing-test fixes need their own admission route or intentionally use another route.
- Whether release milestones should provide historical attribution, replace labels as the gate, or remain deferred.

### Release Team Checklist Integration

Apply this proposed checklist to `kubevirt/sig-release` in a follow-up change:

```markdown
### Code Freeze

- [ ] Publish the freeze/thaw window and eligibility/exception instructions.
- [ ] Confirm enhancement tracking and coordinate required extensions with owning SIGs.
- [ ] Enable the ordinary query's exclusions for main and the next expected release branch in a project-infra PR.
- [ ] Replace the previous cycle's branch entry; use literal names and deploy before the new branch admits PRs.
- [ ] Where supported, generate the exclusions and release configuration together using the release tool.
- [ ] Validate deployment and the eligibility matrix; record actual activation and announce freeze.
- [ ] Monitor critical fixes, labeling failures, and linked exception requests/decisions.

### Code Thaw

- [ ] Confirm the release branch point and create/verify the branch and its CI configuration.
- [ ] Record the release branch and rc.0 tag according to the release schedule.
- [ ] Remove/comment the whole exclusion mapping for both main and the new release branch; preserve policy fixes.
- [ ] Verify restored ordinary eligibility on both branches and unaffected older release branches.
- [ ] Record actual thaw, announce it, and collect process feedback.
```

Contributor documentation should link this policy and the exception process, explain how to diagnose
blocked Tide eligibility, and state that contributors can continue development/review while waiting for thaw.
It should not promise that an admission label alone guarantees merging or release inclusion.

## API Examples

Not applicable. This VEP concerns release procedure and Prow/Tide configuration, not the KubeVirt API.
The activation fragment above illustrates the switch; the linked source is authoritative for full queries.

## Alternatives

### Release Milestones

Reviewers proposed creating a milestone at the start of each release and adapting approved-VEP automation
to assign it instead of the label. A milestone preserves a PR's target-release attribution when viewed later,
whereas the current generic label does not identify a specific release.

The baseline retains the deployed label gate. Before switching to milestone-based admission, define
milestone creation, assignment/removal rules, permissions, current-release selection, critical-fix and bot
routes, and migration of existing PRs. A milestone must represent current eligibility, not merely a stale
historical target. This requires automation changes and explicit Tide query validation.

An alternative is to keep labels for admission and use milestones only for release attribution. In that
case, define who assigns them and retain historical attribution rather than removing it when eligibility
changes. Do not claim either milestone approach is implemented; selection remains a review decision.

### A Dedicated Freeze-Exception Label

A restricted `freeze-exception` query would preserve the meaning of `approved-vep` while admitting other
approved exceptions. It requires a label definition, command restrictions, a query, and governance for
assignment and expiry. It is not required to reproduce the deployed bug/flake routes and is not implemented.

### Priority Without a Freeze Gate

VEP priority is already configured, but allowing all ordinary PRs would no longer enforce a selective freeze.
Retain priority outside freeze and combine it with admission rules during freeze.

### Other Enforcement Mechanisms

- **`require-matching-label` plugin**: A single approved-VEP requirement would incorrectly block critical
  fixes and automation. An equivalent policy needs all alternative routes plus blocking-label integration.
- **Global merge blocker**: Blocks eligible work as well as ordinary work; unsuitable for a selective freeze.
- **Manual enforcement alone**: Leaves policy discovery and consistency to individual reviewers.
- **GitHub branch protection**: Does not natively express this time-dependent alternative-label policy;
  duplicating Tide's merge decision adds another mechanism to maintain.

## Scalability

This policy uses existing Tide label evaluation, config deployment, and approved-VEP automation. It does
not introduce a service or webhook. No additional label-reconciliation triggers or milestone automation
are claimed as part of the baseline.

## Update/Rollback Compatibility

- Freeze/thaw changes take effect after successful config deployment and Tide reconciliation, not at PR creation.
- Existing ordinary `main` PRs become ineligible at activation unless another route applies; eligible PRs
  still need normal checks and reviews. Activation does not undo already merged changes.
- The next-release-branch proposal adds a temporary human-PR merge block on that branch; older release
  branches and the existing bot route remain unaffected. It does not extend the main-only exception queries.
- Thaw restores ordinary eligibility on both excluded branches without removing specialized queries.
- If rollback is needed, change the ordinary query's branch exclusions and verify deployment. Preserve unrelated
  changes and coordinate any early thaw with the release team, especially if branching is incomplete.

## Functional Testing Approach

For configuration changes, use project-infra's existing [check-prow-config presubmit][checkconfig], which
runs Prow `checkconfig`. Syntax/config validation alone does not establish the intended admission policy.
Evaluate the following cases against the candidate queries, with required checks passing and blocking
labels absent unless a case states otherwise:

| Case                                                                        | Freeze     | Thaw       |
|-----------------------------------------------------------------------------|------------|------------|
| Human PR to `main`, `lgtm` + `approved` only                                | Ineligible | Eligible   |
| Same, plus `approved-vep`                                                   | Eligible   | Eligible   |
| Same, plus `kind/bug` + `priority/critical-urgent`                          | Eligible   | Eligible   |
| Same, plus `kind/flake` + `priority/critical-urgent`                        | Eligible   | Eligible   |
| Same, plus only `kind/bug`, only `kind/flake`, or only critical urgency     | Ineligible | Eligible   |
| Same, plus `kind/failing-test` + critical urgency only                      | Ineligible | Eligible   |
| Enhancement/critical-fix route missing `lgtm` or `approved`, no other route | Ineligible | Ineligible |
| Otherwise eligible human PR with any configured blocking label              | Ineligible | Ineligible |
| Release-branch PR, `lgtm` + `approved`                                      | Eligible   | Eligible   |
| `kubevirt-bot` PR with `skip-review`, no bot-route blocker                  | Eligible   | Eligible   |
| Human PR with `skip-review` only                                            | Ineligible | Ineligible |

Verify the bot route's distinct blocking-label list, unchanged rules in other repositories, and priority
ordering separately. Include failure cases for accidental broad `main` queries and for a thaw that leaves
the exclusion enabled.

For the proposed next-release-branch protection, additionally evaluate:

| Case                                                                          | Freeze     | Thaw     |
|-------------------------------------------------------------------------------|------------|----------|
| Human PR to next release branch, `lgtm` + `approved`                          | Ineligible | Eligible |
| Same, plus `approved-vep` or a critical bug/flake label combination           | Ineligible | Eligible |
| Human PR to an older release branch, `lgtm` + `approved`                      | Eligible   | Eligible |
| `kubevirt-bot` PR to next release branch, `skip-review`, no bot-route blocker | Eligible   | Eligible |

Verify the intended branch name is updated each cycle, works once the branch exists, and is not interpreted
as a regex. Test the branch before and after thaw, including a stale release-branch exclusion left active
while `main` is thawed, an accidentally retained older branch, and any alternative query bypassing the
exclusion. Validate generated release-tool output if that follow-up automation is implemented.

An unmerged config PR does not change production Tide behavior. Use isolated query evaluation or a test
deployment for pre-merge behavioral checks; do not call an unmerged PR a production dry run. After deployment,
inspect representative existing PRs/Tide status to confirm the matrix without merging dummy changes. Record
activation/thaw evidence and investigate config-updater or reconciliation failures before declaring completion.

## Implementation History

Dates below are config-PR merge dates, not measured live-cluster activation times.

| Date             | Change                                                                                                                            |
|------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| June 22, 2026    | [#5170](https://github.com/kubevirt/project-infra/pull/5170): Added approved-VEP priority.                                        |
| June 24, 2026    | [#5194](https://github.com/kubevirt/project-infra/pull/5194): Required approved-VEP eligibility initially without branch scoping. |
| June 25, 2026    | [#5197](https://github.com/kubevirt/project-infra/pull/5197): Scoped the gate to `main`, restoring release-branch merging.        |
| June 25, 2026    | [#5198](https://github.com/kubevirt/project-infra/pull/5198): Admitted critical-urgent bug fixes.                                 |
| July 2, 2026     | [#5214][thaw-19]: Lifted the v1.9 freeze using the ordinary query's branch exclusion.                                             |
| October 22, 2026 | [#5529][freeze-110]: Activated the v1.10 freeze using the same switch.                                                            |
| October 26, 2026 | [#5560](https://github.com/kubevirt/project-infra/pull/5560): Admitted critical-urgent flake fixes.                               |

As of October 27, [v1.10 thaw #5553][thaw-110] is open and unmerged. The separate
[VEP merge-pool proposal #5193][merge-pool] was closed unmerged and is not part of the implementation.

On October 29, [#5573][release-config-110] merged release-1.10 presubmit configuration. Its discussion
motivated the next-release-branch exclusion and release-tool proposals above; neither Tide protection
nor that automation was implemented by this PR.

## Graduation Requirements

This is a single-step release-process enhancement, not an API feature requiring Alpha/Beta/GA stages.
The merge history establishes implementation of the core mechanism; completion of the VEP additionally requires:

- [ ] Agreement on the documented eligibility rules, scope, priority distinction, and bot route.
- [ ] Resolution of extension requirements, exception authority, technical routes, and label-access policy.
- [ ] Explicit disposition of milestone attribution/gating and critical failing-test admission.
- [ ] Release tracker/schedules aligned on freeze duration, branching prerequisites, and thaw ordering.
- [ ] Next-release-branch exclusion validated across activation, branch creation, and thaw; older releases unaffected.
- [ ] Contributor documentation for blocked PRs, label recovery, and exceptions.
- [ ] Config validation and recorded behavioral verification of the eligibility matrix at activation and thaw.
- [ ] Release-team, SIG, and contributor feedback reviewed, with remaining process gaps tracked.

[tide-config]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/github/ci/prow-deploy/kustom/base/configs/current/config/config.yaml#L319-L423

[tide-priority]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/github/ci/prow-deploy/kustom/base/configs/current/config/config.yaml#L437-L441

[config-updater]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/github/ci/prow-deploy/kustom/base/configs/current/plugins/plugins.yaml#L776-L783

[restricted-labels]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/github/ci/prow-deploy/kustom/base/configs/current/plugins/plugins.yaml#L814-L847

[vep-workflow]: https://github.com/kubevirt/kubevirt/blob/b738a3394897411693838b02ae4d17a7ddafc509/.github/workflows/label_approved_veps.yaml

[vep-script]: https://github.com/kubevirt/kubevirt/blob/b738a3394897411693838b02ae4d17a7ddafc509/automation/label-approved-veps.py

[schedule-110]: https://github.com/kubevirt/sig-release/blob/fb9ee285f5efba8d163f13000c686f47d13bd6bb/releases/v1.10/schedule.md

[release-tracker]: https://github.com/kubevirt/sig-release/blob/fb9ee285f5efba8d163f13000c686f47d13bd6bb/.github/ISSUE_TEMPLATE/release-tracker.md#L57-L67

[exceptions]: https://github.com/kubevirt/enhancements/blob/main/README.md#exceptions

[freeze-110]: https://github.com/kubevirt/project-infra/pull/5529

[thaw-110]: https://github.com/kubevirt/project-infra/pull/5553

[thaw-19]: https://github.com/kubevirt/project-infra/pull/5214

[merge-pool]: https://github.com/kubevirt/project-infra/pull/5193

[checkconfig]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/github/ci/prow-deploy/files/jobs/kubevirt/project-infra/project-infra-presubmits.yaml

[release-config-110]: https://github.com/kubevirt/project-infra/pull/5573

[next-branch-feedback]: https://github.com/kubevirt/project-infra/pull/5573#issuecomment-6493022900

[freeze-tool-feedback]: https://github.com/kubevirt/project-infra/pull/5573#issuecomment-6492809234

[tide-query]: https://github.com/kubevirt/project-infra/blob/e979fcbbce0e0822e87553a82b84d34165781295/vendor/sigs.k8s.io/prow/pkg/config/tide.go#L620-L625
