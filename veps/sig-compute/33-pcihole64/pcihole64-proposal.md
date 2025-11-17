# VEP #33: Allow to disable the 64-bit PCI hole

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone /
release*.

- [x] (R) Enhancement issue created, which links to VEP dir
  in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

This proposal focuses on allowing to disable the 64-bit PCI hole in
`VirtualMachineInstances` (VM), so that legacy operating systems (OS) like
Windows XP or Server 2k3 can run on KubeVirt.

An explanation of the 64-bit PCI hole can be found
[here](https://en.wikipedia.org/wiki/PCI_hole).

## Motivation

At the moment legacy OSes like Windows XP or Server 2k3 do not work out of the
box on KubeVirt. This prevents big organizations running many VMs of all kinds
from adopting KubeVirt. To allow greater adoption of KubeVirt, this proposal
aims to make these legacy OSes work on KubeVirt.

For more information on the topic
see [this thread on kubevirt-dev](https://groups.google.com/g/kubevirt-dev/c/1Ue4brPoc1g)
and [this issue](https://bugzilla.redhat.com/show_bug.cgi?id=990418)

## Goals

- A way of expressing that the 64-Bit PCI hole should be disabled is added to
  the KubeVirt API in the most OS-independent way possible.
- Legacy OSes like Windows XP or Server 2k3 can run on KubeVirt in a
  supportable way.

## Non Goals

- Committing to a list of supported OSes in KubeVirt.
- Adding OS specific configurables to the KubeVirt API.

## Definition of Users

- VM owners
- Namespace owners
- Cluster admins

## User Stories

- As a VM owner, I want to run legacy OSes like Windows XP or Server 2k3 on
  KubeVirt.
- As a VM owner, I want to migrate existing VMs with legacy OSes to KubeVirt
  and to continue running them with the least possible amount of required
  modifications.
- As a namespace owner or cluster admin, I want to provide
  `VirtualMachinePreferences` or `VirtualMachineClusterPreferences` for legacy
  OSes like Windows XP or Server 2k3.

## Repos

- [https://github.com/kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

Users that want to run legacy OSes do not necessarily need to know what `64-Bit
PCI hole` means (in this case setting its size to zero). Instead, they
should be able to express that they want to run a legacy OSes without worrying
about the details. Annotations provide a simple way of enabling configuration
tweaks or compatibility behavior while avoiding modelling specific fields in the
core API and becoming too technical for users.

This proposal suggests to add the `kubevirt.io/disablePCIHole` annotation
to `VirtualMachineInstance` objects, which will trigger KubeVirt to make the
required configuration changes to allow running legacy OSes by disabling the
64-Bit PCI hole. By using an annotation the core API is not changed and there is
room for future changes to this feature independently of the core API. For
example the implementation could be moved from KubeVirt into a
[trusted or certified plugin](#extending-kubevirt-to-allow-using-trusted-or-certified-plugins)
while retaining the same behavior.

### Disabling the 64-Bit PCI hole on the libvirt domain level

libvirt supports the configuration of the 64-Bit PCI hole by adding a
`pcihole64` node below the `pcie-root` controller node in a domain XML.
Setting the value of `pcihole64` to `0` disables the 64-Bit PCI hole.

```xml
<controller type='pci' index='0' model='pcie-root'>
    <pcihole64 unit='KiB'>0</pcihole64>
</controller>
```

When the feature is enabled, `virtwrap/converter` used by `virt-launcher` should
configure the `pcihole64` on the `pcie-root` controller of an `api.Domain`
object. The required configuration can be added as part of the conversion in
`converter.Convert_v1_VirtualMachineInstance_To_api_Domain`.

Since `converter.Convert_v1_VirtualMachineInstance_To_api_Domain` does not yet
create `pcie-root` controllers, it must also be extended to add a basic
`pcie-root` controller to `domain.Spec.Devices.Controllers` before configuring
the `pcihole64` on it. This can be done before or after adding other controllers
such as usb or scsi.

To allow configuring the `pcihole64` on a `pcie-root` controller the feature
requires an addition to `virtwrap/api/schema.go`.

```golang
type Controller struct {
    Type      string            `xml:"type,attr"`
    Index     string            `xml:"index,attr"`
    Model     string            `xml:"model,attr,omitempty"`
    Driver    *ControllerDriver `xml:"driver,omitempty"`
    Alias     *Alias            `xml:"alias,omitempty"`
    Address   *Address          `xml:"address,omitempty"`
    PCIHole64 *PCIHole64        `xml:"pcihole64,omitempty"`
}

type PCIHole64 struct {
    Value uint   `xml:",chardata"`
    Unit  string `xml:"unit,attr,omitempty"`
}
```

## API Examples

By setting the `kubevirt.io/disablePCIHole` annotation on a
`VirtualMachineInstance` object to `"true"` the feature is enabled. Any other
value than `"true"` will leave the feature disabled. By default, the feature
will also be disabled.

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: my-legacy-vm
  annotations:
    kubevirt.io/disablePCIHole: "true"
spec:
  domain:
    devices: {}
    memory:
      guest: 512Mi
  terminationGracePeriodSeconds: 180
  volumes:
    - containerDisk:
        image: my.registry/images/legacy-os
      name: legacy-os
```

## Alternatives

### Adding a specific field to the core API

The feature could be configured by adding a specific field
`spec.domain.devices.disablePCIHole64` of type `boolean` to the core API.

One advantage of this approach would be the improved discoverability of the
feature, as it would become part of the core API and its documentation.

The disadvantage of this approach, however that the feature cannot be changed
independently of the core API. Adding this field to the core API could also be
interpreted as KubeVirt caring about OS specific configuration, which this
proposal tries to avoid.

### Extending KubeVirt to allow using trusted or certified plugins

Another way to implement this feature is to extend KubeVirt with a plugin
mechanism that would allow vendors to ship extensions to KubeVirt in a
supportable manner, while keeping the changes required for this or other
features outside the KubeVirt codebase.

Today KubeVirt already has a feature called `Sidecar`s, which among other things
allows to change the libvirt domain specification of a `VirtualMachineInstance`
before it is executed. A [proof of concept](#sidecar-proof-of-concept) for this
proposal was created with the `Sidecar` feature. However, currently this
feature is not supportable because, as can be seen in the PoC, it basically
allows the execution of arbitrary code in the context of the `virt-launcher`
pod.

In order to make `Sidecar`s supportable, they would at least have to be changed
so that only trusted code can be run as part of a hook. This could be realized
for example by allowing to run only a single container image that contains all
trusted hooks provided by the cluster admin. Before any hooks can be run, the
cluster admin must deploy and configure this image in the cluster.

The advantage of this approach would be that the KubeVirt codebase and API can
be kept stable and clean, while vendors are still able to develop plugins for
KubeVirt in a supportable way and with the least amount of effort required to
maintain these plugins. This approach would allow third parties to experiment
with new APIs before they potentially become part of the KubeVirt core API.

The disadvantage of hooks is that they bypass KubeVirt's management, control
and validation mechanisms, which is very likely to result in unexpected behavior
and compatibility issues. Enabling multiple hook plugins in production at the
same time is likely to lead to unmanageable interactions which ultimately means
that it is difficult for vendors to support these hook plugins.

### Using a `Sidecar` hook

Although it was already dismissed in the previous alternative, in theory it
would be possible to achieve the goal of this proposal by using a `Sidecar`
hook. However, and as stated above, this feature is not supportable because,
it allows the execution of arbitrary code in the context of the `virt-launcher`
pod.

#### `Sidecar` proof of concept

This is a proof of concept that is using a `Sidecar` hook. It achieves the goal
of being able to run legacy OSes on KubeVirt but it is not supportable.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pcihole64
data:
  pcihole64.py: |
    #!/usr/bin/env python3
    """
    This module can be used as an onDefineDomain sidecar hook in KubeVirt to
    ensure compatibility with Windows XP when using the q35 machine type.
    """

    import xml.etree.ElementTree as ET
    import sys


    def main(domain: str):
        """
        This function parses the domain XML passed in the domain argument, adds a
        pcihole64 element with value 0 to every pcie-root controller and then
        prints the modified XML to stdout.
        """

        xml = ET.ElementTree(ET.fromstring(domain))

        controllers = xml.findall("./devices/controller[@model='pcie-root']")
        for controller in controllers:
            element = ET.Element("pcihole64", {"unit": "KiB"})
            element.text = "0"
            controller.insert(0, element)

        ET.indent(xml)
        xml.write(sys.stdout, encoding="unicode")


    if __name__ == "__main__":
        main(sys.argv[4])
```

### Utilizing the `pc-i440fx` machine type

KubeVirt uses the Q35 machine type by default, which triggers the 64-bit PCI
hole incompatibility with legacy OSes.

A solution could be to enable the `pc-i440fx` machine type on the cluster
and to use it instead of Q35 in the definitions of `VirtualMachineInstances`
that boot legacy OSes.

The advantage of this approach is that it requires no code changes to
KubeVirt. All that is required is a configuration change to KubeVirt.

The disadvantage of this approach is that the `pc-i440fx` machine type is
deprecated and could be removed in the future, which means that a solution will
have to be found again. In addition, this solution can only be enabled by
cluster admins. A VM owner alone cannot enable the `pc-i440fx` machine type on a
VM. Another disadvantage is that KubeVirt is tailored to the Q35 machine type,
so that unexpected incompatibilities can occur.

See
[here](https://github.com/RHsyseng/cnv-supplemental-templates/tree/main/templates/pc-i440fx)
for instructions how to re-enable the `pc-i440fx` machine type on a cluster.

## Scalability

The feature should have no impact on scalability as it will only affect how
libvirt and QEMU emulate the hardware in a virtual machine.

## Update/Rollback Compatibility

Update compatibility of existing VMs is not affected, as the 64-Bit PCI hole
will still be activated by default. The feature will be opt-in and be disabled
by default.

VMs with the feature enabled can be rolled back to an earlier version of
KubeVirt, but they will most likely no longer boot. VMs with the feature
disabled will not be affected in case of a rollback.

## Functional Testing Approach

Apart from unit tests that verify a libvirt domain XML with the correct
`pcihole64` configuration on the `pcie-root` controller is created, a functional
test booting a VM with a legacy OS and the 64-Bit PCI hole disabled could be
added. Alternatively, a VM with a regular test boot image could be created in
which commands can be executed to verify that the 64-bit PCI hole is not
present.

## Implementation Phases

This feature can be implemented in a single phase.

## Feature lifecycle Phases

Due to the limited functional scope it should be acceptable to add the feature
in a single lifecycle phase without a feature gate.
