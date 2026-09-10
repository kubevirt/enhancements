# VEP #399: Multi-checkpoint incremental backup

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] : https://github.com/kubevirt/enhancements/issues/399
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Table of contents

- [Overview](#overview)
- [Motivation](#motivation)
- [Goals](#goals)
- [Non Goals](#non-goals)
- [Definition of Users](#definition-of-users)
- [User Stories](#user-stories)
- [Repos](#repos)
- [Design](#design)
  - [Checkpoint semantics](#checkpoint-semantics)
  - [Checkpoint retention](#checkpoint-retention)
  - [Backing up from an older checkpoint](#backing-up-from-an-older-checkpoint)
  - [Redefinition of the base](#redefinition-of-the-base)
  - [Cleanup](#cleanup)
- [API Examples](#api-examples)
  - [VirtualMachineBackupTracker CR](#virtualmachinebackuptracker-cr-changes)
  - [VirtualMachineBackup CR](#virtualmachinebackup-cr-changes)
- [Alternatives](#alternatives)
- [Scalability](#scalability)
- [Update/Rollback Compatibility](#updaterollback-compatibility)
- [Functional Testing Approach](#functional-testing-approach)
- [Known limitations](#known-limitations)
- [Implementation History](#implementation-history)
- [Graduation Requirements](#graduation-requirements)
  - [Alpha](#alpha)
  - [Beta](#beta)
    - [On-By-Default Readiness](#on-by-default-readiness)
  - [GA](#ga)

## Overview

Extends [VEP 25](../vep.md) incremental backup from a single retained checkpoint per `VirtualMachineBackupTracker` to several, and lets a `VirtualMachineBackup` name which retained checkpoint to use as its base.

## Motivation

A `VirtualMachineBackupTracker` retains one checkpoint, so the only possible incremental base is the one the most recent backup created.

When a vendor loses that backup for reasons outside KubeVirt (e.g., a failed upload, a corrupted object store, any fault on their side) the only base KubeVirt still offers is the one whose data is gone, and the next backup has to be full. On a multi-terabyte VM that is an expensive way to recover. Retaining a few checkpoints, and letting a backup pick among them, turns that full backup back into a delta.

## Goals

- Count-based retention of several checkpoints per `VirtualMachineBackupTracker`.
- Ability to base a `VirtualMachineBackup` on any retained checkpoint.
- Bitmap reclaim bounded by the retention count.

## Non Goals

- Checkpoint sharing across `VirtualMachineBackupTracker`s. Each chain is exclusive to its `VirtualMachineBackupTracker` by convention.
- Parallel backup chains within a single `VirtualMachineBackupTracker`.
- Age or TTL-based checkpoint retention.
- Per-disk checkpoint selection.
- Chain maintenance while the VM is stopped (see [VEP 401](../401-offline-incremental-backup/offline-incremental-backup.md)).

## Definition of Users

- Backup vendors: the primary consumer, integrating against the backup API.

## User Stories

- As a backup vendor, I want to retry an incremental backup from an older checkpoint when my latest backup data is lost, instead of falling back to full.
- As a backup vendor, I want to discard a chain I no longer trust and have its bitmaps reclaimed, without recreating my `VirtualMachineBackupTracker`.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Checkpoint semantics

Checkpoint bitmaps are self-contained and mutually independent. Every bitmap therefore records from its own checkpoint's creation up to the present. For checkpoints `C1`, `C2` and `C3` taken in that order, $b(C3) \subseteq b(C2) \subseteq b(C1)$, and the delta since `C1` is `b(C1)` alone. Three consequences follow:

1. A backup from `C` needs only `b(C)`.
2. Deleting a checkpoint deletes its own bitmaps and nothing else.
3. The parent relationship of checkpoints is metadata only (nothing in the backup path reads it).

This independence is what keeps vendors out of each other's way, each `VirtualMachineBackupTracker` already owns its chain outright and trimming one cannot invalidate another's bases.

### Checkpoint retention

`VirtualMachineBackupTracker` gains `spec.checkpointRetention` and an ordered `status.checkpoints` list.

`spec.checkpointRetention.historyLimit` is the desired chain length. A completed backup appends its checkpoint and drops anything past the count, oldest first, and it does both in the same status write in order to avoid coordination issues.

The append is already ordered ahead of everything else on the completion path, it precedes the teardown that releases the VMI for the next backup, so a checkpoint is never in existence-but-unnamed state while another backup is able to start.

That leaves two writes to `status.checkpoints` that no backup drives: lowering `spec.checkpointRetention.historyLimit`, and dropping a checkpoint [cleanup](#cleanup) reported as absent. Both are ordinary reconciles, and both are a status write and nothing else.

### Backing up from an older checkpoint

`VirtualMachineBackup` gains `spec.fromCheckpoint`, naming the checkpoint to use as the incremental base, unset means `latestCheckpoint`.

Resolution is per disk, a disk is incremental when the named checkpoint's bitmap is present and usable on it (as established in [VEP 25](../vep.md)), and full otherwise. Each checkpoint is judged on its own bitmap, so a disk missing `b(C1)` but still holding `b(C2)` is full for a `C1`-based backup and incremental for a `C2`-based one (i.e., partially covered chain degrades disk by disk instead of failing the backup).

A name that is not in the source `VirtualMachineBackupTracker`'s `status.checkpoints` is rejected on create.

### Redefinition of the base

As already established, Libvirt checkpoint metadata is transient, so a VM restart leaves it holding none of the retained chain. Rather than restoring the chain up front, the base is redefined lazily at backup start, on the path that already reads every disk to decide per-disk backup type (see [VEP 25](../vep.md#backup-type-model)). virt-launcher redefines the requested checkpoint against exactly the disks that still carry its bitmap, then issues `BackupBegin`. Redefinition is idempotent, so it is a no-op when libvirt already holds the checkpoint.

Only the requested base is ever redefined, which the [independence of bitmaps](#checkpoint-semantics) permits, a backup from `C` reads `b(C)` alone and the rest of the chain is irrelevant to it.

The retained chain therefore describes what a vendor may ask for, not what libvirt currently holds. Libvirt holds whichever bases backups have used since the last boot. Nothing sequences a backup behind a redefinition pass, so a backup issued immediately after a restart runs like any other.

A base whose bitmap is gone from every disk still produces a backup, an all-`Full` one, reported as such per volume. That outcome is the signal that the name is no longer a base, and the controller drops it from `status.checkpoints` (`forceFullBackup` reduces the chain regardless, so the two do not compete). One that no backup happens to ask about is dropped by [cleanup](#cleanup) instead, which answers the same question for the whole retained set rather than for one base. A transient failure (e.g., launcher not reachable yet or a conflicting libvirt job) drops nothing, since discarding a checkpoint on an error that will clear destroys a base the vendor may still be holding data against.

### Cleanup

Three important terms:
- a **desired set** for a VM is the union of `status.checkpoints` and `latestCheckpoint` across every `VirtualMachineBackupTracker` referencing it, plus the base a starting backup resolved (if one is starting). `latestCheckpoint` is the newest element of `status.checkpoints` and never the first trimmed, so unioning it in retains nothing extra; it is there so that a tracker whose list is not yet populated still contributes its base.
- an **orphan bitmap** sits on a disk that no tracker names, and occupies space.
- a **zombie checkpoint** is named by a tracker but has no bitmap on any disk.

A new `Reclaim` RPC to virt-launcher is introduced and carries the desired set; virt-launcher joins it against the bitmap inventory it already reads at backup start, deletes what falls outside, and returns the names that matched no bitmap. The controller then drops those from the trackers that named them. Reclaiming a bitmap does not require its checkpoint to be redefined first, so nothing on this path restores chain metadata.

There are two triggers for reclamation:
- On backup start which attempts to reclaim orphaned bitmaps at the moment the backup is about to need the extra space.
- On VMI start which covers what was orphaned while the VM was down, such as a chain trimmed offline or a `VirtualMachineBackupTracker` deleted before its bitmaps could be collected.

Neither gates what triggered it, `Reclaim` is best-effort and a failure emits an event and proceeds. Nothing periodic runs in between, so a VM that stops taking backups keeps its orphans until it takes another or reboots.

Reconciling a set rather than replaying individual deletions means ordering carries no correctness weight. A crash between trimming `status.checkpoints` and deleting the bitmap leaves an orphan, which is by definition outside the next desired set. A deleted `VirtualMachineBackupTracker` needs no cleanup RPC for the same reason, its checkpoints simply leave the union.

`forceFullBackup` on a `VirtualMachineBackupTracker` source reduces `status.checkpoints`, on completion, to just the checkpoint that backup created, and the retained bases are then collected like anything else outside the set. It serves as an escape hatch for a chain a vendor no longer trusts, so it discards the retained bases rather than appending to them. A forced backup that fails before creating its checkpoint leaves `status.checkpoints` unchanged, so the chain it was meant to replace survives and the vendor can retry.

The controller builds the desired set from synced informers and skips the `Reclaim` call, not the backup, if any are not. A tracker missing from that union looks exactly like a tracker with no checkpoints, and its bitmaps would be deleted while its owner is still holding data against them.

## API Examples

### VirtualMachineBackupTracker CR (changes)

**Spec:**
- `checkpointRetention`: how the retained chain is bounded. Mutable.
  - `historyLimit`: how many checkpoints to retain. Default `1`.

**Status:**
- `checkpoints`: the retained checkpoints, oldest first, at most `historyLimit`. Checkpoint names are derived from the `VirtualMachineBackup` that created them, as `<backup name>-<creation timestamp>`, unchanged from [VEP 25](../vep.md).

```yaml
apiVersion: backup.kubevirt.io/v1alpha1
kind: VirtualMachineBackupTracker
metadata:
  name: my-vm-tracker
  namespace: default
spec:
  source:
    apiGroup: kubevirt.io
    kind: VirtualMachine
    name: my-vm
  checkpointRetention:
    historyLimit: 3
status:
  checkpoints:
    - name: my-vm-daily-1-2026-03-01_12-00-00
      creationTime: "2026-03-01T12:00:00Z"
    - name: my-vm-daily-2-2026-03-02_12-00-00
      creationTime: "2026-03-02T12:00:00Z"
    - name: my-vm-daily-3-2026-03-03_12-00-00
      creationTime: "2026-03-03T12:00:00Z"
  latestCheckpoint:
    name: my-vm-daily-3-2026-03-03_12-00-00
    creationTime: "2026-03-03T12:00:00Z"
```

### VirtualMachineBackup CR (changes)

**Spec:**
- `fromCheckpoint`: retained checkpoint to use as the incremental base. Defaults to the `VirtualMachineBackupTracker`'s `latestCheckpoint`.

**Status:**
- `fromCheckpoint`: the base this `VirtualMachineBackup` actually ran against, reported even when the spec field was unset.

A backup based on the oldest retained checkpoint rather than the latest, where one disk lost its bitmap and fell back to full on its own:

```yaml
apiVersion: backup.kubevirt.io/v1alpha1
kind: VirtualMachineBackup
metadata:
  name: my-vm-daily
  namespace: default
spec:
  source:
    apiGroup: backup.kubevirt.io
    kind: VirtualMachineBackupTracker
    name: my-vm-tracker
  mode: Push
  pvcName: backup-target-pvc
  fromCheckpoint: my-vm-daily-1-2026-03-01_12-00-00
status:
  checkpointName: my-vm-daily-1-2026-03-02_12-00-00
  fromCheckpoint: my-vm-daily-1-2026-03-01_12-00-00
  includedVolumes:
    - volumeName: rootdisk
      type: Incremental
    - volumeName: datadisk
      type: Full
```

## Alternatives

A separate checkpoint CRD: a checkpoint has no lifecycle independent of its chain, ordering would have to be reconstructed from object metadata, and it multiplies API objects per VM per backup without adding expressiveness.

Falling back to the nearest newer usable base instead of full: a disk missing the requested base but holding a later one could produce a smaller delta, but the vendor asked for the base whose data they hold. Applying a later delta to it drops the writes in between, and the result looks valid.

## Scalability

[VEP 25](../vep.md#scalability) sizes the backend storage PVC with a flat [`CBTBackendStateOverhead`](https://github.com/kubevirt/kubevirt/blob/92dff93d6f/pkg/storage/cbt/cbt.go#L41-L57) of 512 MiB, derived for 5 disks x 500 GiB with 256 KiB clusters, 64 KiB bitmap granularity and 10 bitmaps per disk. Retention turns that last term into a user-controlled value.

What lands on a disk is the sum of `historyLimit` across every `VirtualMachineBackupTracker` referencing the VM, not any single one's value, for example, three vendors retaining 10 each puts 30 bitmaps on every disk (see [Known limitations](#known-limitations)).

For the reference geometry each retained checkpoint costs roughly ~1.25 MiB of overlay per disk, `ceil(V / G / 8 / S) x S` of bitmap data plus one cluster for the bitmap table, so about ~6.25 MiB per checkpoint across 5 disks. QEMU additionally holds each bitmap in memory at `V / G / 8`, about 1 MiB per bitmap per disk, and every bitmap transfers with its disk during live migration.

## Update/Rollback Compatibility

All new fields are optional with backward-compatible defaults, and the feature is guarded by a new `MultiCheckpointIncrementalBackup` feature gate.

**Upgrade**: existing `VirtualMachineBackupTracker`s keep working. `status.checkpoints` is populated from `latestCheckpoint` on first reconciliation and `historyLimit` defaults to 1, so an upgraded `VirtualMachineBackupTracker` behaves exactly as before until a vendor opts into a larger value. Raising `historyLimit` takes effect from the next backup onward and cannot recover checkpoints already trimmed.

**Rollback**: an older controller ignores `status.checkpoints` and uses `latestCheckpoint`, and ignores `fromCheckpoint` on in-flight backups. Deltas from `latestCheckpoint` stay correct, because that bitmap is self-contained and does not depend on the checkpoints the old controller stopped tracking. The other retained checkpoints' bitmaps remain in the QCOW2 overlays, and an older controller has no `Reclaim` to remove them, so the cost of a rollback is wasted space rather than a broken chain.

## Functional Testing Approach

- After N backups with `historyLimit: K`, the `VirtualMachineBackupTracker` lists `min(N, K)` checkpoints and each disk carries the matching number of bitmaps.
- A backup from each retained checkpoint produces data consistent with a full backup taken at that point.
- Trimming a middle checkpoint leaves the remaining bases usable, confirming that bitmaps are independent.
- A `VirtualMachineBackup` issued immediately after a VM restart runs incrementally, and libvirt holds the base only from that point on.
- Two consecutive backups from the same retained base both succeed, confirming redefinition is idempotent.
- A backup from a retained base after a live migration runs incrementally on the target, where libvirt holds no checkpoint metadata at all.
- A checkpoint whose bitmap is gone from every disk is dropped from `status.checkpoints`, whether a backup used it as a base or `Reclaim` reported it absent, and the checkpoints that kept theirs are not.
- A base covered on some disks but not others is redefined against the covered ones only, so the backup mixes `Incremental` and `Full` rather than failing or reducing the chain.
- `status.checkpoints` never exceeds `historyLimit` in any observed state across a completing backup, confirming the append and the trim are one write.
- A `fromCheckpoint` naming nothing, and one naming a checkpoint retained by a different `VirtualMachineBackupTracker` on the same VM, are both rejected on create rather than producing a full backup.
- A `fromCheckpoint` the source `VirtualMachineBackupTracker` retains but whose bitmaps are gone is admitted, backs up as all-`Full` and is dropped from `status.checkpoints`, confirming validation does not conflate a fabricated base with a dead one.
- Lowering `historyLimit` and immediately starting a backup from a base the decrease removes still runs that disk incrementally, confirming the resolved base is in the desired set even when no tracker names it.
- Trimming reduces `status.checkpoints` without contacting the VMI, and the trimmed bitmap disappears at the next `Reclaim` rather than at the trim.
- Killing virt-launcher mid-backup leaves a bitmap no `VirtualMachineBackupTracker` names, which `Reclaim` collects at the next backup or VMI start, and the surviving bases still produce correct deltas.
- A `Reclaim` that fails emits an event and does not fail or delay the backup that triggered it.
- `forceFullBackup` leaves exactly one checkpoint, and one bitmap per disk after the next `Reclaim`.
- `VirtualMachineBackupTracker` deletion issues no RPC, and its bitmaps are collected at the next `Reclaim` on that VM, or at the next boot if the VM is stopped.

## Known limitations

- **Per-VM retention**: `historyLimit` caps one `VirtualMachineBackupTracker`'s chain, but the bitmaps on a disk are the sum across every `VirtualMachineBackupTracker` referencing the VM. `CBTBackendStateOverhead` is a flat constant that scales with neither disk size nor disk count, so a VM with many of them, many disks, or disks larger than the reference geometry can exceed what its backend storage was sized for.
- **Offline chain maintenance**: `Reclaim` needs a running virt-launcher, so everything trimmed or abandoned while the VM is stopped defers to the next boot, pending [VEP 401](../401-offline-incremental-backup/offline-incremental-backup.md).

## Implementation History

TBD.

## Graduation Requirements

### Alpha

- [ ] `IncrementalBackup` graduated to beta
- [ ] Count-based retention reconciled toward `spec.checkpointRetention.historyLimit`
- [ ] `fromCheckpoint` selecting any retained checkpoint as the incremental base, validated on create against the source `VirtualMachineBackupTracker`'s `status.checkpoints`, resolved by the controller at backup start and reported on `status.fromCheckpoint`
- [ ] `forceFullBackup` purging the retained chain
- [ ] Lazy redefinition of the requested base at backup start, covering VM restart and live migration
- [ ] Bitmap reclaim via a set-based `Reclaim` RPC, driven from the union of the retained sets, triggered at backup start and VMI start, dropping zombie checkpoints from `status.checkpoints`

### Beta

TBD.

#### On-By-Default Readiness

TBD.

### GA

TBD.
