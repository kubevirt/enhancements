# Design Discussion: CPU Host-Allocation Correctness

This document records the design session behind [vep.md][vep-doc]'s CPU-related decisions. It is
kept separate from the main VEP document because it's a narrative and research record, not
committed design — useful for a reader who wants to understand *why* those decisions read the way
they do, but not required to understand the proposal itself.

Individual contributors are referred to by role rather than by name: **the author** is the person
driving this proposal; **Reviewer A**, **Reviewer B**, and **Reviewer C** are three distinct
positions raised during [VEP-300][vep-300]'s review thread
([kubevirt/enhancements#432][vep-300]).

## Design Session Narrative

### 1. The original disagreement (VEP-152/300 review)

Reviewer A raised that a mechanism-shaped CPU field on the VMI spec risks making that mechanism the
user-facing API — the same mistake `dedicatedCpuPlacement` avoided by not naming CPU Manager.
Reviewer A proposed keeping low-level CPU plumbing separate from claim generation/policy, selected
via label-matched policy objects (`MigrationPolicy`-style), with CPU "classes" or a "profile"
shorthand for the exclusive/shared split.

Reviewer B pushed back with a concrete counter-example: a 4-vCPU guest can run on either two shared
host CPUs (2:1 oversubscribed) or four exclusive host CPUs — a real policy choice a provisioner
could make. But if that choice isn't captured anywhere typed, virt-launcher (no kube-api access)
cannot determine, from an allocated compound claim alone, which domain vCPU should get an exclusive
pin and which should share a set. Reviewer B proposed a typed host-allocation-groups field,
explicitly **not** an annotation, because virt-launcher needs schema-validated, discoverable
structure to act on, not a string blob to parse. Reviewer C agreed and trimmed the proposed shape.

### 2. Generalizing the split

The author observed that the "separate guest-facing intent from host wiring" idea already applied
to GPUs/host devices/networks isn't CPU-specific, and suggested extending it as the default pattern
for all devices. Reviewer B asked for a concrete example demonstrating this on a device besides CPU
(network was suggested, since it doesn't carry CPU's extra host-allocation complexity) — this
request produced the `deviceProviderRef` generalization in [vep.md][vep-doc].

### 3. Untangling two different axes

Working through this, it became clear "the mapping" question actually splits into two independent
concerns that had been getting conflated:

- **Provider/backend selection** — who builds the concrete resource, what DeviceClass, what
  topology constraints. Legitimately admin/backend policy, and safely generalizable across every
  device type.
- **Guest-visible allocation intent** — for CPU specifically, which vCPU is in which
  exclusive/shared group. Can't be handed entirely to a provider, because virt-launcher has no way
  to independently verify or reconstruct it, and it's directly consequential to guest-side
  configuration (see [Why Silent vCPU Relabeling Matters](#why-silent-vcpu-relabeling-matters)
  below).

Using [VEP-190][vep-190]'s `GuestDefinition` hook to sidestep the
second concern entirely — letting a provider's sidecar mutate the domain spec directly, with the
grouping decided only inside the provider's own logic — was considered and rejected. Backend
resource sizing (how many exclusive vs. shared device requests to create) happens in a provisioner
controller *before* the pod — and therefore before virt-launcher or any hook — exists, so a
hook-only channel structurally cannot serve that earlier consumer. And for the hook itself to have
the grouping data, either the sidecar needs its own live kube-api access (reintroducing exactly the
trust surface being avoided) or virt-launcher needs the data already (which begs the question of
where it came from). [VEP-190][vep-190]'s own explicit non-goal — no arbitrary, unvalidated XML modification —
reinforced treating the hook as an *execution* mechanism for an already-validated intent, not a
substitute for one.

### 4. Does authoring need to be per-VM?

The author pushed back that per-VM authored mapping doesn't operate at production scale (hundreds
to thousands of VMs) and reopens the ownership question the provider pattern exists to solve.
Re-examining Reviewer B's own two-VMI example showed it doesn't actually require per-VM uniqueness
— it requires *some* VMs to have one grouping and *other* VMs to have a different one, which a
small number of admin-defined provider configurations already covers, the same way a handful of
`StorageClass` objects covers wildly different storage needs without per-PVC bespoke config. So the
disagreement wasn't really "per-VM vs. shared configuration" — Reviewer B's actual invariant,
restated more precisely, is: *something typed and validated, scoped to a specific VMI instance, has
to exist for virt-launcher to trust* — not that a specific VM owner has to author it by hand.

### 5. Where does the resolved, per-instance answer live?

This reframing still leaves a question: given shared authoring, where does the concrete, per-VMI
resolved answer live for virt-launcher to read? The first candidate explored was **VMI status**,
written by whichever component resolves the configuration for a given VMI — reasonable because
virt-launcher already receives the full VMI object (spec and status) through virt-handler's
existing local sync channel, adding no new API capability to virt-launcher itself.

This was rejected once migration was considered: a VMI can have two live pods (source and target)
during migration, and a single VMI-status field describing a *desired* mapping can't hold two
independently-computed answers without new schema complexity.

## Why Silent vCPU Relabeling Matters

The concern raised in [§3](#3-untangling-two-different-axes) can sound abstract ("what if two
mappings produce the same claim shape") without a concrete failure mode attached. This is the
failure mode.

Workloads that need CPU DRA / exclusive placement in the first place — NFV dataplane, DPDK,
real-time guests — routinely pin application threads to specific vCPU *numbers* inside the guest:
`isolcpus=`, `taskset`, a DPDK core mask configuration file. That configuration assumes vCPU N is,
and remains, the guest's dedicated/isolated core, because it was told so once (typically at image
build or first-boot config time) and has no way to re-discover it later.

If an in-guest reboot (the same pod, the same already-allocated claim) or, eventually, a live
migration silently reassigns which vCPU carries the exclusive/shared property — while some
verification step reports "unchanged" because it only looked at claim shape, which didn't change —
the pinned application keeps running exactly where its static configuration says to, on a vCPU that
is now the oversubscribed one instead of the exclusive one. Nothing errors. No event fires. No
condition flips. Every piece of Kubernetes-visible state says the VM is fine. The only observable
symptom is unexplained latency/jitter regression in the workload, with no correlated signal
anywhere in the system to point at the actual cause.

This is why "the check said nothing changed" is worse than no check at all in this specific case —
it produces false confidence exactly in the scenario (a silent, unintended change from something
like a provider controller upgrade) the check exists to catch. It's also why "the change was
intentional" doesn't fully answer the concern: an admin deliberately updating a shared policy
object is a decision about the policy, not necessarily a decision that every already-running VM
referencing it should have its guest-visible pinning identity silently swapped at its next
incidental reboot. This remains an open, unsolved problem — see [vep.md][vep-doc]'s Non Goals.

[vep-190]: ../190-kubevirt-structured-plugins/vep.md
[vep-300]: https://github.com/kubevirt/enhancements/pull/432
[vep-doc]: vep.md
