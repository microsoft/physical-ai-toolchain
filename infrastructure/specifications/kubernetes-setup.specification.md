# Kubernetes Setup

AKS cluster configuration, GPU node pools, and platform extensions for robotics workloads.

## Components

| Component | Purpose |
|-----------|---------|
| AKS Cluster | Managed Kubernetes control plane with RBAC and workload identity |
| System Node Pool | Cluster infrastructure services (`Standard_D8ds_v5`) |
| GPU Node Pools | Training and inference workloads (A10, RTX PRO 6000, H100) |
| NVIDIA GPU Operator | GPU driver lifecycle, device plugin, MIG management |
| KAI Scheduler | Gang-scheduling / coscheduling for multi-GPU jobs |
| AzureML Extension | Arc-connected ML compute, experiment tracking |
| OSMO Control Plane | Multi-cluster workflow orchestration |
| OSMO Backend | PostgreSQL, Redis, and operator services |

## Configuration

### GPU Node Pools

Node pools are configured via the `node_pools` map in `terraform.tfvars`:

| Field | Description |
|-------|-------------|
| `vm_size` | Azure VM SKU with GPU |
| `gpu_driver` | `Install` (AKS-managed) or `None` (GPU Operator / GRID DaemonSet) |
| `priority` | `Regular` or `Spot` |
| `node_taints` | Scheduling taints (`nvidia.com/gpu:NoSchedule`) |
| `node_labels` | Labels for driver management and scheduling |

### GPU Driver Strategy

| GPU | Driver Source | MIG Strategy |
|-----|--------------|--------------|
| H100 | GPU Operator datacenter driver | Disabled |
| RTX PRO 6000 | Microsoft GRID DaemonSet | `mig.strategy: single` (required) |

RTX PRO 6000 nodes must set `nvidia.com/gpu.deploy.driver=false` to prevent GPU Operator driver conflicts with the pre-installed Azure GRID driver.

### Setup Script Order

| Order | Script | Components |
|-------|--------|------------|
| 1 | `01-deploy-robotics-charts.sh` | GPU Operator, KAI Scheduler |
| 2 | `02-deploy-azureml-extension.sh` | AzureML extension, compute attach, InstanceType CRDs |
| 3 | `03-deploy-osmo-control-plane.sh` | OSMO control plane services |
| 4 | `04-deploy-osmo-backend.sh` | OSMO backend operator, pool configuration |

## Dependencies

- Azure Infrastructure: resource group, container registry, storage account
- Network Topology: VNet, AKS subnet, pod subnet, private endpoints
- Identity and Access: managed identities, workload identity federation
- Observability: monitoring agents, log collection
