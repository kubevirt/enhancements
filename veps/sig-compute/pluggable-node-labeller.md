# VEP: Pluggable Node-Labeller for KubeVirt

## Release Signoff Checklist

- [ ] Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] Target version is explicitly mentioned and approved
- [ ] Graduation criteria filled

## Overview

This proposal introduces a pluggable architecture for the node-labeller component in KubeVirt. The goal is to decouple node capability detection and labeling from a single virtualization stack implementation, enabling support for alternative virtstacks via well-defined plugin interfaces.

## Motivation

- Enable KubeVirt to support multiple virtualization stacks by allowing node-labeller to consume capabilities from stack-specific providers.
- Reduce hardcoded dependencies on Libvirt/QEMU/KVM in node labeling logic.
- Allow integrators and downstreams to provide custom capability extraction logic for new or proprietary virtualization stacks.

## Goals

- Refactor node-labeller to use a pluggable capability provider interface.
- Allow alternative implementations to supply node capabilities for labeling.
- Preserve backward compatibility with the current Libvirt/QEMU/KVM-based node-labeller.

## Non-Goals

- Refactoring other KubeVirt components for pluggability.
- Implementing plugins for specific alternative virtualization stacks (only the interface and integration points).

## Definition of Users

- Platform engineers and integrators deploying KubeVirt with non-default virtualization stacks.
- Downstream projects needing custom node labeling logic.

## User Stories

- As a KubeVirt integrator, I want to provide a custom node-labeller plugin for my virtualization stack so that node labels reflect the actual capabilities of my environment.
- As a platform admin, I want to switch node-labeller capability extraction without modifying core KubeVirt code.

## Design

### Pluggable Node-Labeller Architecture

In this design, the node-labeller component itself is implemented as a pluggable module. The virt-handler component interacts directly with the node-labeller to obtain node capability information, which it then uses to apply labels to the node. The node-labeller is responsible for querying the underlying virtualization stack, extracting the required capabilities, and exposing them via a well-defined, versioned RPC API. This API is consumed by virt-handler.

#### Node-Labeller RPC API

The node-labeller exposes the following RPC API to virt-handler:

- **GetHypervFeatures**: Returns a list of Hyper-V compatible features exposed by the hypervisor for optimized guest OS functionality.
- **GetSupportedMachineTypes**: Returns a list of machine types supported by the VMM.
- **GetSupportedCpuModels**: Returns a list of named CPU models that the VMM can expose to the VM.
- **GetHostCpuModelInfo**: Returns the name of the host-model CPU model and the set of additional features required with the host-model CPU.
- **GetSupportedCpuFeatures**: Returns a list of CPU features available on the node.
- **GetNodeTscInfo**: Returns the TSC (Time Stamp Counter) frequency and whether it is scalable.
- **GetNodeSevFeatures**: Returns whether the node supports AMD SEV and SEV+ES.

The API is defined using Protobuf (or similar IDL), versioned with KubeVirt, and is the contract between virt-handler and the node-labeller plugin. The node-labeller implementation is responsible for all virt-stack-specific logic.

#### Example (Protobuf-like) API Definition

```protobuf
service NodeLabeller {
  rpc GetHypervFeatures(Empty) returns (HypervFeaturesResponse);
  rpc GetSupportedMachineTypes(Empty) returns (MachineTypesResponse);
  rpc GetSupportedCpuModels(Empty) returns (CpuModelsResponse);
  rpc GetHostCpuModelInfo(Empty) returns (HostCpuModelInfoResponse);
  rpc GetSupportedCpuFeatures(Empty) returns (CpuFeaturesResponse);
  rpc GetNodeTscInfo(Empty) returns (TscInfoResponse);
  rpc GetNodeSevFeatures(Empty) returns (SevFeaturesResponse);
}
```

### Integration and Configuration

- The node-labeller is deployed as a plugin and selected via configuration or CRD (default: Libvirt/QEMU/KVM).
- New node-labeller plugins can be added to support additional virtualization stacks.

### Backward Compatibility

- If no plugin is specified, the default Libvirt/QEMU/KVM node-labeller is used.
- No changes are required for existing users.

## API Changes

- (TBD) If configuration is exposed via CRD, document the new fields.

## Implementation Phases

1. Define and document the CapabilityProvider interface.
2. Refactor node-labeller to use the interface for capability extraction.
3. Implement the default Libvirt/QEMU/KVM provider.
4. Add plugin loading and configuration logic.

## Open Questions

- What is the best mechanism for plugin discovery and loading (sidecar, dynamic import, etc.)?
- Should the configuration be part of the KubeVirt CRD or a separate resource?

## Feature Lifecycle Phases

### Alpha
- Initial implementation of the pluggable node-labeller interface and default provider.

### Beta
- Feedback-driven improvements, support for at least one alternative provider.

### GA
- Stable interface, documentation, and upgrade path.
