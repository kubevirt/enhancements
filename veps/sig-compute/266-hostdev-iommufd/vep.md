# VEP #266: Support Host Devices Assignment with IOMMUFD

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
`VFIO_IOMMU_TYPE1`. With integration into the Qemu/KVM virtualization stack,
IOMMUFD can now be used to support device passthrough for VMs.

## Motivation

The legacy VFIO (`VFIO_IOMMU_TYPE1`) backend does not support the isolation
and security requirements of Confidential VMs (e.g. AMD SEV-SNP, Intel TDX).
IOMMUFD addresses these limitations, making it the required backend for
assigning PCI host devices (including GPUs and SR-IOV interfaces) to
Confidential VMs.

Certain device plugins, such as the NVIDIA kubevirt-gpu-device-plugin,
have already been modified to include IOMMUFD support (see
[pull request][pr-link]). Meanwhile, the enablement for KubeVirt is missing.

[pr-link]: https://github.com/NVIDIA/kubevirt-gpu-device-plugin/pull/136

## Goals

- Enable the IOMMUFD approach for host devices assignment.

## Non Goals

- Assigning host devices in the same iommu group to different VMs.
- Non-PCI host devices assignment with IOMMUFD.

## Definition of Users

- Device plugin (or DRA device driver) developers

## User Stories

- As a developer, I would like to have a well documented way to support
  devices in KubeVirt.

Note: IOMMUFD usage is transparent to VM owners. KubeVirt automatically
selects the optimal VFIO backend, so VM owners do not need to take any
action to benefit from IOMMUFD.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Assumptions, Constraints, and Dependencies

This feature requires the following host software versions:

- Linux kernel >= 6.2 for IOMMUFD support
- QEMU version with IOMMUFD/VFIO cdev backend support
- libvirt >= 12.2.0 for `<iommufd>` XML support

These requirements are critical for operators planning deployment.

## Design

### IOMMUFD Device Plugin

A new device plugin is introduced for opening and configuring `/dev/iommu`
(IOMMUFD) and passing the file descriptor to virt-launcher pods via
`SCM_RIGHTS` over a unix socket. This will be part of the native device plugin
within kubevirt/kubevirt and virt-handler.

IOMMUFD file descriptors need to be pre-configured with
`IOMMU_OPTION_RLIMIT_MODE` for proper memory pinning accounting during GPU/PCI
device passthrough. While virt-launcher can open `/dev/iommu`, it cannot
change the accounting mode to process-based without elevated capabilities,
which is why the device plugin handles it from the privileged virt-handler
context.

This feature will be introduced behind a feature gate named `IOMMUFD`,
as this will allow the community to test, validate and provide feedback before enabling
it by default.

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

It is the responsibility of device plugins or DRA (Dynamic Resource
Allocation) device drivers (from vendors or KubeVirt) to expose the device
paths required by each host device to the virt-launcher pods; these includes:
`/dev/iommu`,`/dev/vfio/Y`, `/dev/vfio/devices/vfioX``/dev/vfio/vfio`.

### Virt Launcher Changes

Our expectation is that KubeVirt should automatically select the optimal way
for utilizing VFIO. Therefore, for each GPU/host device/SR-IOV interface,
device assignment will use IOMMUFD if both the associated VFIO cdev and the
IOMMUFD device are present within the virt-launcher pod; otherwise, it will
fall back to legacy VFIO.

When using IOMMUFD, a domain will be configured with the `iommufd` XML
element, with its mandatory `enabled` attribute set to `'yes'`. This setting
activates IOMMUFD for all subsequent `hostdev` definitions, unless, in the
case where a device falls back to using legacy VFIO, a specific definition is
overridden with `<driver iommufd='no'/>`.

In addition, virt-launcher should request a pre-configured IOMMUFD file
descriptor from the unix socket allocated by the IOMMUFD device plugin, and
then invoke the [`FDAssociate`][api-ref] libvirt API to hand over the file
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

#### Security Considerations
- No new access is granted. Any pod (or process) that request the device on the host can already open /dev/iommu directly. 
It is just a misc character device. The device plugin does not grant any new access or capabilities.
- The IOMMUFD FD itself is harmless. It only becomes useful when VFIO devices are attached to it. 
Attaching VFIO devices requires their own FDs, which are only available to pods that have allocated those specific 
devices via a device plugin. Pods without such allocations cannot make use of the IOMMUFD FD.
- This is purely about memory accounting. Setting IOMMU_OPTION_RLIMIT_MODE changes from global per-user DMA memory 
accounting to per-process RLIMIT_MEMLOCK. It replicates exactly what libvirt 
does in virIOMMUFDOpenDevice (but we cannot do the configuration inside the unprivileged virt-launcher). 
It does not grant any additional capabilities.
- The SCM_RIGHTS socket is pod-specific and one-time. It is created in a pod-specific directory, 
mounted only into the corresponding virt-launcher pod (with proper SELinux relabeling to container_file_t:s0), 
and consumed once. It is not accessible to other pods.


#### FD Passing Lifecycle

When a virt-launcher pod is created that will use IOMMUFD-backed host devices, the plugin creates a unique 
Unix socket in a pod-specific directory.
The socket path is mounted into the virt-launcher container.
During domain setup, virt-launcher connects, receives the FD via SCM_RIGHTS, and calls Domain.FDAssociate.
The socket is single-use and being cleaned up afterward. This prevents reuse or cross-pod access.

## API Examples

N/A

## Alternatives

### Alternative 1

In the following example, users can specify any device in the list of `gpus`,
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
          iommufd: { }
        - claimName: gpu-iommufd-resource-claim
          name: dra-gpu
          requestName: gpu-iommufd
          iommufd: { }
      hostDevices:
        - deviceName: devices.kubevirt.io/mlx5
          name: host-device
          iommufd: { }
        - claimName: hdev-iommufd-resource-claim
          name: dra-host-device
          requestName: hdev-iommufd
          iommufd: { }
      interfaces:
        - name: sriov-net
          sriov:
            iommufd: { }
```

### Alternative 2

A unified switch could simply be implemented to control the use of IOMMUFD for
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
          sriov: { }
```

However, as IOMMUFD is a very recent introduction, support from device plugins
may not yet be available, which could lead to boot failure upon improper
VMI configuration.

### Alternative 3

Do not include the iommufd-device-plugin as a native one in virt-handler
and leave it as an [external plugin](https://github.com/kubevirt/iommufd-device-plugin)
with specific resource requests:

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
          sriov: { }
      resources:
        limits:
          devices.kubevirt.io/iommufd: "1"
```

However, this requires a change in all VMI specs that want to use it,
and requires a separate daemonset to be manually installed.

## Scalability

- A number of unix socket files are created on the nodes that the IOMMUFD
  device plugin is running for passing the IOMMUFD file descriptor.

## Update/Rollback Compatibility

- This is upgrade compatible.
- On rollback, VMs will fall back to the old behavior.

## Functional Testing Approach

- Unit tests: add coverage for new code.
- E2E tests: extend existing (hostdev, SR-IOV, ...) tests with the new
  approach.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate guards all code changes
- [ ] IOMMUFD device plugin deployed and functional
- [ ] New e2e tests specifically covering the IOMMUFD code path
- [ ] Initial implementation supporting only GPU/PCI device passthrough
- [ ] Unit tests for new code paths

### Beta

### GA

## References

1. https://www.qemu.org/docs/master/devel/vfio-iommufd.html
2. https://docs.kernel.org/userspace-api/iommufd.html
3. https://dri.freedesktop.org/docs/drm/driver-api/vfio.html#iommufd-and-vfio-iommu-type1
4. https://github.com/NVIDIA/kubevirt-gpu-device-plugin/
5. https://libvirt.org/formatdomain.html#host-device-iommufd
