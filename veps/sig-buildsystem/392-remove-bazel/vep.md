# VEP #392: Remove Bazel from KubeVirt Build System

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: 
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes the complete removal of Bazel from the KubeVirt project. Bazel is currently used across the entire build lifecycle: compiling Go binaries, running unit tests, running functional tests, building container images, managing RPM dependencies via bazeldnf, and cross-compiling for non-amd64 architectures. The existing Makefiles already serve as the developer-facing entry point but currently delegate to Bazel commands underneath.

This VEP outlines a phased migration strategy replacing Bazel with standard tooling: `go build`/`go test` for compilation and testing, and Containerfiles for image builds. The approach for RPM dependency management is yet to be decided — options being explored include using bazeldnf as a standalone tool (without invoking Bazel) or an alternative method. As a first step, the base images that require RPM packages are being decoupled from the main KubeVirt build so they can be built independently using Containerfiles.

## Motivation

Bazel has been deeply integrated into KubeVirt's build system since early in the project, handling binary compilation, testing, container image builds, RPM dependency management, and cross-compilation. While Bazel provides strong caching and reproducibility guarantees, it has become a significant source of friction and tech debt:

- **Lack of community knowledge**: Very few contributors understand the Bazel build setup, creating a bus-factor risk and slowing down contributions.
- **Architecture limitations**: s390x is not supported by upstream Bazel, requiring workarounds like cross-building on x86 or manually building Bazel for s390x.
- **Kubernetes divergence**: Kubernetes has already dropped Bazel. Staying aligned with Kubernetes tooling simplifies the contributor experience.
- **Blocks automation**: `BUILD.bazel` files break standard Go tooling and prevent the use of renovatebot/dependabot for automated dependency updates. Security updates in release branches must be created manually.
- **Contributor friction**: The non-standard build setup raises the barrier to entry for new contributors.

Replacing Bazel with standard Go tooling and Containerfiles solves these by leveraging tools that every Go developer already knows, that work natively on all supported architectures, and that integrate seamlessly with dependency automation. Upstream Kubernetes made the same transition and the Go ecosystem provides all the primitives needed native cross-compilation, build caching via `GOCACHE`, and standard module management.

Reference: https://github.com/kubevirt/kubevirt/issues/14038

## Goals

- Completely remove Bazel from the KubeVirt project
- Replace Bazel-based image builds with standard multi-stage Containerfiles
- Replace Bazel-based Go compilation with standard `go build`
- Replace Bazel-based testing with standard `go test` and existing test frameworks
- Decouple base image building from the main KubeVirt build process using Containerfiles (RPM dependencies are still resolved via Bazel-invoked bazeldnf initially)
- Remove Bazel from RPM dependency resolution (approach TBD — bazeldnf standalone is being explored)
- Replace Bazel-based cross-compilation with Go's native cross-compilation or multi-arch container builds
- Replace Bazel's build caching with Go build cache (`GOCACHE`) and container layer caching
- Maintain support for all currently supported architectures (amd64, arm64, s390x)
- Enable standard Go tooling and dependency management workflows
- Align with Kubernetes build patterns and tooling
- Retain the existing Makefile interface while replacing the Bazel commands underneath

## Non Goals

- Removing Bazel in a single PR (the migration is phased to minimize risk)
- Changing the developer-facing Makefile interface (Make targets remain the same, only the underlying implementation changes)
- Modifying the CI/CD pipeline architecture (only the build steps within it change)
- Rewriting existing test logic (tests remain the same, only the test runner changes from Bazel to `go test`)

## Definition of Users

- **KubeVirt developers and contributors**: Benefit from a simpler, more approachable build system using standard tooling
- **CI/CD maintainers**: Benefit from reduced complexity in build infrastructure
- **Release engineers**: Benefit from easier backports and automated dependency updates
- **New contributors**: Lower barrier to entry without needing to learn Bazel

## User Stories

- As a KubeVirt contributor, I want to build container images using standard Containerfiles so that I don't need Bazel expertise to make build changes.
- As a CI maintainer, I want to use standard container build tools so that the build pipeline is easier to debug and maintain.
- As a release engineer, I want to use dependabot/renovatebot so that security patches can be automated across release branches.
- As a developer building for s390x, I want to use standard multi-arch build tools so that I don't need workarounds for Bazel's lack of s390x support.
- As a new contributor, I want to understand the build system quickly so that I can contribute without a steep learning curve.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) (prow job definitions need updating to use non-Bazel targets)

## Design

The migration is phased to incrementally replace each Bazel responsibility with standard tooling while keeping the existing Makefile interface stable.

### Phase 1: Decouple base images

Base images that require RPM packages are decoupled from the main KubeVirt build. These are built independently using Containerfiles. For RPM dependency resolution, invoking bazeldnf directly as a standalone tool (without Bazel) has been explored and is working — see [enhancements#393](https://github.com/kubevirt/enhancements/pull/393) for details.

### Phase 2: Containerfile-based component images

Each KubeVirt component (virt-operator, virt-api, virt-controller, virt-handler, virt-launcher) gets a multi-stage Containerfile that:
- Uses the pre-built base images from Phase 1
- Copies in Go-compiled binaries
- Configures the runtime environment

### Phase 3: Replace Bazel for binary compilation

Replace Bazel `go_binary` targets with standard `go build` commands invoked from the Makefile. Go's native cross-compilation (`GOOS`/`GOARCH`) replaces Bazel's cross-compilation for arm64 and s390x.

### Phase 4: Replace Bazel for testing

Replace `go_test` Bazel targets with standard `go test` invocations. Unit tests and functional tests run directly via Go tooling.

### Phase 5: Build caching strategy

Before Bazel can be fully removed, its caching capabilities must be replaced:

- **Go build cache**: Back up and restore `GOCACHE` in CI for incremental compilation
- **Container layer caching**: Leverage registry-based or local layer caching for image builds
- **Makefile-level caching**: Use file timestamps and checksums to skip unnecessary rebuild steps

### Phase 6: Cross-compilation

Before Bazel can be fully removed, its cross-compilation capabilities must be replaced:

- Go's built-in cross-compilation (`GOOS=linux GOARCH=arm64 go build`) for binaries
- Multi-arch container builds via `podman build --platform` or `buildx` for images

### Phase 7: Remove Bazel entirely

Once caching, cross-compilation, and RPM dependency management are all handled without Bazel, remove all `BUILD.bazel` files, `WORKSPACE`, `.bazelrc`, and Bazel-related tooling from the repository.

## API Examples

N/A — This is a build system change with no user-facing API impact.

## Alternatives

1. **Switch to another hermetic build system (e.g., Buck2, Please)**: Even less community familiarity than Bazel and doesn't align with the Kubernetes ecosystem.
2. **Keep Bazel but invest in documentation and training**: Doesn't address the fundamental issues — s390x support, automated dependency updates, and tooling compatibility remain broken regardless of how well Bazel is documented.

## Scalability

Go's native compilation is fast and parallelizes well across CPU cores. Containerfile-based builds scale horizontally via parallel image builds per architecture. Build caching via container layer caching and Go build cache (`GOCACHE`) provides comparable performance to Bazel's incremental builds. Multi-arch builds can leverage `podman build --platform` or `buildx` for cross-platform support.

CI parallelism remains unchanged — jobs are split by SIG/component as before.

## Update/Rollback Compatibility

This change does not affect the runtime behavior of KubeVirt. The produced container images are functionally identical to Bazel-built images. Existing deployment manifests, operators, and upgrade paths remain unchanged.

Backporting to older release branches that still use Bazel is expected to work without issues for pure code fixes (bug fixes, security patches), since the Go source code is build-system agnostic. However, backports that touch build infrastructure (new binaries, new dependencies, new container components) will require manual adaptation to work with the Bazel-based build in those branches.

## Functional Testing Approach

1. **Image build verification**: Verify all KubeVirt component images build successfully for all supported architectures (amd64, arm64, s390x).
2. **E2E test suite**: Run the full existing e2e test suite against images built with Containerfiles. Each phase must pass the complete CI suite before merging.
3. **Image parity**: Compare image contents between Bazel-built and Containerfile-built images to ensure functional equivalence.
4. **CI performance**: Validate CI build times are within acceptable range compared to Bazel builds.

## Implementation History

- 2025-02-25: Discussion initiated in issue [#14038](https://github.com/kubevirt/kubevirt/issues/14038)
- 2026-06-30: Initial draft PR to decouple base image build [#18286](https://github.com/kubevirt/kubevirt/pull/18286)
- 2026-07-21: Exploring bazeldnf standalone (without Bazel) for RPM dependency management [enhancements#393](https://github.com/kubevirt/enhancements/pull/393)

## Graduation Requirements

### Alpha
- [ ] Base images (RPM-dependent) decoupled and built via Containerfiles (RPM resolution approach TBD)
- [ ] All KubeVirt component images buildable via Containerfiles
- [ ] Bazel no longer used for binary compilation
- [ ] Bazel no longer used for functional tests
- [ ] Bazel no longer used for unit tests
- [ ] All existing e2e tests pass with Containerfile-built images
- [ ] Multi-arch support (amd64, arm64, s390x) verified with new build path

### Beta
- [ ] RPM dependency resolution no longer requires Bazel
- [ ] Build caching strategy implemented and validated in CI
- [ ] Cross-compilation handled without Bazel
- [ ] CI (prow jobs) fully migrated to non-Bazel targets
- [ ] Documentation updated for contributors

#### On-By-Default Readiness

The non-Bazel build path becomes the sole build method. Contributors no longer need Bazel installed or configured. The Makefile interface remains unchanged.

### GA
- [ ] All `BUILD.bazel`, `WORKSPACE`, and Bazel-related files removed from repository
- [ ] At least one full release cycle completed without Bazel
- [ ] No regressions in build time or CI performance
- [ ] Dependabot/renovatebot enabled for automated dependency updates
- [ ] Standard Go tooling (`go mod`, `go vet`, `golangci-lint`) works without workarounds
