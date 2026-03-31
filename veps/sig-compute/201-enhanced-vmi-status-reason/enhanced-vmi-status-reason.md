# VEP #201: Enhanced VMI status reason

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

`vmi.Status.Reason` already exists but it is only set for one case `NodeUnresponsive` and `vmi.Status.Phase` is simultaneously set to `Failed`.

`vmi.Status.Reason` should be set everytime `vmi.Status.Phase` is set, especially for final phases `Succeeded` and `Failed`.

`vmi.Status.Phase` could reach a final state for many reasons including:
- domain state changes, like `Shutdown`, `Destroyed`, `Crash`, etc
- migrations: `Migrated`, `MigrationFailed`, etc
- other failure: `NodeUnresponsive`, `VirtLauncherUnresponsive`, `VirtLauncherCrashed`, etc

This enhancement is focussing on domain state changes where the reason is extracted from `domain.Status.Reason` including the following cases:

| phase      | reason      | 
|------------|-------------|
| `Suceeded` | `Shutdown`  |
| `Suceeded` | `Destroyed` |
| `Failed`   | `Crash`     |

## Motivation

<!--
Why this enhancement is important
-->

Displaying a reason for why the VM is in it's current state is important for end users as well as for debugging.

We are currently missing proper value for `Reason`. This enhancements proposes setting `Reason` to proper value.

## Goals

<!--
The desired outcome
-->

Identify the reason for the current state of the VM.

`Reason` is set for final phases `Failed` and `Succeeded` when they originate from domain state changes.

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

- For non-final phases `Scheduled`, and `Running`, providing Reasons is out of scope
- For final phases `Succeeded` and `Failed`, the following reasons are out of scope:
  - migrations: `Migrated`, `MigrationFailed`, etc
  - other failure: `NodeUnresponsive`, `VirtLauncherUnresponsive`, `VirtLauncherCrashed`, etc

## Definition of Users

<!--
Who is this feature set intended for
-->

For cloud providers that are interested in identifying the reason for the current VM state and possibly displaying it to the end users or for internal observability.

## User Stories

<!--
List of user stories this design aims to solve
-->

- As a VM user I want know why the VM is stopped.

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

For domain state changes, the `Reason` will come from `domain.Status.Reason`.
  

## API Examples

<!--
Tangible API examples used for discussion
-->

Notice that:
- `Reason` currently already exists as `string`. It's proposed to change it to `VirtualMachineInstanceReason` with proper `const`s


```golang
type VirtualMachineInstanceStatus struct {
  
	// A brief CamelCase message indicating details about why the VMI is in this state. e.g. 'NodeUnresponsive'
	// +optional
	Reason VirtualMachineInstanceReason `json:"reason,omitempty"`

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
	UnsetReason VirtualMachineInstanceReason = ""

	// NodeUnresponsiveReason is in various places as reason to indicate that
	// an action was taken because virt-handler became unresponsive.
	NodeUnresponsiveReason VirtualMachineInstanceReason = "NodeUnresponsive"

	// Reasons reflecting domain state changes
	ShutdownReason VirtualMachineInstanceReason = "Shutdown"
	DestroyedReason  VirtualMachineInstanceReason = "Destroyed"
	CrashedReason  VirtualMachineInstanceReason = "Crashed"
)
```


## Alternatives

<!--
Outline any alternative designs that have been considered)
-->


## Scalability

<!--
Overview of how the design scales)
-->


## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

- This is upgrade compatible.
- On rollback, VMs `Reason` field will be missing some values.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

- Unit tests: add coverage for new code.
- E2E tests: including cases to cover various `Reason` values.

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
