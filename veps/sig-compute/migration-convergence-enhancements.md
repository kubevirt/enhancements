# VEP 248: Live Migration Stability & Convergence Improvements 

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: 1.9.0
- This VEP targets beta for version: 1.11.0
- This VEP targets GA for version: 1.12.0

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

<!--
Provide a brief overview of the topic
-->

This proposal makes the following key contributions:

(a) The introduction of a robust migration stall detection algorithm to detect whether the migration has stalled and when further progress is not expected.
(b) Optimized switch-over decision for when to switch to post-copy mode or stop-and-copy.
    (1) In cases where disruptions are allowed: select the best time to switch to post-copy mode or stop-and-copy mode when convergence is not possible.
    (2) In cases where disruptions are not allowed (pre-copy only): either fail the migration early as soon as it is clear convergence is not possible, or dynamically adjust the target migration `downtime` up to a maximum `max_downtime` as selected by the user.
(c) Proactively ensure the max completion time as derived from `completionTimeoutPerGiB` is not overrun by modeling migration completion time where possible.
(d) Data-backed optimizations of migration related defaults.
(e) Improved logging, and in particular exposing an estimation of the expected migration duration

## Motivation

<!--
Why this enhancement is important
-->

1. On high dirty-rate VMs where dirty-rates are more than the network bandwidth, migration runs for a long time wasting energy, compute and bandwidth even if the migration had already gotten as close to convergence as it could much earlier. There are two main reasons for this:
    (a) The defaults on `completionTimeoutPerGiB` are overly conservative with a value of 150s. For example, a moderately sized VM with 16GB memory can attempt to continue migrating for over 40 minutes before timing out. [TODO: show calculations on typical values for how long you really need]
    (b) The existing stall detection mechanism in Kubevirt is weak. Currently, it works by monitoring the progress on remaining bytes during a migration. The timeout on the migration is controlled by the `progressTimeout` field and resets anytime remaining bytes decreases. However, this ignores that during a typical stall, remaining bytes on the migration typically fluctuates up and down due to phase variations in the underlying workload causing the dirty rates to vary.


2. Migration does not trigger the switch-over to post-copy or stop-and-copy until **after** Max Completion Time (as calculated using `completionTimeoutPerGiB`) has been exceeded. Cluster admins who set a completion-time budget cannot have that budget honored proactively.

3. Currently users are completely blind for how long a migration is actually expected to take. [todo expand on this?]



## Goals

<!--
The desired outcome
-->

The desired outcome for end-users is to achieve the lowest possible downtime with in the provided completion time budget while avoiding wasting energy, compute and bandwidth when doing so does not help lower downtime.

- Decrease total migration time, downtime, and/or the time spent in pre-copy for VMs with high dirty rates.
- Proactively `MaxCompletionTime` by estimating migration/convergence duration before committing to paths that would overrun the budget (model and formulas in Design).
- Exposing to users an estimate of the total migration time.
- Minimize API changes and avoid exposing implementation details to end-users.
- Avoid arbitrary hard-coded constants unless we can present a coherent justification for them.
- Ensure robustness against network fluctuations and failures. For example, some solutions to this problem may trigger switch over if bandwidth is decreasing to ensure completion time is met. But if network is dying this is the worst thing you can do (especially in post copy).

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

- Gaurentee that `MaxCompletionTime` will never be exceeded. Only best-effort attempt.
- Optimize bandwidth in multi-vm scenerios

## Definition of Users

<!--
Who is this feature set intended for
-->

## User Stories

<!--
List of user stories this design aims to solve
-->

## Repos

<!--
List of repose this design impacts
-->
- kubevirt/kubevirt

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

### Pre-requisite
This design discussion assumes reader is familiar with the following concepts:
- Live migration
- Pre-copy, Post-copy, hybrid migration, and stop-and-copy
- Downtime
- How live migration works in Kubevirt

### Stall Detection

The key idea behind understanding the stall detection algorithm is recognizing that in a **stalled** migration dirty rates and bandwidth, and therefore remaining bytes are not constant. Therefore, once a migration "converges", it tends to hover around the "converged" value of remaining bytes. 

> __Convergence__:
Here we use convergence as the point when pre-copy stops making progress in each iteration of pre-copy, and the "remaining bytes" flattens out.

We want to trigger the switch-over to post-copy or stop-and-copy at a **local-minima** of "remaining bytes" in order to minimize the downtime.

In order to achieve this, once we stall, we look at "x" iterations since the start of the stall and save the smallest observed "remaining bytes" over these "x" iterations. Afterwards, the moment we see "remaining bytes" lower than that lowest observed value we switch to post-copy or stop-and-copy.

> __Sample Iterations__:
From here, we will refer to these "x" iterations as the "sample iterations".

#### Defining Stall

We consider the migration "stalled" if both of the following conditions are satisfied:
1) At the end of an iteration, "remaining bytes" is larger than or equal to the smallest "remaining bytes" observed in a previous iteration that was also older than the "progress timeout".
2) The "remaining bytes" from the previous iteration to the current iteration went up.

> __Progress Timeout__:
When we say "progress timeout", we are referring to an existing API field under migration settings called `progressTimeout` that represents the maximum number of seconds a migration does not progress. Our revised stall detection algorithm repurposes this API field.

##### Why this definition makes sense

[todo: explain #1 allows remaining bytes to temporarily go up. #2 avoids switching to stall state due to a network blip. how by requiring remaining bytes be the minimum, we avoid triggering post copy during a network blip. there was already an existing issue of a network blip in the middle of post copy, we can't do anything about it. But network blips are expected to be rare, but even so when you are constantly watching for stall, and migrations can last a long time a blip anytime during a migration happening is much more likely (rather than the short duration post copy last).
]

#### Choosing a Sane Default for Progress Timeout
TODO

#### Defining the number of Sample Iterations

In order to identify that we are in a local minima, we must look at how "remaining bytes" has varied since the "stall start time".

> __Stall Start Time__:
The smallest "remaining bytes" observed in a previous iteration that was also older than the "progress timeout".

In general, we will aim to collect at least **5** sample iterations. This is because if we model the "remaining bytes" at the end of each iteration as a random sample, then with a sample size of 5, we can guarantee with a 97% probability that at least one of those samples had a "remaining bytes" value less than the average.

This gives us a very high likelihood of switching over to post-copy or stop-and-copy with at least an below average downtime (and usually better) compared to switching over randomly.

> __Math behind the 97% figure:__
 Given a particular random sample, there is a 50% probability that the random sample is less than the average. Therefore, after 5 random samples, we get 0.5^5=0.03125. Then 1-0.03125=0.96875, giving us the 97% figure above.

Typically, while stalled, iterations are quick. This is because even when dirty rates are high, the "working set" in memory tends to span a relatively few amount of memory.

Nevertheless, in a future section we discusses dynamically lowering the number of sample iterations (or foregoing it all together) if not doing so risks a high probability of running out of time.

#### Algorithm

Putting this all together, here is a high level description of how the stall detection algorithm works:

TODO

### Migration Time Budgeting

We propose modeling how long a migration is estimated to last and pro-actively using this data

#### Choosing a Sane Default for Max Completition Time Per Gib

[todo: run the numbers on calculating given working set size, dirty rate, and bandwidth for how many iterations would it take for a pre-copy migration to stall (and flatten out) on a spreadsheet using formulas]

#### Budgeting Time for Sample Iterations
[todo talk about budgeting the time for the sample iteration + finding the local minima]

TODO: in order to ever trigger switch over, bandwidth must be at least 8mbps
- TODO: just like now if migration duration exceeds x2 the budget, cancel migration
- TODO: maybe once its obvious migration will exceed maxcompletiontime, cancel migration instead of going through with it?
- **Early switchover when the lower bound exceeds the deadline:** if the revised downtime from the lowest `remaining_bytes` ever observed already implies exceeding `MaxCompletionTime`, force switchover immediately — even before stall is detected (Design → **Lower-bound early switchover**).
- **Network safety** for forced convergence: Design → **Network safety** (TODO).
- TODO: maybe predicting when we get stalled?

### `ProgressTimeout` (existing field, removed and repurposed)

Today, **`ProgressTimeout`** is already plumbed from migration configuration into the launcher (e.g. `pkg/virt-handler/migration-source.go` → `cmdclient.MigrationOptions` → `pkg/virt-launcher/virtwrap/live-migration-source.go`). The **`migrationMonitor`** uses it only in **`isMigrationProgressing`**: if **wall-clock seconds since the last time remaining migration data improved** (relative to an internal watermark) exceed `ProgressTimeout`, the monitor considers migration non-progressing; depending on other branches in **`processInflightMigration`**, that can lead to **aborting** the job. That is a **minimal** stall signal—**no** completion-time budgeting, **no** EWMA of dirty rate or bandwidth, **no** local-minimum hunt before forcing pre-copy or pause-and-copy.

This proposal is **strictly better** for the convergence problem this VEP targets: the **existing** `ProgressTimeout` stall behavior will be **removed**, and the **`ProgressTimeout` field will be repurposed** to mean the **time window** that governs which observations are “mature” enough to feed **revised downtime** (e.g. samples **older than** `ProgressTimeout` from “now”), and to drive the convergence policies below. **`ProgressTimeout` does not pin revised downtime to one fixed minimum**—it shapes **inputs** to the predictor; **revised downtime** itself **updates** as lows are observed. Operators keep a single familiar knob; semantics and implementation change accordingly.

**Default value (75 s):** Loosely informed by SimPoint-style phase-behavior analysis (Sherwood et al., 2002): rough phase lengths well under ~15 s for typical workloads, multiplied by ~5 for slack. Operators override via configuration.

TODO: Update API / OpenAPI field description, cluster default (today **150** seconds in `virt-config`), and any user-facing docs to match repurposed semantics; feature gate if required.

### Max completion time and pre-copy timing

Use **EWMA**-smoothed **dirty rate** and **bandwidth** with **remaining data** and a **downtime threshold** to estimate **how long** iterative pre-copy (or equivalent) will take, so migration does not exceed `MaxCompletionTime`. This addresses the current gap: pre-copy is only triggered **after** the deadline because **duration was not estimable**.

**Givens (at each decision tick):**

| Symbol | Meaning |
|--------|---------|
| `ewma_dirty_rate` | EWMA of memory dirtying rate (same units as used for ratio with bandwidth, e.g. bytes/s) |
| `ewma_bandwidth` | EWMA of effective migration throughput (e.g. bytes/s) |
| `data_remaining` | Memory / data still to migrate (`memory left`, e.g. Libvirt `remaining_bytes`) |
| `downtime_threshold` | Target bound driving how many iterations are assumed before the final cutover (same unit family as `data_remaining` when used inside the ratio below—typically a **residual byte budget** aligned with max acceptable downtime via bandwidth; see note) |

**Note 1 — `downtime_threshold` is dynamic.** It is **not** fixed for the whole migration: when it becomes clear the migration **cannot** converge under the current downtime budget, the threshold may be **revised**. In particular, once **stalled**, **`downtime_threshold` is the revised downtime** (the current predicted best-case / policy bound feeding this model—see **Terminology**).

**Convergence coefficient** (matches Liu et al., HPDC 2011, $\lambda = D/R$):

\[
\lambda = \frac{\texttt{ewma\_dirty\_rate}}{\texttt{ewma\_bandwidth}}.
\]

**Predicted migration duration** (same closed form as the base geometric pre-copy model: total time $\approx \sum_i T_i$ with $T_i = V_i/R$, $V_i = V_{\mathrm{mem}}\lambda^i$, with $V_{\mathrm{mem}}$ taken as **`data_remaining`** at prediction time):

$$
T_{\mathrm{pred}}
  = \frac{\texttt{data\_remaining}}{\texttt{ewma\_bandwidth}}
    \cdot \frac{1 - \lambda^{\,n+1}}{1 - \lambda}.
$$

**Number of iterations** $n$ (ceiling of the base-$\lambda$ logarithm of the ratio of threshold to remaining data):
$$
n = \left\lceil \log_{\lambda}\!\left(\frac{\texttt{downtime\_threshold}}{\texttt{data\_remaining}}\right) \right\rceil.
$$

In code, $\log_{\lambda}(x) = \dfrac{\ln(x)}{\ln(\lambda)}$ (equivalently `log(x)/log(λ)`), for $\lambda > 0$, $\lambda \neq 1$.

**Validity and edge cases (TODO: encode explicitly in implementation):**

- The geometric sum requires **$0 < \lambda < 1$** (dirtying slower than the link on average). If **$\lambda \ge 1$** the model does not converge; treat **$T_{\mathrm{pred}}$** as **infinite** or fall back to a capped iteration count / conservative heuristic.
- If **$\lambda \to 1$**, use the limit $\displaystyle \lim_{\lambda \to 1} \frac{1-\lambda^{n+1}}{1-\lambda} = n+1$, i.e. $T_{\mathrm{pred}} \approx \dfrac{\texttt{data\_remaining}}{\texttt{ewma\_bandwidth}} \cdot (n+1)$.
- Clamp **$\texttt{downtime\_threshold} / \texttt{data\_remaining}$** into a valid range for the logarithm (e.g. $(0,1]$) and guard **$n \ge 0$**.

Compare **$T_{\mathrm{pred}}$** (plus any constant resume/overhead terms if needed) to **remaining time until `MaxCompletionTime`** when deciding whether to start or continue a convergence phase.

TODO: Failure modes when EWMA lags reality; interaction with `max_round` / platform caps on iterations.

### Lower-bound early switchover (pre-stall)

Even **before** the VM is stalled, if the **revised downtime** implied by the **lowest `remaining_bytes` observed so far** would cause $T_{\mathrm{pred}}$ to **exceed** `MaxCompletionTime`, **trigger switchover immediately**. The reasoning: the fastest possible completion is a pure pause-and-copy from the best point we have ever seen; that forms a **lower bound** on how long migration can possibly last. If that lower bound already blows the deadline, no future iteration can rescue it, so waiting further only wastes time.

### Stall Start Time and stalled state

- Continuously **update revised downtime** from **observations of the lowest `remaining_bytes` seen**, using a **predictor** (exact method TODO; may combine per-iteration minima, EWMA-style smoothing, and samples **older than** `ProgressTimeout` so recent noise does not dominate). Treat **revised downtime** as a **time-varying** value at each decision tick.
- **Stall** when **current `remaining_bytes` is lower than** the **current** **revised downtime** threshold (evaluated against the **latest** prediction).
- **Stall Start Time** is the **first** time that predicate holds (transition into stalled state), not “the time of the minimum sample” unless those coincide by definition in a given implementation.
- TODO: Reconcile the stall **inequality** and **units** (bytes vs predicted seconds) with the exact Libvirt metric and predictor output.
- After entering **stalled**, the migration controller treats progress as **non-recoverable** for unsticking: policies apply only to **when** to force convergence, not whether the VM is “making progress” again in the pre-stall sense.

### Local minimum and iteration window (3–10)

After stall, collect **3–10 iterations** measuring how **remaining data varies**, using data from **Stall Start Time** forward (so the buffer may already satisfy “enough” iterations at stall detection).

**Justification for 3–10**: TODO

**Additional Switch policies**:

1. Prefer **≥ 3 iterations**. If we have **fewer than three** and the **time required to reach three iterations** would **exceed** `MaxCompletionTime`, **switch immediately**
2. If **time remaining until** `MaxCompletionTime` **< 2×** the **wall-clock span** covered by iterations collected so far (TODO: align with final spec text), switching when remaining bytes is **below the median** over the window is **sufficient** (targets a “decent” switchover under time pressure).
3. Otherwise, continue until **`MaxCompletionTime`** **OR** remaining bytes hits the **smallest value observed** in the stalled observation window (per final implementation semantics).

### Network safety

TODO

## API Examples

<!--
Tangible API examples used for discussion
-->

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

**Iteration-count-based stall threshold** was considered but rejected. An iteration-count window (e.g. "no progress in N iterations") is inferior to a **time-based** window because iterations can be very short; dirty rate and bandwidth measurements over short iterations **fluctuate heavily**. Even if the time window only spans a single iteration, that iteration is long enough to suppress noise. A wall-clock window decouples the stall signal from iteration scheduling artifacts. Remaining bytes while “stuck” tends to **fluctuate**; switching at a **local minimum** reduces the amount of data copied during the disruptive phase. That requires a **time-based** notion of “no progress” (not iteration-count-based), so that short, noisy iterations do not false-trigger or miss real stalls.

Considered using a simplier metric to detect stall. dirty rate > bandwidth does not neccesarily mean pre-copy cannot converge. This is because dirty rate often involves a working set thats relatively small. In this case its still important to allow migration to continue to transfer and directly monitor remaining bytes.

## Scalability

<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha
Split into three stages:
0. Revision of defaults on downtime and completition time.
1. Improved stall detection
2. Integrate network stability tests
3. Improvements towards meeting max deadline

### Beta

### GA