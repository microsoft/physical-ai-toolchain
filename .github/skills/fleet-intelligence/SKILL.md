---
name: fleet-intelligence
description: 'Monitor robot fleet telemetry via Azure IoT Operations, drift detection, Grafana dashboards, and Fabric analytics'
---

# Fleet Intelligence

Monitor and analyze deployed robot fleet telemetry — IoT Operations data collection, Grafana dashboards, drift detection, and Fabric Real-Time Intelligence analytics.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Azure IoT Operations | Edge telemetry collection |
| Azure Event Hubs | Cloud telemetry ingestion |
| Grafana | Fleet dashboards and alerting |
| Microsoft Fabric | Real-Time Intelligence KQL analytics |
| `az` CLI | Azure authentication |
| `kubectl` | Cluster access for IoT Operations |

## Setup Workflow

Deploy fleet intelligence components in order:

### Step 1 — Deploy IoT Operations

```bash
fleet-intelligence/setup/deploy-iot-operations.sh
```

### Step 2 — Deploy telemetry pipeline

```bash
fleet-intelligence/setup/deploy-telemetry-pipeline.sh
```

### Step 3 — Deploy dashboards

```bash
fleet-intelligence/setup/deploy-dashboards.sh
```

### Step 4 — Deploy Fabric Real-Time Intelligence

```bash
fleet-intelligence/setup/deploy-fabric-rti.sh
```

## Telemetry Schemas

| Schema | Path | Description |
|--------|------|-------------|
| Policy Execution | `telemetry/schemas/policy-execution.schema.json` | Inference metrics and action outputs |
| Robot Health | `telemetry/schemas/robot-health.schema.json` | Hardware status and connectivity |

## Drift Detection

Drift detection monitors deployed policies for performance degradation. When drift exceeds configured thresholds, retraining triggers initiate automated training pipelines.

| Component | Path | Description |
|-----------|------|-------------|
| Detection | `drift/detection/` | Statistical tests against training baselines |
| Alerting | `drift/alerting/` | Threshold evaluation and notification routing |
| Triggers | `drift/triggers/` | Automated retraining pipeline launching |

## Key Files

| File | Description |
|------|-------------|
| `fleet-intelligence/README.md` | Domain overview |
| `fleet-intelligence/telemetry/routing/edge-to-eventhub.yaml` | Edge-to-cloud routing config |
| `fleet-intelligence/dashboards/grafana/fleet-overview.json` | Grafana dashboard definition |
| `fleet-intelligence/dashboards/fabric/fleet-kql-queries.kql` | Fabric KQL analytics queries |
