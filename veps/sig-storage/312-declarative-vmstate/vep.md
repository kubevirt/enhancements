# VEP #312: Declarative VMState PVC

## VEP Status Metadata

### Target releases

- Alpha: v1.10
- Beta: TBD
- GA: TBD

### Release Signoff Checklist

- [x] (R) Enhancement issue created, links to this VEP dir
- [ ] (R) Alpha target approved
- [ ] (R) Beta target approved
- [ ] (R) GA target approved

## Overview

This VEP introduces a declarative API field (`virtualMachineState`) in the VMI spec to reference or template VMState PVCs. It lets a VMState PVC be reused across VMs (for migration or templating) and gives explicit control over storage properties like size and storage class, replacing the implicit PVC creation that happens today when TPM, EFI, or CBT is enabled.

## Motivation

VMState PVCs are created implicitly when persistent TPM, persistent EFI, or CBT is enabled. Users cannot reuse an existing VMState PVC in a new VM, pre-provision PVC size (problematic for CBT metadata growth) or declaratively manage all VM resources (breaks GitOps workflows). The PVC is created with a randomly generated name and is not exposed anywhere in the VM manifest, so external tools like migration controllers or backup operators have no straightforward way to discover or manage it.

Exposing the PVC in the API makes it discoverable and allows tools to manage it through standard Kubernetes patterns. This doesn't change what is stored or widen any security boundary, since the data was already persisted and PVCs remain namespace-scoped. The implicit path stays as the default for users who don't need PVC reuse, while the declarative path is opt-in for workflows like cloning, migration, templating and backup/restore.

## Goals

- Declarative API field to reference existing VMState PVCs or template new ones
- VM migration with full state preservation (TPM, EFI, CBT)
- Full VM cloning and templating support
- Explicit control over PVC properties (size, name, storageClass)
- Backward compatibility with implicit PVC creation
- VM-agnostic PVC filesystem structure (no embedded UUIDs or VM identifiers)
- Establish a consistent nomenclature for "VirtualMachineState" across Kubevirt and other projects. Previously, terms like "persistent state", "backend storage" and "VirtualMachineState" were used interchangeably.

## Non Goals

- Auto-migration of existing VMs to the declarative API
- Removing implicit PVC creation (coexistence is required)
- Multiple VMState PVCs per VM

## Definition of Users

- **VM owners:** manage VM lifecycle and state across clusters
- **Platform operators:** provision storage classes and enforce resource policies
- **GitOps tooling:** declaratively manage all VM resources via Git

## User Stories

- **Migration:** Reuse an existing VMState PVC in a new VM after cross-cluster migration or VM recreation, preserving TPM/EFI state without relying on implicit PVC naming or label manipulation
- **Templating:** Create VM template with pre-configured EFI setup, reused across instantiated VMs
- **Cloning:** Clone VM with existing TPM state to preserve guest OS trust
- **GitOps:** Define all VM resources (including the VirtualMachineState PVC) in Git, apply declaratively
- **CBT:** Pre-provision large VMState PVC to avoid CBT metadata space exhaustion

## Repos

kubevirt/kubevirt

## Design

### API

This VEP introduces a new `virtualMachineState` field of type `VirtualMachineStateSpec` in `VirtualMachineInstanceSpec`:

```go
type VirtualMachineStateSpec struct {
    // Template the controller uses to create PVCs it owns.
    // Used for the initial PVC when Source is unset, and for every new PVC
    // the controller creates later for operations like live migration.
    // +optional
    VolumeClaimTemplate *v1.PersistentVolumeClaimTemplate `json:"volumeClaimTemplate,omitempty"`
    // Source is an existing PVC to adopt as the starting state.
    // +optional
    Source *VirtualMachineStateSource `json:"source,omitempty"`
}

type VirtualMachineStateSource struct {
    // Name of the source PVC.
    Name string `json:"name"`
    // A struct wrapping a single `name` today so the source can grow later if necessary (other kinds like volume snapshots, namespace, adoption policy) without a breaking change.
}
```

The two fields are not mutually exclusive. At least one must be set:
- `volumeClaimTemplate` only: controller creates and owns the PVC.
- `source` only: adopt an existing PVC as-is.
- both: adopt `source` as the starting PVC, and use the template for every PVC the controller creates afterwards (this is what enables RWO live migration on the adoption path).

VolumeMode must be Filesystem, other values are rejected.

On the `volumeClaimTemplate` path the controller owns the name via `GenerateName` (like generic ephemeral volumes) and records the live name in status. `volumeClaimTemplate.metadata.name` is rejected and users who need a fixed name pre-create the PVC and set `source`.

Each VMState PVC gets two labels, whether it was created from a template or adopted via `source`. One is a lock that tells if the PVC is in use, so two VMs don't write the same state at once. The other maps the PVC to its VM, so the controller can find it again if it crashes before writing status. The lock is the source of truth for concurrent use: another VM may adopt the PVC only when the current holder is not running.

Status reports the live PVC via `VolumeStatus` and is the source of truth for its name, which may differ from `source` after a migration. 

```go
  // VirtualMachineStateVolume tracks the live VirtualMachineState PVC
	// (UEFI, TPM, CBT state).
	// +nullable
	// +optional
	VirtualMachineStateVolume *VolumeStatus `json:"virtualMachineStateVolume,omitempty" optional:"true"`
```

Virt-controller populates this for both declarative and implicit paths.

### Validation

- At least one of `source` or `volumeClaimTemplate` must be set. They are not mutually exclusive
- Immutability: `virtualMachineState` fields are immutable after VM creation, except for the template's storage capacity and storageClassName
- Volume mode: referenced PVCs must use Filesystem volume mode
- Template name: `volumeClaimTemplate.metadata.name` is rejected.
- PVC existence: if a referenced PVC does not exist or is deleted while the VM exists, virt-controller sets a `VirtualMachineStatePVCNotFound` condition on the VM status.
- Exclusive ownership: if another running VM already references the same VirtualMachineState PVC, virt-controller prevents the VM from starting and reports a condition.

### VirtualMachineState PVC Structure

The current layout is VM-specific: TPM state lives under a UUID-named directory and EFI vars carry the VM name in the filename, which prevents PVC reuse across VMs.

This VEP introduces a canonical, VM-agnostic layout:

```
/
├── tpm/                  # Canonical TPM root (replaces UUID-named directory)
│   └── tpm2/             # swtpm-managed subdirectory (preserved as-is)
│       └── tpm2-00.permall
├── swtpm-localca/        # Local CA for swtpm EK certificate generation
├── efi/
│   └── efi_vars.fd
├── cbt/
└── meta/
```

The `swtpm-localca/` directory is only written during first boot; on subsequent boots and adoptions, `swtpm_setup` runs with `--not-overwrite` and skips certificate generation.

### Libvirt Path Mapping

Libvirt expects TPM state at `<stateroot>/<vm-uuid>/tpm2/` and does not expose a configurable state path in domain XML. The VMState PVC is mounted as a whole volume (no SubPath) at `VMStatePVCMountPath`, and virt-launcher creates an ephemeral symlink before starting libvirt:

```
PathForSwtpm(vmi)/<vm-uuid>  →  VMStatePVCMountPath/tpm/
```

Libvirt resolves the UUID directory via the symlink and uses existing state. EFI does not need a symlink as the NVRAM path is directly configurable in domain XML.

This keeps privileges and side effects to a minimum. Creating the symlink with `ln -s` needs no capabilities, and virt-launcher can read it and reach the target volume without a custom SELinux policy. The symlink is ephemeral: it disappears when the pod terminates, and once the initial state is written the PVC itself is never modified.

### Legacy PVC Migration

Opting in on an existing VM, by adding `virtualMachineState` that points at a legacy PVC, triggers a one-time normalization: virt-launcher rewrites the legacy layout to the canonical one on first boot. TPM and EFI are handled independently, each guarded by its own idempotency gate so a second boot does nothing.

Virt-launcher identifies a legacy VMState PVC by its `persistent-state-for` label, which carries the original VM name (legacy PVCs also use the `persistent-state-for` GenerateName prefix). Virt-controller checks for that label before treating a referenced PVC as a legitimate legacy PVC.

TPM migration is skipped if `/tpm/` already exists. Otherwise virt-launcher scans the top level for subdirectories, ignores `lost+found`, and keeps only UUID-formatted names (`^[0-9a-f]{8}-...-[0-9a-f]{12}$`). A single match is renamed to `/tpm/` through an atomic two-phase rename (`→ /tpm.migrating → /tpm`), so a crash mid-rename can't leave a half-migrated directory. If more than one UUID directory matches, virt-launcher can't tell which is the real one, so it emits a warning event on the VMI listing the candidates, skips TPM migration, and starts from fresh state, leaving the old directories in place for manual recovery. EFI-only PVCs have no UUID directories, so this step is a no-op.

EFI migration is skipped if `efi/efi_vars.fd` already exists. Otherwise virt-launcher looks in `nvram/` for a `*_VARS.fd` file and renames it to `efi/efi_vars.fd`. The implicit path names that file `nvram/<vmname>_VARS.fd` after `vmi.Name`, which is what ties it to a single VM.

As a concrete example, when VM-B adopts a legacy PVC that VM-A used (`persistent-state-for-abcde`):

Before:
```
/
├── aaa-111/              # VM-A's UUID directory
│   └── tpm2/
│       └── tpm2-00.permall
├── nvram/
│   └── vm-a_VARS.fd      # VM-A's name embedded in filename
├── swtpm-localca/
└── lost+found/
```

After:
```
/
├── tpm/                  # renamed from aaa-111/
│   └── tpm2/
│       └── tpm2-00.permall
├── efi/
│   └── efi_vars.fd       # renamed from nvram/vm-a_VARS.fd
├── swtpm-localca/
├── cbt/
├── meta/
└── lost+found/
```

Once the renames are done virt-launcher creates any missing canonical directories (`/tpm/`, `/efi/`, and so on). From then on both gates are satisfied, so every subsequent boot skips migration entirely.

### Implicit Persistence When `virtualMachineState` Is Set

When `virtualMachineState` is set, any enabled TPM or EFI feature is treated as persistent even without `persistent: true`. Providing a state PVC already implies the state should be kept, so a separate `persistent: true` on each device is unnecessary:

```yaml
spec:
  template:
    spec:
      domain:
        devices:
          tpm: {}              # automatically persistent
        firmware:
          bootloader:
            efi: {}            # automatically persistent
      virtualMachineState:
        volumeClaimTemplate: {}
```

If `virtualMachineState` is set with no TPM/EFI/CBT, the PVC is created with empty canonical directories.

Setting `persistent: false` on a device is not rejected. The device stays ephemeral and KubeVirt does not write its state to the PVC, same as the implicit API today. The PVC is still provisioned or adopted. If the device is later set persistent, or CBT is enabled, KubeVirt uses the PVC as usual.

### State Adoption

Adoption is the case where VM-B starts against a PVC that VM-A used before. For TPM state it works without touching the PVC at all:

1. VM-A ran with symlink: `<stateroot>/aaa-111` → `VMStatePVCMountPath/tpm/`. swtpm wrote state to PVC through the symlink.
2. VM-A stopped. Pod terminated, symlink gone. PVC unchanged.
3. VM-B starts with same PVC. Virt-launcher creates symlink: `<stateroot>/bbb-222` → `VMStatePVCMountPath/tpm/`. Libvirt finds existing state through the new symlink.

The PVC filesystem is identical before and after adoption for TPM and EFI state. Only the ephemeral symlink changes. EFI is even simpler and needs no symlink or rename, since the domain XML points straight at `VMStatePVCMountPath/efi/efi_vars.fd` no matter which VM is running, so VM-B just opens the same file VM-A wrote.

The label on the PVC also lets the controller tell whether another VM already uses it, so adoption of a PVC still in use can be detected.

CBT is the exception. Its metadata tracks block changes for one specific VM's disks and means nothing to a different VM, so virt-launcher clears `cbt/` whenever a different VM adopts the PVC. The cost is a single full backup on the next backup cycle.

### Live Migration

Live migration on RWO needs a new PVC on the destination, which the controller can only create if it has a template.

**RWX:** source and target pods mount the same PVC. State moves over the libvirt migration stream, no second PVC.

**RWO with a template:** the controller creates a new PVC on the destination from the template, migrates over the libvirt stream, and records the new name in `status.virtualMachineStateVolume`. This covers `volumeClaimTemplate` alone and the adoption path (`source` + `volumeClaimTemplate`).

**RWO with `source` only (no template):** the controller has no spec to create a destination PVC, so live migration is blocked with a condition and the reason is surfaced on the VM. The VM still starts and cold-migrates. Users who want RWO live migration on an adopted PVC can add a template.

After a migration the live PVC differs from `source`. Its name is authoritative in `status.virtualMachineStateVolume`, and the label on the PVC lets the controller recover the mapping if it crashes before writing status.

### Garbage Collection

- PVCs created from `volumeClaimTemplate` get an OwnerReference to the VM and are deleted with it. When a migration replaces one, Kubevirt deletes the old PVC too.
- PVCs referenced via `source` may not have been created by Kubevirt, so it never deletes them. The user owns their lifecycle, including any left behind after a migration.

### Behavior

When `virtualMachineState` is set, it takes precedence over implicit creation. VMs without `virtualMachineState` continue using the implicit path unchanged.

## API Examples

### Template (owned PVC)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      domain:
        devices:
          tpm: {}
      virtualMachineState:
        volumeClaimTemplate:
          spec:
            resources:
              requests:
                storage: 100Mi
```

### Adopt an existing PVC

```yaml
virtualMachineState:
  source:
    name: existing-vmstate-pvc
```

### Adopt an existing PVC with RWO live migration support

The source PVC seeds the initial state; the template drives any PVC the controller creates on migration.

```yaml
virtualMachineState:
  source:
    name: existing-vmstate-pvc
  volumeClaimTemplate:
    spec:
      storageClassName: fast
      resources:
        requests:
          storage: 100Mi
```

### Migration with State Preservation

```bash
kubectl delete vm original-vm --cascade=orphan
```

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: migrated-vm
spec:
  template:
    spec:
      domain:
        devices:
          tpm: {}
      virtualMachineState:
        source:
          name: original-vm-state
```

## Alternatives

### Metadata File on PVC to keep track of VM UUID and name

Rejected due to clone/snapshot desync. Not Kubernetes-native.

### Ephemeral Bind Mounts

Rejected because it requires `CAP_SYS_ADMIN`. Cleanup is racy on crash. Symlinks provide identical semantics without these drawbacks.

### Directory Renaming on PVC per Adoption

Instead of symlinks, rename the UUID directory on the PVC to match the new VM's UUID each time a different VM adopts the PVC.

Rejected for a few reasons. It mutates persistent storage on every adoption: the PVC filesystem changes each time a VM starts even though the underlying state (TPM keys, EFI vars) is unchanged, whereas the symlink approach leaves the PVC untouched after the initial write. It is also unsafe on crash, because a rename interrupted by a node crash or pod eviction can leave the directory under neither the old nor the new name, requiring a scan for partial renames; the symlink has no such window since it lives outside the PVC. Finally, it breaks snapshots and clones, since a snapshot taken while the directory still carries VM-A's UUID must be renamed before VM-B can use it, while canonical paths are usable by any VM as-is.

## Update/Rollback Compatibility

The `DeclarativeVMState` feature gate controls **API admission only**, but not PVC or api management. VMs that were created with `virtualMachineState` in their spec continue using the declarative path even if the gate is later disabled.

## Functional Testing Approach

- Template creates owned PVC with canonical structure, deleted with VM
- PVC clone/snapshot: new VM boots with cloned state
- Legacy migration: PVC auto-normalized to canonical when user adds `virtualMachineState`
- Adoption of existing PVC: new VM boots with existing state.

## Graduation Requirements

### Alpha (v1.10)

- `DeclarativeVMState` feature gate (opt-in)
- `virtualMachineState` field in `VirtualMachineInstanceSpec`, `virtualMachineStateVolume` (`VolumeStatus`) in VM/VMI status

### Beta

- Feature gate enabled by default after 1-2 releases

### GA

- Feature gate removed

## References

- Enhancement issue: [VEP #312](https://github.com/kubevirt/enhancements/issues/312)
- Upstream libvirt TPM documentation: https://libvirt.org/formatdomain.html#tpm-device
