# Fleet Deployment

The **fleet-delivery control plane (T4 — Scale)**: deliver trained robot policies onto robots across
sites you cannot directly reach, via FluxCD GitOps pipelines, image automation, and a safety gate
before a policy swaps on a physical arm. "Fleet" refers to a fleet of robots, not Kubernetes clusters.

This is the *implemented, necessary* multi-site delivery concern. The *fleet intelligence* cognition
layer, drift detection, retraining, and aggregate analytics, is a separate, mostly unbuilt roadmap
domain ([fleet intelligence](../fleet-intelligence/README.md)). For the canonical tier model and fleet
vocabulary, see [tier-model.md](../design/tier-model.md).

## Topics

- [GitOps architecture](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-deployment/specifications/gitops.specification.md)
- [Deployment gating](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-deployment/specifications/gating-service.specification.md)
