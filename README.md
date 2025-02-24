# KubeVirt Enhancements Tracking and Backlog

This repository is currently Work In Progress but will eventually be used to manage KubeVirt Enhancement Proposals (VEPs), emphasizing centralized prioritization and enhanced SIG involvement and collaboration.

## Process (DRAFT)

1. **VEP Creation**: VEP authors will initiate proposals via PRs to the `kubevirt/enhancements` repository. [Design proposal template](https://github.com/kubevirt/community/blob/main/design-proposals/proposal-template.md)
2. **SIG Review and Collaboration**: Each VEP will have a target SIG, and the SIG will assign a dedicated reviewer to oversee the proposal, collaborate with other SIGs as needed, and provide feedback or veto when necessary.
3. **Centralized Prioritization**: At the start of each release cycle, all accepted VEPs will be designated as the project’s priority, focusing community efforts on the associated pull requests. Acceptance will be based on community support and a commitment to implementation.
4. **Visibility and Tracking**: The Author of an accepted VEPs will open an issue to track their progress, maturity stages (alpha, beta, GA), list the associated bugs, and user feedback
5. **Single source of truth**: Each VEP will be the authoritative reference for the associated feature. This aligns with the Kubernetes KEP process. It will ensure that each enhancement includes all the relevant information, including the design and the state.

The VEP owner is responsible to update it as its development progresses, until it is fully mature (or deprecated).

**NOTE**
Acceptance of enhancement doesn't guarantee that the feature will land in the current or later release. The process is collaborative effort between contributor/s and Approvers. Features not landing to release branch prior to FF will be reverted based on their state (GA/Beta/Alpha, Off/On by default) that reflects how harmful keeping the code is to the system.

## Approvers responsibility

1. After Feature Freeze, approvers needs to go over issue and perform the following checklist:
   1. All PRs are merged into release branch
   2. Docs PR is merged (plan review ahead of release if only placeholder is opened)
   3. Verify that Enhancement was implemented and doesn't need any update
   4. Track any bugs
2. Weekly check-in on progress of Enhancement and its implementation
3. Coordinate SIGs, reviewers and approvers in order to progress the Enhancement

## Implementation Phases

1. **Alpha Rollout (v1.5 Cycle)**:
   - [x] Create the `kubevirt/enhancements` repository.
   - [x] Introduce a template for VEP submissions.
   - [x] Migrate one or two active designs to test the process.
   - [ ] Refine the process based on feedback from initial VEPs.
2. **Full Rollout (v1.6 Cycle)**:
   - [ ] Transition all enhancements to the new process.
   - [ ] Empower SIGs to take increased ownership while maintaining central prioritization.
3. **Future Considerations**:
   - [ ] Gradual reduction in centralized coordination as SIGs become self-sufficient.
