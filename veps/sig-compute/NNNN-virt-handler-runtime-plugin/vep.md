# VEP #NNNN: Virt-Handler Runtime Plugin

## Design

This follow-up to [VEP 82](../82-plugin-virtstack/vep.md) moves
virtualization-stack-specific node operations behind a runtime plugin.

### Deployment and responsibilities

**Each runtime plugin is deployed as a privileged DaemonSet**, with one pod
on each node where its stack is supported. It serves gRPC over a local UNIX
socket.

| Component | Responsibility |
| --- | --- |
| `virt-handler` | Reconcile VMIs, choose the matching plugin, invoke high-level RPCs, retry failures, and report Kubernetes status. |
| Runtime plugin | Find the VM processes and perform backend-specific privileged operations. |

Core calls operations such as `AdjustVMIResources`; it does not prescribe
individual process, affinity, cgroup, or resource-limit changes.

The administrator grants the DaemonSet the host PID visibility and host mounts
needed by its implementation. Privileged mode alone does not provide those
views. The plugin does not need Kubernetes write access for node labels.

VM lifecycle commands still go directly from `virt-handler` to the selected
launcher through the existing Command API. The Notify API, `api.Domain`, and
`DomainStats` remain shared handler-launcher contracts.

### Registration and socket discovery

Use the cluster-scoped `VirtualizationStackPlugin` CRD proposed by VEP 82.
The resource registers a stack; it does not deploy the plugin.

```yaml
apiVersion: virstackplugin.kubevirt.io/v1alpha1
kind: VirtualizationStackPlugin
metadata:
  name: cloud-hypervisor-mshv
spec:
  controller:
    service:
      namespace: cloud-hypervisor-system
      name: cloud-hypervisor-controller-plugin
      port: 9443
  runtime:
    socketName: cloud-hypervisor-mshv.sock
  launcher:
    image: quay.io/company-x/virt-launcher:clh-mshv
```

Proposed host socket directory: `/var/run/kubevirt/virt-runtime/`.
Both `virt-handler` and the plugin mount it through `hostPath`; the plugin
publishes the socket named in its registration. Socket permissions restrict
access to the handler and administrator-managed workloads.

`virt-handler` watches registrations and connects to the declared socket on
its own node. It does not scan for arbitrary sockets. `socketName` must be a
filename, not a path, and registrations must not share a socket name.

Missing sockets are retried with backoff. After a disconnect or registration
update, the handler reconnects and repeats API negotiation. Socket existence
alone does not mean the plugin is usable.

### Runtime API

| RPC | Purpose |
| --- | --- |
| `GetInfo` | Return stack identity, supported RPC API versions, capabilities, and required shared hypervisor device, if any. |
| `AdjustVMIResources` | Apply backend-specific VM resource adjustments. |
| `HandleHousekeepingResources` | Configure supporting VM processes and threads. |
| `GetNodeLabels` | Return opaque node-label key/value pairs. |
| `GetMigrationEndpoints` | Prepare target migration sockets and return their names and paths. |

`GetInfo` uses a fixed discovery schema. The handler verifies the stack identity
against the registration and selects the highest mutually supported API version.
No shared version or a missing required RPC makes that runtime unavailable.
The negotiated version is used for subsequent calls.

Per-VMI requests carry the VMI UID, launcher pod UID, node identity, and relevant
VMI, domain, and configuration data. The plugin verifies the target before
operating on its processes. Protobuf schemas will define the exact fields.
Repeated resource and housekeeping calls must be safe.

RPCs have deadlines and return explicit errors. The handler retries temporary
failures and reports errors on affected VMIs.

### Labels, devices, and choosing the plugin

Node labels are opaque to core. The runtime and controller plugins agree on
the labels and matching launcher node selectors. Core checks Kubernetes label
syntax, tracks labels it applies, and removes stale plugin-owned labels without
overwriting labels owned by another component. Label prefixes must prevent
collisions between stacks.

Core remains the sole kubelet device-plugin registrar for shared hypervisor
devices reported by `GetInfo` and deduplicates reports from multiple stacks.
Exclusive devices remain outside this mechanism.

The handler uses the VMI's resolved stack identity, preserved for its lifetime.
The user should specify the virt-stack for a given VMI using its a field in its spec.
Plugin failure must not redirect an alternate-stack VMI to built-in QEMU logic.

With the feature gate disabled, default behavior is unchanged. Default and
legacy VMIs retain the built-in implementation. New handlers must continue
managing existing launchers without recreating their pods.

### Live migration

Keep the existing migration sequence and launcher Command API.
Each launcher plugin implements `SyncMigrationTarget`, `MigrateVirtualMachine`,
`CancelVirtualMachineMigration`, and `FinalizeVirtualMachineMigration` using
its native virtualization stack.

The runtime plugin performs backend-specific node preparation, including socket
setup and repairs. Core owns the TLS listeners, certificates, and forwarding
between nodes.

`GetMigrationEndpoints` returns an array, with one entry per target socket:

```yaml
sockets:
  - name: libvirt-migration-control
    path: /run/libvirt/virtqemud-sock
  - name: libvirt-migration-state
    path: /run/kubevirt/migration-state.sock
```

Paths refer to the target launcher's filesystem and must be accessible to
`virt-handler`. The call succeeds once the sockets accept connections.
Names are opaque to core; the source and target plugins agree on their meaning.
A backend needing only one socket returns one entry.

The flow is:

1. Target handler prepares storage, networking, and devices, then calls the
   target launcher's `SyncMigrationTarget`.
2. Target handler calls the runtime plugin's `GetMigrationEndpoints`, passing
   the VMI and migration identity.
3. Target handler creates a TLS listener for each socket and publishes the
   target address and socket-name-to-TCP-port mapping in VMI migration status.
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

Core and launcher use the same path construction; no socket-path list is added
to the Command API. Names must be filename-safe and paths must fit UNIX-socket
length limits. Legacy launchers retain their existing socket-path convention.

### End-to-end installation and invocation

1. **Install KubeVirt.** Enable `PluggableVirtualizationStack`. The operator
   installs the registration CRD and gives `virt-handler` read/watch access
   and the shared socket mount. Core starts without waiting for plugins.
2. **Install the stack.** The administrator deploys its privileged runtime
   DaemonSet and controller Service/Deployment, and makes its launcher image
   available. Each runtime pod publishes its local socket.
3. **Register the stack.** Create the `VirtualizationStackPlugin` resource
   above. Plugin workloads may precede KubeVirt, but this resource can only
   be created after its CRD exists.
4. **Discover and negotiate.** Each handler observes the registration, connects
   locally, calls `GetInfo`, and chooses a compatible API version.
5. **Prepare the node.** The handler calls `GetNodeLabels`, applies labels,
   and updates shared device registrations using the `GetInfo` response.
6. **Create a VMI.** The controller uses the matching controller plugin to
   render a launcher pod with selectors for a compatible, available node.
7. **Invoke the runtime.** During VMI reconciliation, the handler calls the
   matching runtime's resource and housekeeping RPCs. The plugin performs
   the operations and returns their results.

Availability must be tracked per stack so one failed runtime does not disable
other stacks on the node. Core removes that stack's scheduling availability
label on failure; affected VMI operations retry without changing stacks.
The exact availability label and controller selector are a shared contract
with the controller-plugin VEP.

## Implementation Plan

1. Isolate existing resource and housekeeping implementations without changing
   behavior; keep them available for default and legacy VMIs.
2. Define protobuf requests, responses, discovery negotiation, errors, and
   retry-safe operation semantics.
3. Add feature-gated registration watching, socket mounts, connection handling,
   and API negotiation to `virt-handler`.
4. Route resource and housekeeping calls using the resolved VMI stack identity;
   preserve the existing launcher Command and Notify APIs.
5. Add opaque-label ownership, per-stack availability, and shared-device
   aggregation. Coordinate selection and scheduling with the controller VEP.
6. Add runtime migration endpoint preparation and named port mappings. Reuse
   core TLS proxies with the shared source socket convention, retaining the
   legacy migration path and unchanged launcher Command API.
7. Provide a reference runtime DaemonSet and installation manifests; exercise
   the full registration-to-invocation flow and legacy-launcher compatibility.

Rollback with alternate-stack VMIs, the availability-label
format, and single-versus-multiple device reporting remain open.
