# VEP #395: OCI Artifact Import for VirtualMachines and VirtualMachineTemplates

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

[VEP #256](../256-oci-export-proposal/oci-export-proposal.md) introduced
OCI artifact export for `VirtualMachines` and
`VirtualMachineTemplates`, but explicitly deferred import as a follow-up
design. Its graduation criteria for Beta/GA include "Import mechanism
implemented" and "Round-trip export/import tested."

This proposal introduces a `VirtualMachineImport` CRD (API group
`import.kubevirt.io`) and controller that imports `VirtualMachines` and
`VirtualMachineTemplates` from OCI artifacts stored in container registries.
The target kind is auto-detected from the OCI manifest's `artifactType`
field. The controller lives in a new `kubevirt/virt-import` repository and
is deployed by `virt-operator`, following the same pattern as
`kubevirt/virt-template`.

Importing disk data is delegated to CDI via `DataVolumes`. This requires
extending CDI's `DataVolumeSourceRegistry` with a `LayerDigest` field to
support fetching individual layer blobs from OCI artifacts by digest, and
handling raw (non-tar) layer blobs.

A proof-of-concept shell script exists at `kubevirt/kubevirt/hack/oci-import.sh`
that validates the overall approach.

### Implementation Phases

The work is split into three sequential phases:

1. **Phase 1 - CDI** (`kubevirt/containerized-data-importer`): Extend CDI's
   `DataVolumeSourceRegistry` with a `LayerDigest` field, add raw blob
   handling for OCI artifact layers, and validate incompatibility with
   node pull mode.
2. **Phase 2 - virt-import** (`kubevirt/virt-import`): Implement the
   `VirtualMachineImport` CRD and controller in a new repository. The
   controller is deployable standalone via Kustomize with cert-manager.
3. **Phase 3 - kubevirt** (`kubevirt/kubevirt`): Add the `OCIImport` feature
   gate, wire `virt-operator` to deploy/remove the import controller, and
   implement the `virtctl vmimport` CLI.

Each phase depends on the previous one. All three target alpha in v1.10.

## Motivation

By completing the import side of the OCI artifact workflow, we get:

* **Full round-trip** - export a VM or template from one cluster, push to a
  registry, import on another cluster
* **Declarative API** - `VirtualMachineImport` CR with status tracking,
  conditions, and garbage collection of intermediate resources
* **Registry-native distribution** - pull VMs and templates from existing
  registry infrastructure
* **Portable imports** - storage-specific details are not stored in the
  artifact; CDI applies cluster-appropriate defaults via StorageProfiles,
  making imports work across clusters with different storage backends

## Goals

* Import `VirtualMachines` from OCI artifacts in registries
* Import `VirtualMachineTemplates` from OCI artifacts in registries
* Auto-detect target kind from OCI `artifactType`
* Support multi-architecture OCI artifacts via optional
  `platform.architecture` selection
* Extend CDI's `DataVolumeSourceRegistry` with a `LayerDigest` field for
  direct OCI artifact layer blob fetching and raw blob handling
* Deploy the import controller via `virt-operator` when the `OCIImport`
  feature gate is enabled
* Provide a `virtctl vmimport` CLI command

## Non Goals

* Node pull mode for OCI artifacts (future enhancement - see
  [Node Pull Mode](#node-pull-mode---future-enhancement))
* Multi-VM appliance import (single VM or template per artifact)
* Guest-level operations (sysprep, network reconfiguration) during import
* Import of non-KubeVirt OCI artifacts (only artifacts matching the VEP #256
  format); non-compliant artifacts are rejected
* Periodic or scheduled imports (could be built on top of
  `VirtualMachineImport` as a future enhancement)

## Definition of Users

* **VM owner**: imports VMs from registries for cross-cluster migration or
  disaster recovery
* **Template owner**: imports templates from registries for sharing reusable
  VM blueprints across clusters
* **Cluster admin**: manages RBAC to control who can create imports;
  configures storage classes and StorageProfiles

## User Stories

* As a VM owner, I want to import a `VirtualMachine` from a registry so I can 
  recreate it on another cluster.
* As a template owner, I want to import a `VirtualMachineTemplate` from a
  registry so I can share reusable templates across clusters.
* As a VM owner, I want to round-trip my VM: export to OCI, push to a
  registry, import on another cluster - and get an equivalent VM.
* As a cluster admin, I want imports to use cluster-appropriate storage
  defaults so I do not need to manually specify volume modes and access
  modes for each disk.

## Repos

* (Phase 1) [kubevirt/containerized-data-importer](https://github.com/kubevirt/containerized-data-importer) -
  `LayerDigest` on `DataVolumeSourceRegistry`, raw blob handling in
  importer
* (Phase 2) [kubevirt/virt-import](https://github.com/kubevirt/virt-import)
  (new) - import controller, CRD, API types
* (Phase 3) [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) -
  feature gate `OCIImport`, `virt-operator` integration, `virtctl vmimport`

## Design

### OCI Artifact Format (reference to VEP #256)

The import mechanism consumes artifacts in the format defined by
[VEP #256](../256-oci-export-proposal/oci-export-proposal.md). See its
[OCI Artifact Format Specification](../256-oci-export-proposal/oci-export-proposal.md#oci-artifact-format-specification)
for the full definition of artifact types, media types, layer annotations,
and config blob structure.

The import controller uses the `artifactType` field on the OCI manifest to
auto-detect the target kind (`VirtualMachine` or `VirtualMachineTemplate`).
It reads `io.kubevirt.disk.name` and `io.kubevirt.disk.size` from layer
annotations to correlate layers to volumes and to size DataVolumes.

**Not in the artifact:** `volumeMode` and `accessModes` are not stored in
the OCI artifact. CDI applies cluster-appropriate defaults via
StorageProfiles on the target cluster. This makes imports portable across
clusters with different storage backends.

### Phase 1: CDI - OCI Artifact Layer Support

This phase extends CDI's `DataVolumeSourceRegistry` to support fetching
individual layer blobs from OCI artifacts by digest and importing raw
(non-tar) blobs. All changes are in
[kubevirt/containerized-data-importer](https://github.com/kubevirt/containerized-data-importer).

#### DataVolumeSourceRegistry LayerDigest

A new `LayerDigest` field is added to `DataVolumeSourceRegistry`:

```go
// DataVolumeSourceRegistry provides the parameters to create a Data Volume from an registry source
type DataVolumeSourceRegistry struct {
    //URL is the url of the registry source (starting with the scheme: docker, oci-archive)
    // +optional
    URL *string `json:"url,omitempty"`
    //ImageStream is the name of image stream for import
    // +optional
    ImageStream *string `json:"imageStream,omitempty"`
    //PullMethod can be either "pod" (default import), or "node" (node docker cache based import)
    // +optional
    PullMethod *RegistryPullMethod `json:"pullMethod,omitempty"`
    //SecretRef provides the secret reference needed to access the Registry source
    // +optional
    SecretRef *string `json:"secretRef,omitempty"`
    //CertConfigMap provides a reference to the Registry certs
    // +optional
    CertConfigMap *string `json:"certConfigMap,omitempty"`
    //Platform describes the minimum runtime requirements of the image
    // +optional
    Platform *PlatformOptions `json:"platform,omitempty"`
    // LayerDigest selects a specific layer from an OCI artifact by
    // its digest (e.g. "sha256:abc123..."). When set, the importer
    // fetches the blob directly and treats it as a raw blob.
    // +optional
    LayerDigest *string `json:"layerDigest,omitempty"`
}
```

When `LayerDigest` is set, `pullMethod` must be `pod` (or unset,
defaulting to `pod`). Node pull is incompatible with `LayerDigest`
because the container runtime cannot handle OCI artifacts with custom media
types. CDI's webhook rejects DataVolumes that combine `LayerDigest`
with `pullMethod: node`.

CDI's registry import behavior changes as follows:

| Step | Normal (container image) | With LayerDigest (OCI artifact) |
|------|--------------------------|----------------------------------|
| Manifest parsing | `image.FromSource()` | Skipped (direct blob fetch) |
| Layer selection | All layers, match by tar path | Skipped (digest identifies the blob) |
| Blob fetch | Via layer iteration | Direct `GetBlob()` by digest |
| Layer processing | `tar.NewReader()`, extract `disk/` files | Raw blob path: stream decompressed blob directly |
| Post-import inspection | `Inspect()` for labels | Skipped |

#### Node Pull Mode - Future Enhancement

Node pull mode cannot work for OCI artifacts because:

1. The container runtime (containerd/CRI-O) expects standard container
   image layers and would fail to pull an artifact with
   `application/vnd.kubevirt.disk.raw+zstd` layers
2. The sidecar container pattern used by CDI's node pull requires a valid
   container image as the sidecar's image reference
3. Even if the runtime could pull the artifact, it would try to unpack
   layers as filesystem overlays, which fails for raw disk blobs

Node pull would require one of:

* CRI-level OCI artifact support (upstream Kubernetes work)
* A different credential-sharing mechanism (e.g., projected service account
  tokens with registry auth)
* A proxy that uses node credentials to pull blobs and serves them to the
  importer pod

This can be a follow-up enhancement in the future.

### Phase 2: virt-import Controller

This phase delivers the `VirtualMachineImport` CRD and controller in a new
[kubevirt/virt-import](https://github.com/kubevirt/virt-import) repository.
The controller is deployable standalone via Kustomize with cert-manager
(the `default` overlay). A second `virt-operator` overlay is provided for
Phase 3 integration.

#### Repository Structure

The repository follows the same pattern as `kubevirt/virt-template`:

* Kubebuilder v4 scaffolding
* Separate `api/` module for importable types
* Two Kustomize overlays: `default` (cert-manager), `virt-operator`
  (external certs managed by `virt-operator`)

The controller does not access container registries directly. Instead, it
spawns a metadata-fetch Job whose Pod mounts the referenced Secret and
ConfigMap for registry authentication. The Job fetches the OCI image
index or manifest, resolves the platform, fetches the config blob, and
writes the artifactType, config blob, and layer descriptors (digest,
size, disk name) in a structured JSON format to stdout. All other log
messages are emitted to stderr. After the Job succeeds, the controller
reads stdout from the Job's Pod logs and uses the parsed output to
create DataVolumes and the target VM or VMTemplate. This avoids
granting the controller cluster-wide Secret or ConfigMap read
permissions.

#### CRD Definition

```go
// VirtualMachineImportSpec defines the desired import.
type VirtualMachineImportSpec struct {
    // Source specifies where to import from.
    Source VirtualMachineImportSource `json:"source"`
    // TargetName is the name for the created VM or VMTemplate.
    // Defaults to the VirtualMachineImport CR name.
    // +optional
    TargetName *string `json:"targetName,omitempty"`
    // StorageClassName overrides the default storage class for all
    // DataVolumes created during import.
    // +optional
    StorageClassName *string `json:"storageClassName,omitempty"`
}
```

`VirtualMachineImportSpec` is immutable after creation. A
`ValidatingAdmissionPolicy` with a CEL expression
(`oldObject.spec == object.spec`) rejects any update that modifies the
spec. To change import parameters, delete the CR and create a new one.

```go
type VirtualMachineImportSource struct {
    // Registry specifies an OCI artifact in a container registry.
    // +optional
    Registry *VirtualMachineImportRegistrySource `json:"registry,omitempty"`
}

type VirtualMachineImportRegistrySource struct {
    // URL is the registry URL
    // (e.g. docker://registry.example.com/vms/fedora:v1).
    URL string `json:"url"`
    // SecretRef is the name of a Secret containing registry credentials.
    // +optional
    SecretRef *string `json:"secretRef,omitempty"`
    // CertConfigMap is the name of a ConfigMap containing registry CA
    // certificates.
    // +optional
    CertConfigMap *string `json:"certConfigMap,omitempty"`
    // Platform selects the architecture variant from a multi-arch
    // artifact.
    // +optional
    Platform *PlatformOptions `json:"platform,omitempty"`
}

type PlatformOptions struct {
    // Architecture selects the platform variant (e.g. amd64, arm64).
    Architecture *string `json:"architecture,omitempty"`
}
```

```go
// VirtualMachineImportStatus reports observed import state.
type VirtualMachineImportStatus struct {
    // Conditions represent the latest observations of the import.
    Conditions []metav1.Condition `json:"conditions,omitempty"`
    // ArtifactInfo contains metadata read from the OCI artifact.
    // +optional
    ArtifactInfo *ArtifactInfo `json:"artifactInfo,omitempty"`
    // DiskImports tracks the status of each disk being imported.
    DiskImports []DiskImportStatus `json:"diskImports,omitempty"`
    // TargetRef references the created VM or VMTemplate.
    // +optional
    TargetRef *corev1.ObjectReference `json:"targetRef,omitempty"`
}

type ArtifactInfo struct {
    // ArtifactType is the OCI artifactType from the manifest.
    ArtifactType string `json:"artifactType"`
    // Architecture is the resolved platform architecture.
    Architecture string `json:"architecture"`
    // DiskCount is the number of disk layers in the artifact.
    DiskCount int `json:"diskCount"`
}

type DiskImportStatus struct {
    // Name is the disk name (io.kubevirt.disk.name annotation).
    Name string `json:"name"`
    // DataVolumeName is the name of the created DataVolume.
    DataVolumeName string `json:"dataVolumeName"`
    // Phase mirrors cdiv1.DataVolumePhase values (e.g. "Succeeded",
    // "ImportInProgress", "Failed").
    Phase string `json:"phase"`
}
```

**Condition types and reasons:**

| Condition | Status | Reason           | Meaning |
|-----------|--------|------------------|---------|
| `Progressing` | `True` | `FetchingMetadata` | Metadata-fetch Job running |
| `Progressing` | `True` | `ImportingDisks` | DataVolumes created, CDI importing |
| `Progressing` | `True` | `CreatingTarget` | Disks done, creating VM/VMTemplate |
| `Progressing` | `False` | `ImportComplete` | Import finished successfully |
| `Progressing` | `False` | `ImportFailed`   | Import failed |
| `Ready` | `True` | `ImportComplete` | Target resource created |
| `Ready` | `False` | `ImportFailed`  | Import failed (see message) |

#### Import Flow

```
User creates VirtualMachineImport CR
  |
  v
Import Controller (virt-import)
  |-- 1. Create metadata-fetch Job:
  |       The Job's Pod mounts the referenced Secret (registry credentials)
  |       and ConfigMap (CA certificates). The Job binary:
  |       a. Fetches OCI artifact from registry (Image Index or Image Manifest)
  |       b. Resolves platform:
  |            If the artifact is a plain Image Manifest (not an Index):
  |              - platform.architecture set -> verify match, fail on mismatch
  |              - unset -> use as-is
  |            If the artifact is an Image Index (manifest list):
  |              - platform.architecture set -> select matching manifest
  |                - multiple matches (e.g. same arch, different variant) -> fail
  |                - no match -> fail
  |              - unset, single manifest -> use the only available manifest
  |              - unset, multiple manifests -> fail, ask user to specify
  |            Manifests with unknown or empty arch/os are skipped.
  |       c. Fetches OCI manifest for resolved platform
  |       d. Validates artifact:
  |            - artifactType must be a known KubeVirt type
  |              (application/vnd.kubevirt.virtualmachine.v1 or
  |               application/vnd.kubevirt.virtualmachinetemplate.v1)
  |            - Each disk layer must have io.kubevirt.disk.name and
  |              io.kubevirt.disk.size annotations
  |            - Layer media types must be supported
  |            Non-compliant artifacts cause the Job to fail.
  |       e. Fetches config blob
  |       f. Writes JSON to stdout: artifactType, config blob, layer
  |          descriptors (digest, size, disk name), resolved architecture
  |       All other messages are emitted to stderr.
  |
  |-- 2. Wait for Job to succeed, read stdout from Pod logs
  |
  |-- 3. For each disk layer:
  |     |-- Create DataVolume:
  |     |     source.registry.url = docker://<artifact-ref>
  |     |     source.registry.layerDigest = <layer-digest>
  |     |     source.registry.secretRef = <from spec, if set>
  |     |     source.registry.certConfigMap = <from spec, if set>
  |     |     storage.resources.requests.storage = <disk-size>
  |     |     storage.storageClassName = <from spec, if set>
  |     v
  |     CDI:
  |       Fetches blob directly via GetBlob() using the layer digest
  |       Detects zstd via magic bytes (existing format-readers.go)
  |       Streams raw disk to PVC (raw blob path, no tar extraction)
  |
  |-- 4. Wait for all DataVolumes to succeed
  |-- 5. Rewrite config blob:
  |       VM: update PVC claimNames to point to created PVCs
  |       VMTemplate: also update dataVolumeTemplates[].spec.source.pvc.name
  |-- 6. Create VirtualMachine or VirtualMachineTemplate
  |-- 7. Set Ready condition to True
```

**PVC naming convention:** `${RESOURCE_NAME}-${DISK_NAME}` (e.g.,
`fedora-vm-rootdisk`), where `RESOURCE_NAME` is the `targetName` if set,
otherwise the `VirtualMachineImport` CR name. `DISK_NAME` is the
`io.kubevirt.disk.name` layer annotation value.

**Namespace scoping:** All resources created during import (metadata-fetch
Job, DataVolumes, PVCs, target VM or VMTemplate) are created in the same
namespace as the `VirtualMachineImport` CR. Cross-namespace import is not
supported.

**Volume sources on imported VMs:** The export format (VEP #256) strips
`dataVolumeTemplates` and replaces DataVolume volume sources with PVC
references. Imported VMs therefore use `persistentVolumeClaim` volume
sources pointing to the PVCs created during import. Non-PVC volumes
(cloud-init, ConfigMap, containerDisk, etc.) are preserved as-is from
the config blob.

**Error semantics:** Import is all-or-nothing. If the metadata-fetch Job
or any DataVolume fails, the controller sets the `Ready` condition to
`False` with reason `ImportFailed` and does not create the target VM or
VMTemplate. Intermediate resources (Jobs, DataVolumes, PVCs) are cleaned
up when the user deletes the `VirtualMachineImport` CR (see garbage
collection below).

**Naming collisions:** If any resource (DataVolume, PVC, VirtualMachine,
or VirtualMachineTemplate) that the controller needs to create already
exists in the namespace, the import fails. The controller does not
overwrite or adopt existing resources.

**Garbage collection:** The controller uses a finalizer on the
`VirtualMachineImport` CR. Behavior depends on the `Ready` condition:

* **During import** (`Ready` is not `True`): deleting the CR triggers the
  finalizer, which deletes all intermediate resources (metadata-fetch Job,
  DataVolumes, and their PVCs). No VM or VMTemplate has been created yet.
* **After successful import** (`Ready` is `True`): the controller removes
  ownerReferences from PVCs (so they are no longer owned by their
  DataVolumes), deletes the DataVolumes, and removes its finalizer. The
  created VM or VMTemplate and its PVCs persist independently. Deleting
  the `VirtualMachineImport` CR at this point only deletes the CR
  itself.

#### RBAC

**Controller RBAC:** The import controller requires permissions to:

* Manage `VirtualMachineImport` CRs and their status (get, list, watch,
  update, patch)
* Create and manage Jobs and read Pod logs (for the metadata-fetch Job)
* Create `VirtualMachines` and `VirtualMachineTemplates`
* Create CDI `DataVolumes` for disk import

The controller does not need permissions to read Secrets or ConfigMaps.
The metadata-fetch Job's Pod mounts them by name, which requires no
additional RBAC for the controller (same pattern as CDI's importer Pods).

**User RBAC:** A `ValidatingAdmissionPolicy` with CEL expressions guards
creation of `VirtualMachineImport` resources, ensuring the requesting
user has permissions to create `VirtualMachines`, `VirtualMachineTemplates`,
and `DataVolumes` in the target namespace. This prevents privilege
escalation where a user without those permissions could otherwise
leverage the controller to create resources on their behalf.

Users do not need `get` access to the referenced `secretRef` Secret.
The metadata-fetch Job's Pod mounts the Secret by name without exposing
its contents to the user. This follows the same pattern as CDI, where
users reference a Secret in a `DataVolume` without needing read access
to it.

### Phase 3: virt-operator Integration and virtctl

This phase wires the import controller into KubeVirt's deployment
lifecycle and adds the `virtctl vmimport` CLI. All changes are in
[kubevirt/kubevirt](https://github.com/kubevirt/kubevirt).

#### Feature Gate

The import functionality uses two controls in the `KubeVirt` CR, following
the same pattern as `virt-template`:

* An `OCIImport` feature gate that must be enabled
* A `spec.configuration.virtImportDeployment.enabled` field (defaults to
  `true` when the feature gate is enabled)

Both must be true for `virt-operator` to deploy the import controller and
CRD. When disabled, `virt-operator` removes the controller deployment and
the `VirtualMachineImport` CRD. Existing `VirtualMachineImport` CRs must
be deleted before disabling the feature.

#### virt-operator Deployment

`virt-operator` deploys the import controller using an embedded YAML bundle
(`//go:embed`) built from the `virt-operator` Kustomize overlay in
`kubevirt/virt-import`. `virt-operator` manages certificate rotation for
the controller's webhook, following the same pattern as
`kubevirt/virt-template`.

#### CLI: virtctl vmimport

```shell
# Import a VM from a registry
$ virtctl vmimport create my-import \
    --url=docker://registry.example.com/vms/fedora:v1 \
    --secret=registry-credentials

# Import with a custom target name for the created VM
$ virtctl vmimport create my-import \
    --url=docker://registry.example.com/vms/fedora:v1 \
    --target-name=fedora-vm

# Import a VMTemplate (auto-detected from artifact type)
$ virtctl vmimport create tpl-import \
    --url=docker://registry.example.com/templates/fedora:v1

# Check status
$ virtctl vmimport status my-import

# Delete import (keeps created VM/VMTemplate, cleans up DataVolumes)
$ virtctl vmimport delete my-import
```

**Flags on `virtctl vmimport create`:**

| Flag               | Default | Description                                                          |
|--------------------|---------|----------------------------------------------------------------------|
| `--url`            | -       | Registry URL (required, e.g. `docker://registry.example.com/vm:v1`) |
| `--target-name`    | -       | Name for the created VM or VMTemplate (defaults to CR name)          |
| `--secret`         | -       | Name of a Secret containing registry credentials                     |
| `--cert-configmap` | -       | Name of a ConfigMap containing registry CA certificates              |
| `--storage-class`  | -       | StorageClass override for all DataVolumes                            |
| `--arch`           | -       | Architecture variant for multi-arch artifacts (e.g. `amd64`). Optional; when unset, single-manifest artifacts are used as-is, multi-manifest artifacts fail with a request to specify |

## API Examples

### Import a VirtualMachine

```yaml
apiVersion: import.kubevirt.io/v1alpha1
kind: VirtualMachineImport
metadata:
  name: my-import
  namespace: default
spec:
  source:
    registry:
      url: docker://registry.example.com/vms/fedora:v1
      secretRef: registry-credentials
      certConfigMap: registry-ca
  targetName: fedora-vm
  storageClassName: ceph-block
```

### Import a VirtualMachineTemplate with architecture selection

```yaml
apiVersion: import.kubevirt.io/v1alpha1
kind: VirtualMachineImport
metadata:
  name: tpl-import
  namespace: templates
spec:
  source:
    registry:
      url: docker://registry.example.com/templates/fedora:v1
      platform:
        architecture: arm64
```

### DataVolume created by the import controller (CDI side)

```yaml
apiVersion: cdi.kubevirt.io/v1beta1
kind: DataVolume
metadata:
  name: fedora-vm-rootdisk
  namespace: default
  ownerReferences:
    - apiVersion: import.kubevirt.io/v1alpha1
      kind: VirtualMachineImport
      name: my-import
spec:
  source:
    registry:
      url: docker://registry.example.com/vms/fedora:v1
      secretRef: registry-credentials
      certConfigMap: registry-ca
      layerDigest: "sha256:a1b2c3d4..."
  storage:
    storageClassName: ceph-block
    resources:
      requests:
        storage: 10Gi
```

### Import status when ready

```yaml
status:
  conditions:
    - type: Ready
      status: "True"
      reason: ImportComplete
      message: VirtualMachine fedora-vm created with 2 disk(s)
    - type: Progressing
      status: "False"
      reason: ImportComplete
  artifactInfo:
    artifactType: application/vnd.kubevirt.virtualmachine.v1
    architecture: amd64
    diskCount: 2
  diskImports:
    - name: rootdisk
      dataVolumeName: fedora-vm-rootdisk
      phase: Succeeded
    - name: datadisk
      dataVolumeName: fedora-vm-datadisk
      phase: Succeeded
  targetRef:
    apiVersion: kubevirt.io/v1
    kind: VirtualMachine
    name: fedora-vm
    namespace: default
```

### Full round-trip example

```shell
# Cluster A: export
$ virtctl vmexport create my-export --vm=fedora-vm
$ virtctl vmexport download my-export --format=oci --output=fedora.oci.tar

# Push to registry
$ skopeo copy oci-archive:fedora.oci.tar \
    docker://registry.example.com/vms/fedora:v1

# Cluster B: import
$ virtctl vmimport create fedora-import \
    --url=docker://registry.example.com/vms/fedora:v1 \
    --secret=registry-credentials
```

## Alternatives

### Use Kubernetes ImageVolume for disk data

Kubernetes ImageVolume (KEP-4639) merges layers as filesystem overlays,
expecting standard OCI/Docker layer media types. The KubeVirt OCI export
format uses `application/vnd.kubevirt.disk.raw+zstd` - raw zstd-compressed
blobs, not tar archives. The kubelet and container runtime would reject
these. Disk data must be pulled via CDI's pod-pull path.

### Implement the controller in kubevirt/kubevirt

The import controller could run as a goroutine inside `virt-controller`,
like `VMExportController`. This avoids the separate repository overhead but
couples the import controller to virt-controller's release cycle and binary
size. The separate repository approach is preferred for modularity, but
this remains a viable alternative.

### Add a new CDI source type (DataVolumeSourceOCIArtifact)

A dedicated `DataVolumeSourceOCIArtifact` could encapsulate all OCI
artifact-specific logic. This was rejected because extending
`DataVolumeSourceRegistry` with `LayerDigest` is less invasive - it
reuses the existing registry pull infrastructure (auth, certs, TLS,
transport) and requires fewer changes to CDI's controller and webhook
logic.

### Client-side only import (no CRD)

The CLI could pull the OCI artifact, extract the config blob and disks, and
create resources directly. This was rejected because there would be no
declarative API, no status tracking, no automation, and large disk data
would need to transfer through the client machine.

## Scalability

Minimal impact. Each import creates one `VirtualMachineImport` CR, one
metadata-fetch Job, and one `DataVolume` per disk. The import controller
is a single-replica deployment with standard watch/reconcile mechanics.
DataVolume imports use CDI's existing scheduling and rate limiting.
Intermediate resources (Jobs, DataVolumes, PVCs) are owned by the import
CR and garbage collected on deletion.

## Update/Rollback Compatibility

This feature adds a new CRD and a new deployment. Both are additive:

* When the feature is disabled, `virt-operator` removes the controller
  deployment and the `VirtualMachineImport` CRD. Existing CRs must be
  deleted before disabling to ensure a clean removal. In-progress imports
  are lost.
* Rolling back to a KubeVirt version without the feature removes the
  controller and CRD. In-progress imports are lost. Created VMs and
  templates are unaffected.
* The CDI `LayerDigest` field is additive. On rollback to a CDI version
  without it, DataVolumes with `layerDigest` set would fail CDI
  validation, causing the DataVolume to fail. Since import uses
  all-or-nothing semantics, the entire import fails in this case.

## Functional Testing Approach

### Phase 1: CDI

**Unit tests:**

* Direct blob fetch by digest when LayerDigest is set
* Raw blob path (skip tar extraction when LayerDigest is set)
* Webhook rejection of LayerDigest + `pullMethod: node`

**Functional tests:**

* DataVolume with LayerDigest imports a single layer blob from a
  multi-layer OCI artifact pushed to a test registry

### Phase 2: virt-import

**Unit tests:**

* OCI manifest and config blob parsing
* Platform resolution logic (single-arch auto-select, multi-arch selection,
  error on ambiguous)
* `artifactType` validation
* Data exchange between controller and metadata-fetch `Job`
* Config blob rewriting (PVC claimName mapping for VMs and
  `dataVolumeTemplates` for VMTemplates)
* DataVolume generation from layer descriptors
* Controller reconciliation state machine (mock client)

**Functional tests:**

* **Round-trip (VM):** export VM -> push to registry -> import ->
  verify imported VM matches original (modulo PVC names)
* **Round-trip (VMTemplate):** export template -> push -> import ->
  verify template matches, including `dataVolumeTemplates`
* **Multi-disk:** import artifact with multiple disk layers -> verify all
  PVCs created and wired correctly
* **Multi-arch:** import artifact with multiple platform variants ->
  verify `platform.architecture` selection works
* **Error cases:** invalid artifact type, missing layer, unreachable
  registry, invalid credentials
* **Cleanup:** delete import CR -> verify metadata-fetch Job, DataVolumes,
  and PVCs are garbage collected

### Phase 3: kubevirt

Tests in `kubevirt/kubevirt` are limited to `virt-operator` deployment
concerns and kept to a minimum.

* **Deployment:** enabling the feature gate deploys the import controller
* **Removal:** disabling the feature gate removes the controller and CRD
* **Configurable:** `virtImportDeployment.enabled` controls deployment
  independently of the feature gate

## Implementation History

* 2026-07-21: Initial proposal. PR: [#396](https://github.com/kubevirt/enhancements/pull/396).

## Graduation Requirements

### Alpha

#### Phase 1: CDI

- [ ] `LayerDigest` field on `DataVolumeSourceRegistry` API
- [ ] Raw blob handling in importer (no tar extraction for OCI artifact
  layers)
- [ ] Webhook rejects `LayerDigest` + `pullMethod: node`
- [ ] Unit and functional tests for CDI changes pass

#### Phase 2: virt-import

- [ ] `VirtualMachineImport` CRD and controller in `kubevirt/virt-import`
- [ ] Import of `VirtualMachines` from OCI artifacts
- [ ] Import of `VirtualMachineTemplates` from OCI artifacts
- [ ] Auto-detection of target kind from `artifactType`
- [ ] Multi-architecture support with single-arch auto-default
- [ ] Config blob rewriting for PVC name mapping (VM and VMTemplate paths)
- [ ] Unit and functional tests pass
- [ ] Round-trip export/import tested for both VMs and VMTemplates

#### Phase 3: kubevirt

- [ ] `OCIImport` feature gate added, disabled by default
- [ ] `virt-operator` deploys import controller when feature gate is enabled
- [ ] `virtctl vmimport` CLI functional
- [ ] User-facing documentation for VirtualMachineImport CRD and virtctl
  vmimport published

### Beta

- [ ] Any API changes based on Alpha feedback incorporated
- [ ] Enable feature gate by default to gather feedback from broader audience
- [ ] User-facing documentation for VirtualMachineImport CRD and virtctl
  vmimport polished

#### On-By-Default Readiness

- [ ] No critical or high-severity bugs open against OCI import
- [ ] E2e tests stable and gating in CI
- [ ] Round-trip export/import passes reliably in CI for both VMs and VMTemplates

### GA

- [ ] Stable API with no breaking changes from beta
- [ ] Production usage feedback incorporated
- [ ] Node pull mode implemented or explicitly deferred with justification

