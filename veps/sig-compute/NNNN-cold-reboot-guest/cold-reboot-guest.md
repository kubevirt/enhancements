# VEP #NNNN: Guest initiated cold reboot support

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

Kubevirt currenty does not detect when guest reboots internally. The qemu process abstracts that away, the VMI is not terminated and new VM settings are not applied post-reboot. Lets call this warm reboot.

This enhancement adds a toggle to the VMI API that instructs qemu to not reboot silently. Instead it will terminate and the reboot will be handled by the kubevirt controllers according to the selected runStrategy. As a (wanted) side effect, the VMI will be recreated using the up-to-date settings from the VM.

Lets calls this new behavior cold reboot from now on.

## Motivation

<!--
Why this enhancement is important
-->

While warm reboot is faster and simpler for most of the typical runtime use cases, there is a special occasion where this optimization is not desirable:

VM installation from media

There are products and systems that do not use cloud native images to run VM. Mostly because they use Virtual Machines not as appliances, but to replace baremetal hardware. Such products coming from the baremetal world typically have an installation media that is used to boot the machine, copy the necessary components to disk and then reboot to the deployed system. Most of them also perform some post-reboot configuration and in my specific case, call to the distributed system controller to announce the node is ready.

This is problematic to automate on Kubevirt, because the warm reboot behavior does not pick up the changes to the boot order done by the orchestrator (first to set CD as the boot source and then an immediate revert to disk boot).

You could argue that there is a special case where the VM can fallback to CD when the disk is empty. That is correct and it works just fine with two caveats. It only covers new machines with empty disks (no redeployment or upgrade from media) and it does not expose enough API for BMC based orchestrators that control the VMs via ipmi or redfish (many suitable projects provide such API over kubevirt).

The same might apply with regards to PXE or other boot sources.

A side note about runStrategy: Once: At this moment it can only guarantee the guest will run until shutdown, the internal reboots do not count. However, what if the user wants to have the VM running only till the next guest reboot? The cold reboot feature would allow that.

To be more specific:

My real use case is installing an OpenShift cluster with combined bare metal workers and virtualized control plane. The installer in platform=baremetal mode can use redfish to inject the generated install media, but it cannot reconfigure and reboot the VM at exactly the proper moment. The VM reboots itself and sadly boots back into CD. That breaks the install process.

## Goals

<!--
The desired outcome
-->

A toggle in VMI instance that would configure libvirt and qemu to select the current warm reboot or the new cold reboot behavior.

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

No additional logic is requested, the toggle that already exists in libvirt and qemu will be exposed to the user or higher level orchestrator. The rest of the kubevirt behavior aligns nicely with it.

Eventually, kubevirt might be able to utilize this functionality to bring a fully featured RunOnce configuration, but it is not necessary at this time.


## Definition of Users

<!--
Who is this feature set intended for
-->

- developers of higher level orchestration and management systems that build on top of kubevirt
- power users that perform manual deployments of complicated software stacks or change boot sources often for testing

## User Stories

<!--
List of user stories this design aims to solve
-->

The real user story:

- As an cluster administrator I want to install Openshift using the IPI method, using platform=baremetal, where some of my nodes are virtualized. I will utilize project like kubevirt-redfish or fakefish to bridge the gap between kubevirt and the installer redfish capabilities.

The kubevirt limited scope user story:

- As a power user I want to be able to change the boot source of a VM and let the VM pick up this new boot source (or other non-hotplug capable configuration) upon the next reboot. The source of the reboot (external via virctl or internal from guest or using the guest agent) should not matter.

## Repos

<!--
List of repose this design impacts
-->

github.com/kubevirt/kubevirt

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

Just a toggle in the VMI object:

```
kind: VirtualMachineInstance
spec:
  domain:
    rebootPolicy: restart (default) | recreate
```

That would configure the libvirt domain XML element:

https://libvirt.org/formatdomain.html#events-configuration

```
<on_reboot>destroy | restart</on_reboot>
```

## API Examples

<!--
Tangible API examples used for discussion
-->

Please see the Design section. There is no extra API requested.

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

I have tried implementing the logic in the higher level orchestrators, however there is not much I could do without the ability to intercept a guest reboot. And the guest reboot event is currently hidden and abstracted away by qemu not quiting. The VMI never notices.

A close enough behavior could be achieved by ejecting the CD source prior to the reboot. However that requires support in the orchestrator with the proper timing and an OS that is OK with that. Unfortunately, the OCP IPI installer does not do this.

## Scalability

<!--
Overview of how the design scales)
-->

Cold reboot has slightly higher resource costs on guest reboot. But the expectation is it will be used only for specific cases. There is no impact outside of this.

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

This does not impact upgrade. Rollback of VMs that use this functionality will revert to the old behavior.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

Two test cases come to mind:

### Testing runStrategy Once behavior

- Create a VM (even cloud image suffices) with lifecycle.on_reboot = recreate
- Launch the VM by setting the runStrategy to Once
- Reboot the guest from inside
- Observe the VMI gets destroyed and NOT recreated

### Testing the ability to pick up new settings

- Create a VM (even cloud image suffices) with lifecycle.on_reboot = recreate
- Launch the VM by setting the runStrategy to Always
- Modify the VM (boot order, memory, or something else)
- Reboot the guest from inside
- Wait and observe the VMI got destroyed and recreated with the new settings

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
- [ ] Implementation behind a feature gate

### Beta


### GA
- API available