# Overview
Implement support for IBM Z Secure Execution VMs in kubevirt

## Motivation
To enable customers to deploy secure workloads on their IBM Z machines using kubevirt.

## Goals
To enable kubevirt to run Secure Execution VMs on s390x

## Non Goals
Changing how existing VMs run.

## Definition of Users
- IBM Z users wanting to secure their new VMs using Secure Execution
- IBM Z users wanting to migrate existing Secure Execution VMs to kubevirt

## User Stories
(list of user stories this design aims to solve)

## Repos
https://github.com/kubevirt/kubevirt
https://github.com/kubevirt/api

# Design
For implementing this feature, the api would need to be expanded to have a Secure Execution option under Launch Security next to AMD SEV.
Similiar to SEV it would be first behind a Feature Gate.

Afterwards kubevirt would need to be expanded to handle setting the correct Launch Security type for Secure Execution.
To achieve this, the virt-launcher pod would currently need to be run as privileged for Secure Execution VMs, as Libvirt checks for `/sys/firmware/uv` when verifying that the Host supports Secure Execution VMs. Unfortunately `/sys/firmware` is intentionally empty in unprivileged pods and kubernetes prevents mounting anything under this path. There are potentially Feature Gates in kubelet to allow this, however i have not yet been able to get this to work. From what i gathered you would need to at least enable `ProcMountType` and `UserNamespacesSupport` to be able to launch a Secure Execution VM inside of an unprivileged pod.
To avoid using privileged pods, the first implementation should explicitly not add launchSecurity to the xml file, but instead do the needed changes in the xml directly. This ensures that libvirt does not check if Secure Execution is supported, enabling virt-launcher to run unprivileged. The drawback of this is of course, that kubevirt needs to ensure all devices use iommu, otherwise they will not be visible to the VM, leading to potentially hard to debug, unkown behaviour and errors.

## API Examples
```
apiVersion: kubevirt.io/v1
kind: VirtualMachineInstance
metadata:
  ...
spec:
  domain:
    launchSecurity:
      secureExecution: {}
  ...
```

## Scalability
It should scale like normal VMs.
The only restriction would be for Secure Execution VMs, as they ´can't run on IBM Z machines that they have not been encrypted with.
To move a Secure Execution VM to a new IBM Z machine, it's public key would first need to be added to the keys used to encrypt the kernel+initramfs+parmfile.

## Security
This design improves Security by allowing users to protect their IBM Z Workloads with Secure Execution.

## Update/Rollback Compatibility
As the feature would need to be enabled in a VM before it takes effect, it would not affect existing or new VMs that do not use it.

Rolling back to a version without Secure Execution would prevent Secure Execution VMs from working.

## Functional Testing Approach
- As much as possible should the feature be tested in unit-tests
- Upstream tests would be difficult and likely require additional hardware, as Secure Execution does not work with nested virtualization and requires being run on LPAR (Bare Metal).
