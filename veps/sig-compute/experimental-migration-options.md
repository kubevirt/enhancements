# VEP 293: Advanced Experimental Migration Options

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version: N/A (perpetually alpha)
- This VEP targets GA for version: N/A (perpetually alpha)

### Release Signoff Checklist

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements]
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes adding a new perpetually Alpha feature gate, `AdvancedExperimentalMigrationOptions`, to KubeVirt. When enabled, this feature gate reads a special experimental configuration section from the `MigrationPolicy` CR (`migrations.kubevirt.io/v1alpha1`). This allows users to experiment with low-level migration-related tunables and experimental APIs without custom builds, and without providing any stability guarantees for these configurations.

`MigrationPolicy` is chosen using **VirtualMachineInstance** and **Namespace** label selectors, so experimental settings on a policy affect only **matching** VMIs’ migrations.

## Motivation

Tuning migrations is a complex task that often simply cannot be adequately achieved in a lab/test environment. In many cases, it is difficult to determine good default values for low-level migration tunables, and different workloads might require distinct settings. To test these low-level tunables effectively without forcing users to rely on custom builds, we need a dedicated area for experimentation. This allows KubeVirt to expose experimental migration features and settings that may or may not graduate into more top-level API fields in the future, while making zero guarantees about their stability.

It is important to note that not everything under the experimental section is added with the intention of graduating it to a stable API. Some fields are added fully knowing we will never officially support them. They are simply low-level tunables that we might want to experiment with using different values.

## Goals

- Expose a configuration space for low-level migration-related tunables and experimental APIs.
- Provide existing default values for cases when defaults don't work, allowing users to experiment with other values.
- Give users the ability to test new migration features without custom builds.
- Ensure strict safeguards (e.g., dropping new experimental fields on create/update when disabled, and using an admission controller to block invalid use) while still allowing cluster upgrades.

## Non Goals

- Moving this feature gate to Beta or GA. `AdvancedExperimentalMigrationOptions` will perpetually and permanently be an Alpha feature gate.
- Providing stability guarantees for any options placed in the experimental section.

## Definition of Users

- **Cluster Administrators:** Who wish to volunteer their user-specific workloads by experimenting with migration tunables.
- **KubeVirt Developers:** Who need to expose new experimental features for gathering community feedback before stabilizing them in higher-level APIs.

## User Stories

- As a cluster administrator, I want to modify low-level migration tunables so I can experiment with values for my specific workload when defaults do not work in order to share this information with KubeVirt developers.
  * As an advanced cluster operator, I want to fine-tune low-level migration performance parameters when I know my workload better than the defaults, accepting that these knobs can expose implementation details and are not treated as supported product configuration.
- As a developer, I want to expose a new migration API temporarily or experimentally so I can gather real-world data from users without committing to a stable API.

## Repos

- `kubevirt/api`
- `kubevirt/kubevirt`

## Design

### Feature Gate and Configuration

We will add a new feature gate called `AdvancedExperimentalMigrationOptions`.
This feature gate is explicitly designed to remain **perpetually and permanently Alpha**. 

When this feature gate is enabled, a special configuration section will be read. This section is exposed via `migrations.kubevirt.io/v1alpha1` `MigrationPolicy` resources. The config exposes a configuration that already has default values for cases when defaults don't work, or when we want to experiment with potentially better defaults on user clusters.

This feature gate is independent from other migration features. Other features might independently expose some tunables via this experimental feature gate. For example, GA features may still expose some tunables via this option.

### Safeguards and Admission Control

Because these are purely experimental and unstable tunables that sometimes expose implementation details, we impose strict conditions on their usage via an admission policy:

- Values inside the experimental section are entirely ignored if the `AdvancedExperimentalMigrationOptions` feature gate is not enabled.
- If the feature gate is disabled, new changes cannot be pushed to the experimental section. Existing values will still be persisted but remain inactive and uneditable until the feature gate is re-enabled.
- Specifically, to preserve data on update (e.g. during a cluster upgrade where the feature gate might be temporarily disabled by default), if the existing object already has a value in the field, it is preserved. If it does not, new usage is cleared. This is handled by the admission controller.

> Note: This approach is also inline with how Kubernetes handles experimental APIs (see https://github.com/kubernetes/community/blob/main/contributors/devel/sig-architecture/api_changes.md#new-field-in-existing-api-version), preserving data on update while reserving the right to break the schema at any time.
>
> However, while K8s applies gates per feature (thanks to nifty codegen that automatically generates code with a simple API marker like `// featuregate=MyFeatureGate` comment), in KubeVirt this code needs to be hand written. So we opt to use **one** perpetually-Alpha gate—`AdvancedExperimentalMigrationOptions`—for all migration-related `experimental.*` configuration, with admission written explicitly for that single gate.

Rationale for this stricter barrier (versus only ignoring `experimental` at runtime while the gate is off): alignment with Kubernetes patterns that tie experimental API surface to a feature gate, and requiring the gate to be enabled is an **explicit** operator acknowledgement that these fields are unstable—stronger than documentation alone, which users may skip. Nevertheless, the API documentation (godoc comments) for the `experimental` field will still explicitly state that the schema, semantics, and existence of the fields under the field are unstable.

## API Examples

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - AdvancedExperimentalMigrationOptions
```

```yaml
apiVersion: migrations.kubevirt.io/v1alpha1
kind: MigrationPolicy
metadata:
  name: test-migration-policy
spec:
  experimental:  # Section ignored with out feature gate
    compression: "zstd"  # Example only
    maxDowntimeMs: 1050  # Example only
```

## Alternatives

- Exposing these experimental values via annotations. However, utilizing a properly structured `experimental` API explicitly denotes that these features are for testing purposes, making the schema clear without the opacity of unstructured annotations. Unstructured annotations also lack validations and creates the potential for security lapses which is why K8s no longer prefers using annotations for experimental API.
- Use of hooks/plugins. While a strong alternative in theory, in practice this would require a serious rework of Live Migration code. However, resolving concerns and bottlenecks in Live Migration is an immediate pain-point.
- Use of virt-launcher arguments. One of the key advantages of using API/CRD is being able to take advantage of hot-configuration changes, and built in validation. We loose iteration speed, and benefits of the API validation eco-system by using command-line args.
- A **dedicated CRD** used only for experimental migration tunables. A separate API can be dropped wholesale in a future release without leaving compatibility scars on `MigrationPolicy`. This VEP instead colocates experimental settings with existing migration configuration for discoverability instead of introducing another resource type. Nevertheless, this would be a valid alternative.
- Forcing administrators to compile KubeVirt with custom values. This is prohibitive and hinders user testing and feedback.

## Scalability

Exposing this configuration API does not inherently impact scalability, although the specific low-level tunables modified via this API might.

## Update/Rollback Compatibility

Update compatibility is explicitly **not supported** for the experimental fields themselves. While upgrading the cluster is allowed and the system will attempt to preserve existing experimental data during the update, the schema of these fields may change or be removed completely between versions without a migration path. If an experimental field is removed or its schema changes incompatibly, the API server will simply drop the data or fail validation on subsequent edits.

As per Kubernetes API guidelines, if an experimental field is removed or graduates out of the `experimental` section into a stable section:
- If a field is removed, its serialized JSON field name (Go `json` struct tag) should not be reused with new semantics. A tombstone comment may be left on the Go struct to record removal.
- If a field graduates to a stable section, a migration path may be provided to automatically transition the experimental configuration into the new stable section, or the experimental field may simply be dropped requiring manual user intervention to populate the new stable field.

## Functional Testing Approach

N/A

## Graduation Requirements

### Alpha
- [ ] Feature gate introduced and documented as perpetually Alpha.
- [ ] Experimental migration configuration section added to the MigrationPolicy CR.
- [ ] Admission policies implemented (blocking new usage without the feature gate while preserving existing data).

### Beta
- Not applicable.

### GA
- Not applicable.
