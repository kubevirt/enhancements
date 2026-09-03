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

This proposal adds support for [DRA](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)-provisioned exclusive CPUs in KubeVirt.

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

CPU DRA addresses this by advertising CPU topology through `ResourceSlice`, allocating via standard claims, and letting the scheduler and driver cooperate on placement before the pod starts. The CPU DRA driver enables VirtualMachine workloads to request CPUs with specific topology and performance characteristics.

Key advantages:

- **Standard Kubernetes APIs:** Allocation uses `ResourceClaim`, `ResourceClaimTemplate`, and `DeviceClass` instead of kubelet-policy-specific side channels.
- **No new VMI API:** Exclusive CPUs are still requested with `cpu.cores` and `dedicatedCpuPlacement`. Users do not author CPU `ResourceClaim` objects.
- **Scheduler-visible allocation:** The synthesized claim is scheduled through standard DRA (`ResourceClaim`, `DeviceClass`, `ResourceSlice`) instead of kubelet-local CPU Manager.
- **Better lifecycle management:** The driver manages allocation, reservation, cleanup, and reclaim of unused CPUs.
- **Composable with other DRA devices:** The virt-launcher pod can carry the CPU claim alongside GPU/NIC claims from VEP #10.

## Goals

- Enable KubeVirt VMs to consume CPU resources via DRA using externally supplied DRA drivers (primarily [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu)) without new VMI API fields
- Have virt-controller synthesize the CPU claim from the VMI CPU topology (`cpu.cores` and related host-CPU accounting)

## Non Goals

- Deprecate kubelet CPU Manager support
- Deploy or manage external DRA CPU drivers in-tree
- Live migration of VMIs with DRA-pinned cpusets
- Provide memory placement or NUMA memory binding via the CPU DRA driver
- NUMA passthrough with CPU DRA is out of scope for this VEP and will be addressed in [VEP #115](../115-pcie-numa-topology-awareness/pcie-numa-topology-awareness.md).
- `groupBy: machine` (opaque `cpuset` from an external scheduler). This VEP’s synthesized claim has no such assignment.
- Allow the same node to publish the same CPUs in both grouped and individual CPU DRA modes
- Support both kubelet CPU Manager (`static` policy) and the DRA CPU driver on the same node at the same time
- Auto/Managed claim (see [Appendix: Auto/Managed Claim Synthesis](#appendix-automanaged-claim-synthesis); covered by VEP #300)
- Advanced use cases that need host-allocation intent on the VMI (Phase 3; see [Appendix: Phased follow-ups](#appendix-phased-follow-ups)). 

## Definition of Users

- **User:** A person who wants exclusive, topology-aware CPUs for a VM via DRA
- **Admin:** A person who manages infrastructure, deploys the DRA CPU driver, and configures `DeviceClass` resources

## User Stories

- As a user, I want exclusive DRA CPUs by setting `cpu.cores` and `dedicatedCpuPlacement` the way I do today, without writing a `ResourceClaim`
- As an admin, I want to control CPU allocation policies through DRA APIs (`DeviceClass`, driver mode) without modifying the VMI schema
- As a user running heterogeneous workloads, I want the virt-launcher pod to carry both the synthesized CPU claim and my GPU/NIC claims so the scheduler can select a node where all resources are available

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)
[user-guide](https://github.com/kubevirt/user-guide)

## Design

This design introduces a new feature gate: `CPUsWithDRA`.
The gate is behavioral only: with it disabled, `dedicatedCpuPlacement` keeps using kubelet CPU Manager. With it enabled, virt-controller synthesizes a DRA CPU claim for those VMIs.

**Mutual exclusivity with guest NUMA passthrough**

Alpha: CPU DRA and `spec.domain.cpu.numa.guestMappingPassthrough` are **mutually exclusive**.

`guestMappingPassthrough` mirrors host NUMA cells into the guest and depends on kubelet Memory Manager / Topology Manager to keep guest memory on those cells, with exclusive CPUs from CPU Manager. The CPU DRA path has no memory-alignment anchor, and the synthesized claim is one request from one group device (typically one NUMA node). That cannot back a multi-cell passthrough map.

When `CPUsWithDRA` is enabled and the VMI sets `numa.guestMappingPassthrough`:

- **virt-api** rejects the combination at admission so the user gets a clear error instead of a topology that is not memory-backed.
- **virt-controller** does not synthesize a CPU `ResourceClaim` and uses the existing dedicated-CPU / kubelet CPU Manager pod path — the same fallback as when the feature gate is off or `DRA_CPUSET_*` is unset.

### Responsibility boundary

- **Admin owns:**
  - driver/operator deployment
  - `DeviceClass` definitions (this VEP uses `dra.cpu` to match [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu))
  - grouped-mode **group granularity** via `driverConfig.groupBy` (default `numanode`; also `socket` or `machine`). This is driver/admin config, not a field on the synthesized claim. `groupBy: machine` is unsupported here (see [Grouped mode](#grouped-mode)).
  - node mutual exclusivity for exclusive CPU allocation: run either kubelet CPU Manager (`static`) or the CPU DRA driver on a node, not both (`cpuManagerPolicy: none` on DRA CPU nodes; see [`dra-driver-cpu` configuration](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/docs/user/configuration.md))
- **KubeVirt owns:**
  - synthesizing the CPU `ResourceClaim` from the VMI CPU topology and attaching it to the virt-launcher pod
  - applying the allocated cpuset to the libvirt domain XML
- **User owns:**
  - requesting exclusive CPUs with the existing VMI CPU fields (`cpu.cores`, `dedicatedCpuPlacement`, and related topology / IO / emulator settings)

### External Dependencies

- A DRA CPU driver is an external dependency; it is selected and deployed outside KubeVirt, and is not delivered by this VEP.
- This VEP uses [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) CPU DRA driver implementation.
- Driver capability requirements:
  - Must publish CPU availability through Kubernetes `ResourceSlice` objects
  - Must support the grouped / consumable-capacity claim shape KubeVirt synthesizes
  - Must inject the allocated cpuset into the virt-launcher compute container as `DRA_CPUSET_<claimUID>=<cpuset>` via CDI
  - For claim-only accounting (no mirrored `resources.requests/limits.cpu`): must publish `nodeAllocatableResourceMappings` on `ResourceSlice` devices so claimed CPUs map into `node.status.allocatable` CPU (KEP-5517). Until `dra-driver-cpu` lands that, Alpha keeps request/limit mirroring as a workaround.
- **Grouped / consumable-capacity claims:** CPU grouped mode uses Kubernetes [DRA consumable capacity](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#consumable-capacity) (`DRAConsumableCapacity`). That feature is **beta and enabled by default on Kubernetes v1.36+**. KubeVirt does not add a separate feature gate for it; `CPUsWithDRA` remains the only KubeVirt gate. On older clusters the Kubernetes gate must be enabled explicitly.
- **Alpha workaround:** Until the driver publishes node-allocatable mappings, `dra-driver-cpu` still expects the claimed CPU count mirrored in the pod's standard `resources.requests/limits.cpu` to avoid double-booking. Virt-controller derives mirrored requests/limits from the [CPU accounting](#cpu-accounting) host CPU total.

### CPU DRA driver

With CPU DRA, a driver runs on each node and publishes CPU availability through Kubernetes `ResourceSlice` objects.
[`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) historically supported two modes via `--cpu-device-mode`: **grouped** (default, consumable capacity) and **individual** (one device per CPU). Individual mode is being **deprecated**. KubeVirt synthesizes grouped-mode claims only.

KubeVirt's synthesized claim uses **grouped mode** (the driver default). Individual mode remains a driver capability; this VEP does not synthesize per-CPU device claims or surface CEL selectors on the VMI.

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
Individual mode is useful when a workload requires precise CPU placement. Expressing that from a VMI would need a user-authored claim API, which is out of scope here.

#### Grouped mode

In grouped mode, CPUs are requested as consumable capacity from a **group device**. Group granularity is **not** on the claim: the admin sets `driverConfig.groupBy` (`numanode` by default; also `socket` or `machine`). The driver publishes one consumable-capacity device per NUMA node or socket accordingly.

Example with the default `groupBy: numanode`:
```text
NUMA node 0: capacity 32 CPUs
NUMA node 1: capacity 32 CPUs
```

This claim shape depends on Kubernetes `DRAConsumableCapacity`, which is available by default on **v1.36+** clusters.

The synthesized claim is a **single** request for *N* CPUs (`dra.cpu/cpu: "<hostCPUs>"`) with **no CEL selector**. Kubernetes DRA allocates that request from **one** group device, so all of a VM’s exclusive CPUs come from one group. With default `groupBy: numanode`, they stay on a **single NUMA node** by construction. The driver then picks physical CPUs inside that group.

That NUMA confinement is the locality this VEP gets without a VMI NUMA API. It is **not** guest NUMA passthrough: Alpha does not combine CPU DRA with `numa.guestMappingPassthrough` (see [Mutual exclusivity with guest NUMA passthrough](#mutual-exclusivity-with-guest-numa-passthrough)). Memory binding and multi-cell guest maps remain [VEP #115](../115-pcie-numa-topology-awareness/pcie-numa-topology-awareness.md).

`groupBy: machine` (one machine-wide capacity device) is **unsupported** here. In that mode an external scheduler must inject opaque `cpuset` assignments into the claim allocation.

If finer per-claim narrowing is ever needed (a specific NUMA node or socket among several), a CEL selector on the published group-device attributes can constrain which group the single request binds to. This VEP does not add that to the synthesized claim; grouping stays the admin’s `groupBy` choice.

### No VMI API changes

Exclusive CPU allocation stays on the existing CPU fields:

- `dedicatedCpuPlacement` — the user-facing request for exclusive host CPUs (unchanged YAML)
- `cpu.cores` / `sockets` / `threads` — guest topology, and the input virt-controller uses to size the claim
- `isolateEmulatorThread`, `ioThreads.supplementalPoolThreadCount` — additional host CPUs included in the claim (see [CPU accounting](#cpu-accounting))

When `CPUsWithDRA` is disabled, `dedicatedCpuPlacement` continues to use kubelet CPU Manager, as today.

When `CPUsWithDRA` is enabled, the same VMI spec is served by DRA: virt-controller creates the claim and attaches it to the virt-launcher pod — except when `numa.guestMappingPassthrough` is set; then CPU DRA is not used.

### CPU accounting

CPU DRA must account for guest vCPUs and any additional host CPUs used by emulator or IO threads:

```text
hostCPUs = guestVCPUs + supplementalPoolThreadCount + emulatorThreadCPUs
```

- **`guestVCPUs`**: `cores` × `sockets` × `threads`
- **`supplementalPoolThreadCount`**: from `ioThreads.supplementalPoolThreadCount` (`supplementalPool` policy)
- **`emulatorThreadCPUs`**: `1` (or `2` with even-parity) if `isolateEmulatorThread`; else `0`

Example: a VM with `cores: 8`, `isolateEmulatorThread: true`, and `ioThreads.supplementalPoolThreadCount: 2` needs a claim for **11** CPUs (`8 + 1 + 2`), not 8.
Undersizing leaves virt-launcher unable to pin emulator/IO threads; oversizing wastes exclusive CPUs.

Virt-controller sizes the synthesized claim to `hostCPUs`, so claim-size consistency is by construction.

### Component changes

**virt-api**

- When `CPUsWithDRA` is on and `dedicatedCpuPlacement` is set, reject `numa.guestMappingPassthrough` at admission. See

**Virt-Controller**

When `CPUsWithDRA` is enabled and the VMI requests exclusive CPUs (`dedicatedCpuPlacement: true`) **and** `numa.guestMappingPassthrough` is unset:

- Computes `hostCPUs` from [CPU accounting](#cpu-accounting) (`cpu.cores` × `sockets` × `threads`, plus emulator / IO-thread extras).
- Creates a KubeVirt-owned `ResourceClaim` (owner-ref the VMI) for that many CPUs in grouped mode, using DeviceClass `dra.cpu`:

  ```yaml
  spec:
    devices:
      requests:
      - name: cpu
        exactly:
          deviceClassName: dra.cpu
          capacity:
            requests:
              dra.cpu/cpu: "<hostCPUs>"
  ```

- Attaches the claim to the virt-launcher pod via `spec.resourceClaims[]` (`resourceClaimName`) and `containers[].resources.claims[]`, merging with any user claims from `vmi.spec.resourceClaims` (GPUs, NICs, host devices).
- **Alpha:** the virt-launcher pod carries **both** the synthesized DRA claim and mirrored `resources.requests/limits.cpu` (from `hostCPUs`) while `dra-driver-cpu` still requires mirroring. Mirrored CPU request equals limit, and `dedicatedCpuPlacement` already requires matching memory request/limit, so the pod stays **Guaranteed QoS** — the same class as the CPU Manager path.
- **After driver support for KEP-5517 mappings:** drop mirrored CPU requests/limits and rely on claim-only allocation; kubelet (v1.37+) already applies `pod.status.nodeAllocatableResourceClaimStatuses` for cgroup and OOM accounting when `DRANodeAllocatableResources` is enabled. Whether that claim-only pod stays Guaranteed or becomes Burstable is a Beta decision (see [Open Issues](#open-issues)).
- If the claim or pod cannot be rendered (missing DeviceClass, feature misconfiguration), virt-controller fails pod creation rather than starting an unpinned exclusive-CPU VM.

When `CPUsWithDRA` is disabled, or the VMI sets `numa.guestMappingPassthrough`, virt-controller does not create a CPU claim and keeps the existing dedicated-CPU / kubelet CPU Manager pod path.

**Virt-Launcher**

- Uses the existing logic to configure the guest CPU topology (`cores`/`sockets`/`threads`).
- Looks for the CDI-injected `DRA_CPUSET_<claimUID>=<cpuset>` environment variable (`<cpuset>` is a Linux list, for example `0-2,14-15`). [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) reserves the `DRA_CPUSET_*` prefix.
- **If `DRA_CPUSET_*` is set:** parse the cpuset and write libvirt `<cputune><vcpupin .../></cputune>`. Emulator and supplemental IO thread pins reuse that same path when those features are enabled.
- **If `DRA_CPUSET_*` is not set:** do not fail the VM. Fall back to the container cgroup cpuset (the existing dedicated-CPU path). Guest vCPUs then run inside the cgroup-isolated set without explicit `vcpupin` from the DRA env.
- The `dra-driver-cpu` NRI plugin independently pins the virt-launcher compute container's cgroup to the allocated CPU set when the claim was prepared.

## API Examples

The [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) grouped-mode `ResourceSlice` examples are published at
[dra-driver-cpu resourceslices examples](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/docs/user/resourceslices-examples.md).

The user authors only the VMI. virt-controller creates the claim and pod.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-with-cpu-dra
spec:
  domain:
    cpu:
      cores: 10
      threads: 1
      sockets: 1
      dedicatedCpuPlacement: true
    resources:
      requests:
        memory: 8Gi
```

With `CPUsWithDRA` enabled, virt-controller creates a `ResourceClaim` equivalent to:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: vmi-with-cpu-dra-cpu
  ownerReferences:
  - name: vmi-with-cpu-dra
    # ...
spec:
  devices:
    requests:
    - name: cpu
      exactly:
        deviceClassName: dra.cpu
        capacity:
          requests:
            dra.cpu/cpu: "10"
```

and renders the virt-launcher pod:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-with-cpu-dra
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
      - name: cpu-dra
        request: cpu
  resourceClaims:
  - name: cpu-dra
    resourceClaimName: vmi-with-cpu-dra-cpu
status:
  nodeAllocatableResourceClaimStatuses:
  - resourceClaimName: vmi-with-cpu-dra-cpu
    containers:
    - compute
    direct:
    - name: cpu
      quantity: "10"
```

After the driver prepares the claim, CDI injects an env var of the form `DRA_CPUSET_<claimUID>=0-9` (exact IDs depend on allocation). virt-launcher writes matching `<vcpupin>` entries. If that env var is absent, virt-launcher uses the compute container's cgroup cpuset instead.

## Alternatives

### User-facing DRA API (`domain.cpu.dra` + authored `ResourceClaim`)

Expose DRA the way GPUs and host devices work: the user (or an admin) creates a CPU `ResourceClaim` / template, then wires it on the VMI with a ClaimRequest on the CPU object:

```yaml
domain:
  cpu:
    cores: 10
    dra:
    - claimName: cpu-claim
      requestName: req-cpu
```

or an inline `CPUSource` that treats `dedicatedCpuPlacement` and `dra` as mutually exclusive backends.

Rejected as the default exclusive-CPU path. `cpu.dra` only exists to point at a claim the user already wrote.

- It names the implementation (DRA) on the VM spec. `dedicatedCpuPlacement` already means “this VM needs exclusive host CPUs.” The later CPU Manager backing made that field *sound* like a kubelet mechanism; `cpu.dra` would leak the new mechanism in the other direction.
- The user would have to know whether the node runs CPU Manager or a DRA CPU driver and rewrite the VM when that changes. With this VEP, the same `dedicatedCpuPlacement: true` spec works for both: gate off → CPU Manager; gate on → synthesized claim.
- CPUs are a quantity plus exclusivity (and later, optional topology). GPUs and host devices are discrete attachments, so `claimName` / `requestName` on the device is natural. Copying that onto `domain.cpu` is the wrong shape.
- The host CPU count is already `cores × sockets × threads` plus emulator and IO threads. Restating that in a `ResourceClaim` is verbose, easy to undersize, and requires knowing KubeVirt’s [CPU accounting](#cpu-accounting) formula. Regular VM authors should not need to be aware of this.


`spec.resourceClaims` remains the advanced escape hatch for an already-authored CPU claim (same place GPUs/hostDevices point today). Regular exclusive-CPU users never write it.

### Extend kubelet CPU Manager / Topology Manager

Keep exclusive CPUs on kubelet `static` CPU Manager and try to close the topology gap with Topology Manager.

This is the path `dedicatedCpuPlacement` uses today, and it remains supported when `CPUsWithDRA` is disabled. It does not solve the scheduling problem this VEP targets:

- The scheduler is blind to topology at placement time. The scheduler only sees aggregate `cpu` requests, so a pod can land on a node whose remaining topology cannot satisfy exclusive CPUs aligned with a GPU or NIC. 
- CPU Manager and Topology Manager both run inside the kubelet, after the scheduler has already bound the pod to a node.
- Topology Manager is node-local. So the scheduler picks a node against `Allocatable.cpu`, the kubelet's Topology Manager then tries to find a NUMA-aligned allocation, discovers it cannot, and rejects the pod with a `TopologyAffinityError`.
- Co-locating CPUs with DRA GPUs, SR-IOV NICs, or host devices needs a scheduler-visible claim, not a second kubelet-local policy.
- kubelet CPU Manager and `dra-driver-cpu` cannot own the same CPUs on one node. Extending CPU Manager would fight the driver rather than compose with the rest of DRA (VEP #10).

DRA is the Kubernetes API for scheduler-visible, topology-aware allocation. This VEP consumes it; it does not replace CPU Manager in-tree or require kubelet changes.


## Scalability

This integration follows the existing DRA scalability model used for other device types (for example, GPUs and HostDevices).
See [VEP #10](../10-dra-devices/vep.md#scalability) for details. virt-launcher does not watch `ResourceClaim` or `ResourceSlice`. It reads `DRA_CPUSET_*` from CDI in the pod.

Virt-controller creates **one synthesized `ResourceClaim` per dedicated-CPU VMI** while `CPUsWithDRA` is on. It does not watch `ResourceSlice`. Claims are owner-referenced to the VMI, so they scale and garbage-collect with VMIs, not with guest vCPU count. 

Synthesized claims are grouped-mode (one consumable-capacity request per VM), so `ResourceSlice` size stays proportional to NUMA/socket groups rather than per-CPU devices.

## Update/Rollback Compatibility

Upgrade is compatible: there are no VMI API changes. With `CPUsWithDRA` disabled, `dedicatedCpuPlacement` keeps using kubelet CPU Manager.

Rollback: disable the feature gate. Restart (or delete) VMIs that used DRA CPUs so they recreate on the CPU Manager path before rolling KubeVirt back.

virt-controller synthesizes the `ResourceClaim`; virt-launcher consumes `DRA_CPUSET_*`. A rolling upgrade can run those at different versions. The gate is off by default, so clusters that have not enabled it are unaffected.

When the gate is on, mixed versions do not fail the VM. Guest pinning falls back to the container cgroup until both components are upgraded.

| Controller | Launcher | Claim | Guest pinning |
| --- | --- | --- | --- |
| new | new | yes | `<vcpupin>` from `DRA_CPUSET_*` |
| new | old | yes | cgroup cpuset |
| old | new | no | cgroup cpuset (CPU Manager if `dedicatedCpuPlacement`) |
| old | old | no | CPU Manager / existing `dedicatedCpuPlacement` |

**New controller / old launcher:** CDI/NRI still pin the container. The old launcher does not read `DRA_CPUSET_*`, so there is no DRA-driven `vcpupin`.

**Old controller / new launcher:** no CPU claim is synthesized, so `DRA_CPUSET_*` is unset and the new launcher uses the cgroup fallback.

## Functional Testing Approach

- **Unit tests (Alpha):** `hostCPUs` accounting; synthesized grouped claim (DeviceClass `dra.cpu`); claim attach/merge with user GPU/NIC claims; virt-api reject of `numa.guestMappingPassthrough` when the gate is on; virt-launcher parse of `DRA_CPUSET_*` into `<vcpupin>`. Also the negatives already in Design: missing `DeviceClass` (or claim/pod cannot be rendered) → virt-controller does not start the virt-launcher pod; `DRA_CPUSET_*` absent → launcher uses the cgroup cpuset and does not fail.
- **E2E (Beta):** in-tree CI-gating lane with real [`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) (required for Beta and GA).

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

- Behavioral changes behind the `CPUsWithDRA` feature gate (no VMI API changes)
- virt-controller synthesizes grouped-mode CPU claims from `cpu.cores` / host-CPU accounting
- virt-launcher applies `DRA_CPUSET_*` to domain XML and falls back to the cgroup cpuset when it is unset
- Virt-launcher pod carries both `resources.requests/limits.cpu` and the synthesized DRA claim; pod remains **Guaranteed QoS**
- Unit tests
- Manual validation with `dra-driver-cpu`
- No in-tree CI e2e gate

### Beta

- Depends on **`dra-driver-cpu`** publishing `nodeAllocatableResourceMappings` in `ResourceSlice` (KEP-5517 driver integration). Kubernetes kubelet/scheduler Alpha2 support already landed in **v1.37**.
- Once the driver lands mappings, update KubeVirt so the virt-launcher pod is claim-only (drop mirrored `resources.requests/limits.cpu`) and relies on `pod.status.nodeAllocatableResourceClaimStatuses` for node accounting; resolve Beta QoS (Guaranteed vs Burstable with DRA accounting)
- In-tree **CI-gating e2e** with `dra-driver-cpu`
- User documentation

### GA

- CI-gating e2e lane remains required
- Upgrade/downgrade testing
- Scale testing on large nodes and large dedicated-CPU VMI counts

## References

- [Kubernetes Dynamic Resource Allocation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
- [Kubernetes ResourceClaim API](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/)
- [KEP-5517: DRA node allocatable resources](https://github.com/kubernetes/enhancements/issues/5517)
- [KubeVirt dedicated CPU resources](https://kubevirt.io/user-guide/compute/dedicated_cpu_resources/)
- [CPU DRA driver](https://github.com/kubernetes-sigs/dra-driver-cpu)
- [dra-driver-cpu how it works](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/docs/user/how-it-works.md) (`DRA_CPUSET_*` via CDI, NRI cgroup pinning)
- [VEP #10 (DRA devices)](../10-dra-devices/vep.md)
- [VEP #115 (PCIe NUMA topology awareness)](../115-pcie-numa-topology-awareness/pcie-numa-topology-awareness.md)

## Appendix: Phased follow-ups

CPU DRA lands in three phases. Phase 1 derives a single exclusive claim from existing topology; Phase 2 lets the provisioner create an aligned claim. DRA (`ResourceClaim`, `DeviceClass`, `matchAttribute`) stays an implementation mechanism.

**Phase 3 (v1.11)** is the follow-up for advanced sharing: shared host CPUs **across VMIs**, and grouped sharing **within a domain** (not explicit co-pinning). That is what needs host-allocation intent on the VMI so virt-launcher can write domain XML. The API is TBD.

Phases 1 and 2 are targeted for **KubeVirt v1.10**. Phase 3 is a later follow-up in **KubeVirt v1.11**.

| Phase | VEP | Target | Who creates the claim | VMI API |
| --- | --- | --- | --- | --- |
| 1 | **This VEP (#152)** | **v1.10** | virt-controller: one exclusive CPU `ResourceClaim` from guest topology + extras | Existing fields only |
| 2 | **VEP #300** | **v1.10** | **Provisioner:** aligned claim when CPU must land with GPU, NIC, or other devices | Existing fields only |
| 3 | Follow-up | v1.11 | Advanced use cases. Examples include shared CPUs across VMIs and grouped sharing within a domain | Host-allocation intent on the VMI (API TBD); not DRA-shaped |

### Phase 1 — VEP #152: exclusive CPUs from existing topology

**v1.10.** In scope for this VEP. virt-controller generates a `ResourceClaim` when `dedicatedCpuPlacement: true`, sized from `cpu.cores` × `sockets` × `threads`, plus emulator thread and IO threads ([CPU accounting](#cpu-accounting)).

Users do not author DRA objects. Exclusive placement is requested with `dedicatedCpuPlacement`; virt-launcher can pin 1:1 from the allocated set without a per-vCPU host-allocation API.

```yaml
domain:
  cpu:
    cores: 4
    sockets: 1
    threads: 1
    dedicatedCpuPlacement: true
    isolateEmulatorThread: true
  ioThreads:
    policy: supplementalPool
    supplementalPoolThreadCount: 2
```

virt-controller turns that into one grouped exclusive claim (here `4 + 1 + 2` host CPUs). virt-launcher pins from `DRA_CPUSET_*` (or the cgroup fallback). Every domain vCPU is exclusive and 1:1 with a host CPU in the allocated set.

### Phase 2 — VEP #300: provisioner creates the claim when alignment is required

**v1.10**, in a separate VEP. When exclusive CPUs must be co-located with a GPU, SR-IOV NIC, or other DRA device (same PCIe root, NUMA node, and similar), the **provisioner** creates the aligned `ResourceClaim`. virt-controller does not synthesize that claim.

More details can be found in vep300

### Phase 3 — Advanced use cases: host-allocation intent

**v1.11.** This phase adds a VMI API for **host-allocation intent**: which domain vCPUs belong to which host CPU allocation groups, and whether those groups are exclusive or shared. Phases 1 and 2 do not have that. Leaving the mapping only in the provisioner would make virt-launcher unable to build domain XML.

One advanced example is mixed exclusive and shared host CPUs. Consider two VMIs, each with four domain vCPUs:

- **VMI1** needs four exclusive host CPUs, each domain vCPU pinned 1:1.
- **VMI2** needs three host CPUs: vCPU 0 pinned to one exclusive host CPU; vCPUs 1–3 floats across two shared host CPUs.

DRA can represent that. For this example the provisioner can create a claim with four exclusive CPU devices for VMI1, and a compound claim with one exclusive and two shareable CPU devices for VMI2. That compound claim is one illustration of an advanced usecases.

The gap is domain XML. Even if claim metadata distinguishes exclusive vs shared host CPUs, nothing in today's VMI spec says which domain vCPU belongs to which allocation, or how the shared vCPUs map onto that set. virt-launcher cannot infer that vCPU 0 needs an exclusive `vcpupin` while vCPUs 1–3 share a two-CPU set.

Phase 3 therefore puts that mapping on the VMI. The provisioner chooses how to satisfy each group (oversubscribe vs dedicate, including a compound claim when needed).

The YAML below is a **discussion example** of host-allocation intent, not an API this VEP graduates. Exact field names vs `dedicatedCpuPlacement` are left to the v1.11 follow-up.

```yaml
# Example only: vCPU 0 exclusive; vCPUs 1-3 share two host CPUs
domain:
  cpu:
    sockets: 1
    cores: 4
    threads: 1
    hostAllocation:
      groups:
      - name: exclusive
        targets:
          vcpus: "0"
        hostCPUs:
          count: 1
          # Exclusive prevents these host CPUs from being allocated
          # to another workload.
          accessMode: Exclusive
          # OneToOne pins each selected vCPU to a distinct host CPU.
          mappingPolicy: OneToOne
      - name: shared
        targets:
          vcpus: "1-3"
        hostCPUs:
          count: 2
          # Shared permits other workloads to use the same host CPUs.
          accessMode: Shared
          # SharedSet pins every selected vCPU to the complete allocated
          # host CPU set. Here, vCPUs 1-3 can run on either host CPU.
          mappingPolicy: SharedSet
```

`hostAllocation.groups` here is host-allocation intent (which vCPUs, how many host CPUs, exclusive vs shared). It is not a DRA claim reference. virt-launcher would write `vcpupin` / shared cpusets from the groups plus the allocated IDs.

```xml
<vcpu placement="static">4</vcpu>
<cpu>
  <topology sockets="1" cores="4" threads="1"/>
</cpu>
<cputune>
  <vcpupin vcpu="0" cpuset="2"/>
  <vcpupin vcpu="1" cpuset="6-7"/>
  <vcpupin vcpu="2" cpuset="6-7"/>
  <vcpupin vcpu="3" cpuset="6-7"/>
</cputune>
```

Exact field names and exclusivity vs `dedicatedCpuPlacement` are left to the v1.11 follow-up.

## Open Issues

1. **`dra-driver-cpu` + [KEP-5517](https://github.com/kubernetes/enhancements/issues/5517):** Kubernetes v1.37 already provides kubelet/scheduler Alpha2 support for `DRANodeAllocatableResources`. The claim-only Beta path is blocked on `dra-driver-cpu` publishing `nodeAllocatableResourceMappings`.
2. **Beta QoS after dropping CPU request/limit mirroring:** Alpha keeps **Guaranteed QoS** via mirrored `requests/limits.cpu`. Omitting those fields after mappings land avoids double-booking CPU against `Allocatable.cpu` but can make the pod Burstable. Decide in Beta whether claim-only VMs must stay Guaranteed or use Burstable with DRA cgroup accounting.
