# VEP 106: Decouple net-attach-def from KubeVirt 

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

**Target Version**: v1.8

VMs that use secondary networks inject custom resource requests into the virt-launcher pod template whenever custom resources are required to exist on the node in order to fulfill the secondary network.
One such example is SR-IOV free VFs that must be available on a node for the VM to make use of.
The names of those custom resources are listed as an annotation in the network-attachment-definition (NAD) CR that corresponds to the secondary network requested by the VM.

Currently, virt-controller queries the NAD CR for every secondary network in every VM that is launched.
Network-attachment-definition is an external project maintained by the k8s network plumbing WG. The mentioned query is the only direct dependency that KubeVirt production code has on this external project.
This proposal aims to remove that query and code dependency in favor of an external replacement that will map the custom resources to the virt-launcher pod instead.  
In addition, the virt-controller role binding that currently allows it to access the NAD can be reduced in scope.


## Motivation

<!--
Why this enhancement is important
-->

The replacement has the architectural advantage of decoupling KubeVirt from an external dependency by simplifying code maintenance and supporting future replacement technologies.
Security will be improved by removing access permissions to the external CRD.
In addition, a performance improvement can be gained from using a project that caches NAD data. 
    

## Goals

NAD query code will be deleted from virt-controller and a replacement injector will be introduced. 

## Non Goals

Removal of other multus related dependencies 

## Definition of Users

This enhancement is intended for:
- VM owners
- Namespace owners

## User Stories

- As a VM owner, I want to accelerate VM load time
- As a VM owner and a namespace owner, I want my system to use the principle of least privilege, such that service accounts will only have access to resources that they absolutely need. 

## Repos

https://github.com/kubevirt/kubevirt
https://github.com/k8snetworkplumbingwg/network-resources-injector

## Design

### Current Architecture
KubeVirt currently has a tight coupling with NetworkAttachmentDefinition (NAD) resources. When a VM with secondary networks is launched, the virt-controller directly queries NAD CRs to extract custom resource requirements and injects them into the virt-launcher pod specification.

### Proposed Architecture
The new architecture decouples KubeVirt from NAD resources by leveraging the network-resources-injector as an external admission controller. This creates a cleaner separation of concerns where KubeVirt focuses on VM lifecycle management while network-resources-injector handles network resource injection.

### High-Level Flow

**Current Flow:**
During VMI creation, virt-controller queries relevant NAD CRs per requested secondary networks. It extracts the `k8s.v1.cni.cncf.io/resourceName` annotation
and adds it as a resource to the virt-launcher pod's resource requests/limits.  

**Proposed Flow:**
network-resources-injector will deploy a mutating webhook that will process virt-launcher pods during admission.
It will inject the same resource requests/limits to virt-launcher pods through its own webhook implementation. 
Since it uses caching, it is expected to perform better than the current flow. 

### Deployment
Similar to Multus and other KubeVirt dependencies, users will be expected to deploy network-resources-injector manually if they use custom resources such as SR-IOV, bridge-marker, or macvtap.
Documentation will be updated to emphasize the dependency.
KubeVirt CI will be enhanced to deploy network-resources-injector in relevant network lanes.

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
    k8s.v1.cni.cncf.io/networks: '[{"name":"sriov-net-attach-def","namespace":"default"}]'
spec:
  containers:
  - name: compute
    resources:
      requests:
        intel.com/sriov_net: "1"
      limits:
        intel.com/sriov_net: "1"
```

## Alternatives

### Alternative 1: Add caching to current implementation
**Pros:**
- Direct control over resource injection logic
- Performance improvement from caching

**Cons:**
- Maintains external dependency on NetworkAttachmentDefinition client
- Keeps tight coupling with external projects
- NAD RBAC role remains necessary for virt-controller


### Alternative 2: Implement Mutating Webhook in virt-operator
**Pros:**
- Keeps functionality within KubeVirt ecosystem
- No external dependencies

**Cons:**
- Adds complexity to virt-operator
- Duplicates admission controller functionality
- Keeps tight coupling with external projects


## Scalability

The network-resources-injector approach provides several scalability advantages over the current implementation:
- Caching of NADs 
- Can be scaled to run in multiple replicas

## Update/Rollback Compatibility

Existing VMs in clusters opting in with the feature gate will have to deploy network-resources-injector during upgrade so that new VMIs and migrated VMs will be able to consume custom network resources. 

## Functional Testing Approach

Existing e2e tests already rely on mapping of custom resources and thus will validate regression.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha
  Will be introduced in KubeVirt v1.8, protected by the `decoupleNAD` FG. If the FG is set, KubVirt will **not** map custom-resources. 

### Beta
  Given the simplicity of the proposal (per KubeVirt, simply deletion of code), Beta will not be required. 

### GA
  Unless negative feedback is received, the feature can be graduated in v1.9.


