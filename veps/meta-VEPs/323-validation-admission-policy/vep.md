# VEP #323: Validation Admission Policy

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: N/A (policy change, no kubevirt version dependency)
- This VEP targets beta for version: N/A
- This VEP targets GA for version: N/A

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- ~[ ] (R) Target version is explicitly mentioned and approved~
- ~[ ] (R) Graduation criteria filled~

## Overview

This meta-VEP captures the outcome of the [validation policy discussion](https://github.com/kubevirt/kubevirt/issues/16708).

KubeVirt currently relies on admission webhooks for nearly all validation logic.
This VEP proposes a three-tier validation policy that uses the right mechanism for each class of validation,
with [ValidatingAdmissionPolicies](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/) (VAPs)
as the primary mechanism for most validations.

## Motivation

KubeVirt's validation is almost entirely implemented via admission webhooks.
While webhooks are flexible, they come with significant drawbacks:

- **Operational burden**: webhooks require a running, reachable service. If virt-api is unavailable,
  all mutating and validating operations on KubeVirt resources are blocked.
- **Complexity**: webhook code is imperative Go, which is harder to review, test, and reason about
  than declarative validation rules.
- **Scalability**: every admission request requires a network round-trip to the webhook service.
- **Maintenance cost**: as KubeVirt grows, the webhook codebase grows with it, making it harder to maintain.

Kubernetes introduced [ValidatingAdmissionPolicies](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/)
(GA since Kubernetes 1.30) as a declarative, in-process alternative to admission webhooks.
VAPs use [CEL](https://kubernetes.io/docs/reference/using-api/cel/) expressions evaluated directly
by the API server - no network hop, no external service dependency.

Additionally, kubebuilder validation markers (e.g., `+kubebuilder:validation:Minimum`, `+kubebuilder:validation:Enum`)
offer OpenAPI schema-level validation for simple, invariant field constraints.

This VEP establishes when to use each mechanism.

## Goals

- Establish a clear, documented policy for choosing between kubebuilder markers, VAPs, and webhooks.
- Migrate existing webhook validations to VAPs iteratively over time.
- Reduce KubeVirt's dependency on admission webhooks for validation.

## Non Goals

- Define a specific timeline or release target for migrating all existing validations.
  Migration should be done iteratively with no pressure.
- Discuss the specifics of individual validations (each migration can be handled in its own PR).
- Replace mutating admission webhooks - this VEP only covers validation.

## Definition of Users

- **KubeVirt developers**: who implement and review validation logic.
- **KubeVirt reviewers/approvers**: who review PRs that add or modify validation.
- **Cluster administrators**: who manage KubeVirt deployments and may need to understand validation behavior.

## User Stories

- As a KubeVirt developer, I want a clear guideline for where to implement validation logic
  so that I don't have to make ad-hoc decisions on each PR.
- As a KubeVirt reviewer, I want a documented policy to point to when reviewing PRs
  that add validation in the wrong place.
- As a cluster administrator, I want validations to work even if the virt-api webhook service
  is temporarily unavailable.

## Repos

- kubevirt/kubevirt

## Design

### Three-tier validation policy

Validation in KubeVirt should use the following mechanisms, in order of preference:

#### 1. Kubebuilder markers (OpenAPI schema validation)

Use kubebuilder validation markers **only** for simple, single-field constraints
that are certain to never change. Examples:

- A string field that must not be empty
- An integer field that must not be negative

These constraints are baked into the CRD's OpenAPI schema. Once published,
they are effectively permanent - changing them is a breaking API change.
Therefore, use them conservatively and only for true invariants.

**Do not** use kubebuilder markers for constraints that may evolve over time. Examples of what to avoid:

- `+kubebuilder:validation:Minimum` / `+kubebuilder:validation:Maximum` - acceptable ranges tend to change as
  requirements evolve (e.g., a port field might later need to allow 0 for auto-assignment).
- `+kubebuilder:validation:Enum` - enum values almost always grow over time. Adding a new value
  to a kubebuilder enum is a breaking schema change.

Use VAPs for these validations instead.

**Note on existing markers**: KubeVirt already has kubebuilder validation markers that do not meet
the above criteria. Removing them would be a breaking API change, so they will remain as-is.
This policy applies to new markers going forward.

#### 2. ValidatingAdmissionPolicies (VAPs)

Use VAPs for **most** validations. This includes:

- Multi-field validations (e.g., "if field A is set, field B must also be set")
- Complex single-field validations that may evolve over time
- Cross-field consistency checks
- Validations that depend on the object's current state (`oldSelf` vs `self`)
- Immutability checks
- Any validation that is not a true invariant

VAPs are declarative, evaluated in-process by the API server, and can be updated
without code changes. They are the right default for nearly all validation logic.

#### 3. Admission webhooks

Use webhooks **only** as a last resort, when VAPs cannot express the validation. Examples:

- Validations that require API calls to other resources (e.g., checking if a feature gate is enabled)
- Validations that require complex procedural logic not expressible in CEL
- Validations with dependencies on external state

When a webhook is necessary, the PR should document why a VAP is insufficient.

### VAP lifecycle management

VAPs are managed by virt-operator and continuously reconciled, just like other
operator-managed resources (RBAC, Deployments, etc.).

This means:
- VAPs are created and updated by virt-operator as part of the normal reconciliation loop.
- Manual modifications to operator-managed VAPs are reverted on the next reconciliation cycle.
- Upgrades naturally apply VAP changes as part of the operator's desired-state reconciliation.

If an administrator needs to customize validation behavior, the proper mechanism is
a configuration knob in the KubeVirt CR, not direct VAP modification.
The operator then uses these configuration values when generating the VAPs.
This ensures admin intent is captured declaratively and survives upgrades.

The specifics of which configuration knobs to expose are left to individual feature PRs.

### Migration strategy

Existing webhook validations should be migrated to VAPs iteratively.
There is no fixed timeline or release target for completing the migration.

When migrating a validation:
1. Implement the VAP equivalent.
2. Add functional tests for the VAP-based validation.
3. Remove the corresponding webhook validation logic.

Each migration can be handled in its own PR. There is no need to migrate all validations at once.

## API Examples

No API changes are proposed. VAPs are standard Kubernetes resources deployed by virt-operator.

## Alternatives

### VAP reconciliation strategies

This VEP proposes continuous reconciliation by virt-operator (option 1 below).
Two alternatives were considered:

#### Option 1: Continuous reconciliation (chosen)

virt-operator creates and continuously reconciles all VAPs, reverting any manual changes.
Admin customization goes through the KubeVirt CR.

**Pros:**
- Upgrades are clean - operator ensures VAPs match the desired state for the current version.
- Consistent with how KubeVirt manages other resources (RBAC, Deployments).
- No drift between expected and actual validation state.
- Consistent with the ecosystem - Gatekeeper, Kyverno, and OpenShift operators all use this pattern.

**Cons:**
- Administrators cannot directly modify VAPs for quick, ad-hoc changes.
- Every customization requires a KubeVirt CR knob, which means code changes and a release cycle.

#### Option 2: No reconciliation

VAPs are applied during install and upgrades but are not continuously reconciled.
Administrators are free to modify them between upgrades.

**Pros:**
- Maximum admin flexibility - any VAP can be modified at any time.
- Simpler operator logic (no reconciliation loop for VAPs).

**Cons:**
- Upgrades become risky. If the operator needs to update a VAP that the admin has modified,
  there is no clean way to merge the changes. The operator must either overwrite (losing admin changes)
  or skip (leaving outdated validation logic).
- Interrupted upgrades may leave VAPs in an inconsistent state with no self-healing mechanism.
- Validation drift is invisible - there is no signal that the running VAPs differ from
  what the current KubeVirt version expects.
- No project in the Kubernetes ecosystem uses this pattern for operator-managed VAPs.

#### Option 3: Opt-out annotation

virt-operator reconciles by default, but skips VAPs annotated with
`kubevirt.io/managed: "false"`. Administrators can opt out specific VAPs from reconciliation.

**Pros:**
- Reconciliation by default for the common case.
- Administrators can customize specific VAPs when needed.

**Cons:**
- Opted-out VAPs face the same upgrade problem as option 2 - the administrator
  must manually review and merge upstream changes on upgrade, or risk running
  outdated validation logic.
- Provides a false sense of control - most administrators will not track upstream
  VAP changes across releases.
- Adds complexity to the operator (annotation checking, partial reconciliation).
- Creates a support burden - debugging validation issues requires checking which
  VAPs are managed and which are not.

### Kubebuilder markers for client-side dry-run

An alternative to the conservative use of kubebuilder markers proposed above is to
use them more broadly, including for validations that may change over time,
in order to enable client-side dry-run (`kubectl apply --dry-run=client`).

This was rejected because kubebuilder markers are baked into the CRD's OpenAPI schema.
Once published, changing them is a breaking API change - clients that depend on the schema
will break. Server-side dry-run (`kubectl apply --dry-run=server`) activates all validation
including VAPs, and should be used instead.

Kubebuilder markers should therefore be reserved for truly immutable field constraints
where we are confident the validation will never change.

### Webhooks only (status quo)

Continuing to use admission webhooks for all validation logic.

**Pros:**
- No migration effort.
- Full flexibility (arbitrary Go code).

**Cons:**
- All the drawbacks listed in [Motivation](#motivation) persist and grow with the codebase.
- KubeVirt does not benefit from the operational improvements that VAPs provide.

## Scalability

VAPs are evaluated in-process by the API server. This eliminates the network round-trip
required by webhooks, improving validation latency and reducing the load on virt-api.

No scalability issues are expected.

## Update/Rollback Compatibility

VAPs are continuously reconciled by virt-operator. On upgrade, the operator applies
the new version's VAPs as part of its normal reconciliation loop. On rollback,
the previous version's operator restores its own VAPs.

No update/rollback compatibility issues are expected.

## Functional Testing Approach

VAP-based validations should be tested via functional tests that exercise the API server's
admission path, verifying that invalid requests are rejected with the expected error messages.

Existing webhook validation tests can be reused with minimal changes, since the test
assertions (create/update a resource and expect rejection) remain the same regardless
of the underlying mechanism.

Unit testing of CEL expressions is available using a vendored CEL evaluation library
introduced in [#17790](https://github.com/kubevirt/kubevirt/pull/17790).

## Implementation History

