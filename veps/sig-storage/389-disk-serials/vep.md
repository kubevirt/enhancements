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
3. The physical serial for raw LUN passthrough disks are not exposed

This VEP aims to provide reliable disk serial number handling and visibility for VM operators to ensure serial numbers are consistently set, preserved, and visible throughout VM lifecycle operations. 

## Motivation

When running workloads where a stable disk-to-device mapping is critical, the `VolumeStatus.target` that is reported for each disk in the VMI is not a reliable signal to use for this purpose. This target device name is generated prior to guest creation and is directly tied to the disk ordering found in the spec. However if the disk ordering is altered by hotplug/unplug operations, this reported mapping could become invalid.

The recommended solution to this problem has been to provide a serial number when defining the disks in a VM so regardless of the disk ordering throughout the lifecycle, users can always use the predefined serial numbers to maintain this mapping for their workloads. Linux machines expose them by `/dev/disk/by-id/` and Windows via the `Get-Disk` powershell command. In order to guarantee this mapping across all disks, a new mechanism is required to automatically assign predictable serial numbers for disks if one is not provided by the user. 

For SCSI LUN disks, KubeVirt currently allows a serial number to be defined however, libvirt explicitly does not support serial overrides for this disk type and will be silently ignored without warning. The secondary part of this VEP will be to reject VMs with LUN disks that define a serial and instead surface the predefined physical serial from the guest for these disk types. By extending the `VolumeStatus` API, a new `serial` field can be populated for each disk in the VM. 

## Goals

- Add a `serial` field to `VolumeStatus` populated with the disk serial as seen by the guest for all disk types.
- Disks that do not define a `serial` will be automatically assigned one.
- Disk serials that are auto assigned need to be stable and reliable across attach and detach cycles
- Explicitly reject `serial` on LUN passthrough disks at admission time.

## Non Goals

- Overriding the physical serial of a SCSI LUN passthrough device in the guest.
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

2. As a VM Owner, I want the physical serial numbers for LUN disks to be visible on the VMI resource

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
    // Serial is the serial number of the disk as seen by the guest OS.
    // For virtio-blk and virtio-scsi disks this reflects the
    // value set in the disk spec. For SCSI LUN passthrough disks this
    // reflects the serial reported by the physical storage device.
    // Requires the QEMU guest agent to be running.
    // +optional
    Serial string `json:"serial,omitempty"`
}
```

The virt-handler populates this field in `updateVolumeStatusesFromDomain`:

- **Virtio-blk and virtio-scsi (emulated) disks**: read directly from
  `disk.Serial` in the libvirt domain XML. No guest agent required.
- **SCSI LUN passthrough disks**: read from the QEMU guest agent via
  `guest-get-disks`, correlated to the volume by matching
  `VolumeStatus.Target` against the device name in the agent response.
  Left empty if the guest agent is unavailable; populated on the next
  successful agent poll.

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
    serial: 6000c29abcdef1234567   # physical device serial from storage array
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
The physical device serial is available in status.volumeStatus[].serial
once the VM is running.
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

Considered. The PVC UID is globally unique and stable even across VM object
deletion. However, resolving the PVC UID at admission time requires a live API
call to the Kubernetes API server, adding latency and a potential failure mode
to the webhook. It also does not generalise to volumes without a backing PVC
(cloudInitNoCloud, containerDisk, emptyDisk). The namespace/vmName:volumeName
hash provides equivalent stability for normal lifecycle operations without these
drawbacks.

### Use only the volume name as the serial source

Rejected. Volume names are only unique within a single VM. Two VMs with a
volume both named `rootdisk` (a common pattern) would receive identical serials,
making cross-VM tooling and storage-array correlation ambiguous. Including the
VM namespace and name in the hash key eliminates this collision.

## Does it belong to core KubeVirt?

Yes. Auto-assignment belongs in the VM mutating webhook, which already handles
similar defaulting (e.g. firmware serial). Populating `VolumeStatus` fields is
already done by virt-handler, and the guest-agent infrastructure for LUN serials
is already present in the agent-poller package. All changes are contained within
the core repository.

## Scalability

Auto-assignment runs once at admission and writes a single string field into the
VM spec; there is no per-reconcile cost. The domain XML path for
`VolumeStatus.Serial` (virtio disks) adds one field read per status update,
which is negligible. The guest-agent path (LUN disks) reuses the existing agent
polling cycle with no additional scheduling overhead. The status field is a
single string per volume; no meaningful memory or etcd impact.

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
repopulated via the guest agent. There may be a brief window during the
migration handoff where the field is stale, but the serial visible inside the
guest is unchanged throughout.

### Storage live migration

The behaviour intentionally differs between disk types:

- **Emulated disks (virtio-blk, virtio-scsi)**: the serial is derived from the
  VM namespace, VM name, and volume name — not from the underlying storage. When
  the data is migrated to a different PV, the volume name in the spec is
  unchanged, so the serial is unchanged. From the guest's perspective the same
  logical disk kept working, which is the correct behaviour.

- **LUN passthrough disks**: the serial is owned by the physical device. If
  storage migration results in the guest being pointed at a different physical
  LUN (different WWID/serial), `VolumeStatus.Serial` will update to reflect the
  new physical serial after the guest agent reports it. If the destination array
  preserves the original LUN identity (e.g. via replication with an identical
  WWID), the serial remains the same. In either case the value in
  `VolumeStatus.Serial` reflects reality, which is the intended behaviour for
  passthrough devices.

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
  serial visible in the guest via `lsblk -o NAME,SERIAL`.
- Verify `VolumeStatus.serial` is populated for a hotplugged LUN disk after
  the guest agent reports it.
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
- [ ] `VolumeStatus.Serial` populated for SCSI LUN disks via guest agent
- [ ] Warning event for VMs with `serial` set on LUN disks (hard rejection
      deferred to beta)
- [ ] Unit and functional tests for all three changes

### Beta
- [ ] Hard validation rejection of `serial` on LUN disks replaces warning
- [ ] Functional test coverage for LUN serial via guest agent across storage
      backends
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
