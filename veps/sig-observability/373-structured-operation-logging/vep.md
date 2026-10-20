# VEP #373: Structured Operation Logging

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version: TBD
- This VEP targets GA for version: TBD

### Feature Gate

- Feature gate name: `StructuredOperationLogging`

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP introduces a reusable mechanism and conventions for emitting structured KubeVirt operation logs. VM operations are the first domain adopting the mechanism, with migration serving as the initial Alpha vertical slice. The VEP defines and validates the operation logging contract through these VM operation use cases; future domains may reuse the mechanism but may require additional schema design and review.

Migration is selected as the initial Alpha vertical slice because it provides a well-defined asynchronous lifecycle, a dedicated operation resource, persisted state transitions, operation duration, and migration-specific context such as source and target nodes. This makes it a useful non-trivial validation case for the generic structured-operation logging mechanism. Selection of migration as the Alpha use case does not make the common `kubevirt.operation.*` schema migration-specific. Alpha is a **single** release of that migration-only slice; splitting Alpha across two releases is not proposed. Further operation types are Beta planning, not a second Alpha.

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
- Use KubeVirt's existing go-kit-backed logger (`log.Log.Object(migration).With(...)`) to construct each canonical lifecycle record at its emission point; broader call-chain or `ctx`-based propagation is deferred to Beta/GA and must not cause existing log lines to inherit operation taxonomy fields

## Non Goals

- Instrumenting all existing log call sites in Alpha — migration to structured logging across all components will happen incrementally in subsequent releases
- Replacing Kubernetes audit logs, or duplicating the authenticated-user attribution that audit logs already provide (see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs))
- Providing an audit trail
- Adding usernames to application logs
- Adding usernames to Kubernetes Events
- Stamping a triggering object, initiating user, or ServiceAccount onto operation logs (including controller-created VMIMs from node drain or eviction)
- Annotation-based operation metadata, or mutating/validating webhooks to enforce it — that design was removed from this VEP
- Changing the Kubernetes Events schema to match structured logs, or requiring operation-specific context to be added to Events
- Changing the log transport mechanism (CLF/Loki pipeline) — records continue to flow through container logs; KubeVirt's JSON logger currently writes to stderr
- Building the downstream dashboards (separate work item)
- Modifying the Kubernetes Events API or creating new CRDs
- Defining schemas for every future KubeVirt subsystem
- Changing the underlying KubeVirt logging implementation globally
- Providing the same backward-compatibility guarantees as the Kubernetes REST API during Alpha/Beta (see [Schema Stability](#schema-stability) for the graduated commitment)

## Relationship to Kubernetes Audit Logs

Kubernetes audit logs remain the authoritative source for API request auditing and authenticated user attribution. This VEP does not propagate authenticated user identity into KubeVirt operation logs.

Structured operation logs serve a different purpose: they describe the asynchronous KubeVirt lifecycle after an API request has been accepted. For example, Kubernetes audit can record that a migration was requested, while KubeVirt structured logs can describe when the migration started, its source and target nodes, whether it succeeded or failed, and its duration.

The two telemetry sources are complementary rather than duplicates.

**Controller-initiated operations (node drain, eviction, workload update):** a user-triggered node drain or eviction policy can cause virt-controller to create a VMIM under the controller service account. The canonical migration records for that VMIM still do **not** carry the draining user. Attribution of "who caused this" belongs in Kubernetes audit of the drain/eviction/VMIM-create request, not in these logs. This VEP treats that gap as acceptable; it will not stamp a triggering object, username, or initiating ServiceAccount onto canonical records (see [Non Goals](#non-goals) and [Triggering object](#triggering-object)).

### Triggering object

Alpha binds every migration record to the `VirtualMachineInstanceMigration` (`kind`/`name`/`namespace`/`uid`). That VMIM *is* the operation instance, including when virt-controller created it on behalf of an eviction policy or node drain. This VEP does not introduce a "triggering object" field, annotation, or webhook to point back at a Node, Eviction, or user request. Reconstructing that chain is an audit-log / Kubernetes-event correlation problem, not an operation-log schema problem.

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
- **Emission timing and best-effort semantics**: the controller attempts to emit the canonical record for a transition only after the corresponding status update for that reconcile has been persisted successfully on the API server. If the status update fails, no canonical-record attempt is made this reconcile; the transition is picked up, persisted, and its record attempted on a subsequent reconcile. **Lifecycle logging is best-effort and is not transactional with the status update; delivery of any given record is not guaranteed exactly-once (or at all).** A crash or process restart between a successful persist and the emit attempt can drop that record permanently. Emitting after persistence deliberately favors avoiding false lifecycle records (logging a transition that never actually committed) over guaranteeing delivery of every persisted transition. This trade-off is acceptable because the persisted VMIM status is the source of truth for the operation's current/final state *while the resource exists*; structured operation logs provide the retained, queryable historical representation used by downstream observability consumers, including after the VMIM object itself has been deleted. Consumers must not treat a missing `started` record as proof the operation never started — it may mean the start persisted and the record was lost; see [Consumer guidance](#consumer-guidance).
- **Missing VMI**: the current migration reconcile deletes or skips a VMIM whose VMI is absent before calling the status-update path. Alpha does not change that behavior or emit a canonical `failed` record without a persisted `MigrationFailed` transition. A previously emitted `started` record can therefore lack a terminal record when the VMI disappears; see [Consumer guidance](#consumer-guidance).
- **Why `in_progress` is deferred**: emitting on every reconcile while the migration is running would conflict with the "only meaningful transitions, not every reconcile" testing requirement below; a separate periodic/ticker-driven mechanism is also out of scope for a narrow Alpha vertical slice. A future release could instead emit bounded `in_progress` records at meaningful, persisted post-start milestones. That design must identify the authoritative milestone source, emit only after its persistence, define retry/deduplication and per-operation volume bounds, and decide whether an optional migration step field is needed. A target pod becoming scheduled is before Alpha's `started` transition to `MigrationRunning`, so it cannot serve as a post-start `in_progress` milestone under this mapping. Alpha ships `started`/`completed`/`failed` only.
- **Object binding requirement**: the canonical record for a migration transition must be emitted using a logger explicitly bound to the `VirtualMachineInstanceMigration` object (see [Field Taxonomy](#field-taxonomy-otel-aligned)), not assumed from ambient state — the same reconcile loop also has log calls bound to the VMI for unrelated, pre-existing log lines.
- **Operation-instance correlation**: the bound object's `kind` + `uid` *is* the operation-instance identity. Alpha does not add a separate `kubevirt.operation.id`. Two concurrent migrations of the same VMI are two VMIMs and therefore two `uid` values; a later hotplug (or other) operation on that VMI will bind a different object and a different `kubevirt.operation.type`. Consumers correlating a timeline for one VMI group by `kubevirt.vmi.name` and `kubevirt.vmi.uid` and **split streams by bound `uid`**, not by VMI name alone. See [Concurrent operations](#concurrent-operations).

### Two-Layer Architecture

This VEP introduces two distinct layers:

1. **General mechanism** — A shared Go package (e.g., `pkg/log/structuredlog`) providing typed field/enum constants and private record-construction helpers. This layer is domain-agnostic and reusable by operation-specific builders; it does not expose an unrestricted canonical-record logger to call sites.

2. **VM operations domain** — The first domain-specific taxonomy built on the mechanism. Defines the generic `kubevirt.operation.*` fields and operation type mappings, plus the VM-operations-specific `kubevirt.vmi.*`, `kubevirt.vm.*`, and `kubevirt.migration.*` field extensions used to validate the contract in Alpha. This VEP defines and validates the mechanism's contract through the VM operations domain, with migration as the Alpha vertical slice, only; it does not itself specify schemas for future domains. Future domains (device health, operator lifecycle, scheduling infrastructure) can define their own field extensions (e.g., `kubevirt.device.*`, `kubevirt.operator.*`) using the same mechanism — see [Schema Stability](#schema-stability) for when that requires further review.

**Operation field namespace:** this VEP uses `kubevirt.operation.*` for the shared operation-identity fields (`type`, `phase`, `duration_ms`), not `kubevirt.vm.operation.*`. The first instrumented domain is VM operations, but the operation fields are the generic mechanism layer: a later device or operator domain should reuse `kubevirt.operation.type` / `phase` rather than mint a parallel `kubevirt.device.operation.*` vocabulary. VM/VMI/migration identity stays under `kubevirt.vmi.*`, `kubevirt.vm.*`, and `kubevirt.migration.*`. Scoping the operation prefix to `kubevirt.vm.operation.*` would bake the first domain into the shared layer and force a rename (or dual prefixes) when a non-VM domain adopts the mechanism.

### Field Taxonomy (OTel-Aligned)

Field names follow [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/) where they describe the same entity role with matching semantics. KubeVirt-specific attributes use the `kubevirt.` namespace prefix, following OTel's domain-prefix convention for vendor/product extensions rather than a specific registry attribute.

The taxonomy has four tiers: **operation identity** (the object the record is about, e.g. the VMIM) → **affected workload identity** (the VMI acted on; name and UID required for Alpha migration) → **optional owning-VM identity** (conditional) → **operation-specific context** (e.g., migration source/target node).

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
| `kubevirt.operation.duration_ms` | int | conditional | Duration in ms of the **operation execution window**. Emitted only on terminal records (`completed`/`failed`) when **both** persisted start and end timestamps are present (see [Duration computation](#duration-computation-alpha-migration)). Omitted when either bound is missing. Never filled in from the reconcile clock. A measured `0` is allowed only when both persisted timestamps exist and are equal; `0` must not be used as a placeholder for "unknown". |
| `error.type` | string | optional | **Deferred to Beta.** Alpha must **omit** this key on every record, including `failed`. No Alpha migration error-type enum is committed (not `timeout`, `target_pod_evicted`, or similar). Emitting ad-hoc strings in Alpha is a taxonomy violation. Beta may introduce a small, typed set of classifications per [OTel error conventions](https://opentelemetry.io/docs/specs/semconv/general/attributes/#error-attributes), only for `failed` records, only when a stable mapping from a controller failure path exists. |

##### Duration computation (Alpha migration)

`duration_ms` is restart-safe wall-clock elapsed time of the operation **execution window**, derived only from timestamps already persisted on a CR. It is not an in-memory timer and must not use `time.Now()` / the current reconcile time as a substitute for a missing bound.

For Alpha migration the **required** source is the VMIM's own persisted `status.phaseTransitionTimestamps`, because those timestamps are written on the same object and in the same status persist that gates canonical-record emission:

| Bound | Meaning | Alpha source |
|---|---|---|
| Start | Execution started | `phaseTransitionTimestamps` entry for `MigrationRunning` |
| End | Execution terminated | `phaseTransitionTimestamps` entry for `MigrationSucceeded` or `MigrationFailed` |

`vmi.status.migrationState.startTimestamp` / `endTimestamp` are **not** used as a substitute when a VMIM bound is missing. They live on a different object and can be patched on a different write (including stamping both to `now` on some early-failure paths). Using them as a fallback would re-introduce the invented-end problem this section forbids.

Rules:

1. Emit `duration_ms` only when **both** VMIM bounds are present after the status persist that gates the canonical record **and** `end >= start`.
2. If the VMIM has a `MigrationRunning` timestamp and no terminal-phase timestamp — including a failure that occurs after `MigrationRunning` but before that terminal timestamp is persisted — **omit** `duration_ms`. Do **not** use reconcile time, the log line's `ts`, VMI `migrationState.endTimestamp`, or any other clock as the end bound. A silently invented end is worse than an omitted duration.
3. If the start bound is missing (the VMIM never reached `MigrationRunning`; Alpha emits only `failed`) — **omit** `duration_ms`. That record is a terminal outcome of an operation that did not start executing; it is not a zero-length execution. Do not treat a VMI `migrationState` pair stamped to the same `now` as a start bound.
4. If both VMIM bounds are persisted and equal, a measured `0` may be emitted. That is a real measurement, not a placeholder.
5. If `end < start` (inconsistent persisted timestamps), omit `duration_ms`.

**Upgrade window:** `phaseTransitionTimestamps` is only appended when a VMIM *changes* phase after the running controller writes that field. VMIMs that already exist at upgrade — in-flight, or already terminal — may lack `MigrationRunning` and/or terminal entries for phases that occurred before the upgrade. Alpha does not backfill those timestamps. Operators must expect `duration_ms` to be omitted on those objects for the remainder of their lifetime (see [Update/Rollback Compatibility](#updaterollback-compatibility)). New VMIMs created after upgrade get timestamps on each subsequent phase change as they do today.

The helper API must expose duration as a computed optional value (see [API Examples](#api-examples)), not as a field the call site is expected to invent.

#### Affected-workload context: `kubevirt.vmi.name` / `kubevirt.vmi.uid` (required for Alpha migration)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.vmi.name` | string | yes | Name of the `VirtualMachineInstance` the operation acts on |
| `kubevirt.vmi.uid` | string | yes (Alpha migration) | UID of the fetched `VirtualMachineInstance` acted on by this migration |

`kubevirt.vmi.name` identifies the affected VMI, independent of whether it has an owning `VirtualMachine` — this is what answers "which VMI was migrated" for *every* migration record, standalone or VM-backed. It is sourced from the migration operation identity itself (`VirtualMachineInstanceMigration.Spec.VMIName`, a required field on the VMIM).

`kubevirt.vmi.uid` is required on every Alpha migration record. The controller fetches the VMI before entering the status-update path that emits these records; if the VMI is absent, it deletes or skips the VMIM and does not enter that path. The migration builder requires the fetched VMI as a constructor argument, derives its UID, and skips an invalid record with a non-canonical warning if that UID is empty. Alpha does not add an API lookup or status write to create a `failed` record for the missing-VMI path.

#### VM context: `kubevirt.vm.name` / `kubevirt.vm.uid` (conditional pair)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.vm.name` | string | conditional | Name of the owning `VirtualMachine`, when its identity is reliably known |
| `kubevirt.vm.uid` | string | conditional | UID of that `VirtualMachine` |

Both fields describe the same entity — the owning `VirtualMachine` — and are present together only when the owning `VirtualMachine`'s identity can be reliably determined: a well-formed controller `OwnerReference` of kind `VirtualMachine` with both `Name` and `UID` populated. The fields are omitted together (never one without the other, never an empty placeholder) both for a standalone VMI and for any case where an owner reference is structurally present but does not reliably identify a `VirtualMachine` (e.g. missing/empty `UID`, wrong `Kind`, or not the controller reference) — merely having *some* owner reference present is not sufficient on its own to emit these fields.

`kubevirt.vm.uid` exists because a Kubernetes `metadata.uid` does not survive delete/recreate: a VM deleted and recreated with the same namespace/name produces a *different* `VirtualMachine` object with a *different* UID. Carrying the VM's UID lets consumers distinguish operations against the current VM from operations recorded against an earlier, now-deleted VM object that happened to share the same name — something the name alone cannot do.

`kubevirt.vm.uid` is distinct from both the generic `uid` field (which reflects whichever object the logger is bound to — the VMIM, for migration records) and `kubevirt.vmi.uid` (the affected VMI's own UID — see Affected-workload context above). On a VM-backed migration record, all three UIDs can appear together with different values: `uid` = which migration, `kubevirt.vmi.uid` = which VMI was migrated, `kubevirt.vm.uid` = which VM owns that VMI.

#### Migration-specific context

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.migration.source_node` | string | conditional | Source node. Omitted when not yet known. |
| `kubevirt.migration.target_node` | string | conditional | Target node. Omitted when not yet selected (e.g., a migration that fails before a target pod is scheduled never has a target node). |

**General rule for every conditional/optional field above**: when a value is unknown or not yet available, the implementation omits the key entirely. Emitting an empty string, zero, or other placeholder in its place is a taxonomy violation, not an acceptable degenerate case. The one documented exception is a **measured** `kubevirt.operation.duration_ms` of `0` when both VMIM bounds exist and are equal (see [Duration computation](#duration-computation-alpha-migration)).

#### Field presence (Alpha migration)

"Required" in the tables above is the instrumentation contract (the helper must populate the field when the rule says so). **Presence** is the consumer contract: whether a query may assume the key exists on a given phase.

| Field | `started` | `completed` | `failed` |
|---|---|---|---|
| `kind`, `name`, `namespace`, `uid`, `component` | always | always | always |
| `kubevirt.operation.type`, `kubevirt.operation.phase` | always | always | always |
| `kubevirt.vmi.name` | always | always | always |
| `kubevirt.operation.duration_ms` | omitted | when both VMIM timestamps exist | when both VMIM timestamps exist |
| `error.type` | omitted | omitted | omitted (Alpha; see field row) |
| `kubevirt.vmi.uid` | always | always | always |
| `kubevirt.vm.name`, `kubevirt.vm.uid` | pair when owner identity is reliably known | same | same |
| `kubevirt.migration.source_node` | when known | when known | when known |
| `kubevirt.migration.target_node` | when known | when known | when known |

LogQL that requires a **when-available** key (for example `kubevirt.migration.source_node` on `completed`) is allowed to return a subset of records; it is not a supported assumption that the key is always present. Changing an **always** cell to omitted or when-available after GA is a breaking change and follows the same deprecation process as removing a field (see [Schema Stability](#schema-stability)). Changing when-available to always is additive.

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

### Concurrent operations

Alpha instruments only migration, so a concurrent hotplug on the same VMI is not a second canonical stream yet. The mechanism still needs a correlation rule before a second type is added:

- **Instance key:** bound-object `kind` + `uid` (Alpha: the VMIM). Do not introduce `kubevirt.operation.id` as a duplicate of `uid`.
- **Workload key:** `kubevirt.vmi.name` and `kubevirt.vmi.uid` (both required for Alpha migration).
- **Type key:** `kubevirt.operation.type`.

A consumer building a per-VMI timeline must group records that share a VMI key **and** the same bound `uid`. Interleaving `migration` and (later) `add_volume` records for one VMI is expected; mixing two migrations into one timeline because they share `kubevirt.vmi.name` is a consumer bug.

This VEP does not attribute concurrent operations to different users. Two users triggering two VMIMs is visible as two `uid`s in logs and as two API requests in Kubernetes audit, not as usernames on the canonical records (see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs)).

### Implementation Safety

Taxonomy keys and enum values are typed and centrally defined in a shared package (`pkg/log/structuredlog/`) imported by all components — call sites use named phase constants (e.g. `structuredlog.PhaseCompleted`) and operation-specific builder methods, not ad hoc keys or values. The migration builder sets `structuredlog.OperationMigration` internally. Component identity (`component`) is set once at binary startup and cannot be overridden at call sites.

Two distinct guarantees apply here, and this VEP does not blur them into one blanket "compile-time" claim:

- Misspelling a **constant's identifier** (e.g. `structuredlog.PhaseCompletd`, which does not exist) is a genuine Go compiler error.
- A **raw string literal** standing in for a constant (e.g. passing `"completd"` as the phase argument to `Migration`) is not rejected by the Go compiler by itself — Go allows an untyped string constant to be implicitly converted to any named string-based type, so a misspelled raw literal compiles silently. To close this gap, Alpha extends the required **static check** (custom `golangci-lint` analyzer or `go vet` pass in the kubevirt repo) to forbid raw string literals in taxonomy **key** positions (e.g. `"kubevirt.operation.type"`) *and* in taxonomy **enum-value** positions (e.g. `"completed"` in place of `structuredlog.PhaseCompleted`) outside the shared package. Developers must use the typed constants; raw string keys or enum values fail CI.

**Typed per-operation builder:** Alpha exposes `structuredlog.Migration(migration, vmi, phase)`, taking a `*virtv1.VirtualMachineInstanceMigration`, a `*virtv1.VirtualMachineInstance`, and a typed phase as required positional arguments. It returns a migration-specific builder, not a `*log.FilteredLogger`. The builder sets the operation type to `migration`, binds its private logger to the VMIM, and derives the required affected-workload fields from `migration.Spec.VMIName` and `vmi.UID`. It ensures the subject `kind` is `VirtualMachineInstanceMigration` even when the input object's GVK is unset. Call sites cannot choose another operation type or bind the record to the VMI by mistake.

- Conditional fields use named methods: `WithOwningVM(name, uid)` emits both owning-VM fields together or neither; `WithNodes(source, target)` emits each migration node only when its value is nonempty.
- `WithDurationBetween(start, end)` accepts persisted VMIM phase-transition timestamps, never a duration literal. It attaches duration only on `completed`/`failed` when both bounds exist and `end >= start`; otherwise it omits the field. Equal bounds produce a measured duration of zero. It must omit duration on `started` even if bounds are supplied.
- The underlying logger, builder state, and common record construction stay private; call sites cannot initialize builder fields through struct literals. The public builder exposes only schema-approved methods and emission methods such as `Info`/`Error`; it has no generic `With(key, value)`, logger accessor, or embedded logger that would allow arbitrary fields or identity overrides. Future operation builders expose only their own approved context methods, so a non-migration builder cannot attach migration nodes. Alpha implements only the migration builder.

The constructor still validates runtime inputs: neither object may be nil; the VMIM's name, namespace, UID, and `Spec.VMIName` and the VMI's UID must be nonempty; the VMI's name/namespace must match the VMIM's target name/namespace; and the phase must be one of Alpha's `started`/`completed`/`failed` values. These conditions cannot be guaranteed by Go argument types. On invalid input, the builder must **not** emit a partial canonical record: subsequent emission calls are no-ops, and the constructor emits exactly one **non-canonical** warning explaining the skip on a fresh VMIM-bound logger (or the base logger when the VMIM is nil), without operation/workload taxonomy keys. Consumers must not treat that warning as a lifecycle record. This validation does not add API reads or prove that the supplied phase transition persisted; the caller still constructs the builder only after a successful status persist, per [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping).

Types restrict available fields and constructor arguments; runtime validation and contract tests establish value correctness and field presence. The static check remains required for raw taxonomy keys and enum values outside the shared package, including phase literals. Exact method names may change during implementation; operation-specific field access, private logger ownership, and the required-vs-conditional split may not. See [API Examples](#api-examples).

### Contextual Logging

For Alpha, the structured-operation logger is constructed specifically for the canonical lifecycle record at its emission point, using the migration builder's private VMIM-bound KubeVirt `*log.FilteredLogger` (`log.Log.Object(migration)`) and its go-kit-backed `With` method internally to add the operation and workload context defined in [Field Taxonomy](#field-taxonomy-otel-aligned) — see [Example Log Output](#example-log-output) for a worked example. It is built and used once, at the point where a canonical lifecycle record is emitted, and then discarded — it is **not** propagated broadly through the existing reconcile call chain. Alpha does not require a `logr` adapter or a global logging implementation change.

Existing log calls elsewhere in the reconcile continue using their own existing loggers and do **not** inherit the operation taxonomy fields. This is what keeps every pre-existing log line unchanged and is required by the feature-gate/compatibility guarantee (see [Feature Gate Mechanism](#feature-gate-mechanism)): if the canonical operation logger were propagated down the call chain, existing log lines would start carrying new structured fields, which this VEP explicitly does not allow.

Broader contextual propagation — for example, passing operation context to several existing call sites via `With`, or a `context.Context`-based logger per KEP-3077 — can remain Future Work/Beta if a concrete need arises, but any such propagation must not cause existing, pre-canonical-record log lines to inherit the operation taxonomy fields.

### Instrumented Reconcile Loops

#### Alpha (minimal vertical slice)

| Component | Reconcile Loop | Operation Types Covered |
|-----------|---------------|------------------------|
| virt-controller | Migration controller | `migration` |

Alpha proves the mechanism, taxonomy, and feature gate end-to-end on one high-value operation type (migration), without requiring a broad controller refactor. That slice is the entire Alpha; it is not split across two Alpha releases.

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

**Presence guarantees** (see [Field presence](#field-presence-alpha-migration)) are part of the consumer contract, not only field names. After GA:

| Presence change | Allowed? | Process |
|---|---|---|
| New optional / when-available field | Yes | Additive; consumers ignore unknown keys |
| when-available → always on a given phase | Yes | Additive |
| always → when-available or omitted on a given phase | No (without deprecation) | Same as removing a field |
| Emitting `error.type` in Alpha | No | Field is reserved; values start at Beta |

This is deliberately weaker than the Kubernetes REST API for *additive* evolution (new `kubevirt.operation.type` values and new optional fields do not need a new VEP). It is not weaker for *breaking* evolution: rename, remove, semantic change, new required field, new phase semantics, new domain prefix, or tightening-to-loosening of an **always** presence cell all require a VEP or KubeVirt API/enhancement review. SIG-observability is asked to bless this split rather than requiring a VEP for every additive type.

#### Adding new operation types / phases after GA

Additive evolution remains allowed after GA:

| Change | After GA? | Process |
|--------|-----------|---------|
| Add a new `kubevirt.operation.type` value (e.g., `backup`) | Yes | Add typed constant + mapping row; document in release notes; no deprecation needed |
| Add a new `kubevirt.operation.phase` value | Yes, sparingly | Prefer mapping onto the existing four phases; new phases need a short design note in the PR |
| Add a new optional field within an already-approved domain (e.g., `kubevirt.migration.mode`) | Yes | Additive; consumers ignore unknown fields |
| Add a new top-level domain prefix (e.g., `kubevirt.device.*`) | Requires review | A materially different domain schema is **not** simple additive evolution — it follows the normal KubeVirt enhancement/API review process (see [Future Work](#future-work)), same as before GA |
| Rename or remove a field name or enum value | No (without deprecation) | One release deprecation notice (Beta+) / forbidden without migration path (GA) |

This VEP's explicit evolution policy is that **not every taxonomy change requires a new VEP**. New reconcile loops that instrument an already-approved operation type/phase using the existing shared constants are **implementation reuse** and do not need a new VEP. Adding a new `kubevirt.operation.type` value or a new optional field inside an already-approved domain is documented in release notes and does not need a new VEP. A new VEP (or a KubeVirt API/enhancement review) is warranted for **public-schema evolution**: changing a field's semantics, renaming or removing a field name or enum value outside the deprecation policy above, adding a new required field, defining new lifecycle-phase semantics, or introducing a materially different domain schema (e.g., a new top-level `kubevirt.<domain>.*` prefix with its own required fields). Requiring a VEP for every additive type or optional field would serialize routine instrumentation behind design-proposal overhead the graduated Alpha→Beta→GA commitments above are meant to avoid.

The single source of truth for the schema is the typed constants in `pkg/log/structuredlog/`. A unit test serializes the field name list (and known enum values) and fails if names are removed or changed unexpectedly, similar to wire-format tests for API types.

Worked call-site and JSON examples are in [API Examples](#api-examples).

## API Examples

This VEP introduces no CRD or REST API changes. The consumer-facing contract is the canonical structured log record; the in-tree API is the Go helper in `pkg/log/structuredlog`. The snippets below are the tangible examples for discussion.

### Example Log Output

KubeVirt's migration controller already builds a logger bound to the migration object today (`log.Log.Object(migration)`); the structured-logging helper is illustrated here extending that existing pattern rather than introducing an unrelated logging style.

```go
// Illustrative API. Exact helper names may change during implementation.
// Construct only after the completed transition has persisted successfully.
// Required VMIM/VMI identity is derived and validated by the constructor.
// Owning-VM identity and migration nodes are conditional.
startTS := phaseTime(migration, virtv1.MigrationRunning) // nil if absent
endTS := phaseTime(migration, virtv1.MigrationSucceeded) // terminal phase

record := structuredlog.Migration(migration, vmi, structuredlog.PhaseCompleted).
    WithOwningVM(vmName, vmUID).
    WithDurationBetween(startTS, endTS)

if state := vmi.Status.MigrationState; state != nil {
    record = record.WithNodes(state.SourceNode, state.TargetNode)
}

record.Info("Migration completed successfully")
```

`Migration` requires both typed objects and a phase argument, binds the private logger to the VMIM, and sets the operation type internally. `migration.Spec.VMIName` identifies the affected VMI; `vmi.UID` comes from the VMI fetched before `updateStatus`, which the controller does not reach when the VMI is absent. Runtime validation rejects missing or mismatched identity with a non-canonical warning and no canonical record (see [Implementation Safety](#implementation-safety)). `vmName`/`vmUID` come from the VMI's controller `OwnerReference` when it reliably identifies a `VirtualMachine`; `WithOwningVM` emits the pair together or omits both. Subject namespace and UID come from the VMIM, not the VMI. `WithNodes` omits unknown nodes; a nil migration state simply leaves both fields absent. `startTS` / `endTS` come from the VMIM `status.phaseTransitionTimestamps` for `MigrationRunning` and the terminal phase ([Duration computation](#duration-computation-alpha-migration)); `phaseTime` is an illustrative local lookup, not a second public API. `WithDurationBetween` omits duration when either bound is missing or reversed, and on nonterminal records. Call sites cannot bypass these rules with generic `.With(...)` or access the underlying logger.

The resulting log (conceptual/illustrative output, not a wire-format guarantee):

```json
{
  "level": "info",
  "timestamp": "2026-07-05T10:00:00.000000Z",
  "pos": "migration.go:1234",
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

This example shows the `completed` case where `duration_ms`, `source_node`, and `target_node` are known. `kubevirt.vmi.uid` is present on every Alpha migration record. A `failed` record emitted before a target node was selected omits `kubevirt.migration.target_node` entirely rather than emitting it empty. A `failed` record whose persisted start or end timestamp is missing omits `kubevirt.operation.duration_ms` rather than inventing an end bound from reconcile time (see [Duration computation](#duration-computation-alpha-migration)).

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

### Consumer guidance

Canonical records are a **lossy historical view**, not a transactional log of VMIM status:

- A `completed` or `failed` record **without** a matching `started` for the same bound `uid` does **not** mean the operation never started. The start may have persisted while the `started` record was lost (best-effort emit). History tables should still show the terminal record, and may mark start time unknown.
- A `started` record without a terminal record does not establish that the migration is still running. If the VMI disappears, the current controller can delete or skip the VMIM before a terminal status transition; best-effort logging can also lose a terminal record. Check live VMIM/VMI status before presenting an operation as in flight.
- Filter and join on bound `uid` (the VMIM) when building one operation's lifecycle. `kubevirt.vmi.name` alone is not an operation instance id; concurrent operations on the same VMI share it ([Concurrent operations](#concurrent-operations)).
- Do not require when-available keys (`kubevirt.migration.source_node`, `duration_ms`, …) in queries that must return every record of a phase. Use them as additional columns, not as filters that drop incomplete-but-valid records, unless that subset is intentional. `kubevirt.vmi.uid` is always present on Alpha migration records.
- `error.type` is absent in Alpha. Do not write Alpha dashboards that filter on it.
- Kubernetes audit, not these logs, answers "who requested this," including node-drain-triggered migrations.

## Alternatives

1. **Audit log correlation**: Join Loki audit stream with infrastructure stream at query time. Rejected: LogQL doesn't support cross-stream joins, requires complex external tooling.
2. **Event-exporter**: Deploy a component that watches K8s Events and pushes structured entries to Loki. Partially viable, but doesn't cover all operations (some happen without K8s Events). Still useful as a complement.
3. **Prometheus metrics for operations**: Use counters/gauges to track operations. Rejected for this use case: metrics lose event-level detail (reason, message, context). Metrics are appropriate for aggregates, not individual event inspection.
4. **managedFields parsing**: Extract identity from `.metadata.managedFields`. Rejected: only shows the last field manager, not necessarily who triggered the operation, and is complex to parse.
5. **Kubernetes Events only**: Rely solely on Kubernetes Events (already emitted for many operations) instead of adding structured logs. Rejected: Events have limited retention (typically one hour by default) and a bounded/truncated message field, and are not designed for historical querying or dashboarding; they remain valuable for real-time `kubectl describe`/watch workflows but are not a substitute for a queryable operation history.
6. **Kubernetes audit logs only**: Rely solely on Kubernetes API audit logs for operation visibility. Rejected: audit logs capture the API request/response at admission time, not the asynchronous KubeVirt-internal lifecycle (e.g., migration progress, node selection, duration) that happens after the request is accepted — see [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs).
7. **Propagate authenticated usernames into structured logs**: Rejected because Kubernetes audit already provides the authoritative identity/request record, while propagating user identity through asynchronous KubeVirt operations introduces ambiguous semantics and considerable implementation complexity.

## Does it belong to core KubeVirt?

Yes. The feature is in-process instrumentation of KubeVirt controllers at the moment a lifecycle transition is persisted. That belongs in `kubevirt/kubevirt`.

- The shared `pkg/log/structuredlog` package is imported by virt-controller (and later other in-tree components). Emission is gated on the same reconcile that writes VMIM status. An external controller, sidecar, or log-rewriter can only watch already-persisted objects and would duplicate phase mapping, object-binding, and feature-gate semantics without access to the in-memory transition that identifies *which* phase just committed.
- A [VEP-190](https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/190-kubevirt-structured-plugins/vep.md) plugin is a domain-XML / node-hook extension point. It cannot wrap virt-controller status persists or enforce a repo-wide linter on call sites.
- HCO, CDI, and other org repos own install/storage workflows, not the migration controller. Putting the helper there would still require kubevirt-core call sites and a cross-repo contract for every emission.
- Downstream consumers (Loki, Perses, troubleshooting UIs) stay out of kubevirt; this VEP only emits the records they query. Dashboards remain separate work (see [Non Goals](#non-goals)).

The two-layer split (shared constants/logger mechanism vs the Alpha VM-operation builder) is why the package is core while future domain prefixes can still be reviewed as public-schema evolution rather than grown as out-of-tree forks of the same mechanism.

## Scalability

- No existing log line is enriched or modified — Alpha adds only new canonical lifecycle-record log lines; no new API calls or watchers are introduced
- Alpha defines three *possible* canonical lifecycle-record types (`started`, `completed`, `failed`); an individual migration emits only the transitions it actually reaches, not all three unconditionally (e.g., a migration that fails before reaching `MigrationRunning` emits only `failed` — see [Functional Testing Approach](#functional-testing-approach)). Hard cap: **at most 3 canonical records per migration**.
- No annotation writes or other new per-operation API writes of any kind
- No new components deployed

### Log-volume measurement (Alpha gate)

Do not merge the kubevirt/kubevirt Alpha implementation on an unmeasured "should be small" claim. Measure on a **reference cluster** defined as the existing kubevirt sig-performance density setup: **100 VMIs**, plus a **100-migration burst** (one live migration per VMI), with virt-controller log verbosity **fixed at 2** (the KubeVirt default). Count canonical records and bytes from the KubeVirt JSON logger's stderr output; also capture the total virt-controller container logs (stdout and stderr) seen by the collector over the same window, with `StructuredOperationLogging` off and on. Keep workload and collection settings the same in both runs.

| Metric | Alpha requirement | When |
|---|---|---|
| Canonical records per migration (`kubevirt.operation.type` present) | ≤ 3 | Unit + integration tests on the implementation PR |
| Canonical-record uncompressed bytes **per migration** | ≤ 8 KiB | Implementation PR, before Alpha merge (verbosity-independent) |
| Total collector-visible virt-controller container-log bytes (stdout + stderr), FG on vs FG off, same burst | Report byte counts and percentage change; no fixed percentage threshold | Implementation PR, before Alpha merge, **verbosity 2 only** |

Record the per-migration canonical byte counts and total-log byte counts/percentage change in the implementation PR, and explain any material increase. The percentage is evidence for review, not a pass/fail threshold: unrelated logs can change its denominator. If a per-migration bound is exceeded, the implementation must shrink emission (not the bound) before merge — Alpha does not add sampling, rate limits, or dropping of canonical records as a mitigation. Re-measure against the same recipe before Beta on-by-default enablement, including the additional operation type required for Beta.

## Update/Rollback Compatibility

- **Update**: Old logs without the new structured fields will simply not have them. LogQL queries using these fields will return empty results for old entries. No breaking change.
- **In-flight and pre-existing VMIMs**: enabling the feature gate does not backfill `status.phaseTransitionTimestamps` for phases that already occurred. A VMIM that was `MigrationRunning` (or already terminal) before upgrade may never carry both duration bounds; its canonical `completed`/`failed` record omits `duration_ms`. That is expected for the remainder of that object's life, not a bug. VMIMs created after upgrade accumulate timestamps on subsequent phase changes as the controller already does.
- **Rollback**: Disabling the feature gate is safe — no canonical structured-operation records are emitted, and no existing log line is affected. There is no annotation or stamped field to roll back. VMIM `phaseTransitionTimestamps` are existing status data and are left in place.

## Functional Testing Approach

1. **Unit tests (structured-operation contract)**: verify records expose `kubevirt.vmi.name` (required, always present — the affected VMI, sourced from `migration.Spec.VMIName`), `kubevirt.vmi.uid` (required, always present — sourced from the fetched VMI), `kubevirt.vm.name`/`kubevirt.vm.uid` (conditional pair — present only when the owning `VirtualMachine`'s identity is reliably known from the VMI's controller `OwnerReference`), `kubevirt.operation.type`/`phase`, the bound-object `uid` (asserted to be the VMIM's UID specifically because the instrumented emission point explicitly binds the logger to the migration object), and, where applicable per their conditional semantics, `duration_ms` and `kubevirt.migration.source_node`/`target_node`. Alpha records **must not** contain `error.type`.
2. **Typed builder / runtime validation**: compile-check that `Migration` requires both typed objects and a phase and that its public API has no generic `With`, logger accessor, or embedded logger. Future builders must not expose migration-only methods. Verify the constructor sets type `migration`, binds subject identity to the VMIM (including kind when GVK is unset), and derives VMI identity. Nil objects, missing required identity, a mismatched VMI name/namespace, or an unsupported phase emit no canonical record and exactly one non-canonical warning without operation/workload taxonomy keys. `WithOwningVM` with only one of name/UID set emits neither VM field; `WithNodes` omits each empty node independently. Verify internal use of KubeVirt's `With` API leaves pre-existing log lines unchanged.
3. **Duration omit-if-incomplete**: `WithDurationBetween` omits `duration_ms` when start is missing, when end is missing, or when `end < start` — including a VMIM whose `phaseTransitionTimestamps` lack `MigrationRunning` (pre-upgrade / in-flight object). On terminal records, equal persisted bounds produce `duration_ms=0`. A `started` record omits duration even when both bounds are supplied. No test may pass a reconcile-now value as a substitute end bound.
4. **Identity distinctness**: for a VM-backed migration, assert the bound-object `uid`, `kubevirt.vmi.uid`, and `kubevirt.vm.uid` are three distinct values on the same record, not merely three present keys. When the VMI is absent, assert the current reconcile returns before status persistence and emits no canonical `failed` record; do not model this as a failed transition with an omitted VMI UID.
5. **Integration tests, split by outcome** — one migration operation follows exactly one of these paths, never a mix:
   - **Successful migration**: expect canonical `started` and `completed` records; no `failed` record; `duration_ms` present on `completed` when both persisted bounds exist.
   - **Failure after migration started**: expect canonical `started` and `failed` records; no `completed` record; `duration_ms` present on `failed` only when both persisted bounds exist, omitted if end was never written.
   - **Failure before the migration reaches `MigrationRunning`**: expect only a canonical `failed` record; `started` is absent; `duration_ms` is omitted (no start bound). (The exact early-failure scenario used to exercise this path is chosen during implementation.)

   Across these scenarios, verify a standalone-VMI migration, and a VMI whose owner reference does not reliably identify a `VirtualMachine` (e.g. missing `UID` or wrong `Kind`), both omit `kubevirt.vm.name`/`kubevirt.vm.uid` together, while a VM-backed migration with a valid controller `OwnerReference` carries both.
6. **Feature-gate compatibility**: with the FG **disabled**, all pre-existing logs remain unchanged and no canonical lifecycle records are emitted at all; with the FG **enabled**, pre-existing logs still remain unchanged and canonical lifecycle records are additionally emitted; Kubernetes Events are unchanged in both modes.
7. **Reconciliation-retry semantics**: repeated reconciles over the same migration state do not emit duplicate/spurious lifecycle-phase records; only meaningful state transitions produce `started`/`completed`/`failed` records (`in_progress` is deferred for Alpha, so it is not part of this test list).
8. **Delivery semantics**: assert that normal reconciliation does not produce duplicate canonical records for the same transition. Lifecycle logging is best-effort and non-transactional with the status update (see [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping)), so exactly-once delivery is not asserted or guaranteed.
9. **Log volume**: the kubevirt/kubevirt Alpha implementation PR must record the 100-VMI + 100-migration reference-burst measurement per [Log-volume measurement](#log-volume-measurement-alpha-gate), including the ≤ 8 KiB canonical byte cap per migration and the collector-visible total-log change at verbosity 2. The total-log percentage is reported for review, not used as a fixed threshold. This is an Alpha merge gate, not a per-PR unit test.
10. **LogQL validation**: run LogQL queries against structured log output on a test cluster to verify filtering works. Include a query that still returns a `completed` record when `started` is absent for the same `uid`, and a query that does not require `kubevirt.migration.source_node`.

## Risks

- **Schema instability** — migration alone may not generalize to other operation types; mitigated by validating the schema against at least one additional, materially different operation type before Beta (see [Graduation Requirements](#graduation-requirements)).
- **Misinterpretation as auditing** — consumers might assume structured logs are an audit mechanism even without a username field; mitigated via [Relationship to Kubernetes Audit Logs](#relationship-to-kubernetes-audit-logs) and the explicit Non-Goals.
- **Incomplete history tables** — a lost `started` record can leave an orphan `completed`/`failed`; mitigated by [Consumer guidance](#consumer-guidance) (do not infer "never started").
- **Missing terminal records** — the current controller can delete or skip a VMIM whose VMI has disappeared before persisting a terminal phase; consumers must not infer "still running" from an unmatched `started` record (see [Consumer guidance](#consumer-guidance)).
- **Excessive/duplicate log records** — mitigated by emitting lifecycle records only for meaningful transitions, not every reconcile, and by the [log-volume measurement gate](#log-volume-measurement-alpha-gate).
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
- 2026-09: VEP revised — template sections (`API Examples`, `Does it belong to core KubeVirt?`, Beta `On-By-Default Readiness`, signoff checklist); constructor with required positional args; duration omit-if-incomplete; log-volume measurement gate; explicit `kubevirt.operation.*` namespace and additive-without-VEP policy; Alpha retargeted from v1.10 to v1.11
- 2026-09: VEP revised — operation-instance correlation via bound `uid`; consumer guidance for lost `started` records; upgrade-window duration omission; `error.type` deferred to Beta; non-canonical warning on `ForOperation` skip; verbosity-pinned plus per-migration byte log-volume bound; field-presence contract
- 2026-09: VEP revised — Alpha logger API aligned with KubeVirt's `With` method; VMI UID required for emitted migration records; missing-VMI path documented; log-volume gate measures collector-visible container logs and treats the relative percentage as review evidence
- 2026-10: VEP revised — typed per-operation migration builder with private logger ownership, named context methods, runtime identity validation, and terminal-only duration attachment

## Graduation Requirements

### Alpha (v1.11)

Alpha is intentionally a **narrow vertical slice** so it does not depend on broad reconcile/`ctx` refactors. It is one Alpha release (migration only), not two.

- [ ] Shared `pkg/log/structuredlog` package with typed field/enum constants, private construction helpers, and a typed migration builder
- [ ] `Migration(migration, vmi, phase)` constructor derives and validates required identity and fixes type/object binding; invalid input skips the canonical record with one non-canonical warning; named methods enforce conditional fields; no public generic `With` or underlying logger access
- [ ] Feature gate `StructuredOperationLogging` checked once per canonical-record emission point (preserve every existing `msg`/severity unchanged; add new canonical records only)
- [ ] Structured fields emitted for **migration** operations in virt-controller Migration controller, per [Alpha Migration Phase Mapping](#alpha-migration-phase-mapping) (`started`/`completed`/`failed`; `in_progress` deferred)
- [ ] `duration_ms` omit-if-incomplete: never substitute reconcile time for a missing end (or start) timestamp; no `error.type` on Alpha records
- [ ] Static check / linter forbidding string-literal taxonomy keys and enum values outside the shared package
- [ ] Unit + integration tests for the migration path (fields present per taxonomy; FG off = no canonical records and no change to existing logs)
- [ ] Log-volume measurement on the 100-VMI + 100-migration reference burst at verbosity 2, within the [per-migration bounds](#log-volume-measurement-alpha-gate), with collector-visible total-log change reported on the implementation PR before merge
- [ ] Field taxonomy documented (generated from or linked to the Go constants)

### Beta (TBD)

A future proposal to graduate `StructuredOperationLogging` to Beta should demonstrate
that the common operation schema has been validated against at least one materially
different operation type beyond migration. The specific operation and implementation
scope are intentionally not defined by this VEP. The Beta target version stays TBD
until that release's planning phase, per the VEP template; the checklist item is
required before *targeting* Beta, not before accepting this Alpha VEP.

Beta graduation, including any expansion of instrumentation coverage, is out of scope
for the Alpha implementation described here and requires separate planning and review.

- [ ] Schema validated against at least one materially different operation type
- [ ] Typed `error.type` values committed for instrumented failure paths (Alpha continues to omit the key until then)
- [ ] Feature gate enabled by default
- [ ] Field taxonomy has no unresolved breaking changes from Alpha
- [ ] At least one downstream consumer validated
- [ ] No unacceptable performance or log-volume regression

#### On-By-Default Readiness

Beta features are enabled by default. Before flipping `StructuredOperationLogging` to
on-by-default, all of the following must be true:

- The Alpha migration slice has shipped at least one release with the feature gate
  available (off by default) without a taxonomy revert.
- At least one additional, materially different operation type is instrumented with
  the shared mechanism and a domain-appropriate builder, with passing contract tests
  for its required fields, linter, duration semantics, and object binding.
- Log-volume re-measurement on the same 100-VMI reference recipe at verbosity 2, with a workload
  that also exercises the additional operation type, still meets the per-operation bounds (≤ 8 KiB canonical bytes and
  ≤ 3 canonical records per operation instance unless that type documents a different hard cap),
  and reports collector-visible total-log bytes and percentage change for review.
- At least one downstream consumer (LogQL query and/or Perses LogsTable) has been
  validated against real canonical records, including `failed` records that omit
  `duration_ms` but retain required `kubevirt.vmi.uid`.
- Default-on does not change existing (non-canonical) log lines; disabling the gate
  remains a supported rollback for Beta.
- No known call site can omit a required field via a struct zero-value (constructor
  in place; linter in CI).

### GA (TBD)

- [ ] Field taxonomy stable for 2 releases without breaking changes
- [ ] Feature gate locked to on (cannot be disabled)
- [ ] At least one downstream consumer (Perses dashboard) validated in production use
