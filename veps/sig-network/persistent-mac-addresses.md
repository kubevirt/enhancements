# VEP: Persistent MAC Addresses for VirtualMachines

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Target Release

KubeVirt v1.8 or next appropriate minor release (pending community approval)

## Overview

This enhancement introduces automatic MAC address persistence for VirtualMachines by adding a MAC synchronizer in `virt-controller` that persists runtime MAC addresses. This ensures MAC addresses remain stable across VM lifecycle operations (stop/start, restart, migration) without requiring manual user intervention.

## Motivation

### Current Behavior and Problems

When a VM is created without explicit MAC addresses specified in its spec:
1. VM is created with empty `spec.template.spec.domain.devices.interfaces[].macAddress` fields
2. VMI is instantiated from the VM template
3. CNI/network plugin assigns MAC addresses at pod creation time
4. MAC addresses appear in VMI status but **not** in VM spec
5. On VM restart/stop-start, **new MAC addresses are assigned** (VM spec is still empty)
6. On VM live migration, the pod interface mac address changes while the MAC addresses specified on the domain persist (inconsistent). This may have implications of functionality when security measurements are use (e.g. macspoofing protection).

This creates several operational problems:

**Guest OS Networking Instability**
- Some Guest OS networking configurations are bound to MAC addresses (DHCP leases, network bonds, udev rules, firewall configurations)
- MAC address changes on restart break these configurations
- Requires manual reconfiguration or guest OS reboot to recognize new MACs
- Poor user experience compared to physical machines and industry-standard virtualization platforms:
  - **VMware vSphere**: MAC addresses [persist by default](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/8-0/vsphere-networking-8-0/mac-addresses.html) across VM power cycles
  - **AWS EC2**: The MAC address of an Elastic Network Interface (ENI) is [stable for the lifetime of that ENI](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-eni.html#eni-basics), and the primary ENI normally stays attached to the instance across stop/start.

**Inconsistent with Physical Machine Behavior**
- Physical machines retain their NICs' MAC addresses across reboots
- VMs should behave similarly for guest OS compatibility

### Real-World Impact

Kubevirt opinionated deployment ([hyperconverged-cluster-operator](https://github.com/kubevirt/hyperconverged-cluster-operator)) currently work around this by:
- relying on external MAC management tools ([kubemacpool](https://github.com/k8snetworkplumbingwg/kubemacpool)) which introduce webhook complexity
- allowing manually specifying MAC addresses in VM specs (error-prone)

## Goals

- Automatically persist MAC addresses without user intervention
- Support common network bindings (masquerade, bridge, custom bindings, SR-IOV)
- Work transparently with any CNI plugin (MAC generation is CNI-agnostic)
- Maintain backward compatibility with VMs that already have explicit MACs

## Non Goals

- Removal of Cluster-wide MAC address allocation feature from kubemacpool (should happen in tandem, but is not part of this VEP)
- Cluster-wide MAC address collision detection (handled by external tools like kubemacpool)
- MAC Persistence for ephemeral entities such as pods, VMIs (except those managed by a VM)

## Definition of Users

- **VM Operators**: Users who create and manage VMs, expecting stable network identities
- **Cluster Admins**: Administrators who manage VM infrastructure and need predictable MAC addresses
- **Application Developers**: Users running applications in VMs that depend on consistent network configuration

## User Stories

**As a VM operator**:
- I want my VM's MAC addresses to persist across restarts without manual intervention, so that guest OS networking configuration remains stable and I don't need to reconfigure the network on every restart.
- I want to migrate a VM across namespaces (e.g., in hosted control plane scenarios) while preserving MAC addresses, so the guest OS doesn't detect a hardware change.

**As a cluster admin**, I want to clone a VM to another namespace and preserve its MAC addresses, so that the guest OS environment remains consistent without manual MAC copying.

**As an application developer**, I want VMs to behave like physical machines where MAC addresses don't change on reboot, so my applications that depend on network identity work correctly.

## Repos

- kubevirt/kubevirt

## Design

### High-Level Architecture

The network synchronizer in `virt-controller`'s VM controller will be adjusted to copy the MAC back to the VM, making it persistent, following the similar pattern as the firmware UUID synchronizer. 

### Synchronizer Logic

MAC persistence will extend the existing network synchronizer in virt-controller, which manages network interface lifecycle. The synchronizer performs a two-step persistence during VM reconciliation:
1. Copy MAC addresses from `vmi.Status.Interfaces[].MAC` to `vmi.Spec.Domain.Devices.Interfaces[].MacAddress` when the VMI spec field is empty.
2. Copy MAC addresses from VMI spec to `vm.Spec.Template.Spec.Domain.Devices.Interfaces[].MacAddress` when the VM template field is empty.

### MAC Address Source

MAC addresses are read from `vmi.Status.Interfaces[].MAC`, which is populated from two sources:
1. **Standard interfaces**: Populated from libvirt domain specification, reflecting CNI-allocated pod network interfaces
2. **SR-IOV interfaces**: Populated from the pod's network-status annotation (requires enhancement as described in Edge Cases)

### Idempotency and Safety

- Controller only patches VM/VMI spec if `macAddress` field is currently empty
- Once persisted, subsequent reconciliations skip that interface (no-op)
- User-specified MACs are never overwritten
- Safe to call multiple times (defense against controller restarts, race conditions)
- Interface matching by name ensures correct MAC is persisted to correct interface

### Edge Cases Handled

**SR-IOV MAC Persistence:**
Currently, SR-IOV interface MAC addresses in VMI status are only populated when explicitly set in the VM spec. For SR-IOV interfaces without pre-specified MACs, a prerequisite step extracts the MAC address from the pod's `k8s.v1.cni.cncf.io/network-status` annotation (populated by the CNI plugin) into `vmi.Status.Interfaces[].MAC`. This follows the existing pattern used for DeviceInfo extraction.
Once the network-status annotation is set, the status.interfaces update logic will be exteneded to copy it to vmi.status's SRIOV interface.

**VMI Recreation:**
- On VM restart, if MACs already persisted, they are reused (no new allocation)
- If MACs not yet persisted, waits for new VMI to reach Running state

**Controller Restarts:**
- Idempotent design allows safe reconciliation after controller restart

**Active Migrations:**
- Cross-namespace migrations create the target VMI by copying the source VMI spec, which is why MAC persistence on the VMI spec persistence is critical as well
- To prevent a race condition where migration starts before MACs are persisted to the source VMI spec, the migration controller will verify that all source VMI interfaces have populated MAC addresses before initiating the target pod creation
- This check will apply to both regular and cross-namespace/cluster migrations

**Avoiding RestartRequired Condition:**

The two-step persistence approach naturally avoids triggering the `RestartRequired` condition. However, to avoid possible issues the RestartRequired will be adjusted to not trigger if the MACs added are the ones reflected from the vmi.status.

## Known Limitations

### VM Export Race Condition

The VM export feature has a similar timing issue as migration. If a VM is exported before the MAC synchronizer has persisted the MAC addresses to `vm.Spec`, the exported VM snapshot will not contain the runtime MAC addresses. However, unlike migration (which has observable VMI status indicating an active migration), VM export does not expose any status on the VM object that the synchronizer can detect to handle this case.

**Impact:** This is a low-mild issue. If a VM is exported immediately after VMI creation (before the synchronizer runs), the exported VM will not have MAC addresses in its spec. However, if the VM was created without explicit MAC addresses in the first place (relying on CNI-generated MACs), users should not have an expectation of MAC persistence in the export. The eventual consistency model ensures that once the synchronizer has persisted the MACs, subsequent exports will naturally include them.

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

- **Controller overhead**: Minimal - synchronizer runs as part of existing VM reconciliation loop
- **API server load**: One additional PATCH per VM on first start (subsequent starts are no-op). Could optionally be optimized to reduce the number of API calls by patching once per reconcile, but it's outside the VEP's scope.
- **Memory impact**: No additional memory required (no new watches or caches)

### Resource Consumption

- Idempotent design prevents unnecessary API calls
- No additional event handlers or watches needed

## Update Compatibility

### Upgrade Behavior

**Existing VMs:**
- VMs with explicit MACs in spec: No change, MACs are preserved
- VMs without MACs (empty spec): On next start, MACs will be persisted
- Running VMs: MACs will be persisted once controller detects them (if not already present)

**New VMs:**
- VMs created without MACs: MACs are automatically persisted on first start
- VMs created with MACs: Explicit MACs are preserved (not overwritten)

## Functional Testing Approach

### Unit Tests

**Network Synchronizer Tests (extending existing test suite):**

**VMI spec persistence:**
- Synchronizer persists MAC from VMI status to VMI spec when VMI spec field is empty
- Synchronizer skips VMI spec interfaces with pre-existing MAC addresses (never overwrites)
- Synchronizer handles VMI without status interfaces gracefully

**VM spec persistence:**
- Synchronizer persists MAC from VMI spec to VM template spec when VM template field is empty
- Synchronizer skips VM template interfaces with pre-existing MAC addresses (never overwrites)
- Synchronizer handles missing VMI gracefully (returns unmodified VM)

**General synchronizer tests:**
- Synchronizer handles VMI not in Running phase (returns unmodified VM)
- Synchronizer persists MAC on multiple interfaces, including the pod network interface
- Interface name matching works correctly across VMI status, VMI spec, and VM template spec
- JSON patch generation is correct for both VMI and VM objects

### E2E Tests

- VM lifecycle: Create → Start → Stop → Start → Verify same MAC
- Live migration: MAC persists across migration
- VM cloning workflow: Clone VM → Verify MAC preserved in clone:
  - when VM created without a MAC address, and is persisted by the new synchronizer
  - when VM created with an explicit MAC address
- Cross-namespace scenario: Create VM in ns-a with MAC, recreate in ns-b with same spec → MAC preserved

Note: Existing tests could be changed to include most cases.

## Implementation Phases

### Phase 1: Core Implementation
- Extend existing network synchronizer to add MAC persistence logic
  - Copy MACs from VMI status to VMI spec
  - Copy MACs from VMI spec to VM template spec
- Implement SR-IOV MAC extraction from network-status annotation to VMI status
- Add migration controller check to ensure MACs are persisted before starting migration
- Add unit and e2e tests

### Phase 2: Documentation
- Update user documentation

## Feature Lifecycle

### Proposed Graduation: Beta (Skipping Alpha)

This enhancement proposes starting at Beta stage, skipping Alpha, based on the following rationale:

1. **No API Changes**: The enhancement only modifies controller behavior. The `macAddress` field already exists in the VM API and is widely used.

2. **Low Risk**: The synchronizer follows the established pattern used by firmware UUID persistence, which has been stable in production.

3. **Idempotent & Safe**: The synchronizer only acts when MAC addresses are absent from VM spec, making it safe and non-disruptive. User-specified MACs are never modified.

4. **No Breaking Changes**: Existing VMs with explicit MAC addresses are unaffected. VMs without MACs get improved behavior (persistence).

### Beta

- **Feature Gate**: `VMPersistentMACs` enabled by default
- **Implementation**: Complete MAC synchronizer implementation integrated into VM controller
- **Testing**: Comprehensive unit and e2e test coverage
- **Documentation**: User-facing documentation in KubeVirt user guide
- **Duration**: 1-2 releases

**Beta Graduation Criteria:**
- [ ] MAC synchronizer implemented following firmware synchronizer pattern
- [ ] Unit tests with extensive coverage
- [ ] Comprehensive e2e tests including:
  - VM restart preserving MACs
  - Multi-interface VMs (including pod network)
  - VM cloning with and without explicit MACs
  - Cross-namespace scenarios
  - Live migration with MAC persistence
- [ ] User documentation published
- [ ] Feature gate protection in place
- [ ] Performance validated (no regression in VM controller reconciliation)
- [ ] Compatible with common CNI plugins (ovn-kubernetes, flannel, calico)

### GA

- **Feature Gate Removal**: Remove `VMPersistentMACs` feature gate
- **Production Ready**: Feature meets all production deployment criteria

**GA Graduation Criteria:**
- [ ] Feature stable in Beta for at least 2 releases
- [ ] Wide adoption by Beta users with positive feedback
- [ ] Integration with kubemacpool validated (no conflicts)
- [ ] All documentation complete (user guide, troubleshooting, migration guide)
- [ ] Performance benchmarks showing no regression in VM reconciliation loop
