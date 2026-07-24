# VEP 401: Offline Incremental Backup

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal extends KubeVirt's Changed Block Tracking (CBT) backup capabilities to support stopped Virtual Machines, using persisted QCOW2 dirty bitmaps exposed via `qemu-nbd`.

## Motivation

The current CBT implementation (VEP #25) supports incremental backups only for running VMs. When a VM stops, the VMI is deleted, the libvirt domain no longer exists, and the only backup option is a full copy. For a 1TB disk with 5% daily change, this means transferring the full terabyte rather than just the 50GB delta.

oVirt solves this using direct `qemu-nbd --bitmap` manipulation, but its design assumes bare-metal with root access. KubeVirt needs a Kubernetes-native adaptation that runs in non-privileged containers.

## Goals

- Enable incremental backups for stopped VMs using persisted QCOW2 dirty bitmaps.
- Prevent data corruption from VM start/delete/migration during backup.
- Maintain non-privileged security model.
- Fall back to full backup if no checkpoint is available.

## Non Goals

- RAW disk format support (dirty bitmaps are QCOW2-specific).
- Automatic VM stop/start orchestration.

## Definition of Users

* Backup vendors - Primary API consumers
* Cluster Admins - Configure backup infrastructure
* VM owners - Create backups of stopped VMs

## User Stories

* As a KubeVirt user, I want to back up my stopped VM incrementally so that I avoid full copies during maintenance windows.
* As a KubeVirt admin managing many VMs, I want incremental backups of stopped VMs so that I reduce nightly backup time and storage consumption.
* As a backup vendor, I want to use the same VirtualMachineBackup API for both online and offline incremental backups.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Overview

When an offline backup is requested, the backup controller first verifies the VM is stopped and that no stale virt-launcher pods remain. Staleness is determined by listing pods with the `kubevirt.io/domain` label matching the source VM and checking for any pod not in a terminal phase (`Succeeded` or `Failed`); if such a pod exists, the backup is failed immediately. If another backup is already in progress for this VM, the controller also fails the new backup immediately with a descriptive reason, preventing indefinite hangs.

Once validated, the backup controller creates a VirtualMachineExport. The export controller spins up a pod that mounts the state PVC and all data volumes read-only, discovers the QCOW2 overlay chain, validates existing bitmaps, starts `qemu-nbd` per disk, and serves the dirty extent map and raw data over HTTP using the existing Go NBD client. While the backup is in progress, the VMI controller blocks VM start.

On completion, the intent to create the new checkpoint bitmap and remove the old one is recorded in the backup CR, and the backup transitions to `Completed` immediately, unblocking the VM. The backup controller deletes the export pod as soon as the backup reaches a terminal state (`Succeeded` or `Failed`) to release PVC mounts promptly. When the VM eventually starts, the VMI controller materializes these pending bitmaps atomically via libvirt QMP before the guest resumes writing.

Offline mode is detected automatically, if no VMI exists for the backup's source VM (or the VMI is in a terminal phase), the backup proceeds in offline mode.

### VM start prevention & Deletion handling

Before creating a virt-launcher pod, the VMI controller performs a lookup of the VMBackup/VMBackupTracker CRs in the namespace. If any CR references the same VM, has offline status and is in progress, the controller sets a `BackupInProgress` condition on the VMI and returns without creating the pod. The VMI stays `Pending` until the backup CR leaves `InProgress`. This follows the same pattern used for migration gating via `ActiveMigrationExistsForVMI()`.

If during a short window of time a VMI appears for the source VM while the offline backup is `InProgress`, the backup controller yields priority to the VM start, it deletes the export pod and marks the backup `Failed` with reason `VMStarted`.

If the source VM is deleted during the backup, the backup controller detects this via its informer watch on the VM object and aborts the process gracefully by deleting the export pod and marking the backup `Failed` with reason `VMDeleted`.

### Backup deadline

To handle stuck backups, the backup controller uses the existing `spec.ttlDuration` field as the maximum allowed duration for an offline backup. If the TTL expires before the backup completes, the controller deletes the export pod and marks the backup `Failed` with reason `DeadlineExceeded`. Since the VMI controller gates only on `status.phase == InProgress`, failing the backup is sufficient to unblock the VM.

### NBD setup (export pod)

The export pod should mount the state PVC and all VM data volumes read-only, plus an emptyDir at `/sockets` for NBD Unix sockets.

The volumes must be mounted at the same paths used by virt-launcher. If the data volume is not reachable at that path, `qemu-nbd` will fail to open the overlay. Since the VMI does not exist for stopped VMs, the export binary cannot rely on VMI metadata to locate overlays. Instead, on startup it discovers overlay files matching the naming convention on the state PVC and runs `qemu-img info --backing-chain --output=json` on each to verify the data-file chain is intact. If a data-file is missing or unreadable, that disk is marked `unavailable` in the export metadata and the remaining disks proceed normally.

For each healthy disk, the export starts something like:
```bash
qemu-nbd --read-only --persistent --shared=8 \
         --socket=/sockets/${diskname}.sock \
         --bitmap=${CHECKPOINT_NAME} \
         --format=qcow2 \
         ${overlay}
```

### Checkpoint and bitmap management

An offline incremental backup must adhere to the same checkpoint semantics as the online (libvirt) path: validate existing bitmaps, serve their dirty extents, and ensure the backup chain is maintained for subsequent backups.

On startup, the export pod validates the bitmaps referenced by the `VirtualMachineBackupTracker` on each disk overlay using `qemu-img info --output=json` and inspecting each bitmap's flags field. If any bitmap is corrupted (for example, due to a prior VM crash), the backup falls back to full mode for the affected disk and the tracker is updated accordingly.

The export pod does not create or remove bitmaps on disk. All volumes are mounted read-only, and `qemu-nbd` serves data in read-only mode. Instead, on successful completion, the backup controller records the intent to create the new checkpoint bitmap and remove the old one in the backup CR status. This intent includes the checkpoint name, the list of disks, and the bitmap to remove.

When the VM eventually starts, the VMI controller processes pending intents from the backup CRs as part of the existing checkpoint redefinition path. The VMI controller applies these intents after the libvirt domain is defined but strictly before `virDomainResume`, ensuring no guest writes can occur before the bitmaps are in place. It uses libvirt's QMP `transaction` command to add the new bitmap and remove the old one atomically across all disks in a single operation. The intent processing is idempotent: if a bitmap already exists (because a prior attempt succeeded before the CR was updated), creation is skipped; if the bitmap to remove is already absent, removal is skipped. If the VM crashes during startup before the transaction completes, the intent remains in the CR and is retried on the next start.

This deferred approach works because no guest writes occur while the VM is stopped. The new checkpoint bitmap, whether created during the backup or at VM start, would be empty in either case, its purpose is solely to anchor the backup chain so that the next backup can determine what has changed since this point. Deferring the bitmap creation to VM start is therefore lossless and avoids the need for application-level crash-consistency logic in the export pod.

If a second offline backup is requested before the VM has started (and the previous intent is still pending), the backup controller rejects the request with a clear condition, since the previous checkpoint has not yet been materialized on disk.

### Pruning and cleanup

Bitmap creation and removal are handled atomically by libvirt QMP when the VM starts, as described above. The deferred intent removes only the specific bitmap it replaces, not all bitmaps on disk. Other bitmaps retained by the `VirtualMachineBackupTracker` (for example, for multi-checkpoint retention as proposed in VEP 25.1) are left untouched.

Orphaned bitmaps (from prior VM crashes or abnormal shutdowns) are detected during the checkpoint redefinition process on VM start. A bitmap is considered orphaned only if it is not referenced by any entry in the tracker's checkpoint list. Bitmaps that are tracked but belong to older retained checkpoints are not removed.

If a VM is deleted after a completed offline backup but before the pending intent is materialized, standard Kubernetes garbage collection via OwnerReferences cleans up the associated backup CRs and their pending intents. Since the underlying PVCs are typically deleted with the VM, the unmaterialized bitmaps are removed along with the disk images and no orphaned state remains.

Checkpoint retention policy, such as maintaining multiple checkpoints for branching backup chains, is outside the scope of this VEP and is addressed by VEP 25.1.

### Known limitations

Only one offline incremental backup can be performed between VM starts. Since the VM is stopped, no guest writes occur after an offline backup completes. A second offline incremental backup would produce an empty delta and serve no purpose. The backup controller rejects such requests while a pending intent exists. This also avoids the technical constraint that the deferred bitmap has not been materialized on disk yet, so there is no on-disk bitmap for a subsequent backup to read dirty extents from. If a full (non-incremental) backup is needed while the VM remains stopped, it can proceed independently of pending intents.

### HTTP endpoints

Rather than spawning external processes per request, the export server maintains persistent NBD client connections to the local `qemu-nbd` processes using the go nbd client, one connection per disk. This is consistent with how the existing export server serves data.

Two endpoints are registered per disk:

Method | Endpoint | Description
-- | -- | --
GET | /exports/{disk}/map | Returns dirty extents JSON. If no checkpoint bitmap exists, returns all allocated blocks (full backup fallback).
GET | /exports/{disk}/data?offset=X&length=Y | Returns raw bytes via `Read()`.

### Push and pull modes

For online backups, push mode hotplugs the target PVC into the running VM and virt-handler writes data directly to it, there's no export pod involved. This path is not available for offline backups because there is no VMI to hotplug into.

In offline mode, the export pod is always created and `qemu-nbd` is always started. The difference is in how the backup data reaches the consumer:

- **Pull mode**: the export pod serves the HTTP endpoints described above. The backup vendor pulls data and extent maps over HTTP. The backup stays `InProgress` until the TTL expires or the vendor signals completion, typically by patching a status field on the `VirtualMachineBackup` CR or by deleting it.
- **Push mode**: The export pod additionally mounts the target PVC defined in spec.pvcName. Instead of serving data over HTTP, the export process writes the required backup data directly to the mounted PVC, avoiding the HTTP layer entirely. The internal data extraction will leverage the same underlying setup used for pull mode, ensuring the output format written to the target PVC aligns with existing KubeVirt push mechanisms. While this keeps the data path local to the pod, it requires the export pod to manage write access and capacity constraints on the target PVC. If this complexity proves problematic during the initial implementation, push mode support for offline VMs may be deferred to a subsequent release without affecting the core pull mode functionality.

### Storage access modes

State PVCs may use either RWO or RWX access modes, and the backup design must handle both. On RWO storage, Kubernetes enforces single-node attachment and QEMU's default file locking provides a secondary defense; note that the export pod must schedule on the same node where the RWO PVC was last attached, which means it will be unschedulable if that node is unavailable. On RWX storage, QEMU file locking is unreliable on NFS and distributed filesystems, and KubeVirt does not explicitly configure it. Since the export pod mounts all volumes read-only, a race that allows a virt-launcher to start concurrently shouldn't result in QCOW2 corruption from the export pod, the worst case is a read of stale bitmap data. The bidirectional controller checks (VMI controller blocks start during backup, backup controller fails the backup if a VMI appears) mitigate this window. For Beta, we can look into using an admission webhook to block the VM start reque
st entirely.

## API Examples

No new CRs are introduced. Offline backup reuses the existing VirtualMachineBackup API from VEP #25, and offline mode is detected automatically based on VMI absence.

## Alternatives

### Read-write export pod with immediate bitmap management

Mount the state PVC read-write in the export pod and use `qemu-img bitmap --add` / `--remove` to create and prune checkpoint bitmaps during the backup itself, rather than deferring to VM start. We rejected this because `qemu-img bitmap` operates per-disk with no transaction envelope, so a pod crash mid-way leaves some disks with orphaned bitmaps and others without, requiring custom rollback. On shared storage where QEMU file locking is unreliable, a read-write export pod racing with a virt-launcher risks QCOW2 corruption rather than just a stale read. Since the VM is stopped, no guest writes occur during the backup, so the new checkpoint bitmap would be empty regardless of when it is created, deferring to VM start is lossless and lets libvirt handle multi-disk atomicity natively via QMP.

### Kubernetes Lease for VM start prevention

The backup controller would acquire a `coordination.k8s.io/v1` Lease; the VMI controller would check for it before creating the virt-launcher pod. We rejected this because Leases are not used for operational gating anywhere in KubeVirt (only for leader election). It would introduce new RBAC grants, a renewal goroutine, and a pattern inconsistent with the informer-cached CR lookups used everywhere else.

### Libvirt paused domain

Start the libvirt domain in paused state to access bitmaps. We rejected this because it requires a VMI to exist (defeating the purpose of offline backup) and consumes memory and CPU for a paused guest.

### VM finalizer to prevent deletion

Add a finalizer to the VM object to block deletion during backup. We rejected this because VirtualMachineExport and online VirtualMachineBackup both handle source deletion via graceful abort. A finalizer risks permanently stuck VMs if the backup controller becomes unavailable.

## Update/Rollback Compatibility

On upgrade, offline backup becomes available immediately behind a feature gate. On rollback, in-progress offline backups will fail, but no persistent state (Leases, VM finalizers) is left behind that could block VM operations. Checkpoints created by online CBT are compatible with offline CBT because dirty bitmaps are QCOW2 format-level metadata, not a libvirt artifact, so `qemu-nbd` reads them directly from the image.

## Functional Testing Approach

Testing should verify data consistency of offline incremental backups end-to-end, and cover the key edge cases:

- Happy path: create an online backup with checkpoint, write additional data, stop the VM, perform an offline incremental backup, verify the dirty extent map reflects only the new writes, and confirm the VM starts successfully after completion.
- VM start is blocked during backup and unblocks after completion.
- Fallback to full backup when no checkpoint exists.
- VM deletion during backup: the backup is aborted gracefully and the VM deletion proceeds.
- Backup deadline exceeded: backup fails, VM can start.

## Implementation Phases

### Alpha (v1.10)

- Offline backup detection and pre-flight validation in backup controller.
- VMI controller `BackupInProgress` check and condition.
- Export pod with overlay chain discovery, bitmap validation, `qemu-nbd`, Go NBD client HTTP handlers.
- VM deletion handling (graceful abort).
- Deferred checkpoint intent recording and VMI controller materialization via QMP on VM start.
- Pull and push mode.
- Feature gate: `OfflineIncrementalBackup` (disabled by default). Requires the `IncrementalBackup` gate from VEP #25 to be enabled too.

### Beta (TBD)

- Feature gate enabled by default.
- No data corruption incidents reported during Alpha.
- E2E tests passing in CI for at least one release cycle.

### GA (TBD)

- Feature gate removal.
- Feature has been stable in Beta for at least one release cycle.

## References

- VEP #25: [Storage agnostic incremental backup using qemu](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/incremental-backup.md)
- VEP #90: [Utility Volumes](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/utility-volumes.md)
- oVirt Backup API: https://www.ovirt.org/develop/release-management/features/storage/incremental-backup.html
