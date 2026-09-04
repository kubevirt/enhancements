# VEP #362: Guest Termination Condition

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

KubeVirt tells users whether a VMI is running, stopped, or failed, but it does
not retain the reason why the guest terminated. Some of that information exists
briefly as a hypervisor lifecycle event in virt-launcher. It is lost once the
launcher pod and VMI are gone.

This VEP adds a `GuestTerminated` condition to the VMI and mirrors it to the VM.
The condition is `False` while the guest is active and `True` after KubeVirt
observes a supported termination reason. If the domain is no longer active but
the reason is unavailable, the condition is `Unknown` instead of guessing.

The reason uses KubeVirt terminology rather than exposing libvirt event names.
This keeps the API useful if the hypervisor implementation changes later.

## Motivation

There are practical differences between a guest running `shutdown now`, a guest
kernel panic, an unexpected QEMU stop, and a shutdown requested by KubeVirt.
Today those cases tend to converge on the same broad VMI phases and readiness
states.

`Ready=False` answers whether the VMI is available. It does not answer why it
stopped. `GuestNotRunning`, for example, is also used while a guest is starting.
`RunStrategy` and `rebootPolicy` decide what KubeVirt should do next; neither is
a record of what happened.

Kubernetes events are useful for notification and investigation, but they are
not enough as the only API. They expire, may be aggregated, and require a
consumer to watch and correlate a separate stream. A condition gives users and
controllers a value they can query directly. Mirroring it to the VM keeps the
answer available after the terminated VMI has been deleted.

This information can be used by higher-level platforms for recovery policy,
user-facing stop reasons, alerting, and other lifecycle decisions that are not
owned by KubeVirt's `RunStrategy`.

## Goals

- Report whether the guest represented by a VMI has terminated.
- Give a terminated guest a normalized, machine-readable reason when KubeVirt
  observed one.
- Keep the latest condition on the owning VM after the VMI is deleted.
- Reset stale termination state when a new guest or replacement VMI starts.
- Preserve a useful termination reason across later hypervisor events that no
  longer carry it.
- Emit Kubernetes events and metrics for supported termination reasons.
- Keep the public API independent of libvirt.

## Non Goals

- Change `RunStrategy`, `rebootPolicy`, or their restart behavior.
- Add a recovery policy to KubeVirt.
- Make `Ready` carry termination history.
- Diagnose application-level failures inside a running guest.
- Attribute node loss when virt-launcher and its hypervisor event stream are
  lost before they can report an outcome.
- Claim cryptographic or transactional proof of which actor caused the final
  ACPI poweroff.

## Definition of Users

- VM owners checking why their VM stopped.
- Cluster administrators and SREs investigating VM lifecycle incidents.
- Controllers and platforms built on top of KubeVirt.
- Monitoring and UI systems presenting guest termination information.

## User Stories

- As a VM owner, I want to see that the guest OS requested shutdown so that I
  do not mistake an expected stop for an infrastructure failure.
- As an administrator, I want a guest crash to be distinguishable from an
  orderly shutdown.
- As a higher-level controller, I want to read the latest termination reason
  from the VM after its VMI has disappeared.
- As a KubeVirt developer, I want this contract expressed in KubeVirt terms so
  that changing the hypervisor backend does not change the public API.

## Repos

kubevirt/kubevirt

## Design

### Condition contract

No new status structure is introduced. The existing VMI and VM condition types
gain `GuestTerminated`:

```go
const (
    VirtualMachineInstanceGuestTerminated VirtualMachineInstanceConditionType = "GuestTerminated"
    VirtualMachineGuestTerminated         VirtualMachineConditionType         = "GuestTerminated"
)
```

For a VMI, the condition has the following meaning:

| Status | Meaning |
|--------|---------|
| `False` | The current guest incarnation has not terminated. |
| `True` | The current guest incarnation has terminated and KubeVirt observed a supported reason. |
| `Unknown` | The domain is no longer active, but KubeVirt could not determine a supported termination reason. |

`GuestTerminated=True` is not an event pulse. It remains true for the terminal
remainder of that VMI's current guest incarnation. Starting a new incarnation
resets it to `False`.

Before KubeVirt has observed an active domain, the condition may be absent.
When the feature is disabled, the condition is removed.

Standard condition timestamps are used. Both timestamps are set when the
condition is created or its status or reason changes. Reconciliation leaves the
condition untouched while status and reason remain the same. There is no
second, custom termination timestamp.

### Reasons

When the condition is `False`, it uses:

| Reason | Message |
|--------|---------|
| `GuestNotTerminated` | `Guest has not terminated` |

When the condition is `True`, it uses one of these normalized reasons:

| Reason | Message | Meaning |
|--------|---------|---------|
| `GuestShutdown` | `Guest requested shutdown of the virtual machine` | The hypervisor reported a guest-completed shutdown and KubeVirt had no pending platform termination intent. |
| `PlatformRequestedShutdown` | `Platform requested shutdown of the virtual machine` | KubeVirt had already requested termination when the shutdown was observed. |
| `HostShutdown` | `Host requested shutdown of the virtual machine` | The hypervisor reported a host-side shutdown without a matching KubeVirt termination intent. |
| `HostStoppedFailed` | `Host observed the virtual machine stop unexpectedly` | The hypervisor reported that the domain stopped unexpectedly or failed. |
| `GuestCrashed` | `Guest crashed` | KubeVirt observed a guest panic or crash that ended the guest incarnation. |

When the condition is `Unknown`, it uses `TerminationReasonUnknown` with the
message `Guest termination reason is unknown`.

The values above are KubeVirt API values. They are not a promise to expose a
particular libvirt detail indefinitely.

### Observing termination

virt-launcher is the first KubeVirt component that sees hypervisor lifecycle
events. It normalizes supported events into an internal termination event and
passes that information through the domain notification to virt-handler.
virt-handler owns VMI status and updates the public condition.

The initial libvirt backend uses this mapping:

| Libvirt event | Libvirt detail | Candidate reason |
|---------------|----------------|------------------|
| `DOMAIN_EVENT_SHUTDOWN` | `DOMAIN_EVENT_SHUTDOWN_GUEST` | `GuestShutdown`, or `PlatformRequestedShutdown` when platform intent takes precedence |
| `DOMAIN_EVENT_SHUTDOWN` | `DOMAIN_EVENT_SHUTDOWN_HOST` | `HostShutdown`, or `PlatformRequestedShutdown` when platform intent takes precedence |
| `DOMAIN_EVENT_CRASHED` | `DOMAIN_EVENT_CRASHED_PANICKED` | `GuestCrashed` |
| `DOMAIN_EVENT_CRASHED` | `DOMAIN_EVENT_CRASHED_CRASHLOADED` | records a guest crash signal, but does not by itself prove that the guest incarnation has terminated |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_CRASHED` | `GuestCrashed` |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_FAILED` | `HostStoppedFailed` |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_DESTROYED` | no new reason |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_SHUTDOWN` | no new reason |
| `DOMAIN_EVENT_STOPPED` | `DOMAIN_EVENT_STOPPED_MIGRATED` | no new reason |

The condition becomes `True` only when the observed lifecycle state means that
the guest incarnation has ended. An early crash signal may be retained as the
candidate reason until a terminal state is observed. This is especially
important for `CRASHED_CRASHLOADED`, where a crash kernel can still be running.

Some useful events are followed by events with less information. A normal guest
shutdown, for example, commonly produces `SHUTDOWN_GUEST` followed by
`STOPPED_SHUTDOWN`. A panic can produce `CRASHED_PANICKED` followed by
`STOPPED_CRASHED`. The later event must not erase the reason already observed.

### Platform termination intent

An ACPI shutdown requested by KubeVirt and a genuine `shutdown now` converge on
the same final operation: the guest writes ACPI S5 and QEMU reports a guest
shutdown. The final event does not contain a request identifier, so exact
causality cannot be recovered from that event alone.

KubeVirt records a pending platform termination intent immediately before
`SignalShutdownVMI` asks libvirt to send the ACPI power-button request. The
intent is scoped to the current domain incarnation and consumed by the first
matching shutdown event. It is cleared when the request fails, a new domain
incarnation starts, or the intent expires.

The freshness period is the larger of two minutes and the VMI termination grace
period plus 30 seconds. The longer value allows guests with a long graceful
shutdown period to finish without losing the platform intent.

While an intent is pending, `PlatformRequestedShutdown` deliberately takes
precedence over `GuestShutdown` or `HostShutdown`. Reaching
`SignalShutdownVMI` means KubeVirt has already committed to terminating that
VMI. In the usual deletion path the VMI has a `deletionTimestamp`, and that
deletion cannot be cancelled. If the guest independently starts shutting down
at the same time, the controlling lifecycle operation is still the KubeVirt
termination.

This reason means "KubeVirt had requested termination when shutdown was
observed." It does not claim that KubeVirt can prove its ACPI request caused the
guest's final S5 write. Obtaining that proof would require a request token to be
carried through the guest and returned with the final shutdown signal.

### Reliable delivery and lifecycle scope

The condition is meant to survive the transient source event, so a supported
termination event cannot exist only in the bounded, best-effort libvirt event
queue.

virt-launcher latches termination-classifying lifecycle data in synchronized
per-launcher state before attempting to enqueue the generic notification. It
then signals the notification loop independently. Queue saturation may coalesce
notifications, but it must not discard the retained termination reason.

The retained data belongs to one guest incarnation. A libvirt
`DOMAIN_EVENT_STARTED` clears it as a fast path. Reconciliation also compares
the active runtime identity with the identity stored alongside the retained
event. If the identity changed, the old event is cleared even if the `STARTED`
callback was dropped.

Later low-signal events from the same incarnation keep the retained reason. If
the domain reaches a terminal state and no reason was retained, virt-handler
sets `GuestTerminated=Unknown` rather than leaving a stale `False` condition or
inventing a reason.

### VMI condition update

virt-handler derives the condition from the latest domain observation:

1. A supported terminal reason produces `GuestTerminated=True`.
2. An active domain produces `GuestTerminated=False`.
3. A terminal domain without a supported reason produces
   `GuestTerminated=Unknown`.
4. A later low-signal update from the same incarnation does not erase an
   already recorded reason.

The normal KubeVirt condition manager performs the update. It leaves an existing
condition untouched while status and reason remain the same.

When the feature is enabled, the reconciliation that moves a VMI to a final
phase must also leave it with `GuestTerminated=True` or
`GuestTerminated=Unknown`. Updating this condition must not be skipped only
because the VMI has already entered a final phase. Other final VMI status is not
recomputed in that path.

### VM condition synchronization

The owning VM mirrors the VMI's `GuestTerminated` condition. The VM copy has a
specific synchronization path rather than relying only on the generic
condition-copy loop:

- While a VMI exists and carries the condition, copy it to the VM.
- If a replacement VMI exists but does not yet carry the condition, remove the
  previous VM condition. This prevents a reason from the old VMI from looking
  current.
- Once the VMI is gone, retain the condition last copied from it.
- A new VMI's `False`, `True`, or `Unknown` condition replaces the retained
  value normally.

VM-owned VMIs already carry `VirtualMachineControllerFinalizer`.
virt-controller updates VM status before removing that finalizer. If the VM
status update fails, the finalizer remains and the copy is retried. Therefore a
VMI carrying `GuestTerminated` cannot disappear before virt-controller has had
the opportunity to persist it on the VM.

For a stopped VM without a VMI, `GuestTerminated=True` describes the latest
guest that belonged to that VM. It stops being current as soon as a replacement
VMI appears, at which point the dedicated synchronization above removes or
replaces it.

### Kubernetes events

When the VMI condition first becomes `True`, or its normalized reason changes,
virt-handler emits a Kubernetes event on the VMI.

- Expected shutdown reasons use event type `Normal`.
- Guest crashes and unexpected host-side stops use event type `Warning`.
- The event reason matches the normalized condition reason.
- The message is the same human-readable text used by the condition.

Events remain complementary observability. Consumers that need current state
should read the condition.

### Metrics

virt-handler exposes:

```text
kubevirt_vmi_guest_os_termination_total{namespace, name, reason}
```

The counter increments when `GuestTerminated` first becomes `True` or changes to
a different supported termination reason. Per-VMI label sets are removed during
final VMI cleanup so the virt-handler process does not retain them indefinitely.
No raw hypervisor messages or other unbounded details are used as labels.

### Feature gate

The initial implementation is guarded by the Alpha `GuestTermination` feature
gate. When disabled, KubeVirt does not publish the VMI or VM condition and does
not emit the termination event or metric.

Internal observation may still take place because it belongs to the existing
virt-launcher domain event path, but it has no user-visible effect while the
gate is disabled.

## API Examples

An active guest:

```yaml
status:
  conditions:
  - type: GuestTerminated
    status: "False"
    reason: GuestNotTerminated
    message: Guest has not terminated
```

A guest that ran `shutdown now`:

```yaml
status:
  phase: Succeeded
  conditions:
  - type: GuestTerminated
    status: "True"
    reason: GuestShutdown
    message: Guest requested shutdown of the virtual machine
    lastProbeTime: "2026-09-04T12:00:00Z"
    lastTransitionTime: "2026-09-04T12:00:00Z"
```

A shutdown observed while KubeVirt was terminating the VMI:

```yaml
status:
  conditions:
  - type: GuestTerminated
    status: "True"
    reason: PlatformRequestedShutdown
    message: Platform requested shutdown of the virtual machine
```

A domain that is terminal without a supported reason:

```yaml
status:
  conditions:
  - type: GuestTerminated
    status: "Unknown"
    reason: TerminationReasonUnknown
    message: Guest termination reason is unknown
```

After the VMI is deleted, the owning VM retains the latest condition:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: fedora
  namespace: default
status:
  printableStatus: Stopped
  conditions:
  - type: GuestTerminated
    status: "True"
    reason: GuestShutdown
    message: Guest requested shutdown of the virtual machine
```

## Alternatives

### Use only `Ready` and VMI phase

`Ready` and phase describe broad availability and lifecycle outcomes. They do
not distinguish an orderly guest shutdown from a guest crash or unexpected
host-side stop. Extending `Ready` with this history would also mix availability
with termination attribution.

### Use only Kubernetes events

The source information is event-like, but the use case is not only audit. A
controller may need the latest reason after it starts, reconnects, or observes a
VM whose VMI has already disappeared. Kubernetes events are retained for a
limited time and are not a suitable state API.

### Add dedicated termination fields to VMI and VM status

An earlier version of this VEP proposed `terminationState` on the VMI and
`lastTerminationState` on the VM. That gives stronger typing and can include the
source VMI UID directly, but it introduces a separate status structure for a
small finite state that fits the standard condition model.

The condition is valid as current state when its lifecycle is defined clearly:
it is `False` for the active incarnation, `True` for its terminal remainder,
and reset when a new incarnation or replacement VMI starts.

### Add a separate platform-termination condition

Another option is to report guest termination and platform termination intent
as two independent conditions. That avoids combining observed shutdown with
intent, but leaves every consumer to perform the same correlation and adds
another condition to VM status.

The proposed design keeps one condition and defines platform intent precedence
inside KubeVirt. Its limitation is documented rather than presented as exact
physical causality.

### Add or extend a poweroff policy

A poweroff policy could decide whether a guest poweroff stops or restarts the
domain. It would not by itself expose why the poweroff happened. It also cannot
distinguish a genuine in-guest shutdown from an ACPI shutdown requested by
KubeVirt because both end with the guest writing ACPI S5.

### Require exact causal attribution from the hypervisor

Libvirt can remember that its shutdown API was called, but it faces the same
correlation problem as KubeVirt: QEMU later reports only that the guest completed
shutdown. Exact attribution would require a token propagated through the guest,
which is not available in the current ACPI shutdown protocol.

## Scalability

Each VMI and VM gains at most one condition. Condition updates occur only when
the state or reason changes.

virt-launcher retains one small termination record for the current domain
incarnation. The Prometheus counter uses one series per observed VMI and reason,
following existing per-VMI metric patterns, and removes those label sets during
VMI cleanup.

## Update/Rollback Compatibility

This is an additive condition type and is backward compatible. Older clients
ignore condition types they do not recognize. Consumers must treat the
condition as optional because it may be absent before the domain is observed,
while the feature is disabled, or during a mixed-version rollout.

After rollback or disabling the gate, controllers stop publishing the condition
and remove it when they next reconcile the affected objects. A value can remain
temporarily on an object that is no longer being reconciled, which is why clients
must not assume the condition is always present.

## Functional Testing Approach

Unit coverage will verify:

- Mapping supported hypervisor lifecycle events to normalized reasons.
- `False`, `True`, and `Unknown` VMI condition transitions.
- A terminal VMI receives `True` or `Unknown` before its finalizers can be
  removed, including when the classifying event arrives late.
- Platform intent precedence, expiry, consumption, and cleanup on start.
- A crash while platform intent is pending does not misclassify the next
  incarnation.
- Low-signal terminal events preserve a previously observed reason.
- Termination state is retained before the best-effort event queue and survives
  queue saturation.
- A changed runtime incarnation clears stale state even when the `STARTED`
  callback is missed.
- VM status is updated before the VM controller finalizer is removed from the
  terminated VMI.
- A replacement VMI removes or replaces the previous VM condition.
- Metrics increment once per condition transition and are cleaned up with the
  VMI.
- Disabling the feature gate removes all user-visible behavior.

Functional tests will cover:

- `shutdown now` inside a Linux guest.
- VMI deletion and graceful platform-requested shutdown.
- Guest kernel panic when the test environment supports a panic device.
- Unexpected QEMU termination.
- VMI deletion followed by creation of a replacement VMI, verifying that the VM
  condition does not retain the old reason.

Node loss is tested as an unclassified outcome where no hypervisor event can be
delivered.

## Implementation History

- 2026-08-26: Draft implementation opened in
  [kubevirt/kubevirt#18948](https://github.com/kubevirt/kubevirt/pull/18948).
- 2026-09-04: VEP revised around the `GuestTerminated` condition contract and
  lifecycle-scoped event retention.

## Graduation Requirements

### Alpha

- [ ] `GuestTermination` feature gate added.
- [ ] VMI and VM `GuestTerminated` condition types added.
- [ ] Initial normalized reason set implemented.
- [ ] Termination-classifying events retained before any best-effort queue.
- [ ] Retained state scoped to a domain incarnation with a reconciliation
      fallback when `STARTED` is missed.
- [ ] Dedicated VM synchronization preserves the terminal condition after VMI
      deletion and clears it for a replacement VMI.
- [ ] Kubernetes events and Prometheus metrics implemented.
- [ ] Unit and functional tests cover the supported paths and lifecycle reset.

### Beta

- [ ] Reason semantics and platform-intent precedence have remained stable for
      at least one release.
- [ ] Upgrade and rollback coverage includes mixed virt-launcher, virt-handler,
      and virt-controller versions.
- [ ] Operational feedback shows no unresolved stale-condition or silent event
      loss cases.
- [ ] User documentation describes condition semantics and known attribution
      limits.

#### On-By-Default Readiness

- [ ] The condition does not change existing VM restart behavior.
- [ ] Metrics and condition updates have acceptable control-plane and
      monitoring overhead at scale.
- [ ] Consumers can safely handle `Unknown` and condition absence.

### GA

- [ ] The normalized reason set and lifecycle contract are considered stable.
- [ ] No known condition-retention issue can leak a previous VMI or domain
      incarnation into the current VM state.
- [ ] The feature gate is removed according to the KubeVirt feature lifecycle.
