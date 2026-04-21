# VEP #287: TLS Group Preferences

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9.0
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue [#287](https://github.com/kubevirt/enhancements/issues/287) created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP adds a `Groups` field to KubeVirt's `TLSConfiguration` API, allowing
cluster administrators to configure TLS supported groups (elliptic curves)
negotiated during TLS handshakes across all virt pod endpoints. This enables
Post-Quantum Cryptography (PQC) readiness by supporting hybrid key exchange
groups such as `X25519MLKEM768`.

## Motivation

The transition to Post-Quantum Cryptography (PQC) is underway. NIST has
finalised its first set of PQC standards and IETF has standardised hybrid key
exchange groups such as `X25519MLKEM768` that combine classical and
post-quantum algorithms. Go 1.24 (which KubeVirt already uses) includes native
support for `X25519MLKEM768` as a `tls.CurveID`.

KubeVirt's existing `TLSConfiguration` supports `MinTLSVersion` and `Ciphers`
but has no mechanism to control which key exchange groups (elliptic curves) are
offered during TLS negotiation. Without this, KubeVirt components use Go's
default curve preferences, which means administrators cannot:

- Enforce specific PQC-ready groups across all virt pod endpoints
- Restrict curves to a known-safe set for compliance or FIPS requirements
- Align KubeVirt's TLS behaviour with platform-wide or organisational TLS
  policies

### Why Not Automatically Enable PQC Groups?

Go 1.24 already includes `X25519MLKEM768` in its default curve preferences, so
KubeVirt components negotiate PQC key exchange automatically when both sides
support it. However, an explicit API field is still needed because:

- **Compliance restrictions**: PQC groups such as `X25519MLKEM768` are not yet
  FIPS-approved. Deployments with FIPS requirements must be able to explicitly
  exclude them.
- **Performance trade-offs**: PQC key exchanges have larger key sizes and
  higher computational cost. Administrators may need to control which groups
  are offered based on their workload profile.
- **Platform alignment**: Kubernetes platforms are beginning to expose TLS
  group configuration in their cluster-wide TLS profiles. KubeVirt needs a
  corresponding API so that meta-operators can propagate these settings into
  the KubeVirt CR, ensuring all virt pod endpoints participate in cluster-wide
  TLS policy rollouts.

## Goals

- Extend `TLSConfiguration` with a `Groups` field to configure TLS supported
  groups
- Apply the configured groups as `CurvePreferences` on all TLS server endpoints
  in virt-api, virt-controller, virt-handler, virt-operator,
  virt-exportproxy, virt-exportserver, virt-synchronization-controller,
  virt-template-apiserver, and virt-template-controller
- Validate group names in the KubeVirt CR admission webhook
- Maintain backward compatibility — omitting `Groups` preserves current Go
  default behaviour

## Non Goals

- Watching platform-specific TLS profile resources directly from virt-operator
  — meta-operators (e.g. HCO) handle this translation
- Configuring TLS groups on client-side connections — this VEP covers
  server-side TLS endpoints only. Server-side configuration is sufficient to
  control which group is actually negotiated: the server selects the group
  from the client's offered set based on its `CurvePreferences`. Since all
  KubeVirt components are Go programs, the client side already offers Go's
  full default set (including PQC groups in Go 1.24+), so the server can
  restrict or prioritise groups without client-side changes. Client-side
  configuration would only be needed to control what appears in the
  ClientHello itself (e.g. to avoid advertising PQC groups for strict
  compliance, or to eliminate HelloRetryRequest round trips when the server
  does not support a group the client sent a key share for).
- Configuring certificate key curves (e.g. changing the hardcoded P-256 in
  `pkg/certificates/triple/cert/cert.go`) — the certificate signing key is
  independent of the key exchange groups negotiated during the TLS handshake.
  This would require a separate API field and should be a separate VEP.
- TLS adherence policy integration — some platforms define a TLS adherence
  policy mechanism that reports whether components comply with the configured
  TLS profile. Integrating with such mechanisms is out of scope for this VEP.

## Definition of Users

- **Cluster administrators**: Configure TLS groups via the KubeVirt CR
  (`spec.configuration.tlsConfiguration.groups`) to enforce curve preferences
  across all virt pod endpoints. On managed platforms, a meta-operator
  typically populates this from the platform's cluster-wide TLS profile.
- **Meta-operator developers**: Read the platform's TLS profile resource and
  translate it into KubeVirt's `tlsConfiguration.groups`.

## User Stories

### Story 1: Enable PQC Hybrid Key Exchange

As a cluster administrator, I want to configure TLS groups on my KubeVirt
deployment to include `X25519MLKEM768` so that all virt pod endpoints negotiate
PQC-ready key exchanges when clients support them.

### Story 2: Restrict Curves for Compliance

As a security officer, I want to restrict TLS groups to a known-safe subset
(e.g. only `X25519` and `secp256r1`) so that non-approved curves are never
negotiated, meeting our compliance requirements.

### Story 3: Cluster-Wide TLS Profile Alignment

As a platform administrator, I configure TLS groups in the cluster-wide TLS
security profile. A meta-operator propagates these to the KubeVirt CR, and all
virt pod endpoints honour the same group preferences as the rest of the
cluster.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Feature Gate

All changes are gated behind the `TLSGroupPreferences` feature gate (disabled
by default). When the feature gate is disabled, the `Groups` field on
`TLSConfiguration` is ignored and Go's default curve preferences apply.

The gate allows controlled rollout and easy rollback: if a misconfiguration
causes TLS issues, disabling the gate immediately restores Go defaults without
needing to clear the `groups` field from the CR. It also allows meta-operators
to enable the feature in lockstep with their platform's TLS group support.

### API Change

Add a `Groups` field to `TLSConfiguration` in
`staging/src/kubevirt.io/api/core/v1/types.go`:

```go
type TLSConfiguration struct {
    // ...existing fields...
    // +optional
    // +listType=set
    Groups []string `json:"groups,omitempty"`
}
```

The `Groups` field uses `[]string` (matching the existing `Ciphers` pattern)
rather than a typed enum. Group names use IANA TLS Supported Groups registry
names (e.g. `X25519`, `secp256r1`, `X25519MLKEM768`), validated at webhook
time against Go's `crypto/tls` supported curves via a `CurvePreferenceNameMap`.

This approach avoids requiring an API change when Go adds support for new
groups (e.g. `SecP256r1MLKEM768` in a future Go version) and is consistent
with how `Ciphers` already works in KubeVirt. It also accommodates builds
that link alternative `crypto/tls` implementations.

> **Note:** Go does not currently provide a public API for translating
> curve/group names to `tls.CurveID` values (unlike cipher suites which have
> `tls.CipherSuites()`). This requires maintaining a manual
> `CurvePreferenceNameMap` in KubeVirt until Go provides an equivalent. There
> is an upstream Go proposal to address this:
> [golang/go#77712](https://github.com/golang/go/issues/77712). Once that
> lands, KubeVirt can replace the manual map with Go's own API, eliminating the
> maintenance burden. This is tracked as a Beta graduation requirement.

### TLS Setup Changes

All TLS server setup functions in `pkg/util/tls/tls.go` already read
`getTLSConfiguration(kv)` and set `CipherSuites` and `MinVersion` on
`tls.Config`. Each function needs one additional line to set
`CurvePreferences`:

```go
kv := clusterConfig.GetConfigFromKubeVirtCR()
tlsConfig := getTLSConfiguration(kv)
ciphers := CipherSuiteIds(tlsConfig.Ciphers)
minTLSVersion := TLSVersion(tlsConfig.MinTLSVersion)
curvePreferences := CurvePreferenceIds(tlsConfig.Groups)
config := &tls.Config{
    CipherSuites:     ciphers,
    MinVersion:       minTLSVersion,
    CurvePreferences: curvePreferences,
    // ...
}
```

The affected functions are:

| Function | Components | File Location |
|----------|-----------|---------------|
| `SetupPromTLS` | virt-controller, virt-handler, virt-operator | `pkg/util/tls/tls.go:29` |
| `SetupExportProxyTLS` | virt-exportproxy | `pkg/util/tls/tls.go:64` |
| `SetupTLSWithCertManager` | virt-api, virt-operator | `pkg/util/tls/tls.go:97` |
| `SetupTLSForServer` | virt-handler, virt-synchronization-controller | `pkg/util/tls/tls.go:173` |
| `buildServer` | virt-exportserver | `pkg/storage/export/virt-exportserver/exportserver.go:261` |

When `Groups` is empty, `CurvePreferenceIds` returns `nil` and
`CurvePreferences` is left unset, preserving Go's default behaviour (currently
`X25519`, `secp256r1`, `secp384r1`, `secp521r1`, `X25519MLKEM768` in Go 1.24).

#### virt-exportserver

The virt-exportserver binary is a special case. Unlike the other components, it
does not have access to the KubeVirt CR at runtime. Instead,
`VMExportController.appendTLSEnvVars()`
(`pkg/storage/export/export/export.go:1724`) reads `TLSConfiguration` from
the KubeVirt CR and injects `TLS_MIN_VERSION` and `TLS_CIPHER_SUITES`
environment variables into the export server pod at creation time.

The `buildServer()` method (`exportserver.go:261`) then constructs a static
`tls.Config` from these values. This path requires:

1. A new `TLSCurvePreferences []tls.CurveID` field on `ExportServerConfig`
   (`exportserver.go:102`)
2. `buildServer()` sets `CurvePreferences` on the `tls.Config`
3. A new `TLS_CURVE_PREFERENCES` environment variable read by
   `cmd/virt-exportserver/virt-exportserver.go`
4. `appendTLSEnvVars()` injects `TLS_CURVE_PREFERENCES` from the KubeVirt CR's
   `tlsConfiguration.groups` field, using `CurvePreferenceIds` to convert
   group names to `tls.CurveID` values

### Curve Name to ID Mapping

Add `CurvePreferenceIds` and `CurvePreferenceNameMap` functions in
`pkg/util/tls/tls.go`, analogous to the existing `CipherSuiteIds` and
`CipherSuiteNameMap`. `CurvePreferenceNameMap` maps IANA group names to
`tls.CurveID` values and is used by both the TLS setup functions and the
admission webhook for validation.

### Deployment Injection

Extend `InjectTLSConfigIntoDeployment` (`pkg/util/tls/tls.go:264`) to inject a
`--tls-groups` flag for virt-template components, matching the existing pattern
for `--tls-cipher-suites` and `--tls-min-version`. The virt-template
components currently do not accept a `--tls-groups` flag, so the flag must
be added to their startup code as part of this work.

### Validation

Extend `validateTLSConfiguration` in
`pkg/virt-operator/webhooks/kubevirt-update-admitter.go` to:

1. **Validate group names** against the `CurvePreferenceNameMap` (dynamically
   built from Go's `crypto/tls` supported curves), rejecting unknown names.
2. **Validate TLS version and group compatibility** — PQC groups such as
   `X25519MLKEM768` are TLS 1.3-only (Go filters them out for TLS 1.2
   connections). If `MinTLSVersion` is below TLS 1.3 and `Groups` contains
   only PQC groups, TLS 1.2 handshakes would fail with an empty curve list.
   The webhook must reject this, requiring at least one classical group
   (`X25519`, `secp256r1`, `secp384r1`, or `secp521r1`) when `MinTLSVersion`
   is below `VersionTLS13`.

### Meta-Operator Integration

KubeVirt does **not** watch platform-specific TLS profile resources directly.
The integration model (unchanged from existing `ciphers`/`minTLSVersion`
handling) is:

1. Platform administrator configures the cluster-wide TLS profile
2. A meta-operator watches the platform TLS profile resource and translates it
   into `kubevirt.spec.configuration.tlsConfiguration.groups`
3. KubeVirt components dynamically pick up the new groups via their existing
   `GetConfigForClient` callbacks

Note that virt-template components receive TLS configuration via command-line
flags injected by virt-operator at deployment time. Changing the `groups`
field triggers a deployment update and pod restart for these components, unlike
the other components which pick up changes dynamically.

## API Examples

### KubeVirt CR with TLS Groups

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
        - TLSGroupPreferences
    tlsConfiguration:
      minTLSVersion: VersionTLS12
      ciphers:
        - TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256
        - TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256
      groups:
        - X25519
        - secp256r1
        - secp384r1
        - X25519MLKEM768
```

When `groups` is omitted, `CurvePreferences` is left unset on `tls.Config`
and Go uses its built-in defaults.

## Alternatives

### Direct Platform TLS Profile Watching

Have virt-operator watch a platform-specific TLS profile resource directly
and apply the TLS configuration.

**Rejected because:**

- Introduces a direct dependency on platform-specific APIs in upstream KubeVirt
- Duplicates the meta-operator's role — meta-operators already translate
  platform configuration into KubeVirt CR fields
- Breaks deployments on platforms that do not provide such a resource

### Enum-Typed Groups

Use a `TLSGroup` enum type with kubebuilder validation instead of `[]string`.

**Rejected because:**

- Requires an API change every time Go adds new group support
- Doesn't accommodate builds linking alternative `crypto/tls` implementations
- Inconsistent with how `Ciphers []string` already works in KubeVirt
- Webhook validation against Go's runtime `crypto/tls` provides the same
  safety without the maintenance burden

### No Feature Gate

Add the `Groups` field without a feature gate since it's optional and
backward-compatible.

**Rejected because:**

- Allows controlled rollout and easy rollback
- Allows meta-operators to enable the feature in lockstep with their
  platform's TLS group support
- Follows KubeVirt's feature lifecycle conventions

## Scalability

No scalability impact. The group configuration is read once per TLS handshake
via the existing `GetConfigForClient` callback, which already reads cipher and
version configuration. The mapping from group names to `tls.CurveID` values
is O(n) over a list of at most 5 entries.

## Update/Rollback Compatibility

**Upgrade:**

- Existing deployments with no `groups` field continue to work unchanged —
  Go defaults apply
- Adding groups to an existing `tlsConfiguration` takes effect dynamically
  via `GetConfigForClient` callbacks for most components. virt-template
  components receive groups via command-line flags and require a deployment
  update (handled automatically by virt-operator).

**Downgrade:**

- If the `TLSGroupPreferences` feature gate is disabled, the `groups` field is
  ignored and Go defaults apply
- No impact on running connections; new handshakes use defaults

**Version Skew:**

- During upgrade, components at different versions may have different group
  support. Components that do not yet understand the `groups` field will use
  Go defaults, which include all the same groups — so there is no security
  regression during rolling updates.
- virt-template components receive TLS configuration via `--tls-groups` flags
  injected by virt-operator. During a rolling upgrade, a new virt-operator may
  inject the `--tls-groups` flag into a deployment running old virt-template
  pods that do not recognise the flag. virt-operator already handles this by
  triggering a rolling deployment update, so old pods are replaced with new
  ones that understand the flag.
- virt-exportserver pods are ephemeral and created on-demand by
  virt-controller with TLS configuration injected as environment variables at
  pod creation time. During a rolling upgrade, a new virt-controller may
  inject the `TLS_CURVE_PREFERENCES` env var into a pod running an old
  virt-exportserver binary — the old binary simply ignores the unknown env
  var and uses Go defaults. Conversely, a new virt-exportserver binary
  created by an old virt-controller will not receive the env var and also
  falls back to Go defaults.

## Functional Testing Approach

### Unit Tests

`pkg/util/tls/tls_test.go`:

- `CurvePreferenceIds` returns correct `tls.CurveID` values for each group
- `CurvePreferenceIds` returns nil for empty input
- `CurvePreferenceIds` skips unknown group names
- Validation rejects invalid group names
- Validation accepts all valid group names

### Functional Tests

Extend `tests/infrastructure/tls-configuration.go`:

- Configure `groups` on the KubeVirt CR
- Verify TLS connections to virt-api, virt-handler, virt-exportproxy,
  virt-template-apiserver, and virt-template-controller negotiate using the
  configured groups
- Verify that a client offering only a non-configured curve (where the
  configured set excludes it) is rejected or falls back appropriately
- Verify that omitting `groups` preserves default behaviour

## Implementation History

## Graduation Requirements

### Alpha

- [ ] Feature gate `TLSGroupPreferences` guards all code changes (disabled by
  default)
- [ ] `Groups []string` field added to `TLSConfiguration`
- [ ] `CurvePreferenceIds` mapping function in `pkg/util/tls/tls.go`
- [ ] `CurvePreferences` set on `tls.Config` in all TLS setup functions
- [ ] `--tls-groups` flag added to virt-template components
- [ ] `InjectTLSConfigIntoDeployment` extended to inject `--tls-groups`
- [ ] Admission webhook validation for group names and TLS version
  compatibility
- [ ] Unit tests for mapping, validation, and TLS setup
- [ ] Functional tests verifying group enforcement on virt pod endpoints
  (including virt-template components)

### Beta

- [ ] Adopt upstream Go API for curve/group name resolution
  ([golang/go#77712](https://github.com/golang/go/issues/77712)) if available,
  replacing the manual `CurvePreferenceNameMap`

### GA
