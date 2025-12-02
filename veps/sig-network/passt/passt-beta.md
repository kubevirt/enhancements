# VEP #21 - Part 2: Passt Binding Core Migration and Beta

## Overview
Following [the initial proposal](./passt-migration-proposal.md), this part extends the `passt` KubeVirt integration to the Beta phase.
The `passt` network binding is currently implemented as a [network binding plugin](https://kubevirt.io/user-guide/network/network_binding_plugins) and is in Alpha phase.
This change migrates the passt binding into the KubeVirt core and exposes it in the API, it will also promote the feature phase from Alpha to Beta and will be conditioned by a new feature gate.
The aim is to target KubeVirt release 1.8; however, that depends on having sufficient feedback for the Alpha phase.
The core passt binding will enable the seamless migration functionality that was introduced in the Alpha phase.
 
The seamless migration functionality will be discontinued for passt binding plugin's users, but the plugin itself will continue to be supported.
The `passtIPStackMigration` feature gate that was introduced in the Alpha phase will be discontinued as well.

> [!NOTE] 
> `passt` used to be a core network binding and was [removed](https://github.com/kubevirt/kubevirt/pull/11915) in KubeVirt release 1.3, as Network Binding Plugins 
> functionality was introduced.

## Design

### API Changes 

#### User-facing API

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

The new `passt` core network binding will be enabled by a new InterfaceBindingMethod member.

Its JSON/YAML name will be `passtBinding`.

> [!NOTE]
> The InterfaceBindingMethod `passt` cannot be used as it was deprecated in release 1.3; therefore, a new name is being used: passtBinding

```go
type InterfaceBindingMethod struct {
	...
	PasstBinding InterfacePasstBinding `json:"passtBinding,omitempty"`
}

type InterfacePasstBinding struct{}
```
> [!NOTE]
> DeprecatedPasstBinding is still a member of the struct. It represents a previous generation of passt which has been deprecated in release 1.3.

A new feature gate `PasstBinding` will be introduced, in Beta phase, to condition the use of this binding and the seamless migration functionality.
The `passtIPStackMigration` feature gate that was introduced in the Alpha phase will be discontinued.

### Validation Webhook
`passtBinding` network binding will only be allowed for interfaces bound to `Pod` network or `multus` default network.
In addition, the admitter will also validate enablement of the `PasstBinding` feature gate. 
A validation will be implemented in the network binding admitter.

### virt-controller
250Mi of RAM will be added to the compute container of virt-launcher pods if VMI has passtBinding, as those are the overheads that passt requires (if all ports are forwarded).

### virt-handler
The passt CNI currently invokes 2 sysctl calls:
- Allow ping group range for user 107
- Allow listening on privileged ports starting from 0.

Both of those calls will be implemented in virt-handler for passtBinding related pods. The nmState spec properties already exist in the [code](https://github.com/kubevirt/kubevirt/blob/release-1.6/pkg/network/setup/netpod/netpod.go#L319)
for `DeprecatedPasstBinding`; they just need to be enabled for passtBinding.

### virt-launcher
The network generator will be enhanced to populate the DomainXML with the passt network interface. The code will be similar to the code that currently configures 
the domain in the sidecar container.
For example: this is the interface representation when no specific ports to forward are specified. 
```xml
    <interface type="vhostuser">
      <source dev="eth0"/>
      <model type="virtio-non-transitional"/>
      <alias name="ua-passtnet"/>
      <backend type="passt" logFile="/var/run/kubevirt/passt.log"/>
      <portForward proto="tcp"/>
      <portForward proto="udp"/>
    </interface>
```
As in the current sidecar code, when Istio is enabled, its reserved ports will be marked for exclusion. 

### passt-repair call modifications
Currently, passt-repair is called during migration only if a network binding plugin is defined in the VMI spec, and the `passtIPStackMigration` feature gate is enabled.
This condition will be modified such that passt-repair would only be called if the VMI has a `passtBinding` (core) interface binding and the `passtBinding` feature gate is enabled.

## Update/Rollback Compatibility

The `passt` network binding plugin will be deprecated once the feature is GA.
Once deprecated, it'll still be maintained for another 3 releases, per KubeVirt's deprecation policy.
This means that the plugin code will continue to work at least until release 1.11 (assuming GA at 1.9).
Existing users may retain existing passt plugin based VMs until then.
Seamless migration functionality will be discontinued for `passt` binding plugin users, following the introduction of the `passt` core network binding.
Users of the `passt` binding plugin will be encouraged to move to the core passt binding in order to enjoy the benefits of the seamless migration feature.

> [!NOTE]
> An upgrade path from the passt binding plugin to the proposed core binding without VM restart will not be supported

## Functional Testing Approach

The current e2e `passt` tests will be duplicated so that, in addition to the existing set that runs with the `passt` plugin, a second variant will run VMs configured with the core `passt` binding. 
The existing set will be labeled to control execution as follows:
- For the first release (1.8), both sets will run as part of presubmits.
- In subsequent releases the plugin tests will run in the SIG Network periodic job to monitor regressions.
