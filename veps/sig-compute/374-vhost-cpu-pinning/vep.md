# VEP #374: vhost-net Thread CPU Pinning

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10.0
- This VEP targets beta for version: v1.11.0
- This VEP targets GA for version: v1.12.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/374)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Add `vhostThreadPolicy` to `spec.domain.cpu` (VMI and instance type), behind a `VhostThreadCPUIsolation` feature gate. When set, KubeVirt reserves one or more extra dedicated pCPUs and pins the VM's `vhost-net` kernel worker thread(s) to them. Requires `isolateEmulatorThread`, and reuses its housekeeping-cgroup mechanism.

## Motivation

With `dedicatedCpuPlacement` + `isolateEmulatorThread`, all threads that don't have their own dedicated CPU, including `vhost-net`'s kernel worker thread(s), share a single pCPU in the "housekeeping" cgroup. Whenever any of those other threads get busy, whether from a high guest packet rate or from unrelated work on that shared pCPU, `vhost-net` has to wait for its turn, adding latency and cutting into network throughput.

## Goals

- Dedicated, opt-in pCPU(s) for `vhost-net` kernel worker threads.
- Reuse the existing housekeeping-cgroup mechanism.
- An API that can grow from "one shared pCPU" to "one pCPU per worker thread" without a breaking change (see Design).

## Non Goals

- Distinguishing `vhost-net` from other vhost-backed devices on the same QEMU process: not possible by name anyway, since the kernel assigns every vhost worker thread the same `vhost-<pid>` name regardless of device type. In practice this means `vhost-vsock` (if VSOCK is enabled) gets isolated the same way as a side effect, which is a welcome bonus, not something worth engineering around.
- Implementing anything beyond the `Shared` policy (see Design) in Alpha. `PerThread`/`Auto` are reserved API values for later.
- Validating that the VM actually uses a `vhost-net`-backed interface.

## Definition of Users

VM owners running dedicated-CPU VMs whose workloads depend on consistent, high-performance networking.

## User Stories

- As a user running a network-sensitive workload on a dedicated-CPU VM, I want `vhost-net` on its own pCPU so it stops competing with everything else sharing the housekeeping pCPU.

## Repos

kubevirt/kubevirt

## Design

New `VhostThreadCPUIsolation` feature gate. New optional string enum `VhostThreadPolicy`, added to the VMI's `spec.domain.cpu` and to `CPUInstancetype`, modeled after the existing `IOThreadsPolicy` (`Shared`/`Auto`, later extended with `SupplementalPool` without an API break):

- `Shared`: one dedicated pCPU for all `vhost-net` worker threads. The only value implemented in Alpha.
- `PerThread`: one dedicated pCPU per `vhost-net` worker thread. Reserved, not implemented yet.
- `Auto`: KubeVirt picks a reasonable number of pCPUs between `Shared` and `PerThread`. Reserved, not implemented yet.

An enum, rather than a bool, because a bool can only ever mean on/off and can't later grow into "one pCPU per thread" without a second field. And rather than a raw CPU count, because users don't know (and shouldn't need to know) how many `vhost-net` worker threads their VM will end up with: that count is an internal detail, driven by multiqueue settings, that KubeVirt already computes today. See Alternatives.

`vmi-create-admitter` rejects the field unless the gate is enabled and `isolateEmulatorThread` is also set, so it always builds on the existing housekeeping cgroup rather than a second cgroup-management path; for Alpha, it also rejects any value other than `Shared`. The resource renderer requests one extra CPU in the virt-launcher pod spec for `Shared`, the same way it does for the emulator thread.

`virt-launcher` allocates the extra pCPU from the same pool used for vCPUs/IOThreads/emulator thread. Libvirt has no notion of "vhost thread pinning" (it's a host kernel thread, not QEMU-managed), so the CPU is recorded in domain metadata (`VhostCPUSet`) instead of the domain XML, cached across migration, and read back by virt-handler. `VhostCPUSet` is defined as a CPU-list string from the start (like `EmulatorPin`'s), so `PerThread`/`Auto` can later populate it with more than one CPU without a metadata format change.

virt-handler already re-applies the housekeeping cgroup on every VMI sync when `isolateEmulatorThread` is set. On top of that, for `Shared` it now:

- Creates a second child cgroup (`vhost`) pinned to `VhostCPUSet`.
- Moves any thread named `vhost-<pid>` into it instead of `housekeeping`. This name isn't specific to `vhost-net`, so a `vhost-vsock` worker (if VSOCK is enabled) is isolated the same way, which is fine: see Non Goals.
- If no such thread exists yet (guest still booting, NIC not up, no such interface at all), that's a no-op for this sync, not an error: nothing to fail or retry.
- Re-runs this same sweep on every later VMI sync, so a `vhost-net` worker thread that doesn't exist yet, or spawns later (e.g. a second queue coming up after boot), gets moved into `vhost` on the first sync after it appears. There's no dedicated fast path for this: convergence speed just tracks however often the VMI already syncs.

A cgroup, not `sched_setaffinity`, is used because the kubelet CPU Manager can reset per-thread affinity but can't override a child cgroup's cpuset; `isolateEmulatorThread` uses the same mechanism for the same reason. Both hypervisor backends (`pkg/hypervisor/{kvm,mshv}`) need this.

## API Examples

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vm-with-pinned-vhost
spec:
  domain:
    cpu:
      cores: 4
      dedicatedCpuPlacement: true
      isolateEmulatorThread: true
      vhostThreadPolicy: Shared
    resources:
      limits:
        cpu: 6 # 4 vCPUs + 1 emulator + 1 vhost
        memory: 4Gi
```

## Alternatives

- **Boolean field**: simplest, but a bool can only ever mean on/off. Growing to `PerThread`/`Auto` later would need a second field instead of a natural extension of this one. Rejected in favor of the enum.
- **Raw CPU count** (e.g. `vhostCPUs: 2`): more flexible than a bool, but forces users to know/guess how many `vhost-net` worker threads their VM will have, an internal detail they shouldn't need to reason about. Rejected in favor of the enum.
- **Per-thread `sched_setaffinity`**: not durable against CPU Manager reconciliation, same reason it was rejected for the emulator thread.
- **Decouple from `isolateEmulatorThread`**: needs the housekeeping cgroup anyway, and skipping it leaves the emulator thread contending with vCPUs. Rejected to avoid a second cgroup-lifecycle path.

## Risks / Open Questions

- `vhostThreadPolicy: Shared` on a VM that never gets a `vhost-net` thread (no such interface, or the guest never brings it up) reserves a pCPU that stays permanently unused, since there's no error state to converge on and give up from, it's simply a no-op on every sync forever. Nothing currently surfaces this to the user; a VMI condition or event would help, worth considering for Beta.
- A `vhost-net` thread that doesn't exist yet at a given sync (guest still booting, or a multi-queue interface bringing up a later queue) is only caught on the next sync (see Design), so nothing is permanently missed, but it can sit in `housekeeping`, contending with the emulator thread, for however long that takes. There's no dedicated fast path to shorten that window today, only whatever cadence the VMI already syncs at.
- Until `PerThread`/`Auto` are implemented, multi-queue interfaces still put all worker threads on the one `Shared` pCPU.
- If VSOCK is also enabled, its `vhost-vsock` worker shares the same pCPU as `vhost-net` (see Non Goals). This seems unlikely to matter in practice: VSOCK carries low-volume, control-plane-style traffic in KubeVirt, not the sustained high packet rates `vhost-net` is meant to handle.

## Scalability

No new controllers, watches, or API resources. One extra pCPU and child cgroup per opted-in VMI today; `PerThread`/`Auto` would scale that with the number of worker threads, when implemented.

## Update/Rollback Compatibility

Optional, gated field, off by default: no impact on existing VMs. Rollback/older components ignore the field and metadata and fall back to today's shared housekeeping cgroup. No version-skew requirement.

## Functional Testing Approach

- **Unit**: cgroup thread split and no-op-when-absent behavior; webhook rejects the field when gated off, `isolateEmulatorThread` is unset, or the value isn't `Shared`; resource renderer; instancetype conflict detection.
- **E2E**: dedicated-CPU VMI with `isolateEmulatorThread` and `vhostThreadPolicy: Shared` set gets `cores + 2` exclusive CPUs, and the `vhost-net` thread's affinity matches `VhostCPUSet`.

## Implementation History

- 2026-06-19: PoC opened, validating the design described above: [kubevirt/kubevirt#18192](https://github.com/kubevirt/kubevirt/pull/18192).

## Graduation Requirements

### Alpha

- [ ] `VhostThreadCPUIsolation` feature gate
- [ ] `vhostThreadPolicy` field (`Shared` only) on VMI `CPU` and `CPUInstancetype`
- [ ] Extra pCPU requested via resource renderer; `VhostCPUSet` computed, cached, and propagated through migration
- [ ] virt-handler pins `vhost-net` threads for both KVM and MSHV via a sweep that reruns on every sync and no-ops when no matching thread exists yet
- [ ] Unit + e2e tests, user-guide docs

### Beta

- [ ] Surface a VMI condition/event when isolation could not be applied (see Risks)

#### On-By-Default Readiness

- [ ] Throughput/latency benchmarks vs. shared housekeeping cgroup
- [ ] Feature gate enabled by default

### GA

- [ ] Feature gate removed
- [ ] Implement `PerThread`/`Auto` based on user feedback
