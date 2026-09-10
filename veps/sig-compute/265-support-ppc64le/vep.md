# VEP-265: Support ppc64le Architecture in KubeVirt

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal outlines the technical requirements and implementation strategy to support official ppc64le (PowerPC 64-bit Little Endian) architecture support in the upstream KubeVirt main branch. The focus is on fixing the broken Bazel build infrastructure, establishing proper cross-compilation toolchains, and ensuring native ppc64le builds can succeed in CI/CD pipelines.

## Motivation

KubeVirt previously supported ppc64le but the support was removed due to infrastructure challenges and lack of sustained maintenance. With renewed commitment from IBM and the Power community, we propose to restore ppc64le support with proper CI infrastructure and long-term maintenance commitment.

### Historical Context

- **2020-02**: ppc64le support initially added (PR kubevirt/kubevirt#2944)
- **2020-03**: Builds disabled due to COPR infrastructure failures
- **2021-06**: Build system references removed, runtime code retained
- **2025-09**: All remaining ppc64le code removed due to lack of maintenance

### Why This Time Will Be Different

**Management Backing & Resources**
- Explicit backing from IBM management for upstream KubeVirt ppc64le support
- Dedicated engineering team assigned for maintenance
- Strategic initiative with allocated resources

**CI Infrastructure Commitment**
- Providing dedicated ppc64le hardware for CI/CD pipelines
- Self-hosted Prow runners maintained by our team

**Downstream Product Dependency**
- Building downstream offerings based on KubeVirt
- Product success depends on maintaining upstream support

**Improved Tooling**
- bazeldnf now has ppc64le support (rmohr/bazeldnf#48 merged)
- Stable CentOS Stream repositories

**Active Maintenance Plan**
- Dedicated team members as code owners
- Regular SIG participation
- Commitment to maintain CI green status

## Goals

1. **Official ppc64le images published from upstream CI**: KubeVirt's release pipeline produces and publishes multi-architecture container images that include a `ppc64le` variant, eliminating the need for downstream consumers to maintain custom builds.

2. **Native KubeVirt VM execution on ppc64le Kubernetes nodes**: Users can run `VirtualMachineInstance` workloads natively on IBM Power (ppc64le) nodes in a KubeVirt cluster using the same API and tooling as other supported architectures.

3. **Continuous validation of ppc64le in upstream CI**: Presubmit and periodic Prow jobs build, test, and validate ppc64le on IBM-provided hardware, keeping ppc64le a supported and green architecture in the upstream project.

4. **No regression to existing architectures**: All changes are additive; existing amd64, arm64, and s390x builds, tests, and images remain unaffected.

## Non Goals

- **Cross-Architecture VM Emulation**: Running x86_64 VMs on ppc64le hosts (or vice versa) through QEMU emulation
- **Big Endian Support**: ppc64 (big endian) architecture is explicitly excluded
- **Power-Specific Optimizations**: Performance tuning specific to Power processors (future enhancement)
- **Bazel Workspace Overhaul**: Complete redesign of build system (addressed separately if needed)

## Definition of Users

- **CI/CD Engineers**: Maintaining KubeVirt build infrastructure and release pipelines
- **Platform Engineers**: Deploying KubeVirt on IBM Power Systems infrastructure
- **Downstream Distributors**: Building KubeVirt packages for ppc64le distributions
- **Enterprise IT Teams**: Operating Power-based virtualization infrastructure
- **Open Source Contributors**: Developing and testing KubeVirt on Power hardware

## User Stories

- As a platform engineer, I want to deploy KubeVirt on my Kubernetes cluster on Power10/Power11 using official upstream images so that I do not need to maintain a custom fork or build pipeline
- As a downstream distributor, I need official ppc64le images and clear documentation so that I can package KubeVirt for my distribution without diverging from upstream
- As a contributor, I want to develop and test KubeVirt features on my Power workstation using the same upstream tooling as other architectures

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) - Core implementation and build system
- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) - CI/CD infrastructure and Prow job configuration
- [kubevirt/kubevirtci](https://github.com/kubevirt/kubevirtci) - Cluster provisioning tooling required for ppc64le e2e test environments
- [kubevirt/containerdisks](https://github.com/kubevirt/containerdisks) - Container disk images; ppc64le variants required for e2e testing and user-facing examples
- [brianmcarey/bazeldnf](https://github.com/brianmcarey/bazeldnf) - RPM dependency management tool (KubeVirt fork, used for all targets including ppc64le)
- [kubevirt/kubevirt.github.io](https://github.com/kubevirt/kubevirt.github.io) - Documentation

## Design

### Proof of Concept Validation

A proof-of-concept implementation has successfully demonstrated that ppc64le support is technically feasible:

1. **Go Toolchain Success**
   - Fixed Bazel 6.5.0 strict toolchain resolution by defining `ppc64le_strict` constraint in `user.bazelrc`
   - Proved that KubeVirt's Go codebase compiles flawlessly for PowerPC architecture
   - No code changes required in core KubeVirt components

2. **RPM Infrastructure Restoration**
   - Authored custom script to execute `bazeldnf` CLI for ppc64le
   - Successfully generated all 11 deterministic RPM trees:
     - `launcherbase`, `handlerbase`, `passt_tree`
     - `libvirt`, `qemu`, `seabios`, `edk2`, `swtpm`
     - Additional dependency trees for complete runtime environment
   - Updated `rpm/repo-cs10.yaml` to point to official CentOS Stream 10 HTTPS mirrors
   - Integrated PowerPC Virt SIG repository (`kvm-power`) for Power-specific packages

3. **Toolchain Workarounds**
   - Proved native compilation possible by injecting custom `bazeldnf_toolchain`
   - Manually downloaded ppc64le binary and registered in `tools/bazeldnf/BUILD.bazel`
   - Demonstrated path forward for official toolchain registration

### Proposed Architecture

#### Phase 1: RPM Infrastructure Restoration

**Update RPM Generation Scripts**

`hack/rpm-deps.sh` will be extended to include `ppc64le` in the existing architecture generation loop alongside `amd64`, `arm64`, and `s390x`. All 11 component RPM trees (`launcherbase`, `handlerbase`, `passt_tree`, `libvirt`, `qemu`, `seabios`, `edk2`, `swtpm`, and dependency trees) will be generated for ppc64le.

**Update Repository Configuration**

`rpm/repo-cs10.yaml` will be extended (not replaced) to include ppc64le-specific CentOS Stream 10 mirror entries for `BaseOS`, `AppStream`, `CRB`, and the Virt SIG (`kvm-power`) repository that provides Power-specific QEMU and libvirt packages.

**Generate Complete RPM Trees**

`bazeldnf` will be invoked for each component tree targeting `--arch ppc64le`, mirroring the existing generation for other architectures. The resulting `BUILD.bazel` entries are additive and do not affect other architectures.

#### Phase 2: Bazel Toolchain Registration

**Register rules_oci and aspect_bazel_lib Toolchains**

`oci_register_toolchains` in `WORKSPACE` will be extended to include `@platforms//cpu:ppc64le`. Similarly, `aspect_bazel_lib` toolchain registrations for `zstd`, `coreutils`, and `jq` will be extended with ppc64le variants. All changes are additive.

### Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| [VEP-392 / #393](https://github.com/kubevirt/enhancements/issues/393) Bazel removal lands during Alpha | Low | Phases 2 and 3 (Bazel toolchain work) will be dropped or adapted; RPM infrastructure, runtime, and CI phases are build-system agnostic and unaffected |
| ppc64le RPM mirror availability (CentOS Stream, Virt SIG) | High | Use multiple mirror entries; monitor mirror health in CI; IBM team owns escalation path |
| Self-hosted ppc64le Prow runners become unavailable | High | IBM Cloud PowerVS instances serve as a backup; CI jobs are non-blocking until GA |
| Changes to shared Bazel files (WORKSPACE, BUILD.bazel) cause regressions on amd64/arm64/s390x | High | All toolchain registrations are additive; PRs require passing CI for all existing architectures before merge |

#### Phase 3: Cross-Compilation Support

**Extend kubevirt/builder Container**

The `kubevirt/builder` Dockerfile will install the `gcc-powerpc64le-linux-gnu` cross-compiler toolchain so that CGO components can be compiled targeting `powerpc64le-linux-gnu` from an x86_64 build host.

**Register cc_toolchain for ppc64le**

A new `toolchain/cc_toolchain_config_ppc64le.bzl` will define a Bazel `cc_toolchain` targeting `powerpc64le-unknown-linux-gnu`, wired to the cross-compiler binaries installed in the builder. A `toolchain()` rule in `toolchain/BUILD.bazel` will constrain it to `exec = x86_64/linux`, `target = ppc64le/linux`. The implementation details belong in the corresponding PRs.

#### Phase 4: CI/CD Integration with Prow

**Prow Job Configuration**

Two categories of Prow jobs will be added in `kubevirt/project-infra`:

- **Build presubmit**: Runs `make bazel-build-images` on a ppc64le Prow node to validate that all container images build successfully for ppc64le. This is a build test, not an e2e test.
- **e2e periodic**: Provisions a transient ppc64le cluster using `kubevirtci`, deploys KubeVirt, and runs the upstream e2e test suite. Full e2e coverage depends on `kubevirtci` gaining ppc64le cluster provisioning support (a prerequisite tracked separately).

The exact Prow job definitions belong in the `project-infra` PR.

**Self-Hosted ppc64le Runners**

IBM will provide and maintain dedicated ppc64le bare-metal hardware as self-hosted Prow runners. IBM Cloud PowerVS instances serve as a backup. Hardware procurement is in progress.

**Multi-Architecture Manifest Generation**

The image publishing pipeline will be extended to include a `ppc64le` variant in each multi-arch manifest list, following the same pattern already used for `amd64`, `arm64`, and `s390x`.

### Runtime Configuration

**QEMU Machine Type Detection**

`pkg/virt-launcher/virtwrap/api/defaults.go` will be extended so that `ppc64le` resolves to the `pseries` QEMU machine type (Power Systems virtual machine), alongside the existing entries for `q35` (x86_64), `virt` (arm64), and `s390-ccw-virtio` (s390x).

No manual firmware path configuration is required: for the `pseries` machine type, QEMU/libvirt automatically selects SLOF (Slimline Open Firmware) without requiring explicit domain XML configuration.

## API Examples

### Basic ppc64le VM

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  name: fedora-ppc64le-vm
spec:
  architecture: ppc64le
  domain:
    cpu:
      cores: 4
    machine:
      type: pseries
    resources:
      requests:
        memory: 4Gi
    devices:
      disks:
      - name: containerdisk
        disk:
          bus: virtio
      - name: cloudinitdisk
        disk:
          bus: virtio
  volumes:
  - name: containerdisk
    containerDisk:
      image: quay.io/kubevirt/fedora-cloud-container-disk:ppc64le
  - name: cloudinitdisk
    cloudInitNoCloud:
      userData: |
        #cloud-config
        password: fedora
        chpasswd: { expire: False }
```

### Multi-Architecture Deployment

A ppc64le VM is scheduled on a Power node automatically via the existing KubeVirt architecture-aware scheduling, using the same `spec.architecture: ppc64le` field. No additional user-facing API changes are required beyond what is shown in the basic example above.

## Alternatives

### Alternative 1: Downstream-Only Support

**Description**: Document that ppc64le builds must use standard Docker/Podman multi-stage builds instead of Bazel until toolchain infrastructure is complete.

**Pros**:
- Immediate workaround for downstream distributors
- Uses native ppc64le host's gcc and dnf package manager
- No dependency on Bazel toolchain fixes

**Cons**:
- Diverges from upstream build methodology
- Requires maintaining separate build pipelines
- Cannot leverage Bazel's hermetic build guarantees
- Increases maintenance burden for downstream maintainers

**Decision**: Use as interim solution while upstream Bazel infrastructure is being fixed. Document clearly in build instructions.

### Alternative 2: Bazel Workspace Overhaul

**Description**: Complete redesign of KubeVirt's build system to use more modern Bazel patterns and toolchain registration.

**Pros**:
- Could solve multiple architecture support issues simultaneously
- Opportunity to modernize build infrastructure
- Better long-term maintainability

**Cons**:
- Massive scope, affects all architectures
- High risk of breaking existing builds
- Requires extensive testing across all platforms
- Delays ppc64le support significantly

**Decision**: Defer to separate enhancement proposal. Focus this VEP on minimal changes to support ppc64le.

### Alternative 3: Remove Bazel Dependency

**Description**: Migrate KubeVirt build system away from Bazel entirely to standard Go tooling and Makefiles.

**Pros**:
- Simpler build system
- Better Go ecosystem integration
- Easier for contributors to understand

**Cons**:
- Enormous migration effort
- Loss of Bazel's hermetic build benefits
- Would affect entire project, not just ppc64le

**Decision**: Rejected. Out of scope for this VEP.

## Scalability

### Build System Scalability

- **Parallel Builds**: Bazel's caching and parallelization work identically for ppc64le
- **CI Resource Usage**: ppc64le builds consume similar resources to other architectures
- **Artifact Storage**: Multi-arch manifests efficiently share layers across architectures

### Runtime Scalability

- **VM Density**: Power systems support high VM density due to superior memory bandwidth
- **Network Performance**: virtio-net scales well on Power architecture
- **Storage I/O**: Power systems' high I/O capabilities benefit virtualized workloads

### Multi-Architecture Cluster Scalability

- Heterogeneous clusters with mixed architectures supported
- Scheduling constraints ensure VMs run on appropriate architecture nodes
- No additional control plane overhead for ppc64le nodes

## Update/Rollback Compatibility

### Update Compatibility

**Bazel Workspace Changes**:
- All toolchain registrations are additive
- Existing x86_64, arm64, s390x builds unaffected
- New `ppc64le_strict` constraint only applies when building for ppc64le

**RPM Infrastructure**:
- New ppc64le RPM trees added alongside existing architecture trees
- No changes to existing architecture RPM definitions
- Repository configuration extended, not replaced

**Container Images**:
- Multi-arch manifests maintain backward compatibility
- Existing single-arch image references continue to work
- New ppc64le images added to manifest lists

### Rolling Upgrade Compatibility

ppc64le support is delivered through multi-arch container images and architecture-aware node scheduling, following the same model as arm64 and s390x. No runtime feature gate is used.

**Version skew**: All ppc64le-specific changes are additive (new RPM trees, new toolchain registrations, new image layers). A cluster running a mixed version of KubeVirt components during a rolling upgrade will not be affected by the presence or absence of ppc64le images.

**Build system**: Toolchain registrations and RPM tree entries can be removed without affecting other architectures if ppc64le support needs to be suspended.

**Note**: KubeVirt does not support cluster rollback. Downgrade compatibility is not a design requirement.

## Functional Testing Approach

### Unit Testing

**Toolchain Detection Tests**:
Unit tests will cover the `getDefaultMachineType` function to verify that `ppc64le` resolves to `pseries` and that existing architecture mappings remain unchanged.

**Build System Tests**:
- Verify RPM tree generation for ppc64le
- Validate Bazel toolchain resolution
- Test cross-compilation from x86_64 to ppc64le

### Integration Testing

**Native ppc64le Build Tests**:
Bazel builds and unit tests will be executed on a ppc64le host as part of the CI pipeline to validate native compilation.

**Cross-Compilation Tests**:
Builds targeting `linux_ppc64le` will be executed on x86_64 hosts to validate the cross-compilation toolchain.

**Container Image Tests**:
Multi-arch manifest inspection will verify that a `ppc64le` entry is present in published image manifests.

### End-to-End Testing

**VM Lifecycle Tests**:
- Create ppc64le VM on Power node
- Verify VM boots successfully
- Test VM migration between ppc64le nodes
- Validate storage and network functionality

**CI/CD Pipeline Tests**:
- Automated build on ppc64le runner
- Image publishing to registry
- Multi-arch manifest creation
- Deployment validation

### Hardware Requirements

**Development Testing**:
- Access to Power9 or Power10 systems
- Minimum 16GB RAM, 8 cores
- CentOS Stream 9/10 or RHEL 9 installation

**CI/CD Infrastructure**:
- Self-hosted Prow runner on dedicated ppc64le bare-metal hardware (provided by IBM)
- Backup: IBM Cloud PowerVS instances

## Implementation History

- **2025-08**: VEP created and submitted for review
- **2026-02**: Proof of concept validation completed — Go toolchain compiles cleanly for ppc64le; all 11 RPM trees generated successfully
- **2026-04**: Phase 1 (RPM infrastructure) completed — `hack/rpm-deps.sh` updated, `rpm/repo-cs10.yaml` configured with ppc64le CentOS Stream 10 and Virt SIG repositories
- **2026-05**: Phase 2 (Bazel toolchains) completed — `bazeldnf` ppc64le binary registered, `rules_oci` and `aspect_bazel_lib` toolchains configured for ppc64le

## Maintenance

Long-term maintenance of ppc64le support is owned by the IBM Power team within the KubeVirt community.

### Maintainers

| Name | GitHub | Affiliation |
|------|--------|-------------|
| Paul Bastide | [@prb112](https://github.com/prb112) | IBM |
| Punith Kenchappa | [@pkenchap](https://github.com/pkenchap) | IBM |
| Guna K Kambalimath | [@GunaKKIBM](https://github.com/GunaKKIBM) | IBM |

### Maintenance Commitments

- **Dedicated hardware**: IBM provides and maintains self-hosted ppc64le Prow runners for CI/CD pipelines.
- **Bug triage**: ppc64le-specific issues will be triaged and resolved by the IBM team within the working group SLA.
- **Release validation**: The team will validate each KubeVirt release on ppc64le hardware prior to graduation milestones.
- **Upstream engagement**: Maintainers are active members of the [WG Arch ppc64le](https://github.com/kubevirt/community/tree/main/wg-arch-ppc64le) and participate in regular community meetings.


## Graduation Requirements

### Alpha

- [ ] **RPM infrastructure**: All 11 component RPM trees generated for ppc64le; `hack/rpm-deps.sh` updated; `rpm/repo-cs10.yaml` extended with ppc64le CentOS Stream 10 and Virt SIG repositories
- [ ] **Bazel toolchain registration**: `rules_oci` and `aspect_bazel_lib` toolchains registered for ppc64le
- [ ] **Cross-compilation**: `gcc-powerpc64le-linux-gnu` added to `kubevirt/builder`; Bazel `cc_toolchain` configured for `exec = x86_64/linux → target = ppc64le/linux`; CGO components cross-compile successfully from x86_64
- [ ] **CI/CD**: Prow build presubmit job operational on IBM-provided ppc64le hardware; multi-arch container images including ppc64le published to quay.io
- [ ] **Runtime**: `pseries` machine type correctly selected for ppc64le VMs
- [ ] **kubevirtci**: ppc64le cluster provisioning supported in kubevirtci (prerequisite for e2e)
- [ ] Unit tests pass for ppc64le-specific code paths
- [ ] Documentation for building and deploying KubeVirt on ppc64le published

### Beta

- [ ] Prow e2e periodic job operational using a kubevirtci-provisioned ppc64le cluster
- [ ] Core VM lifecycle tests pass on ppc64le hardware (create, boot, delete)
- [ ] Live migration tested between ppc64le nodes
- [ ] Performance benchmarks documented and no significant regression vs. other architectures
- [ ] Extended community testing completed with no blocking issues

#### On-By-Default Readiness

N/A — ppc64le support is delivered via multi-arch images and node scheduling, not guarded by a runtime feature gate. No on-by-default enablement step applies.

### GA

- [ ] ppc64le has remained green in upstream CI for at least 3 consecutive KubeVirt releases
- [ ] At least one confirmed production deployment on ppc64le
- [ ] Full e2e test suite passing on ppc64le hardware
- [ ] Comprehensive user-facing documentation merged