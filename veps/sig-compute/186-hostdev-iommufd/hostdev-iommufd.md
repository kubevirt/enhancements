# VEP #186: Support Host Devices Assignment with IOMMUFD

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal advocates for the adoption of IOMMUFD as an alternative
VFIO backend to augment KubeVirt's host device assignment capabilities.

IOMMUFD is a new, general-purpose user API in the Linux kernel intended
to replace existing driver-specific IOMMU implementations, such as
`VFIO_IOMMU_TYPE1`. With the integration in Qemu/KVM virtualization stack,
IOMMUFD now can be used to support device passthrough for VMs.

## Motivation

Certain device plugins, such as the NVIDIA kubevirt-gpu-device-plugin,
have already been modified to include IOMMUFD support (see
[pull request][pr-link]). Meanwhile, the enablement for KubeVirt is missing.

Additionally, this enhancement ensures the proper assignment of PCI/MDEV
host devices (including GPUs and SR-IOV interfaces) to Confidential VMs.

[pr-link]: https://github.com/NVIDIA/kubevirt-gpu-device-plugin/pull/136

## Goals

- Enable the IOMMUFD approach for host devices assignment.

## Non Goals

- Assigning host devices in the same iommu group to different VMs.
- Non PCI/MDEV host devices assignment with IOMMUFD.

## Definition of Users

- VM owners
- Device plugin (or DRA device driver) developers

## User Stories

- As a VM owner, I would like to leverage IOMMUFD for assigning host devices
  to VMs.
- As a developer, I would like to have a well documented way to support
  devices in KubeVirt.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)
- [IOMMUFD Device Plugin](https://github.com/kubevirt/iommufd-device-plugin)

## Design

### IOMMUFD Device Plugin

A new device plugin is introduced for opening and configuring `/dev/iommu`
(IOMMUFD) and passing the file descriptor to virt-launcher pods via
`SCM_RIGHTS` over a unix socket.

The reason is that, IOMMUFD file descriptor needs to be pre-configured with
`IOMMU_OPTION_RLIMIT_MODE` for proper memory pinning accounting during GPU/PCI
device passthrough. Since virt-launcher runs unprivileged and cannot open
`/dev/iommu` itself, this device plugin handles it.

If `/dev/iommu` is not present on the node, the plugin still accepts
allocations and returns a successful empty response, so pods are never
rejected due to missing IOMMUFD support.

### VFIO CDEV Resources Allocation

The VFIO device cdev (character device) - `/dev/vfio/devices/vfioX` -
is introduced by the VFIO subsystem as part of the adaptation of IOMMUFD.
Binding host devices to the `vfio` driver (e.g. `vfio-pci`) results in
the creation of VFIO cdev devices. Each VFIO cdev device is associated
with a specific host device, which is identifiable through the device's
`sysfs` path, for instance,
`/sys/bus/pci/devices/0000:81:00.0/vfio-dev/vfio0`.

Device plugins or DRA (Dynamic Resource Allocation) device drivers (from
vendors or KubeVirt) that intend to use IOMMUFD must target VFIO cdev
devices, instead of the older `/dev/vfio/vfio` container devices and
`/dev/vfio/X` group devices, for assignment to virt-launcher pods.

### Virt Launcher Changes

Our expectation is that KubeVirt should automatically select the optimal way
for utilizing VFIO. Therefore, for each GPU/host device/SR-IOV interface,
device assignment will be handled to use IOMMUFD if both the associated
VFIO cdev and the IOMMUFD device are present within the virt-launcher pod,
otherwise, it will fall back to legacy VFIO.

In the use of IOMMUFD, a domain will be configured with the `iommufd` XML
element, with its mandatory `enabled` attribute set to `'yes'`. This setting
activates IOMMUFD for all subsequent `hostdev` definitions, unless, for the
case that a device falls back to use legacy VFIO, a specific definition is
overridden with `<driver iommufd='no'/>`.

In addition, the domain should request a pre-configured IOMMUFD file descriptor
from the unix socket allocated by the IOMMUFD device plugin, and then should
invoke the [`FDAssociate`][api-ref] libvirt API to hand over the file
descriptor to libvirt and fulfill the `fdgroup` setting of the `iommufd` XML.

[api-ref]: https://pkg.go.dev/libvirt.org/go/libvirt#Domain.FDAssociate

```xml
<domain>
  ...
  <iommufd enabled='yes' fdgroup='iommu'/>
  ...
    <hostdev mode='subsystem' type='pci' managed='no'>
      <source>
        <address domain='0x0000' bus='0x22' slot='0x00' function='0x0'/>
      </source>
      <alias name='ua-gpu-gpu1'/>
      <address type='pci' domain='0x0000' bus='0x0d' slot='0x00' function='0x0'/>
    </hostdev>
    <hostdev mode='subsystem' type='pci' managed='no'>
      <driver iommufd='no'/>
      <source>
        <address domain='0x0000' bus='0x81' slot='0x00' function='0x1'/>
      </source>
      <alias name='ua-hostdevice-local-nic'/>
      <address type='pci' domain='0x0000' bus='0x0e' slot='0x00' function='0x0'/>
    </hostdev>
  ...
</domain>
```

## API Examples

N/A

## Alternatives

### Alternative 1

Refer to the following, users can specify any device in the list of `gpus`,
`hostDevices` or SR-IOV `interfaces` to use IOMMUFD through the associated
`iommufd` field.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: iommufd-example-vmi
spec:
  domain:
    devices:
      gpus:
      - deviceName: nvidia.com/GH100_H100L_94GB
        name: gpu
        iommufd: {}
      - claimName: gpu-iommufd-resource-claim
        name: dra-gpu
        requestName: gpu-iommufd
        iommufd: {}
      hostDevices:
      - deviceName: devices.kubevirt.io/mlx5
        name: host-device
        iommufd: {}
      - claimName: hdev-iommufd-resource-claim
        name: dra-host-device
        requestName: hdev-iommufd
        iommufd: {}
      interfaces:
      - name: sriov-net
        sriov:
          iommufd: {}
```

### Alternative 2

Simply, a unified switch could be implemented to control the use of IOMMUFD for
all device assignments within a given domain.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: iommufd-example-vmi
spec:
  domain:
    useIommufd: true
    devices:
      gpus:
      - deviceName: nvidia.com/GH100_H100L_94GB
        name: gpu
      - claimName: gpu-iommufd-resource-claim
        name: dra-gpu
        requestName: gpu-iommufd
      hostDevices:
      - deviceName: devices.kubevirt.io/mlx5
        name: host-device
      - claimName: hdev-iommufd-resource-claim
        name: dra-host-device
        requestName: hdev-iommufd
      interfaces:
      - name: sriov-net
        sriov: {}
```

However, as IOMMUFD is a very recent introduction, support from device plugins
may not yet be available, which could lead into boot failure upon improper
VMI configuration.

## Scalability

- A new DaemonSet is deployed for the IOMMUFD device plugin.
- A number of unix socket files are created on the nodes that the IOMMUFD
  device plugin is running for passing the IOMMUFD file descriptor.

## Update/Rollback Compatibility

- This is upgrade compatible.
- On rollback, VMs will fall back to the old behavior.

## Functional Testing Approach

- Unit tests: add coverage for new code.
- E2E tests: extend existing (hostdev, SR-IOV, MDEV, ...) tests with the new
  approach.

## Implementation History

## Graduation Requirements

### Alpha

- [] Initial implementation supporting only GPU/PCI device passthrough
- [] Existing GPU/PCI e2e tests pass
- [] Unit tests for new code paths

### Beta

### GA

## References

1. https://www.qemu.org/docs/master/devel/vfio-iommufd.html
2. https://docs.kernel.org/userspace-api/iommufd.html
3. https://dri.freedesktop.org/docs/drm/driver-api/vfio.html#iommufd-and-vfio-iommu-type1
4. https://github.com/NVIDIA/kubevirt-gpu-device-plugin/
5. https://libvirt.org/formatdomain.html#host-device-iommufd
