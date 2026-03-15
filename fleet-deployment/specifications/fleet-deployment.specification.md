# Fleet Deployment

Domain contracts for deploying trained robot policies to edge fleets via FluxCD GitOps pipelines.

## Status

Planned — placeholder for future implementation.

## Components

| Component         | Description                                              |
|-------------------|----------------------------------------------------------|
| GitOps delivery   | FluxCD reconciliation of cluster state from Git          |
| Image automation  | Automatic manifest updates on new model image publish    |
| Deployment gating | Pre-rollout safety and performance validation gates      |
| Inference runtime | On-device model serving via ROS 2 nodes                  |

## Deployment Flow

```text
Model Registry → Image Automation → Gating Service → FluxCD Reconciliation → Edge Cluster
```

## Dependencies

| Dependency   | Purpose                                  |
|--------------|------------------------------------------|
| Training     | Produces trained model checkpoints       |
| Evaluation   | Validates models before deployment       |
| AKS/Arc      | Target cluster infrastructure            |
| FluxCD       | GitOps reconciliation engine             |
