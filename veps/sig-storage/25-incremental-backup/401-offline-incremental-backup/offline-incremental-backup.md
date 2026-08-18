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

When an offline backup is requested, the backup controller verifies the VM is stopped and that no stale virt-launcher pods remain. A pod is stale if it carries the `kubevirt.io/domain` label matching the source VM and is not in a terminal phase (`Succeeded` or `Failed`). If a stale pod exists, or another backup is already in progress for the same VM, the backup is failed immediately.

Once validated, the backup controller creates a VirtualMachineExport. The export controller starts a pod that mounts the state PVC and all data volumes read-write, discovers the QCOW2 overlay chain, validates existing bitmaps, creates the new checkpoint bitmap on each disk, starts `qemu-nbd` per disk, and serves dirty extents and raw data over HTTP.

Bitmap creation on disk and tracker advancement are independent operations that occur at different points in the lifecycle. The full sequence is described in "Checkpoint and bitmap management." The backup controller deletes the export pod as soon as the backup reaches a terminal state to release PVC mounts.

Offline mode is detected automatically: if no VMI exists for the backup's source VM, the backup proceeds in offline mode.

### VM start prevention & Deletion handling

Before creating a virt-launcher pod, the VMI controller looks up VMBackup/VMBackupTracker CRs in the namespace. If any CR references the same VM, indicates offline mode, and has the `Progressing` condition set, the controller sets a `BackupInProgress` condition on the VMI and returns without creating the pod. The VMI remains `Pending` until the backup CR is no longer progressing. This follows the same pattern used for migration gating.

If a VMI appears for the source VM while the offline backup is progressing, the behavior depends on the backup mode:

- **Pull mode**: the backup controller completes the backup: it advances the tracker, deletes the export pod, and sets the `Complete` condition. Completing rather than failing prevents a dead state that would block the incremental chain indefinitely.
- **Push mode**: the backup controller fails the backup with reason `VMStarted`, deletes the export pod, and does not advance the tracker. A partial QCOW2 write on the target PVC is not a usable backup file, so completing would advance the tracker past data that was never fully written.

If the source VM is deleted during the backup, the export pod is deleted and the backup is marked `Failed` with reason `VMDeleted`.

### Backup deadline

The backup controller uses `spec.ttlDuration` as the maximum allowed duration for an offline backup:

- **Push mode**: TTL expiry before the write completes is a failure. The controller deletes the export pod and sets `Failed` with reason `DeadlineExceeded`. The tracker is not advanced.
- **Pull mode**: TTL expiry is a completion event, consistent with online pull mode where TTL-triggered abort is treated as success. The controller advances the tracker, deletes the export pod, and sets `Complete`. Incomplete pulls are the user's responsibility (see "Known limitations").

### NBD setup (export pod)

The export pod mounts the state PVC and all VM data volumes read-write, plus an emptyDir at `/sockets` for NBD Unix sockets. The volumes must be mounted at the same paths used by virt-launcher; `qemu-nbd` fails to open an overlay if its data-file is not reachable at the expected path. Since the VMI does not exist for stopped VMs, the export binary discovers overlay files by naming convention on the state PVC and runs `qemu-img info --output=json` on each to verify the data-file chain. If a data-file is missing or unreadable, that disk is marked `unavailable` in the export metadata and the remaining disks proceed normally.

The export server container image must include `qemu-nbd` and `qemu-img` for bitmap management (expect increased image size).

For each healthy disk, the export starts:
```bash
qemu-nbd --read-only --persistent --shared=8 \
         --socket=/sockets/${diskname}.sock \
         --bitmap=${CHECKPOINT_NAME} \
         --format=qcow2 \
         ${overlay}
```

### Checkpoint and bitmap management

Offline incremental backups follow the same checkpoint semantics as the online (libvirt) path.

On startup, the export pod validates the bitmaps referenced by the `VirtualMachineBackupTracker` on each disk overlay using `qemu-img info --output=json`. If any bitmap has the `in-use` flag set or is marked inconsistent (e.g., from a prior VM crash), the backup falls back to full mode for that disk and the tracker is updated accordingly.

The bitmap lifecycle is split between the export pod and the next VM start:

1. **Create new checkpoint bitmap (startup):** After validation, the export pod runs `qemu-img bitmap --add` on each overlay. This applies to every backup type (Full and Incremental), because each backup must create the tracking anchor for the next one. The bitmap is created before `qemu-nbd` starts, since `qemu-nbd --read-only` locks the overlay against modification. The new bitmap starts empty and does not advance the checkpoint; that is the tracker's role.
2. **Serve data:** `qemu-nbd --read-only` starts per disk, serving the previous checkpoint's dirty extents and data over HTTP.
3. **Advance tracker (completion):** The backup controller updates the `VirtualMachineBackupTracker` only when the backup completes (see "Pull mode completion semantics" and "Backup deadline"). If the backup fails (VM deletion, pod crash), the tracker is not advanced and the chain is preserved for retry.
4. **Remove old bitmap (deferred to VM start):** Verification of the new bitmap and removal of the old checkpoint bitmap are handled during checkpoint redefinition when the VM next starts.

Since `qemu-img bitmap` operates per-disk with no multi-disk transaction support, a pod crash during bitmap creation can leave partial state (bitmap added on some disks but not others). The next backup's startup validation detects this by checking each disk's bitmaps against the tracker and falls back to full mode for any inconsistent disk.

The bitmap management phase runs while the VM is stopped and after the backup controller has verified no virt-launcher pods exist.

### Pruning and cleanup

During checkpoint redefinition at VM start, only the specific bitmap being replaced is removed; other bitmaps retained by the tracker (e.g., for multi-checkpoint retention per VEP 25.1) are left untouched.

Orphaned bitmaps from prior crashes or interrupted operations are also detected during redefinition. A bitmap is orphaned only if no entry in the tracker's checkpoint list references it. Bitmaps belonging to older retained checkpoints are not removed.

Checkpoint retention policy is outside the scope of this VEP and is addressed by VEP 25.1.

### Boot-time checkpoint ordering

When the VM starts after offline backups, the VMI controller processes checkpoints in order:

1. **Redefine checkpoints** across all associated `VirtualMachineBackupTracker` CRs. Redefinition recreates bitmap tracking in the running VM's backend. Offline checkpoints (QCOW2 bitmaps written directly to disk by the export pod) have no backend equivalent to redefine. Redefinition failure for offline checkpoints must not invalidate the checkpoint. Offline checkpoints are identified by a distinguishing marker in the checkpoint name; the redefinition process skips them and clears only the "redefinition needed" flag.
2. **Checkpoint pruning** removes bitmaps exceeding `retainCheckpoints`, including any accumulated while the VM was stopped.

### Known limitations

Only one offline incremental backup can be performed between VM starts. No guest writes occur while the VM is stopped, so a second offline incremental would produce an empty delta. The backup controller rejects such requests when the latest checkpoint bitmap on disk is empty. A full (non-incremental) backup while the VM remains stopped proceeds normally.

**Pull mode trust boundary.** Any event that ends the backup window (TTL expiry, CR deletion, or VMI appearance) advances the tracker regardless of whether the user pulled all data. Since the VM is stopped, this does not compromise on-disk data integrity, but an incomplete pull creates a gap in the user's external backup chain that KubeVirt does not detect. Accidental CR deletion has the same effect, even if no data was pulled.

### HTTP endpoints (pull mode)

In pull mode, the export server reads data from local `qemu-nbd` processes using the same persistent Go NBD client connections as the existing export server. In push mode, no HTTP endpoints are served; the export pod is a batch writer (see "Push and pull modes").

Two endpoints are registered per disk:

Method | Endpoint | Description
-- | -- | --
GET | /exports/{disk}/map | Dirty extents JSON. If no checkpoint bitmap exists, returns all allocated blocks (full backup fallback).
GET | /exports/{disk}/data?offset=X&length=Y | Raw bytes for the specified range.

### Push and pull modes

For online backups, push mode hotplugs the target PVC into the running VM and `virDomainBackupBegin` writes sparse QCOW2 images (one per disk) directly to it; no export pod is involved. For offline backups there is no VMI, no libvirt backup job, and no hotplug. Both modes route through the export pod and `qemu-nbd`. The default is push, consistent with online backup:

- **Pull mode**: The export pod starts `qemu-nbd` per disk and serves the HTTP endpoints above. The backup remains `Progressing` until a completion trigger fires (see "Completion semantics").
- **Push mode**: The export pod mounts the target PVC from `spec.pvcName` alongside the data volumes. `qemu-nbd` is started per disk, but no HTTP endpoints are served; the export pod is a batch writer, not a server. For each disk, the export pod reads dirty extents and block data from the local `qemu-nbd` process (the same internal NBD client used for pull mode) and writes one sparse QCOW2 image to the target PVC. For an incremental backup, only blocks marked dirty in the previous checkpoint's bitmap are written; for a full backup, all allocated blocks are included. No FSFreeze/FSThaw is needed since the VM is stopped.

  The output format, file naming, and directory layout match online push mode (see VEP #25), so existing restore tooling works unchanged.

  The backup completes when all disks have been written and the export pod exits with phase `Succeeded`. The backup controller detects completion via the pod's phase, not via VMI status (there is no VMI). If the pod fails or is killed, the backup is marked `Failed` and the tracker is not advanced. See "Completion semantics" for the full distinction between push and pull.

### Completion semantics

Offline backup has two distinct completion models depending on the mode.

**Pull mode** follows online pull mode semantics. There is no explicit vendor completion signal. Any event that ends the backup window advances the tracker and marks the backup `Complete`:

- **TTL expiry**: See "Backup deadline."
- **CR deletion**: A finalizer on the VirtualMachineBackup CR advances the tracker before allowing deletion of a `Progressing` backup. See "Known limitations."
- **VMI appearance**: See "VM start prevention & Deletion handling." This trigger is unique to offline mode, since online backups always have a VMI present.

**Push mode** has a single success path: the export pod exits with phase `Succeeded`, and the tracker is advanced. Everything else (pod failure, TTL expiry, CR deletion, VMI appearance) is a failure. The tracker is not advanced because a partial QCOW2 write is not a usable backup file.

Online push completes via VMI status (`DOMAIN_JOB_COMPLETED`), offline push via pod phase, and offline pull via the implicit triggers above.

### Storage Access and Concurrency

The export pod mounts both the state PVC and data volume PVCs read-write. On RWX storage, concurrent access could corrupt QCOW2 metadata. The VMI and Backup controllers enforce mutual exclusion as described in "VM start prevention & Deletion handling."

## API Examples

No new CRs or subresources are introduced. Offline backup reuses the existing VirtualMachineBackup API from VEP #25.

The VirtualMachineBackup status includes an `offline` field:

```yaml
status:
  offline: true
```

## Alternatives

### Tracker advancement at backup start

Advance the `VirtualMachineBackupTracker` at backup start time along with bitmap creation. Excluded because it creates a silent data gap: if the backup fails after the tracker has moved forward, no future incremental backup captures the skipped window. Bitmap creation at startup is correct (required before `qemu-nbd` locks the overlay), but tracker advancement must occur only at completion.

### Deferred bitmap materialization at VM start

Mount all volumes read-only in the export pod and record bitmap creation/removal intent in the backup CR status. On next VM start, the VMI controller materializes bitmaps via QMP `transaction`. Excluded because the intent must survive between backup completion and VM start, but the backup CR may be deleted before the VM starts, losing the intent.

### Libvirt paused domain

Start the libvirt domain in paused state to access bitmaps via QMP. Excluded because it requires a VMI (defeating the purpose of offline backup), allocates full guest memory even when paused (`VIR_DOMAIN_START_PAUSED` only suspends vCPUs), and introduces a zombie VMI lifecycle.

### Explicit vendor completion signal (`/complete` subresource)

Add a `/complete` subresource to VirtualMachineBackup following the `certificatesigningrequests/approval` pattern, allowing vendors to explicitly signal pull completion. Excluded because online pull mode ships without any explicit completion signal and introducing one for offline would create an inconsistency between the two modes. If future requirements demand this mechanism, this subresource could be added without breaking the existing implicit triggers.

### VM finalizer to prevent deletion

Add a finalizer to the VM object to block deletion during backup. Excluded because VirtualMachineExport and online VirtualMachineBackup both handle source deletion via graceful abort. A finalizer risks permanently stuck VMs if the backup controller becomes unavailable.

## Update/Rollback Compatibility

On upgrade, offline backup becomes available behind a feature gate. On rollback, in-progress offline backups fail, but no persistent state is left behind that could block VM operations. Checkpoints created by online CBT are compatible with offline CBT because dirty bitmaps are QCOW2 format-level metadata, not a libvirt artifact.

## Functional Testing Approach

- Happy path: create an online backup with checkpoint, write additional data, stop the VM, perform an offline incremental backup, verify the dirty extent map reflects only the new writes, confirm the VM starts successfully after completion.
- VM start is blocked during backup and unblocks after completion.
- Fallback to full backup when no checkpoint exists.
- VM deletion during backup: backup aborted, VM deletion proceeds.
- Backup deadline exceeded (push mode): backup fails with `DeadlineExceeded`, tracker not advanced.
- Backup deadline exceeded (pull mode): backup completes, tracker advanced.
- Concurrent backup rejection: two offline backups for the same stopped VM, second is failed immediately.
- VM start during backup: backup completes (tracker advanced, `Complete`), export pod deleted, VM starts.
- Second incremental rejected: no guest writes since last backup, second incremental request rejected.
- Full backup after incremental: full (non-incremental) backup succeeds while VM remains stopped.
- Bitmap partial failure recovery: crash during bitmap creation, next backup detects inconsistency and falls back to full mode for affected disk.
- CR deletion during backup (pull mode): tracker advanced, export pod cleaned up.
- Incremental chain continuity: Full, Incremental, Incremental chain; each backup creates a bitmap; third backup's extents reflect only writes since the second.
- Offline checkpoint survives VM start: offline checkpoint not invalidated during boot-time redefinition, incremental chain intact.

## Implementation Phases

### Alpha (v1.10)

- Pull and push mode.
- VM deletion handling.
- Tracker advancement on completion.
- Pull mode completion semantics: TTL-as-completion, CR deletion finalizer, VMI appearance handling.
- Boot-time checkpoint redefinition with offline checkpoint skip logic and pruning.
- Feature gate: `OfflineIncrementalBackup` (disabled by default). Requires `IncrementalBackup` from VEP #25.

### Beta (TBD)

- Feature gate enabled by default.
- No data corruption incidents reported during Alpha.
- E2E tests passing in CI for at least one release cycle.

### GA (TBD)

- Feature gate removal.
- Stable in Beta for at least one release cycle.

## References

- VEP #25: [Storage agnostic incremental backup using qemu](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/incremental-backup.md)
- VEP #90: [Utility Volumes](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/utility-volumes.md)
- oVirt Backup API: https://www.ovirt.org/develop/release-management/features/storage/incremental-backup.html
