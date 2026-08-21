# VEP #429: Add emulation preference setting

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

- This VEP targets alpha for version: 1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

To ensure that software emulation remains an opt-in feature, introduce a new setting for maximum allowed emulation tier.

## Motivation

Currently VEP #172 introduced Emulation options to enable hardware architecture independent VMs.
However since emulation is slower than real hardware, it should always be an opt-in feature.
To ensure this remains this way when VEP #172 FG becomes active with the beta stage, a new configuration option for emulation is needed.

This also fullfills one of the Beta goals VEP #172 "Consider per-VM emulation policy".

## Goals

- Allow cluster administrators and users to choose their maximum emulation tier
- Provide sensible defaults

## Non Goals

- Change the current default behaviour of non-emulation VMs

## Definition of Users

**Cluster administrator**: The person configuring the global kubevirt CR

**User**: The VM owner

## User Stories

1. As a cluster administrator, I want to set a cluster-wide policy that
   cross-architecture VMs **must** use hardware acceleration, so that no VM
   silently falls back to slow software emulation without my knowledge.

2. As a user, I do not want my VM to use emulation at all, since I need maximum performance

3. As a user, I want to test my new app on other architecture, for which I need emulation.

## Repos

https://github.com/kubevirt/kubevirt

## Design

A new field `emulationPolicy` is introduced to kubevirt and VM/VMI Spec.

The field controls **which virtualization backends are permitted**
for a cross-architecture VM. The available tiers, from most restricted to most
permissive, are:

```
None (native KVM only)  <  Hardware (KVM only, no TCG)  <  Software (all tiers)
```
At Alpha stage, Hardware Emulation is not yet implemented.
Should it be set anyway the Validation Webhook will reject the change with `Error: Hardware emulation is not yet implemented`.

Each value defines the **maximum fallback depth** allowed:

- **`None`** (default) — no emulation permitted. Only native KVM is allowed.
  A cross-arch VM with this setting will not start via any form of emulation —
  it must land on a node that natively runs the guest architecture. This is the
  safe default: enabling the feature gate does not automatically cause VMs to
  run under emulation unless explicitly opted in.
- **`Hardware`** — hardware cross-arch KVM is permitted, but TCG software
  emulation is not. The VM may use native KVM or hardware cross-arch KVM.
  It will **not** fall back to software emulation and fails to start on nodes
  where only software emulation is available.
- **`Software`** — the full fallback chain is permitted: native KVM > hardware
  cross-arch KVM > software emulation (TCG). KubeVirt selects the best
  available tier automatically. Also used as a per-VM override to loosen a
  cluster-wide `Hardware` policy.

The effective emulation tier is resolved at domain conversion time in
`KvmDomainConfigurator` using the following precedence (highest first):

1. **Per-VM** — `vmi.Spec.emulationPolicy` (if non-empty)
2. **Global** — `clusterConfig.GetEmulationPolicy()` (if non-empty)
3. **Default** — `None` (no emulation; native KVM only)

### Scheduling

Currently there are already 2 different scheduling methods, corresponding to `None` and `Software` tiers.

**None**: Simple node selector using `kubernetes.io/arch`.

**Software**: Introduced with VEP #172 during alpha, removes hard `kubernetes.io/arch` Node selector in
              favour of affinities with priorities Native > Hardware > Software.

To accomodate the case of allowing hardware emulation but not allowing software emulation, a new scheduling case is introduced:

**Hardware**: Removes hard `kubernetes.io/arch`. Uses affinities to select either native nodes or ones with hardware emulation.
              Uses the same affinities for priorization as **Software**.

Example affinity of virt-launcher pod for case **Hardware** affinities:
```yaml
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: kubernetes.io/arch
            operator: In
            values:
            - arm64
        - matchExpressions:
          - key: kubevirt.io/cross-arch-kvm-arm64
            operator: Exists
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
          - key: kubernetes.io/arch
            operator: In
            values:
            - arm64
      - weight: 50
        preference:
          matchExpressions:
          - key: kubevirt.io/cross-arch-kvm-arm64
            operator: EXists
```

## API Examples

Global configuration:
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
      - CrossArchitectureVirtualization
    emulationPolicy: Hardware
```

VM configuration:
```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: arm64-dev-vm
spec:
  architecture: arm64
  emulationPolicy: Software # Allows software emulation, overrides cluster policy
  runStrategy: Always
  template:
    spec:
      domain:
        machine:
          type: virt
        resources:
          requests:
            memory: 1Gi
      volumes:
      - name: containerdisk
        containerDisk:
          image: quay.io/containerdisks/fedora:40-aarch64
```

## Alternatives

- Using a binary enabled/disabled field
- Using global only configuration
- Using VM level only configuration

## Scalability

Not applicable

## Update/Rollback Compatibility

Downgrading to a previous version does not affect existing VMs, new VMs can't use the feature.

Upgrading does not affect existing or new VMs, unless the setting is changed.

## Functional Testing Approach

The design should be tested in unit-test.

Additionally e2e should cover both Software and None settings in e2e of VEP #172 to ensure the resulting pod affinities work as intended.

## Implementation History

- 2026-08-21: Initial VEP proposed

## Graduation Requirements

### Alpha
- [ ] New api fields are introduced
- [ ] unit-tests are available
- [ ] Changes have been tested locally

### Beta
- [ ] e2e test is added
- [ ] Hardware option is added after being implemented in VEP #172
- [ ] Emulation documentation has been updated

#### On-By-Default Readiness

### GA
- [ ] Software emulation is stable
- [ ] VEP #172 is also GA
