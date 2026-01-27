# VEP: Probe Proxy for Dynamic Probe Control

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal introduces a lightweight mechanism to dynamically control GuestAgentPing probes at runtime. By utilizing VMI annotations, administrators can pause these probes during maintenance operations such as backup, guest OS updates (including long-running Windows updates), or filesystem freeze operations. The preferred approach (Alternative 1) extends the existing gRPC channel between `virt-handler` and `virt-launcher` with a single new `SetGuestAgentPaused` RPC. When the user sets the `kubevirt.io/pause-guest-agent-probe` annotation on a VMI, `virt-handler` communicates this to `virt-launcher` on the next reconcile. `virt-launcher` stores the state in an atomic boolean and short-circuits `GuestPing` calls to return immediate success while the flag is set. This prevents probe failures from triggering unwanted Pod restarts without requiring a proxy server, a ConfigMap, or any new Kubernetes resources.

## Motivation

Currently, KubeVirt probes are directly translated into Pod probes and executed by the Kubernetes kubelet against the VM or guest agent. This creates several challenges during standard administrative operations.

**Important**: this issue primarily affects VMs that use GuestAgentPing as a **liveness probe**. When used as a readiness probe, probe failures only remove the VM from Service endpoints — the Pod is not restarted. The scenarios below therefore focus on liveness probe failures, which cause kubelet to forcefully restart the Pod and destroy the running VM.

1. **Backup / Snapshot / Filesystem Freeze**: When taking VM snapshots or backups, the guest may need to be quiesced (`fsfreeze`). While the guest agent is frozen, GuestAgentPing probes will fail and kubelet will restart the Pod. Internal KubeVirt operations that freeze the guest (e.g. VirtualMachineSnapshot, `fsfreeze` via the guest agent) should automatically pause probes for the duration of the freeze.

2. **Guest OS Updates / Reboots**: During in-guest OS updates that require a reboot, the guest agent is unavailable for a period. Liveness probe failures during this window cause a forceful Pod restart, preventing the update from completing. Windows updates are a particularly severe case — they can take a very long time (sometimes hours), involving multiple reboots and extended periods of OS reconfiguration during which the guest agent is unavailable. Because the duration is unpredictable and can far exceed any reasonable `failureThreshold` setting, users need an explicit mechanism to pause probes for the entire update window.

## Goals

- Allow dynamic pausing and unpausing of GuestAgentPing probes via VMI annotations without requiring a Pod restart.
- Return synthetic success from the GuestPing handler when probes are paused, causing virt-probe to exit 0.
- Enable runtime control via VMI annotations to maintain a single source of truth.
- Internal KubeVirt operations that make the guest agent temporarily unavailable (`fsfreeze`, snapshot) should automatically set and clear the annotation so that no manual user intervention is required.
- Maintain strict backward compatibility by leaving existing Kubernetes Pod probe specifications completely untouched.

## Non Goals

- Pausing of probes other than guest-agent-based probes (e.g. TCP, UDP, HTTP, generic Exec). Only GuestAgentPing is in scope.
- Replacing Kubernetes native probe mechanisms
- Providing custom health check logic beyond pause/unpause
- Modifying probe results based on complex conditions (beyond pause state)
- Supporting probe pausing across multiple VMIs simultaneously via a single control plane
- Providing HTTP control endpoints for runtime management from within the Pod (annotation-only control)

## Definition of Users

- **VM User**: A person who runs VMs on KubeVirt and wants their VMs to remain stable during maintenance operations
- **Cluster Administrator**: A person who manages the KubeVirt infrastructure and needs to control probe behavior during cluster-wide operations

## User Stories

- As a VM user, I want GuestAgentPing liveness probes to be automatically paused when internal KubeVirt operations (fsfreeze, snapshot) make the guest agent temporarily unavailable, so that no manual intervention is required
- As a VM user, I want to manually pause GuestAgentPing probes by adding an annotation to my VMI before initiating a guest OS update (including long-running Windows updates), and have probes resume when I remove it after the update completes
- As a cluster administrator, I want to pause GuestAgentPing probes on specific VMIs during scheduled maintenance windows (e.g. OS patching, Windows updates)
- As a cluster administrator, I want to be able to query the current probe pause status of a VM/VMI via its annotation

## Use Cases

### Supported Use Cases

1. **Filesystem Freeze / Snapshot**: When KubeVirt freezes the guest filesystem (e.g. for VirtualMachineSnapshot or an explicit `fsfreeze` call), the pause annotation is automatically set for the duration of the freeze. This prevents GuestAgentPing liveness probes from failing while the guest agent is unresponsive.
2. **Guest OS Updates / Reboots**: A VM user or automation tooling manually sets the pause annotation before initiating an in-guest OS update that requires a reboot, and removes it after the guest agent is available again. This is especially important for Windows updates, which can take hours with multiple reboots — the unpredictable duration makes `failureThreshold` tuning impractical.


### Unsupported Use Cases

1. **Permanent probe disabling**: This feature is meant for temporary pausing, not permanent disabling. For permanent disabling, remove the probe from the VMI spec.
2. **Custom probe responses**: Only success/failure responses are supported, not custom response bodies.
3. **Non-GuestAgentPing probes**: HTTP, TCP, and generic Exec probes are not in scope for this feature.

## Repos

kubevirt/kubevirt

## Design

All alternatives below use the same user-facing interface: VMI annotations control probe pausing.

### Why Annotations Instead of a Spec API Field

An annotation was chosen over a formal API field (e.g. a `paused` boolean in `guestAgentPing`) because the pause state is inherently operational and transient — it does not describe the desired probe configuration but rather a temporary runtime override. Using an annotation keeps the VMI spec clean and declarative. Importantly, the mechanism that delivers the annotation value to `virt-launcher` (gRPC or file) is equally capable of propagating a spec field, so this is not a technical limitation. If a future decision promotes the annotation to a first-class API field, the internal plumbing does not need to change.

### Observability

When GuestAgentPing probes are paused, `virt-launcher` must log the event so that operators can confirm the annotation actually influenced behavior:

- Log at **Info** level when the paused state transitions (paused → unpaused or unpaused → paused), including the VMI name.
- Log at **Debug** level each time a GuestPing call is short-circuited due to the paused flag, to allow fine-grained troubleshooting without flooding logs during normal operation.

### Pausing Probes via Annotation

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vm
  annotations:
    kubevirt.io/pause-guest-agent-probe: "true" 
spec:
  domain:
    devices:
      disks:
      - disk:
          bus: virtio
        name: containerdisk
    resources:
      requests:
        memory: 1Gi
  livenessProbe:
    guestAgentPing: {}
    initialDelaySeconds: 120
    periodSeconds: 20
  volumes:
  - containerDisk:
      image: quay.io/containerdisks/fedora
    name: containerdisk
```

### Dynamically Pausing Probes

```bash
# Add annotation to pause GuestAgentPing probes on running VMI
kubectl annotate vmi my-vm kubevirt.io/pause-guest-agent-probe=true

# Check probe pause status by inspecting the VMI annotation
kubectl get vmi my-vm -o jsonpath='{.metadata.annotations.kubevirt\.io/pause-guest-agent-probe}'
# Output: true

# Remove annotation to unpause probes
kubectl annotate vmi my-vm kubevirt.io/pause-guest-agent-probe-

# Verify probes are active again (annotation should be absent)
kubectl get vmi my-vm -o jsonpath='{.metadata.annotations.kubevirt\.io/pause-guest-agent-probe}'
# Output: (empty - annotation removed)
```

## Alternatives

The preferred alternative is number 1 (gRPC Channel Extension — GuestAgentPing only). Alternatives 2–4 are included to compare the full spectrum of options explored. Alternatives 1 and 2 are narrow in scope (GuestAgentPing only) but extremely lightweight. Alternatives 3 and 4 extend the same two approaches (gRPC and file-based, respectively) to cover all VMI probe types by leveraging `virt-probe` as the common entry point.

### Alternative 1: gRPC Channel Extension (virt-handler → virt-launcher)

This alternative targets only the **GuestAgentPing** probe type and extends the **existing** gRPC channel between `virt-handler` and `virt-launcher` with a single new RPC. No new servers, no ConfigMaps, and no new Kubernetes resources are required. `virt-handler` reads the VMI annotation on every reconcile and calls `SetGuestAgentPaused` over the existing Unix-socket gRPC connection. `virt-launcher` stores the paused flag in an `atomic.Bool` and short-circuits `GuestPing` when it is set.

#### Design

**Control Flow**:

```
┌───────────────────────────────────────────────────────────────┐
│                    virt-handler                               │
│                                                               │
│   VMI annotation:                                             │
│   kubevirt.io/pause-guest-agent-probe: "true"                 │
│           │                                                   │
│           ▼                                                   │
│   syncVirtualMachine() reconcile loop                         │
│   → client.SetGuestAgentPaused(true)                          │
└───────────────────────────────────────────────────────────────┘
                         │
                         │ (existing gRPC over Unix socket)
                         ▼
┌───────────────────────────────────────────────────────────────┐
│                    virt-launcher Pod                          │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  cmd-server: SetGuestAgentPaused RPC handler        │     │
│   │  → domainManager.guestAgentPaused.Store(true)       │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  GuestPing (called by kubelet liveness probe)       │     │
│   │  → if guestAgentPaused: return nil (success)        │     │
│   │  → else: execute guest-ping via QEMU agent          │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  VM / QEMU Guest Agent                              │     │
│   └─────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────┘
```

**Pros:**
- Reuses the already-existing gRPC channel — no new servers, sockets, or listeners
- Near-instant propagation: the next virt-handler reconcile loop (seconds) delivers the state
- No ConfigMaps, no additional Kubernetes resources per VMI
- No Kubernetes API calls from `virt-launcher` — zero extra API server load
- Thread-safe via `atomic.Bool` — no locks required
- If `virt-launcher` restarts, the state is automatically re-applied on the next `virt-handler` reconcile
- Very small surface area: one new RPC, one new field, one changed function

**Cons:**
- Only covers the GuestAgentPing probe type — HTTP, TCP, and generic Exec probes are not covered
- In-memory state in `virt-launcher` — does not survive a `virt-launcher` crash until `virt-handler` reconciles again (typically within seconds)
- Requires both `virt-handler` and `virt-launcher` to carry the updated code; old `virt-launcher` pods simply ignore the unknown RPC (gRPC handles unknown methods gracefully)
- **gRPC overhead**: `SetGuestAgentPaused` is an imperative command — `virt-handler` does not know `virt-launcher`'s current in-memory state (e.g. it may have crashed and restarted, resetting the flag to `false`). Therefore `virt-handler` must unconditionally call the RPC on every reconciliation to guarantee consistency. This adds one extra gRPC round-trip per reconcile loop for every VMI that has a GuestAgentPing probe, regardless of whether the annotation changed. In contrast, the file-based approach (Alternative 2) does not have this overhead since the state persists on disk

Possible implementation can be found here: https://github.com/kubevirt/kubevirt/compare/main...ksimon1:kubevirt:pause-probes?expand=1

### Alternative 2: File-Based Shared State (virt-handler → virt-launcher EmptyDir)

This alternative targets only the **GuestAgentPing** probe type. It requires no gRPC changes, no new Kubernetes resources, and no extra processes. `virt-handler` writes a sentinel file directly into the virt-launcher Pod's `public` EmptyDir by accessing it through the kubelet pods directory (`/pods/<pod-uid>/volumes/kubernetes.io~empty-dir/public/`). `virt-launcher`'s `GuestPing` handler simply stats that file on every probe call — if the file is present the probe returns success immediately; otherwise the real guest-agent ping is executed.

#### Design

**Path mapping:**

| Side | Path |
|------|------|
| virt-handler (host) | `/pods/<pod-uid>/volumes/kubernetes.io~empty-dir/public/probe-paused` |
| virt-launcher (container) | `/var/run/kubevirt/probe-paused` |

virt-handler already mounts `/var/lib/kubelet/pods` → `/pods` (HostPath, bidirectional), giving it direct filesystem access to every virt-launcher Pod's EmptyDir volumes without any new mounts.

**Control Flow:**

```
┌───────────────────────────────────────────────────────────────┐
│                    virt-handler                               │
│                                                               │
│   VMI annotation:                                             │
│   kubevirt.io/pause-guest-agent-probe: "true"                 │
│           │                                                   │
│           ▼                                                   │
│   syncVirtualMachine() reconcile loop                         │
└───────────────────────────────────────────────────────────────┘
                         │
                         │ (shared EmptyDir via kubelet pods path)
                         ▼
┌───────────────────────────────────────────────────────────────┐
│                    virt-launcher Pod                          │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  GuestPing (called by kubelet liveness probe)       │     │
│   │  → stat /var/run/kubevirt/probe-paused              │     │
│   │  → If file exists: return nil (success)             │     │
│   │  → If absent: execute guest-ping via QEMU agent     │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  VM / QEMU Guest Agent                              │     │
│   └─────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────┘
```

**Pros:**
- Zero protocol changes — no new gRPC methods, no new Kubernetes resources
- Instant read on every probe invocation — no goroutine, no polling loop, no in-memory state to re-sync after a crash
- Extremely small change surface: one `os.WriteFile`/`os.Remove` in `virt-handler` and one `os.Stat` in `virt-launcher`
- Survives virt-launcher restarts with no re-sync needed — the file persists in the EmptyDir until virt-handler explicitly removes it (or the Pod is deleted)
- No API server load — both sides operate purely on the local filesystem
- Uses an already-established pattern (`/pods` HostPath) that virt-handler uses for hotplug and sockets today

**Cons:**
- Only covers the GuestAgentPing probe type — HTTP, TCP, and generic Exec probes are not covered
- Relies on the internal kubelet EmptyDir directory layout (`kubernetes.io~empty-dir/<volume-name>/`) — this is a stable but undocumented path also used by the existing hotplug and socket code
- File cleanup must be handled by virt-handler on VMI deletion; a stale file in a reused pod UID is theoretically possible (mitigated by pod UID uniqueness guarantees)
- Slightly less explicit than gRPC — the communication channel is the filesystem rather than a typed API

### Alternative 3: HTTP Proxy in virt-launcher + gRPC Paused State

This alternative covers **all** VMI liveness probe types by running a lightweight HTTP proxy server inside `virt-launcher`. The proxy server is only started when the VMI has probe configuration defined — if no probes are configured, `virt-launcher` skips starting the proxy entirely, so there is zero overhead for VMs without probes. `virt-controller` rewrites every VMI probe (httpGet, tcpSocket, exec, guestAgentPing) into an `httpGet` probe that targets the proxy, encoding the original probe details in custom HTTP headers. The proxy checks the paused flag and either returns HTTP 200 immediately (paused) or forwards to the real target (not paused). The paused flag is delivered via the **existing gRPC channel** from `virt-handler` — no ConfigMap, no Kubernetes API calls from `virt-launcher`.

#### Design

**Probe rewriting (virt-controller):**

All four VMI probe types are transformed into `httpGet` probes targeting the proxy on a fixed internal port (e.g. 9500). The original probe details travel as HTTP headers:

```
VMI: httpGet { port: 8080, path: /health }
→ Pod: httpGet { port: 9500, path: /probe,
        headers: [X-KV-Type: http, X-KV-Host: 127.0.0.1,
                  X-KV-Port: 8080, X-KV-Path: /health] }

VMI: tcpSocket { port: 22 }
→ Pod: httpGet { port: 9500, path: /probe,
        headers: [X-KV-Type: tcp, X-KV-Host: 127.0.0.1, X-KV-Port: 22] }

VMI: exec { command: ["my-script.sh"] }
→ Pod: httpGet { port: 9500, path: /probe,
        headers: [X-KV-Type: exec, X-KV-Command: my-script.sh] }

VMI: guestAgentPing {}
→ Pod: httpGet { port: 9500, path: /probe,
        headers: [X-KV-Type: guestAgentPing] }
```

**Control Flow:**

```
┌───────────────────────────────────────────────────────────────┐
│                    virt-handler                               │
│                                                               │
│   VMI annotation:                                             │
│   kubevirt.io/pause-guest-agent-probe: "true"                 │
│           │                                                   │
│           ▼                                                   │
│   syncVirtualMachine() reconcile loop                         │
│                                                               │
└───────────────────────────────────────────────────────────────┘
                         │
                         │ (existing gRPC over Unix socket)
                         ▼
┌───────────────────────────────────────────────────────────────┐
│                    virt-launcher Pod                          │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  cmd-server: SetProbesPaused RPC handler            │     │
│   │  → probesPaused.Store(true)                         │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  Probe proxy HTTP server (:9500)                    │     │
│   │  ← kubelet httpGet (all probe types)                │     │
│   │                                                     │     │
│   │  if probesPaused.Load():                            │     │
│   │      → return HTTP 200 immediately                  │     │
│   │  else:                                              │     │
│   │      X-KV-Type: http      → HTTP GET to VM port     │     │
│   │      X-KV-Type: tcp       → TCP connect to VM port  │     │
│   │      X-KV-Type: exec      → exec via guest agent    │     │
│   │      X-KV-Type: guestAgentPing → ping guest agent   │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  VM / QEMU Guest Agent                              │     │
│   └─────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────┘
```


**Hardening:**

Since the proxy exposes an HTTP endpoint inside the Pod, the following hardening measures should be applied:

- **Serve only `/probe`** — return HTTP 404 for any other path to minimise the attack surface
- **Validate required headers** — reject requests missing the `X-KV-Type` header (or carrying an unknown type) with HTTP 400
- **Read/write timeouts** — set aggressive `ReadHeaderTimeout`, `ReadTimeout`, and `WriteTimeout` (e.g. 5 s) to prevent slowloris-style connection exhaustion
- **Limit request size** — set `MaxHeaderBytes` and reject requests with a body (probes are header-only GETs) to avoid memory abuse
- **No sensitive data in responses** — return only an HTTP status code and a minimal static body; never echo back headers, internal state, or error details
- **Graceful shutdown** — tie the server's lifecycle to `virt-launcher`'s context so it shuts down cleanly on SIGTERM

**Pros:**
- Covers **all** probe types (HTTP, TCP, Exec, GuestAgentPing) without extending `virt-probe`
- Paused state delivered via existing gRPC — near-instant propagation (seconds), no ConfigMap
- Single source of truth: `virt-handler` annotation → gRPC → `atomic.Bool` in proxy
- No Kubernetes API calls from `virt-launcher`
- State is re-applied on the next `virt-handler` reconcile after a `virt-launcher` restart

**Cons:**
- New HTTP server inside `virt-launcher` — additional component to maintain and secure (only started when probes are configured; see hardening measures above)
- All probe definitions must be rewritten to target the proxy (virt-controller change)
- Every probe invocation goes through an extra HTTP hop even when not paused (kubelet → proxy → VM), adding a small round-trip
- Port 9500 must be reserved and not conflict with anything inside the Pod
- Probe rewriting adds complexity to `virt-controller`, especially for exec probes that need to encode multi-argument commands in a header

### Alternative 4: HTTP Proxy in virt-launcher + File-Based Paused State

Identical architecture to Alternative 3 — a lightweight HTTP proxy server inside `virt-launcher` intercepts all probe types — but the paused flag is delivered via the **sentinel file** approach of Alternative 2 instead of gRPC. As with Alternative 3, the proxy server is only started when the VMI has probe configuration defined, and the same hardening measures (path restriction, header validation, timeouts, request-size limits — see Alternative 3) apply. The proxy stats `/var/run/kubevirt/probe-paused` on each request rather than reading an `atomic.Bool`.

This removes the need for any gRPC or proto changes at the cost of a filesystem stat on every probe call.

#### Design

**Probe rewriting (virt-controller):** Identical to Alternative 3.

**Control Flow:**

```
┌───────────────────────────────────────────────────────────────┐
│                    virt-handler                               │
│                                                               │
│   VMI annotation:                                             │
│   kubevirt.io/pause-guest-agent-probe: "true"                 │
│           │                                                   │
│           ▼                                                   │
│   syncVirtualMachine() reconcile loop                         │
└───────────────────────────────────────────────────────────────┘
                         │
                         │ (shared EmptyDir via kubelet pods path)
                         ▼
┌───────────────────────────────────────────────────────────────┐
│                    virt-launcher Pod                          │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  Probe proxy HTTP server (:9500)                    │     │
│   │  ← kubelet httpGet (all probe types)                │     │
│   │                                                     │     │
│   │  stat /var/run/kubevirt/probe-paused:               │     │
│   │    exists  → return HTTP 200 immediately            │     │
│   │    absent  → forward to real target (by X-KV-Type)  │     │
│   └─────────────────────────────────────────────────────┘     │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  VM / QEMU Guest Agent                              │     │
│   └─────────────────────────────────────────────────────┘     │
└───────────────────────────────────────────────────────────────┘
```

`virt-handler` reconcile and `virt-controller` probe rewriting: identical to Alternative 2 and Alternative 3 respectively.

**Pros:**
- Covers **all** probe types through the proxy
- Zero gRPC or proto changes — no new RPCs, no cmd-server modifications
- virt-launcher cmd-server requires no changes at all
- Survives virt-launcher restarts with no re-sync (file persists in EmptyDir)
- No Kubernetes API calls

**Cons:**
- New HTTP server inside `virt-launcher` — same as Alternative 3
- All probe definitions must be rewritten — same virt-controller change as Alternative 3
- Extra HTTP hop on every probe invocation — same as Alternative 3
- Filesystem `stat` on every probe call (instead of in-process `atomic.Bool` read)
- Same reserved port and probe-rewriting complexity as Alternative 3
- Relies on the internal kubelet EmptyDir directory layout


### Alternatives Comparison

| Aspect | Alt 1: gRPC (GuestAgentPing) ✅ | Alt 2: File-Based (GuestAgentPing) | Alt 3: HTTP Proxy + gRPC State | Alt 4: HTTP Proxy + File State |
|--------|-------------------------------|-------------------------------------|--------------------------------|-------------------------------|
| **Probe scope** | GuestAgentPing only | GuestAgentPing only | All types | All types |
| **Use case scope** | Backup, guest OS maintenance | Backup, guest OS maintenance | Backup, guest OS maintenance | Backup, guest OS maintenance |
| **virt-controller changes** | None | None | Rewrite all probes to target proxy | Rewrite all probes to target proxy |
| **virt-launcher changes** | 1 new RPC + `atomic.Bool` | `os.Stat` check in GuestPing | HTTP proxy server + 1 new RPC | HTTP proxy server only |
| **virt-probe changes** | No changes (short-circuit is in cmd-server) | No changes (short-circuit is in cmd-server) | Bypassed — kubelet uses `httpGet` to proxy instead of exec `virt-probe` | Bypassed — kubelet uses `httpGet` to proxy instead of exec `virt-probe` |
| **virt-handler changes** | 1 RPC call in reconcile | `os.WriteFile`/`os.Remove` in reconcile | 1 RPC call in reconcile | `os.WriteFile`/`os.Remove` in reconcile |
| **Pod probe spec changes** | None | None | All probes rewritten to `httpGet` | All probes rewritten to `httpGet` |
| **API calls from virt-launcher** | 0 | 0 | 0 | 0 |
| **ConfigMaps per VMI** | 0 | 0 | 0 | 0 |
| **Propagation delay** | Seconds (reconcile loop) | Seconds (reconcile loop) | Seconds (reconcile loop) | Seconds (reconcile loop) |
| **Overhead per probe call** | Negligible (atomic read) | `os.Stat` syscall | Extra HTTP hop (kubelet → proxy → VM) | Extra HTTP hop + `stat` syscall |
| **Paused-state check** | In-process `atomic.Bool` | In-process `os.Stat` | In-process `atomic.Bool` in proxy | `os.Stat` in proxy |
| **Survives virt-launcher restart** | Re-applied on next reconcile | Yes (file persists in EmptyDir) | Re-applied on next reconcile | Yes (file persists in EmptyDir) |
| **New server in virt-launcher** | No | No | Yes (HTTP :9500) | Yes (HTTP :9500) |
| **Complexity** | Very Low | Very Low | Medium | Medium |
| **Scalability (1000+ VMs)** | Excellent (0 API calls) | Excellent (0 API calls) | Excellent (0 API calls) | Excellent (0 API calls) |

## Update/Rollback Compatibility

- **Upgrade**: The feature is strictly backward compatible. Running VMs created prior to the update will continue executing `GuestAgentPing` probes normally. The `SetGuestAgentPaused` RPC is called by the updated `virt-handler` on every reconcile; if `virt-launcher` has not yet been updated, gRPC gracefully returns an "unimplemented" error which `virt-handler` logs as a warning and ignores — probes continue working as before.

- **Rollback**:
  - If rolling back to a KubeVirt version without this feature, `virt-handler` will stop sending `SetGuestAgentPaused` calls.
  - `virt-launcher` will keep whatever in-memory state it had until the Pod restarts, at which point the paused flag defaults to `false` and probes resume normally.
  - No Kubernetes resources (ConfigMaps, etc.) need to be cleaned up — there are none.

- **Feature Flag**: No additional feature gate is introduced. The annotation `kubevirt.io/pause-guest-agent-probe` is a soft, opt-in API — it has no effect unless explicitly set. When the annotation is absent (or set to any value other than `"true"`), probes behave exactly as before. This avoids adding unnecessary gating complexity for a lightweight, backward-compatible change.

## Functional Testing Approach

### Unit Tests

1. Depends on selected alternative

### Integration Tests

1. Depends on selected alternative

### E2E Tests

1. Depends on selected alternative

## Implementation History

- 2026-02-16: Initial VEP proposal created
- 2026-03-17: Updated based on PR review feedback (dominikholler, 0xFelix): clarified liveness vs readiness probe scope, added automatic pausing for internal operations, addressed multi-launcher migration behavior, added observability requirements, removed feature gate, addressed gRPC overhead and EmptyDir alternatives
- 2026-03-24: Removed live migration from scope (addressed separately by [kubevirt/kubevirt#17235](https://github.com/kubevirt/kubevirt/pull/17235)). Added Windows updates as a motivation. VEP scope adjusted to focus on annotation-based pausing for backup/snapshot, guest OS updates, and Windows updates.

## Graduation Requirements

### Alpha

- [ ] Annotation-based pause/unpause of GuestAgentPing probes works end-to-end
- [ ] virt-launcher logs probe skip events at Info/Debug level
- [ ] Unit tests cover paused and unpaused code paths
- [ ] E2E test: set annotation → verify probe returns success without contacting guest agent → remove annotation → verify probe resumes

### Beta

- [ ] Snapshot/fsfreeze operations automatically set/clear the pause annotation
- [ ] E2E tests for automatic pausing during fsfreeze
- [ ] Documentation for guest OS update workflow (set annotation before update, remove after)

### GA

- [ ] Feature has been stable for at least two releases
- [ ] No regressions reported


## References

- [Kubernetes Probes Documentation](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [KubeVirt Probes Documentation](https://kubevirt.io/user-guide/user_workloads/liveness_and_readiness_probes/)