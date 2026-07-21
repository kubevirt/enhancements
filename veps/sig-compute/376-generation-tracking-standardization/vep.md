# VEP #376: Standardize Generation Tracking for VM and VMI

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR): https://github.com/kubevirt/enhancements/issues/376
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Kubernetes defines a standard pattern for tracking controller sync status:
`metadata.generation` (incremented by the API server on every spec change) combined with
`status.observedGeneration` (written by the controller to record which generation it last
processed). [KEP-1623](https://github.com/kubernetes/enhancements/tree/master/keps/sig-api-machinery/1623-standardize-conditions)
extends this to the condition level via `metav1.Condition.ObservedGeneration`, allowing
consumers to know precisely which conditions are stale vs. fresh with respect to the
current spec.

KubeVirt's `VirtualMachine` (VM) and `VirtualMachineInstance` (VMI) resources deviate
from this standard in several ways: the VM's existing `status.observedGeneration` field
carries semantics that differ from what Kubernetes consumers expect; and neither
resource's conditions expose an `ObservedGeneration` field. This VEP proposes closing
these gaps in a backward-compatible manner. The absence of a virt-handler processing
signal on VMI is a known gap addressed separately (see Open Questions).

## Motivation

### VM `status.observedGeneration` carries non-standard semantics

The field `vm.status.observedGeneration` does **not** mean what Kubernetes consumers
expect. Per the Kubernetes convention, `status.observedGeneration` should record the
last generation the controller has processed. In KubeVirt, this field instead records the
VM generation that was stamped on the VMI — reflecting "what VM generation does the
running VMI reflect" rather than "what generation has the VM controller processed."

The field with the standard meaning is `vm.status.desiredGeneration`, which is set to
`vm.Generation` on every reconcile by the VM controller. This inversion is a correctness
hazard: any tool that reads `status.observedGeneration` — including `kubectl wait`,
GitOps controllers, and monitoring dashboards — will draw the wrong conclusion about
whether the VM controller is in sync with the current spec.

### virt-handler has no observable generation signal on VMI (deferred)

`VirtualMachineInstanceStatus` has no `observedGeneration` field. When virt-handler
applies an in-place spec change, there is no standard mechanism for a consumer to
determine whether the change has been applied to the running domain. This gap is
acknowledged but not addressed in this VEP; see Open Questions for the structural
reasons it is deferred.

### VM and VMI conditions lack per-condition staleness information

Both resources use custom condition structs (`VirtualMachineCondition`,
`VirtualMachineInstanceCondition`) that do not expose an `ObservedGeneration` field.
Without it, tools cannot distinguish a stale `Ready=True` condition (evaluated against
an old spec) from a fresh one. This prevents correct interoperability with the ecosystem
convention established by KEP-1623.

### Generation annotation is a fragile internal mechanism

The VM controller carries the parent VM's generation across reconcile boundaries via a
VMI annotation (`VirtualMachineGenerationAnnotation`). This is an internal
implementation detail that has become load-bearing, evidenced by a back-fill path through
`ControllerRevision` (`patchVmGenerationFromControllerRevision`) that was added to
recover from cases where the annotation was missing.

## Goals

- Align generation tracking on VM and VMI resources with Kubernetes conventions so that
  standard tooling can correctly observe controller sync status.
- Provide consumers with a reliable, documented signal for whether the VM controller has
  considered the current VM spec in relation to the running VMI.
- Expose per-condition staleness information on VM and VMI status conditions.
- Eliminate reliance on the internal VMI generation annotation as a load-bearing
  mechanism.

## Non Goals

- Removing or renaming existing VM status fields (`observedGeneration`,
  `desiredGeneration`); backward compatibility must be preserved.
- Changing the generation-tracking behavior of the `KubeVirt` operator CR, which already
  follows the standard pattern.
- Defining a comprehensive new VM or VMI condition taxonomy beyond what is needed to
  replace the generation annotation.
- Changing how `metadata.generation` is incremented (controlled by the Kubernetes API
  server).

## Definition of Users

- **Cluster operators** using `kubectl wait`, GitOps tools (ArgoCD, Flux), or custom
  controllers to observe VM and VMI readiness after spec changes.
- **Platform engineers** building automation on top of KubeVirt APIs who rely on
  generation fields for correct change detection.
- **Monitoring and observability tooling** that reads conditions and their staleness to
  compute SLIs.

## User Stories

- As a platform engineer, I want to read a condition on a VM to know whether the VM
  controller has considered the current VM spec in relation to the running VMI, so that
  I can build correct automation without depending on an internal annotation.
- As an SRE, I want each VM and VMI condition to carry an `ObservedGeneration` so I can
  distinguish a stale condition (evaluated against an old spec) from one evaluated against
  the current spec.
- As a GitOps operator (ArgoCD, Flux), I want a reliable sync signal on a VM so that my
  controller can gate downstream actions on confirmed reconciliation without polling or
  timeouts.
- As a test or automation author, I want to wait until the VM controller has processed
  a spec change before validating the next step, so that my test does not race against
  an in-flight reconcile.
- As a UI developer, I want to surface whether a VM's running state reflects its current
  spec so that users can see at a glance whether a pending change has been enacted.

## Repos

- `kubevirt/kubevirt` — primary implementation: VM controller, VMI controller,
  virt-handler, API types
- `kubevirt/enhancements` — this VEP

## Design

### Condition type alignment with `metav1.Condition`

`VirtualMachineCondition` and `VirtualMachineInstanceCondition` are updated to embed
`metav1.Condition` inline, replacing the individually declared fields. The only
KubeVirt-specific field not present in `metav1.Condition` is `LastProbeTime`, which is
retained as a deprecated field alongside the embedded struct:

```go
type VirtualMachineCondition struct {
    metav1.Condition `json:",inline"`

    // Deprecated: LastProbeTime is not part of the standard condition type
    // and will be removed in a future release.
    // +nullable
    // +optional
    LastProbeTime metav1.Time `json:"lastProbeTime,omitempty"`
}

type VirtualMachineInstanceCondition struct {
    metav1.Condition `json:",inline"`

    // Deprecated: LastProbeTime is not part of the standard condition type
    // and will be removed in a future release.
    // +nullable
    // +optional
    LastProbeTime metav1.Time `json:"lastProbeTime,omitempty"`
}
```

This change is purely additive at the JSON level: all previously serialized fields
(`type`, `status`, `reason`, `message`, `lastTransitionTime`) remain present and
unchanged; `ObservedGeneration` is added as a new optional field; `LastProbeTime`
continues to be serialized but is marked deprecated.

Note: for `VirtualMachineCondition`, the Go-level `Type` field changes from the typed
alias `VirtualMachineConditionType` to `string` (as defined by `metav1.Condition`). Both
serialize identically in JSON. Existing call sites that pass a
`VirtualMachineConditionType` constant require a cast to `string`; no API consumers are
affected. The same applies to `VirtualMachineInstanceConditionType` in
`VirtualMachineInstanceCondition`.

**The struct embedding is an unconditional API type change.** It is not feature-gated:
`ObservedGeneration` is always present as a field in the Go struct (and therefore always
present in the API schema) regardless of any feature gate. The feature gate
`VMGenerationTracking` governs only whether controllers *populate* the field — when the
gate is disabled, controllers omit `ObservedGeneration` from condition writes, leaving it
at its zero value, which `metav1.Condition`'s `omitempty` tag causes to be absent from
serialized JSON.

Every condition write in the VM controller and VMI controller is updated to populate
`ObservedGeneration = vm.Generation` and `ObservedGeneration = vmi.Generation`
respectively when the feature gate is enabled. virt-handler condition writes similarly
populate `ObservedGeneration = vmi.Generation`.

**`VMGenerationTracking` feature gate scope.** The gate guards population of
`ObservedGeneration` on all condition writes and creation of the `VMIInSync` condition.
It does not gate the struct embedding itself, which is an unconditional type change.

**Using `apimeta` via an adapter.** `apimeta.SetStatusCondition` operates on
`*[]metav1.Condition` and cannot accept `*[]VirtualMachineCondition` directly. A
thin adapter bridges the two: it extracts the embedded `metav1.Condition` from each
element into a `[]metav1.Condition`, calls `apimeta.SetStatusCondition` on that slice,
then merges the result back into the original `[]VirtualMachineCondition`. Because
`LastProbeTime` is not part of `metav1.Condition` and is not touched by `apimeta`, the
round-trip is lossless — `LastProbeTime` is carried through unchanged from the original
entry, or left unset for newly inserted conditions.

### `VMIInSync` condition on VM

**Layer: VM controller → VMI.** This signal answers whether the VM controller has
accounted for the current VM generation in its reconcile against the running VMI. It
does not indicate whether virt-handler has applied any resulting VMI spec change to the
running domain; that is a known gap deferred to a future VEP (see Open Questions).

A new condition type `VMIInSync` is added to `VirtualMachine`. The VM controller sets
this condition when a VMI exists to record whether it was able to account for the current
VM generation against the running VMI:

- `VMIInSync=True` — the controller processed the current VM generation and successfully
  ensured the VMI reflects the expected state. This covers both the case where no
  VMI-affecting change was required and the case where a change was successfully applied.
- `VMIInSync=False` — the controller processed the current VM generation but was unable
  to bring the VMI to the expected state. This covers any failure in the reconcile flow
  that prevented the controller from completing the necessary update, regardless of
  whether the failure originated in the patch call itself or elsewhere in the reconcile
  logic. The `reason` and `message` fields carry the specific cause.
- Condition absent — no VMI is running (VM is stopped), or the VM is in the process of
  starting and the controller has not yet completed its first reconcile against the new
  VMI. The condition is removed when the VMI is deleted and re-created once a new VMI is
  observed.

**`Unknown` status is not used.** `metav1.Condition` permits `Unknown` for in-progress
states. The VM controller does not use it for `VMIInSync` because the controller always
resolves the condition to a definitive outcome (`True` or `False`) within a single
reconcile pass. There is no persistent in-flight state that outlives a reconcile. The
brief window between VMI creation and the first reconcile is represented by the condition
being absent rather than `Unknown`, consistent with the convention that absence means "not
yet determined."

The defined `reason` values are:

| `status` | `reason` | Meaning |
|---|---|---|
| `True` | `VMIObserved` | Controller successfully accounted for the VMI at the recorded generation |
| `False` | `ReconcileError` | Controller encountered an error that prevented it from completing the reconcile |

`VMIInSync` and `RestartRequired` are orthogonal conditions. When a non-hotpluggable
change is made, the VM controller accounts for it by setting `RestartRequired=True` and
still sets `VMIInSync=True` to record that it has processed the current generation.
A consumer must check both conditions independently: `VMIInSync` answers "has the
controller processed this generation?"; `RestartRequired` answers "does the running VMI
require a restart to reflect the new spec?".

The condition's `ObservedGeneration` is set to `vm.Generation` on every write.
Consumers use two complementary signals:

1. **Staleness**: compare `condition.observedGeneration` with `vm.metadata.generation`.
   If the condition's `observedGeneration` is less than the current VM generation, the
   condition predates the latest spec change and should be treated as stale regardless
   of its `status`.
2. **Reconcile outcome**: the condition `status` (True/False) reflects whether the
   controller successfully completed its reconcile for the generation recorded in
   `observedGeneration`.

Note that `kubectl wait --for=condition=VMIInSync` checks only the condition `status`
field and does not evaluate `observedGeneration`; consumers that need to verify
freshness must check `observedGeneration` explicitly.

### Retire the generation annotation

The `VMIInSync` condition with its `ObservedGeneration` field replaces the
`VirtualMachineGenerationAnnotation` annotation as the mechanism for surfacing "which VM
generation has the controller processed against the running VMI." The annotation was an
internal intermediary used to propagate the generation value up into
`vm.status.observedGeneration`; the condition makes that value directly observable
without an annotation round-trip.

The internal decision logic of `conditionallyBumpGenerationAnnotationOnVmi` — which
currently compares the VMI's ControllerRevision against the current VM template to
determine whether to take action — is an implementation detail. This VEP does not
prescribe whether that logic is preserved, simplified, or replaced; it only prescribes
the output signal. Implementors should evaluate whether the ControllerRevision-based
comparison remains the right mechanism once the annotation output is removed.

Once the `VMIInSync` condition is stable and graduates, the
`VirtualMachineGenerationAnnotation` annotation and the
`patchVmGenerationFromControllerRevision` back-fill path become redundant and can be
removed. At the same point, `vm.status.observedGeneration` and
`vm.status.desiredGeneration` can be marked as deprecated in API type comments and
documentation. Neither field will be removed or have its population behavior changed;
backward compatibility is preserved indefinitely.

**Migration guidance for existing consumers.** The `VMIInSync` condition is the
recommended replacement for the non-standard `vm.status.observedGeneration` field: where
a consumer previously read `observedGeneration` to infer what VM generation was applied
to the running VMI, it should instead read `VMIInSync.observedGeneration` combined with
`VMIInSync.status`.

`vm.status.desiredGeneration` records the VM generation the controller last processed on
any reconcile, including when the VM is stopped. There is no equivalent condition-based
signal for this case — `VMIInSync` is only present when a VMI is running. Consumers of
`desiredGeneration` do not have a migration path to the new conditions-based API; the
field continues to be populated and its semantics are unchanged.

## API Examples

### VM status: VMIInSync condition is stale after a spec change

The VM spec has been updated to generation 5. The controller has not yet reconciled
generation 5 — the `VMIInSync` condition still carries `observedGeneration: 4`.
A consumer detects staleness by comparing `condition.observedGeneration` (4) with
`vm.metadata.generation` (5), without requiring a `False` status toggle.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
  generation: 5
status:
  # Existing fields — non-standard semantics, population unchanged (see Motivation)
  desiredGeneration: 5         # non-standard name: always equals vm.metadata.generation after any successful reconcile
  observedGeneration: 4        # non-standard semantics: the VM generation last applied to the running VMI

  conditions:
  - type: Ready
    status: "True"
    observedGeneration: 4      # stale: evaluated against generation 4
    reason: VMIReady
    lastTransitionTime: "2026-01-01T00:00:00Z"
  - type: VMIInSync
    status: "True"
    observedGeneration: 4      # stale: controller last reconciled at generation 4
    reason: VMIObserved
    message: "Controller successfully accounted for the VMI at the recorded generation"
    lastTransitionTime: "2026-01-01T00:00:00Z"
```

### VM status: controller reconciled generation 5 successfully

```yaml
status:
  desiredGeneration: 5
  observedGeneration: 5

  conditions:
  - type: VMIInSync
    status: "True"
    observedGeneration: 5
    reason: VMIObserved
    message: "Controller successfully accounted for the VMI at the recorded generation"
    lastTransitionTime: "2026-01-01T00:01:00Z"
```

### VM status: controller failed to reconcile the VMI at the current generation

```yaml
status:
  desiredGeneration: 5
  observedGeneration: 4

  conditions:
  - type: VMIInSync
    status: "False"
    observedGeneration: 5      # controller attempted generation 5 but could not complete
    reason: ReconcileError
    message: "Controller encountered an error while accounting for the VMI: <error detail>"
    lastTransitionTime: "2026-01-01T00:01:00Z"
```

### VMI status conditions after a spec change

VMI conditions also carry `ObservedGeneration`, allowing consumers to detect stale
conditions on the VMI independently:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vm
  generation: 3
status:
  conditions:
  - type: Ready
    status: "True"
    observedGeneration: 3      # this condition was evaluated against VMI generation 3
    reason: PodReady
    lastTransitionTime: "2026-01-01T00:00:00Z"
```

Whether virt-handler has enacted a specific spec change at the domain level is determined
by observing the corresponding VMI status fields (e.g. `vmi.status.interfaces` for
network interface changes, `vmi.status.currentCPUTopology` for CPU changes). A generic
virt-handler generation signal is a known gap addressed in Open Questions.

## Alternatives

### Add `ObservedGeneration` to existing types without embedding `metav1.Condition`

Adding `ObservedGeneration int64` directly to `VirtualMachineCondition` and
`VirtualMachineInstanceCondition` as a standalone field would also be purely additive.
However, it would leave the types structurally diverged from `metav1.Condition`
indefinitely, precluding use of the stdlib `apimeta` helpers and leaving the types
non-standard. The embedding approach is preferred because it aligns the types with the
standard while remaining backward compatible.

### Replace condition types with `metav1.Condition` directly

Replacing the custom types with `metav1.Condition` outright would remove `LastProbeTime`,
which is a breaking change for consumers that read that field. The embedding approach
retains `LastProbeTime` as a deprecated field, providing a migration path without
immediate breakage.

### Add a new top-level field to VM to express standard `observedGeneration` semantics

A new field with unambiguous standard semantics (the generation the VM controller last
processed) could be added alongside the existing fields. This was considered but
rejected: the `VMIInSync` condition's `observedGeneration` provides this signal
reliably, because `VMIInSync` is written on every reconcile that processes a VMI.
Other conditions are written only when their value changes, so their `observedGeneration`
may reflect an earlier generation if the condition has not changed recently. A
separate top-level field would duplicate what `VMIInSync.observedGeneration` already
expresses.

## Open Questions

### virt-handler processing signal

There is no generation-tracking signal for whether virt-handler has applied the current
VMI spec to the running domain. This gap is intentionally deferred from this VEP for two
structural reasons.

First, `metadata.generation` tracks spec changes only. The VMI controller writes several
side-effects to `vmi.status` — conditions, volume status, active pods, and others — in
response to a spec change. These status writes do not increment `vmi.metadata.generation`,
so there is no version number a downstream actor can reference to express "I processed
the VMI status as of snapshot X."

Second, the VMI controller and virt-handler both write to `vmi.status` concurrently.
virt-handler uses a full object `.Update()` when writing VMI status; the VMI controller
uses a scoped JSON Patch when the VMI is running, explicitly because virt-handler is
considered the owner at that point. A full Update by virt-handler from a stale copy of
the VMI can silently overwrite status fields that the VMI controller has just patched in.
Without a status subresource and without a status generation, neither actor can safely
express "I processed the complete VMI state at this generation."

In practice, consumers who need to know whether a specific spec change has been enacted
at the domain level already observe the corresponding VMI status fields directly — for
example, `vmi.status.interfaces` for network interface changes,
`vmi.status.currentCPUTopology` for CPU topology changes, and `vmi.status.memory` for
memory changes. These per-feature status fields are more precise than a generic generation
signal and are the established idiom in KubeVirt.

A future VEP should address whether a generic virt-handler processing signal is needed
beyond the existing per-feature status fields, and if so, must first resolve the VMI
status ownership model — clarifying which actor owns each status field and ensuring that
concurrent writes cannot silently override each other — before a generation-based signal
can be made reliable.

## Scalability

`ObservedGeneration` on conditions is a scalar integer written on every reconcile, which
is already the case for all other condition fields. The additional storage and API traffic
are negligible.

## Update/Rollback Compatibility

KubeVirt does not currently support rollback. The following describes observable behavior
if a cluster is downgraded to a release that predates this feature.

The struct embedding of `metav1.Condition` into `VirtualMachineCondition` and
`VirtualMachineInstanceCondition` is an unconditional type change and persists across
rollback. However, controllers in the rolled-back release will not populate
`ObservedGeneration`; because `metav1.Condition` declares the field with `omitempty`,
a zero value is absent from serialized JSON. Consumers will therefore observe
`ObservedGeneration` as absent after rollback, which is indistinguishable from the
feature-gate-disabled state.

The `VMIInSync` condition will be absent after rollback. Consumers should treat an absent
or zero `ObservedGeneration` as "not yet known" rather than "generation 0 processed," and
handle the absence of `VMIInSync` gracefully.

## Functional Testing Approach

Coverage should prefer extending existing VM and VMI lifecycle tests rather than
introducing standalone test cases.

### Unit tests

- VM controller: after a successful reconcile, `VMIInSync=True` is set with
  `ObservedGeneration == vm.Generation`; after a reconcile that fails to complete the
  necessary VMI update, `VMIInSync=False` is set with `ObservedGeneration == vm.Generation`;
  when no VMI exists, the `VMIInSync` condition is absent.
- All condition write paths: `ObservedGeneration` is populated correctly.

### Functional / e2e tests

- Start a VM; verify `VMIInSync=True` with `observedGeneration == vm.metadata.generation`.
- Apply a hotpluggable change (e.g., CPU hotplug); verify that `VMIInSync` remains `True`
  and `observedGeneration` advances to the new VM generation after the controller
  reconciles, without requiring a full VM restart.
- Apply a non-hotpluggable change; verify `VMIInSync=True` with
  `observedGeneration == vm.metadata.generation` after the controller reconciles, and that
  `RestartRequired=True` is set independently.
- Stop a VM; verify the `VMIInSync` condition is absent.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] `VirtualMachineCondition` and `VirtualMachineInstanceCondition` updated to embed
      `metav1.Condition` inline, with `LastProbeTime` retained as a deprecated field.
      This is an unconditional API type change; it is not feature-gated.
- [ ] Feature gate guards population of `ObservedGeneration` in all condition writes and
      the new `VMIInSync` condition. When the gate is disabled, these fields are not
      populated and are absent from serialized JSON.
- [ ] All condition writes in the VM controller, VMI controller, and virt-handler updated
      to populate `ObservedGeneration = obj.Generation` when the feature gate is enabled.
- [ ] `VMIInSync` condition implemented in the VM controller.
- [ ] Unit test coverage for all new fields and condition writes.

### Beta

- [ ] All KubeVirt-internal condition readers verified to handle `ObservedGeneration`
      correctly.
- [ ] Functional test coverage for `VMIInSync` transitions.
- [ ] User-guide and API reference docs updated to document the `VMIInSync` condition
      semantics.

#### On-By-Default Readiness

Beta features are enabled by default and remain gated through the Beta phase. Enabling
by default requires:

- No regressions in existing VM and VMI lifecycle test coverage.
- `VMIInSync` condition semantics documented and reviewed by SIG Compute.

### GA

- [ ] `vm.status.observedGeneration` and `vm.status.desiredGeneration` marked as
      deprecated in API type comments, with the `VMIInSync` condition recommended as the
      replacement.
- [ ] `VirtualMachineGenerationAnnotation` annotation and the
      `patchVmGenerationFromControllerRevision` back-fill path removed.
- [ ] Feature gate removed; all behavior is unconditional.
