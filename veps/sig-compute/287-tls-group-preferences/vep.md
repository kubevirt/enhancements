# VEP #287: TLS Group Preferences

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: N/A (alpha skipped — see *Feature Gate* under Design)
- This VEP targets beta for version: v1.10.0
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue [#287](https://github.com/kubevirt/enhancements/issues/287) created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [X] (R) Alpha target version is explicitly mentioned and approved (N/A — alpha skipped, see *Feature Gate* under Design)
- [X] (R) Beta target version is explicitly mentioned and approved
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
post-quantum algorithms. Go 1.24 added native support for `X25519MLKEM768` as a
`tls.CurveID`, and the Go 1.26 toolchain KubeVirt now builds with additionally
exposes the `SecP256r1MLKEM768` and `SecP384r1MLKEM1024` hybrid groups.

KubeVirt's existing `TLSConfiguration` supports `MinTLSVersion` and `Ciphers`
but has no mechanism to control which key exchange groups (elliptic curves) are
offered during TLS negotiation. Without this, KubeVirt components use Go's
default curve preferences, which means administrators cannot:

- Enforce specific PQC-ready groups across all virt pod endpoints
- Restrict curves to a known-safe set for compliance or FIPS requirements
- Align KubeVirt's TLS behaviour with platform-wide or organisational TLS
  policies

### Platform Alignment

Some platforms mandate that every TLS endpoint in every container support
configurable TLS groups (curves). This covers **all** endpoints, including
metrics/Prometheus endpoints and admission webhooks, not just the primary API
surfaces. KubeVirt must expose a `Groups` field on its existing
`TLSConfiguration` so that a meta-operator can reconcile it from the
cluster-wide TLS profile, mirroring how `ciphers` and `minTLSVersion` are
already handled.

Because a meta-operator can only adopt the API change once a KubeVirt release
exposes the field, landing `Groups` in KubeVirt early is a prerequisite for the
wider rollout. This is the primary reason for prioritising it in v1.10.0.

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
- Enforce TLS-version/group compatibility via a declarative CEL validation on
  `TLSConfiguration`, tolerating unrecognised group names gracefully
- Maintain backward compatibility — omitting `Groups` preserves current Go
  default behaviour

## Non Goals

- Watching platform-specific TLS profile resources directly from virt-operator
  — meta-operators handle this translation
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

All changes are gated behind the `TLSGroupPreferences` feature gate, introduced
directly at **Beta** (Alpha is skipped — see below). Per KubeVirt's
feature-gate lifecycle a Beta gate is enabled by default and can be explicitly
disabled; when disabled, `Groups` is ignored and Go's default curve preferences
apply. The gate is a controlled off switch: if a misconfiguration causes TLS
issues, disabling it restores Go defaults without editing the CR's `groups`
field.

#### Skipping Alpha

The VEP process permits entering above Alpha when the design maturity and risk
justify it. Both hold here, and Beta (rather than a direct GA) still keeps an
off switch and one release of soak before the gate is removed at GA:

- **Additive extension of an already-GA, ungated API.** `Groups` is a third
  optional field on `TLSConfiguration` beside the GA `MinTLSVersion` and
  `Ciphers`, reusing their existing plumbing. An unset field is byte-for-byte
  the current default, so the blast radius is zero.
- **Proven API shape.** An ordered, open list of IANA group names is the same
  contract exposed by mature projects — Prometheus exporter-toolkit's
  `tls_server_config.curve_preferences`
  ([prometheus/exporter-toolkit](https://github.com/prometheus/exporter-toolkit))
  and Envoy's `tls_params.ecdh_curves`
  ([Envoy](https://www.envoyproxy.io/docs/envoy/latest/api-v3/extensions/transport_sockets/tls/v3/common.proto)),
  both wrapping the same Go `crypto/tls` `CurvePreferences` primitive KubeVirt
  uses. Adoption by established projects gives the confidence an Alpha soak
  would otherwise provide.
- **A real platform will adopt it.** OpenShift — a fully open-source, midstream
  platform — exposes a cluster-wide `TLSSecurityProfile`
  ([`config.openshift.io/v1`](https://github.com/openshift/api/blob/master/config/v1/types_tlssecurityprofile.go))
  that a meta-operator already reconciles into KubeVirt's
  `ciphers`/`minTLSVersion`, and will reconcile into `groups` the same way.
  Adoption by an established platform is itself assurance the API is sound, and
  an off-by-default Alpha gate cannot be relied on by such a shipping platform
  whereas an on-by-default Beta gate can.

### API Change

Add a `Groups` field to `TLSConfiguration` in
`staging/src/kubevirt.io/api/core/v1/types.go` as an open string set:

```go
// TLSConfiguration holds TLS options
// +kubebuilder:validation:XValidation:rule="!has(self.groups) || size(self.groups) == 0 || (has(self.minTLSVersion) && self.minTLSVersion == 'VersionTLS13') || !self.groups.exists(g, g in ['X25519MLKEM768','SecP256r1MLKEM768','SecP384r1MLKEM1024']) || self.groups.exists(g, g in ['X25519','secp256r1','secp384r1','secp521r1'])",message="a classical group (X25519, secp256r1, secp384r1 or secp521r1) is required in groups when minTLSVersion is below VersionTLS13 and a TLS 1.3-only group such as X25519MLKEM768 is configured"
type TLSConfiguration struct {
    // ...existing fields...
    // +optional
    // +listType=set
    Groups []string `json:"groups,omitempty"`
}
```

Group names use IANA TLS Supported Groups registry names (e.g. `X25519`,
`secp256r1`, `X25519MLKEM768`). The names KubeVirt currently recognises are
provided as convenience string constants (`TLSGroupX25519`, …,
`TLSGroupSecP384r1MLKEM1024`) rather than a schema-enforced enumeration.

The field is deliberately an **open string set** rather than a typed enum, in
line with the KubeVirt API design guidelines
([kubevirt/kubevirt#18612](https://github.com/kubevirt/kubevirt/pull/18612),
§3.2 "Open String Set (Avoiding Enums)"). A hard OpenAPI enum on an
extensible-choice field is a rolling-upgrade hazard: a newer component that
writes a newly-standardised group name would be rejected by an older
apiserver's enum, so the guidelines require such fields to be modelled as open
`string` primitives (§3.2.2). This also means a group added by a future Go
release needs no API change — the constants and the mapping switch are simply
extended.

Unrecognised group names are ingested without error and ignored at TLS setup
time (see *Curve Name to ID Mapping* below), following the "Graceful Rejection"
guidance in §3.2.3 — unknown values must not crash or hard-fail the object.

The one cross-field constraint that must still be enforced (a recognised
classical group is required when `MinTLSVersion` is below TLS 1.3 **and** a
recognised TLS 1.3-only group is configured) is expressed declaratively as a
CEL `x-kubernetes-validations` rule on `TLSConfiguration`, per §4.2 which
prefers CEL (`XValidation`) over imperative admission webhooks for cross-field
validation. See *Validation* below.

> **Note:** Because `Groups` is an open string set, the mapping from a group
> name to its `tls.CurveID` in `CurvePreferenceIds` is a small explicit switch
> over the recognised names; any name not in the switch is silently skipped.

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

#### Endpoint Coverage

The platform alignment goal requires **every** TLS endpoint in every
container to honour the configured groups, explicitly including
metrics/Prometheus endpoints and admission webhooks — not just the primary API
surfaces. The functions above are the complete set of TLS server-config
construction points in KubeVirt, so applying `CurvePreferences` in each covers
all endpoints:

- **Metrics/Prometheus** endpoints are served via `SetupPromTLS`
  (virt-controller, virt-handler, virt-operator).
- **Admission/mutating webhooks** and the aggregated API are served via
  `SetupTLSWithCertManager` (virt-api, virt-operator).
- **virt-handler / synchronization-controller** server endpoints via
  `SetupTLSForServer`; **exportproxy** via `SetupExportProxyTLS`;
  **exportserver** via `buildServer`; **virt-template** components via the
  injected `--tls-groups` flag.

All KubeVirt TLS endpoints are served directly by the virt pods listed above;
KubeVirt has no separately-managed operand endpoints that construct their own
`tls.Config` outside these functions. The implementation should confirm
this list is exhaustive (e.g. by grepping for `tls.Config` construction across
the tree) so no endpoint is missed.

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

Add the following helper in `pkg/util/tls/tls.go`:

- `CurvePreferenceIds([]string) []tls.CurveID` — converts the configured group
  names to `tls.CurveID` values via an explicit switch over the recognised
  names, used by the TLS setup functions. Unrecognised names are skipped, so an
  older component tolerates group names added in a newer release (§3.2.3).

No `ValidTLSGroup`/`IsTLS13OnlyGroup` helpers are needed: unknown names are
ignored rather than rejected, and the TLS-version/group compatibility
constraint is enforced declaratively via CEL rather than in Go.

### Deployment Injection

Extend `InjectTLSConfigIntoDeployment` (`pkg/util/tls/tls.go:264`) to inject a
`--tls-groups` flag for virt-template components, matching the existing pattern
for `--tls-cipher-suites` and `--tls-min-version`. The virt-template
components currently do not accept a `--tls-groups` flag, so the flag must
be added to their startup code as part of this work.

### Validation

Group validation is **declarative**, not imperative, in line with #18612 §4.2
(prefer CEL over admission webhooks for cross-field validation) and §3.2.3
(graceful handling of unrecognised values):

1. **Unknown group names are not rejected.** They are ingested without error and
   ignored at TLS setup time by `CurvePreferenceIds`, so an older component
   tolerates group names written by a newer one during a rolling upgrade.
2. **TLS version and group compatibility** is enforced by a CEL
   `x-kubernetes-validations` rule on `TLSConfiguration` (the `XValidation`
   marker shown under *API Change*). PQC groups such as `X25519MLKEM768` are
   TLS 1.3-only (Go filters them out for TLS 1.2 connections); if
   `MinTLSVersion` is below TLS 1.3 and, after unrecognised names are skipped,
   the only mappable groups are TLS 1.3-only, a TLS 1.2 handshake would fail
   with an empty curve list. The CEL rule rejects this at admission: when
   `MinTLSVersion` is below `VersionTLS13` **and** `Groups` contains a
   recognised TLS 1.3-only group (`X25519MLKEM768`, `SecP256r1MLKEM768`,
   `SecP384r1MLKEM1024`), it requires at least one recognised classical group
   (`X25519`, `secp256r1`, `secp384r1`, or `secp521r1`) to also be present.

   The rule checks for the *presence of a recognised classical group* rather
   than the *absence of a PQC group*. A negative check (`!(g in [PQC...])`)
   would be satisfied by any unrecognised name — e.g. `Groups: [abcd,
   X25519MLKEM768]` with `MinTLSVersion: VersionTLS12` would wrongly pass, yet
   `abcd` is skipped at TLS setup, leaving only the TLS 1.3-only group and the
   empty-curve failure the rule is meant to prevent. Gating the requirement on
   a recognised PQC group being present (rather than firing whenever no
   classical group is found) means a config that contains only unrecognised
   names is **not** hard-failed, keeping §3.2.3 graceful handling intact —
   such names are skipped at runtime and Go's defaults apply. The residual gap
   is a *newly-standardised* TLS 1.3-only group not yet in the CEL list; this
   errs on the side of permitting rather than falsely rejecting future names,
   consistent with the open-string-set rationale under *API Change*.

No group-specific logic is added to `validateTLSConfiguration` in
`pkg/virt-operator/webhooks/kubevirt-update-admitter.go`.

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

The `TLSGroupPreferences` gate is on by default at Beta, so no
`developerConfiguration.featureGates` entry is needed to use `groups`;
explicitly disabling the gate makes `groups` ignored and Go defaults apply.

## Alternatives

### Direct Platform TLS Profile Watching

Have virt-operator watch a platform-specific TLS profile resource directly
and apply the TLS configuration.

**Rejected because:**

- Introduces a direct dependency on platform-specific APIs in upstream KubeVirt
- Duplicates the meta-operator's role — meta-operators already translate
  platform configuration into KubeVirt CR fields
- Breaks deployments on platforms that do not provide such a resource

### Typed `TLSGroup` Enum

Model `Groups` as a typed `[]TLSGroup` with a hard
`+kubebuilder:validation:Enum` covering the recognised group names, giving
apiserver-level rejection of unknown values and a discoverable value set.

**Rejected because:**

- A hard OpenAPI enum on an extensible-choice field is disallowed by the
  KubeVirt API design guidelines
  ([kubevirt/kubevirt#18612](https://github.com/kubevirt/kubevirt/pull/18612),
  §3.2). It is a rolling-upgrade hazard: a newer component writing a
  newly-standardised group name is rejected by an older apiserver's enum.
- Every newly-standardised group would force an API change (extend the enum),
  whereas an open string set only needs the mapping switch extended.
- The discoverability benefit is retained more cheaply via convenience string
  constants plus the CEL compatibility rule, without the upgrade fragility.

This aligns `Groups` with the existing `Ciphers []string` field, which is also
an open string set.

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
is O(n) over a short list (the handful of standardised groups).

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
- `CurvePreferenceIds` skips unknown group names and preserves order

The TLS-version/group compatibility constraint is a CEL rule on
`TLSConfiguration` and is exercised via the generated CRD rather than a Go unit
test.

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

04-29-2026: Initial VEP proposed but implementation deferred from v1.9.0

09-02-2026: Retargeted to enter directly at Beta in v1.10.0 (Alpha skipped),
behind the `TLSGroupPreferences` feature gate, given the field is an additive
extension of the already-GA `TLSConfiguration` API and the list-of-groups
contract is established prior art in Prometheus and Envoy

## Graduation Requirements

### Alpha

Skipped — see *Skipping Alpha* under Design.

### Beta

- [ ] Feature gate `TLSGroupPreferences` (Beta, enabled by default) guards all
  code changes
- [ ] `Groups []string` open string set added to `TLSConfiguration` (no hard
  enum, per #18612 §3.2), with convenience group-name constants
- [ ] `CurvePreferenceIds([]string)` helper in `pkg/util/tls/tls.go`,
  gracefully skipping unrecognised names
- [ ] `CurvePreferences` set on `tls.Config` in all TLS setup functions
- [ ] `--tls-groups` flag added to virt-template components
- [ ] `InjectTLSConfigIntoDeployment` extended to inject `--tls-groups`
- [ ] CEL `x-kubernetes-validations` rule on `TLSConfiguration` enforcing the
  TLS-version/group compatibility constraint (#18612 §4.2)
- [ ] Unit tests for mapping, validation, and TLS setup
- [ ] Functional tests verifying group enforcement on virt pod endpoints
  (including virt-template components)

### GA

- [ ] Soak the feature in Beta with no outstanding TLS-negotiation issues
  reported against the configured groups
- [ ] Keep the recognised group constants and the `CurvePreferenceIds` mapping
  in step with the groups supported by the Go version KubeVirt ships, extending
  them as new hybrid groups are standardised
