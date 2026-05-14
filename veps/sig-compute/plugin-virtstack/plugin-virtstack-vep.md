# VEP 82: Plugin-based generalization of KubeVirt's virtualization stack #83

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

At present KubeVirt can only be used to create and manage virtual machines (VMs) via Libvirt on the QEMU virtual-machine monitor (VMM), with only the hypervisor being configurable between KVM and MSHV. However, this design is limited and hard to maintain because in the wider industry, there are an increasing number of VMMs and hypervisors available - such as Cloud Hypervisor, OpenVMM (VMMs) and HyperV (hypervisor). Adding and maintaining support for so many backends in-tree in the core KubeVirt repository would make the code difficult to maintain. Furthermore, the dependence of KubeVirt on LibVirt forces any VMM that could be used to have a LibVirt driver, which is untenable.

Therefore, to maximize the adoption of alternative virtualization stacks within KubeVirt, the most preferable design is to decouple it entirely from the underlying virtualization stack components - and make the virt-stack pluggable. Core KubeVirt would provide the orchestration-related functionalities and offload virtualization-related functionalities to the plugin.

This VEP is a continuation of the discussion carried out in a previous VEP: https://github.com/kubevirt/enhancements/pull/83

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

- Core KubeVirt repo: https://github.com/kubevirt/kubevirt

- Additional repositories containing implementation of plugin components (e.g., virt-launcher and admission webhooks) for alternative virtualization stacks.


## Proposed Design

This VEP defines the top-level design direction for introducing a plugin-based virtualization stack model in KubeVirt. Because this effort requires a broad refactoring of tightly coupled areas in the KubeVirt codebase, it is not practical to capture all detailed design changes in a single document.

Therefore, this VEP serves as a tracking VEP for a set of smaller, focused VEPs. Each of those VEPs will propose and document the detailed design for one specific area of tight coupling in KubeVirt (for example, virt-launcher interfaces, domain representation, node capability discovery, and control-plane assumptions).

The intent is to keep this document focused on overall architecture and coordination, while delegating implementation-level design details to targeted follow-up VEPs.

### Pluggable Node Labeler

TBD

### Refactoring Virt-Launcher to make it pluggable

TBD

### Pluggable Admission Webhooks

TBD

### Pluggable Virt-Handler Runtime

TBD

### Pluggable LauncherHypervisorResources for `virt-launcher` rendering by `virt-controller`

TBD