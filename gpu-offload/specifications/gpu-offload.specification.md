---
title: GPU Offload Specification
description: Opt-in contract and behavior specification
ms.date: 2026-08-10
ms.topic: specification
---

Authoritative specification for the opt-in GPU-offloading contract a workload
consumes and the mutating controller's observable behavior.

## Opt-in Contract

A workload opts in through three required signals. Omitting any one disables
offloading for that workload.

| Signal                    | Location          | Value               | Purpose               |
|---------------------------|-------------------|---------------------|-----------------------|
| Label `xavier`            | Workload metadata | `"true"`            | Select for mutation   |
| Annotation `xavierconfig` | Workload metadata | ConfigMap name      | Reference remote.yaml |
| Env `REMOTERPORT`         | Main container    | Port (e.g. `30001`) | Server endpoint       |

The annotation value points to a ConfigMap containing the `remote.yaml` offload
specification. See [remote-spec-schema.md](./remote-spec-schema.md) for schema.

## Controller Behavior

The mutating webhook watches Pods, Deployments, Jobs, and StatefulSets.

When a workload carries all three opt-in signals:

1. The controller adds a ConfigMap volume mount (read-only at `/xavierconfig`)
2. The controller injects `REMOTER_CONFIG`, `CONFIGFROMKUBE`, `XAVIER_CONTAINER`,
   and downward API identity fields
3. The reconciler creates server Deployments for configured server stages
4. Generated server containers receive a readiness probe for `/ready.txt`

The controller does not:

- Add hostPath volumes, host namespaces, or privileged security contexts
- Modify the application container entrypoint or command
- Add a readiness probe to application containers

**Container filtering:**

If `xavierconfig` includes `remoteableconts` list, only those named containers
are mutated; others are left unchanged.

## Configuration Fields

The `remote.yaml` ConfigMap in `data.remote.yaml` may include these fields:

| Field                | Type                  | Implemented | Notes                                                 |
|----------------------|-----------------------|-------------|-------------------------------------------------------|
| `serverimage`        | string                | Implemented | Server container image; defaults to application image |
| `serverreplicas`     | integer               | Implemented | Server Deployment replica count                       |
| `nodeSelector`       | map[string]string     | Implemented | Server pod node selection                             |
| `securityContext`    | object                | Implemented | Validated server container security context           |
| `env`                | list of name/value    | Implemented | Environment merged into server container              |
| `noserverdeployment` | boolean               | Implemented | Skips server Deployment creation                      |
| `serverstages`       | list of stage objects | Implemented | Shared and per-client server stages                   |
| `remoteablecm`       | string                | Implemented | ConfigMap name (required by controller)               |
| `remoteableconts`    | list of strings       | Implemented | Container names to mutate (optional filter)           |

## Behavior Guarantees

1. Mutation is opt-in: controller only acts on workloads with all three signals
2. Immutable remote.yaml: ConfigMap mounted read-only
3. No privilege escalation: controller never adds privileged contexts
4. Atomic per-workload: all containers in a workload see consistent mutation
5. Idempotent: re-applying the same workload manifest produces same result

## Validation

Test coverage:

1. Unit tests for config parsing and workload filtering
2. Integration test: annotated Pod with ConfigMap produces expected mutations
3. Security review: generated manifests have no privilege escalation
4. Edge cases: malformed YAML, missing ConfigMaps, unrecognized container names

## Unsupported / Deferred Features

The following are planned but NOT currently implemented:

- `hostPath`-driven SDK delivery and node-agent deployment
- Docker socket mounting and runtime package installation
- Host network, PID, or IPC namespace propagation
- Legacy pickle compatibility

These are documented in [XAVIER-PORTING.md](../XAVIER-PORTING.md) as deviations
or planned work.

## Tier Model

GPU offloading is a T3–T4 capability (single-site to multi-site deployment
topology). It is NOT T5 (fleet intelligence). See
[docs/design/tier-model.md](../../docs/design/tier-model.md).
