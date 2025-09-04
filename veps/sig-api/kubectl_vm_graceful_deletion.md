# VEP #71: Allow Graceful Deletion of VMs from `kubectl`

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting a milestone/release*.

- [x] (R) Enhancement issue created, which links to VEP directory in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

---

## Overview

Currently, deleting a Virtual Machine (VM) with a long `terminationGracePeriodSeconds` requires two commands:
1. `virtctl stop myvm --force --grace-period=0`
2. `kubectl delete vm myvm`

This process is inefficient and deviates from Kubernetes native deletion workflow.

This enhancement enables users to delete a VM with a custom grace period using a single
```bash
kubectl delete vm/myvm --grace-period=0
```
command, improving usability and aligning with Kubernetes conventions.
## Motivation

VM templates often specify extended `terminationGracePeriodSeconds` to give the guest operating system better opportunity to properly shut down.
However, users frequently need faster deletions during operations like restarts or test teardowns.
The current two-step process is cumbersome and requires familiarity with `virtctl`.
Simplifying this to a single `kubectl` command enhances efficiency, reduces complexity, and aligns with Kubernetes’ declarative approach.

## Goals

- Allow overriding `terminationGracePeriodSeconds` during VM deletion via `kubectl delete`.
- Remove dependency on `virtctl` for expedited VM termination.

## Non-Goals

- Alter existing `virtctl stop` functionality, which remains a valid alternative.
- Introduce new CLI tools or extend `kubectl` with custom subcommands.
- Add a feature gate for the webhook, as the feature is designed to be backward-compatible and enabled by default.

## Definition of Users

- **VM Owners**: VMs with templates specifying long grace periods and need efficient deletion workflows.
- **Developers**: KubeVirt developers require rapid VM cleanup for iterative development or automated testing.

## User Stories

- *As a VM Owner*, I want to delete a VM with a custom grace period using `kubectl delete`, avoiding delays from long default grace periods.
- *As a developer*, I want a single `kubectl` command to terminate VMs quickly without relying on `virtctl`.

## Repositories Affected

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

A **validating webhook** will be implemented to handle `DELETE` operations for `VirtualMachine` resources.
validating and applying the user-specified `--grace-period` by directly patching the associated `VirtualMachineInstance` (VMI).

### Workflow

1. **Interception and Validation**: The validating webhook intercepts
   `DELETE` API calls targeting `VirtualMachine` resources. If the user
   provides a `--grace-period=X` value, the webhook validates that it is a
   non-negative integer.
2. **VMI Patching**: If valid, the webhook patches the corresponding VMI 
   `spec.terminationGracePeriodSeconds` to the user-specified value.
3**Error Handling**: If the patch fails, the webhook rejects the
   deletion request with a descriptive error.
4**Deletion Propagation**: The VM deletion proceeds as usual. The updated
   VMI grace period governs how the launcher pod shuts down.

### Pros

- **User-friendly**: Aligns `kubectl delete` behavior with Kubernetes standards.
- **Backward-compatible**: Users not using `--grace-period` experience no change.

### Cons

- **Custom logic**: Requires KubeVirt-specific webhook to handle a standard flag.

## API Usage Example

```bash
kubectl delete vm myvm --grace-period=5
```

This command deletes the VM `myvm` with a 5-second grace period, overriding any longer default period.

## Alternatives

### Option 1: Enhance Kubernetes Core - already tried this approach 8 years ago

enhance Kubernetes so that the `--grace-period` flag from `kubectl delete` is propagated to a resource’s `metadata.terminationGracePeriodSeconds`, even for custom resources like `VirtualMachine`.
today when custom resources get deleted, the value for `metadata.terminationGracePeriodSeconds` get populated to 0 regardless of the flag given by the user.

This path was already explored in kubernetes/kubernetes#56567 about 8 years ago, but was never fully implemented.
That issue aimed to allow `DELETE` calls to honor the --grace-period flag for all resources—including custom ones—but eventually stalled due to complexity and lack of prioritization.

### Pros

- **Generic solution**: Works for all custom resources, not just VMs.
- **Standard behavior**: Eliminates the need for custom webhook logic.

### Cons

- **Complex coordination**: Requires changes across multiple Kubernetes SIGs.

## Scalability
No scalability concerns are anticipated, as the webhook operates on individual `DELETE` operations.

## Update/Rollback Compatibility
The change is backward-compatible, preserving existing VM deletion behavior when no custom grace period is specified.

## Functional Testing Approach

- **Setup**: Create a VM with a high `terminationGracePeriodSeconds` (e.g., 1600 seconds).
- **Test Case**: Issue `kubectl delete vm myvm --grace-period=3`.
- **Validation**:
    - Verify that the VM, VMI, and associated pod are deleted within the expected timeframe.

## Implementation Phases
- Single pull request to implement the webhook and integration logic.

## Feature Lifecycle Phases
- **No Feature Gate**: The feature will be enabled by default, as it enhances existing behavior without breaking changes.