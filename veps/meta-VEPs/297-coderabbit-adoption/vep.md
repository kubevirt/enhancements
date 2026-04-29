# VEP #297: Adopting CodeRabbit for AI-Assisted Code Review

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: N/A (tooling change, no kubevirt version dependency)
- This VEP targets beta for version: N/A
- This VEP targets GA for version: N/A

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- ~[ ] (R) Alpha target version is explicitly mentioned and approved~
- ~[ ] (R) Beta target version is explicitly mentioned and approved~
- ~[ ] (R) GA target version is explicitly mentioned and approved~

## Overview

This is a meta-VEP to introduce [CodeRabbit](https://www.coderabbit.ai/) as an AI-assisted code
review tool for the kubevirt organization.

The term meta-VEP means this VEP is not for a new kubevirt feature nor an API change.
Little to no changes are required to kubevirt source code.
The deliverables are a GitHub App installation and per-repository configuration files.

kubevirt/kubevirt and kubevirt/enhancements serve as the initial adopting repositories.
Any other repository in the org may join at any stage. Each repository decides independently
whether to retire the current tool, Sourcery, in favour of CodeRabbit.

## Motivation

KubeVirt's review burden goes beyond catching bugs. Reviewers routinely enforce documented
conventions — API field naming, JSON tags, godoc requirements, VEP template compliance, test
helper patterns — that a well-configured tool should flag automatically. Today this depends
entirely on human reviewers remembering to check policies that are already written down, on every
PR, every time.

The root cause is that the current tool, [Sourcery](https://sourcery.ai/), operates on the diff
alone and has no mechanism for ingesting project documentation, contribution guidelines, or
architecture decisions. It cannot know what KubeVirt's conventions are because it has never
been given them.

CodeRabbit addresses this directly. It uses natural language to consume project knowledge —
documentation, guidelines, architecture decisions — and to interact with contributors in plain
English. The result is a tool that can reason about whether a change aligns with documented
project intent, not just whether it is structurally sound.

Beyond documentation awareness, CodeRabbit also addresses two additional gaps in the current tooling:

- Sourcery was originally built for Python. Analysis of other languages, including Go (the primary
  language of kubevirt/kubevirt), is handled by the LLM layer alone with no language-specific rules
  or concurrency modeling.
- Sourcery reviews changed files in isolation and cannot reason about cross-package implications of
  a change.

See [`appendix-coderabbit-vs-sourcery.md`](appendix-coderabbit-vs-sourcery.md) for the full
side-by-side comparison. The comparison is based on public documentation from both vendors and
one independent benchmark; the community is encouraged to validate claims against current
documentation before adoption.

## Goals

- Introduce CodeRabbit as an AI code review tool for the kubevirt organization.
- Enable documentation-driven, semantically-aware review by feeding contribution guidelines and
  architecture documentation into the knowledge base.
- Provide a reference `.coderabbit.yaml` configuration that any repository can adapt and adopt.
- Allow each repository to decide independently whether to retire Sourcery.

## Non Goals

- Mandating CodeRabbit adoption across the organization.
- Replacing human code review.
- Allowing CodeRabbit to block merges or interact with Prow's approval flow; all CodeRabbit
  output is advisory and has no effect on the `/lgtm` or `/approve` workflow.
- Changing the VEP authoring process or review workflow.
- Configuring CodeRabbit for repositories outside the initial adopters; this VEP establishes the
  approach and reference configuration only.

## Definition of Users

- **KubeVirt contributors** submitting pull requests who receive AI-generated review feedback.
- **Reviewers and approvers** whose routine checks on guidelines and policies are assisted by the tool.
- **SIG leads** evaluating tooling effectiveness and deciding on wider adoption.
- **Project maintainers** of other repositories who want to adopt the same approach.

## User Stories

- As a reviewer, I want the tool to detect when a refactor silently changes reachable state
  transitions in the VM lifecycle — for example, when a condition change makes a previously
  unreachable error path reachable — because unit tests only cover paths the author thought to
  write, and a linter cannot reason about state machine semantics.
- As an API reviewer, I want the tool to flag when a change to a CRD field violates documented
  backward-compatibility guarantees — such as narrowing an accepted value range, changing the
  semantics of an existing field, or removing an optional field that clients may depend on — since
  these changes compile and pass tests but break existing users silently.
- As a reviewer of complex controller logic, I want the tool to identify when a goroutine accesses
  shared state without holding the expected lock, or when a channel operation could block
  indefinitely given a plausible ordering of events — because race detectors require the race to
  be exercised at runtime, and human reviewers routinely miss these patterns in large controller
  reconciliation loops.
- As a maintainer of another repository, I want a reference configuration I can adapt to bring
  documentation-driven review to my project without starting from scratch.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) — initial adopter
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) — initial adopter
- Other kubevirt repositories — opt-in at their own discretion

## Design

### GitHub App Installation

The CodeRabbit GitHub App is installed on the kubevirt organization, scoped initially to
kubevirt/kubevirt and kubevirt/enhancements. CodeRabbit is free for public and open-source
repositories with no seat limit.

### Per-Repository Configuration

Each participating repository places a `.coderabbit.yaml` file at its root. This file is a
**living document**: it is expected to evolve continuously as contributor feedback is gathered
and the knowledge base matures. Progression to more assertive settings happens only when the
community is satisfied with the precision and relevance of reviews.

Initial baseline (low-friction, advisory only):

```yaml
language: en-US
reviews:
  profile: chill
  request_changes_workflow: false
knowledge_base:
  issues:
    scope: local
tools:
  golangci-lint:
    enabled: true
```

Path-specific instructions are added for directories or file types that carry documented
conventions (e.g., API types, VEP documents, test helpers). Instructions are written in
plain English and reference the relevant sections of contribution guidelines or architecture
documents directly.

### Knowledge Base

The knowledge base grounds CodeRabbit's reviews in project-specific context.

**kubevirt/kubevirt:**
- Contribution guidelines (`CONTRIBUTING.md`) and coding conventions
- Architecture documentation covering module boundaries and design patterns
- Security-relevant policies

**kubevirt/enhancements:**
- VEP template and governance policies
- `CLAUDE.md` (auto-detected by CodeRabbit) providing VEP authoring guidance

The knowledge base is seeded at Alpha and refined iteratively. Additional documents are added
as contributors identify areas where review quality would benefit from more context.

### Feedback and Iteration

Each assessment cycle consists of:
1. Collecting structured feedback from contributors and reviewers (signal-to-noise ratio,
   missed issues, unhelpful comments).
2. Updating `.coderabbit.yaml` path instructions and knowledge base content accordingly.
3. Re-assessing before the next cycle.

There is no fixed cadence imposed by this VEP; each repository's maintainers decide the
assessment rhythm appropriate for their contributor volume.

## API Examples

No API changes. The following is an illustrative `.coderabbit.yaml` snippet showing
path-specific instructions for kubevirt/kubevirt:

```yaml
language: en-US
reviews:
  profile: chill
  request_changes_workflow: false
  path_instructions:
    - path: "staging/src/kubevirt.io/api/**"
      instructions: >
        Review API types against the kubevirt API conventions documented in CONTRIBUTING.md.
        Flag any fields missing JSON tags, validation markers, or godoc comments.
    - path: "tests/**"
      instructions: >
        Check that new tests follow the existing test helper patterns.
        Flag tests that duplicate existing helpers or that use raw client calls
        where a typed helper exists.
knowledge_base:
  issues:
    scope: local
tools:
  golangci-lint:
    enabled: true
```

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Review noise / fatigue** | Initial configuration uses the `chill` profile and advisory-only mode. `request_changes_workflow` is disabled so CodeRabbit cannot block merges. Configuration is iterated based on contributor feedback before any tightening. |
| **Privacy / data handling** | All kubevirt repositories are public and open-source; no proprietary code is exposed. CodeRabbit's SaaS uses zero data retention post-review and does not train models on submitted code. |
| **Vendor dependence** | Path instructions and knowledge base content in `.coderabbit.yaml` are plain text and not proprietary to CodeRabbit. The configuration can be adapted for any future tool. Adoption is fully reversible by removing the GitHub App. |
| **Pricing model change** | CodeRabbit is currently free for public open-source repositories with no seat limit. Should that change, the community reassesses at that point; the rollback cost is removing a config file. |
| **Dual-tool confusion** | During the Alpha stage both Sourcery and CodeRabbit may be active on the same repository. Each repository's maintainers make an explicit decision on Sourcery before Beta graduation, eliminating the overlap. |
| **Interaction with Prow** | CodeRabbit comments are purely advisory GitHub review comments. They have no interaction with Prow plugins, Tide merge logic, or the `/lgtm` and `/approve` workflow. |

## Alternatives

**Sourcery** is the current tool and the primary evaluated alternative. It was found insufficient
for this codebase on three axes: language depth for Go, cross-file analysis, and documentation
ingestion. The full comparison is in [`appendix-coderabbit-vs-sourcery.md`](appendix-coderabbit-vs-sourcery.md).

## Scalability

No scalability concerns. CodeRabbit runs as a hosted SaaS service; review latency is independent
of repository size. Rate limit details for open-source repositories are documented at
[docs.coderabbit.ai/about/pricing](https://docs.coderabbit.ai/about/pricing) and should be
verified against the current plan before adoption.

## Update/Rollback Compatibility

Fully reversible. Removing the GitHub App from a repository or deleting `.coderabbit.yaml` stops
all CodeRabbit activity immediately. No changes are made to kubevirt source code, build system,
or CI configuration.

## Functional Testing Approach

- Open a test PR on each initial repository and verify CodeRabbit posts a review summary and
  inline comments.
- Validate that path-specific instructions are reflected in the review output.
- Confirm that golangci-lint findings are surfaced as CodeRabbit comments.
- Collect and act on contributor feedback iteratively across assessment cycles.

## Implementation History

Will be updated as implementation proceeds.

## Graduation Requirements

> Note: the stages below reflect **tooling maturity and adoption**, not feature stability in the
> traditional sense. Each stage represents a gradual assessment and integration checkpoint.
> The Alpha / Beta / GA labels follow VEP template convention; an equivalent framing is
> Trial / Evaluation / Adoption.

### Alpha

- [ ] CodeRabbit GitHub App installed on kubevirt/kubevirt and kubevirt/enhancements
- [ ] Initial `.coderabbit.yaml` merged in both repositories with low-friction, advisory-only settings
- [ ] Knowledge base seeded with contribution guidelines and architecture documentation
- [ ] First contributor feedback gathered and used to update configuration and knowledge base

Any other repository may join freely during this stage.

### Beta

- [ ] At least one full assessment cycle completed per participating repository
- [ ] Contributor feedback collected, acted on, and demonstrably reflected in updated configuration
      and knowledge base
- [ ] Signal-to-noise ratio assessed as acceptable by repository maintainers, evidenced by a
      majority of sampled CodeRabbit comments rated useful in a contributor survey or equivalent
      structured feedback mechanism
- [ ] Reference `.coderabbit.yaml` published and documented for other repositories to adopt
- [ ] Each participating repository has made an explicit decision on Sourcery

### GA

- [ ] Org-wide onboarding guidance published covering GitHub App installation, initial configuration,
      and knowledge base setup
- [ ] Configuration and context management practices documented so any repository can onboard and
      maintain CodeRabbit independently
- [ ] A written recommendation is recorded in the organization's tooling documentation naming
      CodeRabbit as the recommended AI-assisted review tool for repositories joining the org
