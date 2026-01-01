# VEP #169: File-Level Restore

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.
- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
Proposal to support virtual machine file-level restore, applying on specific files or directories. This will give users and data protection partners the ability to restore files or subset of user data into a virtual machine.

## Motivation
Currently, you need to restore an entire VM or a specific volume separately, and retrieve the data from it. This enhancement will allow the user to restore the small set of files needed.

## Goals
- Provide a declarative API allowing user to partially restore VM data.
- Guest OS agnostic solution, supporting Windows (NTFS) in addition to Linux.
- Backup vendor agnostic solution.

## Non Goals
- Creating backup PVCs or volume snapshots, indexing their content, browsing them offline to locate specific items, and making them accessible in the appropriate namespace, are all the responsibility of the backup vendor.
- Although reference guest-side file restore helpers will be provided, backup vendors are encouraged to provide their own customized and optimized helpers.

## Definition of Users
* VM owners
* Cluster Admins
* Backup vendors

## User Stories
* As a KubeVirt user, I would like to partially restore my VM data with specific files or directories, from block level backup of the VM persistent storage created by backup vendor.
* As a backup vendor, I would like to offer file level restore capability similar to vSphere.
* As a backup vendor, I would like to offer a declarative KubeVirt API for file level restore.
* As a user of a Linux VM, I would like to restore file or directory on a LUKS encrypted volume in my running VM.
* As a user of a Windows VM, I would like to restore file or directory on a BitLocker encrypted file system in my running VM.

## References

### Existing File-Level Restore Solutions
Several backup vendors already offer their own file-level restore solutions; however, when integrating with KubeVirt they encounter some gaps and pain points, which our KubeVirt-integrated, guest-cooperative solution addresses:
* Simplifying guest OS credentials handling, built for confidential guests.
* Supporting encrypted volumes (LUKS, BitLocker).
* Managing filesystem-specific metadata (ACLs, xattrs, etc.).
Furthermore, our approach provides a simple declarative k8s API, supports both Linux and Windows guests, and enables both automated and manual FLR. KubeVirt will offer an integrated open FLR solution also for users who need FLR from a specific backup PVC or VolumeSnapshot, without dependency on a backup vendor solution.

### VMware File-Level Restore
For VMware File-Level Restore, many backup vendors (Veeam, Commvault, Veritas, Dell PowerProtect etc.) use a guest agent (persistent one, or ephemeral one pushed via the `VMware Tools`). VMware [vSphere Guest Operations API](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere-sdks-tools/7-0/web-services-sdk-programming-guide/virtual-machine-guest-operations/running-guest-os-operations.html) allows interaction with files inside the guest through the hypervisor without requiring guest network connection. Its capabilities include `InitiateFileTransferToGuest`, `MakeDirectoryGuest`, `ListGuestFiles` etc. It is based on the `VMware Tools` control channel. For authentication it requires valid guest OS credentials passed via the API for `root` or a user with specific `sudo` permissions for file operations.

### OADP VM File Restore
The [OADP VM File Restore](https://github.com/migtools/oadp-vm-file-restore?tab=readme-ov-file#oadp-vm-file-restore) project is Velero-oriented, while we suggest a vendor-agnostic one. Their external pod file serving architecture is not designed to support encrypted backup volumes, where we suggest hotplugging the backup PVCs to the VM so we can mount and access their file system. However, for non-encrypted volumes, we can use OADP file-serving for browsing backups in their temporary namespace, before applying our file restore.

## Repos
[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design
### Guest-Level Cooperation
The solution is designed to support file-level restore for both Linux and Windows guests. Windows NTFS filesystems require guest cooperation to properly handle filesystem-specific features, including: access control lists (ACL), alternate data streams (ADS), hard links and junction points, file metadata (attributes and timestamps).

Additionally, encrypted volumes (such as BitLocker for Windows) require guest-level access to ensure files are backed up in their encrypted state and properly restored.

### Implementation Approach
* Hotplug volume will be used to attach to the VM a backup PVC or a VolumeSnapshot restored PVC
* A helper VirtualMachineGuestCommand CRD will be created by the controller for executing allowed guest commands using the specified transport.
* SSH over VSOCK will be the reference transport, while the API will allow adding support for other transports as well (SSH etc.).
* The guest OS-specific operations will be wrapped in a restricted file restore guest helper script or binary. We will provide reference scripts, but helper can be implemented by a backup vendor as well.

### File-Level Restore
When VirtualMachineFileRestore is applied:
* If VolumeSnapShot source, restore it to PVC in the VM namespace
* If PVC source in another namespace, clone it to PVC in the VM namespace
* Hotplug the volume (read-only)
* Create appropriate VirtualMachineGuestCommand
* Guest file restore helper:
  * Unlock the volume if encrypted (key file in target volume)
  * Mount the backup filesystem (read-only)
  * If automated restore:
    * Restore the specified files or directories
    * Unmount the backup filesystem
* Unplug the volume
* Delete the PVC if temporarily created

## API Examples
In order to use this feature both the `DeclarativeHotplugVolumes` and `VSOCK` feature gates must be enabled. The new CRDs are namespace-scoped, and created in the VirtualMachine namespace. For access control, `VirtualMachineFileRestore` will be provided the relevant RBACs.

### Restore
#### Directory Restore from PVC
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  vmiName: fedora
  source:
    pvc:
      name: filebackup1
      #namespace: ns2
  sourcePath: /home/donald # optional
  #sourcePartition: 1      # optional
  #targetPath: /home/duck  # optional
status:
  conditions:
  - type: Completed
    status: "True"
    reason: RestoreCompleted
    message: "All 123 files restored successfully"
    lastTransitionTime: "2026-01-01T01:23:45Z"
  - type: Progressing
    status: "False"
    reason: Finished
    lastTransitionTime: "2026-01-01T01:23:45Z"
```

#### Directory Restore from Volume Snapshot
Similar to PVC except the source
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  vmiName: fedora
  source:
    snapshot:
      name: snap1
  sourcePath: /home/donald
```

#### Manual Restore
When `sourcePath` is not provided, we allow manual restore from the hotplugged backup volume mounted filesystem using any preferred guest tool. When done, the user deletes the CR, so we unmount the filesystem and unplug the volume.
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  vmiName: fedora
  source:
    snapshot:
      name: snap1
```

### Guest Command
To perform the actual guest operations the controller creates a VirtualMachineGuestCommand, which executes the required commands inside the guest via the specified transport (e.g. SSH over VSOCK). We currently do not want to support running arbitrary commands on the guest, so only the `kubevirt-controller` service account has RBAC for creating VirtualMachineGuestCommand. We will define an allow-list of possible acceptable commands. The controller will take the name of the command and its arguments (e.g. paths and names of files), and execute only the acceptable commands. Arguments will be sanitized as well.

```yaml
apiVersion: guestcommand.kubevirt.io/v1alpha1
kind: VirtualMachineGuestCommand
metadata:
  name: cmd1
  namespace: ns1
spec:
  vmiName: fedora
  config: myconfig
  command:
  - filerestore --serial "fedora-filebackup1-backup" --mount-path "/backup" --source-path "/home/donald"
status:
  conditions:
  - type: Completed
    status: "False"
    reason: MissingBinary
    message: "Required binary 'filerestore' not found in guest."
    lastTransitionTime: "2026-01-01T01:23:45Z"
  - type: Failed
    status: "True"
    reason: MissingBinary
    message: "Required binary 'filerestore' not found in guest."
    lastTransitionTime: "2026-01-01T01:23:45Z"
---
apiVersion: guestcommand.kubevirt.io/v1alpha1
kind: VirtualMachineGuestCommandConfig
metadata:
  name: myconfig
  namespace: ns1
config
  transport: vsock
  user: filerestore
  port: 22
  sshKeySecret:
    secretName: my-secret # alternative: `qemuGuestAgent: {}` for temporary secret key injected via qemu-guest-agent
---
kind: Secret
apiVersion: v1
metadata:
  name: my-secret
  namespace: ns1
data:
  key: c3...
type: Opaque
```

## SSH over VSOCK
Guest SSHD needs to be configured to listen for incoming connections on a VSOCK. In Linux this can be done by `systemd-ssh-generator`, `socat` etc. Windows supports VSOCK via `virtio-win`, so we need to install viosock driver, viosock-tcp bridge service and OpenSSH server. `virt-handler` connects the guest `sshd` with restore-user, which is either a user with specific `sudo` permissions for file operations, or `root`. We need to decide if this restore-user is common or unique for each VM. We do not want to give unrestricted ssh access, so the private key is stored in a k8s Secret that only the `kubevirt-handler` service account has RBAC to access. To prevent arbitrary commands being executed inside the VM over the ssh connection we will only support execution of a specifically-named trusted file restore helper in the guest, doing the actual restore (`mount`, `rsync` etc), restricting the command-line, and allow only that helper in `.ssh/authorized_keys`.

## Alternatives

### Hotplug utility volume
A simpler solution suggested is to hotplug a utility volume in `virt-launcher` with the backed up files on a filesystem PVC, instead of inside a disk image on a PVC. Running `rsync` client in `virt-handler` we can copy files from the utility volume to the guest over `ssh+vsock` (using `ProxyCommand`). One problem with this direction is that the backup PVCs are usually created and maintained by backup vendors, so we have no control over them, they can be block volume mode, and they are expected to be encrypted in case the VM volumes are encrypted, so we can access their filesystem outside of the guest only if we have the encryption passphrase, which is unlikely to happen. We also need to support Windows guest NTFS, which is not fully supported by Linux utils such as `rsync`, `scp` etc.

### qemu-agent-command
We initially considered using qemu-agent-command for the file-level operations. However, qemu-agent-command APIs direct usage is strongly discouraged by Libvirt. QGA commands, such as guest-file-*, guest-exec-* etc. are considered host admin backdoors and will be blocked for confidential guests due to security risks. RHEL builds already disable these commands. QGA is also not designed for efficient large-scale data transfer and lacks support for file permissions, xattrs, symlinks, and hard links, making it unsuitable for robust restore.

## Scalability
We use rsync or similar in the guest for file level restore. File transfer is memory and I/O intensive. It is also CPU-intensive, due to file comparison and checksumming. However, we assume file level restores are relatively rare operations, transfer only deltas and not performed on many VMs at once.

## Update/Rollback Compatibility
- The backup volume is ephemerally hotplugged to the VMI just for the time needed
- No changes to existing APIs or objects
- No changes to existing VM/VMI specs

## Functional Testing Approach
A comprehensive test suite that checks the guest file system state is important for this feature. The following cases should be covered:
* Automated directory restore from a PVC source to Linux guest — verify files and metadata match the source.
* Automated directory restore from a VolumeSnapshot source — verify the snapshot is restored to a temporary PVC, files are restored, and the temporary PVC is cleaned up.
* Restore from a PVC in a different namespace — verify the PVC is cloned to the VM namespace and cleaned up after restore.
* Restore of a LUKS-encrypted volume — verify the volume is unlocked and the files are restored.
* Manual restore — verify the backup filesystem is mounted read-only in the guest and remains available until the CR is deleted, then unmounted and volume unplugged.
* Windows guest restore — verify files and NTFS-specific metadata are restored correctly.
* Missing guest helper — verify the VirtualMachineFileRestore reports a Failed phase with a clear error condition.

## Implementation History
...

## Graduation Requirements

### Alpha
There will be a `VirtualMachineFileRestore` feature gate, allowing file-level restore and `VirtualMachineGuestCommand` for guest commands execution.

### Beta
- Adoption by 2 backup and recovery applications or vendors.
- After one or two releases, when we are confident that the feature is working as expected, move to beta.

### GA
GA once the feature has been running in production without issues. Remove feature gates.
