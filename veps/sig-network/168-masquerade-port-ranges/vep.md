# VEP 168: Masquerade Port Ranges

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version: 

## Release Signoff Checklist

Items marked with (R) are required _prior to targeting to a milestone / release_.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved
- [] (R) Beta target version is explicitly mentioned and approved
- [] (R) GA target version is explicitly mentioned and approved

## Overview

This document tries to make forwarding more efficient when a user wants to forward contiguous port ranges to the VM guests using `masquerade` interfaces. Currently, the only possibility is to specify an attribute in the VM spec for each single port which can be impractical and hard to maintain from a user perspective. Additionally, this situation also translates into numerous separate nftables rules, leaving efficiency on the table. 

## Motivation

For some use cases (e.g. using KubeVirt to host general purpose desktop VMs), a user may need broad but bounded forwarding: with the current approach they must enumerate many individual ports, which is impractical to author and maintain.

Presently, when using an interface of type `masquerade` all traffic gets forwarded by default to the VM guest running inside it with an automatic "catch-all" NFTable rule. The only supported way to limit forwarding is the inclusion model in `spec.domain.devices.interfaces.ports`, where each allowed port must be listed explicitly, translating into a single forwarding rule. There is no compact way to define large contiguous allowed intervals and no exclude model ("forward everything except X") either. This PR tries to improve on the former, making the latter redundant.

## Goals

- Enable compact broad-but-bounded forwarding to VM guests when the default catch-all is too permissive and single ports are too restrictive
- Reduce to a minimum the number of nftables rules required making forwarding more efficient for contiguous ports

## Non Goals

- Allowing combined use of `Ports` and `PortRanges` in the initial implementation
- Supporting `PortRanges` on non-masquerade bindings
- Supporting `PortRanges` on secondary multus interfaces
- Forwarding ports to the `virt-launcher` pod itself instead of the guest VMI
- Mutual exclusivity with the usage of Istio service mesh

## Definition of Users

- Any user who wants to expose services or applications running inside a VM guest

## User Stories

- As a User running a general-purpose VM, I want to forward a broad set of ports to the guest for flexibility as port changes require a VM spec update and an associated guest restart. Having to list every allowed port individually is both cumbersome and impractical to maintain.

- As a Cluster Administrator, I want port forwarding to scale efficiently so that forwarding thousands of ports doesn't generate thousands of individual nftables rules, increasing the load on my nodes.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

A new `PortRange` type will be added to the KubeVirt API:

```go
// PortRange represents a range of ports to expose from the virtual machine.
// Default protocol TCP.
// The start and end fields are mandatory
type PortRange struct {
  // Protocol for ports. Must be UDP or TCP.
  // Defaults to "TCP".
  // +optional
  Protocol string `json:"protocol,omitempty"`
  // First port of the range to expose for the virtual machine.
  // This must be a valid port number, 0 < x < 65536.
  Start int32 `json:"start"`
  // Last port of the range to expose for the virtual machine.
  // This must be a valid port number, 0 < x < 65536.
  // Must be greater than or equal to start field.
  End int32 `json:"end"`
}
```

The `PortRanges` field will be added to the existing `Interface` type and made mutually exclusive with the already present `Ports` field during the Alpha release:

```go
type Interface struct {
  ...
  // List of ports to be forwarded to the virtual machine.
  // Mutually exclusive with the portRanges field.
  Ports []Port `json:"ports,omitempty"`
  // List of ranges of ports to be forwarded to the virtual machine.
  // Mutually exclusive with the ports field.
  PortRanges []PortRange `json:"portRanges,omitempty"`
  ...
}
```

### Validation checks

The following validation rules will apply:

- `PortRanges` and `Ports` cannot be used together in the same interface (Note: This mutual exclusivity constraint will be enforced for the Alpha release (v1.9) to simplify implementation; it may be relaxed in Beta or later).
- `PortRanges` can only be used on interfaces of type `masquerade` (not on other bindings).
- Use of `PortRanges` is not allowed on secondary multus interfaces (this feature is only for the pod interface).
- For each range, the `start` field must be less than or equal to `end`.
- If two or more ranges are specified, they must not overlap **unless they use different protocols** (e.g. a TCP range can overlap with a UDP range, but two TCP ranges must not overlap).
- All port numbers must be valid (0 < x < 65536).
- Additional checks may be added for security and consistency as needed.

#### Note on mutual exclusivity

This decision is deliberate: mutual exclusivity between `Ports` and `PortRanges` will be required during Alpha (v1.9) to keep implementation and validation simple and predictable. The team will revisit this constraint during Beta based on Alpha feedback and may allow combined usage (for example: single ports together with ranges) in a later release.

### NFTables rules conversion

The existing logic for converting ports specified in the VM/I specs to nftables rules (currently in `pkg/network/setup/netpod/masquerade/masquerade.go`) will be updated to handle port ranges. For each specified range, a single nftables rule will be created to cover the interval, optimizing the number of rules compared to specifying many single ports.

### Feature Gate

The feature will be guarded by a Feature Gate during Alpha and Beta.
Proposed Feature Gate name: `MasqueradePortRanges`.

## API Examples

The following example demonstrates a configuration using both fields. Note that in the Alpha phase, validation will enforce mutual exclusivity, preventing this specific combination. This example also illustrates that ranges of different protocols (TCP and UDP) are allowed to overlap.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: red
              masquerade: {}
              ports:
                - name: ssh
                  port: 22
                  protocol: TCP
              portRanges:
                - start: 1000
                  end: 8000
                  protocol: TCP
                - start: 1500 # Overlap with TCP range is allowed because protocol is UDP
                  end: 9000
                  protocol: UDP
      networks:
        - name: red
          pod: {}
```

### Validation error example (Alpha)

The following shows an invalid VM manifest that uses both `ports` and `portRanges` on the same interface (not allowed during Alpha), and an example of the API server validation error that should be returned when attempting to create it.

Invalid manifest:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: invalid-vm
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: red
              masquerade: {}
              ports:
                - name: ssh
                  port: 22
                  protocol: TCP
              portRanges:
                - start: 1000
                  end: 2000
                  protocol: TCP
      networks:
        - name: red
          pod: {}
```

Example API server validation output:

```text
Error from server (Invalid): error when creating "invalid-vm.yaml": VirtualMachine.kubevirt.io "invalid-vm" is invalid: spec.template.spec.domain.devices.interfaces[0]: Invalid value: interface: "ports" and "portRanges" cannot be used together while the MasqueradePortRanges feature gate enforces mutual exclusivity in Alpha (v1.9)
```

## Alternatives

An alternative to adding a new field to the `Interface` type, we could instead extend the already present `Ports` field by modifying the `Port` type.

### API Design

```go
// Port represents either a single port or a range of ports to expose from the virtual machine.
// Default protocol TCP.
// The port field is mandatory.
type Port struct {
  // If specified, this must be an IANA_SVC_NAME and unique within the pod. Each
  // named port in a pod must have a unique name. Name for the port that can be
  // referred to by services.
  // Valid only for single ports.
  // +optional
  Name string `json:"name,omitempty"`
  // Protocol for the ports to expose. Must be UDP or TCP.
  // Defaults to "TCP".
  // +optional
  Protocol string `json:"protocol,omitempty"`
  // Number of port to expose for the virtual machine.
  // This must be a valid port number, 0 < x < 65536.
  Port int32 `json:"port"`
  // When specified, the range of ports [port, end] is exposed.
  // This must be a valid port number, 0 < x < 65536.
  // Must be greater than or equal to the port field.
  End int32 `json:"end,omitempty"`
}
```

### API Examples (Extended Port)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: red
              masquerade: {}
              ports: # single ports and port ranges to forward
                - port: 22
                  name: ssh
                  protocol: TCP
                - port: 1000
                  end: 8000
                  protocol: UDP
      networks:
        - name: red
          pod: {}
```

### ExcludedPorts Approach

An alternative is to allow the user to specify a broad set of forwarding rules and explicitly exclude certain ports or ranges. There are two viable designs:

1. **Dedicated Fields:** Add `excludedPorts` (for single ports) and `excludedPortRanges` (for ranges) as separate fields. This is explicit and keeps schemas clear.
2. **Unified Field:** Add a single `excludedPorts` list whose entries can be either a single-port object or a range object (similar to the extended `Port`/`PortRange` structures). This is simpler for users who want one compact field.

Examples (unified `excludedPorts` approach):

```yaml
interfaces:
  - name: red
    masquerade: {}
    excludedPorts:
      - port: 8080
      - start: 1000
        end: 2000
        protocol: TCP
```

Mutual Exclusivity with Inclusion:

Regardless of chosen representation, the Exclusion model is mutually exclusive with the Inclusion model: you must choose either to explicitly list allowed ports (`ports` / `portRanges`) or to start from an open/default policy and list exclusions (`excludedPorts` / `excludedPortRanges`), but not both on the same interface. The two models represent opposite configuration paradigms (allow-list vs. deny-list) and cannot be combined for a single interface.

## Scalability

The proposed modification poses no scalability problems: it can actually make forwarding faster as multiple contiguous ports can be compacted in a single atomic NATting rule instead of multiple ones.

## Update/Rollback Compatibility

**Upgrade:**

- New API field `portRanges` is additive and optional
- Existing VMs are unaffected

## Functional Testing Approach

### Unit Tests

- API validation for PortRanges
  - Valid port numbers
  - Overlap checks (disallowed for same protocol, allowed for different)
  - Mutual exclusivity with the Ports field
- Checks for the updated masquerade rules creation logic

### Functional Tests

- PortRanges validation errors are properly reported

### Integration Tests

- Invalid or not correct fields in the templates are rejected with appropriate errors.

## Implementation History

- 2026-01-03: Initial VEP draft created.
- 2026-04-08: Added explicit validation checks and nftables rules conversion subsection under Design.
- 2026-04-08: Added Alpha/Beta/GA graduation plan and Feature Gate strategy.

## Graduation Requirements

### Alpha (v1.9)

- [ ] Feature gate guards all code changes
- [ ] Initial implementation of `PortRanges`, mutually exclusive with `Ports`
- [ ] Validation and forwarding of ranges for masquerade pod interface only
- [ ] Initial feedback collection from users

### Beta (v1.10)

- [ ] Feature remains protected by a feature gate
- [ ] Extended validation and functional coverage based on Alpha feedback
- [ ] Decision recorded on whether combined `Ports` + `PortRanges` usage should be introduced in this phase or deferred

### GA (v1.11)

- [ ] Feature gate removed and feature enabled by default
- [ ] Graduation criteria fully met with stable behavior
- [ ] Documentation finalized and backward compatibility validated
