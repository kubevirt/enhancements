# VEP #427: Source a Multus network's device from a DRA ResourceClaim

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds one optional field, `resourceClaim`, to `MultusNetwork`:

```yaml
networks:
- name: sriov-net
  multus:
    networkName: vf-nad          # the existing NetworkAttachmentDefinition, unchanged
    resourceClaim:               # NEW
      claimName: gpu-vf
      requestName: vf
```

A network carrying it remains a plain Multus network at runtime — Multus still
invokes the CNI and applies the NAD configuration (VLAN, MAC, trust), the
network-status annotation is still produced, and virt-launcher builds the
`<hostdev>` from network-status exactly as today. The field only changes *how
the device behind the NAD is allocated*: from a DRA `ResourceClaim` request
named in `spec.resourceClaims[]`, instead of a device-plugin extended resource
derived from the NAD's `k8s.v1.cni.cncf.io/resourceName` annotation.

Because the device is now a request in a user-authored claim, it can share that
claim with other DRA devices — in particular a GPU — and a
[`matchAttribute` constraint](https://kubernetes.io/docs/concepts/scheduling-eviction/dynamic-resource-allocation/)
(e.g. `resource.kubernetes.io/pcieRoot`) between the two requests is enforced
by the Kubernetes scheduler. This is the primary use case: **scheduler-enforced
GPU/NIC topology alignment for VMs, without migrating an existing Multus
SR-IOV estate off its NADs.**

This VEP builds on the core DRA infrastructure of VEP #10
(`spec.resourceClaims[]`, the shared `ClaimRequest` type) and is complementary
to VEP #183; see [Relationship to VEP #183](#relationship-to-vep-183).

## Motivation

Today a Multus-backed SR-IOV network is allocated through a device plugin:
virt-controller reads the NAD's `k8s.v1.cni.cncf.io/resourceName` annotation
and injects an opaque extended-resource request into the virt-launcher pod.
The scheduler sees an integer count. It cannot express "this VF and this GPU
must sit under the same PCIe root complex" — a constraint that directly
determines P2P/GPUDirect-class throughput for VMs doing GPU networking.

DRA can express exactly that: two requests in one claim under a
`matchAttribute` constraint. But in current KubeVirt the two worlds cannot
meet:

- **DRA networks (VEP #183, `networks[].resourceClaim`)** replace the Multus
  flow. A NAD estate written for Multus — relying on the `CNIDeviceInfoFile`
  capability, Multus-applied VLAN/MAC/trust, the
  `k8s.v1.cni.cncf.io/network-status` annotation that tooling reads — cannot
  move to that model by configuration alone; it is a rewrite of the CNI
  contract. VEP #183 accordingly scoped SR-IOV out of DRA network devices, and
  currently requires a network binding plugin.
- **DRA extended resources (`DeviceClass.extendedResourceName`)** let the
  existing Multus flow allocate from a DRA pool without any KubeVirt change,
  but the scheduler then creates an implicit single-request claim per device.
  DRA has no cross-claim constraint, so the GPU (in the user's claim) and the
  VF (in the implicit claim) cannot be constrained against each other. The
  operator is left pinning PCIe root complexes by hand in DeviceClass CEL
  selectors — and if a VF pool spans two root complexes, the pair is silently
  split with no error, only degraded throughput.

Meanwhile the ecosystem plumbing for "DRA allocates, Multus configures" has
converged: multus-cni merged DRA `resource.k8s.io/v1` integration
([multus-cni PR #1492](https://github.com/k8snetworkplumbingwg/multus-cni/pull/1492),
2026-07-08), which maps a pod's allocated claim devices to NADs via the
`k8s.cni.cncf.io/resourceName` device attribute, and
[dra-driver-sriov](https://github.com/k8snetworkplumbingwg/dra-driver-sriov)
ships a MULTUS mode that prepares the VF (vfio binding, CDI injection,
device-info file) while leaving CNI invocation to Multus. A plain pod can use
this today. **Only KubeVirt cannot produce the equivalent pod shape** — this
VEP closes that gap with a control-plane-only change.

## Goals

- Let a Multus-backed VMI network source its device (SR-IOV VF) from a DRA
  ResourceClaim request, while Multus continues to run the CNI against the
  existing NAD.
- Thereby allow one claim to carry a GPU request and a VF request under a
  `matchAttribute` constraint, enforced by the scheduler.
- Keep the change confined to virt-api (admission) and virt-controller (pod
  rendering). virt-launcher and virt-handler are unchanged.
- Preserve full backward compatibility: without the new field, and with the
  feature gate off, nothing changes.

## Non Goals

- Changing the DRA network path of VEP #183 (`networks[].resourceClaim`), or
  taking any position on how *DRA networks* should model SR-IOV.
- Mixing pure DRA networks and Multus networks in one VMI (the existing
  rejection stays).
- Deploying or managing the external components (DRA drivers, multus-cni);
  they are cluster prerequisites.
- Hot-plugging interfaces whose device comes from a claim (a pod's
  `resourceClaims` are immutable).
- Standardizing MAC/interface-name configuration through DRA — not needed
  here, since the NAD/Multus path keeps doing both.

## Definition of Users

- **Admin**: manages the cluster, deploys the DRA drivers and multus-cni,
  owns DeviceClasses and the existing NAD estate.
- **VM owner**: authors ResourceClaims/Templates and VMs, and wants a VM's
  GPU and SR-IOV VF to be topology-aligned.

## User Stories

- As a VM owner running GPU workloads with SR-IOV networking, I want the
  scheduler to place my VM's GPU and VF under the same PCIe root complex, so
  that P2P DMA between them does not cross the inter-socket interconnect.
- As an admin with an existing Multus SR-IOV estate (NADs carrying VLAN/MAC
  configuration and the `CNIDeviceInfoFile` contract), I want to adopt DRA
  allocation for those VFs without rewriting the NADs or changing how the CNI
  is invoked.
- As an admin, I want VF allocation policy expressed in standard DRA objects
  (DeviceClass, ResourceClaimTemplate, CEL selectors) rather than in
  device-plugin configuration.
- As a VM owner, I want a VM whose GPU+VF pairing cannot be satisfied to fail
  loudly at scheduling time, rather than run silently mispaired.

## Repos

kubevirt/kubevirt

## Design

### Key decision

The new field lives **inside `MultusNetwork`**, not as a sibling in the
`NetworkSource` union. A claim-backed Multus network therefore stays a
non-DRA network for every `IsDRANetwork` consumer:

- The `NetworkSource` "only one member" contract is untouched, as are the
  existing admission rules (single source, no pure-DRA/Multus mixing).
- Multus annotation generation, pod interface naming, network-status →
  `network-info` conversion, VMI interface status (`multus-status`), and
  virt-launcher's Multus-path hostdev builder all work unchanged.
- The launcher's two hostdev builders (Multus-path via `InfoSourceMultusStatus`,
  DRA-path via `IsDRANetwork`) keep their mutual exclusivity; no duplicate
  hostdevs are possible by construction.

### What actually changes

All changes are gated by a new alpha feature gate **`MultusNetworksWithDRA`**.

**virt-controller (pod rendering):** for a Multus network carrying
`resourceClaim`:

1. Do **not** turn the NAD's `k8s.v1.cni.cncf.io/resourceName` annotation into
   an extended-resource request — the claim allocates the VF; requesting both
   would double-book the device (or make the pod unschedulable when no device
   plugin exists). The NAD is still fetched as an early existence check.
2. Add `{name: claimName, request: requestName}` to the compute container's
   `resources.claims[]`. This is what makes the kubelet apply the request's
   CDI devices (`/dev/vfio/*`) to the compute container.
   `pod.spec.resourceClaims` already carries every `vmi.spec.resourceClaims`
   entry (VEP #10 infrastructure); no change there.

**virt-api (admission):** a claim-backed Multus network must satisfy:

1. The `MultusNetworksWithDRA` feature gate is enabled.
2. `claimName` and `requestName` are both non-empty, and `claimName` resolves
   to a `spec.resourceClaims[]` entry.
3. The matching interface uses the `sriov` binding (initial scope; binding
   plugins are a possible follow-up), and the network is not the default
   network.
4. `claimName/requestName` tuples are unique across networks, GPUs and host
   devices — the shared-claim pattern (one claim, a `gpu` request and a `vf`
   request) is accepted, while double-booking a single request is rejected.
5. Coexistence with a pure DRA network in the same VMI remains rejected by
   the existing rule.

### End-to-end flow

1. The user creates a ResourceClaimTemplate with a `gpu` request, a `vf`
   request, and a `matchAttribute: resource.kubernetes.io/pcieRoot`
   constraint, and references it from `vmi.spec.resourceClaims[]`.
2. virt-controller renders the virt-launcher pod: Multus selection annotation
   as today, both claim requests on the compute container, no extended
   resource for that network.
3. The scheduler allocates the claim, forcing GPU and VF onto one PCIe root.
4. At `NodePrepareResources` the SR-IOV DRA driver (device-info mode) binds
   the VF to vfio-pci, injects `/dev/vfio/*` via CDI, and publishes the
   device-info; the GPU driver prepares the GPU.
5. Multus (with DRA integration) maps the claim's allocated device to the NAD
   via the `k8s.cni.cncf.io/resourceName` device attribute, runs the CNI
   (VLAN/MAC/trust applied), and records network-status.
6. virt-launcher builds the VF `<hostdev>` from network-status (existing
   Multus path) and the GPU `<hostdev>` from DRA device metadata (existing
   VEP #10 path). No launcher change.

### External dependencies

- Kubernetes ≥ 1.34 (DRA structured parameters GA, `resource.k8s.io/v1`).
  The `DRAExtendedResource` gate is *not* needed.
- multus-cni with DRA integration
  ([PR #1492](https://github.com/k8snetworkplumbingwg/multus-cni/pull/1492),
  merged 2026-07-08; first contained in the next multus release), thick
  plugin, with RBAC for reading `resourceclaims`/`resourceslices`.
- A network-device DRA driver that prepares the device but leaves CNI
  invocation to Multus, publishing the `k8s.cni.cncf.io/resourceName` and
  `k8s.cni.cncf.io/deviceID` device attributes —
  [dra-driver-sriov](https://github.com/k8snetworkplumbingwg/dra-driver-sriov)
  in MULTUS mode is the reference implementation.
- The NAD estate itself is unchanged, including its
  `k8s.v1.cni.cncf.io/resourceName` annotation, which Multus now uses as the
  claim→NAD mapping key.

### Relationship to VEP #183

VEP #183 defines *DRA networks*: a new network source where a DRA driver (or
binding plugin) delivers the fully configured device, replacing the Multus
flow for that interface. Its 2026-07 update deliberately scoped SR-IOV out,
"due to competing industry approaches for integrating SR-IOV with DRA".

This VEP does not reopen that question. It does not add an SR-IOV DRA network
type; the network here *is* a Multus network, and the "industry approach" it
composes with is the one the network plumbing WG has already merged
(multus-cni DRA integration + dra-driver-sriov MULTUS mode). DRA's role is
strictly allocation; configuration stays with Multus and the NAD. The two
VEPs share the `ClaimRequest` type and the claim/request-tuple uniqueness
validation, and remain mutually exclusive within one VMI. A separate feature
gate (`MultusNetworksWithDRA` vs `NetworkDevicesWithDRA`) keeps their
maturity and graduation independent; folding them together later is an open
question the SIG can decide at beta.

## API Examples

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: gpu-vf-same-root
spec:
  spec:
    devices:
      requests:
      - name: gpu
        exactly:
          deviceClassName: gpu.example.com
      - name: vf
        exactly:
          deviceClassName: sriovnetwork.k8snetworkplumbingwg.io
          selectors:
          - cel:
              expression: device.attributes["k8s.cni.cncf.io"].resourceName == "example.com/sriov_nic_1"
      constraints:
      - requests: ["gpu", "vf"]
        matchAttribute: resource.kubernetes.io/pcieRoot   # enforced by the scheduler
---
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: vmi-gpu-vf
spec:
  resourceClaims:
  - name: gpu-vf
    resourceClaimTemplateName: gpu-vf-same-root
  domain:
    devices:
      gpus:
      - name: gpu0
        claimName: gpu-vf          # VEP #10, unchanged
        requestName: gpu
      interfaces:
      - name: sriov-net
        sriov: {}
        macAddress: "02:00:00:00:00:01"   # optional; existing Multus MAC path
  networks:
  - name: sriov-net
    multus:
      networkName: vf-nad          # existing NAD, unchanged
      resourceClaim:               # NEW
        claimName: gpu-vf
        requestName: vf
```

Rendered virt-launcher pod (relevant parts):

```yaml
spec:
  resourceClaims:
  - name: gpu-vf
    resourceClaimTemplateName: gpu-vf-same-root
  containers:
  - name: compute
    resources:
      claims:
      - {name: gpu-vf, request: gpu}
      - {name: gpu-vf, request: vf}
  # k8s.v1.cni.cncf.io/networks annotation: unchanged Multus selection element
  # NOTE: no extended-resource request for sriov-net
```

## Alternatives

1. **DRA extended resources (no KubeVirt change).** The NAD's resourceName is
   backed by `DeviceClass.extendedResourceName`; the existing Multus flow
   works as-is. Rejected as insufficient: the VF lands in an implicit,
   single-request claim, DRA has no cross-claim constraint, so the GPU
   pairing must be pinned by hand in per-root DeviceClasses — and a pool
   spanning two root complexes mispairs silently. This remains a valid
   documented fallback for clusters that cannot enable the feature.
2. **Out-of-tree pod-mutating webhook (no KubeVirt change).** Replicate this
   VEP's two rendering changes in a cluster-side mutating admission webhook
   on virt-launcher pods, keyed by a free-form VMI annotation carrying the
   network → (claim, request) mapping (VMI annotations propagate to the
   launcher pod): the webhook (a) removes the extended resource injected from
   the NAD's `resourceName` annotation and (b) adds the claim/request entry
   to the compute container's `resources.claims[]`. This does work against
   stock KubeVirt (verified on v1.9.0), because of three current
   implementation behaviors:
   - `vmi.spec.resourceClaims` is copied wholesale to
     `pod.spec.resourceClaims` (`ToPodResourceClaims`), independent of which
     devices reference the claims — so the scheduler allocates the whole
     claim, `matchAttribute` included, even with only the GPU request
     referenced in the VMI spec;
   - the DRA admitter's cross-check is one-directional
     (`resourceClaims ⊇ {claims referenced by gpus/hostDevices}`): a claim
     entry — or a request inside it — that nothing references is accepted,
     and `requestName` strings are never resolved against the template's
     contents;
   - the network DRA admitter engages only for `networks[].resourceClaim`
     (pure DRA networks) and ignores plain Multus networks.
   The rendered pod is then byte-identical to this VEP's. Rejected as the
   recommended path: the mapping is an untyped annotation with no admission
   contract — double-booked requests, unresolved claims, or default-network
   misuse surface only late, as Pending pods or missing `/dev/vfio/*`; the
   approach depends on non-contractual internals (unreferenced-claim
   acceptance, wholesale claim copy, the `compute` container name) that may
   change in any release; and it adds webhook operational burden plus
   ordering/reinvocation interplay with other pod mutators (e.g. an external
   resource injector re-adding the extended resource). In effect this is an
   untyped out-of-tree fork of virt-controller's rendering logic; this VEP
   promotes the same two changes into KubeVirt proper, with a typed field
   and admission-time validation. The webhook remains a practical stopgap
   for clusters pinned to released KubeVirt versions, and doubles as
   prototype evidence for this design.
3. **Network binding plugins (no core change).** Raised in review as a
   possible vehicle. A binding-plugin registration (as of v1.9:
   `sidecarImage` for domain-XML mutation, `networkAttachmentDefinition`,
   `domainAttachmentType`, `migration`, compute memory overhead) has no
   access to the launcher pod's `resources.claims[]`, its resource
   requests/limits, or scheduling — the allocation-side gap this VEP
   addresses is out of a binding plugin's reach. On the domain side it could
   only substitute for the built-in `sriov` binding, which this VEP
   deliberately keeps (network-status → `<hostdev>` construction and SR-IOV
   migration handling come from the built-in path). Binding plugins remain
   relevant as the extension point for non-SR-IOV bindings later (Open
   Issue 3), aligning with VEP #183's model.
4. **Relax `NetworkSource` to allow `multus` + `resourceClaim` as siblings.**
   Rejected: `IsDRANetwork` would become true for these networks, so every
   consumer (launcher hostdev builders, interface-status computation, naming,
   hotplug filters) would need an exclusivity audit; it also breaks the
   union's "only one member" CRD contract. Far larger blast radius for the
   same result.
5. **Have the SR-IOV DRA driver run the CNI (its STANDALONE mode).** Rejected:
   for an existing NAD estate this is a rewrite of the CNI contract (no
   `CapabilityArgs`/`CNIDeviceInfoFile` support, no network-status
   annotation, different interface naming) plus an NRI requirement — exactly
   the migration this VEP exists to avoid. Clusters without a NAD estate can
   use VEP #183's DRA networks instead.
6. **Wait for SR-IOV support in VEP #183's DRA networks.** Orthogonal: even
   then, estates that must keep Multus-applied NAD configuration would still
   lack a scheduler-enforced pairing path.

## Scalability

No new watches or controllers. virt-controller reads only the VMI spec at
render time (the NAD fetch already exists today); it does not watch
`ResourceClaim` or `ResourceSlice` (same principle as VEP #10 alpha2).
Admission adds O(networks) checks. Per-VMI cost is one fewer extended
resource and one more `resources.claims` entry; scheduling cost is DRA's own,
identical to the same claim used by a container pod.

## Update/Rollback Compatibility

- The API change is a single optional field, gated by `MultusNetworksWithDRA`;
  with the gate disabled the field is rejected at admission and existing
  behavior is unchanged. Upgrades are compatible.
- Rollback: running VMIs that use the field must be deleted and the gate
  disabled before rolling back to a version without the field (same policy as
  other DRA alpha features).
- Interaction: with the `ExternalNetResourceInjection` gate enabled,
  virt-controller does not compute network resources at all and an external
  webhook (e.g. sriov-network-operator's) may re-inject the extended
  resource, reintroducing double allocation. Coexistence is out of scope for
  alpha and documented; an explicit admission rejection is an alpha→beta
  candidate.

## Functional Testing Approach

- **Unit tests** (alpha): admission rule matrix (gate off, empty/unresolved
  claim references, non-SR-IOV interface, default network, duplicate tuples
  across networks/GPUs/host devices, pure-DRA mixing regression); rendering
  (claim entries present, extended resource absent, plain Multus networks in
  the same VMI unaffected, gate-off no-op); vmispec helpers.
- **E2E** (beta): a VMI with a shared GPU+VF claim — VLAN/MAC applied in the
  guest, both devices under one PCIe root; unsatisfiable `matchAttribute`
  fails Pending with a clear event; live migration (template-backed claim
  re-allocated on the target, constraint re-enforced); regression of
  device-plugin Multus SR-IOV VMIs coexisting in the same cluster.
- E2E requires SR-IOV-capable lanes plus a multus release containing the DRA
  integration; alpha follows the established DRA model of no in-tree CI e2e
  gate.

## Implementation History

- 2026-07-08: multus-cni merges DRA integration (PR #1492), enabling the
  "DRA allocates, Multus configures" pod shape this VEP builds on.
- 2026-08-12: Design and proof-of-concept implementation (API + admission +
  rendering + tests, ~4 small PRs; virt-launcher/virt-handler untouched)
  completed against v1.9.0.
- 2026-08-18: End-to-end validation on a physical testbed: a VMI with a shared
  GPU+VF claim under a `matchAttribute: resource.kubernetes.io/pcieRoot`
  constraint, dra-driver-sriov in MULTUS mode and multus-cni with DRA
  integration — NAD configuration applied by Multus, both devices scheduled
  under one PCIe root.
- 2026-08: Initial VEP proposal.
- 2026-08-19: Presented at the KubeVirt DRA bi-weekly (slides + live demo on
  the physical testbed). Review feedback: investigate whether the goal is
  reachable without modifying KubeVirt core, including via network binding
  plugins. Analysis against v1.9.0 recorded in Alternatives 2 and 3: an
  out-of-tree pod-mutating webhook can reproduce the rendering today (relying
  on non-contractual internals), while a binding plugin cannot reach the
  allocation side; this VEP remains the recommended path as the typed,
  validated form of the same two changes.

## Graduation Requirements

### Alpha (v1.10)

- API field, admission, and rendering behind the `MultusNetworksWithDRA`
  feature gate; virt-launcher and virt-handler unchanged.
- Unit tests as described above.
- No in-tree CI e2e gate (consistent with the current DRA alpha model);
  manual validation with dra-driver-sriov (MULTUS mode) + a GPU DRA driver
  sharing one claim.

### Beta

- E2E coverage as described above, including live migration.
- A released multus-cni containing the DRA integration as a documented
  prerequisite.
- Resolution of the `ExternalNetResourceInjection` interplay and of
  interface hotplug handling (explicit rejection or support).
- Decision on gate consolidation with `NetworkDevicesWithDRA` (VEP #183).
- KubeVirt user-guide documentation.

### On-By-Default Readiness

- E2E lanes exercising the feature are voting and stable.
- The feature is inert without the new field, so on-by-default only exposes
  admission of the field; no behavior change for existing workloads.

### GA

- Backward-compatible upgrade path from beta; all beta open issues resolved
  or explicitly deferred by design.

## Open Issues

1. **Claim→NAD mapping is keyed by resourceName only** (multus DRA
   integration): two VFs of the same resourceName attached to two different
   NADs in one VM pair nondeterministically. The GPU–VF constraint itself is
   unaffected. Guidance: split resource pools (and NADs) per PCIe root; a
   richer mapping key is an upstream multus discussion.
2. **Directly referenced claims (`resourceClaimName`)**: the multus DRA
   integration walks `pod.status.resourceClaimStatuses`, which is reliably
   populated for template-generated claims; direct references also cannot be
   re-allocated for a migration target pod. Alpha recommends
   `resourceClaimTemplateName`; whether to reject direct references for
   networks is a beta decision.
3. **Non-SR-IOV bindings**: the alpha scope requires the `sriov` interface
   binding. Extending to binding plugins (aligning with VEP #183's model)
   may be considered later.
