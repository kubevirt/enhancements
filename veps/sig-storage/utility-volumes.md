# VEP \#86: Utility Volumes for virt-launcher Pods

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

  - [+] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
  - [ ] (R) Target version is explicitly mentioned and approved
  - [ ] (R) Graduation criteria filled

## Overview

This proposal introduces a new API mechanism within KubeVirt that enables the general capability of **hot-plugging volumes directly into the virt-launcher Pod**.
These maintenance-focused volumes are attached to the Pod, bypassing the Virtual Machine guest. The primary goal is to establish this foundational mechanism to support various out-of-band operations that require direct Pod-level volume access, such as backup data collection, memory dumps, and diagnostic log retrieval.

## Motivation

KubeVirt currently lacks a **general mechanism for pod-level volume attachment** that enables operational workflows to dynamically attach storage directly to the `virt-launcher` Pod without exposing volumes to the VM guest. Example operational scenarios which require this capability:

  * **Libvirt based Backup Operations:** Collect VM backup data from libvirt - required for [Incremental Backup VEP](https://github.com/kubevirt/enhancements/blob/main/veps/sig-storage/incremental-backup.md). VEP 25 requires the ability to hot-plug a volume directly into the virt-launcher pod without exposing it to the virtual machine domain, enabling libvirt to collect backup output and write it directly to the attached volume for efficient backup operations.
  * **Diagnostic Data Collection:** Collect memory dumps, libvirt/qemu logs or information, system diagnostics etc..

While KubeVirt currently offers the `memorydump` volume source which performs such hotplug pod-level attachment, there is a broader need for a **generalized, extensible mechanism** that can support multiple operational workflows.

This VEP establishes the foundational mechanism that will enable these operational workflows. Adding more individual volumeSource types would lead to API overload and semantic ambiguity - a new, clearly separated mechanism for `virt-launcher`-scoped operations is the optimal solution (more details in [Alternatives](#alternatives)).

## Goals

  * **Establish the foundational mechanism** for pod-level volume attachment in KubeVirt
  * Introduce a new API field, `vmi.spec.utilityVolumes`, for hot-pluggable volumes mounted into the `virt-launcher` Pod without VM guest exposure
  * Enable hot-attach and detach operations on running pods through a controller-managed approach
  * Ensure extensible API design to support diverse operational workflows requiring pod-level volume access

## Non Goals

  * **Implementing specific operational workflows:** This VEP establishes the pod-level volume attachment mechanism; specific use case implementations (backup workflows, memory dump processes, etc.) are outside this scope
  * **Detailing utility volume type implementations:** Focus is on the underlying attachment/detachment mechanism, not on how each utility volume type functions
  * **Defining complete end-to-end workflows:** While this enables various operational capabilities, complete workflow implementations are covered in separate VEPs (e.g., VEP 25 for backup)

## Definition of Users

  * **KubeVirt Controllers:** Primary users with appropriate service account permissions to modify `utilityVolumes` for operational workflows
  * **Kubernetes Administrators/Operators:** Initiate operations through higher-level APIs that trigger controller-managed utility volumes
  * **Backup/DR Solution Vendors:** Develop controllers utilizing this API for VM backup data collection
  * **KubeVirt Developers/Maintainers:** Debug KubeVirt components via controller-managed diagnostic collection

## User Stories

* As a cluster administrator, I want to initiate diagnostic operations through high-level APIs that automatically trigger controllers to manage utility volume attachment and data collection.
* As a developer building KubeVirt extensions, I want a standardized mechanism for controllers to attach temporary volumes to virt-launcher pods for various operational needs.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

The proposed design introduces a new field, `spec.utilityVolumes`, in the `VirtualMachineInstanceSpec`. This field defines **hot-pluggable volumes** mounted directly into the `virt-launcher` Pod, not exposed to the VM guest, ensuring clear separation from guest-facing volumes in `spec.volumes`.

### Key Design Principles

1. **VMI-Only Scope**: Utility volumes are exclusively supported in VMI spec. Attempt to edit VM spec with utility volumes would be rejected.
2. **Controller-Managed Access**: Only KubeVirt controllers with appropriate service account permissions can modify utility volumes.
3. **Foundational Mechanism**: This VEP focuses on establishing the underlying volume attachment/detachment mechanism rather than detailing specific utility volume type implementations.
4. **Workflow Integration**: Controllers managing specific workflows handle both the utility volume management and the operational logic that uses those volumes.
5. **Hot-Plug Consistency**: Monitoring and status reporting leverage existing hot-plug infrastructure and patterns.

### Core Design Components

**New API Field (`utilityVolumes`):**

* `utilityVolumes []UtilityVolume` contains a `name` field, inlined `PersistentVolumeClaimVolumeSource`, and optional `type` field
* `UtilityVolumeType` defines the type of utility operation (`MemoryDump`, `Backup`, etc.) for operational context and controller coordination
* All utility volumes use PersistentVolumeClaims as the underlying storage mechanism for `virt-launcher` Pod operations
* Name collision validation: utilityVolume names must be unique across both `spec.volumes` and `spec.utilityVolumes` lists to prevent naming conflicts
* No corresponding entry in `domain.devices.disks` - explicitly not for the VM domain

**Volume Hot-Attachment/Detachment Mechanism:**

* **Hotplug Process:** Leverages KubeVirt's existing volume hotplug infrastructure, automatically recognized and mounted as directories in the `virt-launcher` Pod
* **Mounting Behavior:** Unlike regular volumes containing `disk.img` files for the VM domain, utility volumes are mounted as filesystem directories directly accessible within the `virt-launcher` Pod environment
* **Lifecycle Management:** Controllers managing specific workflows handle both attachment and detachment by updating the VMI specification. The `virt-controller` watches for changes and `virt-handler` manages Pod volume operations

**Status Reporting:**

Utility volumes use existing VMI `status.volumeStatus` field alongside regular volumes. The `Target` field remains empty since volumes aren't presented to the VM guest. Standard status fields report attachment/detachment lifecycle state only - process-specific monitoring is handled by respective workflow controllers.


### Migration Compatibility

**Design Principle:** Utility volumes should not indefinitely block VM migration. Migration waits with a configurable timeout until no utility volumes exist in the VMI spec before proceeding.

**Migration Behavior:**

VMs with utility volumes remain **migratable** - migration is not rejected outright. When migration is initiated, it will be kept in `Pending` state as long as utility volumes are present in the VMI spec.
> Note: Pending migrations do not count against concurrent migration slots, hence it will not block other migrations from progressing.

Each operation that attached the utility volume needs to be completed and the volumes detached by their respective controllers for the migration to continue. The migration priority mechanism can influence whether operations should be aborted or wait for completion based on migration urgency.

A timeout mechanism will be introduced to prevent indefinite blocking caused by a utility volume that fails to detach. A timer begins at the migration request start, and the migration must transition to `Scheduling` state before timeout expires. If not, the migration will be marked as Failed, preventing system hang and allowing retries once the underlying issue is resolved.

No utility volume attachment pods are created on the destination node during migration. Utility volumes have no point in migrating as they are temporary operational tools for pod-level operations, not part of the VM's persistent state. If desired, utility volumes can be reattached after migration completion.

**Rationale:** This approach balances migration responsiveness with operational continuity by allowing utility operations to complete or abort gracefully while preventing indefinite migration blocking.

## API Examples

The `UtilityVolume` struct provides a simple, PVC-based design for utility volumes:

```go
type UtilityVolume struct {
    // UtilityVolume's name.
    // Must be unique within the vmi, including regular volumes.
    Name string `json:"name"`
    // PersistentVolumeClaimVolumeSource defines the PVC
    // that is hotplugged to virt-launcher
    corev1.PersistentVolumeClaimVolumeSource `json:",inline"`
    // Type represents the type of the utility volume.
    // +optional
    Type *UtilityVolumeType `json:"type,omitempty"`
}

type UtilityVolumeType string

const (
    // MemoryDump represents a volume which will be used to get memory dump
    MemoryDump UtilityVolumeType = "MemoryDump"

    // Backup represents a volume which will be used to get backup output
    Backup UtilityVolumeType = "Backup"
)
```

This design uses PersistentVolumeClaims as the single storage mechanism for all utility volume types, with an optional type field for operational context and controller coordination.

### Add a utility volume

#### Before:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vmi
spec:
  domain:
    # VM domain configuration
  volumes:
    # Existing volumes for the guest VM
    - name: system-disk
      containerDisk:
        image: kubevirt/fedora-cloud-container-disk-demo:latest

```

#### After (controller adds a memoryDump utility volume):
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vmi
spec:
  domain:
    # remains unchanged
  volumes:
    # remains unchanged
    - name: system-disk
      containerDisk:
        image: kubevirt/fedora-cloud-container-disk-demo:latest
  # NEW FIELD:
  utilityVolumes:
    - name: memoryDump-f35782b2bd8c578bea6caf2087efa7e8
      persistentVolumeClaim:
        claimName: data-pvc
      type: MemoryDump
```

## Alternatives

Two approaches involving placing utility volumes within the existing `spec.volumes` list rather than creating a separate `utilityVolumes` field were considered:

**Option A:** Add individual volume source types (e.g., `backupVolumeSource`) alongside the existing `memoryDumpVolumeSource`
**Option B:** Create a single `utilityVolumes` volumeSource type that contains multiple utility volume types, deprecating the standalone `memoryDumpVolumeSource` field

* **Pros:**
    * **Minimal API Surface Changes:** Leverages the existing `spec.volumes` structure and volume processing infrastructure
    * **Consistent with Existing Pattern:** Follows the established `memoryDumpVolumeSource` precedent (Option A) or consolidates under existing VolumeSource patterns (Option B)
    * **Familiar to Users:** Uses the same volume management patterns users already know

* **Cons:**
    * **Semantic Overload:** The `spec.volumes` list is fundamentally designed for volumes presented to the guest VM (except of existing memorydump source). Adding utility-specific volume sources here would overload this concept and confuse users about what volumes are accessible inside vs. outside the VM.
    * **Volume Processing Logic Confusion:** Existing volume processing code in controllers assumes all volumes in `spec.volumes` are for guest VM attachment (disk devices, filesystems). Adding utility volumes here would require extensive conditionals throughout the codebase to differentiate between guest and utility volumes, making the code more complex and error-prone with Option A also doing that for each new type that can be added in the future..
    * **Live Migration Complexity:** The live migration logic processes all volumes in `spec.volumes` as part of the VM's state that may need to be migrated. Utility volumes should NOT migrate (they're Pod-specific), requiring special exclusion logic throughout the migration codebase and creating inconsistent migration behavior within a single volume list.
    * **Snapshot/Backup restore:** VM restore logic typically includes all volumes in `spec.volumes` as part of the VM's persistent state. Utility volumes would be required to be excluded (same as currently done for memory-dump), requiring additional complexity.
    * **API Structure Issues:**
        * **Option A:** API pollution with each new utility use case requiring a new top-level volume source type, leading to an ever-growing list of utility-specific volume sources mixed with guest volume sources.
        * **Option B:** Excessive layering of source types, leading to a complex and deeply nested API structure (e.g., a `volume` with a `volumeSource` of type `utilityVolume` containing a type-specific `utilityVolumeSource`).

**Conclusion:** The chosen design of a separate `utilityVolumes` field alongside the `volumes` list is superior:
- **Clear Separation:** Maintains a clean distinction between guest volumes (`spec.volumes`) and utility volumes (`spec.utilityVolumes`)
- **Unified Management:** Groups all utility-attached volumes under a single, purpose-built field that clearly indicates their shared characteristic of being mounted as directories in the virt-launcher Pod
- **Controller Integration:** Enables tight integration between volume management and the specific workflow controllers that require these volumes
- **API Clarity:** Avoids semantic confusion and excessive nesting, making the API intuitive for users to understand the functionallity and differentiation.
- **Extensible Foundation:** Provides a generalized mechanism that can support future operational needs beyond the immediate use cases


## Scalability
This feature depends on existing [volume hotplug machinery](https://kubevirt.io/user-guide/storage/hotplug_volumes/) which will ultimately be the bottleneck with regard to scale. Adding/removing hotplug volumes requires creating/deleting and maintaining long-running Pods which can limit system scalability.

However, `utilityVolumes` are designed for short-lived operations with a specific lifecycle pattern: attach → collect data/output → detach. The goal is to temporarily attach volumes for data collection (backups, memory dumps, diagnostics, etc) and then promptly detach them once the operation completes. This usage pattern means there should not be many utility volumes attached simultaneously per VM, significantly reducing the scalability impact compared to long-term volume attachments.

## Update/Rollback Compatibility

  * **API Evolution:** Adding `utilityVolumes` to the `VirtualMachineInstance` CRD is an additive change ensuring backward compatibility.
  * **Upgrade Process:**
      * During an upgrade, older `virt-launcher` Pods (managed by pre-upgrade KubeVirt components) will not be aware of or include `utilityVolumes`.
      * New `virt-launcher` Pods, created or recreated by the updated `virt-controller`, will correctly include the `utilityVolumes` defined in the VMI spec.
      * Hot-attachment/detachment operations involving these new `utilityVolumes` would only be possible after the cluster's KubeVirt components are fully upgraded to support the new API and logic.
  * **Rollback:** Rolling back KubeVirt to a version that does not support `utilityVolumes` would mean that the controller would no longer process volumes defined in this new API field. The underlying PVCs and their data remain intact, but the volumes will become inaccessible through the `virt-launcher`. After a rollback, there is no guarantee that such volumes will remain attached, and there will be no mechanism to reattach them using the `utilityVolumes` API.

## MemoryDump Volume Source Deprecation

Moving forward, the `memoryDump` API is planned to transition to a more generalized "utility volumes" design, replacing the current memory dump volume source. This future migration will bring key benefits, including API clarity by separating utility volumes from regular guest storage, simplified management by only requiring VMI-spec updates, and improved consistency across different utility operations.

Note: This transition is not part of this VEP. This VEP focuses exclusively on establishing the foundational utility volumes mechanism, and its completion does not depend on the memory dump migration.

## Functional Testing Approach

### Unit Tests

- **API Testing:**
  - Validation of the new `utilityVolumes` field with `UtilityVolume` struct containing PVC configuration and type
  - Proper handling of various utility volume types (`MemoryDump`, `Backup`, etc.) with PVC-based storage
  - Error handling for invalid or malformed utility volume specifications
  - Name collision validation between `volumes` and `utilityVolumes` lists
- **Controller Logic:**
  - `virt-controller` logic for parsing `utilityVolumes` and managing dynamic attachment/detachment
  - `virt-handler` logic for interpreting `UtilityVolumeSource` and patching `virt-launcher` Pod specs
  - Lifecycle management through different phases (in progress, failed, completed)
  - Migration controller logic for detecting utility volumes in VMI spec and keeping migration in `Pending` state
  - Migration timeout mechanism and failure handling when utility volumes don't detach within configured timeout

### End-to-End Tests

- **Volume Lifecycle:**
  - Hot-attachment and detachment of `utilityVolumes` to running `virt-launcher` Pods
  - Status reporting in VMI volume status reflects utility volume state correctly

### Migration Test Coverage

- Verify VMs with utility volumes remain migratable and migration enters `Pending` state when utility volumes detected
- Verify migration waits until VMI spec contains no utility volumes before advancing to `Scheduling`
- Verify configurable timeout causes migration failure rather than forcing detachment, allowing retries
- Verify no utility volume attachment pods created on destination node during migration

## Implementation Phases

### Alpha

**Complete Implementation**: All functionality is implemented in the alpha phase:

  * **API Design**: `utilityVolumes` field in VMI API with `UtilityVolume` struct containing PVC configuration and type classification
  * **Feature Gate**: `UtilityVolumes` feature gate disabled by default
  * **Controller Logic**: Identifing utilityVolume in VMI spec, attaching and detaching accordingly.
  * **Migration Integration**: Migration controller logic to detect utility volumes and maintain `Pending` state until VMI spec contains no utility volumes
  * **Migration Timeout**: Configurable timeout mechanism to prevent indefinite migration blocking with failure handling
  * **Status Reporting**: Integration with existing volume status mechanisms
  * **Future Compatibility**: Designed to support future memory dump migration if desired
  * **Error Handling**: Comprehensive error handling and resilience
  * **Testing**: Unit and end-to-end test coverage including migration scenarios

### Beta

- **Feature Gate**: `UtilityVolumes` feature gate enabled by default
- **Maturity Criteria**: Feature has been tested and proven stable in real-world scenarios for at least one release

### GA

- **Feature Gate Removal**: Remove the `UtilityVolumes` feature gate entirely
- **Production Readiness**: Feature meets all criteria for production deployment
- **Legacy Cleanup**: Complete deprecation of `memoryDump` volume source (planned for 3 releases after GA)
