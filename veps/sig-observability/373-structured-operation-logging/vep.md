# VEP #373: Structured Operation Logging with Username Enrichment

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Introduce structured, machine-parseable logging for VM operation lifecycle events in KubeVirt components (virt-controller, virt-handler, virt-api). Each operation event (migration, lifecycle change, storage operation, snapshot, network hotplug) will be emitted as a JSON log entry with a well-defined field taxonomy. Additionally, the username of the user who initiated the operation will be propagated via a virt-api admission webhook annotation and included in the structured log output.

## Motivation

KubeVirt components currently log operation information in semi-structured, human-readable format. This makes it difficult for observability tools (Loki, Perses dashboards) to reliably filter and display VM operation events. Downstream features like the VM Operations Timeline (filterable event log similar to the OCP Audit Log Viewer) and In-flight Operations tracking require:

1. **Consistent field names** across all controllers for LogQL filtering
2. **Username tracking** to know who initiated an operation
3. **Operation lifecycle phases** (started, completed, failed) for duration calculation and in-progress detection
4. **Machine-parseable format** that won't break between KubeVirt versions

The Kubernetes project itself has adopted structured logging ([KEP-1602](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/1602-structured-logging)) and contextual logging ([KEP-3077](https://github.com/kubernetes/enhancements/tree/master/keps/sig-instrumentation/3077-contextual-logging)). This VEP brings KubeVirt in line with that direction.

## Goals

- Define a standard field taxonomy for VM operation log entries
- Implement structured logging in virt-controller and virt-handler for all VM lifecycle operations
- Propagate the initiating username from virt-api admission webhook to controller logs via resource annotation
- Enable downstream consumers to build reliable LogQL queries without brittle regex parsing
- Use Go's `logr` contextual logging to propagate operation context through call chains

## Non Goals

- Changing the log transport mechanism (CLF/Loki pipeline) — logs continue to flow via stdout
- Building the downstream dashboards (separate work item)
- Modifying the Kubernetes Events API or creating new CRDs
- Guaranteeing log schema stability as a formal API (best-effort stable, may evolve between minor versions)

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
| `kubevirt.operation.type` | string | yes | Category (see [Operation Type Mapping](#operation-type-mapping) below): `migration`, `lifecycle`, `storage`, `snapshot`, `network`, `scheduling` |
| `kubevirt.operation.phase` | string | yes | Lifecycle: `started`, `in_progress`, `completed`, `failed` |
| `kubevirt.operation.duration_ms` | int | no | Duration in ms (only for `completed`/`failed` phase) |
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

### Username Propagation

1. virt-api mutating admission webhook intercepts lifecycle-changing mutations (start, stop, restart, migrate, add/remove volume, snapshot)
2. Webhook reads `admission.Request.UserInfo.Username`
3. Webhook stamps annotation `kubevirt.io/last-modified-by: <username>` on the resource
4. virt-controller/virt-handler reads the annotation when processing the operation
5. Username is included in the structured log entry
6. For system-initiated operations, username is the service account (e.g., `system:serviceaccount:kubevirt:kubevirt-controller`)

#### Annotation Integrity

Users cannot manually set or spoof the `kubevirt.io/last-modified-by` annotation:

- The **mutating admission webhook always overwrites** any existing value with the authenticated username from `admission.Request.UserInfo.Username` on every lifecycle-changing mutation.
- A **validating admission webhook** rejects any direct writes to this annotation (e.g., via `kubectl annotate` or metadata patches) unless the request originates from the virt-api service account. This ensures the annotation can only be set by the system.

### Contextual Logging

Use `log.Log.With()` to establish operation context at the top of each reconcile loop:

```go
logger := log.Log.With(
    "kubevirt.operation.type", "migration",
    "k8s.namespace.name", vmi.Namespace,
    "kubevirt.vm.name", vmi.Name,
    "k8s.object.kind", "VirtualMachineInstanceMigration",
    "k8s.object.name", migration.Name,
    "user.name", getLastModifiedBy(vmi),
)
```

All subsequent log calls within that reconcile inherit these fields automatically.

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

## Implementation History

- 2026-07: VEP created

## Graduation Requirements

### Alpha (v1.10)

- [ ] Structured logging implemented in virt-controller, virt-handler, and virt-api for all operation types
- [ ] Username annotation stamped by virt-api for all lifecycle mutations
- [ ] Validating webhook rejects direct user writes to the annotation
- [ ] Unit and integration tests passing
- [ ] Documentation: field taxonomy documented in kubevirt.io

### GA

- [ ] Field taxonomy stable for 2 releases without breaking changes
- [ ] No performance regression observed in benchmarks
- [ ] No log volume increase >10% measured on reference cluster
- [ ] At least one downstream consumer (Perses dashboard) validated in production use
