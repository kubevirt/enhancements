# VEP #NNNN: Virt-Handler Runtime Plugin

## Design

This follow-up to [VEP 82](../82-plugin-virtstack/vep.md) moves
virtualization-stack-specific node operations by the `virt-handler` behind a runtime plugin.

### Deployment and responsibilities

**The Runtime Plugin is deployed as a privileged DaemonSet**, with one pod
on each node. It serves gRPC over a local UNIX socket.

| Component | Responsibility |
| --- | --- |
| `virt-handler` | Reconcile VMIs, invoke high-level RPCs, retry failures, and report Kubernetes status. |
| Runtime plugin | Find the VM processes and perform backend-specific privileged operations. |

`virt-handler` core invokes operations such as `AdjustVMIResources` via RPC; it does not prescribe
individual process, affinity, cgroup, or resource-limit changes.
Plugin authors and cluster administrators are responsible for the privileged implementation of these core operations and their safety and correctness.

The administrator grants the DaemonSet the host PID visibility and host mounts
needed by its implementation (similar to `virt-handler`).

VM lifecycle commands still go directly from `virt-handler` to the selected
launcher through the existing Command API. The Notify API, `api.Domain`, and
`DomainStats` remain shared handler-launcher contracts.

### Registration and socket discovery

Use the cluster-scoped `VirtualizationStackPlugin` CRD proposed by VEP #82.
The cluster admin declares the name of the UNIX socket which the runtime is listening on.

```yaml
apiVersion: virstackplugin.kubevirt.io/v1alpha1
kind: VirtualizationStackPlugin
metadata:
  name: cloud-hypervisor-mshv
spec:
  ...
  runtime:
    socketName: cloud-hypervisor-mshv-runtime.sock
  ...
```

Proposed host socket directory: `/var/run/kubevirt/virt-runtime/`.
Both `virt-handler` and the plugin mount it through `hostPath`. `virt-handler` looks for the socket declared in the `VirtualizationStackPlugin` CR and connects to it. Missing sockets are retried with backoff. 

### Runtime API

| RPC | Purpose |
| --- | --- |
| `GetInfo` | Return stack identity, supported RPC API versions, and required hypervisor device. |
| `AdjustVMIResources` | Apply backend-specific VM resource adjustments. |
| `HandleHousekeepingResources` | Configure supporting VM processes and threads. |
| `GetNodeLabels` | Return opaque node-label key/value pairs. |

`GetInfo` uses a fixed discovery schema. The handler verifies the virtualization-stack identity
against the name in the `VirtualizationStackPlugin` CR and selects the highest mutually supported API version.
No shared version or a missing required RPC makes the runtime plugin unavailable.
The negotiated version is used for subsequent calls.

RPCs have deadlines and return explicit errors. The handler retries temporary
failures and reports errors on affected VMIs. Repeated resource and housekeeping calls must be safe.

### RPC arguments and runtime behavior

#### `GetInfo`

**Arguments:** None; this is the initial call before API version negotiation.

**Returns:** Virtualization Stack name, supported runtime API versions, and
the required hypervisor device (e.g., `/dev/mshv`).

**Runtime behavior:** Report the plugin virtualization stack's name. Core checks this against the registration and negotiates a version before invoking the other RPCs. Device registration remains core's responsibility.

#### `AdjustVMIResources`

**Arguments:**

  1. VMI snapshot, including its UID, resource requirements, lifecycle
and migration status, and `Status.ActivePods`
  2. Node name
  3. KubeVirt configuration

**Returns:** Success or an error.

**Runtime behavior:**

1. Determine whether the VMI needs backend-specific resource adjustments, e.g., memlock limit adjustment.
2. Find the local launcher socket and obtain its server PID as described below.
3. Locate the backend's VMM or supporting daemon. The Libvirt implementation
   adjusts `virtqemud` before local QEMU creation and QEMU once it is running.
4. Apply the required adjustments. Today this sets unlimited memory-lock limits
   for workloads requiring locked memory, including VFIO, realtime, and SEV.

Core retains calls before VM startup, after migration-target preparation, when
the target domain is detected, and before host-device hotplug. It does not
choose backend processes or prescribe individual resource-limit changes.

**Launcher discovery:** Virt-controller maintains `VMI.Status.ActivePods` as
pod UID to node name; handler passes that observed information in the request.
The runtime filters entries for its node and checks each candidate's socket:

```text
<kubelet-pods-mount>/<pod-UID>/volumes/kubernetes.io~empty-dir/sockets/launcher-sock
```

The existing handler mounts the kubelet pods directory at `/pods`. The plugin
needs access to the same host directory, but may use a different mount path.
Exactly one matching local socket must exist; absence or ambiguity returns an
error for retry. During migration, node filtering distinguishes source and
target launchers on different nodes.

Connect to the UNIX socket and use Linux `SO_PEERCRED` to obtain its server PID.
The plugin needs host PID visibility and may inspect the server's parent and
process tree to locate its VMM.

#### `HandleHousekeepingResources`

**Arguments:** Serialized VMI and Domain snapshots, plus local node identity.
The Domain snapshot may be absent.

**Returns:** Success or an error.

**Runtime behavior:** Discover the local launcher and backend threads, then
apply supporting-process and thread policy after launcher synchronization.
For Libvirt/KVM this includes creating the housekeeping cgroup, assigning its
CPU set, moving non-vCPU threads, setting realtime vCPU scheduling, and applying
PIT-thread scheduling/affinity where required. Other runtimes implement their
equivalent behavior without assuming QEMU thread names or cgroup layout.

Use the launcher socket/PID discovery described for `AdjustVMIResources`, then
inspect `/proc/<launcher-PID>/cgroup` to locate the original container cgroup.
The runtime owns discovery, cgroup handling, and backend thread configuration;
it does not call handler helpers.

The runtime may use Domain fields such as `CPUTune.EmulatorPin` for placement.
If Domain cannot represent the needed information, it queries its launcher
through a backend-specific UNIX socket. Launcher and runtime authors agree on
the path and protocol; the socket directory must also be accessible to the
runtime DaemonSet. This does not replace the handler-launcher Command/Notify
APIs or Domain reporting required by core.

Domain is an observed snapshot, not guaranteed fresh after synchronization.
The runtime handles absent or stale information or queries its launcher for
current state. A stack-agnostic replacement for Domain is outside this VEP.

#### `GetNodeLabels`

**Arguments:** Local node identity and relevant label-generation configuration.

**Returns:** Opaque node-label key/value pairs.

**Runtime behavior:** Discover the stack's local capabilities and generate the node labels.
Core applies them, tracks ownership,
and removes stale labels. The runtime does not write Kubernetes node labels or
provide core's host topology.

### Node Labels and Hypervisor device

Node labels are opaque to core. The `virt-runtime` and `virt-controller`'s launcher rendering plugins agree on
the labels and matching launcher node selectors. Core `virt-handlers checks Kubernetes label
syntax, tracks labels it applies, and removes stale plugin-owned labels without
overwriting labels owned by another component. Each plugin's labels will have it's name as a prefix so that in the future when multiple plugins on same node/cluster are considered, they don't collide with each other.

Core remains the sole kubelet device-plugin registrar for hypervisor
device reported by `GetInfo`. It sets up a custom device plugin (like today) for the hypervisor device reported by the `virt-runtime` plugin. Every runtime plugin must report a hypervisor device; device-less plugins are
not supported.

### Node scheduling readiness

Virt Handler core advertises node readiness only
after the handshake/API negotiation succeeds, the plugin's node labels are
applied, and the device plugin for its reported hypervisor device has started
and registered with kubelet. Kubelet publishes device capacity; core does not
wait for an exact capacity value before advertising readiness.

### Launcher ownership conventions

Retain existing file, device, and TAP ownership conventions for now. Non-root
launcher implementations use UID/GID 107; this is a shared convention, not a
QEMU requirement. The handler's existing `qemu` account lookup remains compatible
with that identity. Configurable launcher ownership is follow-up work, not a
prerequisite for the runtime plugin interface.

### Node topology

Handler core discovers host topology directly from Linux sysfs, independently
of the selected stack. It supplies NUMA nodes, memory, hugepage information,
node distances, and CPU topology through the existing launcher Command API.
Libvirt capability XML is no longer the source of this topology.

### Guest-agent status

Adopt the separation proposed in
[kubevirt/kubevirt#15402](https://github.com/kubevirt/kubevirt/pull/15402)
(closed without merging). Guest-agent connection detection and support checks
belong in the launcher, not the runtime DaemonSet or handler core.

Extend the existing `GetGuestInfo` response with:

| Field | Meaning |
| --- | --- |
| `GuestAgentConnected` | Whether the guest agent is connected. |
| `GuestAgentSupported` | Whether it supports the operations required for this VMI. |
| `GuestAgentUnsupportedReason` | Explanation when a connected agent is unsupported. |

Each launcher implements these checks for its own agent. The Libvirt launcher
owns the `org.qemu.guest_agent.0` channel check, required QEMU guest-agent
commands, and supported-version fallback. Core supplies relevant VMI and
configuration data but only uses the returned fields to maintain
`AgentConnected` and `UnsupportedAgent` conditions.

Prefer connection events and cached capability information over additional
connection polling. Refresh the cache on reconnect or relevant configuration
changes; continue polling guest information that can change while connected.

Keep the RPC name and preserve compatibility with older launchers through
Command API version negotiation. Missing status fields must not be interpreted
as a disconnected or unsupported agent. Older launchers retain the existing
handler-side checks; plugin launchers must support the extended response.
Update guest-info REST, metrics, and freezer callers together. No new runtime
RPC is needed.

### Console and downward metrics

Launcher plugins retain the existing console socket directory:

```text
/run/kubevirt-private/<VMI-UID>/
```

For supported features, they provide `virt-vnc`, `virt-serial0`, and
`virt-usbredir-<slot>` sockets with the corresponding protocols. Handler core
continues connecting and forwarding directly; no console discovery RPC is added.

Add `GetVmmVersion` to the launcher Command API without removing `GetQemuVersion`.
Each launcher returns its own VMM version information, which the downward
metrics reporter accesses through the generic `GetVmmVersion` client method.
The client tries the new RPC and falls back to `GetQemuVersion` only on gRPC
`Unimplemented`, not on timeouts or other errors. New Libvirt launchers serve
both names through the same implementation so older handlers also work.
Keep the legacy RPC for the supported compatibility window; running VMIs do
not need their launcher pods restarted.

### Live migration

Keep the existing migration sequence and launcher Command API RPC names.
Each launcher plugin implements `SyncMigrationTarget`, `MigrateVirtualMachine`,
`CancelVirtualMachineMigration`, and `FinalizeVirtualMachineMigration` using
its native virtualization stack.

The target launcher creates and serves its backend's migration UNIX sockets.
It reports their endpoints directly to core `virt-handler`; no runtime endpoint-discovery RPC
is needed. Any required privileged node preparation remains a runtime
responsibility. Core owns the TLS listeners, certificates, and forwarding
between nodes.

Extend the existing `SyncMigrationTarget` response with an optional
`migrationEndpoints` array, with one entry per target socket:

```yaml
success: true
migrationEndpoints:
  - name: libvirt-migration-control
    path: /run/libvirt/virtqemud-sock
  - name: libvirt-migration-state
    path: /run/kubevirt/migration-state.sock
```

Paths refer to the target launcher's filesystem and must be accessible to
`virt-handler`. For endpoint-reporting launchers, success means all returned
sockets accept migration connections, not merely that configuration was accepted.
Preparation still in progress returns a retryable result; core does not publish
destination ports or start source migration until preparation succeeds.
Names are opaque to core; the source and target plugins agree on their meaning.
A backend needing only one socket returns one entry.

Add the optional repeated field to the shared protobuf `Response`, preserving
existing fields and the RPC signature. Older handlers ignore the new field;
new handlers retain the hardcoded endpoint convention for older built-in
launchers that do not report endpoints. Custom launchers must supply endpoints;
an absent list must not trigger Libvirt fallback.

The flow is:

1. Target handler prepares storage, networking, and devices, then calls the
   target launcher's `SyncMigrationTarget`.
2. Target launcher returns success with ready migration endpoints.
3. Target handler uses the response to create a TLS listener for each socket
   and publishes the target address and socket-name-to-TCP-port mapping in VMI
   migration status.
4. Source handler reads that mapping and creates a local UNIX proxy for each
   name, forwarding to the corresponding target TLS port.
5. After source node preparation, source handler invokes the launcher's
   `MigrateVirtualMachine`. The launcher connects to the local proxy sockets.
6. Core retains existing migration status, cancellation, finalization, and
   proxy cleanup handling.

Source proxy paths follow a shared convention:

```text
<virtShareDir>/migration-sockets/<migration-UID>/<socket-name>.sock
```

Core and launcher use the same source path construction; target endpoint paths
are not passed to `MigrateVirtualMachine`. Names must be filename-safe and paths
must fit UNIX-socket length limits. Legacy launchers retain their existing
socket-path convention.

### End-to-end installation and invocation

1. **Install the operator.** Install `virt-operator` without creating a KubeVirt
   CR. It installs the plugin registration CRD during bootstrap.
2. **Wait for the CRD.** Wait for its `Established=True` condition.
3. **Install the virtualization stack plugins.** The administrator deploys its privileged runtime
   DaemonSet and controller Service/Deployment, and makes its launcher image
   available.
4. **Register the stack.** Create the `VirtualizationStackPlugin` resource
   pointing to those components.
5. **Install KubeVirt core.** Create the KubeVirt CR with
   `PluggableVirtualizationStack` enabled. The operator installs core and gives handler registration read/watch
   access and the shared socket mount.
6. **Discover and negotiate.** Each handler observes the registration, connects
   locally, calls `GetInfo`, and chooses a compatible API version.
7. **Prepare the node.** The handler calls `GetNodeLabels`, applies labels,
   starts and registers the hypervisor device plugin using `GetInfo`, and
   discovers host topology directly. It then advertises that stack's readiness.
8. **Create a VMI.** The `virt-controller` uses the launcher-rendering
   controller plugin to render a launcher pod with stack availability selectors
   and the required device resource.
9. **Invoke the runtime.** During VMI reconciliation, the handler calls the
   runtime's resource and housekeeping RPCs. The plugin performs
   the operations and returns their results.


## Implementation Plan

1. Isolate existing resource and housekeeping implementations without changing
   behavior; keep them available for default and legacy VMIs.
2. Define protobuf requests, responses, discovery negotiation, errors, and
   retry-safe operation semantics. Implement plugin-owned launcher discovery
   for resource adjustment and housekeeping. Pass serialized VMI/Domain data
   without handler object dependencies.
3. Add operator-bootstrap CRD installation and feature-gated registration
   watching.
   Add socket mounts, connection handling, and API negotiation to `virt-handler`.
4. Route resource and housekeeping calls using each VMI's resolved stack;
   preserve compatibility with the launcher Command and Notify APIs.
5. Add opaque-label ownership, hypervisor device registration, and
   virtualization-stack readiness after the handshake, label application, and
   device-plugin startup.
   Coordinate per-VMI node selector assignment and scheduling with the controller VEP.
6. Extend `SyncMigrationTarget` responses with ready migration endpoints and
   add named port mappings. Reuse core TLS proxies with the shared source socket
   convention, retaining compatibility with legacy built-in launchers.
7. Move guest-agent connection and support checks into the launcher, extend
   `GetGuestInfo`, and update its callers and generated schemas. Preserve
   legacy-launcher behavior and use connection events where available.
8. Replace Libvirt-derived node topology with direct core discovery, preserving
   the topology fields sent to launchers. Keep Libvirt capability consumption
   confined to the default node labeller.
9. Document console and ownership requirements. Add `GetVmmVersion` across the
   Command API, launchers, and downward metrics reporter, with legacy RPC
   fallback and dual-method support in new Libvirt launchers.
10. Provide a reference runtime DaemonSet and installation manifests; exercise
   the full registration-to-invocation flow and legacy-launcher compatibility.

Follow-up details:

- CBT readiness currently depends on `api.Domain` disk `DataStore` fields.
  A backend-independent readiness contract still needs agreement.
- SEV device preparation and its mapping to runtime RPCs need specification;
  the existing attestation Command API remains the starting point.
- QEMU-specific emulation status and error-string handling need normalization
  or explicit launcher compatibility requirements.
- Rollback with alternate-stack VMIs remains open.

## Open Questions

1. Should the Virt-Runtime Plugin expose supported capabilities/functions through which the virt-handler core can determine if the plugin is supported ?
2. All plugins for virtualization stack, i.e., Virt Controller's plugin, Virt Runtime plugin and Virt Launcher are implemented by the same Plugin authors. They are responsible for maintaining any shared volume between the Virt Runtime pod and the Virt Launcher, which might be needed for any out-of-band management of the running VM process for specific virtualization stacks.