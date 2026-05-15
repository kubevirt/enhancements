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

This VEP introduces a declarative API field (`vmState`) in the VMI Spec to explicitly reference or template VMState PVCs. This enables PVC reuse across VMs (migration, templating) and explicit control over storage properties (size, name, class), replacing implicit PVC creation when TPM/EFI/CBT features are enabled.

## Motivation

VMState PVCs are created implicitly when persistent TPM, persistent EFI, or CBT is enabled. Users cannot reuse an existing VMState PVC in a new VM, pre-provision PVC size (problematic for CBT metadata growth), or declaratively manage all VM resources (breaks GitOps workflows). The PVC is also created with a randomly generated name, making it difficult to discover and manage. This VEP introduces a declarative API to address these issues.

## Goals

- Declarative API field to reference existing VMState PVCs or template new ones
- VM migration with full state preservation (TPM, EFI, CBT)
- Full VM cloning and templating support
- Explicit control over PVC properties (size, name, storageClass)
- Backward compatibility with implicit PVC creation
- VM-agnostic PVC filesystem structure (no embedded UUIDs or VM identifiers)

## Non Goals

- Auto-migration of existing VMs to the declarative API
- Removing implicit PVC creation (coexistence is required)
- Multiple VMState PVCs per VM

## Definition of Users

- **VM owners:** manage VM lifecycle and state across clusters
- **Platform operators:** provision storage classes and enforce resource policies
- **GitOps tooling:** declaratively manage all VM resources via Git

## User Stories

- **Migration:** Migrate VM to new cluster, preserving TPM state so guest OS trusts the platform
- **Templating:** Create VM template with pre-configured EFI setup, shared across instantiated VMs
- **Cloning:** Clone VM with existing TPM state to preserve guest OS trust
- **GitOps:** Define all VM resources (including backend storage) in Git, apply declaratively
- **CBT:** Pre-provision large VMState PVC to avoid CBT metadata space exhaustion

## Repos

kubevirt/kubevirt

## Design

### API

This VEP introduces a new `vmState` field in `VirtualMachineInstanceSpec`:

```go
type VMStateVolumeSource struct {
    // Creates a new PVC owned by this VM (garbage-collected on deletion).
    // Mutually exclusive with PersistentVolumeClaim.
    // +optional
    Template *VMStateTemplateSpec `json:"template,omitempty"`
    // References an existing PVC (independent lifecycle, no OwnerReference).
    // Mutually exclusive with Template.
    // +optional
    PersistentVolumeClaim *corev1.LocalObjectReference `json:"persistentVolumeClaim,omitempty"`
}

type VMStateTemplateSpec struct {
    // Name for the created PVC. Required.
    Name string `json:"name"`
    // +optional
    Labels map[string]string `json:"labels,omitempty"`
    // +optional
    Annotations map[string]string `json:"annotations,omitempty"`
    // StorageClassName for the PVC. Default: VMStateStorageClass.
    // +optional
    StorageClassName *string `json:"storageClassName,omitempty"`
    // Storage size. Default: 10Mi without CBT, 1Gi with CBT as metadata grows with disk size and backup count.
    // +optional
    Storage *resource.Quantity `json:"storage,omitempty"`
    // Access mode for the PVC.
    // +optional
    AccessMode *corev1.PersistentVolumeAccessMode `json:"accessMode,omitempty"`
}
```

`VMStateTemplateSpec` is purpose-built rather than reusing `DataVolumeTemplate` because VMState PVCs are always Filesystem mode and never need CDI import/clone/upload. Users who need full PVC control can pre-create the PVC and reference it.

Status fields on both VM and VMI report VMState PVC state, following the same patterns as `VolumeStatus` and `PersistentVolumeClaimInfo`:

```go
type VMStateStatus struct {
    // ClaimName is the name of the PVC backing VM state.
    // Populated for both declarative (vmState API) and implicit PVC creation.
    ClaimName string `json:"claimName,omitempty"`
    // Phase indicates the current lifecycle phase of the VMState PVC.
    // +optional
    Phase VolumePhase `json:"phase,omitempty"`
    // Capacity is the actual storage capacity of the PVC as reported by its status.
    // +optional
    Capacity resource.Quantity `json:"capacity,omitempty"`
}
```

Virt-controller populates this for both declarative and implicit paths:

```bash
kubectl get vm my-vm -o jsonpath='{.status.vmState.claimName}'
```

### Validation

- Mutual exclusivity: only one of `template` or `persistentVolumeClaim`
- Immutability: only capacity and storageClassName can be modified after VM creation; other `vmState` fields are immutable. StorageClassName changes enable storage live migration.
- Volume mode: referenced PVCs must use Filesystem volume mode. Block mode is incompatible with the directory-based canonical layout.
- Volume existence: referenced PVCs must exist at VM creation time
- Exclusive ownership: a VMState PVC can only be referenced by one VM at a time. Virt-controller rejects a VM that references a VMState PVC already in use by another VM, preventing concurrent state corruption on RWX volumes.
- Runtime PVC deletion: if the PVC is deleted while the VM exists, virt-controller sets a `VMStatePVCNotFound` condition on the VM status rather than allowing an opaque scheduling failure.

### VMState PVC Structure

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

**Properties:**
- **Zero privileges:** `ln -s` requires no capabilities
- **SELinux compatibility:** virt-launcher can read the symlink and access the target volume without custom SELinux policies.
- **Ephemeral:** Symlink vanishes on pod termination; PVC is never mutated

### Legacy PVC Migration

When a user opts in by adding `vmState`, virt-launcher normalizes any legacy PVC to canonical structure on first boot. TPM and EFI migrations run independently, each with its own idempotency gate:

**Legacy PVC identification:** Legacy VMState PVCs use the `persistent-state-for` GenerateName prefix and carry a `persistent-state-for` label with the original VM name. Virt-controller validates a referenced PVC is a legitimate legacy VMState PVC by checking for this label.

**TPM migration:** Skipped if `/tpm/` already exists. Otherwise, virt-launcher scans for subdirectories, filtering out `lost+found` and considering only UUID-formatted names (`^[0-9a-f]{8}-...-[0-9a-f]{12}$`). If exactly one match is found, an atomic two-phase rename (`→ /tpm.migrating → /tpm`) preserves crash safety. If multiple match, virt-launcher emits a warning event on the VMI listing the ambiguous directories, skips TPM migration, and proceeds with fresh state initialization. The old UUID directories are preserved for manual recovery. For EFI-only PVCs (no TPM state), no UUID directories exist and this step is a no-op.

> **Note:** An alternative is to block the VM from starting: skip the TPM symlink and set a condition on the VMI, leaving it non-ready until the user resolves the PVC contents manually. This avoids the irreversibility of fresh TPM initialization (once the guest boots with a new TPM identity, recovery requires another identity change). The trade-off is that the VM does not start without manual intervention.

**EFI migration:** Skipped if `efi/efi_vars.fd` already exists. Otherwise, virt-launcher scans `nvram/` for any file matching `*_VARS.fd` and renames it to `efi/efi_vars.fd`. The implicit path stores NVRAM as `nvram/<vmname>_VARS.fd` (filename derived from `vmi.Name`), which is VM-specific and prevents reuse.

After both migrations, virt-launcher creates any missing canonical directories (`/tpm/`, `/efi/`, etc.), so subsequent boots skip migration entirely.

### Implicit Persistence When `vmState` Is Set

When `vmState` is specified, any enabled TPM or EFI feature is **automatically treated as persistent**, even without `persistent: true`. The user has declared intent for explicit state storage. Requiring a separate `persistent: true` on each device would be redundant:

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
      vmState:
        template:
          name: my-vm-state
```

If `vmState` is set with no TPM/EFI/CBT, the PVC is created with empty canonical directories.

### State Adoption

When VM-B adopts a PVC previously used by VM-A, no PVC modification occurs:

1. VM-A ran with symlink: `<stateroot>/aaa-111` → `VMStatePVCMountPath/tpm/`. swtpm wrote state to PVC through the symlink.
2. VM-A stopped. Pod terminated, symlink gone. PVC unchanged.
3. VM-B starts with same PVC. Virt-launcher creates symlink: `<stateroot>/bbb-222` → `VMStatePVCMountPath/tpm/`. Libvirt finds existing state through the new symlink.

The PVC filesystem is identical before and after adoption for TPM and EFI state. Only the ephemeral symlink changes.

**EFI adoption** requires no symlink or rename. The domain XML points directly to `VMStatePVCMountPath/efi/efi_vars.fd` regardless of which VM uses the PVC. VM-B opens the same file VM-A wrote to — no PVC mutation.

**CBT data is wiped on adoption.** Unlike TPM and EFI state, CBT metadata tracks block changes for a specific VM's disks and is meaningless for a different VM. Virt-launcher clears `cbt/` when a different VM adopts the PVC. The only cost is a single full backup on the next backup cycle.

### Legacy PVC Migration Example

When VM-B adopts a legacy PVC previously used by VM-A (`persistent-state-for-abcde`), virt-launcher normalizes it on first boot:

**Before (legacy layout):**
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

**After (canonical layout):**
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

TPM migration renames `aaa-111/ → tpm/` (atomic two-phase via `tpm.migrating`). EFI migration renames `nvram/vm-a_VARS.fd → efi/efi_vars.fd`. Both are skipped on subsequent boots since their idempotency gates are already satisfied. After migration, VM-B and any future VM use the canonical paths without further PVC mutation.

### Live Migration

With RWX, both source and target pods mount the same VMState PVC during migration. State transfers via the libvirt migration stream and no second PVC is created.

RWO live migration is not supported in the initial implementation. The current backend storage RWO mechanism creates and swaps PVCs with generated names, which conflicts with the deterministic PVC naming in the declarative API. RWO migration support is planned as a follow-up. If a migration-capable VM has a non-RWX VMState PVC, virt-controller sets a `VMStatePVCNotMigratable` condition.

### Behavior

**Coexistence:** when `vmState` is set, it takes precedence over implicit creation. VMs without `vmState` continue using the implicit path unchanged.

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
      vmState:
        template:
          name: my-vm-state
          storage: 100Mi
```

### Reference (independent PVC)

```yaml
vmState:
  persistentVolumeClaim:
    name: existing-vmstate-pvc
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
          tpm:
            persistent: true
      vmState:
        persistentVolumeClaim:
          name: original-vm-state
```

## Alternatives

### Metadata File on PVC to keep track of VM UUID and name

**Rejected:** Creates split-brain between etcd and PVC filesystem. Clone/snapshot desync. Not Kubernetes-native.

### Ephemeral Bind Mounts

**Rejected:** Requires `CAP_SYS_ADMIN`. Cleanup is racy on crash. Symlinks provide identical semantics without these drawbacks.

### Directory Renaming on PVC per Adoption

Instead of symlinks, rename the UUID directory on the PVC to match the new VM's UUID each time a different VM adopts the PVC.

**Rejected:**

- **Mutates persistent storage on every adoption.** The PVC filesystem changes each time a VM starts, even though the actual state (TPM keys, EFI vars) is unchanged. With symlinks, the PVC is never modified after initial state creation.
- **Crash safety.** A rename interrupted mid-operation (node crash, pod eviction) leaves the PVC in an ambiguous state, the directory may exist under neither the old nor new name. Recovery requires scanning for partial renames. The symlink approach has no crash window since the symlink is ephemeral and lives outside the PVC.
- **Snapshot/clone divergence.** If a PVC snapshot is taken while the directory carries VM-A's UUID, restoring it for VM-B requires a rename before use. With canonical paths, snapshots and clones are immediately usable by any VM.

### DataVolumeTemplate for PVC Creation

**Rejected:** VMState PVCs never need CDI import/clone/upload. A purpose-built type with only the relevant knobs is simpler and safer.

## Update/Rollback Compatibility

The `DeclarativeVMState` feature gate controls **API admission only**, but not PVC or api management. VMs that were created with `vmState` in their spec continue using the declarative path even if the gate is later disabled.

## Functional Testing Approach

- Template creates owned PVC with canonical structure, deleted with VM
- PVC clone/snapshot: new VM boots with cloned state
- Legacy migration: PVC auto-normalized to canonical when user adds `vmState`
- Adoption of existing PVC: new VM boots with existing state.

## Graduation Requirements

### Alpha (v1.10)

- `DeclarativeVMState` feature gate (opt-in)
- `vmState` field in `VirtualMachineInstanceSpec`, `VMStateStatus` in VM/VMI status

### Beta

- Feature gate enabled by default after 1-2 releases

### GA

- Feature gate removed

## References

- Enhancement issue: kubevirt/kubevirt#312
- Upstream libvirt TPM documentation: https://libvirt.org/formatdomain.html#tpm-device
