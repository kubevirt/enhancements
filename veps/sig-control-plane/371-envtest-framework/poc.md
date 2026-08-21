# VEP #371 Proof of Concept

Supporting material for [`vep.md`](./vep.md). This appendix records the
per-PR breakdown, coverage matrix, and benchmark data for the working
proof of concept; it is not required to understand the feature or its
design.

A working PoC (`tests/envtest/` in kubevirt/kubevirt) demonstrates the
framework, posted as a series of focused PRs (superseding the original
PR #18238):

- [PR #18726][]: Framework base — envtest, controllers, pod simulator, lifecycle test
- [PR #18727][]: Instancetype controller support — synchronizer refactors, regression tests, upgrade tests
- [PR #18728][]: Generation tracking tests — including #18700 regression
- [PR #18729][]: Webhook admission support
- [PR #18730][]: CRD schema and CEL validation tests
- [PR #18731][]: PodSimulator hooks — failure and not-ready simulation

[PR #18726]: https://github.com/kubevirt/kubevirt/pull/18726
[PR #18727]: https://github.com/kubevirt/kubevirt/pull/18727
[PR #18728]: https://github.com/kubevirt/kubevirt/pull/18728
[PR #18729]: https://github.com/kubevirt/kubevirt/pull/18729
[PR #18730]: https://github.com/kubevirt/kubevirt/pull/18730
[PR #18731]: https://github.com/kubevirt/kubevirt/pull/18731

## Coverage

20 tests across 8 categories:

| Category | Tests | What they exercise |
|----------|-------|--------------------|
| VM lifecycle | 1 | VM → VMI → Pod → Scheduled |
| Generation tracking | 1 | ObservedGeneration/DesiredGeneration, ControllerRevision lifecycle |
| Regression | 2 | Bug #16719 (RestartRequired stuck), bug #16071 (stale preferenceRef) |
| Webhook admission | 2 | Mutating defaults, validating rejection |
| CRD validation | 2 | Structural schema, CEL rules |
| Domain XML | 1 | Real converter via gRPC |
| Workload update | 3 | Migration creation, phase gating, deployment ID gating |
| Instancetype upgrades | 8 | ControllerRevision upgrade from v1beta1 (migrated from functional tests) |

## Functional test migration benchmark

Instancetype ControllerRevision upgrades, functional vs. envtest:

| | Functional (sig-compute lane) | envtest (shared env) |
|---|---|---|
| 8 tests | 152.6s total, 19.1s avg | 11.4s total, 1.4s avg |
| Speedup | — | **13.4x** |
| Cluster required | Yes | No |
