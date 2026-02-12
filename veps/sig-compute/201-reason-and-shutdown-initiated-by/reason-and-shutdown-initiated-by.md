# VEP #201: Reason for each Phase + ShutdownInitiatedBy

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

- Set `vmi.Status.Reason` each time `vmi.Status.Phase` is set. Reasons include:
  - `NodeUnresponsive` (already exists for `Phase = v1.Failed`)
  - `Shutdown` (for `Phase = v1.Suceeded`)
  - `Crashed` (for `Phase = v1.Failed`)
  - `Migrated` (for `Phase = v1.Suceeded`)
  - `MigrationTimeout` (for `Phase = v1.Failed`)
  - `MigrationFailed` (for `Phase = v1.Failed`)
  - `VirtLauncherUnresponsive` (for `Phase = v1.Failed`)
  - `VirtLauncherCrashed` (for `Phase = v1.Failed`)
  - `VirtLauncherSecureBootUnsupported` (for `Phase = v1.Failed`)
  - `VirtLauncherIrrecoverable` (for `Phase = v1.Failed`)
- Introduce `vmi.Status.ShutdownInitiatedBy` to indicate the reason for initiating VMI shutdown. `ShutdownInitiatedBy` values include:
  - `PodDeletion`
  - `VMIDeletion`


## Motivation

<!--
Why this enhancement is important
-->

Implementing automatic recovery logic for VM depends on the reason for the shutdown:
- If the VM was cleanly shutdown by the user, no automatic recovery is needed
- For other cases, automatic recovery may take place.

"Cleanly shutdown by a user" can be recognized by the following conditions:
- `Reason == Shutdown` (for `Phase = v1.Suceeded`)
- `ShutdownInitiatedBy NOT IN (PodDeletion, VMIDeletion)`


## Goals

<!--
The desired outcome
-->

Recognize user initiated shutdown alongside the result wether the shutdown succeeded, the VM crashed, or kubevirt failed.

- `Reason` is set for final phases `Failed` and `Succeeded`
- `ShutdownInitiatedBy` is set whenever a shutdown is initiated by kubevirt

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

- Reasons for non-final phases like `Scheduled`, and `Running`

## Definition of Users

<!--
Who is this feature set intended for
-->

For cloud providers that are interested in implementing recovery policy based on the reason for shutdown.

## User Stories

<!--
List of user stories this design aims to solve
-->

- As a VM user I want the VM to recover if it was stopped for reasons outside my control.
- As a VM user I want to choose whether the VM recovers or not if the shutdown is initiated by me

## Repos

<!--
List of repose this design impacts
-->

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/enhancements](https://github.com/kubevirt/enhancements) (this VEP)

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

- Set `vmi.Status.Reason` each time `vmi.Status.Phase` is set.
  - Essentially adding `Reason = ` for every `Phase = `.
- Introduce `vmi.Status.ShutdownInitiatedBy` to indicate the reason for initiating VMI shutdown.
  - Set `ShutdownInitiatedBy` every time `shouldShutdown = true`

## API Examples

<!--
Tangible API examples used for discussion
-->

Notice that:
- `Reason` currently already exists as `string`. It's proposed to change it to `VirtualMachineInstanceReason` with proper `const`s
- `ShutdownInitiatedBy` is added

```golang
type VirtualMachineInstanceStatus struct {
  
	// A brief CamelCase message indicating details about why the VMI is in this state. e.g. 'NodeUnresponsive'
	// +optional
	Reason VirtualMachineInstanceReason `json:"reason,omitempty"`

	// A brief CamelCase message indicating details about how the machine shutdown was initiated.
	ShutdownInitiatedBy VirtualMachineInstanceShutdownInitiatedBy `json:"shutdownInitiatedBy,omitempty"`
  
	// Phase is the status of the VirtualMachineInstance in kubernetes world. It is not the VirtualMachineInstance status, but partially correlates to it.
	Phase VirtualMachineInstancePhase `json:"phase,omitempty"`
  
  // ...
  }
```

`Reason` values

```golang

// These are the valid reasons of vmis.
const (
	// When a VirtualMachineInstance Object is first initialized and no reason is present.
	VmiReasonUnset VirtualMachineInstanceReason = ""

	// NodeUnresponsiveReason is in various places as reason to indicate that
	// an action was taken because virt-handler became unresponsive.
	NodeUnresponsiveReason VirtualMachineInstanceReason = "NodeUnresponsive"

	ShutdownReason VirtualMachineInstanceReason = "Shutdown"
	CrashedReason  VirtualMachineInstanceReason = "Crashed"
	UnknownReason  VirtualMachineInstanceReason = "Unknown"

	MigratedReason         VirtualMachineInstanceReason = "Migrated"
	MigrationTimeoutReason VirtualMachineInstanceReason = "MigrationTimeout"
	MigrationFailedReason  VirtualMachineInstanceReason = "MigrationFailedReason"

	VirtLauncherUnresponsiveReason          VirtualMachineInstanceReason = "VirtLauncherUnresponsive"
	VirtLauncherCrashedReason               VirtualMachineInstanceReason = "VirtLauncherCrashed"
	VirtLauncherSecureBootUnsupportedReason VirtualMachineInstanceReason = "VirtLauncherSecureBootUnsupported"
	VirtLauncherIrrecoverableReason         VirtualMachineInstanceReason = "VirtLauncherIrrecoverableReason"
)
```

ShutdownInitiatedBy values

```golang

// VirtualMachineInstanceShutdownInitiatedBy indicated the how the shutdown is initiated.
type VirtualMachineInstanceShutdownInitiatedBy string

// These are the valid shutdown initiation sources of vmis.
const (
	// When a VirtualMachineInstance Object is first initialized,
	// or when the shutdown is initiated by the guest os.
	VmiShutdownInitiatedByUnset VirtualMachineInstanceShutdownInitiatedBy = ""

	PodDeletionShutdownInitiatedBy VirtualMachineInstanceShutdownInitiatedBy = "PodDeletion"
	VMIDeletionShutdownInitiatedBy VirtualMachineInstanceShutdownInitiatedBy = "VMIDeletion"
)
```

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

### Using annotations instead of status

`ShutdownInitiatedBy` could be set as an annotation on `VMI` instead of introducing `vmi.Status.ShutdownInitiatedBy`.

I still believe the status is the right place for it.

### Implement an Event instead of Status change

An Event will **not suffice** in this case because to detect guest shutdown we need to depend on two separate parts of the status `ShutdownInitiatedBy` and `Reason` that change at different times.

For example, on one iteration of virt-handler, `ShutdownInitiatedBy` is set. Then on a later iteration `Reason` is set to `Shutdown`. Having both stored in the status, a client watching the VMI status will be able to decide that it's a guest shutdown depending on the condition:
- `Reason == Shutdown` (for `Phase = v1.Suceeded`)
- `ShutdownInitiatedBy NOT IN (PodDeletion, VMIDeletion)`


## Scalability

<!--
Overview of how the design scales)
-->


## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

- This is upgrade compatible.
- On rollback, VMs will fall back to the old behavior.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

- Unit tests: add coverage for new code.
- E2E tests: including cases for guest os shutdown and crash.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

10-02-2026: Draft PR: https://github.com/kubevirt/kubevirt/pull/16787


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

### Beta

### GA
