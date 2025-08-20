# VEP #NNNN: Passt Binding Core Migration and Beta

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir i[ns-admin-nichotplug.md](ns-admin-nichotplug.md)n [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

The `passt` network binding is currently implemented as a [network binding plugin](https://kubevirt.io/user-guide/network/network_binding_plugins), and is in Alpha stage.
Since `passt` binding has benefits over Masquerade and Bridge bindings, being a userspace network binding, it is desirable to migrate it (back) into the core,
thus taking full ownership over its code, simplifying the architecture and implementation a great deal, and treating it as a trustable "first class citizen" in the code.
This change, targeted at KubeVirt release 1.6, will also promote the feature stage from Alpha to Beta, and will be conditioned by a new Feature Gate `PasstBindingEnabled`.

> [!NOTE] 
> `passt` used to be a core network binding, and was [removed](https://github.com/kubevirt/kubevirt/pull/11915) in KubeVirt release 1.3, as Network Binding Plugins 
functionality was introduced.   

## Motivation

VEP #20 describes the benefits of passt in detail, it commits to migrate the functionality to the core for the Beta release, and KubeVirt will benefit
from accelerating its graduation as early as possible.
Technically, the functionality that was implemented for VEP#20 Alpha is very complex, the architecture is far from straight-forward, and having the implementation
distributed to many different areas of the product is worsened as a consequence of the plugin implementation.
Specifically, product areas that can be consolidated into concise core code include:
- Sidecar container along with associated GRPC communication + its build/publish process
- CNI plugin + its build/publish process
- DaemonSet that deploys the CNI plugin
- A Network Attachment Definition CR
- KubeVirt Configuration 

E2e tests depend on all of the above.
In addition, since the current implementation is a plugin, the core code cannot refer to it specifically, and instead [looks
for hints](https://github.com/kubevirt/kubevirt/blob/release-1.6/pkg/network/passt/repair.go#L122) of `passt` as a workaround for not being able to reference the API.


## Goals

- Simplification and reduction of code and architecture complexity
- Simplified, documented API for users
- Simplification of operability and testing

## Non Goals

- Functional changes

## Definition of Users

- VM Owner
- Cluster Admin

## User Stories
- As a Cluster Admin I would like to deal with fewer moving parts and dependencies for my network bindings to function.   
- As a VM Owner I would prefer to have a straightforward, documented VM API that natively supports the bindings that I frequently use.   

## Repos

- kubevirt/kubevirt

## Design

### API Changes 
The passt core network binding will be enabled by a new InterfaceBindingMethod member:
```go
type InterfaceBindingMethod struct {
	...
	PasstBinding InterfacePasstBinding `json:"passtBinding,omitempty"`
}

type InterfacePasstBinding struct{}
```
> [!NOTE]
> DeprecatedPasstBinding is still a member of the struct. It represents a previous generation of passt which has been deprecated in release 1.3

A new Feature Gate `PasstBindingEnabled` will be introduced, to condition the use of this binding.

### Validation Webhook
`passtBinding` network binding will only be allowed for interfaces bound to `Pod` network. (TBD multus default network?).
In addition, the admitter will also validate enablement of the PasstBindingEnabled FG. 
A validation will be implemented in the network binding admitter.

### virt-controller
250Mi of RAM will be added in renderresources to virt-launchers if VMI has passtBinding, as those are the overheads that passt requires (if all ports are forwarded).

### CNI code migration
The passt CNI currently invokes 2 sysctl calls:
- Allow ping group range for user 107
- Allow listening on privileged ports starting from 0.
Both of those calls will be implemented in virt-handler for passtBinding related pods. The nmState spec properties already exist in the [code](**https://github.com/kubevirt/kubevirt/blob/release-1.6/pkg/network/setup/netpod/netpod.go#L319)
for `DeprecatedPasstBinding`, they just need to be enabled for passtBinding.

### Sidecar Container code migration
The network generator will be enhanced to populate the DomainXML with the passt network interface. The code will be similar to the code the currently configures 
the domain in the sidecar container, including the selection of primary interface from the VMI status as introduced by https://github.com/kubevirt/kubevirt/pull/15131.
For example: this is the interface representation when no specific ports to forward are specified. 
```xml
    <interface type="vhostuser">
      <source dev="ovn-udn1"/>
      <model type="virtio-non-transitional"/>
      <alias name="ua-passtnet"/>
      <backend type="passt" logFile="/var/run/kubevirt/passt.log"/>
      <portForward proto="tcp"/>
      <portForward proto="udp"/>
    </interface>
```
As in the current sidecar code, when Istio is enabled, its reserved ports will be marked for exclusion. 

### passt-repair call modifications
Currently, passt-repair is called during migration only if a network binding plugin is defined in the VMI spec, this condition will be enhanced to include
`passtBinding` interface binding.

## API Examples
### InterfaceBindingMethod passtBinding
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
spec:
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: default
              passtBinding: {}
      networks:
        - name: default
          pod: {}
```

> [!NOTE]
> The InterfaceBindingMethod `passt` cannot be used as it was deprecated in release 1.3, therefore a new name is being used: passtBinding  

## Alternatives

As an alternative the deprecated InterfaceBindingMethod `passt` can be resurrected, however that could be confusing to users and make documentation cumbersome.  

## Scalability

This feature can only improve scalability as it removes the sidecar container, and eliminates back and forth communication with it. 
It may also slightly accelerate VM startup time by removing a CNI action and one NAD lookup.   

## Update/Rollback Compatibility

The `passt` network binding plugin will be deprecated once the feature is GA, thus the code will continue to support the plugin for at least 4 more releases.
Existing users may retain existing passt plugin based VMs until then, or edit+restart VMs with core passt network binding.  

## Functional Testing Approach

Current e2e tests will be slightly modified to configure VMs to use the core passt binding, instead of the plugin.
Those tests should assure regression of functionality.
The existing e2e tests using passt plugin will continue to run for another version.

## Implementation Phases

Per VEP #20, this proposal is for the Beta stage of the feature.

## Feature lifecycle Phases

### Alpha

### Beta
Protected with FG `PasstBindingEnabled`.
Bug fixes and new functionality will still be applied to the passt binding plugin. 

### GA
Following testing and gathering of user feedback and bug fixing, `passtBinding` network binding can be graduated to GA in KubeVirt release 1.8.
The passt NetworkBinding plugin and associated code/processes will deprecate at this point.