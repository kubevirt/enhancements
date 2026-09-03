# VEP #449: Feature Gate Dependency Tracking

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: TBD
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/449)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Currently, KubeVirt feature gates have no way to express dependencies on other
feature gates. When a user enables a feature gate without also enabling its
dependencies, the feature silently breaks. Additionally, there is no mechanism
to prevent graduating a feature to GA while its dependencies are still in
Alpha or Beta. This VEP proposes adding dependency tracking to the feature
gate system.

## Motivation

Feature gates often have implicit dependencies on other feature gates. For
example, GraceIOVirtualization requires both IOMMUFDGate and
PCINUMAAwareTopologyEnabled to function correctly. Without dependency
tracking, users have no way to know this except by reading source code or
contacting the feature gate owner.

This creates two problems:
1. **Silent failures for users** — enabling a gate without its dependencies
   causes unexpected behavior with no clear error message.
2. **Unsafe graduations for contributors** — a feature can be promoted to GA
   while its dependencies are still in Alpha or Beta, meaning the stable
   feature depends on something that could be removed at any time.

Kubernetes already has dependency tracking in `component-base/featuregate`
with `AddDependencies()`, DFS cycle detection, stability ordering, and
runtime validation. KubeVirt has no equivalent.

## Goals

- Provide a mechanism to declare dependencies between feature gates
- Warn or block when a feature gate is enabled without its dependencies
- Prevent graduating a feature to GA when its dependencies are not yet GA
- Detect and reject cyclical dependencies at registration time

## Non Goals

- Automatically enabling dependencies when a gate is enabled (users must
  explicitly enable them)
- Tracking cross-project dependencies (e.g., Kubernetes feature gates)
- Changing the existing feature gate lifecycle process

## Definition of Users

- **Feature developers** — who need to declare dependencies when registering
  feature gates
- **Cluster administrators** — who need clear warnings when their feature gate
  configuration has unmet dependencies
- **Maintainers** — who need to ensure safe feature graduation

## User Stories

- As a cluster admin, I want to be told which dependencies are missing when I
  enable a feature gate, so I don't get silent failures.
- As a feature developer, I want to declare that my feature depends on other
  feature gates, so the system can validate configurations.
- As a maintainer, I want to prevent accidentally promoting a feature to GA
  when its dependencies are still in Alpha or Beta.
- As a contributor, I want cyclical dependencies to be detected and rejected
  at registration time.

## Repos

kubevirt/kubevirt

## Design

### Dependency Declaration

Add a `DependsOn` field to the existing `FeatureGate` struct in
`pkg/virt-config/featuregate/feature-gates.go`. The field holds a list of
feature gate constant names that must also be enabled for the gate to
function correctly.

Gate owners declare dependencies at registration time in `active.go` using
the existing feature gate constants (e.g., `featuregate.IOMMUFDGate`,
`featuregate.PCINUMAAwareTopologyEnabled`). Gates without dependencies
continue to work unchanged — `DependsOn` defaults to an empty slice.

### Validation at Registration Time

When gates are registered during `init()`, three checks run:

**1. Dependency existence check** — all gates listed in `DependsOn` must
be registered. If a dependency references an unknown gate name (e.g., due
to a typo), registration fails immediately.

**2. Stability ordering** — a feature cannot depend on something less
stable than itself. Stability levels from lowest to highest are:
Deprecated, Alpha, Beta, GA. For example, a Beta gate cannot depend on an
Alpha gate, and a GA gate cannot depend on a Beta gate. This prevents a
stable feature from depending on something that could be removed.

**3. DFS cycle detection** — the full dependency graph is traversed using
depth-first search to detect circular dependencies (e.g., A depends on B,
B depends on C, C depends on A). If a cycle is found, registration fails.

If any check fails, the virt-operator reconcile loop will surface the error
in the KubeVirt CR status conditions, preventing the invalid configuration
from being applied.

### Validation at Runtime (User Configuration)

When a user enables or disables feature gates via the KubeVirt CR
configuration, the **KubeVirt admission webhook** validates that all
dependencies of enabled gates are also enabled. If a gate is enabled but
one of its dependencies is not, the webhook rejects the configuration
update with a clear error message indicating which dependencies are missing.

In Alpha, violations produce **warnings** (logged but not blocking). In
Beta/GA, violations produce **errors** (the configuration change is
rejected by the admission webhook). This gives users time to fix
configurations before enforcement.

### Transitive Dependencies

Only direct dependencies are checked. If A depends on B and B depends on C,
enabling A only checks that B is enabled. B's own validation checks that C is
enabled. This avoids redundant checks.

### GA Graduation Check

When a gate's State is changed to GA, stability ordering ensures all its
dependencies are also GA. This is checked at registration time — if a
contributor submits a PR changing a gate to GA while a dependency is still
Beta, the build fails.

## API Examples

### Declaring a dependency

Gate owners use the existing feature gate constants when declaring
dependencies in `active.go`. For example, `GraceIOVirtualization` would
declare its dependency on `featuregate.IOMMUFDGate` and
`featuregate.PCINUMAAwareTopologyEnabled`.

### User config with missing dependency

If a user enables `GraceIOVirtualization` in their KubeVirt CR without
enabling `IOMMUFDGate`, the admission webhook returns:

`GraceIOVirtualization is enabled but depends on IOMMUFDGate which is not enabled`

### Cycle detection error

If a developer accidentally creates a circular dependency in the code,
the virt-operator reconcile loop surfaces the error:

`cycle detected with feature gate IOMMUFDGate`

## Alternatives

### Alternative 1: Documentation only

Document dependencies in VEPs and code comments instead of enforcing in code.

**Rejected because:** Documentation gets outdated. Code enforcement catches
problems automatically. The GA graduation check cannot be done with
documentation alone.

### Alternative 2: Separate dependency map (like Kubernetes)

Store dependencies in a separate `map[string][]string` via an
`AddDependencies()` function instead of a struct field.

**Considered but not chosen because:** KubeVirt's gate system is simpler than
Kubernetes — no versioned specs, no emulation versions, no lock-to-default.
All gates register in a single `init()` function, so ordering is not a
problem. A struct field keeps everything about a gate in one place and is
easier to read.

### Alternative 3: Hard block from day one

Reject configurations with missing dependencies immediately.

**Not chosen because:** Existing clusters may have configs that work despite
missing dependencies. Starting with warnings gives users time to fix their
configs before enforcement in Beta/GA.

## Scalability

No scalability issues expected. The dependency graph has ~50 nodes (feature
gates) and a small number of edges. DFS cycle detection is O(V+E) — negligible
overhead at startup. Runtime validation adds one loop per gate enablement.

## Update/Rollback Compatibility

Fully backward compatible. Existing feature gate configurations continue to
work unchanged. Dependencies are additive — gates without declared dependencies
behave exactly as before. The `DependsOn` field defaults to an empty slice.

## Functional Testing Approach

- Unit tests for dependency validation (missing dependencies detected)
- Unit tests for DFS cycle detection (cycles caught, diamonds allowed)
- Unit tests for stability ordering (GA depending on Alpha rejected)
- Unit tests for runtime validation (warnings/errors on missing dependencies)
- Integration tests for end-to-end config validation if applicable

## Implementation History

(Will be updated as implementation progresses)

## Graduation Requirements

### Alpha

- [ ] Feature gate guards all code changes
- [ ] DependsOn field added to FeatureGate struct
- [ ] DFS cycle detection at registration time
- [ ] Stability ordering validation
- [ ] Runtime dependency validation with warnings
- [ ] Unit tests for all above
- [ ] Initial dependency declarations for known dependencies

### Beta

#### On-By-Default Readiness

- [ ] Runtime validation upgraded from warnings to errors
- [ ] All active feature gates reviewed for dependencies
- [ ] Proven reliable in CI
- [ ] No open bugs

### GA

- [ ] No option to disable dependency checking
- [ ] All gates have documented and declared dependencies
- [ ] Used in production environments
