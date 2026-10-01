# VEP Proposal Governance

## Overview

[Feature Lifecycle](feature-lifecycle.md) documents two axes for tracking a feature: the
KubeVirt release cycle phases (design → implementation → stabilization) and the feature
maturity stages (alpha → beta → GA). This document covers a third, independent axis: the
**proposal governance** lifecycle — the state a VEP *document itself* is in, regardless of
the feature's implementation maturity.

This axis is captured in a YAML front matter block at the top of every `vep.md`. The front
matter is the single source of truth for a VEP's governance state; the tracking issue and
any project boards should reflect it, not the other way around.

The model — a `status:` field moving through `provisional → implementable → implemented`
(plus terminal states), alongside an explicit `reviewers:`/`approvers:` metadata block — is
adapted from the [Kubernetes KEP process](https://github.com/kubernetes/enhancements/tree/master/keps/NNNN-kep-template),
tailored to KubeVirt's GitHub-native toolchain.

> **Note:** This governance layer was introduced by
> [VEP-282](https://github.com/kubevirt/enhancements/issues/282); see that VEP for the full
> motivation and design discussion.

## Front Matter Schema

```yaml
---
title: Short descriptive title
vep-number: NNNN          # equals the tracking issue number; set on issue creation
creation-date: "YYYY-MM-DD"
status: provisional       # provisional | implementable | implemented |
                          # deferred | rejected | withdrawn | replaced

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

Meta-VEPs (under `veps/meta-VEPs/`) are exempt from `feature-gate` and `stage`, since they
describe process changes rather than features.

## Governance States

A VEP document is in exactly one of seven states at any time:

| State | Meaning | Who sets it | Required by |
|---|---|---|---|
| `provisional` | VEP is open for design discussion | Author, at PR open | PR creation |
| `implementable` | Design approved; VEP is tracked for a release | SIG approvers, at PR merge | VEP Freeze |
| `implemented` | Feature has reached GA; VEP is complete | Author + SIG | After GA code merges |
| `deferred` | Not actively progressing; removed from release tracking | SIG | Any time |
| `rejected` | Will not proceed | SIG approvers + author | Any time |
| `withdrawn` | Author has discontinued the proposal | Author | Any time |
| `replaced` | Superseded by another VEP | Author of replacement | At replacement VEP merge |

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

`provisional`, `implementable`, and `implemented` form the normal progression. `deferred`,
`rejected`, `withdrawn`, and `replaced` are terminal or side states reachable at any point,
as noted above.

## The `provisional` → `implementable` Gate

This transition is the critical gate. It requires:

1. Named reviewers and approvers in the front matter (no `TBD` entries) — the formal signal
   that the project has the capacity and commitment to work on the VEP during the targeted
   release.
2. A target milestone set in the front matter (`milestone.alpha` at minimum).
3. The merge happening before the VEP Freeze for the targeted release (see
   [kubevirt/sig-release](https://github.com/kubevirt/sig-release/tree/main/releases) for
   the schedule).

## Field Rules by Status

| Field | `provisional` | `implementable` |
|---|---|---|
| `vep-number` | required | required |
| `reviewers` | `TBD` allowed | no `TBD` |
| `approvers` | `TBD` allowed | no `TBD` |
| `milestone.alpha` | optional | required |
| `feature-gate` | required (or opt-out justification in body) | required |

## Getting Reviewers

Each owning SIG maintains an `OWNERS` file under `veps/<sig>/` with a `reviewers:` block.
Prow's blunderbuss plugin uses this list to suggest reviewers automatically on new VEP PRs.
Replace `reviewers: [TBD]` in the front matter with the assigned names once the SIG has
triaged the proposal.

## Automation Status

> **Note:** CI enforcement of the rules above (front matter validation and VEP Freeze
> blocking) is tracked separately in VEP-282's remaining work items and has not landed yet.
> Until it does, SIG approvers are responsible for checking these rules manually before
> merging a VEP PR.
