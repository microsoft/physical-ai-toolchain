# Infrastructure

Azure cloud resources, Kubernetes clusters, and networking for the Physical AI Toolchain. This domain covers the full lifecycle from resource provisioning through cluster configuration.

## 📁 Directory Structure

```text
infrastructure/
├── terraform/                         # Infrastructure as Code
│   ├── main.tf                        # Module composition
│   ├── variables.tf                   # Input variables
│   ├── outputs.tf                     # Output values
│   ├── versions.tf                    # Provider requirements
│   ├── terraform.tfvars.example       # Example configuration
│   ├── README.md                      # Terraform quick start
│   ├── prerequisites/                 # Azure subscription setup
│   ├── modules/                       # Terraform modules
│   │   ├── platform/                  # Shared Azure services (VNet, Key Vault, identities)
│   │   ├── sil/                       # AKS cluster and ML extension
│   │   ├── vpn/                       # VPN Gateway module
│   │   ├── automation/                # Azure Automation module
│   │   └── dataviewer/                # Dataviewer container resources
│   ├── vpn/                           # Standalone VPN deployment
│   ├── automation/                    # Standalone automation deployment
│   └── dns/                           # Standalone DNS deployment
├── setup/                             # Post-deploy configuration scripts
│   ├── 01-deploy-robotics-charts.sh   # GPU Operator, KAI Scheduler
│   ├── 02-deploy-azureml-extension.sh # AzureML K8s extension
│   ├── 03-deploy-osmo-control-plane.sh# OSMO control plane
│   ├── 04-deploy-osmo-backend.sh      # OSMO backend services
│   ├── defaults.conf                  # Central version and namespace config
│   ├── defaults.ps1                   # PowerShell defaults
│   ├── README.md                      # Setup quick start
│   ├── lib/                           # Shared shell libraries
│   ├── cleanup/                       # Component removal scripts
│   ├── config/                        # Configuration templates
│   ├── manifests/                     # Kubernetes manifests
│   ├── optional/                      # Optional component scripts
│   └── values/                        # Helm values files
├── specifications/                    # Domain specification documents
├── examples/                          # Example tfvars configurations
└── README.md                          # This file
```

## 🚀 Quick Start

### 1. Initialize Azure subscription

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
```

### 2. Provision infrastructure

```bash
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your configuration
terraform init && terraform plan -var-file=terraform.tfvars
terraform apply -var-file=terraform.tfvars
```

### 3. Connect to the cluster

```bash
az aks get-credentials --resource-group <rg> --name <aks>
```

> [!IMPORTANT]
> Private AKS clusters require VPN connectivity before any `kubectl` commands. Deploy VPN first: `cd infrastructure/terraform/vpn && terraform apply`.

### 4. Run setup scripts

```bash
cd infrastructure/setup
./01-deploy-robotics-charts.sh
./02-deploy-azureml-extension.sh
./03-deploy-osmo-control-plane.sh
./04-deploy-osmo-backend.sh
```

Each script supports `--config-preview` to print configuration without making changes.

## 🌐 Network Modes

| Mode         | Private Endpoints | Private AKS | VPN Required | Use Case                               |
|--------------|-------------------|-------------|--------------|----------------------------------------|
| Full Private | `true`            | `true`      | Yes          | Production deployments                 |
| Hybrid       | `true`            | `false`     | No           | Development with private data services |
| Full Public  | `false`           | `false`     | No           | Evaluation only                        |

## 📖 Documentation

| Guide                                                                          | Description                                          |
|--------------------------------------------------------------------------------|------------------------------------------------------|
| [Infrastructure Deployment](../docs/infrastructure/infrastructure.md)          | Configuration, variables, and deployment walkthrough |
| [Infrastructure Reference](../docs/infrastructure/infrastructure-reference.md) | Architecture, module structure, and troubleshooting  |
| [Cluster Setup](../docs/infrastructure/cluster-setup.md)                       | AKS setup walkthrough and deployment scenarios       |
| [VPN Configuration](../docs/infrastructure/vpn.md)                             | Point-to-site VPN for private cluster access         |
| [Prerequisites](../docs/infrastructure/prerequisites.md)                       | Required tools and versions                          |
