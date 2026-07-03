# VEP #371: Envtest Framework

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Introduce an envtest-based test framework for KubeVirt that exercises
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

2. **E2E tests** (`tests/`): Comprehensive but slow (4-hour timeout),
   resource-intensive, and prone to infrastructure flakiness.

There is no middle ground. Bugs from multi-controller interactions,
race conditions, or state machine transitions cannot be caught by unit
tests and are expensive to reproduce with E2E tests.

The PoC demonstrates this gap with concrete examples:

- [Issue #16719][]: RestartRequired condition never cleared after spec
  revert — requires real ControllerRevision lookup and multi-step
  reconciliation that unit tests mock away.
- [Issue #18396][]: Workload-update migrations race with virt-handler
  DaemonSet re-roll during upgrade — a timing race between controllers
  that E2E tests catch only by luck (it's a flake).
- Instancetype ControllerRevision upgrades — 8 E2E tests that exercise
  pure controller logic, migrated to envtest with a 13.4x speedup.

[issue #16719]: https://github.com/kubevirt/kubevirt/issues/16719
[issue #18396]: https://github.com/kubevirt/kubevirt/issues/18396

## Goals

- Exercise multi-controller interactions without a Kubernetes cluster.
- Use envtest (real kube-apiserver + etcd) for full API fidelity.
- Enable deterministic regression tests for race conditions.
- Run fast enough to execute on every PR in CI, each test completing
  in seconds.
- Provide a migration path for E2E tests that don't need a running
  guest.

## Non Goals

- Replacing E2E tests (infrastructure integration still needs real
  clusters) or unit tests (fastest feedback for single components).
- Testing real hypervisor behavior (QEMU, KVM).

## Definition of Users

- **KubeVirt Developers**: Cheap coverage of multi-controller flows
  and edge cases without provisioning infrastructure.
- **KubeVirt Reviewers**: Validate that PRs do not break
  multi-controller interactions.
- **CI Infrastructure**: Lightweight enough to run alongside unit
  tests on every PR — no cluster provisioning, no KubeVirt deployment,
  no VM boot.

## User Stories

- As a developer, I want to verify the full VM lifecycle (VM → VMI →
  pod → Scheduled) without deploying a cluster.
- As a developer fixing a race condition, I want a regression test with
  deterministic timing control that runs in seconds.
- As a reviewer, I want PRs changing controller logic to include
  envtest tests demonstrating cross-controller correctness.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Core Components

**envtest**: CRDs loaded from the existing Go generation functions in
`pkg/virt-operator/resource/generate/components/crds.go`. A real
`KubevirtClient` created via `GetKubevirtClientFromRESTConfig(cfg)`.

**Real controllers**: Initialized using the same pattern as
`VirtControllerApp`, wired to envtest via real `SharedInformerFactory`.
Currently: VM, VMI, instancetype, and workload-update controllers.

**Pod simulator**: Since envtest has no kubelet, a goroutine watches
for virt-launcher pods, binds them to a fake node, and sets their
status to Running. This drives the VMI controller through its
lifecycle phases.

**Fake libvirt gRPC server**: CGo-free implementation of the
virt-launcher `CmdServer` gRPC interface. Runs the real VMI-to-domain
XML converter and stores results in memory. Tests can assert on
domain XML without any libvirt C library dependency. Since the
framework calls the real `CmdServer` constructors and gRPC service
interfaces directly, any upstream interface change breaks compilation
— no separate contract test is needed.

**Webhook server**: Optional in-process HTTPS server with the real
KubeVirt `ServeVMs` mutating and validating handlers.

### Framework Options

```go
f := framework.New()                              // base: VM + VMI + instancetype controllers
f := framework.New(framework.WithWebhooks())       // + admission webhooks
f := framework.New(framework.WithFakeLibvirt())    // + domain XML conversion
f := framework.New(framework.WithWorkloadUpdateController()) // + workload-update controller
```

Options compose — multiple can be combined in one `New()` call.

### What envtest can and cannot test

**Can test (controller logic):**
- VM/VMI lifecycle, phase transitions, status updates
- ControllerRevision creation, upgrade, conflict detection
- Instancetype/preference application to VMI spec
- Workload-update migration creation and gating logic
- CRD structural schema and CEL validation
- Webhook admission (mutation and validation)
- Generation tracking, condition management, finalizer cleanup
- Domain XML generation via the real converter

**Cannot test (needs real infrastructure):**
- Running guest OS, console access, guest agent
- Real networking (CNI, masquerade, bridge)
- Live migration between nodes
- CDI integration (DataVolumes, DataSources)
- Storage (snapshots, restore, export, hotplug volumes)
- virt-operator deployment and upgrade (possible with extensions, see below)

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

## Trade-offs

- **Controller wiring**: Framework replicates `VirtControllerApp`
  initialization. When controller constructors change, the framework
  must be updated.
- **Fidelity gap**: No real hypervisor, networking, or storage. Bugs
  depending on these remain E2E territory.
- **envtest binaries**: `kube-apiserver` and `etcd` required via
  `setup-envtest`. Standard for envtest users but new for KubeVirt CI.
  The `setup-envtest` version is initially pinned to the latest
  available release. If version skew with KubeVirt's supported
  Kubernetes versions ever causes issues, the pin can be adjusted
  to match the support matrix.
- **Startup cost**: ~5-6s per envtest instance. Tests within an
  Ordered Context should share one instance to amortize this.
- **CI gating**: The envtest suite runs as a non-voting job initially.
  Once it has demonstrated stability (zero flakes across multiple
  release cycles with a representative test count), it is promoted to
  a voting pre-merge check. Flakiness is monitored through the
  standard KubeVirt CI dashboard.

## Scalability

Not applicable — test infrastructure only.

## Update/Rollback Compatibility

Not applicable — no production code changes.

## Proof of Concept

A working PoC ([PR #18238][], `tests/envtest/` on the
`functional-test-framework` branch) demonstrates the framework.

[PR #18238]: https://github.com/kubevirt/kubevirt/pull/18238

| Category | Tests | What they exercise |
|----------|-------|--------------------|
| VM lifecycle | 1 | VM → VMI → Pod → Scheduled |
| Generation tracking | 1 | ObservedGeneration/DesiredGeneration, ControllerRevision lifecycle |
| Regression | 2 | Bug #16719 (RestartRequired stuck), bug #16071 (stale preferenceRef) |
| Webhook admission | 2 | Mutating defaults, validating rejection |
| CRD validation | 2 | Structural schema, CEL rules |
| Domain XML | 1 | Real converter via gRPC |
| Workload update | 3 | Migration creation, phase gating, deployment ID gating |
| Instancetype upgrades | 8 | ControllerRevision upgrade from v1beta1 (migrated from E2E) |

**E2E migration example** — instancetype ControllerRevision upgrades:

| | E2E (sig-compute lane) | envtest (shared env) |
|---|---|---|
| 8 tests | 152.6s total, 19.1s avg | 11.4s total, 1.4s avg |
| Speedup | — | **13.4x** |
| Cluster required | Yes | No |

## Implementation Phases

### Alpha (v1.10.0): Framework and sample tests

The alpha deliverable is the framework itself and a representative
set of tests demonstrating the pattern across different test
categories. The goal is to establish the framework, prove its value,
and provide enough examples for other developers to write envtest
tests for their own controller changes.

**Framework:**
- envtest with all KubeVirt CRDs
- Real VM, VMI, and instancetype controllers in-process
- Pod simulator for fake scheduling
- Webhook server (optional, `WithWebhooks`)
- Fake libvirt gRPC server for domain XML (optional, `WithFakeLibvirt`)
- `make test-envtest` CI target

**Sample tests (representative, not exhaustive):**
- VM lifecycle (VM → VMI → Pod → Scheduled)
- VM generation tracking and ControllerRevision lifecycle
- Regression tests (bugs #16719, #16071)
- CRD validation (structural schema, CEL rules)
- Webhook admission (mutating defaults, validating rejection)
- Domain XML conversion via fake libvirt
- Instancetype ControllerRevision upgrades (migrated from E2E)

### Post-alpha: Extended controller coverage

- Workload-update controller (`WithWorkloadUpdateController`)
- Additional E2E test migrations (instancetype application,
  requirements, revisions)
- virt-handler simulation (`WithVirtHandler`) — VMIs reach Running
- virt-operator testing (`WithVirtOperator`) — install/upgrade
  lifecycle with DeploymentSimulator

## Future Extensions

- **Multi-node migration simulation**: Each node gets its own
  FakeConnection. `MigrateToURI3()` defines a domain on the target
  and removes it from the source.
- **FakeConnection / FakeVirDomain**: Stateful fake libvirt with
  domain state machine, enabling real `LibvirtDomainManager` and
  converter to run against in-memory domain state.

## Implementation History

- **Phase 1 (PoC)**: 20 tests across 8 categories. Framework with
  VM, VMI, instancetype, and workload-update controllers, pod
  simulator, webhook server, fake libvirt gRPC server. Instancetype
  ControllerRevision upgrade tests migrated from E2E (13.4x speedup).
  Branch: `functional-test-framework`, PR #18238.

## Graduation Requirements

Test infrastructure — does not transition through alpha/beta/GA.
