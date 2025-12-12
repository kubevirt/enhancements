# VEP #127: Pass MAC address to network binding plugin sidecar

This VEP extends the original network binding plugin design
[document][kubevirt-network-binding-plugin], available at the
kubevirt/community repository.

[kubevirt-network-binding-plugin]: https://github.com/kubevirt/community/blob/9c41536e982072a9b5843571c9d62a8d9bcea448/design-proposals/network-binding-plugin/network-binding-plugin.md

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Pass the interface mac address through the network binding plugin
device-info downwardAPI to the domain configuration sidecar. Let the
sidecar know about interface mac addresses in kubevirt deployments in
which [kubemacpool][kubemacpool-github] is not considered.

[kubemacpool-github]: https://github.com/k8snetworkplumbingwg/kubemacpool


## Motivation

During the domain modification integration point, network binding
plugins can access interface multus [device-info][] if
it was configured so during network binding plugin
[registration][binding-plugin-registration].

However, some network binding plugins might need more information apart
from that available in the device info structure. The network device mac
address is one example.

That is the case, for example, of vhost-vdpa devices. If the mac address
configured in the qemu command line is not properly alligned with the
one configured during vdpa device creation, the network will not work.

[device-info]: https://github.com/k8snetworkplumbingwg/device-info-spec/blob/main/SPEC.md
[binding-plugin-registration]: https://github.com/kubevirt/kubevirt/blob/8423f336564f399401177b87c7cbb9fe02dd9770/staging/src/kubevirt.io/api/core/v1/types.go#L3433


## Goals

Feeding the mac address information from the multus annotation down to
the network binding plugin sidecar for kubevirt deployments that do not
consider kubemacpool.


## Non Goals

Adding a mechanism to support mac address configuration of interfaces by
network binding plugin during live migration. See the ["Live migration
support"](#live-migration-support) section below for more information.


## Definition of Users

Network binding plugin developers who might need to configure network
interface mac address in the domain xml.

The vDPA device network binding plugin it's one use-case example.


## User Stories

- As a network binding plugin developer, I would like to have access to
  the device mac address configured on the pod interface from sidecar's
  `OnDefineDomain` hook without relying upon other components such as
  kubemacpool, or explicit manual specification on the interface spec.


## Repos

- https://github.com/kubevirt/kubevirt


## Design

There is an implementation proposal available in [kubevirt#15898][].

The current approach extends the
[`downwardapi.Interface`][downwardapi-interface] struct with a new field
named `Mac` containing a string representing the expected device mac
address.  As `downwardapi.NetworkInfo`, the struct shared with the
container, contains a list of `downwardapi.Interface` these values are
shared with the sidecar.

The value assigned to the `Mac` field originates from from value
advertised by the the multus network status annotation,
`k8s.v1.cni.cncf.io/network-status`.

[kubevirt#15898]: https://github.com/kubevirt/kubevirt/pull/15898
[downwardapi-interface]: https://github.com/kubevirt/kubevirt/blob/e35bebb3c1675010483748e4bc63c0a339cfa5e7/pkg/network/downwardapi/types.go#L24


## API Examples

When registering a network binding plugin, the `device-info` downwardAPI
option must be configured:

```{yaml}
network:
 binding:
  networkbindingplugin:
   sidecarImage: "domain/repo/image:tag",
   downwardAPI": "device-info",
```

Mac address will be present in the `kubevirt.io/network-info` structure
and will be passed down to the network binding plugin through the same
mechanism as device-info.

```{json}
"kubevirt.io/network-info": {
  "interfaces": [
    {
      "network": "foo",
      "deviceInfo": {
        "type": "pci",
        "version": "1.0.0",
        "pci":{
          "pci-address": "0000:65:00.4"
        }
      },
      "mac": "3a:17:d7:e5:0f:08"
    },
    {
      "network": "bar",
      "mac": "02:44:be:5a:24:86"
    },
  ]
}
```


## Alternatives

- Add a separate downwardapi option such as `mac-address` to the already
  existing `device-info`.
    - Pros:
        - Can make this feature go through the feature graduation process.
    - Cons:
        - Might need another separate annotation, or share the same file
          with the device-info downwardAPI.
        - Might need another struct, when `NetworkInfo` seems to be
          general enough to hold all of this related network device
          information.
        - Might need to add some extra logic to be able to support
          multiple downward APIs for a single network binding plugin at
          the same time.


## Live migration support

This VEP is not related to and does not affect network binding plugin
live migration. Whether live migration is supported by a network binding
plugin or not, is left to be specified by each network binding plugin
implementation. This does not not rely on the device-info downwardapi
containing the pod interfaces' mac address.

Note that live migrating network interfaces require other mechanisms to
ensure that the mac address from the source is preserved in the
destination. This VEP does not introduce such a mechanism to syncronize
source/destination mac addresses. If that is needed, components such as
[kubemacpool][kubemacpool-github] could be helpful.


### Kubevirt and vDPA device live migration

At the moment, one of the main blockers for vDPA live migration in
kubevirt is the way vDPA device lifecycle will be integrated with
kubemacpool. That will involve introducing changes in other CNIs, such
as ovs-cni, or writing a new CNI for that specific use case.

Other challenges include source/host vDPA device feature matching
checks.


## Scalability

N/A


## Update/Rollback Compatibility

It should not impact update compatibility.


## Functional Testing Approach

Unit tests related to the `device-info` downwardAPI are being extended
to cover this struct and annotation extension.


## Implementation History

- 2025/10/17: Initial proposal sent: <https://github.com/kubevirt/kubevirt/pull/15898>


## Graduation Requirements

### Alpha

### Beta

### GA

This VEP extends the network binding plugin device-info downwardapi
mechanism, which is already generally available.

The implementation is backwards compatible, as it just extends the
structure shared through the downwardapi with an optional field. Network
binding plugins that expect the older `NetworkInfo` structure will just
drop and ignore the new field if they don't need it or expect it.

Moreover, the new field is filled with data read from a validated and
trusted source, the multus network status annotation, which makes the
implementation trivial.

For the reasons posted above, I suggest immediately promoting this
feature into general availability in the next release; v1.8.
