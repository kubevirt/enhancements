# VEP #362: Guest Termination State

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: 1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt can observe several different ways in which a guest or its domain stops:
shutdown initiated from inside the guest, KubeVirt/API requested shutdown,
hypervisor-side stop, and guest crash/panic. Today these cases are mostly
reflected through generic VMI phase and readiness condition. For example, a guest
shutdown can eventually appear as `phase: Succeeded` with
`Ready=False, reason=GuestNotRunning`, while other stop paths may surface as
generic failure, pod termination, or Kubernetes events.

This proposal introduces a normalized guest termination state in the KubeVirt API.
The state records the observed reason, message, and timestamp for a terminated
guest/domain lifecycle. A VM-level copy also records the source VMI UID so the
reason remains meaningful after the VMI object is deleted or replaced. It is
intended as a durable, machine-readable source of truth for users and
higher-level controllers that need to understand why a VM stopped.

The public API is hypervisor-agnostic. Current libvirt lifecycle events are used
only as one implementation source for populating the normalized reasons.

## Motivation

Users and automation often need to distinguish expected guest behavior from
infrastructure or platform behavior. Examples:

- A user runs `shutdown now` inside the guest and expects to understand that the
  VM stopped because the guest requested it.
- A user or controller requests a VM stop through KubeVirt and expects that stop
  to be distinguishable from a shutdown initiated inside the guest.
- A guest kernel panic should be observable as a guest crash instead of being
  conflated with ordinary shutdown.
- Higher-level platforms built on KubeVirt need a stable status surface to decide
  whether to recover, report, alert, bill, or leave an instance stopped.

Prior discussions and features address adjacent behavior but not a structured
termination reason API:

- Guest shutdown behavior and RunStrategy decide what KubeVirt should do after a
  guest stops, but not why the guest stopped.
- `rebootPolicy` makes guest reboot visible to KubeVirt when requested, but does
  not expose the reason for a terminated guest/domain lifecycle.
- Guest panic support emits Kubernetes events ([#16666](https://github.com/kubevirt/kubevirt/pull/16666))
  and metrics ([#17836](https://github.com/kubevirt/kubevirt/pull/17836)) for one crash class,
  but there is no general termination state covering shutdown and stop causes.

## Goals

- Add a normalized guest/domain termination state to VMI status.
- Add a VM-level last termination state copied from the latest relevant VMI so
  the reason remains visible after the VMI is deleted.
- Distinguish at least:
  - guest-initiated shutdown
  - KubeVirt/API requested shutdown
  - host/hypervisor requested shutdown
  - host/hypervisor observed unexpected stop/failure
  - guest crash/panic
- Keep the public API independent from libvirt event names.
- Preserve an observed termination reason across later low-signal terminal
  domain notifications in the same domain lifecycle.
- Emit a Kubernetes event when a guest termination state is observed.
- Expose metrics for observed guest termination reasons.
- Guard the initial implementation behind a feature gate.

## Non Goals

- This proposal does not change RunStrategy semantics.
- This proposal does not change `rebootPolicy` semantics.
- This proposal does not introduce a recovery policy or automatic restart policy.
- This proposal does not make `Ready` condition carry historical termination state.
- This proposal does not guarantee attribution for node crashes where the
  virt-launcher process and hypervisor event stream are lost before an event can
  be observed.
- This proposal does not expose libvirt event names as KubeVirt API values.

## Definition of Users

- VM owners who need to understand why their VM stopped.
- Cluster administrators and SREs investigating VM lifecycle incidents.
- Higher-level controllers and platforms built on KubeVirt.
- Monitoring and UI systems that need a stable source of VM stop attribution.
- KubeVirt developers debugging guest/domain lifecycle behavior.

## User Stories

- As a VM owner, I want to see that my VM stopped because the guest OS requested
  shutdown, so I can distinguish expected in-guest behavior from a failure.
- As a cluster administrator, I want guest crashes to be distinguishable from
  platform-requested shutdown, so alerts and dashboards can report the right
  cause.
- As a higher-level controller, I want a machine-readable stop reason after the
  VMI has disappeared, so I can make recovery decisions based on the last VMI
  lifecycle.
- As a KubeVirt developer, I want a normalized status field instead of relying on
  libvirt-specific lifecycle detail names, so the API can survive future
  hypervisor implementation changes.

## Repos

kubevirt/kubevirt

## Design

### API

Introduce a VMI-scoped termination state:

```go
type VirtualMachineInstanceTerminationState struct {
    // Reason is a short, stable, machine-readable termination reason.
    // +optional
    Reason VirtualMachineInstanceTerminationReason `json:"reason,omitempty"`

    // Message is a human-readable explanation of the observed termination.
    // +optional
    Message string `json:"message,omitempty"`

    // Timestamp is the time at which KubeVirt observed the termination event.
    // +optional
    Timestamp metav1.Time `json:"timestamp,omitempty"`
}

type VirtualMachineInstanceTerminationReason string

const (
    VirtualMachineInstanceTerminationReasonGuestShutdown              VirtualMachineInstanceTerminationReason = "GuestShutdown"
    VirtualMachineInstanceTerminationReasonPlatformRequestedShutdown  VirtualMachineInstanceTerminationReason = "PlatformRequestedShutdown"
    VirtualMachineInstanceTerminationReasonHostShutdown               VirtualMachineInstanceTerminationReason = "HostShutdown"
    VirtualMachineInstanceTerminationReasonHostStoppedFailed          VirtualMachineInstanceTerminationReason = "HostStoppedFailed"
    VirtualMachineInstanceTerminationReasonGuestCrashed               VirtualMachineInstanceTerminationReason = "GuestCrashed"
)
```

Add it to VMI status:

```go
type VirtualMachineInstanceStatus struct {
    // TerminationState reports the observed termination state for this VMI
    // lifecycle.
    // +optional
    TerminationState *VirtualMachineInstanceTerminationState `json:"terminationState,omitempty"`
}
```

Add a VM-level copy that includes the VMI UID:

```go
type VirtualMachineTerminationState struct {
    // VMIUID identifies the VMI lifecycle this termination state came from.
    // +optional
    VMIUID types.UID `json:"vmiUID,omitempty"`

    // Reason is a short, stable, machine-readable termination reason.
    // +optional
    Reason VirtualMachineInstanceTerminationReason `json:"reason,omitempty"`

    // Message is a human-readable explanation of the observed termination.
    // +optional
    Message string `json:"message,omitempty"`

    // Timestamp is the time at which KubeVirt observed the termination event.
    // +optional
    Timestamp metav1.Time `json:"timestamp,omitempty"`
}

type VirtualMachineStatus struct {
    // LastTerminationState reports the latest observed termination state copied
    // from a VMI owned by this VM.
    // +optional
    LastTerminationState *VirtualMachineTerminationState `json:"lastTerminationState,omitempty"`
}
```

The VMI field is the authoritative state for a specific VMI lifecycle. The VM
field is a durable copy for user-facing inspection after the VMI is deleted. The
`vmiUID` field prevents consumers from accidentally treating a previous VMI
lifecycle as the current one.

### Reason semantics

`GuestShutdown` means the guest OS requested shutdown/poweroff and KubeVirt
observed a normal guest termination.

`PlatformRequestedShutdown` means KubeVirt requested domain shutdown as part of
VM/VMI stop or deletion flow and the observed termination corresponds to that
request.

`HostShutdown` means the hypervisor host side requested shutdown or destroy, but
KubeVirt did not associate the event with a tracked platform shutdown request.

`HostStoppedFailed` means the hypervisor reported that the domain stopped
unexpectedly or failed without a more specific guest crash signal.

`GuestCrashed` means the guest reported or triggered a crash/panic signal.

The exact names are open to SIG review. The important contract is that the
reason values are normalized KubeVirt concepts, not libvirt event names.

### Event collection

The initial implementation can derive the normalized state from the existing
virt-launcher to virt-handler domain status flow.

virt-launcher records normalized termination events in the internal domain status
sent to virt-handler. This internal value is not the public API. It is a
transport detail used so virt-handler can update the VMI/VM API, emit events, and
increment metrics.

Current libvirt mapping:

| Libvirt event | Libvirt detail | Normalized reason |
|---------------|----------------|-------------------|
| `DOMAIN_EVENT_SHUTDOWN` | `DOMAIN_EVENT_SHUTDOWN_GUEST` | `GuestShutdown`, unless matched to a pending KubeVirt shutdown intent |
| `DOMAIN_EVENT_SHUTDOWN` | `DOMAIN_EVENT_SHUTDOWN_HOST` | `HostShutdown`, unless matched to a pending KubeVirt shutdown intent |
| `DOMAIN_EVENT_CRASHED` | `DOMAIN_EVENT_CRASHED_PANICKED` | `GuestCrashed` |
| `DOMAIN_EVENT_CRASHED` | `DOMAIN_EVENT_CRASHED_CRASHLOADED` | `GuestCrashed` |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_CRASHED` | `GuestCrashed` |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_FAILED` | `HostStoppedFailed` |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_DESTROYED` | no new reason |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_SHUTDOWN` | no new reason |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_MIGRATED` | no new reason |

`DOMAIN_EVENT_STOPPED_CRASHED` may refine or overwrite a preceding
`DOMAIN_EVENT_CRASHED_*` event because it confirms that the domain has stopped.

`PlatformRequestedShutdown` is detected by recording a pending KubeVirt shutdown
intent when virt-launcher issues the shutdown request on behalf of KubeVirt. The
intent is consumed by the next matching shutdown event. The intent is considered
fresh for the larger of a default freshness window and the VMI termination grace
period plus a small margin. A stale intent is cleared and ignored.

### Sticky internal domain termination event

Some domain lifecycles produce a useful first event followed by a later terminal
event that does not carry enough information by itself. For example:

- guest shutdown: `SHUTDOWN_GUEST` followed by `STOPPED_SHUTDOWN`
- platform-requested shutdown: `SHUTDOWN_GUEST` or `SHUTDOWN_HOST` matched to a
  pending platform intent, followed by `STOPPED_SHUTDOWN` or `STOPPED_DESTROYED`
- guest crash: `CRASHED_PANICKED` followed by `STOPPED_CRASHED`

To avoid losing the useful reason during the final domain notification,
virt-launcher keeps the last observed normalized termination event in its
per-launcher metadata cache. When a later domain notification does not map to a
new normalized reason, virt-launcher attaches the cached event to the domain
status sent to virt-handler.

The cached event is scoped to one virt-launcher/domain lifecycle. It is cleared
when a `DOMAIN_EVENT_STARTED` event is observed. The same start event also clears
any pending platform termination intent. This prevents a termination reason from
leaking into a fresh domain incarnation.

If a low-signal terminal event is observed without any cached normalized event,
KubeVirt does not infer a termination reason from it. This avoids turning
ambiguous terminal states into misleading user-visible attribution.

### VM propagation

When a VMI owned by a VM has `status.terminationState`, virt-controller copies it
to `vm.status.lastTerminationState` with the source VMI UID.

The VM field is intentionally named `lastTerminationState` because it is
historical. It is not a statement that the current VMI, if one exists, is
terminated.

### Kubernetes events

When KubeVirt observes a normalized termination state, it emits a Kubernetes
event on the VMI:

- Event type `Normal` for expected shutdown reasons.
- Event type `Warning` for crash or unexpected stop reasons.
- Event reason is the normalized termination reason.
- Event message is the same human-readable message used in status.

### Metrics

Expose a counter for observed guest termination states:

```text
kubevirt_vmi_guest_os_termination_total{namespace, name, reason}
```

The metric follows the precedent of guest panic metrics and allows operators to
track fleet-wide termination causes. Implementations should avoid adding
high-cardinality detail labels such as raw hypervisor messages.

## API Examples

Guest shutdown visible on the VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: fedora
  namespace: default
  uid: 7d5f0d4d-3df8-442f-bd64-9ee9d0f6fb8e
status:
  phase: Succeeded
  terminationState:
    reason: GuestShutdown
    message: Guest requested shutdown of the virtual machine
    timestamp: "2026-06-15T12:00:00Z"
```

The same reason copied to the VM after the VMI is gone:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: fedora
  namespace: default
status:
  printableStatus: Stopped
  lastTerminationState:
    vmiUID: 7d5f0d4d-3df8-442f-bd64-9ee9d0f6fb8e
    reason: GuestShutdown
    message: Guest requested shutdown of the virtual machine
    timestamp: "2026-06-15T12:00:00Z"
```

Guest crash:

```yaml
status:
  terminationState:
    reason: GuestCrashed
    message: Guest crashed
    timestamp: "2026-06-15T12:05:00Z"
```

Platform requested shutdown:

```yaml
status:
  terminationState:
    reason: PlatformRequestedShutdown
    message: Platform requested shutdown of the virtual machine
    timestamp: "2026-06-15T12:10:00Z"
```

## Alternatives

### Use the Ready condition

`Ready` already reports `GuestNotRunning`, `PodTerminating`, and other
availability states. It is not a good fit for termination attribution because it
is transient, availability-oriented, and can copy pod readiness reasons directly.
`GuestNotRunning` also covers startup and other non-running windows, not only
guest termination.

### Add a GuestTerminated condition

A condition is useful for current state, but less suitable for durable historical
state. It also creates awkward behavior across VM restarts: a `True` condition can
look current even when it describes a previous VMI lifecycle. A structured status
field with source `vmiUID` is clearer for "last observed termination".

### Store the reason only on the VM

The VM is the best user-facing place after the VMI is deleted, but the VMI is the
natural authoritative object for a specific domain lifecycle. Keeping both fields
allows controllers to reason about the current VMI lifecycle while still
preserving a durable VM-level copy.

### Use only Kubernetes events or metrics

Events and metrics are useful for observability, but they are not a stable API
source of truth for controllers. Events can expire and metrics are eventually
scraped. Status is the right API surface for current and last observed state.

## Scalability

The status fields are small and updated only when KubeVirt observes a guest/domain
termination event. The metric adds one counter series per observed VMI and reason,
matching existing KubeVirt per-VMI metric patterns. Implementations should avoid
labels derived from raw messages, domain names, or crash payloads.

## Update/Rollback Compatibility

This is an additive status API and is backward compatible.

During upgrade, new KubeVirt components begin populating the optional status
fields when the feature gate is enabled. Older clients ignore the new fields.

During rollback, older KubeVirt components stop updating the fields. Existing
status values may remain until the object is updated or recreated. Consumers must
treat the fields as optional.

The initial implementation should be guarded by a feature gate, tentatively named
`GuestTermination`.

## Functional Testing Approach

- Unit test mapping from hypervisor lifecycle events to normalized termination
  reasons.
- Unit test pending KubeVirt shutdown intent matching and stale intent cleanup.
- Unit test that later low-signal domain notifications preserve the cached
  normalized termination event.
- Unit test that domain start clears stale in-launcher termination state and
  pending platform termination intent.
- Unit test VMI status updates for each normalized reason.
- Unit test VM propagation from VMI `terminationState` to VM
  `lastTerminationState`.
- Functional test guest-initiated shutdown from inside a Linux guest.
- Functional test KubeVirt/API requested VM stop or VMI deletion.
- Functional test guest panic when panic devices are available.
- Functional test unexpected hypervisor stop/failure path where practical.
- Verify no status, event, or metric is emitted when the feature gate is disabled.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate added.
- [ ] VMI `status.terminationState` API added.
- [ ] VM `status.lastTerminationState` API added.
- [ ] Initial libvirt event mapping implemented.
- [ ] Internal domain termination event cache preserves normalized reasons across
      low-signal terminal notifications.
- [ ] Internal domain termination event cache is cleared on domain start.
- [ ] Kubernetes event emitted for observed termination states.
- [ ] Prometheus metric emitted for observed termination states.
- [ ] Unit tests cover all supported normalized reasons.
- [ ] Functional tests cover guest shutdown, platform shutdown, and guest crash
      where supported by the test environment.

### Beta

- [ ] TBD

### GA

- [ ] TBD
