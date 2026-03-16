# Dashboards

Fleet-wide operational dashboards and alerting for robot fleet monitoring.

## Status

Planned — placeholder for future implementation.

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
