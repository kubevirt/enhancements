---
title: Device Providers — A Unified Translation Layer for VM Device Provisioning
vep-number: NNNN
creation-date: "2026-09-16"
status: provisional

authors:
  - "@EdDev"

owning-sig: sig-compute
participating-sigs:
  - sig-network

reviewers:
  - TBD
approvers:
  - TBD

feature-gate: DeviceProviders
stage: alpha

replaces: ""
superseded-by: ""
---

# VEP #NNNN: Device Providers — A Unified Translation Layer for VM Device Provisioning

This proposal is independent: a provider implementation such as the one [VEP-300][vep-300]
describes may adopt this contract for DRA claim generation, but neither VEP blocks or depends on
the other's merge order.

Supporting material lives alongside this file rather than inline:
[design-discussion.md][design-discussion] records the review discussion that shaped this VEP's
CPU-related design decisions, and
[k8s-controller-webhook-architecture.md][webhook-architecture] covers implementation trade-offs
for a provider controller's process and cache architecture. Neither is required to understand
this proposal; both are there for a reader who wants to check the reasoning or plan an
implementation.

## Overview

Today, a VM's device declarations must encode backend-selection detail directly: which specific
resource already exists, whether one needs to be generated, and by which mechanism. This forces two
costs onto every device type independently: a user must understand backend concepts just to
declare a simple device need, and every device type re-solves the same split between admin policy
and user intent from scratch. This proposal removes that coupling: a VM's device declarations
express only guest-facing intent. Resolving that intent to a concrete backend resource — generated
or already existing, by whatever mechanism fits a given cluster and device type — happens entirely
outside the VM spec.

## Motivation

- **The provider pattern shouldn't be tied to one backend, or even to a fixed choice between
  backends.** A related proposal, [VEP-300][vep-300], builds an admin/user split along these lines, but scopes
  it to DRA claim generation only, leaving Multus-only networks uncovered. [VEP-427][vep-427] shows the problem
  is sharper than "pick one backend": it proposes a third option that combines both, letting a
  Multus-attached network device also source its NIC allocation from a DRA claim for topology
  alignment. Three related proposals independently reaching for their own backend-specific field on
  the same device type is direct evidence that the split itself is the reusable part, not any one
  backend or even a fixed set of backends.
- **"Generate a resource" and "reference an existing one" are the same problem from the VM
  author's point of view.** `VirtualMachineInstanceResourceClaim` already has two mutually-exclusive
  fields for this (`resourceClaimName`, `resourceClaimTemplateName`), and [VEP-300][vep-300] independently
  proposes a third (`managedClaimProvisionerName`) to add provider-generated claims. This is evidence
  that each backend mode keeps getting added to the VM spec as its own named field, one VEP at a
  time, instead of being resolved by a single, backend-agnostic reference.
- **Backend wiring detail keeps leaking back into the guest-facing spec as advanced needs arrive.**
  Every time a device type grows an advanced use case (topology alignment, a specific allocation
  policy), the natural next step is a new VM-spec field describing how the backend resource is
  wired, not just what the guest needs. Left unchecked, this erodes the separation KubeVirt's API is
  meant to provide between what a VM exposes to the guest and how that gets wired to the host.
  Keeping that separation explicit behind one reference lets each side evolve independently, and
  keeps VM specs portable across clusters with different backend implementations.

## Goals

- Give VM authors an option to declare device intent without naming backend details or which
  mechanism resolves it, for any device type.
- A VM spec looks identical whether the underlying backend resource is generated fresh or resolved
  from something that already exists.
- Let admins define reusable provider configuration once and have many VMs reference it, rather
  than requiring per-VM authored backend configuration.
- Apply the same intent/backend split to every KubeVirt device type (CPU, GPU, host devices, and
  networks), not just the ones that happen to have one today.
- Keep the VMI spec limited to guest-visible intent; keep backend/topology/allocation policy in
  admin-owned, independently-evolvable objects.

## Non Goals

- Defining the internal configuration schema a provider controller needs (DeviceClass mappings,
  driver parameters, policy shapes) to express advanced, provider-specific policy, and whether that
  schema attaches directly to `DeviceProviderSpec` or through a separate, controller-owned object.
  This VEP defines only the routing contract; both questions are deferred to whichever VEP defines
  the configuration (for example [VEP-300][vep-300], for a DRA-claim-generating controller). See
  [The `DeviceProvider` Object](#the-deviceprovider-object).
- Fully specifying non-DRA provider backends (for example, a provider that resolves a network
  device to a Multus `NetworkAttachmentDefinition`). This VEP's mechanism is designed not to
  preclude that, but does not commit to it. See [Future Extensions](#future-extensions).
- Shared or pooled host-resource allocation across multiple VMIs (one physical resource, such as a
  CPU set, consumed by several VMIs at once). This is a different problem from per-VM device
  provisioning intent and is not a goal of this VEP.
- Standardizing a typed "host-allocation-groups" VMI spec field for mixed exclusive/shared vCPUs.
  That's left to whichever VEP defines CPU's host-allocation API.
- Guaranteeing that a provider's resolution of a guest-consequential device (CPU exclusive/shared
  placement, most notably) stays stable across events the guest can't observe on its own. A
  routine controller upgrade or policy change could silently relabel which vCPU is exclusive, for
  example. This is a real problem (see
  [design-discussion.md](design-discussion.md#why-silent-vcpu-relabeling-matters)), but this VEP's
  admission-time resolution mechanism (see [Provisioning Flow](#provisioning-flow)) only ever sees a
  pod before it exists, and so has no way to detect or prevent drift after the guest is running.
  Solving it is left entirely to whichever VEP owns CPU's domain-level correctness contract.
- Whether a specific backend/device type's resolved resource can be live-migrated without losing
  guest-visible properties (for example, whether a DRA-allocated exclusive CPU set or a
  passed-through GPU survives a migration). That's backend/hardware-dependent and appropriately
  owned by whichever VEP defines migration support for a given device: [VEP-152][vep-152], [VEP-300][vep-300], and
  [VEP-183](../../sig-network/183-dra-network/vep.md) already exclude it for their respective devices.
- Replacing the `resourceClaimName` / `resourceClaimTemplateName` power-user escape hatches. They
  remain, unchanged, as the direct bring-your-own path.
- Defining how a VM's guest CPU topology translates into host CPU resource consumption: the
  accounting formula for how many host CPUs are needed, and how virt-launcher applies the resulting
  pinning. That remains wherever a CPU-specific VEP places it (today, [VEP-152][vep-152]).

## Definition of Users

- **VM owner:** manages a VM's spec and observes its status.
- **Cluster admin:** manages cluster-scoped resources, including shared, centralized configuration
  used across many VMs.
- **Provider author:** implements and maintains a provider controller.

## User Stories

Split below by whether this VEP delivers the story directly, or lays the foundation for it. This
confirms the mechanism is designed to support it, without claiming this VEP completes it alone.

**Directly enabled by this VEP:**

- As a cluster admin, I want to define one provider configuration and have many VMs reference it,
  rather than authoring per-VM backend configuration.
- As a cluster admin, I want to change the backend allocation method behind a device declaration
  (for example, from Multus to DRA) without needing to update existing VMs' specs or change what VM
  owners are told to write for new ones.
- As a provider author, I want to implement a provider controller using any resolution strategy I
  choose, against one stable, documented VMI-facing contract.

**Enabled as a foundation; full realization depends on a follow-up provider implementation:**

- As a VM owner, I want to declare a network device on my VM and have the cluster decide whether
  it's backed by a generated claim or a pre-existing resource, without my VMI spec needing to know
  which. (Resolve mode ships with this VEP; generate mode for network depends on [VEP-300][vep-300] or
  [VEP-427][vep-427].)
- As a VM owner, I want a GPU and NIC co-placed according to admin-defined policy without learning
  any backend-specific claim syntax or naming. (Depends on a co-placement-aware provider, such as
  [VEP-300][vep-300]'s aligner.)
- As a VM owner, I want exclusive CPUs the way I request them today, without my VM's guest
  configuration silently breaking if the cluster's CPU alignment policy changes underneath a
  routine reboot. (This VEP resolves which backend CPU-set resource satisfies the request; keeping
  that resolution stable across the guest's lifetime is a distinct, unsolved problem (see
  [Non Goals](#non-goals)), left to a CPU-owning VEP.)

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

Terminology used throughout this section: `DeviceProvider` is the Kubernetes object; the out-of-tree
implementation that resolves references to it is a *provider controller* (or just "controller," its
`spec.controller` value). "Provider" alone, in prose, refers to the pairing of the two: an admin's
configured `DeviceProvider` object together with whatever controller serves it.

### Feature Gate

The generic reference mechanism is gated behind `DeviceProviders` (alpha, off by default). This
gate covers routing and validation only; a specific provider implementation may define its own
additional gate for its backend-specific behavior.

### Responsibility Boundary

- **VM owner owns:** device declarations, each optionally carrying a `deviceProviderRef`.
- **Cluster admin owns:** `DeviceProvider` objects, specifying which controller resolves a given
  reference and whatever configuration that controller requires.
- **The VMI controller owns:** rendering the launcher pod for everything it directly controls,
  labeling it so it can be matched by the mutating admission webhook of every provider it
  references, and submitting it for creation, since it doesn't know, and doesn't need to know, what
  backend-specific pod fields a given provider will require. See
  [Provisioning Flow](#provisioning-flow).
- **Provider controller owns:** resolving every `deviceProviderRef` that names it into concrete
  backend resources (generated or pre-existing) via a mutating admission webhook invoked during pod
  creation, patching the pod with whatever backend-specific fields those resources need before it is
  persisted, reporting its own failures (see [Error Handling](#error-handling)), and reporting its own
  liveness on the `DeviceProvider` object's status (see [Provider Status](#provider-status)).
- **Backend scheduling and allocation are out of scope.** Whichever backend a provider relies on
  (the Kubernetes DRA scheduler plugin, device-plugin resource accounting, Multus/CNI, or something
  else) owns actually allocating the device once the pod is persisted. This proposal assumes that
  machinery already exists per backend and does not define or influence it.

### Provisioning Flow

1. The cluster admin creates a `DeviceProvider` object naming the controller that will serve it. The
   provider controller itself registers a Kubernetes [Mutating Admission Webhook][mutating-webhooks]
   against the API server, scoped to virt-launcher pod creation requests that reference that
   provider.
2. The VM owner declares a device with a `deviceProviderRef {name, id}` referencing that provider.
3. Admission validates the rules that are safe to check statically: mutual exclusion, feature gate,
   and immutability (see [Validation](#validation)).
4. The VMI controller renders the launcher pod for everything it directly controls, labels it once
   per distinct provider the VMI references (`deviceproviders.kubevirt.io/provider-<name>`) so each
   provider's webhook selector can match only pods that concern it, and submits a pod creation
   request. It doesn't know what backend-specific pod fields a given provider's resolution will
   need, so it leaves those fields unset rather than guessing.
5. Before the pod is persisted, the Kubernetes API server's mutating admission chain invokes each
   matched provider's webhook. Each webhook reads the owning VMI (to learn which of its devices,
   and which ids, reference it) alongside its own `DeviceProvider` object, resolves every
   `deviceProviderRef` that names it (generating a new backend resource, or resolving against one
   that already exists), and returns a patch adding whatever backend-specific fields those
   resources require (for example, a DRA `resourceClaims[]` entry and matching
   `containers[].resources.claims[]`), plus its own
   `deviceproviders.kubevirt.io/resolved-<name>` annotation confirming it has resolved everything it
   owns on this pod (see [Resolution Receipt](#resolution-receipt)).
6. A generic Validating Admission Policy, running last in the same admission chain, confirms every
   `provider-<name>` label has a matching `resolved-<name>` annotation (see
   [Resolution Receipt](#resolution-receipt)). Only then is the pod persisted, already complete, and
   eligible for normal scheduling immediately, with no further gating or follow-up patch needed.
   Actually allocating the device from there is backend-specific machinery (the Kubernetes DRA
   scheduler plugin, device-plugin resource accounting, Multus/CNI, or whatever a given backend
   uses) that this proposal assumes already exists and does not touch.

Resolving via admission, before the pod exists, is required by Kubernetes itself:
`resourceClaims[]`, `containers[].resources.claims[]`, and container resource requests/limits are
all immutable once a pod is created, so no controller can patch them in afterward. See
[Alternative 2](#alternative-2-scheduling-gate-and-post-creation-patch) for the post-creation
approach this replaced, and
[Alternative 3](#alternative-3-a-dedicated-virt-controller-level-hook) for a bespoke hook mechanism
considered instead of a native admission webhook.

```
        Cluster Admin                   VM Owner
              │                            │
              ▼                            ▼
        DeviceProvider                  VMI + deviceProviderRef
              └───────────────┬────────────┘
                              ▼
              VMI controller renders Pod, labels it
               per referenced provider, submits Create
                              │
                              ▼
           Provider Mutating Admission Webhook(s)
             (invoked before the Pod is persisted)
                     ┌────────┴────────┐
                     ▼                 ▼
                  generate          resolve
                     │                 │
                New Resource   Existing Resource
                     └────────┬────────┘
                              ▼
                Pod persisted, resource refs set
                              │
                              ▼
                     Backend Scheduler
```

### Resolution Receipt

A provider's webhook erroring or timing out is already handled by that webhook's own
`failurePolicy` (see [Error Handling](#error-handling)). But a provider whose webhook was never
registered, or whose selector never matches, produces no error at all: nothing intercepts the pod,
so it would otherwise be created silently incomplete. Detecting this generically, without KubeVirt
needing to know any backend-specific field a provider might have added, needs one small,
deliberately minimal piece of committed API: a resolution receipt.

- The VMI controller's per-provider label from [Provisioning Flow](#provisioning-flow)
  (`deviceproviders.kubevirt.io/provider-<name>`) doubles as the record of which providers this pod
  is expected to be resolved by.
- Each provider's mutating webhook, once it has resolved everything it owns on a given pod, adds its
  own `deviceproviders.kubevirt.io/resolved-<name>` annotation. Every provider only ever writes its
  own key, so independently-authored webhooks never need to coordinate or merge a shared value.
- A single, generic Validating Admission Policy (part of this VEP's own mechanism, not any specific
  provider's) runs last in the same admission chain (validating admission always follows mutating
  admission within a single request) and rejects the `Create` if any `provider-<name>` label lacks a
  matching `resolved-<name>` annotation.

Because this check runs within the same admission request, before the object is persisted, an
unresolved reference fails the pod creation outright: there is no window where an incomplete pod
exists and needs to be found and cleaned up.

### The `DeviceProvider` Object

```go
type DeviceProviderSpec struct {
	// Controller identifies the controller implementation that resolves
	// references to this provider.
	Controller string `json:"controller"`

	// Bindings is a list of rules matching a referencing device's id to a
	// concrete, pre-existing resource. This VEP's own reference
	// implementation for resolve mode (see the "Two Resolution Modes, One
	// Contract" section) uses this field; other controller implementations
	// may ignore it entirely, or a future VEP may add further fields to
	// this same struct for their own resolution strategy.
	//
	// How a controller treats bindings whose matchers could both apply to
	// the same id (first-match-wins, or accepting several ids mapped to
	// the same resource) is that controller's own decision; this VEP does
	// not prescribe one.
	// +optional
	Bindings []DeviceProviderBinding `json:"bindings,omitempty"`
}

type DeviceProviderBinding struct {
	// Matcher selects which referencing devices this binding applies to.
	Matcher DeviceProviderMatcher `json:"matcher"`

	// ResourceClaim identifies the pre-existing claim or template, and
	// which of its requests, this binding resolves to.
	ResourceClaim DeviceProviderResourceClaimRef `json:"resourceClaim"`
}

type DeviceProviderMatcher struct {
	// ID matches a device's deviceProviderRef.id exactly.
	ID string `json:"id"`
}

type DeviceProviderResourceClaimRef struct {
	// ResourceClaimName references a pre-existing ResourceClaim by name.
	// Mutually exclusive with ResourceClaimTemplateName. Named to match
	// the same field on a pod's/VMI's own resourceClaims[] entries, for
	// direct comparison.
	// +optional
	ResourceClaimName string `json:"resourceClaimName,omitempty"`

	// ResourceClaimTemplateName references a pre-existing
	// ResourceClaimTemplate by name, used to stamp a new, VMI-owned
	// ResourceClaim.
	// +optional
	ResourceClaimTemplateName string `json:"resourceClaimTemplateName,omitempty"`

	// RequestName identifies which entry in the referenced claim or
	// template's spec.devices.requests[] this binding answers, the same
	// (claim, request) pairing a pod's containers[].resources.claims[]
	// already uses to pick one request out of a claim that defines several.
	RequestName string `json:"requestName"`
}
```

For example, wiring up this VEP's own minimal reference implementation for resolve mode (see
[Two Resolution Modes, One Contract](#two-resolution-modes-one-contract)):

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: DeviceProvider
metadata:
  name: existing-nics
spec:
  # Identifies which controller resolves references to this provider.
  # Not used by KubeVirt itself for routing. The controller's own
  # registered mutating admission webhook (see Provisioning Flow) is what
  # actually intercepts and resolves references naming this provider.
  controller: kubevirt.io/resource-claim-template
  bindings:
  - matcher:
      id: rdma-0
    resourceClaim:
      resourceClaimTemplateName: rdma-0-template
      requestName: nic
```

`Bindings` is not the provider-specific policy schema [Non Goals](#non-goals) defers. It's a static
redirection to an already-existing resource (the shape this VEP's own resolve-mode reference
implementation needs; see [Two Resolution Modes, One Contract](#two-resolution-modes-one-contract)),
small and generic enough to commit directly in `DeviceProviderSpec`. A future VEP can still extend
`DeviceProviderBinding` with other resolution strategies beyond a static `ID` match.

A DRA-claim-generating controller needs richer configuration instead: DeviceClass mappings and
per-device-type driver parameters to compute claims dynamically. Whether that attaches directly to
`DeviceProviderSpec` (as `bindings` does here) or indirectly, through a separate, controller-owned
object (the pattern [Kubernetes `IngressClass`][ingressclass] uses via `spec.parameters`) is left to
whichever VEP defines that provider's configuration (for example [VEP-300][vep-300]).

### Provider Status

A `DeviceProvider`'s `status` reports whether the controller named in `spec.controller` is actually
present and serving it, closing the observability gap in [Error Handling](#error-handling): today, a
missing or unregistered controller only ever surfaces reactively, as a specific VMI's pod creation
failing, with no way for an admin to check readiness up front, before any VM references the provider.

```go
type DeviceProviderStatus struct {
	// ObservedGeneration is the generation of this DeviceProvider most
	// recently observed by its controller.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions report this DeviceProvider's observed state.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}
```

One condition type is defined: `Registered`. The provider controller named in `spec.controller` sets
it `True` once its mutating admission webhook is confirmed registered and reachable for this
`DeviceProvider`, and is responsible for keeping it current, just as any controller reports its
own health. `False` or absent means exactly what [Error Handling](#error-handling) already describes:
a pod referencing this provider will fail admission rather than persist unresolved.

This is additive to the routing contract itself: a provider controller that never sets `Registered`
behaves exactly as it does without this status, caught reactively at pod admission. It graduates
independently of the routing mechanism; see [Graduation Requirements](#graduation-requirements).

### The Generalized `deviceProviderRef`

Every device declaration gets one optional field:

```go
type DeviceProviderRef struct {
	// Name of the DeviceProvider object.
	Name string `json:"name"`

	// ID is an opaque identifier, scoped to this DeviceProvider, chosen by
	// the VM author. KubeVirt treats it as an opaque string; see the text
	// below for how a provider may interpret it.
	ID string `json:"id"`
}
```

Devices sharing the same `(Name, ID)` pair are grouped together for the provider to jointly
consider, for example for co-placement. That's a minimum, not a limit: a provider's webhook reads
the full VMI, not just one matched device, and may correlate across distinct IDs referencing it too
if its own logic calls for it (see the CPU+GPU+network example in API Examples). A provider may also
interpret `ID` as a policy-class selector, a lookup key against a pre-provisioned resource, or both.

Applied uniformly, this is the mechanism's target shape once every device type is wired. Alpha adds
only Network's `deviceProviderRef` to the API schema; GPU, HostDevice, and CPU's fields follow once
network's mechanism has been evaluated in practice (see
[Graduation Requirements](#graduation-requirements)):

```go
type GPU struct {
	Name string `json:"name"`
	// Mutually exclusive with ClaimRequest (the existing bring-your-own
	// escape hatch via spec.resourceClaims[]).
	// +optional
	DeviceProviderRef *DeviceProviderRef `json:"deviceProviderRef,omitempty"`
	*ClaimRequest      `json:",inline"`
}

// HostDevice gets the same treatment.

type Network struct {
	Name string `json:"name"`
	NetworkSource `json:",inline"` // existing multus/pod union, unchanged
	// +optional
	DeviceProviderRef *DeviceProviderRef `json:"deviceProviderRef,omitempty"`
}

type CPU struct {
	// ... existing fields (Cores, Sockets, Threads, DedicatedCPUPlacement,
	// IsolateEmulatorThread, IOThreads, etc.), all unchanged ...

	// Provider resolves how exclusive/aligned CPU allocation for this VMI
	// is satisfied. Deliberately backend-neutral: no ClaimRequest, no
	// mechanism-specific sub-struct. Whether the resolving controller
	// uses DRA, kubelet CPU Manager, or something else is invisible here.
	// +optional
	Provider *DeviceProviderRef `json:"provider,omitempty"`
}
```

This replaces the two-hop indirection some proposals use (a top-level `resourceClaims[]` entry
naming a provider, plus a device-level `claimName`/`requestName` pointing at that entry) with a
direct, single-hop reference. `spec.resourceClaims[]` remains exactly as it is today, but only for
the `resourceClaimName` / `resourceClaimTemplateName` escape hatches; the provider-driven path no
longer needs an intermediate list entry.

Naming CPU's field `provider` rather than embedding a backend-shaped sub-struct (for example, a
`dra` field) is a deliberate consequence of this design: the field says "something resolves this,"
without naming or implying any particular mechanism.

### Two Resolution Modes, One Contract

A `DeviceProvider`'s controller may:

1. **Generate** a new backend resource, owned by the VMI, named deterministically, torn down by
   owner-reference GC when the VMI is deleted.
2. **Resolve** the reference against something that already exists, for example, cloning
   configuration from a pre-existing template resource selected via `ID`, or (see
   [Future Extensions](#future-extensions)) attaching the pod to an entirely different kind of
   pre-existing resource.

KubeVirt's contract with the provider is identical either way: the VMI controller submits the pod
for creation, and the provider's mutating admission webhook resolves and patches it before it is
persisted (see [Provisioning Flow](#provisioning-flow)), regardless of which mode resolved it.
That's the same "identical VM spec" property [Goals](#goals) states, and it's what lets a
provider's resolution strategy evolve independently of core KubeVirt controllers. How a specific controller actually
assembles and reconciles its backend resource (for a DRA-claim-generating controller: request
collection, idempotent convergence, and so on) is that controller's own design; [VEP-300][vep-300]
works through one such design for DRA `ResourceClaim` generation, and this VEP does not restate it.

Generate mode's worked example is [VEP-300][vep-300]. Resolve mode's is this VEP's own minimal
reference implementation, simple enough to ship directly rather than defer. Given
`DeviceProviderRef{Name: "existing-nics", ID: "rdma-0"}`, its mutating admission webhook is invoked
while the referencing pod is still being admitted, not yet persisted. It looks up the
`existing-nics` `DeviceProvider`'s `bindings` for a matcher with `id: rdma-0` (see
[The `DeviceProvider` Object](#the-deviceprovider-object)), resolves the matched binding's
`resourceClaim.resourceClaimTemplateName`, and stamps a VMI-owned `ResourceClaim` from it, reusing
the same deterministic naming and owner-reference GC contract a generating provider uses. It then
returns a patch adding the pod's `resourceClaims[]`/`containers[].resources.claims[]` fields to
reference it, exactly as a generate-mode controller's webhook would. There's no request collection,
constraint policy, or convergence loop to design here, unlike a generate-mode controller. It's the
simplest provider that satisfies the contract: a concrete anchor for what "resolve" means, and a
starting template for more advanced implementations.

If a device names `existing-nics` with an id that has no matching `bindings` entry, the webhook
rejects the pod's admission outright rather than resolving only some of the devices it owns on the
pod, consistent with [Resolution Receipt](#resolution-receipt)'s per-provider, not per-id,
guarantee: a provider that stamps its `resolved-<name>` annotation is trusted to have fully
resolved everything it owns on that pod.

This split also drives a real implementation choice: whether a provider's webhook needs a
reconciler alongside it at all, and if so, whether the two share one process and cache. See
[k8s-controller-webhook-architecture.md][webhook-architecture] for that trade-off, worked through
for exactly this VEP's resolve-mode-versus-generate-mode split.

### Behavior During Migration

A VMI can have two live pods during migration: source and target. Each pod gets its own,
independent provider resolution at admission time (see [Provisioning Flow](#provisioning-flow)),
exactly as a freshly created VMI would, with no new API needed. For a generating provider, this
means a second concrete backend resource is created for the target pod alongside the source's,
consistent with most backend resources (for example, DRA `ResourceClaim` objects) being
single-consumer. A resolving provider that binds to a shared, pre-existing resource must decide for
itself whether that resource can back two pods at once or needs a second binding for the target;
this VEP doesn't require either answer, only that the provider have one.

This independence is also why a provider's resolved mapping isn't persisted to VMI status: a single
status field describing one desired mapping can't represent two independently-resolved pods'
answers at once. See
[design-discussion.md](design-discussion.md#5-where-does-the-resolved-per-instance-answer-live) for
the full reasoning.

### Validation

Rules generic to the reference mechanism itself, validated at VMI admission. These apply to every
`DeviceProviderRef`-typed field, including CPU's `provider` field, except where noted:

1. **Mutual exclusion:** at most one of `deviceProviderRef`, `resourceClaimName`,
   `resourceClaimTemplateName` may be set per device (GPU, HostDevice, Network; CPU's `provider`
   has no such sibling fields to be exclusive with).
2. **Feature gate:** `DeviceProviders` must be enabled when a `DeviceProviderRef`-typed field is used.
3. **Immutability:** a `DeviceProviderRef`-typed field cannot be changed after VMI creation.

Rules 1 and 3 are implemented as CEL `XValidation` rules on the CRD schema, per the
[API design guidelines][api-design-guidelines]' preference for CEL over imperative webhooks for
cross-field and immutability checks. Rule 2 (feature gate) stays in the admission webhook, since it
depends on cluster-level feature-gate state CEL cannot read.

A fourth rule is validated separately, at pod admission rather than VMI admission, by the Validating
Admission Policy described in [Resolution Receipt](#resolution-receipt): every provider a pod is
labeled for must have left its resolution annotation before the pod may be persisted.

Backend-specific validation (for example, DeviceClass resolution or duplicate request names within
a generated `ResourceClaim`) belongs to the specific provider implementation and its own admission
logic. See [VEP-300][vep-300] for the DRA-claim-generation case.

### Error Handling

Two distinct failure modes both end with the pod `Create` request failing outright, atomically, with
no incomplete pod ever persisted:

- A provider's webhook is registered and matches, but errors or times out while resolving. This is
  handled by that webhook's own configured `failurePolicy` (`Fail` is the expected setting here,
  since a device the VM owner asked for silently going unresolved is worse than a blocked creation).
- A missing `DeviceProvider`, or one with no controller currently serving its configured
  `controller` value, means no webhook ever intercepts the pod at all. This is caught instead by the
  Validating Admission Policy in [Resolution Receipt](#resolution-receipt): the expected
  `provider-<name>` label has no matching `resolved-<name>` annotation, so the policy rejects the
  `Create`.

Either way, the VMI controller's pod creation call fails with a clear admission error rather than
succeeding silently or leaving a pod stuck pending; it retries with its usual backoff, so a
temporarily unavailable provider delays VMI startup rather than losing the request. Operators are
responsible for giving distinct `controller` values to distinct controller deployments, since
KubeVirt does not enforce uniqueness.

Backend-specific error surfacing (events, conditions, retry semantics, finalizer handling) is the
concern of each provider implementation. See [VEP-300][vep-300] for a worked example.

## API Examples

The first example below uses this VEP's own resolve-mode reference implementation: its schema is
part of this VEP's committed contract, and it's what Alpha actually ships for the network device
type (see [Graduation Requirements](#graduation-requirements)). The remaining examples show
request/constraint shapes a DRA-claim-generating controller might produce, illustrative of one
possible provider implementation (the kind [VEP-300][vep-300] describes), not part of this VEP's
own committed schema.

### Network device through resolve mode (this VEP's reference implementation)

The extension this VEP adds relative to prior proposals: a network device resolved through the same
mechanism as GPUs and host devices.

Admin creates the provider, binding an id to a request within a pre-existing
`ResourceClaimTemplate`:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: DeviceProvider
metadata:
  name: existing-nics
spec:
  controller: kubevirt.io/resource-claim-template
  bindings:
  - matcher:
      id: rdma-0
    resourceClaim:
      resourceClaimTemplateName: rdma-0-template
      requestName: nic
```

User declares the network device, referencing the provider directly:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: rdma-vm
spec:
  domain:
    devices:
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 8Gi
  networks:
  - name: rdma-nic
    deviceProviderRef:
      name: existing-nics
      id: rdma-0
```

The reference controller's mutating admission webhook resolves the matched binding and stamps a
VMI-owned `ResourceClaim` by cloning `rdma-0-template`'s spec, then, before the pod is persisted,
returns a patch adding the pod's `resourceClaims[]` and `containers[].resources.claims[]` fields to
reference the `nic` request within it, plus its `resolved-existing-nics` annotation (see
[Two Resolution Modes, One Contract](#two-resolution-modes-one-contract) and
[Resolution Receipt](#resolution-receipt)).

### Network device through generate mode (illustrative)

The same VMI-facing declaration resolved instead by a DRA-claim-generating controller, showing that
a VM spec looks identical whichever mode resolves it (see [Goals](#goals)).

Admin creates the provider:

```yaml
apiVersion: kubevirt.io/v1alpha1
kind: DeviceProvider
metadata:
  name: sriov-rdma
spec:
  controller: policy.kubevirt.io/aligner
  # Additional configuration, if any, is defined by whatever controller
  # implements "policy.kubevirt.io/aligner"; see VEP-300 for an example.
```

User declares the network device, referencing the provider directly:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: rdma-vm
spec:
  domain:
    devices:
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 8Gi
  networks:
  - name: rdma-nic
    deviceProviderRef:
      name: sriov-rdma
      id: nic
```

Illustrative resource produced by a DRA-claim-generating controller:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaim
metadata:
  name: rdma-vm-nic
  labels:
    kubevirt.io/device-provider: sriov-rdma
  ownerReferences:
  - apiVersion: kubevirt.io/v1
    kind: VirtualMachineInstance
    name: rdma-vm
    controller: true
spec:
  devices:
    requests:
    - name: nic
      exactly:
        deviceClassName: sriov.example.com
        count: 1
```

### GPU + NIC co-placement (flattened)

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: gpu-nic-vm
spec:
  domain:
    devices:
      gpus:
      - name: gpu0
        deviceProviderRef:
          name: pcie-aligned
          id: aligned-devices
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 16Gi
  networks:
  - name: rdma-nic
    deviceProviderRef:
      name: pcie-aligned
      id: aligned-devices
```

Both devices share `(pcie-aligned, aligned-devices)`, so a co-placement-aware controller receives
both and can apply whatever joint policy it implements across them.

### CPU, GPU, and network together, without a CPU claim shape in the VMI

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: full-topology-vm
spec:
  domain:
    cpu:
      cores: 16
      dedicatedCpuPlacement: true
      # Named `provider`, not `deviceProviderRef`, but same type and contract; see
      # "The Generalized deviceProviderRef" in Design.
      provider:
        name: hgx-b200-quarter
        id: cpus
    devices:
      gpus:
      - name: gpu0
        deviceProviderRef: {name: hgx-b200-quarter, id: gpu0}
      - name: gpu1
        deviceProviderRef: {name: hgx-b200-quarter, id: gpu1}
      interfaces:
      - name: rdma-nic
        sriov: {}
    resources:
      requests:
        memory: 64Gi
  networks:
  - name: rdma-nic
    deviceProviderRef: {name: hgx-b200-quarter, id: nic}
```

The user asked for 16 cores and exclusive placement; `hgx-b200-quarter`'s controller decides, at
admission time, which backend resource satisfies each of the CPU, GPU, and network references. What
happens to that resolved CPU assignment once the guest is actually running, and whether it stays
stable across later events, is outside this VEP's mechanism (see [Non Goals](#non-goals)). Note
that this example spans device types this VEP's own Alpha scope does not require together; see
[Graduation Requirements](#graduation-requirements).

## Alternatives

### Alternative 1: Label-selector-based policy matching

An earlier proposal: an admin targets a group of VMs via label selector (similar to
`MigrationPolicy`), and the VM spec stays label-only, with no explicit reference at all.
Operationally attractive at scale, but implicit: a VM's provider isn't visible by reading its spec,
and it inherits `MigrationPolicy`'s own known discoverability problem (which policy actually won has
to be surfaced separately). This VEP instead uses an explicit, named `deviceProviderRef`, still
shared across arbitrarily many VMs but discoverable directly from the VMI.

### Alternative 2: Scheduling-gate and post-creation patch

An earlier version of this VEP had the VMI controller create the pod immediately, with one
Kubernetes [scheduling gate][scheduling-gates] per referenced provider, and had each provider
controller watch for gated pods, patch in whatever backend-specific fields it needed, and remove its
own gate once done. Rejected: `resourceClaims[]`, `containers[].resources.claims[]`, and container
resource requests/limits are all immutable once a pod is created, so a provider controller cannot
actually add them to an already-persisted pod: a scheduling gate only delays scheduling, it does
not reopen the pod spec for editing. Replaced by resolving via a mutating admission webhook before
the pod is persisted (see [Provisioning Flow](#provisioning-flow)), which needs no gate at all.

### Alternative 3: A dedicated virt-controller-level hook

Instead of a native Kubernetes mutating admission webhook, the VMI controller could call a
provider-supplied hook directly, analogous to
[VEP-190](../190-kubevirt-structured-plugins/vep.md)'s launcher hooks but running before pod
creation rather than inside `virt-launcher`, passing the VMI manifest and the not-yet-posted pod
and expecting a mutated pod back before performing the actual `Create` call itself. This would solve
the same immutability problem (mutation still happens before the pod is persisted), but was not
chosen: it requires inventing a new hook category and transport that doesn't exist today (VEP-190's
launcher and node hook points all run inside `virt-launcher` or `virt-handler`, after a pod already
exists), where a
native mutating admission webhook is a standard mechanism every provider author already knows, is
enforced uniformly by the API server for any pod-create path (not just the one virt-controller
happens to call), and needs no new prerequisite work in VEP-190 or elsewhere.

## Future Extensions

- **Non-DRA provider backends.** The contract here (see [Provisioning Flow](#provisioning-flow): a
  provider's webhook resolves and patches the pod before it is persisted) doesn't require the
  resolved resource to be a DRA `ResourceClaim`. A provider could resolve a network
  `deviceProviderRef` to a pre-existing `NetworkAttachmentDefinition` via Multus instead. Not
  specified in this VEP; flagged because it's the direct payoff of generalizing the reference away
  from claim-specific language.

## Scalability

The generic mechanism adds, per VMI with at least one `deviceProviderRef`: one mutating admission
webhook call per distinct referenced provider, invoked synchronously as part of the pod-create
request the VMI controller already makes, not a separate API interaction from KubeVirt's
perspective. This is O(1) per referenced provider, independent of cluster size or VM count, and adds
no new controller loop, poll, or watched type at the routing layer itself. The added latency and
availability dependency of each webhook call are inherent to the admission-webhook mechanism, not
specific to this VEP, and are bounded by each webhook's own configured timeout.

Beyond this, scalability depends entirely on the specific provider implementation's own
resource-creation pattern, which is outside this VEP's scope. For a DRA-claim-generating provider,
this follows the existing DRA scalability model. See
[VEP-10 Scalability](../10-dra-devices/vep.md#scalability).

## Update/Rollback Compatibility

- Additive, gated by `DeviceProviders`. With the gate disabled, existing behavior (direct
  `resourceClaimName`/`resourceClaimTemplateName`, Multus networking, `dedicatedCpuPlacement` via
  CPU Manager) is unchanged.
- No migration/deprecation path is defined between this VEP and any provider-implementation VEP
  (see intro).
- **Rollback is not supported once VMs are using `deviceProviderRef`.** Disabling the feature gate
  does not retroactively rewrite already-admitted VMI specs, and a downgraded virt-controller that
  predates this field cannot render those VMIs' pods correctly. The only safe path before
  downgrading is deleting VMIs that use `deviceProviderRef`; there is no in-place conversion back to
  explicit `resourceClaimName`/`resourceClaimTemplateName` references.
- **Disabling the feature gate without a version downgrade** is a distinct scenario from rollback:
  it blocks admission of new `deviceProviderRef` usage, but does not retroactively affect
  already-admitted VMIs: their resources continue to be reconciled by their provider controller as
  before, since the gate governs admission, not ongoing reconciliation of already-created objects.
- **Rolling upgrade / version skew:** the VMI controller's role (render and submit the pod for
  creation; see [Provisioning Flow](#provisioning-flow)) is independent of any provider's internal
  resolution logic, so virt-controller and provider controllers (and their registered webhooks) can
  be upgraded in either order without version-skew concerns at the generic-mechanism level. Skew
  within a specific provider implementation's own controller fleet is that implementation's concern.

## Functional Testing Approach

- Unit tests for the generic reference mechanism: validation rules, mutual exclusion, immutability,
  and the VMI controller's pod-rendering and labeling behavior (see
  [Provisioning Flow](#provisioning-flow)), independent of any specific provider implementation.
- Integration tests exercising both resolution modes: resolve mode via this VEP's own reference
  implementation, generate mode via a fake provider controller standing in for one like [VEP-300][vep-300]'s.
- Functional/e2e: a VMI using `deviceProviderRef` against a minimal reference provider (network,
  per this VEP's Alpha scope), exercising the full admission → webhook resolution → resolution
  receipt check → persisted pod path end to end, independent of any specific backend. Also cover the
  two rejection paths from [Error Handling](#error-handling): a webhook that errors/times out, and a
  provider with no webhook registered at all.
- Provider-implementation-specific testing (for example, DRA claim generation correctness) belongs
  to whichever VEP defines that provider.

## Implementation History

- This document: factored out of design discussion held during [VEP-300][vep-300]'s review
  ([kubevirt/enhancements#432][vep-300]) and a follow-up
  design session on CPU host-allocation correctness.

## Graduation Requirements

### Alpha

- `DeviceProvider` object (cluster-scoped), with the minimal `controller`/`bindings` shape defined
  in this VEP
- `deviceProviderRef` wired end-to-end for **one** device type: network, chosen because it validates
  the routing contract with the simplest device shape. GPU, HostDevice, and CPU's
  `deviceProviderRef`/`provider` fields are not part of Alpha's API schema at all. They're added only
  once network's mechanism has been evaluated in practice, whether in a later Alpha iteration or
  directly at Beta (see Beta below).
- A minimal, KubeVirt-provided reference provider implementing resolve mode (see
  [Two Resolution Modes, One Contract](#two-resolution-modes-one-contract)), used to validate the
  network device type end-to-end without depending on [VEP-300][vep-300]'s or [VEP-427][vep-427]'s generate-mode
  controllers existing yet, and offered as a foundation for future provider implementations to build
  on.
- Generic admission-time resolution behavior, including the resolution receipt (see
  [Provisioning Flow](#provisioning-flow) and [Resolution Receipt](#resolution-receipt)), independent
  of backend
- Validation (the four generic rules)
- API changes behind `DeviceProviders` feature gate (off by default)
- Unit and functional tests for the generic mechanism and the network device type

### Beta

- The generic reference mechanism extended to GPU, HostDevice, and CPU (their respective
  `deviceProviderRef`/`provider` fields), added to the API schema for the first time at this stage
  unless a later Alpha iteration introduces them sooner (see Alpha above)
- `DeviceProvider.status` (`observedGeneration` and the `Registered` condition; see
  [Provider Status](#provider-status)), giving admins a way to verify a provider controller is live
  before referencing it
- At least one provider implementation validated against this contract in practice
- User documentation

#### On-By-Default Readiness

Safe once: the generic routing/validation path has been exercised by early adopters during Alpha
and covered by Beta's functional tests, with no correctness issues attributable to the routing
layer itself, and the disable path described in Update/Rollback Compatibility is documented and
has been exercised.

### GA

- Feature gate removed; `deviceProviderRef` and `DeviceProvider` are unconditionally available
- Upgrade/downgrade testing
- Multiple independent provider implementations validating the contract

## References

- [Kubernetes `IngressClass`][ingressclass]:
  precedent for the `controller` field used by `DeviceProvider`
- [ResourceClaim DeviceConstraint](https://kubernetes.io/docs/reference/kubernetes-api/resource/resource-claim-v1/#DeviceConstraint)

[design-discussion]: design-discussion.md
[mutating-webhooks]: https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/#mutatingadmissionwebhook
[scheduling-gates]: https://kubernetes.io/docs/concepts/scheduling-eviction/pod-scheduling-readiness/
[webhook-architecture]: k8s-controller-webhook-architecture.md
[vep-152]: https://github.com/kubevirt/enhancements/pull/414
[vep-300]: https://github.com/kubevirt/enhancements/pull/432
[vep-427]: https://github.com/kubevirt/enhancements/pull/428
[ingressclass]: https://kubernetes.io/docs/concepts/services-networking/ingress/#ingress-class
[api-design-guidelines]: https://github.com/kubevirt/kubevirt/blob/main/docs/api/design_guidelines.md#42-cel-validation
