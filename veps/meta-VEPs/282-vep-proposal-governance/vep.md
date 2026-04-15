# VEP #282: VEP Proposal Governance

## VEP Status Metadata

### Target releases

<!--
This VEP targets GA for v1.9 so that the new governance model is fully in place
before the v1.10 VEP season (design phase) begins. The intent is for all v1.10 VEPs
to be authored, reviewed, and tracked under the new process from the start.
-->

- This VEP targets alpha for version: N/A
- This VEP targets beta for version: N/A
- This VEP targets GA for version: v1.9

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement [issue](https://github.com/kubevirt/enhancements/issues/282) created
- [X] (R) GA target version is explicitly mentioned and approved

## Overview

This is a meta-VEP proposing a formal **Proposal Governance** layer for the VEP process.
It introduces explicit lifecycle states for VEP documents, structured front matter metadata,
formal reviewer and approver assignment, and CI enforcement — closing the gap between how
KubeVirt describes its VEP process and how it is practiced.

## Motivation

The KubeVirt VEP process defines two well-documented axes for tracking a feature:

1. **Release cycle phases**: Design → Implementation → Stabilization
   (defined in `docs/feature-lifecycle.md`)
2. **Feature maturity stages**: Alpha → Beta → GA, with time-box constraints and feature gate
   requirements (also defined in `docs/feature-lifecycle.md`)

What is missing is a third axis: the **proposal governance** lifecycle — the states a VEP
*document itself* passes through, independent of the feature's implementation maturity.

Currently:

- A merged VEP PR implicitly means "accepted", but there is no explicit field recording
  this, who approved it, or under what conditions.
- There is no formal state to express that a VEP has been deferred, rejected, withdrawn,
  or superseded by another proposal.
- Reviewer and approver assignment is described in the README as a SIG responsibility, but
  there is no structured place in the VEP document to record who has committed to this role.
  These assignments are tracked informally on the per-release GitHub Projects board, but not
  in the VEP itself. Community calls have been necessary to remind VEP authors to seek
  assigned reviewers.
- **There is no formal signal that the project has the capacity and commitment to work on
  a VEP during its targeted release.** A VEP can be proposed for a given release without
  any named person having agreed to review or approve it. The only place where per-release
  capacity is tracked today is the GitHub Projects board for that release
  (e.g. https://github.com/orgs/kubevirt/projects/21), and even there the signal is about
  tracking progress rather than upfront commitment. Without assigned reviewers and approvers,
  a VEP risks being accepted into a release and then stalling because no one has the bandwidth
  or context to drive it forward.
- The VEP template's "Target releases" section is free-text, making it difficult for tooling
  and the release team to extract milestone information programmatically.
- The VEP Freeze date — a hard deadline for design acceptance — has no automated enforcement.
  A VEP can be proposed as tracking a release after the freeze with no CI guard.

The Kubernetes KEP process addresses the first gap with a `status:` field
(`provisional → implementable → implemented` and terminal states), and an explicit
`reviewers:` / `approvers:` metadata block. KubeVirt can adopt the same model,
adapted for its GitHub-native toolchain.

## Goals

- Introduce a formal set of proposal governance states for VEPs, analogous to Kubernetes KEP statuses.
- Provide a structured, machine-readable front matter block in each `vep.md` that captures
  governance metadata: status, authors, reviewers, approvers, owning SIG, and milestone targets.
- Make reviewer and approver assignment explicit and traceable in the VEP document and tracking
  issue, serving as a formal signal that the project has the capacity and commitment to work on
  the VEP in the targeted release.
- Automate enforcement of the VEP Freeze date via CI, with a maintainer-controlled exception path.
- Validate front matter completeness and state-transition correctness on every PR.
- Leverage the existing Prow blunderbuss plugin (already active for the org) to suggest reviewers
  from SIG-level OWNERS files once those files are populated.
- Improve the tracking issue template to surface governance fields and provide a clearer
  per-stage progress table.
- Provide a concise VEP authoring guide documenting requirements, with `AGENTS.md` referencing it for AI tooling.
- The existing GitHub Projects board (e.g. https://github.com/orgs/kubevirt/projects/21) used
  to track VEPs across releases continues to be used alongside the improvements introduced here.

## Non Goals

- Changing the feature maturity lifecycle (Alpha / Beta / GA stages), which is already
  well-defined in `docs/feature-lifecycle.md`.
- Changing which Prow plugins are active (blunderbuss is already enabled at the org level).
- Automating the social process of finding and committing reviewers — tooling can suggest
  and enforce, but the human agreement remains a social act.
- Enforcing reviewer/approver assignments on implementation PRs in `kubevirt/kubevirt`
  (those are governed by that repo's own OWNERS files).
- Replacing or modifying the GitHub Projects board used to track VEPs
  (e.g. https://github.com/orgs/kubevirt/projects/21); that tooling is considered
  complementary and out of scope for this proposal.

## Definition of Users

- **VEP authors**: contributors proposing new features or process changes via VEPs.
- **VEP reviewers**: contributors who commit to reviewing a proposal's design and providing
  timely feedback; assigned by the owning SIG after triage.
- **VEP approvers**: SIG chairs or tech leads who commit to approving the design and
  transitioning the VEP to `implementable` status; assigned by the owning SIG.
- **SIG chairs**: responsible for triage, OWNERS file maintenance, and ensuring VEPs in
  their SIG have assigned reviewers and approvers before a release is targeted.
- **Release team**: responsible for tracking VEP Freeze compliance across all SIGs.

## User Stories

- As a **VEP author**, I want to know clearly what state my proposal is in and what is
  required to move it to the next state, so I can plan my work around release deadlines.
- As a **VEP author**, I want CI to tell me immediately if my front matter is incomplete
  or if I am attempting an invalid state transition, so I don't discover problems at review time.
- As a **VEP reviewer**, I want to be explicitly listed in the VEP document as having
  committed to this review, so my accountability is clear and traceable.
- As a **SIG chair**, I want Prow to automatically suggest reviewers from my SIG's OWNERS
  file when a new VEP PR is opened, so I don't have to manually monitor every incoming PR.
- As a **SIG chair**, I want to see at a glance whether a VEP targeting an upcoming release
  has named reviewers and approvers, so I can assess whether my SIG has the capacity to
  handle it before committing to the release.
- As a **release team member**, I want CI to block VEPs from being marked `implementable`
  for a release after the VEP Freeze date, so I have a reliable signal of what is in-scope.
- As a **community member**, I want to be able to see the current governance state of any
  VEP at a glance by reading its front matter, without having to reconstruct intent from
  PR history.

## Repos

- `kubevirt/enhancements` — all VEP document and tooling changes
- `kubevirt/sig-release` — source of truth for VEP Freeze dates (read-only by CI)

## Design

### Proposal Governance States

A VEP document can be in exactly one of seven states at any time:

```
         [PR opened]
              │
              ▼
        provisional ──────────────────────────────► withdrawn
              │                                    (author decides)
              │  SIG approvers merge VEP PR
              │  before VEP Freeze
              ▼
        implementable ────────────────────────────► deferred
              │                                    (no active progress)
              │  Feature reaches GA
              ▼
        implemented

   (at any point) ──────────────────────────────► rejected
                                                  (SIG + author agree)
   (when superseded) ───────────────────────────► replaced
```

| State | Meaning | Who sets it | Required by |
|---|---|---|---|
| `provisional` | VEP is open for design discussion | Author, at PR open | PR creation |
| `implementable` | Design approved; VEP is tracked for a release | SIG approvers, at PR merge | VEP Freeze |
| `implemented` | Feature has reached GA; VEP is complete | Author + SIG | After GA code merges |
| `deferred` | Not actively progressing; removed from release tracking | SIG | Any time |
| `rejected` | Will not proceed | SIG approvers + author | Any time |
| `withdrawn` | Author has discontinued the proposal | Author | Any time |
| `replaced` | Superseded by another VEP | Author of replacement | At replacement VEP merge |

**Front matter as single source of truth**: The VEP front matter is the authoritative record
of status and metadata. All status transitions — especially `provisional → implementable` —
MUST be done by updating the front matter via a PR. The GitHub Projects board, tracking
issue, and any CI tooling derive their view from the front matter. The new states align with
existing board terminology: `provisional` ≈ "proposed for consideration",
`implementable` ≈ "tracked".

The transition from `provisional` to `implementable` is the **critical gate**. It requires
that:

1. Named reviewers and approvers are listed in the front matter (no `TBD` entries) —
   this is the formal signal that the project has the **capacity and commitment** to work
   on this VEP during the targeted release. Without named people who have agreed to take
   responsibility, a VEP should not target a release, regardless of its technical merit.
2. A target milestone is set in the front matter.
3. The merge happens before the VEP Freeze for that release.

### VEP Front Matter

All `vep.md` files will carry a YAML front matter block (Jekyll-style `---` delimiters)
at the top of the file. This replaces the existing "Target releases" prose section and
"Release Signoff Checklist" in the template body.

Proposed schema:

```yaml
---
title: Short descriptive title
vep-number: NNNN          # equals the tracking issue number; set on issue creation
creation-date: "YYYY-MM-DD"
status: provisional       # one of the seven states above

authors:
  - "@github-handle"

owning-sig: sig-compute   # sig-compute | sig-network | sig-storage
participating-sigs: []    # other SIGs that must LGTM before merge

reviewers:
  - TBD                   # replaced by real handles after SIG triage;
                          # no TBD allowed when transitioning to implementable
approvers:
  - TBD                   # SIG chairs/leads who commit to approving the design;
                          # no TBD allowed when transitioning to implementable

feature-gate: FeatureName # omit field only if VEP body contains explicit opt-out justification
stage: alpha              # alpha | beta | ga — current implementation stage
milestone:
  alpha: "v1.x"
  beta: "v1.x"            # omit until planned
  ga: "v1.x"              # omit until planned

replaces: ""              # VEP number this supersedes, if any
superseded-by: ""         # VEP number that supersedes this, if any
---
```

Field rules enforced by CI:

| Field | `provisional` | `implementable` |
|---|---|---|
| `vep-number` | required | required |
| `reviewers` | `TBD` allowed | no `TBD` — commitment required |
| `approvers` | `TBD` allowed | no `TBD` — commitment required |
| `milestone.alpha` | optional | required |
| `feature-gate` | required (or opt-out justification in body) | required |

Meta-VEPs (which live under `veps/meta-VEPs/`) are exempt from the `feature-gate`
and `stage` fields, as they describe process changes rather than features.

### SIG OWNERS Files

Three new files are added to enable Prow blunderbuss reviewer suggestions:

```
veps/sig-compute/OWNERS
veps/sig-network/OWNERS
veps/sig-storage/OWNERS
```

Each carries only a `reviewers:` block — no `approvers:` block. Merge authority for
VEP PRs remains with the root `OWNERS` approvers. SIG chairs are responsible for keeping
these files current. Blunderbuss is already active at the org level and will immediately
start suggesting reviewers from these files once they exist.

The distinction between front matter `approvers:` and OWNERS-based approvers:

| | Front matter `approvers:` | Root `OWNERS` approvers |
|---|---|---|
| **Represents** | Who committed to approve the *design* | Who can merge *PRs* into the repo |
| **Set by** | SIG chairs, after triage | Repo maintainers |
| **Mechanism** | Social commitment, validated by CI | Prow `/approve` command |

### GitHub Labels

A `kind/vep` label is introduced for the `kubevirt/enhancements` repository. It serves
two purposes:

- **Discoverability**: makes VEP PRs distinguishable from other PRs (process changes,
  tooling fixes, migrations) in GitHub list views, project boards, and search filters.
  SIGs can filter by `kind/vep` to see only active proposals without noise.
- **Automation hook**: the front matter validator GitHub Action (see below) applies this
  label automatically on any PR that touches `veps/**/*.md` and contains a valid front
  matter block, so authors do not need to apply it manually.

The label is registered in `kubevirt/project-infra` (the Prow label configuration) and
applied via the Prow `label` plugin using the `/kind vep` command, or automatically by CI.

### CI Enforcement (GitHub Actions)

Three GitHub Actions enforce the governance model:

**Action 1 — Front Matter Validator** (`.github/workflows/validate-vep.yaml`):
Triggered on every PR touching `veps/**/*.md`. Checks:
- Front matter is present and valid YAML
- All required fields exist and have valid values
- `status` is one of the seven valid states
- Status transition from the base branch is valid (no skipped states, no revival from
  terminal states)
- If `status: implementable`: no `TBD` in `reviewers` or `approvers`; `milestone.alpha` set
- If `status: replaced`: `superseded-by` is non-empty

Posts a single structured comment summarising each check as pass/fail, with fix
instructions. Replaces the previous comment on re-run to avoid spam.

**Action 2 — VEP Freeze Enforcer** (`.github/workflows/enforce-vep-freeze.yaml`):
Triggered on every PR touching `veps/**/*.md`. When a PR sets `status: implementable`
and names a `milestone.alpha`:
- Fetches `https://raw.githubusercontent.com/kubevirt/sig-release/main/releases/v{X}/schedule.yaml`
- Locates the entry whose `what` field contains `"VEP Freeze"`, extracts its `when` date
- If today ≥ freeze date: blocks merge with a comment explaining the freeze and the
  exception path (a maintainer adds the `freeze-exception` label to bypass the block)

**Action 3 — Issue/Document Sync Reminder** (`.github/workflows/sync-vep-status.yaml`,
stretch goal):
Triggered on push to `main` after a VEP PR merges. Posts a comment on the tracking issue
reminding the author to update the "Proposal status" field to match the newly merged
front matter `status:`.

### Improved Tracking Issue Template

The existing `ISSUE_TEMPLATE/vep.md` is updated to:
- Make the issue number the VEP number explicitly (noted in the template instructions)
- Add a "Proposal status" field mirroring the front matter `status:` field
- Add `Reviewers:` and `Approvers:` fields (updated by the SIG after triage), making
  capacity and commitment visible directly in the tracking issue
- Replace free-text timeline bullets with a table: one row per stage (Alpha/Beta/GA),
  columns for VEP PR, Code PRs, Docs PR, and completion status
- Add a checklist that includes the `implementable`-before-freeze requirement explicitly

### Agentic AI Assistance

A concise VEP authoring guide is added to the repository documenting VEP requirements:
front matter fields, valid state transitions, pre-conditions for `implementable`, and the
VEP Freeze rule. This document is the stable, human-readable reference for both contributors
and tooling.

`AGENTS.md` references this guide so that AI coding assistants operating in this repository
can apply the documented requirements — for example:
- Validate front matter fields and flag missing or invalid values proactively
- Warn on invalid state transitions and check pre-conditions before suggesting changes
- Suggest reviewers from the relevant SIG OWNERS file when `reviewers: [TBD]`
- Fetch the current release schedule from `kubevirt/sig-release` and warn when a
  VEP Freeze is approaching for the targeted milestone
- Remind authors to keep the tracking issue's proposal status in sync with the front matter

### Migration of Existing VEPs

Existing VEP documents are migrated in a separate set of PRs (one per SIG), to be
done by SIG chairs after the tooling and template land:

1. Add front matter to each `vep.md`, with SIG chairs determining the correct `status:`
   (`implementable` for actively tracked VEPs, `implemented` for GA features,
   `provisional` for proposals still under discussion).
2. Single-file VEPs (e.g. `veps/sig-network/decouple-nad.md`) are converted to
   the directory format (`veps/sig-network/decouple-nad/vep.md`) for consistency
   and so that blunderbuss can resolve the correct OWNERS file.

### Work Item Summary

| WI | Description | Repo | Depends on |
|---|---|---|---|
| WI-0 | This VEP (foundation and community discussion) | enhancements | — |
| WI-1 | Front matter schema + updated VEP template | enhancements | WI-0 merged |
| WI-2 | SIG OWNERS files (`reviewers:` only) | enhancements | WI-0 merged |
| WI-3 | Register `kind/vep` label | project-infra | WI-0 merged |
| WI-4 | GitHub Actions: front matter validator + freeze enforcer | enhancements | WI-1, WI-3 |
| WI-5 | Improved tracking issue template | enhancements | WI-1 |
| WI-6 | VEP authoring guide + `AGENTS.md` reference | enhancements | WI-1 |
| WI-7 | Migration of existing VEPs (one PR per SIG) | enhancements | WI-1, WI-4 |
| WI-8 | Issue/document sync reminder action (stretch goal) | enhancements | WI-4 |

## API Examples

N/A — this VEP introduces no API changes to KubeVirt itself.

The concrete schema of the VEP front matter block serves as the primary "API" of this
proposal, and is shown in full in the Design section above.

## Alternatives

### Separate `vep.yaml` metadata file (à la Kubernetes `kep.yaml`)

Kubernetes keeps KEP metadata in a separate `kep.yaml` file alongside the `README.md`
proposal document. This keeps metadata cleanly separated from prose and is slightly
easier to parse with tooling.

The inline front matter approach is preferred here because:
- It keeps the metadata co-located and visible without opening a second file
- GitHub renders YAML front matter natively in markdown previews
- It is the simpler migration path for existing single-file VEPs
- The community is smaller than Kubernetes; tooling complexity should be minimised

### Manual tracking only (no CI enforcement)

The existing process could be improved purely through documentation and social norms,
without GitHub Actions. This was effectively the approach before this VEP.

The VEP Freeze enforcement in particular is important to automate: a manual process
relies on the release team catching violations after the fact, whereas CI prevents
them at PR creation time.

### Delegate governance to Prow plugins exclusively

Prow's `approve` and `lgtm` plugins handle PR merge mechanics well. However, they
have no awareness of VEP-specific concepts (front matter fields, governance states,
freeze dates). GitHub Actions fill this gap without requiring changes to the shared
Prow infrastructure.

## Scalability

No scalability implications. This VEP affects only the `kubevirt/enhancements` repository
and its processes.

## Update/Rollback Compatibility

The front matter block is additive — existing `vep.md` files without front matter remain
valid markdown and are not broken by this change. CI validation is introduced gradually:
the validator initially warns on existing VEPs and only enforces on new PRs, to allow
the migration (WI-6) to complete without blocking ongoing work.

## Functional Testing Approach

- The front matter validator GitHub Action is tested on a set of fixture VEP files
  covering valid and invalid states, missing fields, and invalid transitions.
- The freeze enforcer is tested with mocked schedule.yaml responses covering
  pre-freeze, at-freeze, and post-freeze dates.

## Implementation History

<!--
To be filled as work items are completed.
-->

## Graduation Requirements

This meta-VEP is considered complete when:

- [ ] All WI-1 through WI-5 are merged and active
- [ ] At least one full release cycle has operated under the new governance model
      (i.e. VEPs for a release were tracked with front matter, assigned reviewers/approvers,
      and the freeze enforcer ran without false positives)
- [ ] Migration (WI-6) is complete for all active VEPs

## Open Discussion

### Bar for merging a VEP in `provisional` state

The `provisional → implementable` flow implies **two separate merge events**, not one:

1. **First merge (`provisional`)**: The initial VEP PR is merged with `status: provisional`.
   This signals that the owning SIG accepts the proposal as worth pursuing. It does not
   require the design to be final or complete.
2. **Second merge (`implementable`)**: A follow-up VEP update PR changes the status to
   `implementable`. This is the SIG approvers explicitly blessing the design as ready for
   implementation. This PR must be merged before the VEP Freeze for the targeted release.

This separation is intentional: early submission of `provisional` VEPs is encouraged so
that the community has as much time as possible to review and iterate on the design before
the freeze deadline.

> **Note:** the two-merge flow is the **recommended** path, not a hard requirement. A VEP may
> be submitted directly as `implementable` in its first PR if named reviewers and approvers are
> already in place and the merge occurs before the VEP Freeze.

**Open question: what is the bar for the first merge?**

The bar for merging a VEP in `provisional` state is at the owning SIG's discretion.
However, the recommendation is that the *requirements* sections of the VEP —
**Overview, Motivation, Goals, Non Goals, Definition of Users, and User Stories** —
are sufficiently agreed upon before the provisional merge. These sections define the
*problem* being solved and the *scope* of the solution. They should be stable.

The *design* sections — **Design, API Examples, Alternatives** — may still be in flux
at provisional merge time and are expected to evolve during the review period before
the VEP is moved to `implementable`.

This mirrors how Kubernetes describes the `provisional` state: the SIG has accepted
that the work *should* be done, but not necessarily how.
