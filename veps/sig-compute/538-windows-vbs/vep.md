# VEP #538: Windows VBS with native Virtual Secure Mode

## VEP Status Metadata

Tracking issue: [kubevirt/enhancements#538](https://github.com/kubevirt/enhancements/issues/538)

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.11.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Expose native Virtual Secure Mode (VSM) and XMM hypercall features through the
VM/VMI API so Windows guests can use Virtualization-Based Security (VBS), including
Hypervisor-protected Code Integrity (HVCI). Alpha targets the CentOS Stream 10
kernel and default QEMU/libvirt stack supplied by KubeVirt v1.11.0 through
[VEP 210](https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/210-centos-stream-10/vep.md).

## Motivation

Native VSM offers an alternative to nested virtualization for Windows VBS.
KubeVirt needs explicit feature configuration and validation; performance benefits
must be established through matched workload comparisons.

## Goals

- Expose VSM and XMM hypercall input/output through the existing feature API.
- Validate CPU and enlightenment dependencies without changing existing VM defaults.
- Qualify Windows VBS/HVCI on the default CentOS Stream 10 stack.

## Non Goals

- Configure Windows security policy or enable general nested Hyper-V workloads.
- Change default CPU models or Hyper-V enlightenment selection.
- Deliver custom kernels or alternate launchers.
- Support live migration or memory-state save/restore in alpha.

## Definition of Users

Windows VM owners, cluster administrators and integrators qualifying Windows workloads.

## User Stories

- As a VM owner, I can request native VSM and verify VBS/HVCI inside Windows.
- As an administrator, I can select compatible nodes and diagnose unsupported configurations.
- As an existing user, my VM's behavior remains unchanged when I omit the new fields.

## Repos

- `kubevirt/enhancements`: proposal and tracking.
- `kubevirt/kubevirt` and `kubevirt/api`: API, validation, conversion and tests.
- `kubevirt/user-guide`: configuration and limitations.
- `kubevirt/common-instancetypes`: optional Windows preferences.

## Design

### Feature API

Add optional `FeatureState` fields:

| Field under `spec.domain.features` | Libvirt XML |
| --- | --- |
| `hyperv.vsm` | `hyperv/vsm` |
| `hyperv.xmmInput` | `hyperv/xmm_input` |
| `kvm.xmmOutput` | `kvm/xmm_output` |

Omitted fields add no XML. A present feature defaults `enabled` to true; preserve
explicit false through defaulting, conversion and serialization.
A proposed `VSM` gate protects the fields during alpha, including VM templates,
direct VMIs and preferences if added. Follow existing feature lifecycle conventions.

### Hyper-V enlightenments

Retain explicit feature selection. For alpha, require both XMM features with
enabled VSM and reject XMM-only enablement or combination with
`hypervPassthrough`. This limits alpha to the qualified VSM profile; it does not
assert that XMM features inherently require VSM.
The proposed reference profile enables VAPIC, VPIndex, SyNIC, direct synthetic
timers, IPI, extended TLB flush and the Hyper-V clock, with EVMCS disabled.

Confirm mandatory enlightenment dependencies before alpha and qualify synthetic
timers per Windows version. Use CRD CEL rules for cross-field constraints, scoped
to the new fields so existing VMs remain valid. Reserve webhook validation for
checks CEL cannot express, including cluster feature-gate state. Reject conflicts
without overriding explicit choices.

### CPU model handling

The initial qualification target is amd64/KVM with an Intel host-passthrough CPU,
guest `vmx` disabled and EVMCS disabled. Confirm these restrictions before alpha;
other CPU models, vendors and nested-virtualization combinations need qualification.
Reject unsupported architectures, emulation and contradictory settings.

CPU host-model and Hyper-V host-model remain unchanged.

### Kernel and userspace requirements

The CentOS Stream 10 kernel is expected to provide native VSM support for alpha,
alongside the default QEMU/libvirt stack delivered through VEP 210. Validate this
combination and record exact component versions. KubeVirt's domain types and
libvirtxml conversions must preserve the new fields.

Use existing scheduling controls to select compatible nodes. Admission validates
configuration; node-level runtime checks verify capabilities. Report missing
support through the existing `Synchronized=False` condition and warning-event
path, with a stable reason and a message naming the missing capability. Never
silently omit a requested feature.

### Guest configuration and lifecycle

Document each supported Windows version's firmware, Secure Boot, SMM and persistent
TPM requirements using existing APIs. Guest administrators enable VBS/HVCI and
verify its running state inside Windows.

For alpha, report `LiveMigratable=False` through the existing VMI condition, with
a stable VSM reason and explanatory message. Reject migration and incompatible
eviction configuration, and block unsupported memory-state save/restore. Feature
changes require cold restart. Document node-maintenance and disk-only backup
limitations.

## API Examples

VMI fragment; for a VirtualMachine, place under `spec.template`. A complete Windows
profile also needs the agreed dependencies, firmware, TPM and scheduling settings.

```yaml
spec:
  architecture: amd64
  domain:
    cpu:
      model: host-passthrough
      features:
        - name: vmx
          policy: disable
    features:
      hyperv:
        vsm:
          enabled: true
        xmmInput:
          enabled: true
        evmcs:
          enabled: false
      kvm:
        xmmOutput:
          enabled: true
```

## Alternatives

### Hyper-V host-model

Libvirt can discover and expand supported enlightenments, avoiding an API field
for each feature and reducing KubeVirt's dependency logic. This is tracked in
[issue #18359](https://github.com/kubevirt/kubevirt/issues/18359), separately from
CPU host-model.

An unconditional switch would change existing VMs' exposed features on subsequent
VMI creation or cold restart, even without spec changes. Upgrades or placement on
another node could also change the feature set. Explicit overrides, including
features disabled for compatibility, must remain effective.

Migration must preserve the source's expanded feature set, including VSM/XMM
through XML round trips, and verify destination compatibility. Expanding features
independently at the destination risks changing the guest interface. Libvirt
expansion does not establish support for transferring VSM state; mixed node
capabilities and userspace versions further constrain migration.

Host-model therefore needs opt-in or an existing-VM compatibility strategy,
verified overrides, complete serialization, migration checks and upgrade/rollback
semantics. Explicit fields avoid making those broader changes a VSM prerequisite.

### Plugin or sidecar

These still require core support for validation, serialization and lifecycle
handling. Typed CEL also depends on the control plane's available domain fields.

### Single VBS switch

A complete profile would couple guest policy, CPU choices and enlightenments.
Extending the existing feature API is a smaller change.

## Does it belong to core KubeVirt?

Yes. Core KubeVirt owns domain feature configuration, validation, serialization
and lifecycle restrictions.

## Scalability

Fixed-size fields and local validation/conversion add no controller. Measure guest
resource overhead and document the migration restriction's maintenance impact.

## Update/Rollback Compatibility

Omitted fields preserve existing behavior. Verify control-plane/launcher version
skew cannot silently discard requested features.

Stop affected VMs before downgrade and follow a tested guest recovery procedure:
removing fields alone may conflict with Windows security policy or protected state.
Preserve disk, firmware and TPM identities across restart and rollback.

## Functional Testing Approach

- API/defaulting, gate and CEL validation, including XMM-only rejection and unchanged legacy VMs.
- XML placement and round trips, including explicit false and non-opt-in controls.
- Migration, eviction and memory-state restrictions.
- Capability exposure on the default CentOS Stream 10 stack, with asserted failure conditions, reasons and messages.
- Windows VBS/HVCI verification, cold boot, reboot, persistent identity and rollback.
- Matched performance comparisons recording hardware, software, configuration,
  security state, throughput, resource overhead, latency percentiles and boot time.

Keep test definitions upstream; full Windows execution may run in integrator CI.
Evaluate Windows Validation OS for upstream smoke coverage. Agree image access,
recurring execution and ownership before alpha.

## Implementation History

- 23 November 2026: initial VEP prepared.
- 24 November 2026: tracking issue #538 created.

## Graduation Requirements

### Alpha

- [ ] Design and target release approved; gated API and validation implemented.
- [ ] VSM/XMM verified on the CentOS Stream 10 kernel and default launcher from VEP 210.
- [ ] Supported CPU/Windows matrix and component versions documented.
- [ ] Serialization, version skew, failure handling and lifecycle restrictions tested.
- [ ] Windows VBS/HVCI and rollback acceptance passed; recurring test ownership agreed.

### Beta

- [ ] Stable API with recurring supported-matrix coverage.
- [ ] Diagnostics, upgrade/rollback and representative performance qualified.
- [ ] Remaining lifecycle limitations resolved or explicitly accepted.

#### On-By-Default Readiness

Default gate enablement preserves per-VM opt-in semantics and existing VM defaults,
with regression coverage across supported configurations.

### GA

- [ ] Supported hypervisor stack and lifecycle limitations accepted.
- [ ] Sustained functional, security, performance and upgrade coverage.
