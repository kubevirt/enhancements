# VEP #246: Live Migration Compression

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: v1.11
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/246)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Enable optional compression for live migration data streams via
`MigrationPolicy`, reducing bandwidth consumption and improving migration
times. The user selects the algorithm explicitly (`zstd`). Automatic
enablement remains a longer-term goal; for Beta the explicit opt-in API
is treated as stable.

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
- Gated behind a dedicated `MigrationCompression` feature gate
- Preserve a stable explicit opt-in API through Beta; evaluate automatic
  enablement for GA

## Non Goals

- Fine-grained tuning (compression level, thread count) in Alpha/Beta
- Automatic heuristic-based decisions in Alpha/Beta (dirty rates during
  migration are 2–3× higher than pre-migration baseline, making
  predictions unreliable)
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
`MigrationPolicy` CRD. The `spec.experimental` section and its
propagation mechanics are defined by
[VEP 293: Experimental Migration Options](https://github.com/kubevirt/enhancements/pull/295).

The compression field is a string enum: `"none"` (disabled) or `"zstd"`.
When omitted (`nil`) or set to `"none"`, compression is disabled. This
explicit opt-in remains the supported Beta API.

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
multifd channel. CPU-limited and dedicated-CPU VMs (typical Guaranteed
QoS) disable multifd to protect the CPU cap; without multifd, zstd is
not usable (parallel migration threads are forbidden). **Skip enabling
compression for those VMs**.

### Interaction with existing features

| Feature | Interaction |
|---|---|
| Multifd (parallel) | Required for compression — zstd runs per channel |
| Guaranteed QoS / CPU limit / dedicated CPU | Skip compression (no multifd) |
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

### Why `spec.experimental` and explicit algorithm selection

The `spec.experimental` section and its lifecycle are defined by
[VEP 293](https://github.com/kubevirt/enhancements/pull/295).

For Beta, the explicit `compression: zstd|none` knob is kept as the stable
API. Automatic enablement based on dirty rate, bandwidth, and convergence
was investigated as a Beta goal and is deferred to GA: pre-migration dirty
rates are not reliable predictors, and removing or relocating the explicit
algorithm would break the Beta stability guarantee. If automatic enablement
proves reliable later, the explicit knob may move to an advanced-only
section or be removed at GA after a deprecation cycle.

### Consideration for future improvements

Concerns about high CPU usage can be reduced if QEMU can support dynamically changing
compression setting during a migration. New algorithms like QATzip (already present in QEMU)
or lz4 can reduce the CPU usage further, altering the cost equation between the effective
transfer speed vs additional CPU usage.

## Alternatives

1. **Boolean toggle**: Simpler, but no room for additional algorithms.
2. **Cluster-wide default in KubeVirt CR**: Risks regressions for
   CPU-constrained workloads. Can be reconsidered later.
3. **Full configuration** (level, threads): Too much API surface for Alpha/Beta.
4. **Always enable**: CPU overhead makes this unsuitable as a default.
5. **Automatic-only**: Dirty-rate predictions not reliable enough yet;
   deferred past Beta to preserve a stable opt-in API.
6. **xbzrle instead of zstd**: xbzrle is cache-based; zstd gives better
   general-purpose ratio and works naturally with multifd.

## Scalability

No concerns. Compression runs in virt-launcher on the source node. CPU
overhead is modest at zstd level 1 and bounded by migration bandwidth.

## Update/Rollback Compatibility

- **Update**: Field defaults to `nil` (disabled). Existing migrations unaffected.
- **Mixed-version**: Source virt-launcher controls compression. Target
  decompresses transparently.

## Functional Testing Approach

1. **Unit**: `generateMigrationFlags()` includes `MIGRATE_COMPRESSED` and
   params include `Compression: "zstd"` when enabled.
2. **E2E**: Migration succeeds with compression; verify non-zero compression
   bytes in job stats.

## Implementation History

- 2026-05-01: VEP accepted — https://github.com/kubevirt/enhancements/pull/247
- 2026-07-02: Alpha implementation merged — https://github.com/kubevirt/kubevirt/pull/17509
- 2026-07-24: VEP updated to reflect dedicated FG — https://github.com/kubevirt/enhancements/pull/408
- 2026-11-18: Beta graduation targeting (v1.11): record decision to keep explicit
  opt-in API through Beta; defer automatic enablement to GA. Soak testing,
  Prometheus compression metrics, user-guide docs, and early-adopter feedback
  remain open Beta workstreams.
- 2026-11-19: Guaranteed / CPU-limited VMs skip compression (no multifd).

## Graduation Requirements

### Alpha

- [x] Experimental migration options framework from
      [VEP 293](https://github.com/kubevirt/enhancements/pull/295)
      (`spec.experimental` section, propagation path)
- [x] `MigrationCompression` enum and `spec.experimental.compression` field
      added to `ExperimentalMigrationOptions`
- [x] `virt-launcher` maps API enum to libvirt compression method and
      sets hypervisor flags
- [x] E2E test

### Beta

- [X] Soak testing confirming acceptable CPU overhead (Burstable only; Guaranteed
      out of scope — no multifd; predictable, random, "typical", dirtying)
- [x] Investigate automatic enablement (dirty-rate / bandwidth / convergence):
      deferred to GA — keep explicit `compression: zstd|none` opt-in as the
      stable Beta API
- [ ] Compression metrics in Prometheus (today only log `CompressionRatio`)
- [X] User-guide documentation for MigrationPolicy compression
- [X] Early adopter commitment

### GA

- [ ] Automatic enablement proven or explicit opt-in confirmed as long-term API
- [ ] Stable across multiple releases
