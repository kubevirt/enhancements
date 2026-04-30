# VEP #200: Worker Pools

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes adding support for deploying multiple virt-handler DaemonSets
to serve heterogeneous node pools with different virt-handler and virt-launcher
container images. Pool selection is transparent to VM users — administrators
configure pools with label and device selectors, and KubeVirt automatically
matches VMIs to the appropriate pool based on the VMI's existing labels and
device requests.

## Motivation

Many organizations operate heterogeneous Kubernetes clusters with specialized
node pools for different workloads. Examples include:

- **GPU nodes**: Require virt-launcher images with GPU drivers and libraries
- **FPGA nodes**: Need specialized images with FPGA support libraries
- **Secure enclaves**: Run hardened images with additional security configurations

Currently, KubeVirt deploys a single virt-handler DaemonSet that runs the same
virt-handler and virt-launcher images across all nodes. This forces operators to
either:

1. Run separate KubeVirt installations for each node pool (operational overhead)
2. Build a single image containing all specialized components (image bloat)
3. Use external webhooks to mutate VMI pods (fragile, external dependency)

This VEP proposes folding launcher and handler image selection into KubeVirt
natively, removing the need for external webhooks or separate installations and
giving administrators a single configuration surface for heterogeneous clusters.

Worker pools also provide a path for consuming custom handler and launcher
images built from forked release branches that carry patches not yet merged
upstream, without requiring changes to KubeVirt's default images.

## Goals

- Enable operators to deploy additional virt-handler DaemonSets targeting
  specific node pools
- Allow custom virt-handler and virt-launcher images per node pool
- Automatically and transparently select the appropriate virt-launcher image
  for VMIs based on admin-configured selectors that match VMI device requests
  and/or labels
- Maintain backward compatibility with existing single virt-handler deployments
- Require no VMI-level changes from end users — pool selection is entirely
  admin-configured

## Non Goals

- Runtime image switching for running VMIs
- Automatic detection of node capabilities
- Cross-pool live migration with image transformation
- Multi-hypervisor support in Alpha: the hypervisor abstraction layer
  ([VEP #97](../hypervisor-abstraction.md)) introduces per-cluster hypervisor
  configuration. Worker pools are a natural mechanism for extending this
  to per-pool hypervisor backends (e.g., KVM on some nodes, MSHV on others),
  but this is deferred to Beta. See the Beta graduation requirements for
  details.

## Definition of Users

- **Cluster administrators**: Configure worker pools via the KubeVirt CR
  with selectors that match VMIs by device names and/or labels. Administrators
  control which VMIs get which launcher images without end user involvement.
- **VM users**: Create VMIs as normal — requesting devices, setting labels, etc.
  Pool selection happens automatically and transparently.

## User Stories

### Story 1: GPU Node Pool (Admin)

As a cluster administrator using the
[NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator), I want VMIs
that request Tesla T4 GPUs to automatically use a GPU-optimised virt-launcher
image, without requiring VM users to know about pools or set special labels.

The GPU Operator labels nodes with product-specific labels such as
`nvidia.com/gpu.product=Tesla-T4`. KubeVirt's own device plugin registers
per-model GPU resources via `permittedHostDevices` in the KubeVirt CR (e.g.,
`nvidia.com/TU104GL_Tesla_T4`). The pool's `nodeSelector` targets nodes with
the matching product label, while the `selector.deviceNames` matches VMIs
requesting that GPU model.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - WorkerPools
    permittedHostDevices:
      pciHostDevices:
        - pciVendorSelector: "10DE:1EB8"
          resourceName: "nvidia.com/TU104GL_Tesla_T4"
  workerPools:
    - name: gpu-pool
      virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
      nodeSelector:
        nvidia.com/gpu.product: Tesla-T4
      selector:
        deviceNames:
          - "nvidia.com/TU104GL_Tesla_T4"
```

### Story 2: Multi-Pool Configuration (Admin)

As a cluster administrator, I want different virt-launcher images for Tesla T4
GPU and Intel Arria 10 FPGA workloads, each matched automatically by their
KubeVirt device name.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - WorkerPools
  workerPools:
    - name: gpu-pool
      virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
      nodeSelector:
        nvidia.com/gpu.product: Tesla-T4
      selector:
        deviceNames:
          - "nvidia.com/TU104GL_Tesla_T4"
    - name: fpga-pool
      virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-fpga
      nodeSelector:
        fpga.intel.com/present: "true"
      selector:
        deviceNames:
          - "intel.com/fpga-arria10"
```

### Story 3: Custom Handler and Launcher Images (Admin)

As a cluster administrator, I want to deploy custom virt-handler and
virt-launcher images on Tesla T4 GPU nodes because both components require
changes to support the hardware — the handler needs a custom device manager
plugin and node labeller, while the launcher needs GPU driver libraries.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - WorkerPools
  workerPools:
    - name: gpu-pool
      virtHandlerImage: registry.example.com/kubevirt/virt-handler:v1.0.0-gpu
      virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
      nodeSelector:
        nvidia.com/gpu.product: Tesla-T4
      selector:
        deviceNames:
          - "nvidia.com/TU104GL_Tesla_T4"
```

### Story 4: Label-Based Pool (Admin)

As a cluster administrator, I want to assign a custom virt-launcher to VMIs
labelled for a specific workload class, regardless of their device requests.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - WorkerPools
  workerPools:
    - name: secure-pool
      virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-hardened
      nodeSelector:
        security-zone: restricted
      selector:
        vmLabels:
          matchLabels:
            workload-class: secure
```

### Story 5: VM User Creates a GPU VM (Transparent)

As a VM user, I create a VMI requesting an NVIDIA Tesla T4 GPU. I do not need
to know about pools, set special node selectors, or reference any pool
configuration. The cluster administrator has already configured the pool to
match `nvidia.com/TU104GL_Tesla_T4` devices, and the correct virt-launcher
image is selected automatically.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-vm
spec:
  domain:
    resources:
      requests:
        memory: 4Gi
    devices:
      gpus:
        - name: gpu1
          deviceName: nvidia.com/TU104GL_Tesla_T4
      disks:
        - name: containerdisk
          disk:
            bus: virtio
  volumes:
    - name: containerdisk
      containerDisk:
        image: registry.example.com/my-gpu-vm:latest
```

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### VMI to Pool Matching

VMIs are matched to worker pools based on admin-configured selectors. The
matching is evaluated by the virt-controller when rendering the virt-launcher
pod and does not require any pool-specific configuration from the VM user.

#### Node labels vs device names

The pool configuration uses two distinct types of identifiers that should not
be confused:

- **Node labels** (used in `nodeSelector`): Labels present on the node itself,
  typically set by operators such as the
  [NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator) via NFD
  NodeFeatureRules (e.g., `nvidia.com/gpu.present: "true"`,
  `nvidia.com/gpu.product: Tesla-T4`, `nvidia.com/gpu.H100: "true"`). These
  control where the pool's virt-handler DaemonSet is scheduled and where
  matched VMI pods land.
- **Device names** (used in `selector.deviceNames`): Kubernetes device plugin
  resource names as used in VMI `spec.domain.devices.gpus[].deviceName` and
  `spec.domain.devices.hostDevices[].deviceName`. In KubeVirt these are
  per-model names registered via `permittedHostDevices` in the KubeVirt CR
  (e.g., `nvidia.com/TU104GL_Tesla_T4`). These control which VMIs are matched
  to the pool.

#### Selector types

Each pool defines a `selector` with two optional criteria, evaluated with OR
semantics (either matching is sufficient):

- **deviceNames**: Matches if any GPU or HostDevice requested by the VMI
  appears in the pool's `deviceNames` list.
- **vmLabels.matchLabels**: Matches if all specified key-value pairs are
  present on the VMI's labels.

#### Matching rules

1. Pools are evaluated in order (first match wins)
2. Within a pool's selector, `deviceNames` and `vmLabels` are OR'd — either
   matching is sufficient
3. If no pool matches, the default virt-launcher image is used

#### Node placement

The pool's `nodeSelector` is used for two purposes:

- **DaemonSet scheduling**: The pool's virt-handler DaemonSet is scheduled on
  nodes matching the `nodeSelector`
- **VMI pod scheduling**: When a VMI matches a pool, the pool's `nodeSelector`
  is merged into the virt-launcher pod's required node affinity, ensuring the
  VMI lands on a node served by that pool's virt-handler

**Matching examples:**

| VMI devices | VMI labels | Pool deviceNames | Pool vmLabels | Match? |
|-------------|------------|------------------|---------------|--------|
| `nvidia.com/TU104GL_Tesla_T4` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | Yes (device) |
| `{}` | `{workload-class: secure}` | — | `{workload-class: secure}` | Yes (label) |
| `nvidia.com/TU104GL_Tesla_T4` | `{workload-class: secure}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | Yes (device) |
| `intel.com/fpga` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | No |
| `{}` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | `{workload-class: secure}` | No |
| `nvidia.com/TU104GL_Tesla_T4` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | `{workload-class: secure}` | Yes (device, OR) |

### Component Changes

#### virt-operator

- Creates an additional DaemonSet for each worker pool entry
- Applies the pool's `nodeSelector` to each DaemonSet
- Configures anti-affinity on the primary virt-handler to exclude nodes claimed
  by worker pools
- Blocks pool removal while matched VMIs are still running — the pool's
  DaemonSet is only deleted once impacted nodes have been drained

#### virt-handler

No changes to virt-handler itself are required. Additional DaemonSets run the
same virt-handler binary (or a custom image); pool-aware behavior is handled
entirely by virt-operator and virt-controller.

#### virt-controller

- Evaluates VMIs against pool selectors and selects the appropriate
  virt-launcher image
- Merges the matched pool's `nodeSelector` into the virt-launcher pod's node
  affinity
- Annotates matched virt-launcher pods with the pool name
- Detects outdated VMIs when pool configurations change (workload-updater)

## API Examples

A new `workerPools` field is added to `KubeVirtSpec`. Each entry defines a
pool with a name, optional image overrides, a `nodeSelector`, and a `selector`
for matching VMIs:

```go
type KubeVirtSpec struct {
    // ... existing fields ...
    WorkerPools []WorkerPoolConfig `json:"workerPools,omitempty"`
}

type WorkerPoolConfig struct {
    Name              string              `json:"name"`
    VirtHandlerImage  string              `json:"virtHandlerImage,omitempty"`
    VirtLauncherImage string              `json:"virtLauncherImage,omitempty"`
    NodeSelector      map[string]string   `json:"nodeSelector"`
    Selector          WorkerPoolSelector  `json:"selector"`
}

type WorkerPoolSelector struct {
    DeviceNames []string             `json:"deviceNames,omitempty"`
    VMLabels    *WorkerPoolVMLabels   `json:"vmLabels,omitempty"`
}

type WorkerPoolVMLabels struct {
    MatchLabels map[string]string `json:"matchLabels"`
}
```

At least one of `virtHandlerImage` or `virtLauncherImage` must be set.
`nodeSelector` is required and controls both DaemonSet scheduling and VMI pod
placement. `selector` is required and must define at least one of `deviceNames`
or `vmLabels`.

### Validation

Pool names must be unique. Overlapping selectors or `nodeSelector` values
across pools produce warnings (not rejections) since first-match-wins and
anti-affinity provide deterministic behavior.

## Alternatives

### Namespace-based Separation

Run separate KubeVirt installations in different namespaces for each node pool.

**Rejected because:**

- Increases operational complexity
- Prevents resource sharing between pools
- Complicates upgrades

### Per-VMI Image Override Annotations

Allow users to specify virt-launcher image via VMI annotations.

**Rejected because:**

- Security concerns - arbitrary image injection
- No virt-handler customization
- Harder to audit/govern

### Webhook-based Image Mutation

Use an external mutating webhook to inject images based on device/label
matching. This is the approach used by projects such as
[kubevirt-aie-webhook](https://github.com/kubevirt/kubevirt-aie-webhook).

**Rejected because:**

- External dependency with its own lifecycle management
- Doesn't address virt-handler customization
- Fragile — webhook failures block VMI creation
- Not integrated into KubeVirt upgrade/rollback semantics

### KubeVirt Structured Plugins ([VEP #190](../190-kubevirt-structured-plugins/vep.md))

Use the structured plugin framework to implement image selection as a plugin
rather than a native KubeVirt feature.

**Rejected because:**

- VEP #190's admission integration tracks Kubernetes Mutating Admission Policies
  (MAPs) and webhooks by reference but does not implement admission logic itself.
  MAPs cannot perform cross-object lookups — image selection requires inspecting
  the owning VMI from a virt-launcher pod admission request to evaluate device
  and label selectors. This means a full mutating webhook is still required, and
  the Plugin CR only adds an indirection layer around it.
- Domain hooks and node hooks operate on DomainSpec and VM lifecycle events
  respectively — neither provides a mechanism for selecting which container image
  is used for virt-launcher or virt-handler.
- Worker pools addresses both launcher and handler image customization per node
  class, which falls outside the plugin framework's scope.

### nodeSelector-Only Matching

Require VM users to set specific `nodeSelector` labels on VMIs to match pools.

**Rejected because:**

- Leaks infrastructure details (node labels) to VM users
- Requires user coordination with administrators
- Users targeting specific hardware (e.g., GPUs) already express intent via
  device requests — duplicating that intent in `nodeSelector` is redundant

## Scalability

Each entry in `workerPools` creates one additional DaemonSet. The number of
pools is expected to be small (single digits) as it corresponds to distinct node
pool types in the cluster. The VMI-to-pool matching checks device names and
labels with O(n) complexity over the pool list, performed once per VMI pod
creation.

## Update/Rollback Compatibility

**Upgrade:**

- Existing deployments continue to work with single virt-handler
- Additional worker pools can be added incrementally
- Running VMIs are not affected until restart
- Custom per-pool `virtHandlerImage` and `virtLauncherImage` references are
  **not** automatically updated during a KubeVirt upgrade. Administrators must
  manually update these image references in the KubeVirt CR to match the new
  KubeVirt version. The workload-updater will detect outdated VMIs once the
  pool's launcher image is updated.

**Pool removal:**

- Removing a pool from the `workerPools` list is blocked while VMIs matched to
  the pool are still running on worker nodes. Administrators must drain impacted
  nodes before the pool can be removed and its DaemonSet deleted.

**Downgrade:**

- Additional DaemonSets are deleted when feature gate is disabled
- Running VMIs served by additional worker pools continue running
- New VMIs use default images

**Version Skew:**

All virt-handler and virt-launcher images (including custom per-pool overrides)
must be built from the same KubeVirt version. Using mismatched versions is
unsupported and may cause undefined behavior.

## Functional Testing Approach

**Unit tests** covering pool matching logic: device name matching, label
matching, OR semantics, first-match-wins ordering, no-match fallback to default
image, and nodeSelector merging into pod affinity.

**Functional tests** covering the operator lifecycle: DaemonSet creation and
deletion, custom image selection, anti-affinity on the primary virt-handler,
VMI-to-pool matching by device and label, pool removal blocking while matched
VMIs are running, and workload-updater detection of outdated VMIs after pool
configuration changes.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `WorkerPools` guards all code changes (disabled by default)
- [ ] API types `WorkerPoolConfig`, `WorkerPoolSelector`,
  `WorkerPoolVMLabels`
- [ ] DaemonSet creation and deletion
- [ ] VMI matching by device names and/or VM labels
- [ ] Pool `nodeSelector` merged into matched VMI's virt-launcher pod node
  affinity
- [ ] Anti-affinity on primary virt-handler
- [ ] Unit and functional tests
- [ ] User documentation

### Beta

- [ ] Per-pool hypervisor backend configuration via optional `hypervisor` field
  in `WorkerPoolConfig`, enabling mixed-hypervisor clusters (e.g., KVM on some
  nodes, MSHV on others) building on the hypervisor abstraction layer
  ([VEP #97](../hypervisor-abstraction.md))

### GA

- [ ] Criteria to be defined based on Beta feedback
