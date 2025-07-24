# VEP #41: Object Graph API for VM Dependencies

## Release Signoff Checklist

Items marked with (R) are required *prior to targeting to a milestone / release*.

- [x] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] : https://github.com/kubevirt/enhancements/issues/41

## **Overview**

This is a proposal to include an `Object Graph` API in KubeVirt to represent VM and VMI dependencies and their relationships.

## **Motivation**

As new features continue to be added to KubeVirt, the graph of objects related to VMs (DataVolumes, PersistentVolumeClaims, InstanceTypes, Preferences, Secrets, ConfigMaps, etc.) continues to expand. Identifying all the objects that a VM depends on for tasks like backup, disaster recovery, or migration can be error-prone. We should simplify this process for users and partners by creating an authoritative way to retrieve a structured object graph.

## **Goals**

- Introduce a new Object Graph API to represent the list of resources a VM or VMI depends on.
- Expose this API as a subresource of VirtualMachines and VirtualMachineInstances.
- Provide a flexible and extensible data structure.
- Allow for basic filtering (present or future).

## **Non-Goals**

- Reimplementation of existing VM/VMI specs.
- Building a generic Kubernetes-wide graph system.

## **User Stories**

1. As a KubeVirt user, I want a clear way to retrieve all VM and VMI-related dependencies.
2. As a backup partner, I want a way to identify a list of a VM's related objects so I can comprehensively backup and restore everything a VM needs.
3. As a VM owner, I want to easily define an ACM-discovered application and protect my VM with disaster recovery software.
4. As a VM owner, I want to migrate my VM from one cluster to another and identify all necessary dependencies for replication.
5. As a KubeVirt developer, I want a specific place to keep the object graph code updated when I introduce code that changes the relationship of a VM to its dependent objects.

## **Repos**

- [KubeVirt](https://github.com/kubevirt/kubevirt)
- [KubeVirt Velero Plugin](https://github.com/kubevirt/kubevirt-velero-plugin)

## **Design**

### API Endpoints

```
/apis/subresources.kubevirt.io/v1/namespaces/{namespace}/virtualmachines/{name}/objectgraph
/apis/subresources.kubevirt.io/v1/namespaces/{namespace}/virtualmachineinstances/{name}/objectgraph
```

### Graph Representation

We propose a **Hierarchical Object Graph** to represent dependencies.
Each object can have child objects, capturing direct relationships between resources (e.g., VM → VMI → Pod → PVC).

#### API Schema

```go
// ObjectGraphNode represents an individual node in the graph.
type ObjectGraphNode struct {
    ObjectReference k8sv1.TypedObjectReference `json:"objectReference"`
    Labels          map[string]string          `json:"labels,omitempty"`
    Optional        bool                       `json:"optional"`
    Children        []ObjectGraphNode          `json:"children,omitempty"`
}

// ObjectGraph represents the complete dependency graph.
//
// +k8s:deepcopy-gen:interfaces=k8s.io/apimachinery/pkg/runtime.Object
type ObjectGraph struct {
    metav1.TypeMeta `json:",inline"`
    metav1.ListMeta `json:"metadata,omitempty"`
    RootNodes       []ObjectGraphNode `json:"rootNodes"`
}
```

##### Example Output
```json
{
  "items": [
    {
      "objectReference": {
        "apiGroup": "kubevirt.io",
        "kind": "virtualmachineinstances",
        "name": "vm-cirros-source-ocs",
        "namespace": "default"
      },
      "labels": {},
      "optional": false,
      "children": [
        {
          "objectReference": {
            "apiGroup": "",
            "kind": "pods",
            "name": "virt-launcher-vm-cirros-source-ocs-frn9h",
            "namespace": "default"
          },
          "labels": {},
          "optional": false,
          "children": []
        }
      ]
    },
    {
      "objectReference": {
        "apiGroup": "cdi.kubevirt.io",
        "kind": "datavolumes",
        "name": "cirros-dv-source-ocs",
        "namespace": "default"
      },
      "labels": {
        "type": "storage"
      },
      "optional": false,
      "children": [
        {
          "objectReference": {
            "apiGroup": "",
            "kind": "persistentvolumeclaims",
            "name": "cirros-dv-source-ocs",
            "namespace": "default"
          },
          "labels": {
            "type": "storage"
          },
          "optional": false,
          "children": []
        }
      ]
    }
  ]
}
```

### **User Flow**

1. Access the ObjectGraph API through the subresource endpoint for a VM/VMI.
2. Parse the response and filter unnecessary objects (e.g., in backup scenarios).
3. Use the retrieved data as needed.

### **Included Resources**

- **Instance type controllerRevision (`status.instancetypeRef.controllerRevisionRef.Name`)**
- **Preference controllerRevision (`status.preferenceRef.controllerRevisionRef.Name`)**
- **VirtualMachineInstance (VMI):** Identified by VM name.  
  - **Virt-launcher Pod:** Identified by label.
  - **Volumes**:
    - **DataVolumes (`spec.template.spec.volumes[*].dataVolume`)**
    - **PersistentVolumeClaims (`spec.template.spec.volumes[*].persistentVolumeClaim`)**
    - **ConfigMaps (`spec.template.spec.volumes[*].configMap`)**
    - **Secrets (`spec.template.spec.volumes[*].secret`)**
    - **ServiceAccounts (`spec.template.spec.volumes[*].serviceAccount`)**
    - **MemoryDump (`spec.template.spec.volumes[*].memoryDump`)**
  - **AccessCredentials**
    - **SSH Secrets (`spec.template.spec.accessCredentials.sshPublicKey.source.secret`)**
    - **User Password Secrets (`spec.template.spec.accessCredentials.userPassword.source.secret`)**

**Backend Storage PVC**  
Identified by the persistent state PVC label.

**Other Resources:**
- Should optional objects such as `VMExports` or `VMSnapshots` be considered?

## **Alternatives**

## Alternatives Considered

### Flat Dependency List

An alternative was to return a **flat list** of dependent objects without hierarchical relationships.

Example schema:

```go
type ObjectGraphNode struct {
    ObjectReference k8sv1.TypedObjectReference `json:"objectReference"`
    Labels          map[string]string          `json:"labels,omitempty"`
}
```

Example output:

```json
{
  "items": [
    {
      "objectReference": {
        "apiGroup": "kubevirt.io",
        "kind": "VirtualMachineInstance",
        "name": "vm1",
        "namespace": "default"
      }
    },
    {
      "objectReference": {
        "apiGroup": "",
        "kind": "Pod",
        "name": "virt-launcher-vm1",
        "namespace": "default"
      }
    },
    {
      "objectReference": {
        "apiGroup": "cdi.kubevirt.io",
        "kind": "DataVolume",
        "name": "dv1",
        "namespace": "default"
      }
    }
  ]
}
```

We chose the hierarchical representation because:

- Dependency relationships between objects are meaningful and should be explicit.
- Migration, backup, and restore processes often require processing objects in dependency order.
- Hierarchical graphs allow easier extensibility (for example, marking optional nodes).
- Flat lists can become complex as the number of dependencies grows.

### Other Considerations

1. **Naming:** Is `ObjectGraph` descriptive enough even if we are returning a flat list of objects? Would `DependencyList` be more accurate?
2. **Extensibility:** How can we ensure the API is extensible for future enhancements? Should the API be made more intelligent (with fields such as `Optional`) or just rely on labels for extensibility?
3. **Filtering:** Should the user handle filtering, or should we allow some kind of filtering in the ObjectGraph request?

## **Scalability**

Each ObjectGraph is scoped to a single VM or VMI, reducing overall load. The graph is generated on-demand by the virt-api server.

## **Update/Rollback Compatibility**

- Non-intrusive addition via subresources.
- Safe to introduce and disable per version.
- No changes to existing APIs or objects.
- No changes to existing VM/VMI specs.
  
## **Functional Testing Approach**

- Unit tests to validate graph generation logic
- E2E tests to ensure the API behaves as expected

## **Implementation Phases**

1. Implement ObjectGraph types in the API.
2. Implement virt-api logic to construct and return graphs.
3. Expose endpoints under VM and VMI subresources.
4. Add virtctl integration for user access.

## **Feature Lifecycle Phases**

- **Alpha:**
  - Initial implementation with basic functionality.
- **Beta:**
  - Include network resources for cross-cluster live migration:
    - NetworkAttachmentDefinitions
    - IPAMClaims
  - Adapt external repos (e.g., Velero plugin) to use the ObjectGraph API.
- **GA:**
  - Improvements based on feedback.

