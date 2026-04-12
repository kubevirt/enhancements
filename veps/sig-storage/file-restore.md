# VEP #169: File-Level Restore

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.
- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
Proposal to support virtual machine file-level restore, applying to specific files or directories. This will give users and data protection partners the ability to restore files or a subset of user data into a virtual machine.

## Motivation
Currently, you need to restore an entire VM or a specific volume separately, and retrieve the data from it. This enhancement will allow the user to restore the small set of files needed.

## Goals
- Provide a declarative API allowing users to partially restore VM data.
- A guest-OS-agnostic solution, supporting Windows (NTFS) in addition to Linux.
- A backup-vendor-agnostic solution.

## Non Goals
- Creating backup PVCs or volume snapshots, indexing their content, browsing them offline to locate specific items, and making them accessible in the appropriate namespace, are all the responsibility of the backup vendor.
- Backup vendors are encouraged to provide customized guest-side file restore helpers. Reference working helpers will be provided.

## Definition of Users
* VM owners
* Cluster Admins
* Backup vendors

## User Stories
* As a KubeVirt user, I would like to partially restore my VM data with specific files or directories from a block-level backup of the VM persistent storage created by a backup vendor.
* As a backup vendor, I would like to offer file-level restore capability similar to vSphere.
* As a backup vendor, I would like to offer a declarative KubeVirt API for file-level restore.
* As a user of a Linux VM, I would like to restore a file or directory on a LUKS-encrypted volume in my running VM.
* As a user of a Windows VM, I would like to restore a file or directory on a BitLocker-encrypted filesystem in my running VM.

## References

### Existing File-Level Restore Solutions
Several backup vendors already offer their own file-level restore solutions; however, when integrating with KubeVirt they encounter some gaps and pain points, which our KubeVirt-integrated, guest-cooperative solution addresses:
* Simplifying guest OS credentials handling, built for confidential guests.
* Supporting encrypted volumes (LUKS, BitLocker).
* Managing filesystem-specific metadata (ACLs, xattrs, etc.).
Furthermore, our approach provides a simple declarative k8s API, supports both Linux and Windows guests, and enables both automated and manual FLR. KubeVirt will offer an integrated, open FLR solution for users who need FLR from a specific backup PVC or VolumeSnapshot, without depending on a backup vendor solution.

### VMware File-Level Restore
For VMware File-Level Restore, many backup vendors (Veeam, Commvault, Veritas, Dell PowerProtect, etc.) use a guest agent (a persistent one, or an ephemeral one pushed via `VMware Tools`). VMware [vSphere Guest Operations API](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere-sdks-tools/7-0/web-services-sdk-programming-guide/virtual-machine-guest-operations/running-guest-os-operations.html) allows interaction with files inside the guest through the hypervisor without requiring a guest network connection. Its capabilities include `InitiateFileTransferToGuest`, `MakeDirectoryGuest`, `ListGuestFiles`, etc. It is based on the `VMware Tools` control channel. For authentication it requires valid guest OS credentials passed via the API for `root` or a user with specific `sudo` permissions for file operations.

### OADP VM File Restore
The [OADP VM File Restore](https://github.com/migtools/oadp-vm-file-restore?tab=readme-ov-file#oadp-vm-file-restore) project is Velero-oriented, while we suggest a vendor-agnostic approach. Their external pod file-serving architecture is not designed to support encrypted backup volumes; for those, we suggest hotplugging the backup PVCs to the VM so we can mount and access their filesystems. However, for non-encrypted volumes, we can use OADP file-serving to browse backups in a temporary namespace before applying our file restore.

## Repos
[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design
### Guest-Level Cooperation
The solution is designed to support file-level restore for both Linux and Windows guests. On Windows, NTFS filesystems require guest cooperation to properly handle filesystem-specific features, including access control lists (ACLs), alternate data streams (ADS), hard links and junction points, and file metadata (attributes and timestamps).

Additionally, encrypted volumes (such as BitLocker for Windows) require guest-level access to ensure files are properly restored from encrypted backup PVC or snapshot.

### Implementation Approach
* We introduce the `VirtualMachineFileRestore` CRD, which is namespace-scoped and created in the VirtualMachine namespace. Access control is enforced through the relevant RBAC rules.
* A hotplug volume will be used to attach a backup PVC or a VolumeSnapshot-restored PVC to the VM. The `DeclarativeHotplugVolumes` feature gate must be enabled.
* SSH over the network will be used for specific command execution on the guest.
* The guest OS-specific operations will be wrapped in a restricted file-restore guest helper script or binary. We will provide reference scripts, but backup vendors are encouraged to implement their own helpers.
* Encrypted volume key management is in the responsibility of the guest file restore helper, looking for them in a specific location (e.g. next to the helper, or in the VM encrypted volume that we restore to from its encrypted backup PVC/VolumeSnapshot).

### File-Level Restore
virt-controller:
* Watch `VirtualMachineFileRestore`
* If the source is a VolumeSnapshot, restore it to a PVC in the VM namespace
* If the source is a PVC in another namespace, clone it to a PVC in the VM namespace
* Hotplug the volume (read-only)
* Label `VirtualMachineFileRestore` with the target node (e.g. `kubevirt.io/target-node=node01`).
* When the restore completes or the CR is deleted:
  * Unplug the volume
  * Delete the PVC if temporarily created

virt-handler:
* Watch `VirtualMachineFileRestore` using a filtered informer to process only restores to local VMIs.
* SSH into the guest and execute the restore helper with the relevant arguments.
* Update status upon completion (whether succeeded or failed) or timeout.

Guest file restore helper:
* Unlock the volume if encrypted (key file in target volume)
* Mount the backup filesystem (read-only)
* If automated restore:
  * Restore the specified files or directories
  * Unmount the backup filesystem

## API Examples

### Directory Restore from PVC
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  target:
    apiGroup: kubevirt.io
    kind: VirtualMachine
    name: "fedora"
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

### Directory Restore from Volume Snapshot
Similar to PVC restore, except the source is a VolumeSnapshot.
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  target:
    apiGroup: kubevirt.io
    kind: VirtualMachine
    name: "fedora"
  source:
    snapshot:
      name: snap1
  sourcePath: /home/donald
```

### Manual Restore
When `sourcePath` is not provided, we allow manual restore from the mounted filesystem of the hotplugged backup volume using any preferred guest tool. When done, the user deletes the CR so we unmount the filesystem and unplug the volume.
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  target:
    apiGroup: kubevirt.io
    kind: VirtualMachine
    name: "fedora"
  source:
    snapshot:
      name: snap1
```

### Direct restore from external storage (TBD)
For some backup vendors, backup data is not stored on the same cluster and is often moved to external storage like S3, requiring a two-step copy process for restoration, as we expect the backup data on an accessible PVC or volume snapshot within the cluster. To solve the double-copy process, we should support direct file transfer:
```yaml
apiVersion: filerestore.kubevirt.io/v1alpha1
kind: VirtualMachineFileRestore
metadata:
  name: filerestore1
  namespace: ns1
spec:
  target:
    apiGroup: kubevirt.io
    kind: VirtualMachine
    name: "fedora"
  source:
    remote:
      name: s3_backup
      bucket: buck1
  sourcePath: /home/donald # optional
  #targetPath: /home/duck  # optional
```
This reference example assumes the guest helper uses `rclone` and S3 remote was already configured via `rclone config`.

## Guest command execution
`virt-handler` connects to the guest `sshd` as restore-user, a user restricted to `sudo` execution of only the specifically-named trusted file-restore helper (via `.ssh/authorized_keys` and `/etc/sudoers.d`). `virt-operator` generates an SSH key pair and stores it in a Secret. The public key can be added to the guest either manually or propagated by `virt-controller` patching the VM `accessCredentials.sshPublicKey` with `propagationMethod: qemuGuestAgent` so it is injected via qemu-guest-agent. `virt-handler` mounts the private key Secret at `/etc/kubevirt/ssh/id_rsa` and uses it for file-restore SSH connections. If we find that private key compromise is an issue, we can create an ephemeral key pair per SSH connection.

### Guest file restore helper CLI
For covering the mentioned file restore flows, a guest helper CLI should support:
* Restore (automatic):\
  `filerestore.sh restore --serial <SERIAL> --mount-path <MNT_PATH> --source-path <SRC_PATH>`
* Restore (manual - mount only):\
  `filerestore.sh restore --serial <SERIAL> --mount-path <MNT_PATH>`
* Cleanup (unmount and remove mount point):\
  `filerestore.sh cleanup --mount-path <MNT_PATH>`

`SERIAL` is the string that identifies the hotplugged disk inside the guest.\
`MNT_PATH` is the mount path, named specifically for identifying the backup PVC or snapshot.\
`SRC_PATH` is the file or directory path to be restored.

## Alternatives
### SSH over VSOCK
We considered using SSH over VSOCK, which allows guest command execution for VMs without networking. Since this is a relatively rare case, we decided to start with SSH over the network and support SSH over VSOCK later if there is demand. To allow it, the guest SSH daemon needs to be configured to listen for incoming connections on a VSOCK. On Linux this can be done with `systemd-ssh-generator`, `socat`, etc. Windows supports VSOCK via `virtio-win`, so we need to install the viosock driver and viosock-tcp bridge service.

### Hotplug utility volume
A simpler solution that has been suggested is to hotplug a utility volume in `virt-launcher` with the backed-up files on a filesystem PVC, instead of inside a disk image on a PVC. By running an `rsync` client in `virt-handler`, we can copy files from the utility volume to the guest over `ssh+vsock` (using `ProxyCommand`). One problem with this direction is that backup PVCs are usually created and maintained by backup vendors, so we have no control over them; they can use block volume mode, and they are expected to be encrypted when the VM volumes are encrypted, so we can access their filesystem outside the guest only if we have the encryption passphrase, which is unlikely. We also need to support Windows guest NTFS, which is not fully supported by Linux utilities such as `rsync` and `scp`.

### qemu-agent-command
We initially considered using qemu-agent-command for the file-level operations. However, direct use of the qemu-agent-command APIs is strongly discouraged by libvirt. QGA commands such as guest-file-*, guest-exec-*, etc., are considered host-admin backdoors and will be blocked for confidential guests due to security risks. RHEL builds already disable these commands. QGA is also not designed for efficient large-scale data transfer and lacks support for file permissions, xattrs, symlinks, and hard links, making it unsuitable for robust restore.

## Scalability
We use rsync or similar in the guest for file-level restore. File transfer is memory- and I/O-intensive. It is also CPU-intensive due to file comparison and checksumming. However, we assume file-level restores are relatively rare operations that transfer only deltas and are not performed on many VMs at once.

## Update/Rollback Compatibility
- The backup volume is temporarily hotplugged to the VMI only for as long as needed
- No changes to existing APIs or objects
- No changes to existing VM/VMI specs

## Functional Testing Approach
A comprehensive test suite that checks the guest filesystem state is important for this feature. The following cases should be covered:
* Automated directory restore from a PVC source to Linux guest — verify files and metadata match the source.
* Automated directory restore from a VolumeSnapshot source — verify the snapshot is restored to a temporary PVC, files are restored, and the temporary PVC is cleaned up.
* Restore from a PVC in a different namespace — verify the PVC is cloned to the VM namespace and cleaned up after restore.
* Restore of a LUKS-encrypted volume — verify the volume is unlocked and the files are restored.
* Manual restore — verify the backup filesystem is mounted read-only in the guest and remains available until the CR is deleted, then is unmounted and the volume is unplugged.
* Windows guest restore — verify files and NTFS-specific metadata are restored correctly.
* Missing guest helper — verify the VirtualMachineFileRestore reports a Failed phase with a clear error condition.

## Implementation History
...

## Graduation Requirements

### Alpha
The `VirtualMachineFileRestore` feature gate will enable file-level restore.

### Beta
- Adoption by 2 backup and recovery applications or vendors.
- After one or two releases, when we are confident that the feature is working as expected, move to beta.

### GA
Move to GA once the feature has been running in production without issues. Remove feature gates.
