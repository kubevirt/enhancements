# VEP #172: Code Freeze Enforcement via Tide Configuration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes a mechanism to technically enforce code freeze in the `kubevirt/kubevirt` repository by
requiring the `approved-vep` label for merging PRs into the `main` branch during the freeze period. The
approach adapts the proven Kubernetes code freeze enforcement strategy, which modifies the Tide merge query
configuration to gate merging on a milestone (in our case, the `approved-vep` label).

Today, KubeVirt's code freeze is a process-driven agreement: contributors are expected to respect the freeze,
but nothing prevents a non-VEP PR from being merged. This VEP closes that gap by making the freeze technically
enforceable through Prow's Tide component.

## Motivation

The KubeVirt community has established a VEP process
([kubevirt/enhancements](https://github.com/kubevirt/enhancements)) and an `approved-vep` label to indicate
that a PR is tied to an approved, tracked enhancement for the current release. However, the code freeze is
currently only enforced by convention:

- There is no technical mechanism preventing PRs without `approved-vep` from being merged during code freeze.
- Reviewers and approvers must manually check whether a PR should be merged, which is error-prone under time
  pressure near a release.
- Non-VEP PRs that slip through during code freeze can destabilize the release and dilute the community's
  focus on prioritized work.

Kubernetes has solved the same problem for over a decade using a simple, battle-tested approach: modifying the
Tide configuration to require a milestone on PRs targeting frozen branches. KubeVirt can adapt this pattern
using the existing `approved-vep` label instead of a milestone.

## Goals

- Technically enforce code freeze by preventing PRs without the `approved-vep` label from merging into `main`
  during the freeze period.
- Provide a clear, auditable mechanism (a config PR to `kubevirt/project-infra`) to activate and deactivate
  the freeze.
- Provide an exception path for critical bug fixes during the freeze.
- Align with the existing VEP process and `approved-vep` label automation without requiring changes to either.

## Non Goals

- Changing the VEP process itself (proposal, review, approval flow).
- Changing how the `approved-vep` label is assigned (the existing GitHub Actions automation and restricted
  label access remain as-is).
- Enforcing enhancement freeze (the deadline for VEP proposals). Enhancement freeze remains a process-driven
  checkpoint managed by SIGs and the release team.
- Blocking merges on release branches. Release branches have their own cherry-pick workflows and are not
  subject to the code freeze gate proposed here.
- Replacing the exception process. Exceptions continue to follow the existing
  [kubevirt-dev mailing list process](https://groups.google.com/forum/#!forum/kubevirt-dev).

## Definition of Users

- **Release team**: Activates and deactivates the code freeze by submitting config PRs. Grants exceptions by
  applying the `approved-vep` or `freeze-exception` label to critical PRs.
- **VEP owners / contributors**: Their PRs that reference tracked VEPs already receive `approved-vep`
  automatically and are unaffected by the freeze.
- **General contributors**: Their non-VEP PRs (refactoring, minor fixes, new features without a VEP) are
  blocked from merging during the freeze period. They can request an exception from the release team.
- **Reviewers and approvers**: Freed from manually policing the freeze, they can focus on reviewing
  prioritized VEP work.

## User Stories

- As a **release team member**, I want to activate code freeze with a single config PR so that only
  VEP-backed PRs can merge, without having to manually monitor every PR.
- As a **VEP owner**, I want my PRs to merge normally during code freeze because they are already labeled
  `approved-vep`, so the freeze is transparent to me.
- As a **contributor** with a critical bug fix during code freeze, I want a clear exception process so that
  my fix is not blocked unnecessarily.
- As a **reviewer**, I want to trust that only release-appropriate PRs are mergeable during code freeze,
  so I can focus my review bandwidth on prioritized work.

## Repos

- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) - Prow/Tide configuration changes
  (primary)
- [kubevirt/sig-release](https://github.com/kubevirt/sig-release) - Release tracker template updates to
  include freeze activation/deactivation steps
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) - This VEP (documentation only)

## Design

### How Kubernetes Does It

Kubernetes enforces code freeze by modifying the Tide query for `kubernetes/kubernetes` in
`kubernetes/test-infra`. The single Tide query is split into two:

1. A **frozen query** for `master` and the release branch that requires the `milestone: vX.Y` field,
   meaning only PRs assigned to the release milestone can merge.
2. An **unfrozen query** for all other branches with no milestone requirement.

At code thaw, the split is reverted to a single query. Only members of the `milestone-maintainers`
GitHub team can assign milestones, providing access control.

### Adapting for KubeVirt

KubeVirt does not use GitHub milestones for release tracking. Instead, it uses the `approved-vep` label,
which is already:

- Automatically applied/removed by GitHub Actions based on the project board status
- Restricted so only the `restricted-labels-kubevirt` team can manually apply it via `/label approved-vep`

The proposed approach uses `approved-vep` as the merge gate instead of a milestone, achieving the same
effect with existing infrastructure.

### Current Tide Configuration

The current Tide query for `kubevirt/kubevirt` (in `kubevirt/project-infra`) is:

```yaml
tide:
  queries:
  - repos:
    - kubevirt/kubevirt
    # ... other repos in the same query ...
    labels:
    - lgtm
    - approved
    missingLabels:
    - do-not-merge
    - do-not-merge/hold
    - do-not-merge/invalid-owners-file
    - do-not-merge/work-in-progress
    - do-not-merge/release-note-label-needed
    - do-not-merge/invalid-commit-message
    - needs-rebase
    - "dco-signoff: no"
```

### Proposed Change: Activating Code Freeze

At code freeze, the release team submits a PR to `kubevirt/project-infra` that extracts
`kubevirt/kubevirt` from the shared query and splits it into dedicated queries:

```yaml
tide:
  queries:
  # ---- CODE FREEZE: kubevirt/kubevirt main branch ----
  # Only PRs with approved-vep can merge during freeze.
  - repos:
    - kubevirt/kubevirt
    includedBranches:
    - main
    labels:
    - lgtm
    - approved
    - approved-vep
    missingLabels:
    - do-not-merge
    - do-not-merge/hold
    - do-not-merge/invalid-owners-file
    - do-not-merge/work-in-progress
    - do-not-merge/release-note-label-needed
    - do-not-merge/invalid-commit-message
    - needs-rebase
    - "dco-signoff: no"

  # ---- CODE FREEZE: kubevirt/kubevirt other branches ----
  # Release branches and others are not frozen.
  - repos:
    - kubevirt/kubevirt
    excludedBranches:
    - main
    labels:
    - lgtm
    - approved
    missingLabels:
    - do-not-merge
    - do-not-merge/hold
    - do-not-merge/invalid-owners-file
    - do-not-merge/work-in-progress
    - do-not-merge/release-note-label-needed
    - do-not-merge/invalid-commit-message
    - needs-rebase
    - "dco-signoff: no"

  # ---- Remaining repos (unchanged) ----
  - repos:
    - kubevirt/containerdisks
    - kubevirt/containerized-data-importer
    # ... all other repos that were in the original query ...
    labels:
    - lgtm
    - approved
    missingLabels:
    - do-not-merge
    - do-not-merge/hold
    - do-not-merge/invalid-owners-file
    - do-not-merge/work-in-progress
    - do-not-merge/release-note-label-needed
    - do-not-merge/invalid-commit-message
    - needs-rebase
    - "dco-signoff: no"
```

**Effect**: PRs against `main` without the `approved-vep` label will show a Tide status of
"Not mergeable" and cannot be merged by Tide. PRs that already have `approved-vep` (because they
reference a tracked VEP) merge normally.

### Proposed Change: Deactivating Code Freeze (Code Thaw)

At code thaw, the release team submits a revert PR that collapses the split queries back into the
original shared query. This is a straightforward `git revert` of the freeze PR.

### Exception Handling

During code freeze, critical bug fixes that are not tied to a VEP need an exception path:

**Option 1 (Simple)**: The release team manually applies `approved-vep` to the exception PR using
`/label approved-vep`. This works because the label is already restricted to the
`restricted-labels-kubevirt` team. However, it overloads the meaning of `approved-vep` since the PR
is not actually VEP-backed.

**Option 2 (Recommended)**: Introduce a separate `freeze-exception` label and add a second frozen
query that accepts it:

```yaml
  # ---- CODE FREEZE: exception PRs ----
  - repos:
    - kubevirt/kubevirt
    includedBranches:
    - main
    labels:
    - lgtm
    - approved
    - freeze-exception
    missingLabels:
    - do-not-merge
    - do-not-merge/hold
    - do-not-merge/invalid-owners-file
    - do-not-merge/work-in-progress
    - do-not-merge/release-note-label-needed
    - do-not-merge/invalid-commit-message
    - needs-rebase
    - "dco-signoff: no"
```

The `freeze-exception` label would be restricted to the same team (`restricted-labels-kubevirt`) and
added to the labels configuration in `kubevirt/project-infra`. This keeps `approved-vep` semantically
clean: it always means "tied to a tracked VEP."

### Release Team Checklist Integration

The release tracker template in `kubevirt/sig-release` should be updated with:

```markdown
### Code Freeze
- [ ] Submit PR to `kubevirt/project-infra` splitting the Tide query for `kubevirt/kubevirt`
      to require `approved-vep` on `main` branch
- [ ] Announce code freeze on kubevirt-dev mailing list
- [ ] Monitor exception requests

### Code Thaw
- [ ] Revert the code freeze PR in `kubevirt/project-infra`
- [ ] Announce code thaw on kubevirt-dev mailing list
```

## API Examples

Not applicable. This VEP modifies CI/CD configuration (Prow/Tide YAML), not KubeVirt's API.

See the [Design](#design) section for the exact YAML configuration changes.

## Alternatives

### Alternative 1: `require-matching-label` Prow Plugin

Configure the `require-matching-label` plugin to automatically add a `do-not-merge/needs-approved-vep`
label to PRs that lack `approved-vep`:

```yaml
require_matching_label:
- missing_label: do-not-merge/needs-approved-vep
  org: kubevirt
  repo: kubevirt
  branch: main
  prs: true
  regexp: ^approved-vep$
  missing_comment: |
    This PR is missing the `approved-vep` label. During code freeze,
    only PRs tied to an approved VEP can merge.
```

**Why not chosen**: This approach requires both a plugin config change and adding the new
`do-not-merge/*` label to Tide's `missingLabels` list. It has more moving parts than a simple Tide
query split, and still requires config changes to activate/deactivate. The Tide query approach is
simpler, proven, and maps directly to the Kubernetes pattern.

### Alternative 2: `tide/merge-blocker` Label

Open a GitHub issue with the `tide/merge-blocker` label to block all merges during code freeze.

**Why not chosen**: This is a blunt instrument that blocks *all* PRs, including VEP-backed ones.
It would require manually removing the blocker for each approved PR or not using it at all, defeating
the purpose.

### Alternative 3: Manual Enforcement

Continue with the current process-driven approach where reviewers and approvers manually police the
freeze.

**Why not chosen**: This is error-prone, places undue burden on reviewers, and does not scale. The
whole point of this VEP is to replace manual enforcement with a technical one.

### Alternative 4: GitHub Branch Protection Rules

Use GitHub branch protection to require the `approved-vep` label before merging.

**Why not chosen**: KubeVirt uses Prow/Tide for merge management, not GitHub's native merge buttons.
Branch protection rules would conflict with or duplicate Tide's role. Additionally, GitHub branch
protection does not support conditional/time-based label requirements without external tooling.

## Scalability

This change has negligible scalability impact:

- Tide already evaluates label requirements for every PR. Adding `approved-vep` to the required labels
  list for one query does not meaningfully increase processing time.
- The config change is a static YAML modification, not a new service or webhook.
- The `approved-vep` label automation (GitHub Actions) already runs on every PR event and is unaffected.

## Update/Rollback Compatibility

- **Activation**: Submitting the freeze config PR takes effect as soon as it is merged and Tide resyncs
  (within the configured `sync_period` of 2 minutes).
- **Rollback**: Reverting the config PR immediately lifts the freeze. No state is lost, no PRs are
  affected beyond their Tide merge status.
- **No impact on existing PRs**: PRs that already have `approved-vep` continue to merge normally. PRs
  without it simply cannot merge until the freeze is lifted or an exception is granted.
- **No impact on release branches**: Only the `main` branch is gated. Release branch cherry-pick
  workflows are unaffected.

## Functional Testing Approach

- **Pre-merge validation**: The Tide config change can be validated by the existing Prow config
  validation tooling (`checkconfig`) which runs on PRs to `kubevirt/project-infra`.
- **Dry run**: Before the first real freeze, submit the config change as a PR and verify via Tide's
  status reporting that PRs without `approved-vep` show "Not mergeable" while PRs with the label
  remain mergeable. The PR can then be reverted without merging.
- **Ongoing validation**: During each freeze cycle, the release team verifies that:
  - PRs without `approved-vep` show a blocking Tide status on `main`.
  - PRs with `approved-vep` merge normally on `main`.
  - PRs on release branches are unaffected.
  - Exception PRs with `freeze-exception` (if Option 2 is adopted) merge normally on `main`.

## Implementation History

<!--
To be updated as implementation progresses.
-->

## Graduation Requirements

### Alpha

- [ ] Tide query split documented and validated via dry-run PR to `kubevirt/project-infra`
- [ ] `freeze-exception` label created in `kubevirt/project-infra` labels configuration
- [ ] `freeze-exception` label restricted to `restricted-labels-kubevirt` team
- [ ] Release tracker template in `kubevirt/sig-release` updated with freeze/thaw checklist items
- [ ] First code freeze enforced using the Tide query split for one release cycle
- [ ] Feedback collected from release team, VEP owners, and contributors

### Beta

- [ ] Process refined based on Alpha feedback
- [ ] Freeze/thaw config change templated or scripted for easy application by the release team
- [ ] Documentation added to `kubevirt/community` contributor guide

### GA

- [ ] Process used successfully for at least two consecutive release cycles
- [ ] No unresolved issues or process gaps reported
- [ ] Exception process validated and documented
