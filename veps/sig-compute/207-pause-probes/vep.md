# VEP: Probe Proxy for Manual Probe Control

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This proposal enables manual pausing of GuestAgentPing probes via VMI annotations to prevent Pod restarts during maintenance (e.g., guest OS updates). The preferred approach leverages the existing `SyncVirtualMachine` gRPC call, which already sends the full VMI JSON (including annotations) to `virt-launcher` on every reconcile. When `kubevirt.io/pause-guest-agent-probes` annotation is set, `virt-launcher` reads it from the VMI received in `SyncVirtualMachine` and short-circuits `GuestPing` calls to return immediate success. No new RPC, proxy server, ConfigMap, or new resources required.

## Motivation

KubeVirt probes translate to Pod probes executed by kubelet. **This affects VMs using GuestAgentPing as a liveness probe** — failures cause Pod restart and destroy the running VM (readiness probe failures only affect Service endpoints).

1. **Guest OS Updates / Reboots**: Guest agent unavailable during OS updates. Especially Windows updates can take hours with multiple reboots — unpredictable duration exceeds any reasonable `failureThreshold`. Requires explicit pause mechanism.

**Why GuestAgentPing only?** Keeping the scope narrow to a single probe type minimizes implementation risk and complexity. Alternative approaches covering all probe types are documented below but require significantly more complex implementations (see Alternatives 3 and 4).

## Goals

- Manual pause/unpause of GuestAgentPing probes via VMI annotations (no Pod restart)
- Maintain strict backward compatibility (Pod probe specs unchanged)
- Internal actions which might trigger probe pause (e.g. live migration) have higher priority than annotation.

## Non Goals

- Pausing non-GuestAgentPing probes (TCP, UDP, HTTP)
- Replacing Kubernetes probe mechanisms
- Custom health check logic or complex conditions
- Multi-VMI control or HTTP endpoints

## User Stories

- Manually pause via annotation before guest OS updates (especially long Windows updates) and manually remove the annotation when OS update finishes.
- Query pause status via annotation if paused by user

## Use Cases

**Supported:**
1. **Guest OS Updates**: Manual annotation before updates (critical for unpredictable Windows updates, might be used for other OSes updates too)

**Unsupported:**
1. Permanent disabling (remove probe from spec instead)
2. Custom probe responses
3. Non-GuestAgentPing probes
4. Auto-set annotation during internal operations (fsfreeze, snapshot, backup, ...)

## Repos

kubevirt/kubevirt

### Why Annotations Instead of a Spec API Field

While probe pausing can be viewed as a desired state (e.g., for planned maintenance windows or other OSes), using annotations provides operational flexibility: it allows runtime control without VMI spec changes and maintains backward compatibility. For permanent probe disabling, users should remove the probe from the spec instead of keeping the annotation set indefinitely.

### Observability

- **Info** level: Log state transitions (paused ↔ unpaused) with VMI name


### Pausing Probes via Annotation

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-vm
  annotations:
    kubevirt.io/pause-guest-agent-probes: "true" 
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

### Manually Pausing Probes

```bash
# Add annotation to pause GuestAgentPing probes on running VMI
kubectl annotate vmi my-vm kubevirt.io/pause-guest-agent-probes=true

# Check probe pause status by inspecting the VMI annotation
kubectl get vmi my-vm -o jsonpath='{.metadata.annotations.kubevirt\.io/pause-guest-agent-probes}'
# Output: true

# Remove annotation to unpause probes
kubectl annotate vmi my-vm kubevirt.io/pause-guest-agent-probes-
```

## Relationship to Upstream Kubernetes KEP

[KEP #5002: Introduce hot disable of probes](https://github.com/kubernetes/enhancements/issues/5002) is in **Draft stage** (April 2026). Even if implemented, KubeVirt needs its own VM/VMI ↔ Pod interface since the KEP is Pod-focused. The `kubevirt.io/pause-guest-agent-probes` annotation is scoped to GuestAgentPing probes. If the KEP reaches GA and KubeVirt expands to all probe types, a new generic annotation can be introduced alongside migration tooling.

## Design

All alternatives below use the same user-facing interface: VMI annotations control probe pausing.

The preferred alternative is number 1 (Annotation via existing `SyncVirtualMachine` — GuestAgentPing only). Alternatives 2–4 are included to compare the full spectrum of options explored. Alternatives 1 and 2 are narrow in scope (GuestAgentPing only) but extremely lightweight. Alternatives 3 and 4 extend the gRPC and file-based approaches to cover all VMI probe types by leveraging `virt-probe` as the common entry point.

### Alternative 1: Annotation via Existing `SyncVirtualMachine` (Preferred) ✅

Reads the pause annotation from the VMI JSON already delivered by the existing `SyncVirtualMachine` gRPC call. Targets **GuestAgentPing only**. No new RPCs, servers, ConfigMaps, or resources.

`SyncVirtualMachine` sends a `VMIRequest` containing `VMI.vmiJson` — the full JSON-marshaled `VirtualMachineInstance` including `metadata.annotations`. virt-launcher already receives this on every reconcile loop iteration, so the annotation is delivered without any protocol changes.

**Flow**:
```
┌───────────────────────────────────────────────────────────────┐
│                    virt-handler                               │
│                                                               │
│   VMI annotation:                                             │
│   kubevirt.io/pause-guest-agent-probes: "true"                │
│           │                                                   │
│           ▼                                                   │
│   syncVirtualMachine() reconcile loop                         │
│   → client.SyncVirtualMachine(vmi)   (existing call)          │
└───────────────────────────────────────────────────────────────┘
                         │
                         │ (existing gRPC over Unix socket)
                         │ (VMIRequest with full VMI JSON incl. annotations)
                         ▼
┌───────────────────────────────────────────────────────────────┐
│                    virt-launcher Pod                          │
│                                                               │
│   ┌─────────────────────────────────────────────────────┐     │
│   │  SyncVirtualMachine handler                         │     │
│   │  → read annotation from VMI JSON                    │     │
│   │  → domainManager.guestAgentPaused.Store(true/false) │     │
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
- Zero protocol changes — reuses existing `SyncVirtualMachine` call and `VMIRequest` message
- Level-triggered: state is refreshed on every reconcile, no stale in-memory state possible
- Rollback-safe: old virt-handler sends VMI without the annotation → virt-launcher reads absence as `false` → probes auto-unpause
- Fast propagation (seconds via reconcile loop)
- No ConfigMaps, no API calls from virt-launcher
- Thread-safe (`atomic.Bool`)
- Minimal code: annotation check in existing handler, 1 field, 1 function change

**Cons:**
- GuestAgentPing only (no HTTP/TCP/Exec)

[Reference implementation](https://github.com/kubevirt/kubevirt/compare/main...ksimon1:kubevirt:pause-probes?expand=1)

### Alternative 2: File-Based Shared State

GuestAgentPing only. virt-handler writes sentinel file to EmptyDir via `/pods/<pod-uid>/volumes/kubernetes.io~empty-dir/public/probe-paused`. virt-launcher stats file on each probe — exists = success.

**Pros:** No gRPC changes, survives restarts (file persists), minimal code, no API load  
**Cons:** GuestAgentPing only, relies on undocumented kubelet path, file cleanup required

### Alternative 3: HTTP Proxy + gRPC State

Covers **all probe types** via HTTP proxy (:9500) in virt-launcher. virt-controller rewrites all probes to `httpGet` with original details in headers (`X-KV-Type`, `X-KV-Port`, etc.). Paused flag via gRPC.

**Pros:** All probe types, gRPC state delivery, no API calls  
**Cons:** New HTTP server (security hardening needed), extra HTTP hop, probe rewriting complexity, port reservation

### Alternative 4: HTTP Proxy + File State

Same as Alternative 3, but paused flag via sentinel file (Alternative 2 approach) instead of gRPC. Proxy stats file on each request.

**Pros:** All probe types, no gRPC changes, survives restarts, no API calls  
**Cons:** Same as Alt 3 (HTTP server, probe rewriting, extra hop) + `stat` syscall + kubelet path dependency


### Alternatives Comparison

| Aspect | Alt 1: SyncVM ✅ | Alt 2: File | Alt 3: Proxy+gRPC | Alt 4: Proxy+File |
|--------|-----------------|-------------|-------------------|-------------------|
| **Scope** | GuestAgentPing | GuestAgentPing | All types | All types |
| **virt-controller** | No change | No change | Probe rewrite | Probe rewrite |
| **virt-handler** | No change | +file write | +RPC call | +file write |
| **virt-launcher** | +annotation read +atomic.Bool | +os.Stat | +HTTP server +RPC | +HTTP server |
| **Proto changes** | None | None | +1 RPC | None |
| **Pod spec** | No change | No change | All → httpGet | All → httpGet |
| **Overhead/probe** | atomic read | stat syscall | HTTP hop | HTTP hop + stat |
| **State delivery** | Level-triggered | Level-triggered | Edge-triggered | Level-triggered |
| **Rollback safety** | Auto-unpause | Auto-unpause | Stays paused | Auto-unpause |
| **Restart recovery** | Next reconcile | Persists | Next reconcile | Persists |
| **New server** | No | No | Yes (:9500) | Yes (:9500) |
| **Complexity** | Very Low | Very Low | Medium | Medium |

## Update/Rollback Compatibility

- **Upgrade**: Fully backward compatible. Updated virt-handler sends VMI with annotation via existing `SyncVirtualMachine`; old virt-launcher ignores the unknown annotation. Probes work normally.
- **Rollback**: Old virt-handler continues calling `SyncVirtualMachine` with the full VMI JSON. Since it doesn't set the annotation, virt-launcher reads its absence as `false` — probes auto-unpause on the next reconcile. No manual cleanup or pod restart needed.
- **Feature Flag**: None. Annotation is opt-in — no effect unless set to `"true"`.

## Functional Testing Approach

**Unit/Integration/E2E**: Test coverage depends on selected alternative (see graduation criteria for required tests).

## Implementation History

- 2026-02-16: Initial VEP proposal created
- 2026-03-17: Updated based on PR review feedback (dominikholler, 0xFelix): clarified liveness vs readiness probe scope, added automatic pausing for internal operations, addressed multi-launcher migration behavior, added observability requirements, removed feature gate, addressed gRPC overhead and EmptyDir alternatives
- 2026-03-24: Removed live migration from scope (addressed separately by [kubevirt/kubevirt#17235](https://github.com/kubevirt/kubevirt/pull/17235)). Added Windows updates as a motivation. VEP scope adjusted to focus on annotation-based pausing for backup/snapshot, guest OS updates, and Windows updates.
- 2026-04-16: Added section on upstream [Kubernetes KEP #5002](https://github.com/kubernetes/enhancements/issues/5002) (hot disable of probes) and documented the migration path.
- 2026-04-21: Updated based on enp0s3 feedback: clarified why GuestAgentPing-only scope (customer requirements + reduced implementation risk), expanded annotation vs. API field rationale to acknowledge pause as desired state, fixed diagram formatting with code fences.
- 2026-04-24: Removed internal operations (fsfreeze, snapshot, backup) from supported use cases. Auto-pause during internal operations is now explicitly unsupported — annotation is user-driven only. Replaced dedicated `SetGuestAgentPaused` gRPC RPC with reading annotation from the VMI JSON already delivered by `SyncVirtualMachine` — zero protocol changes, level-triggered state, and rollback-safe (old virt-handler sends VMI without annotation → auto-unpause).

## Graduation Requirements

**Alpha:**
- [ ] Annotation pause/unpause works end-to-end
- [ ] Logging at Info/Debug levels
- [ ] Unit tests for paused/unpaused paths
- [ ] E2E: set annotation → success without guest contact → remove → resume

**Beta:**
- [ ] Guest OS update workflow docs

**GA:**
- [ ] Stable for ≥2 releases, no regressions
- [ ] Documentation exists


## References

- [Kubernetes Probes Documentation](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)
- [KubeVirt Probes Documentation](https://kubevirt.io/user-guide/user_workloads/liveness_and_readiness_probes/)
- [Kubernetes KEP #5002: Introduce hot disable of probes](https://github.com/kubernetes/enhancements/issues/5002)