# VEP #0180: Add support for riscv64

## Release Signoff Checklist

Items marked with (R) are required _prior to targeting to a milestone / release_.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

<!--
Provide a brief overview of the topic)
-->

This VEP is primarily dedicated to introducing riscv64 support for kubevirt and some involved components/repos. Thanks to Go’s strong (though not yet complete) support for riscv64, we do not need to make many changes to achieve good riscv64 support. Most of the required modifications are related to third-party dependencies and some test-related components. Through testing, kubevirt has been shown to run well on riscv64.

## Motivation

<!--
Why this enhancement is important
-->

Recently, riscv64 has seen widespread adoption and is receiving increasing attention. As an open-source instruction set architecture, supporting riscv64 is highly meaningful for the open-source community. Official riscv64 support in kubevirt would be significant in expanding the application scenarios for both kubevirt and riscv64.

## Goals

<!--
The desired outcome
-->

1. Switch third-party dependencies that do not support riscv64 to versions that do support riscv64 (mostly via forks).
2. Add riscv64 support to kubevirt.
3. Add riscv64 support to other repo.

## Non Goals

<!--
Why this enhancement is important Limitations to the scope of the design
-->

1. Performance issues on riscv64.
2. more tests for riscv64.
3. CI builds for riscv64.

## Definition of Users

<!--
Who is this feature set intended for
-->

Users who want to run kubevirt on riscv64.

## User Stories

<!--
List of user stories this design aims to solve
-->

1. As a riscv64 developer, I want to develop and test kubevirt-related features on riscv64, so I am very eager to introduce riscv64 support to kubevirt.
2. As an open-source enthusiast, running the open-source kubevirt on open hardware is of significant importance.
3. As a DevOps engineer / SRE, using open-source riscv64 together with kubevirt to run day-to-day web applications is highly meaningful for meeting requirements around open and controllable software and hardware.

## Repos

<!--
List of repose this design impacts
-->

Maybe all repositories will need to be modified.

Here are the repos that need direct riscv64 support added. More repos to be added in the future

1. kubevirt/kubevirt
2. kubevirt/monitoring
3. kubevirt/containerized-data-importer

## Design

<!--
This should be brief and concise. We want just enough to get the point across
-->

Adding riscv64 support to kubevirt is a lengthy but not complex task. Our primary focus is to ensure that kubevirt can successfully pass `make test` and `make go-all`, and that it can start an `Alpine` virtual machine correctly on riscv64.

From kubevirt’s perspective, the overall code logic does not require substantial changes to support riscv64, because Go provides solid support for this architecture (with a few missing pieces, which will be discussed later). Therefore, the work we need to address can be divided into four parts, and the overall PR effort can be advanced accordingly along these four areas.

1. Ensure that third-party dependencies can run on riscv64. This is a prerequisite before discussing how to add riscv64 support to kubevirt itself. This part is very time-consuming and involves many repositories, which I will elaborate on below.
2. Add riscv64 support to kubevirt. This is a relatively straightforward part of the work and can be achieved with only minor modifications.
3. Test kubevirt on riscv64. This includes two aspects: first, ensuring that the introduced PRs do not break existing functionality, meaning that all existing tests on x86 and arm64 continue to pass; second, ensuring that all tests can pass on riscv64.
4. Start an Alpine virtual machine on riscv64. This part mainly involves first bringing up a k8s cluster on riscv64, then deploying kubevirt, and finally starting an Alpine VM. Once we can connect to it by `virtctl`, the entire scope of this VEP can be considered complete.

Fortunately, I have implemented all of this in my own fork. The details can be found [here](https://github.com/ffgan/kubevirt/tree/rv64).

Below, I describe my implementation process.

### 1. Third-party dependencies that require changes

Since kubevirt relies heavily on Bazel-related dependencies, I spent a significant amount of time adding riscv64 support for this part. Unfortunately, the dependency graph is quite large. I have submitted PRs to as many relevant upstream projects as possible in order to obtain native support, but due to time constraints, not all PRs have been reviewed and merged. As a result, we still need to rely on forked repositories to obtain riscv64 support.

For these repositories, my recommendation is to migrate the forks into the kubevirt organization and depend on these migrated forks until upstream projects fully support riscv64.

( I will continue contributing to upstream efforts, and full support is ultimately a matter of time. )

#### 1.1 io_bazel_rules_go

For `rules_go`, I introduced riscv64 support in this [PR](https://github.com/bazel-contrib/rules_go/pull/4507). Upgrading `rules_go` to v0.59.0 provides riscv64 support.

However, `rules_go` introduced a bug fix in v0.58.0 via this [PR](https://github.com/bazel-contrib/rules_go/pull/4439), which causes issues with cross-compiling `cmd/virtctl` in kubevirt. Reproducing this issue is straightforward: as noted in the [v0.58.0](https://github.com/bazel-contrib/rules_go/releases/tag/v0.58.0) release notes, simply upgrading `rules_go` to `v0.58.0`on x86 is sufficient to observe the problem. I believe this issue requires a more in-depth discussion to determine the best solution. For convenience, I have temporarily commented out the cross-compilation logic for `cmd/virtctl`.

After upgrading `rules_go` to `v0.59.0`, riscv64 support in `rules_go` becomes available.

#### 1.2 rules_oci

rules_oci has a fairly large number of dependencies, which I do not intend to enumerate here, as doing so would significantly increase the length of this document.

For detailed riscv64 support, please refer to my [fork](https://github.com/ffgan/rules_oci/tree/add-rv64). I have created a tag and published a corresponding [release](https://github.com/ffgan/rules_oci/releases/tag/v2.2.6.1). By using this release, rules_oci can be used successfully on riscv64.

(If you have any questions about how rules_oci supports riscv64, please feel free to @ me directly. I can provide a separate reply with details or explain it in any other form as needed.)

#### 1.3 bazeldnf

Regarding riscv64 support in bazeldnf, I have already submitted and merged a [PR](https://github.com/rmohr/bazeldnf/pull/161). However, a new release has not yet been published, so a forked version is still required here.

When running `make rpm-deps` on x86, bazeldnf is involved. Due to a long-standing unresolved [bug](https://github.com/rmohr/bazeldnf/issues/114), this issue reappears when I run the command on x86 and update riscv64-related dependencies. This bug is very likely related to how bazeldnf invokes maxsat and interacts with package repositories. I am still investigating this problem.

In practical terms, this means that we need to switch to a newer version of bazeldnf, but the riscv64 package repository I am using cannot currently be handled correctly by bazeldnf. As a result, `make rpm-deps` fails. But this does not prevent us from updating dependencies through other means to complete riscv64 support. Once this issue is resolved, we should be able to return to using `rpm-deps` smoothly.

#### 1.4 rules_docker

As for rules_docker, since Bazel recommends migrating to rules_oci, I do not believe it is necessary to submit a riscv64 support PR upstream (even though the repository was unarchived after being archived, it may only be a matter of time before it is archived again). Therefore, we need to rely on a forked version here.

#### 1.5 go_image_base_riscv64

Since `gcr.io/distroless/base-debian12` does not yet support riscv64, I used the same image as the builder, `registry.risc-vers.cn/wg-cloudcomputing/openeuler:24.03-lts`, as a replacement. I consider this to be necessary. I attempted to use images such as `debian:13.3` and `debian:13.3-slim` as `go_image_base_riscv64`, but likely due to environmental inconsistencies with the builder base image, the resulting images would encounter `core dump` errors when executing any command other than Bash built-ins.

Therefore, I believe that using an image consistent with the builder is a viable approach.

### 2. Adding riscv64 Support to kubevirt

This section contains the core content of our PR and the primary set of changes made to the kubevirt repository. I will break it down into several parts: Bazel, the Go version, the build builder, and other components.

#### 2.1 Bazel

With regard to Bazel, the official Bazel project has not yet formally added riscv64 support. In the latest Bazel 8.x and 9.x series, Bazel can be built from source to obtain a working binary, whereas older versions require a significant number of patches to function. In order to run Bazel on riscv64, I chose to use Bazel 6.5.0 maintained by [openruyi](https://openruyi.cn/). More details can be found [here](https://code.openruyi.cn/risc-verse/wg-cloudcomputing/artifacts/bazel/-/releases).

Again, If you have any questions about the Bazel build provided here, please feel free to @ me directly.

#### 2.2 Go Version Considerations

As mentioned earlier, Go has good support for riscv64 in general. However, support for `-race` on riscv64 requires Go 1.26, which is a limitation I have encountered in practice. Aside from this, I have not observed other Go-related issues.

Go 1.26 is expected to be released in February of this year. At that point, we can upgrade the Go version to 1.26. For now, we can keep the current Go version unchanged, with the corresponding workaround being to temporarily comment out the use of `-race`. Once the Go version is upgraded, the comments can simply be removed. This is not a significant issue.

#### 2.3 Building the Builder Image

For the builder image, the base image used on x86 and aarch64 is `centos:stream9`, which does not yet support riscv64.

Therefore, I used `registry.risc-vers.cn/wg-cloudcomputing/openeuler:24.03-lts` as the base image. This image is based on openEuler, also uses RPM, and provides solid support for riscv64. To maintain consistency, all other riscv64-related RPM usage is aligned with this distribution. Based on practical usage, the results have been satisfactory.

Since Sonobuoy does not support riscv64, we need to build a riscv64-compatible Sonobuoy ourselves. This is not a major issue. I am currently working on adding riscv64 support to Sonobuoy, and I will update this section if there is any progress.

Other than that, there is no impact on the builder images for other architectures.

The resulting image is hosted in our own registry. The specific image tag is `registry.risc-vers.cn/wg-cloudcomputing/kubevirt-builder:2601082230-a023c1e20f-riscv64`, and it has been working without any issues.

#### 2.4 Other Components

There are still many repositories that do not yet support riscv64, but this does not affect our current VEP, so we can temporarily ignore those repositories.

For some required images, such as Fedora, distributions like CentOS and Fedora do provide riscv64 support, but they have not yet been officially released on Docker Hub. If necessary, we can discuss this issue further.

As for Cirros, I have already merged a [PR](https://github.com/cirros-dev/cirros/pull/126) adding riscv64 support, but it has not been officially released.

(Additionally, I am attempting to add riscv64 support for UEFI boot on Cirros, but there are no results yet.)

### 3. Testing on riscv64

The testing is divided into two parts: one set of tests on x86, and another on riscv64.

As mentioned earlier, we need to temporarily disable the use of `-race`, so I commented out that part.

For the tests on x86, I have already verified them, and the results show that everything works as expected, with no impact on x86.

For the riscv64 tests, I essentially duplicated the arm64 test setup and adapted it directly for riscv64; in other words, no riscv64-specific tests were newly introduced.

For `make test` and `make integ-test`, the results indicate that everything is in good shape. Since functest involves too many repositories, I have not run the full functest suite for now. I believe this should be addressed separately by opening another VEP to achieve full functest coverage on riscv64.

( For one of the repositories involved in functest, namely CDI, I have also opened a separate [issue](https://github.com/kubevirt/containerized-data-importer/issues/3948) to provide a detailed explanation.)

Based on these results, we can be confident that kubevirt on riscv64 can run reliably.

### 4. Launching Alpine on riscv64's kubevirt

Below, I briefly describe how to run kubevirt on riscv64 and successfully launch an Alpine VM.

Unfortunately, Kubernetes support for riscv64 is still under active discussion, so we need to deploy Kubernetes and kubevirt ourselves before starting Alpine.

For deploying Kubernetes on riscv64, I used a modified k3s version based on [this repository](https://github.com/CARV-ICS-FORTH/kubernetes-riscv64). The detailed steps are rather lengthy and are therefore omitted here.

(Again, If you are interested, please feel free to @ me, and I will be happy to share the full procedure.)

After the Kubernetes deployment is complete, we can proceed to deploy kubevirt. I have already completed `bazel-push-images`, so we can deploy directly using yaml manifests. The images referenced in those manifests are available from [this registry](https://registry.risc-vers.cn/harbor/projects/2/repositories).

Please refer to [this link](https://github.com/ffgan/kubevirt-rv64/releases/tag/20260116) for the YAML files.

```bash
$ kubectl label node node_name cpu-model.node.kubevirt.io/rv64=true

$ tree .
#.
#├── 20260116.zip # Using the YAML files downloaded from the link above.
#└── kubevirt-rv64-20260116
#    ├── kubevirt-cr.yaml
#    ├── kubevirt-operator.yaml
#    └── vmi-alpine-efi.yaml

$ k3s kubectl apply -f ./kubevirt-rv64-20260116/kubevirt-operator.yaml
$ k3s kubectl apply -f ./kubevirt-rv64-20260116/kubevirt-cr.yaml

# then we can deploy alpine vmi
$ k3s kubectl apply -f ./kubevirt-rv64-20260116/vmi-alpine-efi.yaml

```

Then we wait for all the pods in the cluster to reach the Running state.

```bash
$ kubectl get po -A
NAMESPACE     NAME                                     READY   STATUS      RESTARTS      AGE
default       virt-launcher-vmi-alpine-efi-knqk8       3/3     Running     0             36m
kube-system   coredns-5f499f5dcb-pwt7x                 1/1     Running     1 (15h ago)   22h
kube-system   helm-install-traefik-crd-6bmsb           0/1     Completed   0             22h
kube-system   helm-install-traefik-x79fd               0/1     Completed   2             22h
kube-system   local-path-provisioner-ff964b654-ngkkc   1/1     Running     1 (15h ago)   22h
kube-system   metrics-server-56f4447f89-bllgk          1/1     Running     1 (15h ago)   22h
kube-system   svclb-traefik-bd738d0d-rzgx5             2/2     Running     2 (15h ago)   22h
kube-system   traefik-646c7c9654-d885j                 1/1     Running     1 (15h ago)   22h
kubevirt      virt-api-c5b968544-ts6kq                 1/1     Running     0             40m
kubevirt      virt-controller-689d7ccd95-jghql         1/1     Running     0             39m
kubevirt      virt-controller-689d7ccd95-twh62         1/1     Running     0             39m
kubevirt      virt-handler-k4m2c                       1/1     Running     0             39m
kubevirt      virt-operator-584489874-ks8fm            1/1     Running     0             43m
kubevirt      virt-operator-584489874-lvgcc            1/1     Running     0             43m
```

Then we can enter the Alpine VM.

```bash
$ virtctl console vmi-alpine-efi
...

 * Starting busybox syslog ... [ ok ]
 * Starting firstboot ... [ ok ]

Welcome to Alpine Linux 3.23
Kernel 6.18.0-3-lts on riscv64 (/dev/ttyS0)

localhost login: root
Welcome to Alpine!

The Alpine Wiki contains a large amount of how-to guides and general
information about administrating Alpine systems.
See <https://wiki.alpinelinux.org/>.

You can setup the system with the command: setup-alpine

You may change this message by editing /etc/motd.

localhost:~# uname -a
Linux localhost 6.18.0-3-lts #4-Alpine SMP PREEMPT_DYNAMIC 2025-12-02 22:50:17 riscv64 Linux
localhost:~#
```

At this point, we have successfully added riscv64 support to kubevirt, and testing has confirmed that Alpine can be launched on riscv64.

## API Examples

<!--
Tangible API examples used for discussion
-->

None

## Alternatives

<!--
Outline any alternative designs that have been considered)
-->

None

## Scalability

<!--
Overview of how the design scales)
-->

None

## Update/Rollback Compatibility

<!--
Does this impact update compatibility and how?)
-->

None

## Functional Testing Approach

<!--
An overview on the approaches used to functional test this design)
-->

Temporarily unavailable

## Implementation History

<!--
For example:
01-02-1921: Implemented mechanism for doing great stuff. PR: <LINK>.
03-04-1922: Added support for doing even greater stuff. PR: <LINK>.
-->

Temporarily unavailable

## Graduation Requirements

<!--
The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Feature gate guards all code changes
- [ ] Initial implementation supporting only X and Y use-cases

### Beta
- [ ] Implementation supports all X use-cases

It is not necessary to have all the requirements for all stages in the initial VEP.
They can be added later as the feature progresses, and there is more clarity towards its future.

Refer to https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases for more details
-->

### Alpha

- [ ] Provide riscv64 support for all dependencies that do not yet support riscv64
- [ ] Add riscv64 support
- [ ] Pass `make test` and `make integ-test`
- [ ] Successfully launch on riscv64

### Beta

TBD

### GA

TBD
