# T5 — Operate: Fleet Intelligence (Roadmap)

> [!WARNING]
> **Roadmap direction, not shipped.** The fleet-intelligence domain is currently specified, with
> implementation planned. Everything below describes *intended* capability, not working code.
> Treat this tier as a roadmap placeholder, not an available feature.

T5 is the **fleet-intelligence** cognition layer that sits *above* the implemented
[fleet-delivery control plane](../tier-4-scale/README.md) (T4): drift detection, automated retraining
triggers, and aggregate telemetry analytics across a fleet of deployed robots. "Fleet" means a fleet
of robots, not Kubernetes clusters.

> [!WARNING]
> **Human-in-the-loop is the default; closed-loop retraining is a foot-gun.** Fully autonomous
> retraining on production data can bake current degraded behavior into the next dataset, and drift
> detection needs statistical power that only exists at fleet scale. Fleet intelligence should default
> to the human-supervised stages (T5.0–T5.1), not the autonomous closed loop (T5.3). See the
> [autonomy ladder](../../design/tier-model.md#the-autonomy-ladder-t50t53).

## 🧱 Minimum Infrastructure

| Concern     | What it would require (roadmap)                                                            |
|-------------|--------------------------------------------------------------------------------------------|
| Edge infra  | T4 infrastructure plus **Azure IoT Operations** for MQTT telemetry aggregation.            |
| Cloud infra | T4 cloud plus **Microsoft Fabric Real-Time Intelligence** and drift / retraining services. |
| Autonomy    | A separate axis (T5.0–T5.3): how much of the retraining decision a human delegates.        |

## 🚀 Where to Go

This is a roadmap stub. For the intended architecture and the autonomy ladder, see the existing
domain doc rather than a duplicate here:

- [Fleet Intelligence](../../fleet-intelligence/README.md): intended telemetry, dashboards, drift
  detection, and retraining triggers (roadmap / placeholder).
- [Autonomy ladder (T5.0–T5.3)](../../design/tier-model.md#the-autonomy-ladder-t50t53): the ordered
  decision-authority stages, orthogonal to the T0–T4 infrastructure axis.

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md)
- [Architecture: T5 — Operate](../../contributing/architecture.md#t5--operate)
- [T4 — Scale](../tier-4-scale/README.md) · [Fleet Intelligence](../../fleet-intelligence/README.md)
