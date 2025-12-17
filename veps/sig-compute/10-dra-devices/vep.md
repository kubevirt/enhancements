# VEP #10: Support DRA devices in KubeVirt

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

This proposal is about adding support for DRA (dynamic resource allocation) in KubeVirt.
DRA allows vendors fine-grained control of devices. The device-plugin model will continue
to exist in Kubernetes, but DRA will offer vendors more control over the device topology.

## Motivation

DRA adoption is important for KubeVirt so that vendors can expect the same
control of their devices using Virtual Machines and Containers.

## Goals

- Align on the API changes needed to consume DRA enabled devices in KubeVirt
- Align on how KubeVirt will consume devices by external DRA drivers

## Non Goals

- Replace device-plugin support in KubeVirt
- Align on what drivers KubeVirt will support in tree
- Address any complexity issues when DRA APIs in KubeVirt

## Definition of Users

- A user is a person that wants to attach a device to a VM
- An admin is a person who manages the infrastructure and decide what kind of devices will be exposed via DRA
- A developer is a person familiar with CNCF ecosystem that can develop automation using the APIs discussed here

## User Stories

- As a user, I would like to use my GPU dra driver with KubeVirt.
- As a user, I would like to use KubeVirt's default driver.
- As a user, in heterogeneous clusters, i.e. clusters made of nodes with different hardware managed through DRA drivers,
  I should be able to easily identify what hardware was allocated to the VMI.
- As a developer, I would like APIs to be extensible so I can develop drivers/webhooks/automation for custom use-cases.
- As a device-plugin author, I would like to have a well documented way, intuitive way to support devices in KubeVirt.

## Use Cases

### Supported Usecases

1. GPU Passthrough where the DRA driver publishes required attributes in device metadata files:
   - `resources.kubernetes.io/pciBusID` for passthrough GPU (e.g., "0000:65:00.0")
2. DRA driver must mount metadata files at `/var/run/dra-device-attributes/{claimNamespace}-{claimName}.json`

### Future Usecases
1. NVMe devices https://github.com/kubevirt/community/issues/254
1. Live migration of VMIs using DRA devices

### Unsupported Usecases

1. Devices where the DRA driver does not set the attributes needed to configure libvirt dom XML for the devices will not
   be supported.
    1. In the future a standardization could be envisioned for a PCI device where the attributes are set
       automatically through the DRA framework. When this is achieved, any DRA device should be available in KubeVirt VMs
2. NetworkDevices or Storage Devices which need a lifecycle of its own will not be supported through HostDevices in alpha
   release.
   - For storage, NVMe devices may need to persist beyond pod deletion (e.g., during VM pause). However, current K8s 
     DRA implementation deletes resource claims when the virt-launcher pod is deleted. Managing claims beyond pod 
     lifecycle (for example VM pause case) is out of scope for this design. 
   - For network devices, there is already existing mechanism of configuring the network, any DRA network device will have
     to work existing functionality. There is a need to explore ideas on how to best achieve it potential in sig-network.
     Hence, it is out of scope, will have to be picked up as a separate VEP

## Repos

kubevirt/kubevirt

## Design

For allowing users to consume DRA devices, there are two main changes needed:

1. API changes and the plumbing required in KubeVirt to generate the domain xml with the devices.
2. Driver Implementation to set the required attributes for KubeVirt to use

This design document focuses on part 1 of the problem. This design is introducing two new feature gates:We're introducing a new feature gate `DRADevices`.
1. GPUsWithDRA
2. HostDevicesWithDRA
All the API changes will be gated behind either one of this feature gates so as not to break existing functionality.

Both the GPUs as well as HostDevices have separate lifecycle but essentially have the same API, hence two separate
feature gates are required.

### API Changes

```go
type VirtualMachineInstanceSpec struct {
	..
	..
	// ResourceClaims defines which ResourceClaims must be allocated
	// and reserved before the VMI and hence virt-launcher pod is allowed to start. The resources
	// will be made available to the domain which consume them
	// by name.
	//
	// This is an alpha field and requires enabling the
	// DynamicResourceAllocation feature gate in kubernetes
	//  https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
	//
	// This field is immutable.
	//
	// +listType=map
	// +listMapKey=name
	// +optional
	ResourceClaims []k8sv1.PodResourceClaim `json:"resourceClaims,omitempty"`
}

type GPU struct {
	// Name of the GPU device as exposed by a device plugin
	Name string `json:"name"`
	// DeviceSource is the name of the device provisioned either by device plugins
	// or by DRA enabled device
	// DeviceName string `json:"deviceName"`    <-- inlined into DeviceSource
	DeviceSource      DeviceSource `json:",inline"`
	VirtualGPUOptions *VGPUOptions `json:"virtualGPUOptions,omitempty"`
	// If specified, the virtual network interface address and its tag will be provided to the guest via config drive
	// +optional
	Tag string `json:"tag,omitempty"`
}

type HostDevice struct {
	Name string               `json:"name"`
	// DeviceSource is the name of the device provisioned either by device plugins
	// or by DRA enabled device
	// DeviceName string `json:"deviceName"`    <-- inlined into DeviceSource
	DeviceSource DeviceSource `json:",inline"`
	// If specified, the virtual network interface address and its tag will be provided to the guest via config drive
	// +optional
	Tag string `json:"tag,omitempty"`
}

type DeviceSource struct {
	// DeviceName is the name of the device provisioned by device-plugins
	DeviceName *string `json:"deviceName,omitempty"`
	// ClaimRequest provides the ClaimName from vmi.spec.resourceClaims[].name and
	// DeviceRequestName from resourceClaim.spec.devices.requests[].name
	// this fields requires DRA feature gate enabled
	ClaimRequest *ClaimRequest `json:",inline,omitempty"`
}

type ClaimRequest struct {
	// ClaimName needs to be provided from the list vmi.spec.resourceClaims[].name where this
	// device is allocated
	ClaimName string `json:"claimName"`
	// DeviceRequestName needs to be provided from resourceClaim.spec.devices.requests[].name where this
	// device is requested
	DeviceRequestName string `json:"deviceRequestName"`
}
```

Note: VMI status does not include device attributes as in the alpha version. Virt-launcher reads device metadata directly from files mounted
by the DRA driver at `/var/run/dra-device-attributes/`. This avoids the need for virt-controller to watch and reconcile
DRA objects (ResourceClaims, ResourceSlices) to populate VMI status.

The first section vmi.spec.resourceClaims will have a list of devices needed to be allocated for the VM. Having this
available as a list will allow users to use the device from this list in GPU section or HostDevices section of the
DomainSpec API.

In v1beta1 version of [DRA API](https://pkg.go.dev/k8s.io/api@v0.32.0/resource/v1beta1#DeviceClaim), multiple drivers
could potentially provision devices that are part of a single claim. For this reason, a separate list of claims required
for the VMI (section 1) is needed instead of mentioning the resource claim in devices section
[see Alternate Designs](#alternative-1)

The second section allows for the resource claim to be used in the spec.domain.devices section. The two use cases
currently handled by the design are:

1. allowing the devices to be used as a gpu device (spec.domain.devices.gpu)
2. allowing the devices to be used as a host device (spec.domain.device.hostDevices)

Taking a GPU as an example, we can either have a passthrough-GPU as a PCI device or a virtual-GPU as a mediated device.
The DRA driver publishes device attributes (e.g., `resource.kubernetes.io/pciBusID` for passthrough, `mdevUUID` for
mediated devices) in the metadata file mounted into the virt-launcher pod.

The virt-launcher will have the logic of converting a GPU device into its corresponding domain xml. For device-plugins, it
will continue to look for env variables (current approach). For DRA devices, it reads the metadata file from the
well-known path `/var/run/dra-device-attributes/` to get device attributes and generate the domain xml.

### Device Metadata via Downward API

DRA drivers expose device attributes to workloads by mounting metadata files into pods at well-known locations. This
allows virt-launcher to read device information directly from the filesystem without requiring virt-controller to watch
and reconcile ResourceClaim/ResourceSlice objects.

#### File Location and Format

Device metadata is available at a well-known path inside the virt-launcher pod:

```
/var/run/dra-device-attributes/{claimNamespace}-{claimName}.json
```

For example, for a GPU claim named `pgpu-claim` in namespace `gpu-test1`:

```
/var/run/dra-device-attributes/gpu-test1-pgpu-claim.json
```

#### Metadata File Format

The metadata file contains a JSON structure with device attributes. The exact format may evolve, but the key information
virt-launcher needs is available under `bestEffortData.attributes`. Example:

```json
{
  "requests": [
    {
      "name": "pgpu-request",
      "devices": [
        {
          "name": "pgpu-0",
          "driver": "gpu.example.com",
          "pool": "node-1-gpus",
          "bestEffortData": {
            "attributes": {
              "resources.kubernetes.io/pciBusID": "0000:65:00.0",
              "model": "A100",
              "memory": "80Gi"
            }
          }
        }
      ]
    }
  ]
}
```

#### Required Attributes for KubeVirt

DRA drivers that want to support KubeVirt must publish the following attributes:

| Device Type | Required Attribute | Description |
|-------------|-------------------|-------------|
| Passthrough GPU | `resources.kubernetes.io/pciBusID` | PCIe bus address (e.g., "0000:65:00.0") |

Virt-launcher will read the metadata file and extract the appropriate attribute to generate the libvirt domain XML.

### Webhook changes

1. Allow the VMI only if vmi spec is correct, i.e. resource claims for requested devices are specified

Note: The feature gate verification when VMI requesting DRA device will either be done in webhook or controller, TBD


### Virt controller changes

1. If devices are requested using DRA, virt controller needs to render the virt-launcher manifest such that
   `pod.spec.resourceClaims` and `pod.spec.containers.resources.claim` sections are filled out.
2. Virt-controller does NOT need to watch ResourceClaims or ResourceSlices. Device attributes are made available to
   virt-launcher via the downward API (metadata files mounted by the DRA driver).

### Virt launcher changes

1. For devices allocated using DRA, virt-launcher reads device attributes from metadata files mounted at the well-known
   path `/var/run/dra-device-attributes/`. This replaces the need to read from VMI status or environment variables.
2. Virt-launcher parses the JSON metadata file to extract the required attributes:
   - For passthrough GPU: `bestEffortData.attributes.resources.kubernetes.io/pciBusID`
3. Using the extracted attributes, virt-launcher generates the appropriate libvirt domain XML for GPU passthrough.
4. For device-plugins (non-DRA), virt-launcher continues to use environment variables (`PCI_RESOURCE_<deviceName>`)
   as in the current implementation.

## API Examples

### VM API with PassThrough GPU

```
---
# this is a cluster scoped resource
apiVersion: resource.k8s.io/v1alpha3
kind: DeviceClass
metadata:
  name: gpu.example.com
spec:
  selectors:
  - cel:
      expression: device.driver == 'gpu.example.com'
---
apiVersion: resource.k8s.io/v1alpha3
kind: ResourceClaimTemplate
metadata:
  name: pgpu-claim-template
spec:
  spec:
    devices:
      requests:
        - name: pgpu-request-name
          deviceClassName: gpu.example.com
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  labels:
    kubevirt.io/vm: vm-cirros
  name: vm-cirros
spec:
  resourceClaims:
  - name: pgpu-claim-name
    resourceClaimTemplateName: pgpu-claim-template
  domain:
    devices:
      gpus:
      - name: pgpu
        claimName: pgpu-claim-name
        deviceRequestName: pgpu-request-name
---
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-cirros
spec:
  containers:
  - name: virt-launcher
    image: virt-launcher
    resources:
      claims:
      - name: pgpu-claim-name
        request: pgpu-request-name
  resourceClaims:
  - name: pgpu-claim-name
    source:
      resourceClaimTemplateName: pgpu-claim-template
status:
  resourceClaimStatuses:
  - name: gpu-resource-claim
    resourceClaimName: virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-m4k28
```

## Comparing DRA APIs with Device Plugins

In the case of device plugins, a pre-defined status resource which is usually identified by a device model, e.g.
`nvidia.com/GP102GL_Tesla_P40` is configured. Users consume this device via the following spec:
```yaml
apiVersion: kubevirt.io/v1alpha3
kind: VirtualMachineInstance
metadata:
  labels:
    special: vmi-gpu
  name: vmi-gpu
spec:
  domain:
    devices:
      gpus:
      - deviceName: nvidia.com/GP102GL_Tesla_P40
        name: pgpu
```
In the case of DRA there is a level of indirection, where the information about what device is allocated to the VMI
could be lost in the resource claim object. For example, consider a ResourceClaimTemplate:
```yaml
apiVersion: resource.k8s.io/v1alpha3
kind: ResourceClaimTemplate
metadata:
  name: single-gpu
  namespace: gpu-test1
spec:
  spec:
    devices:
      requests:
      - allocationMode: ExactCount
        count: 1
        deviceClassName: vfiopci.nvidia.com
        name: gpu
---
apiVersion: resource.k8s.io/v1alpha3
kind: DeviceClass
metadata:
  name: vfiopci.example.com
spec:
  config:
  - opaque:
      driver: gpu.nvidia.com
      parameters:
        apiVersion: gpu.nvidia.com/v1alpha1
        driverConfig:
         driver: vfio-pci
        kind: GpuConfig
  selectors:
  - cel:
      expression: device.driver == 'gpu.nvidia.com' && device.attributes['gpu.nvidia.com'].type == 'gpu'
```
If the above driver is deployed in a cluster with three nodes with two different GPUs, say `RTX 4080` and `RTX 3080`.

The user consumes the GPU using the following spec:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-fedora
  namespace: gpu-test1
spec:
  resourceClaims:
  - name: gpu-resource-claim
    resourceClaimTemplateName: single-gpu
  domain:
    gpus:
    - claimName: gpu-resource-claim
      deviceRequestName: gpu
      name: example-pgpu
```

The user will then wait for devices to be allocated. Once the virt-launcher pod is running, it will read the device
metadata from the mounted JSON file at `/var/run/dra-device-attributes/` and use the attributes (e.g.,
`resources.kubernetes.io/pciBusID`) to generate the domain XML.

## Alternatives

### Alternative 1
```
type GPU struct {
	// Name of the GPU device as exposed by a device plugin
	Name              string       `json:"name"`
	DeviceName        string       `json:"deviceName"`
	VirtualGPUOptions *VGPUOptions `json:"virtualGPUOptions,omitempty"`
	// If specified, the virtual network interface address and its tag will be provided to the guest via config drive
	// +optional
	Tag   string `json:"tag,omitempty"`
	Claim string `json:"claim,omitempty"`
}

type HostDevice struct {
	Name string `json:"name"`
	// DeviceName is the resource name of the host device exposed by a device plugin
	DeviceName string `json:"deviceName"`
	// If specified, the virtual network interface address and its tag will be provided to the guest via config drive
	// +optional
	Tag string `json:"tag,omitempty"`
	// If specified, the ResourceName of the host device will be provisioned using DRA driver . which will not require the deviceName field
	//+optional
	ResourceClaim *k8sv1.ResourceClaim `json:"resourceClaim,omitempty"`
}

// ResourceClaim represents a resource claim to be used by the virtual machine
type ResourceClaim struct {
	// Name is the name of the resource claim
	Name string `json:"name"`
	// Source represents the source of the resource claim
	Source ResourceClaimSource `json:"source"`
}

// ResourceClaimSource represents the source of a resource claim
type ResourceClaimSource struct {
	// ResourceClaimName is the name of the resource claim
	ResourceClaimName string `json:"resourceClaimName"`
	// ResourceClaimTemplateName is the name of the resource claim template
	//
	// Exactly one of ResourceClaimName and ResourceClaimTemplateName must
	// be set.
	ResourceClaimTemplateName string `json:"resourceClaimTemplateName"`
}
```
This design misses the use-case where more than one DRA device is specified in the claim template, as each
device will have its own template in the API.
This design also assumes that the deviceName will be provided in the ClaimParameters, which requires the DRA drivers
to have a ClaimParameters.spec.deviceName in their spec.

## Alternative 2
Asking the dra plugin authors to inject env variable to CDI spec.
In order to uniquely identify the device required by the vmi spec, the follow env variable will have to be constructed:
```
PCI_RESOURCE_<RESOURCE-CLAIM-NAME>_<REQUEST-NAME>="0000:01:00.0"
```
Where the RESOURCE-CLAIM-NAME is the name of the ResourceClaim k8s object created either from ResourceClaimTemplate, or
directly by the user. The REQUEST-NAME is the name of the request available in `vmi.spec.domain.devices.gpu/hostdevices.claims[*].request`
In the case of MDEV devices it will be:
```
MDEV_PCI_RESOURCE_<RESOURCE-CLAIM-NAME>_<REQUEST-NAME>="uuid"
```
For this approach the following static fields are required in VMI
```go
type VirtualMachineInstanceStatus struct {
    ..
    ..
	// ResourceClaimStatuses reflects the state of devices resourceClaims defined in virt-launcher pod.
	// This is an optional field available only when DRA feature gate is enabled
	// +optional
	ResourceClaimStatuses []PodResourceClaimStatus `json:"resourceClaimStatuses,omitempty"`
}
type PodResourceClaimStatus struct {
    // Name uniquely identifies this resource claim inside the pod.
    // This must match the name of an entry in pod.spec.resourceClaims,
    // which implies that the string must be a DNS_LABEL.
    Name string `json:"name" protobuf:"bytes,1,name=name"`
    // ResourceClaimName is the name of the ResourceClaim that was
    // generated for the Pod in the namespace of the Pod. If this is
    // unset, then generating a ResourceClaim was not necessary. The
    // pod.spec.resourceClaims entry can be ignored in this case.
    //
    // +optional
    ResourceClaimName *string `json:"resourceClaimName,omitempty" protobuf:"bytes,2,opt,name=resourceClaimName"`
}
```

virt-launcher will use the `vmistatus.resourClaimStatuses[*].ResourceClaimName` and `vmi.spec.domain.devices.gpu/hostdevices.claims[*].request`
to look up the env variable: `PCI_RESOURCE_<RESOURCE-CLAIM-NAME>_<REQUEST-NAME>` or
`MDEV_PCI_RESOURCE_<RESOURCE-CLAIM-NAME>_<REQUEST-NAME>="uuid"` and generate the correct domain xml.

## Handling Future usecase of Live Migration

For the purposes of this document, live migrating a VMI with DRA devices is currently out-of-scope as it is not currently
supported in KubeVirt. However, the downward API approach simplifies future live migration support:

1. The target virt-launcher pod will have its own DRA device allocations with corresponding metadata files mounted.
2. The target virt-launcher reads device attributes directly from its mounted metadata files at
   `/var/run/dra-device-attributes/`.
3. No additional VMI status fields or virt-controller logic is required for the target pod to discover its allocated
   devices.

This design eliminates the need for complex coordination between virt-controller and virt-launcher during migration,
as each virt-launcher pod independently reads its own device metadata.


## Scalability

1. Virt-controller does NOT watch ResourceClaims or ResourceSlices, avoiding additional API server load.
2. Device attributes are read locally by virt-launcher from mounted metadata files, requiring no additional API calls.
3. This approach scales well as the device metadata lookup is a local filesystem read operation.

## Documentation

The following documentation will be provided for beta:

### User Guide: Consuming DRA Devices in KubeVirt VMs

- Prerequisites (Kubernetes version, DRA feature gates, CRI runtime requirements)
- Creating DeviceClass resources
- Creating ResourceClaimTemplates
- Configuring VirtualMachineInstance with `spec.resourceClaims`
- Mapping claims to GPU specifications

### Driver Author Guide: Making DRA Drivers KubeVirt-Compatible

DRA driver authors who want their drivers to work with KubeVirt must:

1. Mount device metadata files at the well-known path:
   ```
   /var/run/dra-device-attributes/{claimNamespace}-{claimName}.json
   ```

2. Publish the following attributes in the metadata file:

   | Device Type | Required Attribute | Example Value |
   |-------------|-------------------|---------------|
   | Passthrough GPU | `resources.kubernetes.io/pciBusID` | `0000:65:00.0` |

3. Follow the JSON structure with `bestEffortData.attributes` containing the required attributes.

### Troubleshooting Guide

- How to verify metadata files are mounted in virt-launcher
- Common issues (missing attributes, file not found)
- Debug commands for inspecting mounted files

## Update/Rollback Compatibility

- The changes in this design are upgrade compatible
- Rollback will continue to work as long as feature gate is disabled
- If the feature is enabled, the VMIs that use DRA devices will have to be deleted and feature gate disabled before 
  attempting rollback.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

### Unit Tests

New code added will have optimum unit test coverage including:
- Metadata file parsing logic
- Attribute extraction from JSON structure
- Domain XML generation with DRA device attributes

### E2E Tests

#### Alpha E2E Tests (Mock)
- API fields are only populated if feature gate is enabled
- Once a VMI is created with DRA devices, the right spec values are set by virt-controller in virt-launcher pod
- Deletion of VMI will lead to release of resources

#### Beta E2E Tests

For beta, real driver integration is required. Either of the following drivers will be used:

- **dra-example-driver**: https://github.com/kubernetes-sigs/dra-example-driver
- **NVIDIA DRA driver**: https://github.com/NVIDIA/k8s-dra-driver

## Implementation Phases

### Alpha
1. API changes
2. Webhook verification
3. virt-controller changes for virt-launcher pod spec (resourceClaims)
4. virt-launcher changes to read device information from metadata files
5. Unit tests
6. Mock e2e tests

### Beta
7. Integrate dra-example-driver or NVIDIA DRA driver for e2e testing
8. Create user documentation
9. Create driver author documentation

### GA
12. Upgrade/downgrade tests
13. Scale tests

## Feature lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- Code changes behind feature gate
- unit tests
- mock e2e test

Note: there are multiple challenges in writing a real e2e test like, developing a driver implementing kubevirt devices,
bringing in the driver in kubevirt CI. Before vendors get a chance to develop a driver, there needs to be an API that 
they can develop against which will not be available until the alpha release. For this reason, writing an e2e test with 
real driver is outside the scope of alpha and will be handled in beta.

### Beta

**Design Changes:**
- Use device metadata files instead of ResourceSlice lookups
- Virt-launcher reads attributes directly from `/var/run/dra-device-attributes/`
- Remove virt-controller dependency on ResourceClaim/ResourceSlice watching

**Documentation:**
- User guide for consuming DRA devices in KubeVirt VMs
- Driver author guide with required attributes and file format
- Troubleshooting guide

**Testing:**
- E2E tests with dra-example-driver or NVIDIA DRA driver

**Feature Gate:**
- Feature gate turned on by default

**Evaluation:**
- Evaluate user and driver authors experience
- Consider additional usecases if any

### GA
- upgrade/downgrade testing 

# References

- Structured parameters
  https://github.com/kubernetes/kubernetes/pull/123516
- Structured parameters KEP
  https://github.com/kubernetes/enhancements/issues/4381
- DRA
  https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
- NVIDIA DRA driver
  https://github.com/NVIDIA/k8s-dra-driver