# VEP 104: Allow disabling feature gates

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Today, Kubevirt's API for feature gate management is a `kv.spec.configuration.developerConfiguration.featureGates`
which is a [list of strings](https://github.com/kubevirt/kubevirt/blob/18c78c0f4d5f4c155ff2b425d0a213b563ac720e/staging/src/kubevirt.io/api/core/v1/types.go#L2813-L2815),
each string is an enabled feature gate.

The API is defined as follows:
```go
 type DeveloperConfiguration struct { 
 	// FeatureGates is the list of experimental features to enable. Defaults to none 
 	FeatureGates []string `json:"featureGates,omitempty"
```

With this approach it is impossible to explicitly disable a feature gate.
This VEP aims to make it possible.

## Motivation

Disabling a feature gate is very important for different reasons, the main one arguably being that it opens the door to
enable beta feature gates by default.
This capability is very important, because it allows widely testing a feature upstream
(which will at least be tested by CI and developers, alongside small users) while possibly disabling it downstream.

This approach enables to gain wider feedback and confidence before the feature becomes GA,
which has the potential to ensure features are much more stable and get a much wider feedback before graduating.

This VEP however is only scoped to allow disabling feature gates.
Enabling Beta feature gates is out-of-scope for this VEP and should be addressed in a follow-up.

## Goals

* Allow to explicitly disable feature gates.

## Non Goals

* Enable feature gates by default.

## Definition of Users

* Feature developers.
* Cluster admins.

## User Stories

* As a feature developer, I want my feature to be widely tested and used before graduating,
so I can gain wide feedback and confidence in it.
* As a cluster administrator, I want to be able to decide on whether to use Alpha/Beta features or not.

## Repos

kubevirt/kubevirt.

## Design

Kubevirt CR's API needs to change in order to allow disablement of feature gates.

See the different alternatives below.

### Chosen approach - Use a complementary slice for disabled feature gates

The chosen approach is approach number 3 from below.

The subject was discussed in an upstream-facing meetings.
The AI-generated summary can be found in:
https://docs.google.com/document/d/10aqnfV4EWYHGilsWvVCqaX3sNdtUqKb1sTKOFrm-W38/edit?usp=sharing

Briefly, despite there was a complete consensus that approach 1 is ideally better,
there was also an agreement that since features gates are highly unlikely to be extended in terms of configuration options
(other than being either enabled or disabled) there is almost no real value in creating another field and exposing
two ways to achieve the same goal.
If in the future we will find the need to extend feature gate configuration, we can always circle back to deprecating
the current fields and fallback to approach number 1.

## API Examples

See below.

## Alternatives

Before deciding to create a VEP, this change was re-implemented three times as part of PR [#14427](https://github.com/kubevirt/kubevirt/pull/14427).

In this section, I'll outline the three approaches so we can discuss which of them is the best moving forward.
I'd be happy for more pros and cons from reviewers if you can think of any. 

### Approach #1 - Use a `FeatureGateConfiguration` struct slice with an `Enabled` field

As part of this approach, a feature enablement is determined by its configuration in the new `kv.spec.configuration.featureGates` field.
This field is a slice of `FeatureGateConfiguration` objects which currently contain two fields, `name` and `enabled`:
```go
type FeatureGateEnablement string
const (
	EnableFeatureGate FeatureGateEnablement = "Enable"
	DisableFeatureGate FeatureGateEnablement = "Disable"
)

type FeatureGateConfiguration struct {
	Name string `json:"name"`
	// Enabled indicates whether the feature gate is enabled or not. Defaults to true.
	// +optional
	Enablement FeatureGateEnablement `json:"enablement,omitempty"`
}
```

`kv.spec.configuration.featureGates` has precedence over `kv.spec.configuration.developerConfiguration.featureGates`.
In addition, `kv.spec.configuration.developerConfiguration.featureGates` would be marked as deprecated and discouraged.
However, if a feature gate exists only in the legacy feature gate slice, it would be considered enabled.
This way we keep backward compatibility.

Usage Example:
```go
kind: KubeVirt
spec:
  configuration:
    featureGates:
      - name: VMExport
        enablement: Enable
      - name: ImageVolume
        enablement: Enable
      - name: DownwardMetrics
        # defaults to Enable
    developerConfiguration:
      featureGates:
        - IncrementalBackup # This enables the FG
        - ImageVolume # Being ignored since the same FG is listed in the above config which takes precedence
```

Pros:
* Extensible: a struct opens the door for further configuration moving forward.
* Aligns with Kubernetes best practices: "the convention is to use a list of subobjects containing name fields ... 
This rule maintains the invariant that all JSON/YAML keys are fields in API objects".
See [Kubernetes api conventions](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md#lists-of-named-subobjects-preferred-over-maps)
for more info.

Cons:
* Verbose: when there is no desire to disable feature gates, the admin would now have to supply the `name: ` boilerplate
which makes the manifest more verbose.
* Exposes two API fields to perform the same task, although one of them is marked as `deprecated`.

### Approach #2 - Use a new map structure from a string to bool

As part of this approach, a new `kv.spec.configuration.featureGateMap` will be added of type `map[string]bool`.
That is a map from string to bool types, i.e. feature name to whether it's enabled or not.

`kv.spec.configuration.featureGates` has precedence over `kv.spec.configuration.developerConfiguration.featureGates`.
However, if a feature gate exists only in the legacy feature gate slice, it would be considered enabled.
This way we keep backward compatibility.

Usage Example:
```go
kind: KubeVirt
spec:
  configuration:
    featureGateMap:
      VMExport: true
      ImageVolume: false
      DownwardMetrics: false
    developerConfiguration:
      featureGates:
        - IncrementalBackup # This enables the FG
        - ImageVolume # Being ignored since the same FG is listed in the above config which takes precedence
```

Pros:
* Simplicity (?): Easy to implement and understand.

Cons:
* Goes against Kubernetes best practices: "the convention is to use a list of subobjects containing name fields".
The api conventions specifically mention using maps as an anti-pattern:
"Lists of named subobjects preferred over maps ...  There are no maps of subobjects in any API objects".
See [Kubernetes api conventions](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md#lists-of-named-subobjects-preferred-over-maps)
for more info.
* Maps are, by definition, unordered. This means that users/clients always have to sort it in a consistent manner
to avoid entities mistakenly assuming that the state had changed because a change of ordering.
* Non-extensible: If we'll need to add more configurations to feature gates in the future, we'll be in the same problem
of having to extend the API.

### Approach #3 - Use a complementary slice for disabled feature gates

As part of this approach, a complementary `kv.spec.configuration.developerConfiguration.disabledFeatureGates` string slice will be added.
The same feature gate cannot be provided to both slices, this should result with an error.

Usage Example:
```go
kind: KubeVirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - IncrementalBackup
        - ImageVolume
      disabledFeatureGates:
        - VMExport
        - DownwardMetrics
        # - ImageVolume  -> invalid, would result in an error since already provided above
```

Pros:
* No need to deprecate the old `kv.spec.configuration.developerConfiguration.featureGates` field.
* Easy adoption: the API is very familiar to what we have to day.

Cons:
* Non-extensible: If we'll need to add more configurations to feature gates in the future, we'll be in the same problem
of having to extend the API.
* We keep both API fields under `kv.spec.configuration.developerConfiguration` instead of finding a new home under `kv.spec.configuration`.

### Approach #4 - Reuse the current string slice, allow special syntax for disablement

As part of this approach no new API fields will be added, but the current `kv.spec.configuration.developerConfiguration.featureGates`
will be reused to allow disabling feature gates.

This will be achieved by allowing the following special syntax: `<gate>=<true|false>`,
or just `<gate>` that will be interpreted as `<gate>=true`.

Usage Example:
```go
kind: KubeVirt
spec:
  configuration:
    developerConfiguration:
      featureGates:
        - "IncrementalBackup: true"
        - "ImageVolume: false"
        - VMExport # defaults to true
```

Pros:
* Avoid adding / changing API fields.
* Syntax is fairly simple and elegant.

Cons:
* Special parsing would be needed by any user/client. This means:
  * Kubevirt's code would need to include many parsing logic. 
  * Standard tooling would have trouble working with that.
* We keep the API field under `kv.spec.configuration.developerConfiguration` instead of finding a new home under `kv.spec.configuration`.

## Scalability

No scalability issues are expected.

## Update/Rollback Compatibility

In all of the approached above backward compatibility will be kept.

In some of the approaches above `kv.spec.configuration.developerConfiguration` will be deprecated, but will not be
removed in the foreseen future.

## Functional Testing Approach

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

This VEP is intended to jump straight to GA, for the following reasons:
* This VEP has an effect on all other feature-gates.
* It is essential for enabling beta features by default.
* It is not really a feature, more of a formal API review. The logic and implementation is straight forward.
* It doesn't make sense to create a feature gate for feature gate management.

I would ask for an approval from each SIG approver in order to ensure this approach is widely accepted.
