---
title: Your short, descriptive title
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

# VEP #NNNN: Your short, descriptive title

## Overview

<!--
Provide a brief overview of the topic
-->

## Motivation

<!--
Why this enhancement is important
-->

## Goals

<!--
The desired outcome
-->

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

## Definition of Users

<!--
Who is this feature set intended for
-->

## User Stories

<!--
List of user stories this design aims to solve
-->

## Repos

<!--
List of repose this design impacts
-->

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

## API Examples

<!--
Tangible API examples used for discussion
-->

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Does it belong to core KubeVirt?

<!--
Explain why this feature belongs to the core KubeVirt repository and which other alternatives were considered.
Other alternatives can be:
- External controllers.
- Plugins (see VEP-190).
- Other existing repositories in the KubeVirt organization (e.g. HCO / CDI / etc.).
-->

## Scalability

<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

### Beta

#### On-By-Default Readiness

<!--
Beta features are enabled by default.
In this section, please specify what needs to be done in order for the VEP to be ready to be enabled by default.
-->

### GA
