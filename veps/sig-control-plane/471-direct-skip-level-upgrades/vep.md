# VEP: Direct (skip-level) upgrades between target releases

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/471)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt is consumed by downstream distributions that require direct upgrade
paths spanning multiple KubeVirt minor releases. Some distributions space their
extended-support releases N+3 minor versions apart, meaning a direct upgrade
skips two intermediate KubeVirt releases.

This VEP defines the direct upgrade use case for KubeVirt, establishes the
v1.11.0 → v1.14.0 window as the first formal target-to-target upgrade span,
and specifies the per-release CI coverage and implementation requirements needed
to demonstrate that direct upgrades are safe. It builds on the policy established
in [VEP #309](https://github.com/kubevirt/enhancements/issues/309) (CRD field
deprecation and removal policy) and the test infrastructure introduced in
[VEP #371](https://github.com/kubevirt/enhancements/issues/371) (envtest
framework).

## Motivation

### The direct upgrade requirement

Downstream distributions package KubeVirt into operators with extended-support
release cadences. These distributions designate specific KubeVirt releases as
target releases — versions that must support a direct upgrade from the previous
target release, skipping all intermediate releases. The first formal
target-to-target window is:

| KubeVirt | Role |
|---|---|
| 1.11 | N — starting target release; skip-level machinery must be present |
| 1.12 | Skipped during direct upgrade |
| 1.13 | Skipped during direct upgrade |
| 1.14 | N+3 — target release |

Downstream distributions require two upgrade orderings to be supported:

- **Operator-first**: KubeVirt 1.14 must work correctly when installed on a
  platform still running 1.11-era infrastructure (N−3 backward reach), so
  distributions can upgrade the operator before the underlying platform.
- **Platform-first**: KubeVirt 1.11 must remain functional after the underlying
  platform advances to the 1.14-era level (N+3 forward reach), so distributions
  can advance the platform before upgrading the operator.

Both orderings require that no stored CRD data is lost across the jump and that
intermediate operator versions are not required as waypoints.

The upstream KubeVirt project must provide guarantees at the correct level of
abstraction so that downstream distributions can build skip-level upgrade edges
without carrying their own cumulative migration logic.

### What "safe" means

An upgrade from N to N+3 is safe if:

1. No stored CRD data is silently lost — all deprecated fields with structured
   replacements are migrated before schema changes are applied (per VEP #309)
2. Workloads that were running at N continue to function correctly at N+3
   without manual intervention
3. The upgrade can be validated by an upstream CI path that tests the N →
   N+3 jump directly, without stepping through intermediate releases

### Why upstream CI is the critical gap

The primary risk in the v1.11 → v1.14 window is not that migration logic will
be absent, but that it will be added, tested only against sequential upgrades,
and then silently broken by intermediate changes before v1.14 ships. Without a
CI path that exercises the direct v1.11.0 → v1.14.0 upgrade, regressions will
be found in downstream distribution testing — at which point fixing them
requires upstream changes under release pressure.

Dan Kenigsberg (upstream KubeVirt) has noted the importance of this: "We'd need
CI — hopefully right in upstream — to ensure upgradability from a two-years-old
kubevirt." The envtest framework from VEP #371 provides the mechanism; this VEP
establishes the requirement.

### Relationship to VEP #309 and VEP #371

This VEP does not redefine the deprecation policy or the test infrastructure.
It uses them:

- **VEP #309** defines *which* fields must be migrated, *when* migration logic
  must exist, and *how* it must be structured (cumulative, handling objects from
  N)
- **VEP #371** defines *how* upgrade tests are written and executed (envtest
  suite, real kube-apiserver + etcd, controllers in-process)
- **This VEP** defines *what* must be demonstrated across the v1.11.0–v1.14.0
  window: the per-release CI coverage targets and implementation milestones that
  collectively prove the direct upgrade path is safe

## Goals

- Formally designate v1.11 as N and v1.14 as N+3 in the upstream KubeVirt
  community
- Define per-release CI coverage requirements for the v1.11.0–v1.14.0 window
- Require that a direct v1.11.0 → v1.14.0 upgrade test exists in upstream CI
  before v1.14.0 ships
- Provide a community-visible tracking point for all work that feeds into the
  N → N+3 direct upgrade guarantee

## Non-goals

- Define the CRD field deprecation policy (VEP #309)
- Define the envtest/upgrade test framework (VEP #371)
- Support direct upgrades between non-target releases (e.g. v1.12 → v1.14)
- Support direct upgrades spanning more than one target release gap (e.g.
  v1.11 → v1.17)
- Define or constrain downstream distribution end-to-end testing strategy
- Implement Kubernetes compatibility-version emulation in the KubeVirt operator
  — API emulation/ratcheting is the downstream platform's responsibility
- Support rollback across a skip-level jump when breaking changes are present

## Definition of Users

- **KubeVirt API authors**: Need per-release guidance on when cumulative
  migration logic is required and which fields are in scope for a given window
- **virt-operator maintainers**: Need to know what migration logic N+3 must
  carry and how it must be sequenced relative to CRD schema application
- **SIG leads and approvers**: Need clear criteria for blocking a field removal
  that would break the direct upgrade path
- **Downstream distribution maintainers**: Need a community-verified direct
  upgrade path they can expose as a supported update edge

## User Stories

- As a downstream distribution maintainer, I want the v1.11.0 → v1.14.0
  upgrade to be tested upstream in CI so that I can expose a direct N → N+3
  update edge without running a parallel audit of every intermediate CRD change

- As a KubeVirt API author, I want a community-designated N+3 release so that
  I know whether a field deprecated in v1.12 must have migration logic in v1.14
  or can wait for a later release

- As a SIG lead reviewing a field removal in v1.12 or v1.13, I want a checklist
  item that confirms the removal does not break the v1.11.0 → v1.14.0 direct
  upgrade path

- As a virt-operator maintainer, I want a clear specification of which
  migrations N+3 = v1.14 must carry, so that the cumulative migration
  requirement from VEP #309 is actionable

## Repos

- kubevirt/kubevirt
- kubevirt/api
- kubevirt/enhancements

## Design

### Target release designation

This VEP formally designates:

| Target release | KubeVirt version |
|---|---|
| N | 1.11 |
| N+3 | 1.14 |

N+3 = v1.14 should be confirmed during the v1.12 planning cycle at the latest,
giving API authors two release cycles of lead time for deprecation and migration
planning.

### Update ordering

Operator-first (1.11→1.14 before advancing the underlying platform) is the
preferred ordering for downstream distributions. Platform-first is also a
supported path. Both orderings require the upstream direct upgrade test to pass
— it is the prerequisite evidence that downstream distributions need to declare
either path safe.

### Per-release CI coverage requirements

#### v1.10 (enabling work — current cycle)

The envtest framework (VEP #371) must be capable of running upgrade tests. The
initial coverage target is v1.8.0 → v1.10.0 (sequential, two hops), validating
the framework itself rather than the direct upgrade path.

Deliverables:
- Envtest suite capable of loading a KubeVirt CRD schema at version A and
  upgrading to version B with controllers in-process
- At least one field migration test covering an at-risk transition in the
  1.8–1.11 window (e.g. `preferredUseEfi` → `preferredEfi`)
- Audit of all at-risk field transitions in the 1.8–1.11 window (see VEP #309
  for the initial list)

#### v1.11 (N — starting target release)

The cumulative migration logic required by VEP #309 must be present in
virt-operator. The direct v1.8.0 → v1.11.0 upgrade path must be tested in CI.

Deliverables:
- virt-operator carries cumulative migration for all fields deprecated in the
  v1.8–v1.10 window (VEP #309 compliance)
- Envtest upgrade test: create objects at v1.8.0 schema state, upgrade directly
  to v1.11.0, verify all deprecated fields are migrated
- N+3 = v1.14 formally designated by the community
- Audit of at-risk field transitions in the v1.11–v1.14 window begun

#### v1.12

Field removals that had cumulative migration in v1.11 may now proceed (per
VEP #309: removal is after the target release that carries migration).

Deliverables:
- Envtest upgrade test: v1.11.0 → v1.12.0 (sequential) in CI
- Any new deprecations in v1.12 tracked against the N+3 = v1.14 deadline

#### v1.13

Deliverables:
- Envtest upgrade test: v1.11.0 → v1.13.0 (skip one release) in CI
- Cumulative migration for fields deprecated in v1.11–v1.12 planned for v1.14

#### v1.14 (N+3 — target release)

The direct v1.11.0 → v1.14.0 upgrade path must be tested in upstream CI before
v1.14.0 ships. This is the gating criterion for N+3 graduation.

Deliverables:
- virt-operator carries cumulative migration for all fields deprecated in the
  v1.11–v1.13 window (VEP #309 compliance for N → N+3 span)
- Envtest upgrade test: create objects at v1.11.0 schema state, upgrade directly
  to v1.14.0, verify all deprecated fields are migrated
- Gating: v1.14.0 release is blocked if the v1.11.0 → v1.14.0 upgrade test
  does not pass in CI

### Interaction with virt-operator upgrade sequencing

VEP #309 requires that cumulative migration logic runs *before* the updated CRD
schema is applied during upgrade. This ordering is critical: if the schema is
applied first, deprecated fields are pruned before migration can populate the
replacements.

The existing virt-operator upgrade sequence already applies CRDs as part of
resource generation. The migration step must be inserted before CRD application.
The exact ordering mechanism (e.g. a pre-upgrade reconciliation phase) is an
implementation detail left to the implementing PR, but the ordering invariant
must be preserved and tested in the envtest suite.

## Functional Testing Approach

All upgrade tests run in the envtest suite (VEP #371):

- **State creation**: Create representative objects (preferences, snapshots,
  clones, pools) with fields in the state they would have at N — including
  deprecated fields set, replacement fields absent
- **Direct upgrade simulation**: Load N+3 CRD schemas and run
  virt-operator migration logic in-process against the stored objects
- **Assertion**: Verify that replacement fields are populated correctly and no
  data is lost; verify that the storage version migrator's subsequent pruning
  of deprecated fields does not lose data

The v1.11.0 → v1.14.0 direct upgrade test is the primary gate for N+3. It
must cover all field transitions identified in the v1.11–v1.14 audit.

## Implementation History

- 2026-05: Initial investigation and demo of storage version migration data
  loss risks.
- 2026-06: VEP #309 proposed; VEP #371 (envtest framework) proposed.
- 2026-07: VEP #309 reworked to adopt target-release-anchored deprecation model.
- 2026-09: Downstream distributions confirmed v1.11–v1.14 as the first formal
  target-to-target window. This VEP filed to track the direct upgrade use case.

## Graduation Requirements

### Alpha (v1.11)

- [ ] N = v1.11 and N+3 = v1.14 designated by the community
- [ ] VEP #309 alpha adopted (field deprecation policy in place)
- [ ] VEP #371 alpha available (envtest framework can run upgrade tests)
- [ ] Envtest upgrade test for v1.8.0 → v1.11.0 (direct) passing in CI
- [ ] virt-operator carries cumulative migration for all at-risk fields in
      the v1.8–v1.10 window
- [ ] Audit of at-risk transitions in the v1.11–v1.14 window complete

### Beta (v1.13)

- [ ] Envtest upgrade tests for v1.11.0 → v1.12.0 and v1.11.0 → v1.13.0
      passing in CI
- [ ] All field deprecations in the v1.11–v1.13 window tracked with N+3
      migration planned
- [ ] virt-operator migration framework extended to cover v1.11–v1.13
      deprecations

### GA (v1.14)

- [ ] Envtest upgrade test for v1.11.0 → v1.14.0 (direct) passing in CI
      and gating the v1.14.0 release
- [ ] virt-operator carries cumulative migration for all fields deprecated
      in the v1.11–v1.13 window
- [ ] No field removals in v1.12–v1.14 have bypassed the v1.11.0 → v1.14.0
      upgrade test
- [ ] N+6 designated or process initiated for the following target release window
