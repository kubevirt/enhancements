# VEP #178: virt-lint integration

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP allows using [virt-lint](https://gitlab.com/MichalPrivoznik/virt-lint)
during VMI boot-up (and later possibly VM creation) to run various validators to
check anything of interest that admins or users should be warned about.

## Motivation

The integration of virt-lint allows for sharing of various policies between
projects as well as the separation between technical part of the codebase and
the policy/validation part.  That consequently allows for separate updates and
settings of the validators.

The bigger idea is that the validators can be shared between other projects that
are interested in checking various scenarios while each project can subscribe
only to those they are interested in.

## Goals

The virt-lint validators will be shipped as part of kubevirt container image(s)
-- for starters the virt-launcher container -- and all VMIs will be checked
before starting.  Validation errors (fatal for virt-lint) will be reported in
the logs and warnings will be forwarded as events to be seen in the web UI and
in VMI's description.

In the future there might be another virt-lint API that supports hinting,
i.e. replying with suggestions, but since that was (at least to my knowledge)
only raised in a non-technical discussion, it is something to be designed.
Being an user of virt-lint, of course, gives leverage in the design discussion
of such APIs.

## Non Goals

- Any modification of the definition.
- Preventing VMI creation.

## Definition of Users

- VM owners
- Cluster admins
- KubeVirt developers

## User Stories

- As a VM owner I have a configuration that cannot, for some reason, be matched
  by the scheduler (e.g. not a hard limit, not implemented yet) and I want to be
  notified if there is a potential (non-fatal) performance, security, or any
  other impact of such configuration.

- As a cluster admin I want to implement some warnings specific for my cluster
  that I want to warn VM owners of.

- As a KubeVirt developer I want to check (e.g. in CI) that testing VMI
  configurations are not producing sub-optimal XML definitions and share such
  known scenarios with other virtualization management solutions.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

Existing virt-lint validators are added to the virt-launcher container image,
but they can be selected based on tags.  Custom validators kept in kubevirt
repository are added as well, but those should be minimal in the end.  The
validators RPM can later be made separate or it can even be fetched directly
from a new git repository if separated from the code.

Whenever LibvirtDomainManager is created it accepts a channel of VirtLintEvent
structs to which messages from virt-lint are sent once the domain XML is ready.
Those are obtained by running selected validators on that XML.  The caller can
decide what to do with them, for starters it can be forwarded as a K8SEvent via
virt-launcher's notify-client.

In the future this might be done earlier if virt-lint supports hinting, since
virt-lint does not need an active connection to libvirt and that functionality
can be supplemented by gathering domain capability XMLs from the nodes, just like node-labeller.sh already does.

## API Examples

No KubeVirt APIs needed.

## Alternatives

All the functionality can be implemented in KubeVirt itself, but that will not
have the added value of knowledge sharing between projects.

The functionality could be implemented in a completely separate container, but
due to its connection with virt-launcher itself it seems like an overhead.  The
new container could be turned off after validations, but it would be needed to
spin up once a change in the domain XML happens.

## Scalability

Since this is used only during VMI creation, the computational impact is
minimal.  Storage impact was not measured (yet).

## Update/Rollback Compatibility

This integration does not affect compatibility.

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

No tests yet, but since there is no new API added and hinting is not implemented
in virt-lint yet, it could be just a collection of VirtLintEvents sent during
startup on a specific configuration.

There is also the possibility to craft a particular VMI configuration for each
KubeVirt-specific use case.

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
