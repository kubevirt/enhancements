# VEP #55: Extend `EvictionStrategy` to Support Specific Cluster-Initiated `virt-launcher` Pod Disruptions

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [X] (R) Graduation criteria filled

## Overview

Under specific well-known circumstances (e.g., scheduler preemption, `NoExecute` taints), a `virt-launcher` pod can be terminated (read deleted).
In these same cases, `PodDisruptionBudget` can also be bypassed. This can easily result in a VM being gracefully shut down, even if its `EvictionStrategy` is set to `LiveMigrate` or `LiveMigrateIfPossible`.

This proposal aims to have our controllers identify well-known cases (initially scheduler preemption and `NoExecute` taints) and, if the `EvictionStrategy` requires `LiveMigrate` or `LiveMigrateIfPossible`, attempt a live migration.

## Motivation

If the `EvictionStrategy` is configured with `LiveMigrate` or `LiveMigrateIfPossible`, the VM owners can reasonably assume that cluster actions outside of their control will result in a live migration. This is currently true for some cases (e.g., node drains, descheduler-initiated evictions) but not for others (e.g., scheduler preemption, `NoExecute` taints), leading to confusing or surprising behavior.
This proposal enhances the reliability and robustness of `LiveMigrate`-based `EvictionStrategies`.

## Goals
- Extend the current behavior of the `EvictionStrategy` (which only works in some scenarios) to ensure consistent live migration attempts regardless of the eviction cause.
- Handle standard Kubernetes features like scheduler preemption and `NoExecute` taints (e.g. well-known `CriticalAddonsOnly=true:NoExecute` that is correctly tolerated by daemonset pods) consistently within KubeVirt relaying on live migrations if possible.

## Non Goals

- This does **not** include handling node pressure eviction, which is locally initiated on the node (it's not a pod deletion request). [A previous proposal](https://github.com/kubevirt/enhancements/pull/9) was covering it.
- This does **not** mean triggering live migration for all `virt-launcher` pod deletions (that would implicitly make a pod deletion request a live migration request). Only specific disruptions identified via the `DisruptionTarget` condition and known reasons should trigger it.

## Definition of Users

- **VM owner**: the user who owns a VM in his namespace on a Kubernetes cluster with KubeVirt.
- **Cluster admin**: the administrator of the cluster.

## User Stories

- As a cluster admin, I want consistent behavior where VMs are always live-migrated during evictions rather than sometimes being shut down.
- As a VM owner, I want scheduler priority preemption to use live migration, not shutdowns, for rearranging workloads.
- As a cluster admin, I don’t want an unprivileged user to cause shutdowns of other users’ VMs via resource-heavy dummy pods.
- As a cluster admin, I expect the `EvictionStrategy` to be honored even when using a `NoExecute` taint for maintenance tasks.

# Design
This section outlines the theory behind how this design works and the code changes required to implement this theory into practice.

## Design Theory
### Actual behavior
#### Scheduler Priority Eviction

When a pod cannot be scheduled due to constraints, the scheduler may trigger preemption.
If the incoming pod has a high priority, the scheduler will preempt lower-priority pods by setting a condition `Type: v1.DisruptionTarget`, `Reason: v1.PodReasonPreemptionByScheduler`, then delete the pod directly (not using the eviction API).
Currently, this causes a graceful shutdown of the VM, not a live migration—even if `evictionStrategy: LiveMigrate` is set.
See: https://github.com/kubernetes/kubernetes/blob/3e5849972e619c58b3a8e8be2ca3ac35c4eb74da/pkg/scheduler/framework/preemption/preemption.go#L160-L202

Also, the scheduler can bypass PDB protection when necessary.
See: https://github.com/kubernetes/kubernetes/blob/3e5849972e619c58b3a8e8be2ca3ac35c4eb74da/pkg/scheduler/framework/preemption/preemption.go#L678-L679

#### `NoExecute` Taint Eviction

When a node is tainted with `Effect: NoExecute`, the taint manager evicts all non-tolerating pods.
It sets a condition with `Type: v1.DisruptionTarget`, `Reason: "DeletionByTaintManager"`, then deletes the pod.
See: https://github.com/kubernetes/kubernetes/blob/3e5849972e619c58b3a8e8be2ca3ac35c4eb74da/pkg/controller/tainteviction/taint_eviction.go#L129-L148

This, again, causes a graceful shutdown instead of a live migration.
PDBs are ignored by the taint manager.

## Repos

- `kubevirt/kubevirt` – main implementation
- `kubevirt/user-guide` – documentation updates
- `kubevirt/hyperconverged-operator` – exposure and enablement of the feature gate

## Design

`virt-controller` already watches `virt-launcher` pods.
If a pod has a `deletionTimestamp` but the corresponding VMI does not, the controller should:

1. Check for `DisruptionTarget` condition with:
   - `Status == True`
   - `Reason == DeletionByTaintManager` or `PodReasonPreemptionByScheduler`
2. Treat this as a valid eviction request and follow the VMI’s `EvictionStrategy`.

Grace period handling remains consistent with existing eviction logic.

## API Examples

These behaviors are guarded by opt-in feature gates:
- `PreemptionEvictionLiveMigration`
- `NoExecuteTaintEvictionLiveMigration`

Once enabled, KubeVirt responds to these eviction causes based on the per-VM or cluster-wide `EvictionStrategy`.

## Alternatives

The ideal long-term solution is for the scheduler and taint manager to use the eviction API.
However, these are sensitive components so such a change could require a long path. Thus, this proposal can act as a consistent workaround in the meantime.
In the past, something around ideas in this area got discussed in https://github.com/kubernetes/kubernetes/issues/91492 

## Scalability
The live migrations performed by this feature are handled by the existing evacuation controller. This controller is already aware of how to best execute live migrations across the cluster by not executing too many live migrations in parallel.

## Update/Rollback Compatibility
Attempting to enable the `PreemptionEvictionLiveMigration` and `NoExecuteTaintEvictionLiveMigration` feature gates on previous versions of KubeVirt will fail when this feature gate does not exist.

## Functional Testing Approach

### Scheduler preemption eviction
- We need a test environment with at least two schedulable worker nodes.
- The test should start a KubeVirt VMI.

#### Naive approach
- The test can directly set a condition with `Type==v1.DisruptionTarget`, `Status==v1.ConditionTrue` and `Reason==v1.PodReasonPreemptionByScheduler` to the virt-launcher pod and then delete it.
- The VM should be migrated to the other node and not shut down.
- The test should finally reclaim the test VM.

#### Realistic approach
- The test should create a high priority preemptive PriorityClass (we can eventually reuse one of the system ones for simplicity). 
- The test should inspect the node that is executing the VM and identifying the memory request of a POD that once scheduled on that node will for sure cause the preemption of the virt-launcher pod.
- The test should create a dummy pod with the identified memory request and the preemptive priority class.
- The test should check that the scheduler has chosen the virt-launcher pod for eviction.
- The test should check that the VMI got migrated to another node and not shut down.
- The test should finally reclaim the test VM, the dummy pod and the priority class used for the test.

### NoExecute taint eviction
- We need a test environment with at least two schedulable worker nodes.
- The test should start a KubeVirt VMI.
- Once the VMI is running, the test should apply a `CriticalAddonsOnly=true:NoExecute` taint to the node that was executing the VMI. `virt-handler` is expected to stay there.
- The test should ensure that the virtual machine will be live migrated and not stopped and restarted.
- The test should finally reclaim the test VM and untaint the node.

## Implementation Phases

This feature can be implemented in a single KubeVirt release cycle.

## Feature lifecycle Phases

### Alpha
The initial release of this feature will be feature gated and considered an Alpha. What we're interested in here is gaining a better understanding of usage patterns.

### Beta
Once we feel comfortable that this feature delivers on the goal of providing best effort live migrations across the majority of VM workloads without side effects, we'll graduate this feature to Beta.

### GA
Graduation to GA will occur once we have a clear signal that this feature behaves as expected in production.

# Open Questions
## Recording Cause of Migration on VMIM
Today when a VMIM is created, we don’t have context into why the system created the VMIM. It would be nice to have information tied to the VMIM that indicates why the migration was invoked… such as "API based eviction", "NoExecute Taint Eviction”, “Preemption Eviction, “virt-launcher update”, etc…

## Migration Time Optimizations
There is always the risk that the live migration will not complete withing a reasonable time.
In the case of Scheduler preemption, this could potentially delay the execution of pods with really high priority classes (such as `system-node-critical` or `system-cluster-critical`) that are critical in terms of system stability.
Instead of blocking forever, we can consider pausing the VMI after a certain timeout during the live migration in an attempt to converge on the Live Migration quicker and evacuate the node to allow the critical components to be scheduled.
