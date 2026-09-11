# VEP 403: Add a VirtualMachineInstance NMI injection subresource

## VEP Status Metadata

### Target releases

- This VEP targets Alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version: v1.12

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

To support kernel and operating system hang analysis, this proposal introduces a Non-Maskable Interrupt (NMI) injection feature for VirtualMachineInstance. When a guest becomes unresponsive, users can inject an NMI to trigger the guest OS's crash dump mechanism (for example, Linux kdump), preserving diagnostic information for post-mortem analysis before performing reset or power-off operations.

The operation is exposed as a new VMI subresource under the KubeVirt subresource API. The `virtctl inject-nmi` command is provided as a convenient client to call this API.

The primary goal of this feature is only to provide a way to inject a NMI.
It does not guarantee that a crash dump is produced. Dump generation, storage location, reboot behavior, and post-processing are controlled by the guest OS configuration, such as Linux kdump/NMI settings or Windows crash dump settings.
If a guest OS is properly configured, it may generate a crash dump in response to the injected NMI.

The design follows the same architectural pattern currently used by KubeVirt runtime subresources such as `pause`, `unpause`, `freeze`, `unfreeze`, `reset`, and `softreboot`

## Motivation

KubeVirt already provides a VM memory dump feature, but the earlier KubeVirt design proposal for this feature explicitly frames the solution around later inspection with Volatility3. [\[github.com\]](https://github.com/kubevirt/community/blob/main/design-proposals/vm-memory-dump.md)
That workflow is useful for generic memory inspection and forensic analysis, but it does not address the main operational requirement for Linux kernel hang analysis. In practice, kernel hang analysis is typically performed on kdump-generated `vmcore` files using kernel-oriented tooling such as `crash` together with matching kernel debuginfo. Red Hat documentation for `crash` explains that it operates on kdump-generated dump files, and Linux kdump documentation explicitly lists NMI as one of the events that can trigger crash dump capture. [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_monitoring_and_updating_the_kernel/analyzing-a-core-dump_assembly_managing-kernel-command-line-parameters-with-uki#running-and-exiting-the-crash-utility_analyzing-a-core-dump), [\[kernel.org\]](https://www.kernel.org/doc/html/latest/admin-guide/kdump/kdump.html)

For mission-critical workloads, the inability to trigger a guest crash dump before resorting to reset or power-off is a significant operational gap. Red Hat’s kdump guide explicitly emphasizes that crash dump data is especially important in business-critical environments. Operational guidance from multiple vendors similarly recommends sending an NMI to a hung VM in order to force dump generation and preserve state for later root-cause analysis before taking more destructive recovery actions. This need is not limited to Linux: Microsoft’s guidance likewise underscores the importance of collecting crash dumps in Windows environments. [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_monitoring_and_updating_the_kernel/installing-kdump_assembly_managing-kernel-command-line-parameters-with-uki#what-is-kdumpinstalling-kdump), [\[learn.microsoft.com\]](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/generate-a-kernel-or-complete-crash-dump), [\[community.ibm.com\]](https://community.ibm.com/community/user/blogs/sachin-bappalige/2025/07/02/vmcore-crash-analysis-in-enterprise-power-system)

KubeVirt currently has a gap here compared to mainstream virtualization platforms. Hyper-V exposes `Debug-VM -InjectNonMaskableInterrupt`; VMware vSphere/ESXi exposes `Send_NMI_To_Guest` and `vmdumper ... nmi`; libvirt exposes `virsh inject-nmi`. Users migrating VM operational practices into KubeVirt reasonably expect an equivalent platform-native function instead of requiring direct access to launcher internals or host-side tools. [\[learn.microsoft.com\]](https://learn.microsoft.com/en-us/powershell/module/hyper-v/debug-vm?view=windowsserver2025-ps), [\[knowledge.broadcom.com\]](https://knowledge.broadcom.com/external/article/301246/how-to-send-nmi-to-guest-os-on-esxi-6x-o.html), [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/virtualization_deployment_and_administration_guide/sect-generic_commands-injecting_nmi)

## Goals

Provide a KubeVirt-native API and CLI mechanism to inject an NMI into a running VMI.

## Non Goals

- This proposal does not define guest OS configuration for crash dump generation.
- This proposal does not guarantee that every guest OS will react identically to an injected NMI.
- This proposal does not replace or redesign the existing `memory-dump` feature.
- This proposal does not standardize guest-side dump collection or post-processing tools.

## Definition of Users

* VM owner.

### User Story 1: Hung guest operating system

As a VM owner, when a guest becomes stuck and unresponsive but the VMI remains alive, I want to inject an NMI so that the guest operating system can generate a crash dump for later kernel analysis.
(Prerequisites such as enabling kdump on Linux are OS-specific and are typically configured by users following instructions from their OS vendor.)

## Repos

* [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### High-level design

This proposal introduces a new VMI subresource:

`PUT /apis/subresources.kubevirt.io/<version>/namespaces/{namespace}/virtualmachineinstances/{name}/injectnmi`

The subresource is the primary API surface for NMI injection. Client tooling such as virtctl may invoke this API:

`virtctl inject-nmi <vmi> [-n <namespace>]`

The command targets VirtualMachineInstance only.
A VM-level alias is intentionally out of scope; it can be added later if the community wants parity with pause/unpause VM resolution behavior.

The subresource request is handled through the same architecture already used by runtime VMI subresources:

1. `virt-api` authenticates and authorizes the request and forwards the request to `virt-handler`.
2. `virt-handler` resolves the target VMI launcher socket and forwards the request over the existing cmd/gRPC channel.
3. `virt-launcher` delegates the operation to the domain manager.
4. The domain manager invokes the hypervisor backend to inject an NMI.

The KubeVirt client-go library exposes a method for invoking this subresource. The `virtctl inject-nmi` command parses the command arguments and uses this method to invoke the API.

### API and authorization

The operation should be modeled as a new PUT subresource under `virtualmachineinstances`, following the same authorization model used by current subresources. KubeVirt’s subresource architecture already performs per-subresource authorization checks and exposes runtime VMI operations through the aggregated subresource API.

The expected authorization model is identical to the model used by softreboot:

* resource: `virtualmachineinstances/injectnmi`
* verb: `update`

### Prerequisite

The operation should require:

* the VMI exists
* the VMI is currently running

### Observability

- On successful backend delivery, emit Normal event reason "NMIInjected".
- On validation or backend delivery failure, emit Warning event reason "NMIInjectionFailed" with a sanitized error message.

### Backend implementation

The target backend implementation is libvirt.

The `DomainManager` interface in `virt-launcher/virtwrap/manager.go` already defines methods such as `PauseVMI`, `ResetVMI`, and `SoftRebootVMI`.
An `InjectNMIVMI` method will be added following the same implementation pattern.

When virt-launcher receives the request, it invokes `virDomainInjectNMI()` through libvirt.

[libvirt virDomainInjectNMI documentation](https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainInjectNMI)

The libvirt API simply injects an NMI and returns. The NMI serves as a trigger for the guest OS's crash dump mechanism, and the subsequent dump collection process is handled entirely by the OS.

## API Examples

### HTTP API

```http
PUT /apis/subresources.kubevirt.io/v1/namespaces/test/virtualmachineinstances/my-vmi/injectnmi
```

### CLI

```bash
virtctl inject-nmi my-vmi -n test
```

### Expected responses

* `202 Accepted` when the request is accepted and dispatched
* `403 Forbidden` the user is not authorized to perform this operation on the VMI
* `404 Not Found` if the VMI does not exist

## Scalability

This is a per-VMI, stateless control-plane request that follows the same request/forwarding path as existing runtime subresources. It is not expected to introduce new scalability characteristics beyond those already present for VMI runtime operations. Existing runtime subresource architecture already supports similar requests like `pause`, `freeze`, `reset`, and `softreboot`.

## Update/Rollback Compatibility

Clients invoking the new injectnmi subresource against older clusters should receive a clear server-side error because the subresource is not recognized.

## Functional Testing Approach

Testing should include:

1. unit tests for the VMI subresource API handler validation and routing
2. unit tests for client-go method wiring
3. unit tests for `virt-handler` lifecycle request handling
4. unit tests for launcher cmd-server and domain-manager dispatch
5. unit tests for `virtctl inject-nmi` command parsing and client invocation
6. End-to-end test
  * Given a running VMI with a guest configured to log NMI events, execute `virtctl inject-nmi` and verify that the guest records evidence of the NMI (for example, NMI-related kernel log entries).

## Implementation History

## Graduation Requirements

### Alpha

- Implement the NMI injection end-to-end behind InjectNMI feature gate
  - Add the `injectnmi` API subresource and backend wiring
  - Add client support via `virtctl inject-nmi`
  - Support for the default hypervisor path
- Add unit tests and e2e test

### Beta

- Document guest prerequisites and non-guarantees

### GA

- Documentation exists
- No known major bugs in default backend
