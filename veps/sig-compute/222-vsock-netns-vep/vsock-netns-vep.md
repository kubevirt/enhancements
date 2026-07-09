# VEP #222: VSOCK network namespace confinement

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version: v1.10.0, v1.11.0
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone /
release*.

- [ ] (R) Enhancement issue created, which links to VEP dir
  in [kubevirt/enhancements](https://github.com/kubevirt/enhancements)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [x] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

VSOCK (`AF_VSOCK`) is a socket family for communication between a virtual
machine and its host, identified by a Context ID (CID) rather than an IP
address.

Linux 7.0
introduces [VSOCK network namespace support](https://stefano-garzarella.github.io/posts/2026-02-11-vsock-netns/),
allowing VSOCK sockets to be confined within network namespaces. This VEP
proposes leveraging this kernel feature in KubeVirt to isolate each
`VirtualMachine`'s (VM) VSOCK device within its Pod's network namespace,
improving security by preventing unauthorized VSOCK access from other
processes on nodes, including those running in other Pods.

## Motivation

The current VSOCK implementation in KubeVirt has several limitations:

1. **No isolation of VM VSOCK devices**: VSOCK is a host-guest protocol - guests
   can only connect to CID 2 (the host) and cannot directly reach other guests.
   However, the host-to-guest (H2G) direction is unconfined: in `global` mode,
   the `vhost-vsock` kernel module exposes all guest CIDs to the host's entire
   VSOCK socket layer, without requiring elevated privileges. VSOCK traffic is
   invisible to Kubernetes NetworkPolicies, which operate on IP-based traffic,
   so there is no way to restrict this access with standard Kubernetes tooling.

2. **Global CID space**: CIDs must be globally unique across all VMs on a node.
   The `virt-controller` maintains a CID allocator to prevent collisions, adding
   complexity with in-memory state. It also complicates cross-cluster live
   migration, since CIDs must be coordinated between source and target clusters
   to avoid conflicts.

### Attack surface of unconfined VSOCK

**Pod-to-VM attack**: A compromised or malicious Pod on the node can open an
`AF_VSOCK` socket without any elevated privileges and scan CIDs starting from 3
to discover VMs. For each discovered VM, the Pod can connect to VSOCK services
running inside the guest - including SSH if systemd v256 auto-started `sshd` on
VSOCK. The VM owner has no way to prevent or even detect the connection.

**VM-to-host attack**: A guest can connect to CID 2 (the host) on any VSOCK
port. If the host runs services on VSOCK - such as the `sshd` instance that
systemd v256 starts automatically when `openssh-server` is installed - the
guest can reach them, enabling lateral movement from VM to node.

Both directions bypass IP-based firewalls and IDS/IPS, since VSOCK traffic
never touches the IP networking stack. Malware such as
[BRICKSTORM](https://seclists.org/oss-sec/2025/q4/298) already exploits
unconfined VSOCK for covert C2 channels on VMware, and the same techniques
apply to KVM.

To mitigate the lack of isolation, KubeVirt provides mutual TLS authentication
via a gRPC service (`System.CABundle`) that runs permanently on each node to
deliver CA certificates to guests.

With kernel-level VSOCK namespace support, the network namespace itself becomes
the trust boundary, making the global CID allocator unnecessary on nodes
running in `local` mode and providing stronger isolation by default. The TLS
identity verification remains valuable as an additional layer - proving the
peer is `virt-handler` - but no longer needs to run permanently on the host
for `local`-mode Pods.

## Goals

- Improve VSOCK security by isolating each VM's VSOCK device within its Pod's
  network namespace, so that only processes within the same Pod can access it
- The change should be transparent to users of the KubeVirt VSOCK API - existing
  workflows continue to work without modification
- Support both `global` and `local` VSOCK modes during beta, so clusters
  that have not yet adopted Linux 7.0 continue to function
- Use the beta period to gather feedback on whether `global` mode support
  should be retained long-term or removed at GA

## Non Goals

- Supporting cross-network-namespace VSOCK access to VMs
- Adding new API fields or feature gates - the existing `VSOCK` feature gate
  is reused
- Providing a knob to switch between `global` and `local` mode - the mode is
  determined by the kernel sysctl on nodes

## Definition of Users

- **VM owners**: Users running VMs with VSOCK enabled who benefit from improved
  isolation without configuration changes
- **Guest application developers**: Developers of applications inside VMs
  that communicate over VSOCKs
- **Cluster administrators**: Operators who deploy and configure KubeVirt on
  clusters with varying kernel versions

## User Stories

- As a **VM owner**, I want VSOCK connectivity to continue working transparently
  regardless of whether namespace confinement is active, with no changes to my
  `VirtualMachine` definitions.

- As a **guest application developer**, I want to expose a service over VSOCK
  and have external clients connect to it through the KubeVirt API (subject to
  RBAC), knowing that namespace confinement ensures only authorized access via
  the API proxy.

- As a **guest application developer**, I want to run a sidecar container
  alongside my VM that communicates with the guest over VSOCK within the shared
  Pod network namespace, relying on namespace confinement for isolation. TLS is
  still advised if mutual authentication between the sidecar and the guest is
  required.

- As a **cluster administrator**, I want `virt-handler` to clearly log when
  VSOCK is running in `global` mode on a node, so I can identify nodes that
  could benefit from kernel configuration updates.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Kernel Feature

Linux 7.0 adds VSOCK network namespace support via two sysctls
([`child_ns_mode`](https://docs.kernel.org/admin-guide/sysctl/net.html#child-ns-mode)
and [`ns_mode`](https://docs.kernel.org/admin-guide/sysctl/net.html#ns-mode)):

- `child_ns_mode` controls the mode assigned to newly created child network
  namespaces. It is read when a child namespace is created and is immutable once
  set to a non-default value:
  - `global` (default): Children inherit global mode - single CID space, all
    VSOCK sockets visible across all network namespaces on the host
  - `local`: Children get isolated VSOCK - sockets can only communicate within
    the same network namespace
- `ns_mode` (read-only) reflects the VSOCK mode of the current network namespace

The init namespace always operates in `global` mode - its `ns_mode` cannot be
changed. Setting `child_ns_mode = local` on the init namespace causes all
subsequently created child network namespaces to inherit `local` mode. Once a
child namespace is created, its mode is immutable. A node reboot is required to
reset `child_ns_mode` back to `global`.

### Change 1: Log VSOCK mode at `virt-handler` startup

When the VSOCK feature gate is enabled, `virt-handler` reads
`/proc/sys/net/vsock/child_ns_mode` at startup and logs the current mode.
It then registers the `devices.kubevirt.io/vhost-vsock` device plugin
regardless of the mode. `virt-handler` is not bound to a specific mode at
startup - it handles both `local` and `global` mode Pods at runtime, even
if `child_ns_mode` is changed after startup (see Change 3).

- If the sysctl does not exist or is set to `global`, `virt-handler` logs
  the mode. At a later point while still in beta, this could additionally
  emit a warning log. Optionally, a metric (e.g.
  `kubevirt_virt_handler_vsock_global_mode`) can be exposed so operators
  can set up monitoring alerts for nodes that still need migration.
- At GA, `virt-handler` may require `local` mode and refuse to register the
  device plugin when the sysctl is missing or set to `global`, or support
  for both modes may be retained.

Configuring the sysctl is the responsibility of the cluster administrator,
using system tooling such as `systemd-sysctl`, `MachineConfig`, or the node
tuning operator.

### Change 2: CID allocation

The dynamic CID allocator in `virt-controller` and the `VSOCKCID` field in
`VirtualMachineInstanceStatus` are kept. Since `virt-controller` operates at
the cluster level, it cannot distinguish between node modes and must continue
allocating unique CIDs for all VMs.

On `local`-mode nodes, `virt-launcher` reads its own `ns_mode`, ignores the
allocated CID, and substitutes a fixed CID (e.g. `3`, the minimum valid guest
CID) in the libvirt domain XML, since each network namespace has its own CID
space. On `global`-mode nodes, the allocated CID is used as before.

If `global` mode support is dropped at GA, the dynamic CID allocator can be
removed at that point.

### Change 3: Namespace-aware VSOCK dialing

With `local` mode, `virt-handler` can no longer dial VSOCK from the host
network namespace. Instead, it enters the Pod's network namespace before
dialing. In `global` mode, entering the Pod namespace before dialing is
harmless - the socket remains reachable. The dial path enters the Pod
namespace unconditionally and reads the Pod's `ns_mode` to determine the
CID. This also handles upgrade scenarios where stale Pods retain `global`
mode on a node that has since been configured for `local` mode:

1. Use the existing `podIsolationDetector` to discover the `virt-launcher`
   Pod's PID
2. Enter the Pod's network namespace using the existing `netns.New(pid).Do()`
   pattern
3. Read `/proc/sys/net/vsock/ns_mode` inside the namespace. If the Pod's
   `ns_mode` is `local`, use the fixed CID; if `global`, use
   `vmi.Status.VSOCKCID`
4. Call `vsock.Dial(cid, port)` inside the network namespace
5. Return the connection

This change applies to all VSOCK connections regardless of TLS. For plain
connections (without `?tls=true`), the flow ends here - the raw VSOCK
connection is returned. For TLS-authenticated connections, Change 4 adds
additional steps before the dial.

### Change 4: On-demand VSOCK CA service

The `System.CABundle` gRPC service currently runs permanently on CID 2
(port 1) in the host network namespace to deliver KubeVirt CA certificates to
guest agents for mutual TLS verification. In `global` mode, this continues to
work as before - guests can reach the host namespace service. In `local` mode,
guests cannot cross the namespace boundary to reach it, so `virt-handler`
starts the service on-demand inside the Pod's network namespace when a
TLS-authenticated VSOCK connection is requested.

The permanent service continues to run regardless of the detected mode.
In `global` mode it serves all guests as before. In `local` mode it is
unreachable (guests cannot cross the namespace boundary) but harmless - it
ensures backward compatibility with stale `global`-mode Pods that may remain
on a node that has since been configured for `local` mode. On-demand listeners
are not used for `global`-mode Pods because all namespaces share the same
VSOCK space in that mode, and concurrent listeners on CID 2:port 1 would
conflict.

When a VSOCK connection request with `?tls=true` arrives from `virt-api`
for a VM in a `local`-mode namespace:

1. `virt-handler` enters the Pod's network namespace
2. Starts a temporary `System.CABundle` gRPC listener on CID 2 (the host CID
   within that namespace)
3. Dials the guest's VSOCK server and initiates the TLS handshake.
   `virt-handler` is the TLS client; the guest application is the TLS server.
   During the handshake, the guest application can connect to CID 2 to fetch
   the CA bundle and use it to verify `virt-handler`'s client certificate.
   This happens as part of the TLS negotiation - no explicit synchronization
   between `virt-handler` and the guest is needed
4. Tears down the CID 2 listener after the handshake completes or fails

## API Examples

No API changes are required. Existing specifications with
`AutoattachVSOCK: true` continue to work as before.

## Alternatives

### Remove the TLS infrastructure entirely

Namespace confinement could replace TLS entirely, since only processes within
the same Pod can reach the VM's VSOCK device. This would simplify the codebase
but loses mTLS verification that the peer is `virt-handler` rather than a
malicious process within the Pod - relevant for confidential computing where
pod-level access should not imply VM-level access. Rejected in favor of making
the CA service on-demand.

### Enforce `local` mode from `virt-handler`

Have `virt-handler` write `child_ns_mode = local` itself at startup instead of
requiring external configuration. This would be secure by default but kernel
sysctl configuration is not `virt-handler`'s responsibility - dedicated node
configuration tooling (`systemd-sysctl`, `MachineConfig`, node tuning operator)
is more appropriate for this. Rejected to keep `virt-handler` focused on its
role and leave system-level tuning to the right tools.

### Drop `VSOCKCID` from the API

The `VSOCKCID` field in `VirtualMachineInstanceStatus` could be removed if
`global` mode is dropped at GA, since the CID would be fixed. This was
rejected because removing a field from the API is a breaking change. Keeping
the field is harmless and preserves API compatibility.

### Require `local` mode from the start

Instead of supporting both modes, `virt-handler` could require `local` mode
from beta and refuse to register the device plugin on nodes without support.
This was rejected because Linux 7.0 - the first kernel with VSOCK namespace
support - is still very new and not yet widely deployed. Requiring it
immediately would force all clusters to upgrade their kernel before using
VSOCK with KubeVirt. Supporting both modes during beta gives operators time
to adopt Linux 7.0 and provides a feedback period to decide whether `global`
mode should be retained long-term or removed at GA.

### New feature gate for namespace confinement

A separate `VSOCKNetNS` feature gate could independently control namespace
confinement. This was rejected in favor of extending the existing `VSOCK` gate -
namespace confinement is an improvement to the VSOCK feature, not a separate
feature.

## Scalability

No scalability concerns. The sysctl is read once per node at `virt-handler`
startup. The network-namespace-aware dial adds one `setns` syscall per VSOCK
connection, which is negligible.

## Update/Rollback Compatibility

### Upgrade

No node configuration changes are required for upgrade. `virt-handler` reads
`/proc/sys/net/vsock/child_ns_mode` at startup and logs the detected mode.
Since `child_ns_mode` can be changed from `global` to `local` at any time
(though not back without a reboot), `virt-handler` does not rely on the
startup value - it determines the effective mode per Pod at dial time
(see Change 3). This means `virt-handler` can simultaneously handle
pre-existing Pods that inherited `global` mode and new Pods created after
the switch to `local` mode. Nodes with `local` mode get namespace-isolated
VSOCK; nodes without the sysctl or with `global` mode continue to work as
before.

If old `virt-launcher` Pods remain (depending on `spec.workloadUpdateStrategy`
in the KubeVirt CR), they retain their inherited mode and dynamically-assigned
CIDs. The namespace-aware dial (Change 3) handles both modes, since entering
a global-mode network namespace before dialing is harmless.

### Rollback

Since `virt-handler` does not modify the sysctl, rollback does not require
node reboots. The old `virt-handler` dials VSOCK from the host network
namespace, which cannot reach into local-mode namespaces. If `local` mode was
configured, the administrator must reboot nodes to reset the sysctl back to
`global` (it is write-once) before the old VSOCK behavior works again.

### GA upgrade

Whether the GA release will require `local` mode or retain support for both
modes is an open question to be decided based on beta feedback. If `global`
mode support is removed at GA, administrators must configure
`child_ns_mode = local` on all nodes before upgrading. Nodes without `local`
mode would no longer register the device plugin, and VMs with VSOCK enabled
would not be scheduled there. This requirement would be communicated in
release notes for the preceding beta releases.

## Functional Testing Approach

- **Unit tests**: Mock `netns.Do()` and `vsock.Dial()` to verify namespace-aware
  dialing logic; test sysctl detection with mock filesystem
- **Functional tests**: VM with VSOCK enabled - verify VSOCK connectivity
  through the API works end-to-end
- **Isolation test**: Verify that a process outside a VM's Pod network namespace
  cannot connect to the VM's VSOCK device when `local` mode is active
- **Global mode test**: Verify that VSOCK continues to work on nodes without
  `child_ns_mode` support (global mode fallback)
- **Existing tests**: Run `tests/vmi_vsock_test.go` to ensure no regressions

## Implementation History

<!--
To be filled as implementation progresses.
-->

## Graduation Requirements

### Beta (v1.10.0)

The VSOCK feature gate graduates to Beta. This VEP adds namespace
confinement support as part of the graduation:

- [ ] `virt-handler` logs `child_ns_mode` at startup and handles both modes
  at runtime
- [ ] Fixed CID used in `local` mode, dynamically-allocated CID in `global`
  mode
- [ ] VSOCK REST proxy enters Pod network namespace before dialing
  (unconditionally, both modes)
- [ ] gRPC CA service on-demand per-Pod-namespace in `local` mode; permanent
  service kept for `global` mode
- [ ] Unit and functional tests covering both modes
- [ ] Documentation updated with namespace confinement behavior and kernel
  requirements
- [ ] Feature gate enabled by default

### Beta (v1.11.0)

- [ ] (optional) `virt-handler` emits a warning log when operating in
  `global` mode
- [ ] (optional) `virt-handler` exposes a metric for `global` mode, enabling
  monitoring alerts
- [ ] Evaluate whether `global` mode support should be retained long-term or
  removed at GA, based on feedback gathered during beta (e.g. adoption of
  Linux 7.0, clusters that cannot upgrade in the near term)
- [ ] Evaluate whether guests actively polling the permanent `System.CABundle`
  service in `global` mode requires extended support beyond GA
- [ ] Stable across at least one release with no regressions

### GA

- [ ] Decide whether to require `local` mode or retain `global` mode support,
  based on beta feedback
- [ ] If `global` mode is removed: `virt-handler` requires
  `child_ns_mode = local`, dynamic CID allocator removed, permanent
  `System.CABundle` gRPC service removed, `global` mode code paths removed
- [ ] Stable across multiple releases
- [ ] No reported regressions in VSOCK connectivity
