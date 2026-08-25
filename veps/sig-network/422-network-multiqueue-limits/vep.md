# VEP 422: Configurable Network Interface Multi-Queue Limits

**Tracking issue:** https://github.com/kubevirt/enhancements/issues/422

**Related bug:** https://github.com/kubevirt/kubevirt/issues/18012

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version: v1.11
- This VEP targets GA for version: v1.12

## Release Signoff Checklist

Items marked with (R) are required _prior to targeting to a milestone / release_.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt enables virtio network multiqueue via `domain.devices.networkInterfaceMultiqueue`.
When enabled, every virtio network interface receives a queue count equal to the guest
vCPU count (sockets × cores × threads), hard-capped at 256. There is no API to set a
lower per-VM or per-interface limit.

For VMs with many NICs and/or high vCPU counts, this can waste guest memory (notably on
Windows with the NetKVM driver), increase MSI vector pressure, and provide little
throughput benefit when queue count far exceeds actual parallelism needs.

This VEP introduces configurable upper bounds on virtio network queue allocation at
cluster, VM, and per-interface scope.

## Motivation

Reported in [kubevirt/kubevirt#18012](https://github.com/kubevirt/kubevirt/issues/18012):
an 8 vCPU Windows Server 2019 VM with 10 virtio NICs and multiqueue enabled showed ~90%
memory utilization after boot.

Contributing factors:

1. **Uniform queue scaling:** With 8 vCPUs and 10 NICs, each NIC gets 8 queues (80 total).
   Guest-side driver buffers can dwarf host vhost memory (~64 KiB/queue on the host).
2. **No tunable cap:** Queue count cannot be set below vCPU count via the KubeVirt API.
3. **Large VM scaling:** A 128 vCPU VM would receive 128 queues per NIC (up to 256),
   with diminishing networking benefit and growing resource cost.
4. **MSI vectors:** Guest OSes typically support ~200 MSI vectors. Many queued NICs can
   approach or exceed practical limits, causing driver fallback behavior.

KubeVirt documentation already warns that multiqueue should not be enabled unconditionally.
Users need API knobs to apply that guidance in production workloads.

Discussion on #18012 reached consensus that `queues == vCPUs` is wasteful for high vCPU
and multi-vNIC VMs, and that tunables or max-queue logic should be added.

## Goals

- Allow VM owners to cap virtio network queue count below vCPU count at VM scope.
- Allow VM owners to optionally set queue count per interface.
- Allow cluster administrators to set a cluster-wide default cap via the KubeVirt CR.
- Preserve backward compatibility: VMs without new fields behave exactly as today.
- Continue reporting effective queue count in `status.interfaces[].queueCount`.

## Non Goals

- Changing default behavior when new fields are unset (still `min(vCPUs, 256)`).
- Per-queue CPU affinity or RSS tuning inside the guest.
- Multiqueue for non-virtio interface models (e.g. e1000, rtl8139).
- Multiqueue for SR-IOV passthrough NICs.
- Live-updating queue count on a running VM (requires domain restart).
- Automatic heuristics based on NIC count without explicit user configuration
  (may be considered in a future enhancement).
- Changing the hard ceiling of 256 queues (`MultiQueueMaxQueues`).

## Definition of Users

- **VM owners** running multi-NIC or Windows workloads who enable multiqueue selectively.
- **Cluster administrators** who want safe defaults for large VMs in shared clusters.
- **Instance type / preference authors** who want to recommend queue limits for workload classes.

## User Stories

- As a VM owner with 10 NICs on an 8 vCPU Windows VM, I want to cap queues to 2 per NIC
  so guest memory stays manageable while keeping multiqueue enabled.
- As a VM owner, I want my primary NIC at 8 queues and secondary NICs at 1 queue.
- As a cluster admin, I want a cluster default max of 16 queues so 128 vCPU VMs do not
  allocate 128 queues per NIC by default.
- As an instance type author, I want to publish a recommended `maxNetworkInterfaceQueues`
  value for a workload class.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt/)

## Design

### Current behavior

When `networkInterfaceMultiqueue` is true, queue count is computed in
`pkg/virt-launcher/virtwrap/converter/network/virtio-queues.go`:

```
queueCount = min(vCPUCount, 256)
```

This value is applied uniformly to every virtio interface in:

- Domain XML generation (`pkg/virt-launcher/virtwrap/converter/network/configurator.go`)
- Tap device setup (`pkg/network/setup/netpod/netpod.go`)

There is no per-interface or per-VM override in the API today.

### Queue count formula

When `networkInterfaceMultiqueue` is true and the interface model supports virtio
multiqueue (default `virtio` model):

```
effectiveQueues(iface) = min(
  vCPUCount,
  clusterMax,           // KubeVirt configuration, if set
  vmMax,                // domain.devices.maxNetworkInterfaceQueues, if set
  iface.queues,         // per-interface override, if set
  MultiQueueMaxQueues   // hard ceiling (256), unchanged
)
```

When `networkInterfaceMultiqueue` is false or unset, `iface.queues` is ignored and
no multiqueue is configured (unchanged behavior).

Precedence: each layer can only **reduce** the queue count relative to vCPU count.
The most specific explicit value wins when lowering the cap:

1. `interfaces[].queues` (per-interface explicit value)
2. `domain.devices.maxNetworkInterfaceQueues` (VM-wide cap)
3. `KubeVirt.spec.configuration.network.maxInterfaceQueues` (cluster cap)
4. vCPU count
5. Hard cap 256

All explicit values must satisfy: `1 <= value <= min(vCPUs, 256)`.

### API additions

#### VM / VMI `Devices`

```go
type Devices struct {
    // ...
    // NetworkInterfaceMultiQueue enables virtio multiqueue when true.
    NetworkInterfaceMultiQueue *bool `json:"networkInterfaceMultiqueue,omitempty"`

    // MaxNetworkInterfaceQueues caps the number of virtio network queues per
    // interface when multiqueue is enabled. If unset, defaults to vCPU count.
    // Must be <= guest vCPU count and <= 256.
    // +optional
    MaxNetworkInterfaceQueues *uint32 `json:"maxNetworkInterfaceQueues,omitempty"`
    // ...
}
```

#### `Interface`

```go
type Interface struct {
    // ...
    // Queues sets the number of virtio multiqueue queues for this interface.
    // Only valid when networkInterfaceMultiqueue is true and model is virtio (default).
    // If unset, the VM-wide cap or vCPU count applies.
    // Must be <= guest vCPU count, <= maxNetworkInterfaceQueues (if set), and <= 256.
    // +optional
    Queues *uint32 `json:"queues,omitempty"`
    // ...
}
```

#### KubeVirt cluster configuration (Beta)

```go
type NetworkConfiguration struct {
    // ...
    // MaxInterfaceQueues sets a cluster-wide upper bound on virtio network
    // interface queue count when multiqueue is enabled. VM and per-interface
    // settings may only further reduce this value.
    // +optional
    MaxInterfaceQueues *uint32 `json:"maxInterfaceQueues,omitempty"`
}
```

#### Instance type preference (Beta)

```go
// PreferredMaxNetworkInterfaceQueues optionally caps virtio network queues
// when multiqueue is enabled.
// +optional
PreferredMaxNetworkInterfaceQueues *uint32 `json:"preferredMaxNetworkInterfaceQueues,omitempty"`
```

### Validation

Webhooks enforce:

- `maxNetworkInterfaceQueues` and `interfaces[].queues` only valid when
  `networkInterfaceMultiqueue == true`.
- Values must be `>= 1` and `<= min(vCPUs, 256)`.
- Per-interface `queues` must be `<= maxNetworkInterfaceQueues` when VM cap is set.
- Per-interface `queues` must be `<= maxInterfaceQueues` when cluster cap is set.
- Cluster cap must be `<= 256`.
- Not allowed on SR-IOV interfaces.
- Ignored for non-virtio models (e1000, etc.) — same as today.

### Implementation touchpoints

| Component | Change |
|-----------|--------|
| `pkg/virt-launcher/virtwrap/converter/network/virtio-queues.go` | Replace uniform `NetworkQueuesCapacity(vmi)` with per-interface effective queue calculation applying all caps |
| `pkg/virt-launcher/virtwrap/converter/network/configurator.go` | Pass per-interface queue count into `newVirtioDriver` |
| `pkg/network/setup/netconf.go` / `netpod/netpod.go` | Use per-interface effective queue count for tap creation |
| `pkg/network/setup/netstat.go` | Continue reporting `status.interfaces[].queueCount` |
| `pkg/virt-api` webhooks | Validation for new fields |
| `pkg/instancetype/preference/apply` | Apply preference default if present (Beta) |
| `tests/network/multiqueue.go` | Extend coverage for caps and per-interface values |

### Feature Gate

The feature will be guarded by a feature gate during Alpha and Beta.
Proposed feature gate name: `NetworkInterfaceQueueLimits`.

### Restart requirement

Changing queue count modifies libvirt domain XML and tap queue configuration.
Updates require VM restart, consistent with enabling or disabling multiqueue today.
This will be documented in the user guide.

### Migration

Queue count is part of domain configuration. Live migration requires matching
effective queue counts on source and target (unchanged constraint class).

## API Examples

### VirtualMachine

**VM-wide queue cap (addresses #18012):**

8 vCPU VM with 10 virtio NICs. Without a cap, each NIC receives 8 queues (80 total).
`maxNetworkInterfaceQueues` caps every virtio NIC at 2 queues (20 total).

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: windows-multi-nic
spec:
  template:
    spec:
      domain:
        cpu:
          cores: 8
        devices:
          # Enables virtio multiqueue for all virtio NICs
          networkInterfaceMultiqueue: true
          # VM-wide cap applied to every virtio interface
          maxNetworkInterfaceQueues: 2
          interfaces:
            - name: net0
              masquerade: {}
            - name: net1
              bridge: {}
            - name: net2
              bridge: {}
            # ... additional interfaces (net3 through net9)
      networks:
        - name: net0
          pod: {}
        - name: net1
          multus:
            networkName: net1
        - name: net2
          multus:
            networkName: net2
```

**Per-interface queue override:**

Primary NIC keeps high throughput; secondary NICs use a single queue.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: mixed-queue-vm
spec:
  template:
    spec:
      domain:
        cpu:
          cores: 8
        devices:
          networkInterfaceMultiqueue: true
          maxNetworkInterfaceQueues: 8
          interfaces:
            - name: primary
              masquerade: {}
              # Per-interface override: 8 queues for primary NIC
              queues: 8
            - name: secondary
              masquerade: {}
              # Per-interface override: 1 queue for secondary NIC
              queues: 1
      networks:
        - name: primary
          pod: {}
        - name: secondary
          multus:
            networkName: secondary-net
```

### KubeVirt cluster configuration (Beta)

**Cluster-wide default cap:**

Cluster administrators set a default upper bound. A 128 vCPU VM defaults to 16 queues
per NIC unless the VM owner sets a lower cap.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    network:
      # Cluster-wide cap on virtio network queue count
      maxInterfaceQueues: 16
```

### VirtualMachineInstance status

**Reported queue count after cap applied:**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: windows-multi-nic
spec:
  domain:
    devices:
      networkInterfaceMultiqueue: true
      maxNetworkInterfaceQueues: 2
      interfaces:
        - name: net0
          masquerade: {}
        - name: net1
          bridge: {}
  networks:
    - name: net0
      pod: {}
    - name: net1
      multus:
        networkName: net1
status:
  interfaces:
    - name: net0
      # Effective queue count reported per interface
      queueCount: 2
    - name: net1
      queueCount: 2
```

### Validation

**Invalid manifest (queues exceed VM-wide cap):**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: invalid-queue-vm
spec:
  template:
    spec:
      domain:
        cpu:
          cores: 8
        devices:
          networkInterfaceMultiqueue: true
          maxNetworkInterfaceQueues: 16
          interfaces:
            - name: default
              masquerade: {}
              # Invalid: 32 exceeds maxNetworkInterfaceQueues (16)
              queues: 32
      networks:
        - name: default
          pod: {}
```

**Example API server validation output:**

```text
Error from server (Invalid): error when creating "invalid-queue-vm.yaml":
VirtualMachine.kubevirt.io "invalid-queue-vm" is invalid:
spec.template.spec.domain.devices.interfaces[0].queues: Invalid value: 32:
must be <= maxNetworkInterfaceQueues (16)
```

## Alternatives

### Alternative A: VM-wide cap only (no per-interface `queues`)

Smaller API surface; sufficient for #18012 but cannot differentiate primary vs
secondary NICs without disabling multiqueue on some interfaces.

### Alternative B: Automatic scaling based on NIC count

e.g. `queues = vCPUs / numVirtioNICs`. Heuristic, harder to predict, surprising for
users; rejected for initial implementation.

### Alternative C: Guest-side-only tuning (`ethtool -L`)

Works on Linux; poor UX on Windows; does not reduce domain/tap allocation at boot.

### Alternative D: Lower the global 256 hard cap

Would affect all users globally; does not solve proportional waste on multi-NIC VMs.

### Alternative E: Disable multiqueue by default for secondary NICs

Behavior change without user control; rejected.

**Recommended:** VM-wide cap + optional per-interface override + cluster default (Beta).

## Scalability

Positive impact for large VMs and multi-NIC workloads: fewer tap queues, fewer
vhost contexts, fewer guest MSI vectors, lower guest driver memory.

No negative cluster scalability impact. Validation is O(number of interfaces).

## Update/Rollback Compatibility

**Upgrade:**

- New fields are optional and additive.
- Unset fields preserve current behavior (`min(vCPUs, 256)`).

**Downgrade:**

- VMs with new fields continue to run until restarted.
- After downgrade, caps may be ignored on next boot.

**Rollback:**

- Removing the feature gate or downgrading KubeVirt does not affect running VMs.
- New fields in stored VM specs are ignored by older versions until next reconcile/restart.

## Functional Testing Approach

### Unit Tests

- Queue formula with combinations of cluster / VM / interface caps.
- Validation errors for out-of-range values and multiqueue disabled.
- Non-virtio models ignore `queues`.
- SR-IOV interfaces reject `queues`.

### E2E Tests

- Fedora VM: 3 vCPUs, multiqueue, `maxNetworkInterfaceQueues: 2` →
  `status.interfaces[0].queueCount == 2`.
- Multi-interface VM with mixed per-interface `queues` values.
- Regression: existing `tests/network/multiqueue.go` entries unchanged when new
  fields are unset.

### Manual / optional

- Windows VM smoke test comparing memory with and without cap (addresses #18012).

## Implementation History

- 2026-08-18: Initial VEP draft. Tracking kubevirt/kubevirt#18012.

## Graduation Requirements

### Alpha (v1.10)

- [ ] Feature gate `NetworkInterfaceQueueLimits` guards new fields
- [ ] `maxNetworkInterfaceQueues` on `Devices`
- [ ] `queues` on `Interface`
- [ ] Validation webhooks
- [ ] virt-launcher + netpod queue propagation
- [ ] Basic e2e test
- [ ] User guide section (can be placeholder)

### Beta (v1.11)

- [ ] `KubeVirt.spec.configuration.network.maxInterfaceQueues`
- [ ] Instance type preference field
- [ ] Extended e2e and validation coverage
- [ ] User documentation finalized
- [ ] Feedback incorporated from #18012 and SIG network

#### On-By-Default Readiness

Not applicable for Beta; feature remains behind feature gate until GA.

### GA (v1.12)

- [ ] Feature gate removed; feature enabled by default
- [ ] Full test coverage and docs finalized
- [ ] Backward compatibility validated
- [ ] No open blockers on #18012 scenario
