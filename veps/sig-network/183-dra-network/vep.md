# VEP #183: Network Device DRA Support

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved

## Overview

This proposal adds support for [DRA (Dynamic Resource Allocation)](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) provisioned network devices in KubeVirt.
It extends the existing KubeVirt networks API with a new network source type for DRA-backed devices, allowing network devices to be allocated via DRA without removing the existing device-plugin + Multus-based approach, which remains supported separately.

This VEP builds upon the core DRA infrastructure defined in VEP #10 ([kubevirt/enhancements/pull/11](https://github.com/kubevirt/enhancements/pull/11)) to add support for network devices.
It defines a generic network-device model, while using SR-IOV as the concrete Alpha example.
This feature requires Kubernetes v1.36 or newer because the DRA downward-API device information path used by KubeVirt was introduced in Kubernetes v1.36.

## Motivation

Today, consuming network devices in KubeVirt relies on the device-plugin + Multus flow. In this model, device plugins expose devices as opaque integer counts. As a result, the scheduler has no visibility into device topology, NUMA affinity, or cross-device constraints, and placement decisions are made without understanding what the workload actually needs.

DRA adoption for network devices is important for KubeVirt driver authors, users, and administrators. Compared to the existing flow, DRA provides a Kubernetes-native, claim-based model that supports richer constraints, enables earlier scheduling decisions based on device requirements, and standardizes device lifecycle management via built-in APIs.

Key advantages for users/admins:
- **Fine-grained and complex constraints:** DRA requests are expressed through `ResourceClaim`/`ResourceClaimTemplate` and `DeviceClass`, so users/admins can describe *what* they need (not only "one device"). This enables richer matching than a plain extended-resource count.
- **Scheduler-aware allocation flow:** Allocation is part of a Kubernetes-native claim flow before workload finalization, so placement decisions can account for device constraints earlier.
- **Standard, built-in Kubernetes API model:** DRA uses first-class Kubernetes APIs (`ResourceClaim`, `ResourceClaimTemplate`, `DeviceClass`) instead of plugin-specific side channels, improving portability, auditability, and ongoing operations.
- **Lower per-driver integration overhead:** KubeVirt consumes allocated device attributes through a standardized DRA metadata/downward-API contract (aligned with VEP #10), reducing custom per-driver integration work for admins, driver authors, and KubeVirt maintainers.

## Goals

- Enable KubeVirt VMs to consume network devices via DRA, using externally supplied DRA drivers
- Define a generic integration model for DRA-backed network devices

## Non Goals

- Replace existing Multus-based network integrations (remain fully supported)
- Deploy/manage external DRA network drivers (provided externally)
- Support coexistence of DRA and legacy device-plugin modes for the same device type in the same cluster
- Live migration of VMs with DRA network devices:
  - Not supported in Alpha (targeted for Beta, using `resourceClaimTemplate`-backed claims only)
  - Permanently unsupported when using `resourceClaim`-backed claims direct, because a direct ResourceClaim is bound to a node after allocation, while migration requires a separate allocation on the destination node
- In-tree `kubevirtci` provider support for DRA network device e2e in Alpha1 (targeted for Alpha follow-up)
- In-tree CI e2e gating in Alpha1 (targeted for Alpha follow-up)
- Standardizing a cross-driver MAC address configuration contract for DRA network devices
- VM network interface hot-plug / hot-plug add for DRA network devices

## Definition of Users

- **Admin**: A person who manages infrastructure and configures DRA device classes and drivers
- **VM owner**: A person who wants to attach DRA-managed network devices to a VM

## User Stories

- As a VM owner, I want to attach DRA-managed network devices to my VM using a `ResourceClaim` or `ResourceClaimTemplate`,
so that I can consume DRA-managed devices the same way I would in a container workload
- As an admin, I want to control network device allocation through the DRA APIs so that I can define allocation policies without modifying KubeVirt configuration.

## Repos

kubevirt/kubevirt

## Design

This design introduces a new feature gate: `NetworkDevicesWithDRA`.
All the API changes will be gated behind this feature gate so as not to break existing functionality.

### End-to-End Flow

#### Admin flow (one-time setup)

1. Enable and configure the target DRA network-device environment in the cluster:
   - Deploy/configure the external DRA driver for the target network device type.
   - Create / Ensure a `DeviceClass` exists that selects the target network devices managed by that DRA driver.
2. Enable the KubeVirt `NetworkDevicesWithDRA` feature gate.

#### User flow (claim setup + per-VM attach)

1. Create claim objects users will consume: one or more `ResourceClaimTemplate` objects (reusable across VMs) or `ResourceClaim` objects, with device requests that reference the selected network-device `DeviceClass` via `deviceClassName`.
2. For each VMI, add a matching entry in `vmi.spec.resourceClaims[]` (using either a `ResourceClaim` or a `ResourceClaimTemplate`).
3. For each VMI, define an interface with a compatible binding and a matching network with `resourceClaim` source, referencing the claim and request via `network.resourceClaim.claimName` and `network.resourceClaim.requestName`.
4. Create the VMI.

#### Responsibility boundary

- **Admin owns:**
  - driver/operator deployment
  - `DeviceClass` definitions
- **User owns:**
  - provisioning/policy of `ResourceClaim`/`ResourceClaimTemplate` objects
  - VMI interface/network wiring
  - selecting claim/request references from provisioned `ResourceClaim`/`ResourceClaimTemplate` objects

### External Dependencies

- A DRA driver for the target network-device type is an external dependency; it is selected and deployed outside KubeVirt, and is not delivered by this VEP.
- Driver capability requirements (for external network-device DRA drivers):
  - Must support publishing device attributes through the Kubernetes DRA metadata/Downward API flow, including support for:
    - `resource.kubernetes.io/pciBusID` (the device PCI address).
    > **Note:** This capability is Alpha in Kubernetes v1.36 and requires Kubernetes >= v1.36 (see [kubernetes/enhancements#5304](https://github.com/kubernetes/enhancements/issues/5304)).

### API Changes

A new network source field `ResourceClaim` is added to `NetworkSource`.
The field reuses the existing core `ClaimRequest` type (already used by GPUs and HostDevices):

```go
// Represents the source resource that will be connected to the VM.
// Only one of its members may be specified.
type NetworkSource struct {
	Pod           *PodNetwork                 `json:"pod,omitempty"`
	Multus        *MultusNetwork              `json:"multus,omitempty"`
	ResourceClaim *ClaimRequest               `json:"resourceClaim,omitempty"`
}
```

### Network Device Integration

When a network interface has a compatible device binding and references a network with `resourceClaim` source:

1. The network admitter validates that exactly one network source type (pod, multus, or resourceClaim) is specified
2. Virt-controller adds the referenced resource claim to the virt-launcher pod spec
3. The pod receives DRA allocation metadata through the Kubernetes DRA metadata-file mechanism
4. Virt-launcher resolves the claim/request to device attributes from that metadata and generates the appropriate libvirt hostdev XML

This approach keeps a clean separation: DRA handles provisioning and allocation metadata publication, while KubeVirt networks API handles VM network configuration.

### Validation

Webhook validations ensure:
1. Networks with `resourceClaim` source must map to interfaces using a DRA-compatible device binding.
2. Each network must reference a unique `claimName` + `requestName` combination.
   No two DRA entities (networks, hostDevices, or GPUs) can share the same tuple, because each interface/network pair must map to exactly one device allocation.
3. Reject mixing `multus` and `resourceClaim` network sources in the same VMI.

### Component Changes

**Virt-Controller:**
- Renders virt-launcher pod spec with resource claims from `vmi.spec.resourceClaims[]` referenced by `vmi.spec.networks[].resourceClaim`

**Virt-Launcher:**
- For DRA-backed network-device interfaces, virt-launcher resolves allocated device data from DRA metadata file content available in the pod
- Generates hostdev XML by:
  - Filtering VMI spec interfaces with compatible device bindings that reference networks with resourceClaim source
  - Matching claim/request identity from VMI network configuration to allocated metadata entries
  - Extracting the PCI address (and related attributes) from metadata entries
  - Generating standard libvirt hostdev XML

- **Note:** If the ResourceClaim/ResourceClaimTemplate is allocating more than one device for the request, KubeVirt will consume the first device from the allocated devices

## Scalability

This integration follows the existing DRA scalability model used for other device types (for example, GPUs and HostDevices). See [VEP #10](../../sig-compute/10-dra-devices/vep.md#scalability) for details.

## Update/Rollback Compatibility

- Changes are upgrade compatible
  - Reasoning: the API changes are additive and guarded by `NetworkDevicesWithDRA`; with the gate disabled, existing VM network behavior remains unchanged.
- Rollback works as long as feature gate is disabled
- If the feature is enabled, VMIs using DRA network devices must be deleted and feature gate disabled before attempting rollback

## Functional Testing Approach

- **Alpha1:**
  - Unit tests in-tree
  - No in-tree CI e2e gate
  - Validation is external/local, consistent with the current DRA alpha approach
  - Validation is executed with SR-IOV as the concrete network device type
- **Alpha follow-up:** Add non-migration e2e coverage and an in-tree lane as follow-up work.
- **Beta:** Extend coverage to migration scenarios (for `resourceClaimTemplateName`-backed claims only).

## Implementation History

- 2026-01-20: Initial design/VEP proposal for DRA-backed network device support

## Graduation Requirements

### Alpha

#### Alpha1

- Core API and integration changes behind `NetworkDevicesWithDRA` feature gate
- Unit tests
- No in-tree CI e2e gate (consistent with the current DRA alpha model)
- Validation with a concrete network-device DRA driver and provider setup (SR-IOV)

#### Alpha follow-up

- Non-migration DRA e2e tests added (we first need MAC support at least for some of those)
- In-tree e2e lane for those non-migration tests

### Beta

- Live migration support for DRA network devices, for `resourceClaimTemplateName`-backed claims
- Migration-specific e2e test coverage
- Evaluate user and driver author experience

### GA

- Support additional DRA-managed network device types beyond the Alpha SR-IOV example.
- Live migration for direct `spec.resourceClaims[].resourceClaimName` claims remains unsupported by design.

## Appendix A: Concrete Example - SR-IOV (Alpha Scope)

This VEP is intentionally generic, and uses SR-IOV as a concrete Alpha example for driver behavior and end-to-end workflow.

For this SR-IOV DRA example, the `NetworkAttachmentDefinition` is an implementation detail of the external SR-IOV DRA driver, not part of the KubeVirt DRA API contract.
This Alpha example is expected to work with the [k8snetworkplumbingwg/dra-driver-sriov](https://github.com/k8snetworkplumbingwg/dra-driver-sriov) implementation.

### API Example: VMI with DRA SR-IOV Network

```yaml
---
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: sriov-nad
spec:
  config: |-
    {
        "cniVersion": "0.4.0",
        "name": "sriov-nad",
        "type": "sriov",
        "vlan": 0,
        "spoofchk": "on",
        "trust": "on",
        "vlanQoS": 0,
        "logLevel": "info",
        "ipam": {
            "type": "host-local",
            "ranges": [
                [
                    {
                        "subnet": "10.0.1.0/24"
                    }
                ]
            ]
        }
    }
---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: sriov-claim-template
spec:
  spec:
    devices:
      requests:
      - name: vf
        exactly:
          deviceClassName: sriovnetwork.k8snetworkplumbingwg.io
      config:
      - requests: ["vf"]
        opaque:
          driver: sriovnetwork.k8snetworkplumbingwg.io
          parameters:
            apiVersion: sriovnetwork.k8snetworkplumbingwg.io/v1alpha1
            kind: VfConfig
            netAttachDefName: sriov-nad
            driver: vfio-pci
            addVhostMount: true

---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-sriov-dra
spec:
  domain:
    devices:
      interfaces:
      - name: sriov-net
        sriov: {}
  networks:
  - name: sriov-net
    resourceClaim:
      claimName: sriov-network-claim
      requestName: vf
  resourceClaims:
  - name: sriov-network-claim
    resourceClaimTemplateName: sriov-claim-template
```

The following pod is generated by `virt-controller` from the VMI above; users do not create it directly.

```yaml
---
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-sriov-dra
spec:
  containers:
  - name: compute
    image: virt-launcher
    resources:
      claims:
      - name: sriov-network-claim
        request: vf
  resourceClaims:
  - name: sriov-network-claim
    resourceClaimTemplateName: sriov-claim-template
status:
  resourceClaimStatuses:
  - name: sriov-network-claim
    resourceClaimName: virt-launcher-vmi-sriov-dra-sriov-network-claim-abc123
```

## References

- DRA: https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
- Example SR-IOV DRA driver: https://github.com/k8snetworkplumbingwg/dra-driver-sriov
- VEP #10 (DRA devices): /veps/sig-compute/10-dra-devices/vep.md
- Kubernetes DRA device information injection: https://github.com/kubernetes/enhancements/pull/5606

## Open Issues

1. **MAC assignment for DRA-backed network interfaces (post-Alpha1):**
   - Keep MAC support as follow-up scope rather than Alpha1 core.
   - Define the final propagation mechanism from `spec.domain.devices.interfaces[].macAddress`.
   - Define ownership of MAC propagation metadata generation (for example, whether Virt-Controller writes it, or an alternative component/path does).
   - If using annotation-based propagation, standardize the `kubevirt.io/dra-networks` payload shape with `claimName` / `requestName` / `mac`. (prefix subject to change, so it will be generic and not kubevirt specific)
     example:
     `kubevirt.io/dra-networks: '[{"claimName":"sriov-network-claim","requestName":"vf","mac":"de:ad:00:00:be:ef"}]'`
   - Define driver-side behavior for consuming MAC requests and applying them to the target network device.
   - Define the minimum MAC support needed to unblock non-migration, MAC based, DRA e2e tests.
2. **Multiple network devices mapped to the same `ResourceClaim` / `ResourceClaimTemplate`:**
   - Current scope focuses on one DRA network device per VM when devices map to the same RC/RCT.
   - After [kubevirt/kubevirt#16769](https://github.com/kubevirt/kubevirt/issues/16769) is merged, support can be extended to multiple devices that reference the same RC/RCT with different `requestName` values.
3. **`infoSource: multus` is not supported for DRA-backed interfaces:**
   - For DRA-backed interfaces, `vmi.status.interfaces[].infoSource` does not report `multus-status`.
   - User impact: consumers that may rely on `infoSource` containing `multus` to identify interface origin will not get that signal for DRA-backed interfaces.
   - Add support for the reciprocal alternative (for example, DRA-tap based flow) according to user and product needs.
4. **`ResourceClaimTemplate` requests with `count > 1` are not supported:**
   - Current scope supports one allocated device per request for DRA-backed network interfaces.
5. **Multiple matched devices are not supported:**
   - When multiple devices match for the same request, KubeVirt fails with an error.
   - Reference: [kubevirt/kubevirt#17028 (diff)](https://github.com/kubevirt/kubevirt/pull/17028/files#diff-0027cc61eb4150ec3e72d9bb6038bc08f5448b06d0211010de6995883126ca1aR128)
6. **Mixed device types in the same `ResourceClaimTemplate` are not yet validated:**
   - A `ResourceClaimTemplate` that includes mixed device types (for example, GPU and SR-IOV), with a VM that requests both, is expected to be supported.
   - This scenario was not validated in the current scope and must be validated in follow-up work.
   - This follow-up is important because mixed-type claims are a key DRA capability.
