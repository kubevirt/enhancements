# VEP #21 - Part 2: Passt Binding Core Migration

## VEP Status Metadata

### Target releases

- ~This VEP targets alpha for version:~
- This VEP targets beta for version: v1.8
- This VEP targets GA for version: v1.10

## Overview
Following [the initial proposal](./vep.md), this part extends the `passt` KubeVirt integration to the Beta phase.
The `passt` network binding is currently implemented as a [network binding plugin](https://kubevirt.io/user-guide/network/network_binding_plugins) and is in Alpha phase.
This change migrates the passt binding into the KubeVirt core and exposes it in the API, it will also promote the feature phase from Alpha to Beta and will be conditioned by a new feature gate.
The aim is to target KubeVirt release 1.8; however, that depends on having sufficient feedback for the Alpha phase.
The core passt binding will enable the seamless migration functionality that was introduced in the Alpha phase.

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

A new feature gate `PasstBinding` will be introduced, to condition the use of this binding and the seamless migration functionality.
The `passtIPStackMigration` feature gate will be discontinued.

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

### persistent guest IP addresses - all IP families
Currently, IPv4 guest addresses persist across migration by setting an indefinite TTL for DHCP, however this does not work for ipv6 due to differences in protocol behavior.
Guest IP addresses must remain consistent, even if pod IPs change during migration.
In order to support persistent guest IPs across migration for all IP families, KubeVirt will configure passt with the IPs discovered from the pod during initial pod network setup.
Passing IP address arguments to passt, disables its auto-discovery capability, which after migration will cause passt not to discover the target pod's address.
Instead, it will use the IP addresses that exist in the DomainXML configured in the first source pod.

## Update/Rollback Compatibility
### passt Network Binding Plugin
The `passt` network binding plugin will be deprecated once the feature is GA.
Once deprecated, it'll still be maintained for another 3 releases, per KubeVirt's deprecation policy.
This means that the plugin code will continue to work through release 1.12, and although no longer supported and released, will continue to be functional.
The seamless migration functionality is discontinued for `passt` binding plugin users, following the introduction of the `passt` core network binding.
As such, the `passtIPStackMigration` feature gate that was introduced for the binding plugin has been discontinued as well.
Users of the `passt` binding plugin are encouraged to move to the core passt binding in order to enjoy the benefits of the seamless migration feature.

> [!NOTE]
> An upgrade path from the passt binding plugin to the proposed core binding without VM restart is not supported

## Functional Testing Approach

E2e `passt` tests are now identical to the legacy binding plugin tests. Tests that have been quarantined/removed due to bugs in the production code will be restored, 
in order to validate all expected networking and migration functionality.
> [!NOTE]
> Seamless TCP migration cannot be tested as part of the e2e testing suite, due to limitation of the current CNI/IPAM infrastructure used by kubevirtci.
> This functionality is tested on a regular basis using external infrastructure.

## Graduation Requirements

### GA
- Remaining issues to be fixed
  - Guest IP addresses [must persist](https://github.com/kubevirt/kubevirt/issues/17841) across migration in both IPv4 and IPv6
