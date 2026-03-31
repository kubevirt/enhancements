# VEP #206: Pre-flight checks for decentralized live migration across clusters

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Before deciding to live migrate a Virtual Machine to another cluster, it is helpful to have confidence that the migration will succeed. In order to achieve this confidence it should be possible to run some pre-flight checks to assess the available capacity on the target cluster as well as checking if there is a working network connection between the source and target cluster. This is especially important if one wants to do a bulk migration between two clusters.

## Motivation

Right now it is possible to kick of the live migration, but it is hard to determine a high degree of probability if the migration can succeed. This enhancement will define a list of pre-flight checks that can be executed before actually attempting the live migration. It will also define which component and resources should execute those checks and how they should be reported back to the user.

## Goals

Define a set of pre-flight checks that can be executed from the source cluster. The source cluster must have the connectionURL of the target cluster. This can give a user on the source cluster confidence the live migration will succeed if attempted.

## Non Goals

Ensure the live migration will succeed if the pre-flight checks indicate it should succeed. It is entirely possible that in the time between the pre-flight check and the actual migration something in the target cluster has changed that causes the live migration to fail.

## Definition of Users

- KubeVirt cluster admin
- KubeVirt virtual machine admin

## User Stories

- As a user of decentralized live migration, I want to have a high degree of confidence that when I attempt to live migration a Virtual Machine to another cluster, that the operation will succeed.
- As a user of decentralized live migration, I want to be able to run non disruptive checks to give me the confidence a decentralized live migration across clusters will succeed.

## Repos

[KubeVirt](https://github.com/kubevirt/kubevirt)

## Design

When considering whether a cross-cluster live migration (using decentralized live migration) will succeed, the following should be checked:

**Note:** Whether decentralized live migration is enabled on both clusters is not a practical pre-flight check—if it is disabled on the target, the pre-flight machinery may be unavailable there as well.

1. **Network**
   - Synchronization controllers can communicate between source and target.
   - Source virt-handler can reach target virt-handler for the migration stream.
   - See [checking networking](#checking-networking) for reachability, ports, latency/bandwidth, TLS, DNS, and MTU.

2. **CPU and architecture**
   - Target has compatible CPU and architecture (e.g. no amd64→arm64, no AMD↔Intel; host-passthrough requires a matching CPU on the target node).
   - See [checking architecture and cpu types](#checking-architecture-and-cpu-types).

3. **Special resources**
   - If the VM needs special resources (e.g. GPU), the target cluster has them available.

4. **Scheduling**
   - VM can be scheduled on the target (node selector, affinity, tolerations, huge pages, quotas).
   - See [checking scheduling constraints](#checking-scheduling-constraints).

5. **Capacity**
   - At least one target node has enough free CPU for the migration (can change with active migrations).
   - At least one target node has enough memory for the VM and migration overhead.
   - Target has enough storage and compatible storage classes/volume capabilities for receiver volumes.
   - See [checking capacity](#checking-capacity).

6. **Versions and configuration**
   - KubeVirt and migration component versions are compatible; feature gates and migration timeout are compatible between source and target.

7. **Target readiness**
   - Target namespace exists; RBAC and migration limits allow the migration.
   - See [checking target cluster readiness](#checking-target-cluster-readiness).

### checking networking

- **Reachability**: Can the synchronization controllers on source and target reach each other (e.g. via connectionURL)? Can the source virt-handler reach the target virt-handler for the migration data stream?
  - **Ports and firewalls**: Are the ports required for migration (e.g. libvirt migration, API, sync) open and not blocked by network policies, security groups, or firewalls between clusters?
  - **TLS and certificates**: If connectionURL uses TLS, are certificates valid and not expired? Can the source cluster trust the target’s serving certificate (or CA)?
  - **DNS**: Can the source cluster resolve the target cluster’s connection URL hostname consistently?
  - **MTU**: If path MTU differs or is lower than expected, large migration packets could be dropped or fragmented; checking or documenting MTU can help troubleshoot failures.
- **Latency and bandwidth**: Cross-cluster migration transfers memory and device state. High RTT or limited bandwidth can cause migration to time out or progress too slowly. A pre-flight check can measure RTT and optionally bandwidth (e.g. with a short probe) and warn if above thresholds.

### checking architecture and cpu types

KubeVirt’s node labeler labels each node that can run VMs with compatible CPU models and architecture. Pre-flight can use these labels to decide whether the target cluster can support the migration.

**Procedure:** For each target node, collect the CPU model (and architecture) from the labels into a cluster-wide map. Compare that map to the source VM’s CPU model. If at least one node exposes a compatible CPU model, the VM can be scheduled on the target for live migration; otherwise, migration is not possible.

### checking capacity

#### cpu capacity

There are some tools available where one can check the available capacity in a cluster. For instance [cluster-capacity](https://github.com/kubernetes-sigs/cluster-capacity). This can give a general scheduling analysis and give an indication if the target live migration pod can be scheduled. It currently does not support checking for available storage.

#### storage capacity

Since there is currently no resources one can query in kubernetes, we can see if the storage provider reports information in prometheus. There are a few well know metrics names that can provide information such as `storage_pool_capacity_bytes` and `csi_external_provisioner_capacity_available`.

#### memory capacity

The target node must have enough allocatable memory for the VM. During live migration the target typically needs at least the VM’s memory size available (and in some phases both sides hold a copy). Pre-flight checks should verify that at least one target node has sufficient free/allocatable memory for the VM’s requested memory (and optionally for migration overhead). This can use node allocatable memory and existing pod requests, similar to CPU capacity checks.

#### checking VM state and migratability

- **VM state**: Is the VM running and in a state that allows migration (e.g. not paused, not in error)? Are there conditions or annotations that mark the VM as non-migratable?
- **Features and devices**: Do the VM’s features (e.g. host-passthrough CPU, SEV, TPM, specific devices or IOMMU) have compatible support on the target? Some features may not be available or may differ across clusters and can cause migration to fail.

### checking scheduling constraints
All of the following we can check by running the cluster-capacity check:
- **Node selector and affinity**: Can the VM’s nodeSelector, nodeAffinity, or podAffinity be satisfied by at least one node in the target cluster?
- **Taints and tolerations**: Do any target nodes that are otherwise suitable have taints that the VM’s tolerations allow? Conversely, are there required taints the VM tolerates that exist on target?
- **Huge pages**: If the VM requests huge pages, do target nodes have enough allocatable huge page capacity of the right size (e.g. 2Mi, 1Gi)?
- **Resource limits and quotas**: Would placing the migration receiver (and the VM) in the target namespace exceed resource quotas or limit ranges in that namespace?

### checking version and configuration compatibility

- **KubeVirt and API versions**: Are the source and target KubeVirt (and migration controller) versions compatible for the decentralized migration protocol? Incompatible versions can cause handshake or protocol failures.
- **Feature gates and config**: Are the same migration-related feature gates and config (e.g. migration timeout, encryption) enabled or compatible on both clusters? A longer migration timeout may be required for cross-cluster due to higher latency.

### checking target cluster readiness

- **Target namespace**: Does the target namespace exist and is it usable (e.g. not terminating, not restricted by admission)?
- **RBAC and service accounts**: Do the migration components on the target have the necessary RBAC to create receiver resources, bind volumes, and run the migrated VM?
- **Decentralized migration enabled on target**: Although not a pre-flight in the strict sense (as noted in the design), a readiness check can still report whether the target cluster has decentralized migration enabled and the expected components running, so the user can correct configuration before attempting migration.


## API Examples

There are two things to consider when designing the API:
1. How can the source cluster connect to the target cluster to interrogate the capabilities of the target cluster.
2. How to report any results back to the user running the pre-flight checks.

### Connecting to the target cluster
We only have one piece of information about the target cluster, that is the `connectionURL`. So it makes sense to use that to retrieve the capabilities of the target cluster. We know that the connectionURL is actually a gRPC endpoint that is normally used for synchronization purposes. We could extend the gRPC functions available on the end point to include the interrogation of the capabilities of the cluster.

We need to provide the following to the function for it properly identify the information we require:
- The source VM spec. 
- The `connectionURL` of the source, so we can check if the target can communicate back to the source properly.

Then the gRPC call can run all the required checks for that VM and return the information requested. If we want this information for a large number of Virtual Machines, we need some kind of aggregation after the result comes back from all the requests. In particular this is needed if we want to determine if the target cluster has enough capacity for all the Virtual Machines that will be migrated.

### Reporting the results back to the end user.

There are two variants of the information we want. Individual Virtual Machines and a large number of Virtual Machines. One can return the result of the gRPC more or less directly into status of a `VirtualMachineInstanceMigration`. Where as the aggregated result is not tied to one specific VM, but a list of `VirtualMachineInstanceMigration`s. I am trying to avoid adding new resources just for the pre-flight checks. Maybe we can generate some prometheus statistics that the user can read and use.

#### Extra fields in the `VirtualMachineInstanceMigration` status (single gRPC result)

To report the result of a **single** pre-flight gRPC call (one VM, one target connectionURL), add the following to `VirtualMachineInstanceMigrationStatus` and supporting types.

**New field on `VirtualMachineInstanceMigrationStatus`:**

```go
// PreFlightCheckResult holds the result of a single pre-flight check gRPC call
// to the target cluster for decentralized migration.
// Populated when a pre-flight check has been run for this migration's target (e.g. SendTo.ConnectURL).
// +optional
PreFlightCheckResult *PreFlightCheckResult `json:"preFlightCheckResult,omitempty"`
```

**New types (in `kubevirt.io/api/core/v1`):**

```go
// PreFlightCheckResult is the result of a single gRPC pre-flight check call
// to the target cluster for decentralized live migration.
// +k8s:openapi-gen=true
type PreFlightCheckResult struct {
	// CheckedAt is when the pre-flight check was performed.
	CheckedAt metav1.Time `json:"checkedAt"`
	// OverallResult is the aggregate result: Pass, Fail, or Warning.
	OverallResult PreFlightCheckResultValue `json:"overallResult"`
	// TargetConnectionURL is the connection URL that was checked (from SendTo.ConnectURL).
	TargetConnectionURL string `json:"targetConnectionURL,omitempty"`
	// Checks is the list of individual check results (e.g. Network, CPU, Scheduling).
	// +listType=atomic
	// +optional
	Checks []PreFlightCheckItem `json:"checks,omitempty"`
	// Message is a short human-readable summary (e.g. "All checks passed" or first failure reason).
	// +optional
	Message string `json:"message,omitempty"`
	// Error is set when the gRPC call itself failed (e.g. connection error, timeout).
	// When set, OverallResult should be Fail and Checks may be empty or partial.
	// +optional
	Error string `json:"error,omitempty"`
}

// PreFlightCheckResultValue is the result of a single check or the overall result.
// +k8s:openapi-gen=true
type PreFlightCheckResultValue string

const (
	PreFlightCheckPass   PreFlightCheckResultValue = "Pass"
	PreFlightCheckFail   PreFlightCheckResultValue = "Fail"
	PreFlightCheckWarning PreFlightCheckResultValue = "Warning"
)

// PreFlightCheckItem is the result of one category of pre-flight check.
// +k8s:openapi-gen=true
type PreFlightCheckItem struct {
	// Category identifies the check (e.g. Network, CPUAndArchitecture, Scheduling, Capacity, VersionAndConfig, TargetReadiness).
	Category string `json:"category"`
	// Result is Pass, Fail, or Warning.
	Result PreFlightCheckResultValue `json:"result"`
	// Message is a short description of the result.
	// +optional
	Message string `json:"message,omitempty"`
	// Details can hold structured or free-form details (e.g. rttMs, bandwidthMbps, nodeNames).
	// +optional
	Details map[string]string `json:"details,omitempty"`
}
```

**Semantics:**

- One VMIM corresponds to one migration (one VMI, one target). `PreFlightCheckResult` holds the result of **one** gRPC call to that target. When the check is re-run, the field is replaced with the latest result.
- `OverallResult`: **Pass** = migration is expected to succeed from a pre-flight perspective; **Fail** = at least one check failed; **Warning** = no hard failures but e.g. high latency or capacity concerns.
- `Checks` align with the design categories: Network, CPU and architecture, Special resources, Scheduling, Capacity, Versions and configuration, Target readiness. Implementations can use the category names above or extend with implementation-specific names.
- `Error` is for gRPC/transport failures (e.g. cannot reach target, TLS error, timeout). When `Error` is set, `OverallResult` should be `Fail` and `Message` can summarize the error.
- `CheckedAt` allows clients to ignore stale results or trigger a refresh.

**Example status snippet (YAML):**

```yaml
status:
  phase: Pending
  preFlightCheckResult:
    checkedAt: "2025-02-16T10:00:00Z"
    overallResult: Pass
    targetConnectionURL: "https://target-cluster.example.com:443"
    message: "All checks passed"
    checks:
      - category: Network
        result: Pass
        message: "Reachability and ports OK"
        details:
          rttMs: "12"
      - category: CPUAndArchitecture
        result: Pass
        message: "Compatible CPU model on target"
      - category: Scheduling
        result: Pass
        message: "At least one node satisfies constraints"
```

**Example with failure:**

```yaml
status:
  preFlightCheckResult:
    checkedAt: "2025-02-16T10:00:00Z"
    overallResult: Fail
    targetConnectionURL: "https://target-cluster.example.com:443"
    message: "No node with compatible CPU model"
    checks:
      - category: Network
        result: Pass
        message: "Reachability OK"
      - category: CPUAndArchitecture
        result: Fail
        message: "No target node exposes compatible CPU model"
```

This API allows reporting the result of a single gRPC call directly in the VMIM status without introducing a new resource, and supports both quick pass/fail and per-category details for debugging and UI display.


## Alternatives

Simply do not do pre-flight checks and rely on status updates of the `VirtualMachineInstance` and `VirtualMachineInstanceMigration` to tell the user what is going on. However this does not help with the use case where if someone wants to migrate a lot of VirtualMachines to another cluster. It won't give an indication if it will even work. 

Use a prometheus/grafana dashboard to get information about the capacity of other clusters. But this doesn't solve the network checks and scheduling constraints. Those dashboards won't give that information.

## Scalability

N/A

## Update/Rollback Compatibility

N/A

## Functional Testing Approach

Attempt to have most testing done with unit tests, where the results of the checks are artificial, and we check that the handling of the results is proper.

## Implementation History

## Graduation Requirements

The requirements for graduating to each stage.
Example:
### Alpha
- [ ] Initial implementation supporting network checks, CPU and architecture checks, any special resources checks, and scheduling checks.

### Beta
- [ ] Implementation supports the rest of the checks like capacity, versioning, and target readiness checks.


### Alpha

### Beta

### GA
