# VEP #246: Live Migration Compression

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/246)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Enable optional compression for live migration data streams via
`MigrationPolicy`, reducing bandwidth consumption and improving migration
times. In Alpha the user selects the algorithm explicitly (`zstd`). The
long-term goal is automatic enablement, at which point the algorithm
selection moves to an advanced override or is removed entirely.

## Motivation

Large-memory VMs or bandwidth-constrained environments often hit migration
timeouts because the dirty rate exceeds the available bandwidth. QEMU/libvirt
support zstd compression on multifd channels (since libvirt 9.4.0), but
KubeVirt does not expose this.

Zstd on the existing multifd channels provides ~3× compression with modest
CPU overhead. Testing on a low-end 1 Gbps / Xeon Silver 4210R setup with
50 GiB VMs showed:

- **3.6× less data on the wire** (3.3 GiB vs 12 GiB)
- **~0.4 CPU cores** overhead per VM (8 multifd streams)
- Migrations that consistently failed without compression always succeeded

Compression trades CPU for bandwidth. This is valuable when bandwidth is the
bottleneck but not universally beneficial, hence explicit opt-in.

## Goals

- Expose compression in `MigrationPolicy` under `spec.experimental.compression`
- Keep the default disabled
- Gated behind the experimental migration options feature gate defined by
  [VEP 293](https://github.com/kubevirt/enhancements/pull/295)
- Path toward automatic enablement in future versions

## Non Goals

- Fine-grained tuning (compression level, thread count) in Alpha
- User-facing documentation of tunables in Alpha
- Automatic heuristic-based decisions in Alpha (dirty rates during migration
  are 2–3× higher than pre-migration baseline, making predictions unreliable)
- Block (disk) migration compression

## Definition of Users

- **Cluster Administrators**: Environments with constrained migration bandwidth
  or large-memory VMs that frequently time out.
- **Platform Engineers**: Building migration policies for multi-tenant clusters.

## User Stories

- As a platform engineer, I want to enable compression for memory-heavy VMs
  via `MigrationPolicy` so they converge within the timeout window without
  affecting other workloads.
- As a cluster admin, I want migrations to eventually compress automatically
  when bandwidth is the bottleneck.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### API and feature gate

Compression is exposed as a field under `spec.experimental` in the
`MigrationPolicy` CRD. This experimental section,
its feature gate, and the propagation mechanics are defined by
[VEP 293: Experimental Migration Options](https://github.com/kubevirt/enhancements/pull/295).
This VEP only specifies the compression-specific field and its mapping to
the hypervisor.

The compression field is a string enum: `"none"` (disabled) or `"zstd"`.
When omitted (`nil`) or set to `"none"`, compression is disabled.

```go
// +kubebuilder:validation:Enum=none;zstd
type MigrationCompression string

const (
    MigrationCompressionNone MigrationCompression = "none"
    MigrationCompressionZstd MigrationCompression = "zstd"
)
```

### Implementation

`virt-launcher` maps the API enum to the libvirt method name via an
explicit mapping table and sets `VIR_MIGRATE_COMPRESSED` +
`VIR_MIGRATE_PARAM_COMPRESSION`.

### How it maps to libvirt/QEMU

| KubeVirt API | libvirt |
|---|---|
| `experimental.compression: "zstd"` | `VIR_MIGRATE_COMPRESSED` flag + `VIR_MIGRATE_PARAM_COMPRESSION = "zstd"` |

With multifd active (default when no CPU limit is set), zstd runs per
multifd channel. Without multifd, QEMU falls back to single-threaded zstd.

### Interaction with existing features

| Feature | Interaction |
|---|---|
| Multifd (parallel) | Compatible — zstd runs per channel |
| Auto-converge | Compatible — orthogonal |
| Post-copy | Compatible — applies to pre-copy phase |
| TLS | Compatible — compression before encryption |
| Bandwidth limit | Compatible — compressed data counts toward limit |

## API Examples

```yaml
apiVersion: migrations.kubevirt.io/v1alpha1
kind: MigrationPolicy
metadata:
  name: compress-heavy-vms
spec:
  selectors:
    namespaceSelector:
      workload-type: memory-intensive
  experimental:
    compression: zstd
```

## Design Rationale

### Why MigrationPolicy only

Compression is a bandwidth-for-CPU tradeoff that depends on workload
characteristics. `MigrationPolicy` provides the right granularity — admins
target specific namespaces or VM classes without risking cluster-wide
regressions.

### Why `spec.experimental`

The explicit algorithm selection is intended as a transitional API. The
long-term goal is automatic enablement based on dirty rate, bandwidth, and
convergence monitoring. If that proves reliable, compression becomes an
implementation detail and the explicit knob either moves to an
advanced-only section or is removed. The `spec.experimental` section and
its lifecycle are defined by
[VEP 293](https://github.com/kubevirt/enhancements/pull/295).

### Consideration for future improvements

Concerns about high CPU usage can be reduced if QEMU can support dynamically changing
compression setting during a migration. New algorithms like QATzip (already present in QEMU)
or lz4 can reduce the CPU usage further, altering the cost equation between the effective
transfer speed vs additional CPU usage.

## Alternatives

1. **Boolean toggle**: Simpler, but no room for additional algorithms.
2. **Cluster-wide default in KubeVirt CR**: Risks regressions for
   CPU-constrained workloads. Can be reconsidered later.
3. **Full configuration** (level, threads): Too much API surface for Alpha.
4. **Always enable**: CPU overhead makes this unsuitable as a default.
5. **Automatic-only**: Dirty-rate predictions not reliable enough yet.
6. **xbzrle instead of zstd**: xbzrle is cache-based; zstd gives better
   general-purpose ratio and works naturally with multifd.

## Scalability

No concerns. Compression runs in virt-launcher on the source node. CPU
overhead is modest at zstd level 1 and bounded by migration bandwidth.

## Update/Rollback Compatibility

- **Update**: Field defaults to `nil` (disabled). Existing migrations unaffected.
- **Rollback**: Feature gate removal causes the field to be ignored.
  No persistent state changes.
- **Mixed-version**: Source virt-launcher controls compression. Target
  decompresses transparently.

## Functional Testing Approach

1. **Unit**: `generateMigrationFlags()` includes `MIGRATE_COMPRESSED` and
   params include `Compression: "zstd"` when enabled.
2. **E2E**: Migration succeeds with compression; verify non-zero compression
   bytes in job stats.
3. **Negative**: `spec.experimental` ignored when gate is disabled.

## Implementation History

<!-- Updated as implementation progresses -->

## Graduation Requirements

### Alpha

- [ ] Experimental migration options framework from
      [VEP 293](https://github.com/kubevirt/enhancements/pull/295)
      (feature gate, `spec.experimental` section, propagation path)
- [ ] `MigrationCompression` enum and `spec.experimental.compression` field
      added to `ExperimentalMigrationConfiguration`
- [ ] `virt-launcher` maps API enum to libvirt compression method and
      sets hypervisor flags
- [ ] E2E test

### Beta

- [ ] Soak testing confirming acceptable CPU overhead
- [ ] Investigate automatic enablement (dirty-rate / bandwidth / convergence)
- [ ] If viable: implement automatic, move explicit algorithm to
      advanced override or remove
- [ ] Compression metrics in Prometheus

### GA

- [ ] Automatic enablement proven or explicit opt-in confirmed as long-term API
- [ ] Stable across multiple releases
