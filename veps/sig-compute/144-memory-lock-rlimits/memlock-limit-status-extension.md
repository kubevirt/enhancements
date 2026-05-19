# VEP #144 Extension: Propagate computed memlock limit via VMI status

*Draft for discussion -- extends the existing VEP #144 (Memory Lock RLimit configuration)*

> **Note:** This draft assumes [kubevirt/kubevirt#17805](https://github.com/kubevirt/kubevirt/pull/17805) (VFIO memlock scaling in virt-handler) and [kubevirt/kubevirt#17857](https://github.com/kubevirt/kubevirt/pull/17857) (shared `pkg/vfio` package and `<memtune><hard_limit>` in domain XML) have merged. #17805 is under review; #17857 is a draft dependent on it. The assumptions about `pkg/vfio`, `CountDevices()`, `CalculateMemlockLimit()`, and `<memtune><hard_limit>` support in the domain converter all come from these PRs.

## Problem

The memlock rlimit value is currently computed independently in multiple places:

- **virt-handler** (`CalculateMemlockSize()`) -- for prlimit64 on the QEMU process
- **virt-launcher** (domain converter) -- for `<memtune><hard_limit>` in the domain XML
- **libvirt** (`qemuDomainGetMemLockLimitBytes()`) -- its own internal calculation

These calculations can drift out of sync. Additionally, the inputs that determine the memlock value come from multiple sources:

- Automatic VFIO device counting (GPUs, HostDevices, SRIOV) -- internally computed
- VEP #144's `reservedOverhead.addedOverhead` -- user or webhook specified
- VEP #144's `reservedOverhead.memLock` flag -- signals locking is required
- Future sources (new device types, platform-specific requirements)

There is no single place where the final memlock value is computed and no mechanism to propagate it to all consumers.

## Proposal

Add a **status-only** field `memLockLimit` to `MemoryStatus`. The value is computed once by virt-controller and consumed by both virt-handler and virt-launcher. No VMI spec changes.

### New status field

```go
type MemoryStatus struct {
    GuestAtBoot    *resource.Quantity `json:"guestAtBoot,omitempty"`
    GuestCurrent   *resource.Quantity `json:"guestCurrent,omitempty"`
    GuestRequested *resource.Quantity `json:"guestRequested,omitempty"`
    MemoryOverhead *resource.Quantity `json:"memoryOverhead,omitempty"`
    MemLockLimit   *resource.Quantity `json:"memLockLimit,omitempty"`
}
```

### Compute-once flow

```
virt-controller
  │
  ├─ Inputs:
  │    - VFIO device count from VMI spec (vfio.CalculateMemlockLimit)
  │    - reservedOverhead.addedOverhead (VEP #144)
  │    - reservedOverhead.memLock flag (VEP #144)
  │    - Future: platform-specific overrides
  │
  └─ Writes vmi.Status.Memory.MemLockLimit
       │
       ├─ virt-handler reads → prlimit64 on QEMU process
       │
       └─ virt-launcher reads (via SyncVirtualMachine gRPC)
            → <memtune><hard_limit> in domain XML
```

No pod annotation is needed. Unlike `MemoryOverheadAnnotationBytes` (which the pod needs at creation time for resource requests), the memlock value is consumed at runtime: virt-handler reads it from VMI status before the domain starts, and virt-launcher receives the full VMI object via gRPC.

### How this unifies both approaches

| Source | Triggers | Current path | With this extension |
| :----- | :------- | :----------- | :------------------ |
| VFIO devices (auto) | KubeVirt counts devices internally | Parallel calc in handler + launcher | Controller computes, writes to status |
| `addedOverhead` (VEP #144) | User/webhook sets spec field | Flows into `GetMemoryOverhead()`, inflates pod request | Feeds controller's memlock calc, writes to status. Pod request handled separately. |
| `memLock: Required` (VEP #144) | User/webhook sets flag | Triggers handler's memlock adjustment | Triggers controller to compute and write to status |

The VMI spec fields (`addedOverhead`, `memLock`) remain as inputs -- they express user intent. The computed result lives only in status.

### Memlock calculation in virt-controller

```go
func calculateMemLockLimit(vmi *v1.VirtualMachineInstance) *resource.Quantity {
    // Start with automatic VFIO calculation
    memlockBytes := vfio.CalculateMemlockLimit(vmi)

    // Add user-specified overhead from VEP #144
    if vmi.Spec.Domain.Memory != nil &&
       vmi.Spec.Domain.Memory.ReservedOverhead != nil &&
       vmi.Spec.Domain.Memory.ReservedOverhead.AddedOverhead != nil {
        memlockBytes += vmi.Spec.Domain.Memory.ReservedOverhead.AddedOverhead.Value()
    }

    if memlockBytes == 0 {
        // Check if memLock is explicitly required without devices
        if util.RequiresLockingMemory(vmi) {
            // Base memlock: guestMemory + standard overhead
            memlockBytes = vfio.GetVirtualMemoryBytes(vmi) + vfio.MMIOOverheadBytes
        }
    }

    if memlockBytes == 0 {
        return nil
    }
    q := resource.NewQuantity(memlockBytes, resource.BinarySI)
    return q
}
```

### Consumer changes

**virt-handler** `AdjustResources()`:

```
1. Read vmi.Status.Memory.MemLockLimit (new)
2. Fall back to vmi.Status.MigrationState.TargetMemoryOverhead (existing, migration)
3. Fall back to local GetMemoryOverhead() calculation (existing, legacy)
```

**virt-launcher** `MemoryConfigurator.configureMemLock()`:

```
1. Read vmi.Status.Memory.MemLockLimit → set <memtune><hard_limit>
2. Fall back to vfio.CalculateMemlockLimit(vmi) (existing, for when status not yet populated)
```

## Impact on VEP #144

### No breaking changes

- `reservedOverhead.addedOverhead` continues to work as an input
- `reservedOverhead.memLock` continues to trigger memlock adjustment
- Existing behaviour preserved when `MemLockLimit` status field is absent

### Improvement: decoupled from pod sizing

Currently `addedOverhead` flows into `GetMemoryOverhead()` which inflates both the pod memory request and the memlock rlimit. With this extension, virt-controller can compute the memlock value separately from the pod sizing. `addedOverhead` could be split into:

- Its contribution to pod memory request (for actual memory consumption)
- Its contribution to the memlock limit (for VFIO address space ceiling)

This aligns with the VEP's stated goal: "Adjust memory lock limits **without impacting VMI scheduling capacity**."

## Benefits

- **Single source of truth** -- memlock value computed once, consumed everywhere
- **No parallel calculations** -- eliminates drift between handler, launcher, and libvirt
- **Status-only** -- no VMI spec changes, no VEP needed for the status field
- **Backwards compatible** -- consumers fall back to existing behaviour when status field absent
- **Extensible** -- future memlock inputs feed into the same controller calculation
- **Decouples memlock from pod sizing** -- addresses VEP #144's stated goal

## Prior work

This extension builds on two PRs that address the immediate multi-device VFIO memlock problem in v1.9:

- [kubevirt/kubevirt#17805](https://github.com/kubevirt/kubevirt/pull/17805) -- scales virt-handler's memlock rlimit for multi-device VFIO passthrough. Extracts `CalculateMemlockSize()` and introduces `CountVFIODevices()` in `pkg/util`.
- [kubevirt/kubevirt#17857](https://github.com/kubevirt/kubevirt/pull/17857) -- extracts the VFIO memlock formula into a shared `pkg/vfio` package and sets `<memtune><hard_limit>` in the domain XML so libvirt uses KubeVirt's value directly.

These PRs solve the problem for v1.9 by sharing the formula between handler and launcher via `pkg/vfio`. This extension replaces the shared formula approach with a single computation in virt-controller propagated via VMI status, eliminating parallel calculations entirely.

## Timeline

- v1.10: Introduce `MemLockLimit` status field and controller computation. Refactor `addedOverhead` to stop flowing into `GetMemoryOverhead()` for memlock. Remove fallback calculations from handler and launcher. Consumers read exclusively from `vmi.Status.Memory.MemLockLimit`.
