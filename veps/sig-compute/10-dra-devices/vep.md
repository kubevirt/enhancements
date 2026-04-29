# VEP #10: Support GPUs with DRA in KubeVirt

## VEP Status Metadata

### Target releases

- This VEP targeted alpha for version: v1.6 (VMI-status based design)
- This VEP targeted alpha2 for version: v1.8 (KEP-5304 device-metadata-file based design)
- This VEP targets beta for version: v1.9
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal is about adding support for GPUs via DRA (Dynamic Resource Allocation) in KubeVirt.
DRA allows GPU vendors fine-grained control over GPU allocation and topology. The device-plugin
model will continue to exist in Kubernetes, but DRA offers vendors more control over GPU scheduling
and configuration.

This VEP establishes the core DRA infrastructure in KubeVirt that will also be used by future VEPs
for other device types (HostDevices, NetworkDevices, CPUs).

In KubeVirt 1.8 (alpha2), this VEP consumes device attributes via the
[Kubernetes DRA Device Attributes Downward API (KEP-5304)](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api).
DRA drivers opt in to publishing per-claim, per-request metadata JSON files that are bind-mounted
into consumer pods via CDI. Virt-launcher reads those files locally to build the libvirt domain
XML. As a result, virt-controller does not need to watch `ResourceClaim` or `ResourceSlice`
objects.

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
   - `resource.kubernetes.io/pciBusID` for passthrough GPU 
   - `mDevUUID` for mediated devices/vGPU (currently hardcoded in KubeVirt, will be standardized when [kubernetes/kubernetes#135552](https://github.com/kubernetes/kubernetes/issues/135552) is resolved)
2. DRA driver must write metadata files via the DRA Attributes Downward API (see [KEP-5304](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api))

### Future Usecases
1. NVMe devices https://github.com/kubevirt/community/issues/254
1. Live migration of VMIs using DRA devices

### Unsupported Usecases

### Unsupported Usecases (in this VEP)

1. GPUs where the DRA driver does not set the required attributes (e.g., `resource.kubernetes.io/pciBusID`, `mDevUUID`) will not
   be supported. Drivers must publish the necessary attributes for KubeVirt to generate the libvirt domain XML.
2. HostDevices via DRA - will be addressed in a separate VEP (HostDevicesWithDRA)
3. NetworkDevices via DRA - will be addressed in a separate VEP (NetworkDevicesWithDRA)
4. CPUs via DRA - will be addressed in a separate VEP (CPUsWithDRA)

## Repos

- `kubevirt/kubevirt`

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
	ResourceClaims []VirtualMachineInstanceResourceClaim `json:"resourceClaims,omitempty"`
}

// VirtualMachineInstanceResourceClaim wraps the k8sv1.PodResourceClaim to allow
// VMI-specific extensions while preserving the upstream wire format.
type VirtualMachineInstanceResourceClaim struct {
	k8sv1.PodResourceClaim `json:",inline"`
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
	// ClaimName references the name of an entry in the
	// VMI's spec.resourceClaims[] array. The referenced
	// entry may use either resourceClaimName or
	// resourceClaimTemplateName.
	// +kubebuilder:validation:MinLength=1
	ClaimName string `json:"claimName,omitempty"`
	// RequestName specifies which request from the
	// ResourceClaim/ResourceClaimTemplate spec.devices.requests array this
	// claim request corresponds to.
	// +kubebuilder:validation:MinLength=1
	RequestName string `json:"requestName,omitempty"`
}
```

### ClaimRequest String Change Rationale

Changing `ClaimRequest.claimName` and `requestName` from `*string` to `string`
is a **Go type change, not a wire-format break**. The JSON/YAML field names, the
valid wire format, and existing persisted objects are all unchanged i.e.
`claimName: "foo"` looks identical on the wire whether the Go field is
`*string` or `string`, hence end users authoring VMIs will see no difference.

The change is intentional and reflects how the API evolved from alpha to alpha2:

1. In early alpha, using pointers for optional scalar fields was a reasonable
   starting point and aligned with common Kubernetes API conventions:
   <https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api-conventions.md>.
2. In alpha2, DRA-specific validation was added and the DRA path now requires
   both identifiers to be present and non-empty.
3. For non-DRA/device-plugin entries, `ClaimRequest` remains absent (nil), so
   `claimName` and `requestName` are naturally omitted.

Given this behavior, plain strings are the cleaner model for the DRA path:
they reduce pointer-handling complexity and make the "required when DRA is
used" contract explicit. The feature is still alpha-gated, so the Go-side
refactor can land cleanly with downstream consumers recompiling against the
new types.

### `vmi.spec.resourceClaims[]` Wrapper Type Rationale

Starting in beta (v1.9), the element type of `vmi.spec.resourceClaims[]` is
an in-tree type (`VirtualMachineInstanceResourceClaim`) that inlines the upstream
`k8sv1.PodResourceClaim`. The wrapper exists because KubeVirt cannot add
fields such as `managedClaim` (see [Appendix C](#c-managed-resource-claims))
to `k8sv1.PodResourceClaim` itself; without it, additional policy fields
would have to live in a parallel array, making the API awkward.

Like the `*string` -> `string` change above, introducing the wrapper is a
**Go type change, not a wire-format break**, hence no user manifests will
be affected. Inlining `k8sv1.PodResourceClaim` keeps existing fields
(`name`, `resourceClaimName`, `resourceClaimTemplateName`) at the same
JSON/YAML level, so existing VMIs round-trip identically; only Go consumers
compiling against the generated client-go types are affected.

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

This design depends on
[KEP-5304: DRA Device Attributes Downward API](https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api).
KubeVirt is a pure consumer of this mechanism: virt-launcher only reads the metadata files
that the framework and the driver produce. For how drivers publish metadata and how pods
consume it in general, refer to the upstream Kubernetes documentation:

- Concept: [DRA -> Device metadata](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#device-metadata)
- Workload-side usage: [Allocate devices to workloads with DRA -> Request devices in workloads](https://kubernetes.io/docs/tasks/configure-pod-container/assign-resources/allocate-devices-dra/#request-devices-workloads)

#### Container-Side File Layout

Virt-launcher reads metadata files from this base directory inside the compute
container:

```
/var/run/kubernetes.io/dra-device-attributes/
|-- resourceclaims/
|   `-- {claimName}/{requestName}/{driverName}-metadata.json     # pre-existing claims
`-- resourceclaimtemplates/
    `-- {podClaimName}/{requestName}/{driverName}-metadata.json  # template-generated claims
```

The two top-level subdirectories (`resourceclaims/` vs `resourceclaimtemplates/`) let
virt-launcher locate the file without having to learn the generated claim name for
template-based claims.

In **alpha2 (v1.8)**, virt-launcher carries its own copy of the metadata types and decode
logic in `pkg/dra/metadata/types.go` and `pkg/dra/utils.go`, mirroring the upstream
`k8s.io/dynamic-resource-allocation/api/metadata` shape.

In **beta (v1.9)**, kubevirt will drop the local copy and consume the metadata via the upstream
[`k8s.io/dynamic-resource-allocation/devicemetadata`](https://pkg.go.dev/k8s.io/dynamic-resource-allocation/devicemetadata)
consumer library, which handles path resolution, file globbing, and multi-version JSON
stream decoding on behalf of the consumer.

#### Required Attributes for KubeVirt

DRA GPU drivers that want to support KubeVirt must include the following attributes:

| Device Type | Required Attribute | JSON Path | Example |
|-------------|-------------------|-----------|---------|
| Passthrough GPU | `resource.kubernetes.io/pciBusID` | `requests[].devices[].attributes["resource.kubernetes.io/pciBusID"].stringValue` | `0000:65:00.0` |
| Mediated Device (vGPU) | `mDevUUID` | `requests[].devices[].attributes["mdevUUID"].stringValue` | `aa618089-8b16-4d01-a136-25a0f3c73123` |

> **Note:** `mDevUUID` is not yet standardized under `resources.kubernetes.io`. See
> [kubernetes/kubernetes#135552](https://github.com/kubernetes/kubernetes/issues/135552) for standardization discussion.
> The attribute name may change once standardization is complete.

If both attributes are present on the same device, KubeVirt treats the device as a mediated
device (UUID takes precedence), because a parent mediated device may also expose a PCI address.

#### Virt-Launcher Parsing

For each GPU or HostDevice entry in `vmi.spec.domain.devices.{gpus,hostDevices}[]` whose
`ClaimRequest` is set, virt-launcher performs the following steps in alpha2 (KubeVirt):

1. Find the matching entry `rc` in `vmi.spec.resourceClaims[]` where `rc.name ==
   ClaimRequest.claimName`. (`rc.name` is the pod-local claim name, a.k.a. `podClaimName` in
   KEP-5304 terminology.)
2. Build the claim subdirectory:
   - If `rc.resourceClaimName` is set (pre-existing claim), use
     `resourceclaims/<rc.resourceClaimName>/<requestName>/` - the path segment is the actual
     `ResourceClaim` object name.
   - If `rc.resourceClaimTemplateName` is set (template-generated claim), use
     `resourceclaimtemplates/<rc.name>/<requestName>/` - the path segment is the pod-local
     `podClaimName`, not the generated `ResourceClaim` name.
3. Glob `*-metadata.json` in that directory. KubeVirt expects exactly one match (one driver
   per request); zero or more-than-one is an error.
4. Decode the JSON stream and pick the first object whose `apiVersion` is supported.
5. Locate the single element of `requests[]` whose `name` equals the `requestName`. KubeVirt
   expects exactly one device in `devices[]` (count > 1 not supported).
6. Extract `mDevUUID` first, then fall back to `resource.kubernetes.io/pciBusID`, and build
   the appropriate libvirt domain element.

In **beta (v1.9)**, steps 2-4 will be replaced by a single call to the upstream
[`k8s.io/dynamic-resource-allocation/devicemetadata`](https://pkg.go.dev/k8s.io/dynamic-resource-allocation/devicemetadata)
library:

- Pre-existing claim: `devicemetadata.ReadResourceClaimMetadata(rc.resourceClaimName, requestName)`
- Template-generated claim: `devicemetadata.ReadResourceClaimTemplateMetadata(rc.name, requestName)`

Steps 1, 5, and 6 from alpha2 will remain in beta. The KubeVirt-local types in
`pkg/dra/metadata/` and the decode helpers in `pkg/dra/utils.go` will be removed.

### Webhook Changes

A dedicated DRA admitter validates DRA-related fields on VMI creation / update:

1. `vmi.spec.resourceClaims[].name` entries must be unique.
2. Every `claimName` referenced from `gpus[]` / `hostDevices[]` must exist in
   `vmi.spec.resourceClaims[]`.
3. A GPU / HostDevice entry must be either device-plugin (`DeviceName` set, `ClaimRequest`
   nil) or DRA (`DeviceName` empty, `ClaimRequest` set), never both and never neither.
4. For DRA GPUs, the VMI may not mix DRA and non-DRA GPUs on the same node, because GPU
   device-plugins and GPU DRA drivers typically cannot coexist on the same node. For DRA
   HostDevices, mixing is allowed.
5. If any DRA GPU is present, `GPUsWithDRA` must be enabled. If any DRA HostDevice is present,
   `HostDevicesWithDRA` must be enabled.
6. Each DRA device must set both `claimName` and `requestName`.
7. The `(claimName, requestName)` pair must be unique across DRA devices in the VMI, so that a
   given allocated device is never consumed by two domain entries.


### Virt controller changes

1. If devices are requested using DRA, virt controller needs to render the virt-launcher manifest such that
   `pod.spec.resourceClaims` and `pod.spec.containers.resources.claim` sections are filled out.
2. Starting in alpha2 (v1.8), virt-controller does NOT need to configure any attribute requests in the pod spec -
   the DRA driver handles attribute exposure via framework coordination (KEP-5304).
3. Starting in alpha2 (v1.8), virt-controller does NOT watch ResourceClaims or ResourceSlices. Device attributes
   are made available to virt-launcher via CDI-mounted metadata files written by the DRA driver. The
   `DRAStatusController` and the `ResourceClaim` / `ResourceSlice` informers that existed in alpha (v1.6) were
   removed as part of this transition.
4. Starting in beta (v1.9), virt-controller will skip the `permittedHostDevices` validation for DRA-managed
   devices. `permittedHostDevices` assumes each device can be represented by a single permitted key
   (the device-plugin API's model), which does not fit well with the DRA API. Equivalent allow-list
   semantics for DRA-managed devices can be added later if desired; for now, it is not supported.

### Virt launcher changes

1. For devices allocated using DRA, virt-launcher reads device attributes from metadata files mounted via CDI
   (as defined by KEP-5304). This replaces the need to read from VMI status or environment variables.
2. Virt-launcher globs for metadata files or parses JSON by claim name to find the allocated device attributes.
3. Virt-launcher extracts the required attributes:
   - For passthrough GPUs: `resource.kubernetes.io/pciBusID` (e.g., "0000:65:00.0")
   - For mediated devices (vGPU): `mDevUUID` (e.g., "aa618089-8b16-4d01-a136-25a0f3c73123")
4. Using the extracted attributes, virt-launcher generates the appropriate libvirt domain XML:
   - Passthrough GPU: uses PCI address in `<hostdev>` element
   - Mediated device: uses mdev UUID in `<hostdev>` element with `model='vfio-pci'`
5. For device-plugins (non-DRA), virt-launcher continues to use environment variables (`PCI_RESOURCE_<deviceName>`
   or `MDEV_PCI_RESOURCE_<deviceName>`) as in the current implementation.

## API Examples

### VM API with PassThrough GPU

```
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
        requestName: pgpu-request-name
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
metadata using KEP-5304 and use the attributes (e.g.,
`resource.kubernetes.io/pciBusID`) to generate the domain XML.

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
   | Passthrough GPU | `resource.kubernetes.io/pciBusID` | `0000:65:00.0` |
   | Mediated Device (vGPU) | `mDevUUID` (not yet standardized) | `aa618089-8b16-4d01-a136-25a0f3c73123` |

3. **Verify metadata files** are written at:
   ```
   /var/run/dra-device-attributes/<driver-name>/<namespace>/<claim-name>/metadata.json
   ```

Note: Virt-launcher will glob for metadata files at `/var/run/dra-device-attributes/*/` and extract the
`resource.kubernetes.io/pciBusID` attribute. Drivers that don't publish this attribute
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
  - This is a general rule; however, an exception can be made if the API
    can be consumed by CPU and Network device types without breaking
    changes to the existing GPU interface.

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

## Appendix: Future Extension Points

This appendix records design directions for problems that are already known and have
been intentionally left out of scope for VEP-10. Its purpose is to demonstrate that
the API graduating with this VEP is sufficient, functional, and forward-compatible
with these future changes i.e., that picking up any of the items below will not
require an API-breaking change to the types VEP-10 graduates.

### A. Multi-device DRA usage with the graduating API

The following VMI spec is an example of how the API graduating in beta (v1.9)
will be used with different types of devices (CPU, network and host devices).
The example uses the Network DRA shape from
[VEP-183](https://github.com/kubevirt/enhancements/pull/185) and the CPU DRA
shape from the CPUsWithDRA POC done as part of
[VEP-152](https://github.com/kubevirt/enhancements/issues/152), both of which
are layered onto the shared `ClaimRequest` type from this VEP without
modifying it:

```yaml
spec:
  resourceClaims:
  - name: gpu-nic-host-device-cpu
    resourceClaimTemplateName: all-resource-template

  domain:
    cpu:
      cores: 8
      dra:
        claimName: gpu-nic-host-device-cpu
        requestName: cpu
    devices:
      gpus:
      - name: gpu0
        claimName: gpu-nic-host-device-cpu
        requestName: gpu
      hostDevices:
      - name: hostdevice0
        claimName: gpu-nic-host-device-cpu
        requestName: hostdev
      interfaces:
      - name: sriov-nic
        sriov: {}
    resources:
      requests:
        memory: 32Gi

  networks:
  - name: sriov-nic
    resourceClaim:
      claimName: gpu-nic-host-device-cpu
      requestName: nic
```

This single VMI references one shared claim from four different consumer
sites: for CPU, GPU, HostDevice, and Network. Co-location constraints
(NUMA alignment, PCI-root alignment) live in the underlying
`ResourceClaimTemplate.spec.devices.constraints[]`. See
[dra-driver-cpu#114](https://github.com/kubernetes-sigs/dra-driver-cpu/issues/114)
for a worked CPU/NIC PCI-root alignment example using
`matchAttribute: "resource.kubernetes.io/pcieRoot"`.

All four places can use the same `ClaimRequest` type, and other
improvements to the user experience can be added as purely additive changes
(for example, the `managedClaim` extension in
[Appendix C](#c-managed-resource-claims)).

### B. Integration with CPU DRA devices

CPU DRA is the most complex follow-on integration. Two threads are worth
examining for VEP-10's API: how the upstream CPU driver exposes devices, and
how VEP-10's graduating API relates to those exposure modes.

**Driver modes - grouped vs individual.** The
[`dra-driver-cpu`](https://github.com/kubernetes-sigs/dra-driver-cpu) supports
two device-exposure modes via the `--cpu-device-mode` flag, with a worked
Pod manifest in the upstream repo for each.

***Grouped mode (default).*** The driver publishes one device per NUMA node
(or socket) carrying `dra.cpu/cpu`
[consumable capacity](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#consumable-capacity),
and the claim requests *N* CPUs from the group. Collapsing N CPUs into a
single device entry scales well on large nodes; the trade-off is that
granular selection of each CPU is not possible. Translating
[`pod_with_resource_claim_grouped_mode.yaml`](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/hack/examples/pod_with_resource_claim_grouped_mode.yaml)
to a VMI:

```yaml
# ResourceClaim (verbatim from the upstream example)
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: claim-cpu-capacity-10
spec:
  devices:
    requests:
    - name: req-cpu-slice
      exactly:
        deviceClassName: dra.cpu
        capacity:
          requests:
            dra.cpu/cpu: "10"
---
# VMI consumes it via VEP-10's domain.cpu.dra ClaimRequest
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-with-grouped-cpu
spec:
  resourceClaims:
  - name: cpu-claim
    resourceClaimName: claim-cpu-capacity-10
  domain:
    cpu:
      cores: 10
      dra:
        claimName: cpu-claim
        requestName: req-cpu-slice    # matches devices.requests[].name above
    resources:
      requests:
        memory: 8Gi
```

***Individual mode.*** The driver publishes one device per allocatable CPU,
and the claim selects them by count and optionally by CEL expressions over
per-CPU attributes. Each CPU is independently addressable, enabling
granular selection; the trade-off is that the `ResourceSlice` grows
linearly with core count, and selectors require node-topology knowledge.
A NUMA-aligned individual-mode example - pin 4 CPUs to NUMA node 0 via a
CEL selector - adapted from
[`pod_with_resource_claim_individual_mode.yaml`](https://github.com/kubernetes-sigs/dra-driver-cpu/blob/main/hack/examples/pod_with_resource_claim_individual_mode.yaml):

```yaml
# ResourceClaim: 4 CPUs aligned to NUMA node 0
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: cpu-numa0-4cpus
spec:
  devices:
    requests:
    - name: numa0-cpus
      exactly:
        deviceClassName: dra.cpu
        count: 4
        selectors:
        - cel:
            expression: device.attributes["dra.cpu"].numaNodeID == 0
---
# VMI consumes it via VEP-10's domain.cpu.dra ClaimRequest - same shape as above
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-numa0-aligned
spec:
  resourceClaims:
  - name: cpu-claim
    resourceClaimName: cpu-numa0-4cpus
  domain:
    cpu:
      cores: 4
      dra:
        claimName: cpu-claim
        requestName: numa0-cpus       # matches devices.requests[].name above
    resources:
      requests:
        memory: 8Gi
```

**The graduating API is mode-agnostic.** As demonstrated in the examples
above, the graduating API does not constrain the driver's exposure model,
and any KubeVirt-side support for choosing between modes can be layered on
additively (for example, a future `managedClaim` policy in
[Appendix C](#c-managed-resource-claims) could synthesize either claim
shape without touching the `ClaimRequest` surface VEP-10 graduates).

### C. Managed Resource Claims

The DRA APIs are complex for users authoring VMIs. Take a concrete
scenario: a user wants their VMI's GPU and SR-IOV NIC to land on the same
PCIe root for performance. The VMI spec already declares "I have a GPU and
a NIC", but expressing the alignment intent requires hand-authoring a
`ResourceClaim` (or template) with
`constraints[].matchAttribute: resource.kubernetes.io/pcieRoot` and the
matching per-device requests (see
[dra-driver-cpu#114](https://github.com/kubernetes-sigs/dra-driver-cpu/issues/114)
for a worked example), then referencing it from the VMI by name - the
author re-expresses intent the VMI spec already carries.

Making this easier for the user is an unexplored problem. One option is
for KubeVirt to manage `ResourceClaim` generation from the VMI spec.

The CPUsWithDRA POC done as part of
[VEP-152](https://github.com/kubevirt/enhancements/issues/152) solves the
equivalent UX problem for the CPU side with a CPU-specific
`cpu.dra.auto: true` flag; the mechanism generalizes across all DRA-backed
device types.

To solve this issue, an additional field, `managedClaim`, can be added
alongside `resourceClaimName` and `resourceClaimTemplateName`, letting
KubeVirt own the claim's lifecycle and shape it from a small set of
high-level policies:

| Field                           | Type   | Meaning                                              |
| ------------------------------- | ------ | ---------------------------------------------------- |
| `resourceClaimName`             | string | Reference to a pre-existing claim                    |
| `resourceClaimTemplateName`     | string | Reference to a pre-existing template                 |
| `managedClaim`                  | struct | KubeVirt constructs and owns the claim               |

A VMI spec example that solves this issue:

```yaml
spec:
  resourceClaims:
  - name: gpu-nic
    managedClaim:                              # NEW additive field 1: KubeVirt-managed claim wrapper
      policies:
      - policy: Aligned
        attribute: resource.kubernetes.io/pcieRoot
  domain:
    devices:
      gpus:
      - name: gpu0
        claimName: gpu-nic
        requestName: gpu
        deviceClassName: gpu.nvidia.com    # NEW additive field 2: per-consumer DRA class
      interfaces:
      - name: sriov-nic
        sriov: {}
  networks:
  - name: sriov-nic
    resourceClaim:
      claimName: gpu-nic
      requestName: nic
      deviceClassName: dranet              # NEW additive field 2: per-consumer DRA class
---
# ResourceClaim: generated and owned by KubeVirt
#
# Generated from the managedClaim policy above. Reference to an example
# NVIDIA DRA-net GPU+NIC alignment example:
#   https://github.com/kubernetes-sigs/dranet/blob/f359add7df198f466f63352e98c177a7ed01371d/examples/demo_nvidia_dranet/resourceclaims.yaml#L37
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: <vmi-name>-gpu-nic                 # owned by the VMI
spec:
  devices:
    requests:
    - name: gpu                            # = vmi.spec.domain.devices.gpus[].requestName
      exactly:
        deviceClassName: gpu.nvidia.com    # = gpus[].deviceClassName above
        count: 1                           # = #gpus[] sharing this claimName
    - name: nic                            # = vmi.spec.networks[].resourceClaim.requestName
      exactly:
        deviceClassName: dranet            # = networks[].resourceClaim.deviceClassName above
        count: 1                           # = #networks[] sharing this claimName
    constraints:
    - matchAttribute: resource.kubernetes.io/pcieRoot   # = managedClaim.policies[].attribute
```

The example demonstrates two properties:

1. **The change is additive.** All new fields introduced above extend
   existing structs without modifying them. Existing VMIs that don't use
   the new fields are unaffected, and the `(claimName, requestName)`
   consumer shape VEP-10 graduates is unchanged.
2. **The graduating API tolerates unexplored problems.** Several design
   decisions remain open and unexplored. As the example above demonstrates,
   none of them should require a change to the APIs graduating through
   this VEP.

### D. Live migration of DRA-backed devices

VEP-10 lists live migration as future work for GPUs and notes that the
metadata-file approach (KEP-5304) simplifies it because each virt-launcher
pod independently reads its own metadata. VEP-183 (Network DRA) explicitly
defers migration. CPU DRA migration is fundamentally constrained because
host-pinned cpusets are not node-portable, the same way
`dedicatedCpuPlacement` is not migration-friendly today.

A dedicated VEP for "live migration with DRA devices" should cover: device
compatibility checks pre-migration, target-side claim allocation,
target-side metadata-file mounting, and the CPU-specific impossibility of
migrating a hard cpuset. This is a future VEP, not an API change to this
one - the `ClaimRequest` types VEP-10 graduates do not need any extension to
support migration; the additions live elsewhere (migration controller,
admitter, KEP-5304 helpers).

## References

- Dynamic Resource Allocation (DRA):
  <https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/>
- KEP-5304 DRA Device Attributes Downward API:
  <https://github.com/kubernetes/enhancements/tree/master/keps/sig-node/5304-dra-attributes-downward-api>
- Structured parameters KEP: <https://github.com/kubernetes/enhancements/issues/4381>
- `mDevUUID` standardization tracking issue:
  <https://github.com/kubernetes/kubernetes/issues/135552>
- NVIDIA DRA driver: <https://github.com/NVIDIA/k8s-dra-driver-gpu>
- dra-example-driver: <https://github.com/kubernetes-sigs/dra-example-driver>