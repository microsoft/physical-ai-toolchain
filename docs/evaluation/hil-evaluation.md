---
title: Hardware-in-the-Loop Evaluation
description: Run local target-policy-hardware HiL or T3 OSMO no-command validation.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-21
ms.topic: how-to
---

Evaluate deployment-representative policy hardware against Isaac simulation without commanding a physical robot. T0 uses two direct ROS 2 processes. T3 adds OSMO/K3s scheduling and retains the independently no-command gate.

## Evaluation Modes

| Mode | Simulator and policy topology | Infrastructure | Evidence |
|------|-------------------------------|----------------|----------|
| Offline replay | Recorded data and policy in one process | Local files | Prediction error against recorded actions |
| Software-in-the-loop | Isaac and policy on development compute | Local process | In-process simulator behavior |
| T0 HiL | Isaac workstation plus separate target policy host | ROS 2 and Docker | Bounded target-hardware control of simulation |
| T3 orchestrated HiL | OSMO workload on local K3s | T2 cloud plus T3 K3s/OSMO | Scheduling and no-command boundary |
| Real-robot execution | Policy commands a physical robot | Robot-specific | Physical task and safety outcomes |

Results from one mode do not substitute for another. T0 final acceptance requires distinct simulator and policy-host identities.

## T0 Prerequisites

| Requirement | Purpose |
|-------------|---------|
| x86_64 ROS 2 Jazzy policy host | Runs the checked-in CPU policy image |
| RTX simulator workstation | Runs the pinned Isaac Lab 2.3.2 image |
| Direct ROS 2 reachability | Connects the two commands without orchestration |
| Matching local artifacts | Ensures policy, task or scene, and I/O semantics agree |

Build the target image and run the IL or RL command pair from [evaluation/hil/README.md](../../evaluation/hil/README.md#-tier-0-quick-start). Use one `ROS_DOMAIN_ID`, `--run-id`, and exchange count on both hosts.

Use [Ubuntu Tier 0 HiL Validation](../recipes/tier-0-dev/ubuntu-hil-validation.md) to prepare the hosts and external artifacts, run both acceptance pairs, and collect the evidence needed to complete implementation.

### IL Inputs

| Input | Contract |
|-------|----------|
| ACT policy directory | LeRobot 0.6 policy, preprocessor, and postprocessor files |
| UR10E scene | User-supplied local USD |
| Scene configuration | Six canonical joints, RGB8 `480x848`, reset pose, 30 Hz cadence, timeout |

The target publishes six delta-radian actions. Isaac Sim applies them only to the configured simulated articulation. The public ALOHA checkpoint does not satisfy this UR10E contract.

### RL Inputs

| Input | Contract |
|-------|----------|
| `policy.pt` | JIT policy for `Isaac-Velocity-Rough-Anymal-C-v0` |
| `policy_io.json` | Exact ordered policy observation/action terms and control period |

The simulator and target exchange descriptor-ordered `Float32MultiArray` messages. `layout.data_offset` carries the one-based exchange sequence so a retained subscriber value cannot satisfy a later step.

## T0 Results

The simulator writes `summary.json` only after a successful bounded run. It includes policy and task identity, seed, requested/completed exchanges, framework outcomes, latency statistics, simulated and wall seconds, and real-time factor.

At invocation start the runner removes only the prior `summary.json`. A failed run re-raises the originating error and leaves no success summary.

## T2 Artifact Handoff

T0 consumes local paths. To evaluate an exact Azure Machine Learning model version at a directly reachable site, materialize it first:

```bash
az ml model download \
  --name <model-name> \
  --version <model-version> \
  --download-path <local-download-root> \
  --resource-group <resource-group> \
  --workspace-name <workspace-name>
```

Pass `<local-download-root>/<model-name>` or the exact downloaded artifact path to the unchanged T0 command. T0 does not contact Azure or pull the passive ACR carrier.

## T3 OSMO Gates

Complete [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md), then run:

```bash
data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt>

data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt>
```

The no-command adapter proposes deterministic actions and rejects all of them with `NO_COMMAND_TRANSPORT`. Expected applied actions remain zero. Physical motion is not implemented.

## Deferred Profiles

* Jetson GPU hosting requires one exact Jetson model, JetPack/L4T release, and compatible NVIDIA PyTorch artifact.
* ALOHA Isaac HiL requires a separate adapter, locally prepared scene, Isaac-collected data, and ACT retraining.
* Policy-promotion thresholds require task- and hardware-specific baseline runs.

## Related Documentation

* [HiL Runtime Components](../../evaluation/hil/README.md)
* [T0 Dev Recipe](../recipes/tier-0-dev/README.md)
* [HiL Evaluation Specification](../../evaluation/specifications/hil-evaluation.specification.md)
