# KubeVirt Enhancements Tracking and Backlog

This repository is used to manage KubeVirt Enhancement Proposals (
VEPs), emphasizing centralized prioritization and enhanced SIG involvement and collaboration.

## WHY

The process aims to focus the community's efforts on prioritized pull requests, increase the review bandwidth, and
ensure clear visibility of feature progress and associated issues. The relatively new split into SIGs requires defining
a common process to ensure synchronization between SIGs and uniformity of the project.

**Glossary**

- VEP: Virtualization Enhancement Proposal
- EF: Enhancement Freeze
- CF: Code Freeze
- RC: Release Candidate

## Process

1. **Visibility and Tracking**: The Author of VEP will open an issue to track their progress, maturity stages (alpha,
   beta, GA), list the associated bugs, and user feedback.
   See the [VEP issue template](https://github.com/kubevirt/enhancements/blob/main/.github/ISSUE_TEMPLATE/vep.md),
   each section should be filled.

2. **VEP Creation**: VEP authors will initiate proposals via PRs to the `kubevirt/enhancements` repository.
   See the [VEP template](https://github.com/kubevirt/enhancements/blob/main/veps/NNNN-vep-template/vep.md),
   each section needs to be filled even if the section doesn't apply to the VEP.

3. **SIG Review and Collaboration**: Although each VEP can have multiple target SIGs, it needs to have a single owning SIG.
   In case of a cross SIG feature, the most relevant SIG should be chosen.
   The owning SIG will ultimately decide if the VEP is accepted and whether it should be tracked for the upcoming release.  
   If so, the SIG will assign dedicated reviewers to oversee the proposal, collaborate with others SIGs as needed, and provide
   feedback or veto when necessary. The author and reviewers work towards merged VEP.

   Each SIG (Compute, Network, Storage) should sign-off on the VEP with an LGTM from a SIG representative (chair or approver),
   even if the proposal doesn't affect them. The sign-off works here as acknowledgment of the changes.

4. **Centralized Prioritization**: At the start of each release cycle, all accepted VEPs will be designated as the
   project’s priority, focusing community efforts on the associated pull requests. Acceptance will be based on community
   support for the VEP and a commitment from the VEP owner to implement the work before Code Freeze.

    - On the EF, approved VEPs will be announced to kubevirt-dev mailing list in order to draw attention to them.
    - Each week a release team will check tracked VEP in order to assure activity.

5. **Single source of truth**: Each VEP will be the authoritative reference for the associated feature. This aligns with
   the Kubernetes KEP process. It will ensure that each enhancement includes all the relevant information, including the
   design and the state.

### Responsibilities

#### VEP Owner

The VEP owner is responsible to update it as its development progresses, until it is fully mature (or deprecated).
In addition, they are encouraged to do the following:

- Talk about the design in the unconference sessions to bring everyone on the same page about the problem, use-cases/design etc...
- Join the relevant SIG calls for further discussions, and when seeking reviews.
- Ask for reviews on community calls if this is a cross-SIG VEP.

#### SIGs

The responsibility of the SIGs is to do their best to help ensure sure the VEP is implemented, not diverging, and that the
implementation is not lacking behind the VEP, following this non-exhaustive list meant as checklist:

1. After Code Freeze, SIGs need to go over a tracking issue and perform the following checklist:
   1. All PRs are merged into release branch
   2. Docs PR is merged (plan review ahead of release if only placeholder is opened)
   3. Verify that the Enhancement was implemented and doesn't need any update or exception
   4. Track any bugs
   5. Make sure the VEP's issue is tracking required PRs and Issues
2. Weekly check-in on progress of the Enhancement and its implementation
3. Coordinate SIGs, reviewers and approvers in order to progress the Enhancement

### Release check-ins

Both the release team and approvers of the VEPs are responsible for weekly check-ins, the outcomes of which will be posted on
the VEP's tracking issue. The following are the goals of the
check-ins:

1. Re-targeting of VEP - In case of implementation not converging, new blockers being discovered, pushback of community
   or withdrawal of an approver, the VEP may need to be re-targeted to a different release. In this case, the VEP needs
   to be updated with the new target and the SIGs should shift the focus on tracked VEPs.
   Re-targeting could also be rejection of the VEP completely in case it is not implementable.

2. Coordination - SIGs are responsible to ensure reviews are not lagging behind by more than a week.
   The release team makes sure there is always an active SIG representative.

### Labels

For easier management of the release and VEPs the following labels will be used:

1. SIG labels - Each SIG will have a label in order to sort which SIG is responsible for the VEP.
2. Target labels - There will be a label in order to target the VEP for release.

> [!NOTE]
> Acceptance of an enhancement doesn't guarantee that the feature will land in the current or later release.
> The process is collaborative effort between contributors and approvers.
> Features not landing in the release branch prior to CF will need to file for an exception,
> see [Exceptions](#exceptions)

## Deadlines

The particular deadlines are always changing based on the release and are published here: [kubevirt/sig-release](https://github.com/kubevirt/sig-release).
The following deadlines are important for the VEP:

1. VEP planning - at the beginning of every release cycle, each SIG would prioritize VEPs and decide which ones are being tracked for the upcoming release.
2. Enhancement Freeze - The deadline for this milestone is Alpha release of KubeVirt. See [kubevirt/sig-release/tree/main/releases](https://github.com/kubevirt/sig-release/tree/main/releases)
3. Code Freeze - This is tracked by each release [kubevirt/sig-release/tree/main/releases](https://github.com/kubevirt/sig-release/tree/main/releases)

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

## Exceptions

Exceptions are served for any edge case that is not specified in this document, by the release team/repository or within
the KubeVirt repository.
Typically, an exception would be asked to allow contributors to continue to working on VEP/PRs/code after the EF or CF
respectively.
Exceptions can be asked before the actual EF/CF.

**How to ask for exception?**  
A request for exception must be sent to the [kubevirt-dev](https://groups.google.com/forum/#!forum/kubevirt-dev)
mailing list, the following should not be missing:

1. Justification for exception
2. Additional time period that is required
3. In case of exception not being granted, what is the impact? (Think about graduation, maturity of the feature, user
   impact, etc.)

## Common Questions

**Do PRs need to be approved by VEP approvers?**  
No, it is the whole SIG responsibility to be approving their code. The approver should be aware of the VEP and approve
based on it. There a process to ensure this happens.

**What to do in case all PRs didn't make it before CF?**  
The author of the VEP needs to file for the exception [Exceptions](#exceptions). The outcome will be determined
individually based on context by maintainers.

**How to raise attention for my VEP?**  
Every SIG has a recurring meeting. The VEP owner is encouraged to join the meeting and introduce the VEP to the community.

**What if my VEP relates to more than one SIG?**  
VEPs can relate to multiple SIGs, but a single SIG should always own it. VEP owners can reach out to the SIG
that seems most relevant for them. The SIG will either own the VEP or suggest that another SIG would own it.

**As a VEP owner, can I change implementation and only then update the VEP?**  
While doing so is valid, it is discouraged. If an implementation was already merged but is ruled out as part
of the VEP update this might lead to reverts, and the VEP owner should be aware of this risk. It is always
recommended to merge a VEP update before starting to implement.
