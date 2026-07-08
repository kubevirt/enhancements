# VEP #NNNN: KSM Zero-Pages-Only Mode and Sleep Scaling Improvement

Owners:
- @mskrivan

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Target version is explicitly mentioned and approved
- [X] (R) Graduation criteria filled

## Table of Contents

- [Overview](#overview)
- [Motivation](#motivation)
- [Goals](#goals)
- [Non Goals](#non-goals)
- [Definition of Users](#definition-of-users)
- [User Stories](#user-stories)
- [Repos](#repos)
- [Design](#design)
  - [API Changes](#api-changes)
  - [KSM Mode: ZeroPagesOnly](#ksm-mode-zeropagesonly)
  - [Improvement: Dynamic Sleep Baseline Scaling](#improvement-dynamic-sleep-baseline-scaling)
  - [Sysfs Management](#sysfs-management)
  - [Fallback and Capability Detection](#fallback-and-capability-detection)
  - [Kernel and Distribution Requirements](#kernel-and-distribution-requirements)
- [API Examples](#api-examples)
- [Alternatives](#alternatives)
- [Scalability](#scalability)
- [Update/Rollback Compatibility](#updaterollback-compatibility)
- [Functional Testing Approach](#functional-testing-approach)
- [Implementation History](#implementation-history)
- [Graduation Requirements](#graduation-requirements)

## Overview

This VEP extends KubeVirt's existing KSM (Kernel Same-page Merging) support with a new
`ZeroPagesOnly` mode that leverages the RHEL/CentOS Stream 9 `redhat_only_zero_pages` kernel
feature. This mode makes KSM practical and efficient for Windows guest workloads by restricting
page deduplication to zero-filled pages only, eliminating the expensive stable/unstable tree
operations that yield little benefit for Windows memory patterns.

This feature depends on a RHEL-only kernel patch (`redhat_only_zero_pages`) that is not
available upstream. See [Kernel and Distribution Requirements](#kernel-and-distribution-requirements)
for the full impact analysis.

## Motivation

KubeVirt has supported KSM since v1.1 via the `ksmConfiguration` field in the KubeVirt CR.
The current implementation has two limitations that prevent effective use in production:

### 1. Full KSM is expensive and ineffective for Windows guests

Standard KSM deduplicates all identical pages by maintaining stable and unstable comparison
trees. This is CPU-intensive and yields diminishing returns for Windows workloads:

- **Windows aggressively zeros memory.** The Windows kernel has a dedicated zero page thread
  (priority 0) that scrubs freed pages. `VirtualAlloc` guarantees zero-initialized memory.
  All physical pages are zeroed on boot. As a result, Windows VMs accumulate large pools of
  zero-filled memory.
- **Windows pages are mostly unique across VMs.** Unlike Linux VMs that share libc, kernel
  text, and other common libraries, Windows VMs have few non-zero identical pages across
  instances. Full tree comparison is wasted CPU.

RHEL 9.9+ kernels (CentOS Stream 9) include a `redhat_only_zero_pages` sysfs knob that
restricts KSM to only deduplicate zero-filled pages, skipping all stable/unstable tree
operations. This dramatically reduces CPU overhead while capturing the majority of memory
savings for Windows workloads.

### 2. The `redhat_only_zero_pages` kernel feature

RHEL 9.9 / CentOS Stream 9 introduced a `redhat_only_zero_pages` sysfs knob (authored by
Andrea Arcangeli, MR !8249). When enabled, KSM's scan loop changes fundamentally:

1. For each scanned page, compute the checksum as usual.
2. If `checksum == zero_checksum`, attempt to merge with the kernel zero page.
3. **Return immediately** — skip stable tree search, unstable tree search, and all
   cross-page comparison.

This turns KSM from a general-purpose memory deduplication engine into a lightweight
zero-page consolidation pass. The CPU cost drops dramatically because the expensive
operations (memcmp, tree insert/lookup, page locking for merge) are eliminated for all
non-zero pages.

This feature is **not proposed upstream** and currently exists only in RHEL/CentOS Stream
kernels. See [Kernel and Distribution Requirements](#kernel-and-distribution-requirements)
for the implications.

## Goals

1. Add a `ZeroPagesOnly` KSM mode that uses the `redhat_only_zero_pages` kernel feature,
   exposed as a first-class API field on `KSMConfiguration`.
2. Keep all changes backward compatible — existing `KSMConfiguration` with no `mode` field
   behaves identically to today.
3. As a secondary improvement, fix the sleep baseline scaling so that effective
   `sleep_millisecs` is consistent across host sizes without requiring per-node annotations.
   This benefits both modes but is not the primary motivation for this VEP.

## Non Goals

- Upstream the `redhat_only_zero_pages` kernel patch (this is a RHEL/CentOS kernel concern).
- Expose all KSM tuning parameters (pages boost/decay/min/max) in the API. These remain
  available as per-node annotation overrides for power users.
- Integrate with the VEP 190 plugin framework (no dependency on node hooks).
- Add Prometheus metrics for KSM (valuable but orthogonal, tracked separately).

## Definition of Users

- **Cluster administrator**: Manages OpenShift/KubeVirt clusters running mixed Linux and
  Windows workloads. Wants memory overcommit benefits without excessive CPU overhead.
- **KubeVirt developer**: Maintains the KSM subsystem in virt-handler.

## User Stories

- As a cluster admin running Windows VMs, I want KSM to deduplicate only zero-filled pages
  so I get memory savings without the CPU cost of full content comparison, which yields
  little benefit for Windows workloads.

- As a cluster admin with heterogeneous hardware (64GB–1TB nodes), I want KSM scan intervals
  to be consistent across all nodes without having to compute and manage per-node annotations.

- As a cluster admin, I want to enable zero-pages-only KSM with a single API field change,
  without deploying MachineConfigs or DaemonSets for sysfs configuration.

## Repos

kubevirt/kubevirt

## Design

### API Changes

A single new field `mode` is added to `KSMConfiguration`:

```go
// KSMMode selects the KSM operating mode.
// +enum
type KSMMode string

const (
    // KSMModeFull runs standard KSM: deduplicates all identical pages across
    // VMs using stable/unstable tree comparison. Higher CPU cost but maximum
    // memory savings. Best for homogeneous Linux workloads that share
    // libraries and kernel pages.
    KSMModeFull KSMMode = "Full"

    // KSMModeZeroPagesOnly restricts KSM to only deduplicate zero-filled
    // pages. Skips all stable/unstable tree operations for non-zero pages,
    // significantly reducing CPU overhead.
    //
    // Requires a kernel with redhat_only_zero_pages support (RHEL 9.9+ /
    // CentOS Stream 9). If the kernel lacks this feature, virt-handler
    // logs a warning and falls back to Full mode on that node.
    //
    // Particularly effective for Windows guests, which aggressively zero
    // memory via the zero page thread and VirtualAlloc, producing large
    // pools of zero-filled pages with few non-zero duplicates across VMs.
    // Also beneficial with init_on_free=1 Linux guests.
    //
    // In this mode, KSM runs continuously without pressure-gating, as
    // zero-page-only scanning has minimal CPU cost.
    KSMModeZeroPagesOnly KSMMode = "ZeroPagesOnly"
)

// KSMConfiguration holds information about KSM.
// +k8s:openapi-gen=true
type KSMConfiguration struct {
    // NodeLabelSelector is a selector that filters in which nodes KSM
    // will be enabled. Empty NodeLabelSelector will enable KSM for
    // every node.
    // +optional
    NodeLabelSelector *metav1.LabelSelector `json:"nodeLabelSelector,omitempty"`

    // Mode selects the KSM operating mode.
    // "Full" (default): standard KSM, deduplicates all identical pages.
    // "ZeroPagesOnly": only deduplicates zero-filled pages (RHEL 9.9+).
    // +optional
    Mode *KSMMode `json:"mode,omitempty"`
}
```

### KSM Mode: ZeroPagesOnly

When `mode: ZeroPagesOnly` is set, virt-handler changes its behavior on eligible nodes:

| Aspect | `Full` (default) | `ZeroPagesOnly` |
|--------|-----------------|-----------------|
| Activation | Pressure-gated: activates when `MemAvailable ≤ MemTotal × freePercent` | Always active (`run=1`) |
| `redhat_only_zero_pages` sysfs | `0` (untouched) | `1` |
| `pages_to_scan` | Ramps up/down based on pressure | Set to `nPagesMax` immediately |
| `sleep_millisecs` | Dynamic (see below) | Dynamic (see below) |
| Deactivation | Gradual ramp-down when pressure lifts | Only when config is removed or node becomes ineligible |

**Rationale for always-on:** Zero-page-only scanning skips all tree operations (no memcmp,
no stable/unstable tree insert/lookup). The per-page cost is just a checksum comparison
against the zero checksum. This is cheap enough that pressure-gating adds complexity without
meaningful CPU savings.

### Improvement: Dynamic Sleep Baseline Scaling

As an opportunistic improvement alongside the mode work, the current static sleep baseline
of 100ms is replaced with a host-proportional baseline. This is not central to the
`ZeroPagesOnly` feature but addresses a long-standing inconsistency that affects both modes.
In `ZeroPagesOnly` mode specifically, `sleep_millisecs` is the only throttle (since KSM is
always-on with max `pages_to_scan`), so getting this right matters.

The current static baseline produces wildly different effective sleep values across host
sizes (2.2s on 8GB vs 10ms floor on 1TB). The fix:

```go
totalGB := memStat.total / (1024 * 1024) // KB → GB
dynamicBaseline := totalGB * 2            // ≈ 1.875 rounded for integer math
```

This cancels out the host-size variable in the sleep formula:

```
sleep = dynamicBaseline * 16GB / usedMemory
      = (totalGB * 2) * 16GB / (totalGB * (1 - freePercent))
      = 32 / (1 - freePercent)               ← independent of host size
```

Effective sleep across all host sizes (8GB to 1TB):

| Free memory | Effective sleep |
|-------------|----------------|
| 10%         | ~36ms          |
| 20%         | ~40ms          |
| 50%         | ~64ms          |
| 70%         | ~107ms         |
| 90%         | ~320ms         |

The `kubevirt.io/ksm-sleep-ms-baseline-override` annotation continues to work and takes
precedence over the dynamic baseline when set, preserving the escape hatch for manual
tuning.

### Sysfs Management

virt-handler already manages three sysfs files via `/proc/1/root/sys/kernel/mm/ksm/`:

| File | Currently managed | After this VEP |
|------|-------------------|----------------|
| `run` | Yes | Yes |
| `sleep_millisecs` | Yes | Yes |
| `pages_to_scan` | Yes | Yes |
| `redhat_only_zero_pages` | No | Yes (when mode=ZeroPagesOnly) |

On deactivation (node becomes ineligible or config is removed), virt-handler resets
`redhat_only_zero_pages` to `0` if it was the one that enabled it (tracked via the
existing `kubevirt.io/ksm-handler-managed` annotation pattern).

### Fallback and Capability Detection

When `mode: ZeroPagesOnly` is configured but the node's kernel lacks
`/sys/kernel/mm/ksm/redhat_only_zero_pages`:

1. virt-handler logs a warning: `"ZeroPagesOnly mode requested but kernel lacks
   redhat_only_zero_pages support, falling back to Full mode on node <name>"`
2. Falls back to `Full` mode behavior on that node.
3. Sets a node annotation `kubevirt.io/ksm-mode-fallback: "Full"` for observability.

This allows rolling kernel upgrades — nodes with the new kernel get zero-pages-only mode,
older nodes get full KSM, and the admin can track convergence via the annotation.

### Kernel and Distribution Requirements

The `ZeroPagesOnly` mode depends on the `redhat_only_zero_pages` sysfs knob, which is a
RHEL-only kernel patch for now(authored by Andrea Arcangeli, CentOS Stream 9 MR !8249). It is
**not available in upstream Linux kernels** yet, when upstreamed it will use a different name.

**Availability:**

| Distribution | Kernel | `redhat_only_zero_pages` |
|-------------|--------|--------------------------|
| CentOS Stream 9 | 5.14.0-xxx | Available (MR !8249 merged) |
| RHEL 9.9+ | 5.14.0-xxx | Available |
| RHEL 9.8 and earlier | 5.14.0-xxx | Not available |
| Upstream Linux | 6.x | Not available |
| Ubuntu, SLES, etc. | Any | Not available |

**Implications for KubeVirt as a distro-agnostic project:**

- On non-RHEL kernels, virt-handler falls back to `Full` mode with a warning (see
  [Fallback and Capability Detection](#fallback-and-capability-detection)).
- The API is distro-agnostic — `mode: ZeroPagesOnly` is a valid setting on any cluster.
  The kernel capability is detected at runtime, not at admission time. This follows the
  same pattern as other KubeVirt features that degrade gracefully based on kernel/hardware
  capabilities (e.g., `ExpandDisks` with LVM, TSC frequency scaling).
- If `redhat_only_zero_pages` is upstreamed in the future, the feature becomes universally
  available without API changes.
- The `Full` mode, the sleep scaling improvement, and all existing KSM behavior are
  fully distro-agnostic and work on any kernel with standard KSM support.

**Impact on CI and testing:**

- Unit tests mock the sysfs paths and work on any kernel.
- Integration/e2e tests for `ZeroPagesOnly` mode require CentOS Stream 9 / RHEL 9.9+ nodes.
  These tests should be gated on kernel capability detection (checking for the sysfs file)
  or run in RHEL-specific CI lanes.
- All existing KSM tests (`Full` mode) continue to run on any kernel.

## Alternatives

### 1. MachineConfig + DaemonSet (current workaround)

Use a MachineConfig to write `redhat_only_zero_pages=1` to sysfs at boot, and a DaemonSet
to compute and set per-node annotation overrides for sleep baseline and free-percent.

**Pros:** Works today without code changes.
**Cons:** Three external components to manage, not portable across platforms, annotations
are fragile and not self-documenting.

### 2. VEP 190 node hooks

Use the plugin framework's `PreVMStart` hook to configure KSM sysfs and annotations on
first VM start.

**Pros:** Fits the plugin architecture.
**Cons:** Still requires a DaemonSet sidecar + Plugin CR. No node-init hook point means KSM
isn't configured until the first VM starts. `ExecuteNodeHookResponse` is empty so annotations
require out-of-band API calls. Strictly more components than Alternative 1.

### 3. Expose all KSM parameters in the API

Move `freePercentThreshold`, sleep baseline, pages boost/decay/min/max, and `use_zero_pages`
into `KSMConfiguration` as explicit fields.

**Pros:** Full declarative control.
**Cons:** Large API surface for parameters most users never touch. Removes the ability to
have per-node tuning without multiple label selectors. The dynamic baseline scaling
(this VEP) makes the sleep baseline parameter unnecessary for most cases.

### 4. Add only `use_zero_pages` support (without `redhat_only_zero_pages`)

Manage the existing upstream `use_zero_pages` sysfs knob instead of the RHEL-only
`redhat_only_zero_pages`.

**Pros:** Works on all kernels.
**Cons:** `use_zero_pages` adds zero-page merging to full KSM — it doesn't skip the
expensive tree operations for non-zero pages. You still pay the full CPU cost. The value
proposition of this VEP is specifically the reduced overhead from skipping non-zero
comparison.

## Scalability

No scalability concerns beyond the existing KSM implementation. The `ZeroPagesOnly` mode
actually reduces per-node CPU usage compared to full KSM, as it eliminates stable/unstable
tree operations. The 3-minute polling interval for the KSM handler loop is unchanged.

## Update/Rollback Compatibility

### Upgrade

- Existing clusters with `ksmConfiguration` and no `mode` field continue to use `Full` mode
  (the default). No behavior change from the mode feature on upgrade.
- The dynamic sleep baseline scaling is a minor behavior change for `Full` mode. On large
  hosts (>128GB), effective sleep will increase (less aggressive scanning). On small hosts
  (<16GB), sleep will decrease slightly. This is a correctness fix — the current behavior on
  large hosts is pathologically aggressive (hitting the 10ms floor). The change is
  independent of the `ZeroPagesOnly` feature and improves `Full` mode on its own.

### Rollback

- Removing the `mode` field reverts to `Full` mode.
- virt-handler resets `redhat_only_zero_pages` to `0` on deactivation (if handler-managed).
- The dynamic baseline formula is a code change with no API surface — rollback to a prior
  virt-handler version restores the old static baseline.

### Mixed-version clusters

During rolling upgrades, some virt-handler instances may not understand `mode`. The field
is ignored by older virt-handlers (they use `Full` mode). Once all virt-handlers are updated,
the new mode takes effect cluster-wide.

## Functional Testing Approach

### Unit tests (`pkg/virt-handler/ksm/ksm_test.go`)

1. **Mode selection:** Verify that `ZeroPagesOnly` mode writes `1` to
   `redhat_only_zero_pages` sysfs and keeps `run=1` regardless of memory pressure.
2. **Fallback:** Verify that when `redhat_only_zero_pages` sysfs does not exist,
   handler falls back to `Full` mode and logs a warning.
3. **Dynamic baseline:** Verify that sleep values are consistent across simulated
   host sizes (8GB, 128GB, 1TB) — all should produce values in the 33–320ms range.
4. **Deactivation:** Verify `redhat_only_zero_pages` is reset to `0` when the node
   becomes ineligible.
5. **Backward compatibility:** Verify that no `mode` field produces identical behavior
   to current `Full` mode with static baseline.

## Implementation History

- KSM support added in KubeVirt v1.1 (PR #10204).
- `redhat_only_zero_pages` kernel feature authored by Andrea Arcangeli, merged in
  CentOS Stream 9 (MR !8249)

## Graduation Requirements

### Alpha

- [ ] Feature gate: none required (extends existing `ksmConfiguration` API).
- [ ] `mode` field added to `KSMConfiguration` with `Full` (default) and `ZeroPagesOnly`.
- [ ] Dynamic sleep baseline scaling implemented.
- [ ] virt-handler manages `redhat_only_zero_pages` sysfs.
- [ ] Fallback to `Full` on kernels without `redhat_only_zero_pages`.
- [ ] Unit tests for all new code paths.

### Beta

- [ ] Confirmed working on relevant kernels.
- [ ] Documentation in user-guide.
- [ ] Feedback from at least one production deployment.

### GA

- [ ] Stable for two releases.
- [ ] No open bugs related to KSM mode selection.
