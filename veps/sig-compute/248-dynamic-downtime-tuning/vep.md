# VEP 248: Live Migration — Dynamic Downtime Tuning and Iteration-Based Convergence Decision

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: 
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Improve live migration convergence by introducing an iteration-aware
algorithm that gradually increases QEMU's `max_downtime` parameter as
migration iterations progress. The algorithm replaces the current
all-or-nothing approach (run until timeout, then either pause the guest,
trigger post-copy, or abort) with a progressive downtime ramp that helps
QEMU complete the switchover with minimal guest disruption.

Configuration is annotation-based — no new API fields are added — making
the feature entirely optional and zero-API-surface in its alpha form.

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
  — how many full passes over dirty memory have completed. A migration
  at iteration 15 is very close to converging and just needs a small
  downtime increase, but KubeVirt doesn't distinguish it from one
  stuck at iteration 1.

- **`max_downtime` is never tuned.** QEMU starts with a default
  `max_downtime` of 300ms and will only switch over when estimated
  remaining transfer time drops below it. For workloads with moderate
  dirty rates, remaining time hovers slightly above 300ms — never
  quite converging. KubeVirt's only response is the nuclear option:
  pause the guest, trigger post-copy, or abort after timeout.

- **Time-based decisions penalize large VMs.** A 256 GiB VM with a
  healthy dirty rate at iteration 20 gets the same treatment as a
  4 GiB VM stuck at iteration 1.

### Proposed behavior

A new algorithm, inspired by QEMU's own auto-converge CPU throttle
and highly effective migration policies in oVirt project, is inserted
into the existing monitoring loop:

1. **Track iteration count** via `MemIteration` from libvirt job stats.
2. **After a configurable number of iterations**, begin increasing
   `max_downtime` via libvirt's `MigrateSetMaxDowntime` API.
3. **Ramp linearly** — add a fixed step (default +150ms) each time
   the iteration counter advances, up to a configurable ceiling
   (default 1500ms).
4. **If the ceiling is reached and migration still hasn't converged**,
   fall through to the existing time-based logic unchanged.

The algorithm strictly improves the current behavior: it gives QEMU
progressively more room to converge *before* existing timeouts fire,
without ever overriding the existing safety nets.

### Why this works

QEMU's switchover condition is:
`estimated_remaining_time ≤ max_downtime`. By gradually raising
`max_downtime` as iterations progress, we signal to QEMU that we
accept a slightly longer switchover pause in exchange for successful
convergence. This is analogous to how auto-converge gradually
throttles vCPUs — but applied to downtime tolerance, which is less
disruptive to the guest workload.

## Goals

* Improve migration convergence for workloads with moderate dirty
  rates that repeatedly fail to converge within the default
  `max_downtime`.
* Zero new API surface — configuration via VMI annotations only.
* Fit entirely within the existing monitoring loop to minimize code
  changes.
* Preserve all existing timeout, progress, and abort semantics as
  fallback behavior.

## Non Goals

* Replacing or modifying the existing post-copy / pause /
  auto-converge mechanisms.
* Exposing algorithm parameters as formal API fields during alpha.
* Implementing bandwidth or CPU throttling.
* Changing migration behavior for any user who does not opt in.

## Definition of Users

* Cluster administrators running workloads with moderate-to-high
  dirty rates that frequently time out or require post-copy/pause.
* Platform engineers wanting finer convergence control without
  enabling auto-converge (which throttles guest CPU).

## User Stories

* As a cluster admin, I want my large-memory VMs to converge without
  being paused or falling back to post-copy, by letting QEMU accept
  a slightly longer switchover downtime after sufficient iteration
  progress.
* As a platform engineer, I want to experiment with downtime tuning
  on a subset of VMs via annotation without changing cluster-wide
  migration config.

## Repos

kubevirt/kubevirt.

## Design

### Configuration via annotations

The feature is enabled per-VMI via annotation on the VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  annotations:
    kubevirt.io/migration-downtime-tuning: |
      {
        "stepMs": 150,
        "maxDowntimeMs": 1500,
        "iterationsBeforeStart": 3,
        "iterationsPerStep": 2
      }
```

If absent, behavior is unchanged. If present with an empty value or
`{}`, sensible defaults are used:

| Parameter              | Default | Description                                    |
|------------------------|---------|------------------------------------------------|
| `stepMs`               | 150     | Increase `max_downtime` by this much per step  |
| `maxDowntimeMs`        | 1500    | Ceiling — never exceed this value              |
| `iterationsBeforeStart`| 3       | Wait for N iterations before first adjustment  |
| `iterationsPerStep`    | 2       | Adjust after every N new iterations            |

QEMU's default `max_downtime` is **300ms**. The algorithm only
increases from the current value. With defaults, the ramp is:
300ms → 450ms → 600ms → 750ms → ... → 1500ms.
Additionally we can start lower, e.g. at 150ms for the first round to help idle VMs
to achieve even lower downtime than today.

### Algorithm

On each monitoring tick (400ms), the existing `processInflightMigration`
method already reads `MemIteration` from libvirt job stats. The new
logic, inserted before the existing switch cases:

1. If annotation is absent → no-op.
2. If `MemIteration < iterationsBeforeStart` → wait.
3. If iteration has not advanced by `iterationsPerStep` since last
   adjustment → wait.
4. Otherwise → `currentDowntime += stepMs`, capped at `maxDowntimeMs`,
   and call `dom.MigrateSetMaxDowntime(currentDowntime)`.
5. Log each adjustment for observability.

All existing timeout / abort / post-copy / pause logic runs after
this and is completely unmodified.

### Interaction with existing features

| Feature               | Interaction                                                           |
|-----------------------|-----------------------------------------------------------------------|
| completionTimeoutPerGiB | Unmodified. Tuning may help converge before timeout fires.          |
| progressTimeout       | Unmodified. Abort-on-stuck still works.                               |
| AllowPostCopy         | Unmodified. Post-copy triggers after timeout if tuning didn't help.   |
| AllowAutoConverge     | Compatible. Auto-converge throttles CPU; this increases downtime tolerance. They complement each other. |
| VFIO switchover       | Unmodified. Separate code path on timeout.                            |
| MigrationCompression  | Compatible. Compression reduces data; tuning helps convergence timing.|

### Example scenario

50 GiB VM, 200 Mbps dirty rate, 800 Mbps migration bandwidth:

| Iteration | DataRemaining | max_downtime | Action                        |
|-----------|---------------|--------------|-------------------------------|
| 1         | 12,400 MiB    | 300ms        | QEMU default                  |
| 2         | 4,200 MiB     | 300ms        | —                             |
| 3         | 1,800 MiB     | 300ms        | `iterationsBeforeStart` reached |
| 5         | 850 MiB       | 450ms        | +150ms                        |
| 7         | 420 MiB       | 600ms        | +150ms                        |
| 9         | 210 MiB       | 750ms        | +150ms                        |
| 10        | 90 MiB        | —            | QEMU switches over            |

Without tuning, this VM continues iterating until the 150\*50s timeout,
then gets forcibly paused. With tuning, it converges at iteration ~10
with 750ms switchover downtime — sub-second, far better than a
multi-second pause, post-copy, or outright abort.

At the ceiling (1500ms), the guest sees at most a 1.5-second pause
during switchover — comparable to a brief network hiccup and
significantly less disruptive than the current fallback options.

## API Examples

See the annotation example in the [Design](#configuration-via-annotations)
section above.

No new CRD fields, webhooks, or RBAC changes are required.

## Alternatives

### Always enable with defaults

Risky — changing default `max_downtime` behavior could surprise users.
Opt-in via annotation is safer for alpha.

### Rely solely on auto-converge

Auto-converge throttles guest CPU, directly impacting workload
performance. Downtime tuning only affects switchover pause duration,
which is far less disruptive.

### Exponential ramp (e.g., 1.5× multiplier) instead of linear

Overshoots quickly — 300 → 450 → 675 → 1012ms in three steps.
Linear +150ms gives predictable, gradual increases and more
iterations to converge before hitting the ceiling.

### Time-based ramp instead of iteration-based

Less precise. Iterations directly reflect QEMU's convergence
progress. Time-based ramps increase too aggressively for slow
migrations or too timidly for fast ones.

### Formal API fields in MigrationConfiguration / MigrationPolicy

More adoption friction, CRD/webhook changes, larger review surface.
Annotation lets us iterate quickly in alpha; promote to API in beta
if proven.

## Scalability

No scalability impact. Runs within the existing per-migration
monitoring goroutine. Adds one `MigrateSetMaxDowntime` libvirt call
per step (typically 5–8 calls per migration). No new API resources
or watches.

## Update/Rollback Compatibility

* **Update**: No impact. Annotation-driven, opt-in. Existing VMIs
  without the annotation are unaffected.
* **Rollback**: Annotation ignored if code is removed. Migrations
  revert to time-based behavior. No persistent state changes.
* **Mixed-version clusters**: Only the source virt-launcher reads
  the annotation. Target node requires no changes.

## Functional Testing Approach

* **Unit tests**: Verify `MigrateSetMaxDowntime` is called with
  expected values at the correct iterations. Verify no-op when
  annotation is absent. Verify ceiling is respected.
* **E2E test**: Migrate a VMI with the annotation and a moderately
  dirty workload. Verify successful completion and log messages
  showing downtime tuning steps.
* **Negative test**: Migrate without annotation — verify baseline
  behavior is preserved.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha
- [ ] Annotation-based configuration (`kubevirt.io/migration-downtime-tuning`)
- [ ] Implementation in existing monitoring loop
- [ ] Structured logging of each adjustment step
- [ ] Unit tests covering algorithm and annotation parsing
- [ ] User guide documentation (experimental annotation)

### Beta
- [ ] Promote parameters to `MigrationConfiguration` / `MigrationPolicy`
      API fields, gated behind a feature gate
- [ ] Soak testing across workload profiles (idle, moderate, heavy)
- [ ] Prometheus metric for downtime adjustments
- [ ] Annotation still supported as override

### GA
- [ ] Feature gate removed
- [ ] Proven stability across multiple releases
- [ ] Consider enabling a sensible default profile by default
