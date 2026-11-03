# VEP #366: Multiple concurrent VNC connections per VMI

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: TBD (to be set during release planning)
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt permits one VNC connection per VirtualMachineInstance. When a new
client connects to the `/vnc` subresource, virt-handler tears down the
previous stream; with `preserveSession=true` it instead rejects the new
request with 503. Either way, never two connections at once.

This VEP adds opt-in support for multiple concurrent VNC connections to the
same VMI, guarded by the `MultipleVNCConnections` feature gate (alpha, off by
default) and bounded by a cluster-configurable per-VMI cap
(`maxVNCConnectionsPerVMI`, default 1). With the gate off, or the cap at its
default, behavior is unchanged.

## Motivation

More than one person (or component) sometimes needs the same VM console at
the same time:

- An instructor watches or assists on a trainee's VM console while the
  trainee keeps their own session.
- A support engineer shadows a session to diagnose a problem without
  disconnecting the console's owner.
- A management UI renders a live console thumbnail while a full interactive
  console is open on the same VM.

The request is not new (kubevirt/kubevirt#7041, closed as stale).

Note that QEMU and libvirt do not impose the limit. QEMU accepts multiple
simultaneous clients on the VNC socket when every client requests a shared
session (the RFB ClientInit shared flag; noVNC and virtctl both set it). The
limit is virt-handler's per-VMI connection bookkeeping.

## Goals

- Allow N simultaneous VNC connections to one running VMI through the
  existing `/virtualmachineinstances/{name}/vnc` subresource, with no
  client-side changes.
- Change nothing by default: multiple connections require both the alpha
  feature gate and a per-VMI cap raised above its default of 1.
- Bound resource usage with the cap; reject connections beyond it with
  HTTP 503, matching the existing USB-redirection slot behavior in
  virt-handler.
- Pin libvirt's VNC `sharePolicy` to `force-shared` when the gate is
  enabled, so a client requesting a non-shared RFB session cannot evict the
  other clients.

## Non Goals

- Server-enforced view-only sessions. RFB has no such concept; every
  connected client can send KeyEvent and PointerEvent messages. Input
  arbitration ("instructor drives, others watch") remains a client or proxy
  responsibility, such as the noVNC `viewOnly` option.
- Changing serial-console semantics. The serial console is an exclusive byte
  stream and stays single-connection.
- Framebuffer or session continuity across live migration.
- Raising virt-api's aggregate stream capacity. The per-replica HTTP/2
  stream limits apply to VNC connections as they do today.

## Definition of Users

- **Cluster Administrators** decide whether to enable the feature gate and
  how high to set the per-VMI connection cap.
- **VM Owners / Operators** open additional console sessions on VMs they
  already hold `subresources/virtualmachineinstances/vnc` access to.
- **UI / Platform Developers** build consoles, dashboards, and thumbnails on
  the `/vnc` subresource without deploying a fan-out proxy.

## User Stories

- As an instructor, I want to open a second VNC session on a trainee's VM to
  watch and assist, without disconnecting the trainee.
- As a support engineer, I want to shadow a user's console session to
  diagnose an issue they are experiencing live.
- As a UI developer, I want to show a live thumbnail of a VM console while
  the user has the full console open, without the thumbnail stealing the
  session.
- As a cluster administrator, I want a per-VMI cap on concurrent viewers so
  that extra VNC sessions cannot exhaust node resources.

## Repos

kubevirt/kubevirt.

## Design

### Where the limit lives today

virt-handler (`pkg/virt-handler/rest/console.go`) keeps one stop channel per
VMI UID in `vncStopChans map[types.UID]chan struct{}`. Every new `/vnc`
request closes the previous connection's channel, which tears down its
relay, and installs its own; with `preserveSession=true` and an active
session, the new request is rejected with 503 instead.

virt-api's VNC subresource handler is a transparent websocket relay with no
connection counting. QEMU's VNC server, a unix socket per VMI, already
accepts multiple shared clients. virt-handler is the only component that
changes.

### virt-handler: bounded multi-connection

The same file already contains the needed pattern. The USB redirection
handler (`USBRedirHandler`) tracks per-VMI connections as a map of stop
channels keyed by slot, allocates the first free slot under a lock, and
returns 503 when every slot is taken. The VNC handler adopts the same
bookkeeping:

- `vncStopChans` becomes a per-UID `map[int]chan struct{}`.
- The effective cap is `maxVNCConnectionsPerVMI` when the
  `MultipleVNCConnections` gate is enabled, and 1 otherwise.
- At an effective cap of 1, behavior is today's: a new connection evicts the
  existing one, unless the existing session requested `preserveSession=true`
  (503).
- Above 1, connections are admitted until the cap is reached and rejected
  with 503 beyond it. No eviction occurs, so `preserveSession` becomes a
  no-op; it is documented as such rather than made an error.

virt-handler already watches the cluster config, so cap changes apply to new
connections without restarts. `NewConsoleHandler` gains a cluster-config
parameter, wired in `cmd/virt-handler/virt-handler.go` like its sibling
components.

### virt-launcher: pin the QEMU share policy

QEMU's default share policy, `allow-exclusive`, tolerates multiple clients
only while every client requests a shared session; one non-shared client
disconnects the rest. With the gate enabled, the generated domain XML sets
`<graphics type='vnc' sharePolicy='force-shared'/>`. The policy is keyed off
the gate alone, not the cap: `force-shared` changes nothing observable with
a single client, and a cap raised later then covers VMIs that are already
running.

virt-launcher has no API-server access, so the flag travels the existing
virt-handler to virt-launcher `ClusterConfig` gRPC message (one new bool
field), into the converter context, and onto the graphics device, the same
route used by the other gate-driven domain XML behaviors. With the gate off
the attribute is omitted and the generated XML is byte-identical to today's.

Note that `sharePolicy` is fixed at domain creation: configuration changes
reach virt-handler admission immediately, but the QEMU-side guarantee covers
only VMIs started after the gate was enabled.

### API

One new field on `KubeVirtConfiguration`:

- `maxVNCConnectionsPerVMI` (`*uint32`, default 1): the maximum number of
  concurrent VNC connections to a single VMI. The field takes effect only
  when the `MultipleVNCConnections` feature gate is enabled; otherwise it is
  inert.

No new subresource, no VMI spec change, no virtctl change. A second
`virtctl vnc` succeeds once the feature is active.

## API Examples

To enable the feature and admit up to 5 concurrent viewers per VMI:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - MultipleVNCConnections
    maxVNCConnectionsPerVMI: 5
```

With the gate enabled but `maxVNCConnectionsPerVMI` unset, the cap is 1 and
behavior is identical to today's.

## Alternatives

1. **External VNC reflector** (the status-quo workaround): holds the one
   upstream connection and fans out to N clients. A reflector requires no
   KubeVirt changes; it must be deployed and secured separately, adds an
   encode hop, and does nothing for `virtctl` or web-console users.
2. **Unbounded connections**, as the VSOCK handler permits: the smallest
   diff, but each VNC client is a full RFB session with its own framebuffer
   encoding cost in the VMI's QEMU process. A cap is required.
3. **A per-VMI spec field instead of a cluster setting**: finer-grained, but
   more API surface and webhook logic for an alpha feature. The resource
   being protected is node and QEMU capacity, cluster infrastructure, so the
   cap sits in cluster configuration; per-VMI granularity can be added later
   if demand appears.
4. **A hardcoded cap constant**, as USB redirection uses
   (`UsbClientPassthroughMaxNumberOf`): a smaller diff, but reasonable
   viewer counts vary by deployment (2 for pair debugging, more for a
   classroom).

## Scalability

Each additional VNC connection is one more websocket stream through
virt-api and one more RFB client for the VMI's QEMU process, whose
framebuffer encoding work scales with the number of connected clients. Both
are bounded by the per-VMI cap, default 1, expected to be set to single
digits. The cluster-wide multiplier (many VMs times many viewers) rides the
same virt-api HTTP/2 stream limits as today; deployments needing large
viewer counts scale virt-api replicas as for any subresource-heavy
workload.

No new API resources, watches, or controllers.

## Update/Rollback Compatibility

- **Update**: the gate is off by default and the cap defaults to 1, so
  upgraded clusters keep today's exact behavior, including the
  `preserveSession` contract.
- **Rollback / gate disable**: established extra connections keep streaming
  until they close on their own; the next incoming connection follows
  single-connection semantics again (evicts all existing streams, or is
  rejected under `preserveSession`). VMIs started while the feature was
  active keep `sharePolicy=force-shared` until restarted; with a single
  client the policy has no observable effect.
- **Mixed versions**: each node's virt-handler enforces the cap, so a node
  running an older virt-handler keeps single-connection behavior for its
  VMIs.

## Functional Testing Approach

1. **Unit** (virt-handler): with the gate off or the cap at 1, a second
   connection evicts the first, and `preserveSession=true` yields 503,
   matching today's behavior exactly. With cap N, N connections stream
   concurrently, connection N+1 gets 503, and slots are released when
   streams end.
2. **Unit** (converter): `sharePolicy` is `force-shared` when the feature
   gate is enabled; the generated domain XML is unchanged otherwise.
3. **E2E**: with the gate enabled and the cap at 2 or more, two concurrent
   VNC streams to one VMI both receive the RFB protocol banner, and a
   stream beyond the cap is rejected. The existing dual-connection test in
   `tests/vnc_test.go` (the preserve-session combinations) remains
   untouched as the gate-off regression guard.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha

- [ ] `MultipleVNCConnections` feature gate guards all behavior changes
- [ ] `maxVNCConnectionsPerVMI` cluster configuration field, default 1
- [ ] virt-handler admits up to the cap, 503 beyond it, legacy behavior at cap 1
- [ ] `sharePolicy=force-shared` on the VNC graphics device when the gate is enabled
- [ ] E2E test for concurrent connections
- [ ] User guide docs

### Beta

- [ ] Feedback from alpha users (training, support, and UI use cases)
- [ ] Evaluate exposing the current viewer count (a metric) for UIs

#### On-By-Default Readiness

`maxVNCConnectionsPerVMI` stays at 1 unless an administrator raises it, so
enabling the gate by default changes no behavior on its own.

### GA

- [ ] Stable across multiple releases with no cap or semantics changes
- [ ] Feature gate removed
