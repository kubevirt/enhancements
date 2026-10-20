# Kubernetes Controller and Webhook Shared Informer Architecture

A provider controller implementing this VEP's mutating admission webhook faces two independent
architecture questions: (1) does the webhook share a process (and an informer cache) with a
reconciler, or run standalone; and (2) if the webhook needs to read a CR to make its decision
(this VEP's own reference provider reads `DeviceProvider.spec.bindings`), does it use a cache —
shared or its own — or call the API server live each time? Both questions are covered below,
each with real precedent and sourced trade-offs, ending in a
[Recommendation](#recommendation) for this VEP's providers specifically.

Using **controller-runtime** (the underlying library for Kubebuilder and Operator SDK), the
central `Manager` manages a single cache backed by informers, shared by every controller and
webhook registered to it. A webhook server is registered to the manager as a `Runnable`,
exactly like a controller, so it can run alongside reconcilers in the same process using the
same shared cache and client. One difference: controllers run under leader election (only one
replica active at a time), while the webhook server does not, since it must keep serving live
admission requests regardless of which replica holds leadership.

## Architecture Overview

```text
                     +-----------------------------------+
                     |           K8s API Server          |
                     +-----------------+-----------------+
                                       |
                     +-----------------+-----------------+
                     |          Shared Manager           |
                     |  +-----------------------------+  |
                     |  | Shared Cache / Informer Store|  |
                     |  +--------------+--------------+  |
                     |                 |                 |
                     |        +--------+--------+        |
                     |        |                 |        |
                     |  +-----v------+   +------v-----+  |
                     |  | Controller |   | Mutating   |  |
                     |  | Reconciler |   | Webhook    |  |
                     |  +------------+   +------------+  |
                     +-----------------------------------+
```

- **Shared state:** both the `Reconciler` and the `Webhook Handler` receive a standard
  `client.Client`. Read operations issued through this client hit the manager's internal
  `cache.Cache` rather than bombarding the API server.
- **Concurrent execution:** calling `mgr.Start(ctx)` launches the cache sync, the webhook
  server, and the controller worker pools as separate goroutines within the same process.

## Benefits of Reading From a Cache

Kubernetes' own guidance for designing low-latency admission webhooks includes, directly: "reduce
the number of API calls made in the mutating webhook server logic"
([Admission Webhook Good Practices][admission-good-practices]). A cache-backed read (via
controller-runtime's shared `Manager` cache, or a webhook's own standalone informer — see
[below](#reading-state-from-a-standalone-webhook)) is exactly how that's achieved: it turns a
network round-trip into a local memory read, which matters directly against the same page's
guidance that a webhook should evaluate "typically in milliseconds." It also reduces watch/GET
load on the API server compared to every admission request issuing its own live call.
controller-runtime keeps an escape hatch for the cases where this isn't good enough:
`mgr.GetAPIReader()` returns a client that always reads whatever was last committed to the API
server, bypassing the cache's own additional lag, for the specific decision that needs it.

**Cache lag, without pretending a live call avoids the same problem:** an informer cache can lag
the latest value committed to the API server — worse under apiserver load (see
[kubernetes/kubernetes#130767](https://github.com/kubernetes/kubernetes/issues/130767), partially
mitigated in Kubernetes v1.36 via resourceVersion-aware skip logic). That lag is real, but it
isn't a reason to treat a live API call as somehow immune to staleness: a direct read only
returns whatever was last committed to etcd, which is itself already a snapshot that can be
behind whatever process — an admin, another controller, an external system — is in the middle of
changing that object. The system is asynchronous throughout; nothing in it, cached or not, hands
back an atomic, up-to-the-instant view of ground truth, and no design here should assume
otherwise. What a cache adds on top of that baseline is one further, boundable lag — the delay
between "committed to the API server" and "delivered to this particular watch" — not a
qualitative difference between "stale" and "fresh." No official Kubernetes source was found
addressing this specific trade-off for admission webhooks — treat "an admission decision might
read a `DeviceProvider` slightly behind the latest committed write" as a real, reasoned
possibility, not a confirmed incident. For this VEP's own use case, reading `bindings` a little
behind either baseline at worst means a device briefly resolves against the previous
configuration, not against no configuration — the safer failure direction regardless of which
read path produced it.

## Reading State From a Standalone Webhook

A webhook with no co-located reconciler still needs to read a CR at request time, and has two
options: call the API live each time, or run its own informer/cache without sharing it with any
reconciler.

The strongest real precedent found is for the caching option: Kubernetes' own in-tree
`ResourceQuota` admission plugin takes a `SharedInformerFactory` via
`SetExternalKubeInformerFactory`
([`k8s.io/kubernetes/plugin/pkg/admission/resourcequota`](https://pkg.go.dev/k8s.io/kubernetes/plugin/pkg/admission/resourcequota))
specifically so its synchronous, per-request admission check reads from an informer-backed
lister instead of calling the API server live on every request — core Kubernetes choosing exactly
this pattern for a latency-sensitive synchronous decision, with no reconciler alongside it.

No clearly documented, named example of a webhook doing plain live API calls per request (no
cache at all) was found in official docs, project architecture pages, or source-level guidance.
That's worth stating plainly as a negative result — it isn't proof the pattern doesn't exist
anywhere, only that it didn't surface as a documented choice, while the caching approach did
(reinforced by the same "reduce API calls" guidance above). Reading a CR straight from the API
server on every request remains simpler to reason about — it avoids a cache's own added
watch-delivery lag on top of the API server's baseline, not staleness itself, since a live read
is still only ever a snapshot of whatever was last committed (see
[Benefits of Reading From a Cache](#benefits-of-reading-from-a-cache) above) — and is a reasonable
choice if request volume is low enough that the added latency and API load don't matter. It works
against the same low-latency guidance a cache-backed read satisfies.

## Precedent in Existing Projects

- **Kubebuilder / Operator SDK scaffolds:** the default project scaffold registers a
  reconciler's `SetupWithManager` and a webhook's `SetupWebhookWithManager` on the same
  `Manager` in the same `main.go`, and the tutorial runs both in one process without
  qualification. This is the toolkit's default, one-process pattern.
  ([Kubebuilder Book — Webhooks](https://book.kubebuilder.io/cronjob-tutorial/webhook-implementation.html),
  [controller-runtime `manager` package][controller-runtime-manager])
- **Gatekeeper (OPA)** registers both its webhook and its controllers on the identical `Manager`
  in `main.go` — `webhook.AddToManager(mgr, ...)` and `controller.AddToManager(mgr, ...)`, one
  `mgr.Start(ctx)`. It goes further than sharing a cache incidentally: its audit component can be
  configured with `--audit-from-cache=true` to read synced resources from the same internal
  cache instead of querying the API server directly — a deliberate choice to reuse the shared
  cache for a second, independent consumer.
  ([`main.go`](https://github.com/open-policy-agent/gatekeeper/blob/master/main.go),
  [`pkg/audit/manager.go`](https://github.com/open-policy-agent/gatekeeper/blob/master/pkg/audit/manager.go))
- **Crossplane** configures its `Manager` with an inline `WebhookServer` alongside its
  controllers' `Setup` calls, all in one process started by a single `mgr.Start(...)`.
  ([`cmd/crossplane/core/core.go`](https://github.com/crossplane/crossplane/blob/main/cmd/crossplane/core/core.go))
- **Kyverno** combines its webhook server with a webhook-configuration controller and a
  certificate renewer in one Admission Controller process, but runs heavier background policy
  processing (generate/mutate-existing rules) as a separate Background Controller process — a
  partial combination, not a full one.
  ([Kyverno: How Kyverno Works](https://kyverno.io/docs/introduction/how-kyverno-works/))
- **Istiod** merges Istio's sidecar-injection webhook and its control-plane components (formerly
  separate Pilot/Galley/Citadel services) into one binary, one `Deployment`
  ([Introducing istiod](https://istio.io/latest/blog/2020/istiod/)) — noted with lower
  confidence than the examples above, since this wasn't verified to the same source-code depth.
  It's also architecturally distinct from the leader-election split described earlier: istiod
  runs every replica active with no leader election at all, avoiding the mismatch by a different
  route than controller-runtime's "leader-elected reconciler, always-on webhook" model.

## Trade-offs of Combining

Combining a webhook and a reconciler in one process is well-supported, but not free. Four
concrete costs are worth weighing before choosing it for a provider controller implementing
this VEP's mutating admission webhook:

- **Blast radius.** An unrecovered panic in either the reconciler or the webhook handler
  crashes the whole process, and neither is protected by default. Reconciler panic recovery
  is an opt-in `RecoverPanic` option, added because "a panic might be triggered by a single
  resource in an unexpected state, so that one bad resource could prevent all other resources
  from being processed"
  ([controller-runtime#797](https://github.com/kubernetes-sigs/controller-runtime/issues/797),
  resolved by
  [controller-runtime#1627](https://github.com/kubernetes-sigs/controller-runtime/pull/1627)).
  Webhook handler panic recovery followed the same opt-in pattern two years later
  ([controller-runtime#1900](https://github.com/kubernetes-sigs/controller-runtime/pull/1900)).
  Without explicitly enabling both options, an uncaught panic in a provider's own reconcile
  logic can take the webhook down with it — and since this VEP's webhook gates pod creation,
  that means new VMIs referencing that provider stop being created cluster-wide until the
  process restarts.
- **Resource contention.** Kubernetes' own guidance is blunt: admission webhooks "should
  evaluate as quickly as possible (typically in milliseconds), since they add to API request
  latency" ([Admission Webhook Good Practices][admission-good-practices]), against a default
  10-second timeout (1-30 second configurable range). Whether a CPU-heavy reconcile loop sharing
  the same process can delay the webhook handler past that budget isn't documented as a named
  incident in the sources checked for this note — Go's scheduler preempts long-running
  goroutines, which mitigates but doesn't eliminate the risk under a CPU-limited container. Treat
  this as a plausible, architecture-driven risk, not a confirmed one, and watch it if a
  provider's reconcile logic is CPU-intensive.
- **Scaling and availability mismatch.** controller-runtime's own documentation states the
  split directly: "controllers need to be run in leader election mode, while webhook server
  doesn't" ([`pkg/manager` reference][controller-runtime-manager]) — a `Runnable` can opt out
  of leader election, so one process can serve both needs. The real cost is on the cache side:
  controller-runtime starts the shared informer cache on every replica, not just the leader,
  specifically so failover is fast (a newly-elected leader already has a warm cache). Running
  enough replicas for webhook availability therefore also means running that many full sets of
  watches against the API server, even though only the leader replica ever reconciles with
  them. (Istiod, noted above, sidesteps this specific mismatch by not using leader election at
  all — a different trade, not a free one.)
- **Independent deployability.** One process means one rollout: a fix to reconcile logic can't
  ship without restarting (and briefly interrupting) the webhook server, and vice versa. This
  wasn't found documented as a named architectural decision in the projects checked here, but
  it follows directly from running both in one binary/`Deployment`.

## Recommendation

For this VEP's own minimal resolve-mode reference provider (see `vep.md`'s
[Two Resolution Modes, One Contract](vep.md#two-resolution-modes-one-contract)), there is no
reconciler at all — it only needs to read `DeviceProvider.spec.bindings` synchronously inside the
webhook call. For that shape, run a standalone webhook backed by its own informer/lister for
`DeviceProvider` objects (the `ResourceQuota` pattern above), rather than a live API call per
admission request — it satisfies Kubernetes' own low-latency guidance without requiring a full
controller-runtime `Manager` or reconcile loop at all.

For a generate-mode provider that also runs a reconciler (for example, one implementing
[VEP-300][vep-300]'s claim-generation design), combining the webhook and reconciler under one
shared `Manager`/cache is a reasonable default when that reconciler is lightweight — this is the
Kubebuilder/Operator SDK tooling default, and matches Gatekeeper's and Crossplane's own choice.
Weigh splitting them into separate deployments instead, per [Trade-offs of
Combining](#trade-offs-of-combining) above, once the reconciler's own resource use, blast radius,
or independent-scaling needs start to matter more than the shared cache is worth.

[admission-good-practices]: https://kubernetes.io/docs/concepts/cluster-administration/admission-webhooks-good-practices/
[controller-runtime-manager]: https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/manager
[vep-300]: https://github.com/kubevirt/enhancements/pull/432
