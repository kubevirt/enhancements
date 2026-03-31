# VEP #222: VSOCK network namespace confinement

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version:
- This VEP targets beta for version: v1.9.0
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
improving security by preventing cross-VM VSOCK access.

Currently, KubeVirt's VSOCK implementation operates in a global CID space
where all VMs' VSOCK devices are reachable from any network namespace on the
host.

## Motivation

The current VSOCK implementation in KubeVirt has several limitations:

1. **No isolation between `VirtualMachines`**: Any process on the host or in any
   Pod can potentially connect to any VM's VSOCK device using its CID. There is
   no network-level boundary between VMs.

2. **Global CID space**: CIDs must be globally unique across all VMs on a node.
   The `virt-controller` maintains a CID allocator to prevent collisions, adding
   complexity.

3. **TLS-based identity verification**: Because VSOCKs have no network namespace
   boundary, KubeVirt runs a gRPC service (`System.CABundle`) permanently on
   each node to provide CA certificates to guests for mutual TLS
   authentication.

With kernel-level VSOCK namespace support, the network namespace itself becomes
the trust boundary, making the global CID allocator unnecessary and providing
stronger isolation by default. The TLS identity verification remains valuable
as an additional layer - proving the peer is `virt-handler` - but no longer
needs to run permanently on the host.

## Goals

- Improve VSOCK security by isolating each VM's VSOCK device within its Pod's
  network namespace, so that VMs cannot access each other's VSOCK
- The change should be transparent to users of the KubeVirt VSOCK API - existing
  workflows continue to work without modification
- Require kernel support for VSOCK network namespace confinement - VMs with
  VSOCK enabled must not start on nodes that lack this support
- Provide operator visibility into the active VSOCK isolation mode

## Non Goals

- Supporting cross-network-namespace VSOCK communication between VMs
- Adding new API fields or feature gates - the existing `VSOCK` feature gate
  is reused

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
  Pod network namespace, without needing TLS-based authentication since the
  network namespace boundary guarantees isolation.

- As a **cluster administrator**, I want to verify which VSOCK isolation mode is
  active on my nodes by inspecting `virt-handler`'s logs.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)

## Design

### Kernel Feature

Linux 7.0 adds VSOCK network namespace support via the sysctl
`/proc/sys/net/vsock/child_ns_mode`:

- `global` (default): Legacy behavior - single CID space, all VSOCK sockets
  visible across all network namespaces on the host
- `local`: Child network namespaces get isolated VSOCK - sockets can only
  communicate within the same network namespace

The mode is set on a parent network namespace and inherited by child
network namespaces at creation time. Once inherited, the mode is immutable for
that network namespace. The `child_ns_mode` sysctl itself is write-once - once
set to `local`, it cannot be changed back to `global` without rebooting the
node.

### Change 1: Enforce `local` mode at `virt-handler` startup

When the VSOCK feature gate is enabled, `virt-handler` writes `local` to
`/proc/sys/net/vsock/child_ns_mode` at startup. If the sysctl is already set
to `local` (e.g. by systemd or a previous run), this is accepted. If the
sysctl does not exist or the write fails, `virt-handler` logs the error and
does not register the `devices.kubevirt.io/vhost-vsock` device plugin. Since
`virt-launcher` Pods for VMs with `AutoattachVSOCK: true` request this device
resource, the scheduler will not place them on nodes that lack support.

### Change 2: Simplify CID allocation

With network namespace isolation, CIDs no longer need to be globally unique -
each network namespace has its own CID space. The dynamic CID allocator in
`virt-controller` is replaced with a fixed CID (e.g. `3`, the minimum valid
guest CID). This simplifies the control plane by removing the allocator, its
in-memory state, and the cluster-wide synchronization logic.

The `VSOCKCID` field in `VirtualMachineInstanceStatus` is kept but will no
longer be set for VMs running in `local` mode, since the CID is always the
same.

### Change 3: Namespace-aware VSOCK dialing

With `local` mode, `virt-handler` can no longer dial VSOCK from the host network
namespace. Instead, it enters the Pod's network namespace before dialing:

1. Use the existing `podIsolationDetector` to discover the `virt-launcher` Pod's
   PID
2. Enter the Pod's network namespace using the existing `netns.New(pid).Do()`
   pattern
3. Read the CID from `vmi.Status.VSOCKCID` if set (upgrade case), otherwise
   use the fixed CID
4. Call `vsock.Dial(cid, port)` inside the network namespace
5. Return the connection - the socket FD remains valid after leaving the network
   namespace

This change applies to all VSOCK connections regardless of TLS. For plain
connections (without `?tls=true`), the flow ends here - the raw VSOCK
connection is returned. For TLS-authenticated connections, Change 4 adds
additional steps before the dial.

### Change 4: On-demand VSOCK CA service

The `System.CABundle` gRPC service currently runs permanently on CID 2 in the
host network namespace to deliver KubeVirt CA certificates to guest agents for
mutual TLS verification. With namespace confinement, this service can no longer
run in the host namespace - guests in `local` mode cannot cross the namespace
boundary to reach it. Instead, `virt-handler` starts the service on-demand
inside the Pod's network namespace when a TLS-authenticated VSOCK connection is
requested.

When a VSOCK connection request with `?tls=true` arrives from `virt-api`:

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

This preserves the mTLS verification guarantee: the guest can verify that the
server on CID 2 holds a certificate signed by the KubeVirt CA, proving that it
is `virt-handler` (a process with `NET_ADMIN` capabilities) rather than a
malicious process. This is a stronger guarantee than what other communication
paths like console or VNC offer, and is relevant for confidential computing
scenarios where pod-level access should not imply VM-level access.

## API Examples

No API changes are required. Existing specifications with
`AutoattachVSOCK: true` continue to work as before.

## Alternatives

### Remove the TLS infrastructure entirely

With namespace confinement, the network namespace itself becomes the trust
boundary. The `System.CABundle` gRPC service, the TLS handshake, and the
`?tls=true` query parameter could all be removed, since the namespace boundary
already guarantees that only processes within the same Pod can reach the VM's
VSOCK device.

**Advantages**:

- Simplifies the codebase by removing the CA service, TLS negotiation, and
  certificate management
- No on-demand server lifecycle to manage
- Clean break while VSOCK is still Alpha

**Disadvantages**:

- Loses the mTLS verification that proves the server on CID 2 is
  `virt-handler` (a process with `NET_ADMIN` capabilities), not a malicious
  process within the Pod
- Weaker than the current security model for confidential computing scenarios,
  where pod-level access should not grant VM-level access
- Other communication paths (console, VNC) already lack this verification, but
  that is an argument for raising the bar rather than lowering it

This was rejected in favor of making the CA service on-demand, which preserves
the stronger authentication guarantee without the operational cost of
permanently running servers.

### Delegate `child_ns_mode` to the system (e.g. systemd)

Instead of `virt-handler` writing `child_ns_mode = local` itself, `virt-handler`
could detect the current mode and adapt to it, leaving the sysctl configuration
to the system (e.g. via a `systemd-sysctl` drop-in or node tuning operator).
`virt-handler` would read `child_ns_mode` at startup, log the result, and always
enter the Pod network namespace before dialing.

**Advantages**:

- No write-once sysctl concern - `virt-handler` never modifies system state,
  eliminating the rollback problem entirely (no node reboot required after
  rollback)
- Broader compatibility - the system administrator controls when and whether to
  enable `local` mode, independent of KubeVirt version
- Fits better with node configuration tools (`systemd-sysctl`, `MachineConfig`,
  node tuning operator) that are the conventional place for kernel tuning

**Disadvantages**:

- **Not secure by default** - VSOCK isolation depends on external configuration.
  A cluster with the VSOCK feature gate enabled but without the sysctl
  configured has no network namespace isolation, which may not be obvious to
  operators
- Requires coordination between KubeVirt deployment and node configuration,
  adding operational complexity
- Different nodes may have different configurations, leading to inconsistent
  security posture across the cluster

This was rejected in favor of `virt-handler` enforcing `local` mode to ensure
secure-by-default behavior: enabling the VSOCK feature gate guarantees isolation
without requiring additional node configuration.

### Drop `VSOCKCID` from the API

The `VSOCKCID` field in `VirtualMachineInstanceStatus` could be removed entirely
since the CID is now fixed. This was rejected because removing a field from the
API is a breaking change that should be avoided. Keeping the field (unused for
new VMs) is harmless and preserves API compatibility.

### Keep `global` mode support (graceful fallback)

Instead of requiring `local` mode, `virt-handler` could fall back to `global`
mode on kernels that do not support `child_ns_mode`. The dynamic CID allocator
would be kept for `global` mode compatibility, and the dial path would enter
the Pod network namespace unconditionally (harmless in `global` mode, required
in `local` mode).

This was rejected because it preserves the weaker security model as a valid
operating mode. By requiring `local` mode, VSOCK is always isolated - there is
no configuration where VMs can access each other's VSOCK. It also allows
simplifying the control plane by removing the CID allocator.

### New feature gate for namespace confinement

A separate `VSOCKNetNS` feature gate could independently control namespace
confinement. This was rejected in favor of extending the existing `VSOCK` gate -
namespace confinement is an improvement to the VSOCK feature, not a separate
feature.

## Scalability

No scalability concerns. The sysctl is set once per node at `virt-handler`
startup. The network-namespace-aware dial adds one `setns` syscall per VSOCK
connection, which is negligible.

## Update/Rollback Compatibility

### Upgrade

After upgrade, `virt-handler` sets `child_ns_mode = local` at startup.
`virt-launcher` Pods are recreated according to `spec.workloadUpdateStrategy`
in the KubeVirt CR - typically all are updated, but under specific
configurations old `virt-launcher` Pods may remain temporarily. Old Pods retain
their inherited `global` mode and their dynamically-assigned CIDs. The
namespace-aware dial (Change 3) handles both modes, since entering a
global-mode network namespace before dialing is harmless. Once all
`virt-launcher` Pods have been recreated, all VMs run in `local` mode with the
fixed CID.

- **Kernel requirement**: Nodes whose kernels do not support `child_ns_mode`
  will no longer be able to run VMs with VSOCK enabled. This should be
  communicated in release notes.
- **gRPC CA service**: The `System.CABundle` gRPC service no longer runs
  permanently in the host network namespace. It is started on-demand inside
  the Pod's network namespace during TLS-authenticated connection requests.
  Guest agents that previously fetched CA certificates at any time must now
  receive them as part of the connection handshake flow.
- **TLS query parameter**: The `?tls=true` query parameter continues to be
  supported. When set, `virt-handler` starts the on-demand CA service in the
  Pod's network namespace before proceeding with the connection.

### Rollback

The `child_ns_mode` sysctl is **write-once** - once set to `local`, it cannot be
changed back to `global` without a node reboot. After rollback, `child_ns_mode`
remains `local`. The old `virt-handler` dials VSOCK from the host network
namespace, which cannot reach into local-mode network namespaces. Since
`virt-launcher` Pods are typically recreated during upgrade (and therefore
inherited `local` mode), most or all VMs will have broken VSOCK after rollback.
**Affected nodes must be rebooted** to reset the sysctl to its default
(`global`).

## Functional Testing Approach

- **Unit tests**: Mock `netns.Do()` and `vsock.Dial()` to verify namespace-aware
  dialing logic; test sysctl detection and write with mock filesystem
- **Functional tests**: VM with VSOCK enabled - verify VSOCK connectivity
  through the API works end-to-end
- **Isolation test**: Two VMs on the same node - verify they cannot reach each
  other's VSOCK device when `local` mode is active
- **Rejection test**: Verify that VMs with VSOCK enabled are rejected on nodes
  without `child_ns_mode` support (if possible)
- **Existing tests**: Run `tests/vmi_vsock_test.go` to ensure no regressions

## Implementation History

<!--
To be filled as implementation progresses.
-->

## Graduation Requirements

### Beta (v1.9.0)

The VSOCK feature is currently Alpha. This VEP graduates it to Beta with
namespace confinement:

- [ ] `virt-handler` enforces `child_ns_mode = local` and rejects VSOCK VMs
  on nodes without support
- [ ] `virt-handler` logs the active VSOCK namespace mode at startup
- [ ] Dynamic CID allocator replaced with fixed CID
- [ ] VSOCK REST proxy enters Pod network namespace before dialing
- [ ] gRPC CA service moved to on-demand per-Pod-namespace lifecycle
- [ ] Unit and functional tests
- [ ] Documentation updated with namespace confinement behavior and kernel
  requirements

### GA

- [ ] Stable across multiple releases
- [ ] No reported regressions in VSOCK connectivity
- [ ] Feature gate enabled by default
