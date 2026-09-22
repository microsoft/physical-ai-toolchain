---
title: remote.yaml Schema
description: Schema and examples for remote.yaml offload specification
ms.date: 2026-09-22
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

> [!WARNING]
> Treat write access to `remote.yaml` as equivalent to arbitrary Deployment
> creation in its namespace. The controller assumes ConfigMap editors are trusted
> deployment operators authorized to select images and use the workload's
> service account, runtime class, image pull secrets, and permitted mounts. Do
> not grant this access to less-trusted users or automation.

## Top-Level Keys

`remote.yaml` declares six optional top-level keys. At least one of `serverstages`,
`remoteclasses`, or `remotefuncs` must be present.

| Key              | Type             | Required | Purpose                                      |
|------------------|------------------|----------|----------------------------------------------|
| `serverstages`   | list of objects  | Yes      | Define named GPU worker pods                 |
| `remoteclasses`  | list of mappings | No       | Classes whose methods execute in stages      |
| `remotefuncs`    | list of mappings | No       | Functions that execute in stages             |
| `allowedmodules` | list of strings  | No       | Permit additional runtime module imports     |
| `encryption`     | boolean          | No       | Encrypt and authenticate RPC payloads; default `true` |
| `networkPolicy`  | boolean          | No       | Restrict server ingress to the namespace     |

## networkPolicy

`networkPolicy` defaults to `true`. The controller creates one ingress
NetworkPolicy for each generated server Deployment. The policy selects only the
server pods and permits TCP and UDP traffic to `REMOTERPORT` from pods in the
same namespace.

The client workload is not selected by these policies, so its inbound and
outbound connectivity is unchanged.

```yaml
networkPolicy: false
serverstages:
  - name: gpu
    perclient: false
```

Set `networkPolicy: false` only when the cluster CNI does not enforce Kubernetes
NetworkPolicy or when another network-security layer manages server ingress.
NetworkPolicy enforcement is additive and depends on the installed CNI.

## encryption

`encryption` defaults to `true`. AES-GCM encrypts and authenticates RPC data
messages. The controller generates one random 32-byte key per opted-in workload,
stores it in a controller-managed Kubernetes Secret, and mounts the same key
read-only into the client containers and every generated server stage.

```yaml
encryption: true
serverstages:
  - name: gpu
    perclient: false
```

The key value is generated during reconciliation and does not appear in Helm
values, workload manifests, ConfigMaps, command-line arguments, or environment
variables. The manifest contains only the generated Secret name and the
`REMOTER_KEY_FILE` path.

`encryption` is a top-level workload setting because the runtime uses one
transport key for all server stages. Configure it before creating the workload;
changing the ConfigMap does not mutate an existing client pod template.

Set `encryption: false` only for trusted development environments that require
plaintext RPC. NetworkPolicy isolation does not authenticate same-namespace
peers and does not replace transport authentication.

## allowedmodules

The runtime imports only modules present in its exact-match allowlist. Modules
referenced by `remoteclasses`, `remotefuncs`, and `stubs` are added automatically.
Use `allowedmodules` for modules that RPC reconstruction requires but the remote
symbol configuration does not reference directly.

The runtime builds an exact callable policy after expanding
`servercallablemethods`, applying `serverdeniedmethods` and `noremotefuncs`,
and resolving stub mappings. It freezes the policy before opening the RPC
listener. Each request must provide matching `key`, module, class, and function
fields, and the resulting canonical identity must be in the frozen policy.
Rejection occurs before argument rehydration, module import, attribute lookup,
or class construction.

Standalone deployments build the same policy from the local `remote.yaml`.
They do not require Kubernetes or the controller. Configure all remote
functions and classes before starting the runtime; authorization cannot expand
after startup. The deprecated `--allowall` runtime option is rejected because
it bypasses exact callable authorization.

```yaml
allowedmodules:
  - mypackage.shared_types
```

Parent package names and similarly prefixed modules are not implicitly allowed.

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

| Field                   | Type            | Required | Meaning                                                        |
|-------------------------|-----------------|----------|----------------------------------------------------------------|
| _(map key)_             | string          | Yes      | Class path in `module.path/ClassName` form                     |
| `remoteloc`             | string          | Yes      | Target `serverstages` entry `name`                             |
| `servercallablemethods` | list of strings | No       | Exact names or glob patterns accepted as inbound server calls  |
| `serverdeniedmethods`   | list of strings | No       | Exact names or glob patterns removed from the server allowlist |
| `noremotefuncs`         | list of strings | No       | Method names that remain local in the calling process          |

Method patterns use case-sensitive, full-name shell matching. The runtime
expands patterns against concrete and inherited methods during startup, logs
wildcard expansions, and stores only exact canonical callable keys. A pattern
that matches no methods fails startup. Denials take precedence over allows.

Use exact names for production configurations. Wildcards also authorize methods
introduced by future dependency versions when those names match the configured
pattern.

**Example:**

```yaml
remoteclasses:
  - "mypackage.policy/Policy":
      remoteloc: gpu
      servercallablemethods:
        - __init__
        - start
        - get_*
        - stop
      serverdeniedmethods:
        - get_debug_*
```

`servercallablemethods: ["*"]` accepts every discovered method, including
inherited and private methods. Use this only when the complete class API is an
intentional RPC surface.

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
| `remoteableenv`      | list    | Implemented | Client env var names allowed onto server  |
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
