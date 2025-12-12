# VEP: Persistent MAC Addresses for VirtualMachines

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This enhancement introduces automatic MAC address persistence for VirtualMachines by adding a controller in `virt-controller` that persists runtime MAC addresses from VMI status back to the VM template spec. This ensures MAC addresses remain stable across VM lifecycle operations (stop/start, restart, migration) without requiring manual user intervention.

## Motivation

### Current Behavior and Problems

When a VM is created without explicit MAC addresses specified in its spec:
1. VM is created with empty `spec.template.spec.domain.devices.interfaces[].macAddress` fields
2. VMI is instantiated from the VM template
3. CNI/network plugin assigns MAC addresses at pod creation time
4. MAC addresses appear in VMI status but **not** in VM spec
5. On VM restart/stop-start, **new MAC addresses are assigned** (VM spec is still empty)

This creates several operational problems:

**Guest OS Networking Instability**
- Some Guest OS networking configurations are  bound to MAC addresses (DHCP leases, network bonds, udev rules, firewall configurations)
- MAC address changes on restart break these configurations
- Requires manual reconfiguration or guest OS reboot to recognize new MACs
- Poor user experience compared to physical machines and industry-standard virtualization platforms:
  - **VMware vSphere**: MAC addresses [persist by default](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-networking-8-0/mac-addresses.html) across VM power cycles
  - **AWS EC2**: The MAC address of an Elastic Network Interface (ENI) is [stable for the lifetime of that ENI](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html#eni-basics), and the primary ENI normally stays attached to the instance across stop/start.

**Inconsistent with Physical Machine Behavior**
- Physical machines maintain their NICs' MAC addresses across reboots
- VMs should behave similarly for guest OS compatibility

### Real-World Impact

Kubevirt opinionated deployment ([hyperconverged-cluster-operator](https://github.com/kubevirt/hyperconverged-cluster-operator)) currently work around this by:
- relying on external MAC management tools (kubemacpool) which introduce webhook complexity
- allowing manually specifying MAC addresses in VM specs (error-prone)

## Goals

- Automatically persist MAC addresses from VMI status to VM spec without user intervention
- Support all interface types (masquerade, bridge, SR-IOV, custom bindings like l2bridge) that showcase MAC-Address on VMI.status.interfaces
- Work transparently with any CNI plugin (MAC generation is CNI-agnostic)
- Maintain backward compatibility with VMs that already have explicit MACs

## Non Goals

- Removal of Cluster-wide MAC address allocation feature from kubemacpool (should happen in tandem, but is not part of this VEP)
- Cluster-wide MAC address collision detection (handled by external tools like kubemacpool)
- Persistence for pod-based workloads (only VM/VMI)

## Definition of Users

- **VM Operators**: Users who create and manage VMs, expecting stable network identities
- **Cluster Admins**: Administrators who manage VM infrastructure and need predictable MAC addresses
- **Application Developers**: Users running applications in VMs that depend on consistent network configuration

## User Stories

**As a VM operator**, I want my VM's MAC addresses to persist across restarts without manual intervention, so that guest OS networking configuration remains stable and I don't need to reconfigure the network on every restart.

**As a cluster admin**, I want to clone a VM to another namespace and preserve its MAC addresses, so that the guest OS environment remains consistent without manual MAC copying.

**As an application developer**, I want VMs to behave like physical machines where MAC addresses don't change on reboot, so my applications that depend on network identity work correctly.

**As a VM operator**, I want to migrate a VM across namespaces (e.g., in hosted control plane scenarios) while preserving MAC addresses, so the guest OS doesn't detect a hardware change.

## Repos

- kubevirt/kubevirt

## Design

### High-Level Architecture

Add a new reconciliation capability to the existing VM controller in `virt-controller` that watches VMI status updates and persists MAC addresses back to the parent VM spec when they become available.

### Controller Logic

Add a VM controller that will persist runtime MACs:

```go
func (c *VMController) persistRuntimeMACsToVMSpec(vm *virtv1.VirtualMachine, vmi *virtv1.VirtualMachineInstance) error {
    if vmi == nil || len(vmi.Status.Interfaces) == 0 {
        return nil
    }
    
    needsUpdate := false
    var patches []map[string]interface{}
    
    for idx, vmIface := range vm.Spec.Template.Spec.Domain.Devices.Interfaces {
        // Skip interfaces that already have MAC addresses in VM spec
        if vmIface.MacAddress != "" {
            continue
        }
        
        // Find corresponding interface in VMI status
        for _, vmiStatusIface := range vmi.Status.Interfaces {
            if vmiStatusIface.Name == vmIface.Name && vmiStatusIface.MAC != "" {
                patch := map[string]interface{}{
                    "op":    "add",
                    "path":  fmt.Sprintf("/spec/template/spec/domain/devices/interfaces/%d/macAddress", idx),
                    "value": vmiStatusIface.MAC,
                }
                patches = append(patches, patch)
                needsUpdate = true
                
                log.Log.Object(vm).Infof("Persisting runtime MAC %s for interface %s to VM spec", 
                                         vmiStatusIface.MAC, vmIface.Name)
                break
            }
        }
    }
    
    if !needsUpdate {
        return nil
    }

    // Apply JSON patches to VM
    err := patchVM(vm, patches)
    if err != nil {
        return err
    }
    
    return nil
}
```

### Reconciliation Trigger Points

The controller will monitor VMI updates via the existing VMI informer with filtering:

```go
func (c *VMController) setupVMIWatcher() {
    c.vmiInformer.AddEventHandler(cache.ResourceEventHandlerFuncs{
        UpdateFunc: func(oldObj, newObj interface{}) {
            oldVMI := oldObj.(*virtv1.VirtualMachineInstance)
            newVMI := newObj.(*virtv1.VirtualMachineInstance)
            
            // Trigger when VMI transitions to having populated interfaces
            oldReady := c.isVMIReadyForMACPersistence(oldVMI)
            newReady := c.isVMIReadyForMACPersistence(newVMI)
            
            if !oldReady && newReady {
                // Enqueue VM for reconciliation
                if vm := c.getParentVM(newVMI); vm != nil {
                    c.enqueueVM(vm)
                }
            }
        },
    })
}

func (c *VMController) isVMIReadyForMACPersistence(vmi *virtv1.VirtualMachineInstance) bool {
    return vmi != nil &&
           vmi.Status.Phase == virtv1.Running &&
           len(vmi.Status.Interfaces) > 0
}
```

### MAC Address Source

MAC addresses are read from `vmi.Status.Interfaces[].MAC`, which is already reliably populated by:
1. `virt-handler` reading MAC addresses from the libvirt domain specification
2. Domain spec MACs originating from CNI-allocated pod network interfaces
3. Existing status reporting infrastructure

### Idempotency and Safety

- Controller only patches VM spec if `macAddress` field is currently empty
- Once persisted, subsequent reconciliations skip that interface (no-op)
- User-specified MACs are never overwritten
- Safe to call multiple times (defense against controller restarts, race conditions)
- Interface matching by name ensures correct MAC is persisted to correct interface

### Edge Cases Handled

**VMI Recreation:**
- On VM restart, if MACs already persisted, they are reused (no new allocation)
- If MACs not yet persisted, waits for new VMI to reach Running state

**Controller Restarts:**
- Idempotent design allows safe reconciliation after controller restart

## API Examples

VM before reconciliation:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  runStrategy: Always
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: default
              masquerade: {}
            - name: secondary
              bridge: {}
      networks:
        - name: default
          pod: {}
        - name: secondary
          multus:
            networkName: my-network
```

After reconciliation (after VMI is Running):
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  runStrategy: Always
  template:
    spec:
      domain:
        devices:
          interfaces:
            - name: default
              macAddress: "0A:00:00:00:00:01"  # Persisted
              masquerade: {}
            - name: secondary
              macAddress: "0A:00:00:00:00:02"  # Persisted
              bridge: {}
      networks:
        - name: default
          pod: {}
        - name: secondary
          multus:
            networkName: my-network
```

## Scalability

### Performance Impact

- **Controller overhead**: Minimal - only triggered on VMI phase transitions to Running, not on every VMI update
- **API server load**: One additional PATCH per VM on first start (subsequent starts are no-op)
- **Memory impact**: No additional memory required (uses existing informer caches)

### Resource Consumption

- Event filtering in informer handler reduces reconciliation queue load
- Idempotent design prevents unnecessary API calls
- No periodic polling - purely event-driven

## Update/Rollback Compatibility

### Upgrade Behavior

**Existing VMs:**
- VMs with explicit MACs in spec: No change, MACs are preserved
- VMs without MACs (empty spec): On next start, MACs will be persisted
- Running VMs: MACs will be persisted once controller detects them (if not already present)

**New VMs:**
- VMs created without MACs: MACs are automatically persisted on first start
- VMs created with MACs: Explicit MACs are preserved (not overwritten)

### Downgrade/Rollback

- MACs already persisted to VM spec remain in etcd (data persists)
- Downgraded controller won't persist new MACs, but existing VMs are unaffected
- No data loss or corruption on rollback

## Functional Testing Approach

### Unit Tests

- Controller persists MAC when VMI status is populated
- Controller skips interfaces with pre-existing MAC addresses
- Controller handles missing VMI gracefully (no-op)
- Controller handles VMI without status interfaces (no-op)
- Interface name matching works correctly
- JSON patch generation is correct

### E2E Tests

- VM lifecycle: Create → Start → Stop → Start → Verify same MAC
- Live migration: MAC persists across migration
- VM cloning workflow: Clone VM → Verify MAC preserved in clone
- Cross-namespace scenario: Create VM in ns-a with MAC, recreate in ns-b with same spec → MAC preserved

## Implementation Phases

### Phase 1: Core Implementation
- Add MAC persistence logic to VM controller
- Add VMI informer event filtering
- Implement reconciliation trigger on VMI Running + interfaces populated
- Add unit tests
- Add e2e test scenarios

### Phase 2: observability and Documentation
- Update user documentation
- Add observability (events)

### Events

Successful MAC persistence will generate a Kubernetes Event on the VM:
```
Type: Normal
Reason: MACAddressesPersisted
Message: Persisted MAC addresses for interfaces: default, secondary
```

Failed persistence will generate a Warning event:
```
Type: Warning
Reason: MACPersistenceFailed
Message: Failed to persist MAC addresses: <error>
```
