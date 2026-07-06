# Fleet Intelligence

The **fleet-intelligence cognition layer (T5 — Operate)**: fleet-wide telemetry collection, operational
dashboards, drift detection, and retraining triggers across a fleet of deployed robots. "Fleet" means a
fleet of robots, not Kubernetes clusters. This is the cognition layer that sits *above* the implemented
[fleet-delivery control plane](../fleet-deployment/README.md) (T4).

> [!WARNING]
> **Roadmap / placeholder, not shipped.** The `fleet-intelligence` domain ships **0 Python files** and
> design specifications only (4 placeholder specs). Everything below describes *intended* capability,
> not working code. Treat this domain as a roadmap direction, not an available feature. For the
> canonical tier model, the autonomy ladder, and fleet vocabulary, see
> [tier-model.md](../design/tier-model.md).
>
> **Human-in-the-loop is the default; closed-loop retraining is a foot-gun.** Fully autonomous
> retraining on production data can bake current degraded behavior into the next dataset, and drift
> detection needs statistical power that only exists at fleet scale. Fleet intelligence should default
> to human-supervised stages (T5.0–T5.1), not the autonomous closed loop (T5.3). See the
> [autonomy ladder](../design/tier-model.md#the-autonomy-ladder-t50t53).

## 📋 Prerequisites

| Requirement          | Purpose                                      |
|----------------------|----------------------------------------------|
| Azure IoT Operations | Edge telemetry collection and MQTT brokering |
| Azure Event Hubs     | Cloud telemetry ingestion                    |
| Grafana              | Fleet operational dashboards                 |
| Microsoft Fabric     | Real-Time Intelligence KQL analytics         |

## 🏗️ Architecture

| Layer         | Component           | Description                                              |
|---------------|---------------------|----------------------------------------------------------|
| Edge          | Telemetry Agent     | Collects inference metrics and health data on each robot |
| Transport     | IoT Operations      | MQTT broker and edge-to-cloud routing                    |
| Ingestion     | Event Hubs          | Cloud endpoint for partitioned telemetry streams         |
| Analytics     | Fabric RTI          | KQL queries for fleet-wide trend analysis                |
| Visualization | Grafana             | Real-time dashboards and alert rules                     |
| Automation    | Drift Detection     | Statistical monitoring for policy degradation            |
| Automation    | Retraining Triggers | Automated training pipeline initiation                   |

## 📖 Related Documentation

| Guide                                                                                                                                                            | Description                           |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------|
| [Telemetry Specification](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-intelligence/specifications/telemetry.specification.md)             | Schema and routing architecture       |
| [Dashboard Specification](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-intelligence/specifications/dashboards.specification.md)            | Fleet dashboard and alerting design   |
| [Drift Detection Specification](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-intelligence/specifications/drift-detection.specification.md) | Detection algorithms and thresholds   |
| [Retraining Specification](https://github.com/microsoft/physical-ai-toolchain/blob/main/fleet-intelligence/specifications/retraining.specification.md)           | Automated retraining trigger pipeline |
