# VEP #455: Add cloud-init vendor-data support

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/455)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP adds vendor-data support to the cloud-init NoCloud and ConfigDrive datasources in KubeVirt. Vendor-data is the channel cloud-init reserves for the entity launching the instance, and it is the only one of the four files these datasources read that KubeVirt cannot supply today. The new fields follow the pattern of the existing `userData` and `networkData` fields and are guarded by the `CloudInitVendorData` feature gate.

## Motivation

The cloud-init NoCloud datasource reads four files: `user-data`, `meta-data`, `vendor-data` and `network-config`. KubeVirt exposes `userData` and `networkData` through the VM API and generates `meta-data` itself, so three of the four are covered. `vendor-data` is the only one that isn't supported.

cloud-init defines vendor-data as a separate channel with a different owner:

> Vendor-data is data provided by the entity that launches an instance
> (e.g., the cloud provider). This data can be used to customize the image to
> fit into the particular environment it is being run in.

KubeVirt is often used as the virtualization layer underneath a larger service. In that setup, the tenant supplies user-data, while the operator has its own configuration that needs to reach cloud-init. Without vendor-data, the only way to deliver this configuration is through the tenant's user-data, which collapses the two layers that cloud-init deliberately keeps separate.

## Goals

- Add vendor-data support to the `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` APIs, with the same three input methods already used for user-data and network-data: inline, base64, and Secret reference
- Write the vendor-data file into the generated NoCloud and ConfigDrive ISOs
- Apply the same validation rules that user-data and network-data already have
- Guard the feature with the `CloudInitVendorData` feature gate

## Non Goals

- Providing a mechanism for enforcing configuration for tenants. cloud-init allows the instance owner to disable vendor-data, so vendor-data provides a way to supply defaults.

## Definition of Users

- **Platform providers**: administrators of the cluster and KubeVirt. They enable the `CloudInitVendorData` feature gate and supply the vendor-data.
- **VM owners**: users who request VMs through the platform provider's API. They supply arbitrary user-data.

## User Stories

- As a platform provider, I want to supply my own cloud-init configuration without modifying the user-data submitted by the VM owner, so that I do not have to parse or rewrite content in a format I do not control.
- As a VM owner, I want my user-data to be passed to cloud-init unchanged and take precedence over the platform's configuration, so that I retain control over my VM.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### API changes

Three optional fields are added to `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` in `staging/src/kubevirt.io/api/core/v1/schema.go`:

```go
type CloudInitNoCloudSource struct {
    // ...existing fields...

    // VendorDataSecretRef references a k8s secret that contains NoCloud vendordata.
    // + optional
    VendorDataSecretRef *v1.LocalObjectReference `json:"vendorDataSecretRef,omitempty"`
    // VendorDataBase64 contains NoCloud cloud-init vendordata as a base64 encoded string.
    // + optional
    VendorDataBase64 string `json:"vendorDataBase64,omitempty"`
    // VendorData contains NoCloud inline cloud-init vendordata.
    // + optional
    VendorData string `json:"vendorData,omitempty"`
}
```

`CloudInitConfigDriveSource` gets the same three fields.

The field names and types match the existing `userData` and `networkData` fields, which already support the same three input methods for both sources. A VM that does not set any of the new fields behaves exactly as before.

### Datasource file layout

The vendor-data content is written into the generated ISO.

| Datasource | Path in the ISO | Existing files |
|---|---|---|
| NoCloud | `vendor-data` | `user-data`, `meta-data`, `network-config` |
| ConfigDrive | `openstack/latest/vendor_data.json` | `user_data`, `meta_data.json`, `network_data.json` |

For ConfigDrive, cloud-init reads `vendor_data.json` as JSON, which means the content must be valid JSON. This is how `networkData` already works.

### Feature gate

The new fields are guarded by the `CloudInitVendorData` feature gate, which is disabled by default. While the gate is disabled, a VMI that sets any of the vendor-data fields is rejected by the validating webhook.

### Validation

The vendor-data fields are validated by the same rules that already apply to `userData` and `networkData`:

- Only one of `vendorData`, `vendorDataBase64`, or `vendorDataSecretRef` may be set.
- `vendorDataBase64` must be valid base64.
- Inline and base64 content is limited to 2 KiB, the same limit as `cloudInitUserMaxLen` and `cloudInitNetworkMaxLen`. Content from a Secret is not counted because the webhook does not read it. Larger content should use `vendorDataSecretRef`.

### Implementation details

- `staging/src/kubevirt.io/api/core/v1/schema.go`: add the three fields to both sources.
- `pkg/virt-config/featuregate/active.go`: register the `CloudInitVendorData` gate.
- `pkg/virt-api/webhooks/validating-webhook/admitters/vmi-create-admitter.go`: reject the fields while the gate is disabled, and apply the validation rules above.
- `pkg/cloud-init/cloud-init.go`: read the vendor-data from the inline, base64 or Secret source, and write it to the ISO.
- `pkg/virt-controller/services/rendervolumes.go`: mount the Secret named by `vendorDataSecretRef` into the virt-launcher pod.

## API Examples

NoCloud:

```yaml
volumes:
  - name: cloudinit
    cloudInitNoCloud:
      vendorData: |
        #cloud-config
        write_files:
          - path: /etc/vendor.conf
            content: hello
```

ConfigDrive:

```yaml
volumes:
  - name: cloudinit
    cloudInitConfigDrive:
      vendorData: '"#cloud-config\nwrite_files:\n  - path: /etc/vendor.conf\n    content: hello\n"'
```

## Alternatives

### Merge into user-data before it reaches KubeVirt

The platform merges its own configuration into the user-data, either in its own API or via a `MutatingAdmissionPolicy`, and passes a single merged document to KubeVirt.

**Rejected.** The tenant chooses the format of the user-data, and cloud-init accepts several: cloud-config, a shell script, a MIME archive, a gzip blob, or a Jinja template. A shell script has no structure to merge into, and a Jinja template is not YAML until cloud-init renders it at boot.

### Wrap user-data in a MIME multipart archive

The platform puts the tenant's user-data and its own configuration into a MIME multipart archive and passes that as the user-data.

**Rejected.** The platform has to rewrite the tenant's input into an archive, so this works only if the platform also restricts what the tenant may submit. Nor does it reproduce the precedence that cloud-init defines between user-data and vendor-data, so the platform would have to implement that on its own.

## Scalability

No scalability impact. The vendor-data takes the same path as the user-data and the network-data.

## Update/Rollback Compatibility

- The new fields are optional. A VM that does not set them behaves exactly as before, whether or not the feature gate is enabled.
- Running VMs are unaffected until they are restarted, because the ISO is written when the VM starts.
- Rolling KubeVirt back to a version without the feature drops vendor-data from the VM spec, so the VM starts without it.

## Functional Testing Approach

- Unit tests: validation rules, feature gate checks, and reading vendor-data from inline, base64, and Secret sources.
- Functional tests: start a VM with vendor-data on both datasources, and verify in the guest that it is applied and that user-data wins on conflict.

## Implementation History

## Graduation Requirements

### Alpha

- [ ] `CloudInitVendorData` feature gate added and registered as Alpha
- [ ] The three vendor-data fields added to `CloudInitNoCloudSource` and `CloudInitConfigDriveSource`
- [ ] Validating webhook rejects the fields while the feature gate is disabled
- [ ] Validating webhook applies the same validation rules as `userData` and `networkData`
- [ ] The vendor-data is written to the NoCloud and ConfigDrive ISOs
- [ ] `vendorDataSecretRef` is mounted into the virt-launcher pod
- [ ] Unit tests for all changed code paths
- [ ] Functional tests verifying the vendor-data is applied in the guest on both datasources
- [ ] User guide docs

### Beta

#### On-By-Default Readiness

### GA
