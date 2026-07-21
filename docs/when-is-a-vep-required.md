# When Is a VEP Required?

## Summary

Not every change to KubeVirt requires a VEP (Virtualization Enhancement
Proposal). This document provides guidelines for contributors and
reviewers to determine when a VEP is needed.

## A VEP Is Required When a Change

1. **Adds or modifies a public API** - new CRD fields, new CRDs, adding
   new possible field values (e.g. enums), or changes to existing API
   semantics or defaults.
2. **Introduces new user-facing behavior that requires a feature gate** -
   new VM/VMI behaviors, new configuration options, new status
   conditions, or anything a user could notice without reading the code.
3. **Adds new behavior under an existing feature gate** - this requires a
   new VEP or an update to the existing one.
4. **Changes component architecture** - adding a new sidecar, splitting a
   controller, introducing a new daemon, or adding new inter-component
   communication (e.g. a new gRPC service).
5. **Requires coordination across multiple PRs or SIGs** due to
   complexity and scope.

## A VEP Is NOT Required For

1. Bug fixes, unless they are extraordinarily complex.
2. Test improvements or new test coverage.
3. Documentation changes.
4. Dependency bumps.
5. Build, CI, or infrastructure changes.
6. Trivial code cleanups (typos, dead code removal).

## Gray Area - Demands Judgment

1. **Large internal refactors** - no API change, but high blast radius
   and cross-team coordination needs.
2. **New internal mechanisms** (e.g. a new webhook, a new internal
   controller) that don't surface in the API but shape future design.
3. **Changes to operator/deployment topology** that don't affect the user
   API but affect how KubeVirt is installed or upgraded.

## Rationale

The cost of creating a VEP is deliberately low - a short document with
motivation, goals, and a high-level design. When in doubt about whether
a change warrants a VEP, prefer writing one. This applies even for
features that pre-date the VEP process or for changes that seem small
in scope. It is better to invest a small upfront effort in a VEP and
get early feedback than to implement a change and be surprised by
objections during review. A lightweight VEP ensures the change is
visible to the community, reviewed by the right SIGs, and tracked
through the feature lifecycle.

## Note

These are guidelines, not rigid rules. Approvers and maintainers may
require a VEP at their discretion for any change they consider
significant enough to warrant one.
