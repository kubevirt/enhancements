# VEP: Pluggable Node-Labeller for KubeVirt

## Release Signoff Checklist

- [ ] Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] Target version is explicitly mentioned and approved
- [ ] Graduation criteria filled

## Overview

This proposal introduces a pluggable architecture for the node-labeller component in KubeVirt. The goal is to decouple node capability detection and labeling from a single virtualization stack implementation, enabling support for alternative virtstacks via well-defined plugin interfaces.

## Motivation

- Enable KubeVirt to support multiple virtualization stacks by making the node-labeller implementation pluggable per virtstack.
- Reduce hardcoded dependencies on Libvirt/QEMU/KVM in node labeling logic.
- Allow integrators and downstreams to provide stack-specific node-labeller plugins for new or proprietary virtualization stacks.

## Goals

- Define a versioned RPC contract between virt-handler and pluggable node-labeller plugins.
- Allow multiple node-labeller plugins to run on the same node when multiple virtstacks are present.
- Ensure labels emitted for one virtstack cannot overwrite labels emitted for another virtstack.
- Preserve backward compatibility with the current Libvirt/QEMU/KVM-based node-labeller.

## Non-Goals

- Refactoring other KubeVirt components for pluggability.
- Defining arbitrary label schemas beyond the set of KubeVirt API-aligned capabilities for a given release.
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

#### Plugin Registration and Discovery

- Each node-labeller plugin registers with virt-handler through a well-known UNIX socket, following a device-plugin-like registration flow.
- Registration includes plugin identity, the associated virtstack ID, supported RPC API version, and the plugin endpoint socket.
- virt-handler maintains an in-memory registry of active node-labeller plugins per node, keyed by virtstack ID.
- virt-handler establishes RPC connections to each registered plugin endpoint and periodically reconciles registration state (add/update/remove) based on plugin liveness.

#### Label Namespacing and Collision Avoidance

- virt-handler applies labels returned by each plugin using a deterministic prefix derived from that plugin's virtstack ID declared in `KubeVirtConfiguration`.
- Label keys from different virtstacks are therefore namespaced and cannot overwrite one another.
- The prefixing logic is enforced in virt-handler, so plugin implementations do not control global label key space.
- If two plugins attempt to register the same virtstack ID on a node, virt-handler treats it as a conflict and keeps only one active registration according to deterministic conflict resolution rules.

### Integration and Configuration

- `KubeVirtConfiguration` is the authoritative declaration point for virtstacks expected in the cluster.
- The configuration includes a list of virtstack entries, each with a unique virtstack ID and node-labeller plugin image/reference.
- Based on this declaration, KubeVirt deploys the corresponding node-labeller plugins to nodes where they are applicable.
- The default Libvirt/QEMU/KVM virtstack remains available when no additional virtstack declarations are provided.

### Deployment Model for Node-Labeller Plugins

For `N` declared virtstacks in `KubeVirtConfiguration`, virt-operator will reconcile `N` dedicated DaemonSets, one DaemonSet per virtstack. Each DaemonSet runs exactly one node-labeller plugin Pod on each eligible node.

This yields the following relationship:

- `N` virtstack declarations
- `N` node-labeller plugin DaemonSets
- Up to `N` node-labeller plugin Pods per node (subject to node eligibility)

#### Why This Model Fits Kubernetes Best

- Native scheduling semantics: DaemonSet provides the required one-Pod-per-node behavior.
- Failure isolation: a crash loop in one virtstack plugin does not restart or block others.
- Independent lifecycle: image updates and rollbacks are performed per virtstack plugin.
- Reconciliation clarity: virt-operator has a direct, declarative mapping from CRD entries to DaemonSet objects.
- Operability: each plugin has distinct Pod identity, events, and status for troubleshooting.

#### Implementation Shape

- virt-operator watches `KubeVirtConfiguration` and reconciles one DaemonSet per declared virtstack.
- Each plugin Pod mounts host paths required for:
  - Registration against virt-handler's well-known UNIX socket.
  - Serving the plugin's own endpoint UNIX socket.
  - Read-only host introspection paths needed by the plugin.
  - The virtstack-specific hypervisor device declared in configuration (for example, `/dev/kvm`).
- On startup, each plugin registers the following with virt-handler:
  - `virtstackID`
  - supported RPC API version
  - plugin endpoint socket path
- virt-handler connects to all registered plugin endpoints on the node and executes the `NodeLabeller` RPCs.
- Label writes are always prefixed by virtstack ID to avoid collisions.
- If duplicate registrations are observed for the same virtstack ID on a node, virt-handler applies deterministic conflict resolution and keeps a single active registration.

#### Declaring Hypervisor Device Access

- Each virtstack declaration in `KubeVirtConfiguration` includes the host device path required by that virtstack's node-labeller plugin.
- virt-operator projects that path into the reconciled DaemonSet as a `hostPath` volume of type `CharDevice` and mounts it into the plugin container at the same path.
- This keeps device requirements declarative and per-virtstack, while avoiding hardcoded device paths in virt-operator logic.

#### CRD YAML Skeleton

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    virtualizationProfiles:
    - id: qemu-kvm
      virtualizationComponents:
        hypervisorDevice: /dev/kvm
      nodeLabeller:
        image: quay.io/example/node-labeller-qemu-kvm:v1.0.0
        apiVersion: v1alpha1
        nodeSelector:
          kubernetes.io/os: linux
        tolerations:
        - key: node-role.kubernetes.io/control-plane
          operator: Exists
          effect: NoSchedule
    - id: cloudhypervisor-kvm
      virtualizationComponents:
        hypervisorDevice: /dev/kvm
      nodeLabeller:
        image: quay.io/example/node-labeller-ch-kvm:v1.0.0
        apiVersion: v1alpha1
        nodeSelector:
          kubernetes.io/os: linux
```

#### DaemonSet YAML Skeleton (Reconciled per Virtstack)

```yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kubevirt-node-labeller-qemu-kvm
  namespace: kubevirt
  labels:
    kubevirt.io/component: node-labeller-plugin
    kubevirt.io/virtstack-id: qemu-kvm
spec:
  selector:
    matchLabels:
      kubevirt.io/component: node-labeller-plugin
      kubevirt.io/virtstack-id: qemu-kvm
  updateStrategy:
    type: RollingUpdate
  template:
    metadata:
      labels:
        kubevirt.io/component: node-labeller-plugin
        kubevirt.io/virtstack-id: qemu-kvm
    spec:
      serviceAccountName: kubevirt-node-labeller-plugin
      nodeSelector:
        kubernetes.io/os: linux
      containers:
      - name: node-labeller-plugin
        image: quay.io/example/node-labeller-qemu-kvm:v1.0.0
        args:
        - --virtstack-id=qemu-kvm
        - --hypervisor-device=/dev/kvm
        - --register-socket=/var/lib/kubevirt/plugins/registration.sock
        - --endpoint-socket=/var/lib/kubevirt/plugins/qemu-kvm.sock
        volumeMounts:
        - name: plugin-dir
          mountPath: /var/lib/kubevirt/plugins
        - name: sysfs
          mountPath: /sys
          readOnly: true
        - name: hypervisor-device
          mountPath: /dev/kvm
      volumes:
      - name: plugin-dir
        hostPath:
          path: /var/lib/kubevirt/plugins
          type: DirectoryOrCreate
      - name: sysfs
        hostPath:
          path: /sys
          type: Directory
      - name: hypervisor-device
        hostPath:
          path: /dev/kvm
          type: CharDevice
```

### Backward Compatibility

- If no virtstack plugins are declared, the default Libvirt/QEMU/KVM node-labeller path is used.
- No changes are required for existing users.

## API Changes

- Extend `KubeVirtConfiguration` with virtstack declarations used for node-labeller plugin deployment and virtstack ID assignment.
- Define a versioned `NodeLabeller` RPC API contract used by virt-handler.
- Document registration payload fields for plugin-to-virt-handler registration over the well-known UNIX socket.

## Implementation Phases

1. Define and document the versioned `NodeLabeller` RPC API.
2. Implement plugin registration and discovery in virt-handler using the well-known UNIX socket.
3. Add `KubeVirtConfiguration` virtstack declarations and deployment wiring for node-labeller plugins.
4. Implement label prefixing in virt-handler using virtstack IDs and add collision/conflict handling.
5. Keep default Libvirt/QEMU/KVM behavior as the fallback path.

## KubeVirtConfiguration Validation

To prevent ambiguous plugin deployment and label namespacing behavior, this proposal includes a new validating webhook for the `KubeVirtConfiguration` CRD.

- The webhook validates that all virtualization profile IDs declared in configuration are unique.
- If duplicate IDs are detected, the admission request is rejected with a clear validation error.
- This guarantees a one-to-one mapping between virtstack ID and node-labeller plugin deployment semantics.

## Virtstack Packaging and Node-Level Plugin Consolidation

Each node-labeller plugin image is expected to include the user-space components required for its virtstack-specific capability discovery workflow. For example, the default `libvirt-qemu-kvm` node-labeller plugin image includes the required Libvirt and QEMU binaries, while the KVM device is provided from the host via the declared hypervisor device mount.

Per virtstack, the deployment remains singleton per node (one Pod instance per node for that virtstack's DaemonSet), which provides deterministic behavior for registration and label production.

This deployment pattern is also aligned with the expected plugin needed for runtime adjustment operations consumed by virt-handler. As a follow-on design direction, these two node-local capabilities (node labeling and runtime adjustment) can be consolidated into a single node-level plugin per virtstack with multiple RPC services exposed over the same registration channel.

## Open Questions

- Should plugin registration be node-local only, or should virt-handler surface registration status into a cluster-visible condition for operability?
- What should the exact conflict-resolution policy be when duplicate registrations for the same virtstack ID are observed?

## Feature Lifecycle Phases

### Alpha
- Initial implementation of the `NodeLabeller` RPC contract, plugin registration flow, and default Libvirt/QEMU/KVM node-labeller plugin path.

### Beta
- Feedback-driven hardening of registration/discovery and support for at least one additional virtstack-specific node-labeller plugin.

### GA
- Stable API, registration semantics, and operational guidance for multi-virtstack deployments.
