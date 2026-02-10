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
container images. Pool selection is transparent to VMI users — administrators
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

- **Cluster administrators**: Configure worker pools by creating `WorkerPool`
  CRs with selectors that match VMIs by device names and/or labels.
  Administrators control which VMIs get which launcher images without end
  user involvement.
- **VMI users**: Create VMIs as normal — requesting devices, setting labels, etc.
  Pool selection happens automatically and transparently.

## User Stories

### Story 1: GPU Node Pool (Admin)

As a cluster administrator using the
[NVIDIA GPU Operator](https://github.com/NVIDIA/gpu-operator), I want VMIs
that request Tesla T4 GPUs to automatically use a GPU-optimised virt-launcher
image, without requiring VMI users to know about pools or set special labels.

The GPU Operator labels nodes with product-specific labels such as
`nvidia.com/gpu.product=Tesla-T4`. KubeVirt's own device plugin registers
per-model GPU resources via `permittedHostDevices` in the KubeVirt CR (e.g.,
`nvidia.com/TU104GL_Tesla_T4`). The pool's `nodeSelector` targets nodes with
the matching product label, while the `vmiSelector.deviceNames` matches VMIs
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
---
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: gpu-pool
spec:
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
  nodeSelector:
    nvidia.com/gpu.product: Tesla-T4
  vmiSelector:
    deviceNames:
      - "nvidia.com/TU104GL_Tesla_T4"
```

### Story 2: Multi-Pool Configuration (Admin)

As a cluster administrator, I want different virt-launcher images for Tesla T4
GPU and Intel Arria 10 FPGA workloads, each matched automatically by their
KubeVirt device name.

```yaml
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: gpu-pool
spec:
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
  nodeSelector:
    nvidia.com/gpu.product: Tesla-T4
  vmiSelector:
    deviceNames:
      - "nvidia.com/TU104GL_Tesla_T4"
---
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: fpga-pool
spec:
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-fpga
  nodeSelector:
    fpga.intel.com/present: "true"
  vmiSelector:
    deviceNames:
      - "intel.com/fpga-arria10"
```

### Story 3: Custom Handler and Launcher Images (Admin)

As a cluster administrator, I want to deploy custom virt-handler and
virt-launcher images on Tesla T4 GPU nodes because both components require
changes to support the hardware — the handler needs a custom device manager
plugin and node labeller, while the launcher needs GPU driver libraries.

```yaml
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: gpu-pool
spec:
  virtHandlerImage: registry.example.com/kubevirt/virt-handler:v1.0.0-gpu
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-gpu
  nodeSelector:
    nvidia.com/gpu.product: Tesla-T4
  vmiSelector:
    deviceNames:
      - "nvidia.com/TU104GL_Tesla_T4"
```

### Story 4: Label-Based Pool (Admin)

As a cluster administrator, I want to assign a custom virt-launcher to VMIs
labelled for a specific workload class, regardless of their device requests.

```yaml
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: secure-pool
spec:
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-hardened
  nodeSelector:
    security-zone: restricted
  vmiSelector:
    vmiLabels:
      matchLabels:
        workload-class: secure
```

### Story 5: VMI User Creates a GPU VMI (Transparent)

As a VMI user, I create a VMI requesting an NVIDIA Tesla T4 GPU. I do not need
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

### Story 6: Rolling Node OS Upgrade (Admin)

As a cluster administrator performing a rolling upgrade from one OS version
to another (e.g., RHEL 9 to RHEL 10), I need to run different virt-handler
and virt-launcher images on upgraded nodes because the new OS introduces
breaking libvirt/QEMU compatibility changes. During the rollout, the cluster
temporarily contains both old and new OS nodes.

Some breaking changes may also remove features entirely — for example,
RHEL 10 removes support for mediated devices (mdev/vGPU). VMIs relying on
mdev must remain on RHEL 9 nodes with a virt-launcher built against the
older libvirt/QEMU stack that still supports mdev. In this case the pool
is not temporary: RHEL 9 nodes and their pool must be retained as long as
mdev workloads exist.

I label upgraded nodes with `os-version: rh10`, create a WorkerPool targeting
them, and progressively label VMIs with `runtime: rh10` to migrate them to
the new runtime stack. VMIs that depend on features removed in RHEL 10
(such as mdev) remain on the default RHEL 9 handler and are not labelled.
Once all compatible VMIs are migrated, the default KubeVirt images can be
updated — but the RHEL 9 pool (or the default handler on remaining RHEL 9
nodes) is retained for workloads that cannot migrate.

```yaml
apiVersion: worker.kubevirt.io/v1alpha1
kind: WorkerPool
metadata:
  name: rh10-pool
spec:
  virtHandlerImage: registry.example.com/kubevirt/virt-handler:v1.0.0-rh10
  virtLauncherImage: registry.example.com/kubevirt/virt-launcher:v1.0.0-rh10
  nodeSelector:
    os-version: rh10
  vmiSelector:
    vmiLabels:
      matchLabels:
        runtime: rh10
```

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### WorkerPool CRD

Worker pool configuration is defined via a standalone cluster-scoped
`WorkerPool` CRD in the `worker.kubevirt.io` API group, following the same
pattern as `MigrationPolicy` (`migrations.kubevirt.io`). Each `WorkerPool`
CR represents a single pool with its image overrides, node selector, and
VMI matching criteria.

Using a standalone CRD rather than embedding configuration in the `KubeVirt`
CR provides several advantages:

- Avoids polluting the KubeVirt CR with per-pool configuration
- Allows pool lifecycle management independent of the KubeVirt CR
- Enables RBAC scoping — administrators can delegate pool management without
  granting access to the entire KubeVirt CR
- Follows established KubeVirt patterns (`MigrationPolicy`)

The `WorkerPool` CRD is installed by virt-operator when the `WorkerPools`
feature gate is enabled. virt-operator watches `WorkerPool` CRs and
reconciles a DaemonSet for each. virt-controller lists `WorkerPool` CRs to
evaluate VMI-to-pool matching during virt-launcher pod rendering.

### VMI to Pool Matching

VMIs are matched to worker pools based on admin-configured selectors. The
matching is evaluated by the virt-controller when rendering the virt-launcher
pod and does not require any pool-specific configuration from the VMI user.

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
- **Device names** (used in `vmiSelector.deviceNames`): Kubernetes device plugin
  resource names as used in VMI `spec.domain.devices.gpus[].deviceName` and
  `spec.domain.devices.hostDevices[].deviceName`. In KubeVirt these are
  per-model names registered via `permittedHostDevices` in the KubeVirt CR
  (e.g., `nvidia.com/TU104GL_Tesla_T4`). These control which VMIs are matched
  to the pool.

#### Selector types

Each pool defines a `vmiSelector` with two optional criteria, evaluated with OR
semantics (either matching is sufficient):

- **deviceNames**: Matches if any GPU or HostDevice requested by the VMI
  appears in the pool's `deviceNames` list.
- **vmiLabels**: Matches if the VMI's labels satisfy the label selector
  (supports both `matchLabels` and `matchExpressions` via the standard
  `metav1.LabelSelector`).

#### Matching rules

1. `WorkerPool` CRs are evaluated in alphabetical order by name (first match
   wins). For example, if a VMI matches `fpga-pool` by device and
   `secure-pool` by labels, `fpga-pool` wins because it comes first
   alphabetically.
2. Within a pool's `vmiSelector`, `deviceNames` and `vmiLabels` are OR'd —
   either matching is sufficient
3. If no pool matches, the default virt-launcher image is used and
   anti-affinity for all pool `nodeSelector` labels is added to the
   virt-launcher pod, ensuring it lands on a node served by the primary
   virt-handler

#### Node placement

The pool's `nodeSelector` is used for two purposes:

- **DaemonSet scheduling**: The pool's virt-handler DaemonSet is scheduled on
  nodes matching the `nodeSelector`
- **VMI pod scheduling**: When a VMI matches a pool, the pool's `nodeSelector`
  is merged into the virt-launcher pod's required node affinity, ensuring the
  VMI lands on a node served by that pool's virt-handler
- **Unmatched VMI pod scheduling**: When a VMI does not match any pool,
  anti-affinity for each pool's `nodeSelector` labels is added to the
  virt-launcher pod's required node affinity (using `NotIn` expressions).
  This prevents unmatched VMIs from landing on pool nodes, ensuring they
  only run on nodes served by the primary virt-handler with the default
  virt-launcher image.

**Matching examples:**

| VMI devices | VMI labels | Pool deviceNames | Pool vmiLabels | Match? |
|-------------|------------|------------------|----------------|--------|
| `nvidia.com/TU104GL_Tesla_T4` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | Yes (device) |
| `{}` | `{workload-class: secure}` | — | `{workload-class: secure}` | Yes (label) |
| `nvidia.com/TU104GL_Tesla_T4` | `{workload-class: secure}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | Yes (device) |
| `intel.com/fpga` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | — | No |
| `{}` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | `{workload-class: secure}` | No |
| `nvidia.com/TU104GL_Tesla_T4` | `{}` | `["nvidia.com/TU104GL_Tesla_T4"]` | `{workload-class: secure}` | Yes (device, OR) |

### Component Changes

#### virt-operator

- Installs the `WorkerPool` CRD when the `WorkerPools` feature gate is enabled
- Watches `WorkerPool` CRs and reconciles one DaemonSet per CR
- Applies each pool's `nodeSelector` to its DaemonSet
- Configures anti-affinity on the primary virt-handler to exclude nodes claimed
  by worker pools
- Blocks pool deletion while matched VMIs are still running — the pool's
  DaemonSet is only deleted once impacted nodes have been drained

#### virt-handler

No changes to virt-handler itself are required. Additional DaemonSets run the
same virt-handler binary (or a custom image); pool-aware behavior is handled
entirely by virt-operator and virt-controller.

#### virt-controller

- Lists `WorkerPool` CRs (ordered alphabetically) and evaluates VMIs against
  pool `vmiSelector` to select the appropriate virt-launcher image
- Merges the matched pool's `nodeSelector` into the virt-launcher pod's node
  affinity
- For unmatched VMIs, adds anti-affinity for all pool `nodeSelector` labels
  to the virt-launcher pod, preventing placement on pool nodes
- Annotates matched virt-launcher pods with the pool name
- Detects outdated VMIs when pool configurations change (workload-updater)

## API Examples

A new cluster-scoped `WorkerPool` CRD is introduced in the
`worker.kubevirt.io/v1alpha1` API group. Each CR defines a pool with optional
image overrides, a `nodeSelector`, and a `vmiSelector` for matching VMIs:

```go
// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object
// +k8s:openapi-gen=true
// +genclient
// +genclient:nonNamespaced
type WorkerPool struct {
    metav1.TypeMeta   `json:",inline"`
    metav1.ObjectMeta `json:"metadata,omitempty"`
    Spec              WorkerPoolSpec `json:"spec"`
}

type WorkerPoolSpec struct {
    VirtHandlerImage  string            `json:"virtHandlerImage,omitempty"`
    VirtLauncherImage string            `json:"virtLauncherImage,omitempty"`
    NodeSelector      map[string]string `json:"nodeSelector"`
    VMISelector       Selector          `json:"vmiSelector"`
}

type Selector struct {
    DeviceNames []string              `json:"deviceNames,omitempty"`
    VMILabels   *metav1.LabelSelector `json:"vmiLabels,omitempty"`
}

// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object
type WorkerPoolList struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    Items           []WorkerPool `json:"items"`
}
```

At least one of `virtHandlerImage` or `virtLauncherImage` must be set.
`nodeSelector` is required and controls both DaemonSet scheduling and VMI pod
placement. `vmiSelector` is required and must define at least one of
`deviceNames` or `vmiLabels`.

### Validation

Pool name uniqueness is enforced by Kubernetes (each `WorkerPool` CR has a
unique `metadata.name`). Overlapping `vmiSelector` or `nodeSelector` values
across pools produce warnings (not rejections) since first-match-wins
(alphabetical by CR name) and anti-affinity provide deterministic behavior.

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

### Embedded in KubeVirt CR

Define worker pools as a `workerPools` list field directly in `KubeVirtSpec`,
with each entry containing the pool name, image overrides, node selector,
and VMI selectors.

**Rejected because:**

- Pollutes the KubeVirt CR with per-pool configuration that grows with each
  additional pool
- No RBAC scoping — pool management requires write access to the entire
  KubeVirt CR
- Pool lifecycle is coupled to the KubeVirt CR, making independent
  creation/deletion of pools harder to manage
- Inconsistent with the standalone CRD pattern established by `MigrationPolicy`

This was the original design proposed in this VEP. Reviewer feedback
suggested following the `MigrationPolicy` pattern with a standalone CRD
instead.

### nodeSelector-Only Matching

Require VM users to set specific `nodeSelector` labels on VMIs to match pools.

**Rejected because:**

- Leaks infrastructure details (node labels) to VMI users
- Requires user coordination with administrators
- Users targeting specific hardware (e.g., GPUs) already express intent via
  device requests — duplicating that intent in `nodeSelector` is redundant

## Scalability

Each `WorkerPool` CR creates one additional DaemonSet. The number of pools is
expected to be small (single digits) as it corresponds to distinct node pool
types in the cluster. The VMI-to-pool matching checks device names and labels
with O(n) complexity over the pool list, performed once per VMI pod creation.

## Update/Rollback Compatibility

**Upgrade:**

- Existing deployments continue to work with single virt-handler
- Additional worker pools can be added incrementally
- Running VMIs are not affected until restart
- Custom per-pool `virtHandlerImage` and `virtLauncherImage` references are
  **not** automatically updated during a KubeVirt upgrade. Administrators must
  manually update these image references in the corresponding `WorkerPool` CRs
  to match the new KubeVirt version. The workload-updater will detect outdated
  VMIs once the pool's launcher image is updated.

**Pool removal:**

- Deleting a `WorkerPool` CR is blocked (via a finalizer) while VMIs matched
  to the pool are still running on worker nodes. Administrators must drain
  impacted nodes before the pool can be deleted and its DaemonSet removed.

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
- [ ] `WorkerPool` CRD in `worker.kubevirt.io/v1alpha1` (cluster-scoped)
- [ ] CRD installation by virt-operator when feature gate is enabled
- [ ] DaemonSet creation and deletion per `WorkerPool` CR
- [ ] VMI matching by device names and/or VMI labels
- [ ] Pool `nodeSelector` merged into matched VMI's virt-launcher pod node
  affinity
- [ ] Anti-affinity on primary virt-handler
- [ ] Finalizer-based deletion protection while matched VMIs are running
- [ ] Unit and functional tests
- [ ] User documentation

### Beta

- [ ] Per-pool hypervisor backend configuration via optional `hypervisor` field
  in `WorkerPoolSpec`, enabling mixed-hypervisor clusters (e.g., KVM on some
  nodes, MSHV on others) building on the hypervisor abstraction layer
  ([VEP #97](../hypervisor-abstraction.md))

### GA

- [ ] Criteria to be defined based on Beta feedback
