# VEP 82: Plugin-based generalization of KubeVirt's virtualization stack #83

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

At present KubeVirt can only be used to create and manage virtual machines (VMs) via Libvirt on the QEMU virtual-machine monitor (VMM), with only the hypervisor being configurable between KVM and MSHV. However, this design is limited and hard to maintain because in the wider industry, there are an increasing number of VMMs and hypervisors available - such as Cloud Hypervisor, OpenVMM (VMMs) and HyperV (hypervisor). Adding and maintaining support for so many backends in-tree in the core KubeVirt repository would make the code difficult to maintain. Furthermore, the dependence of KubeVirt on LibVirt forces any VMM that could be used to have a LibVirt driver, which is untenable.

Therefore, to maximize the adoption of alternative virtualization stacks within KubeVirt, the most preferable design is to decouple it entirely from the underlying virtualization stack components - and make the virt-stack pluggable. Core KubeVirt would provide the orchestration-related functionalities and offload virtualization-related functionalities to the plugin.

This VEP builds on the Hypervisor Generalization VEP ([#98](https://github.com/kubevirt/enhancements/pull/98)) and continues the discussion carried out in [VEP #83](https://github.com/kubevirt/enhancements/pull/83).

## Motivation

There are multiple reasons to decouple KubeVirt from the underlying virtualization stack components.

- Customer scenarios require different virtualization stack components: Given the increasing number of virtualization stack components available today, different customers of KubeVirt would require specific components to cater to their unique needs. Decoupling KubeVirt from Libvirt/QEMU would allow them to use their preferred virtualization stack components.

- Limitations and overhead imposed by Libvirt: Libvirt is a management wrapper which executes functions in its API by calling into the underlying VMM. Such functions can be directly invoked against the VMM by virt-launcher, thereby saving the overhead imposed by the Libvirt daemon. The use of Libvirt as the intermediate VM management layer restricts the virtualization stacks that can be used to only those that have a Libvirt driver. For example, OpenVMM does not have a Libvirt driver, and hence cannot be easily integrated into KubeVirt. Libvirt is implemented in C, and a programming language providing easier memory safety would reduce security risks. The plan for incorporating Rust into Libvirt has had relatively slow progress.
Furthermore, although libvirt provides a useful unified Domain definition across virtualization stacks, it is redundant in the presence of KubeVirt’s own VirtualMachineInstance definition.
Additionally, KubeVirt only utilizes around 20% (~57/292) of all the Libvirt APIs available –leaving Libvirt significantly underutilized while virt-launcher still incurs the overhead of running the Libvirt daemon.

- Maintaining multiple virtualization stacks in-tree is unmaintainable: Implementing support for different virtualization stacks directly in the core KubeVirt repository would significantly increase code complexity, testing burden, and maintenance overhead. A plugin-based architecture allows each virtualization stack implementation to be developed and maintained independently, reducing the maintenance burden on the core KubeVirt project.

### Areas of Tight Coupling Between KubeVirt and Libvirt/QEMU/KVM

- KubeVirt’s virt-launcher is built for Libvirt/QEMU: Although interaction with virt-launcher takes place through well-defined interfaces (CmdServer and NotifyServer), it is not possible to build a virt-launcher component wherein the implementation of those interfaces can be backed by an alternative virtualization stack.

- Libvirt domain XML’s mirror `api.Domain` data structure: KubeVirt thoroughly uses the api.Domain data structure to internally represent a virtual machine instance. This definition is meant to mirror Libvirt’s domain definition.

- Node Labeling involves invoking Libvirt’s QEMU driver: During the initialization of KubeVirt’s virt-handler component on a given node, it generates labels for that node based on the node’s virtualization capabilities. The virt-handler component queries node’s virtualization capabilities by running a virt-launcher container and invoking multiple Libvirt APIs, which in turn query the QEMU VMM. In addition to virtualization capabilities, the node topology is also queried from Libvirt, although that is not tied to the virtualization stack nor used for node labeling.

- Hardcoded Libvirt/QEMU/KVM-specific values in control-plane components: Code of components such as virt-controller and virt-handler contain hardcoded references to one of Libvirt, QEMU or KVM/MSHV. For instance, for computing the memory overhead of the virt-launcher's components, the `LauncherHypervisorResources` interface implementations for both KVM and MSHV assume the presence of a LibVirt daemon and QEMU process in the virt-launcher. Another example is when the virt-handler is called to update the `memlock` limit of a VM's VMM process, and it explicitly looks for a `virtqemud` or `qemu-system-x86` process.

- Libvirt/QEMU-specific guest agent status determination: Virt-Handler checks the state of the Libvirt channel "org.qemu.guest_agent.0” to determine if the guest agent is connected. It also assumes that the guest agent is QEMU Guest Agent and compares the list of supported commands with the set of required commands to determine if the agent is supported.

## Goals

- It should be possible to have multiple VMs based on different virtualization stacks running on the same node. For example, a single node with the KVM hypervisor should be able to host a VM backed by traditional LibVirt/QEMU backend as well as a VM backed by Cloud-Hypervisor.

- Refactor KubeVirt to allow the development of alternative variants of the virt-launcher component for different virtualization stacks.

- Refactor KubeVirt to decouple it from Libvirt, QEMU and KVM.

- Modify the KubevirtConfiguration CRD to allow specification of virtualization stack properties.

- Streamline the process of building and deployment of KubeVirt for alternative virtualization stacks.

- Ensure backward compatibility with existing KubeVirt deployments. Cluster administrators must be able to upgrade to the latest version of KubeVirt incorporating the proposed changes, while retaining the ability to create virtual machines using the default Libvirt/QEMU/KVM-based architecture without requiring API modifications.

## Non Goals

- Implementation of the proposed plugin components (e.g., virt-launcher and admission webhooks) for alternative virtualization stacks.

## Definition of Users

The proposed plugin-based virtualization stack architecture is intended for advanced users and integrators of KubeVirt who require flexibility beyond the default Libvirt/QEMU-based stack. This includes:

- Platform engineers and infrastructure teams deploying KubeVirt in environments where alternative virtualization stacks (e.g., Cloud Hypervisor, Firecracker) are preferred due to performance, security, or hardware compatibility requirements.

- Distributors and downstream projects that package KubeVirt as part of a larger platform and need to support multiple hypervisor backends.

## User Stories

### The Platform Operator (Infrastructure Admin)

- User Story: "As a platform operator, I want to deploy lightweight microVMs (e.g., Cloud Hypervisor) alongside standard workloads to increase tenant density and reduce resource overhead on my nodes."

- Benefit: Flexibility to choose the virtualization stack that best fits the hardware and performance requirements of the organization without maintaining multiple orchestration platforms.

### The Virtualization Stack Developer (Backend Provider)

- User Story: "As a developer of a new virtualization backend, I want to integrate my VMM into KubeVirt without having to modify the KubeVirt core codebase or upstream my VMM-specific logic to the main repository."

- Benefit: Accelerated development cycles and independent release cadences for backend plugins.

### The Infrastructure Developer (KubeVirt Core Maintainer)

- User Story: "As a KubeVirt maintainer, I want to reduce the complexity of the core codebase by offloading VMM-specific implementation details to external plugins, allowing the core to focus on Kubernetes-native orchestration."

- Benefit: Reduced technical debt and a more stable, maintainable core API that is not tightly coupled to libvirt/QEMU lifecycle quirks.

## Repos

- Core KubeVirt repo: [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

- Additional repositories containing implementation of plugin components (e.g., virt-launcher and admission webhooks) for alternative virtualization stacks.

## Proposed Design

This VEP defines the top-level design direction for introducing a plugin-based virtualization stack model in KubeVirt. Because this effort requires a broad refactoring of tightly coupled areas in the KubeVirt codebase, it is not practical to capture all detailed design changes in a single document.

Therefore, this VEP serves as a tracking VEP for a set of smaller, focused VEPs. Each of those VEPs will propose and document the detailed design for one specific area of tight coupling in KubeVirt (for example, virt-launcher interfaces, domain representation, node capability discovery, and control-plane assumptions).

The intent is to keep this document focused on overall architecture and coordination, while delegating implementation-level design details to targeted follow-up VEPs.

### Pluggable Node Labeler

We propose moving virtualization-stack-specific capability discovery and node labeling out of `virt-handler` and into independently deployable node labeler plugins. Rather than exposing a fixed set of capability functions, each plugin would advertise the node labels relevant to its virtualization stack as key-value pairs. This allows a plugin to expose capabilities that KubeVirt does not know about in advance, including stack versions and preview features, without extending an in-tree interface for each new label.

Node labeler plugins would be discoverable by `virt-handler` through a node-local registration mechanism, similar in principle to Kubernetes device plugin registration, instead of requiring a KubeVirt API change for every plugin. Each plugin would register with a unique virtualization-stack ID. The same ID would identify the related node labeler, `virt-launcher`, controller logic, and admission components, and would namespace labels emitted by the plugin to avoid collisions between virtualization stacks.

The contract must support both labels that remain static for the lifetime of the plugin and labels whose values can change while the node is running. `virt-handler` would reconcile the labels advertised by registered plugins, with the detailed design defining how a plugin signals a change or how `virt-handler` periodically refreshes them. Corresponding stack-specific scheduling logic must be able to translate VMI requirements into constraints based on the advertised labels without requiring the core node labeler to understand every plugin-defined capability.

The exact registration protocol, refresh mechanism, and authorization controls will be specified in a focused follow-up VEP. In particular, registration must be restricted to plugins deployed by authorized cluster administrators so that an untrusted workload cannot register a plugin, impersonate a virtualization stack, or overwrite its labels.

### Refactoring Virt-Launcher to make it pluggable

We propose making the entire `virt-launcher` component pluggable. Each virtualization stack would provide its own `virt-launcher` implementation and container image, allowing stack-specific VM lifecycle and VMM integration code to be developed and released outside the core KubeVirt repository.

`virt-handler` would continue to interact with a plugin `virt-launcher` through the existing command API. Within the plugin, the `CmdServer` implementation acts as a shim over that plugin's `DomainManager` implementation, translating command API calls into stack-specific domain operations. The command API is therefore the communication boundary used by `virt-handler`, while `DomainManager` is the internal interface implemented by the plugin; they are complementary layers of the same design.

### Pluggable Admission Webhooks

TBD

### Pluggable Virt-Handler Runtime

We propose a new pluggable sidecar component, tentatively named `virt-runtime`, for operations that `virt-handler` performs against a virtualization stack on the node. The sidecar would carry out stack-specific privileged operations, such as locating the relevant VMM process and setting its `memlock` limit, so that `virt-handler` does not retain privileged Libvirt/QEMU-specific logic in-tree.

`virt-runtime` is the `virt-handler`-side counterpart to the pluggable `virt-launcher`: `virt-handler` would delegate any operation requiring virtualization-stack knowledge or elevated privileges to the runtime associated with that stack. The API contract and the complete set of operations that belong to `virt-runtime`, beyond the initial `memlock` example, require a focused follow-up design.

### Pluggable Virt-Controller Stack Calculations

The virtualization-stack-specific portion of `virt-controller` is limited to calculating the memory overhead and related pod specification properties needed when rendering a `virt-launcher` pod. We propose exposing a common plugin contract through which `virt-controller` supplies the relevant VMI and stack context and receives the calculated overhead and pod properties. The detailed request and response schema is deferred to a focused follow-up VEP.

The deployment model for this plugin remains an open decision. Both of the following models can provide the same calculation contract:

- A sidecar co-located with `virt-controller` would provide a local call path and align the plugin lifecycle and version with each `virt-controller` deployment, but would add the plugin's resource cost to every `virt-controller` pod.

- A cluster-wide service would require only one independently scalable deployment and could share cached data across callers, but would add a network hop and make launcher pod rendering dependent on the availability of that service.

The selected model must also account for any stack-specific scheduling constraints derived from labels advertised by the node labeler plugin. Whether that mapping belongs to this plugin contract or to an admission policy is part of the follow-up design.

## Open Questions

- Should the virt-controller plugin be deployed as a sidecar or as a cluster-wide service?

- What API should `virt-handler` use to invoke `virt-runtime`, and which privileged stack-specific operations belong to that component beyond setting `memlock` limits?

- What node-local registration and communication protocol should node labeler plugins use, and how should plugin lifecycle and failures be reconciled by `virt-handler`?

- Should dynamic node label updates be event-driven, periodically polled, or use a combination of both approaches?

- How should registration be authenticated or restricted so that only plugins deployed by authorized cluster administrators can advertise labels for a virtualization-stack ID?

- Should stack-specific logic that maps VMI requirements to plugin-defined node labels run in the virt-controller plugin or in an admission policy?
