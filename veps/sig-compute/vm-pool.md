# VEP #69: Core lifecycle features for VirtualMachinePool

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [] (R) Target version is explicitly mentioned and approved

## Overview

This design provides an approach for creating a VM grouping and replication abstraction for KubeVirt called a VirtualMachinePool.

## Motivation

The ability to manage a group (or pool) of similar VMs using a higher level abstraction is a staple among commonly utilized Iaas operational patterns. By bringing stateful VM group management to KubeVirt, we open the door for operation teams to utilize their existing patterns for managing KubeVirt VMs. This feature further aligns KubeVirt as an Iaas offering comparable to the public clouds which makes KubeVirt a more attractive option for Iaas management on baremetal hardware.

## Goals

* VM pool abstraction capable of managing replication of stateful VMs at scale
* Automated rollout of spec changes and other updates across a pool of stateful VMs
* Automated and manual scale-out and scale-in of VMs associated with a pool
* Autohealing (delete and replace) of VMs in a pool not passing health checks
* Ability to specify unique secrets and configMap data per VM within a VMPool
* Ability to detach VMs from VMPool for debug and analysis

## Non Goals

* Not designing a VM fleet abstraction capable of managing multiple VM groupings containing VMs which are dissimilar to one another. A VMPool consists only of VMs which are similar in shape to one another that are derived from a single config.

## Definition of Users

* A user is a person who want to create a group of VMs and want to manage them.

## User Stories

* As a cluster user, I want to automate batch rollout of changes (CPU/Memory/Disk/PubSSHKeys/etc…) across a pool of VM replicas.
* As a cluster user managing a pool of VM replicas I want to automate scale out of VM instances based on utilization
* As a cluster user managing a pool of VM replicas I want to automate scale-in of VM instances to optimize cluster resource consumption
* As a user transitioning workloads to KubeVirt I want to use similar management patterns provided by existing Iaas platforms (AWS, Azure, GCP)
* As a cluster admin managing nested Kubernetes clusters on top of KubeVirt VMs, I want the ability to elastically scale the underlying KubeVirt VM infrastructure.
* As a SRE managing the availability VM replicas in a pool, I want to automate VM recovery by auto detecting and deleting misbehaving VMs and having the platform spin up fresh new instances as a replacement.
* As a pool user/manager I want to remove a VM from the pool without modifying it for debugging. The missing VM can be replaced by the pool.


## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

The VMPool design introduces a new API represented as a CRD called the VirtualMachinePool (VMPool) object. This object contains tunings related to managing a set of replicated stateful VMs as well as a template that defines the configuration applied creating the VM replicas. Conceptually, The VMPool's templating mechanism is very similar to how Kubernetes Deployments operate.

## VirtualMachinePool (VMPool) API

The VMPool API represents all the tunings necessary for managing a pool of stateful VMs. The VMPools spec contains the following tunings and values

* **Template** - (Required) A VirtualMachine spec used as a template when creating each VM in the pool.
* **Replicas** - (Required) An integer representing the desired number of VM replicas
* **MaxUnavailable**  - (Optional) (Defaults to 25%) Integer or string pointer, that when set represents either a percentage or number of VMs in a pool that can be unavailable (ready condition false) at a time during automated update.
* **NameGeneration** - (Optional) Specifies how objects within a pool have their names generated
	* **AppendPostfixToSecretReferences** - (default false) Boolean that indicates if VM’s unique postfix should be appended to references to Secrets in the VMI’s Volumes list. This is useful when needing to pre-generate unique secrets for VMs within a pool.
	* **AppendPostfixToConfigMapReferences** - (default false) Boolean that indicates if VM’s unique postfix should be appended to ConfigMap references in the VMI’s Volumes list. This is useful when needing to pre-generate unique secrets for VMs within a pool.

* **UpdateStrategy** - (Optional) Specifies how the VMPool controller manages updating VMs within a VMPool
	* **Unmanaged** - No automation during updates. The VM is never touched after creation. Users manually update individual VMs in a pool.
	* **Opportunistic** - Opportunistic update of VMs which are in a halted state.
	* **Proactive** - (Default) Proactive update by forcing VMs to restart during update.
		* **SelectionPolicy** - (Optional) (Defaults to "random" sort policy when no SelectionPolicy is configured) The priority in which VM instances are selected for proactive scale-in
			* **Selectors** - (Optional) Selectors is a list of selection policies including [LabelSelectors] and [NodeSelectors]
			* **SortPolicy** - (Optional) Catch all polices [Oldest|Newest|Random|Ascending|Descending]
* **ScaleInStrategy** - (Optional) Specifies how the VMPool controller manages scaling in VMs within a VMPool
	* **Unmanaged** - No automation during scale-in. The VM is never touched after creation. Users manually delete individual VMs in a pool. Persistent state preservation is up to the user removing the VMs
	* **Opportunistic** - Opportunistic scale-in of VMs which are in a halted state.
		* **StatePreservation** - (Optional) specifies if and how to preserve state of VMs selected for scale-in.
			* **Disabled** - (Default) all state for VMs selected for scale-in will be deleted
			* **Offline** - PVCs for VMs selected for scale-in will be preserved and reused on scale-out (decreases provisioning time during scale out)
			* **Online** - PVCs and memory for VMs selected for scale-in will be preserved and reused on scale-out (decreases provisioning and boot time during scale out) Each VM’s PVCs are preserved for future scale out
	* **Proactive** - (Default) Proactive scale-in by forcing VMs to shutdown during scale-in.
		* **SelectionPolicy** - (Optional) (Defaults to "random" sort policy when no SelectionPolicy is configured) The priority in which VM instances are selected for proactive scale-in
			* **Selectors** - (Optional) Selectors is a list of selection policies including [LabelSelectors] and [NodeSelectors]
			* **SortPolicy** - (Optional) Catch all polices [Oldest|Newest|Random|Ascending|Descending]
		* **StatePreservation** - (Optional) specifies if and how to preserve state of VMs selected for scale-in.
			* **Disabled** - (Default) all state for VMs selected for scale-in will be deleted
			* **Offline** - PVCs for VMs selected for scale-in will be preserved and reused on scale-out (decreases provisioning time during scale out)
			* **Online** - PVCs and memory for VMs selected for scale-in will be preserved and reused on scale-out (decreases provisioning and boot time during scale out)
Each VM’s PVCs are preserved for future scale out
* **Autohealing** - (Optional)  (Defaults to disabled with nil pointer) Pointer to struct which specifies when a VMPool should should completely replace a failing VM with a reprovisioned instance. 
	* **StartupFailureThreshold** - (Optional) (Defaults to 3) An integer representing how many consecutive failures to reach a running state (which includes failing to pass liveness probes at startup when liveness probes are enabled) should result in reprovisioning.
  * **MinFailingToStartDuration** - (Optional) (Defaults to 5 mins) It is the minimum time a VM must be in a failing status (applies to status conditions like CrashLoopBackOff, Unschedulable) before being replaced. It measures the duration since the VM's Ready condition transitioned to False.


## API Examples

**Automatic rolling updates and scale-in strategy with state preservation to optimization of boot times during scale-out**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachinePool
metadata:
  name: my-vm-pool
spec:
  replicas: 100
  maxUnavailable: 10
  scaleInStrategy:
    proactive:
      statePreservation: Offline
      selectionPolicy:
        sortPolicy: "Oldest"
  updateStrategy:
    proactive:
      selectionPolicy:
        sortPolicy: "Oldest"
  template:
    spec:
      dataVolumeTemplates:
      - metadata:
          name: alpine-dv
        spec:
          pvc:
            accessModes:
            - ReadWriteOnce
            resources:
              requests:
                storage: 2Gi
          source:
            http:
              url: http://cdi-http-import-server.kubevirt/images/alpine.iso
      running: false
      template:
        spec:
          domain:
            devices:
              disks:
              - disk:
                  bus: virtio
                name: datavolumedisk
          terminationGracePeriodSeconds: 0
          volumes:
          - dataVolume:
              name: alpine-dv
            name: datavolumedisk
```

**Manual rolling updates and Manual scale-in strategy**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachinePool
metadata:
  name: my-vm-pool
spec:
  replica: 100
  scaleInStrategy:
    unmanaged: {}
  updateStrategy:
    unmanaged: {}
  template:
    spec:
      dataVolumeTemplates:
      - metadata:
          name: alpine-dv
        spec:
          pvc:
            accessModes:
            - ReadWriteOnce
            resources:
              requests:
                storage: 2Gi
          source:
            http:
              url: http://cdi-http-import-server.kubevirt/images/alpine.iso
      running: false
      template:
        spec:
          domain:
            devices:
              disks:
              - disk:
                  bus: virtio
                name: datavolumedisk
          terminationGracePeriodSeconds: 0
          volumes:
          - dataVolume:
              name: alpine-dv
            name: datavolumedisk
```

**Automatic rolling updates and scale-in strategy with VM Selectors selection policy on scale-in**

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachinePool
metadata:
  name: my-vm-pool
spec:
  replicas: 100
  maxUnavailable: 10
  scaleInStrategy:
    proactive:
      selectionPolicy:
        selectors:
          - labelSelector
            - non-important-vms
        sortPolicy: "Oldest"
      statePreservation: Offline
  updateStrategy:
    proactive:
      selectionPolicy:
        sortPolicy: "Oldest"
  template:
    spec:
      dataVolumeTemplates:
      - metadata:
          name: alpine-dv
        spec:
          pvc:
            accessModes:
            - ReadWriteOnce
            resources:
              requests:
                storage: 2Gi
          source:
            http:
              url: http://cdi-http-import-server.kubevirt/images/alpine.iso
      running: false
      template:
        spec:
          domain:
            devices:
              disks:
              - disk:
                  bus: virtio
                name: datavolumedisk
          terminationGracePeriodSeconds: 0
          volumes:
          - dataVolume:
              name: alpine-dv
            name: datavolumedisk
```

## Manually Detaching VM from VMPool

A VM in a VMPool can be detached from a VMPool by removing the owner reference. This removes that VM from being actively managed by the VMPool.

Since VMs within a VMPool each have a unique sequential postfix applied to each VM name, a detached VM’s sequence number will be skipped during scale-in and scale-out operations until the detached VM is either returned to the Pool (by manualing adding the VMPool owner reference back) or the VM is deleted which frees the resource name.

## Scalability

VirtualMachinePool should efficiently handle large numbers of VMs by supporting batch operations, optimized watch/list events, and scalable PVC/DataVolume management for large pools.

## Update/Rollback Compatibility

VirtualMachinePool is an additive feature, it does not impact existing VMs.

## Functional Testing Approach

The VirtualMachinePool feature will be tested through:

* Unit Tests: Comprehensive unit test coverage for all new code
* End-to-End Tests: E2E test suite covering:
  * VirtualMachinePool creation with persistent storage
  * Rolling updates respecting maxUnavailable and update strategies
  * Scale-in and scale-out operations with different scaleInStrategies
  * Auto-healing functionality for failing VMs 

## Implementation Phases

* API Defintion
* VMPool controller implementation and reconcilation logic
* Unit tests
* E2E tests

## Feature lifecycle Phases

### Alpha
* Basic functionality implementation with unit tests and E2E tests

### Beta
* Implement core lifecycle features of VMpool:
  * UpdateStrategy
  * ScaleInStrategy  
  * AutoHealing
* Comprehensive unit and E2E test coverage

### GA
* Online statePreservation strategy during scale-in operations and memory state reuse during bootup
* Metrics and observability support

## References

* [Design Proposal](https://github.com/kubevirt/community/blob/main/design-proposals/vm-pool.md)
