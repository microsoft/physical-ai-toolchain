---
title: Xavier Porting Notes
description: Decisions and deviations during GPU-offload port
ms.date: 2026-08-10
ms.topic: guidance
---

Engineering decisions taken while porting the pinned upstream Xavier snapshot
(commit c914e88c4d65d5d99e9546c01a3c4def0ead39c5) into this repository's
`gpu-offload` domain.

Licensing permission for reuse has been obtained and is recorded in
[PROVENANCE.md](./PROVENANCE.md).

## Imported vs Excluded Files

| Action   | Upstream path                           | Disposition                           | Why not as-is                                                        |
|----------|-----------------------------------------|---------------------------------------|----------------------------------------------------------------------|
| Imported | platform/offload/controller/mutate.py   | Adapted control-plane mutation logic  | Core controller model remains compatible                             |
| Imported | platform/offload/controller/client.yaml | Retained example ConfigMap contract   | Configuration shape remains compatible                               |
| Imported | platform/offload/*/helm/                | Adapted Helm chart patterns           | Upstream security and runtime assumptions required changes           |
| Excluded | platform/offload/nodeagent/*            | Node-agent removed                    | Privileged node-wide process created excessive blast radius          |
| Excluded | hostPath mount patterns                 | Replaced with SDK-in-image            | Host filesystem coupling violated workload isolation and portability |
| Excluded | Docker socket usage                     | Replaced with Podman-native workflows | Docker daemon access was privileged and unavailable by default       |
| Excluded | Azure-specific assumptions              | Replaced with vendor-neutral behavior | Runtime must work on non-Azure Kubernetes clusters                   |

## Unchanged Architecture

The following core patterns are retained from upstream:

1. Controller-based mutation model creating server deployments
2. `remote.yaml`-style ConfigMap pattern for offload spec
3. Socket RPC framing, heartbeat, cancellation, direct/directqueue patterns
4. Explicit decorators for remoteable call sites

## Deviation 1: MessagePack Codec

**Decision**: Replace `pickle` wire format with versioned MessagePack + adapters

### Why MessagePack Was Necessary

Upstream deserializes network RPC payloads with Python `pickle`. A crafted pickle can
execute arbitrary code during deserialization, before application validation runs. The
offload boundary connects independently built client and server images over a cluster
network, so treating every payload producer as fully trusted would make compromise of
one workload a remote-code-execution path into another. MessagePack preserves the
existing byte-oriented socket transport while restricting decoding to data types and
explicitly registered adapters.

**Implementation**: Versioned MessagePack envelope with explicit adapters. Unknown
types fail serialization and remote exceptions become inert error descriptors.

**Compatibility**: The binary protocol is backward-incompatible with unmodified
upstream servers. Client and server images must use this runtime.

**Validation**: Unit tests cover built-in types, explicit adapters, resource limits,
unknown-type rejection, and safe remote errors.

**Status**: Implemented

## Deviation 2: Remove Node-Agent and HostPath Mounts

**Decision**: No node-agent, hostPath, or privileged defaults. SDK-in-image or
explicit opt-in via admission controller.

### Why Node-Agent Removal Was Necessary

Upstream delivers runtime code through a privileged node agent and hostPath mounts.
That design crosses the pod isolation boundary: the agent can affect every workload on
the node, and mounted host paths expose node files to containers. It also couples
workloads to one node filesystem layout and runtime installation. This repository
requires namespaced, non-privileged workloads that can move between conforming
Kubernetes clusters, so the SDK must be included in each application and server image.

**Implementation**: Build the SDK into application and server images. The controller
copies compatible workload volumes but rejects hostPath volumes.

**Compatibility**: Deployments relying on hostPath must rebuild with the SDK included.
There is no privileged compatibility mode.

**Validation**: Image-based integration tests for SDK-in-image; podman-run local
demo that shows one-command execution.

**Status**: Implemented

## Deviation 3: No Runtime Package Installation

**Decision**: Move runtime dependencies into server image at build time.

### Why Build-Time Dependencies Were Necessary

Upstream installs operating-system and Python packages when the server container
starts. Runtime installation requires a writable root filesystem, often requires root,
and depends on external package services being reachable. It also allows identical
image digests to run different dependency versions at different times. Installing
pinned dependencies during the image build makes startup deterministic and permits the
server to run as non-root with a read-only root filesystem and no package-network access.

**Implementation**: Install Python modules in images at build time. Runtime startup
does not invoke package managers.

**Compatibility**: Operators must use provided images or rebuild with SDK included.

**Validation**: Generated server Deployments run `python3 -m remoter.autoremote` and
set `WRITE_READY_MESSAGE` for the server readiness probe.

**Status**: Implemented

## Deviation 4: Cluster-Wide Admission with Workload Opt-In

**Decision**: The cluster-wide webhook selects workloads with label `xavier: "true"`;
the controller requires the `xavierconfig` annotation.

### Why Explicit Workload Opt-In Was Necessary

A cluster-wide admission webhook receives creation requests for every resource covered
by its rules. Relying only on controller-side annotation checks sends unrelated
workloads through the webhook, increases availability impact when the controller is
unhealthy, and makes accidental mutation harder to prevent. The API server can apply a
label selector before invoking the webhook, while the annotation supplies the detailed
configuration. Requiring both signals makes selection explicit and limits the
admission path to workloads whose owners intentionally enabled offloading.

**Implementation**: Admission handles Pods, Deployments, Jobs, and StatefulSets.
Background reconciliation creates shared and per-client server Deployments.

**Compatibility**: Existing clusters running automatic mutation must explicitly
annotate workloads to continue; this is a safer default.

**Validation**: Controller tests cover mutation, pass-through, malformed configuration,
server Deployment creation, per-client stages, and reconciliation.

**Status**: Implemented

## Deviation 5: Podman-Native One-Command Local Goal

**Decision**: Support `./scripts/run-local-server.sh --image <local-image>` using
Podman for linux/amd64 images.

### Why Podman Was Necessary

Upstream local scripts assume Docker, Docker Buildx, and access to a long-running Docker
daemon. Those are not repository prerequisites, and mounting a Docker socket grants
daemon-equivalent host control. Podman builds and runs images without a long-running
daemon and supports the required linux/amd64 target on macOS arm64. A Podman-native
workflow therefore preserves local cross-platform development without adding Docker or
a privileged daemon as a prerequisite.

**Implementation**: Provide `scripts/` with `podman build` and `podman run` flows.
CI provides cross-builds for linux/amd64.

**Compatibility**: Local devs must have Podman or build for their platform.

**Validation**: Local smoke scripts run server and client against each other with
MessagePack transport.

**Status**: Pending

## Deviation 6: Layered Optional Security Model

**Decision**: Harden the admission controller and reject privileged server settings.

### Why Controller Hardening Was Necessary

The admission controller is cluster-wide and can create or modify workloads, so a
controller compromise has a larger impact than compromise of a namespaced application.
Upstream's service-account-only boundary did not constrain Linux capabilities, root
execution, filesystem writes, host namespace inheritance, or broad Kubernetes API
permissions. The port applies least-privilege RBAC, a non-root read-only controller
container, dropped capabilities, and validation that rejects privileged server
contexts. These controls reduce both control-plane blast radius and privilege inherited
by generated server Deployments.

**Implementation**: The chart runs the controller as non-root with dropped capabilities
and a read-only root filesystem. Configuration validation rejects privileged or root
server contexts. hostPath and host namespace propagation are unsupported.

**Compatibility**: Workloads that require privileged server containers are rejected.

**Validation**: Security scan checklist and test vectors.

**Status**: Implemented

## Configuration Fields Referenced

The following fields are documented in upstream code and examples:

| Field                | Type    | Implemented | Usage                      |
|----------------------|---------|-------------|----------------------------|
| `serverimage`        | string  | Implemented | Server deployment image    |
| `serverreplicas`     | integer | Implemented | Deployment replica count   |
| `nodeSelector`       | map     | Implemented | Server pod node selection  |
| `securityContext`    | object  | Implemented | Container security context |
| `env`                | list    | Implemented | Environment variables      |
| `noserverdeployment` | boolean | Implemented | Skip deployment creation   |
| `remoteablecm`       | string  | Implemented | ConfigMap name             |
| `remoteableconts`    | list    | Implemented | Container filter list      |

These are documented in [remote-spec-schema.md](./specifications/remote-spec-schema.md)

## Validation and Tests

Current test coverage:

1. Unit tests: config parsing and container filtering
2. Integration tests: mutation behavior on annotated workloads
3. Security checks: no hostPath volumes or privilege escalation
4. Server readiness probe verification

Planned additions:

1. End-to-end admission test in a local Kubernetes cluster
2. Application-specific remote invocation smoke test

## Implementation Checkpoints

Teams implementing deviations must confirm:

1. `XAVIER_LIB_PATH` injection mechanism for SDK-in-image (build-time vs runtime)
2. `PODMAN` target platform images and CI cross-builds for linux/amd64
3. Admission opt-in controller implementation path (webhook vs watch pattern)
4. License permission audit evidence and storage location

## Upstream Contribution Candidates

1. Versioned MessagePack envelope + adapters PR to upstream `remoter`
2. SDK-in-image build guidance
3. Test harness and `client.yaml` examples for SDK-in-image and Podman-run flows

## Next Steps

1. Generate committed dependency lockfiles when package-host connectivity is available
2. Run controller and runtime test suites from the frozen lockfiles
3. Add Podman build/run scripts and CI cross-builds
4. Add an end-to-end admission and remote invocation test

> [!NOTE]
> For operator runbooks and end-user guides, see [README.md](./README.md) and
> [specifications/](./specifications/).
