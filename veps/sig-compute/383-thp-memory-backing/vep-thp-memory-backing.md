# VEP: THP Memory Backing for Virtual Machines

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: 1.10
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This enhancement allows VMs declaring hugepages to run without
pre-allocated static hugepages on the node. Instead, guest memory is
backed by regular anonymous pages that are collapsed into Transparent
Huge Pages (THP) at runtime using `MADV_COLLAPSE`. Combined with `mlock`
and immediate preallocation, this achieves equivalent TLB performance to
static hugepages.

The native THP size is architecture-dependent (2M on x86_64, 1M on
s390x, etc. — see Architecture Considerations below). This VEP uses
"THP" generically to mean the platform's native transparent huge page
size.

The VM retains its hugepages declaration in the spec (preserving NUMA
topology and domain XML generation), but the pod is built with regular
memory resources. virt-handler raises `RLIMIT_MEMLOCK` on virtqemud and
a collapse call promotes pages to THP after preallocation completes.

## Motivation

Static hugepages require cluster-level pre-allocation: administrators
must configure nodes with a fixed number of reserved hugepages at boot
or runtime. This creates operational burdens:

1. **Capacity planning** — hugepages reserved at boot are unavailable
   for other workloads, even when no VM uses them.
2. **Scheduling constraints** — VMs can only land on nodes with
   sufficient pre-allocated hugepages, reducing scheduler flexibility.
3. **NUMA fragmentation** — hugepages must be allocated on the correct
   NUMA node; imbalanced allocation leads to scheduling failures.
4. **Cluster heterogeneity** — different VM sizes need different
   hugepage reservations, complicating node profiles.
5. **Operational toil** — changing hugepage reservations requires node
   reboots or careful runtime tuning with risk of allocation failure.

Modern kernels (RHEL 9.2+, upstream 6.1+) support `MADV_COLLAPSE`,
which synchronously collapses regular pages into THPs. Combined with
`mlock` and immediate preallocation, this achieves identical TLB
performance to static hugepages without any pre-allocation.

### Proof of Concept

An out-of-tree addon
([kubevirt-hugepages-addon](https://github.com/michalskrivanek/kubevirt-hugepages-addon))
demonstrates feasibility using admission webhooks and a sidecar hook. It
requires several runtime workarounds due to operating outside KubeVirt's
control plane. Native integration eliminates all of them.

## Goals

- Allow VMs declaring hugepages to run without pre-allocated hugepages
  on the node.
- Achieve equivalent TLB performance to static hugepages via THP
  collapse.
- Preserve existing NUMA topology generation and domain XML structure.
- Use the standard KubeVirt memory overhead calculation — no special
  buffer hacks.
- Require no changes to libvirt or QEMU.
- Support two operational modes: guaranteed THP coverage (VM fails if
  collapse incomplete) and best-effort (collapse is opportunistic,
  coverage reported in status).

## Non Goals

- Replacing static hugepages — both modes coexist; THP mode is opt-in.
- Guaranteeing 100% THP coverage on fragmented nodes — the guaranteed
  mode defines a threshold and failure path, not a physical guarantee.
- Supporting 1G hugepages dynamically — `MADV_COLLAPSE` targets the
  architecture's native THP size, not arbitrary sizes.
- Overcommitting guest memory — `<locked/>` prevents swapping; this is
  a performance feature, not an overcommit feature.
- Modifying the kernel's THP subsystem behavior (defrag, khugepaged
  tuning).

## Definition of Users

- Users running VMs that benefit from hugepages performance but want
  simpler cluster operations.
- Cluster administrators who want to avoid static hugepage reservation
  and NUMA planning.
- Cloud providers offering VM-based workloads where hugepage
  pre-allocation limits bin-packing.

## User Stories

- As a user, I want my VM to get THP performance without requiring
  hugepages to be pre-allocated on the node.
- As a cluster admin, I want to stop managing hugepage reservations
  across heterogeneous nodes.
- As a user, I want my VM to be schedulable on any node with sufficient
  free memory, not only nodes with hugepages available.
- As a user deploying latency-sensitive workloads, I want guaranteed
  THP backing with a clear failure signal if the node cannot provide it.
- As a user deploying general-purpose VMs, I want best-effort THP with
  visibility into actual coverage.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### API

A new `mode` field is added to `spec.domain.memory.hugepages`. The
naming uses "thp" explicitly to distinguish from the kernel's dynamic
hugepage allocation via sysfs (`nr_hugepages`):

```yaml
spec:
  domain:
    memory:
      hugepages:
        pageSize: "2Mi"
        mode: thp
```

`mode` accepts two values:
- `static` (default, current behavior) — pod requests hugepages
  resources, node must have pre-allocated hugepages.
- `thp` — pod requests regular memory, pages are collapsed to THP at
  runtime.

Absence of the `mode` field preserves current behavior (`static`).

**Strictness policy (TBD):** The feature should support two operational
modes — guaranteed (VM fails to reach `Running` if collapse does not
achieve sufficient coverage) and best-effort (collapse is opportunistic,
partial coverage reported in status). The exact API for expressing this
(sub-field, enum value, separate field) is to be determined during
review.

### Component Changes

#### virt-controller (pod template generation)

When THP mode is selected:

1. **Do not add `hugepages-*` resources** to the compute container.
   Instead, set `resources.requests.memory` and
   `resources.limits.memory` to `guest_memory + GetMemoryOverhead()`
   using the standard overhead formula (same path as non-hugepages VMs).

2. **Do not create the `hugepages` emptyDir volume** with
   `medium: HugePages`.

3. **Do not mount `/dev/hugepages`** in the compute container. This
   prevents virt-launcher from writing `hugetlbfs_mount` to
   `qemu.conf`, which would cause virtqemud to fail initialization.

4. **Set RLIMIT_MEMLOCK requirement** — triggers the VEP 144 mechanism
   so virt-handler raises the memory lock limit on virtqemud.

#### virt-handler (memory lock and THP collapse)

When a VMI uses THP mode:

1. **Memory lock**: Call `prlimit64` on the virt-launcher/virtqemud
   process to set `RLIMIT_MEMLOCK = RLIM_INFINITY`. This is the same
   mechanism already used for VFIO passthrough VMs. When libvirt later
   processes `<locked/>` in the domain XML and calls `setrlimit`, the
   limit is already high enough and libvirt's check
   (`current >= limit`) passes — making it a no-op.

2. **THP collapse**: After QEMU preallocates memory (detected by
   monitoring `VmLck` stabilization in `/proc/<pid>/status`):
   - Parse `/proc/<pid>/maps` for large anonymous RW regions.
   - Call `process_madvise(pidfd, iov, MADV_COLLAPSE)` to synchronously
     collapse pages into THPs.
   - Report coverage ratio in VMI status.
   - For guaranteed policy: if coverage < threshold, transition VMI to
     `Failed`.
   - For best-effort policy: Falls back to `khugepaged` for regions that
     cannot be immediately collapsed. Log result, update status, continue.


virt-handler already runs privileged on the host with access to process
PIDs and `CAP_SYS_PTRACE` — no additional capabilities are needed.

#### Domain converter (XML generation)

When THP mode is selected, the converter generates:

```xml
<memoryBacking>
  <source type="anonymous"/>
  <locked/>
  <allocation mode="immediate" threads="4"/>
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
- No `<hugepages>` section — memory is anonymous, not backed by
  hugetlbfs.
- `<source type="anonymous"/>` — regular anonymous mmap, eligible for
  THP collapse.
- `<locked/>` — mlocks all QEMU memory, preventing swapping. Same
  semantics as static hugepages.
- `<allocation mode="immediate" threads="4"/>` — preallocates and
  faults all memory at VM start. Ensures pages exist before collapse.

NUMA topology (`<numatune>`, `<cpu><numa>`) is generated identically to
static hugepages — the kernel's NUMA policy applies to anonymous pages
the same way.

### Session Mode Compatibility

libvirt runs as uid 107 in the virt-launcher container ("session mode").
This has two implications:

1. **`<memtune><hard_limit>` is rejected** — libvirt does not support
   memory tuning in session mode ("Memory tuning is not available in
   session mode") because it cannot manipulate cgroups as non-root.

2. **libvirt cannot raise `RLIMIT_MEMLOCK` itself** — when processing
   `<locked/>`, libvirt calls `setrlimit(RLIMIT_MEMLOCK, RLIM_INFINITY)`
   on the QEMU child process. This fails with `EPERM` because virtqemud
   lacks `CAP_SYS_RESOURCE` (virt-launcher only passes
   `CAP_NET_BIND_SERVICE` as an ambient capability).

The design addresses both by having **virt-handler set the limit
externally** before domain creation. virt-handler runs as root on the
host and calls `prlimit64` to set `RLIM_INFINITY` on the virtqemud
process. When libvirt subsequently attempts `setrlimit`, it finds
`current >= requested` and the call becomes a no-op. This is the same
proven pattern used for VFIO passthrough VMs.

### Memory Overhead

When THP mode is selected, KubeVirt uses its **standard (non-hugepages)
memory overhead formula**. No special calculation is needed because:

- With static hugepages: guest RAM is in the `hugetlb` cgroup controller
  (not charged to `memory` cgroup), so the `memory` resource only needs
  to cover QEMU process overhead.
- With THP mode: guest RAM is in the `memory` cgroup (like any normal
  VM), so `memory` resource = guest + overhead — the same path as any
  non-hugepages VM.

The standard overhead formula already accounts for page tables, QEMU
buffers, and kernel tracking structures for regular memory. No
additional buffer is needed.

### Status Reporting

VMI status will report THP coverage regardless of policy:

```yaml
status:
  memory:
    thp:
      coverage: "98.4%"
      collapsedBytes: 19394658304
      totalBytes: 19713515520
      collapseTime: "2.3s"
  conditions:
    - type: THPCollapsed
      status: "True"
      message: "98.4% of guest memory backed by THP"
```

For guaranteed policy with failure:

```yaml
status:
  phase: Failed
  conditions:
    - type: THPCollapsed
      status: "False"
      reason: THPCollapseFailed
      message: "Only 45% THP coverage achieved; node lacks free order-9 blocks"
```

### Feature Gate

`THPMemoryBacking`

## Alternatives

### Sidecar-based addon (current PoC)

**Description**: External admission webhooks mutate VMI and Pod objects;
a sidecar hook rewrites domain XML and calls `prlimit64` on virtqemud.

**Pros**:
- No KubeVirt code changes required.
- Can be deployed independently on existing clusters.

**Cons**:
- Requires several runtime workarounds (shared PID namespace, root sidecar,
  capability elevation, SCC patches, mount redirection, socket umask,
  memory buffer hacks).
- Fragile — depends on KubeVirt's internal pod structure remaining
  stable across versions.
- Security: sidecar runs as root with `CAP_SYS_PTRACE` +
  `CAP_SYS_RESOURCE`.
- Memory overhead calculation is imprecise (adds a fixed 1 GiB buffer
  instead of using KubeVirt's formula).

### Kernel-level always-THP without mlock

**Description**: Rely on `transparent_hugepage=always` and let the
kernel promote pages passively via khugepaged.

**Pros**:
- Zero code changes anywhere.

**Cons**:
- No guarantee of THP promotion timing — khugepaged is asynchronous and
  may take minutes or hours for large allocations.
- Without `mlock`, guest memory can be swapped under pressure, causing
  unpredictable latency spikes.
- No preallocation — page faults during VM runtime cause jitter.
- Cannot guarantee NUMA locality of promotions.

## Scalability

The THP collapse does a one-shot operation at VM startup (1-3 seconds for
18 GiB of guest memory). This may be noticeable for large VMs, however there
on no ongoing CPU or memory overhead after collapse completes.

Node scheduling capacity improves compared to static hugepages: VMs
compete for generic `memory` resources instead of scarce
architecture-specific hugepage resources, enabling better bin-packing
across the cluster.

## Update/Rollback Compatibility

- The new API field is optional; absence preserves current behavior
  (static hugepages). Fully backward compatible.
- On rollback to a version without the `THPMemoryBacking` feature gate:
  VMIs configured for THP mode will be treated as standard hugepages
  VMs. They will fail to schedule if no static hugepages are available
  on the node. This is safe — the VM will not start, no data loss
  occurs.
- Running VMs are not affected by rollback until they are restarted or
  migrated.

## Functional Testing Approach

### Unit Tests

- virt-controller generates correct pod spec for THP mode (no hugepages
  resources, correct memory limits, no hugetlbfs volume, no
  `/dev/hugepages` mount).
- Domain converter generates correct XML (`<memoryBacking>` with
  anonymous source, locked, immediate allocation, no hugepages).
- Memory overhead calculation matches the standard non-hugepages path.
- NUMA topology generation is identical for both static and THP modes.
- virt-handler sets RLIMIT_MEMLOCK for THP-mode VMs.

### E2E Tests

- VM with THP mode starts successfully on a node with zero
  pre-allocated hugepages.
- VM with THP mode achieves >95% THP coverage (`anon_thp / anon` from
  cgroup memory stats).
- VM with THP mode and NUMA passthrough respects NUMA binding.
- Live migration of a THP-mode VM preserves THP coverage on the
  destination node.

## Prerequisites

- KubeVirt with VEP 144 (Memory Lock RLimit configuration) at Beta or
  GA — provides the `prlimit64` mechanism on virt-handler.
- Kernel with `MADV_COLLAPSE` support: RHEL 9.2+ (kernel-5.14.0-284+)
  or upstream kernel 6.1+.
- THP enabled on the node:
  `/sys/kernel/mm/transparent_hugepage/enabled` set to `always` or
  `madvise`.

## Graduation Requirements

### Alpha

- Feature gate `THPMemoryBacking`.
- virt-controller: generate pod spec without hugepages resources/volumes
  for THP-mode VMs.
- Domain converter: generate anonymous/locked/immediate XML.
- virt-handler: set `RLIMIT_MEMLOCK` for THP-mode VMs.
- THP `MADV_COLLAPSE` collapse call (single pass, log and report coverage).
- Unit tests for all components.

### Beta

- Guaranteed policy support (VM fails if coverage below threshold).
- E2E tests covering startup, THP coverage, NUMA, migration, both
  policies.
- VMI status reporting (`status.memory.thp`).
- Metrics: `kubevirt_vmi_thp_collapse_ratio` gauge,
  `kubevirt_vmi_thp_collapse_duration_seconds` histogram.
- Documentation.

### GA

- Remove feature gate.
- Proven in production with diverse workloads and node configurations.
- Node readiness reporting (THP capability in node conditions or
  labels).

### Architecture Considerations

`MADV_COLLAPSE` is architecture-independent (defined in generic UAPI
headers, available on all architectures with
`CONFIG_TRANSPARENT_HUGEPAGE`). The native THP size is determined by the
architecture's PMD granularity:

| Architecture | Base page | Native THP size |
|-------------|-----------|----------------|
| x86_64 | 4K | 2M |
| aarch64 (4K pages) | 4K | 2M |
| aarch64 (64K pages) | 64K | 512M |
| s390x | 4K | 1M |
| ppc64le (radix, 4K) | 4K | 2M |
| ppc64le (64K pages) | 64K | 16M |

The feature works on all architectures, but the TLB pressure reduction
benefits vary significantly with native THP size. On x86_64 and
aarch64 (4K), collapsing 512 PTEs into a single PMD entry provides a
direct and well-understood performance improvement. On architectures
with very large native THP (512M on aarch64-64K, 16M on ppc64le-64K),
the collapse granularity is coarser and the tradeoffs differ.

Alpha targets x86_64. Other architectures are expected to work but are
validated in later stages.

## References

- [VEP #144: Memory Lock RLimit configuration](https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/144-memory-lock-rlimits/memory-lock-rlimits.md)
- [MADV_COLLAPSE — LWN](https://lwn.net/Articles/887753/)
- [Proof of concept addon](https://github.com/michalskrivanek/kubevirt-hugepages-addon)
- [THP PoC by Fabian Deutsch](https://github.com/fabiand/thp-poc)
- [Kernel documentation: Transparent Hugepage Support](https://www.kernel.org/doc/Documentation/vm/transhuge.txt)
