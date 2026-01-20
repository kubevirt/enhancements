# VEP #183: SR-IOV Network DRA Support

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

This proposal adds support for DRA (Dynamic Resource Allocation) provisioned SR-IOV network devices in KubeVirt.
It extends the existing KubeVirt networks API with a new `ResourceClaimNetworkSource` type, allowing SR-IOV NICs to be allocated via DRA while maintaining compatibility with the existing Multus-based SR-IOV approach.

This VEP builds upon the core DRA infrastructure defined in VEP #10 ([kubevirt/enhancements/pull/11](https://github.com/kubevirt/enhancements/pull/11)) to add support for network devices, specifically SR-IOV NICs.

## Motivation

DRA adoption for network devices is important for KubeVirt so that network device vendors can expect
the same level of control when using Virtual Machines as they have with Containers.
DRA allows network device vendors fine-grained control over device allocation and topology.

## Goals

- Introduce the API changes needed to consume DRA-enabled SR-IOV network devices in KubeVirt
- Introduce how KubeVirt will consume SR-IOV devices via external DRA drivers
- Seamlessly support DRA-based SR-IOV use cases available to containers in KubeVirt VMIs
- Support custom MAC addresses for DRA-based SR-IOV networks

## Non Goals

- Replace existing Multus-based SR-IOV network integration (remains fully supported)
- Deploy DRA SR-IOV driver (handled by sriov-network-operator)
- Support coexistence of DRA SR-IOV and device-plugin SR-IOV
- Live migration of VMs with DRA network devices

## Definition of Users

- **User**: A person who wants to attach SR-IOV network devices to a VM
- **Admin**: A person who manages infrastructure and configures DRA device classes and drivers
- **Developer**: A person familiar with CNCF ecosystem who develops automation using these APIs

## User Stories

- As a user, I want to consume SR-IOV network devices via DRA in my VMs
- As a user, I want to specify custom MAC addresses for DRA-provisioned SR-IOV interfaces
- As an admin, I want to use DRA drivers to manage SR-IOV device allocation with fine-grained control
- As a developer, I want extensible APIs to build automation for DRA-based networking

## Use Cases

### Supported Use Cases

1. SR-IOV network devices where the DRA driver publishes required attributes in device metadata files:
   - `resources.kubernetes.io/pciBusID` for SR-IOV VF passthrough

### Future Use Cases
1. Scalable Functions network devices
2. Live migration of VMIs using DRA network devices (will have a VEP amendment)

## Repos

kubevirt/kubevirt

## Design

This design introduces a new feature gate: `NetworkDevicesWithDRA`. 
All the API changes will be gated behind this feature gate so as not to break existing functionality.

### API Changes

A new network source type `ResourceClaimNetworkSource` is added to the existing `NetworkSource` type:

```go
// Represents the source resource that will be connected to the vm.
// Only one of its members may be specified.
type NetworkSource struct {
	Pod           *PodNetwork                 `json:"pod,omitempty"`
	Multus        *MultusNetwork              `json:"multus,omitempty"`
	ResourceClaim *ResourceClaimNetworkSource `json:"resourceClaim,omitempty"`
}

// ResourceClaimNetworkSource represents a network resource requested
// via a Kubernetes ResourceClaim.
type ResourceClaimNetworkSource struct {
	// ClaimName references the name of an entry in the
	// VMI's spec.resourceClaims[] array.
	// +kubebuilder:validation:MinLength=1
	ClaimName string `json:"claimName"`

	// RequestName specifies which request from the
	// ResourceClaim.spec.devices.requests array this network
	// source corresponds to.
	// +kubebuilder:validation:MinLength=1
	RequestName string `json:"requestName"`
}
```

The VMI must also include the resource claim in `spec.resourceClaims[]` (consistent with GPU and HostDevice DRA usage).

### Status Reporting

For consistency with GPUs and HostDevices, DRA-provisioned network devices populate the same `vmi.status.deviceStatus.hostDeviceStatuses[]` array. The DRA controller in virt-controller:

1. Identifies networks with `resourceClaim` source type
2. Extracts device information from the allocated ResourceClaim and ResourceSlice
3. Populates `hostDeviceStatuses` with network name and allocated device attributes (PCI address)

The status entry name matches the network name from `spec.networks[].name`, allowing virt-launcher to correlate the network configuration with its allocated DRA device.

The detailed mechanism for extracting device information from Pod status, ResourceClaim, and ResourceSlice follows the same approach described in VEP #10.

### SR-IOV Integration

When a network interface has `sriov` binding and references a network with `resourceClaim` source:

1. The network admitter validates that exactly one network source type (pod, multus, or resourceClaim) is specified
2. Virt-controller adds the resource claim to the virt-launcher pod spec via `WithNetworksDRA()` render option
3. The DRA controller populates `vmi.status.deviceStatus` with the PCI address from the ResourceSlice
4. Virt-launcher reads the PCI address from device status and generates the appropriate libvirt hostdev XML (at [`generateConverterContext`](https://github.com/kubevirt/kubevirt/blob/ffa91c8156fecf1d91dd865c6197865a0a3e525b/pkg/virt-launcher/virtwrap/manager.go#L1163), alongside the existing `sriov.CreateHostDevices` call), identical to traditional Multus-based SR-IOV

This approach provides clean separation: DRA handles device provisioning, KubeVirt networks API handles configuration.

**Important:** Traditional Multus-based SR-IOV (using `multus` network source) and DRA-based SR-IOV (using `resourceClaim` network source) are **mutually exclusive per VM**. A single VMI should not mix both approaches. The existing Multus-based SR-IOV API remains fully supported and unchanged.

### Custom MAC Address Support

To support custom MAC addresses for DRA-based SR-IOV networks, KubeVirt will annotate the virt-launcher pod with requested MAC addresses. The MAC address will be taken from the existing `spec.domain.devices.interfaces[].macAddress` field:

```
kubevirt.io/dra-networks: '[{"claimName":"sriov","requestName":"vf","mac":"de:ad:00:00:be:ef"}]'
```

This preserves the structure of `k8s.v1.cni.cncf.io/networks`, but for claimName/requestName instead of NAD.

The SR-IOV DRA driver reads this annotation and passes the claim/request identifier along with the MAC address to the SR-IOV CNI, ensuring the network interface is configured with the specified MAC address.

**Design Rationale:** The annotation-based approach was chosen because it solves the case where ResourceClaim/ResourceClaimTemplate is created by the admin (not by KubeVirt). Since this approach handles the more complex admin-created claim scenario, it naturally also works for the general case where KubeVirt creates the claims ("auto" mode), providing a unified solution for both scenarios.

### Validation

Webhook validations ensure:
1. Networks with `resourceClaim` source have corresponding `sriov` binding interfaces
2. Each network must reference a unique `claimName` + `requestName` combination. No two DRA entities (networks, hostDevices, or GPUs) can share the same tuple, as each interface+network pair must map to exactly one device allocation
3. No mixing of Multus-based and DRA-based SR-IOV in the same VMI.

### Component Changes

**Virt-Controller:**
- Renders virt-launcher pod spec with resource claims from `vmi.spec.resourceClaims[]` referenced by `vmi.spec.networks[].resourceClaim`
- Annotates virt-launcher pod with `kubevirt.io/dra-networks` containing MAC addresses from `spec.domain.devices.interfaces[].macAddress`

**Virt-Launcher:**
- For SR-IOV networks with DRA, virt-launcher uses `vmi.status.deviceStatus` to generate the domain XML instead of Kubevirt's downwardAPI file as in the case of device-plugins
- The `CreateDRAHostDevices()` function generates hostdev XML by:
  - Filtering VMI spec interfaces with SRIOV binding that reference networks with resourceClaim source
  - Looking up the corresponding VMI status device status entry by network name
  - Extracting the PCI address from VMI status device status attributes
  - Generating standard libvirt hostdev XML

- **Note:** If the ResourceClaim/ResourceClaimTemplate is allocating more than one device for the request, KubeVirt will consume the first device from the allocated devices

## API Examples

### VMI with DRA SR-IOV Network

```yaml
---
apiVersion: resource.k8s.io/v1
kind: DeviceClass
metadata:
  name: sriov.network.example.com
spec:
  selectors:
  - cel:
      expression: device.driver == 'sriov.network.example.com'
---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: sriov-network-claim-template
  namespace: default
spec:
  spec:
    devices:
      requests:
      - name: sriov-nic-request
        exactly:
          deviceClassName: sriov.network.example.com
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-sriov-dra
  namespace: default
spec:
  domain:
    devices:
      interfaces:
      - name: sriov-net
        sriov: {}
        macAddress: "de:ad:00:00:be:ef"
  networks:
  - name: sriov-net
    resourceClaim:
      claimName: sriov-network-claim
      requestName: sriov-nic-request
  resourceClaims:
  - name: sriov-network-claim
    resourceClaimTemplateName: sriov-network-claim-template
status:
  deviceStatus:
    hostDeviceStatuses:
    - name: sriov-net
      deviceResourceClaimStatus:
        name: 0000-05-00-1
        resourceClaimName: virt-launcher-vmi-sriov-dra-sriov-network-claim-abc123
        attributes:
          pciAddress: 0000:05:00.1
---
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-sriov-dra
  namespace: default
  annotations:
    kubevirt.io/dra-networks: '[{"claimName":"sriov-network-claim","requestName":"sriov-nic-request","mac":"de:ad:00:00:be:ef"}]'
spec:
  containers:
  - name: compute
    image: virt-launcher
    resources:
      claims:
      - name: sriov-network-claim
        request: sriov-nic-request
  resourceClaims:
  - name: sriov-network-claim
    resourceClaimTemplateName: sriov-network-claim-template
status:
  resourceClaimStatuses:
  - name: sriov-network-claim
    resourceClaimName: virt-launcher-vmi-sriov-dra-sriov-network-claim-abc123
```

## Scalability

The DRA controller in virt-controller uses existing shared informers (no additional watch calls) and filters events to relevant status sections. See [VEP #10](../../sig-compute/10-dra-devices/vep.md#scalability) for detailed scalability analysis.

## Update/Rollback Compatibility

- Changes are upgrade compatible
- Rollback works as long as feature gate is disabled
- If the feature is enabled, VMIs using DRA network devices must be deleted and feature gate disabled before attempting rollback

## Functional Testing Approach

- Unit tests with optimum coverage for new code
- New e2e test lane with all current SR-IOV tests using the new API
(excluding migration tests, which will be added when migration is supported)

## Implementation History

- 2026-01-20: Initial design/VEP proposal for SR-IOV Network DRA support

## Graduation Requirements

### Alpha

- Code changes behind `NetworkDevicesWithDRA` feature gate
- Unit tests
- E2E tests with SR-IOV DRA driver (excluding migration)

### Beta

- Evaluate user and driver author experience
- Consider additional use cases if any
- Work with Kubernetes community on standardizing device information injection
- Live migration support for DRA network devices
  - Live migration will use CDI/NRI to inject device information as files into each pod (mappings of request/claim to PCI addresses)
  - Each virt-launcher reads its pod-specific device file, avoiding conflicts in VMI status
  - Might be initially implemented by SR-IOV DRA driver; future Kubernetes support may generalize this (see [kubernetes/enhancements#5606](https://github.com/kubernetes/enhancements/pull/5606))
  - Details: https://github.com/k8snetworkplumbingwg/dra-driver-sriov/pull/62

### GA

- Upgrade/downgrade testing

## References

- DRA: https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
- SR-IOV DRA driver: https://github.com/k8snetworkplumbingwg/dra-driver-sriov
- VEP #10 (DRA devices): /veps/sig-compute/10-dra-devices/vep.md
- Kubernetes DRA device information injection: https://github.com/kubernetes/enhancements/pull/5606
