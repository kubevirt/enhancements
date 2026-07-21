# VEP #333: Reusable Pod Metadata Input for virt-launcher Plugin Sidecars

## Overview

This proposal defines a reusable sidecar input named `pod-info` for virt-launcher plugin sidecars. `pod-info` exposes selected virt-launcher Pod metadata to an explicitly requesting sidecar through a Kubernetes DownwardAPI volume.

The first supported projection is:

- `metadata.labels` -> `labels`
- `metadata.annotations` -> `annotations`

This VEP does not define a new plugin hook point. It defines a reusable input primitive that can be consumed by structured plugins, network binding plugin sidecars, or temporary alpha compatibility paths.

The intended long-term consumer is the structured plugin model. A separate follow-up VEP should define a structured cloud-init hook, such as `PreCloudInitIso`, for use cases that need to mutate generated cloud-init data before ISO creation.

Legacy hook-sidecar support, if implemented, is an alpha compatibility bridge only and is not the graduation path.

## Motivation

Some virt-launcher sidecars need runtime context from the final launcher Pod, not only from the VMI. Examples include labels or annotations added during Pod rendering, admission, scheduling, or network setup.

Today, sidecars that need this metadata must either receive duplicated configuration through integration-specific channels or call the Kubernetes API. Calling the API requires service account token access and RBAC permissions for a metadata-only use case.

Kubernetes already provides a local, read-only mechanism for this through DownwardAPI volumes. KubeVirt should expose this mechanism only when explicitly requested by a trusted sidecar, and only to that sidecar.


## Goals

- Define `pod-info` as a reusable KubeVirt sidecar input.
- Back `pod-info` with Kubernetes DownwardAPI fields.
- Project Pod labels and annotations as local read-only files.
- Mount the volume only into sidecars that explicitly request `pod-info`.
- Leave the compute container and unrelated sidecars unchanged.
- Avoid requiring Kubernetes API credentials for metadata-only reads.
- Validate unsupported sidecar input values with actionable errors.
- Keep the long-term API aligned with structured plugins.

## Non Goals

- This VEP does not define a structured cloud-init hook.
- This VEP does not make legacy hook sidecars a stable extension API.
- This VEP does not mount Pod metadata into every virt-launcher Pod.
- This VEP does not mount Pod metadata into the compute container by default.
- This VEP does not grant sidecars Kubernetes API credentials or RBAC.
- This VEP does not provide arbitrary Pod mutation or validation.
- This VEP does not guarantee synchronous delivery of late Pod metadata updates.
- This VEP does not initially define per-key filtering for labels or annotations.

## User Stories

1. A structured plugin sidecar needs final virt-launcher Pod labels and annotations to make a startup-time decision, without receiving Kubernetes API credentials.

2. A future structured cloud-init plugin needs Pod metadata produced by admission or network integrations before generating guest cloud-init network data.

3. A network binding plugin sidecar needs to consume KubeVirt-provided device information and selected Pod metadata to configure interface behavior.

4. A cluster administrator wants metadata exposure to be explicit and scoped to approved sidecars instead of mounted into every launcher container.


## Design

When a sidecar requests `pod-info`, virt-controller adds one KubeVirt-owned DownwardAPI volume to the virt-launcher Pod.

```yaml
items:
- path: labels
  fieldRef:
    fieldPath: metadata.labels
- path: annotations
  fieldRef:
    fieldPath: metadata.annotations
```

The volume is mounted read-only into the requesting sidecar at:
```bash
/var/run/kubevirt-private/downwardapi/podinfo
```

The internal volume name should be KubeVirt-owned, for example:
```text
kubevirt-podinfo
```

The volume is rendered only when at least one sidecar requests pod-info.
The mount is added only to sidecars that requested pod-info. It is not added to the compute container and is not added to unrelated sidecars.

## Structured Plugin Target

The long-term API should be expressed through structured plugins. Exact field names should be finalized with VEP-190 owners, but the contract should look conceptually like this:
```yaml
apiVersion: plugin.kubevirt.io/v1alpha1
kind: Plugin
metadata:
  name: example-plugin
spec:
  sidecarInputs:
  - type: PodInfo
    items:
    - labels
    - annotations
```
This VEP owns the semantics of PodInfo. Hook-specific behavior, such as cloud-init mutation, belongs in the structured cloud-init hook VEP.

## Alpha Compatibility Paths

### Hook sidecars
Alpha implementations may expose pod-info through existing hook-sidecar annotations only as a temporary compatibility path.
```yaml
metadata:
  annotations:
    hooks.kubevirt.io/hookSidecars: |
      [
        {
          "image": "example/hook-sidecar",
          "downwardAPI": ["pod-info"]
        }
      ]
```

This path is explicitly non-graduating. It exists only to validate existing integrations while structured plugins gain the required hook points and sidecar input fields.
Before beta, the project must decide whether this path is removed, deprecated, or explicitly documented as alpha-only.

### Network binding plugins

Alpha implementations may allow network binding plugin sidecars to request pod-info through the existing scalar field:
```yaml
spec:
  configuration:
    network:
      binding:
        example-binding:
          sidecarImage: registry.example.com/example-binding:latest
          downwardAPI: pod-info
```

The current scalar field can express device-info or pod-info, but not both. If real use cases require multiple inputs, beta must introduce an additive list or structured field such as sidecarInputs.


Exact field names should be finalized with VEP-190 owners. The important contract is that the plugin explicitly requests the input, and KubeVirt mounts it only into that plugin’s sidecar.

PreCloudInitIso should be modeled as a cloud-init hook family, not a domainHook, because it mutates generated cloud-init data before ISO creation and does not mutate domain XML.

## Validation

Admission must reject unsupported sidecar input values.
Error messages should include:
- the unsupported value
- the supported values
- the field or annotation path that caused the rejection
Supported initial values:

```text
device-info
pod-info
```
For hook-sidecar compatibility, invalid JSON or unsupported downwardAPI values must reject VMI creation when the Sidecar feature gate is enabled.

## Security
Pod labels and annotations may contain integration-specific or sensitive information. Exposing them to a sidecar expands what that sidecar can observe.

Security properties:
- opt-in only
- scoped to requesting sidecars
- not mounted into compute by default
- no Kubernetes API token required
- no new RBAC required
- admins must trust sidecars that request this input

Before beta, the project must decide whether full metadata exposure is acceptable or whether per-key filtering is required.

## Timing Semantics

`pod-info` follows standard Kubernetes DownwardAPI volume semantics.
Metadata present when kubelet materializes the volume is available through the projected files. Later updates are reflected according to kubelet DownwardAPI behavior.
This VEP does not add a KubeVirt synchronization contract for late annotation updates. Any integration that depends on metadata produced after Pod creation must prove the required timing through functional or e2e tests.

## Alternatives

### Kubernetes API access
Sidecars can read their own Pod through the Kubernetes API, but this requires service account token access and RBAC permissions. That is too broad for metadata-only reads.

### Always mount Pod metadata
Always mounting labels and annotations into every launcher Pod is simple, but exposes metadata even when no component needs it.

### VMI metadata only
VMI labels and annotations are insufficient for use cases that need final launcher Pod metadata added during rendering, admission, scheduling, or networking.

### Per-key filtering
Per-key filtering reduces exposure, but may be too restrictive for dynamic network and admission metadata. This should be reconsidered before beta.

## Implementation Phasing

### Phase 1: Alpha compatibility implementation

- Implement pod-info DownwardAPI projection.
- Mount only into requesting sidecars.
- Validate unsupported values.
- Allow temporary hook-sidecar compatibility if accepted.
- Allow network binding scalar downwardAPI: pod-info if accepted.
- Link implementation PRs as alpha compatibility implementations, not final API shape.

### Phase 2: Structured plugin API (Beta)

- Finalize structured plugin sidecar input API.
- Decide whether hook-sidecar compatibility remains.
- Decide whether per-key filtering is required.
- Decide whether network binding needs multi-input support.
- Document upgrade and downgrade behavior.

### Phase 3: GA

- Stable API path documented.
- Stable mount path documented.
- Release-blocking tests cover the accepted API path.
- Security review accepts the metadata exposure model.

## Graduation Requirements

### Alpha
- VEP accepted by owning SIG.
- pod-info is opt-in.
- Volume is mounted only into requesting sidecars.
- Unsupported values are rejected during admission.
- Unit tests cover parsing, validation, and Pod rendering.
- Functional or e2e test proves the sidecar can read labels and annotations without Kubernetes API credentials.

### Beta
- Structured plugin sidecar input API is finalized or an explicit alternative is accepted.
- Hook-sidecar compatibility is removed, deprecated, or explicitly non-graduating.
- Per-key filtering decision is made.
- Upgrade and downgrade behavior is documented.
- At least one real integration validates the feature.

### GA
- API and mount path are stable.
- Release-blocking tests cover the accepted API path.
- Security review is complete.
- No blocker bugs remain for scoping, rollback, or metadata projection semantics.