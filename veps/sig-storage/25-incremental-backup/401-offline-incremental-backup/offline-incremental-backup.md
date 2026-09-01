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

The current CBT implementation (VEP #25) supports incremental backups only for running VMs. When a VM stops, the VMI is deleted, the libvirt domain no longer exists, and the only backup option is a full copy.

oVirt solves this using direct `qemu-nbd --bitmap` manipulation, but its design assumes bare-metal with root access. This VEP adapts that approach to run in non-privileged pods.

## Goals

- Enable incremental backups for stopped VMs using persisted QCOW2 dirty bitmaps.
- Prevent data corruption from VM start/delete/migration during backup.
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

When an offline backup is requested, the backup controller verifies the VM is stopped and that no stale virt-launcher pods remain. A pod is stale if it carries the `kubevirt.io/domain` label matching the source VM and is not in a terminal phase (`Succeeded` or `Failed`). A stale pod or an in-progress backup for the same VM fails the request immediately.

Once validated, the backup controller creates a VirtualMachineExport. The export controller starts a pod that mounts the state PVC and all data volumes read-write, discovers the QCOW2 overlay chain, validates existing bitmaps, creates a new bitmap on each disk, and starts `qemu-nbd` per disk. In pull mode, the pod serves dirty extents and raw data over HTTP. In push mode, the pod writes sparse QCOW2 images to the target PVC.

Bitmap creation on disk and tracker advancement are independent operations that occur at different points in the lifecycle. The full sequence is described in "Checkpoint and bitmap management." The backup controller deletes the export pod as soon as the backup reaches a terminal state to release PVC mounts.

The absence of a VMI for the source VM triggers offline mode automatically.

### VM start prevention & Deletion handling

Before creating a virt-launcher pod, the VMI controller looks up VMBackup/VMBackupTracker CRs in the namespace. When a CR references the same VM, indicates offline mode, and has the `Progressing` condition set, the controller sets a `BackupInProgress` condition on the VMI and returns without creating the pod. The VMI remains `Pending` until the backup CR is no longer progressing. This is the same gating mechanism used for migration.

VMI creation during offline backup is progressing is gated the same way in both modes. Virt-launcher creation is delayed until the backup reaches a terminal state. In push mode the gate clears once the export pod finishes writing. In pull mode it clears once the vendor deletes the backup CR.

Deletion of the source VM during the backup causes the export pod to be deleted and the backup to be marked `Failed` with reason `VMDeleted`.

### NBD setup (export pod)

The export pod mounts the state PVC and all VM data volumes read-write, plus an emptyDir at `/sockets` for NBD Unix sockets. The volumes must be mounted at the same paths used by virt-launcher; `qemu-nbd` fails to open an overlay if its data-file is not reachable at the expected path. Without a running VMI to query, the export binary discovers overlay files by naming convention on the state PVC and runs `qemu-img info --output=json` on each to verify the data-file chain. If a data-file is missing or unreadable, that disk is marked `unavailable` in the export metadata and the remaining disks proceed normally.

The export server container image must include `qemu-nbd` and `qemu-img` for bitmap management.

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

On startup, the export pod validates the bitmaps referenced by the `VirtualMachineBackupTracker` on each disk overlay using `qemu-img info --output=json`. Bitmaps with the `in-use` flag or marked inconsistent (e.g., from a prior VM crash) trigger a fallback to full mode for that disk, and the tracker is updated accordingly. A disk with no existing bitmap (e.g., a hotplugged disk not present in prior backups) gets a full backup while other disks proceed as incremental.

The bitmap lifecycle is split between the export pod and the next VM start:

1. **Create new bitmap (startup):** After validation, the export pod runs `qemu-img bitmap --add` on each overlay. This applies to every backup type (full and incremental) and every disk, because each backup must create the baseline bitmap for the next incremental backup. The bitmap is created before `qemu-nbd` starts, since `qemu-nbd --read-only` locks the overlay against modification. The new bitmap starts empty and does not advance the checkpoint; that is the tracker's role.
2. **Serve data:** `qemu-nbd --read-only` starts per disk, exposing the previous checkpoint's dirty extents and block data. In pull mode, the export pod serves this over HTTP. In push mode, it reads from the local `qemu-nbd` process and writes to the target PVC.
3. **Advance tracker (completion):** The backup controller updates the `VirtualMachineBackupTracker` only when the backup completes (see "Completion semantics"). If the backup fails (VM deletion, pod crash), the tracker is not advanced and the chain is preserved for retry.
4. **Remove old bitmap (deferred to VM start):** Verification of the new bitmap and removal of the old checkpoint bitmap are handled during checkpoint redefinition when the VM next starts.

Since `qemu-img bitmap` operates per-disk with no multi-disk transaction support, a pod crash during bitmap creation can leave partial state (bitmap added on some disks but not others). If the VMExport is still active, the export pod restarts and validates each disk: if a bitmap matching the backup's checkpoint name already exists and is consistent, it is reused; if it exists but is inconsistent, it is removed and recreated. Fallback to full mode is only needed if the previous checkpoint's bitmap (the one being read) is corrupt.

The bitmap management phase runs while the VM is stopped and after the backup controller has verified no virt-launcher pods exist.

### Pruning and cleanup

Orphaned bitmaps from prior crashes or interrupted operations are detected during checkpoint redefinition at VM start. A bitmap is orphaned if no entry in the tracker's checkpoint list references it. Orphaned bitmaps are removed; all others are left untouched.

Checkpoint retention policy is outside the scope of this VEP.

### Boot-time checkpoint ordering

When the VM starts after offline backups, the VMI controller processes checkpoints in order:

1. **Redefine checkpoints** across all associated `VirtualMachineBackupTracker` CRs. Redefinition recreates bitmap tracking in the running VM's backend. Offline checkpoints are redefined the same way as online checkpoints; the bitmap already exists on disk and `--redefine` adopts it.
2. **Checkpoint pruning** removes bitmaps exceeding `retainCheckpoints`, including any accumulated while the VM was stopped.

### Known limitations

Only one offline incremental backup can be performed between VM starts. No guest writes occur while the VM is stopped, so a second offline incremental would produce an empty delta. The export pod checks the latest checkpoint bitmap on startup and reports an empty bitmap via status; the backup controller fails the request based on that status. A full (non-incremental) backup while the VM remains stopped proceeds normally.

**Pull mode trust boundary.** CR deletion advances the tracker regardless of whether the user pulled all data. On-disk data remains intact (the VM is stopped and nothing has changed on the volume), but an incomplete pull creates a gap in the user's external backup chain that KubeVirt does not detect.

**Node affinity window.** Between backup completion and export pod deletion, the data volume PVCs may still be mounted on the export pod's node. During this brief window, the VM is constrained to schedule on that node.

### HTTP endpoints (pull mode)

In pull mode, the export server reads dirty extents and block data from local `qemu-nbd` processes. In push mode, no HTTP endpoints are served; the export pod is a batch writer (see "Push and pull modes").

Two endpoints are registered per disk:

Method | Endpoint | Description
-- | -- | --
GET | /exports/{disk}/map | Dirty extents JSON. If no checkpoint bitmap exists, returns all allocated blocks (full backup fallback).
GET | /exports/{disk}/data?offset=X&length=Y | Raw bytes for the specified range.

### Push and pull modes

For online backups, push mode hotplugs the target PVC into the running VM and `virDomainBackupBegin` writes sparse QCOW2 images (one per disk) directly to it; no export pod is involved. Offline backups have no running VMI, so libvirt backup and PVC hotplug are unavailable. Both modes route through the export pod and `qemu-nbd`. Push is the default:

- **Pull mode**: The export pod starts `qemu-nbd` per disk and serves the HTTP endpoints above. The backup remains `Progressing` until a completion trigger fires (see "Completion semantics").
- **Push mode**: The export pod mounts the target PVC from `spec.pvcName` alongside the data volumes. `qemu-nbd` is started per disk, but no HTTP endpoints are served; the export pod is a batch writer, not a server. For each disk, the export pod reads dirty extents and block data from the local `qemu-nbd` process and writes one sparse QCOW2 image to the target PVC. For an incremental backup, only blocks marked dirty in the previous checkpoint's bitmap are written; for a full backup, all allocated blocks are included. No FSFreeze/FSThaw is needed.

  The output format, file naming, and directory layout match online push mode (see VEP #25), so existing restore tooling works unchanged.

  The backup completes when all disks have been written and the export pod exits with phase `Succeeded`. The backup controller detects completion via the pod's phase, not via VMI status (there is no VMI). If the pod fails or is killed, the backup is marked `Failed` and the tracker is not advanced. See "Completion semantics" for the full distinction between push and pull.

### Completion semantics

Offline backup has two distinct completion models depending on the mode.

**Pull mode** completes on CR deletion. See "Known limitations." TTL expiry is a failure and does not advance the tracker.

**Push mode** only succeeds if the export pod exits `Succeeded`, and only then does the tracker advance. A pod failure or CR deletion fails the backup and leaves the tracker untouched.

Online push completes via VMI status (`DOMAIN_JOB_COMPLETED`), offline push via pod phase, and offline pull via CR deletion.

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

Advance the `VirtualMachineBackupTracker` at backup start time along with bitmap creation. This creates a silent data gap: if the backup fails after the tracker has moved forward, no future incremental backup captures the skipped window. Bitmap creation at startup is correct (required before `qemu-nbd` locks the overlay), but tracker advancement must occur only at completion.

### Deferred bitmap materialization at VM start

Mount all volumes read-only in the export pod and record bitmap creation/removal intent in the backup CR status. On next VM start, the VMI controller materializes bitmaps via QMP `transaction`. The intent must survive between backup completion and VM start, but the backup CR may be deleted before the VM starts, losing it.

### Libvirt paused domain

Start the libvirt domain in paused state to access bitmaps via QMP. This requires an active VMI, allocates guest resources even though no guest workload runs, and introduces a VMI that must be cleaned up after backup.

### Explicit vendor completion signal (`/complete` subresource)

Add a `/complete` subresource to VirtualMachineBackup following the `certificatesigningrequests/approval` pattern, allowing vendors to explicitly signal pull completion. Excluded because online pull mode ships without any explicit completion signal and introducing one for offline would create an inconsistency between the two modes.

### VM finalizer to prevent deletion

Add a finalizer to the VM object to block deletion during backup. Excluded because VirtualMachineExport and online VirtualMachineBackup both handle source deletion via graceful abort. A finalizer risks permanently stuck VMs if the backup controller becomes unavailable.

## Update/Rollback Compatibility

On upgrade, offline backup becomes available behind a feature gate. On rollback, in-progress offline backups fail, but no persistent state is left behind that could block VM operations. Checkpoints created by online CBT are compatible with offline CBT because dirty bitmaps are QCOW2 format-level metadata, not a libvirt artifact.

## Functional Testing Approach

- Happy path: create an online backup with checkpoint, write additional data, stop the VM, perform an offline incremental backup, verify the dirty extent map reflects only the new writes, confirm the VM starts successfully after completion.
- VM start is blocked during backup and unblocks after completion.
- Fallback to full backup when no checkpoint exists.
- VM deletion during backup: backup aborted, VM deletion proceeds.
- Concurrent backup rejection: two offline backups for the same stopped VM, second is failed immediately.
- VM start during pull backup: VM start is blocked until the vendor deletes the backup CR.
- VM start during push backup: gate delays VMI, export pod finishes writing, backup completes, VM starts.
- Hotplugged disk backup: VM with a new disk not present in prior backups; new disk gets full backup, existing disks get incremental.
- Second incremental rejected: no guest writes since last backup, second incremental request rejected.
- Full backup after incremental: full (non-incremental) backup succeeds while VM remains stopped.
- Bitmap partial failure recovery: crash during bitmap creation, export pod restarts and recovers (reuses valid bitmaps, recreates inconsistent ones).
- CR deletion during backup (pull mode): tracker advanced, export pod cleaned up.
- Incremental chain continuity: Full, Incremental, Incremental chain; each backup creates a bitmap; third backup's extents reflect only writes since the second.
- Offline checkpoint survives VM start: offline checkpoint redefined normally during boot-time redefinition, incremental chain intact.

## Implementation Phases

### Alpha (v1.10)

- Pull and push mode.
- VM deletion handling.
- Tracker advancement on completion.
- Completion semantics: CR deletion finalizer, VM start gating.
- Boot-time checkpoint redefinition and pruning.
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
- VEP #25.1: [Advanced checkpoint management](https://github.com/kubevirt/enhancements/pull/400)
- VEP #90: [Utility Volumes](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/utility-volumes.md)
- oVirt Backup API: https://www.ovirt.org/develop/release-management/features/storage/incremental-backup.html
