---
title: remote.yaml Schema
description: Schema and examples for remote.yaml offload specification
ms.date: 2026-08-10
ms.topic: reference
---

Specification for the `remote.yaml` ConfigMap consumed by the offload controller.
The ConfigMap is referenced by annotation `xavierconfig` and contains this schema
in the key `data.remote.yaml`.

## Overview

Transparent GPU offloading runs a lightweight control container next to the robot
while heavy inference executes in a GPU server-stage pod. Fully-qualified Python
classes and functions named in `remote.yaml` execute in the server-stage pod with
no application code changes.

The control container calls its policy as if it ran locally; the platform
intercepts named symbols and routes execution to the GPU pod. This separation
keeps the robot container lightweight while GPU capacity is reserved for inference.

## Top-Level Keys

`remote.yaml` declares three optional top-level keys. At least one of `serverstages`,
`remoteclasses`, or `remotefuncs` must be present.

| Key             | Type             | Required | Purpose                                 |
|-----------------|------------------|----------|-----------------------------------------|
| `serverstages`  | list of objects  | Yes      | Define named GPU worker pods            |
| `remoteclasses` | list of mappings | No       | Classes whose methods execute in stages |
| `remotefuncs`   | list of mappings | No       | Functions that execute in stages        |

## serverstages

A **stage** is a GPU worker pod hosting offloaded classes and functions.

| Field       | Type    | Required | Meaning                                     |
|-------------|---------|----------|---------------------------------------------|
| `name`      | string  | Yes      | Stage identifier (empty string is default)  |
| `perclient` | boolean | Yes      | `false`: shared pod; `true`: per-client pod |
| `resources` | map     | Yes      | Kubernetes resource requests/limits         |

**Example:**

```yaml
serverstages:
  - name: gpu
    perclient: false
    resources:
      limits:
        nvidia.com/gpu: 1
```

The `resources` map follows standard Kubernetes container resource shape. GPU
allocation is expressed under `resources.limits` with key `nvidia.com/gpu`.

## remoteclasses

Each entry is a single-key map: the key is a fully-qualified class path, value
selects the target stage. Method calls on instances execute transparently in the
stage pod.

| Field       | Type   | Required | Meaning                                    |
|-------------|--------|----------|--------------------------------------------|
| _(map key)_ | string | Yes      | Class path in `module.path/ClassName` form |
| `remoteloc` | string | Yes      | Target `serverstages` entry `name`         |

**Example:**

```yaml
remoteclasses:
  - "mypackage.policy/Policy":
      remoteloc: gpu
```

## remotefuncs

Each entry is a single-key map: the key is a fully-qualified function path, value
selects the target stage and declares instancing semantics. Calls execute
transparently in the stage pod.

| Field            | Type    | Required | Meaning                                                                                                      |
|------------------|---------|----------|--------------------------------------------------------------------------------------------------------------|
| _(map key)_      | string  | Yes      | Path in `module.path//function` or `module.path/Class/method` form                                           |
| `singleinstance` | boolean | No       | `true`: called once, then the first result is memoized and returned to every later caller; `false`: per-call |
| `remoteloc`      | string  | Yes      | Target `serverstages` entry `name`                                                                           |

Set `singleinstance: true` on functions that load heavy resources so the model
loads once in the stage pod and every call reuses it.

> [!WARNING]
> `singleinstance` memoizes the return value; the function body runs exactly once.
> Setting it on a per-call method such as `get_action` makes the stage return the
> first action forever, which reads as a policy that emits a constant output rather
> than as an error. Restrict it to calls whose result is the loaded resource.

**Example:**

```yaml
remotefuncs:
  - "mypackage.checkpoint/Checkpoint/load_model":
      singleinstance: true
      remoteloc: gpu
  - "mypackage.checkpoint/Checkpoint/get_action":
      remoteloc: gpu
```

## Minimal Valid Example

Complete, internally consistent `remote.yaml` with one shared GPU stage, one
offloaded class, and two offloaded functions (one with single instancing):

```yaml
serverstages:
  - name: gpu
    perclient: false
    resources:
      limits:
        nvidia.com/gpu: 1
remoteclasses:
  - "mypackage.policy/Policy":
      remoteloc: gpu
remotefuncs:
  - "mypackage.checkpoint/Checkpoint/load_model":
      singleinstance: true
      remoteloc: gpu
  - "mypackage.checkpoint/Checkpoint/get_action":
      remoteloc: gpu
```

## Controller ConfigMap Fields (Deprecated)

The following fields in the ConfigMap annotation are deprecated in favor of
per-stage configuration. They are currently used by the controller but should
not be relied upon in new code.

| Field                | Type    | Implemented | Purpose                                   |
|----------------------|---------|-------------|-------------------------------------------|
| `serverimage`        | string  | Implemented | Image for server deployment               |
| `serverreplicas`     | integer | Implemented | Number of deployment replicas             |
| `nodeSelector`       | map     | Implemented | Node selection for server pods            |
| `securityContext`    | object  | Implemented | Validated security context for containers |
| `env`                | list    | Implemented | Environment variables for containers      |
| `noserverdeployment` | boolean | Implemented | Skip server deployment creation           |
| `remoteablecm`       | string  | Implemented | ConfigMap name (required)                 |
| `remoteableconts`    | list    | Implemented | Container names to mutate (optional)      |

Future work will move configuration into the stage definitions above and deprecate
these top-level fields.

## Scheduling

Pod placement (node selectors, runtime class, tolerations) is configured on the
workload rather than in `remote.yaml`. The offload spec focuses on what to offload
and to which stage; infrastructure concerns remain workload-level.

## Validation

Implementers should validate:

1. Each `serverstages[*].name` is unique
2. Each `remoteloc` references a declared stage `name`
3. `resources.limits.nvidia.com/gpu` is a positive integer when GPU offloading
4. Class paths follow `module.path/ClassName`; function paths use `module.path//function` or `module.path/Class/method`
5. `singleinstance` is only used for functions/methods, not classes

## Workload Opt-In Contract

A workload opts in with three signals (see
[gpu-offload.specification.md](./gpu-offload.specification.md)):

1. Label `xavier: "true"`
2. Annotation `xavierconfig: <configmap-name>`
3. Env `REMOTERPORT` in main container

The ConfigMap holds this `remote.yaml` under key `data.remote.yaml`.
