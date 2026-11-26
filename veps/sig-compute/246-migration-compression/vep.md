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

Zstd on multifd channels trades host CPU for less data on the wire. Benefit
and cost depend strongly on guest-memory compressibility — see
[Performance characteristics](#performance-characteristics). Explicit opt-in
is required because the tradeoff is not universally beneficial.

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
multifd channel. CPU-limited / Guaranteed VMs disable multifd which is not
compatible with zstd, so that combination is unsupported.

### Interaction with existing features

| Feature | Interaction |
|---|---|
| Multifd (parallel) | **Required** — zstd runs per channel; without multifd the combination is unsupported |
| Guaranteed / CPU limit | **Incompatible** — these disable multifd |
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

### Why `spec.experimental` in Alpha, then `spec.advanced` in Beta

In Alpha the explicit `compression: zstd|none` knob lived under
`spec.experimental` ([VEP 293](https://github.com/kubevirt/enhancements/pull/295)).
That matched the transitional API: algorithm selection was intentional, but the
shape and placement were still expected to evolve (including a possible path to
automatic enablement).

For Beta the same opt-in moves to `spec.advanced`. Compression is not
universally beneficial and adds hard-to-predict CPU overhead, so operators
must decide where it pays off; `spec.advanced` is the stable home for that
choice without implying automatic enablement or cluster-wide defaults.

Automatic enablement (dirty rate / bandwidth / convergence) remains deferred to
GA — pre-migration dirty rates are not reliable predictors. If that lands later,
the advanced knob can be deprecated or removed after a deprecation cycle.

### Consideration for future improvements

Concerns about high CPU usage can be reduced if QEMU can support dynamically changing
compression setting during a migration. New algorithms like QATzip (already present in QEMU)
or lz4 can reduce the CPU usage further, altering the cost equation between the effective
transfer speed vs additional CPU usage.

## Performance characteristics

Beta soak on bare metal (Xeon Silver 4210R; Burstable VMs, **16 GiB / 4 vCPU**
for Worst/Typical/Best and **5 GiB / 4 vCPU** for Typical 2; 1 GbE class
link). `none` vs `zstd`.

**Method.** Each scenario fills guest memory with a controlled pattern, then
live-migrates under steady dirtying where noted. Metrics from final migration
info (`CompressionRatio`, wire `DataProcessed`, duration) and host QEMU
migration-thread CPU on source+destination. **Overhead is total cost**
(mean src+dst mig CPU% × duration → core·s), not peak busy% alone — short
high-ratio runs look “hot” in instantaneous % but finish quickly.
Guaranteed / CPU-limited VMs are out of scope (multifd disabled; zstd needs
parallel channels). All-zero memory(same byte) is already transferred
efficiently by QEMU ConstantPages feature.

**Scenarios and samples.** Synthetic dirty uses paced redirty (~30 MB/s) so
guest vCPU stays low; app scenarios dirty via their own clients.

- **Worst (`rand`)** — high-entropy random fill + random-byte dirty; zstd
  should barely help. Ran 6 migrations with zstd and 6 without; both sides
  always finished. These are the only long (~3 min) runs used to compare host
  CPU cost when compression does not shrink the transfer.
- **Typical (`memtier`)** — Valkey loaded with `memtier_benchmark`: sequential
  preload (`--data-size 1000 -R --ratio 1:0 --key-pattern P:P -c 1 -t 4
  --key-minimum 1 --key-maximum 10000000`, ~10 M keys / ~10 Gi), then steady
  dirty (`--data-size 1000 -R --ratio 1:10 --key-pattern R:R -c 2 -t 2`, same
  key range). 6 zstd migrations succeeded; paired `none` runs aborted (need a
  higher `maxDowntimeMs` to converge without compression — cost/wire counted
  until abort).
- **Typical 2 (`memtier`)** — same Gaussian hot-spot memtier family as
  https://github.com/kubevirt/kubevirt/pull/17544 on a smaller guest
  (**5 GiB / 4 vCPU**); Valkey never fills `maxmemory`. Steady dirty:
  `--ratio 5:1 --key-pattern G:G --key-minimum 1 --key-maximum 2000000`
  (`--key-stddev 3000`), `--data-size-range 256-16384 -R`, `-t 12 -c 50
  --pipeline=10`. Ran with **elevated `maxDowntimeMs` (10000)** so both `none`
  and `zstd` converge. 3x 20/20 attempts, all succeeded.
- **Best (`pat` / Redis ABCD)** — highly compressible but *not* ConstantPages:
  synthetic per-page unique header + repeating `ABCD…` body, and Valkey with
  **4 KiB** repeating values. Short burst CPU windows only.

| Scenario | Workload | Ratio | Wire (`DataProcessed`) | CPU overhead (total cost) | Duration |
|---|---|---|---|---|---|
| **Worst** | High-entropy random fill/dirty (`rand`) | <span style="white-space:nowrap">**1.03×**</span> | Similar ~17–20 Gi (zstd 17.2–18.8 vs none 18.0–20.2) | zstd **~2×** none (26.6–30.0 vs 14.2–16.0 core·s) | zstd 162–176 s vs none 170–190 s |
| **Typical** | Cache-like Valkey + memtier (1 KiB random values, steady SET:GET) | <span style="white-space:nowrap">**~8–10×**</span> | zstd **~1.2–1.6 Gi** vs none **~28–37 Gi** (none aborted) | zstd **≪** none-until-abort (~3.5–7.5 vs ~24–33 core·s) despite higher busy% | zstd **~12–16 s**; none aborted after ~4.5–6 min |
| **Typical 2** | Hot-spot Valkey + memtier (Gaussian keys; `maxDowntimeMs=10000`) | <span style="white-space:nowrap">12.3 / **15.0** / 19.8×</span> (min/mean/max, n=60) | zstd 1.60 / **2.07** / 2.20 Gi vs none 4.25 / **6.01** / 7.62 Gi (min/mean/max) | zstd higher total cost: 68.5 / **77.7** / 90.0 vs none 4.3 / **6.0** / 7.6 core·s (min/mean/max) | zstd 16.4 / **20.9** / 22.1 s vs none 41.8 / **58.7** / 74.4 s; downtime zstd 230 / **274** / 456 ms vs none 4200 / **4435** / 4996 ms (min/mean/max) |
| **Best** | Highly compressible pattern (synthetic per-page `ABCD…`, or Redis/Valkey 4 KiB repeating values) | <span style="white-space:nowrap">**~33–52×**</span> | zstd **~0.3 Gi** vs none **~14–18 Gi** | zstd **≪** none (~1–2.5 vs ~10–14 core·s); multi-core spikes for ~4–5 s only | zstd **~4–5 s** vs none **~130–170 s** |

**Windows Server 2019 (16 GiB).** Same bare-metal setup as above. Fitting somewhere
between the worst/best cases.  Under idle or light interactive load (browser / user apps)
the compression ratios are about **~2–3×**, cutting migrate time roughly in half.
With a stress-ng–like compressible dirty pattern in ~70% of memory the ratio rises to
about **~6×** (e.g. ~144 s → ~25 s, ~15 Gi → ~2.5 Gi on the wire).

**Summary.** When guest memory compresses well, zstd turns a bandwidth-bound
migration into a shorter transfer due to less data on the wire, much lower time to
converge, and sometimes *lower* total host CPU spent on the migration despite
higher instantaneous load — provided spare CPU capacity is available for the
multifd/zstd threads. When memory does not compress, you mostly burn extra CPU
for little benefit. Operators should enable it where the working set, link,
and headroom make the tradeoff worthwhile, not as a cluster-wide default.

**Note on memory overhead.** Each migration thread consumes additional memory for compression
and decompression, however they are fairly small. Measurements showed additional
overhead of ~20 MiB for each side. That roughly matches the source code analysis.
Note the compression size is offset by shorter time to migrate - while the migration is
ongoing both sides consume additional memory.

## Alternatives

1. **Boolean toggle**: Simpler, but no room for additional algorithms.
2. **Cluster-wide default in KubeVirt CR**: Risks regressions for
   CPU-constrained workloads. Can be reconsidered later.
3. **Full configuration** (level, threads): Too much API surface for Alpha/Beta.
4. **Always enable**: CPU overhead makes this unsuitable as a default.
5. **Automatic-only**: Dirty-rate predictions not reliable enough yet;
   additional CPU usage is hard to predict.
6. **xbzrle instead of zstd**: xbzrle is cache-based; zstd gives better
   general-purpose ratio and works naturally with multifd.

## Scalability

No concerns. Compression/decompression run in virt-launcher on source and
destination. CPU cost is workload-dependent (see
[Performance characteristics](#performance-characteristics)) and bounded
by migration duration and multifd channel count.

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
  opt-in API through Beta.
- 2026-11-23: Document Beta soak performance characteristics.

## Graduation Requirements

### Alpha

- [x] Experimental migration options framework from
      [VEP 293](https://github.com/kubevirt/enhancements/pull/295)
      (feature gate, `spec.experimental` section, propagation path)
- [x] `MigrationCompression` Alpha feature gate
- [x] `MigrationCompression` enum and `spec.experimental.compression` field
      added to `ExperimentalMigrationOptions`
- [x] `virt-launcher` maps API enum to libvirt compression method and
      sets hypervisor flags
- [x] E2E test

### Beta

- [X] Soak testing confirming acceptable CPU overhead (Burstable / multifd;
      Guaranteed out of scope — see Performance characteristics)
- [x] Investigate automatic enablement (dirty-rate / bandwidth / convergence):
      deferred to GA — keep explicit `compression: zstd|none` opt-in as the
      stable Beta API
- [ ] Compression metrics in Prometheus (today only log `CompressionRatio`)
- [X] User-guide documentation for MigrationPolicy compression
- [X] Early adopter commitment

### GA

- [ ] Automatic enablement proven or explicit opt-in confirmed as long-term API
- [ ] Stable across multiple releases
