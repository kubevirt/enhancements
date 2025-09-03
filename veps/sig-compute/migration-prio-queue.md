# VEP #62: Generalize Priority Queue for Migrations in KubeVirt

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [X] (R) Target version is explicitly mentioned and approved
- [X] (R) Graduation criteria filled

## Overview

This enhancement introduces a `priority` field to the
`VirtualMachineInstanceMigration` (VMIM) API in KubeVirt. It enables
controllers to assign priorities to migrations, ensuring system-initiated
migrations (e.g., node drains, upgrades) take precedence over user-initiated
ones (e.g., hot plug operations). Running migrations will receive a reserved
high priority when re-enqueued, and admission webhooks will limit user-set
priorities to safeguard system operations.

## Motivation

The current migration controller, uses a priority queue where running
migrations have a priority of 0, and pending migrations re-enqueued due to
capacity constraints are set to \-100. This ensures running migrations are
processed first but does not differentiate between system-critical and
user-initiated pending migrations. As a result, user-initiated migrations
(e.g., multiple hot plug operations) can overwhelm the system, delaying
critical operations like node drains or KubeVirt upgrades. This proposal
introduces a flexible priority system to ensure system operations are
prioritized while maintaining the precedence of running migrations.

Note: The current priority queue has known issues (e.g., not always handling
active vs. pending as expected). This enhancement will address these as part of
the implementation.

## Goals

- Add a `priority` field to `VirtualMachineInstanceMigrationSpec`.
- Update the migration controller to order the queue by `priority`.
- Assign the highest, reserved priority (e.g., 1000) to running migrations to
  ensure they are processed first.
- Enable controllers to set priorities based on migration type.
- Restrict user-set priorities via admission webhooks.
- Ensure backward compatibility.

## Non Goals

- Change how migrations are triggered or processed.
- Introduce new migration types.
- Make migrations preemptible.

## User Stories

- As a cluster administrator, I want KubeVirt system-critical operations to proceed without delays from user actions.
- As a VM owner, I want my hot plug operations processed efficiently, respecting system priorities.

## Repos

* [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

A `priority` field will be added to the VMIM API, allowing various controllers to
set priorities for migrations. The migration controller will use this field
to order migrations in the priority queue, ensuring system-initiated migrations
are processed before user-initiated ones. Running migrations will be get a reserved
high priority (e.g., 1000).
Controllers will set priorities based on the migration type, and admission webhooks will ensure users cannot
set high priorities, protecting system operations.

### Priority Order

The priority order for migrations is as follows, with higher numerical values indicating higher priority:

- Running Migrations: 1000 (Protected)
- System-Critical Migrations: 100 - These include urgent tasks like node drains, evacuations, and KubeVirt upgrades. They’re critical for system health and need immediate attention.
- User-Triggered Operations: 50 -  These are user-initiated changes, like hot plug operations.  They’re time-sensitive but should not affect system stability.
- System Maintenance Migrations: 20 - Less urgent maintenance tasks, such as those started by the de-scheduler.
- Manual VMIM Creation without priority or custom admin setting: This applies to admin-created VMIMs where no priority is set. They get the lowest priority by default.

### Components to Change

- Migration Controller: Update to use priority for queue ordering and assign 1000 to running migrations.
- Evacuation Controller: Set priority for node drain/eviction migrations (system-critical).
- Workload Updater Controller: Set priorities based on trigger (upgrades vs. hot plug/workload changes).
- Volume Migration Controller: Set priority for volume-related migrations (if applicable).
- De-scheduler: Set priority for maintenance-related migrations.

## API Examples

```go
type MigrationPriority string

const (
  SystemCritical    MigrationPriority = "system-critical"
  UserTriggered     MigrationPriority = "user-triggered"
  SystemMaintenance MigrationPriority = "system-maintenance"
)

const (
  PriorityRunning           int32 = 1000
  PrioritySystemCritical    int32 = 100
  PriorityUserTriggered     int32 = 50
  PrioritySystemMaintenance int32 = 20
  PriorityDefault           int32 = 0
)

type VirtualMachineInstanceMigrationSpec struct {
    // Priority of the migration, higher values indicate higher priority.
    // +optional
    Priority *MigrationPriority `json:"priority,omitempty"`
[..]
}
```


## Update/Rollback Compatibility

 - VMIMs without `priority` default to 0 and the prioirity field is optional.

## Functional Testing Approach

- Test node drain, upgrades, hot plug, and mixed-priority scenarios. Make sure that the webhook is enforced.


## Feature Lifecycle Phases

### Alpha

- Add `priority` to VMIM CRD behind a feature gate.
- Update key controllers and test basic priority ordering.
- Add feature gate `MigrationPriorityQueue`

### Beta

### GA

- Lock feature gate ON
- Finalize docs

