# VEP #119: Add Service to VMI

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
  writing sensitive information

- As a VM Owner, I want to validate how many people are connected at a given time (considering the
  multi-session VNC

- As a Management tool like kubevirt-ui, I want to verify if a VNC session is connected to allow
  user to know it might disconnect another user session

- As a Management tool, I might want to prevent features like Screenshot when someone is connected
  over VNC

- As a Cluster Admin, I want to validate if automation that uses VNC is still connected

## Repos

- [KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

I have a PoC written in [kubevirt!15833][].

Connecting to QEMU's VNC server will trigger a series of events in QEMU such as:
- [VNC_CONNECTED][]: Emitted when a VNC client establishes a connection
- [VNC_INITIALIZED][]: Emitted after authentication takes place (if any) and the VNC session is made active
- [VNC_DISCONNECTED][]: Emitted when the connection is closed

Those are parsed by libvirt which generates its own [Graphics events][].

Virt-launcher already listen to libvirt events, so this proposal will only extend that to listen
libvirt's Graphics events and update (locally) the VMI Status information and soon after, this
updated VMI Status is sent to virt-handler over gRPC [Send Handle]DomainEvent methods.

The VMI Status will required a field that we could expand if needed in the future but the initial
goal is providing information of how many VNC sessions are established over QEMU's VNC server.

[kubevirt!15833]: https://github.com/kubevirt/kubevirt/pull/15833
[VNC_CONNECTED]: https://gitlab.com/qemu-project/qemu/-/blob/master/qapi/ui.json#L714
[VNC_INITIALIZED]: https://gitlab.com/qemu-project/qemu/-/blob/master/qapi/ui.json#L743
[VNC_DISCONNECTED]: https://gitlab.com/qemu-project/qemu/-/blob/master/qapi/ui.json#L770
[Graphics events]:https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainEventGraphicsPhase

## API Examples

```
status:
  service:
    vnc:
      sessions: 1
```

## Alternatives

- We could also check virt-api and/or virt-handler's rest API handlers for the requests of
  connections but those would not work if users bypass virt-api

## Scalability

The update is requested on VNC session start/end which only happen with a single session Today.
With multiple sessions, QEMU has a default max-session value of 32, we might consider an interval
for updating VMI Status to avoid too frequent updates.

## Update/Rollback Compatibility

There should be no concerns. The gRPC method that communicates between virt-launcher and
virt-handler operates by Marshalling and Unmarshalling virtwrap's api.Domain object, so:

- old virt-launcher on new virt-handler: Will not listen to event, will not update VMI Status.
- new virt-launcher on old virt-handler: Listen and update local VMI Status but will be ignored by
  virt-handler, resulting in no update in VMI Status.

## Functional Testing Approach

For e2e we can extend existing tests to validate that VMI Status has been updated

## Feature lifecycle Phases

The scope of this proposal is to be able to read new information in the VMI Status by using already
established functionality of listening to libvirt events and updating VMI status between
virt-launcher and virt-handler. For that reason, it should be acceptable to extend VMI Status and
implement its update in a single lifecycle phase, without a feature gate.
