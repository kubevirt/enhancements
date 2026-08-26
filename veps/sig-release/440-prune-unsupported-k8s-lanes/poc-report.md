# PoC Report: Prune CI Lanes for Unsupported Kubernetes Versions on Release Branches

Companion PoC results for [vep.md](./vep.md). This ran the extended
`robots/kubevirt remove jobs` (new `--job-config-path-kubevirt-presubmits-release-branches`
flag) against a scratch copy of the real `kubevirt/project-infra` job configs, using
the live `kubernetes/kubernetes` GitHub releases API (unauthenticated). Nothing in the
real repository was modified — the run was performed against throwaway temp copies of
`github/ci/prow-deploy/files/jobs/kubevirt/kubevirt/kubevirt-presubmits*.yaml` and
`kubevirt-periodics.yaml`.

Upstream-supported window resolved at run time to **k8s 1.34 / 1.35 / 1.36**.

## Executive summary

- **90 presubmit job definitions removed** across 11 of 12 release branches
  (319 → 229 versioned lanes, a 28% reduction).
- Pruning operates **per test suite**, not per branch: a "suite" is a group of jobs
  that are identical except for the k8s version they target (e.g. `sig-compute` run
  against 1.31/1.32/1.33 in one file). Only suites with more than one provider variant
  are ever touched. If every variant of a multi-provider suite is unsupported, its
  single most recent variant is kept so the suite is never left without any lane.
  Suites that only ever run against one k8s version in a given branch are **left
  untouched entirely**, regardless of how old that version is.
- This matches what the real job configs actually look like: in **11 of 12**
  branches, the only suites with more than one provider variant are the canonical
  `sig-compute`, `sig-network`, `sig-operator`, `sig-storage` lanes. A handful of
  branches (`0.58`, `0.59`, `1.0`, `1.1`) additionally have 1-2 "special" suites that
  happen to also carry more than one provider variant (`sig-compute-migrations`,
  `sig-compute-cgroupsv2`, `sig-storage-cgroupsv2`, `ipv6-sig-network`) and were
  pruned the same way.
- `release-1.9` had **zero removals** — every multi-provider suite in that branch is
  already within the current 3-release Kubernetes support window (1.34/1.35/1.36).
- `kubevirt-presubmits.yaml` (main) and `kubevirt-periodics.yaml` were untouched.
- Single-provider "special" suites (e.g. `sig-compute-realtime`, `sig-monitoring`,
  `single-node`, `swap-enabled`, `sig-performance`) routinely survive pinned to very
  old k8s versions (down to 1.22 on `release-0.58`) because they were never
  duplicated across providers to begin with — there is nothing redundant to trim for
  them, so this proposal intentionally leaves them alone.

## Per-branch results

| Branch | Jobs before | Jobs after | Jobs removed | Canonical sig suites pruned | Extra multi-provider suites pruned |
|---|---:|---:|---:|---|---|
| release-0.58 | 30 | 21 | 9 | sig-compute, sig-network, sig-storage, operator (1.22, 1.23 → 1.24) | sig-compute-migrations (1.23 → 1.24) |
| release-0.59 | 39 | 28 | 11 | sig-compute, sig-network, sig-storage (1.24, 1.25 → 1.26) | sig-compute-cgroupsv2, sig-storage-cgroupsv2 (1.24, 1.25 → 1.26); ipv6-sig-network (1.25 → 1.26) |
| release-1.0 | 23 | 14 | 9 | sig-compute, sig-network, sig-operator, sig-storage (1.25, 1.26 → 1.27) | ipv6-sig-network (1.25 → 1.26) |
| release-1.1 | 24 | 15 | 9 | sig-compute, sig-network, sig-operator, sig-storage (1.26, 1.27 → 1.28) | ipv6-sig-network (1.26 → 1.27) |
| release-1.2 | 22 | 14 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.27, 1.28 → 1.29) | — |
| release-1.3 | 23 | 15 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.28, 1.29 → 1.30) | — |
| release-1.4 | 24 | 16 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.29, 1.30 → 1.31) | — |
| release-1.5 | 24 | 16 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.30, 1.31 → 1.32) | — |
| release-1.6 | 24 | 16 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.31, 1.32 → 1.33) | — |
| release-1.7 | 32 | 24 | 8 | sig-compute, sig-network, sig-operator, sig-storage (1.32, 1.33 → 1.34, 1.35 kept) | — |
| release-1.8 | 27 | 23 | 4 | sig-compute, sig-network, sig-operator, sig-storage (1.33 → 1.34, 1.35 kept) | — |
| release-1.9 | 27 | 27 | 0 | none — all supported | — |
| **Total** | **319** | **229** | **90** | | |

## Example: surviving single-provider suites on the oldest branches

For `release-0.58`, after pruning, jobs still referencing k8s 1.22/1.23 remain —
correctly — because they belong to suites with only one provider variant in that
file:

```
pull-kubevirt-e2e-k8s-1.22-sig-performance-0.58
pull-kubevirt-e2e-k8s-1.22-sig-compute-realtime-0.58
pull-kubevirt-e2e-k8s-1.22-sig-monitoring-0.58
pull-kubevirt-e2e-k8s-1.23-single-node-0.58
pull-kubevirt-e2e-k8s-1.23-swap-enabled-0.58
pull-kubevirt-e2e-k8s-1.23-sig-compute-cgroupsv2-0.58
```

None of these are duplicated at another k8s version within `release-0.58`, so there
is no redundant provider coverage to trim — they are left exactly as they were.

## Correctness checks performed

- Cross-checked one pruned job (`pull-kubevirt-e2e-k8s-1.24-sig-compute-cgroupsv2`,
  `release-0.59`) against its `TARGET` env var (`k8s-1.24-sig-compute`) to confirm
  the version match wasn't a name coincidence.
- Verified `kubevirt-presubmits.yaml` (main) and `kubevirt-periodics.yaml` are
  byte-identical before/after the run.
- Unit tests added in `pkg/kubevirt/cmd/remove/release_branch_jobs_test.go` cover:
  version extraction, normal multi-provider pruning, the all-unsupported
  keep-latest-variant exception, and — the case this report's methodology caught a
  regression in during review — single-provider suites being left untouched even
  when their only version is unsupported.
