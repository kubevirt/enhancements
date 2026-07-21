# VEP #256: OCI Artifact Export for VirtualMachines and VirtualMachineTemplates

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir
  in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

`VirtualMachineExport` does not support packaging exports into a single
distributable artifact, and it does not support `VirtualMachineTemplate`
(VEP #76) as a source kind.

This proposal defines two OCI artifact formats -
`application/vnd.kubevirt.virtualmachine.v1` for `VirtualMachines` and
`application/vnd.kubevirt.virtualmachinetemplate.v1` for
`VirtualMachineTemplates` - and extends `VirtualMachineExport` with an OCI
layout streaming endpoint. No new CRDs or controllers outside of
`kubevirt/kubevirt` are required.

The artifacts can be downloaded as
[OCI image layout](https://github.com/opencontainers/image-spec/releases/tag/v1.1.1)
archives for local handling with standard tools (oras, skopeo, podman,
crane), and then pushed to an OCI-compliant registry for distribution across
clusters. Direct registry push from the export pod may be added in a future
version.

## Motivation

By packaging VMs and VM templates as OCI artifacts, we get:

* **Registry-native distribution** - push/pull using existing registry
  infrastructure
* **Content-addressable deduplication** - shared disk images are stored once
  in the registry, even when referenced by multiple artifacts
* **Standard tooling** - oras, skopeo, podman, crane
* **Signing and verification** - artifacts in registries can be signed with
  cosign or notation out of the box
* **Lossless round-tripping** - the full CRD object is stored in the config
  blob, preserving all fields including template parameters
* **Native disk format** - raw disk images are stored directly as OCI blobs
  without any conversion

## Goals

### Phase 1 - `VirtualMachine` export

* Define an OCI artifact format for `VirtualMachine` that stores the
  resource definition in the config blob and disk images as individual layers
* Extend the export server with an `/export.oci.tar` endpoint that streams
  OCI image layout archives
* Use an OCI Image Index as the top-level object to support
  multi-architecture VMs from the start
* Extend `virtctl vmexport` with `--format=oci`

### Phase 2 - `VirtualMachineTemplate` export

* Extend the OCI artifact format to `VirtualMachineTemplate`
* Extend `VirtualMachineExport` to accept `VirtualMachineTemplate` as a
  source kind, with the export controller resolving template volume sources

## Non Goals

* Direct registry push from the export pod (deferred to a future version)
* Import of OCI artifacts into the cluster (deferred to a follow-up design)
* Support for multi-VM appliances (single VM or template per artifact)
* Guest-level operations such as sysprep or sealing during export
* Live export of running VMs
* Cross-hypervisor portability (the OCI format is KubeVirt-native)

## User Stories

* As a VM owner, I want to export my `VirtualMachine` including all its
  disks to an OCI artifact so I can migrate it to another cluster.
* As a VM owner, I want to push my VM to an OCI registry so another cluster
  can pull and import it.
* As a VM template owner, I want to push a `VirtualMachineTemplate` to an
  OCI registry so I can share it across clusters.
* As a VM template owner, I want to download a template as a file so I can
  transfer it to an air-gapped cluster.
* As a VM template owner, I want template parameters preserved in the
  artifact so the receiving cluster can re-import the template with its
  parameterization intact.

## Repos

* [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Feature Gate

The OCI export functionality is gated behind a new `OCIExport` feature gate
in the `KubeVirt` CR. When disabled, the export server returns 404 for
`/export.oci.tar` requests and `VirtualMachineExport` rejects
`VirtualMachineTemplate` as a source kind.

### OCI Artifact Format Specification

`VirtualMachines` and `VirtualMachineTemplates` are packaged as OCI
artifacts using the standard OCI image manifest with custom media types. The
top-level object is always an OCI Image Index, even for single-architecture
exports, for forward compatibility.

Two artifact types are defined:

| Artifact Type                                        | Source Kind              |
|------------------------------------------------------|--------------------------|
| `application/vnd.kubevirt.virtualmachine.v1`         | `VirtualMachine`         |
| `application/vnd.kubevirt.virtualmachinetemplate.v1` | `VirtualMachineTemplate` |

The version suffix refers to the artifact format version, not the CRD API
version. The actual CRD API version is recorded inside the config blob.
`VirtualMachineSnapshot` exports use the `virtualmachine.v1` artifact type
since a snapshot represents VM state.

#### Image Index

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.index.v1+json",
  "artifactType": "application/vnd.kubevirt.virtualmachine.v1",
  "manifests": [
    {
      "mediaType": "application/vnd.oci.image.manifest.v1+json",
      "digest": "sha256:abc...",
      "size": 1234,
      "platform": {
        "architecture": "amd64",
        "os": "linux"
      }
    }
  ],
  "annotations": {
    "org.opencontainers.image.title": "my-vm",
    "org.opencontainers.image.description": "Fedora 42 Server VM",
    "org.opencontainers.image.created": "2026-04-09T12:00:00Z"
  }
}
```

The `platform.architecture` field maps to `spec.template.spec.architecture`,
defaulting to `amd64` when unset. The `platform.os` field is always `linux`.

#### Image Manifest

Each manifest in the index describes one architecture variant:

```json
{
  "schemaVersion": 2,
  "mediaType": "application/vnd.oci.image.manifest.v1+json",
  "artifactType": "application/vnd.kubevirt.virtualmachine.v1",
  "config": {
    "mediaType": "application/vnd.kubevirt.virtualmachine.config.v1+json",
    "digest": "sha256:def...",
    "size": 4096
  },
  "layers": [
    {
      "mediaType": "application/vnd.kubevirt.disk.raw+zstd",
      "digest": "sha256:111...",
      "size": 2147483648,
      "annotations": {
        "io.kubevirt.disk.name": "rootdisk",
        "io.kubevirt.pvc.capacity": "10Gi",
        "io.kubevirt.pvc.volumeMode": "Block",
        "io.kubevirt.pvc.accessModes": "ReadWriteOnce",
        "org.opencontainers.image.title": "rootdisk.raw.zst"
      }
    },
    {
      "mediaType": "application/vnd.kubevirt.disk.raw+zstd",
      "digest": "sha256:222...",
      "size": 1073741824,
      "annotations": {
        "io.kubevirt.disk.name": "datadisk",
        "io.kubevirt.pvc.capacity": "5Gi",
        "io.kubevirt.pvc.volumeMode": "Block",
        "io.kubevirt.pvc.accessModes": "ReadWriteOnce",
        "org.opencontainers.image.title": "datadisk.raw.zst"
      }
    }
  ],
  "annotations": {
    "org.opencontainers.image.title": "fedora-server",
    "org.opencontainers.image.created": "2026-04-09T12:00:00Z"
  }
}
```

#### Config Blob

The config blob contains the source CRD object serialized as JSON, with
cluster-specific fields (`namespace`, `uid`, `resourceVersion`,
`creationTimestamp`, `generation`, `managedFields`) stripped.

Non-PVC volumes (cloud-init, `ConfigMap`, `Secret`, etc.) remain inline in
the spec. ContainerDisk volumes keep their image reference as-is - the
referenced image is not re-packaged into the artifact.

#### Disk Layers

Each PVC-backed disk image is stored as a separate layer (blob) in the
manifest. Layers are raw OCI blobs - not tar-wrapped.

For `VirtualMachine` exports, `dataVolumeTemplates` are stripped from the
spec and DataVolume volume sources in `spec.template.spec.volumes` are
replaced with PersistentVolumeClaim volume sources. The actual disk data
is already captured in the layers, so the exported VM definition only
needs plain PVC references.

For `VirtualMachineTemplate` exports, `DataVolumeTemplates` backed by PVCs
are resolved and exported as layers. The `io.kubevirt.disk.name` annotation
on each layer links it to the corresponding volume name in the VM spec
(`spec.template.spec.volumes[].name`).

#### Media Types

| Media Type                                                       | Usage                                    |
|------------------------------------------------------------------|------------------------------------------|
| `application/vnd.kubevirt.virtualmachine.v1`                     | `artifactType`: `VirtualMachine`         |
| `application/vnd.kubevirt.virtualmachine.config.v1+json`         | Config blob: VM definition               |
| `application/vnd.kubevirt.virtualmachinetemplate.v1`             | `artifactType`: `VirtualMachineTemplate` |
| `application/vnd.kubevirt.virtualmachinetemplate.config.v1+json` | Config blob: VM template definition      |
| `application/vnd.kubevirt.disk.raw+zstd`                         | Layer: zstd-compressed raw disk          |

Raw disks are always compressed with zstd during export. Zstd is provided
by the external Go library
[`github.com/klauspost/compress/zstd`](https://github.com/klauspost/compress).

#### Layer Annotations

| Annotation                       | Description                                 |
|----------------------------------|---------------------------------------------|
| `io.kubevirt.disk.name`          | Volume name from the VM spec                |
| `io.kubevirt.pvc.capacity`       | PVC requested capacity (e.g. `10Gi`)        |
| `io.kubevirt.pvc.volumeMode`     | `Filesystem` or `Block`                     |
| `io.kubevirt.pvc.accessModes`    | Comma-separated list (e.g. `ReadWriteOnce`) |
| `org.opencontainers.image.title` | Human-readable filename                     |

### OCI Layout Streaming Endpoint (export server)

A new `/export.oci.tar` endpoint is added to the export server. It streams
an OCI image layout archive directly from the mounted PVCs without
intermediate storage. The endpoint is available for `VirtualMachine`,
`VirtualMachineSnapshot`, and `VirtualMachineTemplate` source kinds.

#### OCI Image Layout Structure

```
export.oci.tar
  oci-layout                        {"imageLayoutVersion": "1.0.0"}
  index.json                        -> Image Index
  blobs/
    sha256/
      <index-digest>                Image Index JSON
      <manifest-digest>             Image Manifest JSON
      <config-digest>               VM or VirtualMachineTemplate JSON
      <disk1-digest>                disk image (streamed from PVC)
      <disk2-digest>                disk image (streamed from PVC)
```

#### Streaming Strategy

TAR headers require the file size and path (which includes the content
digest) before data is written. For compressed disk blobs, neither is known
until after compression. The export server uses a two-pass approach to
avoid scratch space:

**Pass 1 - compute digests (runs on startup, export becomes ready after):**

1. Generate the config blob (VM or template JSON), compute its digest/size
2. Stat each disk file to determine its size
3. Read each disk file sequentially, compressing with zstd, and compute the
   SHA-256 digest of the output (only a running hash state is needed, no
   scratch space)
4. Generate the manifest and index JSON with all digests and sizes

**Pass 2 - stream the TAR (runs on each download request):**

5. Write `oci-layout`, `index.json`, index blob, manifest blob, and config
   blob (all in memory from pass 1)
6. Re-read each disk file from the mounted PVC and stream it into the TAR
   under `blobs/sha256/`

Zstd compression must be deterministic so that pass 1 and pass 2 produce
byte-identical output. This is achieved by using a fixed compression level
and the same `zstd.Encoder` configuration in both passes. Zstd is
deterministic by default when encoder options are held constant.

For compressed raw disks, pass 1 compresses through a counting hash writer
to record both the compressed size and digest without storing the output.

### Status Links Integration

The OCI layout URL is added to `status.links` as a new
`ExportManifestType` constant (`"oci"`). The URL is included in both
internal and external links, accessible through `virt-exportproxy`.

### Architecture: Extend `VirtualMachineExport`

This proposal adds `VirtualMachineTemplate` as a new source kind.

`VirtualMachineTemplate` exports only support the OCI format. The existing
per-volume raw/gzip download links are not generated for template sources,
since individual disk images without the template definition are not useful
for distribution.

**Changes:**

* `VirtualMachineExport` accepts `VirtualMachineTemplate` as
  `spec.source.kind` (with `apiGroup: template.kubevirt.io`)
* Export controller resolves template DataVolumeTemplates to PVCs
* `status.links` for template exports only includes the OCI layout URL
* `virtctl vmexport` gets OCI download subcommands

### End-to-End Flow

```
User                                   kubevirt/kubevirt
----                                   ----------------
Create VirtualMachineExport
  source:
    kind: VirtualMachine           Export controller picks it up
    (or VirtualMachineTemplate) ->   resolves source -> PVCs
                                      creates Service + exporter pod
                                      mounts PVCs + resource ConfigMap
                                    Export server serves:
                                      /volumes/disk1/disk.img    (VM, VMS)
                                      /volumes/disk1/disk.img.gz (VM, VMS)
                                      /export.oci.tar            (VM, VMS, VMT)
```

### Export Controller: `VirtualMachineTemplate` Source Handler

A new source handler implementing the `ExportSource` interface:

1. Fetch the `VirtualMachineTemplate` and validate it has `Ready=True`
2. Extract the embedded VM spec from `spec.virtualMachine`
3. Resolve volume sources:
    - DataVolumeTemplates with a direct PVC source -> resolve to PVCs,
      export disk data as layers
    - DataVolumeTemplates with `sourceRef` or other non-PVC sources -> kept
      as-is in the config blob, no layer
    - Non-PVC volumes (cloudInit, configMap, containerDisk, etc.) -> kept
      inline in config blob, no layer
4. If any resolved PVC does not exist, the export stays in `Pending` phase.
   If a PVC is in use by another pod (RWO), the export stays `Pending`
   until the PVC becomes available. This matches the existing VM export
   behavior.
5. Create a ConfigMap with the serialized `VirtualMachineTemplate`
   (cluster-specific fields stripped)
6. Create the exporter pod with resolved PVCs and ConfigMap mounted

### CLI: virtctl vmexport

```shell
# Export a VirtualMachine to a local OCI archive
$ virtctl vmexport create my-vm-export --vm=my-vm
$ virtctl vmexport download my-vm-export \
    --output my-vm.oci.tar --format=oci

# Export a VirtualMachineTemplate to a local OCI archive
$ virtctl vmexport create my-tpl-export --vmtemplate=fedora-template
$ virtctl vmexport download my-tpl-export \
    --output fedora.oci.tar --format=oci

# Then push to a registry with standard tools:
$ oras copy --from-oci-layout ./fedora.oci.tar:latest \
    registry.example.com/templates/fedora:v1
$ skopeo copy oci-archive:fedora.oci.tar \
    docker://registry.example.com/templates/fedora:v1
```

**New flags on `virtctl vmexport create`:**

| Flag           | Default | Description                                                                                  |
|----------------|---------|----------------------------------------------------------------------------------------------|
| `--vmtemplate` | -       | Source `VirtualMachineTemplate` name (mutually exclusive with `--vm`, `--snapshot`, `--pvc`) |

**Extended flags on `virtctl vmexport download`:**

| Flag       | Default | Description                                        |
|------------|---------|----------------------------------------------------|
| `--format` | `gzip`  | Download format: `raw`, `gzip`, or `oci` (new) |

### RBAC

Additional permissions for the new source kind, added to the existing
export controller `ClusterRole`:

```yaml
- apiGroups: [ "template.kubevirt.io" ]
  resources: [ "virtualmachinetemplates" ]
  verbs: [ "get", "list", "watch" ]
```

### Verifying Exported Artifacts

Until a dedicated import mechanism exists, a verification script based on
existing CLI tools will be provided to validate the OCI layout structure
and confirm that exported disks can be re-imported.

## API Examples

### Export a `VirtualMachineTemplate`

```yaml
apiVersion: export.kubevirt.io/v1
kind: VirtualMachineExport
metadata:
  name: my-template-export
  namespace: my-templates
spec:
  source:
    apiGroup: template.kubevirt.io
    kind: VirtualMachineTemplate
    name: fedora-template
  ttlDuration: 4h
```

## Alternatives

### Use OVA/OVF format

OVA (TAR archive with OVF XML descriptor + VMDK disk images) is the
traditional format for VM portability across hypervisors. It was not chosen
because:

* OVA cannot be pushed to OCI registries - it requires separate distribution
  infrastructure
* VMDK conversion from raw is CPU-intensive
* Mapping KubeVirt resources to OVF is lossy - template parameters and
  KubeVirt-specific fields have no OVF equivalent
* The OVF "standard" varies in practice across hypervisors
* No built-in content-addressable deduplication or signing

OCI artifacts reuse existing registry infrastructure and preserve the full
CRD object without conversion.

### Use ContainerDisks

ContainerDisks are OCI images that embed a single disk in a container
image. They were not chosen because:

* A ContainerDisk can only contain a single disk image and no VM
  definition - multi-disk VMs cannot be represented, and resource
  metadata is lost
* While it would be theoretically possible to export each disk as a
  separate ContainerDisk, this would require external orchestration to
  track which images belong together and how to reassemble them into a
  VM - multiple artifacts to reproduce what a single OCI artifact
  achieves

OCI artifacts store the complete resource definition alongside
arbitrarily many disk layers in one self-contained artifact.

### Add a separate `VirtualMachineTemplateExport` CRD in virt-template

* Unnecessary indirection - users would need to learn a new CRD that wraps
  `VirtualMachineExport`
* The export controller already handles multiple source kinds; adding one
  more is a natural extension
* Avoids cross-repo version coordination

### Generate OCI artifact client-side in virtctl

The CLI could download raw disks via the existing VMExport mechanism and
package them into an OCI artifact locally. This was rejected because:

* It would not provide an in-cluster export URL for programmatic access
* Large disk downloads to the client and re-push to a registry would be
  slow and waste bandwidth (two transfers instead of one for the push case)
* It would not integrate with cluster ingress for external access

### Store resource definition as a layer instead of config blob

The VM or template definition could be stored as a layer alongside disk
images, with an empty config blob. This was rejected because:

* The config blob is the natural place for metadata - tools can inspect it
  without pulling disk layers
* `oras manifest fetch` and similar commands show config blob details,
  making inspection fast and bandwidth-efficient
* It follows the OCI convention where config describes the content and
  layers contain the data

### Single-pass streaming with scratch space

The export server could write the OCI layout to a scratch volume first,
then serve it directly on subsequent downloads. The tradeoff is scratch
space equal to the total export size. The two-pass approach is preferred
because it needs no additional storage.

### Use gzip instead of zstd for disk layer compression

Gzip is available in Go's standard library (`compress/gzip`) and is already
used by the existing raw/gzip export links. Zstd was chosen because it
offers faster compression and decompression at equal or better ratios,
which matters for large disk images. The tradeoff is an external dependency
(`github.com/klauspost/compress/zstd`), but this library is widely used and
well-maintained.

## Scalability

Minimal impact. The export controller adds one new source handler for
`VirtualMachineTemplate`. The actual export work is handled by the existing
per-export pod infrastructure with TTL-based cleanup.

## Update/Rollback Compatibility

Both changes are additive:

* The OCI endpoint adds a new handler to the export server. Rolling back
  means `/export.oci.tar` is not available; existing raw/gzip downloads
  are unaffected.
* The `VirtualMachineTemplate` source kind is rejected as unknown on
  rollback. Existing VM, snapshot, and PVC exports are unaffected.

The OCI artifact format is versioned via the `artifactType` media type.
Format changes are handled by introducing a new artifact type version.

## Functional Testing Approach

* Unit tests: manifest/index generation, config blob serialization, TAR
  layout structure, template source handler resolution
* Functional tests: VM and template export, TTL expiration,
  error cases (missing/unready template, inaccessible PVCs)

## Implementation Phases

### Phase 1: OCI format and `VirtualMachine` export

* OCI manifest and index generation for the `VirtualMachine` artifact type
* Config blob generation from VM spec
* OCI image layout TAR streaming
* `/export.oci.tar` endpoint handler with token authentication
* `--format=oci` flag on `virtctl vmexport download`

This phase works entirely within the existing `VirtualMachine` source kind.

### Phase 2: `VirtualMachineTemplate` source kind

* Add `VirtualMachineTemplate` as a new source kind to
  `VirtualMachineExport`
* Template source handler: fetch template, extract VM spec, resolve
  DataVolumeTemplates to PVCs
* OCI manifest and config blob generation for the template artifact type
* `--vmtemplate` flag on `virtctl vmexport create`

## Graduation Requirements

### Alpha (Phase 1 - `VirtualMachine` OCI export)

- [ ] `OCIExport` feature gate added, disabled by default
- [ ] `/export.oci.tar` endpoint generates valid OCI image layout archives
  for `VirtualMachine` and `VirtualMachineSnapshot` sources
- [ ] OCI manifest uses Image Index with per-architecture manifests
- [ ] Config blob contains VM definition with cluster-specific fields
  stripped, `dataVolumeTemplates` removed, and DataVolume volume sources
  replaced with PVC references
- [ ] Artifact type `application/vnd.kubevirt.virtualmachine.v1` set correctly
- [ ] Disk images stored as individual layers with correct media types and
  annotations
- [ ] `virtctl vmexport download --format=oci` functional
- [ ] Downloaded archives can be pushed to a registry with common tools
- [ ] Unit and functional tests pass

### Alpha (Phase 2 - `VirtualMachineTemplate` source kind)

- [ ] `VirtualMachineTemplate` accepted as source kind in
  `VirtualMachineExport` with correct artifact type
  (`virtualmachinetemplate.v1`)
- [ ] Template source handler resolves DataVolumeTemplate PVCs
- [ ] `virtctl vmexport create --vmtemplate` functional
- [ ] Unit and functional tests pass

### Beta / GA (future)

- [ ] Import mechanism implemented (separate design)
- [ ] Round-trip export/import tested for both VMs and templates
