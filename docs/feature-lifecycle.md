# KubeVirt Feature Lifecycle

## Summary

KubeVirt requires a clear policy on how features are introduced,
evaluated and finally graduated or removed.

This document defines the steps and policies to follow in order
to manage a feature and its lifecycle in KubeVirt.

The document is focusing on introducing features in
a stable API (CRD) version, e.g. `kubevirt.io/v1`.

## Overview

KubeVirt has grown into a mature virtualization management solution
with a large set of features.

New features are being proposed and added regularly to its portfolio.

With time, the challenge of supporting and maintaining such a large
set of features raised the need to re-examine their relevance.
It also raised the need to examine with more care features graduation.

We would like to see features being evaluated carefully
before they are introduced, while they are experimented with and
proven to be actually in use (and useful) before graduating.

> **Note**: Once a feature graduates, it is included in a
> General Availability (GA) release with its functionality available
> to all users. GA features need to comply with [semver](https://semver.org/)
> which adds constraints on their ability to change (including deprecation).

### Kubevirt's release cycle

Each Kubevirt release cycle is split into 3 different phases:
1. **Design phase**: During this phase the community will be focused on creating and reviewing VEP PRs.
At a higher level, during this period the community will start crafting what the next release will look like;
ensure there are enough reviewers and approvers to cover the VEPs, discuss priorities, etc.
The VEP freeze marks the end of this phase.
2. **Implementation phase**: This phase is centered around the implementation of the VEPs that were merged in the design phase.
PRs that implement a VEP would be required to reference it in the PR description, which will result in the bot adding
an `approved-vep` label to the PR.
During this phase, the community will help review `approved-vep` PRs which will be considered as the highest priority PRs to review.
The code freeze marks the end of this phase.
3. **Stabilization phase**: During this time the community will focus on stabilization, allowing only bug PRs to be merged.
The Kubevirt release marks the end of this phase.

Important notes:
- It is recommended to submit VEPs for the next releases as soon as possible.
It is not needed, and not recommended, to wait to the design phase in order to do so.
- PRs that are not related to any VEPs would also be reviewed and implemented during the implementation phase,
but they will be considered to have lower priority.
- Community members, especially VEP authors, are expected to participate in reviews.
Those who focus solely on their own work may see their own submissions deprioritized.

For the exact schedule for each Kubevirt release, please see https://github.com/kubevirt/sig-release/tree/main/releases.

## Feature Lifecycle Policy
The feature lifecycle policy on how to define a feature lifecycle is influenced by
processes and policies from the Kubernetes project.
These sources are scattered around, each focusing on different
aspects of a feature:
- [Feature Gates](https://kubernetes.io/docs/reference/command-line-tools-reference/feature-gates/)
- [Graduation](https://kubernetes.io/blog/2020/08/21/moving-forward-from-beta/)
- [Changing the API](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api_changes.md)
- [Deprecation](https://kubernetes.io/docs/reference/using-api/deprecation-policy/)

The policy takes the top-down approach, starting with the high level
flow that a common feature will traverse through.

Both feature graduation and discontinuation flows are covered.
Including the implications on users.

### Feature Gates
It is important to differentiate between Feature Gates and feature configuration.
- Feature Gate: A flag that controls the presence or availability of a feature in the cluster.
- Feature configuration: Cluster or workload level configuration that allows an admin or user
  (depending on the feature) to control aspects of a feature operation.
  A common usage is to determine if features are opt-in or opt-out by default.

In other words, a Feature Gate flag is solely intended to control
feature lifecycle. It should not be confused and used as a cluster
configurable enablement of the functionality.
In cases where the cluster admin should control a functionality,
regardless to the feature stage, dedicated configuration fields
should be included.

#### Why is a feature gate important?

A feature gate is needed for several reasons:
- To force the user to **explicitly enable it**, therefore expressing an agreement to enable an experimental feature
  that might be broken.
- To loudly express that the API can be changed in a breaking way or even entirely removed at any moment
  (according to the graduation phase. See [Graduation Phases](#graduation-phases) below).
- To loudly express that we are not committing or promising anything regarding the feature's quality.
  It could be broken, could be buggy, could harm security, scale, performance, etc.
  (according to the graduation phase. See [Graduation Phases](#graduation-phases) below).
- To (dramatically) lower the bar for new alpha features.
  This raises the reviewers' confidence that a regression would not introduce to anyone not explicitly enabling the gate.

#### Can a VEP choose to not include a feature gate?

It can be decided that a VEP would not need a feature gate.
However, in order to do so, the VEP author has to provide a strong reasoning and justification.

For example, the following reasons are considered as weak reasons for avoiding a feature gate:
- It is a simple change.
- It is off by default, hence safe.
- It keeps backward compatibility.

Generally, avoiding a feature gate is expected to be rare.
Possible reasons for not having a feature gates could be (although demand specific discussion):
- It's basically impossible to keep both the old and the new living in harmony in the codebase.
- The VEP is about reshaping an existing feature in a relatively simple way.
- The VEP adds logic under an existing gate.

### Feature Stages
A feature is expected to pass in the following order through the following stages:
1. Enhancement proposal.
2. Implementation.
3. Release as Alpha (experimental).
4. Release as Beta (pre-release for evaluation).
5. Release as General Availability (graduation).
6. Removal.

Starting from the Alpha release, it can be removed with restrictions that
depend on the release stage (Alpha, Beta, GA).

[Removal](#removal) of features is widely discussed later
in this document.

#### Enhancement proposal
As the first step for introducing a new feature, a formal proposal is
expected to be shared for public review via mailinglist and
a [design proposal](https://github.com/kubevirt/community/tree/main/design-proposals).

This is the first opportunity to evaluate a new feature.
The proposal needs to include motivation, goals, implementation details
and phases. Review the [proposal template](https://github.com/kubevirt/community/blob/main/design-proposals/proposal-template.md)
for more information.

#### Implementation
The development work on the feature is expected to include coding,
testing, integration and documentation.

#### Releases
- **Alpha**:
  An initial release of the feature for experimental purposes.
  Recommended for non-production usages, evaluation or testing.

  The API is considered unstable and may change significantly.
  There are no backward compatability considerations and it can
  be removed at any time.

  The period in which a feature can remain in Alpha is limited,
  assuring features are not piling up without control.
  See [release stage transition table](#release-stage-transition-table)
  for more information.

  The feature presence is controlled using a Feature-Gate (FG) during
  runtime. It must be specified for the feature to be active.

- **Beta**:
  The first release that can be evaluated with care in production.
  Acting as a pre-release, its main objective is to collect feedback
  from users to assure its usefulness and readiness for graduation.
  If there is no confidence of usage or usefulness, it may remain in
  this stage for some time.

  However, the period in which a feature can remain in Beta is limited,
  assuring features are not piling up without control.
  See [release stage transition table](#release-stage-transition-table)
  for more information.

  The API is considered stable with care not to break backward compatibility
  with previous beta releases.
  This implies that fields may only be added during this stage,
  not removed or renamed.

  The feature presence is controlled using a Feature-Gate (FG) during
  runtime. It must be specified for the feature to be active.

- **GA**:
  The feature graduated to general-availability (GA) and is now part of
  the core features.

  The API is considered stable with care not to break backward compatibility
  with the previous releases.

  The feature functionality is no longer controlled by a FG.

#### Removal
If a feature is targeted for deprecation and retirement,
it needs to pass a deprecation process, depending on its current
release stage (Alpha, Beta, GA).

For more details, see [here](#deprecation-and-removal).

#### Release Stage Transition Table
The following table summarized the different release stages with their
transition requirements and restrictions.

| Stage          | Period range    | F.Gate | Removal Availability       |
|----------------|-----------------|--------|----------------------------|
| Alpha          | 1 to 2 releases | YES    | Between **minor** releases |
| Beta           | 1 to 3 releases | YES    | Between **minor** releases |
| GA             | -               | NO     | Between **major** releases |

Through Alpha and Beta feature releases, a FG must be set in order
for the feature to function.
By default, no FG is specified, therefore the feature is disabled.

If a feature is not able to transition to the next stage in the defined period,
it should be removed automatically.

> **Note**: Exceptions to the period range may apply
> if 2/3 of active maintainers come to agreement to prolong
> a specific feature.

### Deprecation and Removal
One reason for features to go through the Alpha and Beta stages,
is the opportunity to examine their usefulness and adoption.
Same goes with major releases that intentionally allow breaking
backward compatibility (as specified by [semver](https://semver.org/)).

Therefore, it is only natural that some features will not graduate
between the stages, or will be found irrelevant after some time and be
removed when transitioning between major releases.

#### Major Releases
KubeVirt follows semver versioning, in which major versions may
break API compatibility. Therefore, discontinuation of features
is somehow simpler when incrementing the major version.

However, this is not without a cost.
When a new major release is introduced, the previous one is still maintained
and supported, something that does not exist with minor releases.

#### The Deprecation Flow (for Minor releases)
Only Alpha and Beta features can be removed during a minor release.

These are the steps needed to deprecate & remove a feature:
- Proposal: Prepare a proposal to remove a feature with proper
  reasoning, phases, exact timelines and functional alternatives (if any).
  The proposal should be reviewed and approved.
- Notification: Notify the project community about the feature
  discontinuation based on the approved proposal.
  All details of the plan should be provided to allow users and possibly
  down-stream projects to adjust.
  Use all community media options to distribute this information
  (e.g. mailing list, slack channel, community meetings).
- Deprecation warnings: Add deprecation warnings at runtime to warn users
  that the feature is planned to be removed.
  Warnings should be raised when:
  - Feature API fields are accessed.
  - Feature FG is activated.
  - Behavior related to the feature is detected (optional).
- Removal: Feature removal involves removing the core functionality
  of a feature and its exposed API.
  - The core implementation can be removed in two steps:
    - The FG is removed by assuring it is never reported as set
      (i.e. even if it is left by the operator configured, internally
       it is ignored).
      At this stage, the core implementation will follow the FG conditions
      and therefore from the outside the feature is inactive.
    - In case there are no side effects, the core implementation code can
      be removed.
  - The API types are not to be removed, as it may have implications
    with the underlying storage which has already persisted them.
    Kubernetes has not removed fields, it just kept them around with the
    warning that they have been deprecated and no longer available.

    While keeping fields around for a period of a release or two makes
    sense, beyond a limited period it adds a burden on dragging leftover
    fields around to eternity.

### Exceptions
While the project strives to maintain a stable contract with its users,
there may be scenarios where the policy described here will not be a fit.

Therefore, it should be acceptable to have exceptions from time to time
given a very good reasoning and an agreement from 2/3 of the project
maintainers (also known as "approvers").

### Miscellaneous

- New features are to be introduced to major and minor release versions only.
  For clarification, this implies that new features are **not** to be backported.
- CI:
  - Alpha stage features should not be gating on CI.
  - Beta and GA features should be gating on CI.
- API fields may be marked with the following information:
  - Description
  - Release stage (alpha/beta/ga) and release version.

  It is left to a follow-up implementation proposal to define the exact format and
  required/optional information.