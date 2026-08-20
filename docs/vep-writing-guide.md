# Writing a Good VEP

This guide complements [`vep.md`](../veps/NNNN-vep-template/vep.md), the VEP template. The
template defines which sections a VEP must have; this document is about how to write each one
well.

This guide is expected to evolve. Propose changes via PR when a review surfaces a recurring
issue not yet captured here.

## Document Structure

- Keep the template's section set and order intact. Every section must be addressed — write
  "N/A" with a one-line reason if it doesn't apply.
- `vep.md` is the single source of truth: all feature-level content (motivation, design, API,
  graduation) lives in it. Never split a proposal across multiple content-bearing files, and
  never redirect a reader elsewhere to understand what the feature is.
- As the VEP progresses (Alpha → Beta → GA), edit `vep.md` in place. Do not create a
  phase-specific file (`foo-beta.md`, `foo-part2.md`); record the change in Implementation
  History instead.
- Extra files in the VEP directory are for material not required to understand the feature —
  raw research, a POC report, benchmark data, an appendix. If a reader needs the extra file to
  understand the feature or its design, it belongs in `vep.md` instead.

## Overview

- A short summary that lets a reader grasp the VEP's core topic and what problem it resolves —
  not a repeat of Motivation or Design.
- Avoid describing a specific mechanism or solution (the "how"); that belongs in Design.

## Motivation

- Explain why this matters now: the cost of the status quo and the benefit to users — not just
  "we need X."
- Back the claim with evidence where possible (bug reports, user requests, precedent) rather
  than asserting need.
- Don't restate Goals as prose.

## Goals

- State an outcome (what/why) only, never a mechanism (how) — the mechanism belongs in Design.
- Not phased — don't write "Alpha: X, Beta: Y" here; stage scope belongs in Graduation
  Requirements.
- Each goal should be measurable: it must be possible to say whether it was met.

## Non Goals

- Justify each exclusion; "less work" isn't a reason on its own. A smaller scope is fine if
  what's left is still viable and answers the real user need (an MVP); it's not fine if what's
  left only works once the missing pieces are filled in later — whether by a follow-up VEP or
  by updating this same VEP toward Beta/GA. Goals and Non Goals describe the feature's end
  result, not an intermediate step. For example, excluding live-migration support needs strong
  justification on its own — it's core to KubeVirt, and a feature that doesn't account for it
  isn't viable just because it's smaller in scope.
- A Non Goal usually just means out of scope for this VEP, with no claim about the future
  either way. If you're actively rejecting an idea rather than merely leaving it out, say why
  explicitly.

## Definition of Users

- Identify who is affected — a role, not a scenario. Save the concrete story for User Stories.

## User Stories

- Concrete, scenario-based ("As an admin managing X, I need Y so that Z"), not abstract
  capability lists.
- Multiple stories are fine when they show genuinely different needs or personas; don't pad
  with near-identical variations of the same story.
- Stop at what the user needs/experiences — implementation detail belongs in Design.

## Design

- This is where "how" lives; anything mechanism-level found elsewhere belongs here.
- Describe what components do and how they interact — boundaries, interfaces, data — not the
  code that implements it. A precise sequence of calls or a wire format that resolves real
  ambiguity is still design; a step-by-step code walkthrough is not.
- Avoid file and function names unless one is literally an API field or value — the code
  changes constantly, and the VEP should describe a high-level abstraction that survives
  refactoring.
- When naming an API field or value, consider whether a generic term would future-proof it
  against other backing mechanisms (e.g. `podStatus` instead of `multusStatus`, if the field
  could plausibly apply beyond Multus). This doesn't mean avoiding the feature's own subject
  technology — a VEP about `passt` still says `passt`.
- Before introducing a new API mechanism, check whether an existing one already covers the same
  need — reuse or extend it instead of adding a parallel path. If some duplication is genuinely
  unavoidable (e.g. mirroring a field between the VM and VMI to resolve a lifecycle constraint),
  justify it explicitly.
- State the reasoning behind a choice when a reviewer could reasonably ask "why this way?";
  skip it for trivial choices.
- Check prior art (Kubernetes extension mechanisms, related KEPs) before designing something
  novel.
- Cross-reference instead of repeating content from another section.
- Include risks and mitigations covering security, UX, and operational impact. Name the
  specific risk and how it's mitigated; don't write a generic "there are risks" statement.

## API Examples

- Concrete request/response or CR examples, not prose description of fields.
- An edge/error case is encouraged when it clarifies the contract, but not mandatory — the
  normal case is the minimum bar.

## Alternatives

- An option must address the same goals as the chosen approach to count as an alternative; one
  that only serves something already declared a Non Goal is out of scope, not an alternative.
  For an option that does qualify, don't dismiss it by citing a Non Goal — that defines what
  this VEP isn't attempting, not why a working alternative is worse. Reject it on its own
  merits (cost, risk, complexity, fit).
- Trading off a secondary goal doesn't disqualify an alternative; judge it against the primary
  goal(s) it does address.
- Ground the comparison in reference data/implementations from comparable projects where
  possible.

## Scalability

- Quantify: call type, estimated frequency/throughput, originating component, for each new or
  changed API interaction.
- Call out any new list/watch behavior against existing resources explicitly. Avoiding this is
  a common source of unreviewed scale regressions.

## Update/Rollback Compatibility

- Upgrades happen via live-migration: VMs launched under an older version keep running while
  components upgrade underneath them. Address what happens to a VM that predates the change —
  must it be migrated/restarted to pick up new behavior, or must the new code correctly handle
  state/domain XML produced by the older version?
- Cover version skew between coexisting components during a rolling upgrade, not just an
  idealized atomic upgrade.
- KubeVirt doesn't support rollback (downgrading a cluster to an older version) today — say so
  plainly rather than describing theoretical support.
- Still worth reasoning through what a full rollback would require if it were supported: would
  it leave state or behavior an older component couldn't handle? This can surface real risks
  even absent formal rollback support.
- Separately — and distinct from a cluster-version rollback — address feature discontinuation
  while still Alpha/Beta: if the feature or its Feature Gate is dropped, what happens to
  VMs/resources already created under it? This matters most at Beta, where the Feature Gate
  defaults to enabled, so users may be affected without having explicitly opted in.

## Functional Testing Approach

- Be precise about test tiers used (unit, integration, functional/e2e) and which claim each
  one covers; don't loosely call something "e2e."
- Unit tests are a baseline requirement for new code — call out and justify any exception
  explicitly; don't rely on higher-level tests to cover what a unit test should.
- For functional/e2e coverage, assert user-facing/API-level behavior, not internals (e.g.
  domain XML) — internals violate layering.
- Lead with the real happy-path scenario before edge cases.
- This section verifies stated functionality; it isn't where new functionality gets defined.
- All testing must show a sustained non-flaky signal over time, not just "tests exist."

## Implementation History

`vep.md` is edited in place as the feature moves through Alpha/Beta/GA rather than rewritten
from scratch — this section is what keeps that evolution visible over time.

- Terse, dated log of actual milestones (PR merged, alpha shipped, graduation approved).
- Not a forward plan, and not narrative about why the document itself changed shape.

## Graduation Requirements

- Each stage's criteria must be objective and independently verifiable (a metric, test, or
  documented behavior) — no "stable enough."
- Functional tests: encouraged, not required, for Alpha; required for Beta and GA. Alpha's
  relaxed bar applies only inside the feature's own Feature-Gated path; it must not introduce
  risk to existing, already-stable functionality.
- Skipping Alpha, or omitting a Feature Gate, needs strong explicit justification.
- Beta must resolve all known functional/security/monitoring gaps — nothing user-facing left
  open.
- `On-By-Default Readiness` (Beta) exists because Beta Feature Gates default to on: state
  concretely what must be true for that to be safe (known gaps closed, monitoring in place,
  a way to disable it if it misbehaves) — not just "it's ready."
- GA must leave no functional/security/testing item outstanding, including full removal of the
  Feature Gate.
