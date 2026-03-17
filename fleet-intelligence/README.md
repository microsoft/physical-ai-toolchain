# Fleet Intelligence

IoT Operations telemetry, fleet-wide dashboards, drift detection, and retraining triggers for deployed robot fleets.

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
