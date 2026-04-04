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


2. Migration does not trigger the switch-over to post-copy or stop-and-copy until **after** Max Completion Time (as calculated using `completionTimeoutPerGiB`) has been exceeded. Cluster admins who set a completion-time budget cannot have that budget honored proactively. Moreover, while at least, migrations that exceed the allocated time budget by a factor of two are aborted, in the case of post-copy even this is not possible since doing so would result in data loss.

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

The key idea behind understanding the stall detection algorithm is recognizing that in a **stalled** migration dirty rates and bandwidth, and therefore remaining bytes are not constant. Therefore, once a migration "converges", it tends to hover around the "converged" value of remaining bytes by "bouncing" up and down from iteration to iteration.

> __Convergence__:
Here we use convergence as the point when pre-copy stops making progress in each iteration of pre-copy, and the "remaining bytes" flattens out.

After convergence, instead of randomly trigger post-copy or stop-and-copy, we optimize for downtime by triggering the switch-over at a **local-minima** of the "remaining bytes".

In order to achieve this, once detect that we have "stalled" (as defined later), we look at "x" iterations since the start of the stall and save the smallest observed "remaining bytes" over these "x" iterations. Afterwards, the moment we see "remaining bytes" lower than that lowest observed value we switch to post-copy or stop-and-copy.

> __Sample Iterations__:
From here, we will refer to these "x" iterations as the "sample iterations".

> __Optimal Downtime__:
After the sample iterations finish, the downtime corresponding to the lowest observed value of "remaining bytes" is called the "optimal downtime".

### Pre-copy Improvements with Dynamic Downtimes

While the above description of a stall detector focuses on the use case for when either post-copy is enabled or when disruptions are allowed (thus allowing for very long downtimes) this mechanism can also be used to improve the behavior of pre-copy only migrations.

We do this by introducing a new API field called `maxDowntime` which specifies the maximum acceptable downtime in workloads where disruption is not allowed. Currently, migrations use a fixed downtime of 300ms (the default in QEMU) and cannot proceed if the predicted downtime is in excess of this value.

When a migration stalls, it stalls because "remaining data" cannot be brought down low enough to achieve a downtime as low as this. However, in many cases, a downtime higher than the default of 300ms is acceptable and even preferred to failing a migration. We call this value the `maxDowntime`.

> __Target Downtime:__
The "target downtime" refers to the downtime configured in QEMU. This is the threshold that when met, QEMU triggers its internal switch-over to stop-and-copy.

In pre-copy only mode, after stalling, **if** `maxDowntime` is lower than or equal to the "optimal downtime", we proceed with the migration setting the "target downtime" equal to the "optimal downtime"; **else if**, we `maxDowntime * 2 > targetDowntime` then we set the the "target downtime" equal to the `maxDowntime` that perhaps in a future iteration we might get lucky and still converge; **else** we abort the migration early.


### Defining Stall

We consider the migration "stalled" if both of the following conditions are satisfied:
1) At the end of an iteration, "remaining bytes" is larger than or equal to the smallest "remaining bytes" observed in a previous iteration that was also older than the "progress timeout".
2) The "remaining bytes" from the previous iteration to the current iteration went up.

> __Progress Timeout__:
When we say "progress timeout", we are referring to an existing API field under migration settings called `progressTimeout` that represents the maximum number of seconds a migration does not progress. Our revised stall detection algorithm repurposes this API field.


> __Reasoning and Justification for the Stall Definition:__
[todo: explain #1 allows remaining bytes to temporarily go up. #2 avoids switching to stall state due to a network blip. how by requiring remaining bytes be the minimum, we avoid triggering post copy during a network blip. there was already an existing issue of a network blip in the middle of post copy, we can't do anything about it. But network blips are expected to be rare, but even so when you are constantly watching for stall, and migrations can last a long time a blip anytime during a migration happening is much more likely (rather than the short duration post copy last).
]


### Choosing a Sane Default for Progress Timeout
TODO? Default 75s? Loosely informed by SimPoint-style phase-behavior analysis (Sherwood et al., 2002): rough phase lengths well under ~15 s for typical workloads, multiplied by ~5 for slack. Operators override via configuration.


### Defining the number of Sample Iterations

In order to identify that we are in a local minima, we must look at how "remaining bytes" has varied since the "stall start time".

> __Stall Start Time__:
The smallest "remaining bytes" observed in a previous iteration that was also older than the "progress timeout".

In general, we will aim to collect at least **5** sample iterations. This is because if we model the "remaining bytes" at the end of each iteration as a random sample, then with a sample size of 5, we can guarantee with a 97% probability that at least one of those samples had a "remaining bytes" value less than the average.

This gives us a very high likelihood of switching over to post-copy or stop-and-copy with at least an below average downtime (and usually better) compared to switching over randomly.

> __Math behind the 97% figure:__
 Given a particular random sample, there is a 50% probability that the random sample is less than the average. Therefore, after 5 random samples, we get 0.5^5=0.03125. Then 1-0.03125=0.96875, giving us the 97% figure above.

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


### Algorithm

Putting this all together, here is a technical description of how the stall detection algorithm works including how it wires together with the existing Kubevirt codebase:

TODO


- Other use cases could include input for deciding whether or not to activate compression since rn the choice is immutable.
- TODO: think about how when compression is active, data reporting would be off and how to get correct estimates still...
- TODO: prediction algorithm should have expected duration, worst case duration, and best-case duration

## API Examples

<!--
Tangible API examples used for discussion
-->

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