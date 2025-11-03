# VEP #123: Architecture Support Tracking for Feature Gates

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in
[kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This enhancement introduces architecture-specific support tracking for KubeVirt
feature gates. The `SupportByArch` field allows maintainers and users to
clearly identify which CPU architectures (amd64, arm64, s390x) support specific
features, with granular status tracking (Supported, Pending, Unsupported,
Unverified).

## Motivation

As KubeVirt expands to support multiple CPU architectures (amd64, arm64,
s390x), not all features have equal support across architectures. Some features
are hardware-specific (e.g., AMD SEV workload encryption), while others may be
untested or have known compatibility issues on certain architectures.
Currently, there is no systematic way to track and communicate
architecture-specific feature support, leading to:

- User confusion when enabling features on unsupported architectures
- Difficulty tracking testing/verification status across architectures
- No clear indication of which features are architecture-specific by design
- Challenges in planning architecture-specific testing and support efforts

## Goals

- Provide explicit architecture support status for each feature gate
- Enable users to determine feature compatibility before deployment
- Track verification status of features across different architectures
- Document architectural limitations (e.g., hardware-specific features)
- Facilitate targeted testing efforts for multi-architecture support

## Non Goals

- Automatically prevent feature enablement on unsupported architectures
  (enforcement may come in a future iteration)
- Provide fine-grained sub-architecture tracking (e.g., specific CPU models)
- Track operating system or kernel version compatibility
- Replace or modify the existing feature maturity states (Alpha, Beta, GA)

## Definition of Users

This feature is intended for:

- **Cluster administrators** deploying KubeVirt on multi-architecture clusters
  who need to understand which features are available on their architecture
- **KubeVirt developers** implementing and testing features across different
  architectures
- **KubeVirt maintainers** tracking testing coverage and support status across architectures
- **End users** evaluating KubeVirt capabilities for specific architectural deployments

## User Stories

1. As a cluster administrator running KubeVirt on ARM64, I want to know which
   feature gates are verified to work on my architecture before enabling them.

2. As a developer implementing a hardware-specific feature (e.g., SEV
   encryption), I want to clearly document that it only works on specific
architectures.

3. As a maintainer, I want to track which features remain unverified on
   non-amd64 architectures to prioritize testing efforts.

4. As a user evaluating KubeVirt for s390x deployment, I want to understand
   feature parity compared to amd64 deployments.

## Repos

- `kubevirt/kubevirt` - Core implementation of SupportByArch tracking in feature gate infrastructure

## Design

The implementation adds a `SupportByArch` field to the existing `FeatureGate` struct. This field is a map of architecture to support status:

```go
type (
    Support       string
    Arch          string
    SupportByArch map[Arch]Support
)

const (
    SUPPORTED   Support = "Supported"
    PENDING     Support = "Pending"
    UNSUPPORTED Support = "Unsupported"
    UNVERIFIED  Support = "Unverified"

    AMD64 Arch = "amd64"
    ARM64 Arch = "arm64"
    S390X Arch = "s390x"
)

type FeatureGate struct {
    Name          string
    State         State
    VmiSpecUsed   func(spec *v1.VirtualMachineInstanceSpec) bool
    Message       string
    SupportByArch SupportByArch
}
```

**Support Status Definitions:**

- **Supported**: Feature is fully supported and tested on this architecture
- **Pending**: Support is planned but implementation/testing is not yet complete
- **Unsupported**: Feature cannot work on this architecture (e.g., hardware limitations)
- **Unverified**: Feature should theoretically work but has not been explicitly tested/verified

Features without a `SupportByArch` map are considered to have unspecified architecture support (legacy behavior).

## API Examples

**Example 1: Architecture-agnostic feature (default behavior)**

```go
RegisterFeatureGate(FeatureGate{Name: ExpandDisksGate, State: Alpha})
// No SupportByArch specified - architecture support is unspecified
```

**Example 2: Feature verified on amd64, unverified on others**

```go
RegisterFeatureGate(
    FeatureGate{
        Name:  ImageVolume,
        State: Beta,
        SupportByArch: SupportByArch{
            AMD64: SUPPORTED,
            ARM64: UNVERIFIED,
            S390X: UNVERIFIED,
        },
    },
)
```

**Example 3: Hardware-specific feature (AMD SEV)**

```go
RegisterFeatureGate(
    FeatureGate{
        Name:  WorkloadEncryptionSEV,
        State: Alpha,
        SupportByArch: SupportByArch{
            AMD64: SUPPORTED,
            ARM64: UNSUPPORTED,
            S390X: UNSUPPORTED,
        },
    },
)
```

## Alternatives

1. **Documentation-only approach**: Maintain a separate document listing
   architecture support. This was rejected because it would quickly become
outdated and disconnected from the code.

2. **Per-architecture feature gates**: Create separate feature gate names for
   each architecture (e.g., `ImageVolume-amd64`, `ImageVolume-arm64`). This was
rejected as it would create significant complexity and confusion.

3. **Runtime architecture detection and automatic disabling**: Automatically
   disable unsupported features based on detected architecture. This was
deferred to a future enhancement to keep the initial implementation simple and
transparent.

## Scalability

This enhancement has minimal scalability impact:

- The `SupportByArch` map adds a small, fixed amount of memory per feature gate
(3 architecture entries maximum)
- No runtime performance impact as this is metadata used for documentation and
validation
- Future architectures can be added by extending the `Arch` constants

## Update/Rollback Compatibility

This is a backward-compatible addition:

- The `SupportByArch` field is optional and defaults to unspecified if not provided
- Existing feature gates continue to work without modification
- No API changes to KubeVirt custom resources
- No changes to feature gate enablement mechanisms

Updates from versions without this feature to versions with it will not affect
runtime behavior. The field is purely informational in the initial
implementation.

## Functional Testing Approach

Testing will focus on verifying the metadata is correctly stored and can be retrieved:

1. **Unit tests**: Verify that feature gates can be registered with
   `SupportByArch` metadata
2. **Unit tests**: Verify that feature gates without `SupportByArch` continue
   to work (backward compatibility)
3. **Integration tests**: Verify that the support status can be queried via
   feature gate introspection APIs (if/when exposed)

Future work may include:

- Validation that prevents enabling unsupported features
- Warning logs when enabling unverified features
- CLI/API exposure of architecture support information
