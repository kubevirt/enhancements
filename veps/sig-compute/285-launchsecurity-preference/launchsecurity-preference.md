# VEP #285: Preset launch security defaults via preference

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: v1.9.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Add the `launchSecurity` parameter to `VirtualMachineClusterPreference` and
`VirtualMachinePreference` to allow users to specify launch security settings for multiple VMs
while still allowing overrides on a per-VM basis.
Additionally remove `launchSecurity` from `VirtualMachineInstanceSpec`, as it does not make sense there.

## Motivation

Confidential Computing workloads are typically prepared from specific images created by cluster
administrators with additional security measures.
Since VMs started from these images will all run with launch security enabled, this allows cluster
administrators to provide the image templates as a `DataSource` and specify common `launchSecurity`
settings via preferences.
Users can then fine-tune these settings for specific VM instances as needed.

## Goals

- Enable launch security settings via preferences
- Improve the workflow of creating Confidential Computing VMs from common instance types
- Prepare common preference types for IBM Secure Execution
- Deprecate launch security setting from the `VirtualMachineInstanceSpec`

## Non Goals

- Block users from customizing the launch security settings per VM
- Setting global defaults for launch security
- Remove launch security setting from the `VirtualMachineInstanceSpec`, as it would require a new api version

## Definition of Users

**Cluster Administrators:**
- Install the Kubernetes cluster and KubeVirt
- Enable required features
- Prepare common VM image sources
- Perform other tasks related to cluster management, e.g., creating namespaces, providing storage,
  access management, etc.

**Users:**
- Create VMs from prepared VM images
- Manage the lifecycle of VMs
- (Optional) Own the workload running inside the VM

## User Stories

**As a cluster administrator, I want to:**
- Easily enable Confidential Computing in my cluster
- Not spend additional effort leveraging Confidential Computing
- Easily enable users to start Confidential Computing VMs

**As a user, I want to:**
- Easily create Confidential Computing VMs
- Easily run Confidential Computing workloads
- Spend no additional effort running Confidential Computing VMs compared to normal VMs

## Repos

- https://github.com/kubevirt/kubevirt
- https://github.com/kubevirt/api
- https://github.com/kubevirt/common-instancetypes

## Design

For implementation details, see the [PoC PR](https://github.com/kubevirt/kubevirt/pull/17551).

Add the `launchSecurity` field as `preferredLaunchSecurity` to `VirtualMachinePreferenceSpec` the same way as is already done
for `VirtualMachineInstancetypeSpec`:

```go
type VirtualMachinePreferenceSpec struct {
  ...

	// Optionally defines the preferred LaunchSecurity
	//
	// +optional
	PreferredLaunchSecurity *v1.LaunchSecurity `json:"preferredLaunchSecurity,omitempty"`
}
```

When applying preferences to a new VMI, follow the same pattern as for other settings:

1. If the `launchSecurity` field in the VMI is set, do nothing
2. If the `launchSecurity` field in the preference is set, set the `launchSecurity` field in the
   VMI to the value from the preference and return
3. If neither the VMI nor the preference have a `launchSecurity` field, do nothing
4. Settings defined in the choosen instancetype override everything else

The following table summarizes the outcomes:

| VMI   | Preference | Instancetype | Outcome      |
| ----- | ---------- | ------------ | ------------ |
| empty | empty      | empty        | empty        |
| set   | empty      | empty        | VMI          |
| set   | set        | empty        | VMI          |
| empty | set        | empty        | Preference   |
| empty | empty      | set          | Instancetype |
| set   | empty      | set          | Error        |
| set   | set        | set          | Error        |
| empty | set        | set          | Instancetype |

## API Examples

Example preference for Fedora with IBM Secure Execution:

```yaml
apiVersion: instancetype.kubevirt.io/v1beta1
kind: VirtualMachineClusterPreference
metadata:
  annotations:
    openshift.io/display-name: Fedora (IBM Secure Execution)
  labels:
    instancetype.kubevirt.io/arch: s390x
    instancetype.kubevirt.io/os-type: linux
  name: fedora.secure-execution
spec:
  annotations:
    vm.kubevirt.io/os: linux
  devices:
    preferredDiskBus: virtio
    preferredInterfaceModel: virtio
    preferredRng: {}
  preferredArchitecture: "s390x"
  requirements:
    cpu:
      guest: 1
    memory:
      guest: 2Gi
  preferredLaunchSecurity: {}
```

The deprecation of `launchSecurity` from `VirtualMachineInstanceSpec` will be done by emmitting a warning
when the field is used. The functionality will still be preserved.

## Alternatives

1. **Use InstanceTypes instead** → Does not allow override on a per-VM level
2. **Auto-detect if image is Confidential Computing** → High complexity; uncertain if even possible
   in all scenarios

## Scalability

This feature has no impact on scalability.

## Update/Rollback Compatibility

**Update:**
- Users need to manually create/update preferences with the new settings
- There is no change to VM behavior without preference types that include `launchSecurity`
  settings

**Rollback:**
- Running VMs will be unaffected
- Newly created VMIs will not have `launchSecurity` enabled if they relied on a preference for
  enabling it

## Functional Testing Approach

Since this feature only influences the rendering of VMIs, testing can be performed entirely through
unit tests.
New unit tests will be added to ensure the feature works as expected.
No end-to-end tests will be added, as existing tests already cover the actual launch security
features.

## Implementation History

- **21-04-2026:** Created VEP proposal and PoC PR

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
- [ ] API changes are implemented and available
- [ ] New preference field is applied when rendering VMI
- [ ] Unit tests are implemented and passing
- [ ] Added deprecation notice for `launchSecurity` field in `VirtualMachineInstanceSpec`

### Beta
- [ ] `VirtualMachineClusterPreferences` for IBM Secure Execution are added to common-instancetypes

### GA
- [ ] The feature is stable and production-ready
