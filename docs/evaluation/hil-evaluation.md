---
title: Hardware-in-the-Loop Evaluation
description: Run CPU-only and independently no-command HiL validation on an Ubuntu K3s OSMO backend.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-23
ms.topic: how-to
---

Validate the HiL scheduling and policy boundary without physical motion. The implemented `ur10e-no-command` adapter uses deterministic six-axis observations and contains no robot command transport.

## Prerequisites

| Requirement             | Purpose                                                       |
|-------------------------|---------------------------------------------------------------|
| Ubuntu K3s edge plane   | Runs OSMO workflows                                           |
| Online HiL OSMO backend | Selects the edge pool                                         |
| Completed CPU smoke     | Proves CPU scheduling before HiL                              |
| Connection receipt      | Binds backend, pool, local node, K3s target, and OSMO profile |

Complete [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md) through the CPU gate first.

## Safety Contract

| Boundary              | Implemented behavior                                  |
|-----------------------|-------------------------------------------------------|
| Adapter               | `apply_action()` always raises `NO_COMMAND_TRANSPORT` |
| Applied actions       | Must remain zero                                      |
| Robot endpoint        | Not accepted by configuration                         |
| Host devices          | None                                                  |
| Host mounts           | None                                                  |
| Host network          | Disabled                                              |
| Privileged containers | Disabled                                              |
| Physical mode         | No CLI option or implementation exists                |

The deterministic policy proposes a small zero-seeking action for each fixture observation. The proposal exercises the same boundary a real policy would use without importing any command-capable robot library.

## Run CPU Scheduling Proof

Preview and run the CPU-only workflow:

```bash
data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt> \
  --config-preview

data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt>
```

The stage creates a unique workflow, requests zero GPUs, validates the result identity, and verifies the matching completed Pod ran on the owned local K3s node.

## Run No-Command Proof

Preview and run the independently non-commanding workload:

```bash
data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt> \
  --config-preview

data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt>
```

Expected remote result:

```json
{
  "status": "passed",
  "proposed_actions": 10,
  "applied_actions": 0,
  "negative_command_probe": "passed",
  "command_transport": "none",
  "rejection_code": "NO_COMMAND_TRANSPORT"
}
```

## Payload Assets

The public stage packages these tracked assets for the OSMO workflow:

| Artifact                                            | Content                               |
|-----------------------------------------------------|---------------------------------------|
| `evaluation/hil/no_command_runner.py`               | Focused standalone no-command runtime |
| `evaluation/hil/config/ur10e-no-command.json`       | Safety and fixture contract           |
| `evaluation/hil/config/ur10e-observations.jsonl`    | Deterministic observations            |
| `evaluation/hil/workflows/osmo/hil-evaluation.yaml` | CPU-only remote workflow              |

The stage also verifies the matching completed Pod ran on the owned local node. Stop when either proof fails. No physical-motion path follows this validation.

## Result Durability

The no-command workflow declares a unique output URI below the configured OSMO workflow-data endpoint and writes artifacts to OSMO's `{{output}}` directory. The local Arc K3s runtime controller uses the existing OSMO user-assigned managed identity through the `osmo-workflow` ServiceAccount.

The check requires a completed task upload timestamp, retrieves the declared URI through `osmo data download`, verifies the exact five-file result set and manifest integrity, confirms the no-command summary, and rejects credential-shaped content.

Static validation covers the workflow template, publisher/consumer contracts, and output verifier. Live Arc federation, managed-identity upload, and OSMO retrieval remain required environment validation. Do not add a SAS, storage key, direct uploader, or alternate output path.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
