# VEP #451: Relaxing CPU Compatibility POD Scheduling Constraints for Live Migration

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Add a `relaxCPUCompatibility` flag to `VirtualMachineInstanceMigration` (per-VM) and to the global `KubeVirt` CR configuration. When enabled, KubeVirt skips injecting `cpu-model.node.kubevirt.io/` and `cpu-feature.node.kubevirt.io/` labels into the migration target pod's nodeSelector, allowing live migration to proceed even when nodes report subtle CPU capability differences.

## Motivation

During KubeVirt upgrades, nodes must be drained, which requires live migrating running VMs. However, a bug in the current version's CPU nodeSelector injection logic can block live migration — even when the guest would run correctly on the target node. This creates a circular dependency: upgrading requires draining; draining requires live migration; live migration is blocked by the very bug that the upgrade would fix. The only escape without this feature is to shut down VMs, causing production downtime.

This has been reported as a real production problem by multiple users:
- https://github.com/kubevirt/kubevirt/issues/16386#issuecomment-4510530565
- https://github.com/kubevirt/kubevirt/issues/16386#issuecomment-4520652199

## Goals

- Provide a per-VMIM flag (`spec.relaxCPUCompatibility: true`) to skip CPU feature/model label injection into the migration target pod's nodeSelector.
- Provide a global `KubeVirt` CR flag as a cluster-wide fallback for cases where many VMs are affected.
- Emit a warning event on the VMIM resource to communicate that CPU compatibility enforcement is disabled.

## Non Goals

- Guarantee that live migration will succeed without CPU compatibility checks; hardware incompatibilities at the QEMU level are not addressed. If the target node genuinely lacks a CPU instruction or feature that the guest VM actively uses, the VM may fail to migrate, crash, or exhibit undefined behavior at runtime.
- Replace or deprecate the existing node-labeller mechanism.
- Modify the CPU compatibility behavior for non-migration workloads.

## Definition of Users

- **Cluster operators / platform engineers** performing KubeVirt upgrades who need to drain nodes without shutting down production VMs.

## User Stories

- As a cluster operator, I want to trigger a live migration for a VM that is currently blocked by a CPU nodeSelector mismatch, so that I can drain a node during a KubeVirt upgrade without downtime.
- As a cluster operator managing many affected VMs, I want a global setting so I do not need to set the flag on every individual VMIM object.

## Repos

- `kubevirt/kubevirt` — API type changes and migration handler logic

## Design

Two new optional boolean fields are introduced:

1. **Per-VMIM**: `VirtualMachineInstanceMigration.spec.relaxCPUCompatibility *bool`
2. **Global**: `KubeVirtConfiguration.relaxCPUCompatibility *bool` (in the `KubeVirt` CR)

**Precedence logic** (per-VMIM overrides global when explicitly set):

| Per-VMIM | Global | Effective |
|----------|--------|-----------|
| nil      | nil    | false     |
| nil      | false  | false     |
| nil      | true   | true      |
| false    | *      | false     |
| true     | *      | true      |

When the effective value is `true`, the migration handler skips injecting:
- `cpu-model.node.kubevirt.io/*`
- `cpu-feature.node.kubevirt.io/*`
- host-model derived labels (`cpu-model-migration.node.kubevirt.io/*`)
- CPU vendor labels

Only `spec.addedNodeSelector` (if set) is applied. A `Warning` event is emitted on the VMIM object to alert operators that CPU compatibility is not enforced.

**Risk and operator responsibility**: This flag is intended for scenarios where the CPU label mismatch is a false negative — i.e., the labels diverged due to a node-labeller bug or library update, but the underlying CPU hardware is compatible. Operators who enable this flag accept responsibility for verifying that the target node's CPU is actually capable of running the guest. If genuine hardware-level CPU incompatibilities exist (e.g., a required instruction is absent on the target), the migrated VM may crash or behave incorrectly. The emitted `Warning` event serves as the explicit acknowledgement mechanism.

## API Examples

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstanceMigration
metadata:
  name: my-migration
  namespace: default
spec:
  vmiName: my-vm
  relaxCPUCompatibility: true
```

```yaml
# Global setting in KubeVirt CR
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    relaxCPUCompatibility: true
```

## Alternatives

- **Disable node-labeller cluster-wide**: Requires multiple manual steps, affects all workloads, and can leave nodes in an inconsistent state if node-labeller is re-enabled.
- **Manually patch node labels**: Error-prone, affects other workloads, and temporary.
- **Shut down VMs**: Causes production downtime — defeats the purpose of live migration.
- **Wait for a patched release**: Not feasible; the upgrade itself is blocked.

## Scalability

The change only affects the migration target pod scheduling path. No additional API calls or watches are introduced. Impact is negligible.

## Update/Rollback Compatibility

- The new fields are optional (`*bool`), defaulting to `nil` (existing behavior unchanged). Rolling back to a version without this field is safe; the field is ignored.
- No changes to existing VMI or VMIM status fields.

## Functional Testing Approach

- **Unit tests**: Verify the precedence matrix (per-VMIM vs. global) for all combinations; verify that CPU labels are absent from the target pod nodeSelector when `relaxCPUCompatibility=true`.
- **E2E tests**:
  - **Per-VMIM**: Set `relaxCPUCompatibility: true` on a single VMIM object (global flag unset), assert migration completes and a Warning event is emitted; set `relaxCPUCompatibility: false` on a VMIM object with the global flag enabled, assert the per-VMIM override takes effect and migration uses normal CPU label injection.
  - **Global**: Enable `relaxCPUCompatibility: true` in the KubeVirt CR with no per-VMIM override, assert all affected VMIMs on the cluster migrate successfully without CPU nodeSelector labels; disable the global flag and assert existing CPU label injection behavior is restored.
  - **Precedence**: Cover the full combination matrix (per-VMIM nil/true/false × global nil/true/false) in at least one representative E2E scenario to confirm the precedence table is correct end-to-end.

## Implementation History

- 2026-08-27: Feature request opened — kubevirt/kubevirt#18954
- 2026-08-31: Initial implementation PR opened — kubevirt/kubevirt#18979

## Graduation Requirements

<!--
Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- [ ] `relaxCPUCompatibility` field added to `VirtualMachineInstanceMigration.spec`
- [ ] `relaxCPUCompatibility` field added to `KubeVirtConfiguration`
- [ ] Precedence logic implemented in migration handler
- [ ] Warning event emitted when flag is active
- [ ] Unit tests covering all precedence combinations
- [ ] At least one E2E test

### Beta

- [ ] User-guide documentation updated
- [ ] No critical bugs reported during Alpha

#### On-By-Default Readiness

<!--
Beta features are enabled by default.
In this section, please specify what needs to be done in order for the VEP to be ready to be enabled by default.
-->

The field defaults to `false`/`nil` and is purely opt-in; no on-by-default change is needed for Beta unless the community decides otherwise.

### GA

- [ ] Stable across at least two releases with no reported regressions
