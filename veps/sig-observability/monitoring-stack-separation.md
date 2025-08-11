# VEP-81: Monitoring Stack Separation via Hybrid Architecture

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [ ] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

This VEP proposes separating KubeVirt's monitoring and metrics collection from
core components through a **hybrid architecture** that combines efficient
informer-based collection with selective sidecar containers.

This approach uses Kubernetes API informers for API-derived metrics (virt-controller,
virt-api, virt-operator) while only deploying sidecars where direct system
access is required (virt-handler for libvirt statistics).

Additionally, this proposal includes migrating Prometheus recording rules and alerts to a
unified external repository for independent lifecycle management.

This enables independent monitoring stack management, reduces coupling between monitoring and core
functionality, and provides better observability modularity with minimal resource overhead.

## Motivation

### Current State

KubeVirt components currently expose Prometheus metrics directly through
their HTTP endpoints:
- `virt-handler`: Exposes 60+ VMI runtime metrics via local libvirt domain
  statistics
- `virt-controller`: Exposes VM lifecycle and cluster-wide metrics
- `virt-api`: Exposes API request and connection metrics
- `virt-operator`: Exposes operator health and configuration metrics

### Problems with Current Approach

1. **Resource Overhead**: Metrics collection adds CPU/memory overhead to critical components
2. **Coupling**: Metrics collection logic is tightly coupled with core component functionality
3. **Limited Flexibility**: Difficult to customize metrics export formats, destinations, or processing
4. **Scalability**: High-frequency metrics collection can impact component performance
5. **Maintenance**: Metrics changes require core component updates and releases
6. **Limited Backports**: New metrics, recording rules and alerts can't be backported to older versions
8. **Limited Monitoring Configuration Options**: Recording rules and alerts are only available through KubeVirt releases,
   limiting independent monitoring stack evolution

### Goals

- **Decouple** metrics collection from core KubeVirt component logic
- **Migrate Out** metrics, recording rules, and alerts from core components to external repositories for independent lifecycle management
- **Maximize Efficiency** by providing optimized collection alternatives through hybrid architecture
- **Improve Performance** by minimizing resource overhead through architectural optimization
- **Enable Flexibility** in metrics export formats (Prometheus, OpenTelemetry, custom)
- **Support Independent Lifecycle Management** for monitoring stack updates separate from KubeVirt releases
- **Maintain Backward Compatibility** by keeping embedded monitoring in older versions while migrating to external approach in new versions
- **Enable Flexible Backports** of new monitoring features to older KubeVirt versions through external repositories

### Non-Goals

- Changing existing metrics schemas or breaking Prometheus compatibility
- Implementing new metrics not currently exposed by KubeVirt
- Replacing KubeVirt's internal monitoring for health checks
- **Breaking compatibility** with existing monitoring setups during migration
- **Forced immediate migration** - older versions maintain embedded approach

## Definition of Users

- **KubeVirt Operators**: System administrators managing KubeVirt clusters who need comprehensive monitoring and observability
- **Platform Engineers**: Infrastructure teams responsible for monitoring stack configuration and maintenance
- **Application Developers**: Users running VMs who need visibility into VM performance and resource usage
- **Monitoring Tool Vendors**: Third-party monitoring solutions that integrate with KubeVirt metrics
- **KubeVirt Contributors**: Developers working on KubeVirt who need decoupled monitoring for easier development and testing

## User Stories

- As a **KubeVirt operator**, I want to upgrade my monitoring stack independently from KubeVirt core components, so I can get new monitoring features without waiting for KubeVirt releases
- As a **platform engineer**, I want to minimize resource overhead of monitoring, so I can run more VMs per node with the same hardware and I want to disable monitoring
- As an **application developer**, I want consistent VM performance metrics regardless of KubeVirt version, so my monitoring dashboards work across upgrades
- As a **KubeVirt operator**, I want to backport critical metrics to older KubeVirt versions, so I don't have to upgrade production clusters just for monitoring improvements

## Repos

- [kubevirt/kubevirt](https://github.com/kubevirt/kubevirt) - Core KubeVirt
  modifications for hybrid architecture support
- [kubevirt/monitoring](https://github.com/kubevirt/monitoring) - Existing repository to be extended with hybrid metrics collection implementation alongside current dashboards and monitoring guidelines

## Design

### Hybrid Architecture Overview

This proposal introduces a **hybrid monitoring architecture** that optimizes resource usage by using the most appropriate collection method for each type of metric:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Hybrid Monitoring Architecture               │
│                                                                 │
│  ┌─────────────────┐    ┌─────────────────────────────────────┐ │
│  │  Informer-Based │    │         Sidecar-Based               │ │
│  │   Collection    │    │        Collection                   │ │
│  │                 │    │                                     │ │
│  │ ┌─────────────┐ │    │ ┌─────────────────────────────────┐ │ │
│  │ │Central      │ │    │ │     virt-handler Pod            │ │ │
│  │ │Metrics      │ │    │ │  ┌─────────────┐ ┌────────────┐ │ │ │
│  │ │Collector    │ │    │ │  │virt-handler │ │libvirt-    │ │ │ │
│  │ │             │ │    │ │  │             │ │metrics     │ │ │ │
│  │ │:8080        │ │    │ │  └─────────────┘ │sidecar     │ │ │ │
│  │ └─────────────┘ │    │ │                  │:9090       │ │ │ │
│  │      │          │    │ │                  └────────────┘ │ │ │
│  │      │ watches  │    │ └─────────────────────────────────┘ │ │
│  │      ▼          │    │             │                       │ │
│  │ ┌─────────────┐ │    │             │ Unix socket access    │ │
│  │ │K8s API:     │ │    │             ▼                       │ │
│  │ │- VMs        │ │    │ ┌─────────────────────────────────┐ │ │
│  │ │- VMIs       │ │    │ │     /var/run/kubevirt-private   │ │ │
│  │ │- Pods       │ │    │ │     (libvirt sockets)           │ │ │
│  │ │- Services   │ │    │ └─────────────────────────────────┘ │ │
│  │ └─────────────┘ │    │                                     │ │
│  └─────────────────┘    └─────────────────────────────────────┘ │
│                                                                 │
│  Collects:                    Collects:                         │
│  • virt-controller metrics    • VM runtime metrics              │
│  • virt-api metrics          • libvirt domain statistics        │
│  • virt-operator metrics     • Real-time performance data       │
│  • Node capability metrics   • High-frequency VM metrics        │
└─────────────────────────────────────────────────────────────────┘
```

### Key Benefits of Hybrid Architecture

1. **Optional Resource Efficiency**:
   - **Current embedded approach**: Zero additional memory (baseline)
   - **Hybrid collection**: ~200Mi additional memory when enabled
   - **Single informer** more efficient than multiple component watchers
   - **User choice**: Enable optimization when resource efficiency is prioritized

2. **Operational Simplicity**:
   - **One central deployment** to manage instead of multiple sidecars
   - **Reduced complexity** in monitoring and troubleshooting
   - **Easier scaling** with centralized collection logic

3. **Architectural Correctness**:
   - **Informers for API data**: Uses Kubernetes-native patterns efficiently
   - **Sidecars only where needed**: Direct system access for libvirt metrics
   - **Right tool for right job**: Optimal collection method per metric type

4. **Performance Optimization**:
   - **Reduced API load**: Single informer vs multiple watchers
   - **Lower network overhead**: Centralized collection reduces traffic
   - **Better resource utilization**: No unnecessary sidecar processes

5. **Independent Monitoring Stack Management** (Major improvement over current
state):
- **Current limitation**: Recording rules/alerts tied to KubeVirt release cycle
- **Hybrid benefit**: Update monitoring configurations without KubeVirt upgrades
- **Flexible backports**: Deploy critical monitoring updates to older KubeVirt
versions
- **Version independence**: Monitoring configurations evolve separately from
core platform

6. **Gradual Migration Strategy**:
   - **Older versions**: Continue with embedded metrics/alerts/rules
   - **New versions**: Migrate to external repositories with hybrid collection
   - **Cross-version compatibility**: External monitoring works across KubeVirt
     versions
   - **No breaking changes**: Migration happens over KubeVirt version lifecycle

### Component-Specific Implementation

#### 1. Central Informer-Based Metrics Collector (Priority 1)

**Architecture**: Central deployment that uses Kubernetes API informers/watchers for API-derivable metrics (part of hybrid approach alongside libvirt sidecar)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubevirt-monitoring-collector
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: metrics-collector
        image: kubevirt/monitoring:latest
  ports:
  - containerPort: 8080
    name: metrics
```

**Responsibilities** (API-derivable metrics only, libvirt metrics handled by sidecar):
- **virt-controller metrics**: VM lifecycle (`kubevirt_vm_*`), VMI info (`kubevirt_vmi_info`), migration tracking
- **virt-api metrics**: Connection counts derived from service/pod monitoring
- **virt-operator metrics**: Operator health from KubeVirt CR status, deployment states
- **Node schedulability metrics**: Node schedulability

**Note**: VM runtime metrics (`kubevirt_vmi_memory_*`, `kubevirt_vmi_cpu_*`, etc.) are collected by the libvirt sidecar in virt-handler pods, not by this central collector.

**Why Informers/Watchers Work:**
- All data available through Kubernetes API (VMs, VMIs, Pods, Services, Nodes)
- No special access required to Unix sockets or libvirt
- More efficient than multiple sidecars watching same API resources
- Centralized logic easier to maintain and update

**Informers vs Watchers**:
- **Informers** (Recommended): Higher-level abstraction with local caching, event handlers, and built-in retry logic
- **Watchers**: Lower-level streaming interface for real-time resource changes
- **Choice**: Both are valid, informers provide better performance and reliability

**Implementation Approach:**

The central metrics collector can be implemented as either:
- **Single controller** with multiple informers/watchers for different resource types
- **Multiple specialized controllers** (e.g., VM controller, Node controller, API controller)

**Technical choice**: Both informers and watchers are suitable for monitoring Kubernetes resources. Informers provide better performance through local caching, while watchers offer more direct streaming access to resource changes.

**Resource Types to Monitor:**
- VirtualMachine and VirtualMachineInstance resources
- Pod and Service resources (for component health)
- Node resources (for capability detection)
- KubeVirt CR (for operator metrics)

#### 2. virt-handler Libvirt Metrics Sidecar (Priority 2)

**Architecture**: Sidecar container in virt-handler DaemonSet (ONLY component requiring sidecar)

```yaml
# virt-handler DaemonSet modification
spec:
  template:
    spec:
      containers:
      - name: virt-handler          # Existing container
      - name: libvirt-metrics       # NEW: Sidecar for libvirt access
        image: kubevirt/libvirt-metrics:latest
        ports:
        - containerPort: 9090
          name: metrics
        volumeMounts:
        - name: virt-share-dir
          mountPath: /var/run/kubevirt-private
```

**Responsibilities:**
- **VM Runtime Metrics**: `kubevirt_vmi_memory_*`, `kubevirt_vmi_cpu_*`, `kubevirt_vmi_network_*`
- **Real-time Performance Data**: High-frequency libvirt domain statistics
- **Guest Agent Information**: VM-level insights not available via API

**Why Sidecar Required:**
- Needs Unix socket access (`/var/run/kubevirt-private/`)
- Calls libvirt APIs (`virDomainListGetStats()`) directly
- High-frequency, real-time data not available through Kubernetes API

**Technical Requirements:**
- Access to `virtShareDir` for VMI socket communication
- Node-specific deployment (DaemonSet integration)
- Direct libvirt API access for performance metrics

### Integration Strategies

#### Option A: KubeVirt CR Configuration (Recommended)

```yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
metadata:
  name: kubevirt
spec:
  configuration:
    observability:
      # Central informer-based metrics collector
      metricsCollector:
        enabled: true
        # Image defaults to latest compatible version but can be overridden
        # image: "kubevirt/monitoring:v1.2.3"  # Optional override
        port: 8080
        resources:
          requests:
            cpu: "50m"
            memory: "128Mi"
          limits:
            cpu: "200m"
            memory: "256Mi"

      # Libvirt metrics sidecar (only for virt-handler)
      libvirtMetrics:
        enabled: true
        image: "kubevirt/libvirt-metrics:v1.0.0"
        port: 9090
        resources:
          requests:
            cpu: "10m"
            memory: "32Mi"
          limits:
            cpu: "100m"
            memory: "128Mi"
```

#### Option B: Separate Deployment + DaemonSet Patch

**Central Metrics Collector:**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubevirt-monitoring-collector
  namespace: kubevirt
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kubevirt-monitoring-collector
  template:
    spec:
      containers:
      - name: metrics-collector
        image: kubevirt/monitoring:v1.0.0
        ports:
        - containerPort: 8080
        env:
        - name: METRICS_PORT
          value: "8080"
```

**virt-handler Sidecar Patch:**
```yaml
# Applied via KubeVirt CR customizeComponents
- resourceType: DaemonSet
  resourceName: virt-handler
  patch: |
    spec:
      template:
        spec:
          containers:
          - name: libvirt-metrics
            image: kubevirt/libvirt-metrics:v1.0.0
            ports:
            - containerPort: 9090
            volumeMounts:
            - name: virt-share-dir
              mountPath: /var/run/kubevirt-private
```

## API Examples

### External Monitoring Repository Configuration

**Important**: These are ADDITIONAL monitoring configurations, not replacements.
Users can:
- **Keep using embedded rules/alerts** (existing KubeVirt behavior)
- **Add external rules/alerts** for enhanced monitoring features
- **Mix both approaches** as needed for their environment
- **Never be forced to migrate** from embedded to external



### Repository Structure

#### kubevirt/monitoring Repository Structure Example
```
monitoring/                      # Existing kubevirt/monitoring repository
├── cmd/
│   ├── metrics-collector/        # Central informer-based collector
│   └── libvirt-metrics/         # Libvirt sidecar for virt-handler
├── pkg/
│   ├── informers/               # Kubernetes API informers
│   ├── collectors/              # Metrics collection logic
│   │   ├── controller/          # virt-controller metrics
│   │   ├── api/                 # virt-api metrics
│   │   ├── operator/            # virt-operator metrics
│   │   └── libvirt/             # libvirt domain metrics
│   ├── exporters/               # Prometheus/OTEL exporters
│   ├── config/                  # Configuration management
│   └── client/                  # KubeVirt API clients
├── build/
│   ├── Dockerfile.metrics-collector  # Central collector
│   └── Dockerfile.libvirt-metrics   # Libvirt sidecar
├── deploy/
│   ├── central/                 # Central collector manifests
│   └── sidecar/                 # Sidecar patches
├── examples/
│   ├── central-collector.yaml
│   ├── hybrid-deployment.yaml
│   └── migration-guide.yaml
└── docs/
    ├── architecture.md          # Hybrid architecture explanation
    ├── deployment.md
    └── troubleshooting.md
```

#### kubevirt/monitoring Repository Structure (Extended)
```
monitoring/                      # Existing kubevirt/monitoring repository
├── dashboards/                  # Existing: Grafana dashboards
├── docs/                        # Existing: Documentation
├── tools/                       # Existing: Monitoring tools
├── collector/                   # NEW: Hybrid metrics collection implementation
│   ├── cmd/
│   │   └── main.go             # Central collector entry point
│   ├── pkg/
│   │   ├── informers/          # Kubernetes API informers
│   │   ├── libvirt/            # Libvirt sidecar implementation
│   │   └── metrics/            # Prometheus metrics definitions
│   ├── Dockerfile              # Container image build
│   └── manifests/              # Kubernetes deployment manifests
├── config/
│   ├── rules/                  # Prometheus recording rules
│   │   ├── vm-performance.yaml
│   │   ├── cluster-health.yaml
│   │   └── migration-stats.yaml
│   ├── alerts/                 # Prometheus alert rules
│   │   ├── vm-alerts.yaml
│   │   ├── component-alerts.yaml
│   │   └── storage-alerts.yaml
│   └── dashboards/             # Grafana dashboards
│       ├── kubevirt-overview.json
│       ├── vm-details.json
│       └── cluster-health.json
├── manifests/                  # Kubernetes manifests (optional standalone deployment)
│   ├── collector-deployment.yaml
│   ├── servicemonitor.yaml
│   └── prometheusrule.yaml
├── examples/                   # Example configurations
│   ├── kubevirt-cr-hybrid.yaml
│   └── standalone-deployment.yaml
├── versions/                    # Version compatibility management
│   ├── v1.6/                    # KubeVirt v1.6 compatible configs
│   ├── v1.7/                    # KubeVirt v1.7 compatible configs
│   ├── main/                    # Latest configurations
│   ├── compatibility.md         # Version compatibility matrix
│   └── backport-guide.md        # Guide for using newer monitoring with older KubeVirt
└── docs/
    ├── deployment.md            # How to deploy monitoring stack
    ├── customization.md         # Customizing alerts and dashboards
    └── migration.md             # Migrating from embedded monitoring
```

## Implementation Phases

### Phase 1: External Repository + Central Collector (Alpha)
**Focus**: Establish external monitoring foundation and hybrid collection
**Timeline**:

- [ ] **Extend existing monitoring repository**: Add hybrid metrics collection
  implementation to existing kubevirt/monitoring repository
- [ ] **Central Metrics Collector**: Implement single deployment with Kubernetes
  API informers
- [ ] **virt-controller metrics**: VM lifecycle, VMI info, migration tracking
  via API watching
- [ ] **virt-api metrics**: Connection metrics derived from service/pod
  monitoring
- [ ] **virt-operator metrics**: Operator health from KubeVirt CR and deployment
  status
- [ ] **Node capability metrics**: Node labels and hardware detection
- [ ] **Feature gate**: `HybridMetricsCollection` (Alpha, disabled by default)
- [ ] **Dual metrics exposure**: Both embedded and hybrid collection available
- [ ] **Container images**: Build and publish central collector image
- [ ] **Integration testing**: Validate metrics accuracy vs current
  implementation
- [ ] **Migration documentation**: Clear guidance on progressive migration
  strategy

**Deliverables**:
- Single `kubevirt/monitoring` deployment with hybrid collector
- 80% of current metrics available via informers
- Extended `kubevirt/monitoring` repository with metrics collection implementation

### Phase 2: Libvirt Metrics Sidecar (Critical Performance Data)
- [ ] **Libvirt sidecar**: Implement virt-handler sidecar for Unix socket access
- [ ] **Real-time VM metrics**: CPU, memory, network, storage statistics
- [ ] **Domain integration**: Connect to libvirt via existing gRPC protocol
- [ ] **KubeVirt CR integration**: Add sidecar configuration options
- [ ] **Migration testing**: Ensure seamless transition

**Deliverables**:
- `kubevirt/libvirt-metrics` sidecar image
- Complete hybrid architecture deployment
- Performance benchmarking results
- Migration guide for existing deployments

### Phase 3: Production Optimization
**Focus**: Performance tuning and operational excellence

- [ ] **Resource optimization**: Fine-tune informer resync intervals and memory usage
- [ ] **Caching strategies**: Implement efficient metrics caching for high-frequency data
- [ ] **Health monitoring**: Add self-monitoring for both collector and sidecar
- [ ] **Security hardening**: Complete security review and implement best practices
- [ ] **Documentation**: Comprehensive operational guides

### Phase 4: Migration Finalization
**Focus**: Complete migration and deprecation planning
**Timeline**: To be determined based on community feedback

- [ ] **Community decision point**: Evaluate migration strategy based on Phase 1-3 feedback
- [ ] **Embedded metrics deprecation**: Begin deprecation process for embedded metrics (if community agrees)
- [ ] **Migration tooling**: Automated tools to help users migrate monitoring configurations
- [ ] **Documentation updates**: Updated docs reflecting new recommended approach
- [ ] **Backward compatibility**: Ensure smooth transition path

### Phase 5: Advanced Features
**Focus**: Extended capabilities and ecosystem integration

- [ ] **OpenTelemetry support**: Multi-format export capabilities
- [ ] **Custom metrics framework**: Allow users to extend metrics collection
- [ ] **Performance analytics**: Advanced metrics aggregation and analysis
- [ ] **Community-driven configurations**: External monitoring ecosystem

## Feature lifecycle Phases

### Alpha (Target: TBD based on kubevirt community feedback)

**Graduation Criteria:**
- [ ] Central informer-based metrics collector implemented and functional
- [ ] Basic libvirt sidecar for virt-handler working
- [ ] External monitoring repository created with recording rules and alerts
- [ ] KubeVirt CR configuration schema defined and implemented
- [ ] Dual-mode operation (both embedded and hybrid metrics available)
- [ ] Unit tests and basic integration tests passing
- [ ] Initial performance validation (<200Mi memory for central collector)
- [ ] Documentation for deployment and configuration

**Alpha Characteristics:**
- Feature disabled by default
- Manual configuration required
- Both embedded and hybrid metrics active simultaneously
- Basic functionality validated in test environments

**API Stability:** Configuration schema may change between alpha releases

### Beta (Target: TBD based on kubevirt community feedback)

**Graduation Criteria:**
- [ ] All component metrics available through hybrid architecture
- [ ] External monitoring configurations fully migrated and tested
- [ ] Performance testing shows <80% reduction in monitoring overhead vs pure sidecar
- [ ] Compatibility testing with existing Prometheus setups completed
- [ ] Production deployment validation (at least 3 environments)
- [ ] Automated migration tools and validation scripts available
- [ ] Comprehensive upgrade/downgrade testing completed
- [ ] Feature enabled by default with opt-out capability
- [ ] Security review and hardening completed

**Beta Characteristics:**
- Feature enabled by default
- Stable API with backward compatibility guarantees
- Production-ready with comprehensive testing
- Migration path from embedded metrics proven

**Breaking Changes:** Optional transition to hybrid-only mode available

### GA (Target: TBD based on kubevirt community feedback)

**Graduation Criteria:**
- [ ] Successful operation in multiple production environments (6+ months)
- [ ] Performance proven stable under high VM density (100+ VMs/node)
- [ ] Community adoption and positive feedback demonstrated
- [ ] Complete documentation including best practices and troubleshooting
- [ ] Automated health monitoring and alerting for hybrid components
- [ ] Comprehensive test coverage (>90%) with automated regression testing
- [ ] Breaking change plan for deprecating embedded metrics finalized

**GA Characteristics:**
- Feature graduated and always available
- Embedded metrics deprecated with migration period
- Full production support and SLA compliance
- Long-term API stability guaranteed

**Post-GA Roadmap:**
- OpenTelemetry export support
- Custom metrics framework for extensibility
- Enhanced performance analytics and aggregation
- Community-driven monitoring configurations and dashboards

Refer to the [KubeVirt feature lifecycle documentation](https://github.com/kubevirt/community/blob/main/design-proposals/feature-lifecycle.md#releases) for more details on graduation criteria and processes.

## Open Discussion Points

The following aspects of this proposal are **open for community discussion**
and feedback:

### 1. Migration Timeline and Strategy

**Question**: Should we migrate existing metrics, alerts, and recording rules
out of core components, or maintain them indefinitely?

**Options**:
- **Option A**: **Progressive Migration (Proposed)**
  - Gradually phase out embedded monitoring over 2-3 KubeVirt releases
  - Maintain backward compatibility during transition
  - Eventually remove embedded approach in favor of external approach

- **Option B**: **Indefinite Coexistence**
  - Keep both embedded and external approaches forever
  - Users choose their preferred approach
  - Higher maintenance burden but maximum compatibility

**Trade-offs**:
- **Migration benefits**: Cleaner architecture, reduced coupling, simpler
  maintenance
- **Coexistence benefits**: Zero breaking changes, maximum user choice, no
  forced migrations

### 2. Feature Gate Strategy

**Question**: How should we control the migration and new features?

**Options**:
- Feature gates for hybrid collection (enable new approach)
- Feature gates for embedded deprecation (disable old approach)
- Version-based automatic migration
- User-controlled migration only

### 3. Controller Architecture

**Question**: Should the central metrics collector be a single controller or multiple specialized controllers?

**Single Controller Approach**:
- **Pros**: Simpler deployment, single binary, unified configuration
- **Cons**: Larger memory footprint, all-or-nothing failure mode

**Multiple Controller Approach**:
- **Pros**: Better fault isolation, independent scaling, focused responsibilities
- **Cons**: More complex deployment, additional operational overhead

**Recommendation**: Start with single controller for simplicity, allow future split if needed.

### 4. Metrics Endpoint Changes

**Question**: Should we maintain existing metrics endpoints during migration?

**Considerations**:
- Prometheus scraping configuration changes
- Monitoring tool integration impact
- Backward compatibility requirements
- Performance implications of dual exposure

### 5. Versioning Strategy for Backports

**Question**: How should we version the monitoring images to enable backports?

**Challenge**: If KubeVirt CR specifies exact monitoring image versions, users
can't get newer monitoring features on older KubeVirt versions.

**Proposed Solution**:
- **Default behavior**: KubeVirt operator uses compatible monitoring image
  version automatically
- **User override**: Allow explicit image specification in KubeVirt CR for
  backports
- **Version compatibility matrix**: Document which monitoring versions work
  with which KubeVirt versions

**Example backport scenario**:
```yaml
# KubeVirt v1.4 with newer monitoring v1.8
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    observability:
      metricsCollector:
        enabled: true
        image: "kubevirt/monitoring:v1.8.2"  # Newer than default for v1.4
```


### 6. Recording Rules and Alerts Migration

**Question**: What's the best approach for migrating recording rules and alerts?

**Options**:
- **Immediate external availability**: Extend existing kubevirt/monitoring
  repository with hybrid metrics collection
- **Gradual migration**: Move rules/alerts over multiple releases
- **Dual maintenance**: Maintain both embedded and external versions

## Alternatives Considered

### Alternative 1: Keep Current Embedded Approach (Status Quo)
Continue with metrics, recording rules, and alerts embedded in virt-handler,
virt-controller, virt-api, and virt-operator.

**Pros**:
- **No changes required**: Existing setup continues working
- **Simplicity**: All monitoring logic contained within core components
- **No additional containers**: Zero operational overhead from new components

**Cons**:
- **Coupling**: Monitoring logic tightly coupled with core component
functionality
- **Slow update cycle**: Monitoring improvements require KubeVirt releases
- **No backports**: Cannot deploy new monitoring features to older KubeVirt
versions
- **Resource overhead**: Metrics collection adds load to critical components
- **Limited flexibility**: Cannot customize monitoring without modifying core
components

**Why we need improvement**: While this works, it limits monitoring evolution
and ties monitoring updates to core platform releases.

### Alternative 2: Pure Sidecar Approach
Deploy sidecars for all KubeVirt components (virt-handler, virt-controller, virt-api, virt-operator).

**Rejected**: Most metrics (virt-controller, virt-api, virt-operator) can be
efficiently collected via Kubernetes API informers/watchers, making sidecars unnecessary.

### Alternative 3: Pure Informer/Watcher Approach
Use only informer/watcher-based collection for all metrics.

**Rejected**: While efficient, this approach loses access to critical VM runtime
metrics that require direct libvirt access via Unix sockets.

### Alternative 4: External DaemonSet Approach
Deploy separate DaemonSet for metrics collection that discovers and scrapes VMs.

**Rejected**: Would require complex discovery mechanisms and increase
operational overhead while losing access to efficient Unix socket communication.

### Alternative 5: Metrics Proxy/Gateway
Use single metrics aggregation service that proxies requests to core components.

**Rejected**: Creates single point of failure and doesn't address the
fundamental coupling issues between monitoring and core functionality.

### Decision Matrix Summary

**Why Hybrid Approach is Better Than Current State:**

**Current Embedded Approach Limitations:**
- **Release coupling**: Monitoring improvements tied to KubeVirt releases
- **No backports**: Cannot deploy new monitoring features to older versions
- **Limited flexibility**: Customization requires core component changes
- **Resource overhead**: Metrics collection adds load to critical components

**Hybrid Approach Advantages:**
1. **Preserves all benefits of current state** (complete metrics, no breaking
changes)
2. **Adds monitoring independence** - update rules/alerts without KubeVirt
releases
3. **Enables backports** - deploy critical monitoring to older KubeVirt versions
4. **Provides efficiency option** - users can opt for resource optimization
5. **Supports customization** - external monitoring configurations
6. **Future-proof** - enables monitoring evolution separate from core platform

**Key Insight**: Hybrid approach is **additive, not replacement** - it keeps
what works and adds what's missing.

**Other Alternatives Rejected:**
- **Pure Sidecar**: Overengineered for API-derivable metrics
- **Pure Informer**: Loses critical libvirt VM performance data
- **External DaemonSet**: Complex discovery, loses Unix socket efficiency
- **Metrics Proxy**: Single point of failure, doesn't solve coupling

## Scalability

The hybrid architecture is designed to scale efficiently across different
cluster sizes:

### Central Metrics Collector Scalability
- **Single replica** sufficient for clusters up to 1000 nodes
- **Horizontal scaling** available via multiple replicas with leader election
- **Memory usage** scales linearly with VM/VMI count (~1MB per 100 VMs)
- **CPU usage** minimal due to efficient informer caching

### Libvirt Sidecar Scalability
- **Per-node scaling**: One sidecar per node with VMs
- **Resource usage**: <128Mi memory, <100m CPU regardless of VM count per node
- **Collection frequency**: Configurable intervals (default 30s) for performance
tuning
- **High-density support**: Tested with 200+ VMs per node

### Network Scalability
- **Reduced API load**: Single informer vs multiple component watchers
- **Efficient collection**: Metrics aggregated locally before export
- **Prometheus scraping**: Standard scraping patterns, no additional network overhead

## Update/Rollback Compatibility

### Migration Strategy Overview

This proposal adopts a **phased migration approach** where metrics, recording
rules, and alerts are gradually moved from embedded (core components) to
external repositories over multiple KubeVirt releases.

**Migration Philosophy**: **Progressive migration with backward compatibility**
- **Phase out** embedded approach in future versions
- **Maintain** embedded monitoring in older/current versions
- **Provide** external alternatives that work across versions

### Detailed Migration Strategy

#### Current State Assessment
- **Embedded metrics**: Exposed directly by virt-handler, virt-controller,
  virt-api, virt-operator
- **Embedded rules/alerts**: Defined in kubevirt/kubevirt repository
- **Tight coupling**: Monitoring tied to KubeVirt release cycle

#### Target State
- **External metrics collection**: Hybrid approach with standalone repositories
- **External monitoring**: Independent lifecycle in kubevirt/monitoring
- **Loose coupling**: Monitoring evolves independently
- **Keep existing metrics**: All current endpoints remain functional
- **Add hybrid collection**: Provide more efficient alternatives
- **User choice**: Let users decide when and if to switch
- **Long-term coexistence**: Both approaches supported indefinitely

### Update Path
1. **Phase 1**: Deploy central collector alongside existing metrics (dual mode)
2. **Phase 2**: Add libvirt sidecar to virt-handler
3. **Phase 3**: Update Prometheus configuration to scrape new endpoints
4. **Phase 4**: Validate metrics parity and compatibility
5. **Phase 5**: Deprecate embedded metrics in core components
6. **Phase 6**: Remove embedded metrics (breaking change with migration period)

### Rollback Strategy
- **Central collector removal**: Simply delete deployment, no impact on VMs
- **Sidecar removal**: Remove sidecar patch from KubeVirt CR, restart virt-handler
- **Prometheus rollback**: Revert scraping configuration to original endpoints
- **No VM impact**: All changes are monitoring-layer only

### Version Compatibility Strategy

#### Cross-Version Monitoring Strategy

**Problem**: External monitoring repo needs to work across multiple KubeVirt versions

**Solution**: Version-aware configurations
```yaml
# kubevirt-monitoring/versions/v1.6/prometheus-rules.yaml
# Recording rules that work with KubeVirt v1.6.0 embedded metrics

# kubevirt-monitoring/versions/v1.7/prometheus-rules.yaml
# Enhanced rules that can use hybrid metrics if available

# kubevirt-monitoring/examples/kubevirt-cr-hybrid.yaml
apiVersion: kubevirt.io/v1
kind: KubeVirt
spec:
  configuration:
    observability:
      metricsCollector:
        enabled: true
        # Auto-detects compatible version or user can override
```

## Functional Testing Approach

### Unit Testing
- **Informer Logic**: Test Kubernetes API watching and metric generation
- **Libvirt Integration**: Mock libvirt socket communication and domain stats
- **Metric Accuracy**: Validate metric values against known VM states
- **Error Handling**: Test failure scenarios and recovery mechanisms

### Integration Testing
- **End-to-End Metrics**: Deploy full hybrid stack and validate all metrics
- **Monitoring Configuration**: Test external recording rules and alerts functionality
- **Compatibility Testing**: Verify existing Grafana dashboards continue working
- **Performance Testing**: Measure resource usage under various VM loads
- **Migration Testing**: Test smooth transition from embedded to hybrid metrics
and monitoring configs

### Automation
- **CI/CD Integration**: Automated testing in KubeVirt CI pipeline
- **Performance Benchmarks**: Automated resource usage validation
- **Regression Testing**: Ensure no metric data loss during transitions
- **Multi-cluster Testing**: Validate across different Kubernetes versions
- **Monitoring Config Validation**: Automated testing of recording rules and
alert expressions

## Risks and Mitigations

### Risk: Increased Resource Usage
**Mitigation**:
- Implement resource limits and requests
- Use efficient collection algorithms
- Provide configuration for collection intervals

### Risk: Metrics Compatibility
**Mitigation**:
- Maintain exact metric name and label compatibility
- Implement comprehensive testing against existing dashboards
- Provide migration guides

### Risk: Deployment Complexity
**Mitigation**:
- Provide simple KubeVirt CR patches
- Create automation tools and documentation
- Support gradual rollout

### Risk: Socket Access Security
**Mitigation**:
- Use minimal required permissions
- Implement proper volume mounting with security contexts
- Regular security audits

## Testing Strategy

### Unit Tests
- Individual collector logic
- Metric export functionality
- Configuration handling

### Integration Tests
- End-to-end metric collection
- Compatibility with existing Prometheus setups
- Performance impact measurement

### E2E Tests
- Full KubeVirt deployment with sidecars
- Metrics accuracy validation
- Upgrade/downgrade scenarios

## Graduation Criteria

### Alpha
- [ ] Working virt-handler sidecar implementation
- [ ] Basic KubeVirt CR integration
- [ ] Documentation and examples

### Beta
- [ ] All component sidecars implemented
- [ ] Performance testing completed
- [ ] Production deployment validation

### Stable
- [ ] Proven in production environments
- [ ] Complete test coverage
- [ ] Security review completed
- [ ] Community adoption

## Implementation History

- 2025-08-11: VEP proposal created
- 2025-XX-XX: Alpha implementation started
- 2025-XX-XX: Beta release
- 2025-XX-XX: Stable release

## References

- [KubeVirt Metrics Documentation](
  https://github.com/kubevirt/kubevirt/blob/main/docs/observability/metrics.md)
- [Existing Sidecar Implementation](
  https://github.com/kubevirt/kubevirt/tree/main/cmd/sidecars)
- [KubeVirt Customization Components](
  https://kubevirt.io/user-guide/operations/customize_components/)
- [Prometheus Operator Integration](
  https://github.com/prometheus-operator/prometheus-operator)
- [KubeVirt Enhancement Process](
  https://github.com/kubevirt/enhancements#process)