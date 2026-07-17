# VEP #405: Resolve the virt-launcher pod IP in the `portforward` subresource

<!--
Tracking issue: https://github.com/kubevirt/enhancements/issues/405
Originating discussion: kubevirt/kubevirt#18399
-->

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

The `portforward` subresource of `virt-api` tunnels TCP traffic to a VM by
dialing the first IP reported in `vmi.status.interfaces[0].IP`. That address is
the guest's own interface IP. For multihomed virt-launcher pods — where the
guest's primary interface is attached to a network other than the cluster pod
network (a VPC, a primary user-defined network) — the guest IP is not routable
from `virt-api`, and the subresource fails even though the launcher pod itself
remains reachable on the cluster network.

This VEP fixes the subresource's target resolution: `virt-api` dials the
**current `virt-launcher` pod IP**, falling back to the interface IP when the
pod IP cannot be determined. Clients of the subresource — `virtctl
port-forward` and `virtctl ssh` — start working on such networks as a
consequence, but the primary target of this VEP is the subresource API itself.

Whether traffic that lands in the launcher pod then reaches the guest depends
on the network binding/provider forwarding pod-IP traffic to the guest — a
property that varies by binding and is the main scope boundary of this
proposal (see [Design](#design)).

## Motivation

The subresource's contract is "tunnel to a port on the VM". Which address to
dial is an implementation detail of `virt-api`, but the current implementation
(`getTargetInterfaceIP`, `pkg/virt-api/rest/dialers.go`) leaks an assumption
into that contract: that the guest's first interface IP is routable from the
`virt-api` pod. For multihomed launcher pods that assumption does not hold:

```
Internal error occurred: dialing VM: dial tcp 10.88.0.2:22: connect: connection timed out
```

This is not specific to any one provider. The same architecture — the guest
owning a non-pod-network address on its primary interface — underlies
OVN-Kubernetes primary user-defined networks (the `l2bridge` binding) and
Kube-OVN VPC subnets. OpenShift/OKD documents `virtctl ssh` and
port-forwarding as known limitations of primary UDNs for exactly this reason.
In kubevirt/kubevirt#18399, SIG-network confirmed there is no existing
mechanism to work around this and supported dialing the launcher pod IP,
noting that the cluster network is the only network `virt-api` and
`virt-launcher` are guaranteed to share.

## Goals

- The `portforward` subresource resolves a dial target that is routable from
  `virt-api` regardless of which network the guest's primary interface is
  attached to, in deployments where the binding/provider forwards pod-IP
  traffic to the guest. `virtctl ssh` and `virtctl port-forward`, as clients
  of the subresource, start working on such networks as a result.
- Do not regress bindings/configurations where the subresource works today
  (e.g. `masquerade`, pod-network `bridge`).

## Non Goals

- Changing `virtctl console` / `vnc`. Those tunnel through `virt-handler` on the
  node and are unaffected.
- Changing the VirtualMachineInstance API. An alternative that publishes
  `vmi.status.podIP` was prototyped and is described in
  [Alternatives](#alternatives); SIG-network preferred not to extend the VMI
  status for this.
- Adding forwarding of pod-IP traffic to the guest for bindings that do not
  already do so (notably stock `bridge`, per SIG-network in #18399). Whether the
  launcher pod IP reaches the guest is a property of the binding/provider that
  this design depends on; it does not implement it.

## Definition of Users

- **End users** running `virtctl ssh <vm>` / `virtctl port-forward` (or calling
  the `portforward` subresource directly) against VMs whose primary interface
  is on an isolated or non-pod-network.
- **Cluster administrators / network providers** implementing VPC-style or
  primary user-defined networks who need the subresource to keep working
  without exposing a routable guest IP or a second NIC.

## User Stories

- As a user whose VM's primary interface is attached to a VPC / primary UDN, I
  want `virtctl ssh` and `virtctl port-forward` to connect, without having to
  provision a separate `Service` or a second pod-network NIC.
- As a network provider, I want the guest to natively own its network's address
  on its primary interface (workloads oblivious to the infrastructure) while
  the `portforward` subresource keeps working through the launcher pod.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt/)

## Design

The `portforward` subresource builds a `netDial`
(`pkg/virt-api/rest/dialers.go`) and calls `DialUnderlying(vmi)`. Today that
resolves the target with `getTargetInterfaceIP(vmi)`. The proposed change:

1. Resolve the **current** `virt-launcher` pod at dial time via the API client
   already held by `SubresourceAPIApp` (`app.virtCli`) — the same client and
   `pods: list` RBAC the console/VNC path already use. The pod is selected with
   the same predicate as `controller.CurrentVMIPod`: owned by the VMI, on the
   VMI's `status.nodeName`, most recently created. The node filter matters for
   live migration: the target pod is only chosen once the VMI node has been
   handed over.
2. Prefer `pod.status.podIP`; fall back to `vmi.status.interfaces[0].IP` when
   the pod or its IP cannot be determined (empty result, not an error).
3. The client is injected into the `netDial` struct at its single construction
   site in `PortForwardRequestHandler`, mirroring `handlerDial`. The `dialer`
   interface signature is unchanged.

Because the pod is resolved at dial time, each new tunnel targets whatever pod
currently backs the VMI — relevant after live migration, when the launcher pod
and its IP change (see [Functional Testing](#functional-testing-approach)).

The new behavior would be guarded by a feature gate (proposed name
`TunnelToLauncherPod`; naming open to SIG preference) during alpha; when
disabled, `virt-api` dials the interface IP exactly as before.

**Reaching the guest from the launcher pod.** Dialing the pod IP lands the
connection in the launcher pod's network namespace; the binding/provider must
forward it to the guest. This is the substantive scope boundary of the VEP. The
following expectations are drawn from the SIG-network discussion in #18399 and
from architectural reasoning; **none were independently verified as part of
this VEP**:

| Binding / provider | Pod IP expected to reach guest? | Basis |
|---|---|---|
| `passt` | Yes | Reported working by SIG-network (@nirdothan, @0xFelix) in #18399, via a small `virt-api` change. Not verified here. |
| `masquerade` | Likely, with changes | SIG-network in #18399 expects tweaks (e.g. forwarding the tunnel port). Not verified here. |
| `bridge`, guest holds the pod IP | Yes | The pod IP is the guest's own address, so dialing it is equivalent to today's behavior. Reasoning, not verified here. |
| `bridge` on an isolated network, no provider forwarding | No | SIG-network in #18399: stock `bridge` won't forward the pod IP to the guest. |
| Provider/CNI that DNATs pod IP → guest | Yes, at the network level | The motivating deployment adds pod-IP → guest DNAT; reported reachable at the network level, end-to-end `virtctl` not independently confirmed here. |

Alpha would target the `virt-api` dial change plus the bindings/providers that
already forward (`passt`, `masquerade`, provider-DNAT CNIs). Defining or adding
forwarding for stock `bridge` is left to later discussion (beta, or explicitly
unsupported).

## API Examples

No Kubernetes API changes. The feature is enabled cluster-wide via the feature
gate:

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - TunnelToLauncherPod
```

Motivating VMI — a plain `bridge` interface on the pod network, where the guest
receives a non-pod-network (VPC) address from the provider's DHCP:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
spec:
  domain:
    devices:
      interfaces:
        - name: default
          bridge: {}
  networks:
    - name: default
      pod: {}
# status.interfaces[0].ip is the guest's VPC address (e.g. 10.88.0.2),
# not routable from virt-api; the backing pod's status carries the
# routable cluster IP (e.g. 10.244.1.35) that virt-api would dial.
```

## Alternatives

1. **Dial-time resolution in `virt-api`** (this VEP; preferred by SIG-network
   in #18399).
   - Pros: no VMI API change; always reads the live pod IP, so it is current
     immediately after live migration; reuses existing `virt-api` client and
     RBAC.
   - Cons: one `Pod` list per tunnel establishment; re-implements the
     `CurrentVMIPod` selection predicate in `virt-api` (which has a client but
     no pod informer); the routable IP is not visible to users on the VMI.

2. **Publish `vmi.status.podIP`**, populated by `virt-controller`, preferred by
   the dialer. Prototyped alongside the resolver.
   - Pros: `virt-api` reads an already-fetched field (no extra API call); the
     routable IP is visible on the VMI for users/tooling; selection logic stays
     in `virt-controller` where `CurrentVMIPod` already lives.
   - Cons: adds VMI API surface — SIG-network preferred to avoid growing the
     VMI status for this (#18399); the field lags `virt-controller`
     reconciliation, so there is a staleness window after live migration until
     it is updated; writing it interacts with the existing VMI status-update
     path (the mutating webhook that guards status writes, and the absence of a
     status subresource on the VMI CRD).

3. **Extend the network-binding-plugin API** so a plugin declares the tunnel
   endpoint for its interfaces.
   - Pros: lets a binding express a non-default endpoint explicitly.
   - Cons: SIG-network noted in #18399 that dialing the launcher pod IP is
     needed regardless (cluster network is the only guaranteed-shared network),
     so this adds API surface without removing that need.

4. **Expose SSH via a `Service`** (today's documented workaround).
   - Pros: works now, no code change.
   - Cons: does not fix the `portforward` subresource; extra per-VM object.

## Scalability

The resolver performs one namespace-scoped, label- and field-filtered `Pod`
list per tunnel **establishment** (not per byte, not per packet) — the same
cost class as the `virt-handler` pod lookup that console/VNC already perform
per connection. If profiling warrants it, a shared lister/informer could
replace the direct list; `virt-api` already runs informers. No impact on
data-plane throughput.

## Update/Rollback Compatibility

The behavior is gated. With the gate off, `virt-api` dials the interface IP
exactly as today. With it on, the pod IP is preferred; for bindings that work
today the pod IP resolves to the same reachable guest, so there is no
observable change. No persisted API changes are introduced, so rollback is
disabling the gate or downgrading `virt-api`; existing VMIs are unaffected.

## Functional Testing Approach

- **Unit**: the pod-selection predicate (owned-by-VMI, node filter, most-recent
  tiebreak) and the prefer-pod-IP / fall-back-to-interface-IP logic, including
  a target pod on another node being skipped mid-migration. (Present in the
  prototype.)
- **Live migration** (the pod IP changes): after a completed migration, a newly
  opened tunnel must reach the guest on the target launcher pod. Open question
  for the SIG: what is the desired behavior of an *in-flight* tunnel during
  migration — is dropping it (as with any other connection to the departing pod)
  acceptable, or is anything expected to preserve it?
- **Multiple interfaces**: a VM with a secondary network in addition to the
  primary — confirm resolution still targets the launcher pod and that
  interface status reporting is unaffected.
- **Regression**: `masquerade` and pod-network `bridge` tunnels still connect
  with the gate enabled.
- **e2e**: a VM whose primary-interface IP is not routable as the pod IP (via a
  binding/provider that forwards pod-IP → guest), asserting the `portforward`
  subresource (exercised through `virtctl ssh` / `virtctl port-forward`)
  connects.

## Implementation History

<!-- DD-MM-YYYY -->

- 08-07-2026: Support issue kubevirt/kubevirt#18399 opened describing the
  limitation and prototypes.
- 13-07-2026: SIG-network (owning SIG) supported dialing the launcher pod IP
  and requested an enhancement proposal.
- 17-07-2026: Prototype branches and draft VEP shared on the issue
  (author's fork, `lllamnyp/kubevirt`: `vmi-pod-ip-resolver` — this VEP's
  design, unit-tested; `vmi-pod-ip` — the `status.podIP` alternative, deployed
  and observed in a live cluster).
- 19-07-2026: SIG-network endorsed posting the VEP, targeting v1.10 (VEP freeze
  02-09-2026).
- 20-07-2026: Reframed per SIG-network review: the `portforward` subresource
  resolution is the primary target; `virtctl` is a client. `status.podIP`
  alternative set aside to avoid growing VMI status.

## Graduation Requirements

<!--
Initial proposal for SIG-network to refine. Per the template, not all stages
need to be fully specified in the first revision.
-->

### Alpha

- [ ] Feature gate (`TunnelToLauncherPod`) guards all behavior changes.
- [ ] Dial-target resolution implemented in the `portforward` subresource with
      the interface-IP fallback preserved; `dialer` interface unchanged.
- [ ] Unit tests for pod selection and dial-target preference.
- [ ] Initial e2e demonstrating a non-routable primary-interface VM reachable
      via the launcher pod IP.

### Beta

<!-- To be defined with SIG-network. Candidate items: -->

- [ ] Behavior for `masquerade` and for stock `bridge` on isolated networks
      settled (supported via forwarding, or explicitly documented as
      unsupported).
- [ ] Live-migration behavior (including in-flight tunnels) defined and tested.
- [ ] user-guide documentation updated, including which bindings/providers the
      tunnel reaches.

#### On-By-Default Readiness

<!-- To be defined with SIG-network. -->

- [ ] No open regressions for `masquerade` or pod-network `bridge` tunnels with
      the feature enabled.

### GA

<!-- To be defined with SIG-network. Candidate items: -->

- [ ] Enabled by default and the feature gate retired.
- [ ] Adoption/feedback from at least one primary-network provider.
