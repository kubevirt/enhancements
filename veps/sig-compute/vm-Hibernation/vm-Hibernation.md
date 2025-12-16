# VEP #NNNN: Your short, descriptive title

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal introduces a VM hibernation mechanism for KubeVirt, enabling users to stop and start virtual machines by saving and restoring their running memory state.

## Motivation

Some users wish to shut down running machines to free up resources, but the virtual machine state remains the same when turned on as when turned off.

## Goals

Add VM hibernation functionality to kubevirt

## Non Goals

Any modification to the hotplug and hotunplug volume process.

## Definition of Users

End Users: these are people/programs that have permission to update Virtual Machine specifications

## User Stories

A user can edit vm to Hibernate a vm which save memory to a pvc and stop vm. User can edit vm to restore vm form the pvc.

## Repos

[https://github.com/kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### **Triggering Hibernation**

- The user sets `spec.runStrategy: Hibernate` in the VM object to initiate hibernation.
- The controller detects the field change and starts the hibernation process.

```yaml
spec:
  runStrategy: Hibernate
```

The transition of VM `runStrategy` is as follows:

![image-20250915111240394](image-20250915111240394.png)

### VM State Transition

Also we need some new `VirtualMachinePrintableStatus`:Hiberating, hiberated, Resuming.

![image-20250915112257530](image-20250915112257530.png)

### Hibernation and Wake Strategy

The hibernation configuration includes the method, timeout, and the PVC used.  Also we should have a

These should be at the VM level, so it is not suitable to be placed in the KubeVirt CR. There are two approaches: one is to add it to `vm.spec`, the other is to add it as annotations to the VM.

**Method 1:** Add `HibernateStrategy` and `WakeStrategy` in `vm.spec` to specify hibernation-related configuration.

```yaml
HibernateStrategy:
  mode: save
  timeoutSeconds: 500
  claimName: XXX-PVC
WakeStrategy:
  enabled: false 
```

**Method 2:** Add control annotations to the VM.

```yaml
kubevirt.io/hibernation-strategy: save
kubevirt.io/hibernation-strategy-timeout-seconds: 500s
kubevirt.io/hibernation-strategy-claim-name:
kubevirt.io/WakeStrategy-strategy: enabled
```

If the `save` method is used and `claim name` is not set, KubeVirt will create a PVC based on the VM's memory size and render the corresponding strategy fields. If no `hibernation method` is set, the default is the `save` method.

We need to consider scenarios where VM is hiberated but we want to start it directly using the start interface. Therefore, we hope to expose an interface to control this `WakeStrategy`. If `WakeStrategy` is not set, the default is the `restore` method. We need to set `WakeRategy.enabled` to false to trigger start a vm directly using the start interface.

### cluster-level config

We also need cluster-level config, which not only reduces the workload of additional configuration in each virtual machine, but also serves as a fallback mechanism to ensure that the Hibernate process always has a timeout period.

We  can setting global configuration in kubevirt-cr:

```
spec:
  configuration:
    defaultHibernateConfig:
      HibernateStrategy:
        mode: save
        timeoutSeconds: 500
      WakeStrategy:
        enabled: false 
```

### VM Status

Just like memorydump add the `VirtualMachineHibernationStatuses` field to the VM's status:

```yaml
VirtualMachineHibernationStatuses:
  Phase:
  Claim:
  FileName:
  StartTimestamp:
  EndTimestamp:
  Message:
```

The `Phase` field references the `dumpmemory` package and includes:

```go
const (
    HibernationPhaseInitial              HibernationPhase = "Initial"
    HibernationPhaseAssociating          HibernationPhase = "Associating"
    HibernationPhaseInProgress           HibernationPhase = "InProgress"
    HibernationPhaseCompleted            HibernationPhase = "Completed"
    HibernationPhaseFailed               HibernationPhase = "Failed"
)
```

Also we need resume from Hibernated(instead of restore already used). Add `VirtualMachineResumeStatuses`field to the VM's status:

```
VirtualMachineResumeStatuses:
  Phase:
  Claim:
  FileName:
  StartTimestamp:
  EndTimestamp:
  Message:
```

The `Phase` field is also references the `dumpmemory` package and includes:

    const(
        ResumePhaseRestoreAssociating   ResumePhase = "Associating"
        ResumePhaseRestoreInProgress    ResumePhase = "InProgress"
        ResumePhaseRestoreFailed        ResumePhase = "Failed"
        ResumePhaseRestoreCompleted     ResumePhase = "Completed"
        ResumePhaseRestoreUnmounting    ResumePhase = "Dissociating"
        ResumePhaseClean                ResumePhase = "Cleaned"
    )

### VMI Status

In my opinion, we can talk about this later.

---

### 1. Hibernation

#### Step 1: Trigger & Initial Check

1. The user triggers hibernation by setting `spec.runStrategy` of the VM to `Hibernate`.

2. If PVC is not specified, create PVC.

3. Generate the corresponding `FileName`.

4. Render `VirtualMachineHibernationStatuses` , FileName should related to the vmi, may FileName  hash(vmi.uuid):

   ```yaml
   HibernationStatuses:
     Phase: Initial-->associating
     Claim: PVCname
     FileName: filename
   ```

#### Step 2: Hot Mount PVC

1. Use hot-plug logic (similar to current `dumpmemory`; in the future may use "[Utility Volumes](https://github.com/kubevirt/enhancements/pull/91)") to mount the PVC to the `fileName` location.

   `VirtualMachineHibernationStatuses.phase` transitions from `associating` to `inprogress`.

#### Step 3: Perform Hibernation (currently only supports `virsh save`)

1. Use the `save` interface to write memory to fIie `VirtualMachineHibernationStatuses.FileName`.
2. Record `Phase.StartTime` at the beginning.
3. Upon successful hibernation, record `Phase.StartTime`, and update `Phase` to `Completed`. If failed, update phase to `Failed`.

#### Step 4: Cleanup

1. Sequentially clean up the launcher pod and VMI.

---

### 2. Restore

#### Step 1: Trigger

1. The user sets VM `spec.runStrategy` to `always` or other, and `WakeStrategy.enabled` to `true` or .

#### Step 2: Hot Mount PVC

1. Use hot-plug logic (similar to current `dumpmemory`; in the future may use "[Utility Volumes](https://github.com/kubevirt/enhancements/pull/91)") to mount the PVC to the `fileName` location.
   `VirtualMachineHibernationStatuses.phase` transitions from `Associating` to `InProgress`.

#### Step 3: Execute Restore

1. Use the restore interface to write memory state to `HibernationInfo.TargetFileName`.
2. Record `VirtualMachineResumeStatuses.StartTime` at the beginning.
3. Upon successful restore, record `VirtualMachineResumeStatuses.EndTime`, and update phase to `Completed`. If failed, update phase to `Failed`.
3. If `Completed` `VirtualMachineResumeStatuses.phase` transitions from `InProgress` to `Dissociating`.

#### Step 4: Cleanup

1. Hot-unmount (`Dissociating`) and remove vm.status.HibernationStatuses (`Clean`).
1. if pvc is automatically created, it will be automatically deleted as will as user specified pvc will not to delete.

---

### 3. Direct Start Without Restore

#### Step 1: Trigger

1. The user sets VM `spec.runStrategy` to `always` or other, and `WakeStrategy.enabled` to `false` .

#### Step 2: Cleanup

1. remove vm.status.VirtualMachineHibernationStatuses(`Clean`).



## API Examples

### Hibernate a running vm

before

```
spec:
  runStrategy: Running
```

after

```
spec:
  runStrategy: Hibernate
  HibernateStrategy:
    mode: save
    timeoutSeconds: 500
    claimName: XXX-PVC
```

### Resume from a hibernated vm

before

```
spec:
  runStrategy: Hibernate
  HibernateStrategy:
    mode: save
    timeoutSeconds: 500
    claimName: XXX-PVC
```

after

```
spec:
  runStrategy: Running
  HibernateStrategy:
    mode: save
    timeoutSeconds: 500
    claimName: XXX-PVC
```

### start a hibernated vm

before

```
spec:
  runStrategy: Hibernate
  HibernateStrategy:
    mode: save
    timeoutSeconds: 500
    claimName: XXX-PVC
```

after

```
spec:
  runStrategy: Running
  HibernateStrategy:
    mode: save
    timeoutSeconds: 500
    claimName: XXX-PVC
  WakeStrategy:
    enabled: false 
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

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation Phases

<!--
How/if this design will get broken up into multiple phases)
-->

## Feature lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

### Beta

### GA
