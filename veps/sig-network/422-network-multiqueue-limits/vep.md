# VEP 422: Configurable Network Interface Multi-Queue Limits

**Tracking issue:** https://github.com/kubevirt/enhancements/issues/422

**Related bug:** https://github.com/kubevirt/kubevirt/issues/18012

## VEP Status Metadata

### Target releases

- This VEP targets alpha (sidecar POC) for version: v1.11
- This VEP targets beta for version: TBD (only if Phase 1 graduates to core API)
- This VEP targets GA for version: TBD

### Release Signoff Checklist

Items marked with (R) are required _prior to targeting to a milestone / release_.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt enables virtio network multiqueue via `domain.devices.networkInterfaceMultiqueue`.
When enabled, every virtio network interface receives a queue count equal to the guest
vCPU count (sockets × cores × threads), hard-capped at 256. There is no way to set a
lower per-VM or per-interface limit.

For VMs with many NICs and/or high vCPU counts, this can waste guest memory (notably on
Windows with the NetKVM driver), increase MSI vector pressure, and provide little
throughput benefit when queue count far exceeds actual parallelism needs.

This VEP is delivered in two stages:

1. **Phase 1 (POC, no core API):** a sidecar `OnDefineDomain` hook caps virtio
   `driver.queues` on VMs that keep existing `masquerade` / `bridge` bindings.
   Same “experiment first” idea as [VEP-190](https://github.com/kubevirt/enhancements/issues/190).
2. **Phase 2 (core API, later):** if the POC works and SIG agrees, add stable
   VMI/cluster fields (`maxNetworkInterfaceQueues`, `interfaces[].queues`,
   optional cluster default) and apply the cap in virt-launcher and `netpod`.

A new network **binding plugin** is **not** the POC. Queue count is a virtio
device property, not a binding, and the two cannot be combined: the VMI admitter
requires **exactly one** binding per interface. `countInterfaceBindings` in
`pkg/network/admitter/binding.go` counts `bridge`, `masquerade`, `sriov`,
`passtBinding` and `binding` into a single total, and rejects any interface whose
total is not 1:

```
logical <name> interface must have exactly one binding method or binding plugin
```

So `masquerade: {}` together with `binding: { name: queue-cap }` is a hard VMI
creation failure. A `queue-cap` plugin would be a *replacement* for
masquerade/bridge, never an overlay — to cap queues on the #18012 VM it would
have to reimplement masquerade (NAT, DHCP, in-pod bridge), changing the exact
variable the POC must hold constant.

## Motivation

Reported in [kubevirt/kubevirt#18012](https://github.com/kubevirt/kubevirt/issues/18012):
an 8 vCPU / 8 GiB Windows Server 2019 VM with 10 virtio NICs and multiqueue enabled
showed ~90% memory utilization after boot. The reporter also asks whether 128 queues
per NIC is reasonable on a 128 vCPU VM.

Contributing factors:

1. **Uniform queue scaling:** With 8 vCPUs and 10 NICs, each NIC gets 8 queues (80 total).
   Guest-side driver buffers are allocated per queue and can dominate the host-side
   vhost cost.
2. **No tunable cap:** Queue count cannot be set below vCPU count via the KubeVirt API.
3. **Large VM scaling:** A 128 vCPU VM would receive 128 queues per NIC (up to 256),
   with diminishing networking benefit and growing resource cost.
4. **MSI vectors:** Each queue pair consumes guest interrupt vectors. Many queued NICs
   can approach practical guest limits, causing driver fallback behavior.

Exact per-queue memory cost is guest-driver specific and is one of the things the POC
should measure rather than assert.

KubeVirt documentation already warns that multiqueue should not be enabled unconditionally.

SIG network asked to **experiment out-of-tree first** (VEP-190 style) before adding core
API fields. Phase 1 honours that: the cap is proven in a pluggable, opt-in sidecar with
no VMI API surface, and only graduates to core API if the numbers justify it. The one
departure is the vehicle — a hook sidecar rather than a network binding plugin, because
a binding plugin cannot coexist with the `masquerade`/`bridge` configuration the bug is
reported against (see Overview and Alternative B).

## Goals

### Phase 1 (POC — no core API)

- Cap virtio network queue count below vCPU count without new VMI fields.
- Use a sidecar `OnDefineDomain` hook (existing sidecar framework).
- Keep existing `masquerade: {}` / `bridge: {}` networking unchanged.
- Configure the POC cap with a VMI annotation (not a stable API).
- Leave VMs without the sidecar unchanged (`min(vCPUs, 256)`).

### Phase 2 (core API — later)

- Add VM-wide and optional per-interface queue caps to the VMI spec.
- Optional cluster-wide default on the KubeVirt CR.
- Apply the cap in domain XML **and** tap setup (`netpod`).
- Report effective count in `status.interfaces[].queueCount`.

## Non Goals

- New core API fields in Phase 1.
- A new `queue-cap` network binding plugin as the POC (wrong abstraction;
  would replace masquerade/bridge).
- Full [VEP-190](https://github.com/kubevirt/enhancements/issues/190) Plugin CRD
  in Phase 1; the POC uses the existing sidecar `OnDefineDomain` hook.
- Aligning pod tap queues in `netpod` in Phase 1 (follow-up / Phase 2).
- Per-queue CPU affinity or RSS tuning inside the guest.
- Multiqueue for non-virtio models or SR-IOV.
- Live-updating queue count on a running VM.
- Automatic heuristics (e.g. `vCPUs / numNICs`).
- Changing the hard ceiling of 256 (`MultiQueueMaxQueues`).

## Definition of Users

- **VM owners** with multi-NIC or Windows workloads who enable multiqueue.
- **Cluster administrators** who enable the Sidecar feature gate for the POC.
- **SIG reviewers** who want a working POC before freezing core API.

## User Stories

- As a VM owner with 10 NICs on an 8 vCPU Windows VM, I want to cap queues to 2
  per NIC while keeping masquerade/bridge networking.
- As a VM owner, I want to opt into the POC with an annotation, without new
  VMI API fields.
- As a SIG reviewer, I want a POC before committing to core API.
- As a VM owner (Phase 2), I want a supported API field instead of an annotation.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt/)
  - Phase 1: `cmd/sidecars/network-queue-cap/` (sidecar POC)
  - Phase 2: API, virt-launcher, netpod, webhooks

## Design

### Current behavior

When `networkInterfaceMultiqueue` is true, queue count is computed in
`pkg/virt-launcher/virtwrap/converter/network/virtio-queues.go`:

```
queueCount = min(vCPUCount, 256)
```

This value is applied uniformly to every virtio interface in:

- Domain XML (`pkg/virt-launcher/virtwrap/converter/network/configurator.go`)
- Tap device setup (`pkg/network/setup/netpod/netpod.go`)

### Phase 1: Sidecar POC (no core API)

Queue limits are **not** a network binding. The POC is a sidecar that mutates
domain XML after core generation, on VMs that already use masquerade or bridge.

#### How it is attached

- Cluster: enable the `Sidecar` feature gate.
- VMI: the standard `hooks.kubevirt.io/hookSidecars` annotation
  (`pkg/hooks/hooks.go`) pointing at the POC image, exactly like other
  `OnDefineDomain` sidecars. See `cmd/sidecars/README.md`.
- VMI: keep `masquerade: {}` / `bridge: {}` — no interface spec change at all.
- VMI: annotation `network.kubevirt.io/max-queues` (POC only, deprecated in
  Phase 2). Integer `1..256`; missing or unparseable means leave queues unchanged.

The cap **cannot** be passed as extra `args` on the `hookSidecars` entry.
`parseCommandLineArgs` in `cmd/sidecars/sidecar_shim.go` registers only
`--version` and calls `pflag.Parse()`, which exits on an unknown flag; and
`runOnDefineDomain` invokes the hook binary with exactly `--vmi` and `--domain`,
with no pass-through. The VMI JSON is the only channel into the hook, so
configuration must travel as an annotation — the same pattern the smbios sidecar
uses (`smbios.vm.kubevirt.io/baseBoardManufacturer`).

#### Sidecar behavior

Reference layout: `cmd/sidecars/smbios/` — a single `onDefineDomain` binary run by
the stock `sidecar-shim`, reading its configuration from a VMI annotation. This is
deliberately *not* the `network-passt-binding/` layout, which stands up its own
gRPC server (`grpc.NewServer` in its `main.go`) and does not use the shim. The two
are alternatives; mixing shim args with a passt-style binary is not runnable.

1. virt-launcher generates domain XML with `queues = min(vCPUs, 256)`
   (`NetworkQueuesCapacity`).
2. Shim invokes the hook binary with `--vmi` and `--domain`; the hook unmarshals
   both and reads the cap from the VMI annotation.
3. For each interface with a non-nil `Driver.Queues`, if the value exceeds the
   cap, lower it. Bindings (`masquerade`/`bridge`) are not touched.
4. Return mutated domain XML before libvirt define.

**Do not gate on `model.type == "virtio"`.** The domain model is
`virtio-non-transitional` on amd64/arm64 (`defaultTransitionalModelType`), or
`virtio-transitional` when `UseVirtioTransitional` is set; literal `"virtio"`
occurs only on s390x. A `== "virtio"` test would therefore match on s390x alone
and silently no-op everywhere else.

Testing `Driver.Queues != nil` is both correct and sufficient: the configurator
attaches a `Driver` only when the interface type is virtio
(`configurator.go`), and `newVirtioDriver` populates `Queues` only when
multiqueue is on. Non-virtio models and SR-IOV never carry it, so no separate
skip is needed.

POC formula:

```
effectiveQueues = min(vCPUCount, pocCap, MultiQueueMaxQueues)
```

#### Status reporting (works unmodified)

No extra work is needed for status. `ifacesStatusFromDomainInterfaces` in
`pkg/network/setup/netstat.go` derives `QueueCount` from the **live domain's**
`Driver.Queues`, i.e. the value after the sidecar has mutated it. So
`status.interfaces[].queueCount` reports the capped number, and E2E can assert
on it directly rather than parsing domain XML.

#### Known gaps (Phase 1 only)

- **Boot-time tap queues:** at first boot the interface is not yet in the domain,
  so `calcQueuesCapByIface` (`pkg/network/setup/netpod/netpod.go`) falls back to
  `desiredQueueCount` — the vCPU count — and the tap is created with more queues
  than the domain will use. This is **benign**: the tap is over-provisioned, never
  under-provisioned, so vhost simply leaves the surplus queues idle. It costs a
  little host memory and is the reason Phase 2 must align `netpod`. Note the gap
  is narrower than it looks — once the interface carries `InfoSourceDomain`,
  `calcQueuesCapByIface` prefers `ifaceStatus.QueueCount`, which is already the
  capped value.
- **Cap is VM-wide.** Per-interface caps are Phase 2.
- **No validation.** An annotation is not webhook-validated the way a real API
  field would be; the hook must fail safe (leave queues unchanged) on anything
  it cannot parse.
- **`Sidecar` feature gate** is acceptable for a POC, not for production.

#### Restart and migration

Changing the cap requires a VM restart, since it changes domain XML.

Live migration needs matching effective queue counts on source and target. The
POC gets this for free: the `hookSidecars` annotation lives on the VMI, so
virt-controller renders the same sidecar into the target pod, which applies the
same cap to the target domain. Editing the annotation on a running, migrating VM
is out of scope.

### Phase 2: Core API (later)

Only after Phase 1 succeeds and SIG agrees. This is the stable product design.

Formula:

```
effectiveQueues(iface) = min(
  vCPUCount,
  clusterMax,           // KubeVirt configuration, if set
  vmMax,                // domain.devices.maxNetworkInterfaceQueues, if set
  iface.queues,         // per-interface override, if set
  MultiQueueMaxQueues
)
```

API additions:

```go
type Devices struct {
    NetworkInterfaceMultiQueue *bool `json:"networkInterfaceMultiqueue,omitempty"`
    // Caps virtio network queues per interface when multiqueue is enabled.
    // +optional
    MaxNetworkInterfaceQueues *uint32 `json:"maxNetworkInterfaceQueues,omitempty"`
}

type Interface struct {
    // Queues for this virtio interface when multiqueue is enabled.
    // +optional
    Queues *uint32 `json:"queues,omitempty"`
}

type NetworkConfiguration struct {
    // Cluster-wide upper bound. VM/interface settings may only reduce it.
    // +optional
    MaxInterfaceQueues *uint32 `json:"maxInterfaceQueues,omitempty"`
}
```

Phase 2 also:

- Validates values (`1 <= n <= min(vCPUs, 256)`).
- Applies the cap in virt-launcher **and** `netpod` tap queues.
- Drops the POC annotation as the supported interface.

### Feature gates

- **Phase 1:** `Sidecar` (existing). No new VMI feature gate.
- **Phase 2:** new gate if SIG requires one, e.g. `NetworkInterfaceQueueLimits`.

## API Examples

### Phase 1 POC

**Sidecar on existing masquerade/bridge (addresses #18012):**

8 vCPU VM, cap of 2. Interface specs are untouched — the only additions are two
annotations: one attaching the sidecar, one carrying the cap.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: windows-multi-nic
  annotations:
    # POC-only; not a stable API. Read by the hook from the VMI JSON.
    network.kubevirt.io/max-queues: "2"
    hooks.kubevirt.io/hookSidecars: |
      [
        {
          "image": "registry:5000/kubevirt/network-queue-cap:devel",
          "imagePullPolicy": "IfNotPresent",
          "args": ["--version", "v1alpha2"]
        }
      ]
spec:
  domain:
    cpu:
      cores: 8
    devices:
      networkInterfaceMultiqueue: true
      interfaces:
        - name: default
          masquerade: {}
        - name: net1
          bridge: {}
  networks:
    - name: default
      pod: {}
    - name: net1
      multus:
        networkName: net1
```

**Resulting status** (reported from the mutated domain, no extra plumbing):

```yaml
status:
  interfaces:
    - name: default
      queueCount: 2
    - name: net1
      queueCount: 2
```

### Phase 2 (later) — core API

**VM-wide cap:**

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
```

**Per-interface override:**

```yaml
devices:
  networkInterfaceMultiqueue: true
  maxNetworkInterfaceQueues: 8
  interfaces:
    - name: primary
      masquerade: {}
      queues: 8
    - name: secondary
      bridge: {}
      queues: 1
```

**Cluster default:**

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    network:
      maxInterfaceQueues: 16
```

## Alternatives

### Alternative A: Core API only, no POC

Stable fields first. **Deferred to Phase 2** per SIG: experiment before freezing API.

### Alternative B: New `queue-cap` binding plugin as the POC

Register `binding.name: queue-cap` with `domainAttachmentType: tap`.

**Rejected for the POC.** This is not a preference — it is blocked by admission.
`validateInterfaceBindingExists` requires exactly one binding per interface, and
`binding` is counted alongside `masquerade`/`bridge`, so the two can never coexist
on the same interface. A `queue-cap` binding therefore *replaces* masquerade or
bridge, and reproducing #18012 would require reimplementing masquerade inside the
plugin. Queue count is a virtio device property, not an attachment method.

It remains a reasonable vehicle for demonstrating *plugin injection* in general —
it is simply not a drop-in for masquerade/bridge, which is what #18012 needs.

### Alternative C: Automatic scaling (`vCPUs / numNICs`)

Unpredictable; rejected.

### Alternative D: Guest-only `ethtool -L`

Does not reduce domain allocation at boot; poor on Windows.

### Alternative E: Lower the global 256 cap

Does not fix multi-NIC waste; affects all users.

**Recommended:** Phase 1 sidecar POC on masquerade/bridge; Phase 2 core API.

## Scalability

Fewer virtio queues and MSI vectors when the cap is applied. Phase 1 adds one
sidecar container only on opted-in VMs. Phase 2 has no sidecar cost.

## Update/Rollback Compatibility

**Phase 1:** opt-in annotation + sidecar. Unset = today’s behavior. Remove
sidecar → queues return to `min(vCPUs, 256)` on next boot.

**Phase 2:** new fields optional and additive. Unset = today’s behavior.

## Functional Testing Approach

### Phase 1

- Unit: cap `Driver.Queues` when the annotation is `1..256`; leave unchanged on a
  missing or unparseable annotation; do not modify the interface spec.
- Unit: table-drive the interface model over `virtio-non-transitional`,
  `virtio-transitional` and `virtio` so the hook cannot regress into an
  arch-specific string match.
- E2E: Fedora VMI, 8 vCPUs, multiqueue, masquerade **and** bridge, cap `2` →
  assert `status.interfaces[].queueCount == 2` on both interfaces, and assert
  connectivity still works (proving networking was not altered).
- Regression: same VMI without the sidecar → `queueCount == vCPUs`.
- Migration: migrate a capped VMI → target domain reports the same queue count.
- Measurement (the actual point of the POC): Windows guest memory with and
  without the cap, to quantify #18012.

### Phase 2

- Unit: queue formula with cluster / VM / interface caps; webhook validation.
- E2E: `maxNetworkInterfaceQueues: 2` → `queueCount == 2`; mixed per-interface
  `queues`; `netpod` tap queue count matches domain.

## Implementation History

- 2026-08-18: Initial VEP draft proposing core API fields. Tracking kubevirt/kubevirt#18012.
- 2026-08-26: Retarget Alpha to v1.11 after removal from v1.10 tracking board.
- 2026-09-09: Per SIG network, Phase 1 is a POC rather than core API, in the
  spirit of VEP-190. Phase 2 keeps the core API design.
- 2026-09-10: Phase 1 POC is a hook sidecar, not a network binding plugin. A
  binding plugin cannot be combined with `masquerade`/`bridge` — the admitter
  requires exactly one binding per interface — so it would replace the very
  configuration #18012 reports on. The sidecar caps `driver.queues` while leaving
  attachment untouched.

## Graduation Requirements

### Alpha (v1.11) — Phase 1 POC

- [ ] Sidecar `OnDefineDomain` caps virtio `driver.queues`
- [ ] Existing `masquerade`/`bridge` networking unchanged, connectivity verified
- [ ] Cap supplied via VMI annotation (documented as POC, not stable API)
- [ ] E2E proof: vCPUs > cap → `status.interfaces[].queueCount` equals cap
- [ ] Measured guest-memory delta on the #18012 scenario
- [ ] Boot-time tap-queue gap documented
- [ ] SIG review of POC before Phase 2 API

### Beta — Phase 2 core API (TBD)

Phase 1 is a POC and does not graduate to Beta on its own. Beta means the core
API from Phase 2 has landed:

- [ ] `maxNetworkInterfaceQueues` on `Devices`
- [ ] `queues` on `Interface` (optional)
- [ ] Optional `KubeVirt.spec.configuration.network.maxInterfaceQueues`
- [ ] Validation webhooks
- [ ] virt-launcher + `netpod` tap alignment
- [ ] User documentation
- [ ] POC sidecar retired in favor of API fields

#### On-By-Default Readiness

Not applicable for Phase 1 (opt-in sidecar). Phase 2 gate policy per SIG.

### GA

After Phase 2 is stable: feature gate removed or on by default; docs and
compatibility validated; #18012 scenario unblocked.
