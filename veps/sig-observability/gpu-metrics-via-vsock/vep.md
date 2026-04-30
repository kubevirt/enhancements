# VEP #254: Guest GPU Metrics via VSOCK

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
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

This VEP introduces a mechanism for collecting GPU metrics from inside the guest and exposing them as Prometheus metrics on the host. NVIDIA
DCGM (Data Center GPU Manager) 4.5.0 added native support for listening on the VSOCK protocol, enabling direct guest-to-host communication
without a custom guest agent. virt-launcher connects to DCGM inside the guest via VSOCK and exposes the GPU metrics data through the unified
`GetVMStats` gRPC call ([VEP #143](https://github.com/kubevirt/enhancements/pull/81)). virt-handler queries virt-launcher via `GetVMStats`,
and the observability-controller queries virt-handler to produce `kubevirt_vmi_gpu_*` metrics.

## Motivation

GPU passthrough and vGPU workloads are increasingly common in KubeVirt for AI/ML training, inference, and media processing. Host-level GPU
monitoring tools like NVIDIA DCGM exporter are not available in these configurations. The NVIDIA GPU Operator does not deploy this service
on nodes where GPUs are configured for passthrough or vGPU, because the host no longer has direct access to the device. This leaves GPU
workloads inside VMs completely unmonitored.

NVIDIA DCGM 4.5.0 introduced native VSOCK support, allowing the DCGM daemon inside a guest VM to accept connections from the host over
VSOCK. By leveraging this capability, KubeVirt can collect GPU metrics directly from DCGM without maintaining a custom guest agent, providing
per-VM, per-GPU observability that is consistent with the existing `kubevirt_vmi_*` metrics namespace and enabling unified dashboards and
alerting.

## Goals

- Expose per-VM, per-GPU utilization metrics as Prometheus metrics from the observability-controller.
- Support both GPU passthrough and vGPU devices.
- Support Linux and Windows guests.
- Leverage DCGM's native VSOCK support to avoid maintaining a custom guest agent.
- Integrate with the unified `GetVMStats` gRPC call rather than introducing a separate RPC.

## Non Goals

- Managing GPU drivers or DCGM installation inside the guest.
- Supporting non-NVIDIA GPUs (AMD, Intel) in the initial implementation.
- Alerting rules or Grafana dashboards (these can be added separately).
- Collecting GPU metrics from the host side (e.g., via DCGM on the host).

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

- https://github.com/kubevirt/kubevirt
- https://github.com/kubevirt/kubevirt-observability-controller

## Design

The design integrates GPU metrics into the existing monitoring data pipeline established by
[VEP #143](https://github.com/kubevirt/enhancements/pull/81). GPU metrics become a new data category within the unified `GetVMStats` gRPC
call, following the same pattern as domain stats, guest info, and filesystem data.

```
Guest VM (QEMU)                     virt-launcher                      virt-handler                observability-controller
+--------------------------+        +----------------------------+     +---------------------+     +-------------------------+
|                          |        |                            |     |                     |     |                         |
|  DCGM (nv-hostengine)   |         |  DomainManager             |     |                     |     |                         |
|  - collects GPU metrics  | <====> |    gpuMetricsCache         |     |                     |     |                         |
|  - listens on VSOCK      | vsock  |      TimeDefinedCache      |     |                     |     |                         |
|                          |        |    scrapeGPUMetrics()      |     |                     |     |  queries virt-handler   |
+--------------------------+        |                            |     |  GetVMStats()       | <-- |  for all VMI data       |
                                    |  cmd-server (gRPC)         |     |   includes gpuStats |     |                         |
                                    |    GetVMStats() handler    | <-- |                     |     |  emits:                 |
                                    |      includes gpuStats     |     |                     |     |    kubevirt_vmi_gpu_*   |
                                    +----------------------------+     +---------------------+     +-------------------------+
```

### 1. Prerequisites: VSOCK Enablement

Users must enable VSOCK on the VM spec to allow guest-host communication. VSOCK (`AF_VSOCK`) is a socket address family for guest-host
communication using the virtio-vsock transport. KubeVirt already has VSOCK support with per-VMI CID assignment by virt-controller.

DCGM 4.5.0 added native support for listening on the VSOCK protocol. The DCGM daemon (`nv-hostengine`) inside the guest can be configured
to listen on a VSOCK port, allowing virt-launcher on the host to connect and query GPU metrics using DCGM's client protocol. This provides
proper socket semantics including flow control and connection state detection.

**Advantages over virtio-serial:**
- Standard socket API with flow control and connection state detection.
- No data transfer size limitations (virtio-serial Windows drivers fail WriteFile calls >2MB).
- Already supported by KubeVirt with per-VMI CID assignment.
- DCGM natively supports VSOCK, eliminating the need for a custom guest agent.

**Downsides:**
- Requires Linux kernel 4.8+ in the guest; older kernels have no support.
- Windows guests require virtio-win drivers with VSOCK support.

### 2. Guest: DCGM with VSOCK

NVIDIA DCGM runs inside the guest VM as the GPU metrics provider. The DCGM daemon (`nv-hostengine`) is configured to listen on a VSOCK
port, accepting connections from the host. Users are responsible for installing and configuring DCGM in the guest.

DCGM collects GPU metrics via NVML and exposes them through its client API. The guest only needs DCGM installed and configured to listen
on VSOCK; no additional KubeVirt-specific agent is required.

The metrics collected from DCGM include GPU utilization, memory usage, temperature, power consumption, ECC errors, encoder/decoder
utilization, and running process counts.

### 3. virt-launcher: GPU Metrics in GetVMStats

GPU metrics are integrated into the `GetVMStats` gRPC handler introduced by [VEP #143](https://github.com/kubevirt/enhancements/pull/81).
A new `GpuStatsRequest` / `gpuStats` field is added to `VMStatsRequest` / `VMStatsResponse`, following the same pattern as the other data
categories (domain stats, guest info, filesystems, etc.).

**DomainManager (`LibvirtDomainManager`)**: A `gpuMetricsCache` (`TimeDefinedCache[string]`, 3250ms TTL) caches the metrics from DCGM.
The recalculation function (`scrapeGPUMetrics`) connects to the guest's DCGM via VSOCK (using the VMI's CID and a well-known port), queries
GPU metrics through the DCGM client protocol, and returns the response. This follows the same caching pattern as `domainStatsCache` for
domain stats.

**GetVMStats handler**: When the caller includes `GpuStatsRequest` in the `VMStatsRequest`, the handler reads from `gpuMetricsCache` and
populates the `gpuStats` field in the response. This keeps GPU metrics collection consistent with the unified monitoring data pipeline.

### 4. virt-handler: Requesting GPU Stats

virt-handler includes `GpuStatsRequest` when calling `GetVMStats` on each virt-launcher. The GPU metrics data is returned alongside domain
stats and other monitoring data in the same `GetVMStats` response.

### 5. observability-controller: Emitting Prometheus Metrics

The observability-controller ([VEP #143](https://github.com/kubevirt/enhancements/pull/81)) queries virt-handler to collect runtime VM data.
GPU metrics are collected as part of this existing flow. The controller parses the `gpuStats` data from `GetVMStats` responses and emits
`kubevirt_vmi_gpu_*` Prometheus metrics.

The `gpuMetrics` resource metrics implementation emits collector results for each GPU device found in the response. This follows the same
`resourceMetrics` pattern used for CPU, memory, block, network, and filesystem metrics.

### Metrics Emitted

| Metric | Type | Description |
|--------|------|-------------|
| `kubevirt_vmi_gpu_utilization_percent` | Gauge | GPU compute utilization (0-100) |
| `kubevirt_vmi_gpu_memory_utilization_percent` | Gauge | GPU memory controller utilization (0-100) |
| `kubevirt_vmi_gpu_memory_used_bytes` | Gauge | GPU memory used in bytes |
| `kubevirt_vmi_gpu_memory_total_bytes` | Gauge | GPU total memory in bytes |
| `kubevirt_vmi_gpu_temperature_celsius` | Gauge | GPU temperature in degrees Celsius |
| `kubevirt_vmi_gpu_power_usage_milliwatts` | Gauge | GPU power draw in milliwatts |
| `kubevirt_vmi_gpu_ecc_errors_single_bit_total` | Gauge | Lifetime corrected ECC error count |
| `kubevirt_vmi_gpu_ecc_errors_double_bit_total` | Gauge | Lifetime uncorrected ECC error count |
| `kubevirt_vmi_gpu_encoder_utilization_percent` | Gauge | Video encoder utilization (0-100) |
| `kubevirt_vmi_gpu_decoder_utilization_percent` | Gauge | Video decoder utilization (0-100) |
| `kubevirt_vmi_gpu_running_processes` | Gauge | Number of compute processes on the GPU |

All per-device metrics carry labels: `node`, `namespace`, `name`, `gpu_index`, `gpu_uuid`, `gpu_name`, plus VMI labels prefixed with
`kubernetes_vmi_label_`.

## API Examples

Users must enable VSOCK on the VM spec and have DCGM installed and listening on VSOCK inside the guest:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-workload
spec:
  domain:
    devices:
      autoattachVSOCK: true
      gpus:
        - name: gpu1
          deviceName: nvidia.com/A100
```

## Alternatives

### Custom Guest Agent via Virtio-Serial

A standalone Go binary (`gpu-metrics-agent`) runs inside the guest, collects GPU metrics via NVML, and communicates with the host over a
dedicated virtio-serial channel using a simple text protocol (`GET\n` -> JSON response).

**Rejected because:**
- Requires maintaining a separate guest agent repository and release lifecycle.
- Virtio-serial lacks flow control and connection state detection.
- Windows virtio-serial drivers have known issues with large data transfers (>2MB).
- DCGM 4.5.0's native VSOCK support makes a custom agent unnecessary.

### Custom Guest Agent via VSOCK

Same as above but using VSOCK instead of virtio-serial as the transport.

**Rejected because:**
- Still requires maintaining a custom guest agent when DCGM can serve metrics directly.

### Host-Side GPU Metrics (DCGM / Node Exporter)

Collect GPU metrics from the host using NVIDIA DCGM or the GPU node exporter.

**Rejected because:**
- The NVIDIA GPU Operator does not deploy DCGM exporter on nodes where GPUs are configured for passthrough or vGPU, because the host no
longer has direct access to the device.

### QEMU Guest Agent guest-file-read

The guest writes GPU metrics to a file, and the host reads it via QGA's `guest-file-open`, `guest-file-read`, and `guest-file-close`
commands.

**Rejected because:**
- Each scrape requires three QGA round-trips, adding latency.
- Reading while writing can produce partial or corrupt data.
- Enabling `guest-file-read` allows reading arbitrary guest files, requiring careful security analysis.

### QEMU Guest Agent guest-exec

The host uses QGA `guest-exec` to run a metrics collection command inside the guest.

**Rejected because:**
- `guest-exec` is disabled by default in many distributions (e.g., RHEL/CentOS) due to security concerns.
- Common SELinux issues blocking executed commands.
- Output is base64-encoded and must be polled, adding latency.

### Exposing DCGM via regular Kubernetes Networking

Instead of using VSOCK, DCGM inside the guest could listen on a standard network
interface and be exposed to Prometheus via Kubernetes Services and
ServiceMonitors.

**Rejected because:**

- **Additional resource overhead**: Each VM would need a dedicated Service and
ServiceMonitor created and deleted in sync with the VM lifecycle. This scales
with the number of GPU VMs and adds complexity that does not exist with the
VSOCK approach.

- **Network dependency**: Requires the guest to have a network interface on the
pod network. Not all VM use-cases will have usable network configurations.
SR-IOV only, isolated via Multus, or no network connectivity at all. VSOCK is
independent of the networking configuration.

- **Security**: With VSOCK, communication is scoped to host-guest only and is
managed entirely by KubeVirt. virt-handler's Service and ServiceMonitor are the
only externally reachable endpoints where this data will be exposed, and their
security is handled by KubeVirt. Exposing DCGM on a network interface shifts
this responsibility to the user, who must secure DCGM against access from other
sources.

### Dedicated GetGPUMetrics gRPC RPC

A separate `GetGPUMetrics` RPC on the `Cmd` gRPC service, called by virt-handler alongside `GetDomainStats` and `GetFilesystems`.

**Rejected because:**
- VEP #143 introduces a unified `GetVMStats` RPC that consolidates all monitoring data into a single call. Adding a separate RPC for GPU
metrics would work against that consolidation goal.
- GPU metrics fit naturally as a new data category within `GetVMStats`, following the same pattern as domain stats, guest info, and
filesystems.

## Scalability

- **Caching**: GPU metrics are cached in virt-launcher with a 3.25-second TTL, so multiple scrapes within that window reuse the same data
without reconnecting to DCGM.
- **Unified collection**: GPU metrics are fetched as part of the `GetVMStats` call, adding no additional gRPC round-trips between
virt-handler and virt-launcher.
- **No persistent connections**: The host does not maintain long-lived connections to DCGM in the guest.
- **Scale**: Comparable to the existing domain stats and filesystem stats collection, which already collect per-VMI data as part of
`GetVMStats`.

## Update/Rollback Compatibility

- VSOCK must be enabled per-VMI by the user. Once the `GPUMetrics` feature gate is implemented, disabling it or rolling back will stop GPU
metrics collection for new VMIs; existing running VMIs are unaffected.
- This VEP depends on the `GetVMStats` RPC from VEP #143. If VEP #143 is not yet implemented, GPU metrics cannot be collected.
- DCGM inside the guest is an opt-in installation by the VM user. If DCGM is not installed or not listening on VSOCK, virt-launcher returns
empty `gpuStats` and no GPU metrics are emitted for that VMI.
- No API changes beyond requiring `autoattachVSOCK: true`; no migration compatibility concerns.

## Functional Testing Approach

- **Unit tests**: Test the GPU stats handler within `GetVMStats` with mock VSOCK responses (success, error, timeout, DCGM not running).
- **Unit tests**: Test VSOCK connection setup for VMIs with GPU devices and VSOCK enabled vs. absent.
- **Integration tests**: Start a VMI with a mock DCGM VSOCK listener, verify `kubevirt_vmi_gpu_*` metrics are emitted from the
observability-controller metrics endpoint.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `GPUMetrics` guards all code changes
- [ ] `GpuStatsRequest` / `gpuStats` field added to `GetVMStats` proto messages (depends on VEP #143)
- [ ] virt-launcher connects to guest DCGM via VSOCK and populates `gpuStats` in `GetVMStats` response
- [ ] observability-controller parses GPU stats and emits Prometheus metrics
- [ ] Unit tests for GPU stats collection, VSOCK connection, and DCGM protocol handling
- [ ] Documentation for enabling VSOCK and installing/configuring DCGM in the guest

### Beta

- [ ] Windows guest support validated
- [ ] Integration tests with mock DCGM VSOCK listener in kubevirtci
- [ ] Prometheus recording rules and/or alerts for common GPU failure scenarios
- [ ] DCGM version compatibility validated (minimum version requirements documented)

### GA

- [ ] Stable for at least two releases with no breaking changes
