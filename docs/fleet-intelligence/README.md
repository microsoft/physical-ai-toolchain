# Fleet Intelligence

Fleet-wide telemetry collection, operational dashboards, drift detection, and automated retraining triggers for deployed robot fleets.

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
