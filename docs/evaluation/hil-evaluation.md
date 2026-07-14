---
title: Hardware-in-the-Loop Evaluation
description: Run CPU-only and independently no-command HiL validation on an Ubuntu K3s OSMO backend.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-13
ms.topic: how-to
---

Validate the HiL scheduling and policy boundary without physical motion. The implemented `ur10e-no-command` adapter uses deterministic six-axis observations and contains no robot command transport.

## Prerequisites

| Requirement                     | Purpose                                                  |
|---------------------------------|----------------------------------------------------------|
| Ubuntu K3s edge plane           | Runs OSMO workflows                                      |
| Online HiL OSMO backend         | Selects the edge pool                                    |
| Completed CPU smoke             | Proves CPU scheduling before HiL                         |
| Python 3.12                     | Runs local validation                                    |
| Protected isolated OSMO profile | Runs OSMO validation without changing the normal profile |

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

## Run Locally

Preview:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode local \
  --output-dir "$HOME/.local/share/physical-ai-toolchain/results/ur10e-no-command" \
  --config-preview
```

Run:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode local \
  --output-dir "$HOME/.local/share/physical-ai-toolchain/results/ur10e-no-command"
```

Inspect the result:

```bash
jq '{status, proposed_actions, applied_actions, negative_command_probe, command_transport}' \
  "$HOME/.local/share/physical-ai-toolchain/results/ur10e-no-command/summary.json"
```

Expected result:

```json
{
  "status": "passed",
  "proposed_actions": 10,
  "applied_actions": 0,
  "negative_command_probe": "passed",
  "command_transport": "none"
}
```

## Run Through OSMO

Authenticate an isolated profile from the VPN-connected operator workstation:

```bash
install -d -m 0700 "$OSMO_PROFILE_DIR"
XDG_CONFIG_HOME="$OSMO_PROFILE_DIR" osmo login "$OSMO_PRIVATE_URL" \
  --method dev \
  --username admin
```

Preview:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode osmo \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --workflow-name "${HIL_BACKEND_NAME}-ur10e-no-command" \
  --config-preview
```

Submit and wait for terminal success:

```bash
evaluation/hil/scripts/run-hil-evaluation.sh \
  --mode osmo \
  --pool "$HIL_POOL_NAME" \
  --service-url "$OSMO_PRIVATE_URL" \
  --osmo-config-dir "$OSMO_PROFILE_DIR" \
  --workflow-name "${HIL_BACKEND_NAME}-ur10e-no-command"
```

The script fails on workflow failure, cancellation, or a ten-minute timeout.

## Artifacts

Local execution writes:

| Artifact                 | Content                                                    |
|--------------------------|------------------------------------------------------------|
| `observations.jsonl`     | Sequence, timestamp, joint positions, and joint velocities |
| `proposed-actions.jsonl` | Proposed action, latency, applied flag, and rejection code |
| `safety-events.jsonl`    | Expected command-rejection events                          |
| `summary.json`           | Identities, counts, timing, hashes, and pass/fail result   |
| `manifest.json`          | Relative path, size, and SHA-256 for every result artifact |

Artifacts remain local. Add one approved upload owner and storage identity before transfer; do not enable ACSA and an application uploader for the same result tree.

## Physical Motion Gate

Physical motion is deferred. Implement it only after recording:

- Exact robot and controller identity
- Firmware and remote-control mode
- Command transport and exclusive command owner
- Policy image digest and model/configuration hashes
- Joint order, units, position, velocity, acceleration, jerk, force, workspace, duration, and action limits
- Camera and sensor identities plus calibration hashes
- Local operator identity and confirmation procedure
- Independently tested E-stop and manual protective-stop reset
- Safe-stop behavior for stale observations, sensor loss, deadline misses, pod termination, process failure, and operator abort

Do not add an unattended confirmation bypass or automatically clear robot faults.
