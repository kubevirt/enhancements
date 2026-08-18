# VEP #191: Decentralized Live Migration LM network proxy support 

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: 1.10
- This VEP targets beta for version: 1.11
- This VEP targets GA for version: 1.12

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Add an optional proxy datapath to synchronization controllers that forwards decentralized live migration traffic through a multiplexed gRPC tunnel. When enabled, migration data flows through sync controllers instead of directly between virt-handlers. This supports two cross-cluster connectivity models: a dedicated Multus cross-cluster network (reducing IP address requirements on a shared L2/L3 network), or peer proxy traffic over the pod network exposed through Service and Ingress/Route endpoints (no shared cross-cluster network required; an in-cluster migration network may still be used for virt-handler ↔ proxy traffic).

## Motivation

During testing of Decentralized Live Migration we encountered environments we had not considered before, which require additional functionality. In particular:

* **Multi-cluster, single LM network:** More than two clusters are connected through the same live migration (LM) network.
* **Constrained addressing:** The number of available IP addresses on this network is limited; there is no DHCP, and obtaining address blocks for virt-handlers is difficult.
* **Full mesh:** Each cluster must be able to migrate VMs to and from any other cluster, so every cluster must be reachable over the LM network.

Using the LM network directly for Decentralized Live Migration is problematic in this setting because it requires a large number of IP addresses. For each cluster we need *(virt-handlers + synchronization controllers)* addresses—i.e., one per virt-handler and one per synchronization controller—and that cost is incurred in every cluster. For example, with 10 clusters of 10 worker nodes (10 virt-handlers each) and 2 synchronization controllers per cluster, we need 10 × (10 + 2) = **120 IP addresses** on the cross-cluster live migration network. Without DHCP, assigning and maintaining these addresses is unwieldy.

A further issue is operational: when nodes are added or removed, any mechanism that assigns IP addresses to virt-handlers must also update addressing on the cross-cluster live migration network, increasing configuration churn and failure modes.

## Goals

* **Reduced addressing:** With the proxy in place, the cross-cluster live migration network needs only one IP per synchronization controller. Total addresses = (synchronization controllers per cluster) × (number of clusters). Example: 2 controllers per cluster × 10 clusters = 20 addresses.
* **Datapath selection:** Allow admins to choose between direct virt-handler connectivity (`Direct`) and sync-controller proxying (`Proxy`) via `decentralizedLiveMigrationDatapath`.
* **Dedicated cross-cluster network:** Support configuration of a cross-cluster live migration network that is separate from the in-cluster live migration network.
* **Pod-network proxy with external routing:** When `Proxy` is selected without `crossClusterNetwork`, peer sync-controller traffic uses the pod network and is exposed through a Service and Ingress/Route. An optional in-cluster `network` still routes virt-handler ↔ proxy traffic over `migration0`.
* **Synchronization address advertisement:** `status.synchronizationAddresses` advertises the address peer clusters use to reach the sync controller— the Ingress/Route URL when `crossClusterNetwork` is unset, or the `crosscluster0` address when it is set.
* **Placement control:** Allow admins to choose whether synchronization controller pods run on control-plane or worker nodes. Control-plane nodes often lack access to the cross-cluster live migration network; worker nodes typically have it. Support an optional node selector so admins can target the (possibly small) set of nodes that have cross-cluster live migration network access.

## Non Goals

* Allow proxying of any traffic besides decentralized live migrations.
* Requiring the proxy datapath for cross-namespace migrations; those can continue to use the direct datapath over the internal migration network.

## Definition of Users

* Cluster Admins
* Admins managing several clusters

## User Stories

* As a KubeVirt admin managing multiple clusters on a network with limited IP addresses, I want to perform cross-cluster live migration without building and maintaining a complex network that requires many addresses.
* As a KubeVirt admin, I want to choose whether decentralized migrations use direct virt-handler connectivity or a sync-controller proxy, so I can balance IP address constraints against network overhead.
* As a KubeVirt admin managing clusters that cannot share a dedicated live migration L2/L3 network, I want to expose the sync-controller proxy through a Service and Ingress/Route on the pod network so clusters can migrate VMs as long as they can reach each other's ingress endpoints.
* As a migration orchestrator, I want to read the target cluster's external sync endpoint from `status.synchronizationAddresses` so I can set `connectURL` on the source migration without hard-coding ingress hostnames.
* As a KubeVirt admin, I want to configure a dedicated cross-cluster live migration network that is separate from the in-cluster live migration network.
* As a KubeVirt admin, I want to choose whether synchronization controller pods run on control-plane nodes or worker nodes.
* As a KubeVirt admin, I want to use a node selector to place synchronization controllers on specific nodes—for example, the subset of nodes that have access to the cross-cluster live migration network.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Migration datapath selection

Decentralized live migration traffic can follow one of two datapaths, selected by `decentralizedLiveMigrationDatapath` under `spec.configuration.migrations`:

**Direct (default when unset)**

* Existing decentralized live migration behavior.
* Migration data flows directly between virt-handlers (via the migration network or pod network).
* Synchronization controllers handle coordination only (gRPC status sync); they do not proxy migration data.

**Proxy**

* Synchronization controllers terminate virt-handler TLS and proxy migration traffic over a multiplexed gRPC tunnel.
* Reduces IP requirements on the cross-cluster network (see motivation above).
* Applies to decentralized live migrations generally (cross-namespace and cross-cluster).
* Requires the `CrossClusterMigrationProxy` feature gate while Alpha.

With the proxy datapath, migration traffic follows this path:

```
source virt-handler → source sync controller → target sync controller → target virt-handler
```

### Current state migration network

The current live migration network design poses a problem when there are many clusters connected through a single live migration network. Especially if there are constraints on that network in how it is configured. In particular if we need to manually assign IP addresses to virt-handlers and synchronization controllers, and there is no DHCP available on the network. It is technically possible to design a `NetworkAttachmentDefinition` for each cluster that gives a range of IP addresses to a certain cluster, but it becomes a lot harder if the pool of available IP addresses is not a range. Also the number of IP addresses can become problematic if the number of virt-handlers per cluster is large enough. The current state is illustrated in figure 1:

![figure 1](l2_4cluster.png)

### Proposed state migration network

The proposed proxy will allow the same connectivity on an L2 network, but instead of every virt-handler being able to communicate with every other virt-handler directly, it will go through a proxy. The proxy will communicate with a proxy on the other cluster. This reduces the number of IP addresses needed from the number of virt-handlers + the number of synchronization controllers times the number of clusters to just the number of synchronization controllers times the number clusters. The proposed state is illustrated in figure 2:

![figure 2](proxy_cluster.png)

As you can see from the figure, the synchronization controller has two network interfaces. One connected to the 'in cluster' network (10.244.0.0/16), and one connected to the shared L2 network (172.16.0.0/16). The shared network is marked L2 in the figure, but it can also be an L3 network, as long as synchronization controllers can connect to each other it will work.

When `decentralizedLiveMigrationDatapath` is set to `Proxy`, synchronization controllers attach up to two Multus interfaces:

| Interface name | CR field | Purpose |
|----------------|----------|---------|
| `migration0` | `migrations.network` | In-cluster virt-handler ↔ sync-controller migration listeners |
| `crosscluster0` | `migrations.crossClusterNetwork` | Peer sync-controller gRPC traffic between clusters |

If `migrations.network` is set with the proxy datapath, synchronization controllers require the `migration0` interface at startup and will fail to start if it is missing. If `migrations.crossClusterNetwork` is set, synchronization controllers require the `crosscluster0` interface at startup and the sync gRPC port binds only on that interface; when omitted, peer traffic uses the pod network.

The proxy datapath supports four network configurations:

| `migrations.network` | `migrations.crossClusterNetwork` | virt-handler ↔ proxy | proxy ↔ proxy (peer gRPC) |
|----------------------|----------------------------------|----------------------|---------------------------|
| unset | unset | pod network | pod network (Service/Ingress/Route) |
| set | unset | `migration0` (dedicated migration network) | pod network (Service/Ingress/Route) |
| unset | set | pod network | `crosscluster0` (dedicated cross-cluster network) |
| set | set | `migration0` (dedicated migration network) | `crosscluster0` (dedicated cross-cluster network) |

Configurations with `crossClusterNetwork` set use the shared L2/L3 cross-cluster network for peer proxy traffic. `status.synchronizationAddresses` is populated from the `crosscluster0` interface address.

Configurations without `crossClusterNetwork` route peer proxy traffic over the pod network, exposed through Service/Ingress/Route. `status.synchronizationAddresses` is populated with the externally reachable Ingress or Route URL (hostname and port). Orchestrators use this value as the target `connectURL` on the source `VirtualMachineInstanceMigration` (see [VEP #24](../24-decentralized-migration/vep.md)).

### Proxy peer traffic over pod network (Service / Ingress / Route)

When `decentralizedLiveMigrationDatapath` is set to `Proxy` and `migrations.crossClusterNetwork` is **not** set, peer sync-controller gRPC traffic uses the pod network regardless of whether `migrations.network` is set:

* The sync gRPC server binds to the sync-controller pod IP (default port `9185`, configurable via `spec.synchronizationPort`).
* If `migrations.network` **is** set, virt-handler ↔ sync-controller migration listeners use the dedicated migration network (`migration0`) instead of the pod network. Only the proxy ↔ proxy leg uses the pod network.

This mode does not require a shared cross-cluster L2/L3 network or Multus attachment for peer traffic. Instead, each cluster exposes its synchronization controller through standard Kubernetes networking primitives that the admin creates:

1. A **Service** selecting the `virt-synchronization-controller` pods and forwarding to the synchronization port.
2. An **Ingress** or **Route** in front of that Service, giving the cluster a stable hostname (and optionally TLS termination or passthrough).

Cross-cluster live migration then works as long as every cluster can reach every other cluster's ingress/route endpoint.

Traffic path with ingress routing and a dedicated in-cluster migration network (`network` set, `crossClusterNetwork` unset):

```
source virt-handler ──(migration0)──► source sync controller
  ──(pod network / ingress/route)──► target sync controller
  ──(migration0)──► target virt-handler
```

Traffic path with ingress routing and no dedicated networks (both unset):

```
source virt-handler → source sync controller
  → target ingress/route hostname:port
  → target sync controller → target virt-handler
```

The ingress/route must support gRPC over the synchronization port (for example, an NGINX Ingress with `backend-protocol: GRPC`, or an OpenShift Route with passthrough or re-encrypt TLS). The cluster admin creates the Service, Ingress, or Route; KubeVirt discovers the external endpoint and publishes it in `status.synchronizationAddresses`.

### Current state synchronization controller placement

Currently the synchronization controllers are marked as control plane pods, and are thus scheduled on the control plane nodes. This works if the control plane nodes have access to the live migration network. This is not guaranteed to be the case.

### Proposed state synchronization controller placement

By default, synchronization controllers continue to use control-plane placement. Admins can override this with `spec.synchronizationPlacement`, a `ComponentConfig` with the same shape as other KubeVirt component placement fields (node selector, affinity, tolerations). This is independent of the migration datapath, but is commonly needed when only a subset of worker nodes has access to the cross-cluster live migration network.

## API Examples

All migration-related fields live under `spec.configuration.migrations` in the `KubeVirt` CR.

### Field reference

| Field | Values | Default | Feature gate | Notes |
|-------|--------|---------|--------------|-------|
| `decentralizedLiveMigrationDatapath` | `Direct`, `Proxy` | `Direct` | `Proxy` requires `CrossClusterMigrationProxy` | Selects how migration data is transferred |
| `crossClusterNetwork` | NAD name | unset | `CrossClusterMigrationProxy` | Only valid with `Proxy`; attaches as `crosscluster0`. When omitted, peer proxy traffic uses the pod network (exposable via Service/Ingress/Route) |
| `network` | NAD name | pod network | none | With `Proxy`, used for virt-handler ↔ sync-controller listeners on `migration0`. Independent of how peer proxy traffic is routed |

Webhook validation rules:

* `decentralizedLiveMigrationDatapath=Proxy` requires the `CrossClusterMigrationProxy` feature gate.
* `crossClusterNetwork` requires both the feature gate and `decentralizedLiveMigrationDatapath=Proxy`.
* `crossClusterNetwork` is rejected when the datapath is `Direct` or unset.

### Enable proxy datapath on pod network (minimal)

With `crossClusterNetwork` unset, peer proxy traffic uses the pod network. Expose the sync controller with a Service and Ingress/Route so peer clusters can reach it. This applies whether or not `migrations.network` is set (see the network configuration table above).

**KubeVirt configuration — pod network only (each cluster):**

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - DecentralizedLiveMigration
        - CrossClusterMigrationProxy
    migrations:
      decentralizedLiveMigrationDatapath: Proxy
```

**Service exposing the sync controller (each cluster):**

```yaml
apiVersion: v1
kind: Service
metadata:
  name: virt-synchronization-controller
  namespace: kubevirt
spec:
  selector:
    kubevirt.io: virt-synchronization-controller
  ports:
    - name: sync
      port: 9185
      targetPort: 9185
      protocol: TCP
```

**Ingress example (each cluster; gRPC-aware ingress controller required):**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: virt-synchronization-controller
  namespace: kubevirt
  annotations:
    nginx.ingress.kubernetes.io/backend-protocol: "GRPC"
spec:
  rules:
    - host: cluster-b.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: virt-synchronization-controller
                port:
                  number: 9185
```

**OpenShift Route example (each cluster):**

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: virt-synchronization-controller
  namespace: kubevirt
spec:
  host: cluster-b.example.com
  to:
    kind: Service
    name: virt-synchronization-controller
  port:
    targetPort: sync
  tls:
    termination: passthrough
```

**Source migration using the target cluster synchronization address (on the source cluster):**

The orchestrator reads the target cluster's `KubeVirt` `status.synchronizationAddresses` and uses it as `connectURL`:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstanceMigration
metadata:
  name: vmim-source
  namespace: source-ns
spec:
  sendTo:
    key: <unique-migration-id>
    connectURL: "cluster-b.example.com:443"
  vmName: my-vmi
```

The `connectURL` is the Ingress/Route hostname and port from the target cluster's `status.synchronizationAddresses`. It must be reachable from the source cluster and route to the target sync-controller gRPC port.

### Proxy with in-cluster migration network and ingress routing (no cross-cluster network)

Use a dedicated in-cluster migration network for virt-handler ↔ sync-controller traffic while routing peer proxy traffic through Service/Ingress/Route on the pod network. This is a common configuration when clusters have an internal migration network but no shared cross-cluster L2/L3 network.

**KubeVirt configuration (each cluster):**

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - DecentralizedLiveMigration
        - CrossClusterMigrationProxy
    migrations:
      decentralizedLiveMigrationDatapath: Proxy
      network: migration-network
```

The Service and Ingress/Route examples from the previous section apply unchanged. Virt-handler ↔ sync-controller traffic stays on `migration0`; peer proxy traffic is exposed externally and advertised via `status.synchronizationAddresses`.

### Proxy with dedicated cross-cluster network (no in-cluster migration network)

When only `crossClusterNetwork` is set, virt-handler ↔ sync-controller traffic uses the pod network while peer proxy traffic uses the dedicated cross-cluster network (`crosscluster0`). This is the minimal Multus configuration for the addressing savings described in the motivation section: no in-cluster migration network is required, but sync controllers must have `crosscluster0` and peer clusters connect directly over the shared cross-cluster network (not via Ingress/Route).

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - DecentralizedLiveMigration
        - CrossClusterMigrationProxy
    migrations:
      decentralizedLiveMigrationDatapath: Proxy
      crossClusterNetwork: crosscluster-network
```

Traffic path (`network` unset, `crossClusterNetwork` set):

```
source virt-handler ──(pod network)──► source sync controller
  ──(crosscluster0)──► target sync controller
  ──(pod network)──► target virt-handler
```

The source `VirtualMachineInstanceMigration` `connectURL` should point to the target cluster's `status.synchronizationAddresses` entry (the `crosscluster0` address).

### Proxy with in-cluster migration network and cross-cluster network

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - DecentralizedLiveMigration
        - CrossClusterMigrationProxy
    migrations:
      decentralizedLiveMigrationDatapath: Proxy
      network: migration-network
      crossClusterNetwork: crosscluster-network
```

Traffic path (`network` set, `crossClusterNetwork` set):

```
source virt-handler ──(migration0)──► source sync controller
  ──(crosscluster0)──► target sync controller
  ──(migration0)──► target virt-handler
```

### Direct datapath (explicit default)

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    migrations:
      decentralizedLiveMigrationDatapath: Direct
```

### Specify node placement for synchronization controllers

If only certain nodes have access to the cross-cluster live migration network, and those nodes are not control-plane nodes, specify `spec.synchronizationPlacement`. This is a normal `ComponentConfig` node placement, just like the ones defined in `kubevirt.spec.workloads.nodePlacement`.

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  synchronizationPlacement:
    nodePlacement:
      nodeSelector:
        kubevirt.io/has-crosscluster-network: "true"
```

## Alternatives

Instead of the proxy approach, we considered a mechanism that would allow specifying a list of IP addresses assignable to virt-handlers. This could work in principle, but it would not remove the scaling problem: whenever new nodes are added, the list would need to be updated to include IPs for those nodes. Moreover, such a mechanism would have to be implemented at the CNI level, and feasibility would depend on the CNI in use.

## Scalability

One of the issues with the current implementation is scalability: as more virt-handlers join the L2/L3 network, the number of required IP addresses grows linearly. The new design addresses this by requiring only as many IPs as there are synchronization controllers (2 by default). The trade-off is higher network utilization. Migration traffic is carried over three segments in sequence—the source cluster migration network, the cross-cluster live migration network, and the target cluster migration network. The same data traverses each segment, increasing total traffic on the migration path.

## Update/Rollback Compatibility

**Update:** No impact on existing clusters. This is net-new functionality and is only active when `decentralizedLiveMigrationDatapath` is set to `Proxy`. When unset, behavior defaults to `Direct` (existing decentralized live migration datapath).

**Rollback:** If a cluster is rolled back to an older version while `decentralizedLiveMigrationDatapath` or `crossClusterNetwork` is set, those fields are ignored because older versions do not define them. No special handling is required.

## Functional Testing Approach

Testing real cross-cluster live migration is difficult with the current kubevirtci setup, so we simulate it using cross-namespace live migration. We define a dedicated cross-cluster live migration network (separate from the default live migration network), enable `decentralizedLiveMigrationDatapath: Proxy` with `crossClusterNetwork`, and run a cross-namespace live migration over it. These tests extend the existing decentralized live migration namespace functional tests (`with cross-cluster migration network proxy` context in `tests/migration/namespace.go`).

## Implementation History

* Cross-cluster migration network proxy support (sync-controller gRPC tunnel, Multus interface attachment, network policies).
* `decentralizedLiveMigrationDatapath` field (`Direct` / `Proxy`) with webhook validation behind `CrossClusterMigrationProxy` feature gate.
* Migration tunnel manager: multiplexed gRPC streams per migration channel, TLS termination at sync-controller proxy.
* `synchronizationPlacement` for scheduling sync controllers on nodes with cross-cluster network access.
* Ingress/Route URL discovery and advertisement in `status.synchronizationAddresses` when `crossClusterNetwork` is unset.

Implementation branch: [kubevirt `create_decentralized_live_migration_network_proxy`](https://github.com/kubevirt/kubevirt/tree/create_decentralized_live_migration_network_proxy)

## Graduation Requirements

### Alpha

Hidden behind the `CrossClusterMigrationProxy` feature gate. If the feature gate is not enabled:

* `decentralizedLiveMigrationDatapath=Proxy` is rejected by the webhook.
* `crossClusterNetwork` is rejected by the webhook.

This allows users to opt in or out without affecting existing decentralized live migrations (which default to `Direct`).

### Beta

Make the feature gate enabled by default per beta requirements, as it is an extension of decentralized live migration.

### GA

Remove the feature gate. The proxy datapath is available whenever the user sets `decentralizedLiveMigrationDatapath: Proxy`.
