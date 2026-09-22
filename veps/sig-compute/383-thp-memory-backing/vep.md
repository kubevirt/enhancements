# VEP: THP Memory Backing for Virtual Machines

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: 1.10
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] ([#383](https://github.com/kubevirt/enhancements/issues/383))
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This enhancement allows VMs declaring hugepages to run without pre-allocated static hugepages on the node. Instead, guest memory is backed by regular anonymous memory with `mlock` and immediate preallocation. The kernel may promote pages to Transparent Huge Pages (THP) at fault time and later via `khugepaged` (including on mlocked VMAs). Coverage is opportunistic; KubeVirt does not synchronously collapse pages.

The native THP size is architecture-dependent (2M on x86_64, 1M on s390x, etc. — see Architecture Considerations). This VEP uses "THP" generically for the platform's native transparent huge page size.

The VM retains its hugepages declaration in the spec (preserving NUMA topology and domain XML generation), but the pod is built with regular memory resources. virt-handler raises `RLIMIT_MEMLOCK` on virtqemud so the memory can be locked.

THP mode is selected via a per-VMI API (`hugepages.mode`). An optional KubeVirt CR cluster default can apply transparent mode to hugepage VMIs that omit `mode`, so administrators can opt the whole cluster into THP without rewriting every workload. When that cluster configuration is unset, behavior matches today: static hugepages unless the VMI explicitly sets `mode: transparent`.

## Motivation

Static hugepages require cluster-level pre-allocation: administrators must configure nodes with a fixed number of reserved hugepages at boot or runtime. This creates operational burdens:

1. **Capacity planning** — hugepages reserved at boot are unavailable for other workloads, even when no VM uses them.
2. **Scheduling constraints** — VMs can only land on nodes with sufficient pre-allocated hugepages, reducing scheduler flexibility.
3. **NUMA fragmentation** — hugepages must be allocated on the correct NUMA node; imbalanced allocation leads to scheduling failures.
4. **Cluster heterogeneity** — different VM sizes need different hugepage reservations, complicating node profiles.
5. **Operational toil** — changing hugepage reservations requires node reboots or careful runtime tuning with risk of allocation failure.

To simplify operations, an administrator can move the whole cluster to THP with a KubeVirt CR default instead of rewriting every hugepage VMI. Per-VMI `mode` remains for mixed clusters and overrides.

QEMU already advises THP on anonymous guest RAM (`MADV_HUGEPAGE`). With immediate preallocation and host THP enabled, the kernel can allocate PMD-sized pages at fault time; `khugepaged` may collapse remaining 4K pages later — including the mlocked anonymous pages.

### Proof of Concept

An out-of-tree addon ([kubevirt-hugepages-addon](https://github.com/michalskrivanek/kubevirt-hugepages-addon)) demonstrates feasibility using admission webhooks and a sidecar hook. It requires several runtime workarounds due to operating outside KubeVirt's control plane. Native integration eliminates all of them.

## Goals

- Allow VMs declaring hugepages to run without pre-allocated hugepages on the node.
- Preserve hugepage-like **mlocked** and **immediately preallocated** guest RAM.
- Allow the kernel to promote that memory to THP opportunistically when the node can provide it.
- Preserve existing NUMA topology generation and domain XML structure.
- Use the standard KubeVirt memory overhead calculation — no special buffer hacks.
- Require no changes to libvirt or QEMU.
- Provide a per-VMI API as the user opt-in, with a cluster-level opt-in for administrators who want THP without rewriting every hugepage VMI.

## Non Goals

- Replacing static hugepages by default — both modes coexist; without an explicit VMI `mode` or an explicit cluster default, behavior remains static hugepages.
- Guaranteeing THP coverage or failing the VMI when coverage is low.
- memfd-backed VMs in Alpha (virtiofs, passt, and other shared-memory paths).
- Supporting 1G hugepages dynamically — THP uses the architecture's native PMD size, not arbitrary sizes.
- Overcommitting guest memory — `<locked/>` prevents swapping; this is a performance feature, not an overcommit feature.
- Modifying the kernel's THP subsystem behavior (defrag, khugepaged tuning).

## Definition of Users

- Users running VMs that benefit from hugepages performance but want simpler cluster operations.
- Cluster administrators who want to avoid static hugepage reservation and NUMA planning.
- Cloud providers offering VM-based workloads where hugepage pre-allocation limits bin-packing.

## User Stories

- As a user, I want my VM to get THP performance without requiring hugepages to be pre-allocated on the node.
- As a user, I want to opt a single VMI into THP via `hugepages.mode` without changing cluster-wide defaults.
- As a cluster admin, I want an optional KubeVirt CR default so existing hugepage workloads can use THP without rewriting every VMI.
- As a user, I want my VM to be schedulable on any node with sufficient free memory, not only nodes with hugepages available.
- As a user, I want THP promotion when the node can provide it, without the VM failing if coverage is incomplete.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### API

A new `mode` field is added to `spec.domain.memory.hugepages`. Value `transparent` selects regular anonymous memory with locked immediate preallocation (THP-eligible), distinct from static hugetlbfs via sysfs (`nr_hugepages`):

```yaml
spec:
  domain:
    memory:
      hugepages:
        pageSize: "2Mi"
        mode: transparent
```

| Field | Values | Default | Valid when |
|-------|--------|---------|------------|
| `mode` | `static`, `transparent` | `static` | always |
| `pageSize` | e.g. `"2Mi"` | required when hugepages set | always |

`mode` accepts two values:
- `static` (default, current behavior) — pod requests hugepages resources, node must have pre-allocated hugepages.
- `transparent` — pod requests regular memory; domain XML uses locked + immediate allocation (no `<hugepages/>`) to allow THP promotion by kernel.

Absence of the `mode` field preserves current behavior (`static`), unless cluster `defaultMode` is set.

When THP mode is selected, `pageSize` is retained for NUMA topology and domain XML generation but does **not** control the THP size — the architecture's native THP size applies (see Architecture Considerations).

Administrators can set a cluster default on the KubeVirt CR so hugepage VMIs that omit `mode` use transparent mode without rewriting every workload:

```yaml
spec:
  configuration:
    hugepages:
      defaultMode: transparent       # static | transparent
```

If cluster `hugepages` is unset, omitted `mode` remains `static`. The feature gate alone does not change existing hugepage VMIs. Precedence: VMI `mode` → cluster `defaultMode` → `static`.

### Component Changes

#### virt-controller (pod template generation)

When THP mode is selected (VMI `mode: transparent` or cluster `defaultMode: transparent`):

1. **Do not add `hugepages-*` resources** to the compute container. Instead, set `resources.requests.memory` and `resources.limits.memory` to `guest_memory + GetMemoryOverhead()` using the standard overhead formula (same path as non-hugepages VMs).
2. **Do not create the `hugepages` emptyDir volume** with `medium: HugePages`.
3. **Do not mount `/dev/hugepages`** in the compute container. This prevents virt-launcher from writing `hugetlbfs_mount` to `qemu.conf`, which would cause virtqemud to fail initialization.
4. **Set RLIMIT_MEMLOCK requirement** — triggers the VEP 144 mechanism so virt-handler raises the memory lock limit on virtqemud.

#### virt-handler (memory lock)

When a VMI uses THP mode:

1. **Memory lock**: Call `prlimit64` on the virt-launcher/virtqemud process to set `RLIMIT_MEMLOCK = RLIM_INFINITY`. This is the same mechanism already used for VFIO passthrough VMs. When libvirt later processes `<locked/>` in the domain XML and calls `setrlimit`, the limit is already high enough and libvirt's check (`current >= limit`) passes — making it a no-op.

2. **Preallocation**: Set allocation mode `immediate`. Promotion is left to fault-time allocation and `khugepaged`. After live migration, pages arrive as 4K; destination THP recover under the same host policy over time.

#### Domain converter (XML generation)

When THP mode is selected, the converter generates:

```xml
<memoryBacking>
  <source type="anonymous"/>
  <locked/>
  <allocation mode="immediate"/>
  <nosharepages/>
</memoryBacking>
```

Instead of the static hugepages XML:

```xml
<memoryBacking>
  <hugepages>
    <page size="2048" unit="KiB"/>
  </hugepages>
  <source type="memfd"/>
  <locked/>
  <allocation mode="immediate"/>
</memoryBacking>
```

Key differences:
- No `<hugepages>` section — memory is anonymous, not backed by hugetlbfs.
- `<source type="anonymous"/>` — regular anonymous mmap, THP-eligible (subject to host policy).
- `<locked/>` — mlocks all QEMU memory, preventing swapping. Same semantics as static hugepages.
- `<allocation mode="immediate"/>` — preallocates and faults all memory at VM start, consistent with static hugepages VMs.
- `<nosharepages/>` — disables KSM page merging for guest RAM (same effect as `kubevirt.io/mergeable-memory=false`). Applied implicitly so KSM does not compete with THP promotion on guest RAM.

NUMA topology (`<numatune>`, `<cpu><numa>`) is generated identically to static hugepages — the kernel's NUMA policy applies to anonymous pages the same way.

### Session Mode Compatibility

libvirt runs as uid 107 in the virt-launcher container ("session mode"). This has two implications:

1. **`<memtune><hard_limit>` is rejected** — libvirt does not support memory tuning in session mode because it cannot manipulate cgroups as non-root.
2. **libvirt cannot raise `RLIMIT_MEMLOCK` itself** — when processing `<locked/>`, libvirt calls `setrlimit(RLIMIT_MEMLOCK, RLIM_INFINITY)` on the QEMU child process. This fails with `EPERM` because virtqemud lacks `CAP_SYS_RESOURCE` (virt-launcher only passes `CAP_NET_BIND_SERVICE` as an ambient capability).

The design addresses both by having **virt-handler set the limit externally** before domain creation. virt-handler runs as root on the host and calls `prlimit64` to set `RLIM_INFINITY` on the virtqemud process. When libvirt subsequently attempts `setrlimit`, it finds `current >= requested` and the call becomes a no-op. This is the same proven pattern used for VFIO passthrough VMs.

### Memory Overhead

When THP mode is selected, KubeVirt uses its **standard (non-hugepages) memory overhead formula**. No special calculation is needed because:

- With static hugepages: guest RAM is in the `hugetlb` cgroup controller (not charged to `memory` cgroup), so the `memory` resource only needs to cover QEMU process overhead.
- With THP mode: guest RAM is in the `memory` cgroup (like any normal VM), so `memory` resource = guest + overhead — the same path as any non-hugepages VM.

The standard overhead formula already accounts for page tables, QEMU buffers, and kernel tracking structures for regular memory. No additional buffer is needed.

### Feature Gate

`THPMemoryBacking`

## Alternatives

### Sidecar-based addon (current PoC)

**Description**: External admission webhooks mutate VMI and Pod objects; a sidecar hook rewrites domain XML and calls `prlimit64` on virtqemud.

**Pros**:
- No KubeVirt code changes required.
- Can be deployed independently on existing clusters.

**Cons**:
- Requires several runtime workarounds (shared PID namespace, root sidecar, capability elevation, SCC patches, mount redirection, socket umask, memory buffer hacks).
- Fragile — depends on KubeVirt's internal pod structure remaining stable across versions.
- Security: sidecar runs as root with `CAP_SYS_PTRACE` + `CAP_SYS_RESOURCE`.
- Memory overhead calculation is imprecise (adds a fixed 1 GiB buffer instead of using KubeVirt's formula).

### Explicit `MADV_COLLAPSE` from virt-handler

**Description**: After preallocation, virt-handler scans QEMU smaps and calls `process_madvise(MADV_COLLAPSE)`, optionally with coverage policies and a fail gate. The original VEP proposal.

**Pros**:
- Synchronous, predictable promotion at start and after migration.

**Cons**:
- Substantial privileged handler complexity and coupling to VM lifecycle
- `khugepaged` attempts collapse using the same mechanism repeatedly as opposed to a one-time madv_collapse, ultimately providing higher coverage.

Rejected for alpha in favor of lock + prealloc + kernel promotion.

### Kernel-level always-THP without mlock / without prealloc

**Description**: Rely only on `transparent_hugepage=always` with neither `<locked/>` nor immediate allocation.

**Pros**:
- Zero KubeVirt memory-backing changes beyond pod resources.

**Cons**:
- Without `mlock`, guest memory can be swapped under pressure.
- Without preallocation, first-touch faults cause runtime jitter.
- Does not meet the goal of expressing hugepage-like locked + preallocated memory.

### API-less cluster-only conversion

**Description**: No VMI `mode` field. Enabling a KubeVirt CR flag converts every hugepage VMI to transparent mode.

**Pros**:
- Zero user YAML change once the admin enables the feature.
- Smaller API surface.

**Cons**:
- Breaks existing hugepage scheduling semantics for all workloads when enabled (resources flip from `hugepages-*` to ordinary memory).
- Poor fit for mixed clusters that still need real hugetlb (e.g. `pageSize: 1Gi`).

## Scalability

Startup cost is proportional to VM size due to immediate preallocation. For very large and fragmented systems this can be noticable (tens of seconds). Ongoing THP work is done by the kernel (`khugepaged`) with no extra KubeVirt CPU overhead.

Node scheduling capacity improves compared to static hugepages: VMs compete for generic `memory` resources instead of scarce architecture-specific hugepage resources, enabling better bin-packing across the cluster.

## Update/Rollback Compatibility

- New fields are optional; omission or a rollback without `THPMemoryBacking` preserves static hugepages behavior when cluster `configuration.hugepages` is also unset.
- Running VMs are unaffected until restart or migration.

## Functional Testing Approach

### Unit Tests

- virt-controller generates correct pod spec for THP mode (no hugepages resources, correct memory limits, no hugetlbfs volume, no `/dev/hugepages` mount).
- Domain converter generates correct XML (`<memoryBacking>` with anonymous source, locked, immediate allocation, no hugepages).
- Memory overhead calculation matches the standard non-hugepages path.
- NUMA topology generation is identical for both static and THP modes.
- virt-handler sets RLIMIT_MEMLOCK for THP-mode VMs and does not invoke collapse.
- Admission admits `mode: transparent` only when the feature gate is enabled.
- Mode resolution: VMI fields override cluster defaults; unset cluster config yields `static`.

### E2E Tests

**Alpha:**

- VM with THP mode starts successfully on a node with zero pre-allocated hugepages.
- Guest RAM is mlocked and fully resident after start.
- With `defaultMode: transparent`, a hugepage VMI omitting `mode` uses transparent pod/XML shape.
- Pre-copy live migration of a THP-mode VM succeeds; destination keeps locked + immediate semantics (THP coverage lost, recovers under host policy).

**Beta:**

- VM with THP mode and NUMA passthrough respects NUMA binding.

## Prerequisites

- KubeVirt with VEP 144 (Memory Lock RLimit configuration) at Beta or GA — provides the `prlimit64` mechanism on virt-handler.
- THP enabled on the node: `/sys/kernel/mm/transparent_hugepage/enabled` set to `always` or `madvise`.

## Graduation Requirements

### Alpha

- Feature gate `THPMemoryBacking`.
- virt-controller: generate pod spec without hugepages resources/volumes for THP-mode VMs.
- Domain converter: generate anonymous/locked/immediate/nosharepages XML.
- virt-handler: set `RLIMIT_MEMLOCK` for THP-mode VMs (no collapse).
- Optional cluster `configuration.hugepages.defaultMode`.
- Unit tests for all components.
- E2E tests: startup without pre-allocated hugepages; locked + preallocated guest RAM; cluster `defaultMode` applies transparent mode.

### Beta

- Documentation for operators (host THP expectations).
- THP working for memfd-backed VMs (virtiofs, passt, and other shared-memory paths).

### GA

- Remove feature gate.
- Proven in production with diverse workloads and node configurations.
- Node readiness reporting (THP capability in node conditions or labels).

### Architecture Considerations

THP is available on architectures with `CONFIG_TRANSPARENT_HUGEPAGE`. The native THP size is determined by the architecture's PMD granularity:

| Architecture | Base page | Native THP size |
|-------------|-----------|----------------|
| x86_64 | 4K | 2M |
| aarch64 (4K pages) | 4K | 2M |
| aarch64 (64K pages) | 64K | 512M |
| s390x | 4K | 1M |
| ppc64le (radix, 4K) | 4K | 2M |
| ppc64le (64K pages) | 64K | 16M |

The feature works on all architectures, but the TLB pressure reduction benefits vary significantly with native THP size. On x86_64 and aarch64 (4K), collapsing 512 PTEs into a single PMD entry provides a direct and well-understood performance improvement. On architectures with very large native THP (512M on aarch64-64K, 16M on ppc64le-64K), the collapse granularity is coarser and the tradeoffs differ.

Alpha targets x86_64. Other architectures are expected to work but are validated in later stages.

## References

- [VEP #144: Memory Lock RLimit configuration](https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/144-memory-lock-rlimits/memory-lock-rlimits.md)
- [Proof of concept addon](https://github.com/michalskrivanek/kubevirt-hugepages-addon)
- [THP PoC by Fabian Deutsch](https://github.com/fabiand/thp-poc)
- [Kernel documentation: Transparent Hugepage Support](https://www.kernel.org/doc/Documentation/vm/transhuge.txt)
