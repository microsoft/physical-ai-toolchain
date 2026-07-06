# T3 — Production: Single-Site Declarative Deployment (Advanced)

> [!NOTE]
> **Advanced tier.** Most teams should run the full training lifecycle at [T0 — Dev](../tier-0-dev/README.md) or
> [T2 — Pilot](../tier-2-pilot/README.md) first. T3 adds declarative, GitOps-style deployment at a
> single site. It does **not** change how you train or validate.

T3 proves that declarative, GitOps-style deployment does **not** require Azure Arc. Several robots at
one site you control, all reachable from a single operator network, are reconciled to a Git-declared
desired state by a single **local k3s node running FluxCD**. Arc is unnecessary precisely because
there is only one site you can reach directly. Train and curate exactly as at
[T2 — Pilot](../tier-2-pilot/README.md).

## 🧱 Minimum Infrastructure

| Concern     | What you need                                                                         |
|-------------|---------------------------------------------------------------------------------------|
| Hardware    | Several robots at **one** site, reachable from a single operator network.             |
| Edge infra  | One **local k3s node** (a ~60 MB binary) + **FluxCD**. **No Arc, no IoT Operations.** |
| Cloud infra | Same as [T2 — Pilot](../tier-2-pilot/README.md): AzureML, storage, registry, MLflow.  |
| Delivery    | FluxCD reconciles robots to Git-declared desired state; rollback is a `git revert`.   |

## 🚀 Where to Go

This is a stub. The deployment mechanics are documented in the existing deployment docs. This recipe
deliberately does not duplicate them:

- [Fleet Deployment](../../fleet-deployment/README.md): FluxCD GitOps pipelines, image automation,
  and the deployment gating service used to swap policies safely.
- [Infrastructure: cluster setup](../../infrastructure/cluster-setup.md) and
  [advanced cluster setup](../../infrastructure/cluster-setup-advanced.md): standing up the runtime.

## 🎓 Graduate When

- Robots span **multiple sites**, or sites become unreachable from a single operator network. That is
  the point at which a cross-site reachability and identity broker becomes genuinely necessary:
  [T4 — Scale](../tier-4-scale/README.md).

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md)
- [Architecture: T3 — Production](../../contributing/architecture.md#t3--production)
- [T2 — Pilot](../tier-2-pilot/README.md) · [T4 — Scale](../tier-4-scale/README.md)
