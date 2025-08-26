# VEP #60: Individual and Multifunction Host Devices  

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview  

This proposal aims to enhance KubeVirt's host devices interface by improving support for multifunction passthrough. These changes will maintain backward compatibility while providing additional flexibility in device assignment and power management.  

## Motivation  

KubeVirt's existing host devices interface is designed to provide a generic model for PCI passthrough, allowing VirtualMachines (VMs) to boot anywhere in the cluster. While this abstraction is valuable, it presents challenges in specific scenarios:  

**Improved Multifunction PCI Passthrough**  
- Some PCI devices expose multiple functions (e.g., a GPU with an integrated audio controller or a network card with multiple interfaces).  
- Inconsistent function allocation across different physical devices can affect performance and initialization.  
- Power management behavior varies between single-function and multi-function passthrough, affecting device readiness after VM boot.  

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

If `GroupFunctions` is `false`, KubeVirt will create a `PCIDevicePlugin` (for a single function of the device) (same way KubeVirt acts today).  
If `GroupFunctions` is `true`, KubeVirt will create a `PCIMultiFunctionDevicePlugin` (for all functions of the device).  

### VirtualMachine Specification Updates  

- `spec.domain.devices.hostDevices.name` *(stays required)*  
- `spec.domain.devices.hostDevices.deviceName` *(changes to optional instead of required)*  
- `spec.domain.devices.hostDevices.multiFunctionDeviceName` *(new field, optional)*  

If a VM owner requests a `HostDevice` they must provide either `deviceName` or `multiFunctionDeviceName`.  

### In depth dive into current KubeVirt code

#### virt-handler

Pre VEP `device_controller` iterates over the `permittedHostDevices.PciHostDevice` and creates `PCIDevicePlugin`.  
Post VEP `device_controller` creates `PCIDevicePlugin` and `PCIMultiFunctionDevicePlugin` based on `permittedHostDevices.PciHostDevice.GroupFunctions`.  
Each `PCIMultiFunctionDevicePlugin` instance contains instances of `PCIDevice` that are functions of that device (collected using `filepath.Walk(pciBasePath...`).  
This is all the data needed for the `virt-launcher` step.  

#### virt-launcher

Pre VEP `hostdevice` uses `AddressPool` which yields a single address per device.  
The `AddressPool` does not fit the solution presented in this VEP. It will have to be refactored or another solution will be written alongside it. In both cases all code paths that share logic won't be duplicated.  
In any case `virt-handler` prepared enough data to construct the required `xml` with all associated functions of an already allocated device.  

### Power Management Considerations for Single-Function and Multifunction Passthrough  
During single-function passthrough, a sequence of events occurs within the Linux kernel that completely cuts power to the PCIe slot. *In short, Linux calls the ACPI function `_PS3` before passing the device to the VirtualMachine and later invokes `_PS0` to restore the device to its fully operational state.*  

When dealing with multifunction devices, a Kubernetes node may have multiple instances of the same multifunction PCI device. If a VirtualMachine requests two function devices, the scheduler may allocate one function from two different physical devices.  

If this occurs, Linux will not cut power to the host device during passthrough, resulting in behavioral discrepancies compared to scenarios where both functions originate from the same device. *This difference in power state transitions can impact device initialization for certain hardware.*  

## API Examples  

```yaml
# Example of a VirtualMachine with one QAT device, one GRID T4 GPU, and one ConnectX-4 LX device with both ports.
# Requesting a multi-function device works similarly to requesting an individual device, but ensures all required ports are available.
# This setup relies on a controller to register the ConnectX-4 LX device in `KubeVirtConfiguration.permittedHostDevices.pciHostDevice`.
# Example of a single entry in `PciHostDevice`:
# PciHostDevice.PCIVendorSelector: 15B3:1015
# PciHostDevice.ResourceName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX
# PciHostDevice.GroupFunctions: true

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
          - multiFunctionDeviceName: mellanox.com/MT27710_FAMILY_CONNECTX4_LX # Requesting both functions of a ConnectX-4 LX device
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
- Upon rollback, existing VirtualMachine objects using the `multiFunctionDeviceName` field will not be scheduled.  
- The configuration will retain `ResourceName` for all `PciHostDevice` objects, ensuring it remains valid even if the new `GroupFunctions` persists after rollback.  

## Functional Testing Approach  

TODO: Review existing generic host devices tests and adapt them to cover the new grouping mechanism.  

## Implementation Phases  

This feature can be implemented in a single phase.  

## Feature Lifecycle Phases  

Given its limited functional scope, this feature can be added in a single lifecycle phase without a feature gate.  
