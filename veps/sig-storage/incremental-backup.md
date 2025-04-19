# VEP #22: Storage agnostic incremental backup using qemu

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] : https://github.com/kubevirt/enhancements/issues/25

## Overview

Proposal to enable incremental backup with Changed Block Tracking (CBT) using QEMU capabilities.

## Motivation

The current backup options rely on CSI-storage and create a full snapshot of the storage with each backup. This leads to longer backup times and increased storage space usage, whether the backup is stored in the cluster or off-site. Additionally, when stored off-site, a large amount of data needs to be copied over which further extends the backup time.
This can be improved by leveraging QEMU's capability to create incremental backups, which use Changed Block Tracking (CBT) to save only the changes made since the last backup.

## Goals

- Have an API to enable/disable incremental backup in VM.
- Have an API to have a backup either incremental or full using QEMU capabilities that provides CBT without the need for specific storage capabilities.

>> Note: this API's main consumer is backup vendors. They will provide the backup platform and will be responsible for getting the data and moving it to a defined location. A user may imitate backup vendors capabilities for this feature is not directed to the common VM owner.

## Non Goals

Have an API to restore from backup. In this VEP we will present a way to do it but its not something that we will have an API for.

## Definition of Users

* Backup vendors
* Cluster Admins
* VM owners

## User Stories

* As a KubeVirt user, I would like to back up my VM in the most efficient way possible, both in terms of time and storage usage.
* As a Kubevirt admin I would like to take a complete backup of my cluster. Then, I would like to take backup of the changes since the previous backup.
* As a Kubevirt admin I would like to store the backups off-site and lower the amount of data I am copying each time.
* As a Kubevirt admin I would like to restore to a specific time by applying both base backup and smaller time-specific increment.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Enable/Disable QEMU backup

QEMU backup with CBT is only supported with QCOW2 images, currently, Kubevirt supports only raw images.
To enable QEMU backup for a disk, we need to create a QCOW2 overlay that will store the **image metadata** (Not data!) and enable the use of QCOW2 features.
This overlays will be stored on a VM state PVC, similar to how TPM and EFI are handled. If the VM uses either of these features, the overlays will be created on the same PVC. Figure 1 describes how this will look like:
![figure 1](qcow2overlay.png)

A new field, `changedBlockTracking`(*NOTE: open to other name suggestions*), will be added to the VirtualMachine and VirtualMachineInstance CRDs. This field will control the addition or removal of the QCOW2 overlay to each of the VM disks. To enable the feature, one will set it to `true`. If the VM is already running, a restart will be required for the change to take effect (Currently, live addition of the overlay is not possible. Libvirt is working on this, and once it's available, we will work on incorporating it into KubeVirt as well).
When the VM starts with this field set to `true`, the VMI spec will also be created with this field set to `true`. This will trigger the creation of a VM state PVC (if one doesn’t already exist), create a QCOW2 image overlay on top the raw boot image for the disks that have the changedBlockTracking field, and for each of those disks add a data-store tag in the domain XML, resulting in a domain with QCOW2 overlays.
*NOTE: In the case of a hot-plugged disk, the VM will need to be restarted for it to be included in the backup and have CBT. Once live addition of the overlay is supported, restarts will no longer be required.

### Full backup

Libvirt's domain commands will be used to leverage QEMU backup capabilities, with the aim of making our backup API as similar as possible to libvirt's backup and incremental backup API.

To initiate a backup, a VirtualMachineBackup resource will be created. This resource will be managed by a new VMBackup controller.

As outlined in Libvirt, there are two general modes for backup:

```
A `push` mode (where the hypervisor writes out the data to the destination file, which may be local or remote), and a `pull` mode (where the hypervisor creates an NBD server that a third-party client can then read as needed, and which requires the use of temporary storage, typically local, until the backup is complete).
```
* In the initial phase, we will implement **push** mode. In this mode, a filesystem PersistentVolumeClaim (PVC) will either be provided by the user or created as part of the backup process. The PVC must be large enough to accommodate the backup data of all disks. To ensure this, an estimation of the backup size will be performed beforehand.
If a PVC is not provided, the backup process will create one based on the estimated size, with an additional buffer to account for overhead. If a PVC is provided, its size will be validated against the estimated requirement.
Once the PVC is ready, it will be hot-plugged into the virt-launcher pod. The backup process will then begin, writing the data directly to the attached PVC. After the backup completes, the PVC will be unplugged, and it will be up to the user to decide how to handle the backup data—such as moving it to a remote location, if desired.
* In the second phase, we will implement **pull** mode. This will involve defining a complete network API that exposes an endpoint the user can connect to in order to retrieve the backup.
>> Note: More details on the pull mode will be described in a subsequent VEP.

Once the backup is initialized, the controller will pass a backup command to the virt-launcher via the virt-handler using a subresource, containing all the relevant information. Before the backup begins, an FSFreeze command will be issued to ensure file system consistency during the backup. This will be the default behavior, with an option to skip filesystem quiescing if desired.
Then, [`virDomainBackupBegin`](https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainBackupBegin) will be invoked, which, as documented in libvirt, starts a point-in-time backup job for the desired disks of the running domain. This job captures the domain's disks state at the time of initiation, allowing to then call FSThaw (if needed). This minimizes guest downtime and enables the backup to be fetched while the guest continues its workload.

In `push` mode, the backup job in libvirt automatically terminates once all data has been successfully backed up. The controller will be notified when the job completes and will update the `VirtualMachineBackup` phase to `Done`. At this stage, the PVC will be detached from the VM and made available for user operations. Since the `VirtualMachineBackup` resource is no longer needed, it can be safely deleted.

### Incremental backup

The incremental backup process should closely mirror the full backup process, with a few distinctions.

Libvirt has a [Checkpoint](https://libvirt.org/formatcheckpoint.html#checkpoint-xml) resource that represents the point in time when the backup was taken. A checkpoint is created for every backup, including full backups. The checkpoint name can be provided, or if not, it will automatically use a predefined prefix followed by the backup timestamp.

During an incremental backup, libvirt returns the changed blocks based on a provided checkpoint name—typically the checkpoint from the previous backup.
To track previous backups and enable users to initiate incremental backups, a `CheckpointsList` CR will be introduced on a per-VM basis. This CR will contain a list of checkpoint names along with their associated metadata. The `VirtualMachineBackup` CR will include an `incrementalBackup` field, which users can populate with the desired checkpoint name from the list-indicating the point from which the incremental backup should capture changed blocks.

One challenge in Kubevirt is that Libvirt is re-created each time a VM restarts, causing all existing libvirt checkpoints to be lost. To enable incremental backups after a restart—rather than falling back to a full backup, checkpoints metadata must be provided to libvirt during VM initialization. This allows libvirt to redefine the checkpoints, ensuring that an incremental backup can be continued as expected.

To address this, the VM controller will utilize the VM's `CheckpointsList` CR during startup. It will iterate through the list and recreate each checkpoint in libvirt using the stored metadata.

### Offline backups

An initial proposed solution involves starting the VM in a paused state to perform the backup, and then either unpausing or shutting it down once the backup is complete. However, this approach introduces additional considerations and trade-offs, which justify a separate, dedicated discussion. As such, this section is intentionally left without a formal API definition for now.

### State interruptions during backup

According to the previous solution, if the VM was offline when the backup was requested, we will start the VM in a paused state. When the VM is in a paused state, there is no issue with starting it during the backup process, as Libvirt and QEMU guarantee that any VM operations, such as overwriting blocks in the data file, will not affect the backup's consistency. This consistency assurance could not have been guaranteed if an alternative, non-built-in solution had been considered.

In the case of an online backup if the user tries to stop the VM there will be a built-in [solution](https://issues.redhat.com/browse/RHEL-8067) that Libvirt is currently working on. Until then, we may need to handle it on the Kubevirt side by preventing changes to the VM state during the backup or before stopping the VM we would wait for the VirtualMachineBackup object to be deleted and only then allow the VM to stop.

### VM crash

In the event of a VM crash, the CBT information may become corrupted and must be discarded. QEMU provides an API to verify if the dirty bitmaps are valid. Upon VM restart, while redefining the checkpoints, we will check the validity of the bitmaps. If a corrupted bitmap is detected, the checkpoints will be discarded, and the next backup will need to be a full backup.

## API Examples

### Enable/disable QEMU backup

A new `changedBlockTracking` field will be added to the VirtualMachine and VirtualMachineInstance CRs. In the VirtualMachine CR, it will be added under: `Spec.Template.Spec.Domain.Devices.Disk[i].changedBlockTracking`. Similarly, in VirtualMachineInstance CR, it will be added under: `Spec.Domain.Devices.Disk[i].changedBlockTracking`.
The field will be of type \*bool and will determine whether the QCOW2 overlay should be applied on that disk. As with any other field in the VMI template, this change will only take effect when the VM is restarted. Upon restart, the VMI spec will be created with the updated field value.

When the field is present and set to `true`, a VM state PVC will be created if doesn't already exist. Then, For every disk a QCOW2 image will be created using the raw disk image as its data-file. Before applying the domain XML and starting it in virt-launcher manager, the XML will be modified for each disk to use the newly created QCOW2 image as the disk.

The XML will modified from:
```xml
    <disk type='file' device='disk' model='virtio-non-transitional'>
      <driver name='qemu' type='raw' cache='none' error_policy='stop' discard='unmap'/>
      <source file='/var/run/kubevirt-private/vmi-disks/datavolumedisk/disk.img' index='2'/>
      ...
    </disk>
    <disk type='block' device='disk' model='virtio-non-transitional'>
      <driver name='qemu' type='raw' cache='none' error_policy='stop' io='native' discard='unmap'/>
      <source dev='/dev/datavolumedisk2' index='3'/>
      ...
    </disk>

```

To:
```xml
    <disk type='file' device='disk' model='virtio-non-transitional'>
      <driver name='qemu' type='qcow2' cache='none' error_policy='stop' discard='unmap'/>
      <source file='/run/kubevirt-private/libvirt/qemu/swtpm/datavolumedisk.qcow2' index='2'>
        <dataStore type='file'>
          <format type='raw'/>
          <source file='/run/kubevirt-private/vmi-disks/datavolumedisk/disk.img' index='3'/>
        </dataStore>
      </source>
      ...
    </disk>
    <disk type='file' device='disk' model='virtio-non-transitional'>
      <driver name='qemu' type='qcow2' cache='none' error_policy='stop' discard='unmap'/>
      <source file='/run/kubevirt-private/libvirt/qemu/swtpm/datavolumedisk2.qcow2' index='4'>
        <dataStore type='block'>
          <format type='raw'/>
          <source dev='/dev/datavolumedisk2' index='5'/>
        </dataStore>
      </source>
      ...
    </disk>
```

In the case the field is not present or set to `false`, the VM state PVC will deleted if it is not required by other features. The QCOW2 images will be deleted if exists. And the domain XML will be modified back to use the raw images as the disks images.


### VirtualMachineBackup CRD

`VirtualMachineBackup` is a namespace-scoped Custom Resource that initiates the backup process.

The CR name should be a unique identifier for the backup within the namespace, with only one backup allowed per VM at a time.
The CR spec includes the following fields:
- `source`<br>
Specifying the VM source for the backup.
- `mode`<br>
Optional. Should be either `push` or `pull`. Initially, as mentioned, only `push` will be allowed. If not specified, `push` will be the default behavior.
- `pvcName` <br>
Optional. In push mode, a PVC with the specified name will be used to store the backup output. If no name is provided, a PVC will be created with a generated name consisting of the VM name and the backup CR creation time. If a name *is* provided, the backup will expect a PVC with that name to exist.
- `incrementalBackup` <br>
Optional. If not specified, a full backup will be taken. When set, the field value should be the checkpoint name from which the incremental backup should be taken.
- `checkpointName` <br?>
Optional. If not specified, a name will be generated consisting of the VM name and the backup CR creation time. Example: `my-vm-backup-2025-03-03T16:13:28Z`

The CR status includes the following fields:
- `checkpointName`<br>
Updated by the controller with the name of the created Checkpoint for the current backup.
- `pvcName` <br>
Present only for push mode backup. Updated by the controller with the name of the PVC containing the backup output.
- `conditions`<br>
Indicates the state of the backup, such as `Initializing`, `Done`, `Failed` or `Deleting`

Examples:

_Full Backup_
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineBackup
metadata:
    name: backup1
    namespace: ns1
spec:
    source:
        apiGroup: kubevirt.io
        kind: VirtualMachine
        name: my-vm
    mode: push
status:
    checkpointName: my-vm-backup-2025-03-03T16:13:28Z
    pvcName: my-vm-backup-2025-03-03T16:13:28Z
```

_Incremental Backup_
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineBackup
metadata:
    name: backup2
    namespace: ns1
spec:
    source:
        apiGroup: kubevirt.io
        kind: VirtualMachine
        name: my-vm
    incremental: my-vm-backup-2025-03-03T16:13:28Z
status:
    checkpointName: my-vm-backup-2025-03-03T16:13:28Z
    pvcName: my-vm-backup-2025-03-03T16:13:28Z
```

### VirtualMachineCheckpointsList CR

`VirtualMachineCheckpointsList` is a namespace-scoped Custom Resource used to track backup checkpoints per VM.
The CR will be generated when the `changedBlockTracking` field is set to true.

The CR name will be generated by using the VM Name and a suffix. Example: `my-vm-checkpoints`.

The CR spec includes the following field:
- `checkpointsNames`<br>
A list of checkpoint names by their creation time.
- `latestCheckpointName` <br>
The name of the checkpoint that was taken last and probably should be used for the next incremental backup.

The CR status includes the following field:
- `checkpoints`<br>
The list of the checkpoints, each having the name, creation timestamp and their metadata. Metadata may be base64 encoded to avoid clutteringthe yaml.

If the CR lists is empty that means a full backup is in order.
If a checkpoint name is removed from the spec.checkpointsNames list, it will trigger the deletion of the corresponding libvirt checkpoint. Once the deletion is done, the checkpoint will also be removed from the status.checkpoints list.

_CheckpointsList_
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineCheckpointsList
metadata:
    name: my-vm-checkpoints
    namespace: ns1
spec:
    checkpointsNames:
    - my-vm-backup-2025-03-03T16:13:28Z
    - my-vm-backup-2025-03-03T17:13:28Z
status:
    checkpoints:
    - name: my-vm-backup-2025-03-03T16:13:28Z
      creationTime: "2025-03-03T16:13:28Z"
      xmlData: PGRvbWFpbmNoZWNrcG9pbnQ+CiAgICA8bmFtZT4xNTI1ODg5NjMxPC9uYW1lPgogICAgPHBhcmVudD4KICAgICAgICA8bmFtZT4xNTI1MTExODg1PC9uYW1lPgogICAgPC9wYXJlbnQ+CiAgICA8Y3JlYXRpb25UaW1lPjE1MjU4ODk2MzE8L2NyZWF0aW9uVGltZT4KICAgIDxkaXNrcz4KICAgICAgICA8ZGlzayBuYW1lPSd2ZGEnIGNoZWNrcG9pbnQ9J2JpdG1hcCcgYml0bWFwPScxNTI1ODg5NjMxJy8+CiAgICAgICAgPGRpc2sgbmFtZT0ndmRiJyBjaGVja3BvaW50PSdubycvPgogICAgPC9kaXNrcz4KPC9kb21haW5jaGVja3BvaW50Pg==
    - name: my-vm-backup-2025-03-03T17:13:28Z
      creationTime: "2025-03-03T17:13:28Z"
      xmlData: ...
```

In the event of a VM restart, during initialization, the virt-launcher will iterate through the checkpoints list in the status and redefine each checkpoint in libvirt using the stored metadata.
If a checkpoint’s corresponding bitmap is found to be corrupted, all checkpoints will be deleted from the CR list and libvirt, requiring a full backup to be performed during the next backup operation.

### Collection of the backup

> Note: All of the information mentioned below are suggestions for backup vendors or users to fetch the backup. **There will not be an API providing this operations.**

**Push mode:**
PVC name containing the backup is provided in the VirtualMachineBackup CR status. The backup output will be stored as sparsed qcow2 images, one per disk. In the `Restore` section we document an example of how to stitch these files together to construct a restorable image. To move the images, a data-mover pod can be spawned to attach to the PVC and copy the data over to a remote storage. After the data is moved, the PVC can be deleted.

### Restore

> Note: The information mentioned below is a naive option only. **There will not be an API provided for this.**

Assuming that the full backup and all incremental backups up to the desired restore point are available, the incremental backups must be applied in the correct order (from the first incremental to the last) to ensure proper integration with the full backup.

The typical process for applying incremental backups in the QCOW2 format involves using the `qemu-img` tool, which can merge incremental backups with the base full image. You can use the qemu-img rebase command to sequentially apply each incremental backup on top of the full backup.

```bash
$ qemu-img rebase -b fullbackup.qcow2 -f qcow2 -u incremental1.qcow2
```

After applying the first incremental backup, subsequent incremental backups must be applied one by one, in the correct order. For example, apply incremental2.qcow2 on top of the image that already includes incremental1.qcow2, and continue this process for each subsequent incremental backup.
```bash
$ qemu-img rebase -b fullbackup.qcow2 -f qcow2 -u incremental2.qcow2
$ qemu-img rebase -b fullbackup.qcow2 -f qcow2 -u incremental3.qcow2
.
.
.
```

Since KubeVirt only supports raw disk images, the final step is to convert the merged QCOW2 image to a raw format.

```bash
$ qemu-img convert -f qcow2 -O raw fullbackup.qcow2 restored-raw.img
```

Once the raw restored image is created, you can store it in a PVC and use this PVC as the restored volume for the VM. You can use any population method like import or upload.


## Alternatives

The option to use [KEP-3314: CSI Changed Block Tracking](https://github.com/kubernetes/enhancements/tree/master/keps/sig-storage/3314-csi-changed-block-tracking), the native Kubernetes approach for CBT, was considered as an alternative to facilitating libvirt backups.
Advantages:
- Aligning with k8s ecosystem.
- Avoid the need to implement this complex feature ourselves.
- Backup vendors already use CSI API currently for full backups.

Disadvantages:
- This KEP is still in the early stages of implementation and will take considerable time to mature before reaching a stable v1 version and it is not under our control.
- Beyond waiting for the Kubernetes API to mature, each storage provider must implement the optional SnapshotMetadata API as outlined in the KEP. Since this is optional, not all providers will support it, and adoption will be gradual, restricting the available options for users.
- When using Kubernetes VolumeSnapshot, the guest must be frozen during the snapshot of all the VM volumes. In contrast, libvirt uses a dedicated job to capture the disk state quickly, minimizing guest downtime.
- The API is limited to the design choices made by Kubernetes, offering little flexibility for adjustments or additions.

## Scalability

- QCOW2 overlay requires a minimum PVC size.
With certain storage providers, even when a small PVC size is requested, a larger volume may be provisioned based on the provider’s minimum volume size. When managing a large number of VMs, this behavior can lead to inefficient storage.
**This is a general limitation that also affects VM state PVCs.**

- Managing a large number of checkpoints.
If too many checkpoints accumulate, the `CheckpointsList` CR can become large and difficult to manage. To keep it maintainable, it is recommended that users periodically delete old checkpoints. Realistically, once a checkpoint has been successfully backed up from, it is no longer needed and can be deleted.

## Update/Rollback Compatibility

Since the new feature allows users to enable or disable it, upgrades will not pose any issues. Users must opt in by setting the changedBlockTracking to true. The rollback will not be a problem either, as it is essentially the same as setting the changedBlockTracking to false, which will be the default value.

## Functional Testing Approach

Testing should check data consistency before and after add and remove of the QCOW2 overlay.
Data consistency of incremental backup.
Data consistency after VM restart.
Check failure scenario where incremental backup cannot be done and in such case full backup should be required.

## Implementation Phases

- Add/remove the qcow2 overlay
- Subresource to initiate backup (full and incremental) including Libvirt wrapper backup functions.
- New VirtualMachineBackup CR + controller for backups - online backup only
- New VirtualMachineCheckpointsList CR, Handling restart of VM and redefinition of checkpoints.
- Handle VM failure where bitmap is corrupted - next backup needs to be full.
- Offline backup
- API to allow to pull the backup over network.

* Live qcow2 overlay addition - depends on [https://issues.redhat.com/browse/RHEL-80680](https://issues.redhat.com/browse/RHEL-80680)


## Feature lifecycle Phases

### Alpha

IncrementalBackup FeatureGate. Users will have to opt in.

### Beta

After several releases, when we are confident that the feature is working as expected, move to beta.

### GA

GA once the feature has been running in production without issue.
