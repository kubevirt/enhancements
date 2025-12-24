# VEP: Adding NetworkAttachmentDefinitions Metric

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

Introduce a new Prometheus metric to expose information about the NetworkAttachmentDefinitions (NADs) used by VirtualMachines.
This will help identify and monitor the external networks VMs are connected to.

## Motivation

NADs provide crucial configuration for VM networking. Knowing which external network each VM connects to and its properties
(e.g., CNI type, VLAN, topology) is critical for debugging, auditing, and optimization.
This metric fills a visibility gap in VM-network associations.

## Goals

- Add a metric named kubevirt_network_attachment_definition_info.
- Expose NAD properties relevant to VM connectivity as Prometheus labels.
- Support multiple CNI types including OVN, SR-IOV, bridge, and macvlan.

## Non Goals

- This metric does not expose pod-level or container-level network information.
- It will not validate or mutate NADs.
- Does not track live usage or connections per VM instance.

## Definition of Users
- Cluster administrators
- CNV developers
- SREs needing visibility into VM network configuration

## User Stories

- As a cluster admin, I want to monitor what networks VMs are connected to.
- As a CNV engineer, I want to audit CNI types used across clusters.
- As an SRE, I want to correlate network config issues to VM disruptions.

## Repos

kubevirt/kubevirt.

## Design

Metric name: kubevirt_network_attachment_definition_info
Type: GaugeVec
Help: Details about additional network interfaces attached to Virtual Machines.

Labels:

| Label                                | Values                                         | Notes                                 |
| ------------------------------------ |------------------------------------------------|---------------------------------------|
| `namespace`                          | string                                         | From vm.metadata.namespace            |
| `network`                            | string                                         | From nad.metadata.name                |
| `vlan`                               | 0-4095                                         | From nad.config.vlan                  |
| `cni_type`                           | e.g., `bridge`, `sriov`, `ovn-k8s-cni-overlay` | From config.type                      |
| `ipam_type`                          | string                                         | From config.ipam.type                 |
| `ovn_subnets`                        | comma-separated CIDRs                          | From config.subnets                   |
| `udn_role`                           | `primary`/`secondary`                          | From config.role                      |
| `ovn_topology`                       | `layer2`/`layer3`/`localnet`                   | From config.topology                  |
| `ovn_persistent_ips`                 | `true`/`false`                                 | From config.allowPersistentIPs        |
| `mac_spoof_filtering`                | `true`/`false`/`off`/`on`                      | From config.macspoofchk/spoofchk      |
| `bridge_preserving_default_vlan`     | `true`/`false`                                 | From config.preserveDefaultVlan       |
| `bridge_disable_container_interface` | `true`/`false`                                 | From config.disableContainerInterface |

Data is extracted from nad.Spec.Config or nad.Spec.Config -> “plugins”. The implementation parses both formats.

## API Example
Example metric output:

kubevirt_network_attachment_definition_info{
namespace="demo",
network="l2-primary",
cni_type="ovn-k8s-cni-overlay",
udn_role="primary",
ovn_topology="layer2",
ovn_subnets="10.0.0.0/16,2001:db8::/60",
ipam_type="<none>",
vlan="<none>",
ovn_persistent_ips="<none>",
mac_spoof_filtering="<none>",
bridge_preserving_default_vlan="<none>",
bridge_disable_container_interface="<none>"
}

## Alternatives

Integrate this metric into multus-admission-controller instead of KubeVirt. This was discarded as it complicates ownership and deployment.

## Scalability

Metric cardinality is limited by the number of NADs, not per VM, making it scalable.

## Update/Rollback Compatibility

Safe to add. No existing APIs or metrics are changed.

## Testing Approach

- Unit tests will mock NAD inputs with various Spec.Config structures.
- e2e testing will deploy VMs with different network configs and validate exported metrics.

## Implementation Phases

1. Implement NAD parsing and metric registration in virt-controller.
2. Add unit tests.
3. Add e2e tests using example NADs.


## Feature lifecycle Phases

### GA (v1.7.0)
- Documented in official CNV and OpenShift documentation.
- Covered by end-to-end test suites.


