# VEP #344: VM-Scoped Persistent ResourceClaims for DRA Device Passthrough

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10.0
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Release Signoff Checklist

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP introduces VM-scoped persistent ResourceClaims for Kubernetes Dynamic
Resource Allocation (DRA). Today, KubeVirt supports DRA-provisioned devices
(GPUs, host devices) at the VMI level, where ResourceClaims are created
per-Pod and deleted when the Pod is removed. This means that restarting a VM
results in a new ResourceClaim and potentially a different physical device.

This proposal adds a `resourceClaimTemplates` field to `VirtualMachineSpec`
that allows the VM controller to pre-create ResourceClaims owned by the VM
object. These claims persist across VMI restarts and are only garbage-collected
when the VM itself is deleted, ensuring device allocation stability.

## Prior Art

This VEP builds on the work identified in the [GSoC 2024: Persistent Device
Claims for KubeVirt](https://github.com/kubevirt/community/issues/254) project
proposal. That issue described the core problem — device allocations are lost
when VM pods are recreated — and proposed using Kubernetes Dynamic Resource
Allocation (DRA) ResourceClaims as the persistence mechanism. This VEP
implements that vision by integrating persistent ResourceClaims into the VM
controller, following the established `dataVolumeTemplates` pattern.

## Motivation

When using DRA with KubeVirt today, ResourceClaims are scoped to the
virt-launcher Pod. Each time a VM is restarted (stop/start, reboot, crash
recovery), the old Pod and its ResourceClaim are deleted, and a new claim is
created. This causes several problems:

1. **Data loss for stateful devices**: NVMe drives and other storage devices
   provisioned via DRA contain persistent data. Losing the device allocation on
   restart means losing access to that data.

2. **Device instability across reboots**: GPU passthrough users may depend on a
   specific physical device for driver compatibility, licensing, or workload
   continuity. A different GPU after a reboot can cause driver failures or
   license revalidation. Note: for stop/start cycles, the default behavior
   releases devices to avoid wasting resources on stopped VMs. Users who need
   device stability across stop/start can opt in with `persistWhenStopped: true`.

3. **Allocation failures in busy clusters**: In the window between the old
   claim being deleted and the new claim being created, another workload can
   claim the device. This creates a race condition where a VM restart can fail
   due to resource exhaustion, even though the device was available moments
   before.

4. **Inconsistency with other VM-scoped resources**: KubeVirt already supports
   VM-scoped DataVolumes via `dataVolumeTemplates`, which persist across VMI
   restarts. DRA devices lack an equivalent mechanism, creating an asymmetry in
   the resource lifecycle model.

## Goals

- Allow users to declare ResourceClaims that are scoped to the VM lifecycle,
  not the VMI/Pod lifecycle.
- Ensure DRA device allocations persist across VM restarts (stop/start, reboot,
  crash recovery).
- Support all DRA device types (GPUs, host devices, and future DRA-managed
  devices such as SR-IOV NICs) with a single, device-agnostic mechanism.
- Follow established KubeVirt patterns (`dataVolumeTemplates`) for consistency
  and maintainability.
- Provide webhook validation to catch misconfigurations at admission time.

## Non Goals

- Eager allocation (creating ResourceClaims before the VM is started). The
  current implementation creates claims at start time, matching the
  `dataVolumeTemplates` pattern. Eager allocation with a per-entry policy
  (e.g., `allocationMode: Eager | Lazy`) is a potential future enhancement.
- Migration-aware ResourceClaim handling. Live migration with DRA devices is
  out of scope for this VEP.
- Managing ResourceClaimTemplates themselves. Users are responsible for creating
  the `ResourceClaimTemplate` objects that this feature references.

## Definition of Users

- **VM administrators** who manage long-lived VirtualMachines with DRA-provisioned
  hardware (GPUs, NVMe, host devices).
- **Platform engineers** who configure DRA drivers and ResourceClaimTemplates
  for their clusters.

## User Stories

1. As a VM administrator, I want my VM's GPU assignment to survive restarts so
   that the guest OS driver stack remains consistent and I don't need to
   reconfigure the device after each reboot.

2. As a VM administrator, I want my VM's NVMe device to persist across restarts
   so that data stored on the device is not lost when the VM is stopped and
   restarted.

3. As a VM administrator, I want my VM to reliably restart in a busy cluster
   without risking allocation failure because another workload grabbed my
   device during the restart window.

4. As a VM administrator, I want my VM's SR-IOV NIC to keep the same MAC
   address across restarts so that DHCP reservations, firewall rules, and
   monitoring remain valid.

5. As a platform engineer, I want a single mechanism that works for all
   DRA-managed device types (GPUs, NVMe, SR-IOV NICs) without needing
   device-specific configuration in KubeVirt.

## Repos

- `kubevirt/kubevirt` — API types, VM controller, webhook validation, RBAC

## Design

### API Changes

A new type `ResourceClaimTemplateEntry` and a new field `resourceClaimTemplates`
on `VirtualMachineSpec`:

```go
// ResourceClaimTemplateEntry defines a ResourceClaim that should be created
// from a ResourceClaimTemplate and bound to this VirtualMachine's lifecycle.
type ResourceClaimTemplateEntry struct {
    // Name is the logical name used to match this entry to
    // spec.template.spec.resourceClaims[].name in the VMI template.
    // +kubebuilder:validation:MinLength=1
    Name string `json:"name"`
    // ResourceClaimTemplateName is the name of a ResourceClaimTemplate
    // object in the same namespace to create the ResourceClaim from.
    // +kubebuilder:validation:MinLength=1
    ResourceClaimTemplateName string `json:"resourceClaimTemplateName"`
    // PersistWhenStopped controls whether the ResourceClaim is retained
    // when the VM is explicitly stopped (runStrategy set to Halted or
    // stop API called). When nil or false (the default), the claim is
    // deleted on stop to free the device for other workloads, and
    // re-created on the next start. When true, the claim persists for
    // the VM's entire lifetime. Claims always persist across reboots
    // regardless of this setting.
    // +optional
    PersistWhenStopped *bool `json:"persistWhenStopped,omitempty"`
}

type VirtualMachineSpec struct {
    // ... existing fields ...

    // resourceClaimTemplates is a list of ResourceClaims that should be
    // created from ResourceClaimTemplates and are tied to the
    // VirtualMachine's lifecycle.
    // +kubebuilder:validation:MaxItems:=256
    // +listType=map
    // +listMapKey=name
    // +optional
    ResourceClaimTemplates []ResourceClaimTemplateEntry `json:"resourceClaimTemplates,omitempty"`
}
```

### Kubernetes DRA Background: `status.reservedFor`

Kubernetes DRA ResourceClaims have a standard `status.reservedFor` field
that lists which entities are currently using a claim. The DRA resource
controller only deallocates a claim when this list is **empty**. This is
an existing Kubernetes API — no upstream changes are needed.

The field accepts any Kubernetes resource type, not just pods:

```go
type ResourceClaimConsumerReference struct {
    APIGroup string  // e.g., "kubevirt.io"
    Resource string  // e.g., "virtualmachines"
    Name     string
    UID      types.UID
}
```

When a pod references a claim, the scheduler adds the pod to `reservedFor`.
When the pod is deleted, the kubelet removes it. If no other consumers
remain, the DRA controller deallocates the claim.

This VEP uses `reservedFor` to add the VM as a consumer alongside the pod.
When the pod is deleted (stop or reboot), the VM entry remains, keeping the
allocation alive. No anchor pods, sidecar pods, or upstream API changes are
required.

### Controller Behavior

The VM controller manages ResourceClaims following the same pattern as
`dataVolumeTemplates`:

1. **Claim creation** (`handleResourceClaims`): Called during `startVMI()`,
   before the VMI is created. For each entry in `resourceClaimTemplates`:
   - Derives the claim name as `<vm-name>-<entry-name>` (validated at admission
     time to not exceed 63 characters — the Kubernetes name length limit)
   - Checks if the ResourceClaim already exists in the informer cache
   - If not, looks up the ResourceClaimTemplate, creates the ResourceClaim with
     the VM as owner (via `OwnerReference`), and records controller expectations

2. **VMI setup** (`SetupVMIFromVM`): When constructing the VMI from the VM
   template, any `resourceClaims` entry that has a `resourceClaimTemplateName`
   matching a `resourceClaimTemplates` entry is rewritten to use a direct
   `resourceClaimName` pointing to the pre-created claim. This ensures the Pod
   spec references the persistent claim rather than a template.

3. **Lifecycle management**: ResourceClaim informer event handlers
   (`add`/`update`/`delete`) resolve owner references back to the VM and
   re-enqueue it for reconciliation, using the expectations pattern to prevent
   duplicate operations.

4. **Allocation preservation** (`ensureVMInClaimReservedFor`): Called early
   in every VM reconcile, before run strategy decisions. For claims with
   `persistWhenStopped: true`, the controller checks if the claim is
   allocated. Once allocated, if the VM is not in the claim's
   `status.reservedFor`, the controller adds a `ResourceClaimConsumerReference`
   for the VM via the `resourceclaims/binding` subresource. This is
   idempotent — subsequent reconciles skip claims that already have the VM
   in `reservedFor`. The VM entry keeps the allocation alive when the pod
   is deleted, ensuring the same physical device on next start.

5. **Stop-time cleanup** (`cleanupNonPersistentResourceClaims`): When a VM
   is explicitly stopped, the controller deletes ResourceClaims for entries
   where `persistWhenStopped` is false (the default), freeing the devices
   for other workloads. Claims with `persistWhenStopped: true` are retained
   with their allocation intact. On the next start, missing claims are
   re-created from their templates.

6. **VM deletion cleanup**: Before removing the VM finalizer, the controller
   removes the VM from `reservedFor` on all persistent claims. This allows
   the DRA resource controller to deallocate the devices. The claims are
   then garbage-collected via owner references.

7. **Garbage collection**: ResourceClaims have the VM as their controller owner
   reference. Kubernetes garbage collection automatically deletes them when the
   VM is deleted.

### Claim Lifecycle

The default behavior (`persistWhenStopped: false`) releases devices on stop so
they are available to other workloads:

```
VM created (Halted)     → no claims created
VM started              → claims created, VMI created referencing them
VM rebooted             → claims persist, VMI recreated reusing them
VM stopped              → VMI deleted, claims DELETED (devices freed)
VM restarted            → new claims created (may get different device)
VM deleted              → any remaining claims garbage-collected via owner ref
```

With `persistWhenStopped: true`, claims persist for the VM's entire lifetime.
The VM is added to `status.reservedFor` on the claim, which prevents the DRA
resource controller from deallocating when the pod is deleted:

```
VM created (Halted)     → no claims created
VM started              → claims created, scheduler allocates, VM added to reservedFor
VM rebooted             → pod removed from reservedFor, VM entry keeps allocation alive
VM stopped              → pod deleted, VM stays in reservedFor, allocation PRESERVED
VM restarted            → claims already allocated with same device, new pod added
VM deleted              → VM removed from reservedFor, claims deallocated and GC'd
```

Use `persistWhenStopped: true` for devices with persistent state (NVMe),
identity-bound licensing (GPU tied to PCI address), or network identity
(NIC with DHCP reservation). Use the default for devices where resource
efficiency matters more than device stability.

Validated on Dell XE9680 with 4x Samsung PM1745 NVMe drives: 4 VMs each
holding a unique NVMe device across 10 stop/start cycles and 10 reboot
cycles (80/80 total, zero device swaps).

### Manual Reallocation

If a user needs to force a different device allocation on a **stopped** VM
(e.g., replacing failed hardware, changing NUMA placement), they can
delete the claim directly. Claims should only be deleted while the VM is
stopped — deleting a claim on a running VM has no effect until the next
start, since the running pod already has the device allocated.

```
kubectl delete resourceclaim my-vm-nvme-claim
virtctl start my-vm   # controller recreates claim, scheduler allocates fresh
```

The controller's `handleResourceClaims` recreates missing claims on start.
The scheduler allocates from the available device pool, which may assign a
different physical device.

For beta, a `reallocateOnNextStart` field on `ResourceClaimTemplateEntry` is
a candidate if users need a single-step API. For alpha, the manual approach
is sufficient — reallocation is an exceptional operation.

### Webhook Validation

The validating webhook (`validateResourceClaimTemplates`) enforces:

- `PersistentDRAClaims` feature gate must be enabled
- No duplicate `name` entries
- Both `name` and `resourceClaimTemplateName` are required
- Each entry must have a matching `spec.template.spec.resourceClaims[]` entry
  by name
- The derived claim name (`<vm-name>-<entry-name>`) must not exceed 63
  characters (the Kubernetes object name length limit)

### RBAC

The virt-controller service account requires:
- `resourceclaims`: get, list, watch, create, delete
- `resourceclaims/binding`: patch
- `resourceclaimtemplates`: get, list, watch

The `delete` verb is needed for stop-time cleanup of claims where
`persistWhenStopped` is false. The `resourceclaims/binding` patch
permission is needed to add/remove the VM from `status.reservedFor`.
The `/binding` subresource is the Kubernetes-canonical way to modify
`reservedFor`, matching the RBAC pattern used by kube-controller-manager
for StatefulSet volume claims.

### Live Migration

VMs with `persistWhenStopped: true` ResourceClaims cannot be live-migrated.
DRA device allocations are node-specific — the allocated device exists on the
node where the claim was bound. The VMI will have a `HostDeviceNotLiveMigratable`
condition set by default for any VM with DRA host device passthrough, which
blocks migration. Users who need migration should use VMI-scoped claims (the
existing pod-level DRA flow) rather than VM-scoped persistent claims.

### Security Implications

The virt-controller service account has `patch` permission on
`resourceclaims/binding`. This is equivalent to the privileges held by
kube-scheduler and kube-controller-manager for managing pod allocations.
The VM controller is trusted to manage `reservedFor` because:
- It only modifies claims it owns (enforced via OwnerReference)
- It follows the same reservation lifecycle as StatefulSets
- Admission webhooks prevent unauthorized claim creation

### Quota Implications

When `persistWhenStopped: true`, the VM holds a device allocation even when
stopped. This does not count against pod-based resource quotas, but does count
against cluster-wide device capacity reported via ResourceSlices. Cluster admins
should monitor ResourceClaim allocations separately from pod quotas when
persistent claims are in use.

## API Examples

### VM with persistent GPU claim

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm-dra-pgpu
spec:
  runStrategy: Halted
  resourceClaimTemplates:
  - name: pgpu-resource-claim
    resourceClaimTemplateName: pgpu-resource-claim-tmpl
  template:
    spec:
      domain:
        devices:
          gpus:
          - claimName: pgpu-resource-claim
            name: example-gpu
            requestName: pgpu
        memory:
          guest: 1024M
      resourceClaims:
      - name: pgpu-resource-claim
        resourceClaimTemplateName: pgpu-resource-claim-tmpl
      volumes:
      - containerDisk:
          image: registry:5000/kubevirt/fedora-with-test-tooling-container-disk:devel
        name: containerdisk
```

### Comparison: VMI-scoped (before) vs VM-scoped (after)

**Before (VMI-scoped, existing behavior):**
ResourceClaim is created per-Pod by kubelet. Deleted when Pod is removed.
VM restart = new claim = potentially different device.

**After (VM-scoped, this VEP):**
ResourceClaim is created by VM controller with VM as owner. Persists across
VMI restarts. VM restart = same claim = same device allocation.

### VM with mixed persistence policies

NVMe persists when stopped (data preservation). GPU is released on stop
(resource efficiency) and re-allocated on next start.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm-multi-device
spec:
  runStrategy: Always
  resourceClaimTemplates:
  - name: gpu-claim
    resourceClaimTemplateName: gpu-template
    # default: persistWhenStopped: false — GPU freed on stop
  - name: nvme-claim
    resourceClaimTemplateName: nvme-template
    persistWhenStopped: true  # NVMe kept across stop/start
  template:
    spec:
      domain:
        devices:
          gpus:
          - claimName: gpu-claim
            name: passthrough-gpu
            requestName: pgpu
          hostDevices:
          - claimName: nvme-claim
            name: fast-storage
            requestName: nvme
        memory:
          guest: 4096M
      resourceClaims:
      - name: gpu-claim
        resourceClaimTemplateName: gpu-template
      - name: nvme-claim
        resourceClaimTemplateName: nvme-template
```

## Alternatives

### 1. Eager allocation with per-entry policy

Instead of creating claims only at start time, create them as soon as the VM
object is created. This would guarantee device availability even for halted VMs.
A per-entry `allocationMode: Eager | Lazy` field would allow users to choose
the behavior per device — eager for NVMe (data preservation), lazy for GPUs
(efficient utilization).

**Rejected for initial implementation** because it adds API complexity. The lazy
approach matches the existing `dataVolumeTemplates` pattern and is correct for
the common case. Eager allocation can be added as a follow-up enhancement if
reviewers or users identify a strong need.

### 2. Annotation-based approach

Use annotations on the VM to signal which ResourceClaims should persist, rather
than adding a new spec field.

**Rejected** because annotations are unstructured, unvalidated, and not
discoverable via the API schema. A first-class spec field provides type safety,
webhook validation, and clear documentation.

### 3. Finalizer-based claim retention

Instead of creating claims owned by the VM, intercept Pod deletion and transfer
ownership of existing pod-scoped claims to the VM using finalizers.

**Rejected** because it introduces complex lifecycle coordination between the
VM controller and kubelet's claim management. The pre-creation approach is
simpler and follows the established `dataVolumeTemplates` pattern.

### 4. PVCs instead of persistent ResourceClaims

For NVMe storage, use PersistentVolumeClaims via a CSI driver instead of
DRA ResourceClaims.

**Rejected** because PVCs are a storage abstraction — they don't cover GPUs,
SR-IOV NICs, FPGAs, or other non-storage devices. DRA ResourceClaims are the
Kubernetes-standard API for dynamic device allocation, and they carry device
metadata (NUMA node, PCIe topology, driver attributes) that PVCs lack. Even
for NVMe, DRA VFIO passthrough gives direct hardware access without the
filesystem/block driver overhead that CSI imposes.

### 5. Dual declaration of resourceClaimTemplates

The user declares `resourceClaimTemplates` entries on the VM spec **and**
references them in `spec.template.spec.resourceClaims` on the VMI template.
This follows the established `dataVolumeTemplates` pattern, where the VM spec
declares what to create and the VMI template declares what to consume. The
webhook validates that every VM-level entry has a matching VMI-level entry.
The `SetupVMIFromVM` controller logic rewrites template references to direct
claim name references before VMI creation, so the VMI never sees the
template — only the concrete claim.

## Scalability

- Each VM with `resourceClaimTemplates` creates one ResourceClaim per entry.
  The maximum is capped at 256 entries per VM via kubebuilder validation.
- ResourceClaim informers watch cluster-wide, consistent with other KubeVirt
  informers (PVCs, DataVolumes). This adds two new informers to the
  virt-controller.
- Controller expectations prevent duplicate API calls during reconciliation.
- No additional API calls are made for VMs that do not use
  `resourceClaimTemplates`.

## Update/Rollback Compatibility

- The `resourceClaimTemplates` field is optional. Existing VMs without the
  field are unaffected.
- On rollback, orphaned ResourceClaims (created by the new controller) will
  remain until manually deleted or until the VM is deleted (owner reference GC
  still works regardless of the controller version).
- The feature is gated behind a dedicated `PersistentDRAClaims` feature gate
  (alpha, disabled by default). The webhook rejects `resourceClaimTemplates`
  when the gate is off.

## Functional Testing Approach

### Unit Tests (implemented)

- `SetupVMIFromVM` correctly rewrites `resourceClaimTemplateName` to
  `resourceClaimName` for matching entries
- Non-matching entries and direct `resourceClaimName` references are preserved
- `CreateResourceClaimManifest` produces correct ownership, naming, and labels
- Multiple and mixed (direct + template) claims are handled correctly

### Integration Tests (planned)

- VM with `resourceClaimTemplates` creates ResourceClaims on start
- ResourceClaims are deleted on stop when `persistWhenStopped: false` (default)
- ResourceClaims persist on stop when `persistWhenStopped: true`
- ResourceClaims persist across reboots regardless of `persistWhenStopped`
- ResourceClaims are re-created on start after a stop (default behavior)
- ResourceClaims are reused on start when they already exist (`persistWhenStopped: true`)
- ResourceClaims are garbage-collected when VM is deleted
- Webhook rejects invalid configurations (missing template, duplicate names,
  name too long, feature gate disabled)

### E2E Tests (planned)

- Full lifecycle test with a real DRA driver: create VM, start, verify device,
  stop, restart, verify same device allocation
- Requires a cluster with DRA support and a DRA driver installed
- Hardware validation performed on Dell R7725 with Samsung PM1745 NVMe drives
  via dra-driver-nvme VFIO passthrough (5 restart cycles, 2 VMs, 3 claims,
  0 failures)

## References

- [GSoC 2024: Persistent Device Claims for KubeVirt](https://github.com/kubevirt/community/issues/254) — Original project proposal by Alice Frosi, Victor Toso de Carvalho, and Luboslav Pivarc that identified the persistent device claim problem and proposed using DRA as the solution
- [Kubernetes DRA documentation](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
- [Kubernetes DRA enhancement proposal](https://github.com/kubernetes/enhancements/issues/3063)
- [KubeVirt host devices user guide](https://kubevirt.io/user-guide/virtual_machines/host-devices/)

## Implementation History

- 2024-02: Problem identified in [kubevirt/community#254](https://github.com/kubevirt/community/issues/254) (GSoC 2024)
- 2026-05: Initial implementation and hardware validation. PR: [kubevirt/kubevirt#17957](https://github.com/kubevirt/kubevirt/pull/17957)

## Graduation Requirements

### Alpha

- [x] Feature gated behind dedicated `PersistentDRAClaims` feature gate
- [x] `ResourceClaimTemplateEntry` API type and `VirtualMachineSpec.ResourceClaimTemplates` field
- [x] VM controller creates and manages ResourceClaims from templates
- [x] `SetupVMIFromVM` rewrites template references to direct name references
- [x] Webhook validation for `resourceClaimTemplates`
- [x] Unit tests for controller logic, rewrite behavior, and validation
- [x] Hardware validation with NVMe VFIO passthrough
- [ ] Documentation and example manifests

### Beta

- [ ] Integration tests covering full VM lifecycle with persistent claims
- [ ] E2E tests with a real DRA driver
- [ ] Feedback from alpha users incorporated
- [ ] Consider eager allocation policy based on user feedback
- [ ] Documentation in kubevirt.io user guide

### GA

- [ ] Stable across multiple releases
- [ ] No outstanding bugs or behavioral issues
- [ ] Broad user adoption and positive feedback
- [ ] Live migration interaction documented (claims block migration by design)
- [ ] Upgrade/downgrade testing
