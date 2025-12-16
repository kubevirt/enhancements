# VEP 141: Target-Side Pre-Migration Hooks for Domain XML Modification

## Release Signoff Checklist

- [X] (R) Enhancement issue created, which links to VEP dir in [kubevirt/enhancements] (not the initial VEP PR)
- [ ] (R) Target version is explicitly mentioned and approved
- [ ] (R) Graduation criteria filled

## Overview

Currently, in certain scenarios, KubeVirt modifies the domain on the source virt-launcher pod based on information reported by the virt-handler 
running on the target node prior to sending it to the target.
This complicates the migration process and necessitates additional API changes for every transformed field.
This proposal introduces a target-side pre-migration hook system that performs all mutations on the target launcher pod before the migration start.

## Motivation

Currently, KubeVirt modifies the domain XML on the source during live migration via `migratableDomXML()`. This approach has limitations:

1. **Upgrade**
   - In KubeVirt, upgrades are performed via live migration. Since domain changes occur on the source (which runs an older version),
     it cannot anticipate changes required by the newer target.
   - New XML transformation logic requires two migrations: old source → new target, then new source → new target.  
   - Users might not even know a second migration is required, and no automation exists to trigger it during upgrades.

2. **Information Gathering Constraints**  
   - CPUSet and topology information are currently added to the VMI status, filling our API with implementation details.  
   - This approach does not scale as more target-specific information is added through the vmi.status API.
   - By shifting domain mutation to the target virt-launcher, we can simplify the migration workflow and deprecate some of these VMI status fields.

3. **Blocked VEPs**
   Mutating the domain on the target will enable new features and upgrade paths for existing features, such as:
   - vGPU live migration [(VEP 109)](https://github.com/kubevirt/enhancements/issues/110)  
   - Secondary network upgrades [(VEP 111)](https://github.com/kubevirt/enhancements/issues/111)

## Goals

- Streamline the overall migration process.
- Mitigate the growth of the VMI.Status field.
- Enable migration capabilities for new features.
- Enable upgrade paths for existing features.

## Non Goals

- Rewriting migration architecture
- Introducing new API fields
- Changing user-visible migration behavior

## Definition of Users

- KubeVirt maintainers
- Contributors implementing migration-related features

## User Stories

- As a KubeVirt contributor, I want all XML mutations to happen on the target virt-launcher pod via hooks, so 
migration-related features can be implemented safely.
- As a KubeVirt user, I want live migrations to work reliably across upgrades, so VMs maintain correct state without 
extra steps.
- As a KubeVirt maintainer, I want to avoid exposing internal implementation details through vmi.status, keeping the API 
clean and maintainable.

## Repos

- `kubevirt/kubevirt`

## Design

Libvirt hooks allow scripts to run at key points in a VM’s lifecycle, such as start, stop, or migration. 
KubeVirt uses them [to mark](https://github.com/kubevirt/kubevirt/blob/release-1.7/cmd/virt-launcher/qemu) 
which backend storage PVC contains the correct persistent state. 
As part of this design, we want to explore using these hooks to move pre-migration XML 
modifications to the target virt-launcher pod.
For more details, see [Libvirt Hooks](https://libvirt.org/hooks.html#etc-libvirt-hooks-qemu)

The implementation consists of two main components:
1. PreMigration Hook Server
A lightweight server that listens on a Unix domain socket in the target virt-launcher pod. 
It accepts domain XML, applies all registered hook functions sequentially, and returns the modified XML.
2. Libvirt Hook Client
A Go binary that replaces the default qemu hook script. 
When libvirt invokes it during migrate begin, the client forwards domain XML to the hook server and returns 
the modified XML.


### Flow
1. Target virt-launcher starts the PreMigration Hook Server on a Unix domain socket in the 
prepareMigrationTarget function. This is triggered by Libvirt when the source initiates the migration.
2. The default qemu hook script is replaced with the libvirt hook client.
3. Libvirt executes the hook during migrate begin and passes domain XML to stdin.
4. The client connects to the hook server socket and sends the XML.
5. The hook server applies all registered hooks.
6. Modified XML is returned to the client via the socket.
7. The client writes the final XML to stdout.
8. Libvirt defines the domain using the modified XML.
9. The hook server shuts down

### Deprecation Plan

Fields that become unnecessary:

- `vmi.Status.MigrationState.TargetCPUSet`
- `vmi.Status.MigrationState.TargetNodeTopology`

Plan:
1. Stop using these fields when modifying the xml and mark them as deprecated.
2. Continue populating for one release for compatibility with older launcher images.
3. Remove completely in the following release.

### Alternatives

#### Target-Initiated Migration

Avoids hooks but requires:

- Complete rewrite of migration architecture  
- Supporting both source- and target-initiated paths  
- Reworking live migration monitoring from source → target  

Too complex.

## Scalability

It should be a scalable and extendable hook system that allows adding new hooks.

## Update / Compatibility

- No API changes  
- should be safe during upgrade since the final XML should be the same as before

## Functional Testing

- Existing functional tests already cover XML mutation behavior

## Unit Testing

- Convert existing unit tests to fit the new structure
- Place tests related to XML transformation into the hook packages

## Future Plans

- Move source-side logic (NUMA, disk paths, etc.) to hooks  
- Remove deprecated fields population after one release  
- Allow additional VEPs (e.g., VEP 109, 111) to use the same hook system  

## Feature lifecycle Phases

### Alpha
- Implement the target-side hook server and libvirt hook client, protected by the LibvirtHooksServerAndClient feature gate.
- Add unit tests for hooks.

### Beta
- Enable feature gate by default.
- Migrate all existing XML modifications from source to target-side hooks (dedicated CPU pinning, etc.).

### GA

### Post-GA
- Remove the qemu hook shell script and place the libvirt hook client binary directly in the launcher container image.
- Remove all source-side XML modification code in virt-launcher, but continue supporting old virt-launchers in virt-handler.
- Deprecate vmi.status fields that are no longer needed because data can now be fetched directly from the target.