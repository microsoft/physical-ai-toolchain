# Fleet Deployment

Domain contracts for the **fleet-delivery control plane (T4 — Scale)**: delivering trained robot
policies onto robots across sites you cannot directly reach, via FluxCD GitOps pipelines, with a safety
gate before a policy swaps on a physical arm. "Fleet" means a fleet of robots, not Kubernetes clusters.
See [tier-model.md](../../docs/design/tier-model.md) for canonical tier and vocabulary definitions.

## Status

Planned: placeholder for future implementation. Fleet delivery is the *necessary* multi-site concern
in the tier model (T4), distinct from the roadmap *fleet intelligence* cognition layer (T5).

## Components

| Component         | Description                                           |
|-------------------|-------------------------------------------------------|
| GitOps delivery   | FluxCD reconciliation of cluster state from Git       |
| Image automation  | Automatic manifest updates on new model image publish |
| Deployment gating | Pre-rollout safety and performance validation gates   |
| Inference runtime | On-device model serving via ROS 2 nodes               |

## Deployment Flow

```text
Model Registry → Image Automation → Gating Service → FluxCD Reconciliation → Edge Cluster
```

## Dependencies

| Dependency | Purpose                            |
|------------|------------------------------------|
| Training   | Produces trained model checkpoints |
| Evaluation | Validates models before deployment |
| AKS/Arc    | Target cluster infrastructure      |
| FluxCD     | GitOps reconciliation engine       |
