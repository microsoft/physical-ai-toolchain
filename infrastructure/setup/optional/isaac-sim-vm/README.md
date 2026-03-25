---
description: Minimal prerequisites and commands for deploying Isaac Linux VMs with Bicep.
ms.date: 2026-03-25
---

# Deploy an Isaac VM for development on Azure

This optional setup deploys an Azure Virtual Machine into the same network as your platform infrastructure so you can run Isaac Sim for development. Use it when your team does not have local workstations that can run Isaac Sim reliably.

It installs the [Isaac Sim Developer Workstation](https://marketplace.microsoft.com/product/nvidia.isaac_sim_developer_workstation?tab=Overview) marketplace offer.

The deployment enables EncryptionAtHost by default so the VM OS disk, data disk caches, and temp disk data are encrypted at the host.

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
- EncryptionAtHost must be registered for the subscription and supported by the selected VM size.
- Only password authentication is supported.
- [Cendio's ThinLinc server](https://www.cendio.com/) is always installed, we will make optional in the future.

## 📋 Prerequisites

Before deployment:

1. Complete Steps 1 and 2 of the deployment pipeline:

   ```bash
   source infrastructure/terraform/prerequisites/az-sub-init.sh

   cd infrastructure/terraform
   terraform apply -var-file=terraform.tfvars
   ```

1. Register the `EncryptionAtHost` feature for the subscription if it is not already enabled:

   ```bash
   az feature register \
     --namespace Microsoft.Compute \
     --name EncryptionAtHost

   az feature show \
     --namespace Microsoft.Compute \
     --name EncryptionAtHost \
     --query properties.state \
     --output tsv
   ```

   Wait until the feature state is `Registered` before deploying the VM.

1. Enable the dedicated VM subnet in `infrastructure/terraform/terraform.tfvars`:

   ```hcl
   should_create_vm_subnet = true
   ```

1. Re-apply Terraform so the VM subnet and shared network security group outputs exist:

   ```bash
   cd infrastructure/terraform
   terraform apply -var-file=terraform.tfvars
   terraform output vm_subnet
   terraform output network_security_group
   ```

1. If the platform uses a private AKS cluster, complete the VPN deployment step before connecting to private VM resources:

   ```bash
   cd infrastructure/terraform/vpn
   terraform apply
   ```

1. Accept the NVIDIA marketplace terms once per subscription, or let the deployment script handle it automatically:

   ```bash
   az vm image terms accept \
     --publisher nvidia \
     --offer isaac_sim_developer_workstation \
     --plan isaac_sim_developer_workstation_community_linux
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

If the selected VM size or region does not support EncryptionAtHost yet, disable it explicitly:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --disable-encryption-at-host
```

Enable Microsoft Defender for Endpoint on the VM extension deployment:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-dev-01 \
  --enable-mde-linux
```

Deploy a Spot VM for testing workloads that tolerate eviction:

```bash
bash infrastructure/setup/optional/deploy-isaac-sim-vm.sh \
  --vm-name isaac-sim-test-spot-01 \
  --spot-vm \
  --spot-eviction-policy Deallocate
```

### Spot VM operating model

Use Spot only for test work that can stop at any time. In this deployment, Spot VMs use `maxPrice = -1`. That means Azure will not remove the VM because you set a low price limit, but Azure can still remove it at any time if it needs the capacity back.

- Spot VMs do not come with an availability guarantee. Azure might remove the VM with very little warning.
- Azure may send a scheduled event before eviction, but that warning is best effort and can be short.
- `Deallocate` is the default eviction policy. It keeps the disks so you can try to start the VM again later, but the disks still cost money and the VM still uses quota while it is stopped.
- `Delete` removes the VM when Azure evicts it. Use this for short-lived test machines that you do not need to recover.
- If a Spot VM with `Deallocate` is evicted, you must start it again yourself. Restart is not guaranteed because the same size might not be available anymore in that region.
- If a Spot VM with `Delete` is evicted, redeploy the VM. The VM resource is removed during eviction.
- This deployment does not install any special eviction-handling software. Save important work outside the VM.
- Use `Regular` priority for long Isaac Sim sessions, debugging, or any work where interruption would be a problem.

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

Pass `vmPriority=Spot spotEvictionPolicy=Deallocate` to use a lower-cost VM that Azure can interrupt when it needs the capacity. Use this only for test scenarios where losing access to the VM at any time is acceptable.

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

## 🗑️ Cleanup

Delete the VM from the resource group that contains the VM resources:

```bash
az vm delete \
  --resource-group <vm-resource-group> \
  --name <vm-name> \
  --yes
```

The network interface and OS disk use `deleteOption: Delete` and are removed with the VM. The data disk uses `deleteOption: Detach` and remains available unless you delete it separately.

If you deployed with `--isolated-vm-rg`, you can choose to delete the entire derived resource group. Note that deleting the resource group will **delete all VMs deployed to the resource group**:

```bash
# Will delete all VMs in resource group, use with care!
az group delete --name <vm-resource-group> --yes --no-wait
```

Delete the ARM deployment record from the deployment resource group when you no longer need it:

```bash
az deployment group delete \
  --resource-group <deployment-resource-group> \
  --name <deployment-name>
```

If `enableSubnetNatGatewayEgress` was enabled, delete the NAT gateway and public IP separately from the networking resource group:

```bash
az network nat gateway delete \
  --resource-group <network-resource-group> \
  --name <nat-gateway-name>

az network public-ip delete \
  --resource-group <network-resource-group> \
  --name <nat-public-ip-name>
```

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
| `shouldEnableEncryptionAtHost` | `bool`         | No       | `true`                     | Enables EncryptionAtHost for the VM so host caches and temp disk data are encrypted. |
| `vmPriority`                   | `string`       | No       | `Regular`                  | VM priority. Use `Spot` only for test workloads that can be interrupted. |
| `spotEvictionPolicy`           | `string`       | No       | `Deallocate`               | Eviction policy used when `vmPriority` is `Spot`. |
| `image`                        | `ImageConfig?` | No       | `null`                     | Marketplace image configuration. When `null`, `defaultImageConfig` is used as the effective value. |
| `plan`                         | `PlanConfig?`  | No       | `null`                     | Marketplace plan configuration. When `null`, `defaultPlanConfig` is used as the effective value. |
| `osDisk`                       | `DiskConfig?`  | No       | `null`                     | OS disk configuration. When `null`, `defaultOsDiskConfig` is used as the effective value. |
| `dataDisk`                     | `DiskConfig?`  | No       | `null`                     | Data disk configuration. When `null`, `defaultDataDiskConfig` is used as the effective value. |
| `shutdownSchedule`             | `ShutdownSchedule?` | No   | `null`                     | Daily auto-shutdown schedule. When `null`, `defaultShutdownSchedule` is used as the effective value. |
| `mdeLinux`                     | `object?`      | No       | `null`                     | Defender for Endpoint extension settings. Set `{}` to enable with defaults. Set `null` to skip deployment. |

### Structured parameter types

- `CommonTags`: `environment`
- `ImageConfig`: `publisher`, `offer`, `sku`, `version`
- `PlanConfig`: `publisher`, `product`, `name`
- `DiskConfig`: `storageAccountType`, `sizeGb`, `caching`, `deleteOption`
- `ShutdownSchedule`: `time`, `timeZoneId`
