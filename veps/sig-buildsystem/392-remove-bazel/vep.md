# VEP #392: Remove Bazel from KubeVirt Build System

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.11.0 (development during v1.10.0 via fork)
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This VEP proposes the complete removal of Bazel from the KubeVirt project. Bazel is currently used across the entire build lifecycle: compiling Go binaries, running unit tests, running functional tests, building container images, managing RPM dependencies via bazeldnf, and cross-compiling for non-amd64 architectures. The existing Makefiles already serve as the developer-facing entry point but currently delegate to Bazel commands underneath.

This VEP outlines a phased migration strategy replacing Bazel with standard tooling: `go build`/`go test` for compilation and testing, Containerfiles for image builds, and bazeldnf invoked as a standalone CLI (without Bazel) for RPM dependency resolution. The initial implementation PR demonstrates the full non-Bazel build path including binary compilation, unit/functional tests, base and component image builds, and multi-arch support, all without invoking Bazel.

## Motivation

Bazel has been deeply integrated into KubeVirt's build system since early in the project, handling binary compilation, testing, container image builds, RPM dependency management, and cross-compilation. While Bazel provides strong caching and reproducibility guarantees, it has become a significant source of friction and tech debt:

- **Lack of community knowledge**: Very few contributors understand the Bazel build setup, creating a bus-factor risk and slowing down contributions.
- **Architecture limitations**: s390x and ppc64le (Power) are not supported by upstream Bazel, requiring workarounds like cross-building on x86 or manually building Bazel for those platforms. This also makes it difficult for new architectures (e.g., RISC-V) to join the ecosystem in the future.
- **Kubernetes divergence**: Kubernetes has already dropped Bazel. Staying aligned with Kubernetes tooling simplifies the contributor experience.
- **Blocks automation**: `BUILD.bazel` files break standard Go tooling and prevent the use of renovatebot/dependabot for automated dependency updates. Security updates in release branches must be created manually. For example:
  - Tools like `go mod tidy`, `go vet`, `golangci-lint`, and IDE features expect a standard Go module layout. However, `BUILD.bazel` files add non-standard dependency declarations that conflict when dependency graphs diverge between `go.mod` and Bazel's `WORKSPACE`.
  - Dependabot and Renovatebot can automatically bump dependencies in `go.mod`, but they cannot update `BUILD.bazel` files (which requires running `gazelle`). As a result, dependency updates must be done manually.
  - When a CVE is found, Dependabot can automatically patch every release branch. With Bazel, someone must manually run `gazelle` and update Bazel files on each branch, which is slow and error-prone for security-critical fixes.
- **Contributor friction**: The non-standard build setup raises the barrier to entry for new contributors.

Replacing Bazel with standard Go tooling and Containerfiles solves these by leveraging tools that every Go developer already knows, that work natively on all supported architectures, and that integrate seamlessly with dependency automation. Upstream Kubernetes made the same transition and the Go ecosystem provides all the primitives needed — native cross-compilation, build caching via `GOCACHE`, and standard module management.

References:
- https://github.com/kubevirt/kubevirt/issues/14038
- Kubernetes KEP-2420 (prior art): https://github.com/kubernetes/enhancements/tree/master/keps/sig-testing/2420-reducing-kubernetes-build-maintenance

## Goals

- Completely remove Bazel from the KubeVirt project
- Replace Bazel-based image builds with standard multi-stage Containerfiles
- Replace Bazel-based Go compilation with standard `go build`
- Replace Bazel-based testing with standard `go test` and existing test frameworks
- Decouple base image building from the main KubeVirt build process using Containerfiles
- Remove Bazel from RPM dependency resolution using bazeldnf as a standalone CLI
- Replace Bazel-based cross-compilation with Go's native cross-compilation
- Replace Bazel's build caching with Go build cache (`GOCACHE`) and container layer caching
- Maintain support for all currently supported architectures (amd64, arm64, s390x)
- Enable standard Go tooling and dependency management workflows
- Align with Kubernetes build patterns and tooling

## Non Goals

- Removing Bazel in a single PR (the migration is phased to minimize risk)
- Changing the developer-facing Makefile interface (Make targets remain the same except `bazel-*` targets which will be removed, and only the underlying implementation changes)
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
- As a developer building for s390x, I want to build and test without having to build Bazel from source as a workaround for Bazel's lack of s390x support.
- As a new contributor, I want to understand the build system quickly so that I can contribute without a steep learning curve.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/project-infra](https://github.com/kubevirt/project-infra) (prow job definitions need updating to use non-Bazel targets)

## Design

The migration is phased to incrementally replace each Bazel responsibility with standard tooling.

This applies to the development (main) branch only. Existing release branches will not be modified and continue to use Bazel.

### Development approach

During the v1.10.0 cycle, implementation happens in a fork of `kubevirt/kubevirt` so that:
- The existing Bazel build in `kubevirt/kubevirt` is not at risk of breakage during development
- Bespoke prow jobs can be set up in `project-infra` targeting the fork, giving full CI signal on the new build path without flooding `kubevirt/kubevirt` presubmits or breaking existing jobs
- The two build systems are completely isolated during development rather than needing a switching mechanism within the same repo

Once validated, the fork's changes are merged into main targeting v1.11.0 for Alpha. Alpha and Beta changes are upstreamed from the fork to main.

### Phase 1: Replace Bazel for builds and tests (Alpha)

- **Base images**: Decouple RPM-dependent base images from the main build using Containerfiles — see [kubevirt#18286](https://github.com/kubevirt/kubevirt/pull/18286).
- **Component images**: Each KubeVirt component image gets a multi-stage Containerfile that uses pre-built base images, copies in Go-compiled binaries, and configures the runtime environment.
- **Binary compilation**: Replace Bazel `go_binary` targets with standard `go build` commands executed inside multi-stage Containerfiles.
- **Testing**: Replace `go_test` Bazel targets with standard `go test` invocations. Unit tests and functional tests run directly via Go tooling.
- **Cross-compilation**: Go's built-in cross-compilation (`GOOS`/`GOARCH`) for pure Go binaries. C cross-compilation for `cmd/container-disk-v2alpha/main.c` (requires a C cross-compiler toolchain). CGO cross-compilation for `virt-launcher` (libvirt) and `pkg/virt-handler/node-labeller/kvm-caps-info-plugin_amd64.go` (KVM capability detection, amd64 only). Multi-arch container builds via `podman build --platform` or `buildx`.
- **RPM dependency resolution**: RPM resolution uses bazeldnf invoked as a standalone CLI, without Bazel. `hack/rpm-deps.sh` runs `bazeldnf fetch` and `bazeldnf rpmtree` to resolve and pin RPM packages, while `hack/rpm-base-images/generate-rpm-tars.sh` downloads the pinned RPMs and produces rootfs tars via `bazeldnf rpm2tar`. See [kubevirt#18534](https://github.com/kubevirt/kubevirt/pull/18534).

### Dependency fetch resilience

Today, Bazel's remote HTTP cache (`bazel-cache.kubevirt-prow.svc.cluster.local`) provides resilience against upstream unavailability: once a Bazel rule that fetches and processes an RPM or other external dependency has run successfully, its output is cached and subsequent builds never re-fetch from upstream. The non-Bazel build must provide equivalent resilience.

**Go modules**: KubeVirt vendors all Go dependencies in the `vendor/` directory (checked into git). Builds use `-mod=vendor` and `GOPROXY=off`, so zero network access is needed to resolve or download Go modules at build time. This is unchanged by the Bazel removal and already matches the hermeticity Bazel provided for Go dependencies.

**RPM packages**: RPM URLs and SHA checksums are pinned in `WORKSPACE` (checked into git), providing the same determinism as Bazel's `rpm()` rules. Each RPM entry in `WORKSPACE` already includes both an upstream CentOS mirror URL and a GCS mirror URL (`storage.googleapis.com/builddeps/<sha256>`). Base images are decoupled from the main build — regular CI pulls pre-built base images from the registry and never fetches RPMs directly. Only the "rebuild base images" workflow (triggered when RPM dependencies change) needs to download RPM packages. The `generate-rpm-tars.sh` script tries the GCS mirror first and falls back to upstream mirrors, providing the same resilience that Bazel's multi-URL fetching provided.

**Container base images**: Base image references in Containerfiles will be pinned by digest (`@sha256:...`) rather than mutable tags. This ensures immutability and makes builds fail deterministically rather than silently pulling a changed image. The builder image is already pinned to a specific version.

### Phase 2: Build caching and Bazel removal in fork (Beta)

Without caching, the non-Bazel build path is functional but slower (~2x). This phase addresses performance and prepares for full Bazel removal:

- **Go build cache**: Back up and restore `GOCACHE` in CI for incremental compilation
- **Go test cache**: GCS-backed test cache, keyed per architecture (amd64/arm64/s390x). Periodic jobs run the full test suite hourly and upload the cache to GCS. Presubmit jobs restore this shared cache before running tests, significantly reducing wall-clock time when source hasn't changed. Inspired by the approach used by Kubernetes ([test-infra#16623](https://github.com/kubernetes/test-infra/pull/16623)).
- **Container layer caching**: Leverage registry-based or local layer caching for image builds
- **Bazel file removal in fork**: Remove all `BUILD.bazel` files, `WORKSPACE`, `.bazelrc`, and Bazel-related tooling in the fork for testing purposes. The main repo still retains Bazel files since Bazel-based builds continue to run until GA.

### Phase 3: Stability and full migration (GA)

Stability and performance are demonstrated to be comparable to Bazel. All Bazel-based prow jobs are permanently migrated to non-Bazel targets. Bazel files are removed from the main repository and `bazel-*` Make targets are dropped.

## API Examples

N/A — This is a build system change with no user-facing API impact.

## Alternatives

1. **Switch to another hermetic build system (e.g., Buck2, Please)**: Even less community familiarity than Bazel and doesn't align with the Kubernetes ecosystem.
2. **Keep Bazel but invest in documentation and training**: Doesn't address the fundamental issues — s390x support, automated dependency updates, and tooling compatibility remain broken regardless of how well Bazel is documented.

## Scalability

Go's native compilation is fast and parallelizes well across CPU cores. Cross-compilation allows building all architecture variants from a single machine. Build caching via container layer caching and Go build cache (`GOCACHE`) is expected to bring performance close to Bazel's incremental builds.

CI parallelism remains unchanged — jobs are split by SIG/component as before.

## Update/Rollback Compatibility

This change does not affect the runtime behavior of KubeVirt. The produced container images are functionally identical to Bazel-built images. Existing deployment manifests, operators, and upgrade paths remain unchanged.

Backporting to older release branches that still use Bazel is expected to work without issues for pure code fixes (bug fixes, security patches), since the Go source code is build-system agnostic. However, backports that touch build infrastructure (new binaries, new dependencies, new container components) will require manual adaptation to work with the Bazel-based build in those branches.

## Functional Testing Approach

1. **Image build verification**: Verify all KubeVirt component images build successfully for all supported architectures (amd64, arm64, s390x).
2. **E2E test suite**: Run the full existing e2e test suite against images built with Containerfiles. Each phase must pass the complete CI suite before merging.
3. **CI performance**: Validate CI build times are within acceptable range compared to Bazel builds.

## Implementation History

- 2025-02-25: Discussion initiated in issue [#14038](https://github.com/kubevirt/kubevirt/issues/14038)
- 2026-06-30: Initial draft PR to decouple base image build [#18286](https://github.com/kubevirt/kubevirt/pull/18286)
- 2026-07-21: Exploring bazeldnf standalone (without Bazel) for RPM dependency management [kubevirt#18534](https://github.com/kubevirt/kubevirt/pull/18534)
- 2026-07-30: GCS-backed Go test cache for unit tests [kubevirt#18645](https://github.com/kubevirt/kubevirt/pull/18645), [project-infra#5337](https://github.com/kubevirt/project-infra/pull/5337)

## Graduation Requirements

### Alpha
- [ ] Base images (RPM-dependent) decoupled and built via Containerfiles
- [ ] RPM dependency resolution no longer requires Bazel
- [ ] All KubeVirt component images buildable via Containerfiles (including cross-compilation for CGO and C components)
- [ ] Non-Bazel path for functional tests demonstrated and validated
- [ ] Non-Bazel path for unit tests demonstrated and validated
- [ ] All existing e2e tests pass with Containerfile-built images
- [ ] Multi-arch support (amd64, arm64, s390x) verified with new build path

### Beta
- [ ] Build caching implemented and validated in CI (container layer caching, `GOCACHE`, GCS-backed test cache)
- [ ] Dependency fetch resilience validated:
- [ ] Go module vendoring verified (`-mod=vendor`, `GOPROXY=off` — no network fetch at build time)
- [ ] RPM downloads use existing GCS mirror (`gs://builddeps`) with fallback to upstream mirrors
- [ ] Base images pinned by digest in all Containerfiles
- [ ] Bazel files removed in fork for testing
- [ ] Alpha and Beta changes upstreamed from fork to main
- [ ] Documentation updated for contributors

#### On-By-Default Readiness

The non-Bazel build path is validated and upstreamed to main with caching in place. Bazel-based builds still run in parallel until GA confirms stability and performance are comparable.

### GA
- [ ] Stability and performance comparable to Bazel
- [ ] Dependency fetch resilience confirmed under production CI load (mechanisms established in Beta)
- [ ] All Bazel-based prow jobs permanently migrated to non-Bazel targets. Jobs currently using Bazel:
  - `pull-kubevirt-build` / `pull-kubevirt-build-arm64` / `pull-kubevirt-build-s390x`
  - `pull-kubevirt-build-cs10` / `pull-kubevirt-build-cs10-arm64` / `pull-kubevirt-build-cs10-s390x`
  - `pull-kubevirt-unit-test` / `pull-kubevirt-unit-test-arm64`
  - `pull-kubevirt-generate`
  - `pull-kubevirt-verify-rpms` / `pull-kubevirt-verify-rpms-cs10`
  - `pull-kubevirt-verify-go-mod`
  - `pull-kubevirt-gosec`
  - `pull-kubevirt-goveralls`
  - `pull-kubevirt-apidocs`
  - `pull-kubevirt-client-python`
  - `pull-kubevirt-manifests`
  - `pull-kubevirt-prom-rules-verify`
  - `pull-kubevirt-e2e-*` (all e2e jobs use `make bazel-build` for image builds)
  - Note: Release branch jobs are not modified
- [ ] All `BUILD.bazel`, `WORKSPACE`, and Bazel-related files removed from main repository
- [ ] `bazel-*` Make targets removed
- [ ] Dependabot/renovatebot enabled for automated dependency updates
- [ ] Standard Go tooling (`go mod`, `go vet`, `golangci-lint`) works without workarounds
