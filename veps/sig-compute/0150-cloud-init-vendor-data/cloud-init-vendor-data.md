# VEP #150: Add cloud-init NoCloud vendor-data support

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/150)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes adding vendor-data support to the cloud-init NoCloud datasource in KubeVirt. Vendor-data is a standard cloud-init feature that allows cloud providers and operators to inject configuration separately from user-data.

## Motivation

The cloud-init NoCloud datasource supports four types of data:
- **meta-data**: Instance metadata (already supported)
- **user-data**: User-provided configuration (already supported)
- **network-data**: Network configuration (already supported)
- **vendor-data**: Vendor/operator-provided configuration (**not yet supported**)

Currently, Currently, KubeVirt exposes user-data, network-data, and auto-generates meta-data. Only vendor-data remains unsupported, which limits the ability of cloud providers and platform operators to inject their own configuration without modifying user-data.

Cloud-init is designed to merge vendor-data with user-data, allowing operators to provide defaults that users can override. Without vendor-data support, operators must either:
1. Ask users to include platform-specific configuration in their user-data
2. Use post-boot configuration mechanisms that bypass cloud-init

Both alternatives are suboptimal and go against cloud-init's design philosophy.

## Goals

- Add vendor-data support to `CloudInitNoCloudSource` API
- Support three input methods (matching existing patterns for user-data and network-data):
  - Inline string (`vendorData`)
  - Base64 encoded (`vendorDataBase64`)
  - Kubernetes Secret reference (`vendorDataSecretRef`)
- Include vendor-data file in the generated NoCloud ISO
- Maintain full backward compatibility

## Non Goals

- Adding vendor-data support to ConfigDrive datasource (can be done in a separate VEP)
- Merging vendor-data with user-data at the KubeVirt level (cloud-init handles this internally)
- Validating vendor-data content format
- Supporting vendor-data for other cloud-init datasources

## Definition of Users

- **Platform operators**: Organizations running KubeVirt clusters who need to inject baseline configuration across all VMs (monitoring agents, security policies, SSH keys, etc.)
- **Cloud providers**: Providers offering KubeVirt-based virtualization services who need to provide cloud-specific defaults
- **End users**: VM owners who continue using user-data for their application-specific configuration

## User Stories

1. **As a platform operator**, I want to inject default packages and security settings via vendor-data so that all VMs have a consistent baseline without requiring users to modify their user-data.

2. **As a cloud provider**, I want to provide cloud-specific networking configuration or monitoring agents via vendor-data so that VMs are automatically integrated with my platform services.

3. **As a user**, I want my user-data to remain clean and focused on my application configuration while vendor-data handles platform-specific concerns that I don't need to manage.

4. **As a multi-tenant platform operator**, I want to inject tenant-specific configuration via vendor-data secrets so that different tenants can have different baseline configurations.

## Repos

- kubevirt/kubevirt

## Design

The implementation adds three new optional fields to `CloudInitNoCloudSource` struct in the KubeVirt API:

```go
type CloudInitNoCloudSource struct {
    // Existing fields for user-data
    UserDataSecretRef    *v1.LocalObjectReference `json:"secretRef,omitempty"`
    UserDataBase64       string                   `json:"userDataBase64,omitempty"`
    UserData             string                   `json:"userData,omitempty"`
    
    // Existing fields for network-data
    NetworkDataSecretRef *v1.LocalObjectReference `json:"networkDataSecretRef,omitempty"`
    NetworkDataBase64    string                   `json:"networkDataBase64,omitempty"`
    NetworkData          string                   `json:"networkData,omitempty"`
    
    // New fields for vendor-data
    VendorDataSecretRef  *v1.LocalObjectReference `json:"vendorDataSecretRef,omitempty"`
    VendorDataBase64     string                   `json:"vendorDataBase64,omitempty"`
    VendorData           string                   `json:"vendorData,omitempty"`
}
```

### Implementation Details

1. **API Schema**: Add three new optional fields to `CloudInitNoCloudSource` in `staging/src/kubevirt.io/api/core/v1/schema.go`

2. **Cloud-init data handling**: Update `pkg/cloud-init/cloud-init.go` to:
   - Add `VendorData` field to internal `CloudInitData` struct
   - Read vendor-data from inline, base64, or secret sources
   - Write `vendor-data` file to the NoCloud ISO

3. **Secret mounting**: Update `pkg/virt-controller/services/rendervolumes.go` to mount `VendorDataSecretRef` as a volume in the virt-launcher pod

4. **ISO generation**: When generating the NoCloud ISO, include `vendor-data` file alongside existing `user-data`, `meta-data`, and `network-config` files

## API Examples

### Example 1: Inline vendor-data

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      volumes:
        - name: cloudinit
          cloudInitNoCloud:
            userData: |
              #cloud-config
              packages:
                - nginx
            vendorData: |
              #cloud-config
              packages:
                - monitoring-agent
                - security-scanner
              runcmd:
                - systemctl enable monitoring-agent
```

### Example 2: Base64 encoded vendor-data

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      volumes:
        - name: cloudinit
          cloudInitNoCloud:
            userDataBase64: I2Nsb3VkLWNvbmZpZwpwYWNrYWdlczoKICAtIG5naW54Cg==
            vendorDataBase64: I2Nsb3VkLWNvbmZpZwpwYWNrYWdlczoKICAtIG1vbml0b3JpbmctYWdlbnQK
```

### Example 3: Secret reference for vendor-data

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: platform-vendor-data
type: Opaque
stringData:
  vendordata: |
    #cloud-config
    packages:
      - monitoring-agent
    write_files:
      - path: /etc/platform/config.yaml
        content: |
          cluster: production
          region: us-east-1
---
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      volumes:
        - name: cloudinit
          cloudInitNoCloud:
            userDataSecretRef:
              name: my-user-data
            vendorDataSecretRef:
              name: platform-vendor-data
```

### Example 4: Combined with network-data

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: my-vm
spec:
  template:
    spec:
      volumes:
        - name: cloudinit
          cloudInitNoCloud:
            userData: |
              #cloud-config
              hostname: my-vm
            networkData: |
              version: 2
              ethernets:
                eth0:
                  dhcp4: true
            vendorData: |
              #cloud-config
              packages:
                - qemu-guest-agent
```

## Alternatives

### Alternative 1: Use user-data for everything

Users could include vendor/platform configuration directly in their user-data.

**Rejected because:**
- Couples user configuration to platform requirements
- Users must know and maintain platform-specific configuration
- Changes to platform defaults require updating all user-data
- Goes against cloud-init's separation of concerns design

### Alternative 2: Use ConfigMaps/Secrets mounted as disks

Platform configuration could be mounted as additional disks that cloud-init reads.

**Rejected because:**
- More complex setup for users
- Doesn't integrate with cloud-init's native vendor-data merging
- Requires users to configure cloud-init to look for additional datasources

### Alternative 3: Post-boot configuration via DaemonSet or SSH

Platform configuration could be applied after VM boot using external tools.

**Rejected because:**
- Requires additional infrastructure (DaemonSet, SSH access, etc.)
- Doesn't benefit from cloud-init's declarative model
- Race conditions with user's cloud-init configuration
- Less reliable than boot-time configuration

## Scalability

No scalability concerns. The vendor-data is processed identically to user-data, which is already proven at scale in production KubeVirt deployments. The data is:
- Stored in etcd as part of the VM spec (same as user-data)
- Mounted in virt-launcher pod (same as user-data secrets)
- Written to ISO at VM start time (same as user-data)

## Update/Rollback Compatibility

### Backward Compatibility

This change is fully backward compatible:
- All new fields are optional
- Existing VMs without vendor-data continue to work unchanged
- No changes to existing API field semantics

### Rollback Safety

If KubeVirt is rolled back to a version without vendor-data support:
- VMs with vendor-data specified will have those fields ignored
- The vendor-data file will not be included in the ISO
- VMs will still boot and function (just without vendor-data)
- No data loss or VM failures

### Upgrade Path

No special upgrade considerations. The feature is additive and optional.

## Functional Testing Approach

### Unit Tests

1. **Reading vendor-data sources**:
   - Test reading inline vendor-data string
   - Test decoding base64 vendor-data
   - Test reading vendor-data from secret reference
   - Test error handling for invalid base64

2. **ISO generation**:
   - Test that vendor-data file is included in NoCloud ISO when provided
   - Test that vendor-data file is omitted when not provided
   - Test combined user-data, network-data, and vendor-data

3. **Secret resolution**:
   - Test resolving vendor-data from mounted secret
   - Test handling missing secret gracefully

### Functional/E2E Tests

1. **VM boot with vendor-data**:
   - Create VM with vendor-data
   - Verify VM boots successfully
   - Verify cloud-init processes vendor-data

2. **Vendor-data content verification**:
   - Create VM with known vendor-data content
   - Verify vendor-data file exists in NoCloud ISO
   - Verify content matches what was specified

3. **Secret-based vendor-data**:
   - Create secret with vendor-data
   - Create VM referencing the secret
   - Verify vendor-data is correctly injected

## Implementation History

- 2025-12-03: Initial implementation PR opened: https://github.com/kubevirt/kubevirt/pull/16278
- 2025-12-09: VEP created: https://github.com/kubevirt/enhancements/issues/150

## Graduation Requirements

### Alpha

- [x] Implementation complete with all three input methods (inline, base64, secret ref)
- [x] Unit tests for vendor-data handling
- [x] Unit tests pass
- [ ] VEP approved and merged
- [ ] Code PR approved and merged

### Beta

- [ ] Functional e2e tests added and passing
- [ ] Documentation updated in kubevirt/user-guide
- [ ] Used in production by at least one adopter
- [ ] No breaking changes or bug fixes required since Alpha

### GA

- [ ] Stable for at least 2 releases
- [ ] No breaking changes required
- [ ] Positive feedback from adopters
