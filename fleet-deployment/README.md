# Fleet Deployment

Deploy trained robot policies to edge fleets via FluxCD GitOps pipelines, image automation, and deployment gating.

## 📂 Directory Structure

| Directory         | Purpose                                            |
|-------------------|----------------------------------------------------|
| `gitops/`         | FluxCD GitOps manifests and configurations         |
| `gating/`         | Deployment gating service                          |
| `inference/`      | Inference runtime code for on-device model serving |
| `examples/`       | Example deployment configurations                  |
| `specifications/` | Domain specification documents                     |

## Overview

Fleet Deployment manages the lifecycle of trained models from the container registry to production robot fleets. The domain covers:

- **GitOps delivery** — FluxCD reconciles cluster state from Git-declared manifests
- **Image automation** — Automatic policy updates when new model images are published
- **Deployment gating** — Validation gates that block rollout until safety criteria are met
- **Inference runtime** — On-device serving of trained policies via ROS 2 nodes

## 🚀 Quick Start

Bootstrap FluxCD on a target cluster:

```bash
fleet-deployment/gitops/bootstrap.sh
```
