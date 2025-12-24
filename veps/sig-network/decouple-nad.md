# VEP 106: Decouple net-attach-def from KubeVirt

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

KubeVirt utilizes Network Attachment Definition (NAD) annotations to enforce node-level network resource requirements. When a NAD is annotated with a resource name, KubeVirt automatically adds its value as a resource request to the virtual machine's virt-launcher pod. This mechanism ensures the Kubernetes scheduler only places the VM on nodes that can provide the required network.
Common use cases include SR-IOV, bridge network when [bridge-marker](https://github.com/kubevirt/bridge-marker) is used and [macvtap](https://github.com/kubevirt/macvtap-cni).

Currently, virt-controller queries the NAD CR for every secondary network in every VM. It does so by querying the API server each time it templates a new virt-launcher pod, which occurs during initial VM startup and every migration.
`NetworkAttachmentDefinition` is an external CRD maintained by the [k8s network plumbing WG](https://github.com/k8snetworkplumbingwg). The mentioned query is the only direct dependency that KubeVirt production code has on this external project.
This proposal aims to eventually remove that query, in favor of an external replacement that will map the custom resources to the virt-launcher pod instead.  
In addition, the virt-controller and virt-operator ClusterRole that currently allow it to access the NAD will eventually be removed.

## Motivation

- This replacement provides the architectural advantage of decoupling KubeVirt from an external dependency and simplifying code maintenance.
- The query is currently a performance bottleneck that has been [observed](https://github.com/kubevirt/kubevirt/issues/14615) in mass VM activations. It can be alleviated by caching NAD data, thus reducing direct API calls.
- Security will be improved by removing access permissions to the external CRD from KubeVirt's control plane components.


## Goals

- KubeVirt will no longer be aware of NetworkAttachmentDefinition objects.
- Decrease the number of API calls required to spin-up and migrate a VM

## Non Goals

Removal of other multus related dependencies

## Definition of Users

This enhancement is intended for:

- VM owners
- Cluster Admins

## User Stories

- As a VM owner, I want to make sure that my VM keeps running following the upgrade. There is no change of VM spec.

- As a Cluster Admin I want to reduce the scope of ClusterRoles as much as possible, so that my system would use the principle of least privilege.

## Repos

- https://github.com/kubevirt/kubevirt
- https://github.com/kubevirt/kubevirtci

## Design

### Current Architecture

KubeVirt currently has a tight coupling with NetworkAttachmentDefinition (NAD) resources. When a VM with secondary networks is launched, the virt-controller directly queries NAD CRs to extract custom resource requirements (`k8s.v1.cni.cncf.io/resourceName` annotation) and injects them into the virt-launcher pod specification, as resource requests and limits.

### Proposed Architecture

Instead of [querying the NAD directly](https://github.com/kubevirt/kubevirt/blob/v1.7.0-rc.0/pkg/network/multus/nad.go#L52) and [processing it in the VMI controller](https://github.com/kubevirt/kubevirt/blob/v1.7.0-rc.0/pkg/virt-controller/services/template.go#L370), an existing external component, [network-resources-injector](https://github.com/k8snetworkplumbingwg/network-resources-injector), can perform the same operation.
This component includes a mutating webhook that automatically mutates Pods, upon creation, to inject network resource requirements from NetworkAttachmentDefinition annotations into pod resource requests and limits.
network-resources-injector, although a standalone project, is also part of the [sriov network operator](https://github.com/k8snetworkplumbingwg/sriov-network-operator/tree/v1.6.0/bindata/manifests/webhook). It is known to run successfully in KubeVirt clusters that already include the `sriov network operator`, 
even though it does not mutate virt-launcher pods, since the existing KubeVirt code pre-populates the pod template with the resource requests before the admission webhook operates.
This architecture decouples KubeVirt from NAD resources, creating a cleaner separation of concerns where KubeVirt focuses on VM lifecycle management while the admission controller handles network resource injection.
A new Feature Gate will be introduced named `DisableNADResourceInjection`, in preparation for NAD query code removal.
If the FG is enabled:

- The VMI/Migration controller code that queries NADs and populates custom resources in the virt-launcher pod template, will be skipped.
- The RBAC rules allowing NAD objects fetch, deployed by virt-operator, will be disabled.

After 2-3 releases, the code that executes NAD querying will be removed along with the code that deploys associated RBAC rules (applies to both options below).

### Deployment

#### Deprecate existing injection code and require manual deployment of network-resources-injector

Documentation will indicate that network-resources-injector is a required dependency for KubeVirt installations that use secondary networks, and will refer users to the github repo for installation instructions.
Documentation will provide configuration instructions for MutatingWebhookConfiguration to optimize performance, by limiting pod interception to only pods that have the `k8s.v1.cni.cncf.io/networks`.
In addition, a deprecation notice has been [published](https://groups.google.com/g/kubevirt-dev/c/5AvvhNYAtqU) in the kubevirt-dev mailing list to let users prepare and respond.
Relevant CI lanes will be enhanced to perform the deployment, using [official image](https://ghcr.io/k8snetworkplumbingwg/network-resources-injector:v1.8.0) clones. This approach is equivalent to KubeVirt's dependency on Multus and how it is currently deployed.
Other deployment systems in the KubeVirt ecosystem can be considered to assist users in deployment, such as [CNAO](https://github.com/kubevirt/cluster-network-addons-operator) or [HCO](https://github.com/kubevirt/hyperconverged-cluster-operator), however this proposal's scope is limited to KubeVirt/KubeVirt only.


### Deployment Alternatives: 

##### Use virt-operator to deploy network-resources-injector

The network-resources-injector repo includes all the source code, build infrastructure and deployment yamls required for successful deployment of the operator.
Built server images are available in ghcr.io, e.g. ghcr.io/k8snetworkplumbingwg/network-resources-injector:v1.8.0
The image will be cloned and published to the official kubevirt public registry.
Virt-operator will be enhanced to deploy and reconcile the network-resources-injector along with its deployment, service, MutatingWebhookConfiguration and RBAC.
The manifests will be copied from the original repo and converted to go code.

### Pros and Cons

#### Selected deployment option: Deprecation

##### Pros

- Easiest option to implement and maintain
- Does not add any code or build steps to current repo

##### Cons

- Breaks backward compatibility 

#### Alternative deployment option: virt-operator

##### Pros

- No backward compatibility breach
- Synchronized upgrade, where removed code is replaced with added code in a controlled manner 
- Allows for opinionated configuration, e.g. optimize to process only virt-launcher pods. 

###### Cons

- Virt-operator currently only deploys internal components and does not reconcile 3rd party tools. Letting it deal with external components may architecturally be a slippery slope that may furthermore contribute to blurring responsibilities between KubeVirt, [CNAO](https://github.com/kubevirt/cluster-network-addons-operator) and [HCO](https://github.com/kubevirt/hyperconverged-cluster-operator).
It will be hard to reason why virt-operator should deploy network-resources-injector, but not other network dependencies such as multus, sriov-operator/device-plugin, various CNIs etc.
- `network-resources-injector` requires multus to be deployed as it is essentially an extension of the project. It implements an informer on the NAD CRD, which is deployed by multus. Multus is not a strict dependency of KubeVirt and is not even deployed in all CI test lanes. Missing the NAD schema is expected to cause errors in virt-operator. 
- Adds a significant maintenance burden to KubeVirt project.
- NAD related RBAC rules remain in virt-operator though bound to network-resources-injector.
- The objective of decoupling is not achieved.
- Since sriov-operator deploys network-resources-injector, clusters with both sriov-operator and KubeVirt deployed, will result in duplicate deployment/reconciliation attempts of network-resources-injector.

> [!NOTE] The rest of this document assumes Option #1

## API Examples

### NetworkAttachmentDefinitions

**SR-IOV Network:**

```yaml
apiVersion: k8s.cni.cncf.io/v1
kind: NetworkAttachmentDefinition
metadata:
  name: sriov-net-attach-def
  annotations:
    k8s.v1.cni.cncf.io/resourceName: intel.com/sriov_net
spec:
  config: |
    {
      "cniVersion": "0.3.1",
      "type": "sriov",
      "vlan": 100
    }
```


### Expected virt-launcher Pod (After network-resources-injector)

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: virt-launcher-testvm-xxxxx
  annotations:
    # KubeVirt applied annotation
    k8s.v1.cni.cncf.io/networks: '[{"name":"sriov-net-attach-def","namespace":"default"}]'
spec:
  containers:
  - name: compute
    resources:
      requests:
        # network-resources-injector applied request
        intel.com/sriov_net: "1"
      limits:
        # network-resources-injector applied limit
        intel.com/sriov_net: "1"
```

## Scalability

The network-resources-injector approach provides several scalability advantages over the current implementation:

- Caching of NADs avoids redundant API calls.
- By default runs in 2 replicas, but can be scaled to run in multiple replicas, thus highly available.

## Update/Rollback Compatibility

Existing VMs in clusters opting in with the feature gate must deploy network-resources-injector before upgrade, so that new VMs and migrated VMs will be able to consume custom network resources.

Note that since the mechanism is replaced. There will be minor changes in the manner in which errors are handled. For example, if a referenced NAD is missing,
The emitted error event will indicate:   
```
Message: failed to create virtual machine pod: admission webhook "network-resources-injector-mutating-config.k8s.cni.cncf.io" denied the request
```
Whereas it currently indicates:
```
Message: failed to render launch manifest: failed to locate network attachment definition default/ptp-conf
```

### Missing Injector Impact

After code is removed (or if FG is enabled), in case system admins fail to install `network-resources-injector`, VMs requiring network devices will fail to run. VMIs remain in scheduling state and virt-launcher pods fail to initialize, as Kubelet does not request a device from the device plugin.

Examples:

- In case of SR-IOV, sriov-cni fails as it does not receive an allocated PCI device from kubelet. The following event is emitted:

```
Warning  FailedCreatePodSandBox  11s   kubelet            Failed to create pod sandbox: rpc error: code = Unknown desc = failed to setup network for sandbox "6be33fd689d91f280d6f82b9c6e3f018968fed39cd7762eb03f046bae10b8773": plugin type="multus" name="multus-cni-network" failed (add): [default/virt-launcher-testvmi-txhzt-p4hxv:sriov]: error adding container to network "sriov": SRIOV-CNI failed to load netconf: LoadConf(): VF pci addr is required
```

- In case of macvtap, macvtap cni fails for a similar reason: It cannot find a device because it does not receive an allocated device from kubelet. The following event is emitted:

```
Warning  FailedCreatePodSandBox  0s (x6 over 72s)  kubelet  : Failed to create pod sandbox: rpc error: code = Unknown desc = failed to create pod network sandbox k8s_virt-launcher-testvmi-vnmz7-rdm9r_kubevirt-test-default1_d3ea53de-aa74-4128-b91a-a94d9c126e89_0(1f24220d8d124bd8552c287f16e06a35fd5e510f1ed9a08890927aa93094fc4e): error adding pod kubevirt-test-default1_virt-launcher-testvmi-vnmz7-rdm9r to CNI network "multus-cni-network": plugin type="multus-shim" name="multus-cni-network" failed (add): CmdAdd (shim): CNI request failed with status 400: 'ContainerID:"1f24220d8d124bd8552c287f16e06a35fd5e510f1ed9a08890927aa93094fc4e" Netns:"/var/run/netns/b5eaba98-9d38-42bc-b1d2-c2a101b65a90" IfName:"eth0" Args:"IgnoreUnknown=1;K8S_POD_NAMESPACE=kubevirt-test-default1;K8S_POD_NAME=virt-launcher-testvmi-vnmz7-rdm9r;K8S_POD_INFRA_CONTAINER_ID=1f24220d8d124bd8552c287f16e06a35fd5e510f1ed9a08890927aa93094fc4e;K8S_POD_UID=d3ea53de-aa74-4128-b91a-a94d9c126e89" Path:"" ERRORED: error configuring pod [kubevirt-test-default1/virt-launcher-testvmi-vnmz7-rdm9r] networking: [kubevirt-test-default1/virt-launcher-testvmi-vnmz7-rdm9r/d3ea53de-aa74-4128-b91a-a94d9c126e89:net1]: error adding container to network "net1": failed to lookup device "": Link not found
```

## Functional Testing Approach

Existing e2e tests already rely on mapping of custom resources and thus will validate regression.

## Feature Lifecycle

### Beta (Skipping Alpha)

  Since no new code addition is planned (other than validation warnings), there's not much to protect with a multi-phased FG.
  The FG is mainly used in order to provide users with sufficient preparation time, and a means to roll back if they failed to deploy network-resources-injector.
  As such:
  - In release 1.8, the `DisableNADResourceInjection` FG will be introduced in **disabled by default** mode.
    While the FG is enabled, KubeVirt will **not** map custom-resources, and will **not** deploy associated RBAC rules.
    The NAD query and RBAC code will be marked as deprecated.
    This provides users with 1 release period to learn about the new required dependency.
  - In release 1.9 or 1.10 (user feedback dependent), the FG will be redesignated as **enabled by default**, essentially functioning as if the code was removed. However, users can still roll back this behavior by explicitly disabling the FG. 

  Documentation will introduce the FG, highlight the deprecation and reference the network-resources-injector installation instructions.
  A warning will be issued for the VM API in case secondary networks exist and FG is not enabled. A validation webhook will be implemented to issue the warning at VM creation. 


### GA

  In release 1.10 or 1.11, if there's no significant negative feedback from users, the NAD query and RBAC code will be removed, and the FG discontinued.
