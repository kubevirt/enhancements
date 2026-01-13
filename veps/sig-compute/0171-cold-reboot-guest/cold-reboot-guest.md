# VEP #0171: Guest initiated cold reboot support

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

An alternative is representing the behavior using a new runStrategy: BootOnce and a reserved annotation to pass the information to VMI and the domain.

Lets call this new behavior cold reboot from now on.

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

The real user story:

As an cluster administrator I want to install Openshift using the IPI method, using platform=baremetal, where some of my nodes are virtualized. I will utilize project like kubevirt-redfish or fakefish to bridge the gap between kubevirt and the installer redfish capabilities.

The installer in platform=baremetal mode can use redfish to inject the generated install media, but it cannot reconfigure and reboot the VM at exactly the proper moment. The VM reboots itself and sadly boots back into CD. That breaks the install process.

## Goals

<!--
The desired outcome
-->

An API toggle to select the current warm reboot (VM configuration changes not applies) or the new cold reboot behavior (with VM configuration changes applied).

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
- cluster administrators that perform manual deployments of complicated software stacks or change boot sources often for testing
- VM owner is the user with the power to change the VM definition

## User Stories

<!--
List of user stories this design aims to solve
-->

The kubevirt limited scope user stories:

- As a VM owner I want to be able to change the boot source of a VM and let the VM pick up this new boot source (or other non-hotplug capable configuration) upon the next reboot. The source of the reboot (external via virctl or internal from guest or using the guest agent) should not matter.

Removing the context even more to represent just the behavior:

- As a VM owner, I want a VM with runStrategy: Always that recreates the VMI on each guest reboot to pick up any configuration changes made to the VM.
- As a VM owner, I want a VM with runStrategy: Once that exits once the guest shuts off via shutdown or the first reboot.

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
    rebootPolicy: Reboot (default) | Terminate
```

That would configure the libvirt domain XML element:

https://libvirt.org/formatdomain.html#events-configuration

```
<on_reboot>destroy | restart</on_reboot>
```

### A note about changing a running VM

When the boot order is changed while the VM is still running, it will add an annotation of "restart-required". If the VMI is terminated and created again, the annotation should go away. This should be confirmed during testing.

A hotplug behavior for certain fields might trigger a live-migration automatically, unrelated to what happens in the guest. Confirm the event configuration and boot order changes do not do this during testing. In other words - boot order and rebootStrategy fields are not designed as hot-pluggable atm.

## API Examples

<!--
Tangible API examples used for discussion
-->

Please see the Design section. There is no extra API requested.

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

### New runStrategy alternative

A new runStrategy: BootOnce could internally set the necessary libvirt on_reboot=destroy too. It would not allow combination of cold reboots with runStrategy: Always, but it would still allow the orchestrator to implement the required flows instead.

This allows better encapsulation, but makes the implementation slightly more complex.


```
kind: VirtualMachine
spec:
  runStrategy: BootOnce
```

Since runStrategy is a VM only field there is a need to introduce a mechanism to pass the value to the domain controllers via the VMI. This can either be the same field proposed above, or it could be a reserved annotation to not allow users to play with it freely:

```
kind: VirtualMachineInstance
metadata:
  annotations:
    runstrategy.kubevirt.io: BootOnce
```

### Fixing runStrategy: Once

This is just a variant of the previous new runStrategy that would instead reuse the runStrategy: Once and change its behavior to only boot once before completing (no internal reboots). I am not a big fan as it could potentially break the behavior for someone relying on the run until completion (no matter how many reboots) semantics.

### "Hotplug" of boot order change

It is possible to change the event configuration of a domain while the domain is running. An alternative solution might utilize that by emulating boot source hotplug behavior. This would propagate a change from VM, to VMI and then set on_reboot=destroy in the running domain to make sure the boot order change is picked up.

This makes the API less declarative, because it introduces timing constraints to the VM edits. The boot order change will only update the event configuration when a domain is up and running.

This prevents a flow like:

- creating the VM with boot from CD configuration
- starting it
- waiting until it quits
- waiting for some external condition (something else ready)
- changing the boot order (the VM is not running, no event can be configured)
- starting the VM again

I consider this alternative less reliable and prone to introducing race conditions into the behavior. The user would have to wait for the VMI to be scheduled and started and be quick enough to make the change before the VMI terminates.

### Other alternatives

I have tried implementing the logic in the higher level orchestrators, however there is not much I could do without the ability to intercept a guest reboot. And the guest reboot event is currently hidden and abstracted away by qemu not quiting. The VMI never notices.

A close enough behavior could be achieved by ejecting the CD source prior to the reboot. However that requires support in the orchestrator with the proper timing and an OS that is OK with that. Unfortunately, the OCP IPI installer does not do this.

Using the side-car container might allow the necessary libvirt domain XML modification, but setting that up is more complex and resource heavy than a simple toggle.

A global toggle on the cluster level (Kubevirt CR) to switch the behavior for all VMs is also an option, although not a preferred one.

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

- Create a VM (even cloud image suffices) with rebootStrategy = Terminate
- Launch the VM by setting the runStrategy to Once
- Reboot the guest from inside
- Observe the guest gets destroyed and NOT recreated

### Testing the ability to pick up new settings

- Create a VM (even cloud image suffices) with rebootStrategy = Terminate
- Launch the VM by setting the runStrategy to Always
- Modify the VM (boot order, or something else non-hotpluggable)
- Make sure the hotplug live-migration was NOT triggered
- Reboot the guest from inside
- Wait and observe the guest got destroyed and recreated with the new settings
- Make sure the needs reboot annotation is not present anymore

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
- Implementation behind a feature gate RecreateVMIOnReboot
- API proposal implemented
- E2E tests

### Beta
- Implementation behind a feature gate RecreateVMIOnReboot
- final API agreed on and stabilized
- API, use cases and best practices documented

### GA
- API available without a FeatureGate
