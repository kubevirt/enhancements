# VEP 403: Add `virtctl inject-nmi` for VirtualMachineInstance

## VEP Status Metadata

### Target releases

- This VEP targets beta for version: v1.10
- This VEP targets GA for version: v1.11

The change adds a runtime operation comparable to existing VM lifecycle subresources and does not alter the default behavior of existing workloads.
So, this VEP does not have graduation phases guarded by a feature gate.

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds a new runtime operation for `VirtualMachineInstance` (VMI) to inject a Non-Maskable Interrupt (NMI) into a running guest. 
It is exposed through a new `virtctl inject-nmi` command and a corresponding VMI subresource. 

The primary goal of this feature is only to provide a way to inject a NMI.
It does not guarantee that a crash dump is produced. Dump generation, storage location, reboot behavior, and post-processing are controlled by the guest OS configuration, such as Linux kdump/NMI settings or Windows crash dump settings.
Ofcourse, if a guest OS is properly configured, crash dump will be corrected by each guest's os manner.

The design follows the same architectural pattern currently used by KubeVirt runtime subresources such as `pause`, `unpause`, `freeze`, `unfreeze`, `reset`, and `softreboot`

## Motivation

KubeVirt already provides a VM memory dump feature, but the earlier KubeVirt design proposal for this feature explicitly frames the solution around later inspection with Volatility3. [\[github.com\]](https://github.com/kubevirt/community/blob/main/design-proposals/vm-memory-dump.md)
That workflow is useful for generic memory inspection and forensic analysis, but it does not address the main operational requirement for Linux kernel hang analysis. In practice, kernel hang analysis is typically performed on kdump-generated `vmcore` files using kernel-oriented tooling such as `crash` together with matching kernel debuginfo. Red Hat documentation for `crash` explains that it operates on kdump-generated dump files, and Linux kdump documentation explicitly lists NMI as one of the events that can trigger crash dump capture. [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_monitoring_and_updating_the_kernel/analyzing-a-core-dump_assembly_managing-kernel-command-line-parameters-with-uki#running-and-exiting-the-crash-utility_analyzing-a-core-dump), [\[kernel.org\]](https://www.kernel.org/doc/html/latest/admin-guide/kdump/kdump.html)

For mission-critical workloads, the inability to trigger a guest crash dump before resorting to reset or power-off is a significant operational gap. Red Hat’s kdump guide explicitly emphasizes that crash dump data is especially important in business-critical environments. Operational guidance from multiple vendors similarly recommends sending an NMI to a hung VM in order to force dump generation and preserve state for later root-cause analysis before taking more destructive recovery actions. This need is not limited to Linux: Microsoft’s guidance likewise underscores the importance of collecting crash dumps in Windows environments. [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_monitoring_and_updating_the_kernel/installing-kdump_assembly_managing-kernel-command-line-parameters-with-uki#what-is-kdumpinstalling-kdump), [\[learn.microsoft.com\]](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/generate-a-kernel-or-complete-crash-dump), [\[community.ibm.com\]](https://community.ibm.com/community/user/blogs/sachin-bappalige/2025/07/02/vmcore-crash-analysis-in-enterprise-power-system)

KubeVirt currently has a gap here compared to mainstream virtualization platforms. Hyper-V exposes `Debug-VM -InjectNonMaskableInterrupt`; VMware vSphere/ESXi exposes `Send_NMI_To_Guest` and `vmdumper ... nmi`; libvirt exposes `virsh inject-nmi`. Users migrating VM operational practices into KubeVirt reasonably expect an equivalent platform-native function instead of requiring direct access to launcher internals or host-side tools. [\[learn.microsoft.com\]](https://learn.microsoft.com/en-us/powershell/module/hyper-v/debug-vm?view=windowsserver2025-ps), [\[knowledge.broadcom.com\]](https://knowledge.broadcom.com/external/article/301246/how-to-send-nmi-to-guest-os-on-esxi-6x-o.html), [\[docs.redhat.com\]](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/7/html/virtualization_deployment_and_administration_guide/sect-generic_commands-injecting_nmi)

## Goals

- Provide `virtctl inject-nmi <vmi>` to inject an NMI into a VMI.
- Add a new VMI subresource under `subresources.kubevirt.io`.
- Implement the operation end-to-end using the existing KubeVirt runtime subresource architecture.
- Enable injecting an NMI to a VMI by virt-launcher through libvirt.

## Non Goals

- This proposal does not define guest OS configuration for crash dump generation.
- This proposal does not guarantee that every guest OS will react identically to an injected NMI.
- This proposal does not replace or redesign the existing `memory-dump` feature.
- This proposal does not standardize guest-side dump collection or post-processing tools.

## Definition of Users

* VM owner.

### User Story 1: Hung a guest

As a VM owner, when a guest becomes stuck and unresponsive but the VMI remains alive, I want to inject an NMI so that the guest can generate a crash dump for later kernel analysis.

## Repos

* [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### High-level design

This proposal introduces a new VMI subresource:

`PUT /apis/subresources.kubevirt.io/<version>/namespaces/{namespace}/virtualmachineinstances/{name}/injectnmi`

and a corresponding CLI command:

`virtctl inject-nmi <vmi> [-n <namespace>]`

The command targets VirtualMachineInstance only.
A VM-level alias is intentionally out of scope; it can be added later if the community wants parity with pause/unpause VM resolution behavior.


The request path should follow the same architecture already used by runtime VMI subresources:

1. `virtctl` parses command arguments and calls the KubeVirt client.
2. client-go exposes a new VMI operation method.
3. `virt-api` authenticates and authorizes the request, validates the VMI state, and forwards the request to `virt-handler`.
4. `virt-handler` resolves the target VMI launcher socket and forwards the request over the existing cmd/gRPC channel.
5. `virt-launcher` delegates the operation to the domain manager.
6. the domain manager invokes the hypervisor backend to inject an NMI.

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
So `InjectNMIVMI` method also defined as same manner. 

## API Examples

### CLI

```bash
virtctl inject-nmi my-vmi -n test
```

### HTTP API

```http
PUT /apis/subresources.kubevirt.io/v1/namespaces/test/virtualmachineinstances/my-vmi/injectnmi
```

### Expected responses

* `202 Accepted` when the request is accepted and dispatched
* `403 Forbidden` the user is not authorized to perform this operation on the VMI
* `404 Not Found` if the VMI does not exist
* `409 Conflict` the VMI object exists but is not in a state where a running domain can receive NMI.
* `500 Internal Server Error` if the backend request cannot be delivered

## Alternatives

### Alternative 1: Exec into `virt-launcher` and use libvirt or host-side tooling directly

Doesn't fit the user story.
This is operationally fragile, requires users to know internal implementation details, and breaks KubeVirt’s API abstraction model.

### Alternative 2: Reset or power-cycle the guest

Doesn't fit the user story.
While reset or power-off may recover a VM, vendor operational guidance consistently treats NMI-triggered dump collection as preferable when the objective is root-cause analysis of a hang. Resetting without first attempting dump capture can lose important diagnostic state. 

## Scalability

This is a per-VMI, stateless control-plane request that follows the same request/forwarding path as existing runtime subresources. It is not expected to introduce new scalability characteristics beyond those already present for VMI runtime operations. Existing runtime subresource architecture already supports similar requests like `pause`, `freeze`, `reset`, and `softreboot`. 

## Update/Rollback Compatibility

This new inject-nmi sub-command should surface a clear server-side error when used against older clusters, since the old clusters will not recognize it.

## Functional Testing Approach

Testing should include:

1. unit tests for `virtctl` command parsing and client invocation
2. unit tests for client-go method wiring
3. unit tests for `virt-api` request validation and routing
4. unit tests for `virt-handler` lifecycle request handling
5. unit tests for launcher cmd-server and domain-manager dispatch
6. end-to-end tests that verify:
   * the request is accepted for a running VMI
   * the request is rejected for a non-running VMI
   * the request flows through all KubeVirt layers
   * an event is emitted on success/failure

## Implementation History

## Graduation Requirements

### Beta

- Implements end-to-end path of to inject nmi to a VMI
  - Add API subresource and backend wiring
  - Add `virtctl inject-nmi`
  - Support for the default hypervisor path
- Add unit tests and e2e request-path validation
- Document guest prerequisites and non-guarantees

### GA

- User docs merged
- No known major bugs in default backend
