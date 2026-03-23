---
description: Minimal prerequisites and commands for deploying Isaac Linux VMs with Bicep.
ms.date: 2026-02-24
---

# Deploy an Isaac VM for development on Azure

This optional setup deploys an Azure Virtual Machine into the same network as your platform infrastructure so you can run Isaac Sim for development. Use it when your team does not have local workstations that can run Isaac Sim reliably.

It installs the [Isaac Sim Developer Workstation](https://marketplace.microsoft.com/product/nvidia.isaac_sim_developer_workstation?tab=Overview) marketplace offer.

## 🔧 Why Use The Script?

The deployment script integrates the Isaac Sim Developer Workstation with your existing infrastructure and installs the workstation prerequisites after provisioning:

- `uv` installed system-wide at `/usr/local/bin`
- Azure CLI and the Azure ML extension
- AzCopy
- CUDA Toolkit 12.6
- NVIDIA Container Toolkit configured for Docker
- VS Code Insiders, with Git configured to use `code-insiders --wait`
- PowerShell
- [Cendio's ThinLinc server](https://www.cendio.com/) for graphical remote desktop connection

## ⚠️ Limitations

- Only Linux VMs are supported.
- Only private networking is supported. Public IPs are not supported.
- The template reuses an existing subnet and an existing network security group.
- Direct Bicep deployment requires explicit values for `subnetId` and `nsgId`.
- Only password authentication is supported.
- [Cendio's ThinLinc server](https://www.cendio.com/) is always installed, we will make optional in the future.

## 📋 Prerequisites

Before deployment:

- Accept the NVIDIA marketplace terms in the target subscription.
- Authenticate the Azure CLI for the target subscription.
- For Terraform-backed deployment, enable `should_create_vm_subnet = true` and apply Terraform so `vm_subnet` and `network_security_group` outputs exist.

## 🚀 Marketplace Terms Acceptance

Run this once per subscription before deployment:

```bash
az vm image terms accept --publisher nvidia --offer isaac_sim_developer_workstation --plan isaac_sim_developer_workstation_community_linux
```

## 🚀 Terraform-Backed Deployment

Use the optional deployment script when these VMs should attach to the Terraform-managed platform network by default.

Deploy a VM with Terraform-derived defaults:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh --vm-name isaac-sim-dev-01
```

Use `--config-preview` to inspect the resolved configuration without deploying:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --config-preview
```

The script reads these values from Terraform outputs by default:

| Value | Terraform output |
| ----- | ---------------- |
| Resource group | `resource_group.value.name` |
| Location | `resource_group.value.location` |
| Dedicated VM subnet | `vm_subnet.value.id` |
| Shared NSG | `network_security_group.value.id` |

If `terraform.tfstate` is unavailable, pass `--tfvars-file` with a Terraform variables file that includes the same top-level fields used in `terraform.tfvars.example`. The script derives the standard resource names from that file, then resolves the subnet and NSG IDs from Azure.

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --tfvars-file infrastructure/terraform/terraform.tfvars \
  --vm-name isaac-sim-dev-01
```

Deploy into a derived VM-specific resource group:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --isolated-vm-rg
```

Override any detected value explicitly:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --subnet-id /subscriptions/.../subnets/... \
  --nsg-id /subscriptions/.../networkSecurityGroups/...
```

Enable Microsoft Defender for Endpoint on the VM extension deployment:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --enable-mde-linux
```

If you do not want the script to accept marketplace terms automatically, pass `--skip-marketplace-requirements`.

The script prompts for the admin password unless you pass `--admin-password` or set `ISAAC_LAB_VM_ADMIN_PASSWORD`.

## 🚀 Direct Bicep Deployment

```bash
az deployment group create \
  --resource-group <resource-group> \
  --template-file infrastructure/setup/optional/isaac-sim-vm/bicep/main.bicep \
  --parameters \
    vmName=<vm-name> \
    subnetId=/subscriptions/<subscription-id>/resourceGroups/<network-rg>/providers/Microsoft.Network/virtualNetworks/<vnet>/subnets/<subnet> \
    nsgId=/subscriptions/<subscription-id>/resourceGroups/<network-rg>/providers/Microsoft.Network/networkSecurityGroups/<nsg> \
    adminUsername=<admin-username> \
    adminPassword=<admin-password>
```

Pass `vmResourceGroup=<name>` when the VM resources should be created in a different resource group than the deployment resource group.

## 🖥️ Connect To The VM After Deployment

The deployment configures the VM for private-network access only. Connect from a machine that has network reachability to the VM subnet, such as a workstation on the same network or a client connected through the point-to-site VPN.

Get the VM private IP address:

```bash
az vm show -d \
  --resource-group <resource-group> \
  --name <vm-name> \
  --query privateIps \
  --output tsv
```

### ThinLinc client for a GUI session

[ThinLinc server](https://www.cendio.com/) is installed during provisioning. Install the [ThinLinc client](https://www.cendio.com/thinlinc/download/) on your local machine, then connect to the VM private IP address and sign in with the VM admin username and password. Make sure you understand [ThinLinc licensing](https://www.cendio.com/thinlinc/buy-pricing/).

Use this option when you need a remote desktop session for Isaac Sim or other GUI tools.

### SSH for terminal-only access

Use SSH when you only need shell access and do not need a graphical desktop session:

```bash
ssh <admin-username>@<vm-private-ip>
```

Use this option for setup, diagnostics, file transfers, or command-line workflows that do not require a UI.

## ⚙️ Parameters

The deployment template in `main.bicep` accepts the following parameters.

| Name                           | Type           | Required | Declared default           | Description |
| ------------------------------ | -------------- | -------- | -------------------------- | ----------- |
| `vmName`                       | `string`       | Yes      | None                       | Name of the virtual machine to deploy. |
| `location`                     | `string`       | No       | `resourceGroup().location` | Azure region for deployed resources. |
| `vmResourceGroup`              | `string`       | No       | `resourceGroup().name`     | Resource group that receives the VM resources. |
| `tags`                         | `CommonTags?`  | No       | `null`                     | Tags applied to created resources. When `null`, `defaultCommonTags` is used as the effective value. |
| `subnetId`                     | `string`       | Yes      | None                       | Resource ID of the existing subnet used by the VM NIC. |
| `nsgId`                        | `string`       | Yes      | None                       | Resource ID of the existing network security group associated with the VM NIC. |
| `enableSubnetNatGatewayEgress` | `bool`         | No       | `false`                    | Deploy a NAT gateway and attach it to the target subnet for outbound internet egress without a VM public IP. |
| `natGatewayName`               | `string`       | No       | `''`                       | NAT gateway name override when `enableSubnetNatGatewayEgress` is `true`. |
| `natGatewayPublicIpName`       | `string`       | No       | `''`                       | Public IP name override for the NAT gateway when `enableSubnetNatGatewayEgress` is `true`. |
| `adminUsername`                | `string`       | Yes      | None                       | Admin username for the Linux VM. |
| `adminPassword`                | `securestring` | Yes      | None                       | Admin password for the Linux VM. |
| `vmSize`                       | `string`       | No       | `Standard_NV36ads_A10_v5`  | Virtual machine size. |
| `image`                        | `ImageConfig?` | No       | `null`                     | Marketplace image configuration. When `null`, `defaultImageConfig` is used as the effective value. |
| `plan`                         | `PlanConfig?`  | No       | `null`                     | Marketplace plan configuration. When `null`, `defaultPlanConfig` is used as the effective value. |
| `osDisk`                       | `DiskConfig?`  | No       | `null`                     | OS disk configuration. When `null`, `defaultOsDiskConfig` is used as the effective value. |
| `dataDisk`                     | `DiskConfig?`  | No       | `null`                     | Data disk configuration. When `null`, `defaultDataDiskConfig` is used as the effective value. |
| `mdeLinux`                     | `object?`      | No       | `null`                     | Defender for Endpoint extension settings. Set `{}` to enable with defaults. Set `null` to skip deployment. |

### Structured parameter types

- `CommonTags`: `environment`
- `ImageConfig`: `publisher`, `offer`, `sku`, `version`
- `PlanConfig`: `publisher`, `product`, `name`
- `DiskConfig`: `storageAccountType`, `sizeGb`, `caching`, `deleteOption`
