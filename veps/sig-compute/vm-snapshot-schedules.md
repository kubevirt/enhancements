# VEP #231: Introduce VirtualMachine snapshot schedules

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

The Snapshot API allows for point-in-time snapshots of a VM. It still needs a manual intervention to run the snapshot.

The goal of this VEP is to introduce a new way of scheduling snapshots on a VMs, at regular intervals.

This would give the ability for Kubevirt users to safely ensure their VMs are snapshoted regularly.

## Motivation

<!--
Why this enhancement is important
-->

VMs are inherently stateful. Snapshots allow for point-in-time restoration of their state in case something happens to the data or to the metadata (VM defintion).

Using this new feature, users can build a backlog of snapshots in an automated fashion, at regular intervals.

Without the feature, users must manually run the backup or use hacky scripts to automatically run the snapshots.

## Goals

<!--
The desired outcome
-->

A new CRD permits scheduled VM snapshots:
- at regular intervals (CRON)
- for a number of snapshots (history with retention)

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

- The goal is to automate the snapshot API, not to extend it
- We don't want to deal with backups, as in, we do not want to externalize the snapshot somewhere else. This is a complicated task best handled by dedicated tools such as Velero.

## Definition of Users

<!--
Who is this feature set intended for
-->

- VM users are defined as Kubevirt users who can create VMs and snapshot them using the currently implemented snapshot API

## User Stories

<!--
List of user stories this design aims to solve
-->

### Regular snapshots with retention based on a number

- User wishes to snapshot their VM(s) at regular intervals, and keep a backlog of N VMSnapshots
- They create a new VolumeMachineSnapshotSchedule with a CRON expression to backup a set of VMs based on selectors (labelSelectors/matchExpressions)
- Kubevirt controller creates the snapshot at the set interval, and GCs them when the maximum amount defined in the VirtualMachineSnapshotSchedule is reached

## Reguler snapshots with retention based on time

- Same as the story above, but the retention is based on an expiry time on the VMSnapshotSchedule (say, oldest snapshot can be 1 month old)

## Repos

<!--
List of repose this design impacts
-->

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

Introduce a CRD of type VirtualMachineSnapshotSchedule. The controller would create a new VirtualMachineSnapshot according to the schedule defined in the schedule (CRON expression).

It also handles GCing the snapshots when they get out of the window of the schedule (32nd snapshot gets deleted if retention is set to 31 snapshots)

## API Examples

<!--
Tangible API examples used for discussion
-->

```yaml
---
apiVersion: kubevirt/v1beta1
kind: VirtualMachineSnapshotSchedule
metadata:
  # The name for this schedule. It is also used as a part
  # of the template for naming the snapshots.
  name: hourly
  # Schedules are namespaced objects
  namespace: myns
spec:
  # A LabelSelector to control which VMs should be snapshotted
  vmSelector:  # optional
  # Set to true to make the schedule inactive
  disabled: false  # optional
  retention:
    # The length of time a given snapshot should be
    # retained, specified in hours. (168h = 1 week)
    expires: "168h"  # optional
    # The maximum number of snapshots per VM to keep
    maxCount: 10  # optional
  # The cronspec (https://en.wikipedia.org/wiki/Cron#Overview)
  # that defines the schedule. It is interpreted with
  # respect to the UTC timezone. The following pre-defined
  # shortcuts are also supported: @hourly, @daily, @weekly,
  # @monthly, and @yearly
  schedule: "0 * * * *"
  snapshotTemplate:
    # A set of labels can be added to each
    # VirtualMachineSnapshot object
    labels:  # optional
      mylabel: myvalue
```

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

- Have an external project that handles scheduled snapshots through an operator
- Use Velero, but do not externalize the data (only use Velero as a CSI snapshot scheduled)
	- Con: restoring means deleting the VM and re-creating it, as Velero doesn't support "on the side" restores
	- Con: history and granularity of restores is poor

## Scalability

<!--
Overview of how the design scales)
-->

The only scalability problem is the number of VMSnapshots possibly created:
- If the selectors are wide, many VMs may be selected for Snapshot
- If the retention is long, many snapshots may be kept

Having an optional ownerReference from Snapshots to the Schedule can help reduce garbage by cascade-deleting children snapshots.
The GC must also be well tested to make sure it GCs the oldest snapshots.

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

No, new CRD/controller, no impact on current resources.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

- Create short timed snapshots (every minute for example) of a bunch of VMs (2-3)
- Set a low expiry to verify the GC destroys the snapshots after a few minutes
- No check on whether snapshots can be succesfuly restored (it is the job of the tests in the snapshot API)

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

- 14/12/25 - First implementation done here: https://github.com/kubevirt/kubevirt/pull/16339, author seems to be inactive

I'll take over the PR from here as I've had no response from the original author. Original author based their PR on my issue in Kubevirt: https://github.com/kubevirt/kubevirt/issues/15679

## Graduation Requirements

### Alpha
- [ ] Feature gate guards all code changes
- [ ] VirtualMachineSnapshotSchedules introduced

### Beta
- [ ] VMSnapshotSchedules successfully tested

