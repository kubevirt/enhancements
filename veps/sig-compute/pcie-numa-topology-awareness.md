# VEP 115: PCIe NUMA Topology Awareness

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP enables AI workloads in VMs to achieve near-native performance by preserving
host NUMA topology for PCIe devices. KubeVirt will automatically mirror device placement
so AI frameworks can optimize communication paths and avoid cross-socket traffic.

## Motivation

AI workloads in VMs experience significant performance degradation when GPU and network devices
(InfiniBand, RoCE) are presented without proper NUMA topology awareness. AI frameworks such as
NVIDIA's Collective Communication Library (NCCL) and Unified Communication X (UCX) rely on
accurate hardware topology information to optimize communication paths for distributed mode
training. On physical hosts, these frameworks use NUMA affinity information to enable GPUDirect
peer-to-peer communication between co-located devices and select optimal GPU-NIC pairs that
minimize communication latency.

KubeVirt currently presents all PCIe devices as uniformly accessible under a single host bridge,
obscuring their actual NUMA placement. This causes AI frameworks to make suboptimal routing
decisions that result in cross-NUMA memory traffic, even when guest vNUMA topology is properly
configured. The performance impact is substantial: cross-socket communication incurs significantly
higher latency and bandwidth costs, and frameworks cannot leverage direct device-to-device
communication paths.

Current limitations:
- Guest VMs lack visibility into GPU and NIC NUMA placement and device locality.
- AI frameworks cannot optimize communication topology for multi-GPU distributed model training.
- Cross-NUMA memory traffic degrades performance despite correct NUMA-aware device allocation.

## Goals

- Add a new feature gate `PCINUMAAwareTopology`.
- When the afore-mentioned feature gate and `guestMappingPassthrough` are enabled, mirror PCIe
  host device NUMA topology in the guest.

## Non Goals

- Changes in Kubernetes device/resource allocation.
- Hotplug support. This feature only applies during VM creation.
- Support for non-PCIe devices (e.g. USB host devices).

## Assumptions, Constraints, and Dependencies

- **Machine Type**: Requires q35 architecture.
- **Device Support**: PCIe devices only, configured with VFIO passthrough.
- **NUMA Inheritance**: VFs and mdevs inherit NUMA affinity from parent devices.
- **Platform**: Requires KVM/QEMU version supporting `pcie-expander-bus` controller.

## Definition of Users

- Infrastructure administrators managing multi-socket NUMA systems with hardware accelerator workloads.
- Application developers running AI/ML workloads requiring optimal GPU and network performance.
- Cloud providers offering high-performance computing instances.

## User Stories

- As an infrastructure administrator, I want AI workloads in VMs to achieve near-native performance by
  preserving NUMA topology awareness.
- As a developer running distributed ML training, I want my chosen AI framework to automatically detect
  optimal communication paths between GPUs and network devices.
- As a cloud provider, I want to offer GPU instances with predictable, high-performance characteristics.

## Repos

- [kubevirt](https://github.com/kubevirt/kubevirt)

## Design

When creating a VM with NUMA CPU passthrough enabled, KubeVirt will automatically mirror
the host's PCIe device NUMA topology in the guest. This preserves device locality information
that AI frameworks need for optimal performance.

### Core Approach

KubeVirt creates a NUMA-aware PCIe topology using:
- one `pcie-expander-bus` (pxb-pcie) controller per NUMA node.
- one `pcie-root-port` controller per device.
- device placement based on host NUMA affinity.

This approach ensures each passthrough device appears on a guest PCIe bus that corresponds to
its actual host NUMA node, enabling AI frameworks to make optimal communication and memory access
decisions.

A VM with NUMA-aware PCIe topology will look like this:

```
   pcie.0
   --------------------------------------------------------------------------------------
         |                   |                     |                         |
   --------------     ------------------     -------------             -------------
   | PCIe Device |    | pcie-root-port |    |  pxb-pcie   |           |  pxb-pcie   |
   --------------     ------------------    | (NUMA node) |           | (NUMA node) |
                                             -------------             -------------
                                                   |                         |
                                          --------------------      --------------------
                                          |  pcie-root-port  |      |  pcie-root-port  |
                                          --------------------      --------------------
                                                   |                         |
                                             -------------             -------------
                                            | PCIe Device |           | PCIe Device |
                                            |  (GPU/NIC)  |           |  (GPU/NIC)  |
                                             -------------             -------------
```

### Scope

- This feature is enabled only when:
  - the feature gate `PCINUMAAwareTopology` is enabled and
  - the virtual machine's `spec.domain.cpu.numa.guestMappingPassthrough` is set.
- It applies only to passthrough PCIe host devices (GPUs, IB/RoCE NICs, SR-IOV PF/VFs, DRA-backed host devices).
- Mediated devices (mdevs) follow the parent physical PCI device's NUMA node when available. Mediated devices
  should be backed by Virtual Functions (VFs) using VFIO passthrough.
- Devices without NUMA affinity are placed on the default `pci.0` bus and report NUMA node `-1` (absent).

### Device NUMA Affinity Discovery

KubeVirt discovers device NUMA affinity by reading `/sys/bus/pci/devices/<BDF>/numa_node`
from the host filesystem. It then checks if the device's NUMA node matches any of the guest's
configured NUMA nodes. If a match is found, the device will be aligned to that NUMA node; otherwise, it
will default to NUMA node `-1`.

Mediated devices: while PCIe devices already include this information, mediated devices require
additional resolution. We resolve the parent PCIe device address via `/sys/bus/mdev/devices/<mdev-uuid>`.
The mdev UUID is propagated to the virt-launcher pod with the respective environment variable, i.e.
`MDEV_PCI_RESOURCE_<resource-name>`, by either the device plugin or the DRA driver.

DRA devices: the status controller must include the PCIe address in
`DeviceResourceClaimStatus.Attributes.PCIAddress`. If DRA drivers cannot provide this
information, those devices will default to NUMA node `-1`.

### Domain Generation

We propose a new PCI NUMA-aware assigner (in addition to the existing PCI root slot assigner).
This assigner will be executed before any host devices are added to the domain. It will perform
the following steps to create the NUMA-aware PCIe topology for the devices with NUMA affinity information:

1. Discovers the NUMA node for each passthrough PCIe device and if it matches any of the guest's NUMA nodes.
2. Filters out devices without NUMA alignment to be placed on the default `pci.0` bus using the existing root slot assigner.
3. Groups devices by NUMA node: `numaNode -> []api.HostDevice`.
4. Creates `pcie-expander-bus` controllers with `<target busNr="X"><node>Y</node></target>` elements for each NUMA node.
5. Creates one `pcie-root-port` controller per device under the respective `pcie-expander-bus` controller.
6. Assigns device addresses to the `pcie-root-port` aligned with its NUMA node.
7. Inserts the controllers created into the domain before adding the host devices.

### Schema Changes

Since the `virt-launcher/virtwrap/api/schema.go` currently lacks a `target` field in the `Controller`
struct, we need to add a new `ControllerTarget` struct to represent the `<target>` element:

```go
// pkg/virt-launcher/virtwrap/api/schema.go
type Controller struct {
	Type    string            `xml:"type,attr"`
	Index   string            `xml:"index,attr"`
	Model   string            `xml:"model,attr,omitempty"`
	Driver  *ControllerDriver `xml:"driver,omitempty"`
	Alias   *Alias            `xml:"alias,omitempty"`
	Address *Address          `xml:"address,omitempty"`
+   Target  *ControllerTarget `xml:"target,omitempty"`
}

+ type ControllerTarget struct {
+   BusNr *uint32 `xml:"busNr,attr,omitempty"`
+   Node  *uint32 `xml:"node,omitempty"`
+ }
```

BusNr values for `pcie-expander-bus` controllers can be assigned starting from `254` and decrementing
to avoid conflicts with existing controllers. `pcie-root-port` controllers can use bus numbers
`1` through `14` per NUMA node, supporting up to 14 devices each.

According to the libvirt [documentation](https://libvirt.org/formatdomain.html#controllers):
> pci-expander-bus and pcie-expander-bus controllers can have an optional busNr attribute (1-254).
  This will be the bus number of the new bus; All bus numbers between that specified and 255 will be
  available only for assignment to PCI/PCIe controllers plugged into the hierarchy starting with this
  expander bus, and bus numbers less than the specified value will be available to the next lower
  expander-bus (or the root-bus if there are no lower expander buses). If you do not specify a busNumber,
  libvirt will find the lowest existing busNumber in all other expander buses (or use 256 if there are
  no others) and auto-assign the busNr of that found bus - 2, which provides one bus number for the
  pci-expander-bus and one for the pci-bridge that is automatically attached to it (if you plan on adding
  more pci-bridges to the hierarchy of the bus, you should manually set busNr to a lower value).

### Hardware Utils Extension

We will extend the `hardware` package with a function to retrieve a PCIe device's NUMA node
from `/sys/bus/pci/devices/<BDF>/numa_node` and verify it matches the guest's vCPU NUMA affinity.
This enables NUMA-aware device placement during domain generation.

### Open Questions / Follow-Ups

- Mediated devices: do we always know the backing PCIe BDF? If not, extend DRA/device-plugin payloads to supply it.
- Resource ordering: ensure the new controllers obey libvirt constraints (`pcie-expander-bus` busNrs < 256 with room for all the controllers underneath them).
- Performance: reading `/sys` per device should be cheap, but consider caching and logging to detect missing NUMA nodes.
- The downstream bus assigned to each controller is derived from the controller index. Controller indices are assigned
  monotonically by the assigner, and typical deployments remain well below the PCI bus limit of `0xff (255)`. However, if
  future requirements involve exposing hundreds of root ports, the bus numbering scheme should be revisited to avoid
  exhausting the available bus number space before libvirt rejects the configuration.
- PCI 64-bit MMIO Window Limit. The default Q35 machine’s 64-bit PCI hole is insufficient for configurations with multiple
  GPUs attached to pcie-expander-bus controllers. When firmware or the guest OS attempts to map very large 64-bit BARs (Base Address Registers)
  from these devices, address space exhaustion can prevent successful system initialization, causing early boot failures.
- Mirror PCIe topology: ensure that devices are aligned to the same PCIe root (in addition to NUMA node aligment) as on the host.
  This will help frameworks that rely on PCIe hierarchy for locality detection. 

## API Examples

First, open the new feature gate in the KubeVirt CR:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
      - PCINUMAAwareTopology
```

Next, create a VM with NUMA CPU passthrough configuration:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: numa-aware-vm
spec:
  template:
    spec:
      domain:
        cpu:                # CPU NUMA settings
          dedicatedCpuPlacement: true
          numa:
            guestMappingPassthrough: {}
        memory:             # memory NUMA settings
          hugepages:
            pageSize: "2Mi"
        devices:
          gpus:
          - deviceName: nvidia.com/gpu
            name: gpu1
          hostDevices:
          - deviceName: rdma/ib_device
            name: ib1
        resources:
          requests:
            memory: 8Gi
            nvidia.com/gpu: 4
            rdma/ib_device: 2
```

## Implementation Roadmap

1. **NUMA Discovery**: Implement host device topology detection.
2. **Controller Generation**: Create NUMA-aware PCIe bus hierarchy.
3. **Domain Integration**: Modify VM creation pipeline for device placement.
4. **Testing & Documentation**: Comprehensive testing and user guides.

## Examples

### System Configuration
A dual-socket system with:
- **NUMA Node 0**: 4 GPUs + 2 InfiniBand NICs + 1 BlueField DPU
- **NUMA Node 1**: 4 GPUs + 2 InfiniBand NICs + 1 BlueField DPU

### Result
KubeVirt creates:
- one `pcie-expander-bus` controller per NUMA node with a `<target busNr="X"><node>Y</node></target>` element.
- one `pcie-root-port` controller per device.
- devices that are placed on dedicated `pcie-root-port` controllers under the `pcie-expander-bus` controller matching their NUMA node.

### Domain XML

#### Controllers
```xml
<devices>
  <!-- Original pcie-root controller -->
  <controller type='pci' index='0' model='pcie-root'/>

  <!-- NUMA Node 0 pxb-pcie Controller -->
  <controller type='pci' index='1' model='pcie-expander-bus'>
    <model name='pxb-pcie'/>
    <target busNr='248'>
      <node>0</node>
    </target>
    <address type='pci' domain='0x0000' bus='0x00' slot='0x0a' function='0x0'/>
  </controller>

  <!-- NUMA Node 1 pxb-pcie Controller -->
  <controller type='pci' index='2' model='pcie-expander-bus'>
    <model name='pxb-pcie'/>
    <target busNr='240'>
      <node>1</node>
    </target>
    <address type='pci' domain='0x0000' bus='0x00' slot='0x0b' function='0x0'/>
  </controller>

  <!-- Root Ports for NUMA Node 0 (7 devices) -->
  <controller type='pci' index='3' model='pcie-root-port'>
    <target chassis='1' port='0x0'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x00' function='0x0'/>
  </controller>
  <controller type='pci' index='4' model='pcie-root-port'>
    <target chassis='2' port='0x1'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x01' function='0x0'/>
  </controller>
  <controller type='pci' index='5' model='pcie-root-port'>
    <target chassis='3' port='0x2'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x02' function='0x0'/>
  </controller>
  <controller type='pci' index='6' model='pcie-root-port'>
    <target chassis='4' port='0x3'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x03' function='0x0'/>
  </controller>
  <controller type='pci' index='7' model='pcie-root-port'>
    <target chassis='5' port='0x4'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x04' function='0x0'/>
  </controller>
  <controller type='pci' index='8' model='pcie-root-port'>
    <target chassis='6' port='0x5'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x05' function='0x0'/>
  </controller>
  <controller type='pci' index='9' model='pcie-root-port'>
    <target chassis='7' port='0x6'/>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x06' function='0x0'/>
  </controller>

  <!-- Root Ports for NUMA Node 1 (7 devices) -->
  <controller type='pci' index='10' model='pcie-root-port'>
    <target chassis='8' port='0x0'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x00' function='0x0'/>
  </controller>
  <controller type='pci' index='11' model='pcie-root-port'>
    <target chassis='9' port='0x1'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x01' function='0x0'/>
  </controller>
  <controller type='pci' index='12' model='pcie-root-port'>
    <target chassis='10' port='0x2'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x02' function='0x0'/>
  </controller>
  <controller type='pci' index='13' model='pcie-root-port'>
    <target chassis='11' port='0x3'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x03' function='0x0'/>
  </controller>
  <controller type='pci' index='14' model='pcie-root-port'>
    <target chassis='12' port='0x4'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x04' function='0x0'/>
  </controller>
  <controller type='pci' index='15' model='pcie-root-port'>
    <target chassis='13' port='0x5'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x05' function='0x0'/>
  </controller>
  <controller type='pci' index='16' model='pcie-root-port'>
    <target chassis='14' port='0x6'/>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x06' function='0x0'/>
  </controller>
</devices>
```

#### Devices
```xml
<devices>
  <!-- NUMA Node 0 Devices (7 devices on bus 0x01) -->

  <!-- NVIDIA GPUs on NUMA Node 0 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x03' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x07' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x04' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x08' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x05' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x09' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x06' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x0a' function='0x0'/>
  </hostdev>

  <!-- Mellanox IB devices on NUMA Node 0 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x07' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x0b' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x08' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x0c' function='0x0'/>
  </hostdev>

  <!-- BlueField device on NUMA Node 0 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x41' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x01' slot='0x0d' function='0x0'/>
  </hostdev>

  <!-- NUMA Node 1 Devices (7 devices on bus 0x02) -->

  <!-- NVIDIA GPUs on NUMA Node 1 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x83' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x07' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x84' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x08' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x85' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x09' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x86' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x0a' function='0x0'/>
  </hostdev>

  <!-- Mellanox IB devices on NUMA Node 1 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x87' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x0b' function='0x0'/>
  </hostdev>

  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x88' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x0c' function='0x0'/>
  </hostdev>

  <!-- Additional device on NUMA Node 1 -->
  <hostdev mode='subsystem' type='pci' managed='yes'>
    <driver name='vfio'/>
    <source>
      <address domain='0x0000' bus='0x89' slot='0x00' function='0x0'/>
    </source>
    <address type='pci' domain='0x0000' bus='0x02' slot='0x0d' function='0x0'/>
  </hostdev>
</devices>
```

## Scalability

The solution should scale to:

- Large clusters with hundreds of NUMA-enabled nodes.
- Multiple PCIe devices per NUMA node (up to 14+ devices as shown in examples).
- Complex multi-socket systems with 4+ NUMA nodes.
- Efficient controller allocation avoiding resource conflicts.

## Update/Rollback Compatibility

- Feature is opt-in via existing `spec.domain.cpu.numa.guestMappingPassthrough` flag.
- Existing VMs without NUMA specifications continue to work unchanged.
- Devices reporting NUMA node `-1` fallback to the default `pci.0` layout.
- Rollback gracefully disables NUMA-aware features without affecting running workloads.

## Functional Testing Approach

- Unit tests: add cases under `virt-launcher` `virtwrapper` codebase verifying NUMA grouping, controller
  generation, and fallback when NUMA is `-1`.
- Integration tests: craft NUMA VMI functional tests (similar to the ones found in `tests/numa/numa.go`)
  launching with multiple passthrough GPUs on different NUMA nodes; assert domain XML includes `pxb-pcie` `node='0/1'`
  and devices placed under the correct root ports.
- Provide a golden XML fixture akin to `numa-topo-vm-domain.xml` for regression checks.
- Performance benchmarks comparing NUMA-aware vs NUMA-unaware configurations.
- End-to-end tests with AI/ML workloads, e.g. using NCCL.

## Implementation History

<!--
This section will be filled as implementation progresses
-->

## Graduation Requirements

### Alpha
- Basic NUMA topology awareness implemented
- Unit tests and basic integration tests
- Documentation for feature usage

### Beta
- Support for mediated devices and VFs
- Performance validation with real AI workloads
- Comprehensive testing coverage including edge cases
- Integration with DRA (Dynamic Resource Allocation)

### GA
- Production-ready performance and reliability
- Full documentation and operational guides
