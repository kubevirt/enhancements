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
- This VEP targets beta for version: 1.10.0
- This VEP targets GA for version: 1.11.0

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

## Motivation

<!--
Why this enhancement is important
-->

1. On high dirty-rate VMs where dirty-rates are more than the network bandwidth, migration runs for a long time wasting energy, compute and bandwidth even if the migration had already gotten as close to convergence as it could much earlier. There are two main reasons for this:
    (a) The defaults on `completionTimeoutPerGiB` are overly conservative with a value of 150s. For example, a moderately sized VM with 16GB memory can attempt to continue migrating for over 40 minutes before timing out. [TODO: show calculations on typical values for how long you really need]
    (b) The existing stall detection mechanism in Kubevirt is weak. Currently, it works by monitoring the progress on remaining bytes during a migration. The timeout on the migration is controlled by the `progressTimeout` field and resets anytime remaining bytes decreases. However, this ignores that during a typical stall, remaining bytes on the migration typically fluctuates up and down due to phase variations in the underlying workload causing the dirty rates to vary.


2. Migration does not trigger the switch-over to post-copy or stop-and-copy until **after** Max Completion Time (as calculated using `completionTimeoutPerGiB`) has been exceeded. Cluster admins who set a completion-time budget cannot have that budget honored proactively. Moreover, while at least, migrations that exceed the allocated time budget by a factor of two are aborted, in the case of post-copy even this is not possible since doing so would result in data loss.

3. Currently users are completely blind for how long a migration is actually expected to take. [todo expand on this?]



## Goals

<!--
The desired outcome
-->

The desired outcome for end-users is to achieve the lowest possible downtime with in the provided completion time budget while avoiding wasting energy, compute and bandwidth when doing so does not help lower downtime.

- Decrease total migration time, downtime, and/or the time spent in pre-copy for VMs with high dirty rates.
- Better adhere to the `MaxCompletionTime` by switching over to pre-copy or pause-and-copy earlier. 
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

The key idea behind understanding the stall detection algorithm is recognizing that in a **stalled** migration dirty rates and bandwidth, and therefore the estimated downtime, are not constant. Therefore, once a migration "converges", the estimated downtime tends to hover around a "converged" value by "bouncing" up and down from iteration to iteration.

> __Convergence__:
Here we use convergence as the point when pre-copy stops making progress in each iteration of pre-copy, and the estimated downtime flattens out.

> __Estimated Downtime:__
QEMU exposes the estimated downtime through the `DomainJobInfo` API. This is roughly `remaining_data / bandwidth` and represents the expected guest pause duration during the final stop-and-copy phase. We use this as our primary metric rather than remaining bytes because it directly measures what we are optimizing for and implicitly accounts for both remaining data and current bandwidth.

After convergence, instead of randomly triggering post-copy or stop-and-copy, we optimize for downtime by triggering the switch-over at a **local-minima** of the estimated downtime.

In order to achieve this, once we detect that we have "stalled" (as defined later), we look at "x" iterations since the start of the stall and save the smallest observed estimated downtime over these "x" iterations. Afterwards, the moment we see an estimated downtime lower than that smallest observed value, we switch to post-copy or stop-and-copy.

> __Sample Iterations__:
From here, we will refer to these "x" iterations as the "sample iterations".

> __Optimal Downtime__:
After the sample iterations finish, the smallest observed estimated downtime is called the "optimal downtime".

### Pre-copy Improvements with Dynamic Downtimes

While the above description of a stall detector focuses on the use case for when either post-copy is enabled or when disruptions are allowed (thus allowing for very long downtimes) this mechanism can also be used to improve the behavior of pre-copy only migrations.

We do this by introducing a new API field called `maxDowntime` which specifies the maximum acceptable downtime in workloads where disruption is not allowed. Currently, migrations use a fixed downtime of 300ms (the default in QEMU) and cannot proceed if the predicted downtime is in excess of this value.

When a migration stalls, it stalls because "remaining data" cannot be brought down low enough to achieve a downtime as low as this. However, in many cases, a downtime higher than the default of 300ms is acceptable and even preferred to failing a migration. We call this value the `maxDowntime`.

> __Target Downtime:__
The "target downtime" refers to the downtime configured in QEMU. This is the threshold that when met, QEMU triggers its internal switch-over to stop-and-copy.

In pre-copy only mode, after stalling, **if** `maxDowntime` is lower than or equal to the "optimal downtime", we proceed with the migration setting the "target downtime" equal to the "optimal downtime"; **else if**, we `maxDowntime * 2 > targetDowntime` then we set the the "target downtime" equal to the `maxDowntime` that perhaps in a future iteration we might get lucky and still converge; **else** we abort the migration early.


### Defining Stall

We consider the migration "stalled" if both of the following conditions are satisfied:
1) At the end of an iteration, the estimated downtime is larger than or equal to the smallest estimated downtime observed in a previous iteration that was also older than the "progress timeout".
2) The estimated downtime from the previous iteration to the current iteration went up.

> __Progress Timeout__:
When we say "progress timeout", we are referring to an existing API field under migration settings called `progressTimeout` that represents the maximum number of seconds a migration does not progress. Our revised stall detection algorithm repurposes this API field.


> __Reasoning and Justification for the Stall Definition:__
[todo: explain #1 allows remaining bytes to temporarily go up. #2 avoids switching to stall state due to a network blip. how by requiring remaining bytes be the minimum, we avoid triggering post copy during a network blip. there was already an existing issue of a network blip in the middle of post copy, we can't do anything about it. But network blips are expected to be rare, but even so when you are constantly watching for stall, and migrations can last a long time a blip anytime during a migration happening is much more likely (rather than the short duration post copy last).
]


### Choosing a Sane Default for Progress Timeout
TODO? Default 75s? Loosely informed by SimPoint-style phase-behavior analysis (Sherwood et al., 2002): rough phase lengths well under ~15 s for typical workloads, multiplied by ~5 for slack. Operators override via configuration.


### Defining the number of Sample Iterations

In order to identify that we are in a local minima, we must look at how the estimated downtime has varied since the "stall start time".

> __Stall Start Time__:
The iteration with the smallest estimated downtime observed in a previous iteration that was also older than the "progress timeout".

In general, we will aim to collect at least **5** sample iterations. This is because if we model the estimated downtime at the end of each iteration as a random sample, then with a sample size of 5, we can guarantee with a 97% probability that at least one of those samples had an estimated downtime less than the median.

This gives us a very high likelihood of switching over to post-copy or stop-and-copy with at least a below-median downtime (and usually much better) compared to switching over randomly.

> __Math behind the 97% figure:__
 Given a particular random sample, there is a 50% probability that the random sample is less than the median. Therefore, after 5 random samples, we get 0.5^5=0.03125. Then 1-0.03125=0.96875, giving us the 97% figure above.

 > __Why not more samples?__
 While by collecting even more samples, we can likely lower how long downtime lasts, the benefits are maginal at the cost of increased total migration time.

Typically, while stalled, iterations are quick. This is because even when dirty rates are high, the "working set" in memory tends to span a relatively few amount of memory.

Nevertheless, in future works there might be marginal benefits to dynamically lowering the number of sample iterations (or foregoing it all together) if not doing so risks a high probability of running out of time as per the "max completition time". However, this would require being able to accurately model the migration duration (technically feasible, practically it also requires some changes in QEMU and therefore we consider this out of scope for now).


### Proactive Time Budgeting

If requirements on the max completition time are strict enough coupled with a low bandwidth and large state-size, it is possible for a VM with a low dirty rate to still exceed its allocated time budget.

Therefore, another improvement we propose is as follows:
- Anytime the estimated downtime as reported by QEMU exceeds the remaining time budget by a factor less than x2, we switch to post-copy or stop-and-copy.
- If the lowest observed estimated downtime exceeds the remaining time budget by a factor greater than x2, we abort the migration which is consistent with current behavior. However, the additional benefit here is that since post-copy migrations can never be cancelled (due to data loss concerns) no matter how they exceed the time budget by, by proactively looking at the estimated downtime we can choose to never switch-over such a migration.

> __Recall:__ Libvirt/QEMU exposes downtime estimates to us through the `DomainJobInfo` API.

> __Remaining Time Budget__: The "remaining time budget" is defined as the `MaxCompletitionTime` - `TimeElapsed`.

This is an improvement over the current design because currently don't trigger post-copy or pause-and-copy until the max completion time has already been exceeded.


#### Choosing a Sane Default for Max Completition Time Per Gib

[todo: run the numbers on calculating given working set size, dirty rate, and bandwidth for how many iterations would it take for a pre-copy migration to stall (and flatten out) on a spreadsheet using formulas]


## Implementation

Putting this all together, here is a technical description of how the stall detection algorithm works including how it wires together with the existing Kubevirt codebase:


### Feature Gate

A new **Alpha** feature gate `MigrationStallDetection` is introduced and registered in `pkg/virt-config/featuregate/active.go` with `State: Alpha`. When disabled, all behavior remains identical to the current implementation.

### Plumbing: Event-Based Iteration Loop

The migration monitor loop needs to react to iteration boundaries fired by QEMU. QEMU emits a `MIGRATION_PASS` QMP event each time it completes a dirty bitmap sync at the start of a new pre-copy iteration (i.e., the previous iteration's page transfer is complete and the dirty bitmap has been re-scanned). Libvirt translates this into a `VIR_DOMAIN_EVENT_ID_MIGRATION_ITERATION` domain event. The plumbling for this mostly only requires us to expose a `DomainEventMigrationIterationRegister(callback) (int, error)` method in the `Connection` interface in `pkg/virt-launcher/virtwrap/cli/libvirt.go`.


### Migration Monitor State Extensions

The `migrationMonitor` struct is extended with the following fields:

```go
type iterationRecord struct {
    timestamp           time.Time
    estimatedDowntimeMs uint64
    iterationIndex      uint32
}

// New fields on migrationMonitor:
iterationChan           chan int          // synchronising the polling loop with the event-based loop to avoid races
stallDetectionEnabled   bool              // from feature gate via MigrationOptions
maxDowntime             int64             // from MigrationOptions (milliseconds), 0 if unset

minCandidates           []iterationRecord // iteration records within the progressTimeout window that could become the next minRecordOutsideWindow when they age out
minRecordOutsideWindow  *iterationRecord  // record with the smallest estimatedDowntimeMs among iterations older than progressTimeout (nil until first record ages out)
prevIterDowntime        uint64            // estimated downtime at the previous iteration boundary
lastIterTimestamp       time.Time         // timestamp of the most recent iteration boundary (for polling tick skip)
stallDetected           bool
optimalDowntime         uint64            // smallest estimated downtime observed (across window + samples)

// Existing fields to be removed after feature gate graduation (used for an existing, rudimentary stall detector):
- remainingData           uint64
- lastProgressUpdate      int64
- progressWatermark       uint64
```

> __Note:__ 
Instead of keeping the entire iteration history, we maintain only two structures: `minCandidates` holds records within the `progressTimeout` window whose `estimatedDowntimeMs` is lower than `minRecordOutsideWindow` — these are the only records that could become the new minimum when they age out. `minRecordOutsideWindow` summarizes everything older than that window as a single record (the one with the minimum `estimatedDowntimeMs`). As candidates age out of the window, they replace `minRecordOutsideWindow` if their `estimatedDowntimeMs` is lower. Records with a higher downtime are discarded on insertion, keeping the list small.


### Monitor Loop & Algorithm

The existing `startMonitor()` select loop is extended with a new `iterationChan` case alongside the existing `migrationErr` and `time.After` cases. `monitorSleepPeriodMS` is increased from 400ms to **1000ms**. We keep both the event-based and polling-based arms running side by side because iteration events only fire between pre-copy iterations — if a single iteration takes a very long time (e.g., network stall), the polling tick still provides logging visibility and can enforce timeouts. When `stallDetectionEnabled` is **false**, the loop behaves identically to today (the `iterationChan` case simply logs and falls through to the existing `processInflightMigration` logic). When **true**, the iteration boundary arm runs the stall detection algorithm and the polling tick arm is limited to logging and timeout enforcement. No mutex is needed — the callback only sends to `iterationChan`; all state lives in the `startMonitor` goroutine.

> __Note:__ The pseudocode below omits feature gate checks for clarity. The actual implementation guards all new behavior behind the `MigrationStallDetection` feature gate; when disabled, the `iterationChan` case falls through to the existing `processInflightMigration` logic and the polling tick is unchanged.

```
for {
    select {
    case err = <-m.migrationErr:
        // (unchanged) propagate migration errors

    case iterCnt = <-m.iterationChan:
        lastIterTimestamp = now
        jobStats = getJobStats(dom)
        downtime = jobStats.Downtime             // estimated downtime (ms)

        // --- bookkeeping ---
        record = {now, downtime, iterCnt}
        age out minCandidates entries older than progressTimeout into minRecordOutsideWindow
        if minRecordOutsideWindow == nil || record.estimatedDowntimeMs < minRecordOutsideWindow.estimatedDowntimeMs:
            append record to minCandidates       // only keep records that could become the new min

        if postCopy or paused:
            continue

        // --- proactive time budget check (see Design §Proactive Time Budgeting) ---
        remainingBudget = acceptableCompletionTime - elapsed
        if optimalDowntime/1000 > remainingBudget*2:
            dom.AbortJob()                       // hopeless; also prevents starting a doomed post-copy that would take too long
        else if downtime/1000 > remainingBudget:
            triggerSwitchOver(...)                // act now to stay close to budget

        // --- stall detection ---
        if !stallDetected:
            if minRecordOutsideWindow != nil
               && downtime >= minRecordOutsideWindow.estimatedDowntimeMs
               && downtime > prevIterDowntime:
                stallDetected = true
                optimalDowntime = min(minRecordOutsideWindow.estimatedDowntimeMs,
                                     min over minCandidates of estimatedDowntimeMs)

        // --- when stalled ---
        else:
            iterationsSinceStall = iterCnt - minRecordOutsideWindow.iterationIndex

            // sample collection: track the best downtime during the 5 sample iterations
            if iterationsSinceStall <= 5 && downtime < optimalDowntime:
                optimalDowntime = downtime

            // switch-over at local minimum
            if iterationsSinceStall >= 5:
                if optimalDowntime < maxDowntime:
                    dom.MigrateSetMaxDowntime(optimalDowntime)
                else if AllowPostCopy && downtime <= optimalDowntime:
                    dom.MigrateStartPostCopy(0)
                else if AllowWorkloadDisruption && downtime <= optimalDowntime:
                    dom.Suspend()

        prevIterDowntime = downtime
        logMigrationInfo(...)
        checkCompletionTimeout(...)
        checkProgressTimeout(...)

    case <-time.After(monitorSleepPeriodMS):
        // Skip if an iteration boundary was processed within the last monitorSleepPeriodMS
        if !lastIterTimestamp.IsZero() && time.Since(lastIterTimestamp) < monitorSleepPeriodMS:
            continue

        jobStats = getJobStats(dom)
        logMigrationInfo(...)
        checkCompletionTimeout(...)
        checkProgressTimeout(...)
    }
}
```

## API Examples

<!--
Tangible API examples used for discussion
-->

The only API change proposed is adding a `maxDowntime` field to `MigrationConfiguration`. This specifies the maximum acceptable guest pause duration (in milliseconds) during the final stop-and-copy phase. 

> __Note:__ CR validation rejects values larger than 2,000,000ms (the QEMU hard limit). When unset, migrations behave as today by using QEMU's default target downtime of 300ms.

Cluster-wide default via KubeVirt CR:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - MigrationStallDetection
    migrationConfiguration:
      maxDowntime: 2000            # accept up to 2s downtime rather than fail
      progressTimeout: 75          # stall detection window (seconds; existing repurposed API)
      completionTimeoutPerGiB: 60  # tighter time budget than the default 150s
```

Pre-copy only migration with dynamic downtime — the VM can tolerate up to 5s of downtime. If the migration stalls and the optimal observed downtime is within 5s, the target downtime is adjusted dynamically to allow convergence rather than failing:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    migrationConfiguration:
      maxDowntime: 5000
      allowPostCopy: false
      allowWorkloadDisruption: false
```

Post-copy with `maxDowntime` — if the optimal downtime is within `maxDowntime`, the migration completes via stop-and-copy without ever entering post-copy. Post-copy only kicks in when the optimal downtime exceeds `maxDowntime`:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    migrationConfiguration:
      allowPostCopy: true
      maxDowntime: 2000            # prefers stop-and-copy if downtime fits within 2s
```

Workload disruption allowed (guest may be paused indefinitely) — `maxDowntime` has no functional effect here since the guest is simply paused to allow stop-and-copy to complete regardless of how long it takes:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    migrationConfiguration:
      allowWorkloadDisruption: true
```

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

1. **Iteration-count-based stall threshold** was considered but rejected. An iteration-count window (e.g. "no progress in N iterations") is inferior to a **time-based** window because iterations can be very short; dirty rate and bandwidth measurements over short iterations **fluctuate heavily**. Even if the time window only spans a single iteration, that iteration is long enough to suppress noise. A wall-clock window decouples the stall signal from iteration scheduling artifacts. Remaining bytes while “stuck” tends to **fluctuate**; switching at a **local minimum** reduces the amount of data copied during the disruptive phase. That requires a **time-based** notion of “no progress” (not iteration-count-based), so that short, noisy iterations do not false-trigger or miss real stalls.

2. Considered using a simplier metric to detect stall like dirty rate > bandwidth. But even when dirty rate > bandwidth, remaining bytes can still make progress because page dirtying often involves the same pages that are part of a working set. This working set tends to be small. In this case its still important to allow migration to continue to transfer and directly monitor remaining bytes.

3. An alternate proposal for stall detection was proposed here: https://docs.google.com/document/d/15P45MB9LtXTBKMfFkC2CLvEj-Hf9B6lY-W4DIEAq_5w/edit?tab=t.0#heading=h.sj3tv6yulsis. The key idea behind this proposal was to model pre-copy as a geometric series to estimate how long migration would take. Then, the proposal defined "stall" as when either of the two conditions are true:
(a) the projected completion time exceeded the max completion time
(b) dirty rate > bandwidth
Ultimately the design proposed above was favored because it also optimizes for downtime and better considers the variability of the dirty rate and bandwidth.

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

<!--
## Implementation History
Not Applicable

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
Split into two stages:
1. Revision of defaults on downtime from 300ms -> 500ms and decrease completition time per gib.
2. Improved stall detection

### Beta

### GA