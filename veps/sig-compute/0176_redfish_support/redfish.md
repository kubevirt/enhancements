# VEP #0176: Redfish support for managing virtual machines

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

Please accept the inclusion of the kubevirt-redfish project under the kubevirt umbrella. This project provides
the functionality of the Redfish protocol that can be used to integrate kubevirt VMs into higher level
baremetal management systems.

## Motivation

<!--
Why this enhancement is important
-->

Both developers and system administrators require the ability to deploy applications or systems in local virtual environments like bare-metal ones.
Orchestration systems like Spacewalk or OpenShift installers commonly use Redfish and Boot ISO approaches to deploy the operating system or application the user requested in the high level UI.

## Goals

<!--
The desired outcome
-->

- Provide essential BMC functionalities such as (rebooting, changing boot devices, and mounting virtual media) via the Redfish protocol against VMs backed by KubeVirt
- Expose service endpoints for clients to access either in-cluster or externally
- Enable deployment tools like Metal3 and installers (OpenShift ABI or IPI) to treat the virtual machines in the same way they treat bare metal nodes

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

## Definition of Users

<!--
Who is this feature set intended for
-->

- A cluster administrator
- A VM owner

## User Stories

<!--
List of user stories this design aims to solve
-->

## Repos

<!--
List of repose this design impacts
-->

A new repository should be created.

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

The kubevirt-redfish implementation runs as a single service providing endpoints to all eligible VirtualMachines.
Namespaces are represented as Chassis and VirtualMachines are the machines under their respective Chassis.
Each namespace can specify separate Redfish credentials allowing separation of machine administrator privileges for multi tenant deployments.

## API Examples

<!--
Tangible API examples used for discussion
-->

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

An older request was approved in https://github.com/kubevirt/community/blob/main/design-proposals/kubevirtbmc.md but never materialized.

This alternative implementation known as kubevirt-redfish provides only the Redfish implementation that is becoming the industry standard and was only planned as Stage 3
in the original design proposal. However thanks to this only one service and pod are needed, because the namespace and VM separation is handled via HTTP url nesting.

## Scalability

<!--
Overview of how the design scales)
-->

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

## Functional Testing Approach

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

- kubevirt-redfish repository created under the kubevirt organization
- CI systems connected and e2e tests written

### Beta

### GA
