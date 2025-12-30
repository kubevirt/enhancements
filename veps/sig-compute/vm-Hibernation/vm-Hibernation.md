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

- Add VM hibernation functionality to kubevirt(Using the save restore method while retain the possibility of using the suspendToDisk method)
- Hibernation virtual machines process should comply with cloud native principles as much as possible (declarative way)
- Use utility volume scheme to save memory data to a file system type PVC through a controller-managed approach

## Non Goals

- Proactively restore virtual machines that have timed out during the hibernation operation. (Only prompt timeout)
- Any modification to the hotplug and hotunplug volume process.

## Definition of Users

**KubeVirt Controllers:** Primary users with appropriate service account permissions to trigger vm Hibernate.

**Kubernetes Administrators/Operators:** Initiate operations through higher-level APIs that trigger Hibernate.

## User Stories

A user who can edit vm object in k8s. They can edit vm runstrategy to Hibernate a vm which save memory to a pvc and stop vm. As will as user can restore vm form the pvc.

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

The hibernation configuration includes the method, timeout, and the PVC used.  Also we should have a cluster-level default config.

Add `HibernateStrategy` and `StartStrategy` in `vm.spec` to specify hibernation-related configuration.

```yaml
HibernateStrategy:
  mode: save
  warningTimeoutSeconds: 500
  claimName: XXX-PVC
StartStrategy: restore
```

mode field can set to `save` and `suspendToDisk` rightnow. `save` will use save interface in libvirt to save memery in additional storage(utility volume). `suspendToDisk` use pmsuspend interface in libvirt and save memery in system disk.

If the `save` method is used and `claim name` is not set, KubeVirt will create a PVC based on the VM's memory size and render the corresponding strategy fields. 

We need to consider scenarios where VM is hiberated but we want to start it directly using the start interface. Therefore, we hope to expose an interface to control this. We have found that there is already a StartStrategy interface available now, If `StartStrategy ` is not set, If we start vm, vm will directly using the start interface. We need to set `StartStrategy` to`restore` to trigger restore a vm  using the `restore`interface. 

#### Timeout Mechanism

For virtual machines where the hibernation operation has been triggered but fails to complete successfully after a specified period of time, we will not perform automatic recovery processing on them. Based on the concept of cloud-native declarative APIs, when `RunStrategy` is set to `Hibernate`, the controller will attempt to update the virtual machine phase to `Hibernated`.

Additionally, you can specify the `warningTimeoutSeconds` parameter in `HibernateStrategy`: when the time threshold is exceeded, the controller will generate **events** to notify the user that the expected hibernation completion time has been surpassed.

#### cluster-level config

We also need cluster-level config, which not only reduces the workload of additional configuration in each virtual machine, but also serves as a fallback mechanism to ensure that the Hibernate process always has a timeout period.

We  can setting global configuration in kubevirt-cr:

```
spec:
  configuration:
    defaultHibernateConfig:
      HibernateStrategy:
        mode: save
        warningTimeoutSeconds: 500
      StartStrategy: restore
```

> webhook: HibernateStrategy mode and timeoutSeconds must be set when update vm Runstrategy to Hibernate. Virt-api should deny update request if mode and timeoutSeconds dosen't set both in vm and kubevirt-cr.

### VMI spec

Add stopstrategy on vmi.spec like:

```
spec:
#save or suspendTodisk
  stopStrategy: save
```

vmi.spec.stopStrategy will trigger virt-handler send grpc request to virt-launcher. 

Add startstrategy `restore` on vmi(we already have startStrategy `paused`)

```
spec:
#paused or restore
  startStrategy: restore
```

### VM Status

Add the `HibernationStatus` field to the VM's status, `HibernationStatus ` is used to record the Hibernate status of VMs from VM perspective, without including specific Hibernate execution situations.

```yaml
HibernationStatus:
  mode:
  Phase:
  Claim:
  filename:
  // Empty if Hibernate succeed, contains reason otherwise
  Reason:
```

The `Phase` field references the `dumpmemory` package and includes:

```go
const (
    HibernationPhaseInitial              HibernationPhase = "Initial"
    HibernationPhasepvcCreate            HibernationPhase = "pvcCreate"
    HibernationPhaseReadyToHotPlug       HibernationPhase = "ReadyToHotPlug"
    HibernationPhaseHotPlugFinished      HibernationPhase = "HotPlugFinished"
    HibernationPhaseInProgress           HibernationPhase = "InProgress"
    HibernationPhaseCompleted            HibernationPhase = "Completed"
    HibernationPhaseFailed               HibernationPhase = "Failed"
)
```

Also we need resume from Hibernated(instead of restore already used). Add `VirtualMachineRestoreStatuses`field to the VM's status:

```
VirtualMachineRestoreStatus:
  Phase:
  // Empty if restore succeed, contains reason otherwise
  Reason:
```

The `Phase` field is also references the `dumpmemory` package and includes:

```go
const(
    ResumePhaseRestoreInitial       ResumePhase = "Initial"    
    ResumePhaseRestoreInProgress    ResumePhase = "InProgress"
    ResumePhaseRestoreFailed        ResumePhase = "Failed"
    ResumePhaseRestoreCompleted     ResumePhase = "Completed"
    ResumePhaseClean                ResumePhase = "Cleaned"
)
```

---

### VMI status

VMI acts as the execution carrier for specific Hibernate commands.

The issuance of commands and changes in the control flow status are reflected in `VMI.status.conditions`.

Add `VirtualMachineInstanceCondition` with  `Type: HibernationInProgress\HibernationRestoreInProgress`

```
type VirtualMachineInstanceCondition struct {
	Type   VirtualMachineInstanceConditionType `json:"type"`
	Status k8sv1.ConditionStatus               `json:"status"`
	// +nullable
	LastProbeTime metav1.Time `json:"lastProbeTime,omitempty"`
	// +nullable
	LastTransitionTime metav1.Time `json:"lastTransitionTime,omitempty"`
	Reason             string      `json:"reason,omitempty"`
	Message            string      `json:"message,omitempty"`
}
```

Just like `MemoryDump` process info saved in VolumeStatus.  We need `HibernationInfo` for `save ` strategy in volumeStatus.

```
type HibernationInfo struct {
	// ClaimName is the name of the pvc 
	ClaimName string `json:"claimName,omitempty"`
	// TargetFileName is the name of the save ingerface output
	TargetFileName string `json:"targetFileName,omitempty"`
}
```

### workflows with pmSuspend interface

#### 1. Hibernation

##### Step 1: Trigger 

1. The user triggers hibernation by setting `spec.runStrategy` of the VM to `Hibernate`. With `HibernateStrategy.mode` set to `suspendToDisk`. `vm.status.HibernationStatus` add with phase `Initial`,mode`suspendToDisk`.
2. virt-controller add `stopStrategy: suspendToDisk` on vmi when `spec.runStrategy` update to `suspendToDisk` on vm.
3. virt-handler send grpc request to virt-launcher and update `vmi.status.condations` with `HibernationInProgress` on vmi as well as update `HibernationStatus` to `InProgress` on vm.
4. If hibernation succeeds, the `VirtualMachinePrintableStatus` will switch to the **`hibernated`** state. Otherwise, if an error occurs in the hibernation GRPC call, a corresponding event will be added to the VMI. If the timeout threshold is reached, a corresponding hibernation timeout event will also be added to the VMI.

##### step 2: clean

1. Similar to a shutdown, the VMI and its associated launch pod will be deleted when `VirtualMachinePrintableStatus` switch to the **`hibernated`**.

#### 2. Restore

1. With `pmsuspend` mode, user can start VMs in the same way as they do now. virt-controller need clean `HibernationStatuses` on vm when vm is running.

#### 3.Recover from Hibernation

1. If the virtual machine state remains unchanged for an extended period after hibernation is triggered, we can manually update the VM to switch it to other state. If User just update `spec.runStrategy` from `Hibernate` to `Always` \ `Once` \ `Manual` \ `ReturnOnfail` which will reomve `stopStrategy: suspendToDisk` on vmi(Certainly, in extreme cases, if the VM is restored to the `Always` state but the previously issued hibernation request completes afterward, the VM will restart in behavior. This is a notable yet unavoidable scenario.). 
1. If user update `spec.runStrategy` to `halted` controller will tigger stop process.

### workflows with Save interface

#### 1. Hibernation

`HibernationStatus` can convert with below workflow.

![image-20251222104815082](image-20251222104815082-17663717006731.png)

##### Step 1: Trigger & Initial Check

1. The user triggers hibernation by setting `spec.runStrategy` of the VM to `Hibernate`. vm.status.HibernationStatus create whith phase `Initial`,mode`save`. and check pvc is ready to use, if pvc is not suitable for use, conntroller will create event.

   > The pvc size should largger than vm memory size + 512MB (I think we can talk about how to define pvc size more deeper)

   > If PVC is not specified in spec.HibernateStrategy, HibernationStatus phase to `pvcCreate`.and create a pvc(vm memory size + 512MB). 
   >
   > virt-controller should set ownerReferences of PVC with vm.
   
2. Add StartStrategy `restore` on vm.spec.

##### Step 2: Hot Mount PVC

1. While it is confirmed that PVC can be mounted, controller add utilityVolumes spec on vmi.

   ```
     # NEW FIELD:
     utilityVolumes:
       - name: Hibernate-f35782b2bd8c578bea6caf2087efa7e8
         persistentVolumeClaim:
           claimName: data-pvc
         type: Hibernate
   ```
   
2. When pvc has hotpluged to launcher pod, virt-handler sync vmi and exec save domain.

##### Step 3: Perform Hibernation (currently only supports `virsh save`)

1. Use the `save` interface to write memory to fIie with name (restroe-vm.name-timestamp)  and save file name to HibernationStatuses.
2. virt-handler send grpc request to virt-launcher and update `vmi.status.condations` with `HibernationInProgress` on vmi as well as update `HibernationStatus` to `InProgress` on vm.
3. Upon successful hibernation, update `HibernationStatus.Phase` to `Completed`. If failed, update phase to `Failed` and set failed reason.

##### Step 4: Cleanup

1. Sequentially clean up the launcher pod and VMI. Keep HibernationStatus(include pvc info)  in vm status.
1. If hibernate failed, just remove StartStrategy `restore` on vm.

---

#### 2. Restore

VirtualMachineRestoreStatuses  can convert with below workflow.

![image-20251222154631247](image-20251222154631247.png)

##### Step 1: Trigger

1. Set vm `spec.runStrategy` to `always` or other, and `WakeStrategy.mode` to `restore` .

   > webhook: If  pvc name dosen't seva in vm.status.HibernationStatuses, virt-api should deny the update.

2. Set VirtualMachineRestoreStatus on vm with phase `Initial`

##### Step 2: Use utility volume to mount PVC

1. Based on the existing vm start process,While render and create vmi, add utility volume to vmi with PVC store in vm.VirtualMachineHibernationStatus as will as startStrategy restore.
1. While pvc has mounted on launcher pod, just simple check the file(file name).

##### Step 3: Execute Restore

1. After a simple check of mounted file, virt-handler exec `restore` grpc request with mounted file path.
2. Set `VirtualMachineRestoreStatus` to `InProgress`.
2. update `vmi.status.condations` with `HibernationRestoreInProgress` on vmi.
3. Upon successful restore and update`VirtualMachineRestoreStatus` phase to `Completed`. If failed, update phase to `Failed`.
4. Only clean when VirtualMachineResumeStatusin `Completed` phase. if VirtualMachineResumeStatuses `Failed`, just set failed reason(timeout or something) in VirtualMachineResumeStatuses.Reason.

##### Step 4: Cleanup

1. Remove utility volume and`HibernationRestoreInProgress` condations on vmi.

2. Remove VirtualMachineHibernationStatus in vm.status.

   > if pvc is automatically created, it will be automatically deleted as will as user specified pvc will not to delete.

---

#### 3. Direct Start Without Restore

In some cases, such as recovery failure or PVC disappeared due to other reasons. It is impossible to restore vm. 

##### Step 1: Trigger

1. The user sets VM `spec.runStrategy` to `always` or other, and remove `startStrategy` on vm .

   > Dosen't add utility volume on vmi while creating vmi.

##### Step 2: Cleanup

1. remove vm.status.VirtualMachineHibernationStatus(delete).

   > if pvc is automatically created, it will be automatically deleted as will as user specified pvc will not to delete.

## API Examples

### With pmSuspend interface

#### Hibernate a running vm

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
    mode: suspendToDisk
    timeoutSeconds: 500
```

#### Resume/start from a hibernated vm

before

```
spec:
  runStrategy: Hibernate
  HibernateStrategy:
    mode: suspendToDisk
    timeoutSeconds: 500
```

after

```
spec:
  runStrategy: Running
```

### With Save interface

#### Hibernate a running vm

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

#### Resume from a hibernated vm

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
  StartStrategy: restore  
```

#### start a hibernated vm

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
