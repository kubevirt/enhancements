# VEP #259: Raw I/O Support for LUN Disks

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds a `rawio` boolean field to the KubeVirt disk API for LUN-type disks. When enabled, the field sets `rawio='yes'` on the libvirt domain disk element and grants `CAP_SYS_RAWIO` to the virt-launcher pod. The `virt-controller` SecurityContextConstraint (SCC) is also updated to permit this capability.

## Motivation

Enterprise storage environments commonly use dedicated management VMs that communicate with external storage arrays over SCSI protocols. These VMs issue privileged SCSI commands — such as mode page configuration, diagnostic routines, defect management, and firmware updates via `WRITE BUFFER` — to manage the lifecycle of storage devices. This is a standard operational pattern in data centers where the storage management plane runs inside VMs rather than requiring physical access to the host or out-of-band management interfaces.

KubeVirt currently supports exposing volumes as LUN devices (`disk.device: lun`), which allows VMs to issue SCSI commands via the SG_IO interface. However, the kernel's SCSI command filter divides commands into unrestricted and restricted groups. Restricted commands — including maintenance operations (`WRITE BUFFER`, `SEND DIAGNOSTIC`), vendor-specific commands, and service actions — are blocked unless the process has `CAP_SYS_RAWIO`. This means storage management VMs migrated to KubeVirt lose the ability to perform the privileged SCSI operations they were designed for.

Today there is no way for a KubeVirt user to request raw I/O access for a LUN disk. Adding opt-in `rawio` support closes this gap while keeping the elevated privilege gated behind a feature gate and an explicit per-disk opt-in, so that the broader security posture of KubeVirt is not affected for workloads that do not need it.

## Goals

- Allow users to opt in to raw I/O for LUN-type disks via a new API field so that VMs can issue restricted SCSI commands to provisioned devices.
- Ensure the feature does not grant capabilities beyond what the user's namespace PSA level and SCC bindings already permit.
- Gate the feature behind a feature gate (`RawIO`) until GA.

## Non Goals

- Granting `CAP_SYS_RAWIO` globally to all virt-launcher pods. The capability is only added when explicitly requested.
- Supporting `rawio` for non-LUN disk device types (e.g., `disk`, `cdrom`). Libvirt only supports `rawio` on `device='lun'`.
- Modifying the `sgio` (SCSI Generic I/O) filtering attribute. The `sgio` setting is orthogonal and can be addressed separately.
- Changes to PodSecurityPolicy (PSP). KubeVirt does not use PSP, and PSP has been removed from Kubernetes as of v1.25.
- Implementing the validating webhook for non-OpenShift clusters. On upstream Kubernetes, there is no SCC layer and no per-user capability restrictions by default. PSA enforcement at the namespace level is sufficient.

## Definition of Users

- **Storage administrators** who operate storage array management VMs that require privileged SCSI access to manage external storage devices.
- **Cluster administrators** who need to understand and control the security implications of granting raw I/O access.

## User Stories

- As a storage administrator, I want to run a storage array management VM on KubeVirt with LUN passthrough, so that the VM can issue privileged SCSI commands (mode page configuration, diagnostics, firmware updates) to manage the storage array — the same operations it performed on the previous bare-metal or traditional virtualization platform.
- As a storage administrator, I want to update firmware on SCSI storage devices from within my VM using standard tools like `sg_write_buffer`, so that I can manage device lifecycle without physical or out-of-band access to the storage hardware.
- As a storage administrator, I want to issue vendor-specific SCSI commands (e.g., `SEND DIAGNOSTIC`, `READ DEFECT DATA`, vendor-specific mode pages) to a passthrough LUN device from within my VM for ongoing storage health monitoring and management.
- As a cluster administrator, I want raw I/O support to be gated behind a feature gate, so that I can control whether this capability is available on my cluster.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### API Changes

A new optional boolean field `rawio` is added to the `LunTarget` struct (the per-disk configuration under `spec.domain.devices.disks[].lun`):

```go
type LunTarget struct {
    // ...existing fields...

    // Rawio indicates whether the disk should have raw I/O access enabled.
    // When set to true, the libvirt domain will have rawio='yes' on the disk
    // element, and the virt-launcher pod will be granted CAP_SYS_RAWIO.
    // Requires the RawIO feature gate.
    // +optional
    Rawio *bool `json:"rawio,omitempty"`
}
```

### Validation

- A webhook validates that `rawio` is only set when `disk.device` is `"lun"`. Setting `rawio` on any other device type is rejected.
- A webhook rejects `rawio: true` on create (and on update if not already set) when the `RawIO` feature gate is disabled. This prevents users from setting a field that would be silently ignored.
- If a VM already has `rawio: true` set and the feature gate is subsequently disabled, the field is allowed to persist on updates (ratcheting) to avoid breaking existing manifests.

### Libvirt Domain Generation

When `rawio: true` is set on a LUN disk and the `RawIO` feature gate is enabled, the domain converter in virt-launcher sets `rawio='yes'` on the corresponding `<disk>` element in the libvirt domain XML. If the feature gate is disabled, the `rawio` field is ignored even if set on the VMI.

```xml
<disk type='block' device='lun' rawio='yes'>
  ...
</disk>
```

### virt-launcher Pod Security

When any disk on a VMI has `rawio: true` and the `RawIO` feature gate is enabled, virt-controller adds `CAP_SYS_RAWIO` to the virt-launcher container's security context. If the feature gate is disabled, the capability is not added regardless of the field value.

```yaml
securityContext:
  capabilities:
    add:
      - SYS_RAWIO
```

This is only added when needed — virt-launcher pods for VMIs without `rawio` disks are unaffected.

### SecurityContextConstraint (SCC) Changes

The `kubevirt-controller` SCC must be updated to include `SYS_RAWIO` in `allowedCapabilities` (not `defaultCapabilities`). This is a one-time deployment change that permits virt-controller to request this capability for virt-launcher pods when needed. Pods that do not set `rawio: true` on any disk are unaffected.

### Validating Webhook for SCC Capability Enforcement (OpenShift)

Adding `SYS_RAWIO` to the `kubevirt-controller` SCC means virt-controller can create virt-launcher pods with that capability. Since virt-controller creates pods directly (rather than through a Deployment or ReplicaSet), OpenShift's SCC admission evaluates virt-controller's service account, not the pod's. Without additional enforcement, any user who can create a VM could get `CAP_SYS_RAWIO` on their virt-launcher pod regardless of whether their service account permits it.

To mitigate this, a **validating admission webhook** is added that fires on pod creation for virt-launcher pods. The webhook uses the OpenShift `PodSecurityPolicySubjectReview` API (`security.openshift.io/v1`) to check whether the **pod's service account** — not virt-controller's service account — is permitted by its SCC bindings to use the requested capabilities. If the pod's service account does not have an SCC allowing `SYS_RAWIO`, the pod creation is denied.

```yaml
apiVersion: security.openshift.io/v1
kind: PodSecurityPolicySubjectReview
spec:
  template:
    spec:
      containers:
      - name: compute
        securityContext:
          capabilities:
            add: ["SYS_RAWIO"]
  user: "system:serviceaccount:<namespace>:<pod-sa>"
  groups:
  - "system:serviceaccounts"
  - "system:serviceaccounts:<namespace>"
status:
  allowedBy:
    name: <scc-name>  # nil if no SCC permits the capability
```

This approach has two key properties:

1. **Analogous to Deployment behavior.** When a ReplicaSet controller creates a pod in OpenShift, the SCC admission controller automatically checks the pod's service account, not the controller's. Since virt-controller creates pods directly, this automatic check does not apply — the webhook replicates it.

2. **Robust against mutation.** Because this is a validating (not mutating) webhook, it runs after all mutating webhooks in the admission chain. It evaluates the final pod spec, so any capabilities injected by a mutating webhook are also caught.

The webhook is **OpenShift-specific**. On upstream Kubernetes there is no SCC layer, and any user with pod creation RBAC can already request any capability. If a cluster uses Pod Security Admission (PSA), enforcement is at the namespace level — `SYS_RAWIO` is blocked for all pods in a `baseline` or `restricted` namespace regardless of who creates them.

### Pod Security Standards (PSS) Impact

`CAP_SYS_RAWIO` is not in the set of capabilities permitted by the `baseline` or `restricted` Pod Security Standards. When `rawio: true` is requested, the virt-launcher pod will require the `privileged` security level. This should be noted in documentation so that cluster administrators using Pod Security Admission (PSA) enforcement can configure their namespaces accordingly.

### Feature Gate

The feature is gated behind `RawIO`. The gate is checked in two places:

1. **Webhook (API layer):** Rejects new `rawio: true` fields when the gate is disabled, preventing users from setting a field that would have no effect. Existing values are allowed to persist through updates (ratcheting).
2. **Controller and domain converter:** Ignore `rawio: true` when the gate is disabled, ensuring that disabling the gate effectively revokes the capability for all VMs on their next pod creation, even if the field is still present in the spec.

## API Examples

### Enabling raw I/O on a LUN disk

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: storage-mgmt-vm
spec:
  template:
    spec:
      domain:
        devices:
          disks:
            - name: scsi-lun
              lun:
                bus: scsi
                rawio: true
      volumes:
        - name: scsi-lun
          persistentVolumeClaim:
            claimName: my-scsi-device
```

### Without raw I/O (default behavior, unchanged)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: standard-lun-vm
spec:
  template:
    spec:
      domain:
        devices:
          disks:
            - name: scsi-lun
              lun:
                bus: scsi
      volumes:
        - name: scsi-lun
          persistentVolumeClaim:
            claimName: my-scsi-device
```

## Security Considerations

`CAP_SYS_RAWIO` is a broad Linux capability. Beyond enabling raw SCSI commands, it gates access to several privileged operations. This section analyzes each attack surface and explains why, in the context of a virt-launcher container, the capability is effectively scoped to SCSI command filtering on explicitly provisioned devices.

### Attack surface analysis

**`/dev/mem` (physical memory read/write):** In an unrestricted environment, this device allows reading and writing arbitrary physical memory. However, in a virt-launcher pod this is blocked by multiple independent layers: (1) the container's device cgroup uses a deny-all policy and does not whitelist `/dev/mem` — the kernel blocks `open()` at the cgroup level regardless of capabilities; (2) the device node does not exist in the container's tmpfs `/dev`; (3) `CONFIG_STRICT_DEVMEM` (enabled by default since Linux 4.16 and on RHEL/Fedora) restricts access to PCI/BIOS regions only; (4) `CONFIG_IO_STRICT_DEVMEM` further restricts to unclaimed I/O regions; (5) SELinux's `container_t` context blocks access on OpenShift.

**`/dev/kmem` (kernel virtual memory):** Removed from the upstream kernel entirely in version 5.13 (2021). Does not exist on any supported platform.

**`/proc/kcore` (kernel memory dump, read-only):** Masked by the OCI runtime — bind-mounted to `/dev/null` in all standard container runtimes (CRI-O, containerd). Reading it returns nothing.

**`iopl()` / `ioperm()` (I/O port access):** These syscalls grant direct hardware port access, which could in theory be used to reprogram DMA engines via PCI configuration ports (0xCF8/0xCFC) and achieve arbitrary physical memory access. However, both syscalls are **blocked by the default seccomp profile** in CRI-O and containerd. The process receives EPERM before the capability check runs. KubeVirt also ships a custom seccomp profile for virt-launcher (since v0.59.0).

**`/dev/cpu/*/msr` (model-specific registers):** Not accessible — blocked by the device cgroup, and the device nodes do not exist in the container.

**`/proc/sys/vm/mmap_min_addr` (low-address memory mapping):** `/proc/sys` is mounted read-only in containers, preventing modification.

**`FIBMAP` ioctl (file block mapping):** Read-only information disclosure about disk block layout. Low risk — the equivalent `FIEMAP` ioctl provides the same information without any capability requirement.

**`/proc/bus/pci` (PCI configuration space):** Masked by the OCI runtime and blocked by the device cgroup. PCI device nodes are not present in the container.

**`hpsa(4)` / `cciss(4)` devices (host RAID controllers):** Their device nodes are not present in the container (blocked by device cgroup deny-all policy), so the `CAP_SYS_RAWIO`-gated ioctls are unreachable.

**Other device-specific operations:** `CAP_SYS_RAWIO` gates ioctls on several other device types. The device cgroup deny-all policy ensures those devices are not accessible in the container.

**Raw SCSI commands (SG_IO):** This is the intended use case. The device cgroup restricts which block devices are visible to the container, so raw SCSI access is scoped to the specific LUN devices explicitly provisioned for the VM.

### Effective scope in virt-launcher

In summary, `CAP_SYS_RAWIO` in a virt-launcher pod is inert for all attack surfaces except SCSI command filtering, because the actual exploitation vectors require device access or syscall availability that containers block through independent mechanisms (device cgroup, seccomp, masked paths, strict devmem, read-only procfs). The capability is necessary but not sufficient for any of the dangerous operations it theoretically gates. Its practical effect is limited to allowing restricted SCSI commands on explicitly provisioned LUN devices.

### Administrative controls

1. **Opt-in only:** The capability is never added unless the user explicitly sets `rawio: true` on a LUN disk.
2. **Feature gate:** The entire feature is gated, giving cluster administrators control over availability.
3. **Validating webhook (OpenShift):** A validating webhook checks that the virt-launcher pod's service account has an SCC permitting the requested capabilities, preventing privilege escalation through virt-controller or mutating webhooks. See [Validating Webhook for SCC Capability Enforcement](#validating-webhook-for-scc-capability-enforcement-openshift).
4. **Documentation:** Users are advised that enabling `rawio` elevates the privilege level of the virt-launcher pod and takes it out of `restricted` PSS compliance.

## Alternatives

### Use `unpriv_sgio` instead of `CAP_SYS_RAWIO`

The kernel provides a per-device sysfs knob (`/sys/dev/block/<maj:min>/queue/unpriv_sgio`) that allows unprivileged SCSI Generic I/O without requiring `CAP_SYS_RAWIO`. This would avoid granting a broad capability.

However, this approach has significant challenges in KubeVirt:
- sysfs is mounted read-only inside containers, even privileged ones (see [kubevirt/kubevirt#6507](https://github.com/kubevirt/kubevirt/issues/6507)).
- virt-handler could set this value on the host, but it adds complexity: resolving device major:minor, setting the knob before VM start, clearing it on stop and migration, and coordinating between virt-controller and virt-handler.
- Having virt-handler toggle host sysfs knobs to grant unprivileged processes access to restricted SCSI commands is fundamentally privilege escalation outside the standard Kubernetes capability model.

### Annotation-based configuration

An annotation like `kubevirt.io/rawio-disks: "disk1,disk2"` could be used instead of an API field.

This was rejected because:
- Annotations are not validated by the API schema, making misconfiguration harder to detect.
- Security-sensitive settings should be explicit and visible in the API, not hidden in annotations.
- It would not integrate cleanly with the existing disk configuration structure.

### KubeVirt Structured Plugins ([VEP #190](https://github.com/kubevirt/enhancements/issues/190))

The structured plugin model (VEP #190) could partially address this use case. A plugin could handle the domain XML mutation (setting `rawio='yes'` on LUN disks) via a domain hook and the pod security context mutation (adding `CAP_SYS_RAWIO` to the container's capability bounding set) via an admission policy.

However, there is a gap around Linux capability propagation. KubeVirt propagates capabilities to the QEMU process via a chain of ambient capabilities and file capabilities through virt-launcher-monitor, virt-launcher, and virtqemud. Today this chain is hardcoded to `CAP_NET_BIND_SERVICE`. To support a new capability like `CAP_SYS_RAWIO`, all three points in the chain need updating — this cannot be achieved purely through a plugin without core KubeVirt changes.

Additionally, the `virt-controller` SCC would still need to be updated manually to permit the new capability, which falls outside the plugin model's scope.

If the plugin model evolves to support declaring additional Linux capabilities that get wired into the ambient capability chain, this feature could potentially be reimplemented as a plugin in the future.

### Cluster-wide SCC granting `CAP_SYS_RAWIO` to all virt-launcher pods

This would simplify the implementation by always granting the capability.

This was rejected because:
- It violates the principle of least privilege.
- Most workloads do not need raw I/O, and granting it universally expands the attack surface for all VMs.

## Scalability

This feature has negligible scalability impact. It adds a boolean field to the disk spec and conditionally adds a capability to the pod security context. There is no per-operation overhead beyond the initial pod creation.

## Update/Rollback Compatibility

- **Update:** Existing VMs are unaffected. The `rawio` field defaults to `false`/unset, preserving current behavior.
- **Rollback:** If the feature gate is disabled after a VM was created with `rawio: true`, the VM will continue to run but will not have `CAP_SYS_RAWIO` on restart. The field will be rejected by the webhook on new VM creation. Existing VMI objects with the field set will still be present but the capability will not be granted on the next pod creation.

## Functional Testing Approach

- Unit tests for webhook validation (reject `rawio` on non-LUN disks, reject when feature gate is disabled).
- Unit tests for domain converter (verify `rawio='yes'` is set in libvirt XML).
- Unit tests for virt-controller pod template generation (verify `CAP_SYS_RAWIO` is added to security context).
- Functional test issuing a SCSI command (e.g., INQUIRY via `sg_inq`) from within a VM to a LUN disk with `rawio: true`, confirming it succeeds.
- Functional test confirming that the same command fails on a LUN disk without `rawio`.

## Implementation History

<!--
To be filled in as the feature progresses.
-->

## Graduation Requirements

### Alpha
- [ ] Feature gate `RawIO` guards all code changes
- [ ] `rawio` field added to disk API with webhook validation
- [ ] virt-launcher pod gets `CAP_SYS_RAWIO` when `rawio: true` is set
- [ ] Libvirt domain XML includes `rawio='yes'` on the disk element
- [ ] `virt-controller` SCC updated to allow `SYS_RAWIO` in `allowedCapabilities`
- [ ] Validating webhook for virt-launcher pod capability enforcement on OpenShift (using `PodSecurityPolicySubjectReview`)
- [ ] Unit tests in place
- [ ] Functional test issuing a SCSI command (e.g., INQUIRY via `sg_inq`) from within a VM to a LUN disk with `rawio: true`, confirming it succeeds
- [ ] Functional test confirming that the same command fails on a LUN disk without `rawio`
- [ ] Documentation noting PSS/SCC implications

### Beta
- [ ] User guide documentation with examples and security guidance
- [ ] Feedback from alpha users incorporated

### GA
- [ ] Feature gate `RawIO` removed; feature enabled by default
- [ ] No outstanding bug reports related to raw I/O
- [ ] Stable API; field is part of the supported VM spec
