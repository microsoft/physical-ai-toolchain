# T4 — Scale: Multi-Site Fleet Delivery (Advanced)

> [!NOTE]
> **Advanced tier.** This is the legitimate top of the *necessary* ladder. Reach it only when robots
> span multiple sites you cannot directly reach. Single-site teams stay at
> [T3 — Production](../tier-3-production/README.md).

T4 is the **fleet-delivery** control plane: getting validated policies onto robots across sites you
cannot directly reach, safely, with a gate before a policy swaps on a physical arm. The defining
difference from T3 is **multiple sites** — which is exactly what makes **Azure Arc** necessary, as the
cross-site reachability and identity broker that single-site k3s did not need. Here, "fleet" means a
fleet of robots, not Kubernetes clusters.

> [!IMPORTANT]
> T4 delivers and gates policies. It **excludes** drift detection, automated retraining, and aggregate
> telemetry analytics. Those are *fleet intelligence* at [T5 — Operate](../tier-5-operate/README.md).

## 🧱 Minimum Infrastructure

| Concern     | What you need                                                                         |
|-------------|---------------------------------------------------------------------------------------|
| Hardware    | Robots across **multiple** sites you cannot directly reach.                           |
| Edge infra  | **Azure Arc** + AKS or Arc-enabled Kubernetes + FluxCD + a deployment gating service. |
| Cloud infra | T2 cloud + cross-site connectivity and identity, plus the model registry.             |
| Delivery    | FluxCD GitOps; per-site desired state recorded in Git; gating before a policy swaps.  |

## 🚀 Where to Go

This is a stub. The multi-site fleet-delivery mechanics are documented in the existing deployment
docs. This recipe deliberately does not duplicate them:

- [Fleet Deployment](../../fleet-deployment/README.md): the implemented multi-site fleet-delivery
  control plane: FluxCD GitOps, image automation, and the deployment gating service.
- [Infrastructure: advanced cluster setup](../../infrastructure/cluster-setup-advanced.md) and
  [node pool management](../../infrastructure/manage-node-pools.md): multi-site runtime.

## 🎓 Graduate When

- The operator explicitly wants production signals to drive retraining and fleet-wide health
  analytics. This is a deliberate decision, not an automatic consequence of scale:
  [T5 — Operate](../tier-5-operate/README.md) (roadmap).

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md)
- [Architecture: T4 — Scale](../../contributing/architecture.md#t4--scale)
- [T3 — Production](../tier-3-production/README.md) · [T5 — Operate](../tier-5-operate/README.md)
