# VEP 353: Component-Based Testing Framework

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: TBD
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt has a gap between fast unit tests and slow e2e tests — controller logic in the middle goes undertested. This VEP introduces a reusable component testing framework using `envtest` that runs a real Kubernetes API server in-process, enabling controller logic to be tested without needing a full cluster, libvirt, or running VMs. The pilot target is `virt-controller`.

## Motivation

This proposal originated from a GSoC 2026 submission: [proposal](https://docs.google.com/document/d/15iUvFJCyGsmufIvkvF0Y0fmBfu_m7jjWYDe7AuEnsE8/edit?usp=sharing)

KubeVirt's components are Kubernetes controllers. Their core logic involves watching events, driving VMI phase transitions, propagating owner references, and reconciling state across multiple object types.

The existing testing layers handle this poorly:

| Layer | Speed | Needs Cluster | Catches Controller Bugs |
|---|---|---|---|
| Unit tests (`pkg/`) | ~30s | No | Rarely |
| E2E tests (`tests/`) | 45+ min | Yes | Yes |

Unit tests mock away the Kubernetes API entirely — so they don't test controller behavior, only internal function logic. E2E tests catch controller bugs, but at enormous cost in time, infrastructure, and flakiness.

Bugs that only surface in the untested gap today:
- VMI never transitions from `Pending` to `Scheduled` due to a missed status update
- Duplicate reconcile events causing incorrect resource creation
- Race condition when a pod is deleted while VMI is being reconciled
- Wrong owner reference on launcher pod — silent until e2e

## Goals

- Introduce a middle testing layer: component tests that test one KubeVirt controller in isolation against a real Kubernetes API server
- Provide a reusable `pkg/testframework/component/` package that makes writing component tests as straightforward as writing unit tests
- Wire the framework into CI as a `make componenttest` target that runs in under 5 minutes on standard Linux runners
- Deliver a pilot suite of 10+ tests covering `virt-controller` behavior
- Provide contributor documentation so the framework can be adopted for other components over time

## Non Goals

- This VEP does not replace unit tests or e2e tests — it adds a middle layer
- This VEP does not require libvirt, a kubelet, nodes, or running VMs
- Initial implementation does not cover `virt-handler`, `virt-api`, or other components (though the framework is designed to support them)
- This VEP does not introduce any user-facing API changes or feature gates

## Definition of Users

**KubeVirt contributors** writing or reviewing controller logic who need a fast, reliable way to validate behavior without spinning up a full cluster.

**CI infrastructure maintainers** who need a test layer that is fast enough to be a mandatory pre-merge check and stable enough to not introduce flakiness.

## User Stories

- As a contributor fixing a VMI phase transition bug, I want to write a component test that validates the fix against a real API server without waiting 45 minutes for e2e, so I can iterate quickly.
- As a reviewer, I want PRs touching `virt-controller` reconciliation logic to include a component test, so I can be confident the behavior is covered without requiring a full cluster run.
- As a CI maintainer, I want a `make componenttest` target that runs in under 5 minutes on standard Linux runners and can be added as a mandatory pre-merge check.
- As a contributor onboarding to KubeVirt, I want clear documentation on what belongs in a component test vs. a unit test vs. an e2e test, so I know where to write my test.

## Repos

- [`kubevirt/kubevirt`](https://github.com/kubevirt/kubevirt) — all implementation lives here (`pkg/testframework/component/`, pilot suite, CI target)

## Design

### Core Technology: `envtest`

[`sigs.k8s.io/controller-runtime/pkg/envtest`](https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/envtest) starts a real `kube-apiserver` and `etcd` in-process. It requires no kubelet, no nodes, no libvirt, and no actual VMs. It exposes a standard `rest.Config` — any Kubernetes client connects normally. It is used by controller-runtime, Cluster API, Crossplane, and other major CNCF projects.

`virt-controller` only needs the Kubernetes API to do its job — `envtest` gives it exactly that.

### Framework Package: `pkg/testframework/component/`

Four building blocks:

**1. `TestEnvironment` — one-line suite setup**

```go
env := testframework.NewComponentEnvironment()
env.WithCRDs("config/crd/bases")
env.WithComponent(virtcontroller.New(env.Config()))
env.Start()
```

Starts `envtest`, loads KubeVirt CRDs, wires the component under test, and returns a ready `client.Client`.

**2. Object builders — fluent test object construction**

```go
vmi := testbuilder.NewVMI("test-vmi", "default").
    WithArch("amd64").
    WithMemory("256Mi").
    WithCPU(1).
    Build()
```

Covers `VirtualMachineInstance`, `VirtualMachine`, and `KubeVirt` types. Eliminates repetitive struct initialization boilerplate from every test file.

**3. Wait helpers — typed `Eventually` wrappers**

```go
Expect(testframework.WaitForVMIPhase(
    ctx, client, vmi, v1.Running, 30*time.Second,
)).To(Succeed())
```

Replaces raw `Eventually` + `k8sClient.Get` polling loops. Named helpers per resource type and condition.

**4. Auto-cleanup — namespace-scoped teardown**

```go
ns := env.NewTestNamespace(ctx)
defer ns.Cleanup(ctx)
```

All objects created within the namespace are tracked and automatically deleted in `AfterEach`.

### Pilot Suite: `virt-controller`

`virt-controller` is the ideal first target — it is a pure Kubernetes controller with no libvirt or node access, and all its behavior is observable via the API.

Minimum 10 test cases:

1. VMI creation → launcher pod created with correct spec
2. VMI status transitions: `Pending → Scheduling → Scheduled`
3. VMI deletion → launcher pod cleaned up, finalizer removed
4. Correct owner reference set on launcher pod
5. Controller recovers when pod is unexpectedly deleted
6. Concurrent VMI creation handled correctly (goroutine safety)
7. VMI with invalid spec → controller sets error status
8. Pod failure propagated back to VMI status
9. Duplicate reconcile events do not cause duplicate pods
10. Namespace-scoped cleanup — no cross-namespace leakage

### Package Layout

```
pkg/testframework/component/
├── environment.go       # TestEnvironment: envtest lifecycle, CRD loading
├── builders/
│   ├── vmi.go          # VMI fluent builder
│   ├── vm.go           # VM fluent builder
│   └── kubevirt.go     # KubeVirt CR builder
├── waiters/
│   ├── vmi.go          # WaitForVMIPhase, WaitForVMICondition
│   └── pod.go          # WaitForPodReady, WaitForPodDeleted
└── namespace.go         # TestNamespace with auto-cleanup
```

## API Examples

Suite setup in a component test file:

```go
var _ = BeforeSuite(func() {
    env = testframework.NewComponentEnvironment()
    env.WithCRDs("config/crd/bases")
    env.WithComponent(virtcontroller.New(env.Config()))
    Expect(env.Start()).To(Succeed())
})

var _ = AfterSuite(func() {
    Expect(env.Stop()).To(Succeed())
})
```

A complete component test:

```go
It("should create a launcher pod when a VMI is created", func() {
    ns := env.NewTestNamespace(ctx)
    defer ns.Cleanup(ctx)

    vmi := testbuilder.NewVMI("test-vmi", ns.Name).
        WithMemory("256Mi").
        WithCPU(1).
        Build()

    Expect(env.Client().Create(ctx, vmi)).To(Succeed())

    Eventually(func() error {
        return testframework.WaitForVMIPhase(ctx, env.Client(), vmi, v1.Scheduling, 30*time.Second)
    }).Should(Succeed())

    podList := &corev1.PodList{}
    Expect(env.Client().List(ctx, podList, client.InNamespace(ns.Name))).To(Succeed())
    Expect(podList.Items).To(HaveLen(1))
})
```

## Alternatives

### Expanding Unit Test Mocking

Unit tests could use deeper mocks via `k8s.io/client-go/testing` fake clients. This has been tried extensively in KubeVirt. Fake clients do not implement watch semantics, informer cache invalidation, or admission webhook behavior — so they cannot catch the class of bugs component tests address. Deep mock hierarchies also become a significant maintenance burden over time.

### Using a Kind Cluster in CI

Kind starts in 60–90 seconds vs. under 5 seconds for `envtest`, requires a container runtime, and is significantly more complex to operate in CI. `envtest` is sufficient for `virt-controller`, which only needs the API. Kind may be appropriate for future testing layers targeting node-level components like `virt-handler`.

## Scalability

The framework starts a single `envtest` instance per test suite (not per test). Namespace-scoped isolation ensures tests do not interfere with each other. The pilot suite is expected to run in under 5 minutes. As more components are onboarded, each component suite runs as a separate binary, keeping individual suite times bounded.

## Update/Rollback Compatibility

This VEP introduces no user-facing APIs and no feature gates. It adds a new internal package (`pkg/testframework/component/`) and a new CI target (`make componenttest`). There is no rollback concern — removing the package would simply remove the test layer.

## Functional Testing Approach

The framework is self-validating: the `virt-controller` pilot suite serves as the functional test for the framework itself. If the framework is broken, the pilot suite fails. No additional testing infrastructure is required.

## Implementation History

- 2026-06-28: VEP created.

## Graduation Requirements

### Alpha

- [ ] `pkg/testframework/component/` package implemented with `TestEnvironment`, object builders, wait helpers, and auto-cleanup
- [ ] `envtest` integrated into the KubeVirt build system
- [ ] Minimum 10 `virt-controller` component tests implemented and passing in CI
- [ ] `make componenttest` target exists and completes in under 5 minutes on standard Linux runners
- [ ] Contributor documentation merged: what belongs at each testing layer and how to write a component test

### Beta

- [ ] Framework adopted for a second component (e.g., `virt-api` or `virt-handler`)
- [ ] `make componenttest` is a mandatory pre-merge CI check
- [ ] No flaky tests across 30+ consecutive CI runs

### GA

- [ ] Framework adopted by at least three KubeVirt components
- [ ] Full framework API covered by contributor documentation
- [ ] No regressions introduced across two consecutive KubeVirt release cycles
