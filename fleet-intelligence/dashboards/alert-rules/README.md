# Alert Rules

Fleet monitoring alert rules for Grafana and Azure Monitor.

## 📂 Planned Rules

| Rule | Trigger | Severity |
|------|---------|----------|
| High inference latency | Policy inference exceeds threshold | Warning |
| GPU utilization spike | Sustained GPU usage above limit | Warning |
| Connectivity loss | Robot fails health check interval | Critical |
| Drift threshold exceeded | Distribution shift above configured limit | Critical |
| Disk space low | Available disk below minimum threshold | Warning |

## ⚙️ Configuration

Alert rules define thresholds, evaluation intervals, and notification channels. Rules are provisioned via the dashboard deployment script.

## 📋 Status

Planned — awaiting Grafana alerting integration.
