---
name: Virtualization Enhancement Proposal (VEP) Tracker
about: Track a VEP through graduation from Alpha to GA
title: 'VEP NNNN: Your short, descriptive title'
labels: kind/tracker, kind/enhancement
assignees: ''
---
<!-- 
Please refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md 
-->

<!--
This issue's number is the VEP number. Once the VEP PR is opened, set it as the
`vep-number:` field in the VEP's front matter. See docs/vep-governance.md for the
full front matter schema and the proposal governance states referenced below.
-->

**Primary contact (assignee)**:
<!-- 
Handle of the current contact for the feature.

Use the `/assign` command to assign the assignee on creation of the issue.
-->

/assign $ASSIGNEE

**Current Feature Stage**:

<!-- 
The current stage of the feature, should be one of New, Alpha, Beta, GA or Deprecated.
-->

**Proposal status**:

<!--
Mirrors the VEP's front matter `status:` field: provisional | implementable | implemented |
deferred | rejected | withdrawn | replaced. Keep this in sync whenever the VEP PR updates
the front matter status. See docs/vep-governance.md.
-->

**Feature Gate**:

<!-- 
The full name of the feature gate controlling feature visibility before GA.
-->

**Responsible SIGs**:

<!-- 
Primary SIG and optional additional SIGs responsible for the feature.

Use the `/sig $SIG` command to associate the primary SIG with the enhancement.
-->

Primary SIG:
/sig $SIG

Additional SIGs (optional):
/sig $SIG

**Reviewers**:

<!--
Handles of the reviewers who committed to reviewing this VEP's design, assigned by the
owning SIG after triage. Should match the VEP's front matter `reviewers:` field.
-->

**Approvers**:

<!--
Handles of the SIG chairs/leads who committed to approving this VEP's design, assigned by
the owning SIG after triage. Should match the VEP's front matter `approvers:` field.
-->

**Enhancement link**:

<!-- 
Link to the merged enhancement.
For example: https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/10-dra-devices/vep.md.

If the enhancement is not merged yet, please link the VEP PR.
Once the VEP PR is merged, update this section to contain the merged enhancement.
-->

**Timeline**:

<!-- 
See https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more context, please include links to relevant PRs
-->

- [ ] Provisional
  - [ ] VEP PR: <!-- the initial VEP PR that got the proposal accepted for design discussion -->

- [ ] Alpha
  - Target:
  - [ ] VEP PRs: <!-- merged as `implementable` before VEP Freeze -->
  - [ ] Code PRs:
  - [ ] Docs PRs:

<!-- Uncomment these as you prepare the enhancement for the next stage
- [ ] Beta
  - Target:
  - [ ] VEP PRs:
  - [ ] Code PRs:
  - [ ] Docs PRs:

- [ ] GA
  - Target:
  - [ ] VEP PRs:
  - [ ] Code PRs:
  - [ ] Docs PRs:
-->

<!-- 
**Additional context**:
Add any other context about the feature here.
-->

<!--
Full example, filled in:

**Primary contact (assignee)**:

/assign jdoe

**Current Feature Stage**:

Alpha

**Proposal status**:

implementable

**Feature Gate**:

DeviceProviders

**Responsible SIGs**:

Primary SIG:
/sig compute

Additional SIGs (optional):
/sig network

**Reviewers**:

@reviewer-a, @reviewer-b

**Approvers**:

@sig-compute-chair

**Enhancement link**:

https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/123-device-providers/vep.md

**Timeline**:

- [x] Provisional
  - [x] VEP PR: #100

- [x] Alpha
  - Target: v1.10
  - [x] VEP PRs: #123
  - [x] Code PRs: #456, #460
  - [ ] Docs PRs: #789
-->

> [!IMPORTANT]
> Please keep this description up to date.
