# VEP #389: Disk Serial Number Assignment and Visibility

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Disk serial numbers are the primary mechanism by which operating systems reliably identify and track storage devices across reboots, hot-plug cycles, and migrations. However, there are currently three main gaps that exist today:

1. The device ordering reported in the VMI object is often an unreliable way to track disk-to-device mapping and is not idempotent after hot-plug and reboots
2. Disks are not automatically assigned a serial when one is not provided in the VM spec
3. The guest-visible physical serial for raw LUN passthrough disks is not exposed

This VEP aims to provide reliable disk serial number handling and visibility for VM operators to ensure serial numbers are consistently set, preserved, and visible throughout VM lifecycle operations.

## Motivation

When running workloads where a stable disk-to-device mapping is critical, the `VolumeStatus.target` that is reported for each disk in the VMI is not a reliable signal to use for this purpose. This target device name is generated prior to guest creation and is directly tied to the disk ordering found in the spec. However if the disk ordering is altered by hotplug/unplug operations, this reported mapping could become invalid.

The recommended solution to this problem has been to provide a serial number when defining the disks in a VM so regardless of the disk ordering throughout the lifecycle, users can always use the predefined serial numbers to maintain this mapping for their workloads. Linux machines expose them by `/dev/disk/by-id/` and Windows via the `Get-Disk` powershell command. In order to guarantee this mapping across all disks, a new mechanism is required to automatically assign predictable serial numbers for disks if one is not provided by the user.

For SCSI LUN disks, KubeVirt currently allows a serial number to be defined however, libvirt explicitly does not support serial overrides for this disk type and will be silently ignored without warning. The secondary part of this VEP will be to reject VMs with LUN disks that define a serial and instead surface the **guest-visible** physical serial (which `lsblk -o SERIAL`, `/dev/disk/by-id/`, and `Get-Disk` use) so operators can correlate VMI volumes to devices inside the guest the same way they do for emulated disks. By extending the `VolumeStatus` API, a new `serial` field can be populated for each disk in the VM.

## Goals

- Add a `serial` field to `VolumeStatus` populated with the disk serial as seen by the guest for all disk types.
- Disks that do not define a `serial` will be automatically assigned one.
- Disk serials that are auto assigned need to be stable and reliable across attach and detach cycles
- Explicitly reject `serial` on LUN passthrough disks at admission time.
- Surface the guest-visible physical LUN serial from the host block device after attach

## Non Goals

- Overriding the physical serial of a SCSI LUN passthrough device in the guest.
- Exposing NAA WWID / multipath WWID in `VolumeStatus.serial` (array-side identity is out of scope for this field; a separate status field could be considered later if needed).
- Reading LUN serials from PVC/PV metadata (`volumeHandle`, `volumeAttributes`, or vendor annotations).
- Serial number management for virtual media (CD-ROM, ISO images attached as removable media).
- Serial number management for non-disk attachments (USB passthrough, GPU, network interfaces).
- Enforcement of globally unique serial numbers across a cluster.
- Guaranteeing stable disk ordering across hot-plug and reboot (serial numbers provide stable identity independent of ordering).
- Modifying serials that have already been explicitly set by the user.

## Definition of Users

- **VM Owners**: Users running VMs who benefit from improved disk serial visibility
- **Cluster Administrator**: Users managing the Kubernetes cluster and its resources

## User Stories

1. As a VM Owner, I want a better way to maintain a stable disk-to-device mapping after VM reboots
   and hotplug operations.

2. As a VM Owner, I want the guest-visible physical serial for LUN disks on
   the VMI resource so I can correlate a volume to the device inside the guest

3. As a Cluster Administrator, I want serial numbers to be automatically assigned to VM 
   disks only when I have not explicitly provided them, so that disks have identifiers 
   without overwriting my serial assignments.

## Repos

- `kubevirt/kubevirt`

## Design

### Automatic serial assignment

The VM mutating webhook (`pkg/virt-api/webhooks/mutating-webhook/mutators/vm-mutator.go`)
is extended to iterate over disk entries in the VM spec. For any virtio-blk
(`bus: virtio`) or virtio-scsi (`bus: scsi`, `device: disk`) disk
without a user-specified `serial`, a deterministic serial is generated from the
VM namespace, VM name, and volume name:

```go
serial = hex(sha256(fmt.Sprintf("%s/%s:%s", namespace, vmName, volumeName)))[:20]
```

The key properties of this approach are:

- **Cross-VM uniqueness**: two VMs with identical volume names (e.g. both have
  a `rootdisk`) receive different serials because the VM name is part of the
  key.
- **Stable across unplug/replug**: re-attaching the same named volume to the
  same VM always produces the same serial, regardless of what happened in
  between.
- **No API calls required**: namespace, VM name, and volume name are all
  present in the object at admission time, so the webhook can compute the serial
  without hitting the API server.
- **Stable across VM restarts and VM object recreation**: the serial does not
  depend on the VM UID, so recreating a VM with the same name and volume names
  restores the original serials.

The 20-character limit is the safe ceiling for both virtio-blk and virtio-scsi
serials. LUN passthrough disks (`device: lun`) are excluded — the serial is
owned by the physical device and cannot be set via libvirt.

Explicitly set serials are never overwritten.

### `VolumeStatus.Serial`

A new `serial` field is added to `VolumeStatus`:

```go
type VolumeStatus struct {
    // existing fields ...
    // Serial is the serial number of the disk as seen by the guest OS,
    // used to correlate a VMI volume to a device inside the guest
    // (e.g. lsblk -o SERIAL, /dev/disk/by-id/, Get-Disk).
    // For virtio-blk and virtio-scsi disks this reflects the
    // value set in the disk spec (user-specified or auto-assigned).
    // For SCSI LUN passthrough disks this reflects the guest-visible
    // physical SCSI serial read from the host block device after attach.
    // +optional
    Serial string `json:"serial,omitempty"`
}
```

The virt-handler populates this field in `updateVolumeStatusesFromDomain`

- **Virtio-blk and virtio-scsi disks**: read directly from
  `disk.Serial` in the libvirt domain XML.
- **SCSI LUN passthrough disks**: read solely from the host block device
  after attach (see below). Left empty if that serial cannot be resolved.

#### LUN serial source

Once the PVC is attached and virt-handler knows the host block path used
as the domain disk source, resolve that path to the kernel device
and read the guest-visible SCSI serial from
sysfs (e.g. `/sys/block/<dev>/device/serial` / VPD page 0x80). For true
SCSI passthrough this matches what the guest will see via `lsblk` /
`Get-Disk`. Available as soon as the volume is Ready; no guest agent involved.



## API Examples

### Auto-assigned serial

User submits a VM without a serial on the data disk:
```yaml
domain:
  devices:
    disks:
    - name: rootdisk
      bootOrder: 1
      disk:
        bus: virtio
    - name: datadisk
      disk:
        bus: virtio
```

After admission, the VM spec contains:
```yaml
domain:
  devices:
    disks:
    - name: rootdisk
      bootOrder: 1
      disk:
        bus: virtio
      serial: 9f3a1c8b2d4e7f0a5b6c   # auto-assigned, stable
    - name: datadisk
      disk:
        bus: virtio
      serial: a3f1c2e8b4d07195e6f0   # auto-assigned, stable
```

### Reading serial from VMI status

```yaml
status:
  volumeStatus:
  - name: rootdisk
    target: vda
    serial: 9f3a1c8b2d4e7f0a5b6c   # auto-assigned at admission
  - name: datadisk
    target: vdb
    serial: a3f1c2e8b4d07195e6f0   # auto-assigned at admission
  - name: san-lun-01
    target: sda
    serial: S1ZKNXAG822493         # guest-visible physical SCSI serial
```

### LUN serial rejection

Submitting:
```yaml
domain:
  devices:
    disks:
    - name: san-lun-01
      lun:
        bus: scsi
      serial: my-custom-serial
```

Returns:
```
disk "san-lun-01": serial is not supported for LUN passthrough devices.
Libvirt does not forward this value to the guest for bus=scsi device=lun.
The guest-visible physical serial is available in status.volumeStatus[].serial
once the volume is attached on the node.
```

## Alternatives

### Surface serial via annotation rather than a status field

Rejected. Annotations are untyped, have no defined lifecycle, and require
manual management. A structured status field is discoverable, typed, and
automatically populated without operator action.

### Use a random UUID per disk

Rejected. A random serial generated at admission cannot be reconstructed if the
VM object is deleted and recreated. It also breaks the unplug/replug case: a
re-added volume would receive a new serial, losing the stable identity it had
before unplug.

### Use the backing PVC UID as the serial source

Considered for auto-assignment. The PVC UID is globally unique and stable even
across VM object deletion. However, resolving the PVC UID at admission time
requires a live API call to the Kubernetes API server, adding latency and a
potential failure mode to the webhook. It also does not generalise to volumes
without a backing PVC (cloudInitNoCloud, containerDisk, emptyDisk). The
namespace/vmName:volumeName hash provides equivalent stability for normal
lifecycle operations without these drawbacks.

### Use only the volume name as the serial source

Rejected. Volume names are only unique within a single VM. Two VMs with a
volume both named `rootdisk` (a common pattern) would receive identical serials,
making cross-VM tooling and storage-array correlation ambiguous. Including the
VM namespace and name in the hash key eliminates this collision.

### Read LUN serial from PVC/PV (`volumeHandle` / `volumeAttributes`)

Rejected. There is no portable Kubernetes or CSI field for a guest-visible
SCSI serial. `volumeHandle` and `volumeAttributes` are driver-defined: some
SAN drivers may embed a WWN or serial-like key, but formats are not
standardized, and many backends (RBD, cloud block) expose none.

### Expose NAA WWID instead of the guest-visible SCSI serial

Rejected for this field. WWID is the right identifier for storage-array
correlation, but guests typically look up disks by inquiry / VPD 0x80 serial
(`lsblk -o SERIAL`, `Get-Disk`). Emulated disks already put that guest-facing
string in `VolumeStatus.serial`; LUN disks must use the same contract so
operators have one correlation path. A separate WWID status field could be
proposed later if array-side identity is needed.


## Does it belong to core KubeVirt?

Yes. Auto-assignment belongs in the VM mutating webhook, which already handles
similar defaulting (e.g. firmware serial). Populating `VolumeStatus` fields is
already done by virt-handler. Host block-device inspection for the LUN serial
fits existing virt-handler node-local patterns (device-manager sysfs reads,
hotplug major:minor resolution via isolation). All changes are contained
within the core repository.

## Scalability

Auto-assignment runs once at admission and writes a single string field into the
VM spec; there is no per-reconcile cost. The domain XML path for
`VolumeStatus.Serial` (virtio disks) adds one field read per status update,
which is negligible. The LUN path reads local sysfs once the volume path is
known. The status field is a single string per volume.

## Update/Rollback Compatibility

**Auto-assignment**: existing VMs that already have serials (user-specified or
previously auto-assigned) are unaffected — the defaulter is a no-op when a
serial is present. Existing VMs without serials will have serials written into
the spec the first time the VM is updated after the feature is enabled. This is
additive and does not affect running VMIs until the next restart.

On rollback, auto-assigned serials already written into the spec remain there
and continue to function correctly, since KubeVirt simply stops assigning new
ones.

**`VolumeStatus.Serial`**: new optional field. Existing consumers that do not
read it are unaffected. On rollback, the field is absent from status but this
does not affect VM operation.

**LUN serial rejection**: new admission check. Existing VM objects with `serial`
set on a LUN disk will fail admission on the next update. During alpha, a
warning event is emitted rather than a hard rejection, to allow a migration
window.

## Live Migration and Storage Migration

### Compute live migration

Auto-assigned serials are part of the VM spec and travel with the VM object.
The domain XML generated on the destination node is derived from the same spec,
so the guest sees identical serials before and after migration. No special
handling is required.

`VolumeStatus.Serial` for emulated disks is repopulated from the domain XML on
the destination after migration completes. For LUN passthrough disks it is
repopulated from the host block device serial on the destination after attach.
There may be a brief window during the migration handoff where the field is
stale, but the serial visible inside the guest is unchanged throughout for the
same physical LUN.

### Storage live migration

The behaviour intentionally differs between disk types:

- **Emulated disks (virtio-blk, virtio-scsi)**: the serial is derived from the
  VM namespace, VM name, and volume name — not from the underlying storage. When
  the data is migrated to a different PV, the volume name in the spec is
  unchanged, so the serial is unchanged. From the guest's perspective the same
  logical disk kept working, which is the correct behaviour.

- **LUN passthrough disks**: the serial is owned by the physical device. If
  storage migration results in the guest being pointed at a different physical
  LUN (different guest-visible serial), `VolumeStatus.Serial` will update to
  the new host-resolved serial after the destination volume is attached. If
  the destination preserves the original LUN identity, the serial remains the
  same. In either case the value in `VolumeStatus.Serial` reflects the
  guest-visible device serial, which is the intended behaviour for passthrough
  devices.

## Functional Testing Approach

- Create a VM with virtio disks and no explicit serials; verify serials are
  present in the spec after admission, are deterministic (same VM name and
  volume names produce the same serials), and are unchanged after a subsequent
  VM update or unplug/replug cycle.
- Create a VM with an explicit serial on a disk; verify the admission webhook
  does not overwrite it.
- Create a VM with virtio and SCSI disks; verify `VolumeStatus.serial`
  matches the spec values (auto-assigned or user-specified) after the VM starts.
- Create a VM with a SCSI LUN disk; verify `VolumeStatus.serial` matches the
  host SCSI serial for the attached block device (e.g. sysfs `device/serial` /
  VPD 0x80), and that the same string can be used to find the device in the
  guest via `lsblk -o NAME,SERIAL`.
- Verify `VolumeStatus.serial` is populated for a hotplugged LUN disk once the
  volume is attached on the node.
- Verify `VolumeStatus.serial` remains empty when the host block device
  exposes no SCSI serial (no guest-agent fill-in).
- Attempt to create a VM with `serial` set on a LUN disk; verify the admission
  webhook returns a clear error.

## Implementation History

## Graduation Requirements

### Alpha
- [ ] Feature gate guards all code changes
- [ ] Automatic serial assignment for virtio-blk and virtio-scsi
      disks in the VM mutating webhook
- [ ] `VolumeStatus.Serial` populated for virtio-blk and virtio-scsi
      disks from domain XML
- [ ] `VolumeStatus.Serial` populated for SCSI LUN disks from host
      guest-visible SCSI serial after attach (host-only; no guest-agent path)
- [ ] Warning event for VMs with `serial` set on LUN disks (hard rejection
      deferred to beta)
- [ ] Unit and functional tests for all three changes

### Beta
- [ ] Hard validation rejection of `serial` on LUN disks replaces warning
- [ ] Functional test coverage for LUN serial via host SCSI serial across
      storage backends, including multipath where applicable
- [ ] User-guide documentation updated

#### On-By-Default Readiness

Auto-assignment and `VolumeStatus.Serial` are purely additive and safe to enable
by default. The LUN serial rejection requires that existing misconfigured VMs
have had a full alpha cycle to be updated before the hard rejection lands in
beta.

### GA
- [ ] Feature gate removed
- [ ] No regressions over one full beta release cycle
- [ ] E2E coverage for all user stories
