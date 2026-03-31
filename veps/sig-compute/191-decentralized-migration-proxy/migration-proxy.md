# VEP #191: Decentralized Live Migration LM network proxy support 

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Add a proxy to the synchronization controllers that forwards cross-cluster live migration traffic onto the cross-cluster live migration network. Doing so reduces the number of IP addresses required for cross-cluster live migration.

## Motivation

During testing of Decentralized Live Migration we encountered environments we had not considered before, which require additional functionality. In particular:

* **Multi-cluster, single LM network:** More than two clusters are connected through the same live migration (LM) network.
* **Constrained addressing:** The number of available IP addresses on this network is limited; there is no DHCP, and obtaining address blocks for virt-handlers is difficult.
* **Full mesh:** Each cluster must be able to migrate VMs to and from any other cluster, so every cluster must be reachable over the LM network.

Using the LM network directly for Decentralized Live Migration is problematic in this setting because it requires a large number of IP addresses. For each cluster we need *(virt-handlers + synchronization controllers)* addresses—i.e., one per virt-handler and one per synchronization controller—and that cost is incurred in every cluster. For example, with 10 clusters of 10 worker nodes (10 virt-handlers each) and 2 synchronization controllers per cluster, we need 10 × (10 + 2) = **120 IP addresses** on the cross-cluster live migration network. Without DHCP, assigning and maintaining these addresses is unwieldy.

A further issue is operational: when nodes are added or removed, any mechanism that assigns IP addresses to virt-handlers must also update addressing on the cross-cluster live migration network, increasing configuration churn and failure modes.

## Goals

* **Reduced addressing:** With the proxy in place, the cross-cluster live migration network needs only one IP per synchronization controller. Total addresses = (synchronization controllers per cluster) × (number of clusters). Example: 2 controllers per cluster × 10 clusters = 20 addresses.
* **Dedicated cross-cluster network:** Support configuration of a cross-cluster live migration network that is separate from the in-cluster live migration network.
* **Placement control:** Allow admins to choose whether synchronization controller pods run on control-plane or worker nodes. Control-plane nodes often lack access to the cross-cluster live migration network; worker nodes typically have it. Support an optional node selector so admins can target the (possibly small) set of nodes that have cross-cluster live migration network access.

## Non Goals

* Allow proxying of any traffic besides cross cluster live migrations.
* Proxying cross namespace migrations. Those can go over the internal migration network.

## Definition of Users

* Cluster Admins
* Admins managing several clusters

## User Stories

* As a KubeVirt admin managing multiple clusters on a network with limited IP addresses, I want to perform cross-cluster live migration without building and maintaining a complex network that requires many addresses.
* As a KubeVirt admin, I want to configure a dedicated cross-cluster live migration network that is separate from the in-cluster live migration network.
* As a KubeVirt admin, I want to choose whether synchronization controller pods run on control-plane nodes or worker nodes.
* As a KubeVirt admin, I want to use a node selector to place synchronization controllers on specific nodes—for example, the subset of nodes that have access to the cross-cluster live migration network.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

### Current state migration network
The current live migration network design poses a problem when there are many clusters connected through a single live migration network. Especially if there are constraints on that network in how it is configured. In particular if we need to manually assing IP addresses to virt-handlers and synchronization controllers. And there is no DHCP availabe on the network. It is technically possible to design a `NetworkAttachmentDefinition` for each cluster that gives a range of IP addresses to a certain cluster, but it becomes a lot harder if the pool of available IP addresses is not a range. Also the number of IP addresses can become problematic if the number of virt-handlers per cluster is large enough. The current state is illustrated in figure 1:

![figure 1](l2_4cluster.png)


### Proposed state migration network
The proposed proxy will allow the same connectivity on an L2 network, but instead of every virt-handler being able to communicate with every other virt-handler directly. It will go through a proxy. The proxy will communicate with a proxy on the other cluster. This reduces the number of IP addresses needed from the number of virt-handlers + the number of synchronization controllers times the number of clusters to just the number of synchronization controllers times the number clusters. The proposed state is illustrated in figure 2:

![figure 2](proxy_cluster.png)

As you can see from the figure, the synchronization controller has two network interfaces. One connected to the 'in cluster' network, and one connected to the shared L2 network. The network is marked L2 in the figure, but it can also be an L3 network, as long as synchronization controllers can connect to each other it will work.

In order to define a specific cross cluster live migration network, there will need to be a new API field in the migration struct of the `KubeVirt` CR. This will be an optional additional field called `crossClusterNetwork`. It will be the name of a `NetworkAttachmentDefinition` just like the live migration network is now.

### Current state synchronization controller placement
Currently the synchronization controllers are marked as control plane pods, and are thus scheduled on the control plane nodes. This works if the control plane nodes have access to the live migration network. This is not guaranteed to be the case.

### Proposed state synchronization controller placement

## API Examples

### Cross cluster live migration network
Add an additional field to `kubevirt.spec.configuration.migrations` called `crossClusterNetwork`. If enabled this causes the synchronization controller pods to be started with an additional network besides whatever the live migration network is.

Additional cross cluster migration network
```yaml
...
migrations
  ...
  crossClusterNetwork: crosscluster-network
  ...
```

### Specify node placement for synchronization controllers

If only certain nodes have access to the cross cluster live migration network, and those nodes are not control plane nodes it will be necessary to specify the `nodePlacement` for the synchronization controllers. In order to specify the node placement for synchronization controller an additional field `kubevirt.spec.synchronizationPlacement` will be added. This will be a normal nodePlacement just like the ones defined in `kubevirt.spec.workloads.nodePlacement`.

Additional node placement
```yaml
...
spec
  synchronizationPlacement
    nodeSelector:
    - label: selector
...
```

## Alternatives

Instead of the proxy approach, we considered a mechanism that would allow specifying a list of IP addresses assignable to virt-handlers. This could work in principle, but it would not remove the scaling problem: whenever new nodes are added, the list would need to be updated to include IPs for those nodes. Moreover, such a mechanism would have to be implemented at the CNI level, and feasibility would depend on the CNI in use.

## Scalability

One of the issues with the current implementation is scalability: as more virt-handlers join the L2/L3 network, the number of required IP addresses grows linearly. The new design addresses this by requiring only as many IPs as there are synchronization controllers (2 by default). The trade-off is higher network utilization. Migration traffic is carried over three segments in sequence—the source cluster migration network, the cross-cluster live migration network, and the target cluster migration network—so the same data traverses each segment, increasing total traffic on the migration path.

## Update/Rollback Compatibility

**Update:** No impact. This is net-new functionality and is only active when the `crossClusterNetwork` field is set.

**Rollback:** If a cluster is rolled back to an older version while `crossClusterNetwork` is set, that field is ignored because older versions do not define it. No special handling is required. 

## Functional Testing Approach

Testing real cross-cluster live migration is difficult with the current kubevirtci setup, so we will simulate it using cross-namespace live migration. We will define a dedicated cross-cluster live migration network (separate from the default live migration network), then run a cross-namespace live migration over it. These tests will extend the existing decentralized live migration namespace functional tests.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

### Alpha
Tied to decentralized live migration. It will not be hidden behind a feature gate since, it is only enabled if the `crossClusterLiveMigration` network is defined. This will allow users to opt-in or out at will without affecting any decentralized live migrations.

### Beta
Tied to decentralized live migration, as it is an extension of decentralized live migration

### GA
Tied to decentralized live migration
