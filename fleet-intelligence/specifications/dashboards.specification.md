# Dashboards

Fleet-wide operational dashboards and alerting for monitoring a fleet of robots, the visualization
surface of the fleet-intelligence cognition layer (T5 — Operate). "Fleet" means a fleet of robots. See
[tier-model.md](../../docs/design/tier-model.md) for canonical tier and vocabulary definitions.

## Status

Planned: placeholder for future implementation. Part of the roadmap **fleet intelligence** layer (T5);
this domain ships 0 Python files and design specs only.

## Components

| Component              | Description                                                             |
|------------------------|-------------------------------------------------------------------------|
| Grafana Fleet Overview | Real-time dashboard showing robot status, latency, and utilization      |
| Alert Rules            | Threshold-based alerts for latency spikes, connectivity loss, and drift |
| Fabric KQL Queries     | Analytical queries for trend analysis and fleet-wide aggregations       |
| Notification Routing   | Alert delivery to teams via Azure Monitor action groups                 |

## Dashboard Panels

| Panel             | Data Source      | Description                         |
|-------------------|------------------|-------------------------------------|
| Fleet Map         | Robot Health     | Online/offline status by robot ID   |
| Inference Latency | Policy Execution | p50/p95/p99 latency over time       |
| GPU Utilization   | Robot Health     | Per-robot GPU usage heatmap         |
| Drift Indicators  | Drift Detection  | Action distribution shift magnitude |
