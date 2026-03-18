# VEP #235: VM Network Bandwidth Limiting

## Release Signoff Checklist

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [x] (R) Target version is explicitly mentioned and approved

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

## Overview
This VEP proposes adding Network Quality of Service (QoS) support to KubeVirt by enabling bandwidth limiting for Virtual Machine (VM) network interfaces that use tap-based bindings (masquerade, bridge). This allows users to specify bandwidth limits (inbound/outbound) in the VirtualMachine template spec using standard Kubernetes resource quantity format, which are translated to the underlying Libvirt domain configuration and enforced on the VM's virtual network interfaces.

## Motivation
Currently, KubeVirt VMs can consume the full available bandwidth of the host's physical network interface. In multi-tenant or high-density environments, a "noisy neighbor" VM can saturate the link, causing high latency or packet loss for other workloads on the same node. There is a requirement for a native KubeVirt API to enforce fair network resource distribution.

## Goals
- Enable per-interface inbound and outbound traffic shaping for VMs.
- Provide a native Kubernetes-style API using standard resource units for network QoS.
- Ensure cluster stability by preventing individual guests from monopolizing host network resources.

## Non Goals
- Support for dynamic "live" updating of bandwidth without a VM restart during the Alpha phase.
- Implementation of CNI-level shaping (this focuses strictly on the `virt-launcher` / Libvirt layer).
- Support for Explicit Congestion Notification (ECN) marking to signal network congestion, as this is currently unsupported within the bandwidth QoS configuration of the Libvirt Domain XML API.

## Definition of Users
- **Cluster Administrators**: Responsible for node health and resource fair-share.
- **Cloud Service Providers**: Delivering tiered networking performance levels to end-users.
- **Users**: End-users or application owners deploying VMs.

## User Stories
- As a **cluster administrator**, I want to limit a non-critical VM's egress traffic so that production-critical services on the same node maintain guaranteed network throughput.
- As a **Cloud Service Provider**, I want to cap the network bandwidth of "Basic Tier" VMs to 100Mbps so that I can differentiate them from "Premium Tier" offerings.
- As a **user**, I want to define my VM's network speed in standard units (e.g., 1Gi) within the VM manifest so I can ensure my specific application has the guaranteed throughput it needs to function correctly.

*(Note: This API exposes the configuration. Enforcing maximum bandwidth policies across a cluster to prevent users from requesting arbitrary limits is expected to be handled by standard Kubernetes policy engines like OPA Gatekeeper or Kyverno).*

## Repos
- `kubevirt/kubevirt`

## Design

### API Change
The implementation introduces a `Bandwidth` field within the existing `Interface` struct. The API accepts `Inbound` and `Outbound` parameters. Because Kubernetes `resource.Quantity` lacks a native time dimension, the interval for rate-based fields is strictly evaluated as **per second**:

* **Average**: The desired average bandwidth rate, measured in **bytes per second** (e.g., `10Mi` evaluates to 10 Mebibytes per second).
* **Peak**: The maximum transmission rate allowed while discharging a burst, measured in **bytes per second**.
* **Burst**: The maximum absolute amount of data, measured in **bytes**, that can be sent at the `Peak` speed in a single spike before the `Average` limit is enforced.

### Conversion to Domain XML
The `virt-launcher` contains a converter that translates KubeVirt specs into Libvirt domain XML. A new builder function will be added to `configureInterfaces`. This builder will parse the `bandwidth` struct, convert the `resource.Quantity` byte values into the Kilobytes/second (KiB/s) format expected by Libvirt, and populate the empty `<bandwidth>` struct in the XML.

### Validation
To ensure system stability and prevent users from applying ignored configurations, the KubeVirt API validation webhook will enforce the following:
1. **Outbound Peak Rejection**: Per Libvirt documentation, the `peak` attribute in the `outbound` element is ignored because underlying Linux ingress filters perform policing (dropping) rather than classful queueing. The webhook will strictly **reject** any request that attempts to define a `peak` value within the `outbound` block.
2. **Unsupported Backends**: Bandwidth limiting relies on host `tc` rules applied to `tap` devices. The webhook will **reject** the `bandwidth` configuration if the interface type is set to `sriov` or `macvtap`, as these bypass the host `tc` layer entirely.
3. **Integer Overflows**: The translation logic includes safety utility guards to prevent negative values or unit conversions that exceed the system's maximum integer size.

### Feature Gate
Initial implementation is considered Alpha. A dedicated `NetworkBandwidthLimiting` feature gate is required to be enabled in the KubeVirt CR to use this API during the experimental phase.

## Scalability
This feature introduces negligible overhead. The `virt-launcher` translation occurs only during VM creation/modification. At the host level, standard Linux `tc` token bucket filters are highly optimized and introduce minimal CPU overhead, even at scale.

## Update/Rollback Compatibility
As this is a new, additive API field guarded by a feature gate, there are no backward compatibility issues. If the feature gate is disabled, the API will simply drop or ignore the `bandwidth` block on new VM creations. Existing running VMs with applied bandwidth limits will maintain their `tc` rules until restarted.

## Testing
* **Unit Tests**: API validation webhook logic (ensuring `outbound.peak` and SR-IOV are rejected), and `virt-launcher` XML builder translation math (ensuring correct KiB/s conversion and overflow protection).
* **E2E Tests**: Spawning a VM with the feature gate enabled, applying a bandwidth limit to a `masquerade` interface, and verifying that the correct `tc` qdisc rules are applied to the `tap` device on the host node.

## Graduation Requirements

### Alpha (v1.9)
- API structures and Feature Gate merged.
- Validation webhooks implemented.
- `virt-launcher` Domain XML translation implemented.
- Unit and basic E2E tests merged.

### Beta
- Feature gate enabled by default.
- E2E tests expanded to cover both `masquerade` and `bridge` bindings.
- No high-severity bugs reported by early adopters.

### GA
- API promoted to `v1`.
- Proven stability across multiple KubeVirt releases.

## API Examples
```yaml
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
          - name: default
            masquerade: {}
            bandwidth:
              inbound:
                average: "1Gi"
                peak: "2Gi"
                burst: "128Mi"
              outbound:
                average: "500Mi"
                burst: "50Mi"
```

## Translated Libvirt Domain XML
```xml
<interface type='network'>
  <bandwidth>
    <inbound average='1048576' peak='2097152' burst='131072'/>
    <outbound average='512000' burst='51200'/>
  </bandwidth>
</interface>
```
