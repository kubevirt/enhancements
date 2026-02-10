# VEP #200: Virt-Handler Pools

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

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

The [kubevirt-aie-webhook](https://github.com/kubevirt/kubevirt-aie-webhook)
project demonstrates the demand for this capability — it implements an external
mutating webhook that replaces virt-launcher images based on VMI device and
label selectors. This VEP proposes folding that pattern into KubeVirt natively,
removing the need for an external webhook and extending it to also support
per-pool virt-handler customization.

## Goals

- Enable operators to deploy additional virt-handler DaemonSets targeting
  specific node pools
- Allow custom virt-handler and virt-launcher images per node pool
- Automatically and transparently select the appropriate virt-launcher image
  for VMIs based on admin-configured selectors that match VMI device requests
  and/or labels
- Maintain backward compatibility with existing single-handler deployments
- Require no VMI-level changes from end users — pool selection is entirely
  admin-configured

## Non Goals

- Runtime image switching for running VMIs
- Automatic detection of node capabilities
- Cross-pool live migration with image transformation
- Multi-hypervisor support: the hypervisor abstraction layer
  ([VEP #97](../hypervisor-abstraction.md)) introduces per-cluster hypervisor
  configuration. Virt-handler pools are a natural mechanism for extending this
  to per-pool hypervisor backends (e.g., KVM on some nodes, MSHV on others),
  but this is out of scope for the initial implementation. A future iteration
  could add an optional `hypervisor` field to `VirtHandlerPoolConfig` to
  enable mixed-hypervisor clusters.

## Definition of Users

- **Cluster administrators**: Configure virt-handler pools via the KubeVirt CR
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
        - VirtHandlerPools
    permittedHostDevices:
      pciHostDevices:
        - pciVendorSelector: "10DE:1EB8"
          resourceName: "nvidia.com/TU104GL_Tesla_T4"
  virtHandlerPools:
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
        - VirtHandlerPools
  virtHandlerPools:
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
        - VirtHandlerPools
  virtHandlerPools:
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
        - VirtHandlerPools
  virtHandlerPools:
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

VMIs are matched to handler pools based on admin-configured selectors. The
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

- **deviceNames**: Matches if any GPU
  (`vmi.Spec.Domain.Devices.GPUs[].DeviceName`) or HostDevice
  (`vmi.Spec.Domain.Devices.HostDevices[].DeviceName`) in the VMI spec appears
  in the pool's `deviceNames` list.
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
  is merged into the virt-launcher pod's node affinity as an additional
  `RequiredDuringSchedulingIgnoredDuringExecution` term alongside any existing
  affinity from the VMI spec, ensuring the VMI lands on a node served by that
  pool's virt-handler

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

- Creates additional DaemonSets (`virt-handler-<name>`) for each entry in
  `virtHandlerPools`
- Applies the pool's `nodeSelector` to the DaemonSet's pod scheduling
  constraints
- Adds `kubevirt.io/handler-pool: <name>` label to additional DaemonSets
- Injects `RequiredDuringSchedulingIgnoredDuringExecution` node affinity on the
  primary virt-handler with `NotIn` expressions to avoid nodes matching any
  pool's `nodeSelector`
- Deletes additional DaemonSets when removed from configuration

#### virt-controller (TemplateService)

- Evaluates the VMI against pool selectors using
  `handlermatcher.MatchVMIToHandlerPool()` (checks `deviceNames` and
  `vmLabels`)
- Uses `handlermatcher.GetLauncherImageForVMI()` to select virt-launcher image
- Merges the matched pool's `nodeSelector` into the virt-launcher pod's node
  affinity
- Adds `kubevirt.io/handler-pool` annotation to virt-launcher pods identifying
  the handler pool

#### virt-controller (workload-updater)

- Uses `GetLauncherImageForVMI()` to determine expected launcher image per VMI
- Correctly identifies outdated VMIs when handler configurations change

## API Examples

Add a new field `virtHandlerPools` to `KubeVirtSpec`:

```go
// KubeVirtSpec (in types.go)
type KubeVirtSpec struct {
    // ... existing fields ...

    // virtHandlerPools configures additional virt-handler DaemonSets
    // targeting specific nodes with custom images, matched to VMIs via
    // device and label selectors.
    // +optional
    VirtHandlerPools []VirtHandlerPoolConfig `json:"virtHandlerPools,omitempty"`
}
```

Add new types for pool configuration:

```go
// VirtHandlerPoolConfig defines configuration for an additional virt-handler
// DaemonSet that targets specific nodes with custom images and automatically
// matches VMIs via device and label selectors.
type VirtHandlerPoolConfig struct {
    // name is a unique identifier appended to "virt-handler" to form the
    // DaemonSet name. For example, "gpu" results in a DaemonSet named
    // "virt-handler-gpu".
    // +kubebuilder:validation:Required
    // +kubebuilder:validation:Pattern=`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`
    // +kubebuilder:validation:MaxLength=48
    Name string `json:"name"`

    // virtHandlerImage overrides the virt-handler container image for this
    // DaemonSet. If not specified, the default virt-handler image is used.
    // +optional
    VirtHandlerImage string `json:"virtHandlerImage,omitempty"`

    // virtLauncherImage overrides the virt-launcher image used by virt-launcher
    // pods on nodes served by this handler. If not specified, the default
    // virt-launcher image is used.
    // +optional
    VirtLauncherImage string `json:"virtLauncherImage,omitempty"`

    // nodeSelector specifies labels that must match a node's labels for this
    // DaemonSet's pods to be scheduled on that node. When a VMI matches this
    // pool's selector, the nodeSelector is also merged into the virt-launcher
    // pod's node affinity.
    // +kubebuilder:validation:Required
    // +kubebuilder:validation:MinProperties=1
    NodeSelector map[string]string `json:"nodeSelector"`

    // selector defines the criteria for matching VMIs to this pool. A VMI
    // matches if any of the selector's criteria are met (OR semantics).
    // +kubebuilder:validation:Required
    Selector VirtHandlerPoolSelector `json:"selector"`
}

// VirtHandlerPoolSelector defines the criteria for matching VMIs to a pool.
// DeviceNames and VMLabels are OR'd: if either matches, the pool applies.
type VirtHandlerPoolSelector struct {
    // deviceNames matches VMIs that request any of the listed device names
    // via spec.domain.devices.gpus[].deviceName or
    // spec.domain.devices.hostDevices[].deviceName.
    // +optional
    DeviceNames []string `json:"deviceNames,omitempty"`

    // vmLabels matches VMIs whose labels contain all of the specified
    // key-value pairs.
    // +optional
    VMLabels *VirtHandlerPoolVMLabels `json:"vmLabels,omitempty"`
}

// VirtHandlerPoolVMLabels matches VMIs by label selectors.
type VirtHandlerPoolVMLabels struct {
    // matchLabels is a map of key-value pairs. A VMI matches if all
    // entries are present in the VMI's labels.
    // +kubebuilder:validation:MinProperties=1
    MatchLabels map[string]string `json:"matchLabels"`
}
```

### Validation Rules

- Pool must specify at least one of `virtHandlerImage` or `virtLauncherImage`
- Pool `selector` must define at least one of `deviceNames` or `vmLabels`
- Pool `deviceNames` entries must be non-empty strings
- Pool `vmLabels.matchLabels` must have at least one entry when `vmLabels` is
  set
- Pool names must be unique across the `virtHandlerPools` list
- Two pools should not have overlapping selectors that could match the same VMI
  (warning, not rejection, as first-match-wins provides deterministic behavior)

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

Use external mutating webhook to inject images based on device/label matching.

**Rejected because:**

- External dependency with its own lifecycle management
- Doesn't address virt-handler customization
- Fragile - webhook failures block VMI creation
- This is the approach used by
  [kubevirt-aie-webhook](https://github.com/kubevirt/kubevirt-aie-webhook),
  which this VEP supersedes by folding the capability natively into KubeVirt

### nodeSelector-Only Matching

Require VM users to set specific `nodeSelector` labels on VMIs to match pools.

**Rejected because:**

- Leaks infrastructure details (node labels) to VM users
- Requires user coordination with administrators
- Users targeting specific hardware (e.g., GPUs) already express intent via
  device requests — duplicating that intent in `nodeSelector` is redundant

## Scalability

Each entry in `virtHandlerPools` creates one additional DaemonSet. The number of
pools is expected to be small (single digits) as it corresponds to distinct node
pool types in the cluster. The VMI-to-pool matching checks device names and
labels with O(n) complexity over the pool list, performed once per VMI pod
creation.

## Update/Rollback Compatibility

**Upgrade:**

- Existing deployments continue to work with single virt-handler
- Additional handler pools can be added incrementally
- Running VMIs are not affected until restart
- Custom per-pool `virtHandlerImage` and `virtLauncherImage` references are
  **not** automatically updated during a KubeVirt upgrade. Administrators must
  manually update these image references in the KubeVirt CR to match the new
  KubeVirt version. The workload-updater will detect outdated VMIs once the
  pool's launcher image is updated.

**Downgrade:**

- Additional DaemonSets are deleted when feature gate is disabled
- Running VMIs served by additional handler pools continue running
- New VMIs use default images

**Version Skew:**

All virt-handler and virt-launcher images (including custom per-pool overrides)
must be built from the same KubeVirt version. Using mismatched versions is
unsupported and may cause undefined behavior.

## Functional Testing Approach

### Unit Tests

`pkg/virt-controller/services/handlermatcher_test.go`:

- VMI with no devices and no labels returns nil (no pool match)
- VMI with matching GPU deviceName returns pool
- VMI with matching HostDevice deviceName returns pool
- VMI with matching vmLabels returns pool
- VMI matching both deviceNames and vmLabels returns pool (OR)
- VMI with non-matching deviceName returns nil
- VMI with partial vmLabels match returns nil
- Multiple pools returns first match
- Launcher image selection with and without custom images
- Pool nodeSelector is merged into virt-launcher pod node affinity on match

### Functional Tests

`tests/operator/operator.go` (Context: "with VirtHandlerPools feature gate"):

- Creates additional virt-handler DaemonSet when feature gate is enabled
- Verifies DaemonSet has correct handler-pool label
- Verifies DaemonSet has configured nodeSelector
- Additional virt-handler pod runs on labeled nodes
- Deletes additional DaemonSet when removed from configuration
- Uses custom images when specified
- VMI with matching device is matched to pool and gets pool annotation
- VMI with matching labels is matched to pool and gets pool annotation
- Matched VMI's virt-launcher pod has pool's nodeSelector in node affinity
- Unmatched VMI uses default launcher image
- Pool with custom virt-handler image deploys DaemonSet with overridden image
- Configures anti-affinity on primary virt-handler to avoid handler pool nodes
- Workload-updater detects outdated VMIs when pool launcher image changes

## Implementation History

## Graduation Requirements

### Alpha

- Feature gate `VirtHandlerPools` (disabled by default)
- API types `VirtHandlerPoolConfig`, `VirtHandlerPoolSelector`,
  `VirtHandlerPoolVMLabels`
- DaemonSet creation and deletion
- VMI matching by device names and/or VM labels
- Pool `nodeSelector` merged into matched VMI's virt-launcher pod node affinity
- Anti-affinity on primary handler
- Unit and functional tests
- User documentation

### Beta

### GA
