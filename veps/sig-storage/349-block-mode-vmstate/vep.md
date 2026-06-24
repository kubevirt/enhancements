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
"backend-storage" PVC. To live-migrate a VM that has persistent state, that PVC must be
ReadWriteMany (RWX): the source and target `virt-launcher` pods run concurrently during migration
and both attach the backend volume. Today the backend PVC is always created with
`VolumeMode: Filesystem`, so live-migrating a persistent-EFI/TPM VM requires an **RWX-Filesystem**
storage class.

This VEP adds an optional `Block` volume mode for the backend volume, so persistent VM state can live
on **block-replicated** storage that offers RWX natively only at the block level. The EFI NVRAM is
stored as a raw pflash blob on the block device; vTPM state is stored via swtpm's single-file backend
on the block device. Single-writer safety during migration is provided by the existing QEMU/swtpm
migration handoff, not by a shared filesystem.

## Motivation

Live-migration of any VM with persistent backend state requires the backend PVC on both nodes during
the migration window → **RWX is structurally required** (RWO would pin the VM). The real comparison
is therefore *RWX-Block vs RWX-Filesystem*, not *Block vs RWO-Filesystem*.

For an important, widely deployed class of storage, **RWX is only available in Block mode**:

- **DRBD / LINSTOR**: RWX is native via DRBD dual-primary at the **Block** level. Its RWX-*Filesystem*
  path exists only by layering a per-volume **NFS-Ganesha** export on each volume — heavy, plus an
  extra HA component per volume.
- **Ceph**: `rbd` provides RWX-**Block**; RWX-Filesystem is a *separate* system (CephFS, a different
  provisioner), not an attribute of the same RBD volume.

On these backends a persistent-EFI/TPM VM cannot live-migrate today unless the operator stands up a
separate NFS/CephFS layer purely for the small (tens-of-MiB) vm-state volume. A Block-mode backend
volume lets it live-migrate on the storage the cluster already runs, with no NFS/extra filesystem.

## Goals

- Allow the persistent vm-state backend volume to be created in `Block` mode.
- Store EFI NVRAM directly on the raw block device (no filesystem) and keep it live-migratable.
- Store vTPM state on the same kind of block device via swtpm's single-file backend, keeping it
  live-migratable.
- Keep `Filesystem` the default; no behavior change for existing users; guard the new behavior behind
  a feature gate during alpha.

## Non Goals

- Changing the default volume mode or removing the Filesystem path.
- A new on-disk format or a clustered filesystem; we rely on raw single-blob backends + the migration
  handoff for single-writer safety.
- Block mode for changed-block-tracking (CBT) state, which is multi-blob and still needs a filesystem.

## Definition of Users

Cluster operators and platform builders running KubeVirt on block-replicated storage (DRBD/LINSTOR,
Ceph RBD) who need persistent-EFI and/or persistent-vTPM VMs to live-migrate and survive node drains.

## User Stories

- As an operator on DRBD/LINSTOR or Ceph RBD, I want persistent-EFI Windows VMs (e.g. to enroll an
  updated Microsoft UEFI CA / Secure Boot keys) to live-migrate and survive drains, without deploying
  NFS/CephFS just for the vm-state volume.
- As an operator, I want persistent-vTPM VMs (BitLocker, measured boot) to live-migrate on the same
  block-replicated storage.

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

**vTPM (version-gated, same model).** swtpm's single-file backend targets a block device directly
(`--tpmstate backend-uri=file://<dev>`); libvirt exposes it via the TPM emulator
`<source type='file'>` element. swtpm/libvirt transfer the TPM state through the migration stream
(`CMD_SET_STATEBLOB`) and `swtpm --migration release-lock-outgoing` hands the lock from source to
target; shared storage is not required for the transfer itself.

**Single-writer safety.** Neither the raw pflash nvram nor the swtpm `file://` backend takes a
filesystem-level lock (the swtpm *dir* backend does; the *file* backend does not). On a dual-primary
RWX-Block volume, single-writer is guaranteed by the migration protocol: QEMU pauses and flushes the
source before the target resumes (pflash), and swtpm's `--migration` release/acquire hands off the
TPM lock. The implementation rejects `Block` mode for VMs that also require persistent TPM (until the
TPM phase) or CBT, so an unsupported combination cannot be silently misconfigured.

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

Resulting libvirt domain (vTPM, phase 2):

```xml
<tpm model='tpm-crb'>
  <backend type='emulator' version='2.0' persistent_state='yes'>
    <source type='file' path='/dev/vm-state'/>
  </backend>
</tpm>
```

## Alternatives

- **RWX-Filesystem via NFS/CephFS (status quo):** point `vmStateStorageClass` at a class that serves
  RWX-FS (LINSTOR NFS-Ganesha export, or CephFS). Works, but spins up a per-volume NFS export (or
  requires a separate filesystem system) for a tiny vm-state volume.
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
  XML emission; rejection of Block when persistent TPM (pre-phase-2) or CBT is requested.
- e2e (requires a real RWX-Block backend, e.g. DRBD/LINSTOR or Ceph RBD): persistent-EFI VM
  create → reboot survives → live-migrate → state intact on target; same for vTPM in phase 2.
- Negative: an aborted migration must not corrupt the blob (single-writer invariant).

## Implementation History

- EFI-on-Block initial implementation: kubevirt/kubevirt#18215.

## Graduation Requirements

### Alpha

- [ ] Feature gate guards the Block path
- [ ] EFI NVRAM on Block implemented; Block rejected for persistent-TPM/CBT VMs
- [ ] Unit tests; e2e on at least one RWX-Block backend

### Beta

- [ ] vTPM on Block via swtpm `file://` backend (once libvirt TPM file/block source is available in
      the shipped libvirt)
- [ ] e2e covering EFI and vTPM live-migration on a block-replicated backend
- [ ] Documented single-writer/abort-migration behavior

### GA

- [ ] Soak in production-representative environments; no open data-integrity issues
- [ ] Documentation complete
