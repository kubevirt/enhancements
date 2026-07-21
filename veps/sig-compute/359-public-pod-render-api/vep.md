# VEP #359: Public Pod Rendering API for Standalone VM Execution

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created: https://github.com/kubevirt/enhancements/issues/359
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt's internal `TemplateService.RenderLaunchManifest` is the only
mechanism for generating a Pod spec from a VirtualMachineInstance, but it lives
in an internal package (`pkg/virt-controller/services`) and has never been
exposed as a stable, public API. External tools that need to produce Pod
manifests from VM definitions must vendor KubeVirt's internal packages, creating
fragile dependencies that break on minor version upgrades.

This VEP proposes a new public rendering API, `kubevirt.io/render`, exposing
stable `PodFromVM()` and `PodFromVMI()` functions. These wrap the existing
rendering pipeline -- defaults, mutations, network setup, and template rendering
-- behind a clean interface that does not leak controller internals to consumers.

The package follows the same model as `kubevirt.io/api` and
`kubevirt.io/client-go`: it lives under `staging/src/kubevirt.io/render/` as a
standalone Go module with a minimal dependency footprint for external consumers.

## Motivation

Running KubeVirt VMs outside of Kubernetes clusters is an emerging use case.
Edge deployments, developer workstations, and CI/CD pipelines all benefit from
converting a VirtualMachine definition into a standalone Pod manifest that can be
executed by Podman or another OCI-compatible runtime.

The [kubevirt-vm-to-pod](https://github.com/vladikr/kubevirt-vm-to-pod) tool
addresses this use case today. However, to produce a correct Pod spec it must
import **eight internal KubeVirt packages**:

| Internal package | Purpose |
|---|---|
| `pkg/testutils` | Creating a fake `ClusterConfig` (no public constructor exists) |
| `pkg/virt-controller/services` | `TemplateService` and `RenderLaunchManifest` |
| `pkg/defaults` | `SetVirtualMachineDefaults`, `SetDefaultVirtualMachineInstance` |
| `pkg/virt-api/webhooks/mutating-webhook/mutators` | `ApplyNewVMIMutations` |
| `pkg/network/vmispec` | `SetDefaultNetworkInterface` |
| `pkg/util` | `SetDefaultVolumeDisk` |
| `pkg/virt-controller/watch/vm` | `SetupVMIFromVM`, `AutoAttachInputDevice` |
| `pkg/virt-config` | `ClusterConfig` type |

None of these are public API. Any can change signature, move, or disappear
between minor releases. The tool is forced to use `testutils` -- a package
explicitly intended for test code -- in production, because there is no other way
to construct a valid `ClusterConfig` without a running Kubernetes cluster.

Every KubeVirt version bump has the potential to break external consumers. A
stable, public rendering API would eliminate this fragility and enable a healthy
ecosystem of tools that build on KubeVirt's Pod rendering logic.

### Internal benefit to KubeVirt

Beyond external consumers, `pkg/render` can serve as the foundation for
simplifying KubeVirt's own VMI→Pod rendering path in `virt-controller`. Today,
the controller assembles a `ClusterConfig` via informers, creates a
`TemplateService` with 14 positional arguments, applies defaults and mutations
in a specific order, then calls `RenderLaunchManifest`. The render package
encapsulates this entire pipeline behind a single function call with an explicit
`Options` struct. Migrating `virt-controller` to use `pkg/render` is a larger
refactor that would follow as a separate effort after the package is established.

Additionally, this work lays groundwork for the gradual extraction of rendering
logic from `virt-controller/services` into `kubevirt.io/render`, which will also
benefit future hypervisor abstraction efforts (VEP-97).

## Goals

- Expose a public Go package (`kubevirt.io/kubevirt/pkg/render`) for rendering a
  Pod spec from a VirtualMachine or VirtualMachineInstance definition.
- Include the full defaults and mutations pipeline (VM defaults, VMI defaults,
  VMI mutations, network defaults, volume-disk pairing) so that callers receive
  a complete, ready-to-use Pod spec without reimplementing any of these steps.
- Accept configuration through an explicit `Options` struct instead of requiring
  callers to construct a `ClusterConfig` or interact with informer caches.
- Work fully offline — no running Kubernetes cluster, no KubeVirt controllers,
  no API server connection required. The functions must be pure transformations
  from VM/VMI spec + options → Pod spec.
- Provide a foundation for simplifying virt-controller's VMI→Pod rendering
  path in a future refactor.
- Version the render module in lockstep with KubeVirt releases — the rendered
  Pod spec is specific to the KubeVirt version it was built from.

## Non Goals

- **Replacing the internal `TemplateService`**. The internal implementation
  continues to exist and evolve for use by `virt-controller`. The public API
  wraps it; it does not replace it.
- **Supporting live-cluster features** such as migration, hotplug, or live
  snapshot in the public API. These require controller coordination and are out
  of scope for offline rendering.
- **Exposing `ClusterConfig` internals**. The public API accepts feature gates
  and configuration values as plain Go types. Consumers never import
  `pkg/virt-config`.
- **Providing a CLI tool**. A `virtctl render` subcommand could be built on top
  of this package in the future, but is not part of this VEP.

## Definition of Users

- **External tool developers** building utilities on top of KubeVirt's VM
  model (e.g., kubevirt-vm-to-pod, custom GitOps pipelines, templating engines).
- **Edge and standalone VM operators** who generate Pod YAML for execution
  outside Kubernetes via Podman or similar container runtimes.
- **CI/CD systems** that need to validate or generate VM Pod specs offline
  without deploying the full KubeVirt control plane.

## User Stories

- As an external tool developer, I want a stable Go API to generate a Pod spec
  from a VirtualMachine definition so that my tool does not break on every
  KubeVirt minor release.
- As an edge operator, I want to convert VM YAML into a standalone Pod manifest
  offline, without depending on KubeVirt's internal packages or test utilities.
- As a CI engineer, I want to render and validate VM Pod specs in a pipeline
  without running the KubeVirt control plane, so I can catch misconfiguration
  before deployment.
- As a KubeVirt contributor, I want external consumers to use a supported API
  surface so that refactoring internal packages does not generate downstream
  breakage reports.
- As a KubeVirt developer, I want virt-controller's VMI→Pod rendering path to
  use a well-defined internal API so the pipeline is easier to understand,
  test, and refactor.

## Repos

- kubevirt/kubevirt

## Design

### Package Location

The package will be placed under:

**`staging/src/kubevirt.io/render`** (import path: `kubevirt.io/render`)

This follows the same model as `kubevirt.io/api` and `kubevirt.io/client-go`,
making it a stable, independently consumable module with a minimal dependency
footprint for external consumers.

### Core functions

```go
// PodFromVM renders a Pod spec from a VirtualMachine definition.
// It applies VM defaults, extracts the VMI template, applies VMI defaults
// and mutations, configures networking, and calls RenderLaunchManifest.
func PodFromVM(vm *virtv1.VirtualMachine, opts Options) (*corev1.Pod, error)

// PodFromVMI renders a Pod spec from a VirtualMachineInstance definition.
// It applies VMI defaults and mutations, configures networking, and calls
// RenderLaunchManifest.
func PodFromVMI(vmi *virtv1.VirtualMachineInstance, opts Options) (*corev1.Pod, error)
```

Both functions are stateless, pure transformations, and safe for concurrent use.
They do not require a running Kubernetes cluster, KubeVirt controllers, or any
external state — all configuration is passed via the `Options` struct. This
makes them suitable for offline tooling, CI pipelines, and KubeVirt's own tests.

### Options struct

```go
type Options struct {
    // LauncherImage is the virt-launcher container image reference.
    // Required.
    LauncherImage string

    // FeatureGates is the list of KubeVirt feature gates to enable.
    // Example: []string{"ImageVolume", "HostDisk"}
    FeatureGates []string

    // ExportImage is the vm-export sidecar image. Optional; defaults to
    // the standard KubeVirt export image if empty.
    ExportImage string

    // RunAsUser is the UID for the virt-launcher process.
    // Default: 107 (the standard qemu user).
    RunAsUser int64
}
```

New fields can be added to `Options` without breaking existing callers, since Go
struct literals with named fields are forward-compatible with new fields that
have zero-value defaults.

`Options` serves the public/offline use case only. For internal adoption by
`virt-controller`, the rendering pipeline uses the `RenderConfig` and
`ManifestRenderer` interfaces directly, providing full control over
configuration and caching without being constrained by the simplified `Options`
surface.

### Architecture and Long-term Vision

The package introduces two interfaces to enable proper decoupling:

- **`RenderConfig`** — a minimal interface capturing only the configuration
  methods needed by the rendering pipeline.
- **`ManifestRenderer`** — an interface responsible for producing the final Pod
  manifest from a `VirtualMachineInstance`.

These interfaces allow the render module to avoid direct dependencies on
internal KubeVirt components where possible.

```go
type RenderConfig interface {
    IsFeatureGateEnabled(gate string) bool
    GetMachineType(arch string) string
    GetDefaultArchitecture() string
    GetCPUModel() string
    GetCPURequest() *resource.Quantity
    IsVMRolloutStrategyLiveUpdate() bool
    GetMaximumCpuSockets() uint32
    GetMaxHotplugRatio() uint32
    GetMaximumGuestMemory() *resource.Quantity
    GetDefaultNetworkInterface() string
    IsBridgeInterfaceOnPodNetworkEnabled() bool
    GetConfigFromKubeVirtCR() *v1.KubeVirt
    GetQGSSocketPath() string
    GetConfig() *v1.KubeVirtConfiguration
}

type ManifestRenderer interface {
    RenderLaunchManifest(vmi *v1.VirtualMachineInstance) (*corev1.Pod, error)
}
```

`*virtconfig.ClusterConfig` satisfies `RenderConfig` and
`*services.TemplateService` satisfies `ManifestRenderer` without any adapters.
For offline use, the render package provides its own lightweight `RenderConfig`
implementation built from `Options` values, using `featuregate.IsEnabled`
directly — no informers, goroutines, or Kubernetes client required.

#### Current State (Short-term)

For the initial implementation, the default `ManifestRenderer` still relies on
`TemplateService` internally to generate correct Pod specs for offline use. This
results in a limited internal dependency on `pkg/virt-controller/services`
within the default implementation only. We treat this as **temporary technical
debt**.

The public API remains simple:

```go
pod, err := render.PodFromVMI(vmi, render.Options{...})
```

#### Long-term Direction

The long-term goal is to move the core rendering logic into `kubevirt.io/render`
itself. Over time we plan to:

- Extract shared, hypervisor-agnostic rendering logic (defaults, mutations,
  network and volume handling, manifest construction) into the render module.
- Make `virt-controller` a consumer of `kubevirt.io/render` rather than the
  owner of the rendering implementation.
- Use the `ManifestRenderer` interface as the primary extension point.

This direction aligns well with VEP-97 (alternative hypervisors). As KubeVirt
evolves to support multiple hypervisors, having rendering logic live in a
dedicated, reusable module with a clear interface will make it easier to
introduce hypervisor-specific renderers without duplicating shared logic.

The ultimate vision is that `kubevirt.io/render` becomes the canonical home for
Pod rendering logic in KubeVirt.

### Internal wiring

Internally, `PodFromVM` and `PodFromVMI` perform the following steps:

1. Construct a `ClusterConfig` from the provided `Options` (feature gates,
   etc.), replacing the current need for `testutils.NewFakeClusterConfigUsingKV`.
2. Create a `TemplateService` with the launcher image, run-as user, and
   other configuration from `Options`.
3. For `PodFromVM`: apply `defaults.SetVirtualMachineDefaults`, then extract
   the VMI via `vmCtrl.SetupVMIFromVM`.
4. Apply `defaults.SetDefaultVirtualMachineInstance`.
5. Apply `mutators.ApplyNewVMIMutations`.
6. Apply `vmispec.SetDefaultNetworkInterface`.
7. Apply `util.SetDefaultVolumeDisk` and `vmCtrl.AutoAttachInputDevice`.
8. If PVC volumes are referenced and no real PVC cache is available, create
   minimal stub PVC objects so that `RenderLaunchManifest` can proceed.
9. Call `TemplateService.RenderLaunchManifest(vmi)`.
10. Return the resulting `*corev1.Pod`.

This is exactly the pipeline that kubevirt-vm-to-pod implements today by
reaching into internals. Centralizing it in a public package ensures that:

- The pipeline stays correct as internal implementations evolve.
- External consumers automatically pick up fixes and improvements.
- The internal packages can be refactored freely without breaking the public
  contract.

### Version compatibility

The render module version is tied 1:1 to the KubeVirt version. A rendered Pod
spec is specific to the KubeVirt release it was built from — container images,
volume layouts, security contexts, and feature gate behavior all evolve with
each release. Consumers should import the `kubevirt.io/render` version that
matches their target `virt-launcher` image.

Cross-version compatibility (e.g., rendering with v1.11 but running with a
v1.10 virt-launcher) is not a goal and not guaranteed.

The Go API surface (`PodFromVM`, `PodFromVMI`, `Options`) follows standard Go
module conventions: new `Options` fields are additive with zero-value defaults,
so code compiled against an older version continues to compile against newer
versions.

## API Examples

### Rendering a Pod from a VirtualMachine

```go
package main

import (
    "fmt"
    "os"

    "sigs.k8s.io/yaml"
    virtv1 "kubevirt.io/api/core/v1"
    "kubevirt.io/render"
)

func main() {
    vm := &virtv1.VirtualMachine{
        // ... loaded from YAML or constructed programmatically
    }

    pod, err := render.PodFromVM(vm, render.Options{
        LauncherImage: "quay.io/kubevirt/virt-launcher:v1.10.0",
        FeatureGates:  []string{"ImageVolume", "HostDisk"},
    })
    if err != nil {
        fmt.Fprintf(os.Stderr, "error: %v\n", err)
        os.Exit(1)
    }

    out, _ := yaml.Marshal(pod)
    fmt.Println(string(out))
}
```

### Rendering a Pod from a VirtualMachineInstance

```go
pod, err := render.PodFromVMI(vmi, render.Options{
    LauncherImage: "quay.io/kubevirt/virt-launcher:v1.10.0",
    RunAsUser:     1000,
})
```

### Minimal invocation (defaults for everything except launcher image)

```go
pod, err := render.PodFromVM(vm, render.Options{
    LauncherImage: "quay.io/kubevirt/virt-launcher:v1.10.0",
})
```

## Alternatives

### 1. `virtctl render` subcommand

A CLI subcommand on `virtctl` that renders a Pod manifest from a VM YAML file.
This is useful but insufficient: it only serves CLI users, not library consumers
who need programmatic access. A `virtctl render` command could be added later as
a thin wrapper around `pkg/render`.

### 2. Subresource API on VirtualMachine

A new REST endpoint (e.g., `POST /apis/subresources.kubevirt.io/.../render`)
that returns the rendered Pod spec. This requires a running KubeVirt installation
and network access to the API server, making it unsuitable for offline use cases
such as edge deployment preparation and CI pipelines.

### 3. Document internal packages as semi-stable

Mark certain internal packages (e.g., `pkg/virt-controller/services`) as
"semi-stable" via documentation. This does not actually solve the problem:
internal code is still free to change, and the surface area is far too large
(eight packages) for meaningful stability promises. It also forces consumers to
understand the correct ordering of defaults, mutations, and rendering steps.

### 4. Fork or copy the rendering code

Each consuming project copies the rendering logic into its own repository. This
leads to divergence from upstream, duplicated maintenance burden, and subtle
correctness bugs when the upstream pipeline changes and forks do not keep up.
This is effectively what kubevirt-vm-to-pod does today, and it is the pain point
that motivates this VEP.

## Scalability

Not applicable. This VEP introduces a library function, not a controller or
operator. There are no runtime scaling implications for KubeVirt clusters. The
`PodFromVM`/`PodFromVMI` functions are stateless, allocate only short-lived
objects, and can be called from any number of concurrent goroutines.

## Update/Rollback Compatibility

The render module is a compile-time dependency with no runtime cluster
component. It does not modify any existing KubeVirt behavior, API resources,
or controllers.

- **Version alignment**: The render module version must match the target
  KubeVirt / virt-launcher version. When upgrading KubeVirt, consumers update
  their `kubevirt.io/render` dependency to the same version.
- **No cross-version guarantees**: A Pod rendered with one version of the
  module is not expected to work with a different version's virt-launcher.
- **Rollback**: Rolling back KubeVirt in a cluster has no effect on the render
  module, since it is a compile-time dependency. Consumers pin to the version
  that matches their target runtime.

## Functional Testing Approach

- **Unit tests**: Exercise `PodFromVM` and `PodFromVMI` with a representative
  set of VM configurations:
  - ContainerDisk volumes
  - PersistentVolumeClaim volumes (stub PVC path)
  - HostDisk volumes
  - CloudInit (NoCloud and ConfigDrive)
  - Multiple network interfaces (Pod network, Multus)
  - CPU/memory resource requests
  - GPU and host device passthrough
- **Equivalence tests**: For each test case, verify that the Pod produced by
  `render.PodFromVMI(vmi, opts)` matches the Pod produced by directly calling
  `TemplateService.RenderLaunchManifest(vmi)` after applying the same defaults
  and mutations. This ensures the public API does not silently diverge from the
  internal pipeline.
- **Regression tests**: A golden-file test suite that captures the rendered Pod
  YAML for a set of canonical VM definitions. Changes to the output are flagged
  for review, ensuring that internal refactoring does not accidentally alter the
  public API's behavior.
- **Integration test with kubevirt-vm-to-pod**: The kubevirt-vm-to-pod project
  migrates from internal imports to `pkg/render`, serving as a real-world
  validation that the public API covers the necessary use cases.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `RenderAPI` registered (always enabled, used as lifecycle tracker)
- [ ] `PodFromVM` and `PodFromVMI` functions are implemented in
  `kubevirt.io/kubevirt/pkg/render`
- [ ] `Options` struct covers launcher image, feature gates, export image, and
  run-as user
- [ ] Unit tests cover common VM configurations (ContainerDisk, PVC, HostDisk,
  CloudInit, multiple networks)
- [ ] Basic documentation added to kubevirt.io user guide

### Beta

- [ ] `RenderConfig` and `ManifestRenderer` interfaces defined in `pkg/render`
- [ ] `ClusterConfig.IsFeatureGateEnabled` exported to satisfy `RenderConfig`
- [ ] `renderPod` uses `ManifestRenderer` interface instead of constructing
  `TemplateService` directly
- [ ] Compile-time interface satisfaction checks:
  `var _ RenderConfig = (*virtconfig.ClusterConfig)(nil)` and
  `var _ ManifestRenderer = (*services.TemplateService)(nil)`
- [ ] Downstream packages (`pkg/defaults`, `mutators`) refactored to accept
  `RenderConfig` instead of concrete `*virtconfig.ClusterConfig`
- [ ] `TemplateService` decoupled from `*ClusterConfig` via
  `services.ClusterConfigProvider` interface; feature gate calls collapsed
  into generic `IsFeatureGateEnabled`
- [ ] Offline `RenderConfig` implementation provided (built from `Options`,
  no informers or Kubernetes client required)
- [ ] Informer machinery removed from render package — `render.go` no longer
  imports `pkg/virt-config`
- [ ] Equivalence test verifying offline and online paths produce the same Pod

### GA

- [ ] Package moved to `staging/src/kubevirt.io/render/` as a standalone Go
  module with its own `go.mod`
- [ ] Core rendering logic moved into `kubevirt.io/render`, making it the
  canonical home for Pod rendering. Short-term: a limited dependency on
  `TemplateService` is accepted as technical debt in the default
  `ManifestRenderer` implementation. Long-term: the rendering logic is
  fully owned by `kubevirt.io/render`, which also positions it for future
  hypervisor abstraction work
- [ ] `kubevirt.io/kubevirt` root `go.mod` has `replace` directive for local
  development (same pattern as `kubevirt.io/api`, `kubevirt.io/client-go`)
- [ ] `virt-controller` migrated to import rendering from `kubevirt.io/render`
- [ ] Update documentation
- [ ] Remove `RenderAPI` feature gate
