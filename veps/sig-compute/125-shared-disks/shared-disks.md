# VEP #125: Clarify/Improve usage of shared disks in VMs

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)

## Overview

<!--
Provide a brief overview of the topic)
-->
[The KubeVirt VM spec](https://github.com/kubevirt/kubevirt/blob/ada6d32348b68cf82956c7f711549c8f1aea8054/staging/src/kubevirt.io/api/core/v1/schema.go#L724) allows a user to specify whether a disk device is shareable.

Shareable disks can used by applications (like clustered database servers) running in VMs in a cluster. However not very use-case
is suitable for it.

For KubeVirt users it is important to understand how to properly
use this feature when provisioning their VMs.

The VEP is aimed at improving the handling of shared
disks in KubeVirt, including but not limited to:

  1. Better documentation on the usages, limitations and warnings.
     Currenly there is little.
  2. Add necessary validations and improvements to avoid
     wrong usages
  3. Take measures to protect shared disks from being
     accidentally deleted

Tracking Jira: https://issues.redhat.com/browse/CNV-69803

Doc references: https://docs.redhat.com/en/documentation/red_hat_virtualization/4.4/html/virtual_machine_management_guide/sect-virtual_disks

## Motivation

Shared disks often find their uses in clustered virtual machines
to guarantee high availability of an enterprise's mission 
critical applications, such as a clustered database or some
messaging systems.

Improvement on shared disks in KubeVirt help user build robustic
systems that can avoid losing of important data.

<!--
Why this enhancement is important
-->

## Goals

The goals of the VEP is to explore usages of shared disks
for KubeVirt users and find ways to improve, enhence the feature.

That include documentation, coding improvement, and finding
new features for shared disks.

<!--
The desired outcome
-->

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

## Definition of Users

The users here denote those who are using KubeVirt VMs that
have requirements for shareable disks, and those who will consider
to use shared disks as such.

<!--
Who is this feature set intended for
-->

## User Stories

A couple of Jira issues have been risen against shared disks
which are good starting points for exploring more usages (valid/invalid) so we can improve this feature. 

  * https://issues.redhat.com/browse/CNV-53145
  * https://issues.redhat.com/browse/CNV-56067

<!--
List of user stories this design aims to solve
-->

## Repos

https://github.com/kubevirt/kubevirt

<!--
List of repose this design impacts
-->

## Design

1. Documentation improvement: Scope if shared disk usages
   risks. Give some of the sample designs, for example,
   setting up a clustered database to use shared disks.
2. Valication on the VM CRs that uses the shareable disks
   such as if the type of disks are supported (some types maynot
   be set to shareable due to its device type)
3. Checking/Preventing shared disks (DV) from being accidently
   deleted while deleting it owner VM
4. Exploring other areas (if any) to improve. 
5. Investigate the cases while users are doing VM/Storage
   migrations, snapshots and exporting of VMs with shareable disks.
   At the very least we should make clear whether those types
   of operations are supported or not, giving a wanrning or reject
   the operation.

<!--
This should be brief and concise. We want just enough to get the point across
-->

## API Examples

N/A

<!--
Tangible API examples used for discussion
-->

## Alternatives

N/A
<!--
Outline any alternative designs that have been considered)
-->

## Scalability

N/A
<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

N/A
<!--
Does this impact update compatibility and how?)
-->

## Functional Testing Approach

Need to work on ways to test any new behaviors derived 
from this VEP.

<!--
An overview on the approaches used to functional test this design)
-->

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

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

### Beta

### GA


