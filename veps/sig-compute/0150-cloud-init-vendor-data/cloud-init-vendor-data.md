# VEP #150: Add cloud-init vendor-data support

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/150)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes adding vendor-data support to the cloud-init NoCloud and ConfigDrive datasources in KubeVirt. Vendor-data is a standard cloud-init feature that allows cloud providers and operators to inject configuration separately from user-data.

## Motivation

The cloud-init NoCloud and ConfigDrive datasources support three types of data:
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

1. **As a platform operator**, I want a vendor-data field available in the VMI spec so that I can use a MutatingAdmissionPolicy to automatically inject organization-wide defaults (e.g., monitoring agents, security settings) into all VMs without requiring individual VM owners to configure it themselves.

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
    ...
    // New fields for vendor-data
    VendorDataSecretRef  *v1.LocalObjectReference `json:"vendorDataSecretRef,omitempty"`
    VendorDataBase64     string                   `json:"vendorDataBase64,omitempty"`
    VendorData           string                   `json:"vendorData,omitempty"`
}
```

### CloudInitConfigDriveSource

```go
type CloudInitConfigDriveSource struct {
    ...
    // New fields for vendor-data
    VendorDataSecretRef  *v1.LocalObjectReference `json:"vendorDataSecretRef,omitempty"`
    VendorDataBase64     string                   `json:"vendorDataBase64,omitempty"`
    VendorData           string                   `json:"vendorData,omitempty"`
}
```

### Operational Mechanism

Since `vendorData` is a field on the VM/VMI spec, the standard way for a platform operator to inject it cluster-wide — independently from the VM owner — is through a **MutatingAdmissionPolicy** (or MutatingAdmissionWebhook). The admission controller intercepts VM/VMI creation requests and injects the `vendorData` field before the object is persisted. From the VM owner's perspective, cloud-init simply applies additional platform configuration at boot; they do not need to include vendor-data in their spec at all.

This follows the same Kubernetes-native pattern used for sidecar injection, default tolerations, and resource limits. The vendor-data secret is owned and managed by the cluster admin; VM owners have no access to modify it.

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

### Example 1: Platform operator injecting vendor-data via secret (NoCloud)

A cluster admin creates a secret containing the platform configuration, then a MutatingAdmissionPolicy injects `vendorDataSecretRef` into all VMs at admission time. The VM owner's spec does not need to reference vendor-data at all.

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
            vendorDataSecretRef:
              name: platform-vendor-data
```

### Example 2: Platform operator injecting vendor-data via secret (ConfigDrive)

Same pattern using the ConfigDrive datasource, where the vendor-data file is written as `vendor_data.json` in the ISO.

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
            vendorDataSecretRef:
              name: platform-vendor-data
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

1. **Vendor-data content verification**:
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

### Alpha

**Alpha Requirements:**
- [x] Implementation complete with all three input methods (inline, base64, secret ref) for NoCloud
- [x] Implementation complete with all three input methods (inline, base64, secret ref) for ConfigDrive
- [x] Content size validation enforced in API (256 KB limit)
- [x] Unit tests for vendor-data handling pass (NoCloud and ConfigDrive)
- [ ] VEP approved and merged
- [ ] Code PR approved and merged
- [ ] Functional e2e tests added and passing

### GA

**GA Requirements (future, pending real-world adoption):**
- [ ] Stable for at least 2 releases
- [ ] Positive feedback from adopters
- [ ] Documentation updated in kubevirt/user-guide
- [ ] No breaking changes required
