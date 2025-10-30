# VEP #119: Add Service Status to VMI Status

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [#] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

We would like to know if someone is connected over VNC to a VM.

## Motivation

We have customers requesting multiple VNC related features and one of those features is to know how
many users are connected. Today, we only allow a single VNC session but alternatives to allow multi
session is being discussed, see [kubevirt!14798][]

Similarly, there was a request about having knowledge on how many USB devices are being redirected
to a VM and even which type of USB devices they were, see [kubevirt!11838][]

[kubevirt!11838]: https://github.com/kubevirt/kubevirt/pull/11838
[kubevirt!14798]: https://github.com/kubevirt/kubevirt/issues/14798

## Goals

Include a Service Status type and field into VMI Status to provide VNC Session count and possibly
expand in the future if needed.

## Definition of Users

* VM owner, who access the VM over VNC

* Cluster admin, who might need to verify ongoing VNC sessions

## User Stories

- As a VM Owner, I want to check if anyone else is watching my VNC session before accessing or
  writting sensitive information

- As a VM Owner, I want to validate how many people is connected at a given time (considering the
  multi-session VNC

- As a Management tool like kubevirt-ui, I want to verify if a VNC session is connected to allow
  user to know it might disconnect another user session

- As a Management tool, I might want to prevent features like Screenshopt when someone is connected
  over VNC

- As a Cluster Admin, I want to validate if automation that uses VNC is still ongoing/connected
  like [packer-plugin-kubevirt][])

[packer-plugin-kubevirt]: https://github.com/hashicorp/packer-plugin-kubevirt

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

I have a PoC written in [kubevirt!15833][].

The VMI Status should have a field that we could expand if needed in the future but the initial goal
is providing information of how many VNC sessions are established over QEMU's VNC server.

QEMU provides an event every time a VNC session starts and ends. Libvirt provides similar event too
in which we can listen and communiate to virt-handler so it can update VMI Status.

[kubevirt!15833]: https://github.com/kubevirt/kubevirt/pull/15833

## API Examples

```
Status:
  Service Status:
    Vnc Sessions:  1
```

## Alternatives

- We could also check virt-api and/or virt-handler's rest API handlers for the requests of
  connections but those would not work if users bypass virt-api

## Scalability

The update is requested on VNC session start/end which only happes with a single session Today.
With multiple sessions, QEMU has a default max-session value of 32, we might consider an interval
for updating VMI Status to avoid too frequent updates.

## Update/Rollback Compatibility

It'll require virt-launcher & virt-handler communicating over gRPC so that should be considered.

## Functional Testing Approach

For e2e we can extend existing tests to validate that VMI Status has been updated
