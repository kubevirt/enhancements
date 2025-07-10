# VEP 67: Collect Guest CPU-Load Metrics from libvirt (QEMU Guest Agent)

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Expose Linux guest load-average metrics (1 m / 5 m / 15 m) now available via `VIR_DOMAIN_GUEST_INFO_LOAD` in libvirt v1.11004.0. KubeVirt will poll the QEMU Guest Agent every 120 seconds, publish three new Prometheus gauges, add recording rules for guest run-queue length, and ship four new alerts.

## Motivation

Operators can currently observe only host-side CPU-time counters. They cannot diagnose whether latency stems from guest CPU contention or host scheduling pressure. Surfacing guest load closes this visibility gap and enables precise, actionable alerts.

## Goals

- Poll `guest-get-load` via libvirt guest-info every 120 s.
- Export `kubevirt_vmi_guest_load_1m`, `…_5m`, `…_15m` gauges.
- Provide recording rules for guest run-queue.
- Add warning/critical alerts for queue > 10 / > 20.

## Non Goals

- Automated vCPU hot-plug, live-migration, or eviction logic.
- Per-process load collection inside the guest.
- libvirt API changes (keys already merged upstream).

## Definition of Users

- SREs and virtualization administrators operating KubeVirt clusters.
- Application teams that need guest-side CPU-pressure alerts.

## User Stories

1. *Latency triage*: Operator sees high guest load but low host CPU usage → adds vCPUs instead of chasing node contention.
2. *Queue paging*: Critical workload pages only when guest run-queue > 20 for any 120 s window, reducing alert noise.

## Repos

- **kubevirt/kubevirt** – virt-handler polling & metric exposition & Prometheus rules
- **kubevirt/monitoring** – alerts runbooks
- **kubevirt/enhancements** – this VEP document

## Design

### Data flow

```
guest OS → qemu-guest-agent → libvirtd → virt-handler → /metrics → Prometheus
```

### Metrics

| Name                          | Type  | Unit             |
| ----------------------------- | ----- | ---------------- |
| `kubevirt_vmi_guest_load_1m`  | Gauge | runnable threads |
| `kubevirt_vmi_guest_load_5m`  | Gauge | runnable threads |
| `kubevirt_vmi_guest_load_15m` | Gauge | runnable threads |

**Sampling cadence:** 120 s (same as existing guest-info “sys” metrics).

### Recording rules (examples)

```promql
# vCPU count (now keeps `node` label)
kubevirt_vmi_vcpu_count =
  count by (namespace, name, node) (kubevirt_vmi_vcpu_seconds_total)

# Guest run-queue length
kubevirt_vmi_guest_vcpu_queue =
  clamp_min(kubevirt_vmi_guest_load_1m - kubevirt_vmi_vcpu_count, 0)
```

### Alerts

| Alert                          | Expression       | For | Severity |
| ------------------------------ | ---------------- | --- | -------- |
| `GuestVCPUQueueHighWarning`    | `queue > 10`     | –   | warning  |
| `GuestVCPUQueueHighCritical`   | `queue > 20`     | –   | critical |

Runbooks are added under `docs/runbooks/`.

## API Examples

No external API changes. Only new Prometheus metrics such as:

```text
# HELP kubevirt_vmi_guest_load_1m Guest CPU load average over 1 minute
kubevirt_vmi_guest_load_1m{namespace="demo",name="web",node="worker-1"} 2.37
```

## Alternatives

- Poll every 30 s for faster detection (adds marginal GA overhead).

## Scalability

Three extra gauges per VMI ≈ 250 k samples/min for 10 k VMs – within Prometheus capacity.

## Update/Rollback Compatibility

If the guest agent is absent, metrics disappear.

## Functional Testing Approach

- *promtool* unit tests for recording rules and all alerts.
- End-to-end test: launch VM with `stress-ng --cpu 4`, verify load metrics increase and alerts fire.

## Implementation Phases

1. virt-handler polling, new gauges, Prometheus rules.
2. enable rules by default and add Grafana dashboards.
3. evaluate auto-scaling or migration integration (separate VEP).

## Feature lifecycle Phases

### Alpha

- Metrics and alerts enabled by default.
- Runbooks merged.

### Beta

- Do performance & scale tests to lower the collection interval from the guest agent to 30secs for earlier issue detection.


### GA

- Metric names & labels frozen.
- Alerts included in default KubeVirt monitoring stack.
