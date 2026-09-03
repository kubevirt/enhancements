# VEP #434: Mergeable Guest Memory

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue [#434](https://github.com/kubevirt/enhancements/issues/434) created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

KubeVirt can turn Kernel Same-page Merging (KSM) on for a node, or it can be managed
externally, but every VM on that node is a merge candidate. Security- and
latency-sensitive guests have no per-VM way to keep their RAM unshared. This VEP adds
that control as a VMI annotation.

## Motivation

KSM is a density tool: identical pages across VMs are collapsed to one
copy-on-write page. KubeVirt already exposes that at cluster scope via
[`ksmConfiguration`](https://kubevirt.io/user-guide/cluster_admin/ksm/).
QEMU, however, registers *every* guest's RAM as mergeable unless told
otherwise. The result is all-or-nothing: enable KSM on the node and every VM
participates; leave it off and nobody benefits.

That is a poor fit for mixed clusters.

- **Isolation.** Deduplication timing has been used as a cross-VM side
  channel ([USENIX Security 2011](https://www.usenix.org/legacy/event/sec11/tech/full_papers/Suzaki.pdf),
  later practical attacks). A tenant that must not leak through shared pages
  currently has to disable KSM for the whole node.
- **Latency.** A write to a merged page faults a private copy (`cow_ksm` in
  [kernel KSM docs](https://docs.kernel.org/admin-guide/mm/ksm.html)). Guests
  that care about tail latency pay that cost for a density feature they do
  not want.
- **Wasted scan CPU.** A VM with a unique working set still sits in ksmd's
  candidate set. The scan helps nobody and still consumes host CPU.


## Goals

- A VM owner can opt a VM out of host page merging. After the VM is running,
  its guest RAM is not a KSM candidate, including on nodes where KSM is
  enabled.
- VMs that do not opt out keep today's merging behavior.
- The opt-out is a VMI annotation, so it travels with the VM object (create,
  restart, migrate) and can be set from a preference.

## Non Goals

- **Cluster-wide KSM enablement and ksmd tunables** — rejected. That API
  already exists (`ksmConfiguration`). This VEP only adds the missing per-VM
  opt-out.
- **Virtiofs / passt shared memory mappings** — rejected. Those need
  `MAP_SHARED` so a device backend can see guest RAM. That is not content
  merging and is already configured separately. See Design.
- **Sharing-aware live migration** — deferred. Preserving already-merged pages
  across a migration is a different problem
  ([kubevirt/kubevirt#13022](https://github.com/kubevirt/kubevirt/issues/13022)).
- **A `spec.domain.memory` CRD field** — rejected. This is a host memory
  management hint, not guest sizing or backing (`guest` / `maxGuest` /
  `hugepages`). See Alternatives.
- **Instancetype / preference API fields** — deferred. A preference can
  already carry the annotation. A dedicated preference field can wait.
- **Confidential computing / encrypted memory** — rejected. The opt-out
  does not hide pages from the hypervisor. Use `launchSecurity` for that.

## Definition of Users

- **VM owners** of security- or latency-sensitive guests who need one VM
  excluded from merging without changing the node.
- **Cluster administrators** who keep KSM on for density and need a subset of
  VMs to opt out.
- **Platform operators** moving libvirt domains that already disable shared
  pages onto KubeVirt.

## User Stories

- As a VM owner of a multi-tenant guest, I need this VM's RAM excluded from
  host page merging so identical-content pages cannot be used as a cross-VM
  signal.
- As a VM owner of a latency-sensitive guest, I need this VM excluded from
  merging so a write does not take a CoW fault on a previously merged page.
- As a cluster admin who enabled KSM for density, I need individual VMs to
  opt out so I do not have to disable KSM on the whole node.

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt/)

## Design

### Annotation

Add `kubevirt.io/mergeable-memory` on the VMI (and VM template). This is a
host memory-management hint, similar to `kubevirt.io/memfd`.
Value `"false"` opts out:

```yaml
metadata:
  annotations:
    kubevirt.io/mergeable-memory: "false"
```

The name matches kernel `MADV_MERGEABLE` and QEMU `mem-merge`. It is not
`KSM` (a kernel feature) and not `shareable` (easy to confuse with virtiofs
`MAP_SHARED`).

| Annotation | Effect |
| --- | --- |
| omitted | Today's behavior: pages may be merged (on compatible guests) |
| `"true"` | Same as omitted |
| `"false"` | Pages are not merge candidates |

QEMU applies the merge policy at machine creation; changing the annotation
on a running VMI has no effect until restart. A
`VirtualMachinePreference` can set the annotation so many VMs opt out
without a CRD field.

### How the hypervisor is told

When the annotation is `"false"`, VMI-to-domain conversion adds libvirt
`<nosharepages/>` under `<memoryBacking>`. Libvirt turns that into QEMU
`-machine mem-merge=off`, so QEMU does not `madvise(..., MADV_MERGEABLE)` on
guest RAM. ksmd then ignores those pages even if it is running.

```xml
<memoryBacking>
  <nosharepages/>
</memoryBacking>
```

`<nosharepages/>` must be merged into whatever `<memoryBacking>` the VM
already needs (hugepages, memfd, `<access mode="shared"/>` for virtiofs or
passt). Those settings are orthogonal: `MAP_SHARED` shares a mapping with a
device backend; KSM merges identical *content* across processes. Both can be
set at once.

Hugepage-backed RAM is generally not a KSM candidate. The annotation is
still accepted and still emits `<nosharepages/>`; it is a no-op for KSM on
those backends. Encrypted guest memory (SEV/TDX/Secure Execution) also cannot
be merged; the annotation remains allowed and does not replace
`launchSecurity`.

### Relation to node KSM

`ksmConfiguration` still decides whether ksmd runs. This annotation only
decides whether *this* VM's pages are candidates:

- `"false"` on a KSM-enabled node → pages are not merged.
- omitted on a KSM-disabled node → nothing is merged (same as today).

### No feature gate

There is no feature gate.

This is not a new capability: QEMU already marks guest RAM mergeable, and
this annotation is only a hint to opt out. The change is converter-only (one
libvirt element), with no CRD field and no new control-plane path.
Feature-lifecycle allows skipping a gate when a VEP reshapes existing behavior
in a simple way; gating an opt-out hint would add more code than the feature itself.

### Risks

| Risk | Mitigation |
| --- | --- |
| Opting out increases unique host memory and can raise memory pressure | Intended trade-off. Pod requests/limits are unchanged; the scheduler still packs on requested memory. |
| Confusion with virtiofs/passt `MAP_SHARED` | Different mechanism (mapping vs content merge). Docs contrast the two. |
| Annotation stripped or ignored by older virt-launcher | Running QEMU keeps `mem-merge=off` until restart; after that, pages become mergeable again. |


## Alternatives

### CRD field `spec.domain.memory.mergeable`

Would still meet the goals and is more discoverable via `kubectl explain`.

**Rejected.** Guest memory sizing and backing already live on `Memory`
(`guest`, `maxGuest`, `hugepages`). Mergeability is a host hint, like
`kubevirt.io/memfd`. A new Memory field would also add KSM-facing core API
while VEP #381 direction is moving KSM *out* of core. A preference can carry the
annotation; a Memory field is not required for that.

### Name the annotation `kubevirt.io/ksm` or `kubevirt.io/nosharepages`

Would still meet the goals.

**Rejected.** `nosharepages` is a negative boolean (awkward default). `ksm`
names today's kernel mechanism. `mergeable-memory` matches `MADV_MERGEABLE`
and QEMU `mem-merge`, stays a default-true opt-out, and does not bake in KSM.

### Cluster-wide "never merge guest RAM"

A KubeVirt CR flag that sets `mem-merge=off` for every VM.

**Rejected.** It cannot satisfy the primary goal (per-VM opt-out on a node
that still runs KSM for everyone else). Node-level KSM on/off already exists;
the gap is selective exclusion.

### CEL / structured plugin

Could inject `<nosharepages/>` without in-tree converter support.

**Rejected.** This is a valid in-tree hint (same class as `kubevirt.io/memfd`),
not an integrator-specific tweak. A plugin adds complexity for both the
integrator and the user.

## Scalability

No new resource types, no new list/watch, no extra apiserver calls.

| Interaction | Component | When | Cost |
| --- | --- | --- | --- |
| Convert spec → domain | virt-launcher | VM start and migrate | One extra `<nosharepages/>` element when `"false"` |

Host memory for opted-out VMs can grow because KSM no longer collapses their
pages. That is workload density, not control-plane scale. Pod requests are
unchanged.

## Update/Rollback Compatibility

KubeVirt upgrades by rolling components while existing VMs keep running.
New behavior is picked up on **start or live-migration**, not in place.

## Functional Testing Approach

Unit tests are required for new conversion code. Functional tests assert
VM- and host-visible behavior, not domain XML.

### Unit (required from Alpha)

- Omitted and `"true"` → domain does not disable merging.
- `"false"` → domain disables merging (`<nosharepages/>`).
- `"false"` combined with hugepages and/or virtiofs/passt keeps those
  memory-backing settings and still disables merging.

### Functional / e2e

- Happy path: a VMI with `kubevirt.io/mergeable-memory: "false"` reaches
  `Running`.
- Default path: implicitly, a VMI that omits the annotation still reaches `Running`.
- On the node, the QEMU guest-RAM mappings of an opted-out VM are not marked
  mergeable (for example `VmFlags` in smaps lacks mergeable). A default VM on
  the same node still is. This is the user-visible isolation property, not an
  XML snapshot.

### GA

The Beta functional tests stay green across at least one minor release with
no quarantine/flake attributed to this feature.

## Implementation History

N/A — no implementation has merged yet.

## Graduation Requirements

### Alpha

- [ ] Conversion disables guest-RAM merging when
      `kubevirt.io/mergeable-memory: "false"` and does not when the
      annotation is omitted
- [ ] Unit tests listed above pass

### Beta

- [ ] Functional tests listed above pass, including migration
- [ ] User guide documents the annotation, its default, and that it is not
      confidential computing related
- [ ] No open functional or security bugs against the annotation

### GA

- [ ] No outstanding functional, security, or test gaps
- [ ] Beta functional tests have been non-flaky for at least one minor
      release

## References

- [libvirt Memory Backing (`nosharepages`)](https://libvirt.org/formatdomain.html#memory-backing)
- [Kernel Samepage Merging](https://docs.kernel.org/admin-guide/mm/ksm.html)
- [KubeVirt KSM management](https://kubevirt.io/user-guide/cluster_admin/ksm/)
- [Suzaki et al., Memory Deduplication as a Threat to the Guest OS (USENIX Security 2011)](https://www.usenix.org/legacy/event/sec11/tech/full_papers/Suzaki.pdf)
- [VEP #381: KSM Zero-Pages-Only Mode](https://github.com/kubevirt/enhancements/issues/381)
- [VEP writing guide](https://github.com/kubevirt/enhancements/blob/main/docs/vep-writing-guide.md) (from [kubevirt/enhancements#431](https://github.com/kubevirt/enhancements/pull/431))
