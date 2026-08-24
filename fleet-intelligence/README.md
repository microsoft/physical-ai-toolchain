# Fleet Intelligence

The **fleet-intelligence cognition layer (T5 — Operate)**: IoT Operations telemetry, fleet-wide
dashboards, drift detection, and retraining triggers across a fleet of deployed robots. "Fleet" means a
fleet of robots, not Kubernetes clusters. This is the cognition layer above the implemented
[fleet-delivery control plane](../fleet-deployment/README.md) (T4).

> [!WARNING]
> **Roadmap / placeholder, not shipped.** This domain ships **0 Python files** and design
> specifications only. Everything below describes *intended* capability, not working code. Fully
> autonomous retraining is a foot-gun: fleet intelligence should default to human-supervised stages
> (T5.0–T5.1), not the autonomous closed loop (T5.3). See [tier-model.md](../docs/design/tier-model.md)
> for canonical tier, autonomy-ladder, and fleet-vocabulary definitions.

## 📂 Directory Structure

| Directory         | Purpose                                                  |
|-------------------|----------------------------------------------------------|
| `setup/`          | IoT Operations provisioning and telemetry pipeline setup |
| `telemetry/`      | Schemas, on-robot agent, and edge-to-cloud routing       |
| `dashboards/`     | Grafana dashboards, alert rules, Fabric KQL queries      |
| `drift/`          | Drift detection, alerting, and retraining triggers       |
| `specifications/` | Domain specification documents                           |
| `examples/`       | Fleet intelligence workflow examples                     |

## 🏗️ Architecture

| Component            | Description                                   |
|----------------------|-----------------------------------------------|
| Azure IoT Operations | Edge telemetry collection and MQTT brokering  |
| Event Hubs           | Cloud telemetry ingestion endpoint            |
| Grafana              | Fleet-wide operational dashboards             |
| Microsoft Fabric     | Real-Time Intelligence KQL analytics          |
| Drift Detection      | Statistical monitoring for policy degradation |

## 📋 Specifications

| Document                                                           | Description                               |
|--------------------------------------------------------------------|-------------------------------------------|
| [Telemetry](specifications/telemetry.specification.md)             | Telemetry schema and routing architecture |
| [Dashboards](specifications/dashboards.specification.md)           | Fleet dashboard and alerting design       |
| [Drift Detection](specifications/drift-detection.specification.md) | Drift detection algorithms and thresholds |
| [Retraining](specifications/retraining.specification.md)           | Automated retraining trigger pipeline     |
