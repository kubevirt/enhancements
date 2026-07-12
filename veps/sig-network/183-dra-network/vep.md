# VEP #183: Network Device DRA Support

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: v1.10

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved

## Overview

This proposal adds support for network devices provisioned via [DRA (Dynamic Resource Allocation)](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/) in KubeVirt.
It extends the existing KubeVirt networks API with a new network source type for DRA-backed devices, allowing network devices to be allocated via DRA without removing the existing device-plugin + Multus-based approach, which remains supported separately.
In this model, an external DRA driver provisions the network device, while a network binding plugin configures the VM's network interface — KubeVirt orchestrates the integration between the two.

This VEP builds upon the core DRA infrastructure defined in VEP #10 ([kubevirt/enhancements/pull/11](https://github.com/kubevirt/enhancements/pull/11)) to add support for network devices.

## Motivation

Today, consuming network devices in KubeVirt relies on the device-plugin + Multus flow. In this model, device plugins expose devices as opaque integer counts. As a result, the scheduler has no visibility into device topology, NUMA affinity, or cross-device constraints, and placement decisions are made without understanding what the workload actually needs.

DRA adoption for network devices is important for KubeVirt driver authors, users, and administrators. Compared to the existing flow, DRA provides a Kubernetes-native, claim-based model that supports richer constraints, enables earlier scheduling decisions based on device requirements, and standardizes device lifecycle management via built-in APIs.

Key advantages for users/admins:
- **Fine-grained and complex constraints:** DRA requests are expressed through `ResourceClaim`/`ResourceClaimTemplate` and `DeviceClass`, so users/admins can describe *what* they need (not only "one device"). This enables richer matching than a plain extended-resource count.
- **Scheduler-aware allocation flow:** Allocation is part of a Kubernetes-native claim flow before workload finalization, so placement decisions can account for device constraints earlier.
- **Standard, built-in Kubernetes API model:** DRA uses first-class Kubernetes APIs (`ResourceClaim`, `ResourceClaimTemplate`, `DeviceClass`) instead of plugin-specific side channels, improving portability, auditability, and ongoing operations.
- **Lower per-driver integration overhead:** KubeVirt provides a generic integration model where the DRA driver and binding plugin exchange device attributes directly, reducing custom per-driver integration work for driver authors, binding plugin authors, and KubeVirt maintainers.

## Goals

- Enable KubeVirt VMs to consume network devices via DRA, using externally supplied DRA drivers
- Define a generic integration model for DRA-backed network devices
- Support network binding plugins with DRA-backed network devices
- Support live migration of VMs with DRA network devices (using `resourceClaimTemplate`-backed claims only)

## Non Goals

- Replace existing Multus-based network integrations (remain fully supported)
- Deploy/manage external DRA network drivers (provided externally)
- Support coexistence of DRA and legacy device-plugin modes for the same device type in the same cluster
- Live migration of VMs with DRA network devices using direct `resourceClaim`-backed claims,
  because a direct ResourceClaim is bound to a node after allocation, while migration requires a separate allocation on the destination node
- Standardizing a cross-driver MAC address configuration contract for DRA network devices
- Hot-plug / hot-unplug of VM network interfaces backed by DRA network devices

## Definition of Users

- **Admin**: A person who manages infrastructure and configures DRA device classes and drivers
- **VM owner**: A person who wants to attach DRA-managed network devices to a VM

## User Stories

- As a VM owner, I want to attach DRA-managed network devices to my VM using a `ResourceClaim` or `ResourceClaimTemplate`,
so that I can consume DRA-managed devices the same way I would in a container workload
- As an admin, I want to control network device allocation through the DRA APIs so that I can define allocation policies without modifying KubeVirt configuration.
- As a VM owner, I want to upgrade KubeVirt without shutting down my VMs that use DRA-backed network devices so that I can apply updates without VM downtime.
- As a VM owner, I want to live-migrate my VM with DRA-backed network devices so that I can perform maintenance or rebalancing without VM downtime.

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
3. Register the network binding plugin in the KubeVirt CR.

#### User flow (claim setup + per-VM attach)

1. Create claim objects users will consume: one or more `ResourceClaimTemplate` objects (reusable across VMs) or `ResourceClaim` objects, with device requests that reference the selected network-device `DeviceClass` via `deviceClassName`.
2. For each VM, add a matching entry in `spec.template.spec.resourceClaims[]` (using either a `ResourceClaim` or a `ResourceClaimTemplate`).
3. For each VM, define an interface with a network binding plugin and a matching network with `resourceClaim` source, referencing the claim and request via `spec.template.spec.networks[].resourceClaim.claimName` and `spec.template.spec.networks[].resourceClaim.requestName`.
4. Create the VM.

#### Responsibility boundary

- **Admin owns:**
  - driver/operator deployment
  - `DeviceClass` definitions
- **User owns:**
  - provisioning/policy of `ResourceClaim`/`ResourceClaimTemplate` objects
  - VM interface/network wiring
  - selecting claim/request references from provisioned `ResourceClaim`/`ResourceClaimTemplate` objects

### External Dependencies

- Network binding plugin support for DRA network devices requires Kubernetes 1.34+, where DRA Structured Parameters is GA. DRA drivers that publish [device metadata](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#device-metadata) require Kubernetes 1.36+.
- A DRA driver for the target network-device type is an external dependency; it is selected and deployed outside KubeVirt, and is not delivered by this VEP.
- Driver capability requirements (for external network-device DRA drivers):
  - DRA drivers may optionally publish device attributes through the Kubernetes [DRA device metadata](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#device-metadata) flow.

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

A new technology-agnostic `InfoSource` value `pod-status` is introduced for network interfaces whose device is ready in the pod, regardless of the underlying mechanism (DRA, Multus, or future alternatives).
The VMI controller determines this status from the `k8s.v1.cni.cncf.io/network-status` annotation (for Multus-backed interfaces) and `pod.status.resourceClaimStatuses` (for DRA-backed interfaces).

> **Note:** The exact mechanism for determining DRA device readiness is informational and may change during implementation.

This resolves the gap where `infoSource: multus-status` is not reported for DRA-backed interfaces, by providing a technology-agnostic equivalent.

> **Note:** `pod-status` is a new string value in an existing field, not an API schema change.

As additional sources report in, `vmi.status.interfaces[].infoSource` values are combined, e.g.:
- `pod-status` — device is ready in the pod
- `pod-status, domain` — device attached to the VM domain
- `pod-status, domain, guest-agent` — guest agent also reporting the interface

### Limitations Compared to Multus-based Flow

With Multus, network configuration details such as MAC address and interface name can be specified through the `k8s.v1.cni.cncf.io/networks` annotation.
The VMI controller does not populate the `k8s.v1.cni.cncf.io/networks` annotation for DRA networks, except for network binding plugins that have a NAD configured.

The Kubernetes DRA API does not provide a standard way to specify these properties:
- **MAC address:** Cannot be specified through the DRA flow. See Open Issue #1.
- **Interface name:** Cannot be specified through the DRA flow. The guest-visible interface name is determined by libvirt/QEMU.

### Network Device Integration

When a network interface has a network binding plugin and references a network with `resourceClaim` source:

1. The network admitter validates that exactly one network source type (pod, multus, or resourceClaim) is specified
2. Virt-controller renders the virt-launcher pod spec with the referenced resource claim in the compute container's `resources.claims[]`.
   If a network binding plugin sidecar is present, the claim is also added to the sidecar container's `resources.claims[]` so it can access DRA device metadata if published by the driver
3. If the DRA driver publishes device metadata, the pod receives it through the Kubernetes DRA metadata-file mechanism
4. The binding plugin sidecar may use this metadata to resolve allocated device attributes and adjust the domain XML accordingly

The DRA device driver and the network binding plugin are the two active parties that work together to deliver a fully configured network device to the VM.
KubeVirt orchestrates the integration: it wires the DRA allocation into the pod spec and drives the domain configuration lifecycle — but it does not own the device-specific logic on either side.
The metadata contract is defined by the DRA driver and binding plugin authors, not by KubeVirt.

> **Note:** If a request allocates more than one device (i.e., `count > 1`), only the first allocated device is used. See Open Issue #3.

### Validation

Webhook validations ensure:
1. Networks with `resourceClaim` source must map to interfaces using a network binding plugin.
2. Each network must reference a unique `claimName` + `requestName` combination.
   No two DRA entities (networks, hostDevices, or GPUs) can share the same tuple, because each interface/network pair must map to exactly one device allocation.
3. Reject mixing `multus` and `resourceClaim` network sources in the same VMI.
4. Both `claimName` and `requestName` must be non-empty.

### Component Changes

**Virt-Controller:**
- Renders virt-launcher pod spec with resource claims from `vmi.spec.resourceClaims[]` referenced by `vmi.spec.networks[].resourceClaim`

**Network Binding Plugin Sidecars:**
- Some network binding plugins with a sidecar may need access to allocated device attributes (e.g., PCI address) to configure the interface in the domain XML.
  The DRA driver's [device metadata](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/#device-metadata) file is mounted into the sidecar container, allowing it to read these attributes from the DRA allocation metadata and apply them to the domain configuration accordingly.
  Note: DRA device metadata must be implemented by the DRA driver owner; this is outside the scope of KubeVirt.
- When a network binding plugin has a NAD configured, Multus can still be used for CNI invocation — the pod's Multus annotation (`k8s.v1.cni.cncf.io/networks`) is populated with the plugin's NAD entry.

See [Appendix A](#appendix-a-concrete-example---network-binding-plugin-with-dra) for a concrete example.

## Scalability

This integration follows the existing DRA scalability model used for other device types (for example, GPUs and HostDevices). See [VEP #10](../../sig-compute/10-dra-devices/vep.md#scalability) for details.

## Update/Rollback Compatibility

- Changes are upgrade compatible
  - Reasoning: the API changes are additive and guarded by `NetworkDevicesWithDRA`; with the gate disabled, existing VM network behavior remains unchanged.
- Rollback works as long as feature gate is disabled
- If the feature is enabled, VMIs using DRA network devices must be deleted and feature gate disabled before attempting rollback

## Functional Testing Approach

- End-to-end functional tests covering:
  - Network binding plugin with a DRA driver, including live migration
- Stage-specific testing scope is documented in the [Graduation Requirements](#graduation-requirements) section

## Implementation History

- 2026-01-20: Initial design/VEP proposal for DRA-backed network device support
- 2026-07-12: Updated for Beta scope - network binding plugins and live migration; SR-IOV removed from scope due to competing industry approaches for integrating SR-IOV with DRA

## Graduation Requirements

### Alpha

- Core API and integration changes behind `NetworkDevicesWithDRA` feature gate
- Unit tests
- No in-tree CI e2e gate (consistent with the current DRA alpha model)
- Alpha was validated manually with SR-IOV as the concrete network device type

### Beta

- Network binding plugin support with DRA network devices
- Live migration support for DRA network devices, for `resourceClaimTemplateName`-backed claims
- Functional e2e tests for network binding plugin with a DRA driver, including live migration
- KubeVirt user-guide documentation

### GA

- Backward-compatible upgrade path from Beta
- All Beta open issues resolved or explicitly deferred by design

> **Note:** SR-IOV is not supported as a DRA network device type. Support may be revisited once industry approaches for integrating SR-IOV with DRA converge.

## References

- DRA: https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/
- VEP #10 (DRA devices): /veps/sig-compute/10-dra-devices/vep.md
- Kubernetes DRA device information injection: https://github.com/kubernetes/enhancements/pull/5606

## Open Issues

1. **MAC assignment for DRA-backed network interfaces:**
   - For the current Beta scope (binding plugins), MAC specification is outside KubeVirt's responsibility — it is part of the contract between the DRA driver and the binding plugin.
   - A cross-driver MAC propagation mechanism may be needed for future use cases (e.g., SR-IOV with internal KubeVirt bindings).
2. **Multiple network devices mapped to the same `ResourceClaim` / `ResourceClaimTemplate`:**
   - Current scope focuses on one DRA network device per VM when devices map to the same RC/RCT.
   - After [kubevirt/kubevirt#16769](https://github.com/kubevirt/kubevirt/issues/16769) is merged, support can be extended to multiple devices that reference the same RC/RCT with different `requestName` values.
3. **`ResourceClaimTemplate` requests with [`count`](https://kubernetes.io/docs/reference/generated/kubernetes-api/v1.36/#exactdevicerequest-v1-resource-k8s-io) `> 1`:**
   - A single request inside a `ResourceClaimTemplate` can set `count: N` to allocate multiple devices at once. This scenario has not been validated.
   - Example with `count: 2`:
     ```yaml
     apiVersion: resource.k8s.io/v1
     kind: ResourceClaimTemplate
     metadata:
       name: two-nics
     spec:
       spec:
         devices:
           requests:
             - name: nics
               deviceClassName: my-net-class
               count: 2
     ```
   - The DRA driver may publish device metadata to the binding plugin sidecar; it is up to the binding plugin to handle multiple devices from a single request.


## Appendix A: Concrete Example - Network Binding Plugin with DRA

### API Example: VM with DRA Network and Binding Plugin

```yaml
---
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    network:
      binding:
        my-binding-plugin:
          sidecarImage: my-binding-plugin-sidecar:latest
          migration: {}
---
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: network-claim-template
spec:
  spec:
    devices:
      requests:
      - name: dev
        exactly:
          deviceClassName: my-device-class
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm-dra-binding-plugin
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
          - name: dra-net
            binding:
              name: my-binding-plugin
      networks:
      - name: dra-net
        resourceClaim:
          claimName: network-claim
          requestName: dev
      resourceClaims:
      - name: network-claim
        resourceClaimTemplateName: network-claim-template
```

The following pod is generated by `virt-controller` from the VM above; users do not create it directly.

```yaml
---
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-vmi-dra-binding-plugin
spec:
  containers:
  - name: compute
    image: virt-launcher
    resources:
      claims:
      - name: network-claim
        request: dev
  - name: my-binding-plugin
    image: my-binding-plugin-sidecar:latest
    resources:
      claims:
      - name: network-claim
        request: dev
  resourceClaims:
  - name: network-claim
    resourceClaimTemplateName: network-claim-template
```
