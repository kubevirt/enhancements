# VEP #373: Structured Operation Logging with Username Enrichment

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version: v1.12

### Feature Gate

- Feature gate name: `StructuredOperationLogging`

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Establish a general-purpose structured logging framework for KubeVirt and instrument VM operation lifecycle events as the first domain. The framework provides a shared Go package with typed field constants, contextual logger builders, and component-identity patterns that any KubeVirt component can adopt. The first domain — VM operations (migration, lifecycle change, storage, snapshot, network hotplug) — defines a field taxonomy for machine-parseable JSON log entries. Additionally, the username of the user who initiated the operation will be propagated via a virt-api admission webhook annotation and included in the structured log output.

## Motivation

KubeVirt components currently log operation information in semi-structured, human-readable format. This makes it difficult for observability tools (Loki, Perses dashboards) to reliably filter and display VM operation events. Downstream features like the VM Operations Timeline (filterable event log similar to the OCP Audit Log Viewer) and In-flight Operations tracking require:

1. **Consistent field names** across all controllers for LogQL filtering
2. **Username tracking** to know who initiated an operation
3. **Operation lifecycle phases** (started, completed, failed) for duration calculation and in-progress detection
4. **Machine-parseable format** that won't break between KubeVirt versions

The Kubernetes project itself has adopted structured logging ([KEP-1602](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/1602-structured-logging)) and contextual logging ([KEP-3077](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/3077-contextual-logging)). This VEP brings KubeVirt in line with that direction.

## Goals

- Establish a general structured logging framework (shared package, typed constants, contextual logger builders) usable by all KubeVirt components
- Define a standard field taxonomy for VM operation log entries as the first instrumented domain
- Prove the framework end-to-end in Alpha on migration (virt-controller + virt-api username path); expand to other VM lifecycle controllers in Beta
- Propagate the initiating username from virt-api admission webhook to controller logs via resource annotation
- Enable downstream consumers to build reliable LogQL queries without brittle regex parsing
- Use Go's `logr` contextual logging (`With` / `WithValues`) to propagate operation context; defer full `ctx`-based propagation to Beta where useful

## Non Goals

- Instrumenting all existing log call sites in Alpha — migration to structured logging across all components will happen incrementally in subsequent releases
- Changing the log transport mechanism (CLF/Loki pipeline) — logs continue to flow via stdout
- Building the downstream dashboards (separate work item)
- Modifying the Kubernetes Events API or creating new CRDs
- Providing the same backward-compatibility guarantees as the Kubernetes REST API during Alpha/Beta (see [Schema Stability](#schema-stability) for the graduated commitment)

## Definition of Users

- **Cluster administrators** who use Loki/Perses to monitor VM operations and troubleshoot issues
- **Platform engineers** who build observability dashboards consuming KubeVirt logs
- **Security/compliance teams** who need to audit who performed which operations on VMs

## User Stories

- As a cluster admin, I want to query Loki for all migration events for a specific VM so I can see its migration history.
- As a platform engineer, I want to build a Perses LogsTable dashboard that filters VM operations by namespace, operation type, and username without writing fragile regex.
- As a security auditor, I want to see which user initiated a VM deletion or migration.
- As an SRE, I want to filter operations by severity (Normal/Warning) and by source component to quickly find errors.

## Repos

- `kubevirt/kubevirt` — virt-controller, virt-handler, virt-api, virt-operator changes
- `kubevirt/enhancements` — this VEP

## Design

### Feature Gate Mechanism

This feature **augments** existing logs rather than replacing them. The human-readable `msg` field remains unchanged. When the `StructuredOperationLogging` feature gate is enabled, additional structured key-value fields (`kubevirt.*`, `k8s.*`, `user.*`) are attached to the log entry via contextual logging.

There is no `if/else` at each log call site. Instead, the feature gate is checked once at the top of each reconcile loop to decide whether the contextual logger is enriched:

```go
logger := log.Log.WithValues("k8s.namespace.name", vmi.Namespace)
if featuregates.IsEnabled(featuregates.StructuredOperationLogging) {
    logger = logger.WithValues(
        "kubevirt.operation.type", "migration",
        "kubevirt.vm.name", vmi.Name,
        "user.name", getLastModifiedBy(vmi),
    )
}
logger.Info("Migration completed successfully")
```

- **FG off**: logs are identical to today's output (no behavior change)
- **FG on**: same `msg`, with additional structured fields appended

### Two-Layer Architecture

This VEP introduces two distinct layers:

1. **General framework** — A shared Go package (e.g., `pkg/log/structuredlog`) providing typed field constants, contextual logger builders, and a component-identity pattern (set once at startup). This layer is domain-agnostic and reusable by any future instrumentation domain.

2. **VM operations domain** — The first domain-specific taxonomy built on the framework. Defines `kubevirt.operation.*` fields, operation type mappings, and username propagation. Future domains (device health, operator lifecycle, scheduling infrastructure) can define their own field extensions (e.g., `kubevirt.device.*`, `kubevirt.operator.*`) using the same framework without requiring a new VEP.

### Field Taxonomy (OTel-Aligned)

Field names follow [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/) where applicable. Standard OTel attributes are reused as-is. KubeVirt-specific attributes use the `kubevirt.` namespace prefix, following OTel conventions for domain-specific extensions.

#### Standard OTel Attributes (reuse existing conventions)

| Field | OTel Source | Type | Required | Description |
|-------|-------------|------|----------|-------------|
| `k8s.namespace.name` | [K8s Resource](https://opentelemetry.io/docs/specs/semconv/resource/k8s) | string | yes | Target VM namespace |
| `k8s.object.kind` | [K8s Resource](https://opentelemetry.io/docs/specs/semconv/resource/k8s) | string | yes | K8s resource kind being operated on |
| `k8s.object.name` | [K8s Resource](https://opentelemetry.io/docs/specs/semconv/resource/k8s) | string | yes | Specific resource instance name |
| `k8s.event.reason` | [K8s Events](https://opentelemetry.io/docs/specs/semconv/registry/attributes/k8s/) | string | yes | Event reason string (e.g., `SuccessfulMigration`) |
| `k8s.event.reporter.name` | [K8s Events](https://opentelemetry.io/docs/specs/semconv/registry/attributes/k8s/) | string | yes | Source controller: `virt-controller`, `virt-handler` |
| `user.name` | [User](https://opentelemetry.io/docs/specs/semconv/registry/attributes/user/) | string | yes | Who initiated the operation (short name/login) |

#### KubeVirt-Specific Attributes (new, `kubevirt.` prefix)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `kubevirt.vm.name` | string | yes | Target VM name (may differ from `k8s.object.name` when the involved object is a Migration or DataVolume) |
| `kubevirt.operation.type` | string | yes | Category (see [Operation Type Mapping](#operation-type-mapping) below): `migration`, `lifecycle`, `storage`, `snapshot`, `network`, `scheduling`. New values may be added after GA; see [Schema Stability](#schema-stability). |
| `kubevirt.operation.phase` | string | yes | Logging-domain lifecycle: `started`, `in_progress`, `completed`, `failed`. Independent of CRD `.status.phase` (see [Operation Phase vs API Phase](#operation-phase-vs-api-phase)). |
| `kubevirt.operation.duration_ms` | int | no | Duration in ms (only for `completed`/`failed` phase). Derived from CR status timestamps (e.g., `migrationState.startTimestamp`/`endTimestamp`), not in-memory timers — restart-safe and requires no cross-component coordination. Omitted when no persisted timestamps are available. |
| `kubevirt.migration.source_node` | string | no | Source node (migrations only) |
| `kubevirt.migration.target_node` | string | no | Target node (migrations only) |
| `error.type` | string | no | Error classification (only for `failed` phase), per [OTel error conventions](https://opentelemetry.io/docs/specs/semconv/general/attributes/#error-attributes) |

#### Severity Mapping

Log severity follows OTel LogRecord conventions:
- K8s Event `type: Normal` → SeverityText `INFO`, SeverityNumber 9
- K8s Event `type: Warning` → SeverityText `WARN`, SeverityNumber 13

#### Operation Type Mapping

Each `kubevirt.operation.type` value maps to specific KubeVirt events/operations:

| Operation Type | Events / Operations |
|----------------|---------------------|
| `migration` | `MigrationStarted`, `MigrationSucceeded`, `MigrationFailed`, `MigrationAborted`, `MigrationTargetReady` |
| `lifecycle` | `Started`, `Stopped`, `Restarted`, `Paused`, `Unpaused`, `Deleted`, `Created`, `FailedStart` |
| `storage` | `AddVolume`, `RemoveVolume`, `VolumeReady`, `DataVolumeCreated`, `DataVolumeReady`, `DataVolumeFailed` |
| `snapshot` | `SnapshotStarted`, `SnapshotSucceeded`, `SnapshotFailed`, `RestoreStarted`, `RestoreSucceeded` |
| `network` | `InterfaceHotplug`, `InterfaceHotunplug`, `NetworkReady` |
| `scheduling` | `SchedulingFailed`, `Scheduled`, `Evicted`, `NodeDrainStarted` |

#### Operation Phase vs API Phase

`kubevirt.operation.phase` is a **logging-domain** concept, not a mirror of CRD `.status.phase`:

- The four values (`started`, `in_progress`, `completed`, `failed`) describe the lifecycle of a logged operation for consumers (duration, in-flight detection, failure filtering).
- Many KubeVirt CRDs do not expose a status phase at all, and those that do use domain-specific values (e.g., VMI `Running`/`Succeeded`, migration `Succeeded`/`Failed`) that do not map 1:1 to the logging phases.
- Controllers map their own status transitions / events onto these logging phases at the emit site. For example, emitting a `MigrationSucceeded` event maps to `completed`; observing an in-flight migration maps to `in_progress`.
- There is intentionally **no** requirement that every CRD grow a `.status.phase` field for this VEP.

### Username Propagation

The annotation `kubevirt.io/last-modified-by` is stamped on the **triggering object** — the resource whose creation or mutation initiates the operation. This avoids conflicts when concurrent operations are triggered by different users on the same VM.

#### Per-Object Stamping Rules

| Trigger | Annotated Object | How | Phase |
|---------|-----------------|-----|-------|
| Migration | VirtualMachineInstanceMigration (VMIM) | virt-api mutating admission webhook on VMIM creation | Alpha |
| Direct VM/VMI mutation (start, stop, restart, pause) | VM or VMI | virt-api mutating admission webhook on the subresource endpoint | Beta |
| Volume hotplug | VM | virt-api subresource handler stamps annotation on VM | Beta |
| Controller-initiated (eviction, node drain) | — (no annotation) | Logged as service account identity: `system:serviceaccount:kubevirt:kubevirt-controller` | Beta |
| Snapshot / Restore | VirtualMachineSnapshot / VirtualMachineRestore | virt-api mutating admission webhook on CR creation | Beta |

#### Flow

1. virt-api mutating admission webhook intercepts the request
2. Webhook reads `admission.Request.UserInfo.Username`
3. Webhook stamps annotation `kubevirt.io/last-modified-by: <username>` on the **triggering object**
4. virt-controller/virt-handler reads the annotation from the triggering object when processing the operation
5. Username is included in the structured log entry as `user.name`
6. For controller-initiated operations (no user request), `user.name` is the service account identity

#### Concurrent Operations

Because the annotation lives on the triggering object (not on a shared VMI), concurrent operations by different users do not conflict. For example: user A creates a VMIM (migration) while user B hotplugs a volume on the same VM — each operation reads its username from its own triggering object independently.

#### Annotation Integrity

Users cannot manually set or spoof the `kubevirt.io/last-modified-by` annotation:

- The **mutating admission webhook always overwrites** any existing value with the authenticated username from `admission.Request.UserInfo.Username` on every lifecycle-changing mutation.
- A **validating admission webhook** rejects any direct writes to this annotation (e.g., via `kubectl annotate` or metadata patches) unless the request originates from the virt-api service account. This ensures the annotation can only be set by the system.

### Implementation Safety

Field keys are defined as typed constants in a shared package (`pkg/log/structuredlog/`) imported by all components. Component identity (`k8s.event.reporter.name`) is set once at binary startup and cannot be overridden at call sites. A typed operation logger builder enforces required fields at compile time — omitting a required field is a compile error, not a runtime bug.

To prevent string-literal drift, Alpha also adds a **static check** (custom `golangci-lint` analyzer or `go vet` pass in the kubevirt repo) that flags use of known taxonomy key strings (e.g., `"kubevirt.operation.type"`) outside the shared package. Developers must use the typed constants; raw string keys fail CI.

### Contextual Logging

Alpha uses `log.Log.With()` / `WithValues()` at the top of each instrumented reconcile (or subresource handler) and passes the enriched logger explicitly down the call chain. This does **not** require a full `context.Context`-based logger propagation refactor across the codebase.

```go
logger := log.Log.With(
    structuredlog.OperationType, structuredlog.OpMigration,
    structuredlog.Namespace, vmi.Namespace,
    structuredlog.VMName, vmi.Name,
    structuredlog.ObjectKind, "VirtualMachineInstanceMigration",
    structuredlog.ObjectName, migration.Name,
    structuredlog.UserName, getLastModifiedBy(migration),
)
```

All subsequent log calls that receive this logger inherit these fields automatically.

Broader `ctx`-based contextual logging (storing the logger in `context.Context` per KEP-3077) is deferred to Beta/GA as an incremental refactor where it pays off, and is not a blocker for Alpha.

### Instrumented Reconcile Loops

#### Alpha (minimal vertical slice)

| Component | Reconcile Loop | Operation Types Covered |
|-----------|---------------|------------------------|
| virt-controller | Migration controller | `migration` |
| virt-api | Mutating + validating admission on VMIM | Username stamping for migration path |

Alpha proves the framework, taxonomy, feature gate, and username path end-to-end on one high-value operation type (migration), without requiring a broad controller refactor.

#### Beta (expand coverage)

| Component | Reconcile Loop | Operation Types Covered |
|-----------|---------------|------------------------|
| virt-controller | VM controller | `lifecycle` (start, stop, restart) |
| virt-controller | DataVolume controller | `storage` |
| virt-controller | Snapshot controller | `snapshot` |
| virt-controller | Network controller | `network` |
| virt-handler | VMI reconcile loop | `lifecycle` (phase transitions), `scheduling` (eviction) |
| virt-api | VM/VMI subresources, hotplug, Snapshot/Restore admission | Username stamping for remaining triggers |

### Schema Stability

This VEP defines the **general stability and deprecation policy** for the structured logging field taxonomy (not only the Alpha VM-operations domain). The taxonomy is a consumer-facing contract with graduated commitments:

- **Alpha**: Field *names* and *enum values* may be added or renamed between minor releases with release notes. No changes within patch releases.
- **Beta**: Field names are stable. Removal or rename of a field name or enum value requires one release of deprecation notice.
- **GA**: Field *names* are frozen with the same backward-compatibility guarantees as KubeVirt API types.

#### Adding new operation types / phases after GA

Additive evolution remains allowed after GA:

| Change | After GA? | Process |
|--------|-----------|---------|
| Add a new `kubevirt.operation.type` value (e.g., `backup`) | Yes | Add typed constant + mapping row; document in release notes; no deprecation needed |
| Add a new `kubevirt.operation.phase` value | Yes, sparingly | Prefer mapping onto the existing four phases; new phases need a short design note in the PR |
| Add a new optional field (e.g., `kubevirt.migration.mode`) | Yes | Additive; consumers ignore unknown fields |
| Add a new domain prefix (e.g., `kubevirt.device.*`) | Yes | Follows [Future Work](#future-work); same shared package |
| Rename or remove a field name or enum value | No (without deprecation) | One release deprecation notice (Beta+) / forbidden without migration path (GA) |

New controllers with new operation types do **not** need a new VEP solely to register values; they extend the shared constants under this policy. A new VEP is warranted only for cross-cutting design changes (e.g., changing identity propagation or severity mapping).

The single source of truth for the schema is the typed constants in `pkg/log/structuredlog/`. A unit test serializes the field name list (and known enum values) and fails if names are removed or changed unexpectedly, similar to wire-format tests for API types.

### Example Log Output

```json
{
  "level": "info",
  "ts": "2026-07-05T10:00:00.000Z",
  "logger": "virt-controller.migration-controller",
  "msg": "Migration completed successfully",
  "kubevirt.operation.type": "migration",
  "kubevirt.operation.phase": "completed",
  "kubevirt.operation.duration_ms": 45000,
  "kubevirt.vm.name": "web-server-1",
  "kubevirt.migration.source_node": "worker-1",
  "kubevirt.migration.target_node": "worker-3",
  "k8s.namespace.name": "production",
  "k8s.object.kind": "VirtualMachineInstanceMigration",
  "k8s.object.name": "web-server-1-migration-abc123",
  "k8s.event.reason": "SuccessfulMigration",
  "k8s.event.reporter.name": "virt-controller",
  "user.name": "admin@example.com"
}
```

### Downstream LogQL Usage

With structured logs, downstream consumers can write reliable queries using OTel field names:

```logql
{kubernetes_namespace_name="kubevirt", kubernetes_container_name="virt-controller"}
  | json
  | kubevirt_operation_type="migration"
  | k8s_namespace_name="production"
  | user_name=~"admin.*"
  | kubevirt_operation_phase="failed"
```

> **Note**: Loki's `| json` parser converts dotted JSON keys to underscored field names
> (e.g., `"k8s.namespace.name"` becomes `k8s_namespace_name` in filter expressions).
> This is standard Loki behavior and does not affect the JSON log format itself.

## API Examples

### Annotation on VirtualMachine/VirtualMachineInstance

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
  namespace: my-ns
  annotations:
    kubevirt.io/last-modified-by: "admin@example.com"
```

No new CRDs or API fields are introduced. The annotation is internal metadata used for logging enrichment only.

## Alternatives

1. **Audit log correlation**: Join Loki audit stream with infrastructure stream at query time. Rejected: LogQL doesn't support cross-stream joins, requires complex external tooling.
2. **Event-exporter**: Deploy a component that watches K8s Events and pushes structured entries to Loki. Partially viable, but doesn't cover all operations (some happen without K8s Events). Still useful as a complement.
3. **Prometheus metrics for operations**: Use counters/gauges to track operations. Rejected for this use case: metrics lose event-level detail (reason, message, username). Metrics are appropriate for aggregates, not individual event inspection.
4. **managedFields parsing**: Extract user from `.metadata.managedFields`. Rejected: only shows the last field manager, not necessarily who triggered the operation, and is complex to parse.

## Scalability

- No new API calls or watchers — only adds fields to existing log output
- Username annotation is a single annotation write per lifecycle mutation (already happening in admission webhook path)
- Log volume increase is negligible (adding ~200 bytes per operation log line)
- No new components deployed

## Update/Rollback Compatibility

- **Update**: Old logs without new fields will simply not have the structured fields. LogQL queries using these fields will return empty results for old entries. No breaking change.
- **Rollback**: Removing the username annotation write is safe — controllers will log `user.name: ""` (empty). Downstream queries handle empty gracefully (`=~".*"`).

## Functional Testing Approach

1. **Unit tests**: Verify that operation lifecycle log entries contain all required fields with correct values
2. **Integration tests**: Deploy VMs, trigger operations (migrate, snapshot, hotplug), verify structured log output contains expected fields
3. **Username propagation test**: Perform an operation as a specific user, verify the username appears in structured logs
4. **LogQL validation**: Run LogQL queries against structured log output on a test cluster to verify filtering works

## Future Work

The general framework established here is designed to be extended to additional domains beyond VM operations. Potential future instrumentation domains include:

- **Device health** (`kubevirt.device.*`) — device plugin registration, health status changes, resource allocation failures
- **Operator lifecycle** (`kubevirt.operator.*`) — upgrade progress, component rollout, configuration reconciliation
- **Infrastructure scheduling** (`kubevirt.scheduling.*`) — node capacity decisions, topology constraints, placement failures

Each domain would define its own field taxonomy using the shared package and contextual logger patterns. Extension to new domains does not require a new VEP — contributors add domain-specific constants to the shared package and instrument the relevant reconcile loops.

## Implementation History

- 2026-07: VEP created

## Graduation Requirements

### Alpha (v1.10)

Alpha is intentionally a **narrow vertical slice** so it does not depend on broad reconcile/`ctx` refactors:

- [ ] Shared `pkg/log/structuredlog` package with typed field/enum constants and operation logger builder
- [ ] Feature gate `StructuredOperationLogging` checked once at reconcile/handler entry (augment, do not replace `msg`)
- [ ] Structured fields emitted for **migration** operations in virt-controller Migration controller
- [ ] Username annotation stamped by virt-api on VMIM creation; validating webhook rejects direct user writes
- [ ] Static check / linter forbidding string-literal taxonomy keys outside the shared package
- [ ] Unit + integration tests for the migration path (fields present, username propagated, FG off = no new fields)
- [ ] Field taxonomy documented (generated from or linked to the Go constants)

### Beta (v1.11)

- [ ] Expand instrumentation to VM, DataVolume, Snapshot, Network controllers and virt-handler VMI reconcile (see Beta table above)
- [ ] Username stamping extended to VM/VMI subresources, volume hotplug, Snapshot, and Restore
- [ ] Controller-initiated ops log service-account identity as `user.name`
- [ ] Optional: adopt `context.Context` logger propagation where it reduces boilerplate (not required for all call sites)
- [ ] Feature gate enabled by default
- [ ] Field taxonomy unchanged from Alpha (no breaking changes)
- [ ] At least one downstream consumer (Perses dashboard or LogQL queries) validated
- [ ] No performance regression observed in benchmarks
- [ ] No log volume increase >10% measured on reference cluster

### GA

- [ ] Field taxonomy stable for 2 releases without breaking changes
- [ ] Feature gate locked to on (cannot be disabled)
- [ ] At least one downstream consumer (Perses dashboard) validated in production use
