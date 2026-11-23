# VEP 291: Live Migration — Dynamic Downtime Tuning and Iteration-Based Convergence

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/291)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Improve live migration convergence by gradually increasing QEMU's
`max_downtime` as migration iterations progress, with a progressive ramp up of a `target_downtime` up to the configured `maxDowntimeMs`, replacing the current all-or-nothing approach (run until timeout, then pause / post-copy / abort).

The feature is activated by the presence of `spec.experimental.downtimeTuning`
in a `MigrationPolicy`. It ramps `max_downtime` up to the `maxDowntimeMs`
ceiling (see [VEP 252](https://github.com/kubevirt/enhancements/pull/252)).
For Beta the presence-based opt-in and low-level tunables remain the
stable API; cluster-wide / always-on defaults are deferred to GA.

## Motivation

### Current behavior

KubeVirt's migration monitor makes convergence decisions based purely
on wall-clock time:

1. **`completionTimeoutPerGiB × vmSize`** — if exceeded, trigger
   post-copy / pause / abort.
2. **`progressTimeout`** — if `DataRemaining` hasn't decreased for N
   seconds, abort.

This has several problems:

- **No awareness of iteration progress.** QEMU reports `MemIteration`
  but KubeVirt ignores it. A VM that has completed 15 iterations and
  is close to converging gets the same treatment as one that hasn't
  finished even the initial bulk copy.

- **`max_downtime` is never tuned.** QEMU's default 300ms is too low
  for workloads with moderate dirty rates — estimated remaining time
  can hover just above it, never converging.

- **Time-based decisions penalize large VMs.** Large VMs are granted
  excessive timeouts even though their active memory (working set)
  does not necessarily scale with VM size.

### Proposed behavior

QEMU switches over when `estimated_remaining_time ≤ max_downtime`.
By gradually raising `max_downtime` as iterations progress (inspired
by oVirt's migration policy engine), we give QEMU progressively more
room to converge *before* existing timeouts fire, without overriding
existing safety nets. If the ceiling is reached without convergence,
the existing time-based logic still applies.

## Goals

- Activate via presence of `spec.experimental.downtimeTuning` in
  `MigrationPolicy`, gated behind a dedicated `MigrationDowntimeTuning`
  feature gate
- Use `maxDowntimeMs` (top-level field per
  [VEP 252](https://github.com/kubevirt/enhancements/pull/252)) as the
  ceiling for the ramp
- Preserve all existing timeout, progress, and abort semantics as fallback
- Preserve low-level tunables under `spec.experimental.downtimeTuning`
  as the stable Beta API; evaluate always-on defaults for GA

## Non Goals

- Cluster-wide default in KubeVirt CR in Alpha/Beta
- Replacing post-copy / pause / auto-converge mechanisms
- Bandwidth or CPU throttling
- Changing migration behavior for any user who does not opt in

## Definition of Users

- **Cluster Administrators**: Workloads with moderate-to-high dirty rates
  that frequently time out or require post-copy/pause.
- **Platform Engineers**: Wanting finer convergence control without
  enabling auto-converge (which throttles guest CPU).

## User Stories

- As a cluster admin, I want large-memory VMs to converge with a bounded,
  sub-second switchover pause instead of hitting a timeout and being
  forcibly paused, falling back to post-copy, or being aborted.
- As a platform engineer, I want to enable downtime tuning for a subset
  of VMs via `MigrationPolicy` without affecting the rest of the cluster.

## Repos

kubevirt/kubevirt.

## Design

### API and feature gate

Downtime tuning is activated by the presence of
`spec.experimental.downtimeTuning` in a `MigrationPolicy`, gated
behind its own feature gate, `MigrationDowntimeTuning` (Alpha in v1.10;
Beta target v1.11). The gate remains present through Beta and is
enabled by default once graduated to Beta. Either
`MigrationDowntimeTuning` or `MigrationStallDetection` permits setting
`maxDowntimeMs`.

The algorithm ramps `max_downtime` up to the `maxDowntimeMs` ceiling —
a field in `MigrationPolicySpec` / `MigrationConfiguration`.
This VEP reuses that field; see VEP 252 for its definition and
semantics.

Parameters under `spec.experimental.downtimeTuning`:

| Parameter             | Default | Description                                          |
|-----------------------|---------|------------------------------------------------------|
| `initialMs`           | 150     | Starting `target_downtime` (lower than QEMU's 300ms) |
| `steps`               | 7       | Equal steps from initial to `maxDowntimeMs` ceiling  |
| `startAfterIteration` | 3       | Start cooldown after this iteration                  |
| `cooldownSeconds`     | 10      | Minimum seconds between increases                    |

An empty `downtimeTuning: {}` enables tuning with all defaults. For
Beta these low-level fields remain under `spec.experimental` rather
than being dropped, so operators can override the ramp without a
breaking API change.

The effective downtime is recorded in `kubevirt_vmi_migration_last_downtime_duration_seconds` metric.`

### Implementation and algorithm

`virt-launcher` runs the tuning algorithm in the existing monitoring loop.

On each monitoring pass, `processInflightMigration` reads
`MemIteration` and elapsed time. The tuning logic:

1. If `experimental.downtimeTuning` is absent → no-op.
2. On first pass set the initial `target_downtime` → `dom.MigrateSetMaxDowntime(initialMs)`.
3. If `MemIteration < startAfterIteration` → wait.
4. When threshold reached → start cooldown timer.
5. After `cooldownSeconds` elapse → bump by one step, capped at
   `maxDowntimeMs`, call `dom.MigrateSetMaxDowntime()`, reset cooldown.
6. Log each adjustment for observability.

All existing timeout / abort / post-copy / pause logic is unmodified.

With defaults (`maxDowntimeMs=1050`, `steps=7`, cooldown 10s):

```
time:         0s    ~75s        ~85s   ~95s   ~105s   ~115s   ~125s   ~135s
iter:         1     3           ~10    ~20    ~30     ~40     ~50     ~60
max_downtime: 150   150(start)  300    450    600     750     900     1050
```

The time-based cooldown prevents rapid escalation on fast-iterating
1 Gbps links where iterations advance every ~400ms. At 10s cooldown,
each step spans ~10 GiB of transferred data at 1 Gbps — enough for
multiple full iterations, giving QEMU a fair chance to converge at
each downtime level.

### Interaction with existing features

| Feature               | Interaction                                                           |
|-----------------------|-----------------------------------------------------------------------|
| completionTimeoutPerGiB | Unmodified. Tuning may help converge before timeout fires.          |
| progressTimeout       | Unmodified. Abort-on-stuck still works.                               |
| AllowPostCopy         | Unmodified. Post-copy triggers after timeout if tuning didn't help.   |
| AllowAutoConverge     | Compatible. Auto-converge throttles CPU; this increases downtime tolerance. They complement each other. |
| VFIO switchover       | Unmodified. Separate code path on timeout.                            |
| MigrationCompression   | Compatible. Compression reduces data; tuning helps convergence timing.|

The stall-detection work proposed in
[VEP 252](https://github.com/kubevirt/enhancements/pull/252) attempts a smarter and more comprehensive approach, at the price of higher complexity.

### Example scenario

50 GiB VM, dirty rate ~700 Mbps, migration bandwidth ~900 Mbps,
`maxDowntimeMs = 1050`, cooldown 10s.
Because the dirty rate is close to the available bandwidth, QEMU
makes progress but DataRemaining oscillates around a plateau —
estimated downtime hovers around 500–800ms, always above QEMU's
default `max_downtime`.

| Iteration | Time   | max_downtime | Action                                             |
|-----------|--------|--------------|----------------------------------------------------|
| 1–3       | 0–70s  | 150ms        | Bulk copy; below `startAfterIteration`, no cooldown |
| 4         | ~70s   | 150ms        | Threshold reached → cooldown timer starts           |
| ~30       | ~80s   | 300ms        | Iter advanced AND 10s elapsed → first bump          |
| ~55       | ~90s   | 450ms        | Iter advanced AND 10s elapsed → second bump         |
| ~80       | ~100s  | 600ms        | est. < max → QEMU switches over                    |

**Without tuning**: estimated downtime stays around 500–800ms, always
above QEMU's default 300ms — migration never converges and eventually
hits the completion timeout.

**With tuning**: `max_downtime` catches up to `estimated_downtime`
at ~600ms — sub-second switchover, bounded by the ceiling.

### Adoption note

The feature is available in Openshift Virtualization 5.0 as development preview, with a predefined
configuration for higher maximum downtime.

### Benchmark results

Tested on 1 Gbps network, 8 GiB OCP HCP worker node VM (idle hosted
cluster workload, avg dirty rate ~650 Mbps). Defaults: start after
iteration 3, initial 150ms, 7 steps with 10s cooldown, up to 1050ms.
10 migrations with tuning vs 10 without:

| Metric               | With Tuning | Without Tuning | Delta          |
|----------------------|-------------|----------------|----------------|
| Avg Total Time       | 92.2s       | 112.4s         | −18% (20s)     |
| Avg Convergence Time | 24.7s       | 45.0s          | −45%           |
| Median Convergence   | 24.3s       | 35.0s          | −31%           |
| Avg Downtime         | 611ms       | 451ms          | +160ms (+35%)  |
| Avg Iterations       | 28          | 69             | −59%           |
| Avg Data Transferred | 9,694 MiB   | 11,862 MiB     | −18%           |
| Worst Total Time     | 98s         | 161s           | −39%           |
| Convergence Range    | 19–30s (11s)| 24–93s (69s)   | 6× tighter     |

**Under load** (constant ~900 Mbps dirty rate near bandwidth limit):
converges with tuning at ~1s downtime; fails to converge without.

**Idle VM** (CentOS Stream 10, no workload): tuning adds no overhead
and actually achieves lower downtime (129ms vs 244ms) thanks to the
lower initial `target_downtime` of 150ms vs QEMU's default 300ms.

## Performance characteristics

Beta soak on bare metal (Xeon Silver 4210R; Burstable VM, **5 GiB / 4 vCPU**;
1 GbE class link). Focus: **guest downtime** and convergence under a
steady dirty cache workload, with an elevated `maxDowntimeMs` ceiling
so stop-and-copy can succeed.

**Method.** Same Gaussian hot-spot Valkey + memtier family as
https://github.com/kubevirt/kubevirt/pull/17544 (and compression VEP
Typical 2): `--ratio 5:1 --key-pattern G:G --key-minimum 1
--key-maximum 2000000` (`--key-stddev 3000`), `--data-size-range
256-16384 -R`, `-t 12 -c 50 --pipeline=10`; guest never fills
`maxmemory`. Policy `maxDowntimeMs=10000`. Three identical suites, each
**80/80 Succeeded** (configs: downtime-tuning **dt**, stall-detector
**sd**, and the same with zstd). Metrics from final migration info
(`Downtime`, duration). Table stats pool **n=60** per config
(min / **mean** / max).

Without a high enough ceiling this workload aborts: estimated downtime
stays ~4.5–5 s, so default / low `maxDowntimeMs` (900, even 2500) fails
the stall-detector 1.5× gate; only a ceiling ≳ est. downtime (or a
downtime-tuning ramp whose ceil reaches it) converges.

| Config | Guest downtime (ms) | Duration (s) |
|---|---|---|
| **dt** (tuning, no compression) | 4200 / **4435** / 4996 | 41.8 / **58.7** / 74.4 |
| **sd** (stall detector, no compression) | 4304 / **4525** / 4756 | 66.8 / **86.0** / 136.8 |
| **dt-zstd** | 230 / **274** / 456 | 16.4 / **20.9** / 22.1 |
| **sd-zstd** | 237 / **275** / 509 | 47.5 / **67.2** / 116.2 |

**Summary.** With a ceiling that covers the dirty-driven estimate,
migrations converge with measured guest downtime ~**4.4 s** uncompressed
(matching the ~4.5–5 s estimate) instead of aborting under QEMU’s default
300 ms / a too-low policy max. Compression is orthogonal but dominant for
pause length here: zstd drops downtime to ~**0.27 s** for both controllers.

**Downtime tuning vs stall detection.** Both only succeed when the policy
ceiling is in the right ballpark for the dirty-driven estimate (here
≳ ~5 s uncompressed; too-low `maxDowntimeMs` aborts either path). Under the
same high ceiling, **guest downtime is essentially the same** (dt vs sd
within ~2%; zstd pair ~275 ms mean). On this suite, tuning also finished
sooner in wall-clock time (mean **58.7 s** vs **86.0 s** uncompressed;
**20.9 s** vs **67.2 s** with zstd) — how much sooner(or later) depends on the
ramp (`steps` / cooldown): an intermediate step that meets the estimate
lets QEMU stop-and-copy earlier than stall detection.

They differ in *how* they open the switchover window. **Tuning** ramps
`max_downtime` from a low `initialMs` in cooldown-spaced steps, so QEMU can
stop-and-copy as soon as an intermediate step covers the estimate — often
earlier migration completion, and sometimes a pause below the full ceiling.
The tradeoff is that those steps are **predetermined**: the allowed
downtime jumps in fixed increments, so the switchover level is whatever
step first clears the estimate, not necessarily the smallest pause that
would have worked. **Stall detection** waits until the estimate fits
`maxDowntimeMs` (with its 1.5× gate), then soft-stops in one shot — fewer
moving parts, and the decision tracks the live estimate more directly
(closer to the minimal admissible downtime at that moment), at the cost of
staying in precopy until that threshold is met. On this suite the measured
pauses still landed in the same band.

## API Examples

Enable downtime tuning with all defaults (ramps up to `maxDowntimeMs`):

```yaml
apiVersion: migrations.kubevirt.io/v1alpha1
kind: MigrationPolicy
metadata:
  name: tune-downtime
spec:
  selectors:
    namespaceSelector:
      workload-type: memory-intensive
  maxDowntimeMs: 1050
  experimental:
    downtimeTuning: {}
```

With custom low-level overrides:

```yaml
apiVersion: migrations.kubevirt.io/v1alpha1
kind: MigrationPolicy
metadata:
  name: tune-downtime-advanced
spec:
  selectors:
    namespaceSelector:
      workload-type: memory-intensive
  maxDowntimeMs: 1500
  experimental:
    downtimeTuning:
      initialMs: 200
      steps: 5
      startAfterIteration: 2
      cooldownSeconds: 15
```

## Alternatives

1. **Always enable with defaults**: Desirable long-term, but a
   universally-correct `max_downtime` ceiling doesn't exist. Opt-in
   first; enable by default once benchmarks confirm the defaults.
2. **Rely solely on auto-converge**: Throttles guest CPU — effective for
   CPU-bound dirty workloads but impacts performance. Downtime tuning
   affects only the switchover pause. The two complement each other.
3. **Exponential ramp**: Overshoots quickly (150→225→338→506→759→1139ms
   in five steps). Linear gives predictable, gradual increases.
4. **Pure iteration-based ramp**: Rapid escalation on fast links where
   iterations advance in milliseconds. Time-based cooldown ensures each
   level persists long enough for QEMU to attempt convergence.
5. **Cluster-wide default in KubeVirt CR**: Risks regressions. Can be
   considered for Beta/GA once defaults are proven.

## Scalability

No scalability impact. Runs within the existing per-migration
monitoring goroutine. Adds one `MigrateSetMaxDowntime` libvirt call
per step. No new API resources or watches.

## Update/Rollback Compatibility

- **Update**: Field defaults to `nil` (disabled). Existing migrations
  unaffected.
- **Rollback**: Feature gate removal causes the field to be ignored.
  No persistent state changes.
- **Mixed-version**: Source virt-launcher controls tuning. Target node
  requires no changes.

## Functional Testing Approach

1. **Unit**: Verify `MigrateSetMaxDowntime` called with expected values
   at correct iterations. No-op when `experimental.downtimeTuning` is
   absent. Ceiling respected.
2. **E2E**: Migration with tuning enabled + moderately dirty workload.
   Verify successful completion and log lines showing downtime steps. In constrained environment it won't be possible to reliably reach a fixed downtime estimate, excessive non-default values would need to be used.
3. **Negative**: `spec.experimental.downtimeTuning` rejected when
   `MigrationDowntimeTuning` gate is disabled.

## Implementation History

- 2026-04-30: VEP accepted — https://github.com/kubevirt/enhancements/pull/249
- 2026-07-24: VEP updated to reflect dedicated FG — https://github.com/kubevirt/enhancements/pull/409
- 2026-07-31: Alpha implementation merged — https://github.com/kubevirt/kubevirt/pull/17480
  (`MigrationDowntimeTuning` Alpha feature gate, `spec.experimental.downtimeTuning`, e2e)
- 2026-11-18: User-guide docs merged — https://github.com/kubevirt/user-guide/pull/1026
- 2026-11-18: Beta graduation targeting (v1.11): correct Alpha target to v1.10
- 2026-11-23: Document Beta soak performance characteristics (hot-spot downtime)

## Graduation Requirements

### Alpha

- [x] `maxDowntimeMs` top-level field available as the ceiling (per
      [VEP 252](https://github.com/kubevirt/enhancements/pull/252))
- [x] `MigrationDowntimeTuning` Alpha feature gate
- [x] `DowntimeTuningPolicy` struct under `spec.experimental.downtimeTuning`;
      its presence activates the tuning algorithm
- [x] `virt-launcher` runs tuning algorithm in monitoring loop
- [x] E2E test
- [x] User guide docs

### Beta

- [x] Low-level tunables remain in `spec.experimental` (kept)
- [X] Soak testing across workload profiles (idle, moderate, heavy)
- [X] Prometheus metric for downtime adjustments (final effective downtime tracked)
- [X] Early adopter commitment
- [X] Graduate `MigrationDowntimeTuning` feature gate Alpha → Beta
      (enabled by default; gate remains through Beta)

### GA

- [ ] Feature gate removed
- [ ] Consider enabling by default with sensible `maxDowntimeMs`
- [ ] Stable across multiple releases
- [ ] Evaluate overlap with [VEP 252](https://github.com/kubevirt/enhancements/pull/252)
      stall-detection; if that approach subsumes the fixed ramp, this
      feature may be folded in or replaced
