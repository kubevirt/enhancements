# VEP #31: Declarative Volume Hotplug

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

Proposal to support live updates of Virtual Machine volumes by directly editing the VM specification.

## Motivation

Currently, the only way to apply live updates to Virtual Machine volumes is via the VM subresource API. Updates to the disk/volume sections of a Virtual Machine specification will not be applied until the VM is restarted. This is undesireable for the following reasons:

- Restarts are disruptive
- The subresource API is not compatible with a [GitOps](https://kubevirt.io/user-guide/operations/gitops/) workflow
- The subresource API is difficult for a user to invoke directly, so tools like `virtctl` are required

## Goals

- Address shortcomings of the Virtual Machine subresource API by allowing for Virtual Machine volumes to be updated in a declarative way and have those changes applied immediately
- Support empty CD-ROM disks (no corresponding volume)
- Inject/eject of CD-ROM volumes
- A soft goal is to refactor the subresource API implementation to simply call the new declarative API and deprecate `vm.status.volumeRequests`

## Non Goals

- This feature relies on existing [volume hotplug machinery](https://kubevirt.io/user-guide/storage/hotplug_volumes/), so live updates are limited to DataVolume and PersistentVolumeClaim [volume types](https://kubevirt.io/user-guide/storage/disks_and_volumes/#volumes). Support for other volume types may be added in the future

## Definition of Users

- End Users: these are people/programs that have permission to update Virtual Machine specifications

## User Stories

- As a KubeVirt user, I want to be able to use `kubectl edit` to add/remove/change the volumes of a Virtual Machine and have those changes take effect immedietly without restarting the VM
- As a KubeVirt user, I want to be able to add/remove/change the volumes of a Virtual Machine by updating the VM definition, pushing it to a git repository, and when a system like [Open Cluster Management](https://open-cluster-management.io/) or [ArgoCD](https://argoproj.github.io/cd/) applies those changes, they will take effect immedietly without restarting the VM
- As a KubeVirt user, I want to be able define a CD-ROM disk on a Virtual Machine and simulate injecting/ejecting a CD-ROM disk by adding/removing volumes from the VM specification. Those changes should take effect immedietly without starting the Virtual Machine

## Repos

kubevirt/kubevirt

## Design

### Enabling Declarative Volume Hotplug

A couple different options were considered.

#### Option 1 - New default (most of the time)

In this case, the feature will be enabled by default except when VM Rollout strategy is `LiveUpdate` AND Volume Update Strategy is `Migration`

There are currently two Volume Update Strategies, `Replacement` and `Migration`. `Replacement` "stages" declarative volume changes to be applied when the Virtual Machine restarts. `Migrtion` kicks off a live migration which will copy a volume's data to a new PVC.

With this option, the `Replacement` strategy will live update changes to any volume with `hotpluggable: true`

Pros:
- Users can opt into this feature without regard to VM Rollout Strategy which is a cluster-scope setting
- Soft goal of combining the declarative and subresource APIs can be achieved

Cons:
- Not backward compatible but staging the use of a hotpluggable disk is an unlikely use case

#### Option 2 - LiveUpdate VM Rollout Strategy + Hotplug Volume Update Strategy

This option leverages [VM Rollout Strategy](https://github.com/kubevirt/community/blob/main/design-proposals/vm-rollout-strategy/vm-rollout-strategy.md) and [Volume Update Strategy](https://github.com/kubevirt/community/blob/main/design-proposals/volume-update-strategy.md)

- A new Volume Update Strategy called `HotPlug` will be created
- Declarative Volume update will be enabled when the VM Rolout Strategy is `LiveUpdate` and Volume Update Strategy is `Hotplug`
- If either of those values are not set, the volume update will not be applied until the Virtual Machine is restarted

Pros:
- Backward compatible
- Users can opt in

Cons:
- Cannot achieve soft goal of having the subresource API leverage the declarative API

This option is rejected because it is overly complicated and does not allow us to deprecate `vm.status.volumeRequests`

## API Examples

### Add a new Volume

#### Before:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
...
```

#### After
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - disk:
            bus: scsi
          name: plugged-volume
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
      - dataVolume:
          name: hotplugged-disk
          hotpluggable: true
        name: plugged-volume
```

### Remove a Volume

#### Before
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  updateVolumesStrategy: Replacement # not required
...
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - disk:
            bus: scsi
          name: plugged-volume
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
      - dataVolume:
          name: hotplug-disk
          hotpluggable: true
        name: plugged-volume
...
```

#### After
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  updateVolumesStrategy: Replacement # not required
...
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
...
```

### Inject a CD-ROM

#### Before
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - cdrom:
            bus: sata
          name: cdrom
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
...
```

#### After
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - cdrom:
            bus: sata
          name: cdrom
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
      - dataVolume:
          name: hotplug-cdrom
          hotpluggable: true
        name: cdrom
...
```

### Eject A CD-ROM

#### Before
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  updateVolumesStrategy: Replacement # not required
...
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - cdrom:
            bus: sata
          name: cdrom
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
      - dataVolume:
          name: hotplug-cdrom
          hotpluggable: true
        name: cdrom
...
```

#### After
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: vm1
spec:
  updateVolumesStrategy: Replacement # not required
...
  template:
    spec:
      devices:
        disks:
        - disk:
            bus: virtio
          name: root
        - cdrom:
            bus: sata
          name: cdrom
...
      volumes:
      - dataVolume:
          name: root-disk
        name: root
...
```

## Scalability

This feature depends on existing [volume hotplug machinery](https://kubevirt.io/user-guide/storage/hotplug_volumes/) which will ultimately be the bottleneck with regard to scale. Adding/removing hotplug volumes requires creating/deleting and maintaining long-running Pods which can limit system scalability.

## Update/Rollback Compatibility

On upgrade, any `hotpluggable: true` volumes that were staged for update will get hotplugged. This is not expected to be a common configuration though.

## Functional Testing Approach

A comprenhensive test suite that checks the guest state will be important for this feature. The following cases should be covered:

- Add/Remove multiple disk
- Inject/eject CD-ROM
- Add and remove disk in quick succession

## Implementation Phases

## Feature lifecycle Phases

### Alpha

There will be two relevent featuregates:

1. `HotplugVolumes` - This existing featuregate will enable declarative adding/removing volumes and their corresponding disks in pairs
2. `InjectEjectCDROM` - This new feature gate will allow for VM definitions that contain a CD-ROM and no volume

### Beta

Perhaps after one or two releases, when we are confident that the feature is working as expected, move to beta.

### GA

GA once the feature has been running in production without issue. Remove featuregates.
