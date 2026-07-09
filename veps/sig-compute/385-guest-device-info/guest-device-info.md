# VEP #385: Guest Device Info Metrics

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

Expose device driver information reported by the QEMU guest agent's `guest-get-devices` command
as a Prometheus metric, giving VM owners a fleet-wide overview of deployed VirtIO
driver versions on Windows VMs. Outdated driver versions can cause performance degradation,
compatibility issues, and may lack security fixes. This metric enables detecting such VMs via
PromQL before problems arise.

## Motivation

Windows VMs running on KubeVirt use VirtIO drivers for network, storage, balloon, and other
paravirtualized devices. These drivers require periodic updates for performance, bug fixes, and
security patches. Currently there is no centralized way to determine which driver versions are
installed across a cluster - VM owners must check each VM individually.

The QEMU guest agent already supports a `guest-get-devices` command (since QEMU 5.2) that returns
driver name, version, date, and PCI device IDs. KubeVirt has existing infrastructure to poll the
guest agent and expose results as Prometheus metrics. This VEP bridges the gap by adding
`guest-get-devices` to the metrics pipeline.

## Goals

- Expose per-device driver information (name, version, device ID) as a Prometheus metric with
  the driver date as the metric value, enabling both inventory queries and time-based alerting
- Document example PromQL queries for fleet inventory, version distribution, and age-based alerting

## Non Goals

- Automatically updating drivers inside guest VMs
- Defining what constitutes the "latest" or "correct" driver version
- Shipping built-in alert rules (users write their own with documented PromQL examples)
- Adding device info to VMI status or the Kubernetes API
- Supporting non-PCI device types beyond what `guest-get-devices` reports
- Supporting Linux guests (the command is Windows-only in the QEMU guest agent)

## Definition of Users

- **VM owners** managing Windows VMs with VirtIO drivers who need visibility into driver versions
  for update planning, fleet health monitoring, and compliance
- **Compliance/audit tools** that need to verify driver versions meet organizational policies

## User Stories

- As a VM owner, I want to query Prometheus to see which driver versions are installed on my
  Windows VMs so I can identify VMs that need driver updates.
- As a VM owner, I want to create a Grafana dashboard showing driver version distribution across
  my VM fleet so I can track update rollout progress.
- As a VM owner, I want to write Prometheus alerts that fire when VMs are running drivers older
  than a policy threshold or a specific version known to have bugs, so I can prioritize updates.

## Repos

- https://github.com/kubevirt/kubevirt

## Design

### QEMU Guest Agent Command

The `guest-get-devices` command is a Windows-only QEMU guest agent command available since
QEMU 5.2. It returns VirtIO and QEMU PCI device driver information including name, version,
date, and PCI device/vendor IDs. A typical Windows VM reports 6-10 devices.

Example output:
```json
{
  "return": [
    {
      "driver-date": 1721001600000000000,
      "driver-name": "Red Hat VirtIO Ethernet Adapter",
      "driver-version": "100.95.104.26200",
      "id": {
        "device-id": 4161,
        "vendor-id": 6900,
        "type": "pci"
      }
    }
  ]
}
```

The command is Windows-only. On Linux guests it returns `CommandNotFound`, which the existing
agent command infrastructure handles gracefully. No metric is emitted for VMs without device data.

### Metric Definition

**Name:** `kubevirt_vmi_guest_device_driver_date_seconds`

**Type:** Gauge (using `operatormetrics.NewGauge`; labels set via `CollectorResult` per the domainstats pattern)

**Labels:**

| Label | Example | Source |
|-------|---------|--------|
| `node` | `worker-1` | VMI status (standard) |
| `namespace` | `default` | VMI metadata (standard) |
| `name` | `win-vm-1` | VMI metadata (standard) |
| `driver_name` | `Red Hat VirtIO Ethernet Adapter` | `driver-name` |
| `driver_version` | `100.95.104.26200` | `driver-version` |
| `device_id` | `1041` | `id.device-id` (decimal to hex, no 0x prefix) |

**Value:** Driver date as Unix epoch seconds (converted from the nanosecond timestamp).

Using the driver date as the metric value enables `time() - value` arithmetic in PromQL for
age-based alerting (precedent: `kubevirt_vmi_migration_start_time_seconds`).

Labels omitted by design:
- `vendor_id`: always `1af4` or `1b36` (QEMU hard-codes the filter) - low information density
- `device_type`: always `pci` (the only type QEMU supports) - no information

### Cardinality

The `guest-get-devices` command already filters to VirtIO/QEMU vendor IDs only, so cardinality is
inherently bounded to ~6-10 devices per Windows VM. With 1000 VMs this yields ~6000-10000 time
series - comparable to existing per-item metrics like `kubevirt_vmi_filesystem_capacity_bytes`
(one per filesystem per VM) and `kubevirt_vmi_vnic_info` (one per vNIC per VM).

### Data Path

The command is added to both data pipelines. The domainstats scraper path is gated behind the
`GuestDeviceMetrics` feature gate since it introduces a new Prometheus metric. The VMStatsCollector
path requires only the existing `VMStatsCollector` feature gate - no additional gate is needed
because it simply passes through raw guest agent data, consistent with how other guest agent
commands are handled in that pipeline. Driver info is essentially static during a VM's lifetime,
so infrequent polling is sufficient.

**Domainstats scraper path** (Prometheus metric, gated by `GuestDeviceMetrics`):
```
guest-get-devices (QEMU GA)
  --> virt-launcher agent poller: periodically polls, parses JSON,
      stores in AsyncAgentStore
  --> virt-launcher cmd-server: GetDevices() reads from store
  --> virt-handler domainstats scraper: fetches via gRPC cmd-client
  --> device_metrics.go: emits CollectorResult per device
  --> Prometheus scrape
```

**VMStatsCollector path** (gRPC endpoint, gated by `VMStatsCollector`):
```
guest-get-devices (QEMU GA)
  --> virt-launcher: cached in agentDataCaches with per-command TTL
  --> GetVMStats gRPC: returns raw JSON on demand
```

## API Examples

### Metric Output

```
kubevirt_vmi_guest_device_driver_date_seconds{
  node="worker-1", namespace="default", name="win-vm-1",
  driver_name="Red Hat VirtIO Ethernet Adapter",
  driver_version="100.95.104.26200",
  device_id="1041"
} 1721001600
```

### Example PromQL Queries

List all drivers for a specific VM:
```promql
kubevirt_vmi_guest_device_driver_date_seconds{namespace="default", name="win-vm-1"}
```

Find all VMs with a specific device:
```promql
kubevirt_vmi_guest_device_driver_date_seconds{device_id="1041"}
```

Find all VMs running a specific driver version:
```promql
kubevirt_vmi_guest_device_driver_date_seconds{driver_version="100.95.104.26200"}
```

Find VMs with drivers older than 1 year:
```promql
(time() - kubevirt_vmi_guest_device_driver_date_seconds) > (365 * 24 * 3600)
```

Driver version distribution across cluster:
```promql
count by (driver_name, driver_version) (kubevirt_vmi_guest_device_driver_date_seconds)
```

## Alternatives

### Add Device Info to VMI Status

Device info could be added to `VirtualMachineInstanceStatus` (like `GuestOSInfo` today).
Rejected because it adds API surface and VMI status update overhead. The primary use case
(monitoring/alerting) is better served by Prometheus metrics. VMI status can be reconsidered
in a future VEP if API access is needed.

### Add Labels to Existing `kubevirt_vmi_info`

Driver info could be added as labels on `kubevirt_vmi_info`. Rejected because there are multiple
devices per VM, which would duplicate the entire `kubevirt_vmi_info` series per device and
massively increase cardinality of the central VMI info metric.

### Use Only VMStats REST Endpoint

The metric could be collected only via the VMStats REST endpoint (behind `VMStatsCollector`
feature gate) instead of the domainstats scraper. Viable future option when VMStatsCollector
matures to include its own Prometheus metrics layer. For alpha, the command is added to both
pipelines - the VMStatsCollector path passes through raw data under its own feature gate, while
the Prometheus metric comes from the domainstats scraper under `GuestDeviceMetrics`.

### Info-style Metric with Value = 1

Instead of encoding the driver date as the metric value, the metric could follow the standard
`_info` convention with value always set to 1 and the driver date as a label
(`kubevirt_vmi_guest_device_info{..., driver_date="1721001600"} 1`). More consistent with other
KubeVirt info metrics, but loses `time() - value` arithmetic for age-based alerting.

### Built-in Alert Rule

A built-in alert could detect outdated drivers. Rejected because there is no universal
definition of "outdated" - acceptable driver age varies by organization. Example PromQL queries
are documented so users can write alerts with their own thresholds.

## Scalability

- ~6-10 time series per Windows VM; inherently bounded by QEMU's vendor ID filter
- Infrequent polling interval minimizes guest agent load
- Driver info is static during a VM's lifetime, so stale reads are not a concern

## Update/Rollback Compatibility

- The Prometheus metric path is guarded by the `GuestDeviceMetrics` feature gate;
  the VMStatsCollector path is guarded by the existing `VMStatsCollector` feature gate
- No changes to VMI spec or status - the metric is collected and exposed independently
- No impact on VM runtime behavior

## Functional Testing Approach

- Unit tests for JSON parsing of `guest-get-devices` response
- Unit tests for metric emission with mock device data (following the pattern in
  `filesystem_metrics_test.go`)
- e2e testing with a real Windows VM is not feasible in kubevirt/kubevirt CI due to Windows
  licensing constraints. The data path reuses existing guest agent infrastructure already covered
  by e2e tests for other commands. Full e2e validation can be done downstream by vendors with
  access to Windows images.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `GuestDeviceMetrics` guards the domainstats scraper / Prometheus metric path
- [ ] `guest-get-devices` command added to agent poller and VMStatsCollector pipelines
- [ ] Metric emitted per device per VM
- [ ] Unit tests for JSON parsing and metric emission
- [ ] Initial documentation

### Beta

- [ ] Gather user feedback on label selection and metric structure
- [ ] Review cardinality impact from real-world deployments
- [ ] Refine polling interval if needed based on operational experience
- [ ] Documentation finalized

### GA

- [ ] Feature gate promoted to GA
