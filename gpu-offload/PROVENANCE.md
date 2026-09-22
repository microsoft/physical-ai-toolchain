---
title: GPU Offload Provenance
description: Upstream snapshot record and licensing permission
ms.date: 2026-08-10
ms.topic: reference
---

Record of upstream snapshot, licensing permission, and attribution for the Xavier
GPU-offload porting.

## Upstream Snapshot

| Field         | Value                                    |
|---------------|------------------------------------------|
| Repository    | `microsoft/xavier`                       |
| Pinned commit | c914e88c4d65d5d99e9546c01a3c4def0ead39c5 |

Key upstream files referenced and their disposition:

| Upstream path                           | Disposition                                |
|-----------------------------------------|--------------------------------------------|
| platform/offload/controller/mutate.py   | Imported as reference; adapted             |
| platform/offload/controller/client.yaml | Imported as example; retained              |
| platform/offload/*/helm/                | Imported as reference for schema           |
| platform/offload/nodeagent/*            | Examined; intentionally excluded           |
| platform/offload/*/dockerbuild-*        | Reviewed for patterns; Podman used instead |
| platform/offload/*/dockerrun-*          | Reviewed for patterns; Podman used instead |

## Licensing and Permissions

Licensing permission for reuse of the pinned upstream snapshot has been obtained.
Implementation teams must confirm:

1. Concrete location and form of licensing permission audit evidence
2. Any required attribution obligations
3. Storage location in internal compliance records

This file records that permission exists; do NOT rely on this file as license text.

## Delivered Under MIT

The `gpu-offload` domain adapts a GPU-offloading reference architecture as documented
reference material (contract and deployment topology). It is delivered under this
repository's MIT license.

Exception: the ROS 2 bridge at `examples/so101-real-hardware/ros2_bridge/` derives
from the [LeRobot](https://github.com/huggingface/lerobot) project and remains under
its Apache 2.0 license, as recorded in those files' headers.

## Image Names

The prebuilt offloading-engine images consumed by this domain:

- `xavier-mutate`: mutating admission webhook
- `pyremote`: server-stage inference engine

Always reference these through a parameterized registry
(`{{ .Values.image.registry }}/...`) rather than hard-coded registry names.

## Actions Taken During Porting

1. Imported controller design and configuration fields as reference
2. Adapted to repository security and operational policies
3. Excluded node-agent, hostPath, privileged mounts, and runtime-install scripts
4. Retained socket RPC framing, heartbeat, cancellation, direct/directqueue patterns
5. Documented intended deviations and validation plan in XAVIER-PORTING.md
