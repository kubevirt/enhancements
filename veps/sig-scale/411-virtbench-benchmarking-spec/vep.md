# VEP #411: Standardized Benchmarking Specification for KubeVirt

## VEP Status Metadata

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
PRs that will not update the VEP during the planning phase will not be able to graduate the
VEP by creating a code PR to kubevirt/kubevirt to bump the phase in-code.

Please avoid targeting future releases in this section. Only capture the upcoming release.
For example, during the planning phase for version v1.123, do **not** target beta for v.124 in advance.
-->

This VEP does not propose an in-tree KubeVirt API, controller, feature gate,
or runtime behavior change. It defines an out-of-tree benchmarking
specification and an initial reference implementation.

- This VEP targets alpha for version: N/A (This VEP defines an out-of-tree
  benchmarking specification and does not target a KubeVirt release
  version.)
- This VEP targets beta for version: N/A
- This VEP targets GA for version: N/A

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved, if release targeting is required
- [ ] (R) Beta target version is explicitly mentioned and approved, if release targeting is required
- [ ] (R) GA target version is explicitly mentioned and approved, if release targeting is required

### Owning SIG

- Owning SIG: `sig-scale`
- Participating SIGs: `sig-compute`, `sig-storage`, `sig-network` as needed
- VEP owner: Dhruv Bhatnagar
- Reviewers / approvers: TBD by owning SIG

## Overview

This VEP proposes a standardized benchmarking specification for
KubeVirt-focused performance, scale, and operational testing. The
specification defines a versioned scenario catalog, common measurement
points, a machine-readable result schema, CLI expectations, and minimum
documentation requirements for repeatable VM-centric benchmarks.

The existing open-source Virtbench project is proposed as the initial
reference implementation. Virtbench currently provides a CLI-driven
benchmark suite for VM provisioning, guest readiness, live migration,
storage I/O, failure recovery, and related KubeVirt operational scenarios.

The proposed framework is delivered as out-of-tree tooling and does not
introduce a KubeVirt API, controller, feature gate, or in-tree code path.
Graduation in this VEP refers to the maturity of the benchmark
specification, result schema, documentation, governance, and reference
implementation, not to a KubeVirt runtime feature.

The immediate purpose of this VEP is to give KubeVirt maintainers, SIGs,
vendors, and downstream users a common place to review the Virtbench scope,
decide whether the benchmark definitions are useful to the broader
community, and agree on expectations for neutrality, governance, schema
stability, and roadmap.

## Motivation

KubeVirt operators, storage and network vendors, downstream distributions,
and release engineers frequently need to answer VM-centric performance and
scale questions, including:

- How long does it take to provision a large number of VMs?
- How quickly do VMs reach guest readiness after creation or start?
- How does a storage backend behave during a boot storm?
- How does live migration behave at scale?
- How does the cluster recover when a node or component fails?
- How do in-guest I/O characteristics compare across storage backends,
  VM templates, or KubeVirt versions?

Today, these questions are often answered using ad-hoc scripts,
per-vendor harnesses, or environment-specific test automation. Results are
difficult to compare because workloads, VM images, measurement points,
result schemas, concurrency settings, and reporting formats differ.

A shared benchmark specification enables:

- More reproducible KubeVirt performance experiments.
- Clearer comparison of results across environments, storage backends,
  network configurations, and KubeVirt versions.
- A common result format for dashboards, CI systems, and regression
  analysis.
- A community-reviewed place to define benchmark semantics and avoid
  ambiguous performance claims.
- A path for vendors and downstream distributions to contribute scenarios
  without each maintaining a completely separate harness.

## Goals

- Define a canonical catalog of KubeVirt benchmark scenarios covering VM
  provisioning, boot storm, live migration, storage I/O, and failure
  recovery.
- Define exact measurement semantics for the metrics emitted by each
  scenario.
- Define a versioned, machine-readable result schema using JSON and CSV
  artifacts.
- Define the result provenance fields required to interpret a benchmark run,
  including KubeVirt version, Kubernetes / OpenShift version, CDI version,
  benchmark tool version, VM template, storage class, access mode, volume
  mode, and relevant cluster shape.
- Provide a single CLI surface as the reference implementation, using
  `virtbench` as the initial implementation.
- Allow alternative implementations as long as they implement the same
  scenario definitions and emit the same result schema.
- Provide cluster prerequisite validation that fails fast when the target
  cluster is not in a supported state for the selected scenario.
- Be runnable against conformant KubeVirt installations, while explicitly
  documenting scenario-specific prerequisites.
- Define ownership, contribution, and governance expectations for community
  review of benchmark scenarios.
- Provide an incremental Alpha scope that can be maintained, tested, and
  documented reliably.

## Non Goals

- Shipping benchmark code or feature gates inside `kubevirt/kubevirt`.
- Defining official KubeVirt performance SLOs or pass/fail thresholds.
  The framework produces measurements; interpretation is left to consumers.
- Publishing upstream vendor rankings or declaring one storage / network
  provider better than another.
- Claiming official KubeVirt performance numbers from any single
  environment.
- Replacing functional, conformance, or end-to-end test suites such as
  `kubevirt/kubevirt` e2e tests.
- Replacing Kubernetes-level scale tools such as kube-burner.
- Benchmarking the Kubernetes control plane in isolation.
- Requiring production clusters to run disruptive benchmark scenarios.
- Requiring every scenario to run on every KubeVirt deployment.
- Providing a hosted result service. Result storage, dashboard hosting, and
  long-term trend storage are out of scope.
- Making Virtbench the only possible implementation of the benchmark
  specification.

## Definition of Users

- **KubeVirt cluster operators** evaluating, sizing, or validating a
  deployment.
- **Storage vendors** characterizing CSI, clone, snapshot, volume mode, and
  migration behavior with KubeVirt workloads.
- **Network vendors** characterizing VM readiness, migration, and guest
  connectivity behavior.
- **KubeVirt maintainers, release engineers, and SIG leads** investigating
  regressions across KubeVirt versions or validating release candidates.
- **Downstream distributions** such as OpenShift Virtualization and vendor
  appliances running pre-release or qualification testing.
- **CI, scale-test, and performance pipelines** producing trend data over
  time.
- **Contributors and reviewers** who need a reproducible workload definition
  when discussing performance-related issues.

## User Stories

- As a cluster operator, I can run a named benchmark scenario against my
  cluster and obtain JSON / CSV reports describing VM lifecycle timings and
  failures.
- As a storage vendor, I can run boot-storm and in-guest I/O scenarios on a
  candidate CSI driver and compare results against a prior baseline using a
  documented result schema.
- As a release engineer, I can run the Alpha scenario catalog against a
  KubeVirt release candidate and compare results against a previous release
  to surface potential regressions.
- As a KubeVirt maintainer, I can review a benchmark result and understand
  exactly which scenario version, tool version, KubeVirt version, CDI
  version, Kubernetes version, storage class, VM template, and parameters
  produced the numbers.
- As a SIG-scale reviewer, I can ask a contributor to reproduce a reported
  performance issue by pointing to a named scenario and parameter set.
- As a chaos / scale engineer, I can run failure-recovery scenarios to
  characterize how VM workloads behave during node or component disruption.

## Repos

- `kubevirt/enhancements` — this VEP.
- Reference implementation repository hosting the `virtbench` CLI and
  scenario implementations. The current external location is:
  `https://github.com/portworx/kubevirt-benchmark`.
- Long-term home to be confirmed with the owning SIG. Moving the reference
  implementation under the `kubevirt` GitHub organization is a candidate
  path, but the final governance model is discussed separately in this VEP.
- No changes are proposed to `kubevirt/kubevirt`,
  `kubevirt/containerized-data-importer`, or other in-tree repositories.

## Governance and Ownership

During Alpha, the benchmark specification is reviewed through the KubeVirt
enhancements process, while the reference implementation remains in its
current external repository. The reference implementation is currently
maintained by a named set of Portworx (Everpure) maintainers via the
repository's existing CODEOWNERS / maintainer list. This is the interim
answer for who is responsible for fixes and patches during Alpha, and is
superseded by whichever Beta-stage governance model is adopted below.

Before Beta, the project will either:

- move under the `kubevirt` GitHub organization, or
- remain external with an explicitly documented governance, maintainer,
  contribution, and neutrality model accepted by the owning SIG.

The benchmark specification should remain implementation-neutral. Virtbench
is the initial reference implementation, but other tools may implement the
same scenario definitions and result schema.

Community ownership expectations include:

- documented maintainers for the reference implementation;
- a contribution process for adding scenarios, metrics, and schema fields;
- review from affected SIGs for scenarios that stress storage, network,
  migration, or scale behavior;
- a documented compatibility policy for result schema changes;
- clear language that benchmark results are environment-specific and are not
  official upstream KubeVirt performance claims unless explicitly reviewed
  and published as such by the appropriate community process.

## Design

> Note: this section intentionally crosses the boundary between VEP-level
> design and implementation-level specification. Because the reference
> implementation already exists and is in active use, the scenario, schema,
> and CLI details below are included so reviewers can evaluate the actual
> behavior being standardized rather than only an intended design. Sections
> below can be skimmed by reviewers who only need the specification-level
> summary.

The framework has five major parts:

1. benchmark specification;
2. scenario catalog;
3. result schema and provenance model;
4. CLI surface;
5. cluster validation.

### Benchmark specification vs reference implementation

This VEP defines the benchmark specification. Virtbench is the initial
reference implementation.

The specification defines:

- scenario names;
- scenario versions;
- required and optional parameters;
- measurement semantics;
- required result fields;
- result artifact layout;
- compatibility expectations;
- documentation requirements.

The reference implementation provides a working CLI and scenario
implementations that produce schema-conformant artifacts.

Other implementations are allowed as long as they implement the same
scenario definitions and result schema.

### Scenario catalog

Each scenario is identified by a stable name, takes a documented set of
parameters, and emits results conforming to the schema.

The scenario catalog is split into an initial Alpha scope and future /
optional scenarios.

### Alpha scenario catalog

| Scenario | Name | Purpose |
|---|---|---|
| VM provisioning via DataSource clone | `datasource-clone` | Measure VM creation, VMI Running, readiness, and optional guest ping timings for N VMs created from a DataSource. |
| Boot storm | `boot-storm` | Measure simultaneous startup behavior for an existing VM pool. |
| Live migration | `migration` | Measure per-VM and aggregate live migration duration across N VMs. |
| Storage I/O | `fio` or `elbencho` | Measure in-guest I/O characteristics using a documented workload profile. |
| Failure recovery | `failure-recovery` | Measure VM workload recovery behavior under documented node or component failure conditions. |

### Future or optional scenarios

| Scenario | Candidate name | Purpose |
|---|---|---|
| Day-2 VM operations | `vm-ops` | Drain, hotplug-disk, power toggle, rebalance, blkdiscard, snapshot, or similar operational actions. |
| Chaos | `chaos-benchmark` | Concurrent VM create, volume resize, volume clone, restart, snapshot, and other mixed operations. |
| Snapshot / restore | `snapshot-restore` | Measure snapshot and restore behavior for VM workloads. |
| Volume expansion | `volume-expansion` | Measure VM behavior during or after PVC expansion where supported. |

Future scenarios may be promoted into the required catalog after they have
documented semantics, scenario-aware validation, schema-conformant output,
and smoke or scheduled CI coverage.

### Scenario versioning

Scenarios are versioned independently of the framework. A
`scenario_name` + `scenario_version` pair uniquely identifies a workload
definition. This pair is recorded in every result file.

A scenario version changes when:

- required parameters change;
- default concurrency changes;
- VM lifecycle measurement points change;
- guest workload profile changes;
- failure injection behavior changes;
- emitted metrics change in a way that affects interpretation.

### Metric definitions

Benchmark results must use explicit metric names and units. Scenario
documentation must state whether each metric is observed from the Kubernetes
API, from KubeVirt status, from the guest, or from an external client.

| Metric | Unit | Definition | Observation point |
|---|---:|---|---|
| `time_to_vm_created_seconds` | seconds | Time from create request submission to VM object observed by the client. | Kubernetes API |
| `time_to_vmi_running_seconds` | seconds | Time from VM create/start request to VMI phase `Running`. | KubeVirt API |
| `time_to_vmi_ready_seconds` | seconds | Time from VM create/start request to VMI Ready condition, where available. | KubeVirt API |
| `time_to_guest_ip_seconds` | seconds | Time from VM create/start request to guest IP discovery. | KubeVirt status, guest agent, or network observation |
| `time_to_ping_seconds` | seconds | Time from VM create/start request to successful ICMP response from guest IP. | External client |
| `migration_duration_seconds` | seconds | Time from migration object creation to completed / succeeded migration condition. | KubeVirt API |
| `guest_io_iops` | IOPS | IOPS reported by the in-guest workload tool for the documented profile. | Guest workload tool |
| `guest_io_throughput_bytes_per_second` | bytes/second | Throughput reported by the in-guest workload tool for the documented profile. | Guest workload tool |
| `failure_recovery_time_seconds` | seconds | Time from documented failure injection timestamp to workload readiness on a healthy node. | Scenario-specific |
| `operation_success_count` | count | Number of operations that completed successfully. | Scenario-specific |
| `operation_failure_count` | count | Number of operations that failed. | Scenario-specific |

If a metric depends on guest networking, guest agent availability, SSH,
ICMP, or an in-guest tool, that dependency must be explicit in the scenario
documentation.

### Result schema

Every scenario writes results to disk under a deterministic directory
layout. A reference layout is:

```text
results/
└── {storage_version}/
    └── {num_disks}-disk/
        └── {timestamp}_{scenario}_{num_vms}vms/
            ├── {scenario}_results.json
            ├── {scenario}_results.csv
            └── summary_{scenario}.json
```

The per-run JSON file contains:

- `schema_version`;
- framework / tool metadata;
- scenario metadata;
- cluster metadata;
- workload parameters;
- aggregate metrics;
- per-VM or per-operation records;
- error and cleanup status.

The CSV file is a flat projection of the per-record list for ingestion into
spreadsheets, dashboards, and time-series systems.

The `summary_*.json` file contains aggregate metrics and metadata intended
for dashboard and trend tracking.

### Required result provenance

Every result file must include enough context to make the benchmark
interpretable. Required provenance fields include:

- benchmark tool name;
- benchmark tool version;
- benchmark tool git commit, where available;
- scenario name;
- scenario version;
- schema version;
- run timestamp;
- Kubernetes version;
- KubeVirt version;
- CDI version, where relevant;
- `virtctl` version, where relevant;
- cluster distribution, where known;
- node count;
- node CPU / memory shape, where available;
- VM template identifier;
- VM image identifier;
- VM CPU / memory configuration;
- storage class;
- access mode;
- volume mode;
- disk count and disk size;
- CNI / network attachment details, where non-default;
- scenario parameters;
- cleanup behavior and cleanup status.

### Illustrative result file

```json
{
  "schema_version": "1.0.0",
  "tool": {
    "name": "virtbench",
    "version": "0.1.0",
    "git_commit": "unknown"
  },
  "scenario": {
    "name": "datasource-clone",
    "version": "1.0.0"
  },
  "timestamp": "2026-05-14T10:30:00Z",
  "cluster": {
    "kubernetes_version": "v1.31.0",
    "kubevirt_version": "v1.x.y",
    "cdi_version": "v1.x.y",
    "node_count": 6
  },
  "workload": {
    "vm_template": "examples/vm-templates/rhel9-vm-datasource.yaml",
    "vm_image": "rhel9-datasource",
    "storage_class": "my-sc",
    "access_mode": "ReadWriteOnce",
    "volume_mode": "Block",
    "disk_count": 1,
    "disk_size": "30Gi"
  },
  "parameters": {
    "start": 1,
    "end": 100,
    "namespace_prefix": "perf",
    "concurrency": 20
  },
  "aggregate": {
    "total_vms": 100,
    "successful_vms": 98,
    "failed_vms": 2,
    "avg_time_to_vmi_running_seconds": 9.23,
    "avg_time_to_ping_seconds": 12.45,
    "max_time_to_vmi_running_seconds": 15.67,
    "max_time_to_ping_seconds": 18.92
  },
  "records": [
    {
      "namespace": "perf-1",
      "vm_name": "rhel9",
      "status": "Success",
      "time_to_vmi_running_seconds": 8.45,
      "time_to_ping_seconds": 11.23
    }
  ],
  "cleanup": {
    "requested": true,
    "status": "Success"
  }
}
```

### Schema compatibility

Schema changes follow semantic versioning.

- Patch versions may clarify field descriptions or fix non-breaking schema
  issues.
- Minor versions may add fields only.
- Consumers must ignore unknown fields.
- Major versions may rename or remove fields and require a VEP update or an
  explicitly approved compatibility plan.
- Major schema changes require a deprecation window of at least one
  framework minor release during which both old and new schemas are emitted
  or a converter is provided.

### CLI surface

The reference implementation exposes a single `virtbench` command with one
subcommand per scenario, plus `validate-cluster` and `version`.

Common global flags include:

- `--kubeconfig`;
- `--log-level`;
- `--log-file`;
- `--timeout`;
- `--save-results`;
- `--storage-version`;
- `--result-dir`;
- `--skip-validation`.

Scenario subcommands define scenario-specific flags.

The framework does not require an in-cluster controller or operator. It runs
as a client-side tool against an existing KubeVirt installation, using
`kubectl`, `virtctl`, and Kubernetes clients where needed.

### API Examples

End-user invocation:

```bash
# Validate cluster for a storage-backed benchmark
virtbench validate-cluster --storage-class my-sc

# VM provisioning: create 100 VMs from a DataSource and save results
virtbench datasource-clone \
  --start 1 --end 100 \
  --vm-name rhel9 \
  --namespace-prefix perf \
  --vm-template examples/vm-templates/rhel9-vm-datasource.yaml \
  --storage-class my-sc \
  --save-results \
  --storage-version my-csi-1.0.0

# Boot storm against an existing VM pool
virtbench boot-storm \
  --namespace-prefix perf \
  --start 1 --end 100 \
  --save-results

# Live migration of 25 VMs from a specific node
virtbench migration \
  --start 1 --end 25 \
  --source-node worker-1 \
  --save-results

# Storage I/O via elbencho
virtbench elbencho \
  --namespace-prefix perf \
  --start 1 --end 10 \
  --vm-name rhel-elbencho \
  --action run-all \
  --vm-template examples/vm-templates/elbencho-vm.yaml \
  --iops 1000 \
  --block-size 4K \
  --duration 300 \
  --save-results
```

### Cluster validation

A `validate-cluster` subcommand checks prerequisites before any scenario is
run.

Validation is scenario-aware. A cluster may pass validation for
`datasource-clone` but fail validation for `migration`, storage I/O, or
snapshot-related scenarios.

Validation checks may include:

- KubeVirt is installed and healthy;
- CDI is installed and healthy, where required;
- target storage class exists;
- storage class supports the access modes required by the selected scenario;
- volume mode is supported where relevant;
- sufficient schedulable node capacity exists;
- `virtctl` is available on the operator workstation, where required;
- guest image or DataSource exists;
- VM template is valid;
- live migration is available for migration scenarios;
- snapshot or expansion capabilities exist where required;
- guest networking prerequisites are met when guest IP or ping metrics are
  requested.

Scenarios refuse to run if validation has not passed within the current
invocation unless `--skip-validation` is explicitly set.

### Compatibility and prerequisites

Not every scenario is expected to run on every KubeVirt deployment. Each
scenario must document its prerequisites.

| Scenario | Requires CDI | Requires guest network | Requires live migration | Requires volume expansion | Requires snapshot support |
|---|---|---|---|---|---|
| `datasource-clone` | Yes | Optional for guest IP / ping metrics | No | No | No |
| `boot-storm` | Usually, if pool is DataSource-created | Optional for guest IP / ping metrics | No | No | No |
| `migration` | No, unless VM pool creation uses CDI | Optional | Yes | No | No |
| `fio` / `elbencho` | Depends on image setup | Yes, SSH or guest setup may be required | No | No | No |
| `failure-recovery` | Scenario-dependent | Optional | Optional, depending on recovery mode | No | No |
| `vm-ops` | Scenario-dependent | Scenario-dependent | Scenario-dependent | Scenario-dependent | Scenario-dependent |
| `chaos-benchmark` | Scenario-dependent | Scenario-dependent | Scenario-dependent | Scenario-dependent | Scenario-dependent |

### Operational safety

The framework is intended for controlled test environments unless explicitly
configured for production-safe smoke runs.

Scenarios may create large numbers of VMs, PVCs, snapshots, migrations, and
in-guest I/O workloads. Some scenarios may intentionally disrupt nodes or
components. Documentation must describe expected resource impact, cleanup
behavior, and failure modes for each scenario.

Each scenario must document:

- resources created;
- whether cleanup is automatic;
- whether cleanup is best-effort;
- expected impact on API server, storage backend, network, and nodes;
- whether the scenario is safe for production;
- minimum recommended cluster size;
- known disruptive behavior.

## Alternatives

### Use kube-burner directly

kube-burner is already used for Kubernetes and OpenShift scale testing and
has support for KubeVirt CRDs, including VM creation and readiness-related
measurements. It is a strong option for object-density, control-plane scale,
and Kubernetes-level workload generation.

Virtbench is not intended to replace kube-burner. The proposed value of this
VEP is a VM-centric benchmark layer that standardizes end-to-end scenarios
such as DataSource clone to guest readiness, boot storms, live migration
timing, in-guest I/O, failure recovery, and per-VM result artifacts.

Future Virtbench scenarios may use kube-burner as an execution backend while
still emitting the result schema defined by this VEP.

### Continue with per-vendor harnesses

Today each vendor or downstream user may maintain its own benchmark scripts.
This allows local flexibility but produces incomparable numbers, duplicated
effort, and unclear measurement semantics.

This VEP does not prevent vendor-specific automation. It defines a common
scenario and result format that such automation can implement.

### Ship benchmarks inside kubevirt/kubevirt

This was considered and rejected.

Benchmarks have a different cadence, dependency surface, and operational
profile than core KubeVirt. They may depend on guest images, storage
drivers, in-guest tools, dashboards, failure injection, and environment
configuration that are not appropriate for in-tree KubeVirt runtime code.

Keeping the benchmark framework out-of-tree avoids adding feature gates,
controllers, or runtime API surface to KubeVirt.

### Donate the reference implementation to the kubevirt GitHub organization

This is a candidate future path and may be appropriate before Beta.

Hosting the reference implementation under `kubevirt/<name>` would align
ownership with the SIG that owns the benchmark specification and remove the
vendor-specific repository URL from user-facing documentation.

The repository move itself is governed by the KubeVirt community's
project-acceptance and governance process and is tracked separately from
this VEP. This VEP should not require immediate repository transfer for
Alpha.

## Scalability

Scenarios are parameterized by VM count and concurrency. The reference
implementation runs from a single operator workstation and dispatches API
calls to the cluster. Coordination across multiple driver machines is not
required for the Alpha scenario catalog.

Practical upper bounds depend on the cluster under test, including:

- API server QPS;
- etcd throughput;
- scheduler behavior;
- KubeVirt controller behavior;
- CDI behavior;
- storage backend performance;
- network fabric;
- VM image size;
- node count and node shape.

Each scenario must document its concurrency model and the range of VM counts
it has been exercised against in the reference implementation.

For focused storage or network characterization, scenarios may support
single-node or node-selector modes. These modes are useful for isolating
specific bottlenecks but must be clearly marked because they may not
represent normal scheduling behavior.

## Update / Rollback Compatibility

The framework is an out-of-cluster client and does not modify KubeVirt
itself, so there is no cluster-side update or rollback path.

Two compatibility surfaces exist:

### Result schema

Result schema is versioned using `schema_version`.

- Minor versions are additive.
- Consumers must ignore unknown fields.
- Major versions require a VEP update or an approved compatibility plan.
- Major version changes require a deprecation window of at least one
  framework minor release during which both schemas are emitted or a
  converter is provided.

### Scenario definitions

Scenario definitions are versioned independently.

Renaming or removing a scenario requires a deprecation alias for at least
one framework minor release unless the owning SIG approves an exception.

Changing default concurrency, measurement semantics, guest workload profile,
or failure injection behavior requires a scenario version bump.

## Functional Testing Approach

The framework is tested at multiple levels.

### Unit tests

Unit tests cover:

- CLI wrapper behavior;
- parameter parsing;
- result-schema serialization;
- validation logic;
- summary generation;
- error handling.

### Schema conformance tests

Every emitted JSON result file is validated against the JSON schema
definition checked into the repository.

Any scenario that emits invalid schema output fails CI.

### Smoke tests

Smoke tests exercise the Alpha scenario set against a small VM count, such
as 1-2 VMs, on a KubeVirt-capable CI environment.

Smoke tests assert that:

- the scenario starts successfully;
- the scenario completes or fails with a documented error;
- result files are emitted;
- result files conform to schema;
- cleanup behavior works or reports failure clearly.

Smoke tests for the Alpha scenario set should run on every PR to the
reference implementation where infrastructure is available.

### Scheduled or manual tests

Larger and disruptive scenarios may run on scheduled or manually triggered
CI jobs. These include:

- larger boot-storm runs;
- larger migration runs;
- in-guest I/O workloads;
- failure-recovery scenarios;
- chaos scenarios;
- multi-node scale experiments.

Larger-scale performance characterization, such as hundreds or thousands of
VMs, is performed out-of-band on dedicated clusters and is not required as a
gating CI signal for every PR.

## Implementation History

- TBD: VEP opened. PR: TBD.
- TBD: Alpha scenario catalog agreed by owning SIG.
- TBD: Result schema v1.0.0 published.
- TBD: Reference implementation emits schema-conformant results for Alpha
  scenarios.

## Graduation Requirements

> Note: the stages below reflect **tooling maturity and adoption**, not
> feature stability in the traditional sense. Each stage represents a
> gradual assessment and integration checkpoint. The Alpha / Beta / GA
> labels follow VEP template convention; an equivalent framing is
> Trial / Evaluation / Adoption.

### Alpha

- [ ] Owning SIG and participating SIGs are agreed.
- [ ] Benchmark specification is documented in this VEP.
- [ ] Alpha scenario catalog is documented:
      `datasource-clone`, `boot-storm`, `migration`, one storage I/O
      scenario, and `failure-recovery`.
- [ ] Metric definitions and units are documented for Alpha scenarios.
- [ ] Result schema v1.0.0 is published.
- [ ] Reference implementation emits schema-conformant output for Alpha
      scenarios.
- [ ] `virtbench` CLI is installable from the reference implementation
      repository.
- [ ] `validate-cluster` exists and performs scenario-aware validation for
      Alpha scenarios.
- [ ] Documentation covers installation, prerequisites, operational safety,
      cleanup behavior, and at least one worked example per Alpha scenario.
- [ ] Smoke or scheduled tests exist for Alpha scenarios.

### Beta

- [ ] Result schema has had no breaking changes for at least one framework
      minor release.
- [ ] Reference implementation has been used to produce comparable results
      across at least two distinct environments, storage backends, or
      downstream distributions.
- [ ] At least one downstream consumer, CI pipeline, release engineering
      workflow, or vendor pipeline is running the framework on a recurring
      basis or documented validation cycle.
- [ ] Governance and maintainership model has been accepted by the owning
      SIG. This may include moving the repository under the `kubevirt`
      GitHub organization or documenting an accepted external governance
      model.
- [ ] Scenario documentation includes known limitations and compatibility
      notes.
- [ ] Result artifacts include required provenance fields.

### GA

- [ ] Result schema v1.x is declared stable.
- [ ] Any future major schema version bump requires a new VEP or approved
      compatibility plan.
- [ ] Framework has been used in at least one real release, vendor, or
      downstream validation cycle, and the results are documented using the
      stable schema.
- [ ] Multiple independent consumers, such as two or more vendors,
      downstream distributions, or upstream release engineering workflows,
      are using the framework on a recurring basis or documented validation
      cycle.
- [ ] Scenario catalog and documentation are reviewed and signed off by
      `sig-scale` and any affected participating SIGs.
- [ ] Operational safety, cleanup behavior, and disruptive scenarios are
      clearly documented.
