# VEP #143: Monitoring Stack Separation via Hybrid Architecture

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: v1.11
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal introduces a new `kubevirt-observability-controller` component
that consolidates monitoring concerns currently scattered across
`virt-controller`, `virt-handler`, and `virt-launcher`. The controller takes
ownership of cluster-state metrics generation, recording rules/alerts
management, and exposes a clear separation boundary between the KubeVirt control
plane and its monitoring stack.

To achieve this goal, this proposal refactors the gRPC interface between
`virt-handler` and `virt-launcher` to consolidate the many separate monitoring
RPCs into a unified, more flexible `GetVMStats` call, significantly
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

Separating monitoring into a dedicated component in a separete repository with a
unified gRPC interface addresses all of these concerns, simplifying the overall
approach, giving more flexibility to evolve what data is collected and how it is
parsed, and preserving backward compatibility with existing deployments.

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
- Consolidate the many existing monitoring gRPC RPCs into a unified `GetVMStats` RPC
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
- kubevirt/kubevirt-observability-controller

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

The controller collects runtime data (libvirt domain stats, guest agent info)
by calling the `GetVMStats` gRPC directly on each `virt-handler` pod,
bypassing `virt-api`. This mirrors how Prometheus already reaches
`virt-handler` today via the headless `kubevirt-prometheus-metrics` Service and
avoids introducing `virt-api` as a data-forwarding middleman in the
observability path.

*Discovery:* The controller uses VMI informers to determine which nodes
currently host VMIs. For each active node it resolves the corresponding
`virt-handler` pod endpoint through the headless Service and issues a
`GetVMStats` call. Nodes with no running VMIs are skipped entirely.

*Per-VMI requests:* The `GetVMStats` call lets the controller filter which VMIs
to collect from and specify the stats categories it needs on a per-VMI basis,
rather than one set applied to every VMI on the node, so it can collect different
data for different VMIs in a single call.

*Data aggregation:* Each `virt-handler` aggregates the monitoring data from
its local `virt-launcher` pods over the existing Unix-socket gRPC channel
before responding, so the controller receives one consolidated response per
node rather than per-VMI.

*Authentication:* `virt-operator` issues a dedicated client certificate for
the observability controller, signed by the KubeVirt internal CA. The
controller presents this certificate when connecting to `virt-handler`,
which already validates client certificates on its gRPC endpoint. This
keeps the controller external to the core KubeVirt installation while
reusing the existing infrastructure. The observability controller requires RBAC
permissions to read Secrets in the KubeVirt namespace so it can retrieve the
issued certificate and CA bundle. Downstream vendors may choose whether to grant
these permissions.

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

A new `GetVMStats` RPC consolidates monitoring queries into fewer, more flexible
calls. The caller specifies which data categories it needs via per-category
request messages. The server only serializes and returns the requested fields
from the existing caches. This drastically simplifies the approach, as new
libvirt or guest agent fields can be appended to the existing messages without
introducing new RPCs, scrapers, or parsing pipelines.

**Key properties:**

- **Selective serialization**: Only requested fields are populated in the response, avoiding
  unnecessary serialization overhead.
- **Simplified data pipeline**: One unified path for collecting, serializing, and parsing
  monitoring data replaces the per-RPC scraper model. Adding a new data source is a matter
  of appending a field to the proto messages rather than building an end-to-end pipeline.
- **Backward compatible**: Existing RPCs remain in the proto definition and continue to work.
  Callers can be migrated incrementally.
- **Extensible**: New libvirt domain fields and guest agent data can be appended to both
  `VMStatsRequest` and `VMStatsResponse` without breaking the wire format or existing
  callers.

#### Proto Changes

In `pkg/handler-launcher-com/cmd/v1/cmd.proto`:

```protobuf
service Cmd {
  // ... existing RPCs stay for backward compat ...
  rpc GetVMStats(VMStatsRequest) returns (VMStatsResponse) {}
}

// Per-category request messages. Initially empty but can be extended later
// to carry filters, pagination, or specific options without changing the
// VMStatsRequest format.
message DomainStatsRequest      {}
message GuestInfoRequest        {}
message GuestFilesystemsRequest {}
message GuestAgentRequest       {}
message GuestUsersRequest       {}
message GuestGetDevicesRequest  {}

message VMStatsRequest {
  DomainStatsRequest      domainStats      = 1;
  GuestInfoRequest        guestInfo        = 2;
  GuestFilesystemsRequest guestFilesystems = 3;
  GuestAgentRequest       guestAgent       = 4;
  GuestUsersRequest       guestUsers       = 5;
  GuestGetDevicesRequest  guestGetDevices  = 6;
  ...
}

message VMStatsResponse {
  Response response         = 1;
  string   domainStats      = 2;
  string   guestInfo        = 3;
  string   guestFilesystems = 4;
  string   guestAgent       = 5;
  string   guestUsers       = 6;
  string   guestGetDevices  = 7;
  ...
}
```

All response fields are JSON-encoded strings (consistent with the current
approach).

#### Guest Agent Categories

This VEP is the source of truth for the categories `GetVMStats` supports. Categories are added by
appending a request and a response field, as above; the collection, caching, aggregation, transport
and scrape paths are unchanged by such an addition.

`guestGetDevices` carries the output of the `guest-get-devices` agent command: driver name, version,
date and PCI device/vendor ID per device. It is Windows-only, and its payload is cached with a five
minute time-to-live, driver information being mostly static for the life of a VM. It is added for
[VEP #385](../385-guest-device-info/vep.md), which describes the metric built on it.

An agent command the guest does not implement - `guest-get-devices` on a Linux guest, for example -
must yield an empty payload and a successful response, cached for the normal time-to-live. It must
not mark the VMI's stats response unsuccessful, and it must not be re-executed on every scrape.

### Guest Agent Command Cost and Safety

#### Call rate is bounded by the cache, not by the scrape interval

`virt-launcher` holds one time-defined cache per agent command. The caches are lazy: no background
goroutine refreshes them, so a command that no caller requests is never executed, and a command that
every caller requests still reaches the guest agent at most once per time-to-live per VMI. A client
polling `GetVMStats` every second for `guestGetMemoryBlocks` triggers one agent call every 30
minutes.

This bound is a property of `virt-launcher`, not of the caller. It holds for any client holding a
valid certificate for the `virt-handler` stats endpoint, including clients that are not the
`kubevirt-observability-controller`. That is the primary safety argument for this path, and it is
stronger than any per-command measurement, because it does not assume the caller behaves well.

#### Per-command justification

Categories are plumbed in `virt-launcher` but only executed when a caller requests them. The
`kubevirt-observability-controller` resolves its request set per VMI from the enabled metric
allowlist, the guest OS family and the reported agent version, so a command is only issued when a
metric that consumes it is enabled and the guest can answer it.

Every category below carries monitoring value. Those with a metric consumer are already in use,
in either the `kubevirt-observability-controller` or core KubeVirt; those listing none consume no
resources by being present, because the caches are lazy, so a category nobody requests is never
executed and costs nothing beyond a map entry. They are listed so a future VEP proposing a metric
has a starting point rather than re-deriving it.

| Agent command | Agent since | Platforms | Cache | Metric consumer | Monitoring value |
|---|---|---|---|---|---|
| `guest-get-fsinfo` | 2.2 | Windows, Linux, FreeBSD | 5 min | `kubevirt_vmi_filesystem_capacity_bytes`, `kubevirt_vmi_filesystem_used_bytes` | Guest filesystem fill level is invisible from the host, which only sees block device usage. The sole source for alerting before a guest runs out of disk, and already shipped in `virt-handler` behind an existing alert. |
| `guest-get-devices` | 5.2 | Windows | 5 min | `kubevirt_vmi_guest_device_driver_timestamp_seconds` | VirtIO driver name, version and date per device, so operators can find Windows guests on outdated drivers. Added by [VEP #385](../385-guest-device-info/vep.md) and consumed by its alert. No libvirt equivalent exists. |
| `guest-get-osinfo` | 2.10 | all | 5 min | `kubevirt_vmi_guest_os_info` | Guest OS, kernel release and distribution, so fleet dashboards can group VMs by OS and find end-of-life or unpatched images. |
| `guest-network-get-interfaces` | 1.1 | Windows, Linux | 5 min | `kubevirt_vmi_guest_interface_info` | Maps guest-side interface names, IP and hardware addresses to the VMI, which is what lets a network alert resolve to an address an operator can act on. |
| `guest-get-host-name` | 2.10 | all | 5 min | `kubevirt_vmi_guest_hostname` | The name the guest believes it has, which is how in-guest agents and logs identify themselves and therefore the join key back to a VMI. |
| `guest-get-timezone` | 2.10 | all | 5 min | `kubevirt_vmi_guest_timezone` | A wrong or drifting guest timezone silently misaligns in-guest timestamps against cluster metrics, a common and hard-to-diagnose cause of correlation gaps. |
| `guest-get-load` | 10.0 | Windows, Linux | 20 s | `kubevirt_vmi_guest_load_1m`, `kubevirt_vmi_guest_load_5m`, `kubevirt_vmi_guest_load_15m` | Guest load average, separating a VM that is busy from one merely allocated. Already a shipped core metric, collected today by the existing poller rather than through `GetVMStats`, so it must migrate rather than be justified afresh. |
| `guest-get-users` | 2.10 | Windows, Linux | 1 min | `kubevirt_vmi_guest_user_count` | Active login sessions separate an idle VM from one in use, supporting right-sizing and unexpected-access alerting. |
| `guest-get-time` | 1.5 | all | 1 min | none | Guest clock offset against the host, which detects time drift before it breaks certificates, authentication and log correlation. |
| `guest-get-vcpus` | 1.5 | Windows, Linux | 1 min | none | Which vCPUs the guest has brought online, confirming whether a CPU hotplug was actually taken up. |
| `guest-get-cpustats` | 7.1 | Linux | 20 s | none | Per-CPU user, system, idle and I/O wait time inside the guest, distinguishing guest CPU pressure from host steal time. |
| `guest-get-diskstats` | 7.1 | Linux | 20 s | none | Guest-side I/O counts and service times, separating guest I/O behaviour from host block device throughput. |
| `guest-get-disks` | 5.2 | Windows, Linux | 5 min | none | Physical disks and partitions with SMART data, surfacing failing storage as the guest sees it. |
| `guest-network-get-route` | 9.1 Linux, 11.0 Windows | Windows, Linux | 5 min | none | Guest routing table, explaining guest-side connectivity failures that look like network problems from outside. |
| `guest-get-memory-block-info` | 2.3 | Linux | 1 min | none | Guest memory block size, a single small value. The cheap companion needed to interpret `guest-get-memory-blocks`. |
| `guest-get-memory-blocks` | 2.3 | Linux | 30 min | none | Online state of each hotpluggable memory block, confirming whether a memory hotplug was onlined by the guest. The only command whose cost scales with guest size; see the note below. |

Agent version and platform availability are taken from the
[QEMU Guest Agent Protocol Reference](https://qemu-project.gitlab.io/qemu/interop/qemu-ga-ref.html).
The version column matters for more than documentation: the controller's minimum of 5.2 for
`guest-get-devices` matches the reference exactly, and the same gate should apply to any future
category introduced recently. `guest-get-load` is the clearest example, introduced in agent 10.0.

#### How the controller narrows the request set

The table above is the widest set any VMI can produce. The
`kubevirt-observability-controller` narrows it per VMI through five independent gates, each of
which can only remove categories. A category survives to the wire only if every gate allows it.

1. **Collection is opt-in.** The `--enable-vmstats` flag defaults to off. With it off the
   controller never contacts the stats endpoint.
2. **The metric allowlist.** A category is allowed only if at least one metric that consumes it is
   enabled. If the allowlist keeps no stats metric at all, the poller is never started, so an
   operator can disable guest agent collection entirely by metric name without disabling the
   controller.
3. **Guest agent presence.** If the VMI does not report `status.guestOSInfo.id`, no guest category
   is requested and only the libvirt categories are. That field is populated by KubeVirt's own
   libvirt guest info poller, so the controller issues no agent command until KubeVirt has already
   confirmed a working agent. A VM with no guest agent installed therefore never receives one.
4. **Guest OS family.** Beyond the four categories common to all guests, `guest-get-users` is
   requested only for Windows guests and `guest-network-get-interfaces` only for Linux guests. The
   family is read from the same reported OS id, so a command is not sent to a guest whose OS cannot
   answer it.
5. **Guest agent version.** `guest-get-devices` is requested only when the reported agent version
   is 5.2 or newer, parsed from the version string the stats response carries. When the version is
   not yet known the controller requests a category that will cause the next response to report it,
   rather than probing with the command itself. An agent older than 5.2 is never asked.

Collection runs on a 30 second interval, at most 10 nodes concurrently, with a 30 second timeout
per node. Requests for all VMIs on a node are batched into one call to that node's `virt-handler`.
Those three numbers interact, and the interaction is the controller's own scalability limit. A
poll cycle visits every node holding a VMI, ten at a time, and the next tick does not start until
the current cycle finishes.

#### Overlap with the existing agent poller

`virt-launcher` already runs an agent poller for every VMI with a connected agent, independently
of this feature and of whether monitoring is installed, because it populates VMI status and the
guest agent subresources. Seven of the eight categories in use overlap with what it already
collects, and the two paths each issue their own agent commands:

| Agent command | Already collected by the poller as | Poller interval |
|---|---|---|
| `guest-get-users` | `virDomainGetGuestInfo(USERS)` | 10 s |
| `guest-get-osinfo`, `guest-get-host-name`, `guest-get-timezone`, `guest-network-get-interfaces` | `virDomainGetGuestInfo(INTERFACES, OS, HOSTNAME, TIMEZONE)` | 120 s |
| `guest-get-load` | `virDomainGetGuestInfo(LOAD)` | 120 s |
| `guest-get-fsinfo` | `guest-get-fsinfo`, issued directly | 300 s |

The incremental cost is therefore small, and for `guest-get-users` the new path is an order of
magnitude slower than the poller it duplicates. For beta the duplication is accepted rather than
removed, since resolving it means refactoring a path that feeds core VMI status.

For GA the poller should be replaced by this mechanism rather than run alongside it. Its consumers
can read the agent data caches directly in-process, or go through the `GetVMStats` endpoint,
leaving one agent collection path in `virt-launcher`. The next section covers what that requires.

#### After beta: replace the agent poller with this mechanism

The intended end state is a single agent collection path in `virt-launcher`, and the mechanism
introduced by this VEP is the one that should survive. The existing agent poller is then removed
and its consumers move onto the agent data caches, either by reading them directly in-process,
which suits the status path, or through the `GetVMStats` endpoint, which suits anything already
remote.

The new mechanism is the better base for three reasons. It caches raw agent payloads rather than
the fixed set of fields libvirt's guest info call models. It carries a per-command time-to-live
rather than one interval per worker group, so each command can be refreshed at a rate that matches
how fast its data actually changes. And adding a command becomes a table entry rather than a new
poller worker and a new conversion function.

This is deliberately not beta work. It changes a path that populates `vmi.status.guestOSInfo`,
`vmi.status.interfaces` and `vmi.status.fsFreezeStatus`, so it carries regression risk for
networking, snapshot and backup flows that a monitoring graduation should not take on. The
following must be settled first:

- **The caches must become unconditional.** They are created only when the `VMStatsCollector`
  feature gate is enabled. Core VMI status cannot depend on a monitoring feature gate, so the
  caches must exist for every VMI before anything in the status path reads them.
- **Categories that drive status need background refresh.** The agent data caches are lazy and
  refresh only when read. Status updates are push-driven today, so any category feeding status
  needs its cache kept warm rather than waiting for a scrape.

#### Measurements

Measured on the code at main, with the feature gate enabled, on a
single worker of a three-master two-worker cluster.

| | |
|---|---|
| KubeVirt | `e5be198b0b` |
| Kubernetes | v1.35.8 |
| Node | GCP `n2-standard-4`, 4 vCPU, 16GiB, hardware virtualisation available |
| Guest | Fedora, 1GiB, QEMU guest agent connected |

Timings are `virt-launcher`'s own, logged per command at verbosity 2, so they exclude
client and transport overhead.

**Per-command cost.** Each category was requested on its own, cold once and then ten times
in a row.

| Agent command | Cold ms | Warm median ms | Payload bytes |
|---|---|---|---|
| `guest-get-osinfo` | 4.527 | 0.002 | 325 |
| `guest-get-host-name` | 4.167 | 0.002 | 38 |
| `guest-get-timezone` | 4.528 | 0.002 | 36 |
| `guest-get-fsinfo` | 4.977 | 0.002 | 554 |
| `guest-network-get-interfaces` | 4.828 | 0.002 | 686 |
| `guest-get-users` | 4.583 | 0.002 | 13 |
| `guest-get-devices` | 4.683 | **4.104** | 0 (expected fail) |
| `domainStats` | 13.254 | 0.130 | 12 |
| `dirtyRate` | 1205.319 | 0.055 | 0 |

Every agent command costs roughly 4 to 5 milliseconds when it reaches the guest and
2 microseconds when served from cache, a factor of about two thousand.

**The rate bound holds.** Thirty requests were issued over thirty seconds for each category,
well inside every window. Agent round trips were counted by duration, since a cache hit
returns in microseconds.

| Category | Window | Requests | Agent round trips |
|---|---|---|---|
| `guest-get-osinfo` | 5 min | 30 | 0 |
| `guest-get-fsinfo` | 5 min | 30 | 0 |
| `guest-get-users` | 1 min | 30 | 0 |
| `guest-network-get-interfaces` | 5 min | 30 | 0 |

This is the central safety claim of the design and it is confirmed. Request rate does not
reach the guest.

**Failures are not absorbed, and are not subject to the cache window.** Eight requests for
`guest-get-devices` against a Linux guest produced eight agent round trips, where one is expected.

The error returned is `virError(Code=113, ...)`, which is `VIR_ERR_AGENT_COMMAND_FAILED`,
carrying the agent's own text "The command guest-get-devices has not been found". The
implementation tests for `VIR_ERR_ARGUMENT_UNSUPPORTED`, `VIR_ERR_OPERATION_UNSUPPORTED` and
`VIR_ERR_NO_SUPPORT`, which are codes 74, 84 and 3. The code it actually receives is 113, so
the check can never match and the empty-success path is unreachable.

[kubevirt/kubevirt#19632](https://github.com/kubevirt/kubevirt/pull/19632) fixes this by treating
`VIR_ERR_AGENT_COMMAND_FAILED` as a rejection the cache should absorb, so a command the guest
rejects records an empty result and is not re-issued until its window elapses. That closes the
case measured here. It does not change the behaviour for errors the guest agent did not produce,
such as `VIR_ERR_AGENT_UNRESPONSIVE` below, which are still surfaced and still leave the cache
unstamped.

**A missing guest agent is cheap.** When the
agent channel is not connected libvirt does not wait out its five second timeout. It returns
immediately with `VIR_ERR_AGENT_UNRESPONSIVE`, code 86, "QEMU guest agent is not connected".

| Case | Per command | Whole request, 7 categories |
|---|---|---|
| Healthy guest, warm cache | 0.002 ms | 4.4 ms |
| No guest agent at all | 0.37 to 0.70 ms | 3.4 to 6.0 ms |

The five second libvirt timeout therefore applies only when an agent is connected but fails to
answer, not when it is absent. Each of the seven commands still fails against an agentless guest,
so the response carries seven errors, but the request completes in milliseconds.

### 3. Recording Rules and Alerts Migration

Recording rules and alert definitions are currently managed by `virt-operator` and
`virt-controller`. The migration proceeds as follows:

1. **Extract**: All `PrometheusRule` move from `virt-operator` to the
   `kubevirt-observability-controller` codebase.
2. **Reconcile**: The new controller reconciles the `PrometheusRule` CRs, ensuring they match
   the desired state on every sync loop.

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
  `GetVMStats` returns correct data for various flag combinations, and that the
  fallback to legacy RPCs works when the new RPC is unimplemented.
- **E2E tests**:
  - Deploy a cluster with the new controller and verify metrics appear in Prometheus.
  - Verify that `PrometheusRule` CRs are created, updated on config change, and removed on
    uninstall.
  - Perform a rolling upgrade and verify no metric gaps during the transition.
  - Verify gRPC fallback behavior during mixed-version rolling upgrades.

## Implementation Phases

1. **Proto changes**: Add `GetVMStats` RPC, `VMStatsRequest`, and
   `VMStatsResponse` messages. Regenerate Go code.
2. **virt-launcher server**: Implement the `GetVMStats` handler, delegating to existing
   `DomainManager` methods.
3. **virt-handler caller**: Update the domain stats scraper, downward metrics, and REST
   handlers to use the new RPC with version-negotiation fallback.
4. **kubevirt-observability-controller scaffold**: Create the external component
   (new binary, Deployment manifest, RBAC, ServiceAccount, and Service), managed
   outside of `virt-operator`. RBAC must include read access to Secrets in the
   KubeVirt namespace for the client certificate and CA bundle. `virt-operator`
   is extended to issue a dedicated client certificate for the controller,
   signed by the KubeVirt internal CA.
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
- Guest agent collection cost does not scale with the scrape interval. Each command reaches the
  agent at most once per time-to-live per VMI, so the dominant cost is the number of VMIs per node,
  not the polling rate. See
  [Guest Agent Command Cost and Safety](#guest-agent-command-cost-and-safety) for the per-command
  justification, the known gaps in that bound, and the measurements planned before beta.

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

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
- Requires KubeVirt version with the new `GetVMStats` RPC.
- `virt-operator` must have issued the controller's client certificate.
- The controller needs RBAC access to read Secrets in the KubeVirt namespace for
  the client certificate and CA.
- Can be updated on its own release cadence without updating KubeVirt.

### Rollback Scenarios

**Rolling back core KubeVirt:**

- `kubevirt-observability-controller` is external and independently managed, so no action
    required.
- If rolling back to a version without the new `GetVMStats` RPC:
  - `virt-controller`/`virt-handler` metrics endpoints will have VM/VMI metrics again.
  - `kubevirt-observability-controller` can remain installed (but no metrics will be
    collected), or be uninstalled.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- [x] Feature gate `VMStatsCollector` guards KubeVirt-side code changes
  (gRPC refactor in `virt-handler`/`virt-launcher`)
- [x] `GetVMStats` RPC implemented in `virt-launcher` and called by `virt-handler`
  with fallback to legacy RPCs
- [x] Cluster-state metrics emitted by the new controller (subset of metrics migrated)
- [x] Unit and integration test coverage for the new RPC and controller

### Beta

- [ ] All cluster-state metrics migrated from `virt-controller`
- [ ] Recording rules and alert management fully owned by the new controller
- [ ] E2E tests covering upgrade/rollback scenarios and gRPC fallback
- [ ] Legacy individual monitoring RPCs deprecated in documentation
- [x] Guest agent collection: every requested category has a documented metric consumer, and
      categories without one are not requested
- [ ] Guest agent collection: failures respect the per-command window, so no command can be
      re-issued on every scrape
- [ ] Guest agent collection: a bounded overall deadline per `GetVMStats` call, and no collection
      against paused, non-running or migrating VMIs
- [x] Measurements published for every requested category, covering the healthy guest, the
      unresponsive agent, and freeze and thaw contention

### GA

- [ ] Legacy individual monitoring RPCs removed from `virt-handler` caller code
  (proto definitions kept for wire compatibility)
- [ ] Cluster-state metrics removed from `virt-controller` and `PrometheusRule`
  reconciliation removed from `virt-operator` (release N+1);
  `kubevirt-observability-controller` is the sole source for metrics, recording
  rules, and alerts
- [ ] Upgrade/rollback tests pass across two consecutive releases
- [ ] Documentation updated with new architecture diagrams and operator guide
- [ ] The `virt-launcher` agent poller is replaced by the agent data caches, leaving one agent
      collection path, per
      [After beta: replace the agent poller with this mechanism](#after-beta-replace-the-agent-poller-with-this-mechanism)
