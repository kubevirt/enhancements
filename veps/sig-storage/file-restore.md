# VEP #169: File-Level Backup and Restore

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.
- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
Proposal to support virtual machine file-level backup and restore, applying on specific files or directories. This will give users and data protection partners the ability to restore files or subset of user data into a virtual machine.

## Motivation
Currently, you need to restore an entire VM or a specific volume separately, and retrieve the data from it. This enhancment will allow the user restore the small set of files needed.

## Goals
- Provide a declarative API allowing user to partially backup and restore VM data.
- Guest OS agnostic solution, supporting Windows (NTFS) in addition to Linux.
- Backup vendor agnostic solution.

## Non Goals
- Backup file browsing is not supported, but may leverage OADP file serving infrastructure
- Parallel file backups/restores of the same VMs are not supported

## Definition of Users
* VM owners
* Cluster Admins
* Backup vendors

## User Stories
* As a KubeVirt user, I would like to partially restore my VM data with specific files or directories
* As a KubeVirt user, I would like to partially backup my VM data with specific files or directories
* As a backup vendor, I would like to offer file level restore capability similar to vSphere

## Repos
[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design
### Guest-Level Cooperation
The solution is designed to support file-level backup and restore for both Linux and Windows guests. Windows NTFS filesystems require guest cooperation to properly handle filesystem-specific features, including: access control lists (ACL), alternate data streams (ADS), hard links and junction points, file metadata (attributes and timestamps).

Additionally, encrypted volumes (such as BitLocker for Windows) require guest-level access to ensure files are backed up in their encrypted state and properly restored.

### Implementation Approach
* Backup and restore are independent operations, and restore can be supported even without the backup support
* Backup and restore are idempotent operations, so the same CRs can be applied whenever one wants to backup or restore
* Hotplug volume will be used to attach the VM a PVC for backup and restore, or VolumeSnapshot restored PVC for restore
* Backup and restore with guest ssh-able target will also be supported
* SSH over VSOCK will be used to execute guest operations (mount backup volume filesystem, rsync etc.) using a helper  VirtualMachineGuestCommand CRD
* Since we do not depend  on guest agent, our controler creating the VirtualMachineGuestCommand will execute a minimal set of OS-specific (Linux/Windows) commands
* After the specified files are copied, another pass(es) will follow to make sure no files changed during the operation, so we can timestamp it. Unlike fsfreeze this will not guarentee a complete file system consistency.

### File-Level Backup
When VirtualMachineFileBackup is applied:
* If the target is PVC
  * If the PVC does not exist, create it
  * Hotplug the volume
  * Using VirtualMachineGuestCommand
    * Format the volume if needed
    * Mount the backup filesystem
    * Backup the specified files or directories
    * Unmount the backup filesystem
  * Unplug the volume
* If remote target
  * Using VirtualMachineGuestCommand
    * Backup the specified files

### File-Level Restore
When VirtualMachineFileRestore is applied:
* If VolumeSnapShot source, restore it to PVC in the VM namespace
* If PVC source (or VolumeSnapShot restored PVC)
  * Hotplug the volume
  * Using VirtualMachineGuestCommand
    * Mount the backup filesystem
    * Restore the specified files or directories
    * Unmount the backup filesystem
  * Unplug the volume
* If remote source
  * Using VirtualMachineGuestCommand
    * Restore the specified files

## API Examples

For using this feature the HotplugVolumes and VSOCK feature gates must be enabled.

### Backup
#### Directory Backup to PVC
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileBackup
metadata:
  name: filebackup1
spec:
  vmiName: fedora
  sourcePath: /home/donald
  target:
    pvc:
      name: filebackup1 # optional
      size: 1Gi # optional
  #targetPath: /home/duck #optional
status:
  phase: InProgress
```
#### Directory Backup to Remote Host
Similar to PVC except the target
```yaml
  target:
    host:
      address: backup-01.us-east.internal # ip or name
```

### Restore
#### Directory Restore from PVC
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
spec:
  vmiName: fedora
  source:
    pvc:
      name: filebackup1
  sourcePath: /home/donald
  #targetPath: /home/duck #optional
status:
  phase: Succeeded
```
#### Directory Restore from Volume Snapshot
Similar to PVC except the source
```yaml
  source:
    snapshot:
      name: snap1
```
#### Directory Restore from Remote Host
Similar to PVC except the source
```yaml
  source:
    host:
      address: backup-01.us-east.internal # ip or name
```

### Guest Command
To perform the actual guest operations the controller will create a VirtualMachineGuestCommand, which will execute the required commands inside the guest via SSH over, for example:
```yaml
apiVersion: guestcommand.kubevirt.io/v1alpha1
kind: VirtualMachineGuestCommand
metadata:
  name: filebackup1-cmd
spec:
  command:
  - mkdir -p /backup && mount /dev/sdb /backup && rsync -avR /home/agilboa /backup/ && umount /backup && rm
    -rf /backup
  executionMethod: VSOCK
  runPolicy: Once
  timeout: 60
  vmiName: fedora
  vsockConfig:
    port: 22
    sshKeySecret:
      key: ssh-privatekey
      name: my-ssh-key-secret
    useTLS: false
    user: root
status:
  phase: Succeeded
  reason: SuccessfulExecution
  message: Command completed successfully
  lastExecutionTime: "2026-01-04T13:04:12Z"
  conditions: ...
  stderr: ...
  stdout: ...
```

## Alternatives
We initially considered using qemu-agent-command for the file-level operations. However, qemu-agent-command APIs direct usage is strongly discouraged by Libvirt. QGA commands, such as guest-file-*, guest-exec-* etc. are considered host admin backdoors and will be blocked for confidential guests due to security risks. RHEL builds already disable these commands. QGA is also not designed for efficient large-scale data transfer and lacks support for file permissions, xattrs, symlinks, and hard links, making it unsuitable for robust backup and restore.

## Scalability
We use rsync in the guest for file level backup and restore. rsync is CPU-intensive, due to compression, file comparison and checksumming. The file transfer is also memory and I/O intensive. However, we assume file level backup and restore are relatively rare operations, transfer only deltas and not performed on many VMs at once.

## Update/Rollback Compatibility
- The backup volume is ephemerally hotplugged to the VMI just for the time needed
- No changes to existing APIs or objects
- No changes to existing VM/VMI specs

## Functional Testing Approach
A comprenhensive test suite that checks the guest state is important for this feature. The following cases should be covered:
TBD

## Implementation History
...

## Graduation Requirements

### Alpha
There will be two relevent featuregates:
(TODO: consider using only a single feature gate)
1. `VirtualMachineFileRestore` - allowing file-level backup & restore
2. `VirtualMachineGuestCommand` - allowing guest commands execution via SSH over VSOCK

### Beta
After one or two releases, when we are confident that the feature is working as expected, move to beta.

### GA
GA once the feature has been running in production without issues. Remove featuregates.
