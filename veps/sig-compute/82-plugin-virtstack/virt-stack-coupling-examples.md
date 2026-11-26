# Tight Coupling with Virtualization Stack in KubeVirt today


## Introduction

This document provides examples of how KubeVirt's source code is coupled with the virtualization stack across different KubeVirt components.

## virt-controller

`virt-controller` invokes virtualization-stack-specific logic when rendering the `virt-launcher` pod. That includes the calculation of the memory overhead of virtualization components (e.g., `virtqemud` and `virtlogd`) and the specification of pod properties (e.g., command and arguments, volume mounts and run-as user). Below is a list of such examples:

- Launcher image, command-line and environment variables. The launcher command-line/env-vars would be used for selecting virtualization-stack-specific settings like firmware, timeouts, etc.

    ```yaml
    command:
    - /usr/bin/virt-launcher-monitor
    args:
    - --qemu-timeout
    - 345s
    - --ovmf-path
    - /usr/share/edk2/ovmf
    - --hypervisor
    - hyperv-direct
    image: quay.io/kubevirt/virt-launcher:v1.9.0
    ```

- Devices that should be requested by the pod. E.g., `/dev/kvm`

    ```yaml
    resources:
    limits:
        devices.kubevirt.io/mshv: "1"
    requests:
        devices.kubevirt.io/mshv: "1"
    ```

- Virt-stack-specific volumes and volume mounts (e.g., `libvirt-runtime`)

  ```yaml
  volumeMounts:
  - mountPath: /var/run/libvirt
    name: libvirt-runtime
  volumes:
  - emptyDir: {}
    name: libvirt-runtime
  ```

- Security Context and Container Capabilities.

    ```yaml
    securityContext:
        allowPrivilegeEscalation: false
        capabilities:
        add:
        - NET_BIND_SERVICE
        drop:
        - ALL
        runAsGroup: 107
        runAsNonRoot: true
        runAsUser: 107
    ```

- Node Selectors specific to the Virtualization Stack, e.g., CPU Model.

```yaml
machine-type.node.kubevirt.io/pc-q35-7.2: "true"
```

## virt-handler

`virt-handler` owns Kubernetes-facing, node-local VMI orchestration, but several parts of its current runtime model assume that every launcher uses Libvirt and QEMU. The main areas of coupling are:

- **Migration endpoints and transport:** `virt-handler` performs the communication setup for LibVirt to carry out migration. It assumes that the source and target would communicate on ports 49152 and 49153 and that is aware of the UNIX socket in virt-launcher for controlling LibVirt. It also repairs passt sockets below a fixed Libvirt/QEMU runtime directory.
- **Process discovery and resource adjustment:** The KVM and MSHV `VirtRuntime` implementations search for `qemu-system-*` or `virtqemud` processes before applying limits such as `memlock`. The MSHV implementation therefore changes the hypervisor selection without removing the QEMU and Libvirt process model.
- **CPU housekeeping:** CPU tuning intent is read from `api.Domain`, but the implementation discovers QEMU vCPU, emulator, and PIT threads and applies affinity according to QEMU's thread model.
- **Guest-agent and status interpretation:** Guest-agent is assumed to be connected via the LibVirt channel.
- **LibVirt-oriented data structures** like `api.Domain` and `DomainStats` are used throughout the code for runtime representation of a running VM and the metrics reported by LibVirt.

The `VirtualMachineController` itself is not inherently tied to the current stack. Its core state machine observes a VMI and a domain, decides whether to synchronize, shut down, kill, migrate, or clean up the VMI. It either communicates with the corresponding `virt-launcher` via the `Command` API or reports results to Kubernetes. 
