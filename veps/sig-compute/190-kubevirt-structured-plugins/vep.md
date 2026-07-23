# VEP #190: Kubevirt Structured Plugins

Owners:
- @iholder101
- @vladikr

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9. 
- This VEP targets alpha 2 for version: v1.10.
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
  - [Alpha 2 (v1.10): Launcher Hooks](#alpha-2-v110-launcher-hooks)
    - [Renaming Domain Hooks to Launcher Hooks](#renaming-domain-hooks-to-launcher-hooks)
    - [PreBoot Hook](#preboot-hook)
    - [PreMigrationSource Hook](#premigationsource-hook)
  - [Alpha 2 (v1.10): NodeReconcile](#alpha-2-v110-nodereconcile)
  - [Alpha 2 (v1.10): API Examples](#alpha-2-v110-api-examples)
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
- Support two plugin modes: simple CEL-based and advanced sidecar-based.
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

**Simple hooks**: These are CEL-based hooks.
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

```yaml
domainHooks:
  - cel:
      expression: 'Domain{CPU: DomainCPU{Mode: "host-passthrough"}}'
```

<ins>Advanced Hooks</ins>:

```yaml
domainHooks:
  - sidecar:
      socketPath: "/var/run/kubevirt-plugin/my-cool-plugin/my-plugin-socket.sock"
```

Note: The socket would need to reside under `/var/run/kubevirt-plugin/<my-plugin-name>`,
which will be defined as a shared volume with the compute container.
The plugin's container will have visibility only to its plugin folder in an isolated way.

### The Node Hook

To support node-level customizations, hooks can also define operations triggered by the virt-handler pod.
These are invoked via gRPC calls from virt-handler to a plugin server
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
- **PreVMStop**: Before VM stops (e.g., cleanup node resources; aligns with processVmUpdate on shutdown).
- **PostVMStop**: After VM has stopped (e.g., final cleanup).
- **PreMigrationSource**: Before migration from source node (e.g., prepare sockets; aligns with migrateVMI in migration-source.go).
- **PreMigrationTarget**: Before migration to target node (e.g., setup target sockets; aligns with prepareMigrationTarget in migration-target.go).
- **PostMigrationTarget**: After migration completes on target (e.g., repair/verify; aligns with finalizeMigration).

The plugin would have to explicitly mention which hook points it wishes to register for.

#### Node hook API examples

```yaml
nodeHooks:
  - socket: /var/run/my-node-socket.sock
    permittedHooks:
    - PreVMStart
    - PostVMStop
    condition: "vmi.status.conditions.exists(c, c.type == 'Ready' && c.status == 'True')"
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
- failureStrategy: Defines the default behavior when a hook fails. `Fail` (default) blocks the operation, `Ignore` logs the error and continues. Individual hooks can override this.
- condition: A CEL expression that filters which VMIs the plugin applies to. Individual hooks can further narrow with their own condition.
- timeout: Per-hook field. The maximum duration to wait for a hook response before considering it failed (e.g. `30s`, `2m`).

The plugin’s name comes from the Kubernetes object metadata (`metadata.name`).
Versioning and upgrade path are deferred to [future enhancements](#possible-future-enhancements).

### Alpha 2 (v1.10): Launcher Hooks

Alpha 1 introduced **domain hooks** for modifying libvirt domain XML via CEL expressions or sidecar containers.
In practice, the plugin system needs to support hook points inside `virt-launcher` that go beyond domain XML mutation -
for example, modifying guest initialization artifacts or tuning live migration parameters.

To reflect this broader scope, Alpha 2 renames `domainHooks` to `launcherHooks` and introduces a discriminated union
that supports multiple hook types under one array. Each entry in `launcherHooks` is exactly one of:
- **GuestDefinition**: The existing domain XML hook (CEL or sidecar), renamed for hypervisor-agnostic clarity. Same semantics as Alpha 1.
- **PreBoot**: A new hook point for modifying generated artifacts before VM start.
- **PreMigrationSource**: A new hook point for customizing live migration behavior.

Shared fields (`condition`, `failureStrategy`, `timeout`) remain at the hook entry level.
Hooks are applied in declaration order within each plugin, and in alphabetical order by plugin name across plugins -
same as Alpha 1.

#### Renaming Domain Hooks to Launcher Hooks

Alpha 1 named these hooks `domainHooks`, but not all launcher-side hooks are related to domain
mutation. `launcherHooks` better reflects that this category encompasses any hook that runs
inside the `virt-launcher` pod - whether it modifies the guest definition, touches filesystem
artifacts, or tunes migration parameters.

Within `launcherHooks`, the existing domain XML hook becomes `guestDefinition` - a
hypervisor-agnostic name that describes what the hook does (modify the guest's definition)
without leaking libvirt-specific terminology.

#### PreBoot Hook

Some plugin use cases require modifying guest initialization artifacts that are generated at runtime -
for example, cloud-init configuration that depends on CNI data (Multus network-status annotations)
only available after pod creation. The existing domain hook cannot address this because it operates on
domain XML, not guest initialization media.

The `preBoot` hook fires inside `startDomain()`, after all artifacts (cloud-init ISO, NVRAM, firmware, etc.)
have been generated but before the VM is started via `CreateWithFlags`. This placement gives the sidecar
access to the finalized artifacts while still allowing modifications before the guest sees them.

**Design:**
- Sidecar-only (not CEL) - these hooks perform filesystem side effects, not structured data transformations.
- Socket-based signaling: `virt-launcher` calls the sidecar's socket to signal that artifacts are ready.
  The sidecar performs its modifications and responds when done.
- Filesystem access via shared volume: the plugin author's Mutating Admission Policy (MAP) sets up an
  `emptyDir` volume mounted in both the compute container and the sidecar container. The sidecar can
  read and modify generated artifacts directly on the shared filesystem.
- KubeVirt does not need artifact-specific code - the sidecar knows which files it needs to modify.
- The [kubevirt/plugins](https://github.com/kubevirt/plugins) SDK will provide helpers for common operations
  (e.g., cloud-init ISO extraction, modification, and repacking).

**Ordering with multiple plugins:** PreBoot hooks fire sequentially in the standard plugin ordering
(alphabetical by plugin name, declaration order within each plugin). Each plugin's sidecar sees the
filesystem state left by the previous plugin's sidecar. Plugin authors should be aware of this
and design their modifications to be composable.

**Note on hook ordering:** Guest definition hooks fire during domain definition (before `startDomain()`).
PreBoot hooks fire later, during `startDomain()`. A domain mutation hook could add a device that
affects cloud-init configuration - plugin authors should be aware of this ordering when designing
plugins that combine both hook types.

**Example use case:**
A networking plugin that configures guest cloud-init network settings based on runtime CNI data
(see [VEP 337](https://github.com/kubevirt/enhancements/issues/337)). The plugin's sidecar reads
Multus network-status annotations via the Downward API, extracts the cloud-init ISO from the shared
volume, injects the appropriate network configuration, and repacks the ISO.

#### PreMigrationSource Hook

Live migration behavior in KubeVirt is currently controlled entirely by internal logic.
Plugins that manage specialized hardware, networking, or storage may need to customize migration
parameters on a per-workload basis - for example, enabling multifd parallel migration and tuning
thread counts for high-bandwidth workloads, or selecting specific compression methods.

The `preMigrationSource` hook fires in `virt-launcher` on the migration source, just before
the `MigrateToURI3` libvirt API call. The sidecar receives the generated migration flags
and parameters, and can modify them before the migration begins.

**Design:**
- Sidecar-only (not CEL) - migration parameter tuning requires logic that goes beyond simple expressions.
- The sidecar receives the full set of migration flags (e.g., `VIR_MIGRATE_PARALLEL`,
  `VIR_MIGRATE_COMPRESSED`) and parameters (e.g., `Bandwidth`, `ParallelConnections`,
  `Compression` method, `MigrateDisks` list) via a new gRPC message.
- The sidecar can enable/disable flags and set parameter values, giving full control over
  migration behavior. This allows plugins to both enable features (e.g., multifd) and
  configure them (e.g., number of parallel connections).
- A new protobuf service and message types will be defined for this hook point.
- The sidecar can also perform other pre-migration preparation (e.g., signaling external systems,
  preparing source-side resources).

**Naming:** Named `PreMigrationSource` to match the existing node hook convention and to
leave room for a future `PreMigrationTarget` launcher hook on the destination side.

**Example use case:**
A storage plugin that enables multifd migration with a tuned thread count for VMs using
high-throughput network-attached storage, while keeping the default single-stream migration
for VMs with local disks.

### Alpha 2 (v1.10): NodeReconcile

Alpha 1's node hooks are lifecycle-event-driven - they fire at specific points in a VM's lifecycle
(PreVMStart, PostVMStop, migration events, etc.). Some node-level plugin operations need continuous
reconciliation rather than reacting to discrete events. For example, managing a node's hugepage pool requires knowing the total hugepage demand
across all running VMs on a node, not just reacting to individual VM start/stop events.

`NodeReconcile` is a new node hook point that fires on every `virt-handler` reconcile iteration.
Unlike the existing lifecycle hook points which receive a single VMI, `NodeReconcile` provides
a broader node-scoped context.

**Design:**
- Fires on every `sync()` call in the virt-handler VMI controller. Since virt-handler reconciles
  per-VMI, this means `NodeReconcile` fires once per VMI reconcile. Plugin implementations must
  be idempotent and should short-circuit when the relevant state hasn't changed.
- The hook receives a `NodeContext` containing:
  - Node information: labels, annotations, capacity, allocatable resources.
  - A list of VirtualMachineInstances currently running on the node with key characteristics.
  - The KubeVirt CR - providing the cluster-wide KubeVirt configuration (feature gates, migration
    defaults, developer configuration, etc.) to the hook.
- CEL conditions for `NodeReconcile` hooks evaluate against the same `NodeContext`, using `node`,
  `vmis`, and `kubevirt` variables. This allows efficient filtering - for example, a plugin can
  use a CEL condition to only fire when specific node labels are present or when the VMI count
  changes.
- The same `NodeContext` data is sent both to the CEL evaluator (for condition filtering) and
  to the plugin's gRPC socket (for hook execution).

**Performance considerations:** Since this hook fires on every VMI reconcile, plugins should
use CEL conditions aggressively to filter unnecessary calls. The hook implementation itself
should be lightweight - check whether relevant state has changed before performing expensive
operations. The `failureStrategy` and `timeout` fields apply as usual.

**Example use case:**
A hugepage pool management plugin that tracks total hugepage demand across all VMs on the
node and adjusts the node's hugepage allocation accordingly. The plugin uses a CEL condition
to fire only when the node has specific labels, sums the hugepage requests from the VMI list,
and rebalances the pool when the total demand changes.

### Alpha 2 (v1.10): API Examples

```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: network-customizer
spec:
  condition: "vmi.metadata.labels['network-plugin'] == 'custom'"
  failureStrategy: Fail

  launcherHooks:
    # Guest definition mutation via CEL (same as Alpha 1, under new structure)
    - guestDefinition:
        cel:
          expression: |
            Domain{CPU: DomainCPU{Mode: "host-passthrough"}}

    # Guest definition mutation via sidecar
    - guestDefinition:
        sidecar:
          socketPath: "/var/run/kubevirt-plugin/network-customizer/domain.sock"

    # PreBoot: modify cloud-init for custom networking
    - preBoot:
        socketPath: "/var/run/kubevirt-plugin/network-customizer/preboot.sock"
      timeout: 30s

    # PreMigrationSource: tune migration for high-bandwidth workloads
    - preMigrationSource:
        socketPath: "/var/run/kubevirt-plugin/network-customizer/premigrate.sock"
      condition: "vmi.metadata.labels['high-bandwidth'] == 'true'"
      timeout: 60s

  nodeHooks:
    - socket: /var/run/kubevirt-plugins/network-customizer/node.sock
      permittedHooks:
        - PreVMStart
        - PostVMStop
        - NodeReconcile
      condition: "node.metadata.labels['network-plugin-enabled'] == 'true'"
```

#### Launcher hook type structure

Each entry in `launcherHooks` is a discriminated union - exactly one of the following must be set:

| Hook Type | Purpose | Modes | Input/Output |
|-----------|---------|-------|--------------|
| `guestDefinition` | Guest definition mutation | CEL or sidecar | Domain XML in, mutated domain XML out |
| `preBoot` | Modify generated artifacts before VM start | Sidecar only | Socket signal, filesystem access |
| `preMigrationSource` | Customize migration flags and parameters | Sidecar only | Migration config in, modified config out |

#### Node hook points (updated)

All hook points from Alpha 1 remain unchanged. Alpha 2 adds `NodeReconcile`:

| Hook Point | Scope | Context | When |
|-----------|-------|---------|------|
| PreVMStart | VMI | VMI + node name | Before VM launch |
| PostVMStart | VMI | VMI + node name | After VM is running |
| PreVMStop | VMI | VMI + node name | Before VM shutdown |
| PostVMStop | VMI | VMI + node name | After VM stops |
| PreMigrationSource | VMI | VMI + node name | Before migration from source |
| PreMigrationTarget | VMI | VMI + node name | Before migration to target |
| PostMigrationTarget | VMI | VMI + node name | After migration completes on target |
| NodeReconcile | Node | NodeContext (node + VMIs + KubeVirt CR) | Every reconcile iteration |

All hook points except `NodeReconcile` were introduced in Alpha 1.

## API Examples (full version)

```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: monitoring-hook
spec:
  failureStrategy: Fail

  domainHooks:
  # Simple hook: CEL-based
  - cel:
      expression: 'Domain{CPU: DomainCPU{Mode: "host-passthrough"}}'
  # Advanced hook: Sidecar
  - sidecar:
      socketPath: "/var/run/kubevirt-plugin/monitoring-hook/hook.sock"

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
    - socket: /var/run/my-node-socket.sock
      permittedHooks:
      - PreVMStart
      - PreVMStop
      - PostVMStop
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

- [x] Feature gate (`StructuredPlugins`) guards all code changes.
- [x] Plugin CRD is defined and registered.
- [x] Simple domain hooks (JsonPatch) are functional.
- [x] Advanced domain hooks (plugin sidecar via socket) are functional for a single hook point.
- [x] Node hooks are functional for at least PreVMStart and OnVMStop hook points.
- [x] failureStrategy (Fail/Ignore) and timeout are respected.
- [x] Basic functional tests covering domain hooks and node hooks.

### Alpha 2 (v1.10)

- [ ] `domainHooks` renamed to `launcherHooks` with discriminated union structure.
- [ ] `guestDefinition` launcher hook type supports CEL and sidecar modes (functionally equivalent to Alpha 1 domain hooks).
- [ ] `preBoot` launcher hook is functional: fires after artifact generation, before VM start, with shared volume support.
- [ ] `preMigrationSource` launcher hook is functional: sidecar can modify migration flags and parameters.
- [ ] New gRPC service and protobuf messages for `preBoot` and `preMigrationSource` hooks.
- [ ] `NodeReconcile` node hook point is functional: fires on every reconcile with `NodeContext`.
- [ ] CEL evaluator supports `node`, `vmis`, and `kubevirt` variables for `NodeReconcile` conditions.
- [ ] Functional tests covering `preBoot`, `preMigrationSource`, and `NodeReconcile` hook points.

### Beta

### GA
