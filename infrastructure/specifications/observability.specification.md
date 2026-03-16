# Observability

Monitoring, logging, and diagnostics for Azure infrastructure and Kubernetes workloads.

## Components

| Component                                | Purpose                                             |
|------------------------------------------|-----------------------------------------------------|
| Azure Monitor Workspace                  | Prometheus metrics collection and storage           |
| Azure Managed Grafana                    | Dashboard visualization for cluster and GPU metrics |
| Log Analytics Workspace                  | Centralized log aggregation and KQL queries         |
| Azure Monitor Private Link Scope (AMPLS) | Private connectivity for monitoring data            |
| Data Collection Endpoints (DCE)          | Ingestion endpoints for metrics and logs            |
| Data Collection Rules (DCR)              | Routing rules for monitoring data streams           |

## Configuration

| Parameter                         | Description                             | Default |
|-----------------------------------|-----------------------------------------|---------|
| `should_deploy_grafana`           | Deploy Azure Managed Grafana            | `true`  |
| `should_deploy_monitor_workspace` | Deploy Azure Monitor workspace          | `true`  |
| `should_deploy_ampls`             | Deploy Azure Monitor Private Link Scope | `true`  |
| `should_deploy_dce`               | Deploy Data Collection Endpoints        | `true`  |

All observability components deploy by default. Disable individually to reduce cost in development environments.

### Grafana Dashboards

Grafana connects to the Azure Monitor workspace for Prometheus metrics and to Log Analytics for log queries. Pre-configured dashboards cover:

- AKS cluster health and resource utilization
- GPU utilization and memory across node pools
- Training job progress and throughput
- OSMO workflow execution status

### Log Analytics

Container logs from AKS flow to Log Analytics via the monitoring agent. Key log categories:

- `ContainerLogV2` — pod stdout/stderr
- `KubeEvents` — Kubernetes events
- `Perf` — node and container performance counters

## Dependencies

- Azure Infrastructure: resource group, Log Analytics workspace
- Network Topology: AMPLS requires private endpoint subnet
- Kubernetes Setup: monitoring agents deployed onto AKS nodes
