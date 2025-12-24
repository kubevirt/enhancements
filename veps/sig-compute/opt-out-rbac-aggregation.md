# VEP 160: Opt-out of RBAC Role Aggregation

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

KubeVirt currently deploys ClusterRoles with `aggregate-to-*` labels that automatically aggregate KubeVirt-related permissions to the default Kubernetes roles (`admin`, `edit`, `view`). This means that by default, any user with these standard Kubernetes roles automatically receives corresponding KubeVirt permissions within their namespace.

This VEP proposes adding a new configuration option in the KubeVirt CR that allows cluster administrators to opt-out of this default RBAC aggregation behavior. When disabled, KubeVirt ClusterRoles will not be aggregated to the default Kubernetes roles, requiring explicit RBAC assignments for KubeVirt resources.

## Motivation

While RBAC aggregation provides convenience for many deployments, security-conscious environments often require more granular control over permissions. The current behavior presents challenges for organizations that:

- Need to comply with security policies requiring explicit permission grants rather than automatic inheritance
- Want to implement the principle of least privilege where Kubernetes admin roles do not automatically include VM management capabilities
- Require audit trails showing deliberate RBAC assignments for KubeVirt resources
- Have multi-tenant environments where VM management permissions must be explicitly delegated

By providing an opt-out mechanism, cluster administrators gain the flexibility to choose between:
1. **Convenience** (default): Automatic aggregation where users with standard Kubernetes roles automatically get KubeVirt permissions
2. **Control**: Explicit RBAC management where KubeVirt permissions must be manually assigned

## Goals

- Allow cluster administrators to disable RBAC role aggregation
- Ensure the feature is backward compatible with the default behavior unchanged (aggregation enabled)
- Support dynamic toggling of the configuration without requiring KubeVirt reinstallation
- Handle both fresh installations and upgrades/updates correctly

## Non Goals

- Change the default behavior of RBAC aggregation (it remains enabled by default)
- Modify the permissions defined in KubeVirt ClusterRoles themselves
- Provide per-role or per-namespace granularity for aggregation control
- Introduce new ClusterRoles or modify existing role definitions

## Definition of Users

- **Cluster Administrator**: A user responsible for managing the Kubernetes cluster and KubeVirt installation, who configures the KubeVirt CR and manages RBAC policies
- **VM Owner**: A user who creates and manages VirtualMachines, whose effective permissions depend on the RBAC configuration

## User Stories

- As a cluster administrator in a security-conscious environment, I want to disable automatic RBAC aggregation so that users only receive KubeVirt permissions through explicit role bindings
- As a cluster administrator, I want to be able to toggle RBAC aggregation on or off without reinstalling KubeVirt, so that I can adapt to changing security requirements
- As a cluster administrator performing an upgrade, I want the RBAC aggregation setting to be respected for existing ClusterRoles, so that my security posture is maintained

## Repos

- kubevirt/kubevirt

## Design

### API Changes

A new string field `roleAggregationStrategy` is added to the KubeVirt CR's configuration section, with an enum restricting values to `AggregateToDefault` or `Manual`:

```go
// RoleAggregationStrategy represents the strategy for RBAC role aggregation
// +kubebuilder:validation:Enum=AggregateToDefault;Manual
type RoleAggregationStrategy string

const (
    // RoleAggregationStrategyAggregateToDefault enables aggregation of KubeVirt ClusterRoles to default Kubernetes roles
    RoleAggregationStrategyAggregateToDefault RoleAggregationStrategy = "AggregateToDefault"
    // RoleAggregationStrategyManual disables aggregation, requiring manual RBAC assignments for KubeVirt resources
    RoleAggregationStrategyManual RoleAggregationStrategy = "Manual"
)

type KubeVirtConfiguration struct {
    // ... existing fields ...

    // RoleAggregationStrategy controls whether RBAC cluster roles should be aggregated
    // to the default Kubernetes roles (admin, edit, view).
    // When set to "AggregateToDefault" (default) or not specified, the aggregate-to-* labels are added to the cluster roles.
    // When set to "Manual", the labels are not added, and roles will not be aggregated to the default roles.
    // +optional
    RoleAggregationStrategy *RoleAggregationStrategy `json:"roleAggregationStrategy,omitempty"`
}
```

### Implementation Details

The implementation modifies the `virt-operator` RBAC reconciliation logic:

1. **DeepCopy Strategy**: When processing ClusterRoles, a DeepCopy is made to avoid modifying the original strategy objects. This allows dynamic toggling of the aggregation setting without requiring strategy regeneration.

2. **Fresh Installations with Aggregation Disabled**: When `roleAggregationStrategy` is set to `Manual` on a fresh install, ClusterRoles are created without the `aggregate-to-*` labels, leaving no artifacts.

3. **Updates with Aggregation Disabled**: For existing ClusterRoles that already have aggregate labels, the implementation uses resourcemerge's `MergeMap` convention with a trailing "-" suffix to signal label removal. This ensures existing labels are properly cleaned up.

The following aggregate labels are affected:
- `rbac.authorization.k8s.io/aggregate-to-admin`
- `rbac.authorization.k8s.io/aggregate-to-edit`
- `rbac.authorization.k8s.io/aggregate-to-view`

### Behavior Summary

| Configuration | Fresh Install | Existing Install |
|---------------|---------------|------------------|
| `roleAggregationStrategy: AggregateToDefault` (or unset) | KubeVirt ClusterRoles created with aggregate labels | No change to existing labels |
| `roleAggregationStrategy: Manual` | KubeVirt ClusterRoles created without aggregate labels | Aggregate labels removed from existing ClusterRoles |

## API Examples

### Default Behavior (Aggregation Enabled)

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    # roleAggregationStrategy defaults to "AggregateToDefault" when not specified
```

### Opt-out of Aggregation

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    roleAggregationStrategy: Manual
```

### Re-enabling Aggregation

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    roleAggregationStrategy: AggregateToDefault
```

## Alternatives

### Alternative 1: Use environment variable instead of API field

Instead of introducing a new API field into the kubevirt's CRD, use an environment variable to virt-operator Deployment that will control whether the  
`aggregate-to-*` labels will be added to the kubevirt roles or not. In an absence of such env var, existing behavior remains (RBAC aggregation enabled).  
Implementation example: https://github.com/kubevirt/kubevirt/pull/13751

**Pros:**
- No need to introduce a new API field for this feature

**Cons:**
- User experience is worse that having it on the CR. Users can easily explore the configuration options available for them by running `kubectl explain kv.spec.configuration`  
or viewing the API documentation. Making it configurable by enviroment variable will make it obscure and less convenient for users.
- This would require a rollout of the virt-operator pod every time such change is requested, since the env vars are loaded in the pod's creation time.
- On some deployments of kubevirt, e.g. with [Operator Lifecycle Manager](https://olm.operatorframework.io/), there is no option to alter the environment variables of the operators without doing an hack (modifying the ClusterServiceVersion - CSV). Env vars can be configured only on installation time through the Subscription object, and it's immutable.

**Decision:** Rejected as it will make the feature less accessible and usable.

### Alternative 2: Per-Role Configuration

Instead of a global toggle, provide per-role configuration to control aggregation for individual ClusterRoles.

**Pros:**
- More granular control

**Cons:**
- Significantly more complex API and implementation
- Harder to maintain and audit
- Overkill for the primary use case of completely disabling aggregation

**Decision:** Rejected in favor of a simpler global toggle that addresses the primary use case.

### Alternative 3: Separate ClusterRoles Without Aggregation

Create a parallel set of ClusterRoles without aggregate labels that can be used instead.

**Pros:**
- No need to modify existing roles

**Cons:**
- Doubles the number of ClusterRoles to maintain
- Confusing for users with two sets of similar roles
- Does not address the issue of default roles still having aggregated permissions

**Decision:** Rejected as it adds complexity without solving the core problem.

## Scalability

This feature has no scalability implications. The configuration is evaluated during ClusterRole reconciliation, which happens as part of the normal virt-operator reconciliation cycle. No additional API calls or watches are required.

## Update/Rollback Compatibility

This feature is fully backward compatible:

- **Upgrade**: The default behavior remains unchanged. Existing installations continue to have RBAC aggregation enabled unless explicitly disabled.
- **Rollback**: If rolling back to a version without this feature, the aggregate labels on ClusterRoles remain in their current state (present or absent depending on the last configuration). Manual cleanup may be required if aggregation was disabled before rollback.
- **Dynamic Toggle**: The configuration can be changed at any time. The virt-operator will reconcile ClusterRoles to match the desired state on the next reconciliation cycle.

## Functional Testing Approach

The following test scenarios should be covered:

1. **Fresh install with default configuration**: Verify ClusterRoles are created with aggregate labels
2. **Fresh install with aggregation disabled**: Verify ClusterRoles are created without aggregate labels
3. **Update from enabled to disabled**: Verify aggregate labels are removed from existing ClusterRoles
4. **Update from disabled to enabled**: Verify aggregate labels are added to existing ClusterRoles
5. **Effective permissions verification**: Verify that users with `admin`/`edit`/`view` roles have (or don't have) KubeVirt permissions based on the configuration

## Implementation History

- 2025-12: Initial implementation PR: [kubevirt/kubevirt#16350](https://github.com/kubevirt/kubevirt/pull/16350)

## Graduation Requirements

This feature will follow the standard graduation process to allow consumers to experiment with it and provide feedback before it becomes generally available.

### Alpha

- [ ] Configuration option implemented
- [ ] Unit tests covering all configuration scenarios
- [ ] Basic E2E tests verifying aggregate label presence/absence based on configuration
- [ ] Initial documentation in the KubeVirt user guide

### Beta

- [ ] Feature has been available for at least one release cycle
- [ ] Feedback from consumers has been collected and addressed
- [ ] E2E tests extended to cover upgrade scenarios
- [ ] No major issues or bugs reported during Alpha phase
- [ ] Documentation updated based on user feedback

### GA

- [ ] Feature has been stable in Beta for at least one release cycle
- [ ] Positive feedback from consumers confirming the feature meets their requirements
- [ ] All known issues resolved
- [ ] Complete documentation including examples and troubleshooting guides

