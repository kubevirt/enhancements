# VEP #419: Configurable VMExport External Certification Strategy

## VEP Status Metadata

### Target releases

- This VEP targets GA for version: 1.10

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

`VirtualMachineExport` publishes external download links in
`status.links.external`. Each link includes a PEM-encoded CA certificate in the
`cert` field so clients can validate TLS connections to the export endpoint
through the cluster ingress or route.

Today, the export controller always populates that certificate from cluster
trust material (`kube-root-ca.crt` for OpenShift Routes, or the Ingress TLS
secret for Kubernetes Ingress). That works on many clusters, but it is not
always correct: `kube-root-ca` makes no guarantee that the ingress or route
actually presents a certificate signed by that CA. On hosted platforms where
the edge certificate is signed by a public CA, publishing the cluster root CA
is misleading and can cause client verification failures.

This VEP adds cluster-level configuration on the `KubeVirt` CR so
administrators can choose how the `cert` field in external export links is
populated.

## Motivation

VMExport consumers (for example `virtctl vmexport download` and backup
integrations) rely on `status.links.external[].cert` to build a trust chain when
connecting to the export proxy through the cluster edge.

The current behavior assumes the edge certificate chains to the cluster root CA
(or can be read directly from the Ingress TLS secret). That assumption breaks
down in common deployment models:

- **Hosted / managed Kubernetes** where ingress certificates are signed by a
  public CA and clients should use their system trust store instead of a
  cluster-local root.
- **Custom private CAs** where the ingress or route certificate is signed by a
  CA that is not present in `kube-root-ca.crt` or the Ingress TLS secret, but
  is known to the platform administrator.

There is no reliable way for the export controller to infer the correct mode
from in-cluster state alone. Intermediate and leaf CAs, split-horizon DNS, and
platform-specific ingress configuration all make automatic detection unsafe.
Administrators therefore need an explicit, cluster-level knob.

## Goals

- Add `exportConfiguration.externalCertificationStrategy` to the `KubeVirt` CR
  with three supported values:
  - `ClusterRootCA` (default): preserve today's behavior.
  - `CustomRootCA`: publish certificates from a dedicated ConfigMap.
  - `SystemTrust`: leave the external `cert` field empty so clients use their
    system trust store.
- Apply the selected strategy consistently for both OpenShift Route and
  Kubernetes Ingress external link generation.
- Surface a `KubeVirt` status condition when `CustomRootCA` is selected but
  the required ConfigMap is missing or empty.
- Reconcile existing `VirtualMachineExport` objects when the strategy or
  backing ConfigMaps change.

## Non Goals

- Automatically detecting the correct strategy by comparing hostnames and CAs.
  This does not account for intermediate or leaf CAs and can select the wrong
  trust material.
- Probing external endpoints from the export controller to discover presented
  certificates. The controller must not reach out to unknown external endpoints.
- Implicit strategy selection based on ConfigMap presence. Presence-based
  behavior is hard to discover and troubleshoot; strategy selection must be
  explicit.
- OpenShift-specific APIs or Route status fields. The design must work on plain
  Kubernetes with Ingress as well as OpenShift with Routes.
- Changing internal export link certificates. This VEP only affects external
  links exposed through the cluster ingress or route.

## Definition of Users

- **Cluster administrators**: Configure how external export link certificates
  are published for their ingress or route setup.
- **Meta-operator developers** (for example HCO on OpenShift Virtualization):
  Expose the strategy through the platform operator and populate the KubeVirt CR
  on behalf of administrators.
- **VM owners and backup vendors**: Consume `status.links.external` and depend
  on accurate CA material when downloading exports through the cluster edge.

## User Stories

### Story 1: Public-CA signed ingress on a hosted cluster

As a cluster administrator running KubeVirt on a hosted platform whose ingress
is signed by a public CA, I want external export links to omit the `cert` field
so clients verify the endpoint using their system trust store.

### Story 2: Private CA not in kube-root-ca

As a cluster administrator whose ingress or route uses a private CA that is not
present in `kube-root-ca.crt`, I want to provide that CA in a ConfigMap and
have VMExport external links publish it.

### Story 3: Preserve existing behavior

As a cluster administrator on a cluster where `kube-root-ca` or the Ingress TLS
secret already provides the correct trust material, I want the default behavior
to remain unchanged when I do not configure anything.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### API Change

Add `ExportConfiguration` to `KubeVirtConfiguration`:

```go
type ExportConfiguration struct {
    // ExternalCertificationStrategy controls how VirtualMachineExport external
    // link certificates are populated.
    // Defaults to ClusterRootCA.
    // +optional
    // +kubebuilder:validation:Enum=ClusterRootCA;CustomRootCA;SystemTrust
    ExternalCertificationStrategy *ExternalCertificationStrategy `json:"externalCertificationStrategy,omitempty"`
}

type ExternalCertificationStrategy string

const (
    ExternalCertificationStrategyClusterRootCA ExternalCertificationStrategy = "ClusterRootCA"
    ExternalCertificationStrategyCustomRootCA  ExternalCertificationStrategy = "CustomRootCA"
    ExternalCertificationStrategySystemTrust   ExternalCertificationStrategy = "SystemTrust"
)
```

The field lives under `spec.configuration.exportConfiguration` so additional
export-related cluster settings can be grouped here in the future.

### Strategy Semantics

| Strategy | Route source | Ingress source | `status.links.external.cert` |
|----------|--------------|----------------|------------------------------|
| `ClusterRootCA` (default) | `kube-root-ca.crt` ConfigMap (`ca.crt` key) | Ingress TLS secret (`tls.crt`) | Matching PEM chain, or best-effort chain from available certs |
| `CustomRootCA` | `kubevirt-export-external-ca` ConfigMap (`ca.crt` key) | same ConfigMap | PEM chain from ConfigMap; empty if ConfigMap missing or empty |
| `SystemTrust` | n/a | n/a | Empty string |

For `ClusterRootCA` and `CustomRootCA`, the controller selects the best matching
certificate for the export hostname from the configured PEM bundle, including
intermediate CAs when present. If no hostname match is found, it publishes the
available certificates and logs a warning.

For `SystemTrust`, the hostname is still published in the external link; only
the `cert` field is omitted so clients rely on the system trust store. This
matches deployments where the edge presents a publicly trusted certificate.

### CustomRootCA ConfigMap

`CustomRootCA` reads from a ConfigMap named `kubevirt-export-external-ca` in
the KubeVirt install namespace. The ConfigMap is optional and not created by
virt-operator; administrators (or a meta-operator) create it when needed.

Expected format:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubevirt-export-external-ca
  namespace: kubevirt
data:
  ca.crt: |
    -----BEGIN CERTIFICATE-----
    ...
    -----END CERTIFICATE-----
```

The `ca.crt` key matches the key used by `kube-root-ca.crt`.

### Status Condition

When `externalCertificationStrategy` is `CustomRootCA` and the
`kubevirt-export-external-ca` ConfigMap is missing or its `ca.crt` entry is
empty, virt-controller sets a `KubeVirt` condition:

- **Type**: `ExportCustomExternalCA`
- **Status**: `False`
- **Reason**: `ExportExternalCAConfigMapMissing`

The condition is removed when the ConfigMap becomes available or when the
strategy is changed away from `CustomRootCA`. While misconfigured, external
export link certificates remain blank.

### Controller Reconciliation

The VMExport controller in virt-controller:

1. Reads the active strategy from cluster configuration via the KubeVirt CR.
2. Watches the KubeVirt CR, `kube-root-ca.crt`, and
   `kubevirt-export-external-ca` for changes.
3. Reconciles the `ExportCustomExternalCA` condition on KubeVirt status.
4. Requeues all `VirtualMachineExport` objects when the strategy or relevant
   ConfigMaps change so `status.links.external` is refreshed.

Internal export links (cluster-local service access) are unchanged.

### Feature Gate

No feature gate is required. The default strategy is `ClusterRootCA`, which
preserves existing behavior. Administrators opt in to new behavior by setting
the field explicitly.

### Meta-Operator Integration

KubeVirt does not discover ingress or route TLS configuration from
platform-specific APIs. Meta-operators such as HCO should expose the strategy to
platform administrators and write
`spec.configuration.exportConfiguration.externalCertificationStrategy` on the
KubeVirt CR. Hosted OpenShift deployments are expected to set `SystemTrust`
when the apps ingress uses a public CA.

## API Examples

### System trust (hosted cluster with public-CA ingress)

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    exportConfiguration:
      externalCertificationStrategy: SystemTrust
```

Resulting external link excerpt:

```yaml
status:
  links:
    external:
      - url: https://virt-exportproxy-kubevirt.apps.example.com/...
        cert: ""
```

### Custom root CA

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  configuration:
    exportConfiguration:
      externalCertificationStrategy: CustomRootCA
```

With companion ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubevirt-export-external-ca
  namespace: kubevirt
data:
  ca.crt: |
    -----BEGIN CERTIFICATE-----
    MIIB...
    -----END CERTIFICATE-----
```

### Default (unchanged clusters)

Omitting `exportConfiguration` or leaving `externalCertificationStrategy` unset
selects `ClusterRootCA` and preserves the current behavior.

## Alternatives

### Match hostname and CA automatically

Inspect available CAs and only publish a certificate when it matches the export
hostname.

**Rejected** because it ignores intermediate and leaf CAs and can publish the
wrong trust anchor.

### Probe the external endpoint

Have the export controller connect to the ingress or route URL and read the
presented certificate.

**Rejected** because the controller would need to reach unknown external
endpoints, which is undesirable from a security and networking standpoint.

### Implicit CustomRootCA when ConfigMap exists

Use `kubevirt-export-external-ca` automatically when present, otherwise fall
back to `ClusterRootCA`.

**Rejected** because implicit behavior is difficult to discover. The ConfigMap
is still used for `CustomRootCA`, but administrators must select that strategy
explicitly.

## Scalability

Minimal impact. The strategy is read from the KubeVirt CR on each external
link computation. ConfigMap watches are limited to two fixed names in the
KubeVirt install namespace. Reconciliation of all VMExports on strategy change
is proportional to the number of existing export objects and matches existing
KubeVirt certificate rotation requeue behavior.

## Update/Rollback Compatibility

**Upgrade:**

- Existing clusters with no `exportConfiguration` continue to use
  `ClusterRootCA`.
- Setting `SystemTrust` or `CustomRootCA` takes effect on the next VMExport
  reconciliation; existing exports are requeued when the KubeVirt CR or
  ConfigMaps change.

**Downgrade:**

- If the cluster is downgraded to a version without this API field, the
  controller reverts to always using `ClusterRootCA` behavior.
- `exportConfiguration` in the KubeVirt CR is preserved by Kubernetes but
  ignored by older operators.

**Version skew:**

- During a rolling upgrade, virt-controller may refresh external links before
  or after other components. External link content is owned by virt-controller
  and is eventually consistent.

## Functional Testing Approach

### Unit Tests

- Strategy selection defaults and explicit values in cluster configuration.
- External link certificate population for Route and Ingress under each
  strategy.
- `ExportCustomExternalCA` condition set, cleared, and updated on ConfigMap and
  strategy changes.
- VMExport requeue when `exportConfiguration` changes.

### Functional Tests

- VMExport external links with default `ClusterRootCA` on clusters using Ingress
  or Route.
- VMExport external links with `SystemTrust` (empty `cert` field).
- VMExport external links with `CustomRootCA` and a populated
  `kubevirt-export-external-ca` ConfigMap.
- Condition reporting when `CustomRootCA` is selected without the ConfigMap.

## Implementation History

- 2026-07-30: Initial implementation. PR: [kubevirt/kubevirt#18652](https://github.com/kubevirt/kubevirt/pull/18652)

## Graduation Requirements

### GA

- [ ] `exportConfiguration.externalCertificationStrategy` added to the KubeVirt
  CR with enum validation (`ClusterRootCA`, `CustomRootCA`, `SystemTrust`)
- [ ] Default strategy is `ClusterRootCA` when unset
- [ ] External VMExport links honor the strategy for OpenShift Routes and
  Kubernetes Ingress
- [ ] `kubevirt-export-external-ca` ConfigMap supported for `CustomRootCA`
- [ ] `ExportCustomExternalCA` condition reported on the KubeVirt CR when
  `CustomRootCA` is misconfigured
- [ ] VMExports requeued when strategy or backing ConfigMaps change
- [ ] Unit and functional tests pass
- [ ] Meta-operator documentation for hosted and custom-CA deployments
- [ ] User-guide documentation for configuring external export trust
- [ ] User-guide documents all three strategies and the custom CA ConfigMap
