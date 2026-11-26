# VEP #540: Align network state lifetime with the virt-launcher pod

## VEP Status Metadata

### Target releases

This VEP changes internal implementation details of network setup and adds no user-facing API. It is
**not guarded by a feature gate** — there is no toggle, and it does not graduate through feature-gated
alpha/beta/GA stages. The new behavior is active in every release that includes it. Rollout is
sequenced instead by delivery milestones and a bounded backward-compatibility window (see
Update/Rollback Compatibility and Graduation Requirements).

- Initial-change target version: 1.11
- Compatibility-shim removal target version (initial + 3 launcher versions): 1.14

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Initial-change target version is explicitly mentioned and approved
- [X] (R) Backward-compatibility window (3 launcher versions) and the shim-removal target are agreed and approved

## Overview

During network setup, virt-handler persists per-pod network state to on-disk caches. There are
three of them, with three distinct roles and two different locations:

1. **Network config-state cache**: records how far network preparation has progressed for each
   network — `Pending`/`Started`/`Finished`. Produced and consumed by **virt-handler**, to resume
   or skip setup across reconciles and restarts.
2. **Pod-interface info cache**: the pod interface's discovered IPs. Produced by **virt-handler**
   during discovery, consumed by **virt-handler**'s status loop on every reconcile to populate
   `vmi.Status.Interfaces`.
3. **DHCP config cache (bridge binding)**: the per-interface DHCP parameters discovered for
   *bridge*-bound interfaces. Produced by **virt-handler** during discovery, consumed by
   **virt-launcher** to run the in-pod DHCP server for the guest. This one is genuinely
   cross-component. (Masquerade binding builds its DHCP config inside virt-launcher and persists no
   cache, so it needs none of this.)

Caches 1 and 2 share one file on virt-handler's **node-local** filesystem, keyed by VMI UID, at
`/var/run/kubevirt-private/network-info-cache/<vmi-uid>/<network-name>`. Cache 3 is already
**pod-local**: virt-handler writes it into the launcher pod over procfs, at
`/proc/<launcher-pid>/root/var/run/kubevirt-private/vif-cache-<iface>.json`, and virt-launcher reads
it back from its own root (`/proc/self/root/var/run/kubevirt-private/vif-cache-<iface>.json`).

The common thread, and the thing this VEP targets, is that **virt-handler treats on-disk files as
the durable store of record for network state** — either on the node (caches 1, 2) or inside the
launcher's pod, written by virt-handler over procfs (cache 3). That single choice is behind the
reliability problems below. This VEP proposes that virt-handler stop being that store: keep what it
needs in memory, rebuild it from authoritative sources, and hand pod-scoped state to virt-launcher
over the existing control channel rather than writing files.

## Motivation

The three caches fail in related ways, all rooted in where/how the state is stored.

### Config-state: wrong lifetime (too long)

The config-state file lives on the node, keyed by VMI UID, and is reclaimed only when the VMI is torn
down. Its lifetime is therefore longer than the thing it describes — a specific launcher pod's
network namespace. A stale `Finished` left by a dead pod makes a replacement pod on the same node
skip setup entirely:

- A VM with `runStrategy: Always` shut down from inside the guest gets a new virt-launcher pod in a
  fresh network namespace. The old node-local file
  (`/var/run/kubevirt-private/network-info-cache/<vmi-uid>/...`) still exists, so the new pod's setup
  reads `Finished`, returns early, and never installs masquerade/NAT. The VM answers ICMP but not TCP.
  (kubevirt/kubevirt#17888, kubevirt/kubevirt#18906)
- The migration target is the same shape: a failed target attempt can leave stale node-local network
  state behind, which a subsequent attempt reuses. kubevirt/kubevirt#18103 fixed one such loop (its
  direct cause was migration control-flow, and the surfaced `vif-cache … no such file` was a symptom),
  but the reused stale target-side state is the same lifetime problem — bounding the state to the pod
  removes it as a contributing factor.
- Keying the file by VMI UID *and* pod UID was explored (kubevirt/kubevirt#18051) and moving the
  cache into the launcher pod was prototyped (kubevirt/kubevirt#19023). Both treat the symptom from
  one side; this VEP addresses the underlying "virt-handler owns a durable file" choice.

### Config-state: can be torn, and then kills a healthy VMI

Setup marks a network `Started` *before* configuring the namespace and `Finished` *after*. If
virt-handler crashes (or the write is lost) between the two, the next reconcile reads `Started`; for
a network not marked for removal the setup path treats that as unrecoverable and **fails the VMI,
tearing down a pod whose network was actually healthy**. Persisting the marker on disk makes the
inconsistent state survive the very restart that could have recovered from it. This is precisely the
failure this VEP sets out to prevent: virt-handler crashes around a write and the on-disk record is
left wrong or lost.

### Pod-interface IPs: persisted only to avoid wiping status

virt-handler's status loop runs on every reconcile and overlays the cached pod IPs onto
`vmi.Status.Interfaces`. The IPs are written once at discovery and then kept in memory and treated as
immutable. The on-disk copy exists for exactly one reason: after a virt-handler restart the in-memory
cache is empty, and without the file the status loop would set the interface IP to empty and **wipe
the reported IPs**. But that information is not unique to the file — it is present in the live pod
network namespace (the authoritative source, with the pod's own lifetime) and can be recovered from
there after a restart. The node-local file is a durable copy of state that is reconstructable from
reality — the same anti-pattern as the config-state cache.

### DHCP config: an out-of-band write from virt-handler into the pod

The DHCP cache must cross from virt-handler (which discovers it) to virt-launcher (which serves it).
Today that crossing is done by having virt-handler reach into the launcher pod over procfs and write a
file there — an out-of-band, cross-boundary write into a filesystem another component owns, rather
than an explicit handoff. It is also the one place virt-handler writes into the pod at all. Relocating
caches 1 and 2 to launcher-rooted paths (the #19023 shape) would add more such cross-boundary writes
instead of removing them; this VEP goes the other way.

## Goals

1. virt-handler no longer relies on a node-local on-disk file as the durable store of record for
   network state.
2. Each cache's effective lifetime matches the launcher pod it describes: a replacement pod (same
   VMI UID, same node) always performs a full setup; nothing a dead pod wrote is reused.
3. A virt-handler restart or crash never, by itself, fails a healthy VMI or drops its reported IPs.
4. Do not add virt-handler writes into the launcher pod's filesystem, and ultimately remove the one
   that exists today; prefer passing state over the control channel.
5. Keep it internal: no VMI API changes; no new persisted node-local artifacts keyed by VMI UID.

## Non Goals

1. Change *what* state is tracked — the `Pending`/`Started`/`Finished` model, the pod IP fields, the
   DHCP configuration contents, or the `vmi.Status.Interfaces` reporting contract.
2. Make the netlink-level setup operations themselves idempotent/retriable (tracked separately,
   e.g. kubevirt/kubevirt#18906). This VEP constrains where state lives and who writes it; it does
   not rework the setup steps.
3. Introduce a user-facing feature gate or API.

## Definition of Users

This is an internal reliability improvement. The affected "user" is the VM owner, who should never
lose connectivity or reported IPs because of a cache lifetime/durability mismatch, and the KubeVirt
developer maintaining network setup.

## User Stories

- As a VM owner, I never lose connectivity to my running VM because virt-handler reused stale
  network state from a previous, now-dead pod.
- As a VM owner using `runStrategy: Always`, a guest-initiated shutdown and restart yields a pod
  whose network is fully configured.
- As a VM owner, a virt-handler crash or restart never fails my healthy VMI and never blanks the
  IPs reported in its status.

## Repos

kubevirt/kubevirt.

## Design

### Principle

virt-handler remains the **producer and decider** — it runs privileged setup in the pod network
namespace, discovers IPs and DHCP parameters, and decides setup progress. It stops being the
**durable store**. Concretely, each piece of state is served from the source whose lifetime already
matches it:

- What virt-handler needs for itself → **in-memory**, rebuilt on restart from the authoritative
  source with the right lifetime: the live pod itself (its network namespace, and — where virt-handler
  must read pod-local files — the launcher's root over procfs).
- What must reach virt-launcher, or must persist for the pod's life → **handed to virt-launcher**
  over the existing virt-handler↔virt-launcher control channel (the same channel that already drives
  the launcher's in-pod network setup phase), and owned by **virt-launcher** — kept in memory or, if
  persisted at all, written by virt-launcher into its own filesystem.

Each component writes only to its own filesystem; virt-handler does not write into the launcher pod.

### Cache 1 — config-state: in-memory + reconcile-from-reality

Keep the config state in virt-handler's memory and drop the node-local file. The durable truth that
survives a virt-handler restart is **the pod's actual network namespace**, which virt-handler already
reads during setup:

- On a cold cache, a network's state is derived from observed reality rather than a file. The signal
  is the **kubevirt-created interfaces** (the bridge, tap, and dummy that setup adds, each tagged as
  kubevirt-owned), not the pod IP: a fresh pod's namespace has none of them ⇒ `Pending` (full setup
  runs); a namespace carrying the **complete** configured topology ⇒ `Finished` (setup is skipped).
  The IP relocation *helps* here — a dummy interface holding the moved pod IP is itself proof setup
  ran. A replacement pod is a new namespace, so stale `Finished` is impossible by construction — no
  pod-UID keying needed. (The in-between, partially-configured case is handled below.)
- `Started` is now an **in-memory, single-lifetime** hint, not a persisted verdict. The Motivation
  failure — a crash loses the `Finished` write, leaving a persisted `Started` that fails the VMI on
  the next reconcile — cannot occur, because there is no persisted `Started`. After a restart the
  marker is simply absent and state is inferred from the namespace; if apply had in fact completed,
  the namespace shows the complete topology ⇒ `Finished`, so no healthy VMI is failed. Within one
  lifetime, a re-reconcile that sees an in-memory `Started` likewise reconciles against the namespace
  instead of failing — subject to the partial-configuration caveat below.

The clean cases are unambiguous: a pristine namespace is `Pending`, and a namespace that already
matches the desired configuration is `Finished`. The hard case is a **partially** configured
namespace — a crash in the middle of applying it. Deciding "resume safely" there requires the apply
path to tolerate re-entry, and parts of it currently do not: the declarative network-state apply
converges, but the preparatory steps are not idempotent — setup moves the pod IP onto a dummy
interface and removes addresses, so a second pass sees a namespace that no longer looks like the
original pod and can mis-discover or blank data. This is exactly why the present code refuses to
restart a network already marked `Started`. Two ways to make reconcile safe for the partial case, in
preference order:

1. Make the preparatory steps idempotent/re-entrant (detect "already moved" and no-op). This is the
   clean fix but overlaps the separately-tracked idempotency work (Non Goal 2), so it may not land
   first.
2. Until then, keep a durable `Started`/`Finished` marker, but owned and written by **virt-launcher**
   pod-local (via Cache 3's mechanism), read back over procfs — never a node-local virt-handler
   file. This preserves "don't re-run a half-done non-idempotent setup" without the node-local
   durability race.

So: reconcile-from-reality settles the common lifetime bugs (stale `Finished`, fresh-pod setup)
immediately; the partial-crash corner is closed by (1) or, in the interim, (2). Which of the two to
commit to — make preparation idempotent now, or ship the interim launcher-owned marker first — is
left open for sig-network discussion.

### Cache 2 — pod-interface IPs: in-memory + recover from the live namespace

**Reporting is unchanged in shape.** virt-handler discovers the pod IPs during setup, holds them in
memory, and its status loop writes them into `vmi.Status.Interfaces` on every reconcile — exactly as
today. What changes is only how the in-memory copy is repopulated after a virt-handler restart.

Drop the node-local file and keep the IPs only in memory. On a cold cache the IPs are recovered the
same way Cache 1's config-state is — **from the live pod network namespace**, not from a persisted
file and not from the reported `vmi.Status.Interfaces`. Reporting into the status is not the same as
trusting it back as input: the status IP can be merged with or overridden by guest-agent data, so it
is not a faithful source of the *pod* IP; the namespace is.

Where the pod IP lives post-setup is binding-specific, and virt-handler knows the binding. Bridge
binding relocates the address during setup — discovery captures the CNI interface's IP *before*
configuration, then configuration disables it on that interface and moves it onto a kubevirt-created
dummy interface — so recovery reads the dummy. Masquerade leaves the pod IP on the pod interface, so
recovery reads that. Either way the address is present in the namespace of a fully-configured pod and
is read with the same observed-state pass used for the Cache 1 reconcile.

- On a cold cache, the status loop must **not** blank an interface's IP; it repopulates from the
  namespace before reporting. This removes one of the two node-local files entirely and takes it off
  the disk-race surface.
- The only gap is the **partial-configuration** case — a crash after the IP is disabled on the
  original interface but before it lands on the dummy, where the address is momentarily nowhere. That
  is the same partial-crash corner as Cache 1, closed by the same resolution (idempotent re-entry, or
  an interim launcher-owned marker). Outside it, a fully-configured namespace always yields the IP,
  and an unconfigured or partly-configured one triggers setup/resume, which rediscovers it. The VM is
  unaffected regardless (the guest already holds the IP via DHCP); only status reporting would briefly
  lag until recovery runs.

### Cache 3 — DHCP config (bridge binding): pass it on the gRPC channel

This applies to **bridge** binding — the only case that persists a DHCP file today. Masquerade
binding already builds its DHCP config inside virt-launcher at the in-pod phase and needs no handoff,
so it is unaffected.

Keep the DHCP config pod-local (correct already), but replace the *out-of-band write into the pod*
by passing the config **on the existing gRPC channel**. Today the split is: virt-handler does
privileged first-phase setup and discovers the DHCP configuration; virt-launcher runs the second,
in-pod phase, triggered by virt-handler's sync call over the gRPC channel. That second phase already
consumes a per-interface network-setup hint that virt-handler computes and attaches to the sync
request (and also attaches, separately, for the migration target). The discovered DHCP configuration
is per-interface in exactly the same way and rides the same path:

- **Producer.** First-phase setup already discovers the DHCP configuration; instead of only writing
  it to a file as a side effect, it returns it to the caller. virt-handler attaches the per-interface
  configuration to the sync request right where it already attaches the existing per-interface hint.
- **Consumer.** virt-launcher takes the configuration from the sync request instead of reading the
  file, and starts the DHCP server from it. virt-launcher may still write it to its own filesystem
  (unprivileged) if a file is convenient for the DHCP server, or keep it in memory.
- **Restart safety.** If virt-handler crashes after first-phase setup but before the launcher
  consumes the config — and then skips setup on reconcile because the namespace already looks
  configured — the in-memory config would be lost. This is covered because the DHCP configuration is
  **re-derivable from the configured namespace** (the same IP/routes/MTU/gateway data, read the same
  way as the Cache 2 recovery), and the launcher's "ensure the DHCP server is started" step is
  idempotent. virt-handler re-derives and re-sends it on the next sync; a duplicate send is a no-op.
  No persisted virt-handler file is needed to bridge that window.
- virt-handler stops writing the DHCP file into the launcher pod over procfs, so its one write into
  the launcher's filesystem is gone — the handoff is explicit instead of out-of-band.

This reuses an established pattern (a per-interface network hint already flows through the sync
request into the launcher's second phase), adds no new RPC, and makes the handoff explicit and typed
rather than an out-of-band file drop.

### Teardown and unplug

- **Full VMI teardown.** virt-handler drops its in-memory config-state and pod-IP entries for the
  VMI. There are no node-local files of ours left to remove (during the transition, any legacy
  node-local directory is cleaned up here). The bridge DHCP config lives in the launcher pod's own
  filesystem and is reclaimed by the kernel when the pod is deleted, so there is nothing for
  virt-handler to clean up inside the pod.
- **Per-interface unplug (hot-unplug).** virt-handler drops the in-memory entries for the unplugged
  network. Note what unplug does *not* do today: the bridge DHCP server is started once as a
  goroutine in virt-launcher and is **never stopped per-interface** — it runs until the pod is
  deleted. On unplug today, virt-handler only deletes the `vif-cache` file; that does not stop the
  running server. Under this VEP there is no `vif-cache` file, so that delete step simply goes away;
  the DHCP server's lifecycle is unchanged (still pod-lifetime). Adding a per-interface DHCP-server
  stop does not exist today and is out of scope here.
- **Crash safety.** Teardown and unplug are reconciled from reality like setup: if virt-handler
  crashes mid-unplug, the in-memory marker is gone on restart, and it re-derives from the namespace
  and the spec (interface still present but marked for removal ⇒ resume the unplug). No durable
  "unplug in progress" marker is needed, so there is no torn-write race on teardown either.

### Channel plumbing

virt-handler already resolves what it needs to reach the launcher's root over procfs, and already
drives virt-launcher over the gRPC sync channel. Both are reused; no new package-level mutable state
(as in the #19023 prototype) is introduced — the per-pod reference is passed explicitly where a
procfs read remains.

## API Examples

No user-facing API change. The internal storage changes as follows:

| Cache | Before | After |
|---|---|---|
| Config-state | virt-handler file, node-local, keyed by VMI UID, outlives the pod: `/var/run/kubevirt-private/network-info-cache/<vmi-uid>/<network-name>` | virt-handler in-memory; cold-cache truth = observed pod network namespace |
| Pod-interface IPs | virt-handler file, node-local (same file as above) | virt-handler in-memory; cold-cache recovery = re-derived from the live pod namespace |
| DHCP config | virt-handler writes into the pod over procfs: `/proc/<launcher-pid>/root/var/run/kubevirt-private/vif-cache-<iface>.json` | virt-handler passes it in the gRPC sync request; virt-launcher uses it in its in-pod phase (own filesystem/in-memory, unprivileged) |

## Alternatives

- **Move caches 1 and 2 into the pod, written by virt-handler over procfs** (the #19023 shape).
  Fixes lifetime, but adds two more virt-handler writes into the launcher's filesystem, and still
  requires reconciling `Started`. Rejected in favor of in-memory + authoritative-source rebuild, which
  needs no write into the pod at all.
- **Key the node-local cache by VMI UID and pod UID** (kubevirt/kubevirt#18051). Forces fresh setup
  per pod but leaves state on the node and keeps the torn-write/crash race. Treats one symptom.
- **Recover pod IPs by reseeding from `vmi.Status.Interfaces`** instead of re-deriving from the live
  namespace. *Pros:* binding-agnostic (one recovery path for all bindings), and it reuses a store
  virt-handler already writes. *Cons:* the reported status IP is not a faithful source of the *pod*
  IP — it can be merged with or overridden by guest-agent data — so recovery could restore the wrong
  value; it also makes the report its own recovery input, perpetuating any earlier error. Rejected in
  favor of namespace recovery, which yields the true pod IP and unifies with the Cache 1 reconcile;
  the cost is that recovery is binding-aware (virt-handler reads the dummy vs. the pod interface), but
  virt-handler already knows the binding.
- **Persist to a new owned CR.** Durable and non-node-local, but adds an API object and write
  amplification per interface for what the VMI status and the live pod already express. Rejected as
  heavier than reusing existing authoritative sources.
- **Keep virt-handler the writer everywhere** (writing into the pod over procfs as today). Avoids the
  handoff work but keeps virt-handler as the on-disk store of record writing into the launcher's
  filesystem, which is the pattern this VEP sets out to leave behind. Kept only as an interim fallback
  for the DHCP handoff if the gRPC path cannot land in one step.

## Does it belong to core KubeVirt?

Yes. These caches are internal to KubeVirt's network setup, written and consumed by virt-handler and
virt-launcher. There is no external surface and no plugin boundary; the behavior is core to how
KubeVirt configures and reports VM networking.

## Scalability

Neutral to positive. De-persisting caches 1 and 2 removes per-interface node-local file I/O and the
per-VMI cleanup of node-local directories. The DHCP handoff reuses an existing control channel and
per-interface payload. Cold-cache recovery reads the live pod namespace — the same observed-state
pass virt-handler already performs during setup/reconcile — and only on a cold cache, not every
reconcile.

## Update/Rollback Compatibility

The change spans virt-handler and virt-launcher, which upgrade independently, so version skew in
**both** directions must be handled. The guiding rule: a **reader always tolerates both the new and
the old source**, and a **writer keeps emitting the old source** until the components that depend on
it have aged out. For the DHCP handoff that means two compatibility shims, removable on two different
schedules:

- virt-handler keeps **writing** the DHCP file so *old launchers* can still read it —
  removable only once no supported launcher that old remains (gated by the **launcher** version
  window; this is the long-lived direction, since a launcher persists until its VMI migrates or
  restarts).
- virt-launcher keeps **reading** the DHCP file as a fallback so it still works behind an *old
  handler* that does not send the gRPC field — removable only once no supported handler that old
  remains (gated by the **handler** version window; this is the transient direction, seen during a
  rollout).

A single virt-handler serves pods of mixed launcher versions, so during the transition it does both:
sends the config in the sync request *and* writes the file; new launchers prefer the request field.

- **In-flight VMIs across a virt-handler upgrade (de-persisting caches 1 & 2).** The pre-upgrade
  virt-handler wrote node-local files; the upgraded one restarts with a cold in-memory cache. It
  rebuilds config-state by reconciling the live namespace (already-configured ⇒ `Finished`, so setup
  is not needlessly re-run) and pod IPs by re-deriving them from the live namespace. The legacy
  node-local file is read only as a secondary fallback and its directory is cleaned up during the
  transition. No VMI is disrupted and no reported IP is blanked.
- **DHCP handoff, new virt-handler + old virt-launcher pod** (the common rollout case: node
  virt-handler updated, existing launcher pods not yet recycled). The old launcher still expects to
  read the DHCP file and ignores the new sync-request field, so virt-handler **keeps writing the DHCP
  file over procfs until the pod recycles**.
- **New launcher landing on an old handler.** During a rollout the control plane (and thus the
  virt-launcher image used for newly created pods) can be upgraded before a given node's virt-handler,
  so a brand-new launcher pod can be scheduled on a node whose handler is still old and only writes
  the DHCP file. The new launcher must therefore **never assume the gRPC field is present**: it uses
  the field when present and otherwise reads the DHCP file. This file-read fallback must remain in the
  launcher for the whole handler-skew window — it cannot be dropped in the same step that introduces
  the handoff. The case is transient (the handler DaemonSet rolls quickly) but must be correct.
- **Migration across skewed nodes.** Network state is per-node/per-pod and rebuilt by the target
  node's virt-handler against the target pod; a VM migrating between a new and an old node is set up
  fresh on the target by that node's own logic, so mixed-version migration needs no shared cache.
- **Bounded compatibility window.** "Until the pod recycles" is unbounded on its own — a long-running
  VMI may never migrate or restart — so the handler-side shim is bounded to a fixed number of
  **launcher versions**: virt-handler maintains backward compatibility with (keeps writing the DHCP
  file for) launchers up to **3 versions** behind it, and no further. This matches KubeVirt's
  supported launcher version-skew window. After the cutoff,
  virt-handler no longer writes the DHCP file, so a bridge-bound launcher more than 3 versions behind
  — one not migrated or restarted within the window — can no longer receive its DHCP config and is
  explicitly **no longer supported**. This matches KubeVirt's existing expectation that running VMIs
  are live-migrated or restarted to adopt new launchers within the support window; the only hard
  cross-component dependency left by then is bridge-binding DHCP (e.g. a NIC hotplug on such a stale
  launcher would fail). The launcher-side file-read fallback is removed on its own, shorter schedule,
  gated on the handler-skew window (no supported handler still omits the gRPC field). Caches 1 and 2
  carry no launcher dependency, so their node-local shims can be dropped without any cross-component
  break.
- **Rollback.** A downgraded virt-handler reads node-local files again; KubeVirt does not support
  feature-gate rollback as a first-class flow. Because the authoritative sources (live pod, VMI
  status) are intact, the worst case on version skew is a redundant (reconciled-from-reality) setup,
  not a lost or stale configuration.

## Functional Testing Approach

- **Unit tests**
  - config-state: cold cache with an unconfigured namespace ⇒ `Pending`; with a configured namespace
    ⇒ `Finished`/skip; `Started` after a simulated crash ⇒ reconcile, not VMI failure.
  - pod IPs: cold cache ⇒ IPs re-derived from the live namespace, never blanked (dummy interface for
    bridge binding, pod interface for masquerade); the status IP is not trusted back as the recovery
    source.
  - DHCP: virt-launcher takes the config from the sync request; virt-handler does not write it into
    the pod (handoff path), with the file fallback exercised for the skew cases.

## Implementation History

<!--
dd-mm-yyyy: description. PR: <LINK>
-->

- Prior art: kubevirt/kubevirt#18051 (VMI-UID + pod-UID keying, closed), kubevirt/kubevirt#19023
  (move cache into launcher pod over procfs, prototype).
- Related bugs: kubevirt/kubevirt#17888, kubevirt/kubevirt#18906, kubevirt/kubevirt#18103
  (migration-target loop; stale node-local network state and a missing DHCP file were symptoms).

## Graduation Requirements

This VEP is **not feature-gated**, so it does not graduate through feature-gate alpha/beta/GA stages.
The items below are delivery milestones sequenced across releases — the behavior is on for everyone in
each release that ships it, and the only multi-release element is the bounded compatibility window.

### Phase 1 — in-memory caches and reconcile-from-reality

- [ ] Config-state served in-memory with cold-cache state derived from the observed pod network
      namespace; no node-local config-state file; `Started` reconciled rather than fatal.
- [ ] Pod IPs served in-memory with cold-cache recovery by re-deriving from the live pod namespace
      (not the reported VMI status); no node-local IP file; IPs never blanked across a virt-handler
      restart.
- [ ] Replacement pod on the same node performs full setup (no stale `Finished`).
- [ ] Legacy node-local read-fallback and cleanup in place for upgrade.
- [ ] Unit tests.

### Phase 2 — DHCP handoff over gRPC

- [ ] DHCP config delivered to virt-launcher in the gRPC sync request and used by the in-pod phase;
      new launchers prefer it. The procfs DHCP write is retained only as the transition fallback for
      pre-upgrade launchers.

### Phase 3 — remove compatibility shims (gated on version-skew windows)

- [ ] Launcher-compatibility window (3 launcher versions) confirmed with sig-network — how many
      launcher versions behind virt-handler keeps writing the DHCP file — and documented as the point
      beyond which older launchers are unsupported.
- [ ] Once no launcher within 3 versions still needs it, virt-handler stops writing the DHCP file;
      bridge-bound launchers more than 3 versions behind are no longer supported.
- [ ] Once no supported handler still omits the gRPC DHCP field, the launcher's file-read fallback is
      removed (separate, shorter handler-skew schedule).
- [ ] Legacy node-local read-fallback and cleanup removed (no launcher dependency, so not gated on the
      windows above).
- [ ] No node-local, VMI-UID-keyed network state remains, and virt-handler no longer writes any cache
      into the launcher pod's filesystem.
