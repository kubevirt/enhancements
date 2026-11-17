# VEP #440: Prune CI Lanes for Unsupported Kubernetes Versions on Release Branches

## VEP Status Metadata

### Target releases

This VEP is a process/tooling change and does not follow the standard
Alpha/Beta/GA feature graduation model. The target version below reflects
when the automation is implemented and enabled.

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created
- [ ] (R) Target version is explicitly mentioned and approved
- [x] (R) Graduation criteria filled

## Overview

`kubevirt/kubevirt` supports the last 3 Kubernetes minor releases at any given
time. Each stable `kubevirt/kubevirt` release branch (`release-X.Y`) has its
own frozen presubmit job file in `kubevirt/project-infra`
(`github/ci/prow-deploy/files/jobs/kubevirt/kubevirt/kubevirt-presubmits-X.Y.yaml`),
pinned at branch-cut time to the Kubernetes minor versions that were supported
back then. For example, as of this writing `release-1.2` still runs presubmits
against k8s 1.27/1.28/1.29, and `release-1.9` runs against k8s
1.33/1.34/1.35/1.36.

As time passes, Kubernetes itself drops support for those pinned versions
independently of whether the owning KubeVirt release branch is still within
KubeVirt's own support window (see VEP [#233]). Nothing today prunes those
now-unsupported k8s lanes from an otherwise still-active release branch — they
keep running (or bit-rotting) indefinitely, consuming CI capacity and, when
the underlying `kubevirtci` provider for that k8s version is eventually
retired, failing outright with no product-relevant signal.

This VEP proposes extending the existing `robots/kubevirt remove jobs`
automation — which already prunes stale k8s lanes from `main` — to also prune
lanes from the per-release-branch presubmit files. Pruning is scoped **per
test suite** (e.g. `sig-compute` tested across several k8s versions is one
suite): only suites with more than one provider variant are touched, unused
provider variants are removed, and at least one variant per suite is always
kept. Suites that only ever run against a single k8s version in a given
branch are left untouched entirely, since there is no redundant provider
coverage to trim for them.

## Motivation

- `robots/kubevirt remove jobs` and `robots/kubevirt remove always_run`
  already implement this exact class of automation for `main`
  (`kubevirt-presubmits.yaml` / `kubevirt-periodics.yaml`), driven by the
  `kubernetes/kubernetes` GitHub releases API and running as a periodic Prow
  job (`periodic-kubevirt-job-remover`) in `project-infra`. The per-release-
  branch files (`kubevirt-presubmits-X.Y.yaml`) are entirely untouched by this
  or any other automation today — this is a gap, not a deliberate exclusion.
- VEP [#233] bounds the *number of active release branches* by retiring a
  branch's CI entirely once the KubeVirt release itself ages out. That does
  not address the case this VEP targets: a release branch that is still
  within KubeVirt's own support window, but individually tests against one or
  more Kubernetes minor versions that Kubernetes itself has already dropped.
  A branch like `release-1.9` can be fully within KubeVirt's 3-release support
  window while still testing against a k8s version that fell out of the
  upstream Kubernetes 3-release support window months earlier.
- Untested-but-still-configured lanes against unsupported k8s versions provide
  false confidence (they exercise a platform combination no user should be
  running in production) and waste CI compute that could go to supported
  lanes.
- When a `kubevirtci` provider for an old k8s version is eventually removed
  (tracked separately by `robots/kubevirtci-bumper`), any release branch still
  referencing it starts failing for reasons unrelated to the code under test,
  creating noise and manual cleanup work.

## Goals

- Automatically detect, per active `kubevirt/kubevirt` release branch and per
  test suite within it, which configured Kubernetes minor version lanes are
  no longer within the upstream Kubernetes support window.
- Remove the presubmit job definitions for those lanes from the
  branch-specific job config file only, and only for suites that actually
  have more than one provider variant to begin with.
- Guarantee every multi-provider test suite retains at least one working
  lane, even if every k8s version it was originally configured for has
  fallen out of support. Never touch suites that only ever ran against a
  single k8s version — they have no redundant coverage to trim.
- Reuse and extend existing `robots/kubevirt` tooling and its existing periodic
  Prow job wiring rather than introducing a parallel mechanism.

## Non Goals

- Retiring a release branch's CI entirely, or making any decision about
  whether a KubeVirt release itself is EOL — that is the domain of VEP
  [#233].
- Changing which Kubernetes versions `main` tests against, or the existing
  `main`-branch lane-sliding automation (`remove jobs` / `remove always_run` /
  `copy jobs` / `require presubmits`) — those are unaffected and continue to
  work exactly as today.
- Pruning periodic or postsubmit jobs for release branches — as of this
  writing, `kubevirt-periodics.yaml` only runs against `main`
  (`base_ref: main`) and `kubevirt-postsubmits.yaml` carries no k8s-version-
  specific lanes, so there is nothing to prune there for release branches.
- Removing or archiving the `kubevirtci` provider itself — that remains
  `kubevirtci-bumper`'s responsibility.
- Changing the definition of "supported Kubernetes versions" (last 3 minor
  releases) — this VEP reuses the same source of truth the existing `main`
  automation already relies on.

## Definition of Users

- **KubeVirt release maintainers**: benefit from bounded, self-cleaning CI
  configuration on release branches without manual bookkeeping.
- **KubeVirt contributors sending backports**: see fewer irrelevant/failing
  presubmit lanes on release-branch PRs.
- **SIG Release / CI infra maintainers (project-infra)**: own and operate the
  automation.

## User Stories

- As a **release maintainer**, I want release branches to stop testing
  against Kubernetes versions that are no longer supported upstream, so that
  CI capacity is spent on combinations users can actually run in production.
- As a **contributor** backporting a fix to an older release branch, I don't
  want my PR gated on a k8s lane that is failing only because the underlying
  provider is stale and unsupported, unrelated to my change.
- As a **CI infra maintainer**, I want this cleanup to happen automatically
  through the same tooling and periodic job that already does this for
  `main`, instead of a new one-off mechanism to maintain.

## Repos

- [kubevirt/project-infra](https://github.com/kubevirt/project-infra)

## Design

### Current state

- `pkg/kubevirt/cmd/remove/jobs.go` implements `kubevirt remove jobs`. It
  queries `kubernetes/kubernetes` GitHub releases, computes the latest 3
  supported minor versions via `pkg/querier`/`pkg/kubevirt/release`, and
  removes `main`-branch presubmit/periodic job definitions
  (`pull-kubevirt-e2e-k8s-<ver>-<sig>` /
  `periodic-kubevirt-e2e-k8s-<ver>-<sig>`) for versions older than that
  window, but only ever reads/writes `kubevirt-presubmits.yaml` and
  `kubevirt-periodics.yaml`.
- Each release branch's presubmit jobs live in their own file, scoped with
  `branches: [release-X.Y]`, e.g.
  `kubevirt-presubmits-1.9.yaml`, `kubevirt-presubmits-1.2.yaml`, etc., with
  job names following the same `pull-kubevirt-e2e-k8s-<ver>-<sig>` pattern
  used on `main`.
- This automation already runs unattended via the
  `periodic-kubevirt-job-remover` cron in `project-infra-periodics.yaml`,
  which shells out to `git-pr.sh --command "go run ./robots/kubevirt remove
  jobs ..." --branch remove-kubevirt-jobs --target main` to open a PR against
  `project-infra`'s `main` branch whenever the command changes a file.

### Proposed change

Extend `kubevirt remove jobs` (no new subcommand) with a new flag pointing at
the directory containing the per-release-branch presubmit files (default:
the same directory as `kubevirt-presubmits.yaml`, globbing
`kubevirt-presubmits-*.yaml` and excluding the bare main-branch file, which
continues to be handled by the existing logic in the same run).

For each matched release-branch file, in the same invocation that already
handles `main`:

1. Parse the k8s minor version out of every job name in the file (any job
   carrying a `k8s-<major>.<minor>` segment, not just the four canonical
   sigs — release branches in practice have many more specialized lanes than
   `main`, see the [PoC report](./poc-report.md)).
2. Group jobs into **suites**: jobs whose name is identical once the k8s
   version segment is blanked out belong to the same suite (e.g.
   `sig-compute` tested against 1.31/1.32/1.33 is one suite with three
   variants).
3. Suites with only one variant are skipped entirely — untouched, regardless
   of whether that single version is supported.
4. For suites with two or more variants, compute which variants are outside
   the current upstream-supported window (the same "latest 3 minor
   `kubernetes/kubernetes` releases" computation already performed once per
   run, shared across `main` and every release-branch file).
5. **Exception**: if every variant of a suite would be removed, keep the job
   for its single highest (most recent) variant instead, and only remove the
   rest. This guarantees a multi-provider suite never ends up with zero CI
   signal.
6. Remove the resulting set of presubmit entries from that file, and write
   back only files that actually changed.

The existing `git-pr.sh`-based periodic wiring is unchanged: a single run can
now touch `kubevirt-presubmits.yaml` plus zero or more
`kubevirt-presubmits-X.Y.yaml` files, and all changes from one run land in one
PR against `project-infra` `main`, exactly like today's multi-file changes
already do.

### Example

Given upstream Kubernetes has moved its supported window to 1.34/1.35/1.36,
and `kubevirt-presubmits-1.7.yaml` (scoped to `branches: [release-1.7]`) has:

- `sig-compute`/`sig-network`/`sig-operator`/`sig-storage` tested against
  1.32/1.33/1.34/1.35 (a 4-variant suite each) → 1.32 and 1.33 are outside
  the supported window and removed, 1.34 and 1.35 are kept.
- `sig-compute-realtime` tested only against 1.32 (a 1-variant suite) → left
  untouched even though 1.32 is unsupported, since there is no other variant
  to fall back to.

If instead a branch like `release-1.2` has `sig-compute` tested only against
1.27/1.28/1.29, and all three are outside the supported window, the exception
applies to that suite: 1.27 and 1.28 are removed, 1.29 (the highest) is kept.
A single-variant special suite pinned to 1.27 elsewhere in the same file
would be left alone.

See the [PoC report](./poc-report.md) for the full real-data run: 90 job
definitions removed across 11 of 12 release branches, with single-provider
suites on the oldest branches correctly surviving at k8s versions as old as
1.22.

## API Examples

N/A — this is a CI tooling/process change, not a KubeVirt API change. The
closest analog is the `robots/kubevirt remove jobs` CLI surface itself, which
gains one new flag (working name, subject to implementation review):

```
kubevirt remove jobs \
  --job-config-path-kubevirt-presubmits=.../kubevirt-presubmits.yaml \
  --job-config-path-kubevirt-periodics=.../kubevirt-periodics.yaml \
  --job-config-path-kubevirt-presubmits-release-branches=.../kubevirt/kubevirt/ \
  --dry-run=false
```

## Alternatives

### Alternative 1: New standalone subcommand instead of extending `remove jobs`

A separate `kubevirt remove release-jobs` command was considered. Rejected in
favor of extending `remove jobs`: the supported-version computation, job-name
parsing, and GitHub client setup are identical to what `remove jobs` already
does for `main`; a second command would duplicate that logic and require a
second periodic job entry to keep in sync.

### Alternative 2: One PR per changed release branch

Looping the periodic job's shell logic to invoke `git-pr.sh` once per changed
release-branch file (isolated PR/branch per release) was considered, for
independent reviewability/revertability. Rejected for the initial
implementation in favor of a single PR per periodic run, consistent with how
`remove jobs`/`copy jobs` already batch multi-file changes into one PR today;
this can be revisited if single batched PRs prove too noisy to review in
practice.

### Alternative 3: Manual/quarterly human review instead of automation

Relying on release maintainers to manually audit and prune release-branch
lanes periodically was considered. Rejected: this is exactly the kind of
easily-forgotten bookkeeping that the existing `main`-branch automation was
built to eliminate, and the same reasoning applies to release branches.

### Alternative 4: Time-based lane removal (e.g., N months after branch cut)

Removing lanes based on branch age rather than actual upstream Kubernetes
support status was considered. Rejected for the same reason VEP [#233]
rejected a time-based EOL trigger: it decouples the decision from the actual
signal that matters (whether Kubernetes itself still supports the version)
and can prune a still-supported version early or keep an unsupported one too
long depending on release cadence drift.

## Scalability

This reduces the number of active presubmit lanes across all release
branches proportionally to how many pinned k8s versions age out of upstream
support, complementing VEP [#233]'s bound on the number of active branches.
Combined, the two proposals bound both axes of release-branch CI growth:
number of branches (#233) and number of k8s lanes per branch (this VEP).

## Update/Rollback Compatibility

N/A — this only affects CI job configuration in `project-infra`, not any
KubeVirt runtime behavior, API, or upgrade path.

## Functional Testing Approach

- Unit tests in `pkg/kubevirt/cmd/remove/release_branch_jobs_test.go`
  (table-driven, mirroring existing coverage in `jobs_test.go`), covering:
  version extraction, normal multi-provider-suite pruning, the
  all-variants-unsupported exception (keep the latest variant), a no-op case
  where a suite's lanes are all still supported, and — the case a first
  draft of this proposal's PoC got wrong — single-provider suites being left
  untouched even when their one and only version is unsupported.
- `make test` continues to cover the whole `pkg/kubevirt/...` tree.
- The [PoC report](./poc-report.md) validates the logic against real,
  current job configs rather than only synthetic fixtures.
- The first live (non-dry-run) run is reviewed manually as a normal PR before
  the periodic job is trusted to run unattended with `--dry-run=false`,
  matching how the existing `main`-branch automation was rolled out.

## Implementation History

## Graduation Requirements

This is a process/tooling VEP and does not follow the standard Alpha/Beta/GA
feature graduation model. It uses an equivalent three-phase progression
instead:

### Phase 1: VEP

- [ ] VEP accepted by SIG Release

### Phase 2: Implementation

- [ ] `remove jobs` extended with the release-branch pruning flag and
      per-suite exception logic
- [ ] Unit tests added covering prune / exception / no-op cases
- [ ] Validated against real job configs (see the [PoC report](./poc-report.md))

### Phase 3: Wire to the scheduled jobs

- [ ] `periodic-kubevirt-job-remover` wiring updated to pass the new flag
- [ ] First run reviewed manually in `--dry-run=true` mode
- [ ] Periodic job runs unattended with `--dry-run=false`
- [ ] Finalized once the next new release branch is observed to confirm the
      keep-latest-variant exception leaves it with a running lane per suite,
      and single-provider suites are left untouched

[#233]: https://github.com/kubevirt/enhancements/blob/main/veps/sig-release/233-release-branch-eol-policy/vep.md
