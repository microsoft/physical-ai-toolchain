---
sidebar_position: 12
title: Azure Bastion Access
description: Azure Bastion Standard and private Linux jumpbox setup for private AKS, OSMO, and Dataviewer access
author: Microsoft Robotics-AI Team
ms.date: 2026-11-25
ms.topic: how-to
keywords:
  - bastion
  - jumpbox
  - private-access
  - entra-id
---

Azure Bastion Standard with native client tunneling and an optional private Entra-authenticated Linux jumpbox for accessing private AKS, OSMO, and Dataviewer endpoints without installing a VPN client.

> [!NOTE]
> Part of the [Deployment Guide](README.md). Use Azure Bastion as an alternative or companion to [VPN Gateway](vpn.md) when connecting to a private cluster.

## Prerequisites

| Requirement           | Version or Role                                       | Verification or Setup                                          |
|-----------------------|-------------------------------------------------------|----------------------------------------------------------------|
| Azure CLI             | Latest with `bastion` extension                       | `az extension add --name bastion`                              |
| Terraform             | `>= 1.9.8, < 2.0`                                     | `terraform version`                                            |
| Platform deployment   | Existing VNet and main subnet deployed                | `cd infrastructure/terraform && terraform apply`               |
| Subscription provider | `Microsoft.Compute` registered (for jumpbox VM)       | `source infrastructure/terraform/prerequisites/az-sub-init.sh` |
| Entra ID SSH role     | `Virtual Machine Administrator Login` or `User Login` | Set `should_assign_current_user_admin_login = true`            |

## Quick Start

Deploy Azure Bastion and the private jumpbox from the standalone stack:

```bash
cd infrastructure/terraform/bastion
cp terraform.tfvars.example terraform.tfvars
# Edit environment, location, resource_prefix, and jumpbox_config.admin_ssh_public_key
terraform init && terraform apply
```

Set `should_deploy_jumpbox = false` and leave `jumpbox_config.admin_ssh_public_key = null` to deploy Azure Bastion without a jumpbox VM.

## Configuration

| Variable                                 | Description                                                                      | Default        |
|------------------------------------------|----------------------------------------------------------------------------------|----------------|
| `bastion_subnet_address_prefix`          | CIDR for `AzureBastionSubnet` (minimum `/26`, avoids `10.0.4.0/24` VM subnet)    | `10.0.10.0/26` |
| `should_create_bastion_nsg`              | Create and associate the 8-rule Microsoft-compliant Bastion NSG                  | `true`         |
| `bastion_allowed_source_address_prefix`  | Source prefix allowed on Bastion HTTPS port 443                                  | `Internet`     |
| `bastion_config.sku`                     | Bastion SKU (`Standard` or `Premium` required for native tunneling)              | `Standard`     |
| `bastion_config.scale_units`             | Bastion scale units (`2` to `50`)                                                | `2`            |
| `should_deploy_jumpbox`                  | Deploy private Ubuntu 24.04 LTS jumpbox VM with `AADSSHLoginForLinux`            | `true`         |
| `jumpbox_config.vm_size`                 | Trusted Launch compatible VM size                                                | `Standard_B2s` |
| `jumpbox_config.admin_ssh_public_key`    | SSH public key (required only when `should_deploy_jumpbox = true`)               | `null`         |
| `should_assign_current_user_admin_login` | Grant deploying principal `Virtual Machine Administrator Login`                  | `false`        |
| `aks_cluster_id`                         | Optional AKS cluster ID for jumpbox `Azure Kubernetes Service Cluster User Role` | `null`         |

## Connecting with `connect-bastion.sh`

`infrastructure/setup/connect-bastion.sh` resolves resource group, Bastion host, and jumpbox metadata from CLI flags, environment variables, or Terraform outputs in `infrastructure/terraform/bastion`.

### Entra ID SSH Session

```bash
# Preview resolved configuration
./infrastructure/setup/connect-bastion.sh ssh --config-preview

# Connect using Microsoft Entra ID (default --auth-type AAD)
./infrastructure/setup/connect-bastion.sh ssh
```

To connect with an SSH private key instead of Entra ID, ensure owner-only permissions (`chmod 600`) on the key file:

```bash
./infrastructure/setup/connect-bastion.sh ssh \
  --auth-type ssh-key \
  --username azureuser \
  --ssh-key ~/.ssh/id_ed25519
```

### Native TCP Tunnel and Local Port Forwarding

Forward private cluster services (such as OSMO UI or Dataviewer) to localhost through the jumpbox:

```bash
# Open a raw Bastion TCP tunnel to jumpbox port 22 on localhost:50022
./infrastructure/setup/connect-bastion.sh tunnel --resource-port 22 --local-port 50022

# Open a Bastion tunnel and forward internal endpoints over SSH (-L)
./infrastructure/setup/connect-bastion.sh proxy \
  --username azureuser \
  --ssh-key ~/.ssh/id_ed25519 \
  --forward 8080:osmo.internal.example:80 \
  --forward 8000:dataviewer.internal.example:8000
```

The cloud-init template installs only standard Ubuntu APT utilities (`jq`, `curl`, `git`, `python3`, `ca-certificates`, `gnupg`) with no remote shell installers. After logging in to the jumpbox, install Azure CLI, `kubectl`, or the OSMO CLI from signed distribution repositories if you run cluster administration directly on the VM.

## Network and NSG Architecture

| Component              | Placement and Rules                                                                            |
|------------------------|------------------------------------------------------------------------------------------------|
| `AzureBastionSubnet`   | Default `10.0.10.0/26` (no NAT Gateway association and no subnet delegations)                  |
| `nsg-bas-*`            | All 8 mandatory Microsoft Bastion inbound/outbound rules (`443`, `8080/5701`, `22/3389`, `80`) |
| `nic-jb-*` / `vm-jb-*` | Deployed in `snet-{prefix}-{env}-{instance}` with private IP only and no public IP             |
| Jumpbox security       | Trusted Launch (`secure_boot_enabled`, `vtpm_enabled`), System-Assigned MI, SSH-only           |

> [!IMPORTANT]
> Jumpbox SSH ingress from `AzureBastionSubnet` relies on the default Azure virtual network `AllowVnetInBound` rule on the platform subnet NSG (`nsg-{prefix}-{env}-{instance}`). If you add custom deny-all inbound rules to the platform NSG, add an explicit inbound TCP port 22 allow rule from `bastion_subnet_address_prefix`.

## Bastion vs. VPN Gateway

| Criterion               | Azure Bastion + Jumpbox                                    | VPN Gateway (`vpn/`)                                   |
|-------------------------|------------------------------------------------------------|--------------------------------------------------------|
| Local client required   | Azure CLI (`az` + `bastion` extension) only                | Azure VPN Client or strongSwan                         |
| Direct `kubectl` access | Via jumpbox or SSH tunnel/proxy                            | Direct from local workstation                          |
| Authentication          | Microsoft Entra ID (`AADSSHLoginForLinux`)                 | Microsoft Entra ID or client certificates              |
| Subnet CIDR default     | `10.0.10.0/26` (`AzureBastionSubnet`)                      | `10.0.3.0/27` (`GatewaySubnet`)                        |
| Independent teardown    | `cd infrastructure/terraform/bastion && terraform destroy` | `cd infrastructure/terraform/vpn && terraform destroy` |

## Cost Management and Teardown

Azure Bastion Standard bills hourly even when idle. Destroy the standalone Bastion stack when remote access is not required; core AKS, Azure ML, and storage resources remain unaffected:

```bash
cd infrastructure/terraform/bastion
terraform destroy
```

## Troubleshooting

| Symptom                                   | Likely Cause                                       | Resolution                                                       |
|-------------------------------------------|----------------------------------------------------|------------------------------------------------------------------|
| `ResourceProviderNotRegistered` on VM     | `Microsoft.Compute` not registered in subscription | Run `az provider register --namespace Microsoft.Compute --wait`  |
| `az network bastion ssh` permission error | Missing Entra VM login role on jumpbox             | Set `should_assign_current_user_admin_login = true` and re-apply |
| Bastion tunnel hangs or refuses port 22   | Custom deny rule on platform subnet NSG            | Allow inbound TCP 22 from `AzureBastionSubnet` on `nsg-*`        |
| `Protected input must not be accessible`  | Private SSH key permissions too permissive         | Run `chmod 600 <ssh-key-path>`                                   |

## Related Documentation

* [Deployment Guide](README.md)
* [VPN Gateway Configuration](vpn.md)
* [Infrastructure Reference](infrastructure-reference.md)
* [Cluster Setup](cluster-setup.md)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
