# Fleet Deployment

The **fleet-delivery control plane (T4)**: deliver trained robot policies onto robots across sites you
cannot directly reach, via FluxCD GitOps pipelines, image automation, and a safety gate before a policy
swaps on a physical arm. "Fleet" here means a fleet of robots, not Kubernetes clusters. This is the
*implemented, necessary* multi-site concern; the *fleet intelligence* cognition layer (drift,
retraining, aggregate analytics) is a separate roadmap domain. See
[`fleet-intelligence/`](../fleet-intelligence/README.md). For canonical definitions of the tier model
and fleet vocabulary, see [tier-model.md](../docs/design/tier-model.md).

## 📂 Directory Structure

| Directory         | Purpose                                             |
|-------------------|-----------------------------------------------------|
| `gitops/`         | FluxCD GitOps manifests and configurations          |
| `gating/`         | Deployment gating service                           |
| `inference/`      | Inference runtime code for on-device model serving  |
| `setup/`          | Build / sign / attest workflow for inference images |
| `examples/`       | Example deployment configurations                   |
| `specifications/` | Domain specification documents                      |

## Overview

Fleet delivery manages the lifecycle of trained models from the container registry to production
robots across multiple sites (T4 — Scale). The domain covers:

- **GitOps delivery:** FluxCD reconciles per-site cluster state from Git-declared manifests
- **Image automation:** Automatic policy updates when new model images are published
- **Deployment gating:** Validation gates that block rollout until safety criteria are met
- **Inference runtime:** On-device serving of trained policies via ROS 2 nodes

## 🚀 Quick Start

Bootstrap FluxCD on a target cluster:

```bash
fleet-deployment/gitops/bootstrap.sh
```

Publish a registered AzureML model as a signed, attested inference image:

```bash
# 1. build + sign
fleet-deployment/setup/build-aml-model-image.sh --model-name <model>

# 2. attach SBOM + OpenVEX attestations (run separately; can be repeated)
fleet-deployment/setup/attest-image.sh \
  --image <acr>.azurecr.io/<model>@sha256:<digest>
```

See [setup/README.md](setup/README.md) for the full workflow, base-image
pinning, and VEX-triage guidance.
