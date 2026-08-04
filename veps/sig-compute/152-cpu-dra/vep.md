# VEP #152: Support CPUs with DRA

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds support for [DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)-provisioned CPUs in KubeVirt.
It extends the KubeVirt CPU API with a DRA-specific field so VMs can claim topology-aware CPUs through standard DRA APIs (`ResourceClaim`, `ResourceClaimTemplate`, `DeviceClass`, `ResourceSlice`), while keeping the existing `dedicatedCpuPlacement` path.

With CPU DRA, CPU topology is advertised to the Kubernetes scheduler through `ResourceSlice` objects.
This makes CPU placement scheduler-visible and composable with other DRA resources such as GPUs, SR-IOV NICs and host devices.

This VEP uses [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) CPU DRA driver implementation.
That driver is an out-of-tree alternative to kubelet CPU Manager: it discovers node CPU topology, publishes CPUs via `ResourceSlice` (individual per-CPU devices or grouped consumable capacity), and pins claimed workloads through CDI/NRI.
It is mutually exclusive with kubelet CPU Manager on the same node.

Kubernetes is also closing the scheduler/kubelet accounting gap for DRA-managed node-allocatable resources (CPU, memory, and similar) via [KEP-5517](https://github.com/kubernetes/enhancements/issues/5517).
That work unifies DRA claim allocations with standard `node.status.allocatable` accounting so drivers like `dra-driver-cpu` no longer need fragile request-mirroring workarounds to avoid double-booking.

This VEP builds upon the core DRA infrastructure defined in VEP #10 ([kubevirt/enhancements/pull/11](https://github.com/kubevirt/enhancements/pull/11)).

## Motivation

Today, exclusive CPU placement in KubeVirt relies on `dedicatedCpuPlacement`. It requests a Guaranteed QoS virt-launcher pod so kubelet's static CPU Manager can pin exclusive host CPUs to the container. Which CPUs are chosen remains entirely in kubelet's hands after the pod is scheduled.

Operators can tune node-local policies such as Topology Manager to improve CPU/device alignment, but this remains a kubelet-local decision. The scheduler places the pod using aggregate resource requests, and only afterward kubelet choose the exclusive CPU set, so scheduling and topology allocation are not fully coordinated.
As a result:

- Topology-aware decisions happen too late — after bind — so a pod can land on a node where the desired CPU/device topology cannot be satisfied and fail or get a suboptimal pin set.
- Co-locating exclusive CPUs with GPUs, SR-IOV NICs, or host devices depends on node-local Topology Manager policy rather than a scheduler-visible, claim-level constraint.
- Users cannot express fine topology intent (NUMA, L3/uncore cache, CEL selectors, cross-device `matchAttribute`) in the VMI API the way DRA claims allow.

CPU DRA addresses this by advertising CPU topology through `ResourceSlice`, allocating via standard claims, and letting the scheduler and driver cooperate on placement before the pod starts. The CPU DRA driver enables VirtualMachine workloads to request CPUs with specific topology and performance characteristics.

Key advantages:

- **Fine-grained CPU allocation:** Claims can request dedicated cores, NUMA alignment, CEL selectors over per-CPU attributes, and cross-device constraints via `matchAttribute`.
- **Standard Kubernetes APIs:** Allocation uses `ResourceClaim`, `ResourceClaimTemplate`, and `DeviceClass` instead of kubelet-policy-specific side channels.
- **Better lifecycle management:** The driver manages allocation, reservation, cleanup, and reclaim of unused CPUs.
- **Shared DRA infrastructure:** Reuses the `ClaimRequest` and `resourceClaims[]` patterns from VEP #10, so CPUs compose with other DRA-backed devices.

## Goals

- Enable KubeVirt VMs to consume CPU resources via DRA using externally supplied DRA drivers (primarily [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu))
- Support both grouped-mode (consumable capacity) and individual-mode (per-CPU device) claim shapes

## Non Goals

- Deprecate existing `dedicatedCpuPlacement` / kubelet CPU Manager support
- Deploy or manage external DRA CPU drivers in-tree
- Live migration of VMIs with DRA-pinned cpusets
- Provide memory placement or NUMA memory binding via the CPU DRA driver
- NUMA passthrough with CPU DRA is out of scope for this VEP and will be addressed in [VEP #115](../115-pcie-numa-topology-awareness/pcie-numa-topology-awareness.md).
- Allow the same node to publish the same CPUs in both grouped and individual CPU DRA modes
- Support both kubelet CPU Manager (`static` policy) and the DRA CPU driver on the same node at the same time
- Auto/Managed claim (see [Appendix: Auto/Managed Claim Synthesis](#appendix-automanaged-claim-synthesis); covered by VEP #300)

## Definition of Users

- **User:** A person who wants exclusive, topology-aware CPUs for a VM via DRA
- **Admin:** A person who manages infrastructure, deploys the DRA CPU driver, and configures `DeviceClass` resources

## User Stories

- As a user, I want to use my CPU DRA driver with KubeVirt so that VM workloads can consume topology-aware CPUs the same way containers do
- As a user, I want to request exclusive CPUs through the existing DRA claim APIs without changing how I author claims for other devices
- As an admin, I want to control CPU allocation policies through DRA APIs without modifying KubeVirt configuration
- As a user running heterogeneous workloads, I want CPU allocation co-located with GPU or NIC devices via shared claims and constraints so the scheduler can select a node where all resources are available and topologically compatible

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

This design introduces a new feature gate: `CPUsWithDRA`.
All API changes are gated behind this feature gate so as not to break existing functionality.

### Responsibility boundary

- **Admin owns:**
  - driver/operator deployment
  - `DeviceClass` definitions
  - node mutual exclusivity for exclusive CPU allocation: run either kubelet CPU Manager (`static`) or the CPU DRA driver on a node, not both (`cpuManagerPolicy: none` on DRA CPU nodes; see [`dra-driver-cpu` configuration](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/docs/user/configuration.md))
- **KubeVirt owns:**
  - API mutual exclusivity of `cpu.dedicatedCpuPlacement` and `cpu.dra` (the two `CPUSource` options)
- **User owns:**
  - provisioning/policy of `ResourceClaim`/`ResourceClaimTemplate` objects
  - selecting claim/request references from provisioned `ResourceClaim`/`ResourceClaimTemplate` objects

### External Dependencies

- A DRA CPU driver is an external dependency; it is selected and deployed outside KubeVirt, and is not delivered by this VEP.
- This VEP uses [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) CPU DRA driver implementation.
- Driver capability requirements:
  - Must publish CPU availability through Kubernetes `ResourceSlice` objects
  - Must support the claim shapes KubeVirt will consume (grouped and/or individual mode, depending on cluster configuration)
  - Must publish the allocated CPU IDs and per-CPU topology attributes in a KEP-5304 device metadata file mounted into the virt-launcher compute container (see [VEP #10](../10-dra-devices/vep.md#device-metadata-via-dra-attributes-downward-api))
  - For claim-only accounting (no mirrored `resources.requests/limits.cpu`): must publish `nodeAllocatableResourceMappings` on `ResourceSlice` devices so claimed CPUs map into `node.status.allocatable` CPU (KEP-5517). Until `dra-driver-cpu` lands that, Alpha keeps request/limit mirroring as a workaround.
- **Grouped / consumable-capacity claims:** CPU grouped mode uses Kubernetes [DRA consumable capacity](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#consumable-capacity) (`DRAConsumableCapacity`). That feature is **beta and enabled by default on Kubernetes v1.36+**. KubeVirt does not add a separate feature gate for it; `CPUsWithDRA` remains the only KubeVirt gate. On older clusters the gate must be enabled explicitly, or only individual-mode claims should be used.
- **Alpha workaround:** Until the driver publishes node-allocatable mappings, `dra-driver-cpu` still expects the claimed CPU count mirrored in the pod's standard `resources.requests/limits.cpu` to avoid double-booking. Virt-controller derives mirrored requests/limits from the [CPU accounting](#cpu-accounting) host CPU total and validates against the claim when rendering the pod.

### CPU DRA driver

With CPU DRA, a driver runs on each node and publishes CPU availability through Kubernetes `ResourceSlice` objects.
[`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) supports two modes of operation via `--cpu-device-mode`.

#### Individual mode

Individual mode publishes each allocatable CPU as a separate DRA device.

Example:
```text
CPU 0
CPU 1
CPU 2
CPU 3
```

The scheduler can select exact CPUs based on attributes such as CPU ID, core, socket, NUMA node, or CPU type.
Individual mode is useful when a workload requires precise CPU placement.

#### Grouped mode

In grouped mode, CPUs are requested as consumable capacity from a device group (for example, a NUMA node or socket).
This claim shape depends on Kubernetes `DRAConsumableCapacity`, which is available by default on **v1.36+** clusters.

Example:
```text
NUMA node 0: capacity 32 CPUs
NUMA node 1: capacity 32 CPUs
```

A workload can request *N* CPUs from one group.
The scheduler chooses a suitable group, and the CPU DRA driver picks exact CPU IDs within that group during allocation or preparation.
Grouped mode is useful when a workload needs exclusive CPUs without selecting each CPU individually — for example, "give this VM *N* exclusive CPUs from a suitable NUMA node or socket."

Today, when the driver picks CPUs from a group, it tries (best-effort) to keep them within a single L3/uncore cache to reduce cache latency and contention (`PreferAlignByUnCoreCache`, on by default). The user cannot request a specific L3 domain; it is only an allocation preference inside the chosen NUMA/socket group.

In the future, [partitionable devices](https://github.com/kubernetes/enhancements/blob/master/keps/sig-scheduling/4815-dra-partitionable-devices/README.md) could model the L3 cache as its own selectable group under a NUMA parent. That would let a claim explicitly target CPUs from one L3 domain, instead of relying on best-effort alignment.

KubeVirt's API is mode-agnostic: both claim shapes are consumed through the same `domain.cpu.dra` `ClaimRequest` reference.

### API Changes

Exclusive CPU allocation is modeled as a single inline `CPUSource` on `CPU`, with two mutually exclusive options:

- `dedicatedCpuPlacement` — kubelet CPU Manager (`static` policy) path (existing behavior)
- `dra` — DRA claim path (alternative; reuses `ClaimRequest` from [VEP #10](../10-dra-devices/vep.md))

`CPUSource` is embedded with `json:",inline"`, so existing YAML (`cpu.dedicatedCpuPlacement`) stays unchanged and Go field promotion keeps `cpu.DedicatedCPUPlacement` working.

```go
type CPU struct {
	// Cores, Sockets, Threads, Model, Features, IsolateEmulatorThread, etc.
	// ...

	// CPUSource selects how exclusive host CPUs are obtained.
	// At most one of DedicatedCPUPlacement or DRA may be set.
	// Both unset is valid (non-exclusive CPUs).
	CPUSource `json:",inline"`
}

// CPUSource is the exclusive-CPU allocation source for a VMI.
type CPUSource struct {
	// DedicatedCPUPlacement requests the scheduler to place the VirtualMachineInstance on a node
	// with enough dedicated pCPUs and pin the vCPUs to it.
	// +optional
	DedicatedCPUPlacement bool `json:"dedicatedCpuPlacement,omitempty"`

	// DRA requests exclusive / topology-aware CPUs via a ResourceClaim
	// listed in vmi.spec.resourceClaims[]. This is the DRA alternative
	// to DedicatedCPUPlacement. Requires the CPUsWithDRA feature gate.
  // +listType=atomic
	// +optional
  DRA []ClaimRequest `json:"dra,omitempty"`
}
```

### Validation

The validating webhook (virt-api) enforces:

- `CPUsWithDRA` must be enabled when `cpu.dra` is set.
- Both source fields may be unset; this preserves the existing non-exclusive CPU behavior.
- If exclusive CPUs are requested, exactly one source is selected: `cpu.dedicatedCpuPlacement` (kubelet CPU Manager) or `cpu.dra`, never both.
- When `cpu.dra` is set, both `claimName` and `requestName` must be set.
- `claimName` must match an entry in `spec.resourceClaims`.

Claim-size consistency against `hostCPUs` is **not** checked in virt-api (that would require fetching the `ResourceClaim` and slow VMI creation). Virt-controller performs that check when rendering the virt-launcher pod; see [CPU accounting](#cpu-accounting).

#### CPU accounting

CPU DRA must account for guest vCPUs and any additional host CPUs used by emulator or IO threads:

```text
hostCPUs = guestVCPUs + supplementalPoolThreadCount + emulatorThreadCPUs
```

- **`guestVCPUs`**: `cores` × `sockets` × `threads`
- **`supplementalPoolThreadCount`**: from `ioThreads.supplementalPoolThreadCount` (`supplementalPool` policy)
- **`emulatorThreadCPUs`**: `1` (or `2` with even-parity) if `isolateEmulatorThread`; else `0`

Example: a VM with `cores: 8`, `isolateEmulatorThread: true`, and `ioThreads.supplementalPoolThreadCount: 2` needs a claim for **11** CPUs (`8 + 1 + 2`), not 8.
Undersizing leaves virt-launcher unable to pin emulator/IO threads; oversizing wastes exclusive CPUs.

For Alpha, users must size explicit claims using this formula and for users using managed claim features VEP 300 can be used.
If the claim's CPU count is less than `hostCPUs`, virt-controller fails validation while rendering the virt-launcher pod.

### Component Changes

**Virt-API**

- Extends the VMI create admitter with CPU DRA validation (mutual exclusivity, claim-reference checks).

**Virt-Controller**

- Renders the virt-launcher pod spec with resource claims from `vmi.spec.resourceClaims[]` referenced by `vmi.spec.domain.cpu.dra`.
- Checks that the claim's CPU count is at least `hostCPUs`, as described in [CPU accounting](#cpu-accounting). If it is not, virt-controller fails validation while rendering the virt-launcher pod.
- **Alpha:** the virt-launcher pod carries **both** DRA claims and mirrored `resources.requests/limits.cpu` (from the [CPU accounting](#cpu-accounting) host CPU total) while `dra-driver-cpu` still requires mirroring.
- **After driver support for KEP-5517 mappings:** drop mirrored CPU requests/limits and rely on claim-only allocation; kubelet (v1.37+) already applies `pod.status.nodeAllocatableResourceClaimStatuses` for cgroup and OOM accounting when `DRANodeAllocatableResources` is enabled.

**Virt-Launcher**

- Uses the existing logic to configure the guest CPU topology (`cores`/`sockets`/`threads`).
- For DRA-backed VMs, reads allocated CPU attributes from the DRA metadata file available in the pod, reusing the existing path for GPU and NIC DRA devices.
- Uses the allocated CPU IDs to write the libvirt `<cputune><vcpupin .../></cputune>` entries through the existing dedicated CPU placement logic. Emulator and supplemental IO thread pins reuse the same dedicated-CPU path when those features are enabled.
- If KEP-5304 metadata is unavailable, virt-launcher fails the VM.
- The `dra-driver-cpu` NRI plugin independently pins the virt-launcher compute container's cgroup to the same allocated CPU set.

## API Examples

The [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) supports two device-exposure modes via the `--cpu-device-mode` flag.

`ResourceSlice` examples for both individual and grouped modes are published:
[dra-driver-cpu resourceslices examples](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/docs/user/resourceslices-examples.md).

### Grouped mode

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: grouped-mode-cpu-claim-template
spec:
  spec:
    devices:
      requests:
      - name: req-cpu-slice
        exactly:
          deviceClassName: dra.cpu
          capacity:
            requests:
              dra.cpu/cpu: "10"
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-with-grouped-cpu
spec:
  resourceClaims:
  - name: grouped-mode-cpu-claim
    resourceClaimTemplateName: grouped-mode-cpu-claim-template
  domain:
    cpu:
      cores: 10
      dra:
        claimName: grouped-mode-cpu-claim
        requestName: req-cpu-slice
    resources:
      requests:
        memory: 8Gi
```

The following pod is generated by `virt-controller` from the VMI above; users do not create it directly.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-with-grouped-cpu
spec:
  containers:
  - name: compute
    image: virt-launcher
    resources:
      requests:
        cpu: "10"
        memory: 8Gi
      limits:
        cpu: "10"
        memory: 8Gi
      claims:
      - name: grouped-mode-cpu-claim
        request: req-cpu-slice
  resourceClaims:
  - name: grouped-mode-cpu-claim
    resourceClaimTemplateName: grouped-mode-cpu-claim-template
status:
  nodeAllocatableResourceClaimStatuses:
  - resourceClaimName: grouped-mode-cpu-claim
    containers:
    - compute
    direct:
    - name: cpu
      quantity: "10"
```

### Individual mode

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: individual-mode-cpu-claim
spec:
  devices:
    requests:
    - name: numa0-cpus
      exactly:
        deviceClassName: dra.cpu
        count: 4
        selectors:
        - cel:
            expression: device.attributes["dra.cpu"].numaNodeID == 0
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-with-individual-cpu
spec:
  resourceClaims:
  - name: cpu-claim
    resourceClaimName: individual-mode-cpu-claim
  domain:
    cpu:
      cores: 4
      dra:
        claimName: cpu-claim
        requestName: numa0-cpus
    resources:
      requests:
        memory: 8Gi
```

The following pod is generated by `virt-controller` from the VMI above; users do not create it directly.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-with-individual-cpu
spec:
  containers:
  - name: compute
    image: virt-launcher
    resources:
      requests:
        cpu: "4"
        memory: 8Gi
      limits:
        cpu: "4"
        memory: 8Gi
      claims:
      - name: cpu-claim
        request: numa0-cpus
  resourceClaims:
  - name: cpu-claim
    resourceClaimName: individual-mode-cpu-claim
status:
  nodeAllocatableResourceClaimStatuses:
  - resourceClaimName: individual-mode-cpu-claim
    containers:
    - compute
    direct:
    - name: cpu
      quantity: "4"
```

## Scalability

This integration follows the existing DRA scalability model used for other device types (for example, GPUs and HostDevices).
See [VEP #10](../10-dra-devices/vep.md#scalability) for details.

## Update/Rollback Compatibility

- Changes are upgrade compatible
  - Reasoning: the API changes are additive and guarded by `CPUsWithDRA`; with the gate disabled, existing CPU behavior remains unchanged.
- Rollback works as long as the `CPUsWithDRA` feature gate is disabled
- If the feature is enabled, VMIs using DRA CPUs must be deleted and the feature gate disabled before attempting rollback

## Functional Testing Approach

- Unit tests in-tree with strong coverage of API validation
- Coverage for both individual-mode and grouped-mode claim shapes
- Mock e2e tests covering the supported scenarios
- External validation with `dra-driver-cpu` (no in-tree CI e2e gate in Alpha, consistent with the current DRA alpha model)

## Implementation History

- 2026-07-31: Initial design/VEP proposal for DRA-backed CPU support

### KEP-5517 implementation history

- **Alpha (Kubernetes v1.36):**  
    - Feature gate `DRANodeAllocatableResources` introduced, disabled by default.
    - Core API changes — `Device.NodeAllocatableResourceMappings` and `PodStatus.NodeAllocatableResourceClaimStatuses`.
    - Scheduler uses ResourceClaim via `DynamicResources` plugin for node allocatable resource accounting.
    - Kubelet still uses spec.requests for cgroup enforcement.
    - In-Place Pod Resizing blocked for pods using DRA node allocatable resources (API validation restriction).

- **Alpha2 (Kubernetes v1.37):**
    - Kubelet cgroup enforcement — reads `pod.status.nodeAllocatableResourceClaimStatuses` for `cpu.max`, `cpu.weight`, `memory.max`, hugepage limits, OOM score adjustments.
    - Supports both mapping models — direct (exclusive CPUs, consumable capacity pools) and overhead (accelerator host memory dependencies).
    - In-Place Pod Resizing restriction lifted for standard spec resources on DRA pods.

- **Beta (Kubernetes v1.38):**
    - At least one DRA driver has integrated NodeAllocatableResourceMappings in ResourceSlice and validated end-to-end.

## Graduation Requirements

### Alpha

- API changes behind the `CPUsWithDRA` feature gate
- Support for both individual-mode and grouped-mode `ResourceClaim` shapes
- Webhook validation
- virt-controller and virt-launcher changes
- Virt-launcher pod carries both `resources.requests/limits.cpu` and DRA claims (request mirroring while `dra-driver-cpu` lacks `nodeAllocatableResourceMappings`)
- Unit tests
- Mock e2e tests
- External validation with `dra-driver-cpu`

### Beta

- Depends on **`dra-driver-cpu`** publishing `nodeAllocatableResourceMappings` in `ResourceSlice` (KEP-5517 driver integration). Kubernetes kubelet/scheduler Alpha2 support already landed in **v1.37**.
- Once the driver lands mappings, update KubeVirt so the virt-launcher pod is claim-only (drop mirrored `resources.requests/limits.cpu`) and relies on `pod.status.nodeAllocatableResourceClaimStatuses` for node accounting
- User documentation

### GA

- Upgrade/downgrade testing
- Scale testing on large nodes

## References

- [Kubernetes Dynamic Resource Allocation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
- [Kubernetes ResourceClaim API](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/)
- [KEP-5304: DRA Device Attributes Downward API](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api)
- [KubeVirt dedicated CPU resources](https://kubevirt.io/user-guide/compute/dedicated_cpu_resources/)
- [CPU DRA driver](https://github.com/kubernetes-sigs/dra-driver-cpu)
- [dra-driver-cpu #265](https://github.com/kubernetes-sigs/dra-driver-cpu/pull/265) / [#286](https://github.com/kubernetes-sigs/dra-driver-cpu/pull/286) (KEP-5304 metadata for allocated CPUs)
- [VEP #10 (DRA devices)](../10-dra-devices/vep.md)
- [VEP #115 (PCIe NUMA topology awareness)](../115-pcie-numa-topology-awareness/pcie-numa-topology-awareness.md)

## Appendix: Auto/Managed Claim Synthesis

Out of scope for this VEP. Both CPU auto claim synthesis and cross-device
`managedClaim` synthesis are tracked as **VEP #300** (see the sketch in
[VEP #10 Appendix C](../10-dra-devices/vep.md#c-managed-resource-claims)).

Alpha of this VEP uses only the explicit `ClaimRequest` path under
`domain.cpu.dra`.

Hand-authoring a CPU `ResourceClaim` is verbose for common exclusive-CPU
cases. As a follow-up, `cpu.dra` can offer an auto-claim path where KubeVirt synthesizes and owns the claim. This is kubevirt-owned, not on Kubernetes `PodResourceClaim`. Cross-device managed claims (GPU + NIC + CPU sharing one claim) are covered in the same VEP #300 work.

`autoClaim` and an explicit `ClaimRequest` are mutually exclusive.

## Open Issues

1. **`dra-driver-cpu` + [KEP-5517](https://github.com/kubernetes/enhancements/issues/5517):** Kubernetes v1.37 already provides kubelet/scheduler Alpha2 support for `DRANodeAllocatableResources`. This VEP's claim-only Beta path is blocked on `dra-driver-cpu` publishing `nodeAllocatableResourceMappings`.
2. CPU DRA-backed VMIs may not be Guaranteed QoS if CPU requests/limits are omitted.The initial implementation must decide whether Guaranteed QoS remains a requirement,or whether CPU DRA-backed VMIs use Burstable QoS with DRA-based cgroup accounting.
3. Want Guaranteed QoS -> need normal CPU request/limit.
Want no duplicate CPU + no throttling -> omit CPU request/limit and rely on DRA accounting.