# VEP #60: Individual and Multifunction Host Devices  

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview  

This proposal aims to enhance KubeVirt's host devices interface by enabling more precise targeting of PCI devices and improving support for multifunction passthrough. These changes will maintain backward compatibility while providing additional flexibility in device assignment and power management.  

## Motivation  

KubeVirt's existing host devices interface is designed to provide a generic model for PCI passthrough, allowing VirtualMachines (VMs) to boot anywhere in the cluster. While this abstraction is valuable, it presents challenges in specific scenarios:  

1. **Precise Allocation of Individual PCI Devices**  
   - Users cannot currently request a specific device for debugging or troubleshooting.  
   - Malfunctioning devices cannot be easily assigned to a VM for diagnostics and potential resolution without hardware replacement.  

2. **Improved Multifunction PCI Passthrough**  
   - Some PCI devices expose multiple functions (e.g., a GPU with an integrated audio controller or a network card with multiple interfaces).  
   - Inconsistent function allocation across different physical devices can affect performance and initialization.  
   - Power management behavior varies between single-function and multi-function passthrough, affecting device readiness after VM boot.  

## Goals  

- Enable booting a VM with a specific individual PCI device (for debugging or production use cases).  
- Allow booting a VM using an entire PCI device with all its associated functions.  
- Support requesting different hardware revisions of a device.  

## Non-Goals  

- Support for live migration.  

## Definition of Users  

- VM owners.  
- Cluster administrators.  

## User Stories  

- As a cluster administrator, I want the ability to debug a specific malfunctioning PCI device.  
- As a cluster administrator, I want the ability to boot a VM using a particular PCI device in production.  
- As a cluster administrator, I want the ability to boot a VM using a specific silicon revision of a hardware device.  
- As a VM owner, I want the ability to allocate an entire PCI device, including all its functions, to my VM.  

## Repositories Impacted  

- [https://github.com/kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)  
- [https://github.com/harvester/pcidevices](https://github.com/harvester/pcidevices) - 3rd party controller  

## Design  

### Maintain Backward Compatibility  

Retain existing fields in the VirtualMachine specification:  
- `spec.domain.devices.hostDevices.name` *(currently required)*  
- `spec.domain.devices.hostDevices.deviceName` *(currently required)*  

Preserve the registration process in third-party controllers:  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.PCIVendorSelector` *(currently required)*  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.ResourceName` *(currently required)*  

`KubeVirtConfiguration.permittedHostDevices.PciHostDevice.ResourceName` remains required to ensure compatibility.  

### Introduce PCI Device Grouping Mechanism  

- Allow controllers to optionally assign devices to a group, identified by:  
  - `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.GroupName` *(user-defined)*  
  - `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.GroupUUID` *(system-generated)*  
- A group may contain one or more devices, but all devices within a group must reside on the same node.  
- Users can define custom group names or follow the `vendor.com/device` convention.  

### Implementation Considerations  

#### VirtualMachine Specification Updates  
- `spec.domain.devices.hostDevices.deviceName` *(optional instead of required)*  
- `spec.domain.devices.hostDevices.groupName` *(optional)*  

#### Controller Registration Updates  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.GroupName` *(optional)*  
- `KubeVirtConfiguration.permittedHostDevices.PciHostDevice.GroupUUID` *(optional)*  

### Power Management Considerations for Single-Function and Multifunction Passthrough  
During single-function passthrough, a sequence of events occurs within the Linux kernel that completely cuts power to the PCIe slot. *In short, Linux calls the ACPI function `_PS3` before passing the device to the VirtualMachine and later invokes `_PS0` to restore the device to its fully operational state.*  

When dealing with multifunction devices, a Kubernetes node may have multiple instances of the same multifunction PCI device. If a VirtualMachine requests two function devices, the scheduler may allocate one function from two different physical devices.  

If this occurs, Linux will not cut power to the host device during passthrough, resulting in behavioral discrepancies compared to scenarios where both functions originate from the same device. *This difference in power state transitions can impact device initialization for certain hardware.*  

### Fine-Grained Control Over Devices with Different Silicon Revisions  

Ideally, the `vendor.com/device` convention would provide a reliable method for identifying PCI devices. However, in practice, vendors often advertise different silicon revisions under the same device name, making precise identification challenging.  

Allowing users to define custom group names provides a more granular level of control over their infrastructure, ensuring compatibility with specific hardware revisions. While permitting custom strings in `deviceName` could offer similar flexibility, doing so would break the existing interface and may not be viable.

## API Examples  

```yaml
# Example of a VirtualMachine with one QAT device, one GRID T4 GPU, and one ConnectX-4 LX device with both ports.
# Requesting a group works similarly to requesting an individual device, but ensures all required ports are available.
# This setup relies on a controller registering the ConnectX-4 LX device in `KubeVirtConfiguration.permittedHostDevices.pciHostDevice`.
# The actual config will contain 2 object of this spec:
# PciHostDevice.ResourceName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX
# PciHostDevice.GroupName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX
# PciHostDevice.GroupUUID: "bc3922d5-097d-4d9d-8669-df89b7af35d0"

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
          - groupName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX # Requesting both functions of a ConnectX-4 LX device
            name: net1
```

### Debugging with Individual Device Assignment  

In cases where manual debugging is needed, cluster administrators can create a new temporary group, assign the malfunctioning PCI device to that group, and request a VM using that group.  

Using a temporary group prevents modifications to ResourceName, ensuring compatibility with third-party controllers that expect values in the vendor.com/device format.

## Alternatives  

After reviewing the [DRA devices VEP](./10-dra-devices/vep.md), this proposal shares some similarities with the Dynamic Resource Allocation (DRA) mechanism. Key differences include:

Simpler user interface: The grouping mechanism presented here is easier to configure compared to the DRA approach.

Multifunction passthrough handling: DRA does not currently provide an explicit method for ensuring all functions of a device are allocated together.

## Scalability  

This feature has no significant impact on scalability, as it does not introduce complex scheduling logic and leverages KubeVirt's existing host device management code.

## Update/Rollback Compatibility  

- The proposed changes are upgrade-compatible.
- Upon rollback, existing VirtualMachine objects using the groupName field will not be scheduled.
- The configuration will retain ResourceName for all PciHostDevice objects, ensuring it remains valid even if unused group names and UUIDs persist after rollback.

## Functional Testing Approach  

TODO: Review existing generic host devices tests and adapt them to cover the new grouping mechanism.

## Implementation Phases  

This feature can be implemented in a single phase.

## Feature Lifecycle Phases  

Given its limited functional scope, this feature can be added in a single lifecycle phase without a feature gate.
