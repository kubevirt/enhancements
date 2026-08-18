# VEP #326: Expose `directsync` and `unsafe` disk cache modes

## VEP Status Metadata

### Target releases

- This VEP targets GA for version: v1.10

### Rationale for targeting GA directly

These cache modes (`directsync` and `unsafe`) are not new hypervisor features. They have been stable, well-understood primitives in QEMU and libvirt for over a decade:

- `directsync` has been supported by QEMU since 2011 ([QEMU commit 92196b2f](https://github.com/qemu/qemu/commit/92196b2f5664d5defa778b1b24df56e2239b5e93)) and treated as migration-safe for shared storage by libvirt since 2017 ([libvirt commit fed9cc8](https://github.com/libvirt/libvirt/commit/fed9cc8)).
- `unsafe` has been supported by QEMU since 2010 ([QEMU commit 016f5cf6](https://github.com/qemu/qemu/commit/016f5cf6ff465411733878a17c8f8febb7668321)).
- Libvirt has supported live migration for all cache modes (including those using the host page cache) since 2019 ([libvirt commit 4514abb](https://github.com/libvirt/libvirt/commit/4514abb)).
- OpenStack Nova has exposed all five cache modes as first-class configuration options (`disk_cachemodes`) for years ([Nova libvirt config](https://docs.openstack.org/nova/latest/configuration/config.html#libvirt.disk_cachemodes)).
- KubeVirt's own CI infrastructure ([kubevirtci](https://github.com/kubevirt/kubevirtci)) has used `cache=unsafe` for its VM disks since its early days.
- Community users have independently requested both modes: [kubevirt#14265](https://github.com/kubevirt/kubevirt/issues/14265), [kubevirt#14363](https://github.com/kubevirt/kubevirt/pull/14363), [harvester#7906](https://github.com/suse-edge/harvester/issues/7906).

This proposal does not introduce a new API field; it adds two values to an existing `DriverCache` enum that already exposes three of libvirt's five cache modes. The validation, defaulting, and I/O path logic already exist. A feature gate would add process overhead without meaningful risk reduction for values that are over a decade old in the underlying stack.

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt currently exposes three disk cache modes to users: `none`, `writethrough`, and `writeback`. The underlying hypervisor (QEMU/libvirt) supports two additional modes -- `directsync` and `unsafe` -- that address specific performance and data-safety trade-offs not covered by the existing three. This VEP proposes adding `directsync` and `unsafe` as first-class `DriverCache` values in the KubeVirt API, along with the validation, defaulting, and test coverage required to support them.

## Motivation

Users running database workloads (e.g. MSSQL, PostgreSQL) inside VMs often need precise control over the I/O path to balance performance and durability. The existing three modes leave gaps:

- **No "safe + bypass host cache" option without guest flush penalties**: `none` bypasses the host page cache (via `O_DIRECT`) but reports writeback cache to the guest, causing QEMU to issue `fdatasync()` on every guest write. `directsync` also bypasses the host cache but issues `fdatasync()` only on guest-initiated flush commands, which can significantly reduce I/O overhead for flush-rare workloads on storage with fast sync (NVMe, BBU-backed RAID).
- **No "maximum throughput, relaxed durability" option**: Some workloads (CI pipelines, ephemeral VMs, batch processing) tolerate data loss on host crash in exchange for maximum I/O throughput. `unsafe` disables all host-side flush processing and caches aggressively, which is currently not expressible.

Additionally, `directsync` is safe for shared/multi-attach disks (it uses `O_DIRECT`, ensuring data coherency), but KubeVirt currently rejects all shareable disks unless `cache=none`.

### Performance evidence

HammerDB TPC-C benchmarks against Microsoft SQL Server on Windows Server 2025 (32 vCPU, 440 GiB memory, virtio with 8 I/O multiqueues, 1 GiB hugepages, Intel Sapphire Rapids) show a consistent ~43-48% throughput improvement when switching from `cache=none` to `cache=directsync` using a temporary hook (see https://github.com/kubevirt/kubevirt/pull/17718):


| Virtual users            | 100        | 200        | 300        | 400        | 500        |
| ------------------------ | ---------- | ---------- | ---------- | ---------- | ---------- |
| `cache=none` (TPM)       | 1,023,457  | 1,041,870  | 1,004,201  | 999,718    | 1,007,184  |
| `cache=directsync` (TPM) | 1,464,588  | 1,494,152  | 1,486,263  | 1,432,949  | 1,464,549  |
| **Improvement**          | **+43.1%** | **+43.4%** | **+48.0%** | **+43.3%** | **+45.4%** |


The improvement comes from how MSSQL interacts with the guest-visible cache mode. With `cache=none`, the guest sees a writeback cache and QEMU issues `fdatasync()` on every write (FUA emulation). With `cache=directsync`, the guest sees a writethrough cache; MSSQL can rely on the writethrough guarantee and reduce the frequency of explicit flush commands, significantly lowering the `fdatasync()` overhead on the host I/O path.

## Goals

- Add `directsync` and `unsafe` as valid values for `DriverCache`.
- Validate that `directsync` is only used on storage that supports direct I/O (same check as `none`).
- Allow `directsync` as a valid cache mode for shareable disks.
- Ensure the new cache modes are preserved across live migration. Block live migration for `unsafe` (see [Live migration](#live-migration)).
- Validate that all cache modes, including the new ones, function correctly through functional tests covering migration and data integrity.

## Non Goals

- Changing the default cache mode (remains `none` for block devices, auto-detected for file-based).
- Modifying the KubeVirt CR-level `DiskVerification` or cluster-wide cache override mechanism.

## Definition of Users

- **VM administrators** who need to tune disk I/O for specific guest workloads (databases, CI, batch).
- **Platform operators** who manage shared storage and need `O_DIRECT`-safe cache modes beyond `none`.

## User Stories

- As a VM administrator running MSSQL inside a VM on NVMe-backed PVCs, I want to set `cache: directsync` so that writes bypass the host page cache (maintaining data coherency for shared disks) while avoiding redundant `fdatasync()` calls on every write, because my storage already has fast sync.
- As a VM administrator running ephemeral CI workloads, I want to set `cache: unsafe` so that disk I/O is as fast as possible, because I can tolerate data loss if the host crashes.
- As a platform operator, I want to use `directsync` on shared/multi-attach block volumes so that multiple VMs can safely access the same disk with data coherency, without being limited to `cache: none`.

## Repos

- kubevirt/kubevirt

## Design

### New API constants

Two new `DriverCache` values are added:

```go
const (
    CacheNone         DriverCache = "none"
    CacheWriteThrough DriverCache = "writethrough"
    CacheWriteBack    DriverCache = "writeback"
    CacheDirectSync   DriverCache = "directsync"   // new
    CacheUnsafe       DriverCache = "unsafe"       // new
)
```

### Cache mode behavior

The five modes differ in three dimensions: host page cache usage, guest-visible cache reporting, and per-write sync behavior. These are standard hypervisor cache semantics (currently implemented via QEMU/libvirt):


| Mode           | Host page cache | Guest sees writeback | Sync per write             | Direct I/O |
| -------------- | --------------- | -------------------- | -------------------------- | ---------- |
| `none`         | No              | Yes                  | Yes (FUA emulation)        | Yes        |
| `writethrough` | Yes             | No                   | Yes (FUA emulation)        | No         |
| `writeback`    | Yes             | Yes                  | No (only on guest flush)   | No         |
| `directsync`   | No              | No                   | Yes (FUA emulation)        | Yes        |
| `unsafe`       | Yes             | Yes                  | No (guest flushes ignored) | No         |


**Key distinctions:**

- `none` vs `directsync`: Both bypass the host page cache via direct I/O. The difference is that `none` reports writeback cache to the guest, causing the hypervisor to emit `fdatasync()` after every write via FUA emulation. `directsync` reports writethrough to the guest, so the guest application controls when explicit flushes are issued. Both modes call `fdatasync()` per write, but `directsync` allows workloads that manage their own flush behavior to reduce redundant sync overhead.
- `writeback` vs `unsafe`: Both use the host page cache and report writeback to the guest. The difference is that `unsafe` ignores guest-initiated flush commands, never calling `fdatasync()`. This means maximum throughput but data may not be in sync on host crash, even if the guest explicitly flushed.
- **Shareable disk safety**: Direct I/O modes (`none`, `directsync`) bypass the host page cache, preventing stale reads when multiple VMs access the same block device. Modes that use the host cache (`writeback`, `writethrough`, `unsafe`) are unsafe for shared access because each host may cache different views of the data.

### Validation changes

**Direct I/O validation:**

- When `directsync` is requested, the same direct I/O support check used for `none` is applied. If the backing storage does not support direct I/O, the mode falls back to `writethrough` (matching `none`'s existing fallback to `writeback`).
- `unsafe` requires no special backing store checks (it uses the host page cache).

**Shareable disk validation:**

- The existing restriction that only allows `cache=none` for shareable disks is relaxed to also accept `directsync`, since it also uses direct I/O and is safe for shared access.

**Admission webhook:**

- The webhook validates that the provided cache string is one of the five known values.

### Live migration

**`directsync`:** Fully compatible with live migration. Libvirt has treated `directsync` as migration-safe for shared storage since [2017](https://github.com/libvirt/libvirt/commit/fed9cc8). It uses direct I/O like `none`, so no host page cache state needs to be flushed or transferred. The cache mode is preserved on the destination.

**`unsafe`:** Live migration is **blocked** for VMIs that have any disk with `cache=unsafe`. The hypervisor does not flush dirty host page cache data to stable storage during migration ([confirmed by QEMU maintainer](https://github.com/kubevirt/kubevirt/pull/17718#issuecomment-5132933426)), which means data written by the guest but not yet flushed to the backing store may be lost when the source VM is destroyed after migration completes. KubeVirt sets a `LiveMigratable=False` condition with reason `CacheUnsafeNotMigratable` to prevent this. Users who need migration with relaxed durability can use `writeback`, which does flush the host page cache during migration.

This concern was raised during implementation review (see [discussion](https://github.com/kubevirt/kubevirt/pull/17718#issuecomment-5132150153)) and the `unsafe` cache mode has been split into a [separate implementation PR](https://github.com/kubevirt/kubevirt/pull/18664) with the migration blocker.

## Alternatives

1. **Expose individual cache sub-properties**: The hypervisor internally represents cache modes as a combination of boolean flags (direct I/O, writeback reporting, flush suppression). Exposing these individually would give maximum flexibility but adds API complexity and requires users to understand hypervisor internals. The five named modes cover all practical combinations.
2. **Use I/O mode as a proxy for direct I/O**: The I/O mode (`native`, `threads`) is orthogonal to cache mode. `native` requires direct I/O but doesn't control the caching behavior. These are separate concerns and should remain separate API fields.
3. **Do nothing**: Users can already work around missing modes by patching the hypervisor domain configuration via hooks or plugins, but this is fragile, not validated, and not portable across migrations.

## Scalability

No scalability impact. Cache mode is a per-disk property resolved at VMI creation time. No new controllers, watchers, or API calls are introduced.

## Update Compatibility

- **Forward compatible**: Existing VMIs with `none`, `writethrough`, or `writeback` are unaffected. New VMIs specifying `directsync` or `unsafe` are only valid on clusters running the version that introduces these values.
- **API versioning**: The new constants are additive to the `DriverCache` type. No existing constants are modified or removed.

## Functional Testing Approach

- **Migration with cache modes**: Functional tests verify that VMIs using the new cache modes (`directsync`, `unsafe`) successfully complete live migration (where permitted) and that data written before migration is readable after migration.
- **Shared block disk migration**: Functional tests verify migration of PVC-backed shared block disks with `directsync`, confirming data integrity across migration.
- **Migration blocking for `unsafe`**: Unit tests verify that the live migration condition is set to `False` with reason `CacheUnsafeNotMigratable` when any disk uses `cache=unsafe`.
- **Validation**: Unit tests cover admission validation for all five cache modes, including shareable disk restrictions and direct I/O fallback behavior.

## Implementation History

- 2026-06-04: Initial PR adding `directsync` and `unsafe` to API types, validation, and test coverage ([kubevirt/kubevirt#17718](https://github.com/kubevirt/kubevirt/pull/17718)).
- 2026-06-04: VEP created ([kubevirt/enhancements#327](https://github.com/kubevirt/enhancements/pull/327)).
- 2026-08-06: `unsafe` cache mode split into separate implementation PR with live migration blocker ([kubevirt/kubevirt#18664](https://github.com/kubevirt/kubevirt/pull/18664)).

## Graduation Requirements

### GA (v1.10)

- `directsync` and `unsafe` accepted as valid `DriverCache` values.
- Direct I/O validation for `directsync` matches `none`.
- `directsync` allowed for shareable disks.
- Live migration blocked for VMIs with `cache=unsafe` disks.
- Unit tests for all validation paths.
- Functional tests for live migration with the new cache modes.
- Documentation updated in kubevirt.io user guide.
- `virtctl` accepts the new modes in cache-related flags (e.g. hotplug volume).

