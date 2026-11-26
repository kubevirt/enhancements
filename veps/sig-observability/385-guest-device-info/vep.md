# VEP #385: Guest Device Info Metrics

> **Reviewing for kubevirt/kubevirt?** Read
> [kubevirt/kubevirt Changes](#kubevirtkubevirt-changes) and nothing else.

## VEP Status Metadata

### Target releases

This VEP extends [VEP #143](../143-monitoring-stack-separation/vep.md) and defines no feature gate
of its own, so it tracks no target versions here. It ships at the maturity of the pipeline it
extends, see [VEP #143's target releases](../143-monitoring-stack-separation/vep.md#target-releases).

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [x] (R) Alpha target version is explicitly mentioned and approved: tracked by VEP #143
- [x] (R) Beta target version is explicitly mentioned and approved: tracked by VEP #143
- [x] (R) GA target version is explicitly mentioned and approved: tracked by VEP #143

## Overview

VirtIO driver versions drift apart across a fleet of Windows VMs, invisibly to the cluster. This
VEP surfaces the driver information the QEMU guest agent already reports:

- A metric carrying driver name and version per device, for fleet-wide inventory
- An alert for VMs whose drivers are behind

What counts as "behind" is a support-policy decision KubeVirt cannot make, so the comparison is
driven by an optional mapping supplied by the distributor. Upstream supplies none, so the alert
cannot fire on a default installation.

## Motivation

Reading a Windows VM's driver versions means opening a session to that VM, once per VM. At a handful
this is tedious; at hundreds it is not done at all. So:

- Driver-related performance and stability problems are diagnosed one VM at a time, after they
  surface, rather than predicted from a version known to be behind
- A driver version carrying a published vulnerability sits unnoticed
- A driver update campaign has no progress signal

The data is already reachable: QEMU added `guest-get-devices` in 5.2 for this purpose, and VEP
#143's pipeline already collects guest agent data of this kind.

## Goals

- Driver name and version are queryable from Prometheus for every device a Windows VM reports
- Devices running a driver older than a minimum version are identified per device and per Windows
  release, against minimums that KubeVirt does not supply
- Those minimums are supplied and revised as data at runtime, without rebuilding or reconfiguring
  KubeVirt
- The metric supports user-written version conditions beyond the built-in one
- No metric already reported for a VM changes

## Non Goals

- **Updating drivers inside guest VMs.** Rejected: KubeVirt does not manage software inside a guest,
  and doing so is a far larger problem than reporting.
- **Defining the "latest" or "correct" driver version.** Rejected: it varies per device, per Windows
  release and per distribution's support policy, and changes independently of KubeVirt releases, so
  KubeVirt has no basis on which to pick one.
- **Basing the built-in alert on driver age.** Rejected, see Alternatives.
- **Per-namespace or per-VM policy overrides.** Deferred: currency is a property of the driver, not
  of who runs the VM. Scoping can be added later without changing the metric or the mapping.
- **Adding device info to VMI status or the Kubernetes API.** Deferred, see Alternatives.
- **Device types the guest agent does not report.** Deferred: it reports only PCI devices today. If
  it gains more, the metric can carry them unchanged.
- **Linux guests.** Rejected: `guest-get-devices` is Windows-only, and Linux ships VirtIO drivers in
  the kernel, so the version-drift problem does not arise in the same form.

## Definition of Users

- **VM owners** running Windows VMs
- **Vendors distributing KubeVirt**, who ship the observability stack with their product
- **Cluster administrators** on an upstream install

## User Stories

- As a VM owner with a fleet of Windows VMs, I want to see the installed driver versions across all
  of them from Prometheus, so that I can plan updates and watch a rollout without checking each VM.
- As a VM owner, I want to be told which VMs run drivers my organization considers out of date, so
  that I can prioritize them rather than review the whole fleet.
- As a vendor distributing KubeVirt, I want to ship my supported driver versions with my product, so
  that customers get outdated-driver alerts without configuring anything.
- As a cluster administrator on an upstream install, I want to declare my own supported versions, so
  that I get the same alerting without a vendor.
- As a cluster administrator running only Linux VMs, I want no effect on the metrics I already
  collect.

## Repos

| Repo | Carries                                                                       |
|---|-------------------------------------------------------------------------------|
| [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) | One more guest agent category on the stats path VEP #143 defines              |
| [kubevirt/kubevirt-observability-controller](https://github.com/kubevirt/kubevirt-observability-controller) | Metrics, ConfigMap defining minimum driver versions, recording rule and alert |
| [kubevirt/monitoring](https://github.com/kubevirt/monitoring) | Runbook                                                                       |

The next section, [kubevirt/kubevirt Changes](#kubevirtkubevirt-changes), is
self-contained. A reviewer of kubevirt/kubevirt needs nothing else in this
document.

## kubevirt/kubevirt Changes

Two changes, both on the stats collection path VEP #143 defines: one more guest agent category, and
tolerance for a category the guest agent does not implement. The device payload crosses the repository
boundary as an opaque field and is parsed in `kubevirt-observability-controller`.

### QEMU Guest Agent Command

Windows-only, available since QEMU 5.2. Returns driver name, version, date and PCI device/vendor ID
for each VirtIO and QEMU PCI device. A typical Windows VM reports 6-10.

Example output:
```json
{
  "return": [
    {
      "driver-date": 1721001600000000000,
      "driver-name": "Red Hat VirtIO Ethernet Adapter",
      "driver-version": "100.95.104.26200",
      "id": {
        "device-id": 4161,
        "vendor-id": 6900,
        "type": "pci"
      }
    }
  ]
}
```

### Data Path

`guest-get-devices` becomes another guest agent category on the collection path VEP #143 defines,
requested and cached like the categories already on it. The contract change is one request field and
one response field, both named `guestGetDevices`; collection, caching, aggregation, transport and
scrape are unchanged.

Driver information is mostly static for the life of a VM, so the command takes
the longest time-to-live the pipeline defines for cached agent data: 30 minutes.

### Unsupported and Failing Agent Commands

On a Linux guest the agent replies `CommandNotFound`, which libvirt surfaces as a typed error. The
pipeline does not tolerate that today:

- A failed agent command marks a VMI's whole stats response unsuccessful and the caller discards
  every metric for that VMI, so requesting the category cluster-wide would delete all
  VMStatsCollector metrics for every Linux VM
- The per-command cache records no timestamp when a refresh fails, so a permanently failing command
  is re-executed on every scrape instead of once per time-to-live

This VEP distinguishes *the agent does not implement this command* from *the command failed*, using
the typed libvirt error. An unsupported command yields an empty payload and a successful response,
cached for the normal time-to-live - for every cached agent command, not only this one. A Linux VM
therefore produces no device series, and no other metric for it is affected.

### Rejected Alternatives

| Option | Rejected because |
|---|---|
| Emit the metric from virt-handler's domainstats scraper, behind its own gate, reaching every install | Builds a second collection pipeline in the code VEP #143 is migrating away from, to be dismantled a release or two later, and separates the metric, the rules and the mapping loader from each other |
| Add device info to `VirtualMachineInstanceStatus`, as `GuestOSInfo` is today | Adds API surface and VMI status update overhead for a use case Prometheus already serves. Can be revisited if API access is ever needed |
| Stop folding per-command failures into the response's top-level success flag, instead of tolerating `CommandNotFound` specifically | The more complete fix, also covering the unreachable-agent case. Per-command errors are already carried, one `Response` per category; what discards them is the aggregate flag and the caller's all-or-nothing handling of it. Changing that changes the observable behaviour of an API VEP #143 owns and is still stabilizing |

### Scale Impact

| Interaction | Type | Originating component | Frequency | Change |
|---|---|---|---|---|
| Stats fetch from each virt-launcher | gRPC, node-local socket | virt-handler | once per VMI per poll | unchanged count; response grows by one field |
| `guest-get-devices` on the guest agent | guest agent command | virt-launcher | once per VM per 30 minutes | **new**, bounded by the command's time-to-live, not by the poll rate |

- Response growth is ~6-10 records of ~150 bytes, so 1-2 KB per Windows VMI per poll. Linux VMIs add
  nothing

### Existing VMs Across an Upgrade

A VM running across the upgrade reports no device data until it is live-migrated or restarted onto a
new virt-launcher. No VMI spec or status field changes and no domain XML is interpreted, so there is
no further migration-time compatibility concern.

### Testing

Functional, in the Windows lane:

- A Windows VMI's stats response carries a `guestGetDevices` payload listing its VirtIO devices

Unit:

- An agent reporting the command unsupported yields an empty payload and a successful result,
  leaving every other category in the response intact
- A second request within the time-to-live does not re-issue the command to the guest

### Alpha Checklist

- [x] Unsupported agent commands no longer fail a VMI's whole stats response, and are cached per TTL
      rather than retried every scrape
- [x] `guest-get-devices` added as a VMStatsCollector category, end to end
- [x] Unit tests for the unsupported-command handling, per Testing above
- [ ] Windows lane test asserting a Windows VMI's stats response carries device data

Dependencies, owned by VEP #143:

- [ ] An unreachable guest agent no longer fails a VMI's entire stats response. Pre-existing, but
      `VMStatsCollector` defaulting to enabled exposes every cluster to it

## Design

Everything from here to the end of this document is `kubevirt-observability-controller`, apart from
the runbook in `kubevirt/monitoring`. The controller parses the `guestGetDevices` payload described
above and emits one sample per device.

### Metric Definition

**Name:** `kubevirt_vmi_guest_device_driver_build_number`

**Type:** Gauge, one sample per device per VM, emitted by `kubevirt-observability-controller`
alongside its other guest-agent-derived metrics. A VM reporting no device data produces no samples.

**Labels:**

| Label | Example | Source |
|-------|---------|--------|
| `node`, `namespace`, `name`, `kubernetes_vmi_label_*` | `worker-1`, `default`, `win-vm-1`, `kubernetes_vmi_label_env="prod"` | stamped on every VMStatsCollector series |
| `driver_name` | `Red Hat VirtIO Ethernet Adapter` | `driver-name` |
| `driver_version` | `100.95.104.26200` | `driver-version` |
| `device_id` | `1041` | `id.device-id` (decimal to hex, no 0x prefix) |
| `guest_os_version_id` | `2022` | the VMI's reported guest OS version |

**Value:** The build number of `driver_version`.

Omitted by design: `vendor_id`, always `1af4` or `1b36` because QEMU hard-codes the filter, and
`device_type`, always `pci`.

### Driver Build Number

A Windows driver version has four unsigned 16-bit decimal components. For virtio-win, the last
component is the driver build and increases with every build within one virtio-win stream; the
first three are not needed to order builds. `100.103.104.30200` is build `30200`, and `100.103.104.30201` is newer. A version without
exactly four 16-bit decimal components has no build number and produces no sample. Build numbers are
compared only within one device and Windows release.

### Driver Version Reference ConfigMap

An optional ConfigMap named `kubevirt-guest-device-drivers-config` in
`kubevirt-observability-controller`'s own namespace, normally shipped by the distributor. Upstream
does not create it; only an annotated example ships in the controller's docs.

- Loaded through a controller-runtime watch. The ClusterRole currently grants only `get` on
  ConfigMaps and needs `list` and `watch` added
- An explicit schema `version` is required and rejected if unrecognized, so a later change cannot be
  misparsed by an older controller
- Each `(deviceID, guestOSVersionID)` key has exactly one minimum `driverVersion`, and every
  `driverVersion` must have a build number
- `deviceID` is normalized by the same helper the device metric uses to render the agent's decimal
  `id.device-id` as hex; drift between the two would silently match nothing
- `kubevirt_guest_device_driver_config_invalid` is `1` while a ConfigMap present in the cluster is
  being rejected

### Reference Metric

**Name:** `kubevirt_guest_device_driver_minimum_build_number`

**Type:** Gauge, emitted by `kubevirt-observability-controller` from the ConfigMap alone. The value is
the build number of the minimum version.

**Labels:**

| Label | Example | Source |
|-------|---------|--------|
| `device_id` | `1041` | `devices[].deviceID`, normalized |
| `guest_os_version_id` | `2022` | `devices[].minimum[].guestOSVersionID` |
| `driver_version` | `100.103.104.30200` | `devices[].minimum[].driverVersion` |
| `device_name` | `VirtIO network device` | `devices[].deviceName` |

### Recording Rule and Alert

Both are registered in the controller, which VEP #143 makes the owner of `PrometheusRule` content.
`vmi:kubevirt_vmi_guest_device_driver_build_number:max` yields one series per outdated (VMI, device
ID, driver version), valued at the installed build number:

```promql
max by (namespace, name, device_id, driver_name, driver_version, guest_os_version_id) (
  kubevirt_vmi_guest_device_driver_build_number
)
< on (device_id, guest_os_version_id) group_left
max by (device_id, guest_os_version_id) (
  kubevirt_guest_device_driver_minimum_build_number
)
```

A device whose installed build is below the minimum for that device and the VM's Windows release is
kept; an equal or newer build is not. A device the mapping does not list, a VM reporting no
`guest_os_version_id`, or a version without a build number matches nothing and so never alerts.

- `max by` on the right makes the reference unique per match key, so multiple controller replicas
  emitting it cannot abort the rule
- `max by` strips `node` and every `kubernetes_vmi_label_*`, so the series cannot inherit
  user-defined cardinality

The alert is added to the controller's VM workload alerts:

| Field | Value |
|-------|-------|
| Alert | `OutdatedGuestDeviceDrivers` |
| Expr | `vmi:kubevirt_vmi_guest_device_driver_build_number:max{namespace!=''}`, with the `vm` label added like every VM alert |
| For | `1h` |
| `severity` | `warning` |
| `operator_health_impact` | `none` |

It fires per VMI and device. `summary` states the condition; `description` names the VM, driver and
version. `runbook_url` is injected by the controller, leaving only the runbook itself to write in
kubevirt/monitoring.

### Risks and Mitigations

| Risk | Mitigation |
|---|---|
| **Operational.** Raising a minimum above what the fleet runs alerts the whole fleet at once, since the alert fires per (VM, device) pair | The `1h` `for` duration and Alertmanager grouping. Steady state is zero series, so a sudden fleet-wide firing signals the mapping changed, not the fleet |
| **Correctness.** The comparison relies on virtio-win's numbering, where the last version component orders builds. A driver numbered differently would be compared wrongly | The mapping lists only drivers its author chose, normally the distributor's own virtio-win drivers. The example ConfigMap documents the numbering assumption. Builds from different streams, e.g. upstream and a downstream rebuild, are not comparable, so one mapping holds minimums from one stream |
| **Operational.** A botched mapping edit could make active alerts look resolved | An invalid mapping retains the last valid one; `kubevirt_guest_device_driver_config_invalid` exposes that state for alerting |
| **UX.** With no mapping the rule is permanently empty, which is indistinguishable from a misconfiguration | Documented explicitly, plus an annotated example mapping so the reason for the silence is discoverable |
| **Security.** Driver inventory is disclosed to anyone who can read the metrics, including which VMs run a version with a published vulnerability | Not a new class of exposure: `kubevirt_vmi_guest_os_info` already reports guest OS and kernel version through the same endpoint under the same controls |
| **Operational.** No targeted off switch. The controller's allowlists are *allow*-lists, so suppressing this metric means enumerating every other metric to keep, and the coarse alternative takes every other VMStatsCollector metric down with it | A deny-list or per-feature switch, listed under On-By-Default Readiness as a prerequisite for shipping on by default. The mechanism is the controller maintainers' choice |
| **Security.** Mapping contents become metric label values | Writing it requires permission in the controller's administrator-owned namespace. Label values are data, never PromQL or a template, and are escaped on exposition. ConfigMap size bounds the reference metric's cardinality |

## API Examples

### Metric Output

```
kubevirt_vmi_guest_device_driver_build_number{
  node="worker-1", namespace="default", name="win-vm-1",
  driver_name="Red Hat VirtIO Ethernet Adapter",
  driver_version="100.95.104.26200",
  device_id="1041", guest_os_version_id="2022"
} 26200
```

### Example PromQL Queries

Inventory, filtered by any label - here one device across the fleet:
```promql
kubevirt_vmi_guest_device_driver_build_number{device_id="1041"}
```

Find VMs running a network driver older than build `26200`, without a mapping:
```promql
kubevirt_vmi_guest_device_driver_build_number{device_id="1041"} < 26200
```

### Reference ConfigMap

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: kubevirt-guest-device-drivers-config
  # the controller's own namespace; this is the default kustomize deployment
  namespace: virt-observability-controller-system
data:
  drivers.yaml: |
    version: v1alpha2
    devices:
      - deviceID: "1041"
        deviceName: "VirtIO network device"   # informational only
        minimum:
          - guestOSVersionID: "2022"
            driverVersion: "100.103.104.30200"
```

With this mapping, `win-vm-1` above alerts: build `26200` is below the minimum `30200` for Windows
Server 2022.

## Alternatives

### Rejected Options

The collection-side alternatives are in [kubevirt/kubevirt Changes](#rejected-alternatives).

| Option | Rejected because |
|---|---|
| Derive the requested stats categories from the metrics allowlist, so a narrowed deployment never issues the command | The feature is on by default, so enablement must not depend on a control most deployments leave unset. As an optimization it saves one agent call per Linux VM per 30 minutes, and belongs in VEP #143's scope |
| Add driver info as labels on `kubevirt_vmi_info` | Multiple devices per VM would duplicate the entire `kubevirt_vmi_info` series per device, inflating the central VMI info metric |
| Also expose the driver date, as a metric value or label | virtio-win stamps every driver of a release with the same date, so it orders nothing the build number does not, and doubles the device series |
| Fire the alert on `time() - <driver date>` exceeding a threshold | A stable device's current driver can be over a year old, so any threshold produces false positives |
| Carry the current driver's release date in the mapping and flag anything older | Requires a date maintained per entry |
| List the current versions per device and flag any version not listed | Flags newer drivers, and drivers that differ only in components that do not order builds |
| Compare all four version components | 64 bits exceed what a float64 sample holds exactly, so it needs two samples per device and a two-stage comparison, while the last component alone already orders virtio-win builds |
| Rewrite the recording rule expression from the mapping instead of exposing a reference metric | Makes rule content dynamic and untestable by upstream CI, and reloads the rule group on every edit |

## Scalability

### API and Call Interactions

The virt-launcher and guest agent interactions are in [kubevirt/kubevirt Changes](#scale-impact).

| Interaction | Type | Originating component | Frequency | Change |
|---|---|---|---|---|
| Stats poll of each virt-handler | HTTPS request | `kubevirt-observability-controller` | once per node per 30s, up to 10 nodes concurrently | unchanged count; response grows by one field per VMI |
| Watch on the driver mapping ConfigMap | watch | `kubevirt-observability-controller` | one long-lived watch | **new**, see below |

### New Watch Behavior

The mapping is read through a **new watch on ConfigMaps**, which the controller does not have today.
It must be restricted to the single named ConfigMap in the controller's namespace, via a field
selector or equivalent; an unrestricted watch would cache every ConfigMap in the namespace and is
not acceptable. The ClusterRole gains `list` and `watch`, scoped no wider.

### Series Counts

- ~6-10 series per Windows VM, bounded by QEMU's vendor ID filter: ~6000-10000 for 1000 VMs
- Linux VMs contribute none
- The reference metric is one series per mapping entry - tens in practice
- The recording rule is a filter, so its output is bounded by the number of *outdated* devices, near
  zero in a healthy fleet, and `max by` strips the unbounded labels

## Update/Rollback Compatibility

### Enablement on Upgrade

The feature turns on without user action once `VMStatsCollector` is `Beta` and enabled by default.
On a cluster with no mapping: new device series on Windows VMs as they are migrated or
restarted, a recording rule that evaluates to nothing, and no alert able to fire. Linux-only
clusters see no new series.

### Rollback

KubeVirt does not support rolling a cluster back. Nothing here would obstruct one: the feature
writes no state, adds no VMI field, and creates no resource an older component would interpret. The
only consequence would be the series disappearing.

## Functional Testing Approach

### The Happy Path

A Windows VM with a running guest agent reports its VirtIO devices, one series per device appears,
and a mapping with a minimum above one device's installed build fires `OutdatedGuestDeviceDrivers`
for that VM and device and nothing else.

| Segment | Tier | Covers |
|---|---|---|
| Payload to device series | unit | A captured response parses into the expected label sets, build values and series count |
| Device series plus mapping to a firing alert | rule tests (promtool) | Over synthetic series: a build below the minimum produces exactly one series; an equal build, a newer build and an uncovered device produce none |
| Mapping ConfigMap to reference metric | functional, Linux guests | Applying, mutating and deleting the mapping is reflected in `kubevirt_guest_device_driver_minimum_build_number` |

### Edge and Regression Cases

Unit:

- A mapping with an unrecognized schema version, two minimums for one key, or a `driverVersion`
  without a build number is rejected, the previous one retained, and the invalid-config gauge set
- The build number parser accepts exactly four 16-bit decimal components and returns the last
- The mapping loader and the metric normalize device identifiers identically, so an entry written in
  the mapping's form matches a device reported in the agent's form

Rule tests (promtool):

- With no reference series, the recording rule is empty and the alert cannot fire
- Duplicate reference series, as produced by multiple controller replicas, do not abort the rule

Functional, against a cluster with Linux guests only:

- No device series are produced, and every other VMStatsCollector metric is still reported
- Whichever disable mechanism is chosen, it suppresses the metric, the recording rule and the
  alert individually, and a test asserting that is written alongside it

## Implementation History

- 2026-07-09: VEP created: [#386](https://github.com/kubevirt/enhancements/pull/386)
- 2026-11-23: Alert on drivers below a minimum: [#532](https://github.com/kubevirt/enhancements/pull/532)
- 2026-11-24: Implemented in kubevirt/kubevirt-observability-controller#38, HCO ConfigMap migrated in
  kubevirt/hyperconverged-cluster-operator#4755

## Graduation Requirements

### Alpha

Ships while VEP #143 is alpha. See Target releases.

kubevirt/kubevirt-observability-controller:

- [x] Device metric, one series per device per VM, labelled with `guest_os_version_id`
- [x] ConfigMap loader, with `deviceID` normalization shared with the metric, and `list`/`watch`
      added to the ClusterRole
- [x] Reference metric and `config_invalid` gauge
- [x] The recording rule and the `OutdatedGuestDeviceDrivers` alert
- [x] promtool harness ported in, with tests for the rule and alert
- [x] Unit and functional tests, per Functional Testing Approach
- [ ] Example ConfigMap in the docs, stating that minimums compare the last version component
- [ ] Operator documentation, including that the metric is Windows-only and the alert needs a
      ConfigMap the distributor or administrator supplies

kubevirt/monitoring:

- [x] `OutdatedGuestDeviceDrivers` runbook merged

### Beta

Graduates with VEP #143, with no feature gate of its own.

#### On-By-Default Readiness

Silent by default:

- [ ] With no ConfigMap, the alert provably cannot fire
- [ ] A Linux-only cluster shows no device series and no regression in any other VMStatsCollector
      metric
- [ ] An invalid mapping retains the previous valid one rather than falling back to empty, so a bad
      edit cannot clear active alerts
- [ ] The ConfigMap watch is scoped to the single named ConfigMap, not the whole namespace

A way to disable it if it misbehaves:

- [ ] A supported way to disable this metric, its rule and its alert without enumerating every other
      metric to keep, and without disabling `VMStatsCollector` wholesale
- [ ] That mechanism documented for operators

#### Scale

- [ ] No measurable cardinality impact in scale testing

### GA

- [ ] Feedback gathered on label selection and metric structure from real deployments
- [ ] Feedback gathered on the ConfigMap schema, including whether `guest_os_version_id` is granular
      enough or a `guest_os_kernel_release` dimension is needed
- [ ] TTL refined if operational experience calls for it
- [ ] ConfigMap schema promoted to `version: v1`, with documented `v1alpha2` compatibility
