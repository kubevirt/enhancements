# VEP 199: NVIDIA Grace GPU Passthrough Baseline in KubeVirt

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/199) (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP introduces a small, **inference-first** baseline that lets KubeVirt run NVIDIA Grace Hopper and Grace Blackwell (GB200/GB300) GPU passthrough VMs on ARM64.

It is gated by a new `GraceIOVirtualization` feature gate and is intentionally limited to what has already been validated in the [kubevirt-aie](https://github.com/kubevirt/kubevirt-aie) fork.

The Phase 1 contract is:

- A new `GraceIOVirtualization` feature gate controlling all Grace-specific domain conversion behavior.
- Explicit dependency on `PCINUMAAwareTopology` ([VEP 115](https://github.com/kubevirt/enhancements/issues/115)) for NUMA-aware PCI placement.
- Explicit dependency on the generic IOMMUFD path defined by [VEP 266](https://github.com/kubevirt/enhancements/issues/266) ([design PR #267](https://github.com/kubevirt/enhancements/pull/267)) for host device binding and FD delivery.
- SMMUv3 IOMMU device emission per PCIe bus that hosts a passed-through Grace GPU.
- ACPI Generic Initiator (GI) guest NUMA topology derived from the host via ACPI/sysfs and remapped onto guest NUMA cell IDs.
- Automatic, platform-aware large-BAR `pcihole64` sizing from device BARs.
- Admission validation for the Grace-specific configuration constraints introduced by Phase 1.

EGM, vCMDQ, PCIe link speed/width modeling, PCIe switch topology mirroring, mixed GPU+NIC topologies, and any per-GPU PXB isolation work that goes beyond the kubevirt-aie baseline are explicitly **out of scope** for this VEP and will be specified in follow-up VEPs or design documents.

## Motivation

NVIDIA Grace Hopper and Grace Blackwell systems are becoming a standard substrate for AI and HPC on ARM64. Running these workloads inside KubeVirt VMs gives operators multi-tenancy, security isolation, and a Kubernetes-native lifecycle, but Grace introduces Cache Coherent Interconnect via NVLink-C2C between CPU and GPU and ARM64-specific I/O virtualization requirements (SMMUv3, ACPI Generic Initiator NUMA cells, very large device BARs) that x86 passthrough does not have.

Without first-class support, operators must hand-craft QEMU command lines covering SMMUv3, GI NUMA cells, and 64-bit PCI hole sizing per VM. This VEP builds on the Grace baseline validated in [kubevirt-aie](https://github.com/kubevirt/kubevirt-aie) fork and defines the remaining Phase 1 guest-visible contract needed for upstream KubeVirt.

## Grace Platform Requirements

Grace GPU passthrough requires KubeVirt to construct more than a conventional PCI host-device assignment. The guest platform must expose the IOMMU, NUMA, and MMIO resources expected by the NVIDIA guest driver and firmware.

### Guest-visible SMMUv3

Grace systems use ARM SMMUv3 for hardware-accelerated DMA translation. For the Grace GPU passthrough configurations targeted by Phase 1, the guest-visible SMMUv3 topology is part of the device contract rather than an opaque host detail. KubeVirt must create the required SMMUv3 devices for the PCI buses that contain passed-through Grace GPUs and attach the host devices to those IOMMU instances during domain generation.

### ACPI Generic Initiator Topology

Grace platforms expose GPU-associated NUMA topology through ACPI SRAT and sysfs. The guest needs corresponding ACPI Generic Initiator structures so the NVIDIA guest driver can associate passed-through GPUs with the NUMA nodes and memory resources expected by the platform. KubeVirt derives this topology from host sysfs and emits the guest NUMA and GI information automatically.

### Large-BAR MMIO Aperture

Grace GPUs expose large 64-bit prefetchable PCI BARs that can exceed the default QEMU 64-bit PCI MMIO aperture. If the aperture is too small, guest firmware can fail to assign the GPU BARs, which can prevent the VM from booting or leave the GPU unusable. KubeVirt therefore auto-sizes `pcihole64` from the assigned device BARs during domain generation. The computed value is implementation-owned and is not exposed as a VMI API in Phase 1.

## Goals

- Make Grace GPU passthrough work on Grace Hopper and Grace Blackwell using the inference-first model already validated in kubevirt-aie.
- Confine all Grace-specific domain conversion behavior behind the `GraceIOVirtualization` feature gate.
- Reuse existing KubeVirt building blocks (VEP 115 NUMA-aware PCI placement, VEP 266 IOMMUFD) instead of duplicating them.
- Derive Grace-specific guest topology (GI NUMA cells, NUMA distances, MMIO aperture) from host ACPI/sysfs at domain conversion time.
- Reject statically invalid Grace VMI configurations at admission time, and fail fast with actionable errors for node-local discovery or runtime failures.

## Non Goals

- Defining a user-visible API for Grace topology (GI cell counts, NUMA distances, MMIO sizing, per-GPU PCI placement). All of these are inferred from the host.
- EGM (Extended GPU Memory) backing for guest memory.
- vCMDQ hardware command queue virtualization.
- PCIe link speed and width modeling on guest root ports.
- PCIe switch topology mirroring (e.g. ConnectX/BlueField/NVMe behind a switch).
- Mixed GPU + SR-IOV NIC topologies with independent isolation policies.
- Per-GPU PXB isolation beyond what is already validated by the kubevirt-aie baseline.
- Generic large-BAR `pcihole64` API. This VEP does not introduce an override option to escape auto-configuration, see [Large-BAR MMIO Aperture (auto-sized)](#large-bar-mmio-aperture-auto-sized).
- vGPU or MIG orchestration. This VEP covers passthrough only.
- x86 platforms or non-Grace platforms.
- Upstreaming the NVIDIA QEMU/libvirt patches; those are a platform prerequisite, not a KubeVirt code change.
- Packaging NVIDIA QEMU/Libvirt with specific support on Grace-system for KubeVirt.

## Definition of Users

Infrastructure administrators, cloud providers, and platform engineers deploying NVIDIA Grace Hopper or Grace Blackwell systems for AI and HPC workloads on ARM64 Kubernetes clusters.

## User Stories

- As a platform engineer, I want to assign one or more Grace GPUs to a VMI using the standard KubeVirt host device API and have KubeVirt configure SMMUv3, GI NUMA cells, and the 64-bit PCI hole automatically.
- As an infrastructure administrator, I want KubeVirt to reject Grace VMIs that are statically incompatible with the Phase 1 baseline (for example missing feature gates or an unsupported architecture) at admission time rather than at VM boot.
- As a cluster operator, I want Grace-specific behavior to be off by default and opt-in through a single feature gate so existing non-Grace workloads are unaffected.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) upstream.
- [kubevirt/kubevirt-aie](https://github.com/kubevirt/kubevirt-aie) for the validated Grace baseline; see [release-1.8-aie-nv](https://github.com/kubevirt/kubevirt-aie/tree/release-1.8-aie-nv) and [NVIDIA fork branch](https://github.com/kubevirt/kubevirt-aie/tree/release-1.7-aie-nv)
- [kubevirt/iommufd-device-plugin](https://github.com/kubevirt/iommufd-device-plugin) for the IOMMUFD device plugin used by VEP 266.

---

## Design

### Design Principles

This VEP follows an **inference-first** model.

Operators do not describe Grace topology in the VMI spec. They request Grace GPUs through the existing host-device flow, and KubeVirt `virt-launcher` derives Grace-specific guest topology from host state at domain conversion time.

| Behavior | Source |
| --- | --- |
| Whether Grace handling applies | `GraceIOVirtualization` feature gate, a Grace-class GPU host-device request, and Grace host detection in `virt-launcher` |
| SMMUv3 device emission | VEP 199 guest contract, VEP 115 PCI bus placement, and host IOMMU capability discovery |
| GI NUMA cell count and grouping per GPU | Host ACPI/sysfs, with documented implementation fallback for incomplete platform data |
| Guest NUMA distance vectors involving GI cells | Host ACPI SLIT / `nodeX/distance` when available, remapped to guest cell IDs |
| Large-BAR `pcihole64` size | Assigned device BARs plus implementation-owned platform alignment/safety margins |
| Host device binding via IOMMUFD | Generic VEP 266 IOMMUFD path |
| NUMA-aware PCI placement of Grace GPUs | VEP 115 `PCINUMAAwareTopology`; VEP 199 consumes the resulting guest PCI buses |

This VEP defines the **required guest-visible behavior** for the Phase 1 baseline. It does not prescribe one exact discovery algorithm. An implementation is acceptable if it produces the same guest contract, fails closed when required platform support is missing, and remains compatible with VEP 115 and VEP 266.

### Feature Gates and Dependencies

Phase 1 introduces a single feature gate:

- **`GraceIOVirtualization`** (alpha, off by default) gates all Grace-specific domain conversion behavior described in this VEP. `GraceIOVirtualization` does not implicitly enable any other feature gate. Where Grace depends on existing KubeVirt functionality, those dependencies must be enabled explicitly:

- **`PCINUMAAwareTopology`** ([VEP 115](https://github.com/kubevirt/enhancements/issues/115)) must be enabled. Grace reuses the generic NUMA-aware PCI placement behavior and does not introduce a separate placement planner in Phase 1.

- **IOMMUFD** ([VEP 266](https://github.com/kubevirt/enhancements/issues/266)) must be available for Grace host-device binding. VEP 199 consumes the generic device plugin, FD delivery, and domain-level `<iommufd>` behavior from VEP 266; it does not redefine those mechanisms.

Admission rejects statically invalid Grace VMIs, such as missing required feature gates or unsupported architecture. Node-local details, including actual GI ranges, distance vectors, BAR sizes, and IOMMUFD FD availability, are validated by `virt-launcher` during domain conversion and must fail fast with actionable errors when unavailable.

Dependency status at the time of this VEP:

- **VEP 115 / `PCINUMAAwareTopology`** is tracked by [issue #115](https://github.com/kubevirt/enhancements/issues/115). The tracker is open and lists the feature gate as `PCINUMAAwareTopology`.
- **VEP 266 / IOMMUFD host-device assignment** is tracked by [issue #266](https://github.com/kubevirt/enhancements/issues/266), with the design currently discussed in [PR #267](https://github.com/kubevirt/enhancements/pull/267).

### Activation Surface

#### Grace Device Classification

A **Grace-class GPU host device** is a PCI host device whose KubeVirt `permittedHostDevices` entry maps the requested `deviceName` to a known NVIDIA Grace GPU PCI vendor/device ID. Resource names such as `nvidia.com/GB100_*` are operator-facing handles, but the classification is based on the underlying PCI vendor/device IDs, not on the resource name string alone.

Admission may use the KubeVirt host-device configuration to determine whether a requested `spec.domain.devices.hostDevices[].deviceName` is Grace-class. During domain conversion, `virt-launcher` must verify the assigned BDF through sysfs, for example `/sys/bus/pci/devices/<bdf>/vendor` and `/sys/bus/pci/devices/<bdf>/device`, before applying Grace-specific topology.

Node labels are not the classification mechanism. They may help scheduling, but Grace activation depends on the feature gate, the requested host-device resource, and runtime verification of the assigned PCI device.

There is **no Grace-specific user-facing VMI API** in Phase 1. Grace handling is activated when all of the following are true:

1. `GraceIOVirtualization` is enabled and the required VEP 115 / VEP 266 prerequisites are configured.
2. The VMI requests one or more Grace-class GPU host devices through the existing host-device API.
3. `virt-launcher` detects a Grace-capable host and matching Grace GPU devices.

No annotation is required to opt a VMI into Grace handling. The previously proposed `alpha.kubevirt.io/graceVirtualization` annotation is removed from this VEP. Sub-feature toggles such as EGM and vCMDQ are deferred to follow-up VEPs.

### Phase 1 Guest Contract

When a Grace VMI is admitted, scheduled to a compatible Grace node, and Grace handling is active, KubeVirt provides the following guest-visible behavior:

- Each PCIe bus that carries a passed-through Grace GPU has an associated guest-visible SMMUv3 IOMMU device in the libvirt domain.
- Each passed-through Grace GPU is associated with a guest GI NUMA cell range, and the GPU `<hostdev>` references that range using `<acpi nodeset='...'>`.
- Guest NUMA distances are derived from host ACPI/sysfs data when available and remapped to guest cell IDs. Documented platform defaults may be used only as implementation fallback.
- The `pcie-root` controller has an auto-sized `<pcihole64>` value large enough for the aggregate 64-bit prefetchable BAR footprint plus KubeVirt-owned platform margins.
- Host-device binding uses the generic VEP 266 IOMMUFD path.

---

## SMMUv3 Emission

When Grace handling is active, KubeVirt emits one libvirt `<iommu model='smmuv3'>` element for each guest PCIe bus that carries a passed-through Grace GPU. The `pciBus` value is derived from the guest PCI bus or controller allocated by the VEP 115 NUMA-aware placement logic; VEP 199 does
not prescribe a numbering scheme.

```xml
<devices>
  <iommu model='smmuv3'>
    <driver pciBus='...' accel='on' ats='...' ril='...' ssidSize='...' oas='...'/>
  </iommu>
</devices>
```

`pciBus` is derived from the guest PCI bus/controller allocated by the VEP 115 placement logic. `accel='on'` is required for the Phase 1 Grace contract; VEP 199 does not define a non-accelerated SMMUv3 fallback.

The remaining SMMUv3 attributes are implementation-owned domain XML details. Where possible, KubeVirt should infer them from host IOMMU/device capabilities and the libvirt/QEMU platform defaults. The values used by the current kubevirt-aie baseline, such as `ats='on'`, `ril='off'`, `ssidSize='20'`, and `oas='48'`, may be used as alpha defaults for qualified Grace systems, but they are not user-visible API and are not stable VEP-level constants. If QEMU/libvirt grow reliable auto defaults for these attributes, KubeVirt may omit explicit values and let the lower layers select them.

Notes:

- VEP 199 owns the Grace guest-visible SMMUv3 topology.
- VEP 266 owns the generic IOMMUFD path: device-plugin allocation, FD delivery, per-`<hostdev>` IOMMUFD binding, and the domain-level `<iommufd>` association.
- `cmdqv` is not set in Phase 1. vCMDQ is deferred to a follow-up VEP.
- If the selected node cannot provide the required VEP 266 IOMMUFD transport, KubeVirt must fail closed with an actionable error. Phase 1 does not define a silent downgrade to non-accelerated SMMUv3 for Grace passthrough.

---

## ACPI Generic Initiator Guest Topology

NVIDIA Grace GPU passthrough requires guest ACPI Generic Initiator (GI) NUMA topology for the assigned GPUs. Without the expected GI topology, the NVIDIA guest driver can fail to initialize the passed-through GPU or leave it unusable for CUDA.

### Sysfs-First Discovery Contract

Phase 1 uses a sysfs-first discovery model. KubeVirt derives the guest GI topology from the selected host devices and the host-visible NUMA topology instead of making fixed Grace constants part of the VEP contract.

The `release-1.8-aie-nv` already validated the core sysfs-first discovery primitive: it reads the GPU NUMA node, enumerates online host NUMA nodes, and identifies CPU-less, memory-less nodes from sysfs. VEP 199 builds the upstream contract around that host-discovered topology.

The guest GI cell IDs, GI grouping, and distance matrix are therefore derived from host state. Phase 1 does not define a fixed contract such as exactly 8 GI cells per GPU or a fixed 10/11/40/80/120 distance table, which was validated as an alternative in `release-1.7-aie-nv` branch.

### Guest NUMA Cell Layout

The resulting guest topology adds CPU-less, zero-memory GI cells after the CPU NUMA cells produced by `guestMappingPassthrough`, and references those cells from the corresponding GPU `<hostdev>`:

```xml
<cpu>
  <numa>
    <!-- CPU cells from guestMappingPassthrough (count varies) -->
    <cell id='0' cpus='0-N' memory='...' unit='KiB'/>
    <!-- ... -->

    <!-- GI cells for each passed-through Grace GPU. Count and grouping
         are derived from host ACPI/sysfs. -->
    <cell id='K'   memory='0' unit='KiB'/>
    <cell id='K+1' memory='0' unit='KiB'/>
    <!-- ... -->
  </numa>
</cpu>

<devices>
  <hostdev mode='subsystem' type='pci' managed='no'>
    <source>
      <address domain='0x0000' bus='0xNN' slot='0x00' function='0x0'/>
    </source>
    <acpi nodeset='K-K+M'/>
  </hostdev>
</devices>
```

The number of GI cells per GPU and the exact `nodeset` ranges depend on host ACPI data; they are not part of the VEP contract.

### NUMA Distance Matrix

The guest distance matrix for the synthesized topology is built by remapping host node IDs to guest cell IDs and reading the corresponding host distance vectors from `/sys/devices/system/node/nodeX/distance`. Because guest cells are renumbered (and a partial-passthrough VM may include only a subset of the host's GPUs), distances are projected through the host-to-guest mapping rather than copied verbatim.

If the host does not expose a distance vector for a synthesized cell (for example a GI cell that is not visible via sysfs), the implementation MAY fall back to the kubevirt-aie baseline distance pattern. This fallback is documented as an implementation detail; the VEP contract is "use host ACPI/sysfs distances, remapped to guest cells".

The schema impact (optional `<distances>` child on `<numa><cell>`, optional `cpus` attribute for CPU-less GI cells) is shared with VEP 115 and is not re-specified here.

---

## Large-BAR MMIO Aperture (auto-sized)

Grace GPUs expose large 64-bit prefetchable BARs that exceed default QEMU 64-bit PCI MMIO aperture. If the aperture is too small, guest firmware can fail to assign the GPU BARs, which can prevent the VM from booting or leave the GPU unusable in the guest.

Phase 1 **auto-sizes** `pcihole64` from the assigned PCI host devices. The value is not exposed as a VMI API.

1. For each passed-through PCI device, inspect `/sys/bus/pci/devices/<bdf>/resource`.
2. Sum the sizes of BARs marked as 64-bit prefetchable memory resources.
3. Round the total up using an implementation-defined alignment and safety margin based on the kubevirt-aie baseline.
4. Emit `<pcihole64 unit='KiB'>...</pcihole64>` on the `pcie-root` controller.

```xml
<controller type='pci' index='0' model='pcie-root'>
  <pcihole64 unit='KiB'>...</pcihole64>
</controller>
```

The Phase 1 implementation may use platform-aware safety margins or temporary Grace-specific floors while the generic sizing algorithm is refined. These details are intentionally implementation-owned and are not exposed to users as VMI fields or annotations.

If alpha users find a supported Grace topology where the inferred value is insufficient, the preferred fix is to improve KubeVirt's inference logic. A generic user override, if still required after alpha feedback, should be proposed in a follow-up large-BAR VEP rather than added to VEP 199.

---

## Admission Validation

When `GraceIOVirtualization` is enabled, the admission webhook applies the following Phase 1 checks to VMIs that request Grace-class GPU host devices:

| Check | Reason |
| --- | --- |
| `PCINUMAAwareTopology` feature gate is enabled | Grace placement reuses the VEP 115 planner; without it, NUMA-aware PCI topology is unavailable. |
| IOMMUFD prerequisites are configured at the cluster level | Grace passthrough requires the VEP 266 IOMMUFD path. |
| `spec.architecture` is `arm64` and `spec.domain.machine.type` is `virt` | Grace is ARM64-only and uses the `virt` machine type. |
| `dedicatedCpuPlacement` is `true` | Required so the planner can build deterministic guest NUMA cells. |
| Requested host-device resource names map to permitted Grace GPU PCI vendor/device IDs | Prevents Grace handling from silently applying to unrelated devices. |

VEP 199 does not define or require a fixed list of `deviceName` strings. `deviceName` remains the existing KubeVirt host-device resource handle. Operators map that resource to Grace GPU PCI IDs through KubeVirt host-device configuration, for example `permittedHostDevices.pciHostDevices[].pciVendorSelector` and `.resourceName`, or through an external host-device provider that exposes the same resource-to-BDF allocation information.

KubeVirt's generic PCI device plugin can provide these resources. An external provider, such as NVIDIA's KubeVirt GPU device plugin, may also be used, but VEP-199 does not classify Grace devices from NVIDIA resource-name patterns such as `nvidia.com/GB100_*`. The source of truth is the PCI vendor/device ID.

Admission classifies a requested `spec.domain.devices.hostDevices[].deviceName` as Grace-class only when KubeVirt host-device configuration maps that resource name to a known Grace GPU PCI vendor/device ID, for example through `permittedHostDevices.pciHostDevices[].resourceName` and `pciVendorSelector`. Admission does not classify Grace devices from resource-name patterns or node labels. After scheduling, `virt-launcher` must verify the allocated BDF through sysfs `vendor`/`device` before applying Grace-specific topology. If the allocated device does not match the expected Grace PCI ID, KubeVirt must fail closed with an actionable error. Node labels may be used only for scheduling or operator policy.

Future features (EGM, vCMDQ, mixed GPU+NIC topology, etc.) are not part of this VEP and have no admission rules here.

Admission validation is intentionally limited to information available before the VMI is scheduled. Host-specific discovery failures (for example missing GI sysfs data on a selected node, unreadable BAR resources, or incomplete distance vectors) are reported by `virt-launcher` during domain conversion.
Those failures should be surfaced with actionable events and logs. Grace platform defaults may be used only for explicitly documented alpha fallback paths such as GI grouping or distance construction; IOMMUFD availability is not optional for the Phase 1 guest contract.

---

## API Examples

### KubeVirt Configuration

Enable the Grace baseline and its dependencies on the cluster:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - GraceIOVirtualization
        - PCINUMAAwareTopology
        # IOMMUFD enablement follows VEP 266 (cluster-side device plugin
        # and any associated feature gate it introduces).
```

### Grace Baseline VMI (single representative example)

A representative Phase 1 VMI: a Grace Blackwell node with up to four GPUs passed through using the standard host device API. No Grace-specific annotation, no EGM, no vCMDQ, no SR-IOV NICs in this scope. SMMUv3, GI cells, and the 64-bit PCI hole are populated by `virt-launcher`.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: grace-baseline
spec:
  architecture: arm64
  domain:
    cpu:
      cores: 60
      model: host-passthrough
      sockets: 2
      threads: 1
      dedicatedCpuPlacement: true
      numa:
        guestMappingPassthrough: {}
    devices:
      autoattachGraphicsDevice: false
      autoattachMemBalloon: false
      disks:
        - disk:
            bus: virtio
          name: rootdisk
      interfaces:
        - name: default
          bridge: {}
      hostDevices:
        - deviceName: nvidia.com/GB100_HGX_GB200
          name: gpu1
        - deviceName: nvidia.com/GB100_HGX_GB200
          name: gpu2
        - deviceName: nvidia.com/GB100_HGX_GB200
          name: gpu3
        - deviceName: nvidia.com/GB100_HGX_GB200
          name: gpu4
    firmware:
      bootloader:
        efi:
          secureBoot: false
    machine:
      type: virt
    memory:
      guest: 16Gi
    resources:
      requests:
        memory: 10Gi
  networks:
    - multus:
        default: true
        networkName: default/ovn-primary
      name: default
  volumes:
    - name: rootdisk
      persistentVolumeClaim:
        claimName: pvc-rootdisk
```

This is intentionally the only VMI example in the VEP. EGM-backed, vCMDQ-enabled, and mixed GPU+NIC examples are deferred to the VEPs that introduce those features.

---

## Schema Changes

VEP 199 introduces no KubeVirt CRD schema changes. It adds no VMI fields and no VMI annotations. The only new user-visible activation surface is the alpha `GraceIOVirtualization` feature gate, combined with existing host-device requests and the required VEP 115 / VEP 266 dependencies.

The implementation emits or consumes the following libvirt domain XML as internal domain-generation behavior. These XML elements are not exposed as VMI API:

| Domain XML | Ownership | Purpose |
| --- | --- | --- |
| `<iommu model='smmuv3'>` with `<driver pciBus='...' .../>` | VEP 199; validated in the downstream kubevirt-aie baseline | Emits guest-visible SMMUv3 instances for PCIe buses carrying assigned Grace GPUs. |
| `<acpi nodeset='...'>` | VEP 199 | Binds each Grace GPU `<hostdev>` to the derived guest GI NUMA cell range. |
| `<pcihole64 unit='KiB'>` | VEP 199 behavior using existing domain XML support | Sets the auto-sized 64-bit PCI MMIO aperture for large GPU BARs. |
| `<distances>` and NUMA `<cell>` attributes | VEP 115, consumed and extended by VEP 199 for Grace GI cells. | Expresses guest NUMA cells and remapped distance vectors, including Grace GI cells. |
| `<iommufd enabled='yes' fdgroup='...'/>` | VEP 266 | Provides the generic IOMMUFD domain binding consumed by Grace host devices. |

If an upstream implementation lacks any required internal libvirt XML structs, those structs may be added to `virtwrap/api/schema.go` as implementation detail. They do not create a new VMI API surface.

---

## Out of Scope

The following items appeared in earlier VEP-199 drafts but are removed from the Phase 1 contract. They are deferred until the Phase 1 baseline lands and the relevant pieces are ready for their own focused design discussion:

- **EGM (Extended GPU Memory).** Guest memory backed by `/dev/egmN`, `<memory model='egm'>` device elements, and EGM-specific allocation policy.

- **vCMDQ (hardware command queue virtualization).** SMMUv3 `cmdqv='on'` and the associated host kernel, QEMU, and guest-driver prerequisites.

- **PCIe link speed and width modeling.** Per-root-port `x-speed` / `x-width` overrides derived from the host PCI path.

- **PCIe switch topology mirroring.** Mirroring multi-element host PCI paths using guest PCIe switch controller models.

- **Mixed GPU and SR-IOV NIC topologies.** Independent isolation policies per device type and the corresponding admission rules.

- **Grace-specific PCI bus isolation policy beyond the Phase 1 baseline.** Phase 1 emits the SMMUv3 topology needed for the PCI buses that contain assigned Grace GPUs and reuses the generic NUMA-aware PCI placement behavior from VEP 115. It does not introduce a Grace-specific PXB allocation algorithm, dedicated-PXB policy, or the previously proposed `dedicatedPXB` rule.

- **User-facing `pcihole64` override API.** Earlier drafts discussed exposing a VMI annotation for 64-bit PCI MMIO aperture overrides, the downstream prototype [NVIDIA fork branch](https://github.com/kubevirt/kubevirt-aie/tree/release-1.7-aie-nv) used `alpha.kubevirt.io/pciHole64Size` annotation. A large-BAR override is not Grace-specific and should be considered in a follow-up generic large-BAR VEP if alpha feedback proves it is still needed. VEP 199 relies on auto-sizing and implementation-owned platform-aware margins.

- **Live migration of Grace VMIs.** Live migration of Grace VMIs is not supported in Phase1. Grace guest topology is derived from node-local state, including assigned PCI BDFs, SMMUv3/IOMMUFD availability, GI node discovery, NUMA distances, and large-BAR sizing. This VEP does not define migration compatibility checks, device state transfer, or destination-side topology reconstruction.

- **Multi-node NVLink / GB200 NVL topology modeling.** Multi-node NVLink topology modeling is out of scope for the alpha baseline.

---

## Alternatives

- **Hardcoded GI and distance constants in KubeVirt.** Earlier drafts encoded fixed values, such as 8 GI nodes per GPU and a fixed 10/11/40/80/120 NUMA distance pattern. As part of this VEP contract, Phase 1 treats host discovery as the source of truth. Any hardcoded Grace values are limited to implementation-owned fallback behavior for known platform gaps and are not part of the user-facing API or guest contract. see [ACPI Generic Initiator Guest Topology](#acpi-generic-initiator-guest-topology)

- **User-controlled `pcihole64` override as the Phase 1 API.** Earlier drafts exposed a VMI annotation to override the 64-bit PCI MMIO aperture. The AIE working group discussion favored an inference-first model: KubeVirt should own known Grace large-BAR sizing behavior instead of requiring users to discover and apply per-VM workarounds. VEP 199 therefore does not add a user-facing `pcihole64` field or annotation. If alpha feedback shows that a generic override is still required, it should be proposed in a follow-up large-BAR VEP rather than added to the Grace-specific Phase 1 contract.

- **Grace-specific IOMMUFD plumbing in this VEP.** Earlier drafts described the IOMMUFD device plugin, FD transport, and per-`<hostdev>` driver semantics. These mechanisms are generic host-device infrastructure and are now owned by [VEP 266](https://github.com/kubevirt/enhancements/issues/266). VEP 199 consumes the VEP 266 IOMMUFD path and limits itself to the Grace-specific guest platform contract built on top of it.

---

## Scalability

Phase 1 inherits most of its scalability profile from the building blocks it depends on: VEP 115 handles NUMA-aware PCI placement, and VEP 266 handles host device binding and IOMMUFD FD delivery.

For each VMI, Grace-specific work is local to the assigned host devices and the host NUMA/sysfs topology discovered by `virt-launcher`. Guest GI/NUMA cells scale with the discovered Grace GPU topology and host GI grouping. The auto-sized `pcihole64` value scales with the aggregate 64-bit prefetchable BAR footprint of the passed-through devices plus any implementation-owned platform margin.

VEP 199 does not introduce new control-plane or data-plane components. It adds admission checks and domain conversion behavior on top of the existing KubeVirt components and the VEP 115 / VEP 266 mechanisms.

## Update/Rollback Compatibility

- Existing VMs that do not request Grace-class GPU host devices are unaffected. Grace-specific domain conversion is reached only when `GraceIOVirtualization` is enabled, the VMI requests a Grace-class GPU host device through the standard host device API, and `virt-launcher` detects a Grace-capable host.
- Disabling `GraceIOVirtualization` disables Grace-specific domain conversion for newly admitted or restarted VMIs. Existing non-Grace host-device behavior, including the generic IOMMUFD path from VEP 266, is unaffected.
- Because Phase 1 introduces no stable VMI API fields or annotations, rollback does not require an API deprecation cycle. Grace VMIs created while the alpha feature gate was enabled may require the feature gate and platform prerequisites to remain available in order to restart or be recreated.

## Functional Testing Approach

### Unit tests

- SMMUv3 emission: one `<iommu model='smmuv3'>` per Grace GPU bus, with `accel='on'`, no `cmdqv`, and inferred or documented alpha-default values for implementation-owned attributes such as `ats`, `ril`, `ssidSize`, and `oas`.
- GI discovery from sysfs fixtures: per-GPU GI grouping, deterministic remapping onto guest cell IDs, behavior under partial GPU passthrough, and the documented fallback path.
- Distance matrix construction from host distance vectors and the host-to-guest cell mapping.
- `pcihole64` auto-sizing from synthetic `/sys/bus/pci/devices/<bdf>/resource` fixtures, including platform-aware alignment margins or floors.
- Admission webhook: each Phase 1 rule is exercised in both accept and reject directions.

### End-to-end tests

- On Grace hardware (Hopper and/or Blackwell): VMI boots, the in-guest NVIDIA driver initializes, and `nvidia-smi` reports the passed-through GPUs.
- With `GraceIOVirtualization` disabled, Grace VMIs are rejected by admission. The validated reference behavior is the kubevirt-aie test suite that landed with [kubevirt-aie#10](https://github.com/kubevirt/kubevirt-aie/pull/10).

## Implementation History

## Platform Prerequisites

- ARM64 Grace Hopper or Grace Blackwell host with SMMUv3 nested translation support and IOMMUFD enabled in the host kernel (Linux >= 6.2 for IOMMUFD, see [VEP 266](https://github.com/kubevirt/enhancements/issues/266)).
- NVIDIA-patched QEMU and libvirt providing SMMUv3 nested translation, per-bus `<iommu model='smmuv3'>`, `<acpi nodeset='...'>` on `<hostdev>`, and the domain-level `<iommufd>` element. The Phase 1 baseline does not require vCMDQ, EGM, or vGPU support in QEMU/libvirt.
- These NVIDIA patches are being upstreamed; until acceptance they remain a platform prerequisite for Grace deployments.

## Graduation Requirements

### Alpha (v1.9)

- `GraceIOVirtualization` feature gate implemented and off by default.
- Phase 1 guest contract emitted by `virt-launcher` for Grace VMIs as described in this VEP.
- Admission validation enforced as described in [Admission Validation](#admission-validation).
- Unit tests for each Phase 1 component.
- End-to-end tests on Grace hardware that boot a Grace VMI and validate the in-guest NVIDIA driver brings up the GPU.

### Beta

- NVIDIA QEMU/libvirt patches required by Phase 1 are accepted upstream.
- Operational documentation for cluster operators of Grace hosts.
- Extended testing on Grace Hopper, GB200, and GB300.

### GA

- `GraceIOVirtualization` enabled by default on detected Grace hosts.
- Stable, documented behavior across Grace Hopper, GB200, and GB300, with a clear policy for any Grace-only alpha escape hatches introduced during alpha (either promoted to a stable API by a follow-up VEP, or removed).
