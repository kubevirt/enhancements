# VEP #60: Individual and Multifunction Host Devices  

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview  

This proposal aims to enhance KubeVirt's host devices interface by improving support for multifunction passthrough. These changes will maintain backward compatibility while providing additional flexibility in device assignment and power management.  

## Motivation  

KubeVirt's existing host devices interface is designed to provide a generic model for PCI passthrough, allowing VirtualMachines (VMs) to boot anywhere in the cluster. While this abstraction is valuable, it presents challenges in specific scenarios:  

**Improved Multifunction PCI Passthrough**  
- Some PCI devices expose multiple functions (e.g., a GPU with an integrated audio controller or a network card with multiple interfaces).  
- Inconsistent function allocation across different physical devices can affect performance and initialization.  
- Power management behavior varies between single-function and multi-function passthrough, affecting device readiness after VM boot.  

Passing the entire host device including all of its functions allows KubeVirt to be used in automated testing farms of pci host devices.  
This allows CI jobs and PCI driver developers to request a virtual machine with a host device and gain access to the entire device.  
For PCI driver development, it's crucial to boot a VM with a preserved PCI device tree mirroring the host system and to perform a bus reset prior to VM startup.  

## Goals  

- Allow booting a VM using an entire PCI device with all its associated functions.  

## Non-Goals  

- Support for live migration.  

## Definition of Users  

- VM owners.  

## User Stories  

- As a VM owner, I want the ability to allocate an entire PCI device, including all its functions, to my VM.  

## Repositories Impacted  

- [https://github.com/kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)  
- [https://github.com/harvester/pcidevices](https://github.com/harvester/pcidevices) - example 3rd party controller  

## Design  

### Maintain Backward Compatibility  

Retain existing fields in the VirtualMachine specification:  
- `spec.domain.devices.hostDevices.name` *(currently required)*  
- `spec.domain.devices.hostDevices.deviceName` *(currently required)*  

Preserve the registration process in third-party controllers:  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.PCIVendorSelector` *(currently required)*  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.ResourceName` *(currently required)*  

### Introduce PCI Device Grouping Mechanism  

- Allow controllers to optionally group functions of the same device filtered using `PCIVendorSelector`:  
  - `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.GroupFunctions` *(bool, default false)*  

If `GroupFunctions` is `false`, the `virt-launcher` pod will own a single function of the device (the current behavior).  
If `GroupFunctions` is `true`, the `virt-launcher` pod will own the entire device, including all of its functions.  

### VirtualMachine Specification Updates  

- `spec.domain.devices.hostDevices.name` *(stays required)*  
- `spec.domain.devices.hostDevices.deviceName` *(stays required)*  

### In depth dive into current KubeVirt code

#### Common consept

Similar to how devices are passed to the `virt-launcher` pod today (via an environment variable prefixed with `PCI_RESOURCE`), a new prefix will be introduced: `MULTIFUNCTION_PCI_RESOURCE`.  
For example: `MULTIFUNCTION_PCI_RESOURCE_VENDOR_COM_DEVICE="0000:02:00.0,`.  

The `MULTIFUNCTION_PCI_RESOURCE` variable will contain a comma-separated list of PCI addresses (`domain:bus:device.function`) for each device's **function 0**.  
Function 0 was chosen because, according to the PCIe specification, it must exist on every physical device.  

#### virt-handler

The existing `PCIDevicePlugin` will be updated to handle multifunction device grouping.  

**Current Behavior**  
The `device_controller` creates a `PCIDevicePlugin` that registers all IOMMU groups for a given `resourceName`.  
During allocation, it passes the `PCI_RESOURCE` environment variable to the `virt-launcher` pod.  

**Proposed Behavior**  
The `device_controller` will pass the new `GroupFunctions` flag to the `PCIDevicePlugin`. The plugin's logic will then change based on this flag:  
- When `GroupFunctions` is `true`, the plugin will:
  - Register only the IOMMU group belonging to the device's **function 0**.
  - Handle the Allocate request by passing the new `MULTIFUNCTION_PCI_RESOURCE` environment variable.

If the flag is `false`, the current behavior is preserved.  

When a `virt-launcher` pod requests a `resourceName` managed by `GroupFunctions: true`,  
it will start with a `MULTIFUNCTION_PCI_RESOURCE` environment variable and it will gain ownership of all the device's functions once scheduled.  

#### virt-launcher

`virt-launcher` creates resource pools from the provided environment variables.  
It iterates through the `hostDevices` in the `VirtualMachine` spec and pops a matching resource from the pools.  
If it finds a match, it generates the Libvirt XML using the PCI address from the popped value.  

The new multifunction PCI resource will be treated as a new pool type.  
When `virt-launcher` finds a match for a multifunction device, it will have the PCI address for function 0.
It will then scan the host's `/sys/bus/pci/devices` directory to find all other functions of that device and generate Libvirt XML elements for every associated function.   

TODO: discuss fixing the PCI tree and implement logic in `virt-launcher` that fixes the tree.  
Once all related functions are found they should be grouped to each device and the VM should see all related functions in the context of a single PCI device.  

### Power Management Considerations for Single-Function and Multifunction Passthrough  

During single-function passthrough, a sequence of events occurs within the Linux kernel that completely cuts power to the PCIe slot. *In short, QEMU trigers Linux (VFIO) to reset each function and if possible it will issue a full slot or bus reset*  

When dealing with multifunction devices, a Kubernetes node may have multiple instances of the same multifunction PCI device. If a VirtualMachine requests two function devices, the scheduler may allocate one function from two different physical devices residing for example on two separate pcie slots.  

If this occurs, Linux will not cut power to the host device during passthrough, resulting in behavioral discrepancies compared to scenarios where both functions originate from the same device. *This difference in power state transitions can impact device initialization for certain hardware.*  

## API Examples  

```yaml
# Example of a VirtualMachine with one QAT device, one GRID T4 GPU, and one ConnectX-4 LX device with both ports.
# Requesting a multi-function device works similarly to requesting a single-function device,
# but ensures all required functions (ports) are allocated together from one physical card.
# This setup relies on a controller to register the devices in `KubeVirtConfiguration.permittedHostDevices.pciHostDevice`,
# and specifically the ConnectX-4 LX device using `GroupFunctions: true`.
# Example of a single entry in `PciHostDevice`:
# PciHostDevice.PCIVendorSelector: 15B3:1015
# PciHostDevice.ResourceName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX
# PciHostDevice.GroupFunctions: true
#
# Note: If the cluster has two types of ConnectX-4 cards (e.g., a single-port and a dual-port variant that both use the same vendor selector),
# the VM could be scheduled with either variant.
#
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: example-vm
spec:
  running: true
  template:
    spec:
      domain:
        devices:
          hostDevices:
          - deviceName: intel.com/qat # Intel QAT device
            name: quickaccess1
          - deviceName: nvidia.com/GRID_T4-1Q # NVIDIA GRID T4 GPU
            name: gpu1
          - deviceName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX # Requesting all functions of a ConnectX-4 LX device
            name: net1
```

## Alternatives  

After reviewing the [DRA devices VEP](./10-dra-devices/vep.md), this proposal shares some similarities with the Dynamic Resource Allocation (DRA) mechanism. Key differences include:  

* *Simpler user interface:*  
The grouping mechanism presented here is easier to configure compared to the DRA approach.  
* *Multifunction passthrough handling:*  
DRA does not currently provide an explicit method for ensuring all functions of a device are allocated together.  

## Scalability  

This feature has no significant impact on scalability, as it does not introduce complex scheduling logic and leverages KubeVirt's existing host device management code.  

## Update/Rollback Compatibility  

- The proposed changes are upgrade-compatible.  
- Devices that were configured with `GroupFunctions` in the KubeVirt configuration will be treated as individual functions upon rollback.  

## Implementation Phases  

This feature should be broken down into three independent implementation phases:

1. **Phase 1: Build the core logic into `virt-launcher`**  
This involves adding the new env var to `virt-launcher` - find all the related device functions, and generate the correct Libvirt XML.

2. **Phase 2: Hook into `virt-handler` and the default device plugin**  
Update `virt-handler` and KubeVirt's device plugin to handle requests for these devices. This is optional, as people could write their own device plugins to use the Phase 1 logic.

3. **Phase 3: Correctly order the guest PCI tree**  
We need to make sure the guest OS sees a single device with multiple functions, not a bunch of separate devices.

## Functional Testing Approach  

Phase 1 can be tested using the existing `AddressPool` and `Generic HostDevice` framework under `pkg/virt-launcher/virtwrap/device/hostdevice/generic`.  
Phase 2 requires minimal test adjustions in the existing `pkg/virt-handler/device-manager/pci_device_test.go`.  
Phase 3 can be tested using tests on the logic that places devices on the root complex (see: `PlacePCIDevicesOnRootComplex`).  

## Feature Lifecycle Phases  

Given the limited scope and the fact that an administrator must explicitly opt-in by setting `GroupFunctions: true`, phases 1 and 2 can be implemented in a single release without needing a feature gate.  
