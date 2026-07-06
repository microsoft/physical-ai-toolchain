# Telemetry

Telemetry schema and routing architecture for collecting data across a fleet of robots via Azure IoT
Operations, the data-ingestion foundation of the fleet-intelligence cognition layer (T5 — Operate).
"Fleet" means a fleet of robots. See [tier-model.md](../../docs/design/tier-model.md) for canonical
tier and vocabulary definitions.

## Status

Planned: placeholder for future implementation. Part of the roadmap **fleet intelligence** layer (T5);
this domain ships 0 Python files and design specs only.

## Components

| Component               | Description                                                        |
|-------------------------|--------------------------------------------------------------------|
| Policy Execution Schema | Standardized event format for inference metrics and action outputs |
| Robot Health Schema     | Standardized event format for hardware and connectivity status     |
| On-Robot Agent          | Sidecar container collecting and forwarding telemetry events       |
| MQTT Broker             | IoT Operations edge message broker for local telemetry transport   |
| Edge-to-Cloud Routing   | Data flow rules routing telemetry from edge MQTT to Event Hubs     |
| Event Hubs              | Cloud ingestion endpoint for fleet telemetry streams               |

## Data Flow

| Stage           | Location     | Description                                              |
|-----------------|--------------|----------------------------------------------------------|
| Collection      | Edge device  | Telemetry agent gathers metrics from inference node      |
| Local transport | Edge device  | Events published to IoT Operations MQTT broker           |
| Routing         | Edge → Cloud | IoT Operations routes events to Azure Event Hubs         |
| Ingestion       | Cloud        | Event Hubs receives and partitions telemetry streams     |
| Analytics       | Cloud        | Fabric Real-Time Intelligence and Grafana consume events |
