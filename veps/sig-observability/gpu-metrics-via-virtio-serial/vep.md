# VEP #254: Guest GPU Metrics via virtio-serial

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

GPU workloads running inside KubeVirt virtual machines currently lack observability. Cluster administrators and users have no way to monitor
GPU utilization, memory usage, temperature, power consumption, or error counts for GPUs passed through to VMs.

This VEP introduces a mechanism for collecting GPU metrics from inside the guest and exposing them as Prometheus metrics on the host. A
lightweight guest agent communicates with the host via a virtio-serial channel, and virt-handler scrapes the agent on each Prometheus
collection cycle to produce `kubevirt_vmi_gpu_*` metrics.

## Motivation

GPU passthrough and vGPU workloads are increasingly common in KubeVirt for AI/ML training, inference, and media processing. Host-level GPU
monitoring tools like NVIDIA DCGM exporter are not available in these configurations. The NVIDIA GPU Operator does not deploy this service
on nodes where GPUs are configured for passthrough or vGPU, because the host no longer has direct access to the device. This leaves GPU
workloads inside VMs completely unmonitored.

By collecting metrics from inside the guest via NVML and forwarding them to the host over virtio-serial, KubeVirt can provide per-VM,
per-GPU observability that is consistent with the existing `kubevirt_vmi_*` metrics namespace, enabling unified dashboards and alerting.

## Goals

- Expose per-VM, per-GPU utilization metrics as Prometheus metrics from virt-handler.
- Support both GPU passthrough and vGPU devices.
- Support Linux and Windows guests.
- Use virtio-serial as the transport, avoiding network dependencies inside the guest.
- Keep the guest agent lightweight, stateless, and easy to install.

## Non Goals

- Managing GPU drivers or NVML installation inside the guest.
- Supporting non-NVIDIA GPUs (AMD, Intel) in the initial implementation. The protocol is vendor-agnostic, but the first agent implementation
uses NVML.
- Alerting rules or Grafana dashboards (these can be added separately).
- Collecting GPU metrics from the host side (e.g., via DCGM).

## Definition of Users

- **Cluster administrators** who need to monitor GPU utilization across VMs for capacity planning, cost allocation, and health monitoring.
- **VM users** running GPU workloads who want to see GPU metrics alongside other VM metrics in existing monitoring infrastructure.
- **Platform teams** building autoscaling or scheduling decisions based on GPU utilization.

## User Stories

### GPU Utilization Monitoring
As a cluster administrator, I want to see GPU utilization, memory usage, and temperature for each VM so I can identify underutilized or
overheating GPUs and take action.

### Capacity Planning
As a platform engineer, I want per-VM GPU metrics in Prometheus so I can build dashboards showing GPU utilization trends across the cluster
and plan capacity.

### Error Detection
As an operations engineer, I want to be alerted when a GPU inside a VM reports ECC errors so I can proactively migrate the workload before
hardware failure.

## Repos

- https://github.com/kubevirt/kubevirt (virtio-serial channel, virt-handler collector)
- https://github.com/kubevirt/gpu-metrics-agent (guest-side agent, separate repo)

## Design

The design has four components spanning three processes:

```
Guest VM (QEMU)                     virt-launcher                         virt-handler
+--------------------------+        +-------------------------------+     +---------------------------+
|                          |        |                               |     |                           |
|  gpu-metrics-agent       |        |  QEMU                         |     |  domainstats scraper      |
|                          |        |    virtio-serial backend      |     |                           |
|  - collects NVML metrics |        |    gpu-metrics.sock (UNIX)    |     |  - GetDomainStats()       |
|  - responds with JSON    | <====> |                               |     |  - GetFilesystems()       |
|                          |        |  DomainManager                |     |  - GetGPUMetrics()        |
|  /dev/virtio-ports/      |        |    gpuMetricsCache            |     |                           |
|  org.kubevirt.           |        |      TimeDefinedCache (3.25s) |     |  resourceMetrics:         |
|  gpu-metrics.0           |        |    scrapeGPUMetrics()         |     |    gpuMetrics.Collect()   |
|                          |        |      connect → GET\n → JSON   |     |    → kubevirt_vmi_gpu_*   |
+--------------------------+        |                               |     |                           |
                                    |  cmd-server (gRPC)            |     |  cmd-client (gRPC)        |
                                    |    GetGPUMetrics() RPC        | <-- |    GetGPUMetrics()        |
                                    +-------------------------------+     +---------------------------+
```

### 1. Virtio-Serial Channel (virt-launcher)

When a VMI has GPU devices configured (`spec.domain.devices.gpus`), the domain converter adds a virtio-serial channel to the libvirt domain
XML. The virt-launcher process creates the socket directory (`/var/run/kubevirt-private/gpu-metrics-channel/`) during initialization, before
the domain is started. libvirt/QEMU then binds the UNIX socket at that path.

This produces:
- **Host side (virt-launcher pod)**: UNIX socket at `/var/run/kubevirt-private/gpu-metrics-channel/gpu-metrics.sock`
- **Guest side**: character device at `/dev/virtio-ports/org.kubevirt.gpu-metrics.0` (Linux) or named pipe
`\\.\Global\org.kubevirt.gpu-metrics.0` (Windows)

### 2. Guest Agent (gpu-metrics-agent)

A standalone Go binary that runs inside the guest as a systemd service (Linux) or Windows service. On startup it:
1. Opens the virtio-serial device.
2. Initializes NVML (gracefully handles failure and remains running with error responses).
3. Enters a read loop waiting for request lines from the host.
4. On each request, collects GPU metrics via NVML and writes a JSON response.

The request/response protocol is newline-delimited:
- **Request**: any text line ending with `\n` (e.g., `GET\n`)
- **Response**: a single JSON object followed by `\n`

The agent handles host disconnects (EOF on the char device when the host closes the socket) by re-entering the read loop on the same file
descriptor, matching virtio-serial reconnection semantics.

Response schema:
```json
{
  "version": "1.0.0",
  "error": {"code": 12, "message": "ERROR_LIBRARY_NOT_FOUND"},
  "devices": [
    {
      "index": 0,
      "uuid": "GPU-abc-123",
      "name": "Tesla T4",
      "gpuUtilizationPercent": 75,
      "memoryUtilizationPercent": 38,
      "memoryUsedBytes": 4294967296,
      "memoryTotalBytes": 17179869184,
      "temperatureCelsius": 54,
      "powerUsageMilliwatts": 121180,
      "powerLimitMilliwatts": 250000,
      "eccErrorsSingleBit": 0,
      "eccErrorsDoubleBit": 0,
      "encoderUtilizationPercent": 15,
      "decoderUtilizationPercent": 5,
      "runningProcesses": 3,
      "pcieTxBytesPerSecond": 102400,
      "pcieRxBytesPerSecond": 204800
    }
  ]
}
```

If NVML is unavailable, `error` is populated and `devices` is empty. The agent remains running.

### 3. virt-launcher: DomainManager and gRPC

Two layers handle GPU metrics on the virt-launcher side:

**DomainManager (`LibvirtDomainManager`)**: A `gpuMetricsCache` (`TimeDefinedCache[string]`, 3250ms TTL) caches the raw JSON from the guest
agent. The recalculation function (`scrapeGPUMetrics`) connects to the local virtio-serial UNIX socket, sends `GET\n`, and reads the JSON
response. This follows the same caching pattern as `domainStatsCache` for domain stats.

**cmd-server (`GetGPUMetrics` RPC)**: A new `GetGPUMetrics` method on the `Cmd` gRPC service delegates to `DomainManager.GetGPUMetrics()`,
which returns the cached value. This follows the same pattern as `GetDomainStats`, keeping the cmd-server thin.

### 4. Prometheus Collector (virt-handler)

GPU metrics are collected as part of the existing **domainstats** collector, following the same `resourceMetrics` pattern used for CPU,
memory, block, network, and filesystem metrics. No separate collector is needed.

On each Prometheus scrape, the domainstats scraper:

1. Connects to each VMI's virt-launcher via its cmd-client socket (same as for domain stats).
2. Calls `cli.GetGPUMetrics()` alongside `GetDomainStats()` and `GetFilesystems()` within the same scrape.
3. Parses the JSON response into `GPUMetricsResponse` and stores it in `VirtualMachineInstanceStats.GPUStats`.
4. The `gpuMetrics` resource metrics implementation emits collector results for each GPU device.

GPU metric scrape failures are logged at warning verbosity and do not block the rest of the domain stats collection. If the guest agent is
not installed or not running, `GPUStats` is nil and no GPU metrics are emitted for that VMI.

This approach reuses the existing `ConcurrentCollector` infrastructure (concurrency limiting, per-VMI timeouts, socket discovery) rather
than duplicating it.

### Metrics Emitted

| Metric | Type | Description |
|--------|------|-------------|
| `kubevirt_vmi_gpu_agent_status` | Gauge | Agent status (0 = OK, non-zero = NVML error code) |
| `kubevirt_vmi_gpu_utilization_percent` | Gauge | GPU compute utilization (0-100) |
| `kubevirt_vmi_gpu_memory_utilization_percent` | Gauge | GPU memory controller utilization (0-100) |
| `kubevirt_vmi_gpu_memory_used_bytes` | Gauge | GPU memory used in bytes |
| `kubevirt_vmi_gpu_memory_total_bytes` | Gauge | GPU total memory in bytes |
| `kubevirt_vmi_gpu_temperature_celsius` | Gauge | GPU temperature in degrees Celsius |
| `kubevirt_vmi_gpu_power_usage_milliwatts` | Gauge | GPU power draw in milliwatts |
| `kubevirt_vmi_gpu_ecc_errors_single_bit_total` | Gauge | Lifetime corrected ECC error count from NVML |
| `kubevirt_vmi_gpu_ecc_errors_double_bit_total` | Gauge | Lifetime uncorrected ECC error count from NVML |
| `kubevirt_vmi_gpu_encoder_utilization_percent` | Gauge | Video encoder utilization (0-100) |
| `kubevirt_vmi_gpu_decoder_utilization_percent` | Gauge | Video decoder utilization (0-100) |
| `kubevirt_vmi_gpu_running_processes` | Gauge | Number of compute processes on the GPU |

All per-device metrics carry labels: `node`, `namespace`, `name`, `gpu_index`, `gpu_uuid`, `gpu_name`, plus VMI labels prefixed with
`kubernetes_vmi_label_`. The `gpu_agent_status` metric carries `version`, `error_code`, and `error_message` labels instead of per-device
labels.

## API Examples

No changes to the KubeVirt API are required. The virtio-serial channel is added automatically when GPUs are present in the VMI spec:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-workload
spec:
  domain:
    devices:
      gpus:
        - name: gpu1
          deviceName: nvidia.com/A100
```

The GPU metrics channel appears in the domain XML alongside the existing guest agent channel:

```xml
<channel type='unix'>
  <source mode='bind' path='/var/run/kubevirt-private/gpu-metrics-channel/gpu-metrics.sock'/>
  <target type='virtio' name='org.kubevirt.gpu-metrics.0'/>
</channel>
```

## Alternatives

### VSOCK Instead of Virtio-Serial

VSOCK (`AF_VSOCK`) provides socket-based communication between guest and host without virtio-serial.

**Rejected because:**
- VSOCK requires kernel support that is not universally available, especially on older guests and Windows.
- Virtio-serial is already used by KubeVirt for qemu-guest-agent and downward metrics, making it a proven transport.
- Virtio-serial channels appear as simple character devices in the guest, making the agent trivial to implement on both Linux and Windows.

### Host-Side GPU Metrics (DCGM / Node Exporter)

Collect GPU metrics from the host using NVIDIA DCGM or the GPU node exporter.

**Rejected as the sole approach because:**
- The NVIDIA GPU Operator does not deploy DCGM exporter on nodes where GPUs are configured for passthrough or vGPU, because the host no
longer has direct access to the device.

### Embedding Metrics in QEMU Guest Agent

Extend the existing QEMU guest agent to collect GPU metrics.

**Rejected because:**
- Out-of-scope arbitrary NVIDIA-specific monitoring commands to the QEMU guest agent.

## Scalability

- **Per-scrape overhead**: One UNIX socket connection per VMI with GPUs, per Prometheus scrape. Connection is short-lived (connect, request,
read, close). A 5-second timeout prevents slow agents from blocking the scrape.
- **Caching**: GPU metrics are cached in virt-launcher with a 3.25-second TTL, so multiple Prometheus scrapes within that window reuse the
same data without reconnecting to the guest agent.
- **Concurrency**: GPU metrics are fetched as part of the existing domainstats scraper, which scrapes all VMIs in parallel using the
`ConcurrentCollector` infrastructure.
- **No persistent connections**: The host does not maintain long-lived connections to guest agents.
- **Scale**: Comparable to the existing domain stats and filesystem stats collection, which already scrape per-VMI data on each Prometheus
collection.

## Update/Rollback Compatibility

- The virtio-serial channel is only added when the VMI has GPU devices. Once the `GPUMetrics` feature gate is implemented, disabling it or
rolling back will remove the channel from new VMIs; existing running VMIs retain the channel until they are stopped.
- The guest agent is an opt-in installation. If the agent is not installed, virt-handler logs a connection failure and emits no GPU metrics
for that VMI.
- No API changes; no migration compatibility concerns.

## Functional Testing Approach

- **Unit tests**: Test the collector callback with mock socket responses (success, error, timeout, agent not running).
- **Unit tests**: Test virtio-serial channel creation in domain XML converter when GPUs are present vs. absent.
- **Integration tests**: Start a VMI with a mock GPU metrics agent, verify `kubevirt_vmi_gpu_*` metrics are emitted from the virt-handler
metrics endpoint.
- **Guest agent tests**: Tested in the gpu-metrics-agent repo (protocol, NVML mock, reconnection behavior).

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `GPUMetrics` guards all code changes
- [ ] Virtio-serial channel created for VMIs with GPU devices
- [ ] virt-handler collector scrapes guest agent and emits Prometheus metrics
- [ ] Guest agent supports Linux with NVML
- [ ] Unit tests for collector, channel creation, and agent protocol
- [ ] Documentation for installing and running the guest agent

### Beta

- [ ] Guest agent supports Windows
- [ ] Integration tests with mock agent in kubevirtci
- [ ] Prometheus recording rules and/or alerts for common GPU failure scenarios
- [ ] Protocol versioning validated (agent version vs. host expectations)

### GA

- [ ] Stable for at least two releases with no protocol-breaking changes
