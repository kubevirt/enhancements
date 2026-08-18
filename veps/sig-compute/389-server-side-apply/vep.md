# VEP #389: Introduce Server-Side Apply

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Adopt Kubernetes [Server-Side Apply](https://kubernetes.io/docs/reference/using-api/server-side-apply/)
(SSA) in KubeVirt by generating typed apply configurations for all
KubeVirt API groups and wiring them into the generated clientset. This
gives every KubeVirt controller a first-class `Apply` method, enabling
declarative, field-level ownership. Whether a given API interaction
should move to SSA, remain as a read-modify-write update, or use
another pattern is evaluated case by case — SSA is not a blanket
replacement for all update paths.

## Motivation

KubeVirt controllers today use the classic update loop: read the
current object, mutate fields, write back the full object. This has
several well-known problems:

- **Conflict retries**: Two controllers updating the same resource race
  on `resourceVersion`. The loser retries, re-reads, and re-applies —
  wasted work that scales poorly under contention.
- **Full-object writes are error-prone**: A controller that only cares
  about `.status.conditions` must still send the entire object back,
  risking accidental overwrites of fields managed by other actors.
- **No field ownership tracking**: There is no way to know which
  controller or user owns a given field. `kubectl apply` uses the
  `last-applied-configuration` annotation as a client-side
  approximation, but controllers typically bypass it entirely.
- **Strategic merge patch limitations**: Client-side patches require
  hand-crafting patch payloads and don't compose well when multiple
  controllers patch the same resource.

Server-Side Apply solves these by moving the merge logic into the API
server and tracking per-field ownership via managed fields. Upstream
Kubernetes has been using SSA for its own controllers since 1.22 and
it has been GA since 1.18.

## Goals

- Generate typed apply configuration types for all KubeVirt API groups
  (`core/v1`, `export/v1beta1`, `export/v1`, `snapshot/v1alpha1`,
  `snapshot/v1beta1`, `instancetype/v1beta1`, `pool/v1alpha1`,
  `pool/v1beta1`, `migrations/v1alpha1`, `clone/v1alpha1`,
  `clone/v1beta1`, `backup/v1alpha1`)
- Wire apply configurations into the generated clientset so every
  resource type exposes an `Apply` and `ApplyStatus` method
- Integrate `applyconfiguration-gen` into the existing code generation
  pipeline (`hack/generate.sh`)
- Enable incremental adoption of SSA across KubeVirt controllers,
  where each API interaction is evaluated individually to determine
  whether SSA is the right fit

## Non Goals

- Mandate SSA for all API interactions — each call site is evaluated
  individually; some update patterns may remain as read-modify-write
  or patch where SSA is not the best fit
- Migrate all controllers in a single release — adoption is
  incremental and controller owners decide when to switch
- Change any user-facing API or behavior — SSA is an internal
  implementation detail of how controllers write to the API server
- Require SSA for external consumers of the KubeVirt client-go library
  — the existing `Update` and `Patch` methods remain available
- Deprecate or remove the existing update-based client methods

## Definition of Users

- **KubeVirt developers**: Primary consumers. SSA provides an
  additional tool for controller logic, making field ownership explicit
  and reducing boilerplate where appropriate.
- **Downstream/third-party controller authors**: Consumers of
  `kubevirt.io/client-go` who can adopt `Apply` methods at their own
  pace.

## User Stories

- As a KubeVirt developer, I want to update only the fields my
  controller owns without fetching and sending back the entire object,
  so that my controller is simpler and doesn't conflict with other
  controllers.
- As a KubeVirt developer, I want the API server to track which
  controller owns each field, so that ownership conflicts are surfaced
  explicitly instead of silently overwritten.
- As a downstream controller author, I want typed apply configuration
  builders for KubeVirt resources, so that I can adopt SSA without
  manually crafting patch payloads.

## Repos

- kubevirt/kubevirt

## Design

### Code generation

`applyconfiguration-gen` and `client-gen` are integrated into the
existing `hack/generate.sh` pipeline to produce typed apply
configurations and `Apply`/`ApplyStatus` clientset methods for all
KubeVirt API groups. Generated output lands under
`staging/src/kubevirt.io/client-go/applyconfigurations/`.

### Adopting SSA in controllers

SSA is not a blanket replacement for every API write. Each API
interaction must be evaluated individually to determine whether SSA
is appropriate. Factors to consider include:

- **Field ownership boundaries**: SSA works best when a controller
  owns a well-defined, non-overlapping set of fields. When multiple
  actors legitimately write to the same fields (e.g. spec fields
  shared between the user and a mutating webhook), SSA ownership
  semantics require careful consideration.
- **Read-then-write dependencies**: If the new value depends on the
  current state of the object (e.g. incrementing a counter, toggling
  a condition based on current conditions), a read-modify-write with
  conflict retry may still be the correct pattern.
- **Atomic multi-field updates**: When several fields must be updated
  together as a consistent snapshot, `Update` with optimistic
  concurrency provides a stronger consistency guarantee than SSA,
  where fields are merged independently.

#### `Force: true` convention

Following the
[Kubernetes SSA documentation](https://kubernetes.io/docs/reference/using-api/server-side-apply/#using-server-side-apply-in-a-controller),
all controller Apply calls must use `Force: true`. This is the
recommended practice for controllers: it ensures that a controller
can always reclaim ownership of the fields it manages, even if
another actor (e.g. a previous version of the same controller using
`Update`) has taken ownership. Without `Force: true`, an Apply call
that encounters a field ownership conflict returns an error instead
of proceeding.

#### Mixed SSA/Update coexistence during incremental adoption

During incremental migration, some controllers will use Apply while
others (or older versions of the same controller) still use Update.
This mixed state is expected and safe:

- **Update → Apply transition**: When a controller switches from
  `Update` to `Apply` with `Force: true`, it takes ownership of the
  fields it applies. Fields it previously wrote via `Update` but no
  longer includes in its apply configuration become unowned — this is
  intentional and must be reviewed during migration.
- **Rolling updates (virt-handler DaemonSet)**: During a rolling
  DaemonSet update, old pods use `Update` and new pods use `Apply` on
  the same VMI resources. Because `Force: true` is used, the new pod's
  Apply succeeds regardless of prior ownership. The old pod's `Update`
  also succeeds because `Update` does not check managed fields. The
  risk is that an old pod's `Update` may temporarily re-take ownership
  of fields the new pod applied. This is transient — once the rollout
  completes, only Apply-based pods remain. Each migration PR must
  evaluate this window and confirm that the transient ownership flip
  does not cause incorrect behavior.
- **Different controllers on the same resource**: Two controllers
  writing to different fields on the same resource — one via Apply,
  one via Update — coexist without issue. The Apply controller owns
  its fields; the Update controller implicitly owns all fields it
  writes.

Each adoption is a separate PR with a dedicated discussion to evaluate
the tradeoffs for that specific call site. The PR should:

1. Justify why SSA is the right choice for the targeted API interaction
2. Replace `Update`/`UpdateStatus` with `Apply`/`ApplyStatus` where
   appropriate
3. Set a unique `FieldManager` name identifying the controller
4. Adjust or remove retry-on-conflict loops only where SSA makes them
   unnecessary
5. Add/update tests

#### Initial adoption candidates

The following API interactions are identified as strong candidates for
SSA adoption. Each has a single controller owning the status fields,
with no cross-controller write contention. Final evaluation happens in
the individual PRs.

| Controller | File | Fields | Why |
|------------|------|--------|-----|
| VirtualMachineInstanceReplicaSet | `pkg/virt-controller/watch/replicaset/replicaset.go` | `Status.Replicas`, `ReadyReplicas`, `LabelSelector`, `Conditions` | Pure compute-and-set pattern. Single owner, no retry loop. Simplest controller in the codebase. |
| VirtualMachinePool | `pkg/virt-controller/watch/pool/pool.go` | `Status.Replicas`, `ReadyReplicas`, `LabelSelector`, `Conditions` | Nearly identical pattern to ReplicaSet. Single owner. |
| VirtualMachineClone | `pkg/virt-controller/watch/clone/clone.go` | `Status.Phase`, `SnapshotName`, `RestoreName`, `TargetName`, `Conditions` | Clean state machine with well-defined phase transitions. Single owner. |
| VirtualMachineExport | `pkg/storage/export/export/export.go` | `Status.Phase`, `ServiceName`, `Links`, `Conditions`, `TokenSecretRef` | Observes pod/service state, computes status. Single owner. |
| VirtualMachineRestore | `pkg/storage/snapshot/restore.go` | `Status.Complete`, `RestoreTime`, `Conditions`, `Restores` | All callers funnel through single `doUpdateStatus`. Single owner. |
| VirtualMachineSnapshot | `pkg/storage/snapshot/snapshot.go` | `Status.Phase`, `ReadyToUse`, `Error`, `Conditions` | Derives status from VMSnapshotContent. Single owner. |
| KubeVirt Operator | `pkg/virt-operator/kubevirt.go` | `Status.Conditions`, `OperatorVersion`, `TargetKubeVirtVersion` | Single operator, single CR per cluster. Already separates status from metadata updates. |

#### CRD schema requirements for list fields

SSA merge behavior for lists depends on the OpenAPI schema annotations
in the CRD. For conditions slices — listed as SSA-managed fields in
several candidates above — the CRD must declare:

```yaml
x-kubernetes-list-type: map
x-kubernetes-list-map-keys:
  - type
```

Without these annotations, the list defaults to `atomic` merge
semantics: an Apply that sends a single condition would take ownership
of the entire conditions list and remove all other entries.

KubeVirt CRDs today do not consistently carry these annotations on all
condition slices. Adding the correct `x-kubernetes-list-type: map` and
`x-kubernetes-list-map-keys: [type]` markers is a prerequisite before
any controller can safely Apply individual conditions via SSA. This
schema update must land before or alongside the first controller
migration that targets a conditions field.

Each adoption PR that touches a list field must verify the
corresponding CRD schema has the correct list-type annotation. The
code generation pipeline should be extended to enforce this — or at
minimum, the review checklist for adoption PRs must include CRD
schema validation.

**Not recommended for initial adoption**: VMI status updates in
`pkg/virt-handler/` and `pkg/virt-controller/watch/vmi/`. These have
shared ownership between virt-controller (JSON Patch with test-and-set)
and virt-handler (full Update), with conditional logic depending on VMI
phase. These require careful field manager coordination and should be
evaluated after SSA is proven on simpler controllers.

**Example — status condition update (good SSA candidate)**:

A controller that owns a specific status condition can apply just
that field without fetching the full object:

```go
applyConfig := kubevirtv1.VirtualMachine(name, namespace).
    WithStatus(kubevirtv1.VirtualMachineStatus().
        WithConditions(newCondition))
_, err = client.ApplyStatus(ctx, applyConfig, metav1.ApplyOptions{
    FieldManager: "my-controller",
})
```

**Example — state-dependent spec update (SSA may not fit)**:

When the new value depends on the current state, read-modify-write
with conflict retry remains appropriate:

```go
vm, err := client.Get(ctx, name, metav1.GetOptions{})
if err != nil {
    return err
}
vm.Status.Conditions = updateConditionBasedOnCurrentState(vm)
_, err = client.UpdateStatus(ctx, vm, metav1.UpdateOptions{})
// on conflict: retry from the top
```

### Field manager conventions

Each controller uses a descriptive, unique field manager name:

- `virt-controller/<subsystem>` (e.g. `virt-controller/vm-controller`)
- `virt-handler/<subsystem>`
- `virt-api/<subsystem>`

#### Field manager renaming and cleanup

If a field manager is renamed (e.g. during a controller refactor),
the old manager name remains in the object's managed fields metadata
indefinitely. Stale entries are harmless — they don't affect
controller behavior or API server processing — but they do add noise
to `managedFields` output.

When renaming a field manager, the migration PR should include a
one-time cleanup step: an Apply with the old field manager name and
an empty apply configuration. This causes the old manager to release
ownership of all its fields, removing the stale entry. The new
manager's Apply (with `Force: true`) then takes ownership as usual.

```go
// Release ownership from the old field manager name
emptyApply := applycorev1.VirtualMachine(name, namespace)
_, err := client.Apply(ctx, emptyApply, metav1.ApplyOptions{
    FieldManager: "old-controller-name",
})
```

This cleanup can run once during the first reconcile after upgrade
and be removed in the following release.

### Interaction with existing features

| Feature | Interaction |
|---------|-------------|
| Webhooks (validating/mutating) | Unaffected. SSA requests pass through admission like any other. |
| Subresource endpoints | `ApplyStatus` targets the `/status` subresource, same as `UpdateStatus`. |
| RBAC | No new verbs. `patch` permission is required for `Apply` (already granted to controllers). |
| Instance types | Apply configurations are generated for instancetype API types like any other group. |
| Live migration | Migration controllers are migrated incrementally; no behavioral change. |

## API Examples

SSA is an internal implementation concern; it does not add or change
any user-facing API. The generated apply configuration types are used
only by controller code.

Example of applying a VirtualMachine status update:

```go
import (
    applycorev1 "kubevirt.io/client-go/applyconfigurations/core/v1"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

applyConfig := applycorev1.VirtualMachine(name, namespace).
    WithStatus(applycorev1.VirtualMachineStatus().
        WithConditions(
            applycorev1.VirtualMachineCondition().
                WithType(v1.VirtualMachineReady).
                WithStatus(k8sv1.ConditionTrue),
        ))

_, err := virtClient.VirtualMachine(namespace).ApplyStatus(
    ctx, applyConfig, metav1.ApplyOptions{FieldManager: "virt-controller/vm-controller"})
```

## Alternatives

1. **Client-side strategic merge patch**: Already available but
   requires manually constructing patch payloads and doesn't track
   field ownership. SSA provides typed builders and ownership tracking
   out of the box.
2. **Client-side apply with `last-applied-configuration`**: The
   annotation-based approach `kubectl apply` uses. Not suitable for
   controllers — it doesn't handle multi-actor ownership and is
   deprecated in favor of SSA.
3. **Keep read-modify-write everywhere**: Status quo. Works but
   creates unnecessary contention and retry logic in cases where a
   controller owns a clear set of fields. SSA provides a better tool
   for those cases, while read-modify-write remains appropriate where
   the new value depends on current state.
4. **Adopt SSA all at once**: Too risky. Each API interaction has
   different ownership and consistency requirements. A case-by-case
   evaluation with dedicated discussion per call site ensures the
   right pattern is used everywhere.

## Scalability

For API interactions where SSA is adopted, it can reduce API server
load under contention by replacing retry loops with a single `Apply`
call where the API server resolves ownership declaratively. The
overall impact depends on how many call sites are migrated and how
contention-prone they are.

The generated apply configuration types add to the `client-go` binary
size but have no runtime cost beyond what is already incurred by the
existing types.

SSA increases the size of each object's `managedFields` metadata,
since the API server records per-field ownership for every field
manager. This growth is proportional to the number of distinct field
managers and the number of fields they own. While the overhead per
object is modest, it adds up across large informer caches — every
cached object carries its full `managedFields`.

To mitigate memory pressure on the controller side, informer caches
can strip `managedFields` from cached objects using a transform
function, as demonstrated in
[kubevirt/kubevirt#14243](https://github.com/kubevirt/kubevirt/pull/14243).
This is safe when the controller interacts with the API server via
`Apply` or `Patch` (which don't require the cached `managedFields`)
but requires caution if the controller uses `Update` starting from a
cached object — the update would persist the object without
`managedFields`, effectively clearing ownership tracking. Each
adoption PR should evaluate whether its informer caches would benefit
from this optimization.

## Update/Rollback Compatibility

- **Update**: Fully backward compatible. The generated `Apply` methods
  are additive — existing `Update`/`Patch` methods remain untouched.
  Controllers can be migrated one at a time across releases.
- **Rollback**: If a controller is rolled back to a version that uses
  `Update` instead of `Apply`, the managed fields metadata is ignored
  by the older code. No data loss or behavioral change.
- **Mixed-version clusters**: During rolling updates, old and new
  controller versions may briefly coexist. A controller using `Apply`
  and one using `Update` on the same resource will not conflict — the
  API server handles both. The `Update` caller becomes the owner of all
  fields it writes, which is the same semantic as today.

## Functional Testing Approach

1. **Unit tests**: Verify that apply configurations are correctly
   generated and compile. Test that controller logic produces the
   expected apply configuration for given inputs.
2. **Integration tests**: For each migrated controller, verify that
   `Apply`/`ApplyStatus` calls produce the expected resource state.
   Verify field ownership via managed fields metadata.
3. **E2E tests**: Existing E2E tests cover the behavioral surface. No
   new E2E tests are needed for the generation step itself. Each
   controller migration PR must pass the existing E2E suite to confirm
   no behavioral regression.

## Implementation History

2025-04-10: POC implementing apply configuration generation and clientset
integration. PR: https://github.com/kubevirt/kubevirt/pull/17435.

## Graduation Requirements

### Alpha

- [ ] `applyconfiguration-gen` integrated into the build pipeline
- [ ] Apply configurations generated for all KubeVirt API groups
- [ ] Generated clientset includes `Apply` and `ApplyStatus` methods
- [ ] At least one API interaction migrated to SSA as proof of viability
- [ ] Unit and integration tests for the migrated API interaction
- [ ] Field manager naming convention documented

### Beta

- [ ] SSA adopted in multiple controllers across different subsystems
- [ ] Each adoption PR includes a justification for why SSA fits that
      specific API interaction
- [ ] No regressions in E2E test suite
- [ ] Field ownership visible and correct in managed fields metadata
- [ ] Downstream consumers documented on how to use apply configurations

#### On-By-Default Readiness

There is no feature gate for SSA adoption. A feature gate would
require maintaining two code paths (Apply and Update) for every
migrated call site, doubling the testing surface and negating much
of the simplification SSA provides.

The risk of regressions is mitigated by the adoption model itself:

- Each migration is a small, scoped PR targeting a single API
  interaction with dedicated review
- Every PR must pass the full E2E suite before merge
- Rollback is a simple code revert of a contained change — no
  cluster-wide flag to flip
- SSA is a stable, GA Kubernetes API mechanism (since 1.18) — the
  risk is in the migration, not the mechanism

### GA

- [ ] SSA adopted wherever it was evaluated as the right fit
- [ ] Remaining read-modify-write patterns have documented rationale
      for why they were kept
- [ ] Stable across multiple releases