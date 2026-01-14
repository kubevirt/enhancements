# VEP: Monitoring Stack Separation via Hybrid Architecture

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal introduces a new `kubevirt-observability-controller` component
that consolidates monitoring concerns currently scattered across
`virt-controller`, `virt-handler`, and `virt-launcher`. The controller takes
ownership of cluster-state metrics generation, recording rules/alerts
management, and exposes a clear separation boundary between the KubeVirt control
plane and its monitoring stack.

To achieve this goal, this proposal refactors the gRPC interface between
`virt-handler` and `virt-launcher` to consolidate the many separate monitoring
RPCs into a unified, more flexible `GetMonitoringData` call, significantly
reducing per-scrape round-trips and allowing more flexibility in what data is
collected.

## Motivation

KubeVirt's monitoring capabilities have grown organically inside core components.
This creates several problems:

- **Tight coupling**: Metric registration, recording rules, and alerting logic are embedded
  in `virt-controller` and `virt-handler`, making it impossible to evolve them independently
  or ship monitoring fixes without a full KubeVirt release.

- **Excessive gRPC round-trips**: Every Prometheus scrape triggers many individual gRPC calls
  per VMI between `virt-handler` and `virt-launcher`, which hurts performance at scale and
  increases latency for scrape targets.

- **Rigid data model**: Each existing RPC has a fixed request/response shape with its own
  parsing logic, making it difficult to collect additional libvirt or guest agent fields.

- **Unclear ownership**: Recording rules and alert definitions live alongside operational
  controller logic, complicating code review, testing, and on-call responsibility boundaries.

Separating monitoring into a dedicated component with a unified gRPC interface addresses
all of these concerns, simplifying the overall approach, giving more flexibility to evolve
what data is collected and how it is parsed, and preserving backward compatibility with
existing deployments.

### Background

This proposal follows up on the monitoring code refactor introduced in
[kubevirt/community#219](https://github.com/kubevirt/community/pull/219), which
proposed a code refactor for the monitoring logic in all KubeVirt components. The
goal was to have a consistent monitoring package, a code structure that is easy to
maintain and evolve, while moving closer to the Kubernetes metric implementation style.

That effort recognized it was important to separate monitoring logic from
business logic to make the codebase more modular, readable, and maintainable,
reducing complexity, lowering the risk of introducing errors, and allowing each
component to be tested independently.

With that foundation in place, the work proposed here becomes significantly
simpler. The monitoring logic is already isolated in dedicated `pkg/monitoring`
directories with consistent patterns, the shared library handles metric
registration, documentation generation, and linting, and the monitoring code
across components follows a uniform structure. This proposal builds on that
foundation by taking the next logical step: extracting the already-separated
monitoring code into its own independently deployable component and
consolidating the gRPC data collection interface.

## Goals

- Introduce a `kubevirt-observability-controller` that generates cluster-state metrics from
  VM/VMI resource specs and status using Kubernetes informers.
- Move recording rules and alert management out of `virt-controller` into the new component.
- Consolidate the many existing monitoring gRPC RPCs into a unified `GetMonitoringData` RPC
  between `virt-handler` and `virt-launcher`, significantly reducing per-VMI round-trips.
- Simplify the approach for collecting and parsing monitoring data so that adding new libvirt
  domain fields or guest agent data does not require new RPCs or dedicated scrapers.
- Provide flexibility to evolve the monitoring data model.
- Maintain backward compatibility.

## Non Goals

- Replacing Prometheus as the metrics backend or introducing alternative monitoring systems.
- Changing the metrics exposition format (still OpenMetrics / Prometheus text format).
- Migrating non-monitoring concerns out of `virt-controller` or `virt-handler`.

## Definition of Users

- **Cluster administrators**: configure and operate KubeVirt monitoring, set up alert routing,
  and manage Prometheus scrape targets.
- **Platform engineers**: integrate KubeVirt metrics into broader observability stacks
  (Grafana dashboards, alerting pipelines, SLO frameworks).
- **KubeVirt developers**: contribute to monitoring features without needing to modify core
  control-plane components.

## User Stories

- As a cluster administrator, I want monitoring changes to be shipped and updated independently
  from the KubeVirt control plane so that I can receive monitoring fixes without a full
  KubeVirt upgrade.
- As a platform engineer, I want a single, well-documented source of cluster-state VM metrics
  so that I can build reliable dashboards without depending on internal `virt-controller`
  implementation details.
- As a KubeVirt developer, I want monitoring code to live in a separate component with clear
  interfaces so that I can contribute monitoring improvements without risk of breaking
  control-plane logic.

## Repos

- kubevirt/kubevirt

## Design

The proposal has three pillars:

1. **kubevirt-observability-controller**: a new component for cluster-state metrics and rule
   management.
2. **Unified Monitoring gRPC**: a consolidated RPC that replaces the many per-VMI calls
   with a simpler, more flexible approach.
3. **Recording rules and alerts migration**: relocating rule/alert definitions and lifecycle
   management to the new controller.

### 1. kubevirt-observability-controller

An external HA Deployment (with leader-elected replica), deployed and managed independently
from `virt-operator`. It is **not** part of the core KubeVirt installation and has its own
release lifecycle.

**Responsibilities:**

| Concern | Current Owner | New Owner |
|---|---|---|
| Cluster-state VM/VMI metrics (spec & status) | virt-controller | kubevirt-observability-controller |
| Runtime VM/VMI metrics (guest agent, libvirt domain) | virt-handler | kubevirt-observability-controller |
| PrometheusRule CR management | virt-controller / virt-operator | kubevirt-observability-controller |
| Alert definitions | virt-controller / virt-operator | kubevirt-observability-controller |

**Cluster-State Metrics Generation:**

The controller uses standard Kubernetes informers (shared with a `kube_state_metrics`-style
pattern) to watch `VirtualMachine` and `VirtualMachineInstance` resources. It generates metrics
from resource specifications and status fields, replacing the metrics currently emitted by
`virt-controller`.

Examples of metrics that move:

- `kubevirt_vm_created_total`
- `kubevirt_vmi_phase_count`
- `kubevirt_vmi_vcpu_seconds`
- All VM/VMI label and annotation-derived metrics

The controller exposes a `/metrics` endpoint scraped by Prometheus.

**Runtime VM/VMI Metrics Generation:**

The controller uses the `GetMonitoringData` RPC to collect the data from the guest agent and libvirt domain.

**Recording Rules and Alerts Management:**

The controller reconciles `PrometheusRule` CRs that contain KubeVirt's recording rules and
alert definitions. This gives it full ownership of the monitoring rule lifecycle:

- Create/update `PrometheusRule` objects on startup and when the KubeVirt CR changes.
- Remove stale rules on uninstall or configuration changes.
- Version-stamp rules to enable clean upgrades.

### 2. Unified Monitoring gRPC Refactor

#### Current Architecture

Today, monitoring data is served through many separate gRPC RPCs in `cmd.proto` (e.g.
`GetDomainStats`, `GetGuestInfo`, `GetUsers`, `GetFilesystems`, `GetDomain`, among others).
Each has its own request/response message (all requests are `EmptyRequest`), its own
serialization logic, and a dedicated scraper on the `virt-handler` side. The server
(`server.go`) delegates to `DomainManager` methods, which read from two caches
(`domainStatsCache` and `AsyncAgentStore`).

This architecture has two compounding problems:

1. **Performance**: Per Prometheus scrape, each VMI triggers multiple individual gRPC
   round-trips across the different scrapers.
2. **Inflexibility**: The current model limits what information is collected.
   Adding a new libvirt statistic or guest agent field requires defining a new proto message,
   writing a new RPC, adding a new scraper in `virt-handler`, and wiring the data through to
   the metrics endpoint, all tightly coupled to the existing parsing pipeline. This rigidity
   has led to useful data being left uncollected because the cost of plumbing it through is
   disproportionate.

#### Proposed Architecture

A new `GetMonitoringData` RPC consolidates monitoring queries into fewer, more flexible calls.
The caller specifies which data categories it needs via boolean flags; the server only
serializes and returns the requested fields from the existing caches. This drastically
simplifies the approach, as new libvirt or guest agent fields can be appended to the existing
messages without introducing new RPCs, scrapers, or parsing pipelines.

**Key properties:**

- **Selective serialization**: Only requested fields are populated in the response, avoiding
  unnecessary serialization overhead.
- **Simplified data pipeline**: One unified path for collecting, serializing, and parsing
  monitoring data replaces the per-RPC scraper model. Adding a new data source is a matter
  of appending a field to the proto messages rather than building an end-to-end pipeline.
- **Backward compatible**: Existing RPCs remain in the proto definition and continue to work.
  Callers can be migrated incrementally.
- **Extensible**: New libvirt domain fields and guest agent data can be appended to both
  `MonitoringRequest` and `MonitoringResponse` without breaking the wire format or existing
  callers.

#### Proto Changes

In `pkg/handler-launcher-com/cmd/v1/cmd.proto`:

```protobuf
service Cmd {
  // ... existing RPCs stay for backward compat ...
  rpc GetMonitoringData(MonitoringRequest) returns (MonitoringResponse) {}
}

message MonitoringRequest {
  bool domainStats      = 1;
  bool guestInfo        = 2;
  bool guestFilesystems = 3;
  bool guestAgent       = 4;
  bool guestUsers       = 5;
  ...
}

message MonitoringResponse {
  Response response         = 1;
  string   domainStats      = 2;
  string   guestInfo        = 3;
  string   guestFilesystems = 4;
  string   guestAgent       = 5;
  string   guestUsers       = 6;
  ...
}
```

All response fields are JSON-encoded strings (consistent with the current approach). The
messages are designed to be extended over time, as additional libvirt domain data and guest
agent fields will be appended as new boolean flags and response fields as monitoring
coverage grows.

### 3. Recording Rules and Alerts Migration

Recording rules and alert definitions are currently managed by `virt-operator` and
`virt-controller`. The migration proceeds as follows:

1. **Extract**: All `PrometheusRule` move from `virt-operator` to the
   `kubevirt-observability-controller` codebase.
2. **Reconcile**: The new controller reconciles the `PrometheusRule` CRs, ensuring they match
   the desired state on every sync loop.

## Scalability

### kubevirt-observability-controller

- Uses shared informers (list+watch) against the Kubernetes API server, the same pattern
  used by `kube-state-metrics`. Memory footprint scales linearly with the number of VM/VMI
  objects.
- HA with leader election. Horizontal scaling is not required because metric
  generation is CPU-light (serializing cached object state).
- `/metrics` endpoint is scraped by Prometheus at the configured interval (typically 30s).
  No additional load on the API server beyond the informer watches.

### Unified gRPC

- Significantly reduces per-VMI gRPC calls per scrape cycle regardless of how many data types
  are requested.
- Response size may increase per call since multiple data types are returned, but total bytes
  transferred remains the same (or decreases slightly due to reduced framing overhead).
- Selective serialization ensures the server only marshals requested fields.

## Update/Rollback Compatibility

The `kubevirt-observability-controller` is an **external** component, deployed and managed
independently from `virt-operator`. It is not part of the core KubeVirt installation, has
its own release lifecycle, and is installed/upgraded separately by the cluster administrator.
This architectural decision provides clear separation of concerns and simplifies
compatibility.

### Upgrade Scenarios

**Upgrading KubeVirt without monitoring:**

- Core KubeVirt upgrade proceeds normally.
- No metrics available (expected behavior).
- No action required.

**Upgrading KubeVirt with monitoring desired:**

- Install/upgrade `kubevirt-observability-controller` independently (it is not managed by
  `virt-operator`).
- Requires KubeVirt version with the new `GetMonitoringData` RPC.
- Can be updated on its own release cadence without updating KubeVirt.

### Rollback Scenarios

**Rolling back core KubeVirt:**

- `kubevirt-observability-controller` is external and independently managed, so no action
    required.
- If rolling back to a version without the new `GetMonitoringData` RPC:
  - `virt-controller`/`virt-handler` metrics endpoints will have VM/VMI metrics again.
  - `kubevirt-observability-controller` can remain installed (but no metrics will be
    collected), or be uninstalled.

## Migration Strategy

Cluster-state metrics, recording rules, and alert definitions currently owned
by `virt-controller` / `virt-operator` will migrate to
`kubevirt-observability-controller` over two release cycles, giving users time
to adopt the new component. The same two-phase approach applies to all three
concerns.

### Phase 1: Dual-emission (release N)

**Metrics:** Both `virt-controller` and `kubevirt-observability-controller`
emit the same cluster-state metrics. Users who install the new controller will
see duplicate series; they can use Prometheus relabeling or recording rules to
deduplicate if needed. This phase exists so that dashboards and alerts continue
to work regardless of whether the new controller is installed.

**Recording rules and alerts:** Both `virt-operator` and
`kubevirt-observability-controller` reconcile the same `PrometheusRule` CRs.
Because the rule content is identical, Prometheus evaluates the same
expressions regardless of which controller wrote the CR. If both controllers
are running, the last writer wins; since the rule bodies match, the outcome is
the same.

### Phase 2: Removal from core KubeVirt (release N+1)

`virt-controller` stops emitting the migrated cluster-state metrics and
`virt-operator` stops reconciling `PrometheusRule` CRs. From this release
onward, the only source for these metrics, recording rules, and alerts is the
`kubevirt-observability-controller`. Users who depend on any of these **must**
install the new controller before upgrading to this release.

### Timeline summary

| Release | virt-controller metrics | PrometheusRule reconciliation (virt-operator) | kubevirt-observability-controller | Action required |
|---|---|---|---|---|
| N | Emitted | Active | Emits metrics + reconciles rules (if installed) | None: install the new controller at your convenience |
| N+1 | **Removed** | **Removed** | Sole source of metrics, rules, and alerts | Install kubevirt-observability-controller before upgrading |

### Communication plan

- Release N release notes will announce the deprecation of cluster-state
  metrics in `virt-controller` and `PrometheusRule` management in
  `virt-operator`, and recommend installing
  `kubevirt-observability-controller`.
- Release N+1 release notes will list the removal as a breaking change with
  a link to the installation guide for the new controller.

## Functional Testing Approach

- **Unit tests**: Each metric generator function is tested independently with synthetic
  VM/VMI objects. The unified gRPC handler is tested with mock `DomainManager` implementations
  to verify selective serialization.
- **Integration tests**: A test harness spins up the `kubevirt-observability-controller` with
  a fake Kubernetes API server, creates VM/VMI objects, and asserts the expected metrics are
  exposed on the `/metrics` endpoint.
- **gRPC integration tests**: A test `virt-launcher` gRPC server verifies that
  `GetMonitoringData` returns correct data for various flag combinations, and that the
  fallback to legacy RPCs works when the new RPC is unimplemented.
- **E2E tests**:
  - Deploy a cluster with the new controller and verify metrics appear in Prometheus.
  - Verify that `PrometheusRule` CRs are created, updated on config change, and removed on
    uninstall.
  - Perform a rolling upgrade and verify no metric gaps during the transition.
  - Verify gRPC fallback behavior during mixed-version rolling upgrades.

## Implementation Phases

1. **Proto changes**: Add `GetMonitoringData` RPC, `MonitoringRequest`, and
   `MonitoringResponse` messages. Regenerate Go code.
2. **virt-launcher server**: Implement the `GetMonitoringData` handler, delegating to existing
   `DomainManager` methods.
3. **virt-handler caller**: Update the domain stats scraper, downward metrics, and REST
   handlers to use the new RPC with version-negotiation fallback.
4. **kubevirt-observability-controller scaffold**: Create the external component (new binary,
   Deployment manifest, RBAC, ServiceAccount, and Service), managed outside of `virt-operator`.
5. **Cluster-state metrics migration**: Move VM/VMI informer-based metrics from
   `virt-controller` to the new controller.
6. **Recording rules and alerts migration**: Move `PrometheusRule` reconciliation to the new
   controller.
7. **Dual-emission release (N)**: `virt-controller` continues emitting cluster-state
   metrics and `virt-operator` continues reconciling `PrometheusRule` CRs, while
   `kubevirt-observability-controller` emits the same metrics and reconciles the same
   rules. Deprecation notice published in release notes.
8. **Removal release (N+1)**: `virt-controller` stops emitting the migrated metrics and
   `virt-operator` stops reconciling `PrometheusRule` CRs. Users must install
   `kubevirt-observability-controller` to retain metrics, recording rules, and alerts.

## Implementation History

<!-- Filled in as implementation progresses -->

## Graduation Requirements

### Alpha

- [ ] Feature gate `MonitoringStackSeparation` guards KubeVirt-side code changes
  (gRPC refactor in `virt-handler`/`virt-launcher`)
- [ ] `GetMonitoringData` RPC implemented in `virt-launcher` and called by `virt-handler`
  with fallback to legacy RPCs
- [ ] Cluster-state metrics emitted by the new controller (subset of metrics migrated)
- [ ] Unit and integration test coverage for the new RPC and controller

### Beta

- [ ] All cluster-state metrics migrated from `virt-controller`
- [ ] Recording rules and alert management fully owned by the new controller
- [ ] E2E tests covering upgrade/rollback scenarios and gRPC fallback
- [ ] Legacy individual monitoring RPCs deprecated in documentation
- [ ] Performance benchmarks comparing per-scrape latency before and after

### GA

- [ ] Legacy individual monitoring RPCs removed from `virt-handler` caller code
  (proto definitions kept for wire compatibility)
- [ ] Cluster-state metrics removed from `virt-controller` and `PrometheusRule`
  reconciliation removed from `virt-operator` (release N+1);
  `kubevirt-observability-controller` is the sole source for metrics, recording
  rules, and alerts
- [ ] Upgrade/rollback tests pass across two consecutive releases
- [ ] Documentation updated with new architecture diagrams and operator guide
