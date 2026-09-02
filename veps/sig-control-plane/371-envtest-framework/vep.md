# VEP #371: KubeVirt Integration Test Framework

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Introduce an integration test framework for KubeVirt that exercises
real controller logic end-to-end without deploying a Kubernetes cluster
or running virtual machines. The framework uses [envtest][] to run a
real kube-apiserver and etcd locally with KubeVirt CRDs installed and
runs real controllers in-process against this API server.

Inspired by [OpenStack Nova's functional test framework][nova-functional],
which has used the same pattern (real services, fake external
dependencies) for over a decade.

[nova-functional]: https://docs.openstack.org/nova/latest/contributor/testing.html#functional-tests
[envtest]: https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/envtest

## Motivation

KubeVirt currently has two testing layers:

1. **Unit tests** (`pkg/**/*_test.go`): Fast, isolated. Each controller
   tested with GoMock — Controller A's writes are not visible to
   Controller B.

2. **Functional tests** (`tests/`): Comprehensive but slow (4-hour timeout),
   resource-intensive, and prone to infrastructure flakiness.

There is no middle ground. Bugs from multi-controller interactions,
race conditions, or state machine transitions cannot be caught by unit
tests and are expensive to reproduce with functional tests.

A working proof of concept has been implemented in kubevirt/kubevirt
across a series of focused PRs (#18726–#18731): 20 tests across 8
categories, covering VM/VMI lifecycle, instancetype upgrades, webhook
admission, CRD validation, and regression cases (see
[Proof of Concept](./poc.md) for the per-PR breakdown, coverage matrix,
and benchmark data). The PoC demonstrates this gap with concrete
examples:

- [Issue #16719][]: RestartRequired condition never cleared after spec
  revert — requires real ControllerRevision lookup and multi-step
  reconciliation that unit tests mock away.
- [Issue #18396][]: Workload-update migrations race with virt-handler
  DaemonSet re-roll during upgrade — a timing race between controllers
  that functional tests catch only by luck (it's a flake).
- Instancetype ControllerRevision upgrades — 8 functional tests that exercise
  pure controller logic, migrated to envtest with a 13.4x speedup.
- [CRD field deprecation and removal][issue #309] — KubeVirt uses
  `NoneConverter` for all CRDs, so storage version migration can only
  prune fields, not transform them. When deprecated fields are
  replaced by structurally different fields (e.g., `preferredUseEfi`
  bool → `preferredEfi` struct), the old field is silently dropped
  during migration and the new field is left empty — permanent data
  loss. Skip-level upgrades compound this: every intermediate field
  removal is applied in a single migration. envtest can reproduce
  this because it runs a real kube-apiserver + etcd with real schema
  pruning, enabling tests that create objects under an old CRD schema,
  apply the upgraded schema, simulate the GET+PUT migration cycle,
  and assert that data is preserved or correctly converted.

[issue #16719]: https://github.com/kubevirt/kubevirt/issues/16719
[issue #18396]: https://github.com/kubevirt/kubevirt/issues/18396
[issue #309]: https://github.com/kubevirt/enhancements/issues/309

## Goals

- Catch bugs arising from multi-controller interactions, race
  conditions, and state-machine transitions that unit tests cannot
  reach and functional tests catch only expensively or by luck.
- Exercise controller logic against authentic API-server semantics for
  CRD resources — OpenAPI validation, status subresource enforcement,
  and resourceVersion behaviour.
- Validate CRD declarative validation (structural schemas and CEL
  rules).
- Detect silent data loss from CRD schema evolution and storage
  version migration, including skip-level upgrade scenarios where
  multiple field removals compound into a single migration
  ([VEP #309][issue #309]).
- Enable deterministic regression tests for race conditions.
- Run fast enough to execute on every PR in CI, each test completing
  in seconds.
- Provide a migration path for functional tests that don't need a running
  guest.

## Non Goals

- Replacing functional tests (infrastructure integration still needs real
  clusters) or unit tests (fastest feedback for single components).
- Testing real hypervisor behavior (QEMU, KVM).

## Definition of Users

- **KubeVirt Developers**: Contributors writing or modifying
  controller logic, CRD schemas, or webhook admission handlers.
- **KubeVirt Reviewers**: Maintainers reviewing PRs that change
  controller behavior or API validation rules.
- **CI Infrastructure**: Automated pre-merge test pipelines that need
  lightweight, hermetic test execution.

## User Stories

- As a developer, I want to verify the full VM lifecycle (VM → VMI →
  pod → Scheduled) without deploying a cluster.
- As a developer fixing a race condition, I want a regression test with
  deterministic timing control that runs in seconds.
- As a reviewer, I want PRs changing controller logic to include
  integration tests demonstrating cross-controller correctness.
- As a developer deprecating a CRD field, I want a test that creates
  objects under the old schema, applies the new schema, simulates
  storage version migration, and verifies no data is silently lost.
- As a release engineer certifying a skip-level upgrade (e.g., v1.8
  → v1.11), I want integration tests that exercise the cumulative CRD
  schema diff across all intermediate releases and assert that
  deprecated fields are correctly converted or explicitly removed.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Core Components

**envtest**: Part of the [controller-runtime][] project and the standard
testing substrate used by Kubebuilder, Operator SDK, and Cluster API.
It starts a real `kube-apiserver` and `etcd` process locally (managed
by `setup-envtest`), providing real API-server fidelity for CRD resources
— real OpenAPI validation, status subresource enforcement, and
resourceVersion semantics — without requiring a full Kubernetes cluster.
Note that the kubelet, scheduler, and kube-controller-manager are absent,
so built-in Kubernetes resources (e.g., Pods) do not behave identically to
a real cluster; these are simulated by framework components such as the
pod simulator. KubeVirt CRDs are loaded from the same Go definitions
virt-operator generates in production, and tests talk to the API server
through a real KubeVirt client — no fakes at the client layer.

[controller-runtime]: https://github.com/kubernetes-sigs/controller-runtime

**Real controllers**: Initialized through the same startup path the
production virt-controller uses, wired to envtest via real shared
informers. The VM controller and all its synchronizer sub-controllers
run in-process alongside the VMI controller. virt-handler (which also
acts as a controller, updating VMI status), the workload-update
(migration) controller, and virt-operator are also supported as optional
components (`WithVirtHandler`, `WithWorkloadUpdateController`,
`WithVirtOperator`).

**Pod simulator**: Since envtest has no kubelet, a goroutine watches for
virt-launcher pods, binds them to a fake node, and sets their status to
Running. Defaults to happy-path scheduling, with optional test hooks to inject
delays or failures (e.g., unschedulable pods, OOMKilled/Evicted status). This
drives the VMI controller through its lifecycle phases.

**Webhook server**: Optional in-process HTTPS server running the real
KubeVirt mutating and validating admission handlers.

**Fake libvirt gRPC server**: An in-memory implementation of the
virt-launcher command gRPC interface, maintained in-house alongside the
framework in kubevirt/kubevirt. It runs the real VMI-to-domain XML
converter and captures the generated domain XML in memory so tests can
assert on it deterministically, without a running libvirtd or QEMU. (The
libvirt C library itself is not a blocker — `libvirt-devel` is already
present in the builder image.) Standing up this fake and the accompanying
virt-handler simulation is a non-trivial effort — potentially warranting
its own supporting doc or follow-up VEP — and is therefore scoped to Beta
(see [Graduation Requirements](#graduation-requirements)).

### Framework Options

```go
f := framework.New()                              // base: VM (inc. instancetype) + VMI controllers
f := framework.New(framework.WithWebhooks())       // + admission webhooks
f := framework.New(framework.WithWorkloadUpdateController()) // + workload-update controller
f := framework.New(framework.WithFakeLibvirt())    // + domain XML conversion
```

Options compose — multiple can be combined in one `New()` call.

### What envtest can and cannot test

**Can test (controller logic):**

- VM/VMI lifecycle, phase transitions, status updates
- ControllerRevision creation, upgrade, conflict detection
- Instancetype/preference application to VMI spec
- Workload-update controller: VM live migration creation and gating logic during KubeVirt upgrades
- Live updates (CPU, memory, and volume hotplug) — controller-level request handling and status
  tracking, with virt-handler simulation (analogous to live migration simulation)
- CRD structural schema and CEL validation
- Webhook admission (mutation and validation)
- Generation tracking, condition management, finalizer cleanup
- Domain XML generation via the real converter — asserting the full
  pipeline from the VM/VMI object submitted to the API server, through
  webhook- and controller-injected defaults, down to the domain XML handed
  to libvirt. Unit tests already cover the converter in isolation (starting
  from an already-built domain spec); this complements them by exercising
  that end-to-end path rather than duplicating it
- CRD schema evolution and storage version migration — create objects
  under one CRD version, apply an updated schema, simulate the
  GET+PUT migration cycle, and assert field preservation

**Cannot test (runtime/dataplane aspects require real infrastructure):**

- Running guest OS, console access, guest agent
- Runtime networking dataplane (CNI plugins, masquerade, bridge forwarding)
- Live migration dataplane (actual memory transfer between nodes)
- CDI dataplane (actual volume population and cloning)
- Storage dataplane (snapshot I/O, restore, export, hotplug volume attachment)

### Risks and Mitigations

- **Controller-wiring drift**: The framework replicates the production
  virt-controller initialization, so it must be updated when controller
  constructors change. *Mitigation*: constructors are called directly in
  the framework's setup, so a signature change breaks compilation and
  produces an immediate signal rather than a silent gap (see
  [Functional Testing Approach](#functional-testing-approach)).
- **Fidelity gap**: There is no real hypervisor, networking, or storage,
  and the absence of a kubelet, scheduler, and kube-controller-manager
  means built-in resource behaviour is simulated (e.g., the pod
  simulator) and can diverge from a real cluster. *Mitigation*: the
  "cannot test" boundary above is explicit; the framework validates
  controller logic and API interactions, and bugs depending on
  infrastructure remain functional-test territory. Simulations (pod
  simulator, fake libvirt) can also drift from real component behaviour
  over time, so this tier complements rather than supersedes functional/e2e
  tests and is never treated as the sole source of truth — e2e coverage
  remains necessary even for behaviour this tier exercises.
- **New CI dependency**: envtest requires `kube-apiserver` and `etcd`
  binaries via `setup-envtest`, which is new for KubeVirt CI.
  *Mitigation*: binaries are baked into the existing build container
  image at a pinned Kubernetes version (see Functional Testing
  Approach), so no runtime download or cluster provisioning is needed.
- **Startup cost**: ~5-6s per envtest instance. *Mitigation*: tests
  within an Ordered Context share a single instance to amortize the cost.

## API Examples

This VEP does not introduce user-facing KubeVirt API changes. The
"API" is the Go framework used by test authors. The framework is
intentionally thin — it wires up real controllers and lets tests use
the same client and matcher APIs already familiar from functional tests.

### Framework Setup

```go
f := framework.New(
    framework.WithWebhooks(),
    framework.WithWorkloadUpdateController(),
)

var _ = BeforeEach(func() { f.Start(); DeferCleanup(f.Stop) })
```

`New()` accepts composable options. `Start()` boots envtest (real
kube-apiserver + etcd), loads all KubeVirt CRDs, creates seed data
(namespace, a ready node), and starts the selected controllers and
simulators. `Stop()` tears everything down.

### Writing a Test

Tests use `f.VirtClient()` for KubeVirt resources and
`f.K8sClient()` for core Kubernetes resources — the same interfaces
used in functional tests. Assertions use the existing shared matchers from
`tests/framework/matcher/`.

```go
It("should reach Scheduled phase", func() {
    vm := libvmi.NewVirtualMachine(
        libvmi.New(libvmi.WithResourceMemory("128Mi")),
        libvmi.WithRunStrategy(virtv1.RunStrategyAlways),
    )
    vm, err := f.VirtClient().VirtualMachine("default").Create(
        context.Background(), vm, metav1.CreateOptions{},
    )
    Expect(err).ToNot(HaveOccurred())

    Eventually(matcher.ThisVMIWith(
        f.VirtClient(), vm.Namespace, vm.Name,
    )).Should(matcher.BeInPhase(virtv1.Scheduled))
})
```

No new helper APIs to learn — the framework provides real controllers
and a real API server; tests interact through standard clients and
matchers.

## Alternatives

### Enhanced Fake Clients (no envtest)

Use `k8s.io/client-go/testing` fake clientset with wired informers.
**Con**: no OpenAPI validation, no status subresource enforcement, no
real resourceVersion semantics. **Decision**: envtest. The startup cost
is acceptable for the fidelity gain, and envtest is the ecosystem
standard (Kubebuilder, Operator SDK, Cluster API).

### Mock-Based Approach (current pattern)

Continue using GoMock for all controller tests. **Con**: cannot test
multi-controller interactions; mocks verify calls made, not end state
reached. **Decision**: keep GoMock for unit tests, add envtest for
integration coverage.

## Scalability

Not applicable — test infrastructure only.

## Update/Rollback Compatibility

Not applicable — no production code changes.

## Functional Testing Approach

This VEP introduces a new **integration** test tier that sits between
unit tests (fastest feedback for a single component) and functional/e2e
tests (full cluster, real dataplane). The tier's scope is controller
logic and API interactions, as bounded by the "can/cannot test" list in
Design.

The framework itself is validated by its own test suite — the PoC tests
*are* the framework's integration tests. New framework code carries unit
tests where the logic is isolated (e.g., pod-simulator failure hooks);
the integration tests cover the multi-controller wiring that unit tests
cannot. If the framework breaks (controller wiring, pod simulator, CRD
loading), the tests fail.

**Compile-time safety**: Controller constructor signatures and gRPC
service interfaces are called directly in the framework's setup path.
When upstream constructors or interfaces change, the framework fails to
compile, producing an immediate signal — no separate contract test is
needed.

**CI integration**: The `make test-integration` target runs all integration
tests on every PR alongside `make test` (unit tests). Like unit tests, the
target is wrapped in `hack/dockerized` and runs inside the same build
container image. The `kube-apiserver` and `etcd` binaries required by
envtest are baked into that image at a pinned Kubernetes version — the
latest version supported by the target KubeVirt release as defined in the
[KubeVirt K8s Support Matrix][support-matrix]. The version can be overridden
at build time via an `ENVTEST_K8S_VERSION` environment variable. No
cluster provisioning or runtime binary download is needed.

The suite can be run locally with `make test-integration`, which also
uses `hack/dockerized` — no separate setup is needed beyond having the
build container available.

[support-matrix]: https://github.com/kubevirt/sig-release/blob/main/releases/k8s-support-matrix.md

**Regression coverage**: Each bug fix merged with an envtest
regression test provides ongoing validation that the framework
correctly exercises the relevant controller paths.

## Implementation History

- **2026-08-17**: VEP proposed.
- **2026-08 (PoC)**: Framework PoC posted as PRs #18726–#18731
  (superseding PR #18238) — 20 tests across 8 categories with VM (inc.
  instancetype), VMI, and workload-update controllers, pod simulator,
  webhook server, and fake libvirt gRPC server. See
  [Proof of Concept](./poc.md).

## Graduation Requirements

This is test-only infrastructure: it ships no user-facing runtime
behaviour, introduces no Feature Gate, and creates no API objects or VM
state. The standard On-By-Default Readiness and Feature-Gate-removal
criteria therefore do not apply, and there is no runtime feature to
enable or disable per stage. The Alpha/Beta/GA stages below combine what
gets built with the framework's integration into the contributor and CI
workflow — from a non-voting experiment to a required, gating pre-merge
check. Absent a feature gate, progression through these stages is tracked
solely by the target release versions recorded in the VEP metadata above.

### Alpha (v1.10.0)

Build:

- envtest with all KubeVirt CRDs; real VM (with all synchronizers,
  including instancetype) and VMI controllers in-process; pod simulator;
  optional webhook server; the `make test-integration` CI target.
- Representative tests: VM lifecycle, generation tracking and
  ControllerRevision lifecycle, regression tests (#16719, #16071), CRD
  validation, webhook admission, instancetype ControllerRevision
  upgrades (migrated from functional tests).

Criteria:

- `make test-integration` runs on every PR as a **non-voting** job.
- Flakiness is monitored via the standard KubeVirt CI dashboard.
- Contributors are encouraged, but not required, to include integration
  tests for controller changes.

### Beta

Build:

- Fake libvirt gRPC server for domain XML and virt-handler simulation
  (VMIs reach Running); workload-update controller.
- Further functional-test migrations (instancetype application,
  requirements, revisions).

Criteria:

- Guidelines published covering when integration tests are a
  nice-to-have vs. a hard requirement for PRs touching controller logic.
- VEP template updated to reference the integration test framework as an
  option in the Functional Testing Approach section.
- Suite demonstrates a low, monitored flake rate over at least one full
  release cycle while still non-voting.

### GA

Build (extended coverage):

- virt-operator install/upgrade lifecycle testing.
- Live migration API: migration object lifecycle and migration
  controller phase transitions (as OpenStack Nova tests live migration
  through the API layer without a real backend).
- CDI (DataVolume/DataSource) and storage (snapshot/restore) controller
  logic against the API server.
- Multi-node migration simulation with a stateful fake libvirt, enabling
  the real domain manager and converter to run against in-memory domain
  state.

Criteria:

- Promoted to a **voting** pre-merge check once the suite has
  demonstrated zero flakes across multiple release cycles with a
  representative test count (Alpha coverage as the baseline).
- Integration tests **required** for PRs introducing new controller
  logic or modifying existing controller behaviour, per the published
  contribution guidelines.
- Contributor guide for writing integration tests published in the
  KubeVirt developer documentation.
