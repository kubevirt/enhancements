# VEP #497: cert-manager Integration and Per-Pod Certificate Identity

## VEP Status Metadata

### Target releases

- This VEP targets alpha for version: v1.10
- This VEP targets beta for version:
- This VEP targets GA for version:

- **Authors**:
  - Roman Mohr (@rmohr)
  - Howard Ke (@rootn9633)
  - Shruti Jain (@shrutijain20)
- **Owning SIG**: sig-control-plane
- **Participating SIGs**: sig-compute, sig-observability

### Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement [issue](https://github.com/kubevirt/enhancements/issues/497) created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Alpha target version is explicitly mentioned and approved
- [ ] (R) Beta target version is explicitly mentioned and approved
- [ ] (R) GA target version is explicitly mentioned and approved

## Overview

This proposal adds native `cert-manager` (`cert-manager.io/v1`) support to KubeVirt as an alternative to KubeVirt's built-in `selfSigned` certificate strategy. Instead of generating shared certificates stored in Kubernetes `Secret`s, each KubeVirt pod generates its own private key locally and requests a pod-specific X.509 certificate via a namespaced `cert-manager.io/v1` `CertificateRequest` resource approved by `virt-operator`.

The implementation is split into two phases:
1. **Phase 1 (Control-Plane & Node PKI + Role/Node Verification)**: Adds `spec.certificateRotateStrategy.certManager` (`issuerRef`, `caBundleConfigMapRef`, `duration`), per-pod `CertificateRequest` management, a namespaced `CertificateRequest` approver in `virt-operator`, and strict IP SAN and node/role `CommonName` verification for `virt-api` and `virt-handler` (including live migration). Metrics endpoint TLS behavior remains unchanged in Phase 1.
2. **Phase 2 (Metrics Endpoint mTLS & Dedicated Metrics PKI)**: Adds `spec.certificateRotateStrategy.certManager.metrics` (`issuerRef`, `caBundleConfigMapRef`, `duration`), issues IP-verified server certificates for `/metrics`, enforces mutual TLS (`tls.RequireAndVerifyClientCert`) on metrics endpoints, and allows isolating metrics scraping trust from internal control-plane trust.

## Motivation

KubeVirt currently supports two ways to manage internal component certificates under `spec.certificateRotateStrategy.selfSigned`:
1. **Built-in self-signed rotation**: `virt-operator` generates a self-signed CA and shared component certificates stored in Kubernetes `Secret`s (`kubevirt-ca`, `kubevirt-virt-handler-server-certs`, `kubevirt-virt-handler-migration-client-certs`, etc.).
2. **Pre-populated Secrets (`ca.duration == 0`)**: An external tool writes certificates directly into KubeVirt's expected `Secret` names.

This Secret-based model has four limitations:

1. **No direct integration with external CAs via `cert-manager` Issuers**: Cluster administrators who standardize on `cert-manager` (`Issuer` or `ClusterIssuer` backed by HashiCorp Vault, Google Cloud CAS, AWS Private CA, Venafi, etc.) must pre-create and maintain static Kubernetes `Secret`s matching KubeVirt's internal naming scheme. Furthermore, `cert-manager` `Certificate` CRs store private keys in Kubernetes `Secret`s in etcd, exposing the private key to any process or user with `Secret` read access in the namespace.
2. **Shared `virt-handler` certificates without node or IP identity**: Because `virt-handler` runs as a `DaemonSet`, mounting a `Secret` gives every `virt-handler` pod across all nodes the exact same certificate (`CN=kubevirt.io:system:node:virt-handler`) and private key, with no Pod IP Subject Alternative Name (SAN) and no node name in the certificate. As a result, KubeVirt sets `InsecureSkipVerify: true` in `pkg/util/tls/tls.go` and only checks that the peer certificate is signed by the KubeVirt CA and carries a generic `CommonName` prefix. If one node is compromised or network routing is spoofed:
   - `virt-api` cannot cryptographically verify that the `virt-handler` answering at a given IP is actually the `virt-handler` for the node where the target VMI runs.
   - During live migration, the source `virt-handler` cannot verify that the target `virt-handler` certificate belongs to the scheduled target node, and the target `virt-handler` cannot verify that the incoming connection originates from the scheduled source node.
3. **Unverified Pod IP certificates and optional client auth on `/metrics`**: Prometheus and monitoring agents scrape KubeVirt pods directly via Pod IP (`https://<pod-ip>:8443/metrics`). Shared `Secret` certificates do not contain individual Pod IP SANs, forcing scrapers to disable server hostname/IP verification (`insecure_skip_verify: true`). In addition, `SetupPromTLS` uses `tls.VerifyClientCertIfGiven`, which does not require scrapers to present a valid client certificate.
4. **No trust-domain separation between metrics scrapers and internal control plane**: Metrics scrapers often run outside KubeVirt's control plane (e.g., cluster-wide monitoring or external tenant scrapers). Using the internal KubeVirt control-plane CA for metrics mTLS requires sharing the control-plane trust domain with external scrapers.

## Goals

- **Use Case 1 (Centralized external PKI via `cert-manager`)**: Allow KubeVirt components (`virt-operator`, `virt-api`, `virt-controller`, `virt-handler`, `virt-exportproxy`) to obtain and rotate certificates from a `cert-manager` `Issuer` or `ClusterIssuer` using `cert-manager.io/v1` `CertificateRequest`s, keeping private keys local to each pod.
- **Use Case 2 (Exact roles and node/IP identities for `virt-handler` and control-plane components)**: Issue per-pod certificates containing the pod's IP in `IPAddresses` SAN and node/role-specific `CommonName`s, validated by `virt-operator` before approval, so `virt-api` and `virt-handler` (including live migration proxies) verify the exact peer node identity, IP address, and role.
- **Use Case 3 (Fully verified Pod IP certificates for metrics scraping with mTLS)**: Issue metrics server certificates with the pod's IP SAN and enforce `tls.RequireAndVerifyClientCert` on `/metrics` against a configured CA bundle (Phase 2).
- **Use Case 4 (Dedicated CA/Issuer for metrics)**: Support an optional `metrics` PKI configuration (`spec.certificateRotateStrategy.certManager.metrics`) so operators can use a different `Issuer` and CA bundle (lower security boundary / shared with external scrapers) for `/metrics` than for internal control-plane mTLS (Phase 2).

## Non Goals

- Replacing or removing the default `selfSigned` certificate rotation strategy.
- Managing `cert-manager` installation, `Issuer`/`ClusterIssuer` lifecycle, or external CA bundle provisioning (`ConfigMap` creation remains the responsibility of the cluster administrator or tools such as `trust-manager`).
- Changing guest-internal certificates or workload-level TLS outside KubeVirt components.
- Modifying `/metrics` TLS behavior in Phase 1.

## Definition of Users

- **Cluster Administrators / Platform Operators**: Configure KubeVirt to integrate with enterprise PKI via `cert-manager` and configure Prometheus / monitoring scrapers with mTLS.
- **Security / Compliance Engineers**: Require pod-local private keys, strict node-bound identities for `virt-handler`, verified IP SANs, and isolated trust domains between control-plane traffic and metrics scraping.

## User Stories

1. As a platform owner, I want all KubeVirt certificates backed by a company-wide chain of trust, including configuring certificate rotation lifetimes.
2. As a cluster administrator, I want KubeVirt to request component certificates from my existing `cert-manager` `Issuer` or `ClusterIssuer` and trust a CA bundle from a `ConfigMap`, without storing component private keys in Kubernetes `Secret`s.
3. As a security engineer, I want each `virt-handler` pod to hold certificates bound to its specific Kubernetes `Node` name, Pod IP, and role (console/lifecycle server, migration server, migration client, vsock client) so a compromised node cannot impersonate another node during `virt-api` calls or live migration.
4. As a platform operator, I want Prometheus to scrape KubeVirt `/metrics` endpoints over mTLS with full server IP SAN verification (`insecure_skip_verify: false`) and mandatory client certificate verification.
5. As a security engineer, I want to configure a separate `cert-manager` `Issuer` and CA bundle for `/metrics` endpoints so external metrics scrapers do not share the CA used for internal KubeVirt control-plane communication.

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt)
- [kubevirt/user-guide](https://github.com/kubevirt/user-guide)

## Design

### Two-Phase Implementation Plan

| Phase | Scope | Metrics Port Behavior |
| :--- | :--- | :--- |
| **Phase 1** | Control-plane and node `cert-manager` integration (`spec.certificateRotateStrategy.certManager`), pod-local private keys and `CertificateRequest` rotation manager, `virt-operator` namespaced `CertificateRequest` approver, and strict node/role/IP verification for `virt-api` and `virt-handler` (including live migration). | **Unchanged**: `/metrics` continues to use `SetupPromTLS` (`tls.VerifyClientCertIfGiven`) with the component's server certificate as it does today. |
| **Phase 2** | Metrics endpoint mTLS (`SetupPromTLSWithCA` with `tls.RequireAndVerifyClientCert`), dedicated metrics server certificates (`LoadCertConfigForMetrics` with Pod IP SAN), and optional separate metrics Issuer/CA configuration (`spec.certificateRotateStrategy.certManager.metrics`). | **Full mTLS**: `/metrics` presents a server certificate with the Pod IP SAN signed by the metrics (or default) Issuer and requires client certificates signed by the metrics (or default) CA bundle. |

---

### 1. API Changes (`kubevirt.io/api/core/v1`)

`KubeVirtCertificateRotateStrategy` is extended with `CertManager`:

```go
type KubeVirtCertificateRotateStrategy struct {
	SelfSigned  *KubeVirtSelfSignedConfiguration `json:"selfSigned,omitempty"`
	CertManager *CertManagerConfiguration        `json:"certManager,omitempty"`
}

// Phase 1 fields + Phase 2 Metrics field
type CertManagerConfiguration struct {
	// IssuerRef is a reference to a cert-manager.io Issuer or ClusterIssuer
	// used for KubeVirt control-plane and node certificates.
	// +required
	IssuerRef *k8sv1.TypedLocalObjectReference `json:"issuerRef"`

	// CaBundleConfigMapRef is a reference to a ConfigMap in the KubeVirt namespace
	// containing the PEM-encoded CA bundle under the "ca-bundle" key.
	// +required
	CaBundleConfigMapRef *k8sv1.TypedLocalObjectReference `json:"caBundleConfigMapRef"`

	// Duration is the requested certificate validity period (defaults to 24h).
	// +optional
	Duration *metav1.Duration `json:"duration,omitempty"`

	// Metrics configures a dedicated cert-manager Issuer and/or CA bundle
	// for the /metrics endpoints (Added in Phase 2).
	// +optional
	Metrics *CertManagerMetricsConfiguration `json:"metrics,omitempty"`
}

// Added in Phase 2
type CertManagerMetricsConfiguration struct {
	// IssuerRef is an optional reference to a cert-manager.io Issuer or ClusterIssuer
	// for metrics server certificates. If omitted, CertManager.IssuerRef is used.
	// +optional
	IssuerRef *k8sv1.TypedLocalObjectReference `json:"issuerRef,omitempty"`

	// CaBundleConfigMapRef is an optional reference to a ConfigMap in the KubeVirt namespace
	// containing the PEM-encoded client CA bundle ("ca-bundle" key) used to verify
	// metrics scraper client certificates. If omitted, CertManager.CaBundleConfigMapRef is used.
	// +optional
	CaBundleConfigMapRef *k8sv1.TypedLocalObjectReference `json:"caBundleConfigMapRef,omitempty"`

	// Duration is the requested validity period for metrics server certificates (defaults to 24h).
	// +optional
	Duration *metav1.Duration `json:"duration,omitempty"`
}
```

#### Webhook Validation Rules
- `selfSigned` and `certManager` are mutually exclusive in `spec.certificateRotateStrategy`. If both are set, the validating webhook rejects the `KubeVirt` CR.
- In `certManager`:
  - `issuerRef` must be non-nil, `apiGroup` must be `"cert-manager.io"`, `kind` must be `"Issuer"` or `"ClusterIssuer"`, and `name` must be non-empty.
  - `caBundleConfigMapRef` must be non-nil, `apiGroup` must be `""` (or omitted), `kind` must be `"ConfigMap"`, and `name` must be non-empty.
- In `certManager.metrics` (Phase 2):
  - If `metrics.issuerRef` is set, it must follow the same validation rules as `issuerRef`.
  - If `metrics.caBundleConfigMapRef` is set, it must follow the same validation rules as `caBundleConfigMapRef`.

---

### 2. Why `CertificateRequest` Instead of `Certificate`

`cert-manager` offers two main resources: `Certificate` and `CertificateRequest`. This design uses `CertificateRequest` directly from each pod for three reasons:

1. **Private keys never leave the pod**: A `cert-manager` `Certificate` resource stores the generated private key in a Kubernetes `Secret` in etcd. With `CertificateRequest`, each pod generates its own ECDSA P-256 private key on pod-local storage, builds an X.509 CSR, and submits only the public CSR in a `CertificateRequest`.
2. **Per-pod IP SANs and per-node identities**: Every pod has a unique `status.podIP` (and each `virt-handler` runs on a specific `Node`). Generating CSRs per pod allows embedding the exact Pod IP in `IPAddresses` SAN and the node name in `CommonName`, which is impossible with a single `Secret` mounted by a `Deployment` or `DaemonSet`.
3. **Namespaced approval**: Unlike `certificates.k8s.io/v1` `CertificateSigningRequest` (which is cluster-scoped), `cert-manager.io/v1` `CertificateRequest` is namespaced. `virt-operator` only needs RBAC permissions to watch and approve `CertificateRequest`s within the KubeVirt installation namespace.

---

### 3. Per-Pod `CertificateRequest` Manager (`pkg/certificates/bootstrap`)

Each KubeVirt component initializes a `CertificateRequestCertificateManager` (implementing `k8s.io/client-go/util/certificate.Manager`) for each certificate role it requires:

1. **Pod IP Injection**: All KubeVirt component pod specs (`virt-operator`, `virt-api`, `virt-controller`, `virt-handler`, `virt-exportproxy`) pass `--pod-ip-address=$(MY_POD_IP)` via the Downward API (`status.podIP`). Component startup fails fast if `--pod-ip-address` is empty.
2. **Key & Certificate Storage**:
   - Stateless control-plane deployments (`virt-operator`, `virt-api`, `virt-controller`, `virt-exportproxy`) store private keys and certificates in `--cert-dir` (default `/tmp` on `emptyDir`).
   - `virt-handler` stores private keys and certificates on a host-local directory (`/var/lib/kubevirt-node-certificates`). If a node reboots or `virt-handler` restarts while `kube-apiserver` or `cert-manager` is temporarily unreachable, `virt-handler` loads its existing valid certificate and key from disk and continues serving local node operations.
3. **CSR & `CertificateRequest` Lifecycle**:
   - On startup (if no valid certificate exists on disk) and when reaching 70%–90% of the current certificate's lifetime (`jitteryDuration` around 80% with exponential backoff on errors):
     1. Generate a fresh ECDSA P-256 private key.
     2. Create an X.509 CSR with `Subject.Organization = ["kubevirt.io:system"]`, the role-specific `CommonName`, `IPAddresses = [podIP]`, and `DNSNames` (for Service-backed servers).
     3. Delete any pre-existing `CertificateRequest` with the same deterministic pod-and-role name, then create a new `CertificateRequest` in the KubeVirt namespace referencing the configured `issuerRef`, `duration`, and `usages`.
     4. Poll until `CertificateRequest` has condition `Ready=True` and `status.certificate` is populated.
     5. Atomically write the private key and signed certificate chain to `<certDir>/<prefix>-current.pem`, update the in-memory `tls.Certificate`, and delete the completed `CertificateRequest` CR so completed requests do not accumulate in etcd.
4. **Dynamic Strategy Switching**:
   - All components register a `ClusterConfig` callback (`shouldUpdateCertificateManager`) that detects transitions between `selfSigned` and `certManager` (or changes to `metrics.issuerRef` in Phase 2) and triggers a clean process exit (`os.Exit(0)`) so kubelet restarts the container with the new certificate manager.

---

### 4. Certificate Roles, Identities, and Peer Verification (Use Case 2)

To prevent impersonation across roles or across nodes, every certificate issued via `CertificateRequest` carries an explicit role and identity in its `CommonName`, `Usages`, and SANs:

| Component | Role / Cert Manager | `CommonName` (`O=kubevirt.io:system`) | `Usages` | `DNSNames` SAN | `IPAddresses` SAN |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `virt-operator` | Webhook Server | `kubevirt.io:system:server:kubevirt-operator-webhook` | `digital signature`, `key encipherment`, `server auth` | `kubevirt-operator-webhook.<ns>.svc` | `<podIP>` |
| `virt-api` | API / Webhook Server | `kubevirt.io:system:server:virt-api` | `digital signature`, `key encipherment`, `server auth` | `virt-api.<ns>.svc` | `<podIP>` |
| `virt-api` | `virt-handler` Client | `kubevirt.io:system:client:<podName>` | `digital signature`, `key encipherment`, `client auth` | None | `<podIP>` |
| `virt-controller` | Server | `kubevirt.io:system:server:virt-controller` | `digital signature`, `key encipherment`, `server auth` | None | `<podIP>` |
| `virt-exportproxy` | Export Proxy Server | `kubevirt.io:system:server:virt-exportproxy` | `digital signature`, `key encipherment`, `server auth` | None | `<podIP>` |
| `virt-handler` | Console & Lifecycle Server | `kubevirt.io:system:node:<nodeName>` | `digital signature`, `key encipherment`, `server auth` | None | `<podIP>` |
| `virt-handler` | Migration Server | `kubevirt.io:system:node:<nodeName>-migration` | `digital signature`, `key encipherment`, `server auth` | None | `<podIP>` |
| `virt-handler` | Migration Client | `kubevirt.io:system:client:<nodeName>-migration` | `digital signature`, `key encipherment`, `client auth` | None | `<podIP>` |
| `virt-handler` | VSOCK Client | `kubevirt.io:system:client:<nodeName>-vsock` | `digital signature`, `key encipherment`, `client auth` | None | `<podIP>` |
| All components *(Phase 2)* | Metrics Server | `kubevirt.io:system:metrics:<component>` | `digital signature`, `key encipherment`, `server auth` | None | `<podIP>` |

#### Strict Peer Verification for `virt-handler` Interactions
With per-pod and per-node certificates in place:
1. **`virt-api` to `virt-handler` (Console, VNC, Pause/Unpause, Freeze, Guest Info, Backup, SEV)**:
   - When `virt-api` dials a `virt-handler` pod for a VMI scheduled on `<nodeName>` at `<podIP>`, the client TLS verifier validates:
     - The certificate chain is signed by the configured KubeVirt CA bundle and has `ExtKeyUsageServerAuth`.
     - The server certificate's `IPAddresses` SAN matches the dialed `<podIP>`.
     - The server certificate's `CommonName` equals `kubevirt.io:system:node:<nodeName>`.
   - `virt-handler`'s console/lifecycle server validates that the client certificate has `ExtKeyUsageClientAuth` and `CommonName` matching `kubevirt.io:system:client:virt-api*`.
2. **`virt-handler` to `virt-handler` (Live Migration)**:
   - When the source `virt-handler` on `<sourceNode>` dials the target `virt-handler` on `<targetNode>` at `<targetPodIP>`:
     - The source `virt-handler` verifies the target server certificate has `ExtKeyUsageServerAuth`, `IPAddresses` SAN matching `<targetPodIP>`, and `CommonName == kubevirt.io:system:node:<targetNode>-migration`.
     - The target `virt-handler` verifies the source client certificate has `ExtKeyUsageClientAuth` and `CommonName == kubevirt.io:system:client:<sourceNode>-migration` (matching the expected `sourceNode` of the active migration on that target node).
   - A `virt-handler` on node `A` can no longer initiate or accept migration streams claiming to be node `B`, nor can a migration client certificate be used to call the `virt-handler` console/lifecycle API.

---

### 5. `virt-operator` `CertificateRequest` Approver (`pkg/virt-operator/certificates/approver`)

When `cert-manager` creates a `CertificateRequest`, the Kubernetes API server populates `spec.username`, `spec.groups`, and `spec.extra` (including `authentication.k8s.io/pod-name`, `authentication.k8s.io/pod-uid`, and `authentication.k8s.io/node-name` from bound ServiceAccount tokens).

`virt-operator` runs a controller watching `CertificateRequest`s in the KubeVirt namespace. Before setting `Approved=True`, `virt-operator` parses the X.509 CSR in `spec.request` and validates:

1. **Organization & Email**: `Subject.Organization` must be `["kubevirt.io:system"]` and `EmailAddresses` must be empty.
2. **Pod Lookup & IP Ownership**:
   - Extracts `podName` from `spec.extra["authentication.k8s.io/pod-name"]`, fetches the `Pod` from the local informer store in the KubeVirt namespace, and verifies the pod's `spec.serviceAccountName` matches `spec.username`.
   - Verifies `len(csr.IPAddresses) == 1` and `csr.IPAddresses[0]` equals `pod.Status.PodIP`.
3. **Role-Specific Identity**:
   - **`system:serviceaccount:<ns>:kubevirt-handler`**:
     - `csr.DNSNames` must be empty.
     - Extracts the node name from `pod.Spec.NodeName` (and cross-checks `spec.extra["authentication.k8s.io/node-name"]` when present).
     - For server auth (`Usages`: `digital signature`, `key encipherment`, `server auth`): `CommonName` must be `kubevirt.io:system:node:<nodeName>`, `kubevirt.io:system:node:<nodeName>-migration`, or (in Phase 2) `kubevirt.io:system:metrics:virt-handler`.
     - For client auth (`Usages`: `digital signature`, `key encipherment`, `client auth`): `CommonName` must be `kubevirt.io:system:client:<nodeName>-migration` or `kubevirt.io:system:client:<nodeName>-vsock`.
   - **`system:serviceaccount:<ns>:kubevirt-apiserver`**:
     - Server cert: `CN == kubevirt.io:system:server:virt-api`, `DNSNames == [virt-api.<ns>.svc]`, `Usages` = server auth.
     - Client cert: `CN == kubevirt.io:system:client:<podName>`, `DNSNames` empty, `Usages` = client auth.
     - Metrics cert (Phase 2): `CN == kubevirt.io:system:metrics:virt-api`, `DNSNames` empty, `Usages` = server auth.
   - **`system:serviceaccount:<ns>:kubevirt-controller`**:
     - Server cert: `CN == kubevirt.io:system:server:virt-controller`, `DNSNames` empty, `Usages` = server auth.
     - Metrics cert (Phase 2): `CN == kubevirt.io:system:metrics:virt-controller`, `DNSNames` empty, `Usages` = server auth.
   - **`system:serviceaccount:<ns>:kubevirt-operator`**:
     - Server cert: `CN == kubevirt.io:system:server:kubevirt-operator-webhook`, `DNSNames == [kubevirt-operator-webhook.<ns>.svc]`, `Usages` = server auth.
     - Metrics cert (Phase 2): `CN == kubevirt.io:system:metrics:kubevirt-operator-webhook`, `DNSNames` empty, `Usages` = server auth.
   - **`system:serviceaccount:<ns>:kubevirt-exportproxy`**:
     - Server cert: `CN == kubevirt.io:system:server:virt-exportproxy`, `DNSNames` empty, `Usages` = server auth.
     - Metrics cert (Phase 2): `CN == kubevirt.io:system:metrics:virt-exportproxy`, `DNSNames` empty, `Usages` = server auth.

If any check fails, `virt-operator` marks the `CertificateRequest` condition `Denied=True` with a descriptive reason.

---

### 6. CA Bundle Management

- `spec.certificateRotateStrategy.certManager.caBundleConfigMapRef` points to a `ConfigMap` in the KubeVirt namespace containing PEM-encoded root/intermediate CA certificates under the key `ca-bundle`.
- `virt-operator` watches this `ConfigMap` and reconciles the `caBundle` field on all KubeVirt `ValidatingWebhookConfiguration`, `MutatingWebhookConfiguration`, and `APIService` resources.
- Each KubeVirt binary watches the configured `ConfigMap` via `kvtls.NewCAManager` to dynamically update its in-memory `x509.CertPool` without restarting pods when the CA bundle rotates.

---

### 7. Phase 2: Metrics Endpoint mTLS and Dedicated Metrics PKI (Use Cases 3 & 4)

In Phase 1, `/metrics` continues to use `kvtls.SetupPromTLS(serverCertManager, clusterConfig)`, which presents the component's default server certificate and sets `ClientAuth: tls.VerifyClientCertIfGiven`.

In Phase 2, `/metrics` is upgraded to support full mTLS and an optional dedicated PKI under `spec.certificateRotateStrategy.certManager.metrics`:

1. **Metrics Server Certificate (`SetupMetricsCertificateManager` / `LoadCertConfigForMetrics`)**:
   - When `certManager` is configured:
     - If `certManager.metrics != nil && certManager.metrics.issuerRef != nil`, the component starts a dedicated `promCertManager` using `metrics.issuerRef` and `metrics.duration`, storing keys/certs in `<certDir>/metrics` (so filenames do not collide with the component's control-plane certificate), and requesting `CN=kubevirt.io:system:metrics:<component>` with `IPAddresses=[podIP]`.
     - If `metrics.issuerRef` is nil, the component reuses its `cert-manager`-issued server certificate (which already includes `IPAddresses=[podIP]`).
   - Because the certificate contains the Pod IP in `IPAddresses` SAN, Prometheus / metrics scrapers verify the server certificate against the scraped Pod IP without `insecure_skip_verify: true`.
2. **Metrics Client Certificate Verification (`SetupPromTLSWithCA`)**:
   - `pkg/util/tls/tls.go` introduces `SetupPromTLSWithCA(caManager ClientCAManager, certManager certificate.Manager, clusterConfig *virtconfig.ClusterConfig) *tls.Config`:
     - When `caManager == nil` (the `selfSigned` strategy default), `ClientAuth` remains `tls.VerifyClientCertIfGiven` for backward compatibility.
     - When `caManager != nil` (when `certManager` is configured), `GetConfigForClient` loads the `x509.CertPool` from `caManager.GetCurrent()`, sets `ClientCAs: clientCAPool`, and enforces `ClientAuth: tls.RequireAndVerifyClientCert`. If the CA pool cannot be loaded, connections to `/metrics` are rejected.
   - If `certManager.metrics != nil && certManager.metrics.caBundleConfigMapRef != nil`, `metricsCAManager` watches the `ConfigMap` referenced by `metrics.caBundleConfigMapRef`. Otherwise, it falls back to the control-plane `caManager` (`certManager.caBundleConfigMapRef`).
3. **Healthz Endpoint Considerations**:
   - Kubelet readiness/liveness probes hitting `/healthz` do not present client certificates. When `SplitMetricsServer` is used or when `/metrics` requires mTLS, `/healthz` either runs on a listener that does not require client certs or uses an HTTP readiness check / split listener so kubelet probes continue to succeed while `/metrics` enforces `RequireAndVerifyClientCert`.

## API Examples

### Phase 1: Control-Plane and Node PKI with `cert-manager`

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  certificateRotateStrategy:
    certManager:
      issuerRef:
        apiGroup: cert-manager.io
        kind: Issuer
        name: kubevirt-internal-issuer
      caBundleConfigMapRef:
        kind: ConfigMap
        name: kubevirt-internal-ca-bundle
      duration: 24h0m0s
```

### Phase 2: Dedicated Metrics CA and Issuer for `/metrics` mTLS

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
  namespace: kubevirt
spec:
  certificateRotateStrategy:
    certManager:
      issuerRef:
        apiGroup: cert-manager.io
        kind: Issuer
        name: kubevirt-internal-issuer
      caBundleConfigMapRef:
        kind: ConfigMap
        name: kubevirt-internal-ca-bundle
      duration: 24h0m0s
      metrics:
        issuerRef:
          apiGroup: cert-manager.io
          kind: ClusterIssuer
          name: cluster-monitoring-issuer
        caBundleConfigMapRef:
          kind: ConfigMap
          name: monitoring-scraper-ca-bundle
        duration: 12h0m0s
```

## Alternatives

1. **Use `cert-manager.io/v1` `Certificate` CRs created by `virt-operator`**:
   - *Rejected*: `Certificate` CRs write private keys into Kubernetes `Secret`s in etcd and share a single certificate across all replicas of a `Deployment` or `DaemonSet`. That prevents per-pod IP SANs and per-node `virt-handler` certificates.
2. **Use Kubernetes `certificates.k8s.io/v1` `CertificateSigningRequest` (CSR)**:
   - *Rejected*: Kubernetes CSRs are cluster-scoped resources requiring cluster-wide RBAC permissions to watch and approve, whereas `cert-manager` `CertificateRequest`s are namespaced and natively support arbitrary `Issuer` and `ClusterIssuer` backends.
3. **Use CSI driver (`cert-manager-csi-driver`)**:
   - *Rejected*: Requires deploying an additional privileged CSI driver DaemonSet on every cluster node and does not easily support dynamic runtime reconfiguration between `selfSigned` and `certManager` via the `KubeVirt` CR without recreating pod specs.

## Does it belong to core KubeVirt?

Yes. Certificate bootstrapping, TLS configuration (`pkg/util/tls`), webhook/APIService CA bundle injection, `virt-operator` RBAC, and `virt-handler` node/migration identity verification are built directly into KubeVirt's core control-plane and node binaries (`virt-operator`, `virt-api`, `virt-controller`, `virt-handler`, `virt-exportproxy`).

## Scalability

- **In-place certificate validation without `kube-apiserver` load**:
  - In contrast to projected ServiceAccount token authentication (which requires `TokenReview` and `SubjectAccessReview` calls to `kube-apiserver` to determine caller identity and permissions, or complex local caching and invalidation of review outcomes), X.509 client and server certificates encode the verified role, node name, and Pod IP directly in the certificate. Peers validate certificates locally against the in-memory CA bundle on every connection (`virt-api` to `virt-handler`, live migration streams, and `/metrics` scrapes) without putting request pressure on `kube-apiserver` or maintaining review caches.
- **Number of `CertificateRequest`s**:
  - Control-plane pods (`virt-operator`, `virt-api`, `virt-controller`, `virt-exportproxy`) create 1–2 `CertificateRequest`s per replica (plus 1 in Phase 2 when a separate metrics issuer is configured).
  - Each `virt-handler` pod creates 3 `CertificateRequest`s (console/lifecycle server, migration server/client, vsock client; plus 1 in Phase 2 when a separate metrics issuer is configured).
  - On a 500-node cluster with a 24h certificate duration, `virt-handler` generates ~1,500–2,000 `CertificateRequest`s per ~19h rotation window (~0.03 requests/sec average).
- **Mitigations**:
  - Rotation uses a randomized jitter between 70% and 90% of certificate validity (`jitteryDuration`) to prevent thundering herds across nodes.
  - `virt-handler` persists certificates on `/var/lib/kubevirt-node-certificates` on the host so pod restarts do not trigger redundant certificate issuances while the existing certificate is still valid.
  - Completed `CertificateRequest` objects are deleted immediately after the signed certificate is retrieved, keeping etcd object counts near zero at steady state.

## Update/Rollback Compatibility

- **Default Behavior**: `selfSigned` remains the default strategy when `spec.certificateRotateStrategy.certManager` is not set. Existing clusters are unaffected on upgrade.
- **Switching Strategies**: Switching a running cluster between `selfSigned` and `certManager` (or enabling/disabling `certManager.metrics` in Phase 2) updates the `KubeVirt` CR. `virt-operator` updates webhook/APIService CA bundles, and all KubeVirt components detect the strategy change via `shouldUpdateCertificateManager` and exit cleanly to restart with the newly configured certificate manager.
- **Rollbacks**: Rolling back the `KubeVirt` CR from `certManager` to `selfSigned` restores `kubevirt-ca` Secret generation and restarts components with Secret-mounted certificates.

## Functional Testing Approach

- **Unit Tests**:
  - **Certificate bootstrapping and rotation**: CSR generation, `CertificateRequest` lifecycle, rotation scheduling, error backoff, and disk caching across node, client, service, and metrics roles.
  - **`CertificateRequest` approval**: Approval and denial of valid and invalid `CertificateRequest`s across all KubeVirt ServiceAccounts, verifying rejection of mismatched Pod IPs, wrong node names, unauthorized DNS SANs, or invalid key usages.
  - **TLS peer verification**: Node- and role-specific certificate verification for `virt-handler` server/migration connections and mTLS enforcement on `/metrics`.
  - **Webhook validation**: Mutual exclusivity of `selfSigned` and `certManager` and validation of `issuerRef`, `caBundleConfigMapRef`, and `metrics` fields.
- **End-to-End (e2e) Functional Tests**:
  - **Phase 1**: Deploy KubeVirt with a `cert-manager` `Issuer` and CA bundle `ConfigMap`, verify all control-plane and `virt-handler` pods become Ready, start a VMI, verify `virt-api` console/VNC/guest-info connections and live migration succeed with strict node/IP certificate verification, and test live switching between `selfSigned` and `certManager`.
  - **Phase 2**: Configure `spec.certificateRotateStrategy.certManager.metrics` with a separate metrics `Issuer` and client CA `ConfigMap`, verify `/metrics` scraping fails without a valid client cert or with a control-plane client cert, and succeeds with a client cert signed by the metrics CA while verifying the server's Pod IP SAN.

## Implementation History

- **Phase 1**: Control-plane and node `cert-manager` integration, `virt-operator` `CertificateRequest` approver, and strict `virt-handler` role/node/IP verification (leaves metrics TLS unchanged).
- **Phase 2**: `/metrics` mTLS (`SetupPromTLSWithCA`) and dedicated metrics PKI (`spec.certificateRotateStrategy.certManager.metrics`).

## Graduation Requirements

### Alpha
- **Phase 1**:
  - API fields `spec.certificateRotateStrategy.certManager` (`issuerRef`, `caBundleConfigMapRef`, `duration`) and webhook validation implemented.
  - `CertificateRequestCertificateManager` and `virt-operator` `CertificateRequest` approver implemented with unit tests.
  - Per-node and per-role certificate verification wired for `virt-api` and `virt-handler` (console/lifecycle, migration, vsock).
- **Phase 2**:
  - API field `spec.certificateRotateStrategy.certManager.metrics` implemented and validated.
  - `SetupPromTLSWithCA` and `LoadCertConfigForMetrics` wired into `virt-operator`, `virt-api`, `virt-controller`, `virt-handler`, and `virt-exportproxy`.

### Beta
- E2E tests running in CI covering `cert-manager` installation, VMI lifecycle, console access, live migration, certificate rotation, and (for Phase 2) `/metrics` mTLS scraping with a separate metrics CA.
- Documentation published in `kubevirt/user-guide` covering `Issuer`, `ConfigMap` (`trust-manager`), and Prometheus `ServiceMonitor` / `PodMonitor` mTLS configuration.

#### On-By-Default Readiness
- `certManager` is an opt-in strategy in `spec.certificateRotateStrategy` because it requires `cert-manager` and an external `Issuer` / CA bundle `ConfigMap` to be present in the cluster. `selfSigned` remains the default out-of-the-box strategy.

### GA
- Zero critical bugs over two consecutive releases.
- Validated in production environments across upgrades, certificate rotations, and `selfSigned` <-> `certManager` transitions.
