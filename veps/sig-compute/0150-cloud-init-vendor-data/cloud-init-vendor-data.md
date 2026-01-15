# VEP #150: Add cloud-init vendor-data support

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/150)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes adding vendor-data support to the cloud-init NoCloud and ConfigDrive datasources in KubeVirt. Vendor-data is a standard cloud-init feature that allows cloud providers and operators to inject configuration separately from user-data.

## Motivation

The cloud-init NoCloud and ConfigDrive datasources support four types of data:
- **meta-data**: Instance metadata (already supported)
- **user-data**: User-provided configuration (already supported)
- **network-data**: Network configuration (already supported)
- **vendor-data**: Vendor/operator-provided configuration (**not yet supported**)

KubeVirt currently supports user-data and network-data and auto-generates meta-data. However, vendor-data is not supported, limiting cloud providers and platform operators from injecting custom configuration without modifying user-data.

Cloud-init is designed to merge vendor-data with user-data, allowing operators to provide defaults that users can override. Without vendor-data support, operators must either:
1. Ask users to include platform-specific configuration in their user-data
2. Use post-boot configuration mechanisms that bypass cloud-init

Both alternatives are suboptimal and go against cloud-init's design philosophy.

## Goals

- Add vendor-data support to `CloudInitNoCloudSource` API
- Add vendor-data support to `CloudInitConfigDriveSource` API
- Support three input methods (matching existing patterns for user-data and network-data):
  - Inline string (`vendorData`)
  - Base64 encoded (`vendorDataBase64`)
  - Kubernetes Secret reference (`vendorDataSecretRef`)
- Include vendor-data file in the generated NoCloud and ConfigDrive ISOs
- Implement resource limits (maximum entry counts and value lengths) to prevent abuse
- Maintain full backward compatibility

## Non Goals

- Merging vendor-data with user-data at the KubeVirt level (cloud-init handles this internally)
- Validating vendor-data content format
- Supporting vendor-data for other cloud-init datasources

## Definition of Users

- **Platform operators**: Organizations running KubeVirt clusters who need to inject baseline configuration across all VMs (monitoring agents, security policies, SSH keys, etc.). These operators control the cluster infrastructure and use vendor-data to provide platform-level defaults.

- **Cloud providers**: Providers offering KubeVirt-based virtualization services who need to provide cloud-specific defaults. Similar to platform operators, they control the service infrastructure and use vendor-data to integrate VMs with platform services.

- **End users**: VM owners who create and manage VirtualMachine resources. End users typically do not directly interact with vendor-data; instead, they benefit from platform-level configuration provided by operators through vendor-data while focusing their user-data on application-specific needs. In multi-tenant scenarios, namespace-level secrets can allow controlled vendor-data injection.

The typical relationship is: platform operators/providers define vendor-data that applies to VMs, while end users define user-data for their applications. Cloud-init merges these configurations with user-data taking precedence for conflicts.

## User Stories

1. **As a platform operator**, I want to inject default packages and security settings via vendor-data so that all VMs have a consistent baseline without requiring users to modify their user-data.

2. **As a cloud provider**, I want to provide cloud-specific networking configuration or monitoring agents via vendor-data so that VMs are automatically integrated with my platform services.

3. **As a user**, I want my user-data to remain clean and focused on my application configuration while vendor-data handles platform-specific concerns that I don't need to manage.

4. **As a multi-tenant platform operator**, I want to inject tenant-specific configuration via vendor-data secrets so that different tenants can have different baseline configurations.

## Repos

- kubevirt/kubevirt

## Design

The implementation adds three new optional fields to both `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` structs in the KubeVirt API:

### CloudInitNoCloudSource

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

### CloudInitConfigDriveSource

```go
type CloudInitConfigDriveSource struct {
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

1. **API Schema**: Add three new optional fields to both `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` in `staging/src/kubevirt.io/api/core/v1/schema.go`

2. **Cloud-init data handling**: Update `pkg/cloud-init/cloud-init.go` to:
   - Add `VendorData` field to internal `CloudInitData` struct
   - Read vendor-data from inline, base64, or secret sources for both NoCloud and ConfigDrive
   - Write `vendor-data` file to the NoCloud ISO
   - Write `vendor_data.json` file to the ConfigDrive ISO

3. **Secret mounting**: Update `pkg/virt-controller/services/rendervolumes.go` to mount `VendorDataSecretRef` as a volume in the virt-launcher pod for both NoCloud and ConfigDrive volumes

4. **ISO generation**: When generating the ISOs:
   - For NoCloud: include `vendor-data` file alongside existing `user-data`, `meta-data`, and `network-config` files
   - For ConfigDrive: include `vendor_data.json` file alongside existing `user_data`, `meta_data.json`, and `network_data.json` files

### Entry Limits and Validation

Following the design pattern established in VEP #100 (custom metadata support), this VEP implements resource limits to protect cluster resources and prevent abuse:

- **Content size limit**: Maximum total size of vendor-data content is **256 KB** (262,144 bytes)
- **Enforcement**: Limits are enforced during API validation, preventing VMI creation if vendor-data exceeds the threshold
- **Limit scope**: 
  - Applies to the raw content size of vendor-data (inline string, base64-decoded content, or secret value)
  - Measured after base64 decoding but before ISO generation
  - Consistent with user-data size limits already enforced in KubeVirt

**Rationale**: Unlike VEP #100's metadata fields which use a key-value map structure (hence the 16-key limit), vendor-data is free-form YAML/text content similar to user-data. Therefore, a content size limit (matching existing user-data limits) is more appropriate than key-count limits. This provides resource protection while allowing operators flexibility to provide comprehensive platform configuration.

## API Examples

### NoCloud Examples

#### Example 1: Inline vendor-data (NoCloud)

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

#### Example 2: Base64 encoded vendor-data (NoCloud)

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

#### Example 3: Secret reference for vendor-data (NoCloud)

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

#### Example 4: Combined with network-data (NoCloud)

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

### ConfigDrive Examples

#### Example 5: Inline vendor-data (ConfigDrive)

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
          cloudInitConfigDrive:
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

#### Example 6: Base64 encoded vendor-data (ConfigDrive)

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
          cloudInitConfigDrive:
            userDataBase64: I2Nsb3VkLWNvbmZpZwpwYWNrYWdlczoKICAtIG5naW54Cg==
            vendorDataBase64: I2Nsb3VkLWNvbmZpZwpwYWNrYWdlczoKICAtIG1vbml0b3JpbmctYWdlbnQK
```

#### Example 7: Secret reference for vendor-data (ConfigDrive)

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
          cloudInitConfigDrive:
            userDataSecretRef:
              name: my-user-data
            vendorDataSecretRef:
              name: platform-vendor-data
```

#### Example 8: Combined with network-data (ConfigDrive)

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
          cloudInitConfigDrive:
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

The vendor-data feature has minimal scalability impact and includes resource protection mechanisms:

- **Content size limits**: The 256 KB maximum size for vendor-data prevents resource exhaustion from excessively large payloads. This matches existing user-data size limits in KubeVirt.
- **API validation**: Size limits are enforced during API validation, preventing VMI creation if vendor-data exceeds the threshold.
- **Processing overhead**: Vendor-data is processed identically to user-data, which is already proven at scale in production KubeVirt deployments. The data is:
  - Stored in etcd as part of the VM spec (same as user-data)
  - Mounted in virt-launcher pod (same as user-data secrets)
  - Written to ISO at VM start time (same as user-data)
- **Performance impact**: The 256 KB size limit ensures cloud-init processing remains performant and does not impact VM startup times.
- **Resource protection**: Size limits protect against both accidental misconfiguration and intentional abuse of the vendor-data feature.

## Update/Rollback Compatibility

### Backward Compatibility

This change is fully backward compatible:
- All new fields are optional
- Existing VMs without vendor-data continue to work unchanged
- No changes to existing API field semantics

### Rollback Safety

Rolling back KubeVirt to a version without vendor-data support is safe and causes graceful degradation:

**What happens on rollback:**
- VMs with vendor-data specified in their spec will have those fields ignored by the older KubeVirt version
- The vendor-data file will not be included in the generated NoCloud or ConfigDrive ISO
- VMs will boot and function normally, but without vendor-data configuration applied
- No VM failures, crashes, or data loss will occur

**Why this is safe:**
- vendor-data is optional configuration; its absence does not prevent VMs from starting
- Cloud-init gracefully handles missing vendor-data (it simply skips that merge step)
- VMs retain their user-data, network-data, and meta-data configuration
- After upgrade back to a vendor-data-capable version, vendor-data will be processed again

**Recommendation:**
- Platform operators should be aware that vendor-data-dependent configuration (e.g., mandatory monitoring agents) will not be applied to VMs during rollback
- Consider testing rollback scenarios if vendor-data provides critical platform functionality

### Upgrade Path

No special upgrade considerations. The feature is additive and optional.

## Functional Testing Approach

### Unit Tests

1. **Reading vendor-data sources** (NoCloud and ConfigDrive):
   - Test reading inline vendor-data string
   - Test decoding base64 vendor-data
   - Test reading vendor-data from secret reference
   - Test error handling for invalid base64

2. **ISO generation**:
   - Test that vendor-data file is included in NoCloud ISO when provided
   - Test that vendor_data.json file is included in ConfigDrive ISO when provided
   - Test that vendor-data file is omitted when not provided
   - Test combined user-data, network-data, and vendor-data

3. **Secret resolution**:
   - Test resolving vendor-data from mounted secret for NoCloud
   - Test resolving vendor-data from mounted secret for ConfigDrive
   - Test handling missing secret gracefully

### Functional/E2E Tests

1. **VM boot with vendor-data**:
   - Create VM with vendor-data (NoCloud)
   - Create VM with vendor-data (ConfigDrive)
   - Verify VM boots successfully
   - Verify cloud-init processes vendor-data

2. **Vendor-data content verification**:
   - Create VM with known vendor-data content
   - Verify vendor-data file exists in NoCloud/ConfigDrive ISO
   - Verify content matches what was specified

3. **Secret-based vendor-data**:
   - Create secret with vendor-data
   - Create VM referencing the secret (both NoCloud and ConfigDrive)
   - Verify vendor-data is correctly injected

## Implementation History

- 2025-12-03: Initial implementation PR opened: https://github.com/kubevirt/kubevirt/pull/16278
- 2025-12-09: VEP created: https://github.com/kubevirt/enhancements/issues/150
- 2025-12-25: Added ConfigDrive vendor-data support to implementation

## Graduation Requirements

### GA

**Rationale for direct GA graduation:**

Following the precedent set by VEP #100 (custom metadata support), this enhancement proposes direct GA graduation without an Alpha/Beta phase. The rationale includes:

- **Simple, additive API changes**: Three optional fields (`VendorData`, `VendorDataBase64`, `VendorDataSecretRef`) following established patterns for user-data and network-data, added to both NoCloud and ConfigDrive
- **Built-in resource protection**: Content size limits (256 KB maximum) enforced through API validation
- **Low risk nature**: 
  - Optional fields with no impact on existing VMs
  - Identical processing pattern to user-data (proven at scale)
  - Graceful rollback behavior (VMs boot without vendor-data)
- **Well-understood functionality**: Vendor-data is a standard cloud-init feature with established semantics
- **No feature gate needed**: Simple extension of existing cloud-init support; no experimental behavior or complex interactions

**GA Requirements:**
- [x] Implementation complete with all three input methods (inline, base64, secret ref) for NoCloud
- [x] Implementation complete with all three input methods (inline, base64, secret ref) for ConfigDrive
- [x] Content size validation enforced in API (256 KB limit)
- [x] Unit tests for vendor-data handling pass (NoCloud and ConfigDrive)
- [ ] VEP approved and merged
- [ ] Code PR approved and merged
- [ ] Functional e2e tests added and passing
- [ ] Documentation updated in kubevirt/user-guide
- [ ] Stable for at least 2 releases
- [ ] No breaking changes required
- [ ] Positive feedback from adopters
