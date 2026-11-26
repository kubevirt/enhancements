# VEP 82 Supplement: Pluggable virt-launcher Pod Rendering in virt-controller

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: `v1.11.0`
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to this document
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This document is a focused companion to
[VEP 82: Plugin-based generalization of KubeVirt's virtualization stack](./vep.md).
It defines how `virt-controller` makes rendering of `virt-launcher` Pods
pluggable while retaining ownership of Kubernetes orchestration and validation
of the final `virt-launcher` Pod returned by the plugin.

Today, `virt-controller` renders a `virt-launcher` pod spec that contains several values specific to the in-tree Libvirt/QEMU virtualization stack. Examples are the `compute` container's image itself, command-line arguments, runtime directories, memory overhead, and stack-specific node-selectors. This prevents an out-of-tree virtualization stack from supplying its own `virt-launcher` without modifying KubeVirt core.

This VEP splits rendering into three stages:

1. `virt-controller` renders the launcher Pod using the existing default
   Libvirt/QEMU/KVM logic and builds a versioned request containing the VMI,
   rendered Pod, and renderer context.
2. The selected CEL or RPC plugin adapts that Pod for its virtualization stack.
3. `virt-controller` validates the returned Pod spec before creating it.

The plugin implements the following logical transformation:

```text
VirtualMachineInstance + default launcher Pod + renderer context -> final launcher Pod
```

The default renderer supplies the initial Pod, including common KubeVirt fields
and default-stack settings. The plugin may replace or remove stack-specific
settings and is responsible for the correctness of the final Pod.
The final Pod is validated by `virt-controller` for structural correctness and
cluster policy, but validation does not make KubeVirt responsible for the
plugin's rendering behavior. Plugin implementers are responsible for the
correctness, compatibility, and stability of their complete `virt-launcher`
Pod implementation.

## Motivation

The current launcher Pod renderer combines common KubeVirt Pod construction
with details required by the in-tree Libvirt/QEMU/KVM stack.

Examples of stack-specific behavior include:

- selecting the `virt-launcher` image and pull policy;
- setting the launcher command, arguments, and environment;
- mounting `/var/run/libvirt` and other runtime directories;
- calculating memory consumed by the VMM and management processes;
- selecting nodes by machine type, CPU model, firmware, or stack capability;

Allowing a plugin to adapt the default `virt-launcher` Pod lets alternate stacks
reuse common rendering logic while replacing stack-specific settings. CEL
provides an in-process declarative option; RPC supports independently deployed
implementations.

## Goals

- Support CEL and RPC plugins that receive the VMI, default-rendered launcher
  Pod, and renderer context and return the final `virt-launcher` Pod.
- Make plugin implementers responsible for virtualization-specific and
  virtualization-agnostic Pod fields and for maintaining stable behavior for
  KubeVirt.
- Keep Kubernetes clients, informer stores, controller caches, and resolved
  cluster state inside `virt-controller` core.
- Enforce structural and security requirements on the complete Pod returned by
  a plugin.
- Isolate renderer failures to affected VMIs and avoid making
  `virt-controller` unavailable.
- Preserve current Libvirt/QEMU/KVM rendering behavior when the feature is
  disabled or the default in-tree renderer is selected.
- Define the plugin interface such that it can be versioned and tested independently
  of a specific virtualization stack.

## Non Goals

- Making admission webhooks, `virt-handler`, node labeling, or
  `virt-launcher` lifecycle APIs pluggable. Those are covered by the parent VEP
  or separate follow-up VEPs.
- Defining how a VMI selects a virtualization stack.
- Removing Libvirt/QEMU semantics from the KubeVirt v1 API. Generalizing the KubeVirt API
  to make it virtualization-stack-agnostic is a much bigger work that will be taken up in 
  the future based on the learnings from the parent VEP #82.
- Allowing a renderer plugin to create Kubernetes resources or call back into
  `virt-controller`.
- Exposing controller-local clients, informer stores, or Go implementation
  types through the proposed plugin API. The plugin implementation does not have access to any data structures from core KubeVirt. The plugin implementation should be self-sufficient and not 
  depend on more information from `virt-controller` than what is provided via the interface.


## Definition of Users

- **Virtualization stack developers** who provide a launcher implementation
  and need to construct its Pod without changing KubeVirt core.
- **Platform operators** who install and configure virtualization stack
  plugins.
- **KubeVirt maintainers** who own the RPC contract, orchestration, and final
  validation.
- **KubeVirt distributors** who may ship additional renderers while preserving
  the upstream control-plane contract.

## User Stories

- As a cluster administrator, I want to run VM workloads on a virtualization
  stack that KubeVirt does not support in tree, without maintaining a fork of
  KubeVirt.
- As a cluster administrator, I want to use a specialized virtualization stack
  for workloads that require different hypervisor capabilities, hardware
  integration, isolation properties, or performance characteristics.
- As a virtualization stack developer, I want to integrate an experimental or
  niche launcher with KubeVirt so that users can exercise it in real clusters
  and provide feedback before it is considered for broader upstream support.
- As a KubeVirt distributor, I want to ship and support a differentiated
  virtualization stack while continuing to consume upstream KubeVirt releases
  without carrying invasive changes to `virt-controller`.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements)
- Out-of-tree repositories that implement CEL or Pod-level RPC renderers

## Design

### Rendering Pipeline

The launcher manifest pipeline is:

1. Render the default Libvirt/QEMU/KVM launcher Pod for the requested mode.
2. Build a versioned request containing the VMI, rendered Pod, and renderer
   context, and invoke the selected CEL or RPC plugin, if any.
3. Receive the final `virt-launcher` Pod, or use the default Pod unchanged when
   no plugin is selected.
4. Validate the Pod.
5. Create the Pod, or report a render failure on the VMI and retry according
   to controller policy.

```mermaid
flowchart LR
    VMI[VirtualMachineInstance] --> Default[Render default Libvirt/QEMU/KVM Pod]
    Default --> Request[VMI, rendered Pod, renderer context]
    Request --> Select{Renderer method}
    Select -->|In-tree| Final[Final Pod]
    Select -->|CEL| CEL[Evaluate cached CEL program]
    Select -->|RPC| RPC[Call renderer Service]
    CEL --> Final
    RPC --> Final
    Final --> Validate[Validate final Pod]
    Validate -->|valid| Create[Create virt-launcher Pod]
    Validate -->|invalid| Failure[Report VMI render failure]
```

Conceptually, core uses a single renderer interface:

```go
type LauncherManifestRenderer interface {
    Render(
        context.Context,
        *LauncherManifestRenderRequest,
    ) (*corev1.Pod, error)
}

type LauncherManifestRenderRequest struct {
    VMI             *virtv1.VirtualMachineInstance
    Pod             *corev1.Pod
    RendererContext LauncherRendererContext
}

type LauncherRendererContext struct {
    Configuration *virtv1.KubeVirtConfiguration
    Mode          LauncherRenderMode
}
```

The CEL and RPC adapters implement this interface after the default renderer
has produced the input Pod. Each adapter returns a final Pod to the same
validation path. Implementations work on an isolated Pod copy and must not
mutate the request's VMI or renderer context.

### Relationship to VEP 359

This VEP and [VEP 359](../359-public-pod-render-api/vep.md) refactor the same
`TemplateService.RenderLaunchManifest` pipeline. The work must be sequenced as
follows:

1. VEP 359 implements the default `ManifestRenderer` for the in-tree
   Libvirt/QEMU stack.

2. Virt-Controller starts using VEP 359's `RenderConfig` and `ManifestRenderer`
   to build default Libvirt/QEMU-based `virt-launcher` pods.

3. `virt-controller` renders a virt-launcher pod with the default renderer and passes
 it along with the VMI, and serializable renderer context to the
   selected CEL or RPC adapter.

### Renderer Input

Both renderer methods receive these logical values:

| Field | Type | Description |
|---|---|---|
| `vmi` | `VirtualMachineInstance` | The VMI being reconciled. |
| `pod` | `Pod` | Launcher Pod produced by the default Libvirt/QEMU/KVM renderer. |
| `rendererContext` | `LauncherRendererContext` | Effective, defaulted `KubeVirtConfiguration` and lifecycle rendering mode. |
| `apiVersion` | string | The selected renderer contract version. |

Supported lifecycle render modes are:

- `launch`: a normal source `virt-launcher` Pod;
- `migration-target`: a migration target Pod;
- `provisioning`: a temporary Pod used for storage provisioning.

Renderers must reject unknown modes. Go's `context.Context` controls CEL
evaluation cancellation or the RPC deadline and cancellation. It is distinct
from the serializable renderer context and is not serialized into the request.

Only versioned, serializable values cross the renderer boundary. Plugins never
receive Kubernetes clients, informer handles,
`TemplateService`, `ClusterConfig`, or internal caches.

### Renderer Output

Both renderer adapters produce one logical result:

| Field | Type | Description |
|---|---|---|
| `pod` | `Pod` | The complete launcher Pod to pass to core validation. |

### Plugin Responsibilities

A renderer adapts the default Pod and owns the correctness of its final
specification. It may:

- set the compute-container image, command and arguments and image pull policy;
- add stack-specific environment variables;
- add stack-specific volumes, mounts, and volume devices;
- request hypervisor resources and stack-specific resource claims;
- replace default-stack memory overhead with stack-specific overhead; if the FG `VmiMemoryOverheadReport`
  is enabled, the renderer plugin should also set the annotation `kubevirt.io/memory-overhead-bytes=<memory-overhead-bytes>`;
- add stack capability node selectors (ones that are generated by `node-labeller` currently);
- add CPU model, CPU feature, machine type, firmware, or
  confidential-computing scheduling constraints;
- preserve or adapt virtualization-agnostic metadata, ownership, security contexts,
  resources, scheduling, networking, storage, probes, and supporting
  containers required for a valid `virt-launcher` Pod;
- preserve user intent represented by the VMI and render context.

A renderer must:

- return the final `virt-launcher` Pod that can be deployed by `virt-controller`
  without further mutations;
- produce correct VMI ownership and identity;
- produce deterministic output for the same request and plugin configuration;
- be safe when the same request is evaluated more than once;
- avoid creating resources or causing side effects during rendering;
- return errors explicitly; and
- maintain compatibility and stable behavior across supported KubeVirt
  versions.

### CEL Renderer

The CEL method is configured inline in `VirtualizationStackPlugin` and executes
inside `virt-controller`; no controller plugin Deployment or Service is needed.
Its typed bindings expose `vmi`, `pod`, `rendererContext`, and administrator
`params`, using the same versioned inputs as RPC.

CEL returns the final Pod directly, using the input `pod` as the starting point
for stack-specific changes, just like RPC. Expressions construct a new Pod
value rather than mutating the input in place. The versioned CEL environment
must provide typed Pod construction or immutable copy/update helpers.
The adapter converts the result to a Kubernetes Pod and passes it through the
common final-Pod validation path; no JSONPatch is generated or applied.
Returning `pod` preserves the default Pod. Evaluation and result-conversion
errors are explicit render failures.

### Pod-level RPC Renderer

The RPC method is provided by a cluster Service referenced by the
`VirtualizationStackPlugin` resource. The plugin provider owns its Deployment,
Service, scaling, and lifecycle.

The logical API is:

```proto
service LauncherManifestRenderer {
  rpc GetPluginInfo(GetPluginInfoRequest)
      returns (GetPluginInfoResponse);

  rpc RenderLauncherManifest(RenderLauncherManifestRequest)
      returns (RenderLauncherManifestResponse);
}

message GetPluginInfoRequest {}

message GetPluginInfoResponse {
  string plugin_name = 1;
  repeated string supported_api_versions = 2;
}

message RenderLauncherManifestRequest {
  string api_version = 1;
  kubevirt.io.api.core.v1.VirtualMachineInstance vmi = 2;
  k8s.io.api.core.v1.Pod pod = 3;
  LauncherRendererContext renderer_context = 4;
}

message LauncherRendererContext {
  kubevirt.io.api.core.v1.KubeVirtConfiguration configuration = 1;
  LauncherRenderMode mode = 2;
}

message RenderLauncherManifestResponse {
  k8s.io.api.core.v1.Pod pod = 1;
}

enum LauncherRenderMode {
  LAUNCHER_RENDER_MODE_UNSPECIFIED = 0;
  LAUNCHER_RENDER_MODE_LAUNCH = 1;
  LAUNCHER_RENDER_MODE_MIGRATION_TARGET = 2;
  LAUNCHER_RENDER_MODE_PROVISIONING = 3;
}
```

`virt-controller` discovers the Service from the `VirtualizationStackPlugin` CR, calls `GetPluginInfo`, and selects the
highest mutually supported renderer API version. Negotiation is cached and
invalidated when the registration, Service endpoint set, or controller version
changes.

Automatic retries must not multiply calls within one reconcile without a
strict bound. Normal controller work-queue retry and backoff are the primary
recovery mechanism. Because rendering is side-effect-free and idempotent, a
request may be repeated after an ambiguous transport failure.

### Registration and API Examples

Exactly one of `controller.renderer.cel` or `controller.renderer.rpc` must be
declared in each `VirtualizationStackPlugin`.

#### CEL

```yaml
apiVersion: virstackplugin.kubevirt.io/v1alpha1
kind: VirtualizationStackPlugin
metadata:
  name: openvmm-mshv
spec:
  controller:
    renderer:
      apiVersion: v1alpha1
      cel:
        expressions:
        - |
          pod
  runtime:
    socketName: openvmm-mshv.sock
```

This identity expression illustrates the Pod-valued result contract; it does
not adapt the Pod for OpenVMM. An alternative stack must return a Pod with
incompatible default-stack settings replaced or removed. Multiple expressions
are evaluated in declaration order, each receiving the preceding expression's
returned Pod as `pod`; the last result is the final Pod.

#### RPC cluster service

```yaml
apiVersion: virstackplugin.kubevirt.io/v1alpha1
kind: VirtualizationStackPlugin
metadata:
  name: openvmm-mshv
spec:
  controller:
    renderer:
      apiVersion: v1alpha1
      rpc:
        service:
          namespace: openvmm-system
          name: openvmm-controller-plugin
          port: 9443
        timeout: 5s
  runtime:
    socketName: openvmm-mshv.sock
```

### Renderer Selection

The mechanism by which the selected virtualization stack is recorded on a VMI
is defined by the parent VEP. Once a stack is selected,
`virt-controller` resolves exactly one `VirtualizationStackPlugin` and its
renderer.

The selection is stable for the lifetime of a launcher Pod. Reconciliation may
re-render before Pod creation, but it must not mutate an already-created
launcher Pod to a different stack or renderer. Migration targets use the same
stack as the source VMI.

The in-tree Libvirt/QEMU/KVM renderer always supplies the initial Pod. When
selected without a plugin, that Pod enters final validation unchanged.
Otherwise, the CEL or RPC adapter adapts it and enters the same validation path.

### Validation

The final Pod returned by a CEL or RPC plugin is treated as untrusted.
Validation runs immediately after rendering and before Pod creation. Core does
not mutate or repair the renderer output, only validates it.

Validation must verify:

- a non-nil Pod and structurally complete compute container;
- correct VMI identity, owner references, namespace, and generated name;
- no unauthorized service account, host namespace, host path, privilege,
  capability, device, or security-context escalation;
- permitted host devices against effective cluster policy;
- output object size and collection-count limits;

Important: Passing validation establishes that core may create the Pod; it does not certify the
plugin's functional correctness or transfer maintenance responsibility to
KubeVirt.

### Failure Handling and VMI Status

Renderer failure never makes `virt-controller` itself unready. It prevents Pod
creation only for the affected VMI.

Failures are classified as:

| Class | Examples | Handling |
|---|---|---|
| Registration | missing plugin, invalid CEL or RPC configuration | condition/event; no plugin invocation |
| Compatibility | no common API or CEL environment version, unsupported mode | condition/event; retry after plugin or VMI change |
| CEL definition | compile/type/static-cost error | plugin not ready; condition/event; affected VMIs wait |
| Availability | RPC unavailable, deadline exceeded | condition/event; work-queue retry with backoff |
| Evaluation | CEL runtime/cost/result-conversion error, RPC application error | condition/event on affected VMI |
| Output | nil, incomplete, malformed, or forbidden final Pod | condition/event; Pod is not created |

Stable reasons should distinguish at least:

- `VirtualizationStackPluginNotFound`;
- `VirtualizationStackPluginNotReady`;
- `RendererAPINotCompatible`;
- `RendererUnavailable`;
- `RendererFailed`;
- `RenderedPodInvalid`.

Repeated events are rate limited. Conditions include the selected stack,
observed plugin generation, and a sanitized error summary. They must not
include the full VMI or Pod.

There is no automatic fallback from a selected CEL or RPC renderer to the
default Libvirt/QEMU stack. Such fallback could silently launch a VM with different
isolation, device, firmware, or compatibility properties.

## API Versioning

Three versions evolve independently:

1. the `VirtualizationStackPlugin` CRD version;
2. the launcher-render contract version;
3. the CEL environment or RPC protocol version implementing that contract.

The selected contract version determines:

- input field semantics;
- required properties of the complete output Pod;
- supported modes;
- final validation rules.

An RPC plugin advertises supported protocol versions; a CEL registration
declares the environment version through its renderer API version. Core must not run a
renderer when no compatible version exists.

Changes that only add optional input or response fields may be compatible.
Changes to required fields or interpretation of existing fields require a new
version.

## Update/Rollback Compatibility

The `PluggableVirtualizationStack` feature gate guards plugin selection and
dispatch during alpha.

With the gate disabled, only the default in-tree virtualization stack is supported:

- CEL and external RPC registrations are not invoked;
- existing VM and VMI APIs require no changes.
- The in-tree default stack continues to use its existing launcher Pod
  renderer. Golden and end-to-end tests enforce this invariant.

Already-running launcher Pods are not re-rendered during a
`virt-controller` upgrade. A renderer update affects only Pods rendered after
the new plugin generation is observed. Operators are responsible for ensuring
that plugin updates remain compatible with the corresponding runtime and
launcher images.

During a KubeVirt upgrade:

- an existing compatible contract version remains usable through the supported
  version-skew window;
- a new controller must negotiate an older RPC version or reject it explicitly;
- incompatible registrations become not ready rather than being interpreted
  using new semantics.

Rollback cannot preserve VMIs created with a renderer or stack unknown to the
rolled-back KubeVirt version. Before beta, the project must define and test the
supported rollback window.

## Design Alternatives

### Core-Constructed Base Pod Completed by the Plugin

Constructing a virtualization-stack-neutral base Pod would require defining a
new boundary between common and stack-specific fields. Instead, the selected
design reuses the complete default-stack renderer. CEL or RPC plugins may
replace or remove its settings rather than only adding fields.

### Core-to-Plugin RPC Contract defined as Fine-grained Functions

The parent VEP originally proposed RPCs such as `GetLauncherImage`,
`GetLauncherCommand`, `GetAdditionalVolumes`, `GetLauncherCapabilities`,
`GetRunAsUserGroup`, and `GetNodeSelectors`.

This design rejects that approach. It mirrors today's implementation details,
requires protocol growth for each new Pod concern, creates ordering and
conflict questions across calls, increases network round trips, and can combine
individually valid fragments into an invalid Pod. The Pod-level contract
replaces these RPCs.

### Deploy Renderer Plugin as a Sidecar of `virt-controller`

Running an RPC renderer beside each `virt-controller` avoids a cluster Service
call but couples plugin installation, scaling, upgrade, and failure domains to
the core Deployment.

Rejected in favor of in-process CEL or the independently managed Service model
selected by the parent VEP.

## Functional Testing Approach

### Unit tests

- Verify the in-tree default renderer produces semantically equivalent Pods to
  the pre-refactor path.
- Run the same output-validation test table against the in-tree default
  renderer and CEL and RPC adapters.
- Verify plugins receive the VMI, default-rendered Pod, and renderer context,
  replace default-stack settings, and preserve required common fields.
- Verify nil, oversized, malformed, and incomplete output is rejected.
- Verify RPC negotiation, deadlines, cancellation, status mapping, and
  malformed responses.
- Verify CEL compilation and caching, cost limits, cancellation, unchanged and
  modified Pod results, and evaluation or result-conversion failures.


### Conformance tests

A standalone conformance suite is published for plugin implementers to run
against CEL definitions or an RPC service without deploying a KubeVirt cluster. It verifies:

- deterministic and idempotent rendering;
- supported render modes;
- contract-version behavior;
- construction of a complete Pod;
- correct virtualization-specific and virtualization-agnostic fields;
- error handling;
- final-Pod validation compatibility.

The suite supplies representative render requests and checks the responses
against the shared render contract and final-Pod validation rules.

### Functional and end-to-end tests

- Start a VMI using the in-tree default renderer with the feature gate both
  disabled and enabled.
- Start equivalent VMIs using CEL and RPC renderers.
- Verify migration-target and provisioning modes.
- Make the RPC service unavailable and verify only affected VMIs fail and
  recover after service restoration.
- Return a forbidden privileged Pod and verify Pod creation is blocked.
- Upgrade `virt-controller` while VMIs rendered by the RPC plugin are running.
- Verify renderer updates affect new Pods without mutating running Pods.

## Implementation History

- 2026-10-02: Initial companion VEP drafted.
- 2026-10-14: Changed the plugin contract to complete Pod construction over
  RPC only.
- 2026-11-15: Restore CEL alongside RPC and pass the default-rendered launcher
  Pod, VMI, and renderer context to plugins.

## Graduation Requirements

### Alpha

- [ ] Guard renderer selection and dispatch with the
  `PluggableVirtualizationStack` feature gate.
- [ ] Demonstrate semantic equivalence of the in-tree default renderer with
  pre-refactor behavior.
- [ ] Implement the versioned VMI, default-rendered Pod, and renderer-context
  request and final-Pod response contract.
- [ ] Add mutually exclusive CEL and RPC renderer configuration to
  `VirtualizationStackPlugin`, and implement both adapters.
- [ ] Implement validation for final launcher pod spec returned by the plugin.
- [ ] Run output-validation tests against the in-tree default renderer and CEL
  and RPC adapters.
- [ ] Demonstrate one non-default virtualization stack end to end.
- [ ] Verify that invalid output from the renderer plugin cannot create a Pod.
- [ ] Verify that a renderer failure affects only VMIs selecting that renderer.

### Beta

- [ ] Implement Pod-level RPC discovery, version negotiation, mTLS, deadlines,
  cancellation, and error mapping.
- [ ] Run contract tests against CEL and RPC renderers.
- [ ] At least two non-default RPC stack implementations have exercised the
  contract.
- [ ] The renderer API has documented compatibility and deprecation policies.
- [ ] Upgrade, version skew, and rollback behavior are covered by CI.
- [ ] RPC load, failover, and backoff behavior is validated at supported
  control-plane scale.
- [ ] The conformance suite is published for out-of-tree plugin authors.
- [ ] Security review of registration, mTLS, and final validation is complete.
- [ ] No unresolved high-severity renderer isolation or validation defects.

#### On-By-Default Readiness

- [ ] The default renderer has no known behavior regression.
- [ ] Operator status clearly identifies invalid and unavailable renderers.
- [ ] Metrics and alerts distinguish plugin failures from core reconciliation
  failures.
- [ ] Documentation covers installation, upgrades, troubleshooting, and
  removal.

### GA

- [ ] The render contract and compatibility policy are stable.
- [ ] The feature gate is removed.
- [ ] RPC implementations have sustained multi-release usage.
- [ ] All supported upgrade and rollback paths are continuously tested.
- [ ] Conformance is required for supported third-party renderers.

## Open Questions

- Which protobuf representation will preserve Kubernetes unknown fields while
  remaining consumable by out-of-tree plugins?
- What RPC timeout and concurrency defaults are appropriate at supported VMI
  creation rates?
- What authentication authority issues and rotates renderer server and client
  certificates?
- Which VMI condition should own render failures before the VMI has a launcher
  Pod?
