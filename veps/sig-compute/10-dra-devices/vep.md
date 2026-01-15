# VEP #10: Support GPUs with DRA in KubeVirt

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

This proposal is about adding support for GPUs via DRA (Dynamic Resource Allocation) in KubeVirt.
DRA allows GPU vendors fine-grained control over GPU allocation and topology. The device-plugin
model will continue to exist in Kubernetes, but DRA offers vendors more control over GPU scheduling
and configuration.

This VEP establishes the core DRA infrastructure in KubeVirt that will also be used by future VEPs
for other device types (HostDevices, NetworkDevices, CPUs).

## Motivation

DRA adoption for GPUs is important for KubeVirt so that GPU vendors can expect the same
level of control when using Virtual Machines as they have with Containers.

## Goals

- Align on the API changes needed to consume DRA-enabled GPUs in KubeVirt
- Align on how KubeVirt will consume GPUs via external DRA drivers
- Establish the core DRA infrastructure (API patterns, metadata file reading) that future device VEPs will build upon
- Enable all DRA GPU use cases available to containers to work seamlessly with KubeVirt VMIs

## Non Goals

- Replace device-plugin support in KubeVirt
- Align on what GPU drivers KubeVirt will support in tree
- Address any complexity issues with DRA APIs in KubeVirt
- Support for HostDevices via DRA (separate VEP: HostDevicesWithDRA)
- Support for NetworkDevices via DRA (separate VEP: NetworkDevicesWithDRA)
- Support for CPUs via DRA (separate VEP: CPUsWithDRA)

## Definition of Users

- A user is a person that wants to attach a GPU to a VM
- An admin is a person who manages the infrastructure and decides what GPUs will be exposed via DRA
- A developer is a person familiar with the CNCF ecosystem that can develop automation using the APIs discussed here

## User Stories

- As a user, I would like to use my GPU DRA driver with KubeVirt.
- As a user, in heterogeneous clusters (i.e., clusters with different GPU models managed through DRA drivers),
  I should be able to easily identify what GPU was allocated to the VMI.
- As a developer, I would like APIs to be extensible so I can develop drivers/webhooks/automation for custom GPU use-cases.
- As a GPU driver author, I would like to have a well-documented, intuitive way to support GPUs in KubeVirt VMs.

## Use Cases

### Supported Usecases

1. GPU devices where the DRA driver publishes required attributes in device metadata files:
   - `resources.kubernetes.io/pciBusID` for passthrough GPU 
   - `mDevUUID` for mediated devices/vGPU (currently hardcoded in KubeVirt, will be standardized when [kubernetes/kubernetes#135552](https://github.com/kubernetes/kubernetes/issues/135552) is resolved)
2. DRA driver must write metadata files via the DRA Attributes Downward API (see [KEP-5304](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api))

### Future Usecases
1. NVMe devices https://github.com/kubevirt/community/issues/254
1. Live migration of VMIs using DRA devices

### Unsupported Usecases

### Unsupported Usecases (in this VEP)

1. GPUs where the DRA driver does not set the required attributes (e.g., `resources.kubernetes.io/pciBusID`, `mDevUUID`) will not
   be supported. Drivers must publish the necessary attributes for KubeVirt to generate the libvirt domain XML.
2. HostDevices via DRA - will be addressed in a separate VEP (HostDevicesWithDRA)
3. NetworkDevices via DRA - will be addressed in a separate VEP (NetworkDevicesWithDRA)
4. CPUs via DRA - will be addressed in a separate VEP (CPUsWithDRA)

## Repos

kubevirt/kubevirt

## Design

For allowing users to consume GPUs via DRA, there are two main changes needed:

1. API changes and the plumbing required in KubeVirt to generate the domain XML with the GPU.
2. Driver implementation to set the required attributes for KubeVirt to use.

This design document focuses on part 1 of the problem. This design introduces a new feature gate: `GPUsWithDRA`.
All the API changes will be gated behind this feature gate so as not to break existing functionality.

> **Note:** This VEP establishes the core DRA infrastructure (API patterns, `ClaimRequest` type, metadata file reading)
> that will be reused by future VEPs for HostDevices, NetworkDevices, and CPUs.

### API Changes

```go
type VirtualMachineInstanceSpec struct {
	..
	..
	// ResourceClaims define which ResourceClaims must be allocated
	// and reserved before the VMI, hence virt-launcher pod is allowed to start. The resources
	// will be made available to the domain which consumes them
	// by name.
	//
	// This field depends on kubernetes feature DynamicResourceAllocation which GA'ed in 1.34
	// https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
	// This field should only be configured if the GPUsWithDRA feature gate is enabled.
	//
	// +listType=map
	// +listMapKey=name
	// +optional
	ResourceClaims []k8sv1.PodResourceClaim `json:"resourceClaims,omitempty"`
}

type GPU struct {
	// Name of the GPU device as exposed by a device plugin
	Name string `json:"name"`
	// DeviceName is the name of the device provisioned by device-plugins
	DeviceName string `json:"deviceName,omitempty"`
	// ClaimRequest provides the ClaimName from vmi.spec.resourceClaims[].name and
	// requestName from resourceClaim.spec.devices.requests[].name
	// This field should only be configured if the GPUsWithDRA feature gate is enabled.
	*ClaimRequest     `json:",inline"`
	VirtualGPUOptions *VGPUOptions `json:"virtualGPUOptions,omitempty"`
	// If specified, the virtual network interface address and its tag will be provided to the guest via config drive
	// +optional
	Tag string `json:"tag,omitempty"`
}

// ClaimRequest is used to reference a specific device request from a ResourceClaim.
// This type is shared infrastructure that will be reused by future device VEPs
// (HostDevicesWithDRA, NetworkDevicesWithDRA, CPUsWithDRA).
type ClaimRequest struct {
	// ClaimName needs to be provided from the list vmi.spec.resourceClaims[].name where this
	// device is allocated
	// +optional
	ClaimName *string `json:"claimName,omitempty"`
	// RequestName needs to be provided from resourceClaim.spec.devices.requests[].name where this
	// device is requested
	// +optional
	RequestName *string `json:"requestName,omitempty"`
}
```

Note: VMI status does not include device attributes. Virt-launcher reads device metadata directly from files mounted
by the DRA driver via the DRA Attributes Downward API (see [KEP-5304](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api)).
This avoids the need for virt-controller to watch and reconcile DRA objects (ResourceClaims, ResourceSlices) to populate VMI status.

The `vmi.spec.resourceClaims` field contains a list of resource claims needed for the VM. Having this
available as a list allows users to reference claims from the GPU section of the DomainSpec API.
Future VEPs will extend this to support HostDevices, NetworkDevices, and CPUs.

In v1 version of [DRA API](https://pkg.go.dev/k8s.io/api@v0.34.0/resource/v1#DeviceClaim), multiple drivers
could potentially provision devices that are part of a single claim. For this reason, a separate list of claims required
for the VMI is needed instead of mentioning the resource claim inline in the devices section
[see Alternate Designs](#alternative-1).

The `ClaimRequest` type allows the resource claim to be referenced in the `spec.domain.devices.gpus` section.

Taking a GPU as an example, we can either have a pGPU as a PCI device or a vGPU as a mediated device.
The DRA driver publishes device attributes (e.g., `resource.kubernetes.io/pciBusID` for passthrough, `mDevUUID` for
mediated devices) in the metadata file mounted into the virt-launcher pod.

The virt-launcher will have the logic of converting a GPU device into its corresponding domain xml. For device-plugins, it
will continue to look for env variables (current approach). For DRA devices, it reads the metadata file from the
DRA Attributes Downward API (defined by KEP-5304) to get device attributes and generate the domain xml.

### Device Metadata via DRA Attributes Downward API

> **Note:** This section describes the expected mechanism based on
> [KEP-5304: DRA Device Attributes Downward API](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api).

KEP-5304 requires drivers to populate device metadata files:
1. DRA drivers opt-in by calling `AttributesJSON(true)` in their framework configuration
2. During `NodePrepareResources`, drivers call the framework's `WriteDeviceMetadata` helper to write attribute files
3. Files are mounted into pods via CDI (Container Device Interface)
4. Workloads (virt-launcher) discover and read files via globbing or by claim name

#### File Location

**Host Path** (where DRA driver writes the file):
```
/var/run/dra-device-attributes/<driver-name>/<claim-name>/metadata.json
```

**Container Path** (where virt-launcher reads the file):
```
/var/run/dra-device-attributes/<driver-name>/<claim-name>/metadata.json
```

The host path is bind-mounted into the container via CDI at the same location.

**Example for a GPU claim:**

| Path Type | Example |
|-----------|---------|
| Host | `/var/run/dra-device-attributes/gpu.example.com/my-gpu-claim/metadata.json` |
| Container | `/var/run/dra-device-attributes/gpu.example.com/my-gpu-claim/metadata.json` |

#### Example: Kubernetes Objects to Metadata File

Given the following Kubernetes objects:

```yaml
# Pod requesting a GPU via DRA
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-fedora-9bjwb
  namespace: gpu-test1
spec:
  containers:
  - name: compute
    resources:
      claims:
      - name: gpu-resource-claim
  resourceClaims:
  - name: gpu-resource-claim
    resourceClaimTemplateName: single-gpu
status:
  resourceClaimStatuses:
  - name: gpu-resource-claim
    resourceClaimName: virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-m4k28
---
# ResourceClaim created from template
apiVersion: resource.k8s.io/v1alpha3
kind: ResourceClaim
metadata:
  annotations:
    resource.kubernetes.io/pod-claim-name: gpu-resource-claim
  generateName: virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-
  name: virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-m4k28
  namespace: gpu-test1
  ownerReferences:
  - apiVersion: v1
    blockOwnerDeletion: true
    controller: true
    kind: Pod
    name: virt-launcher-vmi-fedora-9bjwb
spec:
  devices:
    requests:
    - exactly:
        allocationMode: ExactCount
        count: 1
        deviceClassName: gpu.example.com
      name: gpu
status:
  allocation:
    devices:
      results:
      - device: gpu-2
        driver: gpu.example.com
        pool: dra-example-driver-cluster-worker
        request: gpu
    nodeSelector:
      nodeSelectorTerms:
      - matchFields:
        - key: metadata.name
          operator: In
          values:
          - dra-example-driver-cluster-worker
  reservedFor:
  - name: virt-launcher-vmi-fedora-9bjwb
    resource: pods
    uid: 8ffb7e04-6c4b-4fc7-bbaa-c60d9a1e0eaa
---
# ResourceSlice published by the DRA driver
apiVersion: resource.k8s.io/v1
kind: ResourceSlice
metadata:
  generateName: kind-dra-control-plane-gpu.example.com-
  name: kind-dra-control-plane-gpu.example.com-drr27
  ownerReferences:
  - apiVersion: v1
    controller: true
    kind: Node
    name: kind-dra-control-plane
spec:
  devices:
  - basic:
      attributes:
        driverVersion:
          version: 1.0.0
        index:
          int: 0
        model:
          string: LATEST-GPU-MODEL
        uuid:
          string: gpu-8e942949-f10b-d871-09b0-ee0657e28f90
        resource.kubernetes.io/pciBusID:
          string: "0000:01:00.0"
    name: pgpu-0
  driver: gpu.example.com
  nodeName: kind-1.31-dra-control-plane
  pool:
    generation: 0
    name: kind-1.31-dra-control-plane
    resourceSliceCount: 1
```

The DRA driver generates the following metadata file at:
`/var/run/dra-device-attributes/gpu.example.com/virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-m4k28/metadata.json`

```json
{
  "apiVersion": "resource.k8s.io/v1alpha1",
  "kind": "DeviceMetadata",
  "metadata": {
    "name": "virt-launcher-vmi-fedora-9bjwb-gpu-resource-claim-m4k28",
    "namespace": "gpu-test1",
    "uid": "8ffb7e04-6c4b-4fc7-bbaa-c60d9a1e0eaa"
  },
  "podClaimName": "gpu-resource-claim",
  "requests": [
    {
      "name": "gpu",
      "devices": [
        {
          "driver": "gpu.example.com",
          "pool": "kind-1.31-dra-control-plane",
          "device": "pgpu-0",
          "attributes": {
            "driverVersion": { "string": "1.0.0" },
            "index": { "int": 0 },
            "model": { "string": "LATEST-GPU-MODEL" },
            "uuid": { "string": "gpu-8e942949-f10b-d871-09b0-ee0657e28f90" },
            "resources.kubernetes.io/pciBusID": { "string": "0000:01:00.0" }
          }
        }
      ]
    }
  ]
}
```

> **Note:** The `podClaimName` field is only present for template-generated claims. For pre-existing claims
> (where `resourceClaimName` is specified directly in the pod spec), this field is absent.

#### JSON Schema

```json
{
  "apiVersion": "resource.k8s.io/v1alpha1",
  "kind": "DeviceMetadata",
  "metadata": {
    "name": "my-gpu-claim",
    "namespace": "gpu-test1",
    "uid": "abc-123-def"
  },
  "podClaimName": "pgpu-generated-claim-name-from-template",
  "requests": [
    {
      "name": "pgpu-request",
      "devices": [
        {
          "driver": "gpu.example.com",
          "pool": "node-1",
          "device": "pgpu-0",
          "attributes": {
            "index": { "int": 0 },
            "uuid": { "string": "gpu-18db0e85-..." },
            "model": { "string": "A100" },
            "resources.kubernetes.io/pciBusID": { "string": "0000:65:00.0" }
          }
        }
      ]
    }
  ]
}
```

#### Field Reference

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `metadata.name` | string | Automatic | Actual ResourceClaim object name |
| `metadata.namespace` | string | Automatic | Claim namespace |
| `podClaimName` | string | Automatic | User-defined claim name from `pod.spec.resourceClaims[].name`. Only present for template-generated claims. |
| `requests[].name` | string | Automatic | Request name from ResourceClaim |
| `requests[].devices[].driver` | string | Automatic | Driver name |
| `requests[].devices[].pool` | string | Automatic | Pool name from allocation |
| `requests[].devices[].device` | string | Automatic | Device name within the pool |
| `requests[].devices[].attributes` | map | Automatic | Device attributes from ResourceSlice |

#### Required Attributes for KubeVirt

DRA GPU drivers that want to support KubeVirt must include the following attributes:

| Device Type | Required Attribute | JSON Path | Example |
|-------------|-------------------|-----------|---------|
| Passthrough GPU | `resources.kubernetes.io/pciBusID` | `requests[].devices[].attributes["resources.kubernetes.io/pciBusID"].stringValue` | `0000:65:00.0` |
| Mediated Device (vGPU) | `mDevUUID` | `requests[].devices[].attributes["mdevUUID"].stringValue` | `aa618089-8b16-4d01-a136-25a0f3c73123` |

> **Note:** `mDevUUID` is not yet standardized under `resources.kubernetes.io`. See
> [kubernetes/kubernetes#135552](https://github.com/kubernetes/kubernetes/issues/135552) for standardization discussion.
> The attribute name may change once standardization is complete.

#### Virt-Launcher Parsing

When a VMI references a DRA device via `gpu.claimName`, virt-launcher must resolve this user-defined name
to the actual ResourceClaim to locate the correct metadata file. The resolution differs based on claim type:

- **Template-generated claims** (`resourceClaimTemplateName`): The metadata file includes a `podClaimName` field
  containing the user-defined name.
- **Pre-existing claims** (`resourceClaimName`): The user-defined name in `vmi.spec.resourceClaims[].name` directly
  maps to the actual claim name specified in `resourceClaimName`.

#### Virt-Launcher Claim Resolution Algorithm

For each GPU entry in `vmi.spec.domain.devices.gpus[]`:

1. Get the user-defined claim name from `gpu.claimName`
2. Find the matching entry in `vmi.spec.resourceClaims[]` by name
3. **If `resourceClaimName` is set** (pre-existing claim):
   - The actual claim name equals `resourceClaimName`
   - Locate metadata file at container path `/var/run/dra-device-attributes/*/<resourceClaimName>/metadata.json`
4. **If `resourceClaimTemplateName` is set** (template-generated claim):
   - Glob all metadata files at container path `/var/run/dra-device-attributes/*/*/metadata.json`
   - Parse each file and match where `podClaimName` equals `gpu.claimName`
5. Within the matched metadata file, find the request where `requests[].name` equals `gpu.requestName`
6. Extract device attributes (`resources.kubernetes.io/pciBusID` or `mdevUUID`)
7. Generate libvirt domain XML using extracted attributes

### Webhook changes

1. Allow the VMI only if vmi spec is correct, i.e. resource claims for requested devices are specified

Note: The feature gate verification when VMI requesting DRA device will either be done in webhook or controller, TBD


### Virt controller changes

1. If devices are requested using DRA, virt controller needs to render the virt-launcher manifest such that
   `pod.spec.resourceClaims` and `pod.spec.containers.resources.claim` sections are filled out.
2. Virt-controller does NOT need to configure any attribute requests in the pod spec - the DRA driver handles
   attribute exposure via framework coordination (KEP-5304).
3. Virt-controller does NOT need to watch ResourceClaims or ResourceSlices. Device attributes are made available to
   virt-launcher via CDI-mounted metadata files written by the DRA driver.

### Virt launcher changes

1. For devices allocated using DRA, virt-launcher reads device attributes from metadata files mounted via CDI
   (as defined by KEP-5304). This replaces the need to read from VMI status or environment variables.
2. Virt-launcher resolves user-defined claim names to actual ResourceClaim names using the algorithm described in
   [Claim Name Resolution](#claim-name-resolution) and [Virt-Launcher Claim Resolution Algorithm](#virt-launcher-claim-resolution-algorithm).
3. Virt-launcher extracts the required attributes:
   - For passthrough GPUs: `resources.kubernetes.io/pciBusID` (e.g., "0000:65:00.0")
   - For mediated devices (vGPU): `mDevUUID` (e.g., "aa618089-8b16-4d01-a136-25a0f3c73123")
4. Using the extracted attributes, virt-launcher generates the appropriate libvirt domain XML:
   - Passthrough GPU: uses PCI address in `<hostdev>` element
   - Mediated device: uses mdev UUID in `<hostdev>` element with `model='vfio-pci'`
5. For device-plugins (non-DRA), virt-launcher continues to use environment variables (`PCI_RESOURCE_<deviceName>`
   or `MDEV_PCI_RESOURCE_<deviceName>`) as in the current implementation.

## API Examples

### VM with Multiple GPUs (Template and Pre-existing Claims)

This example demonstrates a VMI with two GPUs: one from a template-generated claim and one from a pre-existing shared claim.

```yaml
---
# Pre-existing shared GPU claim (can be shared across pods)
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: my-shared-gpu
  namespace: gpu-test
spec:
  devices:
    requests:
    - name: shared-request
      exactly:
        deviceClassName: gpu.example.com
---
# Template for dynamic GPU allocation
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: pgpu-claim-template
  namespace: gpu-test
spec:
  spec:
    devices:
      requests:
      - name: template-request
        exactly:
          deviceClassName: vgpu.example.com
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-multi-gpu
  namespace: gpu-test
spec:
  resourceClaims:
  - name: dynamic-gpu                            # user-defined name
    resourceClaimTemplateName: pgpu-claim-template
  - name: existing-gpu                           # user-defined name
    resourceClaimName: my-shared-gpu             # actual claim name
  domain:
    devices:
      gpus:
      - name: pgpu
        claimName: dynamic-gpu
        requestName: template-request
      - name: vgpu
        claimName: existing-gpu
        requestName: shared-request
```

**Claim Resolution for this example:**

| GPU | `claimName` | Claim Type | Actual Claim Name | Resolution Method |
|-----|-------------|------------|-------------------|-------------------|
| `pgpu` | `dynamic-gpu` | Template | `vmi-multi-gpu-dynamic-gpu-xyz123` | Match via `podClaimName` field in metadata |
| `vgpu` | `existing-gpu` | Pre-existing | `my-shared-gpu` | Direct from `resourceClaimName` |

### VM API with PassThrough GPU

```yaml
---
# this is a cluster scoped resource
apiVersion: resource.k8s.io/v1
kind: DeviceClass
metadata:
  name: gpu.example.com
spec:
  selectors:
  - cel:
      expression: device.driver == 'gpu.example.com'
---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: pgpu-claim-template
spec:
  spec:
    devices:
      requests:
      - name: pgpu-request-name
        exactly:
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
apiVersion: kubevirt.io/v1
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
apiVersion: resource.k8s.io/v1
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
apiVersion: resource.k8s.io/v1
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
      requestName: gpu
      name: example-pgpu
```

The user will then wait for devices to be allocated. Once the virt-launcher pod is running, it will read the device
metadata from the DRA Downward API and use the attributes (e.g.,
`resources.kubernetes.io/pciBusID`) to generate the domain XML.

## Alternatives

### Alternative 1: Inline ResourceClaim in GPU struct

```go
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
```

This design was rejected because it misses the use-case where more than one DRA device is specified in the claim template,
as each device would require its own template reference in the API. The current design with a separate `resourceClaims`
list at the VMI spec level allows multiple GPUs to share a single claim.

### Alternative 2: Environment Variables via CDI

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
directly by the user. The REQUEST-NAME is the name of the request available in `vmi.spec.domain.devices.gpus[*].requestName` or `vmi.spec.domain.devices.hostDevices[*].requestName`
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

This approach was considered but the metadata file approach was chosen instead because:
1. It provides a richer, structured format for device attributes
2. It aligns with the Kubernetes DRA Attributes Downward API direction (KEP-5304)
3. It avoids polluting the container environment with device-specific variables

## Handling Future Usecase of Live Migration

For the purposes of this document, live migrating a VMI with DRA GPUs is currently out-of-scope as it is not currently
supported in KubeVirt. However, the metadata file approach simplifies future live migration support:

1. The target virt-launcher pod will have its own GPU allocations with corresponding metadata files mounted via
   the DRA Downward API.
2. The target virt-launcher reads GPU attributes directly from its mounted metadata files.
3. No additional VMI status fields or virt-controller logic is required for the target pod to discover its allocated
   GPUs.

This design eliminates the need for complex coordination between virt-controller and virt-launcher during migration,
as each virt-launcher pod independently reads its own GPU metadata.


## Scalability

1. Virt-controller does NOT watch ResourceClaims or ResourceSlices, avoiding additional API server load.
2. Device attributes are read locally by virt-launcher from mounted metadata files, requiring no additional API calls.
3. This approach scales well as the device metadata lookup is a local filesystem read operation.

## Documentation

The following documentation will be provided for Alpha2/Beta:

### User Guide: Consuming GPUs via DRA in KubeVirt VMs

- Prerequisites (Kubernetes version, DRA feature gates, CRI runtime requirements)
- Creating GPU DeviceClass resources
- Creating ResourceClaimTemplates for GPUs
- Configuring VirtualMachineInstance with `spec.resourceClaims`
- Mapping claims to GPU specifications in `spec.domain.devices.gpus`

### GPU Driver Author Guide: Making DRA GPU Drivers KubeVirt-Compatible

GPU DRA driver authors who want their drivers to work with KubeVirt must:

1. **Enable metadata exposure** using the framework helper:

   ```go
   helper, err := downwardapihelper.Start(
       ctx,
       driver,
       driverName,
       kubeletOpts,
       downwardapihelper.DeviceMetadataJSON(metadataPath, cdiRoot),
   )
   ```

2. **Publish required attributes** in ResourceSlice device data:

   | Device Type | Required Attribute | Example Value |
   |-------------|-------------------|---------------|
   | Passthrough GPU | `resources.kubernetes.io/pciBusID` | `0000:65:00.0` |
   | Mediated Device (vGPU) | `mDevUUID` (not yet standardized) | `aa618089-8b16-4d01-a136-25a0f3c73123` |

3. **Verify metadata files** are written at:
   ```
   /var/run/dra-device-attributes/<driver-name>/<claim-name>/metadata.json
   ```

Note: Virt-launcher will glob for metadata files at `/var/run/dra-device-attributes/*/` and extract the
`resources.kubernetes.io/pciBusID` attribute. Drivers that don't publish this attribute
in their ResourceSlice will not work with KubeVirt GPU passthrough.

### Troubleshooting Guide

- How to verify GPU metadata files are mounted in virt-launcher
- Common issues (missing attributes, file not found)
- Debug commands for inspecting mounted files

## Update/Rollback Compatibility

- The changes in this design are upgrade compatible
- Rollback will continue to work as long as the `GPUsWithDRA` feature gate is disabled
- If the feature is enabled, VMIs that use DRA GPUs must be deleted and the feature gate disabled before
  attempting rollback.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

### Unit Tests

New code added will have optimum unit test coverage including:
- Metadata file parsing logic
- Attribute extraction from JSON structure
- Domain XML generation with GPU DRA device attributes

### E2E Tests

#### Alpha E2E Tests (Mock)
- API fields are only populated if `GPUsWithDRA` feature gate is enabled
- Once a VMI is created with DRA GPUs, the right spec values are set by virt-controller in virt-launcher pod
- Deletion of VMI will lead to release of GPU resources

#### Alpha2 E2E Tests (Mock)
- Validate metadata file parsing logic
- Verify virt-launcher correctly reads attributes from mounted metadata files

#### Beta E2E Tests

For beta, real GPU driver integration is required. Either of the following drivers will be used:

- **dra-example-driver**: https://github.com/kubernetes-sigs/dra-example-driver
- **NVIDIA DRA driver**: https://github.com/NVIDIA/k8s-dra-driver

## Implementation Phases

### Alpha (v1.6)
1. API changes (`ResourceClaims`, `ClaimRequest` type, GPU struct updates)
2. VMI status API (`vmi.status.deviceStatus` with `gpuStatuses[]`)
3. Webhook verification for GPU DRA requests
4. virt-controller changes:
   - Render virt-launcher pod spec with `resourceClaims`
   - Watch ResourceClaims/ResourceSlices to populate `vmi.status.deviceStatus`
5. virt-launcher reads GPU attributes from `vmi.status.deviceStatus` to generate domain XML
6. Unit tests
7. Mock e2e tests

### Alpha2 (v1.8)
7. Adopt KEP-5304 device metadata file approach
8. Remove virt-controller dependency on ResourceClaim/ResourceSlice watching
9. virt-launcher reads GPU attributes directly from mounted metadata files
10. Update e2e tests for metadata file approach

### Beta
11. Integrate dra-example-driver or NVIDIA DRA driver for e2e testing
12. Create user documentation for GPU DRA
13. Create GPU driver author documentation

### GA
14. Upgrade/downgrade tests
15. Scale tests

## Feature Lifecycle Phases

<!--
How and when will the feature progress through the Alpha, Beta and GA lifecycle phases

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha (v1.6)

- Code changes behind `GPUsWithDRA` feature gate
- Unit tests
- Mock e2e test

**VMI Status Approach (Alpha):**

In Alpha, virt-controller populates `vmi.status.deviceStatus` by:
1. Watching virt-launcher pods, ResourceClaims, and ResourceSlices
2. Extracting device name from `resourceclaim.status.allocation.devices[].deviceName`
3. Looking up device attributes (`pciAddress`, `mDevUUID`) from ResourceSlice objects
4. Populating `vmi.status.deviceStatus.gpuStatuses[]` with the discovered attributes

Virt-launcher then reads `vmi.status.deviceStatus` to generate domain XML.

**Alpha v1.6 VMI Status API** (existing in codebase):
- `vmi.status.deviceStatus.gpuStatuses[]` - populated by virt-controller
- `DeviceResourceClaimStatus` with `pciAddress` and `mDevUUID` attributes

Note: There are multiple challenges in writing a real e2e test like developing a GPU driver implementing KubeVirt
requirements, bringing the driver into KubeVirt CI. Before vendors get a chance to develop a driver, there needs
to be an API that they can develop against which will not be available until the alpha release. For this reason,
writing an e2e test with a real GPU driver is outside the scope of alpha.

### Alpha2 (v1.8)

**Design Changes:**
- Use device metadata files via DRA Attributes Downward API (KEP-5304) instead of ResourceSlice lookups
- Virt-launcher reads GPU attributes directly from mounted metadata files
- Remove virt-controller dependency on ResourceClaim/ResourceSlice watching
- Remove `vmi.status.deviceStatus` (no longer needed)

**Rationale for Alpha2 instead of Beta:**
- KEP-5304 is not yet merged in Kubernetes
- The design has significant changes from Alpha (removing VMI status approach)
- Need to validate the new metadata file approach before graduating to Beta

**Testing:**
- Update mock e2e tests for metadata file approach
- Unit tests for metadata file parsing

### Beta

**Prerequisites:**
- KEP-5304 merged and available in Kubernetes
- Alpha2 design validated
- Design must be proven stable with no major changes required during one full release cycle

**Documentation:**
- User guide for consuming GPUs via DRA in KubeVirt VMs
- GPU driver author guide with required attributes and file format
- Troubleshooting guide

**Testing:**
- E2E tests with dra-example-driver or NVIDIA DRA driver

**Feature Gate:**
- `GPUsWithDRA` feature gate turned on by default

**Evaluation:**
- Evaluate user and GPU driver author experience
- Consider additional GPU use cases (vGPU, MIG)

### GA
- Upgrade/downgrade testing

## Related VEPs

This VEP establishes the core DRA infrastructure for KubeVirt. The following VEPs will build upon this foundation:

- **HostDevicesWithDRA**: Support for generic host devices via DRA
- **NetworkDevicesWithDRA**: Support for network devices via DRA
- **CPUsWithDRA**: Support for CPU resources via DRA 

# References

- Structured parameters
  https://github.com/kubernetes/kubernetes/pull/123516
- Structured parameters KEP
  https://github.com/kubernetes/enhancements/issues/4381
- DRA
  https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
- NVIDIA DRA driver
  https://github.com/NVIDIA/k8s-dra-driver