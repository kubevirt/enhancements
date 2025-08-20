# VEP #NNNN: Protect Pods with NetworkPolicies

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview
This VEP suggest to deploy NetworkPolicies as part of the KubeVirt deployment, to protect pods from unwanted traffic.
The VEP will suggest how to implement in KubeVirt, and all other components that are deployed with it, including the
HCO, when it is used to deploy KubeVirt, but also CDI, CNAO, and SSP.

> Note: In this VEP, when we refer to KubeVirt, we also mean the components that may be deployed with it, such as CDI,
> CNAO, SSP, AAQ, and HCO.

## Motivation
Following the [introduction of the AdminNetworkPolicy and BaselineAdminNetworkPolicy](https://network-policy-api.sigs.k8s.io/api-overview/)
kubernetes resources, the cluster administrators can define a set of network policies that will be applied to all pods
in the cluster, or to a specific namespace.

This allows the cluster administrators to define a set of rules that will block important traffic to or from KubeVirt
pods, and that may have a negative impact on the KubeVirt functionality, if done incorrectly.

But even if the no AdminNetworkPolicy or BaselineAdminNetworkPolicy is defined, deploying NetworkPolicies increases the 
security of KubeVirt, by allowing only the required traffic to and from the pods, and blocking all other traffic.

This VEP suggests to deploy NetworkPolicies as part of the KubeVirt deployment, to protect pods from unwanted traffic,
but also to allow traffic that may be blocked by the cluster administrators. Even if the cluster administrators blocked
the traffic in a way that the NetworkPolicies cannot override, still the existence of the NetworkPolicies will help us
to identify the issue, and to help the cluster administrators to better define the cluster scope policies, by having a 
reference of the traffic that KubeVirt pods need.

## Goals
Define how to deploy NetworkPolicies as part of the KubeVirt deployment.

## Non Goals
The VEP does not aim to get into the specific details of the NetworkPolicies specifications.

## Definition of Users
* Component Developer: This includes developers of KubeVirt, CDI, CNAO, SSP, AAQ, and HCO.
* Cluster Administrator: This includes administrators of the Kubernetes cluster, who are responsible for the overall
  security of the cluster.

## User Stories
* Deployment of core KubeVirt
* Deployment of KubeVirt components that are deployed with it, such as CDI, CNAO, SSP, AAQ, and HCO, Using
  [Operator Lifecycle Manager (OLM)](https://olm.operatorframework.io/).
* Deployment of KubeVirt components that are deployed with it, such as CDI, CNAO, SSP, AAQ, and HCO, using HCO's 
  eployment scripts

## Repos
* KubeVirt core: [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
* HCO: [kubevirt/hyperconverged-cluster-operator](https://github.com/kubevirt/hyperconverged-cluster-operator)
* CDI: [kubevirt/containerized-data-importer](https://github.com/kubevirt/containerized-data-importer)
* SSP: [kubevirt/ssp-operator](https://github.com/kubevirt/ssp-operator)
* CNAO: [kubevirt/cluster-network-addons-operator](https://github.com/kubevirt/cluster-network-addons-operator)
* AAQ: [kubevirt/application-aware-quota](https://github.com/kubevirt/application-aware-quota)

## Design
### Deploying NetworkPolicies using OLM
#### Overview
We are publishing the KubeVirt components in the OperatorHub[https://operatorhub.io/operator/community-kubevirt-hyperconverged],
to be installed using OLM. 

This includes an index image, that is a collection of versions of the operator, and bundle images, that includes the
installation manifests for a specific version.

The bundle images are a data only images. It contains the `manifests` directory, which includes the CSV (Cluster Service
Version) yaml file, that defines the operator deployments, their RBAC permissions, the webhooks, and so on. The
`manifests` directory also includes the required CRD yaml files.

From OLM version v0.32.0, it is also possible to add `NetworkPolicy` yaml files in the `manifests` directory, and OLM
will deploy them as part of the operator deployment.

> note: OLM will only deploy namespace scoped resources in the installation namespace, regardless the `namespace` field
> of the resource.

The OLM does not maintain the NetworkPolicies after they are deployed. It does not remove them on upgrade, if the next
version of the operator does not include them.

#### Definitions
In this section, "Operator" refers to a component deployed by OLM, such as the virt-operator, or
the hyperconverged-clustrer-operator (HCO), and so on.

"Operand" refers to a component that is deployed by the operator, such as virt-api, cdi-controller, aaq-controller, and
so on.

#### Implementation
According to OLM requirements, the bundle image may only contain NetworkPolicies that are required for the **operators**
themselves.

##### NetworkPolicies for operands
Any NetworkPolicy that is required for the **operands**, must be deployed by the operator that creates the specific
operand.

The operators are responsible to deploy the NetworkPolicies that are required for the operands, and to reconcile them to
the required state. This VEP will not impose any further requirements on the operators, and it is the responsibility
of each component developer to define the required NetworkPolicies for their component, and implement the required logic
to deploy and reconcile them.

##### NetworkPolicies for the Operator
The operators NetworkPolicies will be added to the `manifests` directory of their bundle image. Each operator image
already contains the `csv-generator` application that is used to generate the CSV and the CRD files. These files are
collected by the `build-manifests` script in the HCO repository, and are added to the `manifests` directory of the
bundle image.

The `csv-generator` application will be extended to also generate the NetworkPolicies yaml files, if a new flag 
named `--dump-network-policies` is set to `true`. The `build-manifests` script in HCO will add the generated 
NetworkPolicies to the `manifests` directory of the bundle image.

##### Common NetworkPolicies for the operators
The HCO's `csv-merger` application, that is included in the HCO operator image, will provide three common use
NetworkPolicies:
* `hco-allow-egress-to-dns`: This NetworkPolicy allows the pods to access the DNS service in the cluster.
* `hco-allow-egress-to-api-server`: This NetworkPolicy allows the pods to access the Kubernetes API server.
* `hco-allow-ingress-to-metrics-endpoint`: This NetworkPolicy allows Prometheus to read metrics from the pod.

The purpose of these NetworkPolicies is to ease the component developers, and to provide a common set of
NetworkPolicies that are required for most of the components.

The `hco-allow-egress-to-dns` and the `hco-allow-egress-to-api-server` NetworkPolicies will applied to any pod in the
installed namespace, with the `hco.kubevirt.io/allow-access-cluster-services` label. i.e., the NetworkPolicies's `podSelector`
will be:
```yaml
spec:
  podSelector:
    matchExpressions:
      - key: hco.kubevirt.io/allow-access-cluster-services
        operator: Exists
```

The `hco-allow-ingress-to-metrics-endpoint` NetworkPolicy will be applied to any pod in the installed namespace, with
the `hco.kubevirt.io/allow-prometheus-access` label; i.e., the NetworkPolicy's `podSelector` will be:
```yaml
spec:
  podSelector:
    matchExpressions:
      - key: hco.kubevirt.io/allow-prometheus-access
        operator: Exists
```

A component developer that wants to use these NetworkPolicies, can add the required labels to the pods in their
components, and the NetworkPolicies will be applied to them.

##### Deprecated NetworkPolicies on Upgrade
The `build-manifests` script will add the `hco.kubevirt.io/csv-version` label to each NetworkPolicy resource with the
CSV version.

On upgrade, HCO will remove any NetworkPolicy that has a different `hco.kubevirt.io/csv-version` label than the current
CSV version. This will ensure removing of redundant NetworkPolicies that are not included in the new version, as this is
not done by OLM.

### Deploy using HCO's deployment scripts
**TBD**: This will be very similar to the OLM deployment use-case. The different is only the packaging of the
NetworkPolicies yaml files in HCO repository.

### Deploying with specific component scripts or manifests
This is left for each team to decide how to implement, but the general idea is to provide a way to deploy the
NetworkPolicies that are required for the operator of the component.

#### Suggestion
Each component will add an option to the csv-generator, to provide **only** the NetworkPolicies yaml files
without the CDR or the CSV. This will allow to collect the network policy into a deploy-script, or to allow the user to
deploy NetworkPolicies directly with something similar to:
```shell
podman run --rm --entrypoint csv-generator quay.io/kubevirt/virt-operator:v1.2.3 --dump-network-policies=true --dont-dump-crd-csv=true | \
kubectl apply -f -
```

## API Examples
n/a

## Alternatives
TDB - during discussion

## Scalability
n/a

## Update/Rollback Compatibility
See the [Deprecated NetworkPolicies on Upgrade section above](#deprecated-networkpolicies-on-upgrade). 

## Functional Testing Approach
Q: should we test the non-functional feature, like the NetworkPolicies?

If the regular functional tests are working properly, then the NetworkPolicies are defined correctly.

## Implementation Phases
Each component should implement the following steps:
* Add logic to the operator to deploy the NetworkPolicies that are required for all the operands that are deployed by
  the operator, and reconcile them to the required state.

* add support for the `--dump-network-policies` flag to the `csv-generator` application, to generate the
  NetworkPolicies yaml files for the operator itself.

## Feature lifecycle Phases
n/a - it is expected from each component to always deploy the operand's NetworkPolicies, and to update them as needed.
The NetworkPolicies for the operators will be provided by the csv-generator application of each component, depending on
the `--drop-network-policy` flag, and in each component deploy scripts or manifests.
