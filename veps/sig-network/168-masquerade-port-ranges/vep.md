# VEP 168: Masquerade Port Ranges

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This document describes a mechanism for interfaces of type `masquerade` to forward contiguous port ranges to the VM guests.

## Motivation

Currently, when using an interface of type `masquerade`  all traffic gets forwarded by default to the VM guest running inside it. The only way to achieve a different behaviour is to configure specific ports in the VM/VMI template (`spec.domain.devices.interfaces.ports` field that gets evaluated when using the masquerade core network binding mentioned before). However, when using KubeVirt to host general purpose desktop VMs, you may not know a priori which ports to forward while still wanting to keep the flexibility of a large set of forwarded ports already available to use. This way we avoid the need to update the VM template and the required guest restart, with all the associated problems (e.g. losing the entire state when using ephemeral instances)

## Goals

- Support configuring entire port ranges to be forwarded to VM guests instead of single specific ones

## Definition of Users

- Any user who wants to expose services or applications running inside a VM guest

## User Stories

- As a Virtual Machine user, I want to specify multiple contiguous ports to forward without having to specify every single one

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## API Design

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

The `PortRanges` field will be added to the existing `Interface` type and made mutually exclusive with the already present `Ports` field:

```go
type Interface struct {
  ...
  // List of ports to be forwarded to the virtual machine.
  // Mutually exclusive with the portRanges field.
	Ports []Port `json:"ports,omitempty"`
  // List of ranges of ports to be forwarded to the virtual machine.
  // Mutually exclusive with the ports field.
	PortRanges []PortRanges `json:"portRanges,omitempty"`
  ...
}
```

## API Examples


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
              portRanges: # port ranges to forward
                - start: 1000
                  end: 8000
                  protocol: TCP
                - start: 1000
                  end: 8000
                  protocol: UDP
      networks:
      - name: red
        pod: {}
```

## Alternatives

An alternative to adding a new field to the `Interface` type, we could instead extend the already present `Ports` field by modifying the `Port` type

### API Design

A new `PortRange` type will be added to the KubeVirt API:

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
### API Examples


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
              ports: # port ranges to forward
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

## Scalability

The proposed modification poses no scalability problems: it can actually make forwarding faster as multiple contiguous ports can be compacted in a single atomic NATting rule instead of multiple ones.

## Update/Rollback Compatibility

**Upgrade:**

- New API field `portRange` is additive and optional
- Existing VMs are unaffected

## Functional Testing Approach

### Unit Tests

- API validation for PortRanges
  - Valid port numbers
  - No overlapping ranges
  - Mutual exclusivity with the Ports field
- Checks for the updated masquerade rules creation logic

### Functional Tests

- PortRanges validation errors are properly reported

### Integration Tests

- Invalid or not correct fields in the templates are rejected with appropriate errors.

## Implementation History

The modification could be carried out in the following steps:
- Schema modifications
- Addition of exhaustive tests of the Schema Validators and the Masquerade Rules
- Modifications to the Schema to Masquerade Rules converters
- Adaptation and extensions of the Schema Validators
- Updates to the Official Documentation

## Graduation Requirements

Due to the small size of the modification, the entire feature could be developed in either one or two stages (Beta and GA). The backward compatibility of the feature can let us avoid the usage of Feature Gates in the Beta phase and we could leave the Documentation updates to the GA phase.
