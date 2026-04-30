# VEP 224: Metadata Service (MDS) Enablement for KubeVirt VMIs

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

This proposal introduces an opt-in mechanism for exposing a link-local Metadata Service (MDS) endpoint to a KubeVirt guest.

The proposition suggests introducing a new [network binding plugin](https://kubevirt.io/user-guide/network/network_binding_plugins/) whose CNI component configures the virt-launcher pod network namespace during pod network setup (for example attaching the metadata address and installing redirection rules so traffic to the well-known link-local IP, default `169.254.169.254/32`, reaches a metadata listener in the pod). An accompanying sidecar image registered on the KubeVirt CR can run the metadata HTTP service.
This document has two focuses:

a. **Network plumbing** required in the virt-launcher pod namespace to make an MDS endpoint reachable from the guest in a consistent way, across different cluster overlay/network deployments and KubeVirt interface bindings.

b. **Metadata service** exposure in the virt-launcher pod (typically a sidecar) that responds to MDS requests.

## Motivation
Users running VM workloads on Kubernetes frequently want a cloud-like metadata endpoint for:

- Instance identity and lifecycle signals
- Bootstrap configuration that must be fetched at runtime (as opposed to static cloud-init)
- In-cluster services that expect the conventional metadata IP (`169.254.169.254`)

## Goals
- Provide a mechanism to expose a link-local MDS endpoint using a **network binding plugin** (NAD + CNI + optional sidecar), suitable for a [predefined data path](#supported-use-cases-initial).
- Provide a mechanism for developers to bring their own metadata service implementations (sidecar image on the binding and/or optional container spec on the VMI).

## Non Goals
- Define the metadata schema/content served by MDS.
- Standardize an authentication/authorization model for metadata content.
- Support arbitrary user-defined privileged containers in virt-launcher pods.
- Provide IPv6 MDS in the initial phase (future work).

## Definition of Users
- A user is a person that wants a guest to access a metadata endpoint over a conventional link-local IP.
- An admin is a person who manages the cluster, defines the platform-level resources and controls the security constraints for KubeVirt VMs.
- A developer is a person who wants to build a metadata provider/service to run alongside KubeVirt.

## User Stories
- As a user, I want my guest OS to retrieve its own workload identity metadata from a well-known link-local IP (e.g. `169.254.169.254`).
- As a developer, I want to plug in my own metadata service implementation that runs as a sidecar in the virt-launcher pod.
- As a developer, I want metadata service enablement to be explicit.
- As an admin, I want my VMs to remain in private subnet (no public internet, no cluster API access), while allowing the guest to reach the metadata service.
- As an admin, I want the MDS packet steering to be constrained so only guest-originated traffic can reach it.

## Use Cases

### Supported Use cases (initial)

1. VMI declares a dedicated network using a registered MDS network binding (Multus `networkName` pointing at an NAD whose plugin chain includes the MDS CNI), with guest networking configured so metadata traffic uses that interface.
2. Guest consuming metadata via link-local IP `169.254.169.254` on the path the MDS CNI configures (initial implementations may scope to **TCP** to selected ports).
3. Custom metadata service via sidecar image configured on the KubeVirt CR for that binding.

### Unsupported Use cases (initial)
1. Arbitrary user-injected privileged sidecars in virt-launcher pods.
2. Multi-tenant isolation guarantees beyond what the selected datapath can enforce (requires further design).

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| **Privilege surface** — CNI runs with elevated privileges on the node during `ADD`; scope and review the MDS CNI; document what it mutates in the pod netns. | Prefer binding-plugin CNI for plumbing; keep compute container free of `CAP_NET_ADMIN` for MDS-specific setup. |
| **Reply routing and `table local` changes (alternative path)** — Editing kernel’s local route for the VM IP and adding host routes/neighbor entries may cause unexpected kernel behavior. | Ensure the network plumbing is limited to the pod network namespace; prefer primary CNI design that avoids fragile local-table hacks where possible. |
| **Datapath mismatch** — Initial MDS CNI designs may only match specific topologies (e.g. secondary Multus leg, TCP-only DNAT). | Document guest routes and binding prerequisites. |
| **Custom MDS container: malicious or vulnerable images** — the image supplied may be malicious, poorly maintained, or contain vulnerabilities; may pose security threads when running in the same network namespace as other virt-launcher containers | Document that image choice and trust are the user’s responsibility. Optionally support cluster-level policy (e.g., image allowlist, signed images). Enforce non-root and minimal base images if needed. |
| **Custom MDS container: resource abuse** — User can set high CPU/memory requests or limits on the MDS sidecar, causing node pressure or noisy-neighbor effects. | Validate and optionally cap resource requests/limits for the injected MDS container. |

## Design

### High-level behavior

When a VMI uses the MDS-capable network binding:
1. During pod network setup, the MDS CNI (privileged, standard CNI phase) configures the pod netns so traffic to the metadata IP is steered to a local listener (exact mechanism is binding-defined; one example is attaching the address to `lo` and using DNAT for TCP to `localhost` where a sidecar listens).
2. The sidecar image declared on the KubeVirt CR for that binding runs in the virt-launcher pod and serves HTTP metadata on the expected port(s).

Cluster admins registers the binding on the `KubeVirt` CR (`spec.configuration.network.binding.<name>`) with at least `networkAttachmentDefinition` and, when needed, `sidecarImage` per [Network Binding Plugins](https://kubevirt.io/user-guide/network/network_binding_plugins/).

### Metadata server injection

To allow developers to bring their own metadata server implementation, this proposal suggest specifying the MDS image as `sidecarImage` on the KubeVirt CR under `spec.configuration.network.binding`.

### Network plumbing (primary): MDS CNI in the binding plugin

The recommended implementation performs pod netns setup in the MDS CNI invoked from the NAD referenced by the binding (often chained after other plugins when required).

Illustrative responsibilities (exact rules are implementation-defined):

1. Ensure the metadata IP (default `169.254.169.254/32`) is reachable in the pod netns in a way consistent with the chosen datapath (for example assigned to `lo` interface).
2. Install `nat` `PREROUTING` rules (nft or iptables) to DNAT TCP to `169.254.169.254 → 127.0.0.1:<metadata-port>`.
3. Optionally restrict matches by ingress interface (`iifname`) and tune sysctls (for example `rp_filter`) where needed.
4. On `DEL`, remove installed addresses and rules.

The CNI must coexist with other rules in the netns (for example **masquerade** on another interface); ordering and test matrix are implementation concerns.

**Deployment sketch**

- Nodes: install the MDS CNI binary under the cluster CNI path (e.g. `/opt/cni/bin`).
- **NAD**: CNI JSON in `spec.config`; `type` must match the installed binary name (see example below).
- **`KubeVirt` CR**: register a binding key under `spec.configuration.network.binding.<name>` with `networkAttachmentDefinition: <namespace>/<nad-metadata.name>` and `sidecarImage`.
- **VMI**: an `interfaces[]` entry with `binding.name` equal to **that same** `<name>`, and a `networks[].multus.networkName` equal to the **NAD’s `metadata.name`** in the VMI’s namespace; guest OS routes (or link-local scope) so `169.254.169.254` uses that interface where applicable.

The following examples use one consistent set of identifiers: binding **`metadata-service`**, NAD name **`kubevirt-metadata-service`** in namespace **`default`**, CNI binary **`kubevirt-metadata-binding`**. Replace namespaces, names, and image as needed for your cluster.

**`KubeVirt` CR (register binding)**

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    network:
      binding:
        metadata-service:
          networkAttachmentDefinition: default/kubevirt-metadata-service
          sidecarImage: registry.example/network-metadata-service:v1
          migration:
            method: link-refresh
```

**`NetworkAttachmentDefinition` (NAD referenced above)**

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: kubevirt-metadata-service
  namespace: default
spec:
  config: |
    {
      "cniVersion": "1.0.0",
      "name": "kubevirt-metadata-binding",
      "plugins": [
        { "type": "kubevirt-metadata-binding" }
      ]
    }
```

Install a CNI binary named **`kubevirt-metadata-binding`** on each node (e.g. under `/opt/cni/bin/`) so it matches `"type"` in the NAD. Add chained plugins inside `"plugins"` if your datapath requires them.

**VMI (request MDS on a dedicated Multus interface)**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: example-with-mds
  namespace: default
spec:
  domain:
    devices:
      interfaces:
        - name: default
          masquerade: {}
        - name: metadata
          binding:
            name: metadata-service
    resources:
      requests:
        memory: 512Mi
  networks:
    - name: default
      pod: {}
    - name: metadata
      multus:
        networkName: kubevirt-metadata-service
```

`interfaces[].binding.name` **`metadata-service`** must match the key under `spec.configuration.network.binding` on the **`KubeVirt` CR**. `networks[].multus.networkName` **`kubevirt-metadata-service`** must match the **NAD** `metadata.name` in the **same namespace as the VMI** (`default` here, aligned with `networkAttachmentDefinition: default/kubevirt-metadata-service`).

### Where the logic runs

- **MDS CNI** (binding plugin): configures the pod network namespace during CNI `ADD`/`DEL` on the node.
- **Sidecar** (binding `sidecarImage`): serves metadata; may participate in DomainXML mutation per binding design.
- **virt-controller**: merges launcher pod spec (Multus annotations, sidecar container from binding registration).


## Virt-controller changes
For VMIs using the MDS binding: ensure launcher pod annotations and sidecar containers match the **KubeVirt CR** binding registration (NAD, `sidecarImage`, optional 
overhead).

## Status and conditions

Add a VMI condition (name TBD) indicating MDS plumbing readiness, for example:

- `status.conditions[type=MetadataServiceReady]` with `True/False` and a reason/message when misconfigured.

Future work may add a small `status.mds` struct containing derived values (selected interface/bridge, metadata IP, etc.) for debuggability.

## Scalability

The expected overhead is per-VMI and bounded -- a small ruleset and address configuration in CNI; sidecar memory/CPU per binding overhead settings.

The design should not introduce additional watches beyond existing VMI/pod reconciliation loops.


## Alternatives

### Metadata Service Injection

#### Alternative 1: `vmi.spec.mds.mdsTemplate` reference to a namespaced `PodTemplate` resource
This approach allows a shard template object for metadata server to be used across many VMIs, but it introduces indirection and lifecycle coupling to an external object that's not managed to KubeVirt.

#### Alternative 2: Rely entirely on cluster-admin mutation (MutatingWebhook/Kyverno)
This approach does not require further addition to the new KubeVirt API, and is readily available. But it has the drawback of making the MDS injection behavior becomes cluster-specific and less portable. It also adds difficulty to provide consistent validation, and user-facing guarantees in KubeVirt.

#### Alternative 3: Metadata service injection via cloud-init or hook sidecars
Cloud-init provides static bootstrap data at boot. This proposal adds a runtime metadata endpoint for on-demand identity inquiry. They are complementary — cloud-init for one-time config and MDS for metadata service during the VM's lifetime. The existing `hooks.kubevirt.io/hookSidecars` flow is for short-lived hooks that read domain XML or cloud-init payload and write modified contents to stdout; whereas MDS is a long-lived server that listens for guest traffic, so that pattern does not fully fit. We are therefore proposing a dedicated API for declaring the MDS sidecar.

### Network Plumbing

#### Alternative 1: Allow user-injected privileged sidecars in virt-launcher pods

This is the most flexible approach for downstreams, but it significantly expands the attack surface and complicates KubeVirt’s
security posture. It also introduces compatibility concerns across clusters with different PodSecurity admission policies.
For these reasons, this VEP proposes an upstream-managed, opt-in feature rather than arbitrary privileged injection.

#### Alternative 2: MutatingAdmissionWebhook that injects a privileged initContainer

This can be implemented out-of-tree today, but it is not portable across security-restricted clusters and is hard to make
reliable across upgrades. It also does not provide a stable KubeVirt API surface for users.

#### Alternative 3: virt-handler-only netns plumbing

virt-handler is already a privileged node component and can `nsenter` the virt-launcher pod network namespace to apply the nft/sysctl/route changes.
This reduces the need for virt-launcher to carry elevated capabilities, but requires a handshake/readiness mechanism so the VM only starts after MDS plumbing is complete.

#### Alternative 4: VSOCK-based metadata service

A VSOCK-based MDS would simplify the implementation (no veth/nftables/FDB/routes, no extra capabilities, binding-agnostic). The main trade-off is that the guest would use the VSOCK API instead of the well-known metadata IP `169.254.169.254`, which is what most cloud providers and existing IMDS clients use today. Because the proposal expects developers to bring their own MDS, a VSOCK-based design would mean those custom implementations would need to listen on VSOCK rather than on the link-local IP. Validating that the injected MDS is actually listening on the expected VSOCK address/port would be non-trivial.

#### Alternative 5: Virt-launcher bridge L2 plumbing (summary)

In-tree **virt-launcher** execution of veth + bridge **nftables** + reply routing (full detail under [Alternative: virt-launcher bridge L2 plumbing](#alternative-virt-launcher-bridge-l2-plumbing)) remains a valid option when operators accept additional compute capabilities or need behavior tightly coupled to the core **bridge** binding without deploying a separate MDS CNI.

# References

- KubeVirt Network Binding Plugins: `https://kubevirt.io/user-guide/network/network_binding_plugins/`
- Network binding plugin design: `https://github.com/kubevirt/community/blob/main/design-proposals/network-binding-plugin/network-binding-plugin.md`
- Link-local metadata convention (common cloud pattern): `https://datatracker.ietf.org/doc/html/rfc3927`

