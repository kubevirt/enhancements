# VEP #457: Mutable Virtual Machine Display Name

## VEP Status Metadata

- Author: Shirly Radco
- Enhancement issue: [#457](https://github.com/kubevirt/enhancements/issues/457)

### Target releases

<!--
A PR must update this section during the planning phase of a given release in order to track it.
Please avoid targeting future releases in this section. Only capture the upcoming release.
-->

- This VEP targets alpha for version:
- This VEP targets beta for version:
- This VEP targets GA for version:

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

Users can assign a mutable inventory name that preserves human text from source platforms (spaces, punctuation, Unicode) without changing Kubernetes object identity. The VM stays the same object: `metadata.name`, `metadata.uid`, guest identity, and references from other resources do not change.

## Motivation

A Virtual Machine's operational workload often outlives the business context encoded in `metadata.name`. Administrators need a recognizable inventory name while automation keeps using the Kubernetes object name.

Administrators may recognize a VM by a descriptive inventory name that differs from its provisioning identifier. Imported names can also contain spaces or non-ASCII characters. After KubeVirt removed object rename ([kubevirt/kubevirt#5564](https://github.com/kubevirt/kubevirt/pull/5564)), there is no supported way to change that presented name without replacing the object. That history is under [Prior art](#prior-art-object-rename); it motivates preserving identity, not a particular storage mechanism.

## Goals

- Users can set, change, and clear a human-readable VM inventory name, including spaces, punctuation, and Unicode, on running and stopped VMs.
- An imported VM can keep its source inventory name even when `metadata.name` must be a Kubernetes DNS-1123 string.
- Existing VMs stay unchanged when the feature is installed or enabled: no automatic assignment of inventory names.
- Users can look up VirtualMachine objects by the inventory name they stored.

## Restrictions

- Duplicate inventory names are allowed, including within one namespace.
- Operational identity remains `metadata.name` and `metadata.uid`.

## Non Goals

- **Renaming Kubernetes API objects**: This VEP does not alter `metadata.name`, guest hostnames, DNS records, or disk volumes. Object rename contradicts Kubernetes identity and already caused data loss ([kubevirt/kubevirt#5564](https://github.com/kubevirt/kubevirt/pull/5564)). RFEs that require the object name itself to change stay open.
- **Propagating display names**: The VirtualMachine object is the source of truth. Copying the string to VMIs, Pods, DataVolumes, PVCs, Secrets, ConfigMaps, or Events would duplicate identity and drift. Callers already operate on `metadata.name`.
- **Name reservations or uniqueness enforcement**: A user-edited descriptive string is a poor uniqueness key. Enforcing it needs a naming controller. Duplicates are a restriction of this API, not a later addition (see [Restrictions](#restrictions) and [Validation](#validation)).
- **Policy binding**: A non-unique, user-editable string must not be an authorization key, backup selector, or migration-policy selector.
- **Historical rewriting**: Audit records, logs, and past Events keep the object name. Rewriting history is unnecessary for inventory presentation.
- **Fuzzy server-side search**: Lookup is exact and case-sensitive. Substring, regex, and case-insensitive match belong in a client or inventory index, not new apiserver operators.
- **Console and third-party UX**: This VEP delivers the VirtualMachine API contract. Consoles, importers, and other clients consume that contract in their own trackers.

## Definition of Users

- VM administrators who rename and locate VMs as business purpose changes.
- CLI users and automation authors who need supported discovery by a human-readable name.
- Migration administrators preserving recognizable source-platform names.
- Console and integration developers presenting VM identity consistently.
- Cluster administrators responsible for feature enablement and compatibility.

## User Stories

- As a VM administrator, I can assign display name `SAP Database Server` to a VM provisioned as `finance-vm-001`, so users recognize the workload while automation targets `metadata.name`.
- As a CLI user, I can list VMs by the exact inventory name I stored and obtain the canonical resource name for operational commands.
- As a migration administrator, I can keep a source inventory name (including spaces) on the VM after import, even when `metadata.name` must be a Kubernetes DNS-1123 string.

## Repos

| Repository | Expected scope |
| --- | --- |
| `kubevirt/api` | VM field, API docs, generated types |
| `kubevirt/kubevirt` | CRD schema, CEL/admission, selectable fields, printer column, feature gate, clone/restore handling, tests, release notes |
| `kubevirt/user-guide` | Rename, search, identity, and compatibility documentation |
| `kubevirt/enhancements` | This VEP |

Consoles, importers, and other clients consume the field in separate tracked work. No Kubernetes core or kubectl change is proposed.

## Design

### Prior art (object rename)

KubeVirt previously implemented rename by deleting and recreating the VM under a new resource name. A reported failure involving `dataVolumeTemplates` caused backing storage and persistent data to be lost ([Bug 1954017](https://bugzilla.redhat.com/show_bug.cgi?id=1954017)), and the rename capability was subsequently removed ([kubevirt/kubevirt#5564](https://github.com/kubevirt/kubevirt/pull/5564)). This proposal updates presentation on the existing VM object, preserving its resource name, UID, and ownership relationships.

This history supports preserving identity. It does not establish which storage mechanism (spec field, label, or annotation) is best.

### API

Add an optional string pointer on `VirtualMachineSpec`:

```go
// DisplayName is an optional, mutable, non-unique inventory name.
// When omitted, interfaces may present metadata.name as the effective name.
// It does not change resource identity or runtime configuration.
// +optional
DisplayName *string `json:"displayName,omitempty" optional:"true"`
```

A pointer is required so JSON Merge Patch `{"spec":{"displayName":null}}` clears the field. An unrelated merge patch that omits the key leaves it unchanged. Full-object replacement that omits the field removes it. Server-side apply follows normal field ownership. There is no schema default, admission default, or backfill.

### Validation

- **Presence**: Optional. Omission leaves the field absent (`nil`).
- **Empty string**: Invalid when the field is present. Length is 1 to 253 Unicode code points.
- **Characters**: CRD CEL plus admission reject leading/trailing whitespace, line breaks, and control characters. Printable Unicode letters, numbers, internal spaces, and punctuation are allowed. Matching is exact and case-sensitive.
- **Uniqueness**: Not required, including within one namespace.

Accepting non-unique names is a permanent API decision. Uniqueness cannot be added later without breaking existing clusters.

### Native discovery

Because the name is a spec field, declare `.spec.displayName` in CRD `selectableFields` and add a `DisplayName` printer column. Exact lookup uses `kubectl get vm --field-selector spec.displayName=...`. This is convenience on top of the stored string, not the justification for the field. Keep existing `Age`, `Status`, and `Ready` columns. Schema, selectable-field declaration, and printer column stay installed regardless of the feature gate. This feature is not backported.

- Selection matches the **stored** field, not the computed presentation name. An absent `displayName` does not match `metadata.name`.
- Queries are LIST operations. JSON output is an `items` collection even when exactly one VM matches.
- Exact namespace-scoped queries can return several VMs. For a single-target operation: zero matches → not found (do not fall back to another lookup); one match → bind `metadata.name`, namespace, and UID; more than one → ambiguity, require explicit resource selection.

```yaml
selectableFields:
  - jsonPath: .spec.displayName
additionalPrinterColumns:
  - name: DisplayName
    type: string
    jsonPath: .spec.displayName
```

### Runtime isolation

`displayName` is a sibling of `spec.template`, not part of the VMI template. A display-name-only update may advance `metadata.generation` and may be captured in configuration revisions. It must not change VMI template/runtime configuration, start a restart or migration, or set a false `RestartRequired` condition. A combined update that also changes `spec.template` must still apply the runtime change.

Tests lock this invariant. Do not rely on today's controller comparison remaining unchanged.

### Clone and restore

| Operation | Default | Override |
| --- | --- | --- |
| Clone | Strip inherited `displayName`. The new VM presents its own `metadata.name` until a name is set. | Explicit target `spec.displayName` patch, including copying the source value. |
| Restore to a new VM | Restore captured value or absence. | Explicit target spec patch. |
| In-place restore | Restore captured value or absence. A snapshot taken before this field existed **clears** the current display name. | Explicit target spec patch to keep or replace it. |

### Feature gate and admission

Propose alpha gate `VMDisplayName`. The structural schema stays installed when the gate is off. Disablement must not strip stored values.

| Request | Gate enabled | Gate disabled |
| --- | --- | --- |
| Create without `displayName` | Allow; leave absent | Allow; leave absent |
| Create or change to a nonempty valid name | Allow, including duplicates | Reject |
| Unrelated update retaining the stored name | Allow | Allow |
| Clear with `displayName: null` | Allow | Allow |
| Explicit empty string | Reject | Reject |
| Read or select existing values | Normal RBAC | Normal RBAC |

The same rules apply to controller-originated clone and restore requests.

### Consumer guidance

Client UX is out of scope for this VEP (see [Non Goals](#non-goals)). Guidance for API consumers:

- Present `spec.displayName` when set, otherwise `metadata.name`.
- Show `metadata.name` and namespace (and UID where destructive) next to the display name in confirmations.
- Execute operations against canonical identity, never against display name alone.
- Do not silently take the first match of an ambiguous query.

### Risks and mitigations

- **UX — duplicate names.** Duplicates are allowed, so an exact query can return several VMs. Mitigation: list returns every match; consumers must not silently pick the first result ([Consumer guidance](#consumer-guidance)). Operations use `metadata.name` / UID.
- **UX — presentation vs stored field.** Clients may show `metadata.name` when `displayName` is absent, but list filters match only the stored field. Mitigation: document the mismatch; [API Examples](#api-examples) show that clearing does not make `metadata.name` selectable.
- **Operational — GitOps replace.** A typed client that omits the field on full `PUT` / replace can clear it. Mitigation: document merge patch or server-side apply; do not add admission that restores a deliberately cleared value ([Update/Rollback Compatibility](#updaterollback-compatibility)).
- **Operational — in-place restore of a pre-field snapshot.** Restoring a snapshot taken before the field existed clears the current display name. Mitigation: restore table; allow an explicit target patch to keep or replace it ([Clone and restore](#clone-and-restore)).
- **Operational — false restart.** A display-name-only edit must not restart the VMI or set `RestartRequired`. Mitigation: the field is a sibling of `spec.template`; tests lock name-only vs mixed edits ([Runtime isolation](#runtime-isolation)).
- **Security — name confusion.** The string is not an authorization or policy key ([Non Goals](#non-goals)). Mitigation: destructive UIs show `metadata.name`, namespace, and UID. The field is not part of `spec.template` / guest configuration.

## API Examples

Set a display name on an otherwise valid VM:

```yaml
apiVersion: kubevirt.io/v1
kind: VirtualMachine
metadata:
  name: finance-vm-001
  namespace: production
spec:
  displayName: "SAP Database Server"
  # Existing runStrategy, template, disks remain here.
```

```bash
kubectl patch vm finance-vm-001 -n production --type=merge \
  -p '{"spec":{"displayName":"SAP Database Server"}}'

kubectl get vm -n production \
  --field-selector 'spec.displayName=SAP Database Server'

kubectl get vm finance-vm-001 -n production

kubectl patch vm finance-vm-001 -n production --type=merge \
  -p '{"spec":{"displayName":null}}'
```

Clearing removes the field. Presentation may fall back to `finance-vm-001`, but `--field-selector spec.displayName=finance-vm-001` does not match that absent field.

## Alternatives

A display name is identifying metadata, not VM runtime state. The Kubernetes-shaped default for queryable identity is a **label**; the default for arbitrary text is an **annotation**. Flexible text and native discovery are separate requirements. Evidence below supports a flexible text contract over label restrictions. Choosing `spec` rather than an annotation is a separate decision (shared API plus selectable fields), weighed against API evolution and controller risk.

### Label

A well-known label such as `kubevirt.io/display-name` is the preferred alternative **if** inventory names can be constrained to Kubernetes label values.

**What a label gets right**

- Native `kubectl get vm -l kubevirt.io/display-name=sap-db` (`-l` is the selector CLI people already use; `--field-selector` has no short flag).
- No new `VirtualMachineSpec` field, merge-patch-null pointer, or `selectableFields` on the VM CRD.
- Label changes do not affect `spec.template`, so they do not induce `RestartRequired` by themselves.
- Clone already copies or filters labels; no new spec-stripping rule is required.

**Why a label is insufficient for some documented names**

A Kubernetes label provides familiar native selection and accommodates compact naming conventions. However, its 63-character limit and restricted character set (`[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?`) exclude some documented inventory naming practices. Public integration reports include descriptive names such as `VM01 (Dev/Ops)` ([OpenText Data Protector idea](https://community.opentext.com/portfolio/data-protector/i/ideas/unsupported-characters-in-vmware-vm-names), declined for insufficient community support) and VM names containing Chinese characters ([Dell Avamar KB 000191789](https://www.dell.com/support/kbdoc/en-us/000191789/avamar-after-vmware-image-backup-is-replicated-the-target-avamar-server-will-not-show-the-correct-vm-name-if-the-name-contains-chinese-character)). These examples demonstrate a compatibility gap. They do not establish how common these requirements are among KubeVirt users.

Truncating or slugifying such names (`vm01-dev-ops`) is a different string than the source inventory name. A label would satisfy a narrower mutable-alias capability (hyphenated ASCII, ≤63 characters). This proposal chooses a flexible display-name contract to support descriptive names and preserve supported source names during import. Native exact discovery, if required, is provided by declaring the stored field selectable; that is not an argument that a label cannot select label-safe values.

A label is also shared selector metadata. The same key/value space is used by Services, NetworkPolicies, templates, and GitOps. A “display name” label can be overwritten or accidentally reused as a targeting selector.

**Caveat:** If SIG-compute decides that label-safe names are acceptable, stop this spec-field design and document a well-known label instead.

### Annotation

An annotation can store spaces and Unicode (large string, few charset limits). Consoles could read `kubevirt.io/display-name` today.

Kubernetes has **no** native annotation selector. `kubectl get vm` cannot filter on an annotation without fetching the list and filtering client-side (or adding a kubectl feature this VEP does not propose). There is also no single validated contract: each product can pick a different key. An annotation is the right alternative only if native CLI discovery is dropped and the name is console-only.

### Other options

| Alternative | Trade-off |
| --- | --- |
| Unique display names | Addresses lookup, but needs a naming controller for a descriptive attribute. Rejected; duplicates remain an end-state API freeze. |
| Separate VMInfo CR | Selectable data, but another API, controller, RBAC, and stale mapping. |
| `virtctl`-only alias lookup | Improves one CLI; does not store human text on the VM API or help `kubectl get vm`. |

Object rename is out of scope ([Non Goals](#non-goals)); the history is under [Prior art](#prior-art-object-rename).

`spec.displayName` keeps a flexible text contract on the VM object. Placement in `spec` (instead of an annotation) is a separate trade-off: one validated API, admission, clone/restore behavior, and CRD `selectableFields` for exact `kubectl` discovery, weighed against API evolution and controller risk. Selectable fields are a consequence of that placement, not the reason to reject labels for label-safe names.

## Does it belong to core KubeVirt?

Yes. A shared, validated naming field on the VirtualMachine API belongs in `kubevirt/kubevirt` (generated types in `kubevirt/api`) if the community accepts a first-class contract rather than a metadata convention.

- HCO, CDI/HPP, an external controller, or a [VEP-190](https://github.com/kubevirt/enhancements/blob/main/veps/sig-compute/190-kubevirt-structured-plugins/vep.md) plugin cannot add a validated VM spec field or CRD selectable fields.
- Admission, clone/restore handling, and runtime-isolation tests belong in the existing VM lifecycle.
- A well-known label remains a viable alternative if SIG-compute rejects unrestricted inventory text (see [Alternatives](#label)).

## Scalability

No new resource types, informers, or periodic writes. virt-controller does not reconcile on a timer to copy or default `displayName`.

| Interaction | Call | Origin | Expected frequency |
| --- | --- | --- | --- |
| Set, change, or clear the name | `UPDATE` / merge-patch / SSA of `VirtualMachine` | User, console, or importer via virt-api | Human or import events (create/rename), not per-reconcile |
| Read a VM | Existing `GET VirtualMachine` | Same callers | Unchanged |
| Look up by stored name | `LIST` / `WATCH` of `VirtualMachine` with field selector `spec.displayName` | kubectl, consoles, automation | Interactive inventory queries and occasional automation; not virt-handler or per-VM hot path |

The field selector is apiserver watch-cache filtering of the existing VirtualMachine list/watch. It does not add an etcd secondary index. Cost is comparable to label selection. Enablement does not backfill, uniqueness-scan, or write related resources.

Measure namespace-scoped and cluster-scoped `LIST`/`WATCH` (with and without the field selector) at representative fleet sizes, including 10,000+ VMs where supported. Consumers must not poll the full inventory on every keystroke; use a scoped list or an existing inventory index.

## Update/Rollback Compatibility

VMs that predate this field keep running with `displayName` absent. They do not need live-migration or restart to remain valid. Resource-name commands continue to work. Additional printer columns may affect table-parsing scripts; prefer structured output.

During a rolling upgrade, version skew is the same as any new optional VM spec field: an older virt-controller ignores the field; a newer API server accepts it. The VMI/domain is unchanged because the field is not on `spec.template`.

**Feature-gate disablement:** Distinct from a cluster-version rollback. Schema, types, conversion, and selector declarations remain. Stored values remain readable. Unrelated updates that retain the value succeed. Clearing is allowed. Disablement does not delete or strip `displayName`. Re-enablement needs no backfill. At Beta the gate defaults to on, so disablement is the supported way to turn the feature off if it misbehaves.

**Older clients and GitOps:** A typed client that omits an unrecognized field on full `PUT` / replace can clear it. Unrelated merge-patch omission leaves it alone. Prefer merge patch or server-side apply. Do not add admission that restores deliberately cleared values.

**Cluster downgrade:** KubeVirt does not support rolling the control plane back to an older version. If such a rollback were supported, an older schema that lacks the field could prune it on write; running guests would be unaffected because the name is not in the domain.

## Functional Testing Approach

Lead with the happy path: set a display name on a running VM, list VMs by that **stored** name, bind `metadata.name` from the result, then clear the field; the VM stays running and `RestartRequired` is not set.

**Unit / admission** (required for new validation and gate logic)

- Absent field, empty-string rejection, 1/253/254 code-point bounds, whitespace and control characters.
- Gate matrix: omitted create, valid create/update, retain, clear, empty string; gate on and off.
- Disablement does not strip stored values.

**Controller / integration**

- Name-only set/change/clear on running and stopped VMs: generation may change; `RestartRequired` is not set; VMI is not restarted.
- Combined display-name plus template change still applies the runtime change.
- Clone strips inherited name unless overridden; restore keeps captured value or absence; legacy snapshot in-place restore clears the field.

**Functional / API**

- Field selector is exact and case-sensitive against the stored field.
- Absent field does not match `metadata.name`.
- Zero, one, and multiple matches in one namespace, including duplicate display names.

Tests assert API and user-visible behavior, not a particular controller helper. Coverage must stay non-flaky; do not rely on e2e to replace unit tests of admission and validation.

## Implementation History

- 2026-09-09: Initial VEP draft. Tracker: [#457](https://github.com/kubevirt/enhancements/issues/457).

## Graduation Requirements

### Alpha

- [ ] Optional/non-unique API, empty-string rejection, CEL/admission, and `VMDisplayName` gate implemented.
- [ ] Schema, selectable field, and printer column published; gate-off schema retained and values not stripped.
- [ ] Name-only and mixed-edit tests prove no false `RestartRequired` / VMI restart; runtime changes in mixed edits still apply.
- [ ] Clone/restore defaults and overrides, including legacy in-place restore clearing the field.
- [ ] Field-selector tests: stored-field match, no fallback to `metadata.name`, zero/one/many matches.
- [ ] User-guide covers presentation fallback and GitOps ownership.

### Beta

Functional tests required. Close user-facing gaps found in Alpha.

- [ ] Duplicate names: `LIST` with `spec.displayName` returns every match; zero, one, and many matches are covered by tests and the user-guide.
- [ ] Clone, restore (including legacy in-place clear), and gate disablement/re-enablement tests remain green.
- [ ] RBAC: create/update/clear/list of `displayName` uses existing VirtualMachine permissions; no extra verbs.
- [ ] Client-skew: merge-patch omission leaves the field; full replace that omits it clears it (documented and tested).
- [ ] Scale: namespace-scoped and cluster-scoped `LIST`/`WATCH` with and without the field selector at the sizes in [Scalability](#scalability).
- [ ] No new Prometheus metrics or alerts. This is inventory metadata, not workload health. Remaining user-facing gaps are docs (discovery, GitOps replace), not monitoring.

#### On-By-Default Readiness

Beta feature gates default to on.

- [ ] Existing VMs unchanged on enablement; no naming-driven mass writes or restarts.
- [ ] Gate disablement preserves stored values and unrelated updates (supported opt-out if the feature misbehaves).
- [ ] User-guide states that ambiguous matches must not be silently bound to the first result.
- [ ] Old typed-client replace semantics are documented.

### GA

- [ ] Feature gate removed; the field is part of the core VM API.
- [ ] No open API, lifecycle, security, or testing issue from Alpha/Beta.
- [ ] Conformance covers the field and native `--field-selector` on the release's supported Kubernetes matrix.
