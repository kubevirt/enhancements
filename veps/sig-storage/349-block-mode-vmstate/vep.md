# VEP #349: Block-mode backend storage for persistent VM state (EFI NVRAM, vTPM)

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: TBD (next release)
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

- [ ] (R) Enhancement issue created, which links to VEP dir in kubevirt/enhancements
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt persists VM firmware state — EFI NVRAM (Secure Boot variables) and vTPM state — in a
"backend-storage" PVC, today always created with `VolumeMode: Filesystem`. Persistent-state VMs are
already live-migratable on ordinary RWO (or RWX) filesystem storage: on migration KubeVirt creates a
fresh backend PVC on the target and copies the small state blob across (kubevirt/kubevirt#12629).

This VEP adds an optional `Block` volume mode for the backend volume, so persistent VM state can live
**natively on block storage** (DRBD/LINSTOR, Ceph RBD) — on the same kind of raw volume the VM's disks
already use, with no filesystem layer for the tens-of-MiB state blob. The EFI NVRAM is stored as a raw
pflash blob on the block device; vTPM state via swtpm's single-file backend. On RWX-Block storage the
backend volume can additionally be shared by the source and target during migration, so no
per-migration target PVC or state copy is needed; single-writer safety is then provided by the
existing QEMU/swtpm migration handoff, not by a shared filesystem.

## Motivation

RWO-Filesystem backend storage already migrates (kubevirt/kubevirt#12629 creates a fresh target PVC
and copies the small state blob during the migration window), so Block mode is **not** required for
migratability. It is offered as an additive, opt-in option for operators whose storage is natively
block:

- **Block-native, no filesystem layer.** On DRBD/LINSTOR and Ceph RBD the VM's disks already live on
  raw block volumes. A Block-mode backend lets the small VM-state volume use that same storage
  directly, instead of requiring a Filesystem PVC — and avoids a per-volume NFS-Ganesha / CephFS
  layer for operators who specifically want *shared* (RWX) state on those backends, where RWX is
  native only at the Block level.
- **RWX-Block can skip the per-migration copy.** Where the block class offers RWX (DRBD dual-primary,
  Ceph `rbd`), the backend volume is shared by source and target during migration, so no target PVC
  is created and no state copy happens. The copy is cheap for a tens-of-MiB blob, so this is an
  optimization, not a correctness requirement.
- **Consistency.** Persistent VM state lives on the same storage system, in the same volume mode, as
  the VM's disks.

`Filesystem` remains the default and the existing RWO-FS + copy-on-migrate path is unchanged.

## Goals

- Allow the persistent vm-state backend volume to be created in `Block` mode.
- Store EFI NVRAM directly on the raw block device (no filesystem) and keep it live-migratable.
- Store vTPM state on a separate raw block device (one device per blob) via swtpm's single-file
  backend, keeping it live-migratable.
- Keep `Filesystem` the default; no behavior change for existing users; guard the new behavior behind
  a feature gate during alpha.

## Non Goals

- Changing the default volume mode or removing the Filesystem path.
- A new on-disk format or a clustered filesystem; we rely on raw single-blob backends + the migration
  handoff for single-writer safety.
- Block mode for changed-block-tracking (CBT) state, which is multi-blob and still needs a filesystem.

## Definition of Users

Cluster operators and platform builders running KubeVirt on block storage (DRBD/LINSTOR, Ceph RBD)
who want persistent-EFI and/or persistent-vTPM VM state to live on the same block storage as their VM
disks — without a separate filesystem, and (on RWX-Block) without a per-migration state copy.

## User Stories

- As an operator on DRBD/LINSTOR or Ceph RBD, I want persistent-EFI Windows VMs (e.g. to enroll an
  updated Microsoft UEFI CA / Secure Boot keys) to keep their NVRAM on the same block storage as their
  disks, without a Filesystem PVC — and, on RWX-Block, without KubeVirt creating and copying a target
  state PVC on every migration.
- As an operator, I want persistent-vTPM VMs (BitLocker, measured boot) to store TPM state on that
  same block storage.

## Repos

- kubevirt/kubevirt (backend-storage, virt-controller, virt-launcher/virtwrap, API).
- External dependencies (no code, version requirements only): libvirt (`<nvram type='block'>` ≥ 8.5;
  TPM `<source type='file'>` ≥ 10.9), swtpm (`file://` backend ≥ 0.7; `--migration` ≥ 0.8).
- Related, separate: kubevirt/containerized-data-importer (StorageProfile capabilities for LINSTOR).

## Design

A new cluster-level config selects the backend volume mode:

`KubeVirt.spec.configuration.vmStateVolumeMode: Filesystem (default) | Block`

When `Block`, the backend-storage PVC is created with `VolumeMode: Block`; its access mode is
negotiated from the StorageProfile exactly as for Filesystem today (RWX preferred when
`vmStateStorageClass` is set). The PVC is rendered into virt-launcher as a raw `VolumeDevice`.

**EFI NVRAM (available today).** The OVMF VARS store is a single fixed-size blob attached by QEMU as
pflash. libvirt backs the pflash with the block device via `<nvram type='block'><source dev=.../>`.
libvirt does not auto-populate a *raw* block nvram from the firmware template (only qcow2, since
10.8), so virt-launcher seeds the blank device from the OVMF VARS template once before libvirt opens
it. The VARS travel in QEMU's normal pflash migration stream.

**vTPM (same model, libvirt-version-gated).** swtpm's single-file backend targets a block device
directly (`--tpmstate backend-uri=file://<dev>`); libvirt exposes it via the TPM emulator
`<source type='file'>` element (libvirt >= 10.9). swtpm/libvirt transfer the TPM state through the
migration stream (`CMD_SET_STATEBLOB`) and `swtpm --migration release-lock-outgoing` hands the lock
from source to target; shared storage is not required for the transfer itself.

**One raw device per blob.** Each raw block device backs exactly one state blob, so EFI NVRAM and
vTPM state cannot share a device: a VM with both persistent EFI and persistent TPM gets two backend
PVCs -- `/dev/vm-state` for the NVRAM pflash and a second `/dev/vm-state-tpm` for the swtpm state.

**Device sizing.** A Block device is consumed directly (the NVRAM device *is* the OVMF varstore
pflash), so it is sized to the firmware/state blob rather than the larger Filesystem default: the
q35 firmware-flash window caps the combined OVMF code + varstore pflash at 8 MiB, so a too-large
varstore device makes QEMU refuse to start. This requires a storage class whose minimum volume size
is small enough (a few MiB).

**Single-writer safety.** Neither the raw pflash nvram nor the swtpm `file://` backend takes a
filesystem-level lock (the swtpm *dir* backend does; the *file* backend does not). On a dual-primary
RWX-Block volume, single-writer is guaranteed by the migration protocol: QEMU pauses and flushes the
source before the target resumes (pflash), and swtpm's `--migration` release/acquire hands off the
TPM lock. The implementation rejects `Block` mode only for VMs that also enable changed-block-tracking
(CBT), whose multi-blob bitmaps need a filesystem; that combination cannot be silently misconfigured.

## API Examples

```yaml
# Cluster config
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    vmStateStorageClass: my-block-replicated-sc   # offers RWX in Block mode
    vmStateVolumeMode: Block
```

Resulting libvirt domain (EFI):

```xml
<os firmware='efi'>
  <nvram type='block' template='/usr/share/.../OVMF_VARS.fd'>
    <source dev='/dev/vm-state'/>
  </nvram>
</os>
```

Resulting libvirt domain (vTPM, on its own second device):

```xml
<tpm model='tpm-crb'>
  <backend type='emulator' version='2.0' persistent_state='yes'>
    <source type='file' path='/dev/vm-state-tpm'/>
  </backend>
</tpm>
```

## Alternatives

- **RWO-Filesystem + copy-on-migrate (the default / status quo):** the backend PVC is a plain RWO
  filesystem volume; on migration KubeVirt provisions a fresh target PVC and copies the small state
  blob (kubevirt/kubevirt#12629). Works on essentially any storage class, needs no block support, and
  is the path most deployments use. The only cost is creating + copying a tens-of-MiB PVC per
  migration. Block mode is the alternative for operators who prefer block-native state and/or want to
  avoid that per-migration copy.
- **RWX-Filesystem via NFS/CephFS:** point `vmStateStorageClass` at a class that serves RWX-FS
  (LINSTOR NFS-Ganesha export, or CephFS) for a shared filesystem backend. Works, but spins up a
  per-volume NFS export (or requires a separate filesystem system) for a tiny vm-state volume.
- **FS-on-block (swtpm `dir://` with locking) for TPM:** format a small filesystem on the block
  device and use swtpm's locking dir backend. Gains swtpm-level locking but needs mkfs/mount
  (privilege) and a strict unmount/mount handoff during migration (no concurrent RW mount of a
  non-clustered FS). Heavier than `file://` + migration handoff; kept as a fallback.

## Scalability

The vm-state volume is tiny (tens of MiB) and one per VM; no new scaling dimension beyond the existing
per-VM backend PVC. The Block path removes a per-volume NFS export on block-replicated backends,
reducing per-VM infrastructure compared to the RWX-Filesystem alternative.

## Update/Rollback Compatibility

`Filesystem` remains the default; existing VMs are unaffected. `vmStateVolumeMode` is a cluster
config field guarded by a feature gate during alpha. The volume mode is fixed at PVC creation; a VM
created with a Block backend keeps it. Downgrading KubeVirt to a version without the feature leaves
existing Block backend PVCs in place but unmanaged for the Block path — operators should migrate such
VMs off Block before downgrade (documented).

## Functional Testing Approach

- Unit: access-mode/volume-mode negotiation; `<nvram type='block'>` and TPM `<source type='file'>`
  XML emission; two-PVC creation for persistent EFI + TPM; rejection of Block when CBT is requested.
- e2e (requires a real RWX-Block backend, e.g. DRBD/LINSTOR or Ceph RBD): persistent-EFI VM
  create → reboot survives → live-migrate → state intact on target; same for vTPM.
- Negative: an aborted migration must not corrupt the blob (single-writer invariant).

## Implementation History

- EFI NVRAM and vTPM on Block: kubevirt/kubevirt#18215 (two commits; validated end-to-end on a
  DRBD/LINSTOR RWX-Block backend).

## Graduation Requirements

### Alpha

- [ ] Feature gate guards the Block path
- [ ] EFI NVRAM and vTPM on Block implemented (one raw device per blob); Block rejected for CBT VMs
- [ ] Unit tests; e2e on at least one RWX-Block backend

### Beta

- [ ] e2e covering EFI and vTPM live-migration on a block-replicated backend
- [ ] vTPM on Block exercised on a libvirt build with the TPM `<source type='file'>` element
- [ ] Documented single-writer/abort-migration behavior

### GA

- [ ] Soak in production-representative environments; no open data-integrity issues
- [ ] Documentation complete
