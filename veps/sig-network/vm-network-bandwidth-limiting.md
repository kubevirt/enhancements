# VEP #235: VM Network Bandwidth Limiting

## Release Signoff Checklist

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [x] (R) Target version is explicitly mentioned and approved

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

## Overview
This VEP proposes adding Network Quality of Service (QoS) support to KubeVirt by enabling bandwidth limiting for Virtual Machine (VM) secondary network interfaces managed by Multus. This allows users to specify bandwidth limits in the VirtualMachine template spec, which `virt-controller` translates into Multus network-selection annotations (`k8s.v1.cni.cncf.io/networks`) on the `virt-launcher` pod, leveraging standard Kubernetes CNI chaining to enforce limits at the pod boundary.

## Motivation
Currently, KubeVirt VMs attached to secondary networks can consume the full available bandwidth of the host's physical network interface. While administrators can manually configure CNI bandwidth plugins inside Multus `NetworkAttachmentDefinition` (NAD) objects, doing so hardcodes the limit for all VMs using that specific NAD. There is a requirement for a native KubeVirt API to define these limits on a per-VM basis, allowing for dynamic QoS tiers (e.g., via KubeVirt `VirtualMachineInstancetypes`) without proliferating duplicate NADs across the cluster.

## Goals
- Enable per-interface traffic shaping for secondary VM networks.
- Provide a native Kubernetes-style API using standard resource units for network QoS.
- Act as a control-plane abstraction layer: translating KubeVirt API intent into standard Multus CNI JSON annotations.
- Ensure limits survive Live Migration natively by persisting the intent in the VMI spec.

## Non Goals
- **Primary Network (Masquerade) Support:** Due to live-migration IP reassignment limitations and the lack of a NAD for the default network, shaping the primary network is out of scope for the Alpha phase.
- **Dynamic "live" updating:** Because CNI plugins only execute at pod creation (the CNI ADD phase), users cannot dynamically update the bandwidth of a running VM without a restart during the Alpha phase.
- **Direct Libvirt/tc manipulation:** This VEP strictly relies on the Kubernetes-native Bandwidth CNI plugin at the pod boundary, rather than configuring `tc` rules directly on the tap device via Libvirt.

## Definition of Users
- **Cluster Administrators**: Responsible for node health and resource fair-share.
- **Cloud Service Providers**: Delivering tiered networking performance levels to end-users via `Instancetypes`.
- **Users**: End-users deploying VMs who need predictable network throughput.

## Design

### API Change
The implementation introduces a `Bandwidth` field within the existing `Interface` struct. The API aligns exactly with the standard Kubernetes Bandwidth CNI plugin capabilities, accepting `Rate` and `Burst` parameters for both ingress and egress:

* **Rate**: The desired average bandwidth rate (e.g., `100M`, `1G`).
* **Burst**: The maximum amount of data that can be sent in a single spike before the `Rate` limit is enforced.

### Conversion to Multus Annotations
The `virt-controller` will act as an abstraction layer, injecting the API configuration directly into the pod's Multus annotation. 

When a VM defines a bandwidth limit on a secondary interface, `virt-controller` will translate the parameters and append the bandwidth arguments to the JSON payload of the `k8s.v1.cni.cncf.io/networks` annotation on the `virt-launcher` pod before the pod boots.

### Validation
To ensure system stability and predictable behavior, the KubeVirt API validation webhook will enforce the following:
1. **Primary Network Rejection:** The webhook will explicitly reject the `bandwidth` configuration if it is applied to the default pod network (e.g., `masquerade` without a Multus NAD), as primary network shaping is currently unsupported.
2. **Standard Formatting:** Ensure the rate and burst values conform to standard Kubernetes resource quantity formats and that burst values are mathematically sound relative to the rate.

### Feature Gate
Initial implementation is considered Alpha. A dedicated `NetworkBandwidthLimiting` feature gate is required to be enabled in the KubeVirt CR to use this API during the experimental phase.

## Scalability
This feature introduces negligible overhead to the control plane. The `virt-controller` translation occurs only during pod creation. At the host level, standard CNI bandwidth plugins utilize Linux `tc` token bucket filters, which are highly optimized. 

## Update/Rollback Compatibility
As this is a new API field guarded by a feature gate, there are no backward compatibility issues. Disabling the feature gate will cause the API to ignore the `bandwidth` block on new VM creations. Existing running VMs with applied bandwidth limits will maintain their CNI constraints until restarted.

## Testing
* **Unit Tests**: API validation webhook logic (ensuring primary networks are rejected) and `virt-controller` annotation generation (ensuring the Multus JSON array is constructed and merged correctly).
* **E2E Tests**: Spawning a VM with a secondary Multus network and the feature gate enabled, applying a bandwidth limit, and verifying that the `virt-launcher` pod receives the correct Multus annotations and the CNI bandwidth plugin executes successfully.

## API Examples
```yaml
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
          - name: secondary-net
            bridge: {}
            bandwidth:
              ingressRate: "100M"
              ingressBurst: "10M"
              egressRate: "50M"
              egressBurst: "5M"
      networks:
      - name: secondary-net
        multus:
          networkName: my-bridge-nad
```
## Translated Pod Annotation (virt-controller output)
```yaml
metadata:
  annotations:
    k8s.v1.cni.cncf.io/networks: |
      [
        {
          "name": "my-bridge-nad",
          "namespace": "default",
          "bandwidth": {
            "ingressRate": "100M",
            "ingressBurst": "10M",
            "egressRate": "50M",
            "egressBurst": "5M"
          }
        }
      ]
```
