---
sidebar_position: 1
title: Getting Started
description: Entry point for deploying the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-06-12
ms.topic: overview
keywords:
  - getting-started
  - quickstart
  - deployment
  - onboarding
---

Deploy the Physical AI Toolchain and submit your first training job. This hub guides you through setup, deployment, and verification.

The default path starts on a laptop, not in the cloud. Begin with [Start Here — T0 Dev](#start-here--t0-dev), then graduate to higher tiers only when your scale demands them.

## Choose Your Tier

Adoption is modeled as six graduated tiers (T0-T5). Each tier states the minimum infrastructure needed to complete the full training lifecycle: capture demonstrations on a robot, train an imitation policy, validate it, and run that policy back on the robot. Each tier is a legitimate stopping point. Start at T0 and graduate only when a concrete trigger forces it.

| Tier                | When to start here                                                   | Graduate when…                                                                                                                                             | Quick start                                                   |
|---------------------|----------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| **T0 — Dev** ⭐      | Default. One laptop, one robot; zero cloud and zero Kubernetes.      | No local GPU; the task needs many training iterations as conditions vary; or a second person needs the data.                                               | [Tier 0 — Dev](../recipes/tier-0-dev/README.md)               |
| **T1 — Lab**        | One site, a few robots, a shared GPU box; first cloud storage.       | Training scale or team size outgrows one GPU box; dataset governance and catalogs become necessary.                                                        | [Tier 1 — Lab](../recipes/tier-1-lab/README.md)               |
| **T2 — Pilot** ✅    | Recommended production. One site at scale; cloud training default.   | The robot count or update cadence makes hand-updating each robot error-prone and version skew real, while everything is still at one reachable site.       | [Tier 2 — Pilot](../recipes/tier-2-pilot/README.md)           |
| **T3 — Production** | Advanced. Single-site declarative deploy (local k3s + Flux, no Arc). | Robots span multiple sites, or sites become unreachable from a single operator network.                                                                    | [Tier 3 — Production](../recipes/tier-3-production/README.md) |
| **T4 — Scale**      | Advanced. Multi-site **fleet delivery**; Arc reachability broker.    | You explicitly want production signals to drive retraining and fleet-wide health analytics. This is a deliberate decision, not an automatic consequence of scale. | [Tier 4 — Scale](../recipes/tier-4-scale/README.md)           |
| **T5 — Operate**    | Roadmap. **Fleet intelligence** for drift detection and retraining.  | Available after the roadmap implementation lands.                                                                                                          | [Tier 5 — Operate](../recipes/tier-5-operate/README.md)       |

⭐ default · ✅ recommended production

For the tier-by-tier infrastructure boundaries see the [Architecture Overview](../contributing/architecture.md). Jump to
[T0 — Dev](../contributing/architecture.md#t0--dev),
[T1 — Lab](../contributing/architecture.md#t1--lab),
[T2 — Pilot](../contributing/architecture.md#t2--pilot),
[T3 — Production](../contributing/architecture.md#t3--production),
[T4 — Scale](../contributing/architecture.md#t4--scale), or
[T5 — Operate](../contributing/architecture.md#t5--operate).
See the canonical [Tier Model](../design/tier-model.md) for the authoritative tier table and vocabulary.

> [!NOTE]
> **Roadmap honesty.** T5 (Operate / fleet intelligence) is on the roadmap and not yet available. The fleet-intelligence domain is currently specified, with implementation planned. Today's shipping capability spans T0-T4.

### Start Here — T0 Dev

The default starting path is **one laptop and one robot**, with zero cloud and zero Kubernetes. You close the full capture -> train -> validate -> run loop entirely on local hardware.

1. **Set up:** clone the repo and run `./setup-dev.sh` (Python 3.12 via `uv`, virtual environment, training dependencies). No Azure subscription required.
2. **Capture:** record ROS 2 bags to local disk on the robot or laptop.
3. **Move data:** `cp` or `rsync` from robot to laptop.
4. **Curate:** run the dataviewer in `local` mode on the laptop.
5. **Train:** run `lerobot-train` on the laptop (CPU or a local GPU).
6. **Track:** training outputs are written to local disk; hosted experiment tracking enters at T2.
7. **Validate:** run `run-local-lerobot-eval.py` / `play.py` locally.
8. **Run on robot:** launch the inference node as a plain process or container. No Flux, no gating, no GitOps.

**Edge infra:** ROS 2 and Docker only. **Cloud infra:** none. See the [Tier 0 — Dev recipe](../recipes/tier-0-dev/README.md) for the step-by-step walkthrough.

> [!TIP]
> **Graduate when** you have no local GPU, the task needs many training iterations as conditions vary, or a second person needs the data. At that point, step up to [Tier 1 — Lab](../recipes/tier-1-lab/README.md) (first cloud storage) or jump straight to the recommended production path, [Tier 2 — Pilot](../recipes/tier-2-pilot/README.md) (cloud training). The [Quickstart](quickstart.md) covers the cloud (T2 — Pilot) path end to end.

## 🚀 Guides

| Guide                                      | Description                                                |
|--------------------------------------------|------------------------------------------------------------|
| [Start Here — T0 Dev](#start-here--t0-dev) | Default local-first path: laptop + one robot, no cloud     |
| [Choose Your Tier](#choose-your-tier)      | Pick a tier and see its graduation triggers                |
| [Quickstart](quickstart.md)                | Cloud path (T2 — Pilot): clone to the first cloud training job |
| Architecture Overview (coming soon)        | System topology, components, and data flow                 |
| Glossary (coming soon)                     | Term definitions for Azure, NVIDIA, and OSMO               |

## ⏱️ Time and Cost

The local default path (T0 — Dev) has **no cloud cost**. It runs entirely on your laptop. The estimates below apply to the cloud path ([Quickstart](quickstart.md), T2 — Pilot and up).

| Item                  | Estimate           |
|-----------------------|--------------------|
| Total deployment time | ~1.5-2 hours       |
| Quick validation cost | ~$25-50            |
| GPU VM rate           | ~$3.06/hour (A100) |

> [!NOTE]
> Run `terraform destroy` when finished to stop incurring costs. See [Cost Considerations](../contributing/cost-considerations.md) for detailed estimates.

## 📋 Prerequisites Summary

The default path (T0 — Dev) needs only **Python ≥3.12** plus ROS 2 and Docker, with no Azure subscription and no Kubernetes tooling. The additional tools below are required only for the cloud path ([Quickstart](quickstart.md), T2 — Pilot and up).

| Tool      | Version | Required for           |
|-----------|---------|------------------------|
| Python    | ≥3.12   | All tiers (incl. T0)   |
| Terraform | ≥1.9.8  | Cloud path (T2+)       |
| Azure CLI | ≥2.65.0 | Cloud path (T2+)       |
| kubectl   | ≥1.31   | Kubernetes tiers (T3+) |
| Helm      | ≥3.16   | Kubernetes tiers (T3+) |

For the cloud path, an Azure subscription with Contributor + User Access Administrator roles, GPU quota for `Standard_NC24ads_A100_v4`, and an NVIDIA NGC account are required. See [Prerequisites](../contributing/prerequisites.md) for full details.

## 📚 Related Documentation

| Resource                                                                                          | Description                             |
|---------------------------------------------------------------------------------------------------|-----------------------------------------|
| [Contributing Guide](../contributing/README.md)                                                   | Development workflow and code standards |
| [Deployment Guide](https://github.com/microsoft/physical-ai-toolchain/blob/main/deploy/README.md) | Detailed deployment reference           |
| [Cost Considerations](../contributing/cost-considerations.md)                                     | Pricing breakdown and optimization      |
