# VEP #509: VirtualMachine hibernation (save and resume the memory state)

## VEP Status Metadata

### Target releases

To be set during SIG release planning; per the template, no future release is
targeted in advance:

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

No targets are set yet. Tracking issue: kubevirt/enhancements#509.

> Draft status: an implementation of this VEP is proposed in
> kubevirt/kubevirt#19816 (branch `feat/vm-memory-snapshot` of
> github.com/qibergames/kubevirt), with a backport to v1.9.1 on
> `feat/vm-memory-snapshot-v1.9.1`. The backport ran end to
> end on a 2-node cluster (k3s, NFS storage, an 8 GiB guest running a game
> server): repeated hibernate and resume cycles of the same guest boot, cancel,
> save timeouts, restore failures and the release of the saved state PVC. The
> measurements quoted below come from there. The functional tests of the
> branch compile but did not run in kubevirtci yet.
>
> This VEP and its implementation were written with AI assistance (Claude,
> Anthropic) under the author's direction; see the AI disclosure in the VEP PR.

## Overview

Hibernation saves the RAM and device state of a running VirtualMachine (VM)
to a PersistentVolumeClaim (PVC) and stops the VM. The next start of the VM
resumes the guest where it left off, without a boot. QEMU writes the state as
a migration stream to a file (libvirt `virDomainSaveParams`), by default in
QEMU's `mapped-ram` format with parallel channels. It is read back on start
with `virDomainRestoreParams`. A saved state can be used once: it is deleted
after the VM resumes from it, and discarded whenever the disks might no
longer match it.

## Motivation

Booting a VM can take far longer than resuming it. A guest running a large
application (JVM services, game servers, IDEs, CI runners with warm caches)
often needs tens of seconds after boot before it is useful. Platforms that
scale idle VMs to zero today have to choose between paying for idle VMs and
making users wait for a cold start.

KubeVirt can pause a VM, but a paused VM still holds its memory, CPU
reservation and node. KubeVirt snapshots only cover disks. There is no way to
release all resources of a VM while keeping its running state.

Related upstream discussions:

- [kubevirt/kubevirt#18054](https://github.com/kubevirt/kubevirt/issues/18054)
  proposes memory snapshots via QEMU migration to a file on a dynamically
  provisioned PVC.
- [kubevirt/kubevirt#17242](https://github.com/kubevirt/kubevirt/issues/17242)

## Goals

- Save the memory and device state of a running VM to a PVC, then stop the
  VM and release its pod, CPU and memory.
- Resume the VM from the saved state on its next start, on a node allowed by
  the topology of the saved state's storage that can
  run its CPU, with the guest continuing without a reboot.
- Never resume a saved state whose disks changed since it was saved.
- Own the life cycle of the PVC holding the state: create it, delete it after
  use, and have it garbage collected with the VM.
- Resume fast. Resume latency is the main metric, then the size of the state.

## Non Goals

- Memory snapshots that are kept and can be resumed several times, i.e. a
  `VirtualMachineSnapshot` with memory. This needs disk snapshots consistent
  with the memory and a restore that rolls the disks back. It can build on the
  save and resume plumbing of this VEP later.
- Guest-initiated hibernation (ACPI S4, suspend-to-disk inside the guest).
- Lazy (post-copy) resume, where the guest runs before all of its memory is
  read. QEMU cannot do a post-copy restore from a file.
- Hibernating VMs whose device state cannot be migrated (host devices, vGPUs,
  SR-IOV, persistent reservations, …).

## Definition of Users

- VM owners, who hibernate and resume their VMs.
- Platform operators building scale-to-zero on top of KubeVirt.
- Cluster admins, who enable the feature gate and pick the storage class.

## User Stories

- As a game server host, I hibernate a server once the last player leaves and
  resume it when a player connects, in a few seconds instead of a 30 to 45
  second cold start of a modded JVM server.
- As a CI platform, I keep pre-warmed runner VMs hibernated and resume one per
  job.
- As a VM owner, I release the resources of a VM I am not using without
  losing my session.
- As a cluster admin, I know what a hibernated VM costs: one PVC, sized like
  its plugged memory and filled like its used memory.

## Repos

- kubevirt/kubevirt

## Design

### Flow

```
virtctl hibernate vm   virt-api              virt-controller (VM)          virt-handler / virt-launcher
---------------------  --------------------  ----------------------------  -------------------------------
PUT .../hibernate  ->  validate, set
                       status.savedState
                       (Pending) and stop
                       the VM (runStrategy
                       Halted or a stop
                       request)
                                             hold the stop of the VMI
                                             record spec hash, disk PVC
                                             UIDs, host-model CPU labels
                                             create the PVC (VM-owned),
                                             hot plug it as the SavedState
                                             utility volume
                                             saved-state-target       ->   volume mounted: SaveVirtualMachine
                                                                           RPC (file domain-<hash>.save)
                                                                           -> virDomainSaveParams
                                                                           (sparse, N channels, O_DIRECT);
                                                                           libvirt stops the domain with
                                                                           shutoff reason "saved"
                                             VMI Succeeded, VMI       <-   vmi.status.savedState: Saved
                                             savedState Saved ->
                                             VM Saved, stop proceeds

virtctl start vm       start subresource     startVMI: is the state valid?
                                             yes: vmi.spec.savedStateSource
                                                  + CPU node selector,
                                                  then hot plug the PVC
                                                  as saved-state-source
                                                  (attachment pod created
                                                  once the launcher pod is
                                                  scheduled)
                                             no:  discard it, boot    ->   volume mounted: the domain is
                                                                           defined as usual, then
                                                                           virDomainRestoreParams with the
                                                                           pod's XML as dxml instead of
                                                                           virDomainCreate
                                             Restored: unplug the     <-   vmi.status.savedState: Restored
                                             volume, delete the PVC
                                             RestoreFailed: keep the  <-   vmi.status.savedState:
                                             state, hold the start         RestoreFailed, VMI Failed,
                                             until it is discarded         the domain is never booted
```

### API

Feature gate: `VMHibernation` (alpha). It also needs the `UtilityVolumes`
feature gate, since the state is saved to a utility volume.

#### Subresources

| Subresource | Body | Effect |
|---|---|---|
| `PUT virtualmachines/{name}/hibernate` | `HibernateOptions` | Saves the state of the running VM and stops it |
| `PUT virtualmachines/{name}/discardsavedstate` | none | Deletes the saved state; the next start boots. Also cancels a `Pending` hibernation, which keeps the VM running |

Resuming needs no new subresource: starting a hibernated VM resumes it
(`virtctl start`, `runStrategy: Always`, …). Starting a VM whose hibernation
is still `Pending` cancels the hibernation, like `discardsavedstate`: the user
asks for the VM to run, and it still runs. Starting it while its state is
being saved (`Saving`) is refused with a message to start it again once it is
hibernated, and starting a VM whose resume failed (`RestoreFailed`) is
refused until the saved state is discarded. Both subresources are added to
the `kubevirt.io:edit` and `kubevirt.io:admin` cluster roles, and so to the
`kubevirt-operator` cluster role too: Kubernetes refuses to let the operator
create or update a role granting more than it holds itself. A deployment
that only swaps the operator image, keeping an older install manifest, stalls
on that error.

```go
type HibernateOptions struct {
	// StorageClassName of the PVC holding the saved state. Defaults to the default storage class.
	StorageClassName *string `json:"storageClassName,omitempty"`
	DryRun           []string `json:"dryRun,omitempty"`
}
```

Hibernate stops the VM according to its run strategy, the way `stop` does.
`Always` and `Once` switch to `Halted`. `Manual` and `RerunOnFailure` get a
stop request, marked as the one of the hibernation, in the same status patch
that requests the hibernation. `Halted` and `WaitAsReceiver` are rejected.
The run strategy is recorded in `savedState.runStrategy`: a start of the
hibernated VM sets it back, so a `Once` VM stays `Once` after a resume rather
than becoming `Always`, and a cancel or failure restores it.

#### VirtualMachine status

```go
type VirtualMachineStatus struct {
	...
	SavedState *VirtualMachineSavedState `json:"savedState,omitempty"`
}

type VirtualMachineSavedState struct {
	Phase              VirtualMachineSavedStatePhase // Pending, Saving, Saved, Restoring, Restored, RestoreFailed, Failed
	ClaimName          string
	StorageClassName   *string
	FileName           string
	SourceUID          types.UID            // VMI the state was saved from
	RunStrategy        *VirtualMachineRunStrategy // run strategy when hibernating, restored by a cancel or failure
	SourceNodeSelector map[string]string    // labels a node needs to resume it (host-model CPU)
	SpecHash           string               // hardware identity of the VM spec at save time
	VolumeClaimUIDs    map[string]types.UID // disk PVC name -> UID at save time
	RestoreUID         types.UID            // VMI resuming from the state
	SupersededUID      types.UID            // failed restore VMI a retry replaces
	SaveStartTimestamp, SaveEndTimestamp       *metav1.Time
	RestoreStartTimestamp, RestoreEndTimestamp *metav1.Time
	Discard            bool
	Message            string
}
```

Two printable statuses are added: `Hibernating` while the state is saved,
and `Hibernated` while the VM is stopped with a saved state.

#### VirtualMachineInstance

```go
type VirtualMachineInstanceSpec struct {
	...
	// Set by the VM controller when a hibernated VM starts.
	// Rejected in VM templates.
	SavedStateSource *SavedStateSource `json:"savedStateSource,omitempty"`
}

type SavedStateSource struct {
	ClaimName string `json:"claimName"`
	FileName  string `json:"fileName"` // a plain file name at the root of the PVC
}

type VirtualMachineInstanceStatus struct {
	...
	SavedState *VirtualMachineInstanceSavedStateStatus `json:"savedState,omitempty"`
}

type VirtualMachineInstanceSavedStateStatus struct {
	Phase          VirtualMachineInstanceSavedStatePhase // Saving, Saved, SaveFailed, Restored, RestoreFailed
	FileName       string
	StartTimestamp, EndTimestamp *metav1.Time
	Message        string
}
```

A new `UtilityVolumeType`, `SavedState`, marks the hot plugged volume the
state is written to.

Two experimental annotations on the VMI (set them in the VM template) tune
the save and restore. They are not meant to graduate as is:

- `kubevirt.io/saved-state-parallel-channels` (default 4, max 64) overrides
  the number of parallel channels;
- `kubevirt.io/saved-state-bypass-cache: "false"` turns direct I/O off (see
  [Performance](#performance));
- `kubevirt.io/saved-state-timeout` (a duration) overrides how long a save
  may take before virt-launcher aborts it (see
  [Stuck hibernations](#stuck-hibernations)).

### Save

1. virt-api validates the request: the VMI runs, is not migrating and is not
   resuming. The VM has no pending `RestartRequired` changes, the VMI has no
   hotplugged volumes, its disks persist across a stop (no `containerDisk`
   other than a CD-ROM, `emptyDisk` or `ephemeral` volume, whose writes live
   in the launcher pod), it has no vTPM, persistent EFI variables, GPU, host
   device or SR-IOV interface (rejected explicitly in alpha, whatever the
   `LiveMigratable` condition says: their state is outside the saved state or
   bound to a node), and its device state is migratable. The
   `LiveMigratable` condition is reused for that check, except that the reason
   `DisksNotLiveMigratable` is accepted: the disks are not copied, and the VM
   is stopped before they are used anywhere else.
2. The VM controller records what the state is only valid with (see
   [Consistency](#consistency)) and creates the PVC: `ReadWriteOnce`,
   filesystem mode, owned by the VM, annotated with the node the VMI runs on,
   and sized for the plugged guest memory plus 2% and 512 MiB for the device
   state, the mapped-ram bitmap and alignment, divided by 0.94 for the
   reserved blocks and metadata of the file system (like the default file
   system overhead of CDI). A save that runs out of space fails with a message
   naming the PVC as too small and its free space, and the VM keeps running. It hot plugs the PVC
   into the VMI as a `SavedState` utility volume named `saved-state-target`,
   reusing the utility volume plumbing of memory dumps.
3. While the hibernation is `Pending` or `Saving`, the VM controller does not
   delete the VMI, whatever the run strategy asks for, within the limits of
   [stuck hibernations](#stuck-hibernations).
4. Once the volume is mounted, virt-handler commits to the save by persisting
   `Saving` on the VMI status, and only then sends the new `SaveVirtualMachine`
   RPC (see [Stuck hibernations](#stuck-hibernations) for why). virt-launcher
   takes a save once; a failed save is reported, never retried. The state file is named after the PVC, `domain-<12 hex digits of the
   SHA-256 of the PVC name>.save`, which identifies the save (see
   [Hibernating a resumed VM](#hibernating-a-resumed-vm)). virt-launcher calls
   `virDomainSaveParams` in the background with
   `image_format=sparse` (QEMU `mapped-ram` + `multifd`),
   `parallel.channels=N` and `VIR_DOMAIN_SAVE_BYPASS_CACHE` (QEMU
   `direct-io`). If libvirt rejects these parameters (libvirt < 11.2), it
   falls back to the raw format.
5. libvirt pauses the guest, writes the state, and stops the domain with the
   shutoff reason `saved`. QEMU inactivates and flushes its block devices when
   the migration completes, so the disks are consistent with the state. A
   failed save resumes the guest. virt-launcher does not resume a domain that
   is paused by an ongoing save.
6. virt-handler reports `Saved` on the VMI only once the domain is shut off
   with the reason `saved`, which libvirt sets only after a successful save.
   That reason already moves the VMI to `Succeeded`.
7. The VM controller moves the VM to `Saved` and lets the stop proceed. If
   saving fails, libvirt resumes the guest and the hibernation is rolled back:
   the VM is `Failed` with the reason, keeps running, and gets its run
   strategy back (see [Stuck hibernations](#stuck-hibernations)). Stopping it
   is left to the user, who can retry or stop it.

`virtctl hibernate --wait` returns once the state is saved and the VMI it was
saved from is gone. The launcher pod takes a few seconds to terminate after
the save, and a start issued earlier waits for it, which showed up as a
2.5 s slower resume on the PoC.

The `Hibernated` event reports how long the save took, and how long it took
from the creation of the PVC to the start of the save: provisioning, hot
plugging and mounting the volume.

### Hibernating a resumed VM

Hibernate, resume, hibernate again is the main use case. A resumed VMI keeps
`spec.savedStateSource`, and its status keeps the result of the restore. So:

- **The restore source and the save target never collide.** The PVC a VMI
  resumes from is the hot plugged utility volume `saved-state-source`, and the
  PVC a state is saved to the hot plugged utility volume `saved-state-target`,
  both in the hot plug directory `/var/run/kubevirt/hotplug-disks/`.
  virt-handler only saves to `saved-state-target`.
- **A save is identified by its file, not by the presence of a status.**
  virt-handler starts a save when a `SavedState` utility volume is mounted
  and the VMI status does not report a save (`Saving`, `Saved`,
  `SaveFailed`) to the file of that PVC. A restore status, or the failed save
  of an earlier hibernation of the same VMI, does not block it. virt-handler
  and the VM controller ignore save results, in the VMI status or in the
  domain metadata of virt-launcher, that name another file.
- **Every hibernation request gets a new PVC**, named
  `<vm>-saved-state-<first 8 characters of the VMI UID>-<5 random
  characters>`, so that a retry never reuses a PVC that is still terminating.
- **Stale PVCs are deleted.** While a VM has a saved state, the VM
  controller deletes the saved state PVCs it owns other than the current
  one, e.g. the PVC a VMI resumed from when it is hibernated again before the
  controller deleted it.
- A hibernation is rejected while a `SavedState` utility volume of an
  earlier attempt, or the restore source, is still attached. The restore
  source is unplugged seconds after the resume.

The first prototype missed this: virt-handler only saved when the VMI status
had no saved state at all, which a resumed VMI always has. The hibernation
stayed `Pending` forever with the stop held.

### Stuck hibernations

The hold on the stop must not last forever, and a hibernation that does not
complete must leave the guest running and the state PVC alone while QEMU may
write to it.

- **Cancel and the start of the save serialize on the VMI.** The VM
  controller arms a save by hot plugging the target volume; virt-handler may
  then commit to it. virt-handler commits by persisting `Saving` on the VMI
  status (a full update with the resource version it saw the armed volume
  with) and only asks virt-launcher to save once that is persisted. The VM
  controller cancels by unplugging the volume in a JSON patch that tests the
  resource version of the VMI it saw without a commit. Whichever writes first
  wins and the other one fails: either the save never starts, or the cancel
  sees the commit and lets the hibernation go on, reporting that it could not
  be cancelled. A hibernation is only marked cancelled once its save is
  disarmed. The VMI status of a save that was lost is rebuilt from the domain
  metadata of virt-launcher, so a completed save is never mistaken for a VMI
  that stopped on its own.
- **Cancel.** `discardsavedstate`, or a start, is accepted while the
  hibernation is `Pending`. The VM keeps running (see the roll back below),
  the PVC is deleted, and once it is gone `status.savedState` is removed. A
  stop is refused while the hibernation is `Pending` or `Saving`, with a
  message pointing to the discard.
- **Roll back.** A hibernation that is cancelled, or fails while the VMI
  still runs (a failed or aborted save, a timeout, a failure to record the
  source), leaves the VMI running. virt-api records the run strategy in
  `savedState.runStrategy` when hibernating. For `Always` and `Once`, which
  hibernating switched to `Halted`, the VM controller restores the run
  strategy in the spec before it releases the hold on the stop: the hold
  lasts while the hibernation is `Failed` and the spec still holds the
  `Halted` it set, and the saved state is not removed from the status before
  that, even when discarded. For `Manual` and `RerunOnFailure`, the stop
  request of the hibernation, marked with the `kubevirt.io/hibernation` data
  key and the PVC name, is withdrawn in the same status update that fails it;
  a stop request of the user is kept, and the VM stops as asked once the
  hibernation ended. Once restored, the record is cleared, so a later stop by
  the user is never undone. A hibernation whose VMI stopped on its own before
  the save is not rolled back: there is nothing left to keep running.
- **Incomplete requests.** virt-api records the hibernation in the status,
  then switches the run strategy to `Halted` in the spec: two writes that
  cannot be atomic. The VM controller does not arm a save before both are
  done. If the second write fails, virt-api withdraws the request with a patch
  that only tests the phase, the PVC name and the source VMI and sets
  `discard`, so it does not depend on what the controller already added; the
  controller also cancels a request whose stop did not follow within 30 s.
- **Timeouts.** A `Pending` hibernation that did not start saving within 10
  minutes of the request (`savedState.requestTimestamp`) is cancelled, even
  if its PVC was never created, e.g. because of a ResourceQuota. virt-launcher
  aborts a save (`virDomainAbortJob`, retried every 10 s until the job ends)
  that does not complete within max(10 minutes, 1 minute per GiB of guest
  memory), or the `kubevirt.io/saved-state-timeout` annotation; libvirt then
  resumes the paused guest and the save fails, which rolls the hibernation
  back. Past that timeout plus a minute, the VM controller only reports the
  hibernation as stuck (`HibernationStuck` event, message): it keeps the stop
  held and the PVC attached until virt-launcher reports the end of the save or
  the VMI, and so QEMU, is gone. An unresponsive virt-launcher fails the VMI,
  which ends the hibernation; a guest paused by a stuck save is never stopped
  by the hibernation. A save virt-launcher never took within 2 minutes of the
  commit is reported failed by virt-handler.
- **Deletion.** A VM being deleted holds nothing: its VMI is stopped right
  away, whatever the phase of the hibernation, and the saved state PVC, owned
  by the VM, is garbage collected with it.

A `Saving` hibernation cannot be cancelled by the user in alpha; it is
bounded by the timeout.

### Resume

The central design question is how virt-launcher, which defines and starts the
domain from the VMI spec, can start a domain from a saved state, since
`virDomainRestore` creates the domain itself.

virt-launcher keeps its normal start path: it converts the VMI to a domain,
runs the hook sidecars, allocates hotplug ports and **defines** the domain.
Only the final `virDomainCreateWithFlags` is replaced by
`virDomainRestoreParams` with:

- `file`: the state file on the PVC, hot plugged into the VMI as the
  `SavedState` utility volume `saved-state-source` (see
  [the restore source](#the-restore-source));
- `dxml`: the inactive XML of the domain virt-launcher just defined, with the
  `<cpu>` element, the `<vcpus>` list and the machine type taken from the
  saved image (`virDomainSaveImageGetXMLDesc`). With the default host-model
  CPU, the defined XML holds `host-model`, which would be expanded on this
  node, while the image holds the CPU the guest actually runs with. With
  hotpluggable vCPUs (the default when sockets can grow), libvirt numbers the
  order the vCPUs were enabled in at boot and refuses a restore whose
  definition differs ("vcpu enable order of vCPU '0' differs"). The MAC
  addresses are taken from the image too: an interface without a
  `macAddress` in the VMI spec gets the MAC of its pod interface, which
  differs in every pod, and the guest keeps the one it runs with, as after a
  live migration;
- `parallel.channels`, when the image is sparse;
- `VIR_DOMAIN_SAVE_BYPASS_CACHE` (unless turned off by annotation), plus `VIR_DOMAIN_SAVE_RUNNING`, or
  `VIR_DOMAIN_SAVE_PAUSED` whenever a boot would start paused
  (`startStrategy: Paused`, isolated emulator thread).

In libvirt (`qemuDomainRestoreInternal`), the restored definition becomes the
live definition of the defined persistent domain
(`virDomainObjListAdd(ADD_LIVE | CHECK_LIVE)`). Before that, libvirt checks
that the guest ABI of `dxml` matches the saved one
(`virDomainDefCheckABIStability`). So host-specific details (paths, tap
devices, sidecar hooks) follow the new pod, and a hardware change is refused.
QEMU is started in incoming mode and loads the state, like a live migration
target, except that libvirt drives it from a file.

Alternatives considered for the resume:

- Reusing the live migration target path. That path is driven by the source
  libvirt (peer to peer), not by the target virt-launcher, so it cannot be
  reused without the migration protocol. Doing it by hand
  (`qemu:commandline` with `-incoming defer`, then QMP `migrate-incoming
  file:…`) bypasses libvirt's state tracking and taints the domain. In QEMU,
  `cont` during the `inmigrate` run state only sets autostart, so libvirt
  would report the domain as running before it is.
- `virDomainRestore` without `dxml` would restore the source pod's XML, with
  its paths, and skip the hook sidecars of this pod.

The guest clock stood still while the VM was hibernated. After resuming,
virt-launcher sets the guest time through the guest agent, as it does after
an unpause or a migration.

#### Restore failures

The VM moves to `Restoring` only once the VMI resuming from the state was
created: a failed creation (quota, webhook timeout) keeps the state `Saved`,
and the next attempt resumes from it. virt-handler commits to the restore once
the source volume is mounted, by persisting `Restoring` on the VMI status in
the same update as the mount, and only then starts the domain. Until that
commit the guest cannot have run: a resuming VMI that stops before it keeps
the saved state (`Saved` again), while one that stops after it without a
result may have run the guest, so its state is discarded (`Failed`). The VM
controller also keeps a VMI that reported the result of its restore until the
VM recorded it, so that a lost status update never discards a valid state.

A restore fails on an incompatible CPU or QEMU, a corrupt or missing state
file, or a guest ABI mismatch. virt-launcher never boots the domain instead:
that would discard the state, which is the decision of the user (or of the
operator that hibernated the VM), not of the runtime. It reports the failure
in the domain metadata, leaves the domain defined and stopped, and refuses to
retry, so a failed restore is not looped. virt-handler reports `RestoreFailed`
on the VMI and moves it to `Failed`.

The VM controller then moves the hibernation to `RestoreFailed` with the
reason, emits a `ResumeFromSavedStateFailed` warning event, keeps the PVC,
shows the printable status `ResumeFailed`, and does not start a new VMI on its
own, whatever the run strategy. The guest never ran, so the disks still match
the saved state. Retry policy: there is no automatic retry; a start request
retries the resume once (the state goes back to `Saved`), for transient
failures such as an I/O error or a node that came back, and
`discardsavedstate` lets the VM boot (immediately with `runStrategy: Always`).
The VMI that failed to resume is kept for inspection until then; once the
resume is retried or the state discarded, the VM controller replaces it like
a VMI that never started, whatever the run strategy: `Once` and `Manual`
would otherwise keep the final VMI and never start again. The retry records
the VMI it supersedes (`supersededUID`, and clears `restoreUID`), and only
that VMI is replaced: a `Saved` state also follows a lost update of the VM
status recording the creation of a restore VMI, whose failure must be
recorded, not retried, or the VM would retry on its own while the update
fails. A restore VMI with a result the VM does not record yet, whether
`Restoring` or `Saved` after such a lost update, holds every run strategy
until the result is recorded. A start retries
the resume of a VM that always runs, reruns on failure or runs once without
changing its spec, and sends the usual start request for a manual one.
An operator that falls back to a cold boot discards; one that wants to try
another node starts again.

With the feature gate off, a VM in `RestoreFailed` boots and its saved state
is discarded, as for any boot without the state.

A resuming VMI that no node can run (no CPU-compatible node, storage pinned to
a full node) stays `Restoring`, with the scheduling failure in
`savedState.message`. A discard stops it if it did not commit to the restore
yet, and the VM boots. Without a discard, the VM controller gives up on a VMI
that did not commit within 10 minutes of its creation: it stops it and moves to
`RestoreFailed`, keeping the state. A VMI that committed is never given up on,
whatever the time loading the state takes on slow storage; past the saving
timeout of the VM, the message reports that the resume takes long. A discard
while the state is being loaded waits for the result.

#### The restore source

Kubernetes protects a PVC from deletion while a pod uses it
(`kubernetes.io/pvc-protection`), and the volumes of a pod cannot change. A
state PVC mounted as a volume of the launcher pod would stay, `Terminating`,
with the whole state file, as long as the resumed VM runs. So the VM
controller hot plugs the PVC into the resuming VMI as the `SavedState` utility
volume `saved-state-source`, right after creating it, and virt-handler waits
for the volume to be mounted before it starts the domain. Once the VM is
`Restored`, the controller unplugs the volume and deletes the PVC, which is
gone seconds after the resume. A failed restore keeps it.

The VM controller creates the resuming VMI with the volume already in
`spec.utilityVolumes`. It is the only utility volume a VMI may be created
with: the VMI admission accepts exactly the `SavedState` volume
`saved-state-source` of the claim in `spec.savedStateSource`. Hot plugging it
right after the creation instead cost 1.8 s on the PoC, when the VM
controller backed off after conflicting status updates.

The hot plug still costs time: the attachment pod can only be rendered once
virt-handler reports the SELinux context of the launcher, since it must run
with the same MCS level, i.e. once the launcher runs. On the PoC, the
attachment pod took 2.2 s to start after that, and the resume (start to
application ready) took 5.1 to 6.8 s instead of 5.3 s with the PVC mounted
as a pod volume. Starting the attachment pod together with the launcher pod
would need a known MCS level for both, e.g. one chosen by the VMI
controller. A migration target never resumes from the state.

### Node placement

A state can only resume on a node whose CPU can run the saved guest CPU. For
host-model CPUs, the VM controller records the host-model CPU and its required
features from the labels of the source node, as the migration controller does
for a migration target pod. It adds them to the node selector of the resuming
VMI. All other placement constraints come from the VM spec, which cannot
change (see below).

The resuming VMI also gets a preferred node affinity (weight 100, `matchFields`
`metadata.name`) for the node the state was saved on, recorded in the
`kubevirt.io/saved-state-source-node` annotation of the PVC. On the PoC, the
CPU node selector alone matched both nodes, and a resume on the other node
read the state over 1 Gbit/s NFS in 13 s instead of 1 s on the node that
served it. It is only a preference: node local storage already pins the
resume through the PV node affinity, and a busy source node does not block
the resume. Locality is a property of the storage, so a cluster-wide knob may
be better (see open questions).

### Consistency

A saved state is only valid with the disks it was saved with. It is used at
most once, and it is discarded when:

- the VM spec changed. `SpecHash` covers the VMI template spec, the
  instancetype and preference matchers and the DataVolume templates, but not
  the run strategy or metadata. A changed spec would usually also change the
  guest ABI, which libvirt refuses anyway;
- a disk PVC was replaced, e.g. by a `VirtualMachineRestore`, which recreates
  the PVCs (`VolumeClaimUIDs`);
- a VMI of the VM booted without the state, e.g. because the feature gate was
  turned off. Any VMI that is not the source and has no `savedStateSource`
  discards the state;
- a previous resume never reported back;
- the VM resumed from it.

KubeVirt cannot detect writes to a disk PVC by other pods while the VM is
hibernated. Resuming after such writes corrupts the guest file systems, as it
would for a paused VM. This is documented.

### Security and trust model

The saved state holds the whole guest memory: keys, credentials, whatever the
guest had in RAM. It is as sensitive as the guest disks, and it is stored in
a PVC of the namespace of the VM:

- Anyone who can create pods mounting PVCs, or read the underlying storage,
  in that namespace can read it, as they can read the disks of the VM. The
  namespace is the trust boundary, as for the disks.
- Use a storage class with encryption at rest for hibernated VMs that handle
  secrets (`virtctl hibernate --storage-class`), and restrict who can mount
  PVCs in the namespace.
- Only the VM controller may create a VMI resuming from a saved state: the
  VMI admission rejects `spec.savedStateSource` from any other requester,
  including other KubeVirt components, and the VM controller only resumes
  from a PVC it provisioned for the VM (owned by the VM and labeled as a
  saved state). A user cannot point a VMI at another PVC of the namespace.
- Someone who can write the PVC can tamper with the state, which QEMU loads
  into the guest. libvirt checks the domain definition and QEMU the device
  state, but not the integrity of the memory. An integrity digest of the
  state, computed while saving and checked before resuming, is an open
  question: it costs a full read of the state on the resume path.

### PVC life cycle

- Created by the VM controller and owned by the VM, so it is deleted with it.
- Named `<vm>-saved-state-<first 8 characters of the VMI UID>-<5 random
  characters>`, so that a new hibernation, or a retry on the same VMI, never
  reuses a PVC of a previous one that is still terminating.
- Labeled `kubevirt.io=hibernation` and `kubevirt.io/vm=<vm>`.
- Deleted after a resume or a failure or cancel, once detached from the VMI
  that saved to it or resumed from it, on `discardsavedstate`, or as stale
  when another hibernation of the VM supersedes it. A failed restore keeps it
  until it is discarded.
- The VM controller never deletes a PVC it does not own.

### Performance

The resume time is roughly:

`pod scheduling and start + launcher and libvirt start + reading the used guest memory + loading the device state`

Reading the memory is bounded by the storage read throughput:

| Used guest memory | 1 Gbit/s NFS (~110 MB/s) | 10 Gbit/s NFS (~1 GB/s) | Local NVMe (~2-3 GB/s) |
|---|---|---|---|
| 2 GiB | ~19 s | ~2 s | <1 s |
| 4 GiB | ~38 s | ~4 s | ~1.5-2 s |

These are estimates. Pod start typically adds 2 to 5 s.

What keeps it low:

- **mapped-ram (`sparse`).** Every RAM page has a fixed offset in the file.
  Zero pages are not written and the file is sparse, so a resume reads only
  the pages that were in use. With fixed offsets, several channels can read
  in parallel and with `O_DIRECT`. The raw format is one sequential stream,
  read by one thread.
- **Free page reporting** is on by default in KubeVirt. Pages the guest frees
  are returned to the host and read as zero, so they are neither written nor
  read. Guests can shrink further before a hibernation, e.g. by dropping the
  page cache, or with a full GC plus heap uncommit in a JVM. KubeVirt does not
  act inside the guest. A balloon-based shrink before saving is a possible
  extension.
- **Parallel channels** (default 4).
- **Direct I/O.** The state does not go through the page cache, which would be
  charged to the launcher pod, sized for the guest only, and would cache
  gigabytes twice on the node.

Measured on the PoC with the first prototype (2 nodes, NFS over 1 Gbit/s, ~1.7 GB of non-zero guest
RAM): resume 1 s on the node serving NFS, 13 s on the other; start to
application ready 7.3 s; save 29 s, against ~12 s for a manual QMP `migrate`
to a `file:` URI with mapped-ram and 4 multifd channels without `O_DIRECT`.
The difference was direct I/O on NFS: with
`kubevirt.io/saved-state-bypass-cache: "false"`, the save took 3 to 4 s
(32 to 41 s with direct I/O), and the attach of the volume about 2 s. With
the restore source hot plugged (final build, which saves in 2 to 3 s there),
start to application ready took 5.1 to 6.8 s
on the node serving NFS.

Without direct I/O, the pages QEMU writes and reads go through the page
cache, which is charged to the memory cgroup of the launcher pod. Measured
on the PoC:

- an 8 GiB guest with ~2.5 GB of used memory and no memory limit: +2.6 GB of
  page cache during the save, up to 2.5 GB of it dirty, and the 2.6 GB read
  by a restore stayed charged as inactive file pages for the whole run;
- a 1 GiB guest with 650 MB of random data in tmpfs and a 1294 MiB pod limit:
  the cgroup sat at its limit for the whole save (`memory.events` `max`
  1049), and the save of ~700 MB took 9 s. Reclaim and write back throttling
  kept it from an OOM kill in this run, but the pod has no headroom left for
  anything else then, on storage whose write back may stall.

So virt-launcher writes back and drops the page cache of the save file every
250 ms while saving (`sync_file_range` plus `POSIX_FADV_DONTNEED`), and drops
the state read by a restore right after it. With that, on the 8 GiB guest,
dirty pages peaked at 125 MB and the file cache at under 1 GB, without
slowing the save (3 s), and no page cache stayed charged after a restore.
It does not help when write back is slower than QEMU writes: pages under
write back cannot be dropped, and the memory limited 1 GiB guest, saving to
NFS on another node, still sat at its limit for most of its 8 s save (no OOM
kill either). A memory limited VM on slow storage should keep direct I/O,
or get headroom for the state in its pod. Direct I/O remains the default.

Trade-offs:

- Compression (libvirt's `zstd`, `lzop`, … formats) shrinks the state, and so
  the read on slow storage. But it runs as a single external process, cannot
  be combined with mapped-ram or parallel channels, and decompression becomes
  the bottleneck on fast storage. QEMU's multifd compression is not supported
  with mapped-ram either. This VEP picks mapped-ram. An `imageFormat` option
  can be added if compressed states turn out to be needed on slow storage.
- The PVC is sized for the plugged guest memory, but only the used memory is
  allocated on storage with sparse files. Storage without sparse files
  allocates the full size.
- `ReadWriteOnce` is enough: the state is written by one pod and read later by
  another one, possibly on another node. Local volumes tie the resume to their
  node.

### Interactions with other features

- A VMI resumed from a state can be live migrated once the restore source is
  unplugged, seconds after the resume. The target pod never resumes from the
  state.
- Hibernating a VMI with hotplugged volumes is rejected in alpha. The VM would
  start without the hotplugged disks attached, so the restored device state
  would not match.
- CPU and memory hotplug are live updates of the VM template, so they are part
  of the spec hash and survive a hibernation consistently.
- Network: the guest keeps its MAC addresses. With `masquerade` on the pod
  network, the guest IP does not change. `bridge` on the pod network makes the
  VMI not live migratable (unless allowed by annotation), so hibernation
  rejects it too. Established connections break, as with any migration across
  pods.

## API Examples

```shell
$ virtctl hibernate myvm --storage-class fast-rwo --wait
VM myvm hibernated to PVC myvm-saved-state-6f0a1c3e-x7k2p, saving took 6.2s
$ kubectl get vm myvm
NAME   AGE   STATUS       READY
myvm   3d    Hibernated   False
$ virtctl start myvm                  # resumes
$ virtctl discard-saved-state myvm    # or forgets the state: the next start boots
```

```yaml
status:
  printableStatus: Hibernated
  savedState:
    phase: Saved
    claimName: myvm-saved-state-6f0a1c3e-x7k2p
    fileName: domain-3b1f0c9e2a47.save
    sourceUID: 6f0a1c3e-...
    specHash: 0c4b...
    volumeClaimUIDs:
      myvm-root: 5a4f5bd1-...
    sourceNodeSelector:
      cpu-model-migration.node.kubevirt.io/Skylake-Client-IBRS: "true"
      cpu-feature.node.kubevirt.io/vmx: "true"
    saveStartTimestamp: "2026-11-04T20:00:00Z"
    saveEndTimestamp: "2026-11-04T20:00:06Z"
```

## Alternatives

- **libvirt managed save** (`virDomainManagedSave`) keeps the image in
  libvirt's state directory, which lives in the ephemeral launcher pod, and
  cannot be pointed to a PVC.
- **libvirt internal snapshots with memory** do not support raw disks, which
  KubeVirt uses by default (noted in #18054), and keep the memory inside
  qcow2 images.
- **libvirt external snapshots with memory** also create disk overlays, which
  this VEP does not need since the VM stops right after the save. They are the
  natural base for a future keepable memory snapshot.
- **A `VirtualMachineHibernation` CRD** instead of subresources and VM status.
  A hibernation is a one-at-a-time state of a VM, like a memory dump or a stop,
  rather than an object with its own life. A CRD fits keepable memory
  snapshots better (see Non Goals).
- **Keeping the domain paused in a minimal pod** still holds the memory.
- **Guest-initiated hibernation (S4)** needs guest support and configuration,
  is slow (the guest writes its memory to its own swap device) and is not
  transparent.
- **Mounting the state PVC as a volume of the launcher pod on resume** (the
  first prototype). It is faster to set up, but the PVC cannot be released
  while the pod runs, so every resumed VM held its used state, and its storage
  request, until it stopped.

## Does it belong to core KubeVirt?

Yes. It changes the VM lifecycle in virt-controller, the domain start in
virt-launcher and the handler/launcher protocol, none of which can be extended
from outside. It reuses utility volumes, which already exist for memory dumps.

## Scalability

- One PVC per hibernated VM, holding at most the plugged guest memory.
- One Node GET per hibernation in the VM controller, to record the CPU labels.
- Hibernating many VMs at once loads the storage like as many migrations to
  storage. There is no rate limit in alpha.

## Update/Rollback Compatibility

- All new fields are optional and the feature is gated.
- Turning the gate off makes the next start boot. The VM controller then
  discards the saved state, since the boot changes the disks.
- A state saved by one QEMU can be resumed by a newer one with the same machine
  type, the same guarantee as live migration. The machine type of the image
  is kept on resume and libvirt checks ABI stability. Downgrades are not
  supported, as with live migration.
- During an upgrade, an old virt-launcher has no `SaveVirtualMachine` RPC. The
  save fails and the VM keeps running.

## Functional Testing Approach

- Unit tests (in the prototype): the hibernation state machine, the hold of
  the stop, the resume preparation and invalidation, the PVC life cycle,
  subresources, admission, the virt-handler status mapping and the
  virt-launcher XML merge.
- Unit tests for hibernating a resumed VMI: the save starts although the VMI
  status reports the restore, the save target is not the restore source, a
  new PVC is used and the stale one deleted, the restore status and earlier
  failed saves are ignored, and the cancel and timeouts release the stop.
- Unit tests for the roll back of cancelled and failed hibernations (run
  strategy, stop request, hold), the save abort, restore failures, the hot
  plug and release of the restore source, and the attachment pod created
  before the launcher pod is ready.
- Unit tests for the review fixes: the cancel and the commit of the save
  serialized on the VMI resource version (disarm before cancel, a committed
  save wins, a failed disarm), the restore commit and its abandonment rules,
  the Restoring phase only after the VMI creation, the hold of a VMI with an
  unrecorded restore result, a deleted VM holding nothing, the Pending
  timeout without a PVC, a stuck save keeping its PVC, marked stop requests,
  incomplete requests, the gate off, the retry of a failed resume (only the
  VMI the retry superseded is replaced, a failure after a lost status update
  is recorded and never retried on its own), Once kept
  through a resume, every run strategy in virt-api, the admission of saved
  state sources, the PVC size and the device rejections.
- e2e (`tests/storage/hibernation.go`): hibernate and resume twice, checking
  the guest boot ID and the release of the PVC; cancel a `Pending`
  hibernation by discarding it or by starting the VM; corrupt the state,
  check `RestoreFailed` and `ResumeFailed`, retry with a start, discard and
  boot; delete a VM while its hibernation is `Pending`; resume a `Once` VM as
  `Once`; refuse a VM with a container disk or a vTPM.
- e2e (to do):
  - hibernate and resume a Fedora VM, then check that the guest uptime
    continues, a tmpfs file survives, and the guest clock is corrected;
  - resume on another node (RWX and RWO storage);
  - change the VM spec while hibernated: the state is discarded and the VM
    boots;
  - restore a VirtualMachineSnapshot while hibernated: the state is discarded;
  - a failed save (PVC too small) and a save timeout: the VM keeps running
    and the PVC is deleted;
  - discard, and VM deletion while hibernated;
  - `Manual` and `RerunOnFailure` run strategies, starting paused;
  - live migration after resuming;
  - hibernate, resume and hibernate again, several cycles, on the same and
    on another node;
  - let a `Pending` hibernation time out;
  - a VMI that no node can resume.

## Implementation History

- 2026-11: prototype on `feat/vm-memory-snapshot`.
- 2026-11: first e2e on a 2-node cluster. Fixed afterwards: hibernating a
  resumed VMI hung in `Pending`; distinct volume names and paths for the
  restore source and the save target; per-request PVC and file names; cancel
  and timeouts for stuck hibernations; preferred resume on the source node;
  `O_DIRECT` can be turned off.
- 2026-11: review fixes. The cancel and the start of the save, and the
  restore, are committed through the VMI status before virt-launcher acts;
  the VM moves to Restoring only once the VMI exists; the hibernation never
  holds a deleted VM; the Pending timeout runs from the request; a stuck save
  keeps its PVC until virt-launcher reports; hibernation stop requests are
  marked; incomplete requests are cancelled; Once is kept through a resume; a
  start retries a failed resume; only the VM controller may create a resuming
  VMI; the PVC size covers the file system overhead; vTPM, persistent EFI,
  GPUs, host devices and SR-IOV are rejected.
- 2026-11: second round. A cancelled or failed hibernation keeps the VM
  running with its run strategy, a start cancels a `Pending` hibernation,
  virt-launcher aborts a save that times out, a failed restore never boots
  and ends in `RestoreFailed`, the restore source is hot plugged and released
  after the resume, `--wait` waits for the VMI to be gone, the page cache of
  the state is dropped, the vCPU enable order is restored, and VMs with
  ephemeral disks are rejected.
- 2026-11: proposed as this VEP; implementation PR kubevirt/kubevirt#19816.

## Graduation Requirements

### Alpha

- [ ] Feature gate guards all code changes (with the gate off, VMs boot and
  saved states are discarded)
- [ ] Every run strategy covered by unit tests: hibernate, cancel, resume
- [ ] Save and resume of VMs with migratable devices and without hotplugged volumes
- [ ] e2e tests for the flows above

### Beta

- [ ] Hotplugged volumes
- [ ] Metrics: save and resume durations, state sizes, failed and cancelled
  hibernations (e.g. `kubevirt_vm_hibernation_save_duration_seconds`,
  `kubevirt_vm_hibernation_restore_duration_seconds`,
  `kubevirt_vm_hibernation_state_bytes`)
- [ ] vTPM and persistent EFI variables, which are rejected in alpha
- [ ] Decision on the tuning knobs (parallel channels, image format): KubeVirt CR or API

#### On-By-Default Readiness

- [ ] Behaviour on storage without sparse files or `O_DIRECT` is known and documented

### GA

- [ ] Feedback from production users

## Open questions

1. Should the state carry an integrity digest checked before resuming (see
   [Security and trust model](#security-and-trust-model))?
2. Should hibernation be a run strategy (e.g. `runStrategy: Hibernated`)
   instead of a subresource plus `Halted`?
3. Where should the tuning knobs live: KubeVirt CR, API options or
   annotations?
4. Should KubeVirt shrink the guest memory (balloon) before saving?
5. Should the `Pending`, `Saving` and resume timeouts be configurable beyond
   the experimental annotation, and should a user be able to abort a
   `Saving` hibernation?
6. Should a resume prefer the source node always, never, or per storage
   class?
7. Should the default for direct I/O depend on the storage (e.g. off on NFS)?
