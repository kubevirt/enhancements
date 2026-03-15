# VEP #229: Beta features on by default

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- ~[ ] (R) Target version is explicitly mentioned and approved~
- ~[ ] (R) Graduation criteria filled~

## Overview

This is a meta-VEP to enable beta features by default.

The term meta-VEP means this VEP is not for a new feature nor an API change.
Even more so, little to no code changes are required for this VEP.
However, after some discussions, it was agreed that this is a good platform to discuss these changes in a transparent
and public form.

## Motivation

The Beta phase of a feature acts as a "dress rehearsal" before going to GA.
In this phase the community needs to ensure the feature is stable,
gain wide user-feedback,
ensure the API remains stable, etc.

By enabling beta feature gates by default, we "eat our own dog food", and achieve the following:
- Every beta feature will be tested by our CI with every entered PR.
- Every beta feature will be tested by each of our developers by default during development.
- Different beta features will be tested with one another.

_Note: only the feature gate will be enabled by default.
If a feature is disabled by default via configuration, this is out of scope for this vep._

This will give us "out-of-the-box" wide testing and user feedback,
turning our contributors to early beta testers by default.
It also aligns with the KEP process in Kubernetes.

Downstream vendors will obviously have the ability to decide which features are being enabled or disabled in production.
See below for more information.

## Goals

- Beta features would be enabled by default (see specific policy below).
- Help graduate features by enabling them for a larger audience earlier in the process.
- Downstream vendors would be able to list immature yet enabled features, and easily disable those they do not like.

## Non Goals

- Discuss the requirements of graduation to beta.
This is out of scope and should be discussed [elsewhere](https://github.com/kubevirt/enhancements/pull/214).

## Definition of Users

- KubeVirt developers: who are planning and implementing features via VEPs.
- KubeVirt reviewers/approvers: who are approving feature to graduate between alpha/beta/GA phases.
- Cluster administrator: who's in charge of managing production clusters.

## User Stories

- As a VEP developer, I want to gain wide feedback from users before graduating to GA.
- As a VEP reviewer/approver, I want to gain a high confidence regarding a feature's stability before approving GA graduation.
- As a cluster admin, I want to be able to list immature yet enabled features, and easily disable those I do not like.

## Repos

- kubevirt/kubevirt
- kubevirt/hyperconverged-cluster-operator

## Design

### All Beta features becoming on-by-default

From v1.9 onwards, **every** beta feature gate will be on by default.

This will not be configurable, but auto-enabled for all Beta features with no technical ability to disable by default.
See [Downstream control](#downstream-control) below for more details regarding how to enable these in production.

### Code changes

The code changes needed to support this are extremely minimal.

The [isFeatureGateEnabled()](https://github.com/kubevirt/kubevirt/blob/9d789083c379bf96131f7064ed0d5571806b8279/pkg/virt-config/feature-gates.go#L32)
will need to take into account that Beta features are on by default.

### Downstream control

Downstream vendors should have control over which features are enabled/disabled in their production environments.
Generally, it is recommended to disable non-GA features in production.

In order for vendors to know the state of KubeVirt's feature gates without having to parse its codebase,
which is fragile and cumbersome,
KubeVirt will include a metadata file as part of its release artifacts that will reflect the state of all of its feature gates.

This metadata file will be auto-generated via a simple script which will parse KubeVirt's [active.go file](https://github.com/kubevirt/kubevirt/blob/02d3ea105dd61960f754cd8da2721fe9dd8ff00d/pkg/virt-config/featuregate/active.go).
I think the specific format can be discussed in the relevant implementation PR,
but just to give a general idea, it can look something similar to:

```json
[
  {
    "name": "cool-feature-1",
    "phase": "alpha"
  },
  {
    "name": "cool-feature-2",
    "phase": "beta"
  }
]
```

There are a few options in order to disable feature gates downstream:

#### HCO native approach

Using the feature state metadata file mentioned above, [HCO](https://github.com/kubevirt/hyperconverged-cluster-operator)
will explicitly disable all on-by-default non-GA feature gates.

Being opinionated, HCO will have an in-code list of exceptions which it chooses to configure differently.
HCO would have the liberty to decide which features will be enabled/disabled by default when KubeVirt is deployed via HCO.

This means that whenever a VEP owner thinks that an exceptional configuration is required,
e.g. enabling a beta feature by default on production environments which deploy KubeVirt by using HCO,
it is the VEP owner's responsibility to add a PR to HCO which adds it to the list of exceptionally configured features.

#### HCO Json-Patches

If a downstream vendor's desired feature gate configuration differs from HCO's defaults        
and the HCO maintainers decline to accommodate it,                                          
the cluster-admin can still use JSON patches to override the KubeVirt CR's `DisabledFeatureGates` list directly - as a last resort.

#### non-HCO deployments

When KubeVirt is not deployed via HCO,
the `spec.configuration.DeveloperConfiguration.FeatureGates`
and `spec.configuration.DeveloperConfiguration.DisabledFeatureGates`
can be used in order to control feature gate behavior.

## API Examples

I think that we should discuss the (minor) implementation details and formats in the implementation PRs.

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

## Scalability

No scalability issues are expected.

## Update/Rollback Compatibility

No update/rollback computability issues are expected.
On older versions (v1.8 and older) beta features are off by default.

## Functional Testing Approach

Tests suite setup can be simplified, as Beta FGs do not need to be enabled there.

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

## Graduation Requirements

As said above, this is not a feature nor an API change.
This meta-VEP policy should be effective immediately from v1.9 onwards.
