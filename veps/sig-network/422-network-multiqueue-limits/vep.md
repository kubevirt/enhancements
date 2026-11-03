# VEP 422: Configurable Network Interface Multi-Queue Limits

**Tracking issue:** https://github.com/kubevirt/enhancements/issues/422

**Related bug:** https://github.com/kubevirt/kubevirt/issues/18012

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required _prior to targeting to a milestone / release_.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

With `networkInterfaceMultiqueue` enabled, every virtio NIC gets one queue per guest vCPU, capped at 256. A VM cannot request a smaller number.

Phase 1 (Alpha, v1.11) is a proof of concept. It uses a [VEP 190](https://github.com/kubevirt/enhancements/issues/190) Plugin launcher sidecar hook, not the legacy `hooks.kubevirt.io/hookSidecars` annotation. The hook reads `network.kubevirt.io/max-queues` and lowers `driver.queues` on the virtio interfaces in the generated domain. The VM keeps its current masquerade or bridge interfaces. No new API fields are added.

Phase 2 is the API: `maxNetworkInterfaceQueues`, optional per-interface `queues`, and an optional cluster ceiling, applied to both the domain XML and the tap. It is not part of Alpha. Reviewers decide whether to proceed with it after the proof of concept.

## Motivation

[kubevirt/kubevirt#18012](https://github.com/kubevirt/kubevirt/issues/18012) reports an 8 vCPU, 8 GiB Windows Server 2019 VM with 10 virtio NICs and multiqueue enabled sitting at about 90% memory after boot. Guest drivers allocate buffers per queue. On Windows (NetKVM) that cost shows up as guest memory.

The API only offers multiqueue on or off, plus the hard cap of 256 in `NetworkQueuesCapacity`. The proof of concept checks whether a smaller queue count reduces that memory use before the API fields are added.

## Goals

Phase 1:

- Cap virtio `driver.queues` on an existing masquerade or bridge VM with `network.kubevirt.io/max-queues`, through a VEP 190 Plugin launcher hook.
- Report the capped count in `status.interfaces[].queueCount`.
- Compare Windows guest memory on the #18012 shape with and without the cap.

Phase 2, if reviewers proceed after the proof of concept:

- Let a VM set an upper bound on virtio NIC queues below the vCPU count.
- Allow an optional per-interface queue count.
- Allow an optional cluster-wide upper bound that a VM can only lower.
- Use one count for the domain XML and the tap device.
- Keep `min(vCPUCount, 256)` when the new fields are unset.

## Non Goals

- API fields in Alpha.
- Changing the hard cap of 256 (`MultiQueueMaxQueues`).
- Multiqueue for non-virtio models or SR-IOV.
- Changing the queue count on a running VM.
- Deriving a queue count automatically from vCPU and NIC counts.
- A network binding plugin. The admitter allows one binding per interface, so a plugin would replace masquerade or bridge and would not reproduce #18012.
- Shipping the annotation, or the legacy `Sidecar` hook, as the supported interface once the API exists.

## Definition of Users

- VM owners running multi-NIC or Windows guests with multiqueue enabled.
- Cluster administrators who want one ceiling for virtio NIC queues, if phase 2 proceeds.

## User Stories

- As a VM owner with 10 NICs on an 8 vCPU Windows VM, I want to try 2 virtio queues per NIC without giving up masquerade or bridge.
- As a VM owner, if phase 2 proceeds, I want one NIC to use more queues than the others.
- As a cluster administrator, if phase 2 proceeds, I want a default ceiling that individual VMs can only lower.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt/): phase 1 adds a Plugin launcher sidecar under `cmd/plugin-sidecars/`, same layout as `cmd/plugin-sidecars/test-launcher-hook`. Phase 2 adds the API, admission, virt-launcher, and netpod changes.

## Design

### Current behavior

When `networkInterfaceMultiqueue` is true, `NetworkQueuesCapacity` returns `min(vCPUCount, 256)`. That value is written onto every virtio interface in the domain and used to size the tap. `status.interfaces[].queueCount` reports the count taken from the domain.

### Phase 1: Plugin launcher hook

Phase 1 follows [VEP 190](https://github.com/kubevirt/enhancements/issues/190). The legacy `Sidecar` feature gate and `hooks.kubevirt.io/hookSidecars` are not used. VEP 190 is replacing that mechanism with a `Plugin` and is deprecating the old hook sidecar.

A cluster `Plugin` registers one launcher sidecar hook. The `Plugins` feature gate must be enabled. A MutatingAdmissionPolicy, referenced from the Plugin, injects the sidecar into virt-launcher and mounts `/var/run/kubevirt-plugin/network-queue-cap/`. virt-launcher calls `GuestDefinition` on that socket after it generates the domain and before libvirt defines it. The hook point is `GuestDefinition`.

The VM opts in by setting:

```yaml
network.kubevirt.io/max-queues: "<integer>"
```

The Plugin condition runs the hook only when that annotation is present. The value is an integer from 1 to 256. For each virtio interface the hook sets `driver.queues` to `min(current queues, max-queues)`. Interfaces that are not virtio are left unchanged. If the annotation is missing or not an integer in that range, the hook returns the domain unchanged.

The VM still sets `networkInterfaceMultiqueue: true` and keeps `masquerade` or `bridge`. No new VMI fields are added.

`status.interfaces[].queueCount` is filled from the domain, so it shows the capped value after the hook runs.

Limits of this phase, which the API in phase 2 is there to close:

- The annotation is not validated by admission. A bad value is ignored by the hook.
- Each opted-in VM runs an extra container.
- netpod sizes the tap from the VMI spec before `GuestDefinition` runs, so the tap can still have one queue per vCPU while the domain XML has the capped count.
- One annotation applies the same cap to every virtio NIC.

### Phase 2: API

Phase 2 is specified now and implemented later, only if reviewers decide the proof of concept is not a sufficient interface.

```go
type Devices struct {
	NetworkInterfaceMultiQueue *bool `json:"networkInterfaceMultiqueue,omitempty"`
	// Upper bound on virtio queues for each NIC when multiqueue is enabled.
	// +optional
	MaxNetworkInterfaceQueues *uint32 `json:"maxNetworkInterfaceQueues,omitempty"`
}

type Interface struct {
	// Queue count for this virtio NIC when multiqueue is enabled.
	// Still limited by the VM cap, the cluster cap, the vCPU count, and 256.
	// +optional
	Queues *uint32 `json:"queues,omitempty"`
}

type NetworkConfiguration struct {
	// Cluster-wide upper bound. A VM or interface value can only go lower.
	// +optional
	MaxInterfaceQueues *uint32 `json:"maxInterfaceQueues,omitempty"`
}
```

For each virtio NIC, with multiqueue enabled:

```
effectiveQueues = min(
  vCPUCount,
  256,
  cluster max,     // KubeVirt.spec.configuration.network.maxInterfaceQueues, if set
  VM max,          // domain.devices.maxNetworkInterfaceQueues, if set
  interface queues // interfaces[].queues, if set
)
```

Fields that are unset are left out of the `min`. Admission accepts 1 through 256 and rejects the new fields when `networkInterfaceMultiqueue` is not true.

The same helper is used for the domain XML and for the tap, including first boot before the interface has domain status. A new count takes effect the next time the VM starts. The annotation from phase 1 is not part of this API.

Phase 2 is gated by `NetworkInterfaceQueueLimits`.

## API Examples

**Phase 1.** The Plugin is cluster-scoped. The annotation goes on the VM template so it is copied onto the VMI. 8 vCPUs, cap of 2 queues on each virtio NIC:

```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: network-queue-cap
spec:
  condition: '"network.kubevirt.io/max-queues" in vmi.Annotations'
  launcherHooks:
    - sidecar:
        socketPath: /var/run/kubevirt-plugin/network-queue-cap/hook.sock
        permittedHooks:
          - GuestDefinition
  mutatingAdmissionPolicies:
    - name: network-queue-cap
```

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: windows-multi-nic
spec:
  template:
    metadata:
      annotations:
        network.kubevirt.io/max-queues: "2"
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

`status.interfaces[].queueCount` is 2 on both virtio interfaces.

**Phase 2, VM-wide cap.** The same VM without the sidecar:

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

**Phase 2, per-interface count:**

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

**Phase 2, cluster ceiling:**

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

### Sidecar as the only interface

The annotation is enough to measure one VM. It is kept as phase 1 for that measurement. It is not the long-term interface, because it is not validated, it adds a container, and it cannot set the tap queue count or a per-NIC value. Those are what phase 2 is for, if reviewers choose to implement it.

### API fields in Alpha, skipping the sidecar

Adding the fields immediately would skip the memory comparison the proof of concept is for. The API stays in phase 2.

## Scalability

Phase 1 adds a container only to virt-launcher pods selected by the Plugin's MutatingAdmissionPolicy. Phase 2 does not add a container. VMs that set a cap allocate fewer virtio queues and fewer MSI vectors.

## Update/Rollback Compatibility

Phase 1: removing the annotations and restarting the VM returns `driver.queues` to `min(vCPUCount, 256)`.

Phase 2: the new fields are optional and additive. Unset means `min(vCPUCount, 256)`. Clearing them and restarting the VM returns to that count. The phase 1 annotation is not migrated into the fields.

## Functional Testing Approach

Phase 1:

- Unit-test the hook: a value from 1 to 256 lowers virtio `driver.queues`; a missing or invalid annotation leaves the domain unchanged; a non-virtio interface is left unchanged.
- On the #18012 shape (8 vCPUs, 10 virtio NICs), compare Windows guest memory with `network.kubevirt.io/max-queues: "2"` and without the annotation.
- Record `status.interfaces[].queueCount` and the tap queue count, including the case where they differ.

Phase 2, when it is implemented:

- Unit-test the queue formula with VM, interface, and cluster values set and unset.
- Unit-test admission: values 1 through 256 are accepted; the new fields are rejected when multiqueue is off.
- E2E: `maxNetworkInterfaceQueues: 2` on an 8 vCPU VM leaves `queueCount` at 2, and the tap queue count matches the domain on first boot.

## Implementation History

- 2026-08-18: Initial draft proposing API fields. Tracks kubevirt/kubevirt#18012.
- 2026-08-26: Alpha retargeted to v1.11.
- 2026-09-09: Split into a sidecar experiment and a later API, after SIG asked to try the idea before freezing fields.
- 2026-10-05: Draft treated the API as Alpha and the sidecar as a non-deliverable.
- 2026-10-07: Phase 1 (Alpha) is a VEP 190 Plugin launcher sidecar and `network.kubevirt.io/max-queues`. The hook point is `GuestDefinition`. The legacy hook sidecar is not used. The API fields stay in the VEP as phase 2, for after the proof of concept.

## Graduation Requirements

### Alpha (v1.11)

- [ ] Plugin launcher sidecar hook that honors `network.kubevirt.io/max-queues`
- [ ] Virtio `driver.queues` capped for a VM that keeps masquerade or bridge
- [ ] `status.interfaces[].queueCount` shows the capped value
- [ ] Memory comparison on the #18012 VM shape, with and without the cap
- [ ] Note of whether the tap queue count matches the domain

No new API fields in Alpha.

### Later stage: API

Started only if reviewers decide the proof of concept is not a sufficient interface.

- [ ] Feature gate `NetworkInterfaceQueueLimits`
- [ ] `maxNetworkInterfaceQueues` on `Devices`
- [ ] Optional `queues` on `Interface`
- [ ] Optional `KubeVirt.spec.configuration.network.maxInterfaceQueues`
- [ ] Admission validation as described above
- [ ] Domain XML and netpod tap queues use the same count
- [ ] User documentation
- [ ] The phase 1 annotation is no longer the supported interface

#### On-By-Default Readiness

E2E coverage for the VM cap, the per-interface value, the cluster ceiling, and migration. The feature does not depend on a sidecar.

### GA

- [ ] Feature gate removed
- [ ] A VM like the one in #18012 can set the queue cap through the API
