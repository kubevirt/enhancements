# VEP #190: Kubevirt Structured Plugins

Owners:
- @iholder101
- @vladikr

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9. 
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Table of contents

- [Release Signoff Checklist](#release-signoff-checklist)
- [Overview](#overview)
- [Motivation](#motivation)
- [Goals](#goals)
- [Non Goals](#non-goals)
- [Definition of Users](#definition-of-users)
- [User Stories](#user-stories)
- [Repos](#repos)
- [Design](#design)
  - [The Domain Hook](#the-domain-hook)
    - [Domain Hook modes](#domain-hook-modes)
    - [Domain Hook API examples](#domain-hook-api-exmaples)
  - [The Node Hook](#the-node-hook)
    - [Node hook points](#node-hook-points)
    - [Node hook API examples](#node-hook-api-examples)
  - [Admission Policies and Webhooks](#admission-policies-and-webhooks)
    - [Admission options](#admission-options)
    - [API example:](#api-example)
  - [Plugin metadata](#plugin-metadata)
- [API Examples (full version)](#api-examples-full-version)
- [Alternatives](#alternatives)
- [Possible future enhancements](#possible-future-enhancements)
  - [Versioning and Upgrade Path](#versioning-and-upgrade-path)
  - [multi-plugin support](#multi-plugin-support)
- [Scalability](#scalability)
- [Update/Rollback Compatibility](#updaterollback-compatibility)
- [Functional Testing Approach](#functional-testing-approach)
- [Implementation History](#implementation-history)
- [Graduation Requirements](#graduation-requirements)
  - [Alpha](#alpha)
  - [Beta](#beta)
  - [GA](#ga)

## Overview

KubeVirt currently has a gated feature that allows for virtual machine (VM) customization through a sidecar model that modifies raw libvirt XML.
While functional, this approach is very limited (see below), and untrivial to create complex features with.

This proposal is building on the philosophy of frameworks like NRI (Node Resource Interface).
It introduces a structured hooking-based plugin mechanism to replace the existing "sidecar hook" model.
The new mechanism will enable a safer, holistic, maintainable and powerful way of integrating out-of-tree functionality
into KubeVirt.

Not only will this plugin mechanism be able to modify the domain XML in a more structured way, but this proposal adds
node-level hooks to perform operations by virt-handler, allowing privileged modifications to resources like
housekeeping CPUs, PRlimit, or node sockets.

Additionally, we propose integrating Kubernetes' Mutating Admission Policy to extend customization to pod and vmi
specifications, enhancing the flexibility of virtual machine deployments.

## Motivation

Often, KubeVirt features must be implemented through external extensions rather than in-tree changes.
See user stories below for detailed cases where this is needed.

The current hooking mechanism in KubeVirt allows sidecars to directly modify libvirt XML.
While this is helpful, this is an extremely partial and fragile solution that does not allow for
complex features to be implemented completely out of tree.
For example, it lacks the ability to perform node-level (possibly privileged) operations,
change the virt-launcher's resources, modify the VMI's conditions, and more.

This proposal suggests to deprecate and eventually remove the old Sidecar hook approach.

Instead, we will be introducing a complete and full plugin mechanism that will allow for complex features
to be implemented entirely out of tree, while being able to perform node-level operations,
modify objects like pods, VMs and VMIs via admission techniques and much more.
This will be achieved by introducing a `Plugin` Custom Resource Definition (CRD) that will serve as a central
place to capture all plugin-related components, which will let the admin both reason about a plugin easily
and to permit only whatever's acceptable by him via admission policies.

## Goals

The high level goal is to allow complex features to be implemented out-of-tree while integrating well with KubeVirt.

For example, this includes:
- Support a structured way of modifying the `DomainSpec` via plugins connected at well-defined hook points.
- Support two plugin modes: simple JSON/CEL-based and advanced sidecar-based.
- Support modifying Kubernetes objects (e.g. VM, VMI, virt-launcher pod, other pods, etc) via admission policies or webhooks.
  this can be helpful to modify properties like resources, conditions (e.g. migratability), etc.
- Track and depend on Kubernetes Admission Policies and Webhooks as part of a plugin,
  providing a central place to reason about objects modifications and validations.
- Support node-level (possibly privileged) operations in virt-handler via trusted plugins in well-defined hook points.
- Deprecate and eventually remove the `Sidecar` feature gate.

## Non Goals

- Allowing arbitrary, unvalidated XML modifications.
- Provide a helper library/framework for simplifying Plugin development (this should be addressed in a follow-up VEP).

## Definition of Users

- KubeVirt Developer: aims to develop features for KubeVirt.
- Cluster administrator: in charge of managing clusters in production.

## User Stories

- As a cluster admin, I would like to extend or modify KubeVirt in my clusters, but I am not interested in forking
  KubeVirt or rebuilding it due to the maintenance overhead.
- As a KubeVirt developer, I would like to design a feature for advanced VM owners, so that they can test it quickly
  and provide feedback. When it is in good shape, I would like to file a mature VEP with a POC and feedback from
  real users.
- As a KubeVirt developer of a very complex feature, I understand that a VEP would be challenging to review and comprehend.
  To help the community understand my intentions, I wish to build a high-quality POC that demonstrates a limited-scoped
  working feature to serve as a base ground for an enhancement proposal discussion.
- As a KubeVirt developer, I want to be able to develop a feature that the core
  KubeVirt community does not aim to maintain for at least one of these reasons:
  - A niche specialized behavior is needed that does not make sense upstream.
  - Too complex / dangerous / for most use-cases.
  - No expertise in this subject among the community members (no one to maintain it).

## Repos

kubevirt/kubevirt

## Design

The architecture of the new Plugin mechanism will consist of these different components:
**domain hook**, **node hook**, **mutating policies** and **plugin metadata**.

Let's describe them one by one,
then explain the overall CRD structure that centralizes all plugin-related configuration and components.

### The Domain Hook

The proposed mechanism focuses on modifying the `DomainSpec`, a Go struct generated from the VirtualMachineInstance
(VMI) spec, which serves as the internal representation of a VM's desired configuration before conversion to libvirt XML.
Hooks will be executed after the initial `DomainSpec` generation but before the final XML is created.

The current KubeVirt `DomainSpec` struct only includes a subset of the fields available in the full libvirt `DomainSpec`.
To allow the plugin complete control over the generated XML (similar to the legacy Sidecar Hook),
it will be replaced it with the complete, fully-expanded libvirt DomainSpec struct.

#### Domain Hook modes

The domain hook would support two types of hooks:

**Simple hooks**: These are JSON or CEL based hooks.
These hooks are valuable for simple XML changes that do not require complex logic.
These hooks are easily deployable, eliminating the need to write code, provide a container image,
running another sidecar container, etc.

**Advanced (plugin-based) hooks**: In this mode, a sidecar container would run inside `virt-launcher`.
This sidecar container would listen to a defined socket, waiting for requests by virt-launcher.
In pre-defined hook points (e.g. before the domain XML is applied) virt-launcher would send a request to the socket,
providing the `DomainSpec` struct generated by virt-launcher alongside a `DomainHookContext` struct that will contain
extra information.

Currently, we think of a single hook point, right after the domain XML is generated but before it's handed to libvirt.
Moving forward we can support more hook points if needed.

#### Domain Hook API examples

<ins>Simple Hooks</ins>:

JsonPatch based simple hook:
```yaml
domainHooks:
  - type: JsonPatch
    operations:
      - op: add
        path: /spec/devices/disks
        value:
          name: monitoring-disk
          disk:
            bus: virtio
```

CEL based simple hook:
```yaml
domainHooks:
  - type: ApplyConfiguration
    expression: |
      object.spec.devices.disks + [
        {
          "name": "monitoring-disk",
          "disk": {
            "bus": "virtio"
          }
        }
      ]
```

<ins>Advanced Hooks</ins>:

```yaml
domainHooks:
  - type: Plugin
    socketPath: "/var/run/kubevirt-plugin/my-cool-plugin/my-plugin-socket.sock"
```

Note: The socket would need to reside under `/var/run/kubevirt-plugin/<my-plugin-name>`,
which will be defined as a shared volume with the compute container.
The plugin's container will have visibility only to its plugin folder in an isolated way.

### The Node Hook

To support node-level customizations, hooks can also define operations triggered by the virt-handler pod.
These are invoked via gRPC (or ttRPC, as used by NRI) calls from virt-handler to a plugin server
(a DaemonSet running on each node).
The communication is via a socket (its path is defined by the `Plugin` CR).
The existence of a socket marks the readiness of the plugin - similarly to NRI plugins.

The node hooks will not be running by virt-handler directly, but instead will be triggered by virt-handler via an RPC
mechanism to be run inside the plugin's DaemonSet.
This approach gives the cluster-admin the flexibility to decide (via validating admission policies) what are the acceptable
resources and capabilities that the plugin DaemonSet will be defined with.
It also delegates the responsibility of managing resources to the plugin author.

These hooks are invoked at specific points in virt-handler’s controllers.
Node hooks cannot modify `DomainSpec` or XML directly but can influence runtime behavior.
Plugins can implement both domain and node hooks for cohesive functionality.

Similarly to the domain hooks, `virt-handler` communicates with the relevant socket at pre-defined hook points.
It provides a `NodeHookContext` struct to provide context to the plugin's DaemonSet.

#### Node hook points

- **PreVMStart**: Before VM launch (e.g., setup node devices/network; aligns with vmUpdateHelperDefault).
- **PostVMStart**: After VM is running (e.g., verify node resources).
- **PreMigrationSource**: Before migration from source node (e.g., prepare sockets; aligns with migrateVMI in migration-source.go).
- **PostMigrationSource**: After migration completes on source (e.g., cleanup; aligns with handleSourceMigrationProxy).
- **PreMigrationTarget**: Before migration to target node (e.g., setup target sockets; aligns with prepareMigrationTarget in migration-target.go).
- **PostMigrationTarget**: After migration completes on target (e.g., repair/verify; aligns with finalizeMigration).
- **OnVMStop**: When VM stops (e.g., cleanup node resources; aligns with processVmUpdate on shutdown).
- **PostVMStop**: Called periodically.

The plugin would have to explicitly mention which hook points it wishes to register for.

#### Node hook API examples

```yaml
nodeHooks:
  - mode: Plugin
    socket: /var/run/my-node-socket.sock
    permittedHooks:
    - PreVMStart
    - PostMigrationSource
selector:
  matchLabels:
    use-plugin: my-plugin
  matchFields:
    - key: metadata.name
      operator: Prefix
      value: vm-
  conditions: # CEL-based
    - "vmi.status.phase == 'Running'"
    - "vmi.status.conditions.exists(c, c.type == 'Ready' && c.status == 'True')"
    - "vmi.status.conditions.exists(c, c.type == 'LiveMigratable' && c.status == 'True')"
```

### Admission Policies and Webhooks

In order to provide pod or VMI level modifications and validations,
the new plugin mechanism would be able to track and depend on Kubernetes
[Mutating Admission Policies](https://kubernetes.io/docs/reference/access-authn-authz/mutating-admission-policy/)
and [Validation Admission Policies](https://kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/).
By utilizing these admission policies, we would support providing a declarative policy to modify and/or validate VMIs
and pods which would be performed on the API server's side.

The fact that admission policies/webhooks are listed in the `Plugin` CR has the following advantages:
1. It provides a centric place to holistically understand every plugin component.
2. It allows the KubeVirt plugin mechanism to track these objects and consider the plugin ready only when they are
deployed and ready for action.

#### Admission options

This VEP proposes to use admission mutations and validations according to the amount of complexity needed.
It is recommended to use the least complex method whenever possible.

These admission policies/webhooks will be tracked by KubeVirt's plugin mechanism,
but a different component (e.g. the job/controller that implements the plugin) will be responsible to deploy and delete them.

<ins>Admission policies</ins>:

Admission policies can be used to mutate or validate an object using a declarative model that runs on the API server.
This is preferred over admission webhooks whenever possible.

For these cases, the plugin can reference admission policies by name and namespace.
The plugin mechanism will verify that the referenced policies are ready before considering
the plugin to be fully ready to use.

<ins>Admission webhooks</ins>:

Since admission policies are purely CEL (or JSONPatch) based, they are more limited than admission webhooks.
For example, they cannot make API calls or perform a chain of statements - only a single expression.
Therefore, in complex scenarios, an admission webhook would also be supported.
Similarly to admission policies, the plugin can reference webhooks by name.
The plugin mechanism will verify that the referenced webhooks are ready before considering
the plugin to be fully ready to use.

#### API example:

```yaml
spec:
  mutatingAdmissionPolicies:
    - name: "my-mutating-policy-1"
  validatingAdmissionPolicies:
    - name: "my-validating-policy-1"
    - name: "my-validating-policy-2"
  mutatingAdmissionWebhooks:
    - name: "my-mutating-webhook-1"
    - name: "my-mutating-webhook-2"
  validatingAdmissionWebhooks:
    - name: "my-validating-webhook-1"
```

### Plugin metadata

Each plugin would have to provide some basic metadata about itself.

The main idea here goes beyond specifying a unique name and identity to the plugin,
but also set the ground for providing an upgrade path, versioning, multi-plugin support, dependency between plugins and more.
See [possible plans for the future](#possible-future-enhancements) below.

These are the basic fields every plugin would need to populate:
- name:  a unique identifier for the plugin.
- Version: Specifies the plugin’s version.
- failureStrategy: Defines the behavior when a plugin fails. `Fail` (default) blocks the operation, `Ignore` logs the error and continues.
- timeout: The maximum duration to wait for a plugin response before considering it failed (e.g. `30s`, `2m`). Defaults to `30s`.

## API Examples (full version)

```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: monitoring-hook
spec:
  PluginMetadata:
    name: "best-plugin-ever"
    version: "0.0.1"
    failureStrategy: Fail
    timeout: 30s

  domainHooks:
  # Simple hook: JsonPatch
  - type: JsonPatch
    operations:
      - op: add
        path: /spec/devices/disks
        value:
          name: monitoring-disk
          disk:
            bus: virtio
  # Simple hook: CEL-based ApplyConfiguration
  - type: ApplyConfiguration
    expression: |
      object.spec.devices.disks + [
        {
          "name": "extra-disk",
          "disk": {
            "bus": "virtio"
          }
        }
      ]
  # Advanced hook: Plugin sidecar
  - type: Plugin
    socketPath: "/var/run/kubevirt-plugin/my-cool-plugin/my-plugin-socket.sock"

  mutatingAdmissionPolicies:
    - name: "my-mutating-policy-1"
  validatingAdmissionPolicies:
    - name: "my-validating-policy-1"
    - name: "my-validating-policy-2"
  mutatingAdmissionWebhooks:
    - name: "my-mutating-webhook-1"
    - name: "my-mutating-webhook-2"
  validatingAdmissionWebhooks:
    - name: "my-validating-webhook-1"

  nodeHooks:
    - mode: Plugin
      socket: /var/run/my-node-socket.sock
      permittedHooks:
      - PreVMStart
      - OnVMStop
      - PostVMStop

  selector:
    matchLabels:
      use-plugin: my-plugin
    matchFields:
      - key: metadata.name
        operator: Prefix
        value: vm-
    conditions: # CEL-based
      - "vmi.status.phase == 'Running'"
      - "vmi.status.conditions.exists(c, c.type == 'Ready' && c.status == 'True')"
      - "vmi.status.conditions.exists(c, c.type == 'LiveMigratable' && c.status == 'True')"
```

## Alternatives

One of the obvious alternatives to this approach is forking KubeVirt in order to provide out-of-tree functionality.

While a fork is possible, the maintenance price of using it is huge,
mainly because the core KubeVirt is not aware of the made changes,
hence the added features can go through rough rebase conflicts, which is bad,
or be completely broken by new KubeVirt logic, which is worse.

## Possible future enhancements

These are ideas that we are not currently aiming to implement.
They are left here for a reference for future discussions.

* Version Incompatibility: XML changes may break with updates to KubeVirt or libvirt.
Plugins declare version constraints (kubeVirtVersionCondition, libvirtVersionCondition) and upgrade paths,
ensuring compatibility across versions.
This is an example to be considered:
```yaml
spec:
  PluginMetadata:
    name: "best-plugin-ever"
    version: "0.0.1"
    failureStrategy: Fail
    timeout: 30s
    kubeVirtVersionCondition: "version > 1.5"
    libvirtVersionCondition: "version > 9.0.0"
    upgradePaths:
      - KubeVirtVersion: 1.7
        pluginImageTag: 1.7
      - KubeVirtVersion: 1.8
        pluginImageTag: 1.8
    dependsOn:
      - "another-cool-plugin"
    preservePaths: "domainspec.devices.disks"
```
* Better Auditing: Changes are not tracked, making debugging difficult.
Consider for modifications to be logged via Kubernetes events for traceability.
* Support multi-plugins: dependencies, conflict detection, etc.

### Versioning and Upgrade Path

With the following fields the plugin system will enable to properly define version constraints and a planned upgrade path:
- kubeVirtVersionCondition: a CEL based condition constraint for KubeVirt's version.
- libvirtVersionCondition: a CEL based condition constraint for Libvirt's version.
- upgradePaths: This field will be a list of values mapping a KubeVirt version to an image tag.
  - For example: `KubeVirtVersion: 1.7 -> pluginImageTag: 1.7, KubeVirtVersion: 1.8 -> pluginImageTag: 1.8`, etc.
  - In the above example, picture the v1.7 image existing and used in production, while the 1.8 image does not yet exist.
However, when v1.8 is released, an image with a 1.8 tag would be added to a remote registry, allowing the Plugin owner
To plan an upgrade path in advance.

In order to update the Plugin itself, a new Plugin CR instance can be created on the cluster with different version conditions.

### multi-plugin support

The plugin system architecture aims to allow multiple plugins running on the same cluster.
In addition, plugins could provide dependencies between one another.
The plugin system should also validate that plugins do not override each other's values.

The following fields will be defined for this purpose:
- dependsOn: a list of plugins that have to run before this plugin.
- preservePaths: paths to exempt from modifications (NRI-inspired for conflict avoidance).

In addition, behind the scenes, modifications to the `DomainSpec` would be converted to a list of JSON patches.
This would allow multiple plugins to work on separate set of fields and allow separation of concerns.
In addition, this will allow validating that there is no conflict between different plugins so that they don't
override each other's data.

Only if a plugin is dependent on another plugin, both can edit the same fields, because now a clear ordering
is defined between them. This way one plugin can do some work that another plugin would finalize.

### Split to more parte CRDs

Create further orthogonality and allow reuse by having a `DomainHook` CR, a `NodeHook` CR and a `Plugin` CR which can
reference them to control the orchestration for their use.

An interesting idea to revisit.

## Scalability

From the Plugin mechanism perspective we don't envision any scalability concerns.

However, obviously the plugin authors would have the responsibility to keep their implementation scalable.

## Update/Rollback Compatibility

See `Versioning and Upgrade Path` section above.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

Alpha

- [ ] Feature gate (`StructuredPlugins`) guards all code changes.
- [ ] Plugin CRD is defined and registered.
- [ ] Simple domain hooks (JsonPatch) are functional.
- [ ] Advanced domain hooks (plugin sidecar via socket) are functional for a single hook point.
- [ ] Node hooks are functional for at least PreVMStart and OnVMStop hook points.
- [ ] failureStrategy (Fail/Ignore) and timeout are respected.
- [ ] Basic functional tests covering domain hooks and node hooks.

### Beta

### GA
