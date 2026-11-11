---
title: GPU Offload Provenance
description: Upstream snapshot record, licensing permission, and attribution
ms.date: 2026-11-11
ms.topic: reference
---

Record of the upstream snapshot, licensing permission, and attribution for the Xavier
GPU-offload port.

## Upstream Snapshot

| Field         | Value                                    |
|---------------|------------------------------------------|
| Repository    | `microsoft/xavier`                       |
| Pinned commit | `c914e88c4d65d5d99e9546c01a3c4def0ead39c5` |

Key upstream files referenced during the port and their recorded disposition:

| Upstream path                           | Disposition                                |
|-----------------------------------------|--------------------------------------------|
| platform/offload/controller/mutate.py   | Imported as reference; adapted             |
| platform/offload/controller/client.yaml | Imported as example; retained              |
| platform/offload/*/helm/                | Imported as reference for schema           |
| platform/offload/nodeagent/*            | Examined; intentionally excluded           |
| platform/offload/*/dockerbuild-*        | Reviewed for patterns; Podman used instead |
| platform/offload/*/dockerrun-*          | Reviewed for patterns; Podman used instead |

These paths and dispositions are retained from the original porting record.

## Licensing and Permissions

The original porting record states that licensing permission for reuse of the pinned
upstream snapshot has been obtained.

Implementation teams must confirm and retain:

1. The concrete location and form of the licensing permission audit evidence.
2. Any required attribution obligations.
3. The storage location in internal compliance records.

The permission statement does not replace the underlying licence or permission
documentation. Do not rely on this file as licence text.

## Repository and Third-Party Licensing

The repository's root `LICENSE` file contains the MIT License. Repository-authored
GPU-offload material is covered by that licence unless a file or applicable third-party
notice specifies otherwise.

The SO-101 example at `examples/so101-real-hardware/` pins the LeRobot Python package
to version `0.6.1`, as recorded in its `.lerobot-version`, `pyproject.toml`, and
`uv.lock` files.

LeRobot is a third-party dependency. Its applicable licence and attribution obligations
must be determined from the LeRobot distribution and its licence notices. The
repository's MIT licence does not replace or relicense third-party dependencies.

There is no separate `ros2_bridge/` directory in the tracked SO-101 example. Therefore,
this provenance record does not identify a ROS 2 bridge there as an Apache-2.0
exception.

## Image Names

The prebuilt offloading-engine images consumed by this domain:

- `xavier-mutate`: mutating admission webhook
- `pyremote`: server-stage inference engine

Always reference these through a parameterized registry
(`{{ .Values.image.registry }}/...`) rather than hard-coded registry names.

## Actions Taken During Porting

1. Imported controller design and configuration fields as reference.
2. Adapted to repository security and operational policies.
3. Excluded node-agent, hostPath, privileged mounts, and runtime-install scripts.
4. Retained socket RPC framing, heartbeat, cancellation, and direct/directqueue patterns.
5. Documented intended deviations and validation plans in `XAVIER-PORTING.md`.
