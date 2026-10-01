---
name: infrastructure
description: 'Deploy and manage Azure infrastructure for the Physical AI Toolchain including Terraform IaC, Kubernetes setup, GPU configuration, and network topology'
---

# Infrastructure Skill

Deploy and manage Azure cloud infrastructure for the Physical AI Toolchain — Terraform IaC, AKS cluster configuration, GPU node pools, and network topology.

## Prerequisites

| Tool | Requirement |
|------|-------------|
| Azure CLI | `az login` authenticated |
| Terraform | 1.5+ |
| kubectl | Matching cluster version |
| Helm | 4.2+ |
| shellcheck | For script validation |

## Deployment Workflow

Follow these steps in order for a complete deployment.

### Step 1 — Initialize Azure subscription

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
```

Exports `ARM_SUBSCRIPTION_ID` and validates Azure CLI authentication.

### Step 2 — Configure Terraform variables

```bash
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with environment-specific values. Example configurations are in `infrastructure/examples/`:

| File | Scenario |
|------|----------|
| `terraform.tfvars.dev` | Single spot GPU pool, public networking |
| `terraform.tfvars.prod` | Multiple GPU pools, full private networking, HA |
| `terraform.tfvars.hybrid` | Private data services, public AKS API server |

### Step 3 — Provision infrastructure

```bash
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

### Step 4 — Deploy VPN (private clusters only)

Required when `should_enable_private_aks_cluster = true`:

```bash
cd infrastructure/terraform/vpn
terraform init && terraform apply
```

### Step 5 — Connect to cluster

```bash
az aks get-credentials --resource-group <rg> --name <aks>
kubectl cluster-info
```

### Step 6 — Run setup scripts

```bash
cd infrastructure/setup
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo.sh
```

Scripts must run in numeric order. Each supports `--config-preview` for dry-run output.

## Network Mode Selection

Three network modes control connectivity and security:

| Mode | `should_enable_private_endpoint` | `should_enable_private_aks_cluster` | VPN Required |
|------|----------------------------------|-------------------------------------|--------------|
| Full Private | `true` | `true` | Yes |
| Hybrid | `true` | `false` | No |
| Full Public | `false` | `false` | No |

Full Private is the default and recommended for production. Hybrid mode allows `kubectl` access without VPN while keeping data services private.

## Common Operations

### Plan changes

```bash
cd infrastructure/terraform
terraform plan -var-file=terraform.tfvars
```

### Apply changes

```bash
terraform apply -var-file=terraform.tfvars
```

### Destroy infrastructure

```bash
terraform destroy -var-file=terraform.tfvars
```

### VPN setup

```bash
cd infrastructure/terraform/vpn
terraform init && terraform apply
```

### DNS configuration

```bash
cd infrastructure/terraform/dns
terraform init && terraform apply
```

### Validate setup scripts

```bash
shellcheck infrastructure/setup/01-deploy-robotics-charts.sh
infrastructure/setup/01-deploy-robotics-charts.sh --config-preview
```

### Check Terraform formatting

```bash
terraform fmt -check -recursive infrastructure/terraform/
```

## Directory Structure

```text
infrastructure/
├── terraform/                         # Infrastructure as Code
│   ├── main.tf                        # Module composition
│   ├── variables.tf                   # Input variables
│   ├── outputs.tf                     # Output values
│   ├── versions.tf                    # Provider requirements
│   ├── terraform.tfvars.example       # Example configuration
│   ├── prerequisites/                 # Azure subscription setup
│   ├── modules/                       # Terraform modules
│   ├── vpn/                           # Standalone VPN deployment
│   ├── automation/                    # Standalone automation deployment
│   └── dns/                           # Standalone DNS deployment
├── setup/                             # Post-deploy cluster configuration
│   ├── 01-deploy-robotics-charts.sh   # GPU Operator, KAI Scheduler
│   ├── 02-deploy-azureml-extension.sh # AzureML K8s extension
│   ├── 03-deploy-osmo.sh             # OSMO control plane and backend
│   ├── defaults.conf                  # Central version and namespace config
│   └── lib/                           # Shared shell libraries
├── specifications/                    # Domain specification documents
└── examples/                          # Example tfvars configurations
```

## GPU Configuration Reference

| GPU | VM SKU | Driver Source | `gpu_driver` | MIG Strategy |
|-----|--------|--------------|--------------|--------------|
| A10 | `Standard_NV36ads_A10_v5` | AKS-managed | `Install` | N/A |
| RTX PRO 6000 | `Standard_NC144ds_xl_RTXPRO6000BSE_v6` (1 GPU, 96 GB) | AKS-managed GRID driver | `Install` | `single` |
| H100 | `Standard_NC40ads_H100_v5` | GPU Operator | `None` | Disabled |

Only RTX PRO 6000 pools created with `gpu_driver = "None"` need the `nvidia.com/gpu.deploy.driver=false` label, which hands them to the fallback GRID driver DaemonSet. Preview RTX sizes (128, 256, or 320 vCPUs) no longer deploy. Each `NC144ds_xl` node needs 144 vCPUs of RTX PRO 6000 quota, plus one more node's worth for an upgrade surge; park a pool with autoscaling off and `node_count = 0` until quota exists.

## Documentation

| Guide | Description |
|-------|-------------|
| [Infrastructure README](../../../infrastructure/README.md) | Domain overview and quick start |
| [Terraform README](../../../infrastructure/terraform/README.md) | Terraform configuration reference |
| [Setup README](../../../infrastructure/setup/README.md) | Setup script reference |
| [Infrastructure Deployment](../../../docs/infrastructure/infrastructure.md) | Full deployment walkthrough |
| [GPU Configuration](../../../docs/reference/gpu-configuration.md) | Detailed GPU driver and operator reference |
