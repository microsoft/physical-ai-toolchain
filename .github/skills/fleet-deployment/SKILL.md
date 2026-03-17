---
name: fleet-deployment
description: 'Deploy trained robot policies to edge fleets via FluxCD GitOps, image automation, and deployment gating'
---

# Fleet Deployment

Deploy trained robot policies to edge robot fleets using FluxCD GitOps pipelines, automated image updates, and deployment gating.

## Prerequisites

| Tool | Requirement |
|------|-------------|
| `kubectl` | Authenticated to target cluster |
| `flux` | FluxCD CLI 2.x |
| `az` CLI | Azure authentication for ACR access |

## Deployment Workflow

### Step 1 — Bootstrap FluxCD

```bash
fleet-deployment/gitops/bootstrap.sh
```

Installs Flux components on the target cluster and configures Git source reconciliation.

### Step 2 — Configure image automation

Define `ImageRepository`, `ImagePolicy`, and `ImageUpdateAutomation` resources in `fleet-deployment/gitops/image-automation/`.

### Step 3 — Set up deployment gating

Configure gate criteria in `fleet-deployment/gating/` to validate models before rollout.

### Step 4 — Deploy to fleet

FluxCD reconciles cluster state from Git. New model images trigger automated manifest updates and gated rollout.

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `fleet-deployment/gitops/` | FluxCD manifests, sources, releases, and cluster overlays |
| `fleet-deployment/gating/` | Deployment gating service and Kubernetes manifests |
| `fleet-deployment/inference/` | On-device inference runtime code |
| `fleet-deployment/examples/` | Example deployment configurations |
| `fleet-deployment/specifications/` | Domain specification documents |

## Specifications

| Document | Description |
|----------|-------------|
| [fleet-deployment.specification.md](../../fleet-deployment/specifications/fleet-deployment.specification.md) | Domain overview and component contracts |
| [gitops.specification.md](../../fleet-deployment/specifications/gitops.specification.md) | FluxCD GitOps architecture |
| [gating-service.specification.md](../../fleet-deployment/specifications/gating-service.specification.md) | Deployment gating service |
