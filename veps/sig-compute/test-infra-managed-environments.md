# VEP 356: Test Infrastructure Abstraction for Managed KubeVirt Environments

## VEP Status Metadata

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Graduation criteria filled

This VEP does not follow the standard Alpha/Beta/GA lifecycle because it modifies test infrastructure only,
not KubeVirt production code. See [Completion Criteria](#completion-criteria) for graduation requirements.

## Overview

KubeVirt end-to-end tests frequently modify the KubeVirt Custom Resource (CR) to toggle feature gates, adjust
migration configuration, and alter other cluster-wide settings. These modifications work in standalone KubeVirt
deployments, but fail in environments where KubeVirt is managed by an overarching operator such as the
HyperConverged Cluster Operator (HCO). In managed environments, the managing operator owns the KubeVirt CR and
reconciles it, reverting any direct modifications made by tests.

This VEP proposes introducing a set of small, focused Go interfaces that abstract how tests mutate KubeVirt
configuration. A default implementation preserves today's behavior (direct KubeVirt CR patching), while alternative
implementations can target the managing operator's API instead. All interface definitions and implementations will
reside in `tests/libkubevirt/config/`, colocated with the existing test helpers they wrap.

## Motivation

KubeVirt's test suite contains serial tests that directly patch the KubeVirt CR via strategic merge patch or JSON
patch to configure the system under test. The key functions driving this behavior are:

- `UpdateKubeVirtConfigValueAndWait()` -- strategic merge patches the entire `spec.configuration` of the KubeVirt
  CR, then waits for propagation to virt-controller, virt-api, and virt-handler. Used by ~35 direct call sites
  across the test suite, with additional indirect usage through wrapper functions.
- `EnableFeatureGate()` / `DisableFeatureGate()` -- manipulate the `FeatureGates` and `DisabledFeatureGates` slices
  on the KubeVirt CR.
- `RegisterKubevirtConfigChange()` -- applies targeted JSON patches to the KubeVirt CR using functional options
  (`KvChangeOption`).
- Migration-specific helpers like `SetDedicatedMigrationNetwork()` that modify migration configuration fields.

This approach assumes exclusive ownership of the KubeVirt CR, which is true in a vanilla KubeVirt deployment but
not in managed environments.

In managed environments (e.g., OpenShift Virtualization), HCO acts as a meta-operator that deploys and manages
KubeVirt, CDI, and other operands. The HyperConverged CR is the single source of truth -- HCO copies configuration
values from it to the operand CRs. If a test directly modifies the KubeVirt CR, HCO's reconciliation loop detects
the out-of-band change and reverts it, incrementing the `kubevirt_hco_out_of_band_modifications_total` metric. This
makes tests that modify KubeVirt configuration unreliable or entirely non-functional in these environments.

Currently, the test framework has **no awareness of managed environments** -- there are no skip conditions,
decorators, or alternative code paths for HCO-managed clusters. This gap prevents KubeVirt tests from being reused
on managed platforms, forcing consumers to maintain separate test forks or skip large portions of the test suite.

Enabling tests to run correctly on managed environments improves the overall testing coverage of KubeVirt as users
actually deploy it, and reduces the maintenance burden of separate test forks.

## Goals

- Define small, focused Go interfaces for each category of KubeVirt configuration mutation performed by tests
  (feature gates, migration configuration, general KubeVirt configuration, etc.).
- Provide a **default implementation** that patches the KubeVirt CR directly, preserving current behavior with
  zero regressions.
- Define an **HCO implementation** that patches the HyperConverged CR instead of the KubeVirt CR directly.
- Place all interface definitions and implementations in `tests/libkubevirt/config/`, colocated with the existing
  test helpers they wrap. All consumers of these interfaces are internal to the test suite.
- Migrate existing test utilities (`EnableFeatureGate`, `DisableFeatureGate`, `UpdateKubeVirtConfigValueAndWait`,
  `RegisterKubevirtConfigChange`, etc.) to use the new interfaces.

## Non Goals

- Modifying or extending HCO itself. The managed implementation will use HCO's existing API (patching the
  HyperConverged CR).
- Replacing or refactoring the KubeVirt CR API. The interfaces abstract the *test infrastructure*, not the
  product API.
- Automatically detecting whether the environment is managed. The implementation to use will be selected at compile
  time via Go build tags, not auto-detection. Auto-detection may be considered as a future convenience enhancement
  but is explicitly deferred from this proposal.
- Covering all possible managing operators. The initial managed implementation targets HCO specifically. The
  interface design allows additional implementations in the future.
- Modifying non-KubeVirt-CR resources like `MigrationPolicy` CRDs, which are separate custom resources not owned
  by HCO's reconciliation.

## Definition of Users

- **KubeVirt test developers**: Write and maintain KubeVirt e2e tests. They use the new interfaces transparently --
  the default implementation preserves their current workflow.
- **Managed platform test consumers**: Teams running KubeVirt tests on managed platforms (e.g., OpenShift
  Virtualization on OCP). They provide a managed implementation and configure the test suite to use it.

## User Stories

### As a KubeVirt test developer
I want to write tests that toggle feature gates and modify KubeVirt configuration without worrying about the
deployment topology, so that my tests work on both standalone and managed environments.

### As a managed platform test consumer
I want to run KubeVirt e2e tests against my HCO-managed cluster by swapping in a managed implementation, so that
I can validate KubeVirt behavior as real users experience it without maintaining a separate test fork.

## Repos

- `kubevirt/kubevirt` -- primary repo; interfaces, default implementation, and test migration
- `kubevirt/hyperconverged-cluster-operator` -- reference for HCO API; no code changes required in HCO itself

## Design

### Interface Definitions

The design introduces a set of focused interfaces, each covering a specific category of KubeVirt configuration
mutation. All interfaces and implementations are colocated in `tests/libkubevirt/config/`, close to the existing
test helpers they wrap.

```go
// FeatureGateManager controls KubeVirt feature gate activation.
type FeatureGateManager interface {
    EnableFeatureGate(feature string) error
    DisableFeatureGate(feature string) error
}

// KubeVirtConfigManager manages KubeVirt cluster-wide configuration.
// UpdateConfiguration uses merge semantics (not replace): only fields
// explicitly set in the provided config are applied. Zero-valued fields
// are ignored, matching the behavior of the existing strategic merge
// patch approach.
type KubeVirtConfigManager interface {
    UpdateConfiguration(config v1.KubeVirtConfiguration) error
}

// MigrationConfigManager manages migration-specific configuration.
type MigrationConfigManager interface {
    SetMigrationConfiguration(config *v1.MigrationConfiguration) error
    SetDedicatedMigrationNetwork(nadName string) error
    ClearDedicatedMigrationNetwork() error
}
```

A composite interface may be provided for convenience:

```go
// ConfigManager combines all configuration management capabilities.
type ConfigManager interface {
    FeatureGateManager
    KubeVirtConfigManager
    MigrationConfigManager
}
```

### Default Implementation (Direct KubeVirt CR Patching)

The default implementation wraps the existing logic from `tests/libkubevirt/config/` and
`tests/testsuite/kubevirtresource.go`. It patches the KubeVirt CR directly and waits for propagation:

```go
type directKubeVirtConfig struct {
    client kubecli.KubevirtClient
}

func (d *directKubeVirtConfig) EnableFeatureGate(feature string) error {
    // Same logic as current config.EnableFeatureGate():
    // Read KubeVirt CR, append to FeatureGates, remove from
    // DisabledFeatureGates, strategic merge patch, wait for propagation.
}

func (d *directKubeVirtConfig) UpdateConfiguration(
    config v1.KubeVirtConfiguration,
) error {
    // Same logic as current UpdateKubeVirtConfigValueAndWait():
    // Strategic merge patch spec.configuration, EnsureKubevirtReady(),
    // poll /healthz for config-resource-version propagation.
}

// ... other methods follow the same pattern
```

### Managed Implementation (HCO API Patching)

The managed implementation patches the HyperConverged CR instead of the KubeVirt CR. It maps KubeVirt
configuration fields to their HCO equivalents:

```go
type managedKubeVirtConfig struct {
    client    dynamic.Interface  // or a typed HCO client
    namespace string
    hcName    string
}

func (m *managedKubeVirtConfig) EnableFeatureGate(feature string) error {
    // Patch the HyperConverged CR's spec.featureGates list to add
    // the feature gate. HCO reconciliation propagates the change
    // to the KubeVirt CR.
}

func (m *managedKubeVirtConfig) UpdateConfiguration(
    config v1.KubeVirtConfiguration,
) error {
    // Map KubeVirt configuration fields to their HyperConverged CR
    // equivalents under spec.virtualization.*, spec.security.*, etc.
    // Patch the HyperConverged CR.
    // Wait for HCO reconciliation to propagate changes to KubeVirt CR.
    // Then wait for KubeVirt component propagation (healthz polling).
}

// ... other methods follow the same mapping pattern
```

The managed implementation uses the `dynamic` client or an unstructured client to avoid importing HCO types as a
compile-time dependency. The managed implementation targets the HCO **v1 API** (`hco.kubevirt.io/v1`), which uses
`spec.virtualization.*` paths and list-of-objects feature gates. The HCO v1beta1 API (with different field
structures) is not targeted.

### Two-Phase Wait Logic for Managed Implementation

When patching through HCO, configuration changes propagate through two reconciliation layers:

1. **HCO reconciliation**: HCO detects the HyperConverged CR change and updates the KubeVirt CR. This typically
   completes within seconds but may take longer under load or during upgrades.
2. **KubeVirt component propagation**: virt-controller, virt-api, and virt-handler detect the KubeVirt CR change
   and update their in-memory configuration. The existing `waitForConfigToBePropagated` logic polls `/healthz`
   endpoints with a 10-second timeout per component.

The managed implementation must:
1. Patch the HyperConverged CR.
2. Poll the KubeVirt CR until the expected configuration change appears (confirming HCO has reconciled).
3. Read the KubeVirt CR's `ResourceVersion` after HCO reconciliation.
4. Run the existing propagation wait logic using that `ResourceVersion`.

The total timeout for the managed path will be longer than the direct path to account for HCO reconciliation delay.

### Field Mapping: KubeVirt CR to HyperConverged CR

The managed implementation must map between the two APIs. Key mappings:

| KubeVirt CR Path | HyperConverged CR Path |
|---|---|
| `spec.configuration.developerConfiguration.featureGates` | `spec.featureGates` (list of `{name, state}` objects) |
| `spec.configuration.migrationConfiguration.*` | `spec.virtualization.liveMigrationConfig.*` |
| `spec.configuration.migrationConfiguration.network` (set) | `spec.virtualization.liveMigrationConfig.network` |
| `spec.configuration.migrationConfiguration.network` (clear) | Remove from HyperConverged CR |
| `spec.configuration.vmRolloutStrategy` | Hard-coded by HCO to `LiveUpdate` |
| `spec.workloadUpdateStrategy` | `spec.virtualization.workloadUpdateStrategy` |
| `spec.configuration.evictionStrategy` | `spec.virtualization.evictionStrategy` |
| `spec.configuration.developerConfiguration.cpuAllocationRatio` | `spec.virtualization.vmiCPUAllocationRatio` |

Note: `spec.workloadUpdateStrategy` is on `KubeVirtSpec`, not under `spec.configuration`.

KubeVirt feature gates are simple strings in a list, while HCO feature gates are objects with `name` and optional
`state` fields. The naming convention also differs (KubeVirt PascalCase vs HCO camelCase). Some KubeVirt gates
are hard-coded by HCO as always-enabled, some have no HCO equivalent, and some HCO gates implicitly enable
multiple KubeVirt gates. The HCO implementation must maintain a mapping table and define error handling for
unmapped gates.

### Unmappable Configuration Fields

Not all fields in `v1.KubeVirtConfiguration` have HCO equivalents. The following fields are set by
`AdjustKubeVirtResource()` or by individual tests but have no corresponding HCO API path:

| KubeVirt Configuration Field | HCO Equivalent |
|---|---|
| `seccompConfiguration` | None |
| `developerConfiguration.logVerbosity` | None |
| `tlsConfiguration` | None |
| `networkConfiguration` (binding plugins) | None |
| `supportedGuestAgentVersions` (deprecated) | None |

The HCO implementation must handle these unmappable fields gracefully. The recommended approach is to return an
error if a test attempts to set them through the HCO path. Tests that require these fields may need to be skipped
or annotated for managed environments.

Additionally, some test files directly patch the KubeVirt CR bypassing all helper abstractions -- modifying fields
like `spec.uninstallStrategy`, `spec.certificateRotateStrategy`, `spec.infra`, `spec.workloads`, and
`metadata.annotations`. These patterns are out of scope for the initial interface design since they are confined
to operator-specific tests that verify KubeVirt operator behavior itself.

### Implementation Selection

The active implementation is selected at compile time via Go build tags:

```go
// manager_default.go — included in default builds (no build tag)
//go:build !managed_hco

package config

func newConfigManager(client kubecli.KubevirtClient) ConfigManager {
    return newDirectKubeVirtConfig(client)
}
```

```go
// manager_hco.go — included only when built with -tags managed_hco
//go:build managed_hco

package config

func newConfigManager(client kubecli.KubevirtClient) ConfigManager {
    ns := getEnvOrDefault("HCO_NAMESPACE", "kubevirt-hyperconverged")
    name := getEnvOrDefault("HCO_NAME", "kubevirt-hyperconverged")
    return newManagedKubeVirtConfig(client, ns, name)
}
```

By default, `go test` compiles the direct implementation. Managed platform builds add `-tags managed_hco` to their
test invocation to compile in the HCO implementation instead. The managed code does not exist in the default binary
at all, keeping the test infrastructure clean. The HCO CR name and namespace default to `kubevirt-hyperconverged`
but can be overridden via `HCO_NAMESPACE` and `HCO_NAME` environment variables for deployment-specific
configuration.

Build tags are the standard Go mechanism for conditional compilation and make the implementation choice explicit
and auditable in build scripts.

### Package Layout

All interfaces and implementations are colocated in `tests/libkubevirt/config/`, alongside the existing test
helpers they wrap:

```
tests/libkubevirt/config/
├── interfaces.go        # Interface definitions
├── direct.go            # Default KubeVirt CR implementation
├── managed_hco.go       # HCO managed implementation
├── manager_default.go   # Build-tag selected factory (default)
├── manager_hco.go       # Build-tag selected factory (HCO)
└── ... (existing files)
```

The HCO implementation uses `k8s.io/client-go/dynamic` for HCO interactions, avoiding any dependency on HCO
Go types.

### Suite-Level Initialization

The test suite has an `--apply-default-e2e-configuration` flag (defined in `tests/flags/flags.go`) that controls
whether `AdjustKubeVirtResource()` modifies the KubeVirt CR at suite startup to apply a baseline set of feature
gates and configuration for the full e2e test matrix. On managed environments this flag is typically set to `false`
because HCO would revert the changes.

With the new `ConfigManager`, `AdjustKubeVirtResource()` will route its baseline configuration through the active
implementation. When the managed implementation is selected, it applies the baseline through the HyperConverged CR,
enabling the full e2e test matrix to run on managed clusters without disabling
`--apply-default-e2e-configuration`. Similarly, `RestoreKubeVirtResource()` in `AfterEach` will use `ConfigManager`
to restore configuration.

Note that `AdjustKubeVirtResource()` patches the entire `spec` (not just `spec.configuration`), including
`spec.certificateRotationStrategy`. Fields outside `spec.configuration` that have no HCO equivalent (e.g.,
`certificateRotationStrategy` maps to HCO's `spec.security.certConfig` with a different structure) will require
explicit mapping or be excluded from the managed path with appropriate documentation.

### Test Migration

Existing call sites are migrated by converting the current helper functions into thin wrappers that delegate to the
global `ConfigManager`. This approach eliminates the need to modify individual test files:

1. `EnableFeatureGate` / `DisableFeatureGate` in `tests/libkubevirt/config/featuregate.go` delegate to
   `ConfigManager.EnableFeatureGate()` / `ConfigManager.DisableFeatureGate()`.
2. `UpdateKubeVirtConfigValueAndWait` in `tests/libkubevirt/config/kvconfig.go` delegates to
   `ConfigManager.UpdateConfiguration()`.
3. `SetDedicatedMigrationNetwork` / `ClearDedicatedMigrationNetwork` in `tests/libmigration/migration.go` delegate
   to `ConfigManager.SetDedicatedMigrationNetwork()` / `ConfigManager.ClearDedicatedMigrationNetwork()`.

Note: The existing functions have return types (`*v1.KubeVirt`) that differ from the proposed interface methods
(which return `error`). The wrapper functions will preserve the existing return types for backward compatibility by
reading the KubeVirt CR after the `ConfigManager` call completes.

The `RegisterKubevirtConfigChange` function with `KvChangeOption` functional options (`WithWorkloadUpdateStrategy`,
`WithVMRolloutStrategy`, `WithNetBindingPluginIfNotPresent`) is not directly covered by the proposed interfaces.
These call sites (approximately 16 across the test suite) will continue using the existing
`RegisterKubevirtConfigChange` mechanism initially. A future iteration may introduce additional interface methods
to cover these patterns.

## API Examples

### Standalone test (unchanged experience)

```go
// Tests continue to use the same high-level helpers.
// The underlying implementation is selected via build tag at compile time.
var _ = Describe("My feature", Serial, func() {
    It("should work when feature gate is enabled", func() {
        config.EnableFeatureGate("MyFeature")
        DeferCleanup(config.DisableFeatureGate, "MyFeature")

        // ... test logic ...
    })
})
```

### Direct usage of the interface (advanced)

```go
mgr := config.NewConfigManager(virtClient)

err := mgr.EnableFeatureGate("LiveMigration")
Expect(err).NotTo(HaveOccurred())

err = mgr.SetMigrationConfiguration(&v1.MigrationConfiguration{
    ParallelMigrationsPerCluster: pointer.P(uint32(10)),
    BandwidthPerMigration:        resource.MustParse("1Gi"),
})
Expect(err).NotTo(HaveOccurred())
```

## Alternatives

### Alternative 1: JSON Patch Annotations on the HyperConverged CR

HCO supports `kubevirt.kubevirt.io/jsonpatch` annotations that allow bypassing its reconciliation for specific
fields. Tests could use these annotations instead of a managed implementation.

**Rejected because**: JSON Patch annotations produce a `TaintedConfiguration` status condition and trigger the
`UnsupportedHCOModification` alert. They are documented as "particularly dangerous when upgrading" and are
explicitly not the intended API for regular configuration changes. Using them in automated tests would mask real
configuration issues and does not represent how users interact with managed KubeVirt deployments.

### Alternative 2: Disable HCO Reconciliation During Tests

Pause or scale down HCO during test runs that modify the KubeVirt CR, then restore it afterward.

**Rejected because**: This changes the system under test. The goal is to validate KubeVirt behavior *as managed
users experience it*, including the managed operator's reconciliation. Disabling HCO defeats this purpose and may
mask bugs in the interaction between KubeVirt and its managing operator.

### Alternative 3: Decorate and Skip KubeVirt CR Mutation Tests

Add a Ginkgo decorator (e.g., `decorators.MutatesKubeVirtCR`) to all tests that modify the KubeVirt CR and skip
them entirely on managed environments. A label filter such as `--label-filter='!MutatesKubeVirtCR'` would exclude
these tests when running against HCO-managed clusters.

**Rejected because**: This approach reduces test coverage rather than extending it. Tests that modify KubeVirt
configuration exercise important code paths (feature gate toggling, migration config changes, config propagation)
that are equally relevant on managed platforms. Skipping them means managed environments are only validated with
the default configuration, missing regressions that surface under non-default settings. The goal of this VEP is to
*run* these tests on managed environments, not avoid them.

## Scalability

This proposal does not introduce new runtime components or affect KubeVirt's production scalability. The impact is
limited to the test infrastructure:

- The interface abstraction adds a single level of indirection with no measurable performance cost.
- The managed implementation makes one API call to the HyperConverged CR instead of one to the KubeVirt CR -- the
  same number of API interactions.
- HCO's reconciliation loop propagates changes within seconds, comparable to the existing wait-for-propagation
  logic.

## Update/Rollback Compatibility

This proposal does not affect KubeVirt's runtime behavior, API, or upgrade path. All changes are confined to the
test infrastructure.

- **Standalone KubeVirt tests**: The default implementation preserves identical behavior. No regressions expected.
- **Managed platform consumers**: Can adopt the HCO implementation at their own pace by building with
  `-tags managed_hco`.

## Functional Testing Approach

- **Unit tests**: Each interface implementation (direct and managed) will have unit tests using fake clients to
  verify correct patch generation and API interactions.
- **Standalone CI**: Runs with the default (direct) implementation. Any regression in existing test behavior
  indicates a bug in the migration, not the design.
- **Managed CI**: A sig-compute CI lane builds and runs the KubeVirt test suite with `-tags managed_hco` against
  an HCO-managed cluster, validating that the managed implementation correctly propagates configuration through
  HCO. Coverage may expand to additional SIG lanes once the sig-compute lane is stable.
- **Integration smoke test**: A small set of tests (e.g., enable/disable a feature gate, modify migration config)
  will be specifically verified on both direct and managed implementations in CI before the full migration.

## Implementation Phases

1. **Phase 1**: Introduce interface definitions and both implementations (direct and HCO) in
   `tests/libkubevirt/config/`. Create a global `ConfigManager` instance initialized in the test suite's
   `BeforeSuite`.
2. **Phase 2**: Convert existing helper functions (`EnableFeatureGate`, `DisableFeatureGate`,
   `UpdateKubeVirtConfigValueAndWait`, `SetDedicatedMigrationNetwork`, `ClearDedicatedMigrationNetwork`) into thin
   wrappers that delegate to the global `ConfigManager`. This preserves all existing call-site signatures and
   requires no individual test file changes.
3. **Phase 3**: Route `AdjustKubeVirtResource()` and `RestoreKubeVirtResource()` through `ConfigManager` for the
   configuration fields it covers.
4. **Phase 4**: Address remaining `RegisterKubevirtConfigChange` / `KvChangeOption` call sites, either by extending
   the interface or providing managed-aware `KvChangeOption` implementations.

Each phase can be a separate PR to keep reviews manageable.

## Implementation History

<!--
To be filled as work progresses.
-->

## Feature lifecycle Phases

This VEP does not modify KubeVirt production code and therefore does not require a feature gate or the standard
Alpha/Beta/GA lifecycle. All changes are confined to test infrastructure -- no runtime behavior, product API, or
KubeVirt CR schema is affected.

### Completion Criteria

- [ ] Interface definitions merged in `tests/libkubevirt/config/`
- [ ] Default (direct) implementation merged, wrapping existing logic
- [ ] HCO implementation merged
- [ ] Existing helper functions (`EnableFeatureGate`, `DisableFeatureGate`, `UpdateKubeVirtConfigValueAndWait`,
  `SetDedicatedMigrationNetwork`, `ClearDedicatedMigrationNetwork`) converted to thin wrappers delegating to
  `ConfigManager`
- [ ] Unit tests for both implementations
- [ ] Feature gate name mapping table (KubeVirt PascalCase to HCO camelCase) implemented and tested
- [ ] Error handling defined for unmapped feature gates and configuration fields
- [ ] `AdjustKubeVirtResource()` and `RestoreKubeVirtResource()` routed through `ConfigManager`
- [ ] `RegisterKubevirtConfigChange`/`KvChangeOption` call sites addressed
- [ ] All configuration-mutating test utilities use the new interfaces
- [ ] Documentation on how to use the `managed_hco` build tag
