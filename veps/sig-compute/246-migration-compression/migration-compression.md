# VEP #246: Live Migration Compression

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.9
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements](https://github.com/kubevirt/enhancements/issues/246)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Enable optional zstd compression for live migration data streams, reducing
network bandwidth consumption and improving migration completion times for
memory-intensive workloads.

## Motivation

Live migration transfers the VM's entire dirty memory over the network. For
large-memory VMs or environments with limited migration bandwidth, this can
result in slow migrations, timeouts, or inability to converge.

QEMU and libvirt have supported transparent compression of migration streams
since libvirt 1.0.3 (xbzrle/mt) and 9.4.0 (zstd/zlib via multifd), but
KubeVirt does not currently expose this capability.

Zstd compression offers an excellent speed-to-ratio tradeoff and is well-suited
for multifd parallel migration, which KubeVirt already enables by default when
CPU limits are not set. Enabling zstd compression on the already-existing
multifd channels can significantly reduce the volume of data transferred with
minimal CPU overhead.

## Goals

- Expose a simple on/off toggle for enabling zstd compression on live migration.
- Make the toggle available in both `MigrationConfiguration` (cluster-wide
  default) and `MigrationPolicy` (per-VM/namespace override).
- Simple API keeps flexibility to change to any future better algorithm under
  the hood without changing the Kubevirt API.
- Guard the feature behind an Alpha feature gate.

## Non Goals

- Exposing fine-grained compression tuning (compression level, thread count,
  method selection) in Alpha. These can be added in Beta/GA if needed.
- Supporting legacy compression methods (xbzrle, mt/zlib) as first-class
  options. Zstd is the modern default with the best performance profile.
- Compression of block (disk) migration data — this VEP only covers memory
  stream compression.

## Definition of Users

- **Cluster Administrators**: Operating environments with constrained migration
  bandwidth or large-memory VMs that frequently time out during migration.
- **Platform Engineers**: Building migration policies for multi-tenant clusters
  where bandwidth is shared.

## User Stories

- As a cluster admin, I want to enable migration compression for all live
  migrations so that my large-memory VMs can migrate within the
  `CompletionTimeoutPerGiB` window on a bandwidth-constrained migration
  network.
- As a platform engineer, I want to enable compression for a specific
  namespace of memory-heavy VMs via `MigrationPolicy` without affecting
  other workloads.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### API changes

Add a `MigrationCompression *bool` field to two structs:

**`MigrationConfiguration`** (in `staging/src/kubevirt.io/api/core/v1/types.go`):

```go
type MigrationConfiguration struct {
    // ... existing fields ...

    // MigrationCompression enables zstd compression of the live migration
    // data stream, reducing network bandwidth at the cost of additional CPU.
    // Defaults to false
    MigrationCompression *bool `json:"migrationCompression,omitempty"`
}
```

**`MigrationPolicySpec`** (in `staging/src/kubevirt.io/api/migrations/v1alpha1/types.go`):

```go
type MigrationPolicySpec struct {
    // ... existing fields ...

    //+optional
    MigrationCompression *bool `json:"migrationCompression,omitempty"`
}
```

### Feature gate

Add `MigrationCompression` to `pkg/virt-config/featuregate/active.go` at Alpha
state. The API field is only honored when the gate is enabled.

### Implementation

The change follows the same pattern as `AllowAutoConverge` and `AllowPostCopy`:

1. **virt-handler** (`pkg/virt-handler/migration-source.go`): Read the resolved
   migration configuration and propagate `MigrationCompression` into
   `MigrationOptions`.

2. **MigrationOptions** (`pkg/virt-handler/cmd-client/client.go`): Add
   `MigrationCompression bool` field.

3. **virt-launcher** (`pkg/virt-launcher/virtwrap/live-migration-source.go`):
   - In `generateMigrationFlags()`: When enabled, set
     `libvirt.MIGRATE_COMPRESSED`.
   - In migration params (`libvirt.DomainMigrateParameters`): Set
     `CompressionSet: true` and `Compression: "zstd"`.

4. **MigrationPolicy** (`staging/src/kubevirt.io/api/migrations/v1alpha1/types.go`):
   In `GetMigrationConfByPolicy()`, propagate `MigrationCompression` override
   the same way as `AllowAutoConverge`.

### How it maps to libvirt/QEMU

| KubeVirt API                      | libvirt flag / param                        |
|-----------------------------------|---------------------------------------------|
| `migrationCompression: true`      | `VIR_MIGRATE_COMPRESSED` flag               |
|                                   | `VIR_MIGRATE_PARAM_COMPRESSION = "zstd"`    |

When multifd is also active (enabled it by default when no CPU limit is set), QEMU
uses zstd compression on each multifd channel.
When multifd is not active, QEMU falls back to single-threaded zstd compression
of the main migration stream.

### Interaction with existing features

| Feature            | Interaction                                        |
|--------------------|----------------------------------------------------|
| Multifd (parallel) | Compatible — zstd runs per multifd channel          |
| Auto-converge      | Compatible — orthogonal mechanisms                  |
| Post-copy          | Compatible — compression applies to pre-copy phase  |
| TLS                | Compatible — compression runs before encryption     |
| Bandwidth limit    | Compatible — compressed data counts toward limit    |

## API Examples

### Cluster-wide via KubeVirt CR

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    migrations:
      migrationCompression: true
```

### Per-namespace via MigrationPolicy

```yaml
apiVersion: migrations.kubevirt.io/v1alpha1
kind: MigrationPolicy
metadata:
  name: compress-heavy-vms
spec:
  selectors:
    namespaceSelector:
      workload-type: memory-intensive
  migrationCompression: true
```

## Alternatives

1. **Expose full compression configuration** (method, level, threads):
   More flexible but significantly more API surface for uncertain benefit.
   Can be added in Beta if users need tuning knobs.

2. **Always enable compression**: Simpler, but compression does consume CPU.
   An explicit opt-in avoids regressions for CPU-constrained workloads.

3. **Use xbzrle instead of zstd**: xbzrle is a cache-based approach better
   suited for incremental dirty page detection. Zstd provides better
   general-purpose compression and works naturally with multifd.

## Scalability

No scalability concerns. Compression is handled entirely within the
virt-launcher process on the source node. The CPU overhead of zstd is modest
at compression level 1 and bounded by the migration bandwidth.

## Update/Rollback Compatibility

- **Update**: No impact. The field defaults to `nil` (disabled). Existing
  migrations are unaffected.
- **Rollback**: If the feature gate is removed after being enabled, the field
  is ignored and migrations proceed uncompressed. No persistent state changes.
- **Mixed-version clusters**: The source virt-launcher controls compression.
  The target does not need any changes — decompression is handled
  transparently by QEMU on the receiving end.

## Functional Testing Approach

1. **Unit tests**: Verify `generateMigrationFlags()` includes
   `MIGRATE_COMPRESSED` when the option is set, and params include
   `Compression: "zstd"`.
2. **Integration/E2E test**: Migrate a VMI with compression enabled, verify
   successful completion. Check migration job info for non-zero
   `compression_bytes` counter.
3. **Negative test**: Verify compression is not used when the feature gate is
   disabled, even if the API field is set.

## Implementation History

<!-- Updated as implementation progresses -->

## Graduation Requirements

### Alpha

- [ ] Feature gate `MigrationCompression` guards all code changes
- [ ] `MigrationCompression` field added to `MigrationConfiguration` and
      `MigrationPolicySpec`
- [ ] `MigrationPolicy.GetMigrationConfByPolicy()` propagates the override
- [ ] `virt-handler` passes the option through to `virt-launcher`
- [ ] `virt-launcher` sets `MIGRATE_COMPRESSED` flag and `compression=zstd`
      param on the libvirt migration call
- [ ] Basic E2E test: migration completes successfully with compression enabled
- [ ] Documentation in user guide

### Beta

- [ ] Soak testing on real workloads confirming CPU overhead is acceptable
- [ ] Optional: expose `CompressionLevel` parameter for tuning
- [ ] Compression bytes/pages metrics exposed via Prometheus
- [ ] Feature gate enabled by default

### GA

- [ ] Feature gate removed, compression available without gate
- [ ] Proven stability across multiple releases
