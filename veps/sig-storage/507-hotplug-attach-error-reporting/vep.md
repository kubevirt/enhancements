# VEP #507: Structured domain sync errors, starting with hotplug attach failures

## VEP Status Metadata

### Target releases

- This VEP targets GA for version: v1.11

### Rationale for targeting GA directly

This is a bug fix in how an existing failure is reported, not a new feature. It adds no API field and no
feature gate, and users do not opt in. The only user-visible change is a more precise reason and message on the
volume that failed, which an older component simply does not produce (see Update/Rollback Compatibility).
There is nothing for an alpha or beta phase to gate.

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

When a domain sync fails for one volume, virt-handler cannot tell which volume it was. virt-launcher reports the
failure as a single error for the whole sync, so it can only be shown on the VMI's `Synchronized` condition and in
a `SyncFailed` event.

This VEP proposes a structured way for virt-launcher to report per-volume errors on a failed domain sync. It uses
the gRPC rich error model that kubevirt/kubevirt#18285 is introducing: the sync's gRPC status error carries one
detail per affected volume, and virt-handler unwraps them as typed causes.

The first use is hotplug attach failures, which today leave the volume at `MountedToPod` with reason
`VolumeMountedToPod`, as if nothing went wrong. With this VEP, virt-handler sets reason `HotplugAttachFailed` and
the hypervisor error as the message on the affected volume only. Detach and disk update failures, which come from
the same sync, can use the same mechanism later (see [Future use cases](#future-use-cases)).

## Motivation

A common way to hit this today: `virtctl addvolume` uses the volume name as the disk serial when `--serial` is
not set, and defaults to the SCSI bus. QEMU rejects SCSI serials longer than 36 characters, but admission accepts
up to 256. Hotplugging a PVC with a longer name passes admission and then never attaches. Observed on stock
KubeVirt v1.9.0:

- The failing volume reports `MountedToPod` / `VolumeMountedToPod` / "has been mounted in virt-launcher pod".
- `Synchronized=False` and `SyncFailed` (25 times in about 90 seconds) carry
  `'device_add': The serial number can't be longer than 36 characters`, but nothing ties it to a volume.
- `syncDisks` returns on the first failed attach, so a valid volume hotplugged afterwards has the same status
  as the failing one. There is no way to tell them apart.

The user has to read virt-launcher logs to find out which volume is broken and why
(kubevirt/kubevirt#19245).

Adding an admission check for this particular limit is not the answer. As the review of kubevirt/kubevirt#18734
put it, KubeVirt webhooks should not try to reimplement every limitation of the underlying stack, which leaks
implementation details into the management layer. Instead, when QEMU or libvirt rejects something, the error
should reach the VM owner clearly, on the object they are looking at. This VEP does that for volumes.

kubevirt/kubevirt#18734 went through three versions, each of which showed something to avoid:

- Rejecting long serials at admission for every bus. Withdrawn after review: for virtio-blk, QEMU silently
  truncates the serial to 20 characters instead of rejecting it, so the check would break existing virtio-blk
  VMs that work today. More generally, it duplicates a hypervisor limit in a webhook, and it only covers one
  trigger, not attach failures in general.
- Inferring the failed volume in virt-handler from the sync-wide `syncErr`. It can blame the wrong volume,
  because the error carries no volume identity.
- Recording errors in virt-launcher's metadata cache and adding a `DomainStatus.Disks` field. It works, but it
  extends the metadata cache with map-typed, per-resource error state and adds a second channel for errors next
  to the sync result. That sets a pattern for error propagation that we may not want long-term.

kubevirt/kubevirt#18285 is moving handler↔launcher gRPC from `Response{Success, Message}` to standard gRPC
status errors, and kubevirt/kubevirt#18423 (under review) uses `status.WithDetails()` for `GetVMStats`. Carrying
structured error details on that path gives one consistent way to report per-resource sync failures, for
attach errors now and for similar cases later, without interim plumbing that would need reworking.

## Goals

- Define one structured, extensible way for virt-launcher to report per-volume errors on a failed domain sync:
  a detail on the gRPC status error returned by `SyncVirtualMachine`.
- Give virt-handler a typed Go error it can inspect with `errors.As`, following the shape of Kubernetes'
  `StatusError` / `StatusDetails.Causes`.
- Use it to report a hypervisor-level hotplug attach failure on the affected volume's status, without affecting
  other volumes.
- Keep current behavior otherwise: no new volume phase, and failed attaches keep being retried.

## Non Goals

- Migrating the remaining handler↔launcher RPCs to gRPC status errors. That is kubevirt/kubevirt#18285.
- Reporting detach and disk update failures on the volume. The mechanism supports them (see
  [Future use cases](#future-use-cases)), but how virt-handler should show them is left to follow-ups.
- Errors that are not returned by a domain sync, such as guest I/O errors (see
  [Future use cases](#future-use-cases)).
- Changing `syncDisks` to keep attaching other disks after one fails. Worth discussing separately; the
  error detail format allows more than one cause, so it does not block that change.
- Adding admission checks that duplicate hypervisor limits, such as QEMU's 36-character limit for SCSI disk
  serials. The point of this VEP is to report such failures instead (see Motivation).
- Assigning or exposing disk serials. That is VEP-389 (kubevirt/enhancements#490). It avoids some serial-related
  attach failures by assigning serials that fit, but it does not change explicitly set serials, and it does not
  cover attach failures in general, which is what this VEP reports.
- Any change to the VMI CRD.

## Definition of Users

- VM owners and cluster admins who hotplug volumes and need to understand why a volume never became ready.
- Tooling and UIs that display `volumeStatus` (e.g. `kubectl describe vmi`, OpenShift console).
- KubeVirt developers adding per-resource error reporting between virt-launcher and virt-handler.

## User Stories

- As a VM owner, when a volume I hotplugged does not show up in the guest, I want its `volumeStatus` to tell
  me why, so I can fix it (for example, by setting `--serial`) without reading virt-launcher logs.
- As a VM owner hotplugging several volumes, I want only the volume that actually failed to show an error.
- As a cluster admin, I want a warning event that names the failing volume, so alerts and dashboards can point
  to it.
- As a KubeVirt developer, I want one way to return per-volume sync errors, so a new case does not need new
  plumbing between virt-launcher and virt-handler.

## Repos

- kubevirt/kubevirt

## Design

The design follows the outline proposed in the kubevirt/kubevirt#18734 review. It needs `SyncVirtualMachine`
to return a gRPC status error on failure, which kubevirt/kubevirt#18423 introduces (see
[Dependency on kubevirt/kubevirt#18423](#dependency-on-kubevirtkubevirt18423)).

1. **Proto (`pkg/handler-launcher-com/cmd/v1`)**: add one generic detail message. The service signatures stay the
   same.

   ```proto
   message VolumeError {
     enum Operation {
       OPERATION_UNSPECIFIED = 0;
       ATTACH = 1;
     }
     string volumeName = 1;
     Operation operation = 2;
     string message = 3;
   }
   ```

   Only `ATTACH` is defined by this VEP. A new case adds an enum value, which is an additive proto change.

2. **virt-launcher**: when `AttachDeviceFlags` fails in `syncDisks`, return a typed Go error that carries the
   volume name and wraps the libvirt error. In `SyncVirtualMachine`, the cmd-server checks for it with
   `errors.As` and returns:

   ```go
   st, _ := status.New(codes.Internal, "failed to sync vmi: "+msg).WithDetails(&cmdv1.VolumeError{
       VolumeName: name,
       Operation:  cmdv1.VolumeError_ATTACH,
       Message:    attachErr.Error(),
   })
   return nil, st.Err()
   ```

   If `WithDetails` fails, it falls back to the plain status error, so the sync error is never lost.

3. **cmd-client (virt-handler side)**: `handleError` already wraps status errors with `%w`. It additionally
   decodes the `VolumeError` details into a typed error that implements `error` and `Unwrap()`:

   ```go
   type SyncError struct {
       Err    error            // the wrapped gRPC status error
       Causes []SyncErrorCause // one per affected volume
   }

   type SyncErrorCause struct {
       Type    SyncErrorCauseType // e.g. VolumeAttachFailed, mapped from VolumeError.Operation
       Name    string             // volume name
       Message string
   }
   ```

   Callers that only check `err != nil` keep working. Callers that need detail use `errors.As`, the same way
   `apierrors.IsNotFound()` is used today. Details with an operation the client does not know are ignored.

4. **virt-handler**: `updateVMIStatus` already receives the sync error. It passes the extracted causes down to
   `updateVolumeStatusesFromDomain` and `updateHotplugVolumeStatus`. For a hotplug volume that is mounted, still
   in the spec, and at `MountedToPod`:
   - if the sync error has a `VolumeAttachFailed` cause for this volume, set reason `HotplugAttachFailed` and
     message `Failed to attach hotplugged volume <name> to VM: <error>`, and emit one Warning event when the
     reason first changes;
   - if the sync succeeded, reset the reason to `VolumeMountedToPod`;
   - if the sync failed with no cause for this volume, leave the volume status unchanged, because the failure
     may have happened before `syncDisks` ran.

   The phase never changes, so the existing retry loop keeps retrying the attach. The reason is named
   `HotplugAttachFailed` rather than `HotplugFailed`, because virt-handler already emits a `HotplugFailed` event
   when mounting a hotplug volume into the virt-launcher pod fails, which is a different step.

The error lives only in the failing sync's return value. Nothing is stored in virt-launcher, there is no
cleanup when a disk is removed, and after a virt-handler restart the next failed sync reports it again.

### Future use cases

The same detail and typed error are meant to cover other per-volume failures returned by a domain sync. These
are not implemented by this VEP:

- **Detach failures.** `syncDisks` returns the `DetachDeviceFlags` error the same way it returns attach errors
  today. An `Operation` value for detach would let virt-handler report it on the volume being unplugged.
- **Disk update failures**, such as CD-ROM inject and eject. These come from `UpdateDeviceFlags` in the same
  function, and would get their own `Operation` value.
- **Guest I/O errors** are different: they are not returned by a sync. virt-launcher learns about them from a
  domain event and currently only sends an `IOerror` event. The `VolumeError` format could describe them too, but
  carrying them to virt-handler would need the notify path rather than `SyncVirtualMachine`, so they are out of
  scope here.

### Live migration

No migration-specific handling is needed. A VMI with a hotplug volume whose attach keeps failing can still be
migrated: the failed disk is not part of the source domain, so it is not part of the migrated domain either.
Once the VMI runs on the target, virt-handler on the target node syncs it, `syncDisks` in the target virt-launcher
retries the attach, and a persistent failure is reported again from there. No error state needs to be carried
from the source, because none is stored. This flow will be covered by a functional test (see Functional
Testing Approach).

### Dependency on kubevirt/kubevirt#18423

kubevirt/kubevirt#18423 moves the cmd-server RPCs, including `SyncVirtualMachine`, to gRPC status errors. It was
on hold during the v1.10 cycle and was taken off hold on 2026-11-04; it is now under review. This VEP only needs
one part of it: the failure path of `SyncVirtualMachine` returning a status error that can carry details. The
client side is already in place, because `handleError` handles status errors since kubevirt/kubevirt#18375.

The plan is to build on top of kubevirt/kubevirt#18423. If it has not merged when implementation starts, the
implementation PR changes only the failure path of `SyncVirtualMachine`, coordinated with the #18423 author to avoid
conflicting with it. Old clients keep working either way, because `handleError` falls back to
`Response{Success, Message}`.

## API Examples

No CRD change. A volume with a failed attach, as seen in `vmi.status.volumeStatus` (output from the prototype
in kubevirt/kubevirt#18734):

```yaml
- name: hotplug-volume-with-a-very-long-pvc-name-xyz
  phase: MountedToPod
  reason: HotplugAttachFailed
  message: "Failed to attach hotplugged volume hotplug-volume-with-a-very-long-pvc-name-xyz to VM: virError(Code=1, Domain=10, Message='internal error: unable to execute QEMU command 'device_add': The serial number can't be longer than 36 characters')"
  hotplugVolume:
    attachPodName: hp-volume-xxxxx
    attachPodUID: ...
```

A second volume hotplugged after it keeps `reason: VolumeMountedToPod`.

## Alternatives

- **One detail message per case** (e.g. `DiskAttachError{name, message}`, then `DiskDetachError`, and so on).
  Simpler for the first case, but every new case adds a message and a decoding branch in the client. A single
  `VolumeError` with an `Operation` keeps the client generic and maps directly onto `SyncErrorCause.Type`.
- **Admission validation of serial length** (first version of #18734, for every bus). Withdrawn: virtio-blk
  truncates long serials instead of rejecting them, so it would break existing VMs. A SCSI-only check avoids that,
  but it still duplicates a hypervisor limit in a webhook and only covers one trigger.
- **Infer the volume from the sync-wide `syncErr` in virt-handler** (second version of #18734). Rejected: the
  error has no volume identity, so it can blame the wrong volume. That version also added a terminal
  `HotplugFailed` phase, which stopped retries.
- **`DomainStatus.Disks` filled from the metadata cache** (third version of #18734). It works: verified end to
  end, including across virt-handler restarts. Rejected because it extends the metadata cache with map-typed
  per-resource error state and adds a second channel for errors next to the sync result.
- **A new terminal volume phase.** Rejected: it changes the API and stops retries, so a transient failure would
  not recover by itself.

## Does it belong to core KubeVirt?

Yes. It changes the internal virt-launcher ↔ virt-handler protocol and virt-handler's volume status handling,
both of which only exist in core KubeVirt.

## Scalability

Negligible. Details are only produced when a sync fails, contain at most one entry per disk, and travel on the
existing `SyncVirtualMachine` call. Nothing new is stored or polled.

## Update/Rollback Compatibility

virt-handler and virt-launcher can run different versions during an update, so both directions must work:

- **New virt-handler, old virt-launcher**: the old launcher returns no details, so no causes are found and
  behavior is the same as today.
- **Old virt-handler, new virt-launcher**: the old handler sees a status error and ignores unknown details.
  kubevirt/kubevirt#18375 already handles status errors on the client side, so behavior is the same as today.

The detail message is additive, and so is each later `Operation` value: a client ignores operations it does not
know. Version skew for the move to status errors itself is covered by the multi-release plan in
kubevirt/kubevirt#18285.

## Functional Testing Approach

- Unit tests at each layer: virt-launcher returns the typed error from `syncDisks`; cmd-server attaches the
  detail; cmd-client decodes it into `SyncError` and ignores unknown operations; virt-handler sets the reason
  only on the matching volume, keeps the other volumes' reasons, emits one Warning event, and resets the reason
  after a successful sync.
- One e2e test that hotplugs a volume the hypervisor rejects and checks the reason and message on that volume
  only, then live-migrates the VMI and checks that the target reports the same failure. Hotplugging a disk on
  the SCSI bus with a serial longer than 36 characters is a reliable trigger: admission accepts it and QEMU
  rejects it at `device_add`.

## Implementation History

- 2026-08: First version of kubevirt/kubevirt#18734 (serial length check at admission for every bus), withdrawn
  after review. Second version (sync-wide `syncErr`), replaced after review.
- 2026-09: Third version of kubevirt/kubevirt#18734 (`DomainStatus.Disks` via metadata cache), verified on
  KubeVirt v1.9.0. Problem reported in kubevirt/kubevirt#19245.
- 2026-10: Review on kubevirt/kubevirt#18734 asked for a VEP aligned with kubevirt/kubevirt#18285.
- 2026-11: VEP proposed.

## Graduation Requirements

Not applicable: there is no feature gate, and the change is transparent to users apart from the more precise
volume status. It ships in a single release once the following are met:

### Alpha

Not applicable.

### Beta

Not applicable.

#### On-By-Default Readiness

Always on. Old components ignore the detail, as described in Update/Rollback Compatibility.

### GA

- `SyncVirtualMachine` returns a gRPC status error on failure, through kubevirt/kubevirt#18423 or the minimal
  change described in [Dependency on kubevirt/kubevirt#18423](#dependency-on-kubevirtkubevirt18423).
- `VolumeError` detail, typed client error and virt-handler handling merged with unit tests.
- e2e test covering a rejected hotplug attach.
- User-guide note on the `HotplugAttachFailed` volume reason.
