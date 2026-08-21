# VEP #300: Managed DRA Claims

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds managed DRA claim generation to KubeVirt via a new
`ManagedClaimProvisioner` CRD. Admins create provisioner objects that
encode DeviceClass names and the name of the provisioner controller.
Users reference a provisioner by name in the VMI spec and declare their
devices as they do today. The managed-claim framework invokes the
provisioner to generate the `ResourceClaim` automatically.

The design extends the approach sketched in
[VEP-10 Appendix C](../10-dra-devices/vep.md#c-managed-resource-claims)
and incorporates feedback from the VEP-300 tracking issue discussion.

## Motivation

DRA claim authoring is complex. A user who wants a GPU and SR-IOV NIC
co-placed on the same PCIe root must:

1. Know the exact DeviceClass names deployed on their cluster
2. Know topology attribute names (`resource.kubernetes.io/pcieRoot`)
3. Author a `ResourceClaim` or `ResourceClaimTemplate` with per-device
   requests and `matchAttribute` constraints
4. Wire `claimName` and `requestName` references from the VMI spec into
   the claim

The VMI spec already says "I have a GPU and a NIC." The topology intent
("put them on the same PCIe root") should not require re-expressing that
in a separate Kubernetes object.

With a `ManagedClaimProvisioner`, the admin selects the provisioner and
provides DeviceClass mappings. Users reference the provisioner by name
and declare their devices. The provisioner receives all device
declarations for the managed claim and assembles the `ResourceClaim`.

## Goals

- Let users express topology-aligned multi-device claims without
  hand-authoring `ResourceClaim` objects
- Separate admin concerns (provisioner selection and DeviceClass
  mappings) from user concerns (device declarations) via the
  provisioner CRD
- Support extensibility via independent provisioner controllers
- Reuse existing device declaration patterns (`gpus[]`, `hostDevices[]`,
  `networks[]`, `cpu.dra`) as the source of truth for claim generation

## Non Goals

- Replace explicit `resourceClaimName` or `resourceClaimTemplateName`
  references (these remain as power-user escape hatches)
- Define CPU consumption behavior (owned by VEP-152)
- Define guest NUMA topology construction (owned by VEP-115)
- Support live migration of DRA-backed devices

## Definition of Users

- **User:** a person who wants DRA devices in a VM.
- **Admin:** a person who deploys DRA drivers, creates DeviceClass
  resources, and creates `ManagedClaimProvisioner` objects
- **Developer:** a person building custom provisioner controllers or
  automation on top of KubeVirt DRA APIs

## User Stories

- As a user, I want to request a GPU and NIC co-placed on the same PCIe
  root without learning DRA claim syntax or knowing DeviceClass names
- As a user, I want to request GPUs, NICs, and CPUs on the same NUMA
  node with a single provisioner reference
- As an admin, I want to define DeviceClass mappings once and have all
  users reference them by name
- As a developer, I want to implement a custom provisioner
  with my own claim generation logic

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Feature Gate

All changes are gated behind `ManagedDRAClaims` (alpha, off by default).

### Responsibility Boundary

- **User owns:** device declarations (`gpus[]`, `hostDevices[]`,
  `networks[]`, `cpu.dra`)
- **Admin owns:** `ManagedClaimProvisioner` objects (DeviceClass
  mappings and provisioner controller selection)
- **Managed-claim framework owns:** provisioner-controller watches,
  reconciliation helpers, and ResourceClaim lifecycle
- **Virt-controller owns:** rendering managed claim references into the
  launcher pod
- **Claim provisioner owns:** desired ResourceClaim device requests and
  constraints for one managed claim
- **Scheduler owns:** device allocation, topology constraint satisfaction

### Scope Boundary with VEP-152

VEP-152 and VEP-300 are developed concurrently:

- **VEP-152 owns:** `cpu.dra` struct (`CPUDRASource`), `deviceClassName`
  on `CPUDRASource`, CPU consumption (virt-launcher vCPU pinning), CPU
  accounting formula, `CPUsWithDRA` feature gate, unified `dedicated` API
- **VEP-300 owns:** `ManagedClaimProvisioner` CRD,
  `managedClaimProvisionerName` field on
  `VirtualMachineInstanceResourceClaim`, the managed-claim provisioning
  framework, and the `ManagedDRAClaims` feature gate

VEP-152's future `autoClaim` path for CPU-only claims is superseded by
VEP-300's managed claims, which handle CPUs as part of cross-device claims.

### External Dependencies

- [KEP-6072](https://github.com/kubernetes/enhancements/issues/6072)
  (standard topology attributes): GA in Kubernetes 1.37. Standardizes
  `resource.kubernetes.io/numaNode`.
- [KEP-5491](https://github.com/kubernetes/enhancements/issues/5491)
  (list-typed attributes): alpha in Kubernetes 1.36. Required for
  `pcieRoot` alignment with CPUs. The CPU DRA driver publishes `pcieRoot`
  as a list (a CPU group is affine to multiple PCIe roots), while GPU/NIC
  drivers publish it as a scalar. KEP-5491 redefines `matchAttribute` as
  non-empty set intersection, enabling cross-device alignment. See
  [dra-driver-cpu#114](https://github.com/kubernetes-sigs/dra-driver-cpu/issues/114).
- [dra-driver-cpu](https://github.com/kubernetes-sigs/dra-driver-cpu)
  v0.2.0+: publishes `pcieRoot` list attribute in grouped mode (PR #163).

### API Changes

#### New CRD: `ManagedClaimProvisioner`

A cluster-scoped resource that encodes DeviceClass mappings and the
provisioner controller name:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: ManagedClaimProvisioner
metadata:
  name: pcie-aligned
spec:
  # Identifies the controller responsible for claim generation.
  # Built-in: policy.kubevirt.io/aligner
  # Third-party controllers use their own name.
  provisioner: policy.kubevirt.io/aligner

  # DeviceClass mappings and optional DRA configuration.
  # Each name is one of cpu, gpu, hostDevice, or network.
  deviceTypes:
  - name: cpu
    deviceClassName: cpu.dra.k8s.io
  - name: gpu
    deviceClassName: gpu.example.com
    opaque:
      driver: gpu.example.com
      parameters:
        apiVersion: gpu.example.com/v1alpha1
        kind: GPUConfig
        iommu:
          backendPolicy: LegacyOnly
          enableAPIDevice: true
  - name: hostDevice
    deviceClassName: pci.example.com
  - name: network
    deviceClassName: sriov.example.com
```

The provisioner receives the managed claim entry and every VMI device
that references it. The provisioner chooses the generated
`ResourceClaim.spec.devices.constraints`, including any upstream
[`DeviceConstraint`](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/#DeviceConstraint)
objects. KubeVirt does not expose provisioner-specific constraint or
pairing policy in `ManagedClaimProvisioner.spec`.

`deviceTypes` is a named list. The name maps VMI device declarations to
the provisioner configuration: `cpu` maps to `domain.cpu.dra`, `gpu` to
`domain.devices.gpus[]`, `hostDevice` to `domain.devices.hostDevices[]`,
and `network` to `spec.networks[].resourceClaim`.

`opaque` is optional driver-specific configuration. When present, the
provisioner renders a Kubernetes `DeviceClaimConfiguration` in
`ResourceClaim.spec.devices.config`, with `requests` set to every
generated request for that device type and `opaque` copied from the
provisioner. This follows the ResourceClaim configuration model used
for KubeVirt GPU DRA claims.

```go
type ManagedClaimProvisionerSpec struct {
	Provisioner string `json:"provisioner"`

	// +listType=map
	// +listMapKey=name
	DeviceTypes []ManagedClaimDeviceType `json:"deviceTypes"`
}

type ManagedClaimDeviceType struct {
	Name            string `json:"name"`
	DeviceClassName string `json:"deviceClassName"`
	Opaque          *resourcev1.OpaqueDeviceConfiguration `json:"opaque,omitempty"`
}
```

The YAML fields map directly to this spec: `provisioner` identifies the
controller, and each `deviceTypes[]` entry maps to
`ManagedClaimDeviceType`.

#### Modified: `VirtualMachineInstanceResourceClaim`

Add `ManagedClaimProvisionerName` as a third mutually-exclusive option:

```go
type VirtualMachineInstanceResourceClaim struct {
	// Name uniquely identifies this resource claim inside the VMI.
	Name string `json:"name"`

	// ResourceClaimName is the name of a ResourceClaim object in the
	// same namespace as this VMI.
	// Exactly one of ResourceClaimName, ResourceClaimTemplateName, or
	// ManagedClaimProvisionerName must be set.
	ResourceClaimName *string `json:"resourceClaimName,omitempty"`

	// ResourceClaimTemplateName is the name of a ResourceClaimTemplate
	// object in the same namespace as this VMI.
	// Exactly one of ResourceClaimName, ResourceClaimTemplateName, or
	// ManagedClaimProvisionerName must be set.
	ResourceClaimTemplateName *string `json:"resourceClaimTemplateName,omitempty"`

	// ManagedClaimProvisionerName references a cluster-scoped
	// ManagedClaimProvisioner object that controls how the
	// ResourceClaim is generated.
	// The managed-claim framework passes VMI device declarations to
	// the matching provisioner controller, which generates a ResourceClaim.
	// Exactly one of ResourceClaimName, ResourceClaimTemplateName, or
	// ManagedClaimProvisionerName must be set.
	// +optional
	ManagedClaimProvisionerName *string `json:"managedClaimProvisionerName,omitempty"`
}
```

#### `GPU` and `HostDevice`

No new fields added to `GPU` or `HostDevice` by this VEP. The existing
structs are used as-is with their `ClaimRequest` fields (`claimName`,
`requestName`).

#### CPU (`CPUDRASource`, defined by VEP-152)

VEP-152 adds the following structure (shown here for reference):

```go
type CPU struct {
	// ... existing fields (Cores, Sockets, Threads, etc.) ...

	// DRA enables Dynamic Resource Allocation for CPU resources.
	// +optional
	DRA *CPUDRASource `json:"dra,omitempty"`
}

type CPUDRASource struct {
	// ClaimRequest references a specific request from a ResourceClaim
	// listed in vmi.spec.resourceClaims[].
	*ClaimRequest `json:",inline"`
}
```

VEP-300 scans `cpu.dra` during claim generation alongside the other
device types. The DeviceClassName is resolved from the provisioner
CRD's `cpu.deviceClassName` field. The CPU count in the
generated claim is derived from VEP-152's accounting formula
(`cores x sockets x threads + emulatorThreadCPUs + supplementalPoolThreadCount`).
See [VEP-152 (PR #414)](https://github.com/kubevirt/enhancements/pull/414)
for details.

### DeviceClassName Resolution

DeviceClass names are defined in the `ManagedClaimProvisioner` CRD.
The controller determines device type by which VMI field the device
appears in:

- `domain.devices.gpus[]` -> `deviceTypes[name=gpu]`
- `domain.devices.hostDevices[]` -> `deviceTypes[name=hostDevice]`
- `spec.networks[].resourceClaim` -> `deviceTypes[name=network]`
- `domain.cpu.dra` -> `deviceTypes[name=cpu]`

### Claim Generation Algorithm

```
GenerateClaim(managedClaimContext) -> ResourceClaimSpec:

  1. Collect device requests:
     a. Scan domain.devices.gpus[] - for each GPU where
        claimName == claimEntry.Name, look up DeviceClassName
        from deviceTypes[name=gpu], create a
        DeviceRequest with Name=requestName,
        DeviceClassName=resolved, Count=1.
     b. Scan domain.devices.hostDevices[] - same pattern,
        using deviceTypes[name=hostDevice].
     c. Scan spec.networks[] - for each network where
        resourceClaim.claimName == claimEntry.Name, look up
        DeviceClassName from deviceTypes[name=network],
        create a DeviceRequest.
     d. Scan domain.cpu.dra (VEP-152) - if claimName matches,
        look up DeviceClassName from deviceTypes[name=cpu],
        create a DeviceRequest with CPU count derived from
        VEP-152's accounting formula
        (cores x sockets x threads + emulator + IOThreads).
        The claim shape (capacity vs count) is determined by
        the DeviceClass and driver mode, not by the user.

  2. Validate:
     - At least one device must reference the claim.
     - Every device type must have a DeviceClassName in the
       provisioner CRD.
     - No duplicate requestName values.

  3. For each configured device type with opaque configuration, add a
     DeviceClaimConfiguration with the generated request names for that
     type and the configured opaque value.

  4. The provisioner builds spec.devices.constraints from the full set
     of collected requests. The built-in provisioner applies its
     PCIe-root and NUMA topology policy. Other provisioner controllers
     define their own constraint-generation behavior.

  5. Assemble ResourceClaim:
     - Name: <vmi-name>-<claim-name>
     - Namespace: vmi.Namespace
     - OwnerReference: VMI (controller=true, for GC)
     - Labels: kubevirt.io/managed-claim: <claim-name>
     - Spec.Devices.Requests: collected requests
     - Spec.Devices.Config: generated device configurations
     - Spec.Devices.Constraints: collected constraints
```

### Managed-Claim Provisioning Framework

The managed-claim framework is a reusable controller library. A
provisioner controller uses it to watch matching VMIs and
`ManagedClaimProvisioner` objects, collect the claim's devices, and
create or update the generated `ResourceClaim`.

KubeVirt ships a separate topology-aligner controller for
`policy.kubevirt.io/aligner`. A third party can install a separate
controller for its own provisioner name. Provisioner controllers are
not registered in virt-controller and do not need to be compiled into
KubeVirt.

```go
type ClaimProvisioner interface {
	GenerateClaim(ctx *ManagedClaimContext) (*resourcev1.ResourceClaimSpec, error)
}

type ManagedClaimContext struct {
	VMI         *v1.VirtualMachineInstance
	Claim       *v1.VirtualMachineInstanceResourceClaim
	Provisioner *v1alpha1.ManagedClaimProvisioner
	Devices     ManagedClaimDevices
}

type ManagedClaimDevices struct {
	GPUs        []v1.GPU
	HostDevices []v1.HostDevice
	Networks    []ManagedClaimNetwork
	CPU         *v1.CPUDRASource
}

type ManagedClaimNetwork struct {
	Network   v1.Network
	Interface *v1.Interface
}
```

`Devices` contains every declaration in the VMI that references
`Claim.Name`; it is not limited to a single device request. This lets a
provisioner choose its own request grouping and generate one or more
upstream `DeviceConstraint` objects without exposing that grouping in
the VMI or `ManagedClaimProvisioner` API.

The framework is initialized with the provisioner name that its
controller serves. The built-in topology-aligner controller uses:

```go
managedclaim.NewController(
    "policy.kubevirt.io/aligner",
    &TopologyAlignerProvisioner{},
)
```

For each `spec.resourceClaims[]` entry with
`managedClaimProvisionerName != nil` whose referenced
`ManagedClaimProvisioner.spec.provisioner` matches its configured name,
the provisioner controller:

1. Collects all VMI device declarations whose `claimName` matches the
   managed claim entry and constructs `ManagedClaimContext`.
2. Calls `GenerateClaim`.
3. Sets the deterministic claim name, namespace, labels, and VMI owner
   reference on the returned desired object.
4. Creates or updates the `ResourceClaim` and retries reconciliation on
   errors or observed changes.

The generated claim has labels that identify the managed claim entry
and provisioner:

```yaml
metadata:
  labels:
    kubevirt.io/managed-claim: <claim-entry-name>
    kubevirt.io/managed-claim-provisioner: <provisioner-name>
```

Virt-controller does not invoke `GenerateClaim` and does not create or
update managed claims. It derives the same deterministic claim name and
renders it into the launcher pod's `resourceClaims` entry. Pod creation
and ResourceClaim reconciliation proceed independently.

```
sync() -> check deletionTimestamp -> check dataVolumes ->
  check topologyHints -> handleBackendStorage() ->
  wait backendStorageReady ->
  RenderLaunchManifest() -> createPod()
```

The scheduler and DRA allocation wait until the referenced ResourceClaim
exists and becomes allocatable.

### Provisioner Expectations

Each provisioner controller uses expectations to avoid reconciling
before its ResourceClaim informer cache reflects a create or update.
The framework provides this common behavior to provisioner controllers.

### Implementation Details

**Claim naming:** the generated claim is named
`<vmi-name>-<claim-name>`. If this exceeds the 253-character DNS subdomain
limit, the name is truncated and a short hash suffix is appended to
preserve uniqueness.

**Idempotency:** provisioner reconciliation is naturally idempotent. If
the ResourceClaim already exists with the correct owner reference, the
framework converges it to the provisioner's desired spec.

**RBAC:** the built-in provisioner controller needs `create`, `get`,
`list`, `watch`, `update`, and `delete` permissions on `resourceclaims`
in the `resource.k8s.io` API group, plus `patch` permission on
`virtualmachineinstances/status`. Third-party provisioner controllers
need equivalent permissions in their own RBAC configuration.

**Multiple managed claims per VMI:** a VMI can have multiple
`resourceClaims[]` entries, each independently using
`managedClaimProvisionerName`, `resourceClaimName`, or
`resourceClaimTemplateName`. Each managed claim entry is reconciled
independently. The pod can be created while claims are reconciling; DRA
allocation waits for the corresponding claims.

### Validation

The validating webhook enforces:

1. **Mutual exclusion:** exactly one of `resourceClaimName`,
   `resourceClaimTemplateName`, or `managedClaimProvisionerName` must
   be set per `resourceClaims[]` entry.
2. **Feature gate:** `ManagedDRAClaims` must be enabled when
   `managedClaimProvisionerName` is used.
3. **Provisioner exists:** the referenced `ManagedClaimProvisioner`
   must exist at VMI creation time.
4. **Immutability:** `managedClaimProvisionerName` cannot be changed
   after VMI creation.
5. **Device coverage:** at least one device declaration must reference
   each managed claim entry (no empty claims).
6. **Device type configuration:** `deviceTypes[].name` must be unique
   and one of `cpu`, `gpu`, `hostDevice`, or `network`. Every device type
   referenced by devices in the managed claim must have a non-empty
   `deviceClassName`. When `opaque` is set, its `driver` must be set.
7. **Unique request names:** no duplicate `requestName` values within
   a managed claim.
### Error Handling

Provisioner controllers report their own failures through the existing
VMI status conditions list. The condition type
`ManagedClaimProvisioningFailed` includes the managed claim entry and
provisioner name in its reason and message; it is diagnostic only and
is not a pod-creation readiness handshake.

**Claim generation failure:** if a provisioner controller cannot
generate a claim (for example, no DeviceClassName is resolvable), it
sets a `ManagedClaimProvisioningFailed` condition on the VMI. The
condition reason is `FailedCreateResourceClaim`; its message identifies
the managed claim entry, provisioner name, and error. The controller
also emits a `FailedCreateResourceClaim` event and retries on the next
reconciliation. It clears the condition after successfully reconciling
the claim. The launcher pod remains pending until the claim exists and
can be allocated.

**ResourceClaim deleted externally:** if a managed claim's ResourceClaim
is deleted while the VMI is running, its provisioner controller detects
this through the informer and re-creates it on the next reconciliation.

**No provisioner controller:** if no controller serves the configured
provisioner name, no matching ResourceClaim appears and the launcher pod
remains pending. KubeVirt cannot distinguish this from a provisioner
controller that is temporarily unavailable.

**VMI deletion during claim creation:** the controller checks
`vmi.DeletionTimestamp` before creating claims. If the VMI is being
deleted, the controller skips claim creation. Owner reference GC handles
cleanup of any already-created claims.

## API Examples

### Single GPU

Admin creates provisioner:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: ManagedClaimProvisioner
metadata:
  name: gpu-default
spec:
  provisioner: policy.kubevirt.io/aligner
  deviceTypes:
  - name: gpu
    deviceClassName: gpu.example.com
    opaque:
      driver: gpu.example.com
      parameters:
        apiVersion: gpu.example.com/v1alpha1
        kind: GPUConfig
        iommu:
          backendPolicy: LegacyOnly
          enableAPIDevice: true
```

User creates VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-vm
spec:
  resourceClaims:
  - name: my-gpu
    managedClaimProvisionerName: gpu-default
  domain:
    devices:
      gpus:
      - name: gpu0
        claimName: my-gpu
        requestName: gpu
    resources:
      requests:
        memory: 8Gi
```

Generated `ResourceClaim`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: gpu-vm-my-gpu
  labels:
    kubevirt.io/managed-claim: my-gpu
  ownerReferences:
  - apiVersion: kubevirt.io/v1
    kind: VirtualMachineInstance
    name: gpu-vm
    controller: true
spec:
  devices:
    requests:
    - name: gpu
      exactly:
        deviceClassName: gpu.example.com
        count: 1
    config:
    - requests: [gpu]
      opaque:
        driver: gpu.example.com
        parameters:
          apiVersion: gpu.example.com/v1alpha1
          kind: GPUConfig
          iommu:
            backendPolicy: LegacyOnly
            enableAPIDevice: true
```

### GPU + NIC Co-Placed on PCIe Root

Admin creates provisioner:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: ManagedClaimProvisioner
metadata:
  name: pcie-aligned
spec:
  provisioner: policy.kubevirt.io/aligner
  deviceTypes:
  - name: gpu
    deviceClassName: gpu.example.com
  - name: network
    deviceClassName: sriov.example.com
```

User creates VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-nic-vm
spec:
  resourceClaims:
  - name: aligned-devices
    managedClaimProvisionerName: pcie-aligned
  domain:
    devices:
      gpus:
      - name: gpu0
        claimName: aligned-devices
        requestName: gpu
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 16Gi
  networks:
  - name: rdma-nic
    resourceClaim:
      claimName: aligned-devices
      requestName: nic
```

Generated `ResourceClaim`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: gpu-nic-vm-aligned-devices
  labels:
    kubevirt.io/managed-claim: aligned-devices
spec:
  devices:
    requests:
    - name: gpu
      exactly:
        deviceClassName: gpu.example.com
        count: 1
    - name: nic
      exactly:
        deviceClassName: sriov.example.com
        count: 1
    constraints:
    - matchAttribute: resource.kubernetes.io/pcieRoot
      requests: [gpu, nic]
```

### Multi-GPU + NIC + CPU on Same NUMA Node and PCIe Root

Admin creates provisioner:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: ManagedClaimProvisioner
metadata:
  name: hgx-b200-quarter
spec:
  provisioner: policy.kubevirt.io/aligner
  deviceTypes:
  - name: cpu
    deviceClassName: cpu.dra.k8s.io
  - name: gpu
    deviceClassName: gpu.example.com
  - name: network
    deviceClassName: sriov.example.com
```

User creates VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: full-topology-vm
spec:
  resourceClaims:
  - name: all-devices
    managedClaimProvisionerName: hgx-b200-quarter
  domain:
    cpu:
      cores: 16
      dra:
        claimName: all-devices
        requestName: cpus
    devices:
      gpus:
      - name: gpu0
        claimName: all-devices
        requestName: gpu0
      - name: gpu1
        claimName: all-devices
        requestName: gpu1
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 64Gi
  networks:
  - name: rdma-nic
    resourceClaim:
      claimName: all-devices
      requestName: nic
```

Generated `ResourceClaim`:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: full-topology-vm-all-devices
  labels:
    kubevirt.io/managed-claim: all-devices
spec:
  devices:
    requests:
    - name: gpu0
      exactly:
        deviceClassName: gpu.example.com
        count: 1
    - name: gpu1
      exactly:
        deviceClassName: gpu.example.com
        count: 1
    - name: nic
      exactly:
        deviceClassName: sriov.example.com
        count: 1
    - name: cpus
      exactly:
        deviceClassName: cpu.dra.k8s.io
        count: 16
    constraints:
    - matchAttribute: resource.kubernetes.io/pcieRoot
      requests: [gpu0, gpu1, nic]
    - matchAttribute: resource.kubernetes.io/numaNode
```

The built-in provisioner selected the GPU and NIC requests for the
PCIe-root constraint and all requests for the NUMA constraint. Its
pairing and constraint policy is implementation behavior, not part of
the `ManagedClaimProvisioner` API. The CPU DRA driver publishes
`pcieRoot` as a list attribute (KEP-5491); `matchAttribute` uses set
intersection.

## Alternatives

### Alternative 1: Inline `managedClaim` on VMI spec

The initial design (from
[VEP-10 Appendix C](../10-dra-devices/vep.md#c-managed-resource-claims))
placed device constraints (`matchAttribute` / `distinctAttribute`) and
DeviceClass names directly on the VMI spec via a `managedClaim` field on
`resourceClaims[]` and `deviceClassName` on each device declaration.
Admin defaults were configured in the KubeVirt CR
(`managedClaimDefaults`).

Rejected because:
- DeviceClass names and device constraints are admin concerns, not user
  concerns. Putting them in the VMI leaks infrastructure details.
- No extensibility for independent provisioner controllers without adding a
  `controllerName` field (which duplicates what the CRD's `provisioner`
  field does more cleanly).
- Admin defaults in the KubeVirt CR are a single global config. The
  CRD allows multiple provisioner profiles per cluster.

### Alternative 2: Webhook-based claim generation

The claim generation logic runs in a mutating admission webhook instead
of a controller. The webhook creates the ResourceClaim during VMI
admission and replaces `managedClaim` with `resourceClaimName` before
the VMI is persisted.

Rejected because:
- Creating Kubernetes objects during admission is an anti-pattern
  (blocks the request, can leak objects if a downstream webhook
  rejects the VMI).
- No retry on failure (webhook fails, VMI creation fails).
- Kubernetes made the same decision when implementing DRA extended
  resources in the scheduler instead of a webhook.

### Alternative 3: No KubeVirt managed-claim framework

Users install a separate controller (for example, a topology
coordinator) that watches VMIs and generates ResourceClaims
independently. KubeVirt has no managed-claim API or framework.

Rejected because:
- No standard contract between KubeVirt and separate controllers
  (how does virt-controller know when to proceed with pod creation?).
- Every external implementation reinvents the same integration
  (naming, expectations, ownership).
- KubeVirt should provide a built-in default implementation while
  allowing independent provisioner controllers.

## Implementation History

- 2026-08-05: Initial VEP draft based on VEP-10 Appendix C design
- 2026-08-11: Redesigned with ManagedClaimProvisioner CRD based on
  feedback from Alay Patel (VEP owner)
- 2026-08-12: Added controller-based generation
- 2026-08-13: Aligned CPUDRASource with VEP-152

## Scalability

Managed claim generation adds one `ResourceClaim` CREATE per managed
claim entry during VMI reconciliation. This follows the existing DRA
scalability model. See
[VEP-10 Scalability](../10-dra-devices/vep.md#scalability).

## Update/Rollback Compatibility

- API changes are additive and gated by `ManagedDRAClaims`. With the
  gate disabled, existing behavior is unchanged.
- Rollback: disable the feature gate and delete VMIs using managed
  claims before downgrading.
- Generated `ResourceClaim` objects are standard Kubernetes resources
  and are cleaned up by owner-reference GC.

## Functional Testing Approach

- Unit tests for framework reconciliation and `GenerateClaim`: single
  device, multi-device, built-in topology constraint generation,
  opaque configuration rendering, and error cases
- Unit tests for validation: mutual exclusion, missing DeviceClassName,
  invalid device type configuration, empty claims, duplicate request
  names, and provisioner existence
- Integration tests with fake ManagedClaimProvisioner objects (envtest)
- Integration tests: provisioning failure sets and successful
  reconciliation clears `ManagedClaimProvisioningFailed`
- E2E: VMI with managed claim for GPU (requires DRA driver in CI)

## Graduation Requirements

### Alpha

- `ManagedClaimProvisioner` CRD (cluster-scoped)
- `managedClaimProvisionerName` field on
  `VirtualMachineInstanceResourceClaim`
- Managed-claim framework and built-in topology provisioner
  (`policy.kubevirt.io/aligner`)
- API changes behind `ManagedDRAClaims` feature gate (off by default)
- GPU, HostDevice, Network, and CPU support
- Built-in PCIe-root and NUMA topology constraint generation
- Opaque device configuration rendering
- Controller-based claim generation with deterministic claim naming
- User-visible provisioning failure condition
- Independent provisioner controllers selected by `provisioner`
- Validation
- Unit tests and mock e2e tests
- Requires KEP-6072 (GA in Kubernetes 1.37)
- `pcieRoot` alignment with CPUs requires KEP-5491 (alpha in Kubernetes
  1.36)

### Beta

- Feature gate on by default
- User documentation
- Real-driver e2e testing

### GA

- Upgrade/downgrade testing
- Scale testing on large nodes

## Future Extensions

- **Partition mode:** provisioner defines device counts and resources,
  user references provisioner name without declaring devices
- **Multiple DeviceClasses per device type:** array of DeviceClass
  entries with names, devices reference by name
- **Memory and hugepages:** when the CPU DRA driver adds memory
  allocation support, memory can participate in managed claims

## References

- [VEP-10: Support GPUs with DRA](../10-dra-devices/vep.md) (Appendix C:
  Managed Resource Claims)
- [VEP-115: PCIe NUMA Topology Awareness](../115-pcie-numa-topology-awareness/vep.md)
- [VEP-152: Support CPUs with DRA (PR #414)](https://github.com/kubevirt/enhancements/pull/414)
- [VEP-183: DRA for Network Devices](../../sig-network/183-dra-network/vep.md)
- [ResourceClaim DeviceConstraint](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/#DeviceConstraint)
- [KEP-6072: Standard Topology Attributes](https://github.com/kubernetes/enhancements/issues/6072)
- [KEP-5491: List Types for Attributes](https://github.com/kubernetes/enhancements/issues/5491)
- [KEP-5304: DRA Device Attributes Downward API](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api)
- [dra-driver-cpu#114: NIC/CPU alignment](https://github.com/kubernetes-sigs/dra-driver-cpu/issues/114)
