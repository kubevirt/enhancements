# VEP #81: Monitoring Stack Separation via Hybrid Architecture

## Release Signoff Checklist

Items marked with (R) are required
*prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in
[kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes the creation of `kubevirt-metrics-exporter`, a new external
component that decouples metrics collection from KubeVirt's core components
(virt-controller, virt-handler) through a hybrid architecture. The proposal
addresses resource overhead, maintainability, and flexibility limitations of the
current embedded monitoring approach.

The hybrid architecture combines two collection methods:

1. **Cluster State Metrics**: Uses Kubernetes informers to generate metrics from
VM/VMI resource specifications and status (replacing virt-controller metrics)

2. **Runtime Metrics**: Queries virt-handler's new `/monitoring/query` endpoint
to collect libvirt domain statistics via a validated command allowlist
(replacing virt-handler metrics)

This separation enables independent lifecycle management of the monitoring
stack, reduces overhead on critical KubeVirt components, and allows for flexible
monitoring configurations including the ability to disable monitoring entirely.
The design maintains full backward compatibility with existing metric schemas
and dashboards while providing a clear migration path from embedded to external
metrics collection.

Key benefits include: independent monitoring stack upgrades, ability to backport
new metrics/alerts to older KubeVirt versions, reduced resource consumption on
core components, and flexibility in metrics export formats.

## Motivation

### Current State

KubeVirt components currently expose Prometheus metrics directly through their
HTTP endpoints:

- `virt-handler`: Exposes 60+ VMI runtime metrics via local libvirt domain
statistics
- `virt-controller`: Exposes VM lifecycle and cluster-wide metrics
- `virt-api`: Exposes API request and connection metrics
- `virt-operator`: Exposes operator health and configuration metrics

### Problems with Current Approach

1. **Resource Overhead**: Metrics collection adds CPU/memory overhead to
critical components
2. **Coupling**: Metrics collection logic is tightly coupled with core component
functionality
3. **Limited Flexibility**: Difficult to customize metrics export formats,
destinations, or processing
4. **Scalability**: High-frequency metrics collection can impact component
performance
5. **Maintenance**: Metrics changes require core component updates and releases
6. **Limited Backports**: New metrics, recording rules and alerts can't be
backported to older versions
7. **Limited Monitoring Configuration Options**: Recording rules and alerts are
only available through KubeVirt releases, limiting independent monitoring stack
evolution

## Goals

- **Decouple** metrics collection from core KubeVirt component logic
- **Migrate Out** metrics, recording rules, and alerts from core components to
external repositories for independent lifecycle management
- **Maximize Efficiency** by providing optimized collection alternatives through
hybrid architecture
- **Improve Performance** by minimizing resource overhead through architectural
optimization
- **Enable Flexibility** in metrics export formats (Prometheus, OpenTelemetry,
custom)
- **Support Independent Lifecycle Management** for monitoring stack updates
separate from KubeVirt releases
- **Maintain Backward Compatibility** by keeping embedded monitoring in older
versions while migrating to external approach in new versions

## Non Goals

- Changing existing metrics schemas or breaking Prometheus compatibility
- Implementing new metrics not currently exposed by KubeVirt
- Replacing KubeVirt's internal monitoring for health checks
- **Breaking compatibility** with existing monitoring setups during migration
- **Forced immediate migration** - older versions maintain embedded approach

## Definition of Users

- **KubeVirt Operators**: System administrators managing KubeVirt clusters who
need comprehensive monitoring and observability
- **Platform Engineers**: Infrastructure teams responsible for monitoring stack
configuration and maintenance
- **Application Developers**: Users running VMs who need visibility into VM
performance and resource usage
- **Monitoring Tool Vendors**: Third-party monitoring solutions that integrate
with KubeVirt metrics
- **KubeVirt Contributors**: Developers working on KubeVirt who need decoupled
monitoring for easier development and testing

## User Stories

- As a **KubeVirt operator**, I want to upgrade my monitoring stack
independently from KubeVirt core components, so I can get new monitoring
features without waiting for KubeVirt releases
- As a **platform engineer**, I want to minimize resource overhead of
monitoring, so I can run more VMs per node with the same hardware, and I want
to be able to disable monitoring
- As an **application developer**, I want consistent VM performance metrics
regardless of KubeVirt version, so my monitoring dashboards work across upgrades
- As a **KubeVirt operator**, I want to backport critical metrics to older
KubeVirt versions, so I don't have to upgrade production clusters just for
monitoring improvements

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt): Core KubeVirt
modifications for hybrid architecture support

- [kubevirt/kubevirt-metrics-exporter](https://github.com/kubevirt/kubevirt-metrics-exporter):
New repository for the kubevirt-metrics-exporter component

## Design

### Architecture Overview

The kubevirt-metrics-exporter component implements a hybrid architecture that
decouples metrics collection from core KubeVirt components while maintaining
compatibility with existing monitoring infrastructure.

#### Component Architecture

```mermaid
┌─────────────────────────────────────────────────────────────────────┐
│                    Prometheus / Monitoring Stack                    │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ (scrape /metrics)
                                 │
┌────────────────────────────────▼────────────────────────────────────┐
│                    kubevirt-metrics-exporter                        │
│  ┌──────────────────────────┐  ┌─────────────────────────────────┐  │
│  │  Cluster State Metrics   │  │   Runtime Metrics Collector     │  │
│  │  - Informers (VMI, VM,   │  │   - Query virt-handler          │  │
│  │    VirtualMachinePool,   │  │   - Parse libvirt responses     │  │
│  │    etc)                  │  │   - Generate runtime metrics    │  │
│  │  - Generate state metrics│  │                                 │  │
│  └──────────────────────────┘  └────────────┬────────────────────┘  │
└─────────────────────────────────────────────┼───────────────────────┘
                                              │ (monitoring queries)
                                              │
┌─────────────────────────────────────────────▼───────────────────────┐
│                         virt-handler (per node)                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  New: Monitoring Query Endpoint (/monitoring/query)          │   │
│  │  - Validates allowed read-only commands (allowlist)          │   │
│  │  - Supported: guestinfo, domstats, guestfsinfo, etc          │   │
│  │  - Rejects write operations or dangerous commands            │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
└─────────────────────────────────┼───────────────────────────────────┘
                                  │ (gRPC)
                                  │
┌─────────────────────────────────▼────────────────────────────────────┐
│                     virt-launcher (per VMI)                          │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  Existing gRPC Interface - Extended for Monitoring           │    │
│  │  - Executes validated libvirt commands                       │    │
│  │  - Returns raw XML/JSON output                               │    │
│  │  - No parsing or metric generation                           │    │
│  └──────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

#### Data Flow

1. Cluster State Metrics (Replaces virt-controller metrics)

    ```mermaid
    Kubernetes API → kubevirt-metrics-exporter Informers → Metrics Generation
                       (VMI, VM, VMPool resources)         (state, phase, etc)
    ```

     - kubevirt-metrics-exporter runs informers for Kubernetes resources (VMI, VM, VirtualMachinePool, etc.)
     - Generates metrics based on resource `spec` and `status` fields
     - Replaces metrics currently generated by virt-controller
     - Examples: `kubevirt_vm_resource_requests`, `kubevirt_vm_resource_limits`, `kubevirt_vm_info`, `kubevirt_vm_labels`

2. Runtime Metrics (Replaces virt-handler metrics)

    ```mermaid
    kubevirt-metrics-exporter → virt-handler → virt-launcher → libvirt
           (query)               (validate)     (execute)      (raw data)
              ↓                      ↓              ↓              ↓
        Parse & Generate      ←   Forward    ←   Forward     ←  Raw Output
          (metrics)              (raw data)     (raw data)      (XML/JSON)
    ```

     - kubevirt-metrics-exporter sends monitoring queries to virt-handler's new `/monitoring/query` endpoint
     - virt-handler validates the command against an allowlist (read-only operations only)
     - Allowed commands: `guestinfo`, `domstats`, `guestfsinfo`, `domblkinfo`, etc.
     - Rejected commands: any write operations, domain lifecycle commands, destructive operations
     - virt-handler forwards validated requests to virt-launcher via gRPC
     - virt-launcher executes the libvirt command and returns raw output (XML/JSON)
     - virt-handler forwards raw output back to kubevirt-metrics-exporter
     - kubevirt-metrics-exporter parses the raw output and generates Prometheus metrics
     - Examples: `kubevirt_vmi_guest_load_1m`, `kubevirt_vmi_cpu_usage_seconds_total`, `kubevirt_vmi_memory_usable_bytes`

#### Security Model

virt-handler Monitoring Query Allowlist

The virt-handler endpoint implements strict command validation:

- **Allowed**: Read-only monitoring commands
  - `domstats` - Domain statistics
  - `guestinfo` - Guest agent information
  - `guestfsinfo` - Guest filesystem information
  - etc.
  
- **Rejected**: All other commands including
  - Domain lifecycle operations (start, stop, destroy)
  - Configuration changes
  - Migration commands
  - Snapshot operations
  - Any write operations

Authentication & Authorization

- kubevirt-metrics-exporter uses ServiceAccount with RBAC permissions
- virt-handler endpoint validates requests using mutual TLS or token
authentication
- Only authenticated kubevirt-metrics-exporter instances can query the endpoint

#### Component Responsibilities

kubevirt-metrics-exporter

- Runs as a Deployment (typically 1-2 replicas for HA)
- Maintains Kubernetes informers for VM-related resources
- Queries virt-handler instances for runtime metrics
- Parses raw libvirt output into Prometheus metrics
- Exposes unified `/metrics` endpoint for Prometheus scraping
- Independent lifecycle from KubeVirt core components

virt-handler

- Adds new `/monitoring/query` HTTP endpoint
- Implements command allowlist validation
- Proxies validated queries to virt-launcher via gRPC
- Returns raw responses without processing
- Minimal additional resource overhead

virt-launcher

- Extends existing gRPC interface for monitoring queries
- Executes validated libvirt commands
- Returns raw XML/JSON output
- No metric generation logic

virt-controller

- Existing metrics marked as deprecated
- Metrics remain available during migration period
- Eventually removed in future release after migration complete

#### Migration Strategy

1. **Phase 1**: Deploy kubevirt-metrics-exporter alongside existing metrics
2. **Phase 2**: Both systems run in parallel (validation period)
3. **Phase 3**: Deprecate virt-controller and virt-handler embedded metrics
4. **Phase 4**: Remove deprecated metrics in future major release

#### Backward Compatibility

- Existing metric names and labels preserved
- Monitoring dashboards continue to work without changes
- Users can opt-in to kubevirt-metrics-exporter while retaining old metrics
- Gradual migration path with clear deprecation timeline

## API Examples

### virt-handler Monitoring Query Endpoint

HTTP Route

```go
ws.Route(ws.POST("/v1/namespaces/{namespace}/virtualmachineinstances/{name}/monitoring/query").To(monitoringHandler.QueryHandler))
```

Path Parameters:

- `namespace`: VMI namespace
- `name`: VMI name

Request Body:

```json
{
  "command": "domstats",
  "parameters": ["--state", "--cpu-total"]
}
```

- `command` (required): Monitoring command to execute (e.g., `domstats`, `guestinfo`, `guestfsinfo`)
- `parameters` (optional): List of command-specific parameters

Response Body:

Success (200 OK):

```json
{
  "raw_output": "{\"state.state\": \"1\", \"state.reason\": \"1\", \"cpu.time\": 316700000000, ...}"
}
```

Error (400/403/404/500):

```json
{
  "error": "command not in allowlist"
}
```

## Alternatives

### Direct libvirt Access via virt-launcher Sidecar

**Approach:** Deploy a sidecar container in the virt-launcher pod with direct
access to libvirt socket, allowing kubevirt-metrics-exporter to query libvirt
directly without going through virt-handler.

**Why this was rejected:**

1. **API Stability Concerns**
   - libvirt is an internal implementation detail of virt-launcher, not a stable KubeVirt API
   - No guarantees around libvirt's stability as KubeVirt evolves
   - With KubeVirt exploring multiple VMMs (see VEP 97: Introduce abstraction to
   enable alternative hypervisors), this approach risks breaking monitoring in the future
   - virt-launcher should self-report its stats to maintain proper encapsulation

2. **Security Risks**
   - virt-launcher is designed as an untrusted component with strict isolation
   - Granting a sidecar direct libvirt access expands the attack surface, even if read-only
   - Violates the security boundary design of virt-launcher

3. **Resource Contention**
   - libvirt operations can be blocking and resource-intensive
   - libvirtd runs in virt-launcher's compute container
   - External calls from a sidecar could interfere with core VMI operations
   - Could lead to unexpected throttling or OOM issues
   - Resource consumption would be counted toward virt-launcher limits, not the sidecar limits
   - Risk of impacting critical VM operations due to monitoring overhead

## Scalability

The hybrid architecture addresses the scalability concerns identified in the
Goals section by decoupling metrics collection to a separate component.

By removing continuous metrics collection from virt-handler and virt-controller,
the design reduces baseline resource consumption on critical components. The
virt-handler monitoring endpoint will perform minimal work, only validating the
incoming requests and passthroughing them to virt-launcher, avoiding
resource-intensive data processing.

Decoupling metrics collection enables independent scaling decisions. The new
component can more easily be configured for different scrape intervals,
selective monitoring for different VMI tiers, or completely disable workload
monitoring in resource-constrained environments, without impacting KubeVirt core
components or requiring their reconfiguration.

## Update/Rollback Compatibility

kubevirt-metrics-exporter is designed as a separate, optional component that is not
included in core KubeVirt installations. This architectural decision provides
clear separation of concerns and simplifies compatibility.

### Upgrade Scenarios

Upgrading KubeVirt without monitoring:

- Core KubeVirt upgrade proceeds normally
- No metrics available (expected behavior)
- No action required

Upgrading KubeVirt with monitoring desired:

- Install kubevirt-metrics-exporter separately before or after core upgrade
- kubevirt-metrics-exporter requires KubeVirt version with `/monitoring/query` endpoint
- kubevirt-metrics-exporter can be updated without updating KubeVirt

### Rollback Scenarios

Rolling back core KubeVirt:

- kubevirt-metrics-exporter is independent, so no action required
- If rolling back to version without `/monitoring/query` endpoint:
  - virt-controller/virt-handler metrics endpoint will have VM/VMI metrics again
  - kubevirt-metrics-exporter can remain installed (but no metrics will be collected), or be uninstalled

## Functional Testing Approach

### Unit Tests

- Verify that virt-launcher's gRPC interface is able to execute libvirt commands and return the raw output.
- Verify that virt-handler's `/monitoring/query` endpoint is able to validate the commands and return the raw output.
- Verify that kubevirt-metrics-exporter is able to parse the raw output and generate the metrics.

### Integration Tests

- Verify that kubevirt-metrics-exporter is able to export the metrics in its own `/metrics` endpoint.
- Verify that kubevirt-metrics-exporter is able to query the virt-handler `/monitoring/query` endpoint and return the metrics.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha

- Implementation of virt-handler `/monitoring/query` endpoint with command allowlist validation
- Extended virt-launcher gRPC interface for monitoring queries
- Basic kubevirt-metrics-exporter deployment collecting cluster state and runtime metrics
- Unit tests for command allowlist validation and metrics parsing
- Integration tests for end-to-end metric collection flow
- Security review of virt-handler command allowlist and authentication mechanism

### Beta

- kubevirt-metrics-exporter generates all metrics previously provided by virt-controller and virt-handler
- Migration documentation
- Deprecation notices added to virt-controller and virt-handler embedded metrics
- Scalability testing with large number of VMs showing acceptable performance

### GA

- Deprecated embedded metrics removed from virt-controller and virt-handler
- Documentation updated to use kubevirt-metrics-exporter as primary approach
