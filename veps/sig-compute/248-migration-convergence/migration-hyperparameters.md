# Migration stall detection: hyper-parameters and defaults

This document argues for **reasonable starting defaults** for the knobs used by [VEP 248: Live Migration Stability & Convergence](./migration-convergence.md) (iteration-aligned stall detection, local-minimum switch-over, and related behavior). Values here are intended for the **alpha** phase; beta should revisit tuning using cluster telemetry as described in the VEP.

---

## Quick reference

| Knob | Existing | Where configured | Proposed default | Role (short) |
|------|----------|------------------|------------------|--------------|
| `progressTimeout` | Yes | `migrationConfiguration` | **60s** | Wall-clock window for “progress” and stall; bounds patience for relaxation |
| `completionTimeoutPerGiB` | Yes | `migrationConfiguration` | **150 s/GiB** (unchanged) | Hard migration budget; stall detection usually ends jobs earlier |
| `maxDowntimeMs` | No | `migrationConfiguration` | **900 ms** | After stall, max acceptable final pause for pre-copy-only paths without post-copy / workload disruption |
| `stallMargin` | No | `experimental.stallDetection` | **0.04** (4%) | Relative band around best remaining bytes for stall and switch-over |
| `ewmaAlpha` | No | `experimental.stallDetection` | **0.4** | EWMA weight on each bandwidth sample vs history |
| `precopyPossibleFactor` | No | `experimental.stallDetection` | **1.5** | Pre-copy-only: abort if implied downtime ≥ `maxDowntimeMs ×` this factor |
| `patienceWindowDecayFactor` | No | `experimental.stallDetection` | **0.5** | Multiplier for relaxation patience after each missed window |
| `searchLocalMinima` | No | `experimental.stallDetection` | **true** | If **false**, switch-over as soon as stall is detected (no local-minimum wait) |
| `observeOnly` | No | `experimental.stallDetection` | **false** | Run stall detector in "shadow mode" where decisions are logged but not acted on |
| `elevatedLogging` | No | `experimental.stallDetection` | **true** | Leave an enhanced "trail" in both pod logs and Prometheus |

---

## Justifications for Default Values

### `completionTimeoutPerGiB`

Empirically, the default value of `completionTimeoutPerGiB` (150s) is often seen as too large. Analytically analyzing completion times, we found that with a `completionTimeoutPerGiB` of 150s, pre-copy only can converge as long as dirty rates are at least ~55 mbps lower. While likely still conservative, with the implementation of this VEP, we recommend leaving it as is since stall detection will abort most migrations far earlier.

See https://docs.google.com/spreadsheets/d/1V-FcX6gO4mNx5fBTgnBq-UnkXAn2y4pTjuYtgDZ1cCc/edit?gid=0#gid=0 (Migration Time Calculator) to see how various parameters impact migration time. Controlling precise dirty rates and bandwidth experimentally is difficult, so this analysis and evaluation of a "good" value of max completion time is based on analytical reasoning and a mathematical model of pre-copy as a geometric series.

### `progressTimeout`

The `progressTimeout` window also manages a trade-off between optimizing for a better downtime and total migration time. Large `progressTimeout` windows increase the chances of finding a better downtime at the cost of increasing the total migration time.

A sane default for this value has the following requirements:
(1) large enough that a temporary increase in remaining bytes (caused by bandwidth fluctuations or program workload phase change) does not **false-trigger** stall; (2) short enough not to stall migration unnecessarily; (3) grounded in typical operator tolerance.

We suggest a default value of **60s** for **`progressTimeout`**. This value is large enough that dips in "remaining bytes" caused by short, temporary fluctuations in bandwidth and dirty rate are ignored, avoiding false triggers of the stall detector.

UX research often cites **~10s** before users treat a UI as hung; Kubernetes’ default probe period is **10s**; many app timeouts cluster near **30s** (SSH, browsers); Windows gives up a TCP handshake after **~21s** without reply (Linux is more lenient). We infer operators care most about keeping **final** downtime **well under ~15s**; beyond that, marginal gains from finer tuning usually do not justify much longer stall-detection delay.
At **60s** we still expect $\geq 4$ sample iterations. Assuming each sample iteration draws remaining bytes from a stationary(ish) distribution, $P(\text{at least one} \leq \text{median in 4 tries})$ = $1 − 0.5^4 = 0.9375$, which provides acceptable odds of acting near a favorable remaining-byte level.


### `maxDowntimeMs`

In practice, many operators treat **about one second** of guest-visible pause as the upper end of what still feels like a “live” migration: longer pauses start to look like a short outage rather than a seamless handoff.

QEMU’s **target downtime** and the **downtime implied** from `remainingBytes` and bandwidth are both dominated by how long it should take to **push the last bytes over the network**. They do not fully reflect everything that happens during the final stop-and-copy window: synchronizing state, tearing down the source device model, resuming on the destination, and other fixed costs. Measured wall-clock pause therefore often **lands above** what a back-of-the-envelope network-only estimate suggests.

**900 ms** is chosen as a **conservative** default where the actual downtime still should typically land below ~1s thus satisfying the “still live” bar while leaving headroom for overhead. Clusters that can tolerate more disruption should raise `maxDowntimeMs` explicitly (subject to CR validation and QEMU limits; see the parent VEP).

### `stallMargin`

The value of the stall margin percentage balances a trade-off between migration time and downtime. A small margin is stricter about finding a near-optimal switch-over point resulting in slightly better downtimes at the cost of total migration time. We propose to use a "Stall Margin" of **4%** as we believe this value is a good balance of trade-offs.

For reference, the tables below show how a different margin percentage translates to remaining bytes and downtime thresholds. We used a 500Mbps network as the reference network because we assume most end users will have at least 1Gbps. Then, since by default, we allow two concurrent migrations, choosing 500Mbps as the reference for available bandwidth is reasonable.

**2%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.02 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 31.9 MB  | 510 ms     | 10 ms    |
| 62.5 MB  | 1,000 ms   | 63.8 MB  | 1,020 ms   | 20 ms    |
| 125 MB   | 2,000 ms   | 127.5 MB | 2,040 ms   | 40 ms    |
| 250 MB   | 4,000 ms   | 255 MB   | 4,080 ms   | 80 ms    |
| 625 MB   | 10,000 ms  | 637.5 MB | 10,200 ms  | 200 ms   |
| 1.88 GB  | 30,000 ms  | 1.92 GB  | 30,600 ms  | 600 ms   |
| 3.75 GB  | 60,000 ms  | 3.83 GB  | 61,200 ms  | 1,200 ms |

**3%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.03 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 32.2 MB  | 515 ms     | 15 ms    |
| 62.5 MB  | 1,000 ms   | 64.4 MB  | 1,030 ms   | 30 ms    |
| 125 MB   | 2,000 ms   | 128.8 MB | 2,060 ms   | 60 ms    |
| 250 MB   | 4,000 ms   | 257.5 MB | 4,120 ms   | 120 ms   |
| 625 MB   | 10,000 ms  | 643.8 MB | 10,300 ms  | 300 ms   |
| 1.88 GB  | 30,000 ms  | 1.93 GB  | 30,900 ms  | 900 ms   |
| 3.75 GB  | 60,000 ms  | 3.86 GB  | 61,800 ms  | 1,800 ms |

**4%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.04 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 32.5 MB  | 520 ms     | 20 ms    |
| 62.5 MB  | 1,000 ms   | 65 MB    | 1,040 ms   | 40 ms    |
| 125 MB   | 2,000 ms   | 130 MB   | 2,080 ms   | 80 ms    |
| 250 MB   | 4,000 ms   | 260 MB   | 4,160 ms   | 160 ms   |
| 625 MB   | 10,000 ms  | 650 MB   | 10,400 ms  | 400 ms   |
| 1.88 GB  | 30,000 ms  | 1.95 GB  | 31,200 ms  | 1,200 ms |
| 3.75 GB  | 60,000 ms  | 3.9 GB   | 62,400 ms  | 2,400 ms |

**5%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.05 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 32.8 MB  | 525 ms     | 25 ms    |
| 62.5 MB  | 1,000 ms   | 65.6 MB  | 1,050 ms   | 50 ms    |
| 125 MB   | 2,000 ms   | 131.3 MB | 2,100 ms   | 100 ms   |
| 250 MB   | 4,000 ms   | 262.5 MB | 4,200 ms   | 200 ms   |
| 625 MB   | 10,000 ms  | 656.3 MB | 10,500 ms  | 500 ms   |
| 1.88 GB  | 30,000 ms  | 1.97 GB  | 31,500 ms  | 1,500 ms |
| 3.75 GB  | 60,000 ms  | 3.94 GB  | 63,000 ms  | 3,000 ms |

**6%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.06 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 33.1 MB  | 530 ms     | 30 ms    |
| 62.5 MB  | 1,000 ms   | 66.3 MB  | 1,060 ms   | 60 ms    |
| 125 MB   | 2,000 ms   | 132.5 MB | 2,120 ms   | 120 ms   |
| 250 MB   | 4,000 ms   | 265 MB   | 4,240 ms   | 240 ms   |
| 625 MB   | 10,000 ms  | 662.5 MB | 10,600 ms  | 600 ms   |
| 1.88 GB  | 30,000 ms  | 1.99 GB  | 31,800 ms  | 1,800 ms |
| 3.75 GB  | 60,000 ms  | 3.98 GB  | 63,600 ms  | 3,600 ms |

**8%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.08 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 33.8 MB  | 540 ms     | 40 ms    |
| 62.5 MB  | 1,000 ms   | 67.5 MB  | 1,080 ms   | 80 ms    |
| 125 MB   | 2,000 ms   | 135 MB   | 2,160 ms   | 160 ms   |
| 250 MB   | 4,000 ms   | 270 MB   | 4,320 ms   | 320 ms   |
| 625 MB   | 10,000 ms  | 675 MB   | 10,800 ms  | 800 ms   |
| 1.88 GB  | 30,000 ms  | 2.03 GB  | 32,400 ms  | 2,400 ms |
| 3.75 GB  | 60,000 ms  | 4.05 GB  | 64,800 ms  | 4,800 ms |

**10%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.10 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 34.4 MB  | 550 ms     | 50 ms    |
| 62.5 MB  | 1,000 ms   | 68.8 MB  | 1,100 ms   | 100 ms   |
| 125 MB   | 2,000 ms   | 137.5 MB | 2,200 ms   | 200 ms   |
| 250 MB   | 4,000 ms   | 275 MB   | 4,400 ms   | 400 ms   |
| 625 MB   | 10,000 ms  | 687.5 MB | 11,000 ms  | 1,000 ms |
| 1.88 GB  | 30,000 ms  | 2.07 GB  | 33,000 ms  | 3,000 ms |
| 3.75 GB  | 60,000 ms  | 4.13 GB  | 66,000 ms  | 6,000 ms |

**12%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.12 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 35 MB    | 560 ms     | 60 ms    |
| 62.5 MB  | 1,000 ms   | 70 MB    | 1,120 ms   | 120 ms   |
| 125 MB   | 2,000 ms   | 140 MB   | 2,240 ms   | 240 ms   |
| 250 MB   | 4,000 ms   | 280 MB   | 4,480 ms   | 480 ms   |
| 625 MB   | 10,000 ms  | 700 MB   | 11,200 ms  | 1,200 ms |
| 1.88 GB  | 30,000 ms  | 2.11 GB  | 33,600 ms  | 3,600 ms |
| 3.75 GB  | 60,000 ms  | 4.2 GB   | 67,200 ms  | 7,200 ms |

**14%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.14 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 35.6 MB  | 570 ms     | 70 ms    |
| 62.5 MB  | 1,000 ms   | 71.3 MB  | 1,140 ms   | 140 ms   |
| 125 MB   | 2,000 ms   | 142.5 MB | 2,280 ms   | 280 ms   |
| 250 MB   | 4,000 ms   | 285 MB   | 4,560 ms   | 560 ms   |
| 625 MB   | 10,000 ms  | 712.5 MB | 11,400 ms  | 1,400 ms |
| 1.88 GB  | 30,000 ms  | 2.14 GB  | 34,200 ms  | 4,200 ms |
| 3.75 GB  | 60,000 ms  | 4.28 GB  | 68,400 ms  | 8,400 ms |

**16%:**

| Remaining Bytes | Expected Downtime (500 Mbps) | Remaining Bytes × 1.16 | Expected Downtime (500 Mbps) | Downtime Difference |
|---|---|---|---|---|
| 31.25 MB | 500 ms     | 36.3 MB  | 580 ms     | 80 ms    |
| 62.5 MB  | 1,000 ms   | 72.5 MB  | 1,160 ms   | 160 ms   |
| 125 MB   | 2,000 ms   | 145 MB   | 2,320 ms   | 320 ms   |
| 250 MB   | 4,000 ms   | 290 MB   | 4,640 ms   | 640 ms   |
| 625 MB   | 10,000 ms  | 725 MB   | 11,600 ms  | 1,600 ms |
| 1.88 GB  | 30,000 ms  | 2.18 GB  | 34,800 ms  | 4,800 ms |
| 3.75 GB  | 60,000 ms  | 4.35 GB  | 69,600 ms  | 9,600 ms |

### `ewmaAlpha`

Migration bandwidth as reported by libvirt is effectively a **short snapshot** and jitters iteration to iteration. The monitor smooths it with an exponentially weighted moving average (EWMA): each new sample updates the estimate with weight **`ewmaAlpha`** on the fresh reading and **(1 − `ewmaAlpha`)** on the previous estimate.

The trade-off is standard for EWMAs: a **larger** `ewmaAlpha` **follows new samples faster** (good when bandwidth really changes) but **lets short spikes move the estimate**; a **smaller** `ewmaAlpha` **smooths spikes** but **lags** real sustained speedups or slowdowns. We want something that **reacts quickly enough** to genuine path changes yet **still damps** the brief excursions libvirt’s snapshot tends to produce.

**0.4** felt like a fair compromise in that tension—not so aggressive that single outliers dominate `impliedDowntime`, not so conservative that recovery from a real drop is unusably slow. There is no closed-form proof that 0.4 is optimal; beta telemetry should revisit it alongside real traces.

### `precopyPossibleFactor`

In **pre-copy-only** mode with no post-copy and no workload disruption, if implied downtime is only slightly above `maxDowntimeMs`, the algorithm may **keep trying** in hope a later iteration improves; if it is **far** above, the migration is treated as **hopeless** and aborted immediately. `precopyPossibleFactor` is the multiplier on `maxDowntimeMs` that defines that “hopeless” line (`maxDowntimeMs × precopyPossibleFactor`).

There is **no strong empirical argument** for a particular value here yet—**1.5×** is an **engineering default** that sounded reasonable: far enough from `maxDowntimeMs` to avoid aborting borderline cases, tight enough not to burn the full completion budget on migrations that are unlikely to become acceptable. Treat it as **placeholder tuning** until cluster data exists.

### `patienceWindowDecayFactor`

After stall, if remaining bytes never re-enters the band around `bestRemainingBytes`, the design **relaxes** the switch-over target to the **next larger** distinct remaining-byte level in history (see the parent VEP), but only after a **patience** window. Each time that window expires without convergence, patience is multiplied by **`patienceWindowDecayFactor`** before the next relaxation step.

This is again a **downtime vs total migration time** trade-off: a **larger** factor (slower decay) waits longer on each relaxation target, which may yield a slightly better switch-over but extends wall-clock time; a **smaller** factor steps through relaxed targets **faster**, shortening the migration at the cost of possibly switching on a less favorable local minimum.

**0.5** (halve the patience each round) felt **reasonable**: patience for successive relaxation targets decays **fast enough** that the job does not sit too long on a `bestRemainingBytes` level that the workload or network may never hit again, while still giving each level a meaningful window anchored initially to `progressTimeout`.

### `searchLocalMinima`

The `searchLocalMinima` flag controls whether the stall detector waits for a local minimum in remaining bytes before triggering a switch-over. When set to **false**, the migration will switch-over immediately upon detecting a stall.

A default of **true** is chosen because waiting for a local minimum generally results in a smaller final downtime. While an immediate switch-over might reduce the total migration time, the secondary goal of the stall detector is to optimize the switch-over point to minimize workload disruption.

### `observeOnly`

The `observeOnly` flag allows the stall detector to run in a "shadow mode" where it evaluates the migration and logs its decisions (e.g., when it would have aborted or switched over) without actually interfering with the migration process. 

A default of **false** is chosen because the primary goal of this feature is to actively improve migration convergence and stability by acting on stalls. The "shadow mode" is primarily intended for debugging, telemetry gathering, and safe experimentation by cluster administrators who want to observe the stall detector's behavior on their workloads before fully committing to its actions.

### `elevatedLogging`

The `elevatedLogging` flag controls the verbosity of the stall detector's output, leaving an enhanced "trail" in both pod logs and Prometheus metrics when enabled.

A default of **true** is chosen for the alpha phase. Given the complexity of migration dynamics and the introduction of several new heuristics, having rich telemetry is crucial for evaluating the real-world performance of the stall detector. This data will be invaluable for refining the default hyper-parameters and understanding edge cases before the feature graduates to beta. Once the feature matures, high-frequency telemetry to Prometheus will be reduced or removed.
