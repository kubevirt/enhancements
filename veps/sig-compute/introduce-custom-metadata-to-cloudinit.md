# VEP #100: Introduce Custom Metadata to Cloud-init

## Release Signoff Checklist
Items marked with (R) are required *prior to targeting to a milestone / release*.
- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview
This VEP proposes enhancing KubeVirt's cloud-init metadata support by enabling users to add custom metadata to cloud-init. 
This addresses missing functionality for users to be able to add custom values to the cloud-init metadata.

## Motivation
KubeVirt's cloud-init support previously did not allow users to add custom values to the metadata. 
This prevented users from passing application-specific information to their VMs through cloud-init.
Issue https://github.com/kubevirt/kubevirt/issues/15836 the motivation behind this VEP.

## Goals
- Enable users to add custom metadata fields to cloud-init through the metaData field in both `NoCloud` and `ConfigDrive` configurations
- Implement  `MetaData` in the `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` struct to allow users add custom metadata to the cloud-init
- Implement `MetaDataSecretRef` in the `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` struct to allow users to reference Kubernetes secrets containing custom metadata
- Ensure custom metadata is properly merged with standard metadata fields (instance-id, local-hostname, instance-type, etc.) that KubeVirt automatically generates

## Non Goals


## Definition of Users
VM owners and workload developers who need to pass custom metadata to their virtual machines 
through cloud-init for application configuration, environment-specific settings, or runtime parameters. 
This includes developers deploying applications that require custom configuration data accessible via cloud-init metadata,
such as environment variables, application settings, or deployment-specific parameters.
Cluster administrators may be indirectly impacted as they need to understand the new
metadata capabilities and potentially create secrets for users, 
but they are not the primary users of this functionality.

## User Stories
- As a VM owner, I want to add custom metadata fields to my cloud-init configuration 
so that my applications can access application-specific configuration data.
- As a VM owner, I want to reference Kubernetes secrets containing
custom metadata so that I can securely pass sensitive configuration data to my VMs.

## Repos
- kubevirt/kubevirt (core API)

## Design
Enhance `CloudInitNoCloudSource` and `CloudInitConfigDriveSource` with `MetaData` field as `map[string]string `and `MetaDataSecretRef` for secret references. 
Secret resolution reads custom metadata and merges with standard fields.

## API Examples
```yaml
volumes:
- name: cloudinitdisk
  cloudInitNoCloud:
    userData: |
      #cloud-config
      package_update: true
    metaData:
      app_name: "my-application"
      environment: "production"
      cost_center: "12345"
```

```yaml
volumes:
- name: cloudinitdisk
  cloudInitNoCloud:
    userData: |
      #cloud-config
      package_update: true
    metaDataSecretRef:
      name: my-metadata-secret
---
apiVersion: v1
kind: Secret
metadata:
  name: my-metadata-secret
type: Opaque
stringData:
  app_name: "my-application"
  environment: "production"
  cost_center: "12345"      
```

## Alternatives

## Scalability

## Update/Rollback Compatibility
- New field is optional; existing VMIs remain unaffected.
- On downgrade, projected volumes fall back to errors or no-op.
## Functional Testing Approach
- Unit tests: API validation, struct marshaling, metadata merging.
- Integration tests: Deploy VM/VMI with custom metadata, verify metadata is accessible in guest.
## Implementation History
- October 17, 2024: Implementation completed and PR submitted.
- October 19, 2024: VEP drafted (after reviewer feedback indicated VEP was required for API changes).
## Graduation Requirements
### Alpha (v1.8)
- Custom metadata support implemented.
- MetaData functionality complete.
- MetaDataSecretRef functionality complete.
### Beta
- Full IRSA support.
### GA

