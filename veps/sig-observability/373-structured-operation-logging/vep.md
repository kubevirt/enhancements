# VEP #373: Structured Operation Logging

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Feature Gate

- Feature gate name: `StructuredOperationLogging`

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP introduces a reusable mechanism and conventions for emitting structured KubeVirt operation logs. VM operations are the first domain adopting the mechanism, with migration serving as the initial Alpha vertical slice. The VEP defines and validates the operation logging contract through these VM operation use cases; future domains may reuse the mechanism but may require additional schema design and review.

Migration is selected as the initial Alpha vertical slice because it provides a well-defined asynchronous lifecycle, a dedicated operation resource, persisted state transitions, operation duration, and migration-specific context such as source and target nodes. This makes it a useful non-trivial validation case for the generic structured-operation logging mechanism. Selection of migration as the Alpha use case does not make the common `kubevirt.operation.*` schema migration-specific.

## Motivation

KubeVirt components currently log operation information in semi-structured, human-readable format. This makes it difficult for observability tools (Loki, Perses dashboards) to reliably filter and display VM operation lifecycle information. Downstream features like a VM Operations Timeline and In-flight Operations tracking need to answer:

1. **What operation occurred?**
2. **Which VM/resource was affected?**
3. **What lifecycle phase did it reach** (started, completed, failed), for duration calculation and, as the mechanism and phase set mature beyond Alpha, coarse in-progress tracking (Alpha defers `in_progress` and delivery is best-effort, so precise in-progress detection is not yet a guarantee)?
4. **Did it succeed or fail, and what failure context is available?**
5. **How long did it take?**
6. **What operation-specific context is useful** (e.g., migration source/target nodes)?

Answering these reliably requires **consistent field names** across all controllers for LogQL filtering, and a **machine-parseable format** that won't break between KubeVirt versions.

This VEP is scoped to operation *lifecycle* observability. It does not attempt to answer "who initiated it" — see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs).

The Kubernetes project itself has adopted structured logging ([KEP-1602](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/1602-structured-logging)) and contextual logging ([KEP-3077](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/3077-contextual-logging)). This VEP brings KubeVirt in line with that direction.

## Goals

- Establish a reusable structured operation logging mechanism (shared package, typed constants, contextual logger builders) usable by all KubeVirt components
- Define a standard field taxonomy for VM operation log entries as the first instrumented domain, providing stable, machine-queryable operation metadata
- Prove the mechanism end-to-end in Alpha on migration (virt-controller), as the first Alpha vertical slice
- Provide operation lifecycle information: operation type, phase, and duration
- Provide operation-specific troubleshooting context (e.g., migration source/target nodes)
- Preserve existing human-readable log messages and severity — no existing log line is altered or removed; a small, fixed set of new feature-gated lifecycle records is added for each defined transition, uniformly, regardless of whether an existing line happens to sit next to it (see [Feature Gate Mechanism](#feature-gate-mechanism))
- Enable downstream consumers (Loki/LogQL, Perses dashboards, troubleshooting/operation-history UIs) to build reliable queries without brittle regex parsing
- Use Go's `logr` contextual logging (`WithValues`) to construct each canonical lifecycle record at its emission point; broader call-chain or `ctx`-based propagation is deferred to Beta/GA and must not cause existing log lines to inherit operation taxonomy fields

## Non Goals

- Instrumenting all existing log call sites in Alpha — migration to structured logging across all components will happen incrementally in subsequent releases
- Replacing Kubernetes audit logs, or duplicating the authenticated-user attribution that audit logs already provide (see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs))
- Providing an audit trail
- Adding usernames to application logs
- Adding usernames to Kubernetes Events
- Changing the Kubernetes Events schema to match structured logs, or requiring operation-specific context to be added to Events
- Changing the log transport mechanism (CLF/Loki pipeline) — logs continue to flow via stdout
- Building the downstream dashboards (separate work item)
- Modifying the Kubernetes Events API or creating new CRDs
- Defining schemas for every future KubeVirt subsystem
- Changing the underlying KubeVirt logging implementation globally
- Providing the same backward-compatibility guarantees as the Kubernetes REST API during Alpha/Beta (see [Schema Stability](#schema-stability) for the graduated commitment)

## Relationship to Kubernetes Audit Logs

Kubernetes audit logs remain the authoritative source for API request auditing and authenticated user attribution. This VEP does not propagate authenticated user identity into KubeVirt operation logs.

Structured operation logs serve a different purpose: they describe the asynchronous KubeVirt lifecycle after an API request has been accepted. For example, Kubernetes audit can record that a migration was requested, while KubeVirt structured logs can describe when the migration started, its source and target nodes, whether it succeeded or failed, and its duration.

The two telemetry sources are complementary rather than duplicates.

## Definition of Users

- **Cluster administrators** who use Loki/Perses to monitor VM operations and troubleshoot issues
- **Platform engineers** who build observability dashboards consuming KubeVirt logs

## User Stories

- As a cluster admin, I want to query Loki for all structured migration lifecycle records for a specific VM, including source/target node and duration, to build its migration operation history.
- As a platform engineer, I want to build a Perses LogsTable dashboard that filters VM operations by namespace and operation type without writing fragile regex.
- As an SRE, I want to filter operations by severity (e.g., INFO/ERROR) and by source component to quickly find errors.

## Repos

- `kubevirt/kubevirt` — virt-controller, virt-handler, virt-api, virt-operator changes
- `kubevirt/enhancements` — this VEP

## Design

### Feature Gate Mechanism

This feature preserves every existing log line unchanged, and, for each defined lifecycle transition, attempts to emit one new, feature-gated canonical structured-operation record. The human-readable `msg` and severity of every pre-existing log line remain unchanged; canonical structured-operation records are separate, new log lines, not modifications of existing ones.

The feature gate is not checked with an `if/else` at every existing call site. Instead, the `StructuredOperationLogging` feature gate is checked once per canonical-record emission point — i.e., where a defined lifecycle transition, such as a migration reaching `completed`, has just been persisted (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping) below for exactly when). A single reconcile can reach a given transition (e.g. `failed`) from several different internal branches; the feature gate wraps the canonical-record construction at each of them, not once per reconcile pass.

The construction shown in [Example Log Output](#example-log-output) illustrates what the emitted record looks like; this section defines *when* it is emitted.

- **FG off**: no canonical structured-operation records are emitted; every existing log line is identical to today's output (no behavior change).
- **FG on**: every existing log line remains unchanged, and canonical structured-operation records are additionally emitted for the defined lifecycle transitions.

### Alpha Migration Phase Mapping

Alpha instruments the migration controller's status-update reconcile path. The table below maps KubeVirt migration controller state transitions to `kubevirt.operation.phase` values so that "started"/"completed"/"failed" are not left to interpretation, and defines exactly when — relative to status persistence — each canonical record is attempted.

| `kubevirt.operation.phase` | In-memory transition (identifies *which* phase) | Attempted only after |
|---|---|---|
| `started` | The VMIM transitions to `MigrationRunning`, indicating that migration execution has started according to the existing VMIM lifecycle | The status update for this reconcile is persisted successfully |
| `completed` | `Status.Phase` transitions to `MigrationSucceeded` | The status update for this reconcile is persisted successfully |
| `failed` | `Status.Phase` transitions to `MigrationFailed` (regardless of which internal failure path triggered it) | The status update for this reconcile is persisted successfully |
| `in_progress` | **Deferred for Alpha — not defined or emitted.** | — |

- Each Alpha phase maps to exactly one status-phase transition already present in the controller today; the mapping is anchored to current code, not aspirational. The status-phase assignment identifies *which* phase transitioned — it is not itself the emission point.
- **Emission timing and best-effort semantics**: the controller attempts to emit the canonical record for a transition only after the corresponding status update for that reconcile has been persisted successfully on the API server. If the status update fails, no canonical-record attempt is made this reconcile; the transition is picked up, persisted, and its record attempted on a subsequent reconcile. **Lifecycle logging is best-effort and is not transactional with the status update; delivery of any given record is not guaranteed exactly-once (or at all).** Emitting after persistence deliberately favors avoiding false lifecycle records (logging a transition that never actually committed) over guaranteeing delivery of every persisted transition. This trade-off is acceptable because the persisted VMIM status is the source of truth for the operation's current/final state *while the resource exists*; structured operation logs provide the retained, queryable historical representation used by downstream observability consumers, including after the VMIM object itself has been deleted.
- **Why `in_progress` is deferred**: defining it precisely for Alpha would require either emitting on every reconcile while the migration is running (which conflicts with the "only meaningful transitions, not every reconcile" testing requirement below), or a separate periodic/ticker-driven emission mechanism, out of scope for a narrow Alpha vertical slice. Alpha ships `started`/`completed`/`failed` only; `in_progress` is left for a future release once a clear, non-reconcile-coupled emission trigger is designed.
- **Object binding requirement**: the canonical record for a migration transition must be emitted using a logger explicitly bound to the `VirtualMachineInstanceMigration` object (see [Field Taxonomy](#field-taxonomy-otel-aligned)), not assumed from ambient state — the same reconcile loop also has log calls bound to the VMI for unrelated, pre-existing log lines.

### Two-Layer Architecture

This VEP introduces two distinct layers:

1. **General mechanism** — A shared Go package (e.g., `pkg/log/structuredlog`) providing typed field/enum constants and contextual logger builders. This layer is domain-agnostic and reusable by any future instrumentation domain.

2. **VM operations domain** — The first domain-specific taxonomy built on the mechanism. Defines the generic `kubevirt.operation.*` fields and operation type mappings, plus the VM-operations-specific `kubevirt.vmi.*`, `kubevirt.vm.*`, and `kubevirt.migration.*` field extensions used to validate the contract in Alpha. This VEP defines and validates the mechanism's contract through the VM operations domain, with migration as the Alpha vertical slice, only; it does not itself specify schemas for future domains. Future domains (device health, operator lifecycle, scheduling infrastructure) can define their own field extensions (e.g., `kubevirt.device.*`, `kubevirt.operator.*`) using the same mechanism — see [Schema Stability](#schema-stability) for when that requires further review.

### Field Taxonomy (OTel-Aligned)

Field names follow [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/) where they describe the same entity role with matching semantics. KubeVirt-specific attributes use the `kubevirt.` namespace prefix, following OTel's domain-prefix convention for vendor/product extensions rather than a specific registry attribute.

The taxonomy has four tiers: **operation identity** (the object the record is about, e.g. the VMIM) → **affected workload identity** (the VMI acted on; name required, UID conditional) → **optional owning-VM identity** (conditional) → **operation-specific context** (e.g., migration source/target node).

#### Subject-object and emitter context (existing KubeVirt fields, unchanged)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kind` | string | yes | Kind of the object the record is about (e.g., `VirtualMachineInstanceMigration`) |
| `name` | string | yes | Name of that object |
| `namespace` | string | yes | Namespace of that object |
| `uid` | string | yes | UID of that object — identifies *which specific instance* of the operation this record is about (e.g., which migration) |
| `component` | string | yes | What emitted the record (e.g., `virt-controller`), set once at binary startup |

These are KubeVirt's pre-existing plain logger fields (populated today via `log.Log.Object(obj)`, plus the existing `component` field), kept as-is for continuity with current logs rather than mapped onto OpenTelemetry's `k8s.namespace.name`/`k8s.object.*` Collector-receiver-style attributes: those attributes conventionally describe the Kubernetes resource the telemetry-producing workload itself runs as, not an arbitrary subject object an operation log is *about* — a different entity role than the one needed here. `kind`/`name`/`namespace`/`uid` answer "what object is this operation log about"; `component` separately answers "what emitted this log" — the two are not the same kind of context and are documented separately.

**Object-binding requirement**: for migration records, `uid` (and `kind`/`name`/`namespace`) must reflect the `VirtualMachineInstanceMigration`, not the VMI or any other object — see the object-binding requirement in [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping).

#### Common operation attributes

`kubevirt.operation.type` identifies the specific operation performed (e.g. `migration`); `kubevirt.operation.phase` identifies where that operation currently is in its lifecycle (`started`/`completed`/`failed`). The two are independent axes: `type` answers "what operation occurred," `phase` answers "what lifecycle stage did it reach" — see [Operation Type Mapping](#operation-type-mapping) for how operation types are defined.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.operation.type` | string | yes | The specific operation performed, e.g. `migration` (see [Operation Type Mapping](#operation-type-mapping) below). Not a broad category — a consumer must be able to tell exactly what happened from this field alone. For Alpha, only `migration` is implemented and committed. New values may be added after GA; see [Schema Stability](#schema-stability). |
| `kubevirt.operation.phase` | string | yes | Logging-domain lifecycle: `started`, `in_progress`, `completed`, `failed`. Independent of CRD `.status.phase` (see [Operation Phase vs API Phase](#operation-phase-vs-api-phase)). Alpha emits `started`/`completed`/`failed` only; `in_progress` is deferred (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping)). |
| `kubevirt.operation.duration_ms` | int | conditional | Duration in ms. Emitted on terminal records (`completed`/`failed`) when the persisted CR timestamps needed to compute it are reliably known (derived from CR status timestamps, e.g. `migrationState.startTimestamp`/`endTimestamp` — not in-memory timers, so it is restart-safe). Omitted (never `0`) when not reliably known. |
| `error.type` | string | optional | Error classification, per [OTel error conventions](https://opentelemetry.io/docs/specs/semconv/general/attributes/#error-attributes). Applicable to `failed` records only, and only when a stable, known error classification exists for that failure path; omitted when no stable classification is available. |

#### Affected-workload context: `kubevirt.vmi.name` (required) / `kubevirt.vmi.uid` (conditional)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.vmi.name` | string | yes | Name of the `VirtualMachineInstance` the operation acts on |
| `kubevirt.vmi.uid` | string | conditional | UID of that `VirtualMachineInstance`, when the VMI object is available at the emission point |

`kubevirt.vmi.name` identifies the affected VMI, independent of whether it has an owning `VirtualMachine` — this is what answers "which VMI was migrated" for *every* migration record, standalone or VM-backed. It is required, not conditional, for Alpha migration records, and is sourced from the migration operation identity itself (`VirtualMachineInstanceMigration.Spec.VMIName`, which is a required field on the VMIM), not from a fetched VMI object — so it remains available even on failure paths where the VMI object cannot be fetched.

`kubevirt.vmi.uid` is **conditional**, not required: it is emitted when the actual `VirtualMachineInstance` object is available to the controller at the emission point (so its UID is known), and omitted — never an empty placeholder — when it is not. The migration controller has failure paths where the VMIM transitions to `MigrationFailed` while the VMI object is unavailable; on those paths the record still carries `kubevirt.vmi.name` but omits `kubevirt.vmi.uid`. This VEP does not add a new API lookup or persistence mechanism solely to guarantee `kubevirt.vmi.uid`'s availability.

#### VM context: `kubevirt.vm.name` / `kubevirt.vm.uid` (conditional pair)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.vm.name` | string | conditional | Name of the owning `VirtualMachine`, when its identity is reliably known |
| `kubevirt.vm.uid` | string | conditional | UID of that `VirtualMachine` |

Both fields describe the same entity — the owning `VirtualMachine` — and are present together only when the owning `VirtualMachine`'s identity can be reliably determined: a well-formed controller `OwnerReference` of kind `VirtualMachine` with both `Name` and `UID` populated. The fields are omitted together (never one without the other, never an empty placeholder) both for a standalone VMI and for any case where an owner reference is structurally present but does not reliably identify a `VirtualMachine` (e.g. missing/empty `UID`, wrong `Kind`, or not the controller reference) — merely having *some* owner reference present is not sufficient on its own to emit these fields.

`kubevirt.vm.uid` exists because a Kubernetes `metadata.uid` does not survive delete/recreate: a VM deleted and recreated with the same namespace/name produces a *different* `VirtualMachine` object with a *different* UID. Carrying the VM's UID lets consumers distinguish operations against the current VM from operations recorded against an earlier, now-deleted VM object that happened to share the same name — something the name alone cannot do.

`kubevirt.vm.uid` is distinct from both the generic `uid` field (which reflects whichever object the logger is bound to — the VMIM, for migration records) and `kubevirt.vmi.uid` (the affected VMI's own UID, conditional on the VMI object being available — see Affected-workload context above). On a VM-backed migration record where the VMI object is available, all three UIDs can appear together with different values: `uid` = which migration, `kubevirt.vmi.uid` = which VMI was migrated, `kubevirt.vm.uid` = which VM owns that VMI.

#### Migration-specific context

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.migration.source_node` | string | conditional | Source node. Omitted when not yet known. |
| `kubevirt.migration.target_node` | string | conditional | Target node. Omitted when not yet selected (e.g., a migration that fails before a target pod is scheduled never has a target node). |

**General rule for every conditional/optional field above**: when a value is unknown or not yet available, the implementation omits the key entirely. Emitting an empty string, zero, or other placeholder in its place is a taxonomy violation, not an acceptable degenerate case.

#### Severity Mapping

Canonical lifecycle-record severity is derived directly from the operation's own lifecycle outcome, independent of whether a corresponding Kubernetes Event exists or what type it has — structured logs and Kubernetes Events remain separate telemetry sources (see [Non Goals](#non-goals)). Severity follows OTel LogRecord conventions:

| `kubevirt.operation.phase` | SeverityText | SeverityNumber |
|---|---|---|
| `started` | `INFO` | 9 |
| `completed` | `INFO` | 9 |
| `failed` | `ERROR` | 17 |

`failed` uses `ERROR`, not `WARN`. This is an explicit policy decision for the canonical record — it is **not** a claim that every existing (non-canonical) failure-path log call in the migration controller is already at `ERROR` today; this VEP does not audit or guarantee the severity of pre-existing, unrelated log lines. The choice is informed by the general pattern in `pkg/virt-controller/watch/migration/migration.go`, where the failure branches that transition a migration to `MigrationFailed` predominantly log via `.Error(...)`/`.Errorf(...)`, while `Warning`/`Warningf` there is used for non-terminal conditions (e.g., a target pod that is currently unschedulable but has not yet caused the migration to fail). `failed` is a terminal outcome for the canonical record and is logged at `ERROR` accordingly, independent of whatever severity any nearby existing log line happens to use.

#### Operation Type Mapping

`kubevirt.operation.type` names the specific operation itself. It is deliberately **not** a Kubernetes Event reason (e.g. `MigrationTargetReady`) and **not** an intermediate CRD status value — those describe events or CR states, not the operation being performed, and mixing them into this field would make `kubevirt.operation.type=lifecycle` / `kubevirt.operation.phase=completed` unable to tell a consumer whether a VM was started, stopped, or restarted.

For Alpha, the only implemented and committed value is:

| Operation Type | Description |
|----------------|-------------|
| `migration` | Live migration of a VMI, as instrumented by the migration controller (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping)) |

Future operation types are **illustrative candidates only, not commitments**, and would be added following the [Schema Stability](#schema-stability) evolution policy as additional domains are instrumented — for example `start`, `stop`, `restart`, `pause`, `unpause`, `add_volume`, `remove_volume`, `snapshot`, `restore`. `kubevirt.operation.action` is not introduced; a specific operation type value (e.g. `start`) is sufficient on its own. If grouping related operation types later proves useful (e.g. for dashboards), a separate optional `kubevirt.operation.category` field can be considered as future schema evolution — it is not part of this VEP and is not added preemptively.

#### Operation Phase vs API Phase

`kubevirt.operation.phase` is a **logging-domain** concept, not a mirror of CRD `.status.phase`:

- The four values (`started`, `in_progress`, `completed`, `failed`) describe the lifecycle of a logged operation for consumers (duration, in-flight detection, failure filtering). Alpha emits `started`/`completed`/`failed` only; `in_progress` is deferred (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping)).
- Many KubeVirt CRDs do not expose a status phase at all, and those that do use domain-specific values (e.g., VMI `Running`/`Succeeded`, migration `Succeeded`/`Failed`) that do not map 1:1 to the logging phases.
- Controllers map their own status transitions onto these logging phases at the emit site — see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping) for the migration mapping.
- There is intentionally **no** requirement that every CRD grow a `.status.phase` field for this VEP.

### Implementation Safety

Taxonomy keys and enum values are typed and centrally defined in a shared package (`pkg/log/structuredlog/`) imported by all components — call sites are expected to reference named constants (e.g. `structuredlog.OperationMigration`, `structuredlog.PhaseCompleted`), not ad hoc strings. Component identity (`component`) is set once at binary startup and cannot be overridden at call sites.

Two distinct guarantees apply here, and this VEP does not blur them into one blanket "compile-time" claim:

- Misspelling a **constant's identifier** (e.g. `structuredlog.PhaseCompletd`, which does not exist) is a genuine Go compiler error.
- A **raw string literal** standing in for a constant (e.g. `Phase: "completd"`) is not rejected by the Go compiler by itself — Go allows an untyped string constant to be implicitly converted to any named string-based type, so a misspelled raw literal compiles silently. To close this gap, Alpha extends the required **static check** (custom `golangci-lint` analyzer or `go vet` pass in the kubevirt repo) to forbid raw string literals in taxonomy **key** positions (e.g. `"kubevirt.operation.type"`) *and* in taxonomy **enum-value** positions (e.g. `"completed"` in place of `structuredlog.PhaseCompleted`) outside the shared package. Developers must use the typed constants; raw string keys or enum values fail CI.

The exact helper API shape, and any stronger required-field enforcement (e.g. a constructor that takes required fields as function arguments instead of an optional struct literal), is left to implementation.

### Contextual Logging

For Alpha, the structured-operation logger is constructed specifically for the canonical lifecycle record at its emission point, using the VMIM-bound logger (`log.Log.Object(migration)`) plus the operation and workload context defined in [Field Taxonomy](#field-taxonomy-otel-aligned) — see [Example Log Output](#example-log-output) for a worked example. It is built and used once, at the point where a canonical lifecycle record is emitted, and then discarded — it is **not** propagated broadly through the existing reconcile call chain.

Existing log calls elsewhere in the reconcile continue using their own existing loggers and do **not** inherit the operation taxonomy fields. This is what keeps every pre-existing log line unchanged and is required by the feature-gate/compatibility guarantee (see [Feature Gate Mechanism](#feature-gate-mechanism)): if the canonical operation logger were propagated down the call chain, existing log lines would start carrying new structured fields, which this VEP explicitly does not allow.

Broader contextual propagation — for example, passing operation context to several existing call sites via `WithValues`, or a `context.Context`-based logger per KEP-3077 — can remain Future Work/Beta if a concrete need arises, but any such propagation must not cause existing, pre-canonical-record log lines to inherit the operation taxonomy fields.

### Instrumented Reconcile Loops

#### Alpha (minimal vertical slice)

| Component | Reconcile Loop | Operation Types Covered |
|-----------|---------------|------------------------|
| virt-controller | Migration controller | `migration` |

Alpha proves the mechanism, taxonomy, and feature gate end-to-end on one high-value operation type (migration), without requiring a broad controller refactor.

#### Beta (illustrative candidate future coverage — not a commitment)

Beta's actual required scope is validating the mechanism against at least one additional, materially different operation type beyond migration before enabling the feature by default (see [Graduation Requirements](#graduation-requirements)) — not the full table below. The table illustrates plausible future coverage to size the mechanism's generality; it is not a Beta commitment, and the specific operation type(s) chosen to satisfy the Beta gate may be a subset of it (or something else entirely):

| Component | Reconcile Loop | Illustrative Future Operation Types |
|-----------|---------------|------------------------|
| virt-controller | VM controller | `start`, `stop`, `restart` |
| virt-controller | DataVolume controller | e.g. `add_volume`, `remove_volume` |
| virt-controller | Snapshot controller | e.g. `snapshot`, `restore` |
| virt-controller | Network controller | e.g. interface hotplug/hotunplug |
| virt-handler | VMI reconcile loop | e.g. lifecycle phase transitions, eviction |

### Schema Stability

This VEP defines the **general stability and deprecation policy** for the structured logging field taxonomy (not only the Alpha VM-operations domain). The taxonomy is a consumer-facing contract with graduated commitments:

- **Alpha**: Field *names* and *enum values* may be added or renamed between minor releases with release notes. No changes within patch releases.
- **Beta**: Field names are stable. Removal or rename of a field name or enum value requires one release of deprecation notice.
- **GA**: Field *names* and *enum values* are both frozen with the same backward-compatibility guarantees as KubeVirt API types — neither may be renamed or removed without the formal deprecation/migration path described in the table below.

#### Adding new operation types / phases after GA

Additive evolution remains allowed after GA:

| Change | After GA? | Process |
|--------|-----------|---------|
| Add a new `kubevirt.operation.type` value (e.g., `backup`) | Yes | Add typed constant + mapping row; document in release notes; no deprecation needed |
| Add a new `kubevirt.operation.phase` value | Yes, sparingly | Prefer mapping onto the existing four phases; new phases need a short design note in the PR |
| Add a new optional field within an already-approved domain (e.g., `kubevirt.migration.mode`) | Yes | Additive; consumers ignore unknown fields |
| Add a new top-level domain prefix (e.g., `kubevirt.device.*`) | Requires review | A materially different domain schema is **not** simple additive evolution — it follows the normal KubeVirt enhancement/API review process (see [Future Work](#future-work)), same as before GA |
| Rename or remove a field name or enum value | No (without deprecation) | One release deprecation notice (Beta+) / forbidden without migration path (GA) |

New reconcile loops that instrument an already-approved operation type/phase using the existing shared constants are **implementation reuse** and do not need a new VEP. A new VEP (or a KubeVirt API/enhancement review) is warranted for **public-schema evolution**: changing a field's semantics, renaming or removing a field name or enum value outside the deprecation policy above, adding a new required field, defining new lifecycle-phase semantics, or introducing a materially different domain schema (e.g., a new top-level `kubevirt.<domain>.*` prefix with its own required fields).

The single source of truth for the schema is the typed constants in `pkg/log/structuredlog/`. A unit test serializes the field name list (and known enum values) and fails if names are removed or changed unexpectedly, similar to wire-format tests for API types.

### Example Log Output

KubeVirt's migration controller already builds a logger bound to the migration object today (`log.Log.Object(migration)`); the structured-logging helper is illustrated here extending that existing pattern rather than introducing an unrelated logging style.

```go
// Illustrative API. Exact helper names may change during implementation.

logger := structuredlog.ForOperation(
    log.Log.Object(migration),
    structuredlog.Operation{
        Type:    structuredlog.OperationMigration,
        Phase:   structuredlog.PhaseCompleted,
        VMIName: migration.Spec.VMIName, // sourced from the VMIM itself; required, available even when the VMI object cannot be fetched
        VMIUID:  vmi.UID,                // conditional: only set when the VMI object is available at this call site (omitted otherwise)
        VMName:  vmName, // VMName/VMUID are a pair: both set only when the owning VirtualMachine's identity is reliably known from the VMI's controller OwnerReference, both omitted otherwise
        VMUID:   vmUID,
    },
)

logger = logger.WithValues(
    structuredlog.MigrationSourceNode, vmi.Status.MigrationState.SourceNode,
    structuredlog.MigrationTargetNode, vmi.Status.MigrationState.TargetNode,
    structuredlog.OperationDurationMS, migrationDuration.Milliseconds(),
)

logger.Info("Migration completed successfully")
```

`migration.Spec.VMIName` identifies the affected VMI and is always populated for a migration record, since it comes from the `VirtualMachineInstanceMigration` object itself (a required field on the VMIM spec) rather than requiring a fetched VMI object. `vmi.UID` is only included when the VMI object is actually available at the emission point — e.g. not on failure paths where the VMI could not be fetched — and is omitted, not emitted empty, otherwise. `vmName`/`vmUID` are sourced from the VMI's controller `OwnerReference` when it reliably identifies a `VirtualMachine` (both populated together in that case, both omitted together otherwise). There is no `Namespace` field in the `Operation` literal because `log.Log.Object(migration)` already supplies the VMIM's namespace as an existing subject-object-context field, so the illustrative helper does not introduce a separate operation-level namespace field.

The resulting log (conceptual/illustrative output, not a wire-format guarantee):

```json
{
  "level": "info",
  "ts": "2026-07-05T10:00:00.000Z",
  "logger": "virt-controller.migration-controller",
  "component": "virt-controller",
  "kind": "VirtualMachineInstanceMigration",
  "name": "vm1-migration-abc",
  "namespace": "default",
  "uid": "<vmim-uid>",

  "kubevirt.vmi.name": "vm1",
  "kubevirt.vmi.uid": "<vmi-uid>",

  "kubevirt.vm.name": "vm1",
  "kubevirt.vm.uid": "<vm-uid>",

  "kubevirt.operation.type": "migration",
  "kubevirt.operation.phase": "completed",
  "kubevirt.operation.duration_ms": 42000,

  "kubevirt.migration.source_node": "worker-a",
  "kubevirt.migration.target_node": "worker-b",

  "msg": "Migration completed successfully"
}
```

Note the three visibly distinct UID placeholders — `<vmim-uid>`, `<vmi-uid>`, `<vm-uid>` — since they belong to three different Kubernetes objects. In this example `kubevirt.vmi.name` happens to equal `kubevirt.vm.name` ("vm1", by KubeVirt's VM-backed-VMI naming convention), but the `uid` values never coincide; this is why name equality between a VMI and its owning VM cannot substitute for carrying both UIDs.

> The example assumes the lifecycle record is bound to the `VirtualMachineInstanceMigration`, so the existing object `uid` identifies the migration instance. Implementations must verify the object context at each instrumented call site and must not rely on the generic `uid` field as operation identity when the logger is bound to a different object. This matters because the same migration controller also has log calls bound to the VMI, not always the migration object.

This example shows the `completed` case where `duration_ms`, `source_node`, `target_node`, and the VMI object itself are all known, so `kubevirt.vmi.uid` and both migration-node fields are present. Per the conditional semantics above, a `failed` record emitted before a target node was selected would omit `kubevirt.migration.target_node` entirely rather than emit it empty, and a `failed` record emitted on a path where the VMI object could not be fetched would still carry `kubevirt.vmi.name` (from `migration.Spec.VMIName`) but omit `kubevirt.vmi.uid` entirely.

### Downstream LogQL Usage

With the structured field taxonomy, downstream consumers can write reliable LogQL queries — combining KubeVirt's existing `kind`/`name`/`namespace`/`uid`/`component` fields with the new `kubevirt.*` extensions:

```logql
{kubernetes_namespace_name="kubevirt", kubernetes_container_name="virt-controller"}
  | json
  | kubevirt_operation_type="migration"
  | namespace="production"
  | kubevirt_operation_phase="failed"
```

> **Note**: Loki's `| json` parser converts dotted JSON keys to underscored field names
> (e.g., `"kubevirt.operation.type"` becomes `kubevirt_operation_type` in filter expressions).
> This is standard Loki behavior and does not affect the JSON log format itself.

## Alternatives

1. **Audit log correlation**: Join Loki audit stream with infrastructure stream at query time. Rejected: LogQL doesn't support cross-stream joins, requires complex external tooling.
2. **Event-exporter**: Deploy a component that watches K8s Events and pushes structured entries to Loki. Partially viable, but doesn't cover all operations (some happen without K8s Events). Still useful as a complement.
3. **Prometheus metrics for operations**: Use counters/gauges to track operations. Rejected for this use case: metrics lose event-level detail (reason, message, context). Metrics are appropriate for aggregates, not individual event inspection.
4. **managedFields parsing**: Extract identity from `.metadata.managedFields`. Rejected: only shows the last field manager, not necessarily who triggered the operation, and is complex to parse.
5. **Kubernetes Events only**: Rely solely on Kubernetes Events (already emitted for many operations) instead of adding structured logs. Rejected: Events have limited retention (typically one hour by default) and a bounded/truncated message field, and are not designed for historical querying or dashboarding; they remain valuable for real-time `kubectl describe`/watch workflows but are not a substitute for a queryable operation history.
6. **Kubernetes audit logs only**: Rely solely on Kubernetes API audit logs for operation visibility. Rejected: audit logs capture the API request/response at admission time, not the asynchronous KubeVirt-internal lifecycle (e.g., migration progress, node selection, duration) that happens after the request is accepted — see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs).
7. **Propagate authenticated usernames into structured logs**: Rejected because Kubernetes audit already provides the authoritative identity/request record, while propagating user identity through asynchronous KubeVirt operations introduces ambiguous semantics and considerable implementation complexity.

## Scalability

- No existing log line is enriched or modified — Alpha adds only new canonical lifecycle-record log lines; no new API calls or watchers are introduced
- Alpha defines three *possible* canonical lifecycle-record types (`started`, `completed`, `failed`); an individual migration emits only the transitions it actually reaches, not all three unconditionally (e.g., a migration that fails before reaching `MigrationRunning` emits only `failed` — see [Functional Testing Approach](#functional-testing-approach))
- No annotation writes or other new per-operation API writes of any kind
- Log volume increase from the new canonical records should be measured on a reference cluster rather than asserted as a fixed byte estimate
- No new components deployed

## Update/Rollback Compatibility

- **Update**: Old logs without the new structured fields will simply not have them. LogQL queries using these fields will return empty results for old entries. No breaking change.
- **Rollback**: Disabling the feature gate is safe — no canonical structured-operation records are emitted, and no existing log line is affected. There is no annotation or stamped field to roll back.

## Functional Testing Approach

1. **Unit tests (structured-operation contract)**: verify records expose `kubevirt.vmi.name` (required, always present — the affected VMI, sourced from `migration.Spec.VMIName`), `kubevirt.vmi.uid` (conditional — present only when the VMI object is available at the emission point), `kubevirt.vm.name`/`kubevirt.vm.uid` (conditional pair — present only when the owning `VirtualMachine`'s identity is reliably known from the VMI's controller `OwnerReference`), `kubevirt.operation.type`/`phase`, the bound-object `uid` (asserted to be the VMIM's UID specifically because the instrumented emission point explicitly binds the logger to the migration object), and, where applicable per their conditional semantics, `duration_ms`, `kubevirt.migration.source_node`/`target_node`, and `error.type`.
2. **Identity distinctness**: for a VM-backed migration where the VMI object is available, assert the bound-object `uid`, `kubevirt.vmi.uid`, and `kubevirt.vm.uid` are three distinct values on the same record, not merely three present keys. Separately, for a failure path where the VMI object is unavailable, assert `kubevirt.vmi.name` is still present while `kubevirt.vmi.uid` is absent.
3. **Integration tests, split by outcome** — one migration operation follows exactly one of these paths, never a mix:
   - **Successful migration**: expect canonical `started` and `completed` records; no `failed` record.
   - **Failure after migration started**: expect canonical `started` and `failed` records; no `completed` record.
   - **Failure before the migration reaches `MigrationRunning`**: expect only a canonical `failed` record; `started` is absent. (The exact early-failure scenario used to exercise this path is chosen during implementation.)

   Across these scenarios, verify a standalone-VMI migration, and a VMI whose owner reference does not reliably identify a `VirtualMachine` (e.g. missing `UID` or wrong `Kind`), both omit `kubevirt.vm.name`/`kubevirt.vm.uid` together, while a VM-backed migration with a valid controller `OwnerReference` carries both.
4. **Feature-gate compatibility**: with the FG **disabled**, all pre-existing logs remain unchanged and no canonical lifecycle records are emitted at all; with the FG **enabled**, pre-existing logs still remain unchanged and canonical lifecycle records are additionally emitted; Kubernetes Events are unchanged in both modes.
5. **Reconciliation-retry semantics**: repeated reconciles over the same migration state do not emit duplicate/spurious lifecycle-phase records; only meaningful state transitions produce `started`/`completed`/`failed` records (`in_progress` is deferred for Alpha, so it is not part of this test list).
6. **Delivery semantics**: assert that normal reconciliation does not produce duplicate canonical records for the same transition. Lifecycle logging is best-effort and non-transactional with the status update (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping)), so exactly-once delivery is not asserted or guaranteed.
7. **LogQL validation**: run LogQL queries against structured log output on a test cluster to verify filtering works.

## Risks

- **Schema instability** — migration alone may not generalize to other operation types; mitigated by validating the schema against at least one additional, materially different operation type before Beta (see [Graduation Requirements](#graduation-requirements)).
- **Misinterpretation as auditing** — consumers might assume structured logs are an audit mechanism even without a username field; mitigated via [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs) and the explicit Non-Goals.
- **Excessive/duplicate log records** — mitigated by emitting lifecycle records only for meaningful transitions, not every reconcile.
- **Semantic-convention mismatch** — mitigated by precisely scoping which fields are, and are not, OpenTelemetry semantic-convention attributes (see [Field Taxonomy](#field-taxonomy-otel-aligned)).

## Future Work

The general mechanism established here is designed to be extended to additional domains beyond VM operations. Potential future instrumentation domains include:

- **Device health** (`kubevirt.device.*`) — device plugin registration, health status changes, resource allocation failures
- **Operator lifecycle** (`kubevirt.operator.*`) — upgrade progress, component rollout, configuration reconciliation
- **Infrastructure scheduling** (`kubevirt.scheduling.*`) — node capacity decisions, topology constraints, placement failures

Each domain would define its own field taxonomy using the shared package and contextual logger patterns. Extension to new domains follows the schema-evolution policy in [Schema Stability](#schema-stability): additive use of the existing mechanism does not need a new VEP, while introducing a materially different domain schema is reviewed as public-schema evolution.

**Evaluate proposing a generic managed-virtual-machine entity to the OpenTelemetry semantic conventions.** No directly applicable managed-virtual-machine convention exists today — `host.*` describes the host a process runs *on*, not a VM a controller manages *about*. This is Future Work only: it does not block this VEP, and `kubevirt.vm.name`/`kubevirt.vm.uid` remain KubeVirt-specific attributes regardless of outcome.

## Implementation History

- 2026-07: VEP created
- 2026-08: VEP revised — username/initiator-identity propagation removed; scope narrowed to operation lifecycle observability, with an explicit "Relationship to Kubernetes Audit Logs" section

## Graduation Requirements

### Alpha (v1.10)

Alpha is intentionally a **narrow vertical slice** so it does not depend on broad reconcile/`ctx` refactors:

- [ ] Shared `pkg/log/structuredlog` package with typed field/enum constants and operation logger builder
- [ ] Feature gate `StructuredOperationLogging` checked once per canonical-record emission point (preserve every existing `msg`/severity unchanged; add new canonical records only)
- [ ] Structured fields emitted for **migration** operations in virt-controller Migration controller, per [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping) (`started`/`completed`/`failed`; `in_progress` deferred)
- [ ] Static check / linter forbidding string-literal taxonomy keys and enum values outside the shared package
- [ ] Unit + integration tests for the migration path (fields present per taxonomy; FG off = no canonical records and no change to existing logs)
- [ ] Field taxonomy documented (generated from or linked to the Go constants)

### Beta (TBD)

A future proposal to graduate `StructuredOperationLogging` to Beta should demonstrate
that the common operation schema has been validated against at least one materially
different operation type beyond migration. The specific operation and implementation
scope are intentionally not defined by this VEP.

Beta graduation, including any expansion of instrumentation coverage, is out of scope
for the Alpha implementation described here and requires separate planning and review.

- [ ] Schema validated against at least one materially different operation type
- [ ] Feature gate enabled by default
- [ ] Field taxonomy has no unresolved breaking changes from Alpha
- [ ] At least one downstream consumer validated
- [ ] No unacceptable performance or log-volume regression

### GA (TBD)

- [ ] Field taxonomy stable for 2 releases without breaking changes
- [ ] Feature gate locked to on (cannot be disabled)
- [ ] At least one downstream consumer (Perses dashboard) validated in production use
