# VEP #326: Expose `directsync` and `unsafe` disk cache modes

## VEP Status Metadata

### Target releases

- This VEP targets beta for version: v1.10
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- (R) Alpha target version is explicitly mentioned and approved
- (R) Beta target version is explicitly mentioned and approved
- (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt currently exposes three QEMU disk cache modes to users: `none`, `writethrough`, and `writeback`. QEMU supports two additional modes -- `directsync` and `unsafe` -- that address specific performance and data-safety trade-offs not covered by the existing three. This VEP proposes adding `directsync` and `unsafe` as first-class `DriverCache` values in the KubeVirt API, along with the validation, defaulting, and test coverage required to support them.

## Motivation

Users running database workloads (e.g. MSSQL, PostgreSQL) inside VMs often need precise control over the I/O path to balance performance and durability. The existing three modes leave gaps:

- **No "safe + bypass host cache" option without guest flush penalties**: `none` bypasses the host page cache (via `O_DIRECT`) but reports writeback cache to the guest, causing QEMU to issue `fdatasync()` on every guest write. `directsync` also bypasses the host cache but issues `fdatasync()` only on guest-initiated flush commands, which can significantly reduce I/O overhead for flush-rare workloads on storage with fast sync (NVMe, BBU-backed RAID).
- **No "maximum throughput, relaxed durability" option**: Some workloads (CI pipelines, ephemeral VMs, batch processing) tolerate data loss on host crash in exchange for maximum I/O throughput. `unsafe` disables all host-side flush processing and caches aggressively, which is currently not expressible.

Additionally, `directsync` is safe for shared/multi-attach disks (it uses `O_DIRECT`, ensuring data coherency), but KubeVirt currently rejects all shareable disks unless `cache=none`.

### Performance evidence

HammerDB TPC-C benchmarks against Microsoft SQL Server on Windows Server 2025 (32 vCPU, 440 GiB memory, virtio with 8 I/O multiqueues, 1 GiB hugepages, Intel Sapphire Rapids) show a consistent ~43-48% throughput improvement when switching from `cache=none` to `cache=directsynca` using a temporary hook (see https://github.com/kubevirt/kubevirt/pull/17718):


| Virtual users            | 100        | 200        | 300        | 400        | 500        |
| ------------------------ | ---------- | ---------- | ---------- | ---------- | ---------- |
| `cache=none` (TPM)       | 1,023,457  | 1,041,870  | 1,004,201  | 999,718    | 1,007,184  |
| `cache=directsync` (TPM) | 1,464,588  | 1,494,152  | 1,486,263  | 1,432,949  | 1,464,549  |
| **Improvement**          | **+43.1%** | **+43.4%** | **+48.0%** | **+43.3%** | **+45.4%** |


The improvement comes from how MSSQL interacts with the guest-visible cache mode. With `cache=none`, the guest sees a writeback cache and QEMU issues `fdatasync()` on every write (FUA emulation). With `cache=directsync`, the guest sees a writethrough cache; MSSQL can rely on the writethrough guarantee and reduce the frequency of explicit flush commands, significantly lowering the `fdatasync()` overhead on the host I/O path.

## Goals

- Add `directsync` and `unsafe` as valid values for `v1.DriverCache`.
- Validate that `directsync` is only used on storage that supports `O_DIRECT` (same check as `none`).
- Allow `directsync` as a valid cache mode for shareable disks.
- Ensure all five cache modes survive live migration with the correct libvirt XML.
- Provide unit and e2e test coverage for the new modes.

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

Two new `DriverCache` values are added to `staging/src/kubevirt.io/api/core/v1/types.go`:

```go
const (
    CacheNone         DriverCache = "none"
    CacheWriteThrough DriverCache = "writethrough"
    CacheWriteBack    DriverCache = "writeback"
    CacheDirectSync   DriverCache = "directsync"   // new
    CacheUnsafe       DriverCache = "unsafe"       // new
)
```

### QEMU cache mode behavior

The five modes differ in three dimensions: host page cache usage, guest-visible cache reporting, and per-write sync behavior.


| Mode           | Host page cache | Guest sees writeback | `fdatasync()` per write    | `O_DIRECT` |
| -------------- | --------------- | -------------------- | -------------------------- | ---------- |
| `none`         | No              | Yes                  | Yes (FUA emulation)        | Yes        |
| `writethrough` | Yes             | No                   | Yes (FUA emulation)        | No         |
| `writeback`    | Yes             | Yes                  | No (only on guest flush)   | No         |
| `directsync`   | No              | No                   | Yes (FUA emulation)        | Yes        |
| `unsafe`       | Yes             | Yes                  | No (guest flushes ignored) | No         |


**Key distinctions:**

- `none` vs `directsync`: Both use `O_DIRECT`. The difference is that `none` reports writeback cache to the guest (`cache.writeback=on`), causing QEMU to emit `fdatasync()` after every write via FUA emulation. `directsync` reports writethrough to the guest (`cache.writeback=off`), so QEMU only emits `fdatasync()` per write from its own writethrough logic -- but since the guest does not see a writeback cache, it may batch or defer flushes, and QEMU's writethrough path still calls `fdatasync()`. In practice, both call `fdatasync()` per write, but `directsync`'s lack of host cache means slightly different read performance characteristics.
- `writeback` vs `unsafe`: Both use the host page cache and report writeback to the guest. The difference is that `unsafe` ignores guest-initiated flush commands (`BDRV_O_NO_FLUSH`), never calling `fdatasync()`. This means maximum throughput but data loss on host crash even if the guest explicitly flushed.
- **Shareable disk safety**: `O_DIRECT` modes (`none`, `directsync`) bypass the host page cache, preventing stale reads when multiple VMs access the same block device. Modes that use the host cache (`writeback`, `writethrough`, `unsafe`) are unsafe for shared access because each VM's host may cache different views of the data.

### Validation changes

`**SetDriverCacheMode` in `converter.go`:**

- When `directsync` is requested, the same `O_DIRECT` support check used for `none` is applied. If the backing file/device does not support `O_DIRECT`, the mode falls back to `writethrough` (matching `none`'s existing fallback to `writeback`).
- `unsafe` requires no special backing store checks (it uses the host page cache, no `O_DIRECT` needed).

**Shareable disk validation in `Convert_v1_Disk_To_api_Disk`:**

- The existing check `cache != none → error` is relaxed to `cache != none && cache != directsync → error`, since `directsync` also uses `O_DIRECT` and is safe for shared access.

**Admission webhook:**

- The existing `DriverCache` validation already accepts any string value that maps to a valid QEMU cache mode. The webhook validates that the provided cache string is one of the five known values.

### Live migration

All five cache modes are compatible with QEMU live migration. The cache mode is a property of the disk driver, not of the migration state, and is preserved in the libvirt domain XML on the target host. No special migration handling is needed.
However they deserve to be tested, there has been issues in the past.

## Alternatives

1. **Expose `cache.direct` and `cache.no-flush` sub-properties individually**: QEMU represents cache modes as a combination of boolean flags (`cache.direct`, `cache.writeback`, `cache.no-flush`). Exposing these individually would give maximum flexibility but adds API complexity and requires users to understand QEMU internals. The five named modes cover all practical combinations.
2. **Use `io: native` as a proxy for `O_DIRECT`**: The `io` mode (`native`, `threads`) is orthogonal to cache mode. `io: native` requires `O_DIRECT` but doesn't control the caching behavior. These are separate concerns and should remain separate API fields.
3. **Do nothing**: Users can already work around missing modes by patching libvirt domain XML via hooks, but this is fragile, not validated, and not portable across migrations.

## Scalability

No scalability impact. Cache mode is a per-disk property resolved at VMI creation time. No new controllers, watchers, or API calls are introduced.

## Update/Rollback Compatibility

- **Forward compatible**: Existing VMIs with `none`, `writethrough`, or `writeback` are unaffected. New VMIs specifying `directsync` or `unsafe` are only valid on clusters running the version that introduces these values.
- **Rollback**: If a cluster is rolled back to a version that does not recognize `directsync` or `unsafe`, existing VMIs with these modes will fail validation on restart/migration. Running VMIs are unaffected until they are stopped. Administrators should update VMI specs before rollback.
- **API versioning**: The new constants are additive to the `DriverCache` type. No existing constants are modified or removed.

## Functional Testing Approach

### E2e tests

- **Migration with cache modes**: A `DescribeTable` tests that VMIs with `writeback`, `writethrough`, `directsync`, and `unsafe` cache modes successfully complete live migration and preserve the correct cache mode in the post-migration libvirt domain XML.
- **Shared block disk migration**: A `DescribeTable` tests migration of PVC-backed shared block disks with `none` and `directsync`, verifying cache mode preservation.

## Implementation History

- 2025-06-04: Initial PR adding `directsync` and `unsafe` to API types, validation, and test coverage (kubevirt/kubevirt#17718).
- 2025-06-04: VEP created.

## Graduation Requirements

### Alpha

- `directsync` and `unsafe` accepted as valid `DriverCache` values.
- `O_DIRECT` validation for `directsync` matches `none`.
- `directsync` allowed for shareable disks.
- Unit tests for all validation paths.
- E2e tests for migration with all five cache modes.

### Beta

- No regressions reported from alpha usage.
- Documentation updated in kubevirt.io user guide.
- Confirmation that `virtctl` exposes the new modes in any cache-related flags.

### GA

- Stable across at least two releases.
- No outstanding bugs related to the new cache modes.

