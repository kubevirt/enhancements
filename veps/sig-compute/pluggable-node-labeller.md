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

### Pluggable Capability Provider Interface

- Define a standard interface (e.g., CapabilityProvider) for extracting node virtualization capabilities.
- The node-labeller loads the appropriate provider based on configuration.
- Each provider implements methods to extract and report capabilities (e.g., supported machine types, CPU models, features).

### Integration and Configuration

- Node-labeller is configured to use a specific capability provider (default: Libvirt/QEMU/KVM).
- New providers can be added as plugins and selected via configuration or CRD.

### Backward Compatibility

- If no plugin is specified, node-labeller defaults to the current Libvirt/QEMU/KVM logic.
- No changes required for existing users.

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
