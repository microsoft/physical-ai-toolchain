---
sidebar_position: 11
title: Manage Node Pools
description: Add and remove AKS node pools on an existing cluster without redeploying infrastructure
author: Microsoft Robotics-AI Team
ms.date: 2026-04-24
ms.topic: how-to
keywords:
  - node-pools
  - aks
  - osmo
  - scaling
---

Add, remove, or resize AKS GPU and CPU node pools on a running cluster and reconcile OSMO pool, platform, and pod-template configs without rerunning infrastructure deployment.

> [!NOTE]
> This workflow is for adjusting pool composition after initial deployment. For first-time cluster provisioning, see [Cluster Setup](cluster-setup.md).

## When to Use

Use this when a workload requires resources the existing pools cannot provide. Examples:

- An SDG workflow requires `>= 6.5` vCPU but the initial pool uses `Standard_B4` (4 vCPU).
- A new model needs H100 GPUs, but only A10 Spot nodes exist.
- A pool is no longer used and should be removed to reclaim quota.

## How It Works

The `node_pools` Terraform variable in `infrastructure/terraform/` drives AKS node pool creation through `for_each`. Adding or removing a map key causes Terraform to create or destroy only that pool and its subnet; existing pools, the control plane, and cluster state are untouched.

The script maintains a managed overlay at `infrastructure/terraform/node-pools.managed.auto.tfvars.json`. Terraform auto-loads `*.auto.tfvars.json` files after `terraform.tfvars`, so the overlay becomes the effective source of truth for `node_pools`. On first `add` or `remove`, the script seeds the overlay from whatever Terraform currently resolves `var.node_pools` to, then applies the mutation.

After `terraform apply` succeeds, the script re-runs `04-deploy-osmo-backend.sh`, which regenerates the OSMO `POD_TEMPLATE`, `POOL`, and `BACKEND` configs against the new pool list. The backend operator reloads these configs automatically.

## Prerequisites

- Terraform state in `infrastructure/terraform/` matches the deployed cluster.
- `kubectl`, `terraform`, `az`, `helm`, `osmo`, and `jq` available on `PATH`.
- Active Azure CLI session (`az login`) with rights to modify the cluster resource group.
- Active OSMO session compatible with the flags previously passed to `04-deploy-osmo-backend.sh` (for example, `--use-acr`).
- VPN connection if the cluster is private (default).

## Usage

```bash
bash infrastructure/setup/optional/manage-node-pools.sh <command> [OPTIONS]
```

| Command  | Purpose                                                                    |
|----------|----------------------------------------------------------------------------|
| `list`   | Print configured node pools from current Terraform state                   |
| `add`    | Create a new node pool, apply Terraform, and sync OSMO configs             |
| `remove` | Destroy a node pool, apply Terraform, and sync OSMO configs                |
| `sync`   | Re-render OSMO `POD_TEMPLATE`, `POOL`, and `BACKEND` configs only          |

### Common Options

| Flag                 | Purpose                                                                   |
|----------------------|---------------------------------------------------------------------------|
| `-t`, `--tf-dir DIR` | Terraform directory (default: `infrastructure/terraform/`)                |
| `--skip-apply`       | Update the overlay file but skip `terraform apply`                        |
| `--skip-osmo-sync`   | Skip `04-deploy-osmo-backend.sh` reconciliation                           |
| `--osmo-args ARGS`   | Extra args forwarded to `04-deploy-osmo-backend.sh` (quote the whole string) |

### Add Options

| Flag                        | Required  | Description                                                   |
|-----------------------------|-----------|---------------------------------------------------------------|
| `--name NAME`               | yes       | Terraform map key and AKS node pool name                      |
| `--vm-size SIZE`            | yes       | Azure VM size (for example, `Standard_D8ds_v5`)               |
| `--subnet CIDR`             | yes       | Subnet address prefix; must not overlap existing subnets      |
| `--priority P`              | no        | `Regular` or `Spot` (default `Regular`)                       |
| `--node-count N`            | see below | Fixed node count                                              |
| `--auto-scale`              | see below | Enable cluster autoscaler on this pool                        |
| `--min-count N`             | see below | Min nodes with `--auto-scale`                                 |
| `--max-count N`             | see below | Max nodes with `--auto-scale`                                 |
| `--eviction-policy P`       | no        | `Delete` or `Deallocate` (Spot only, default `Delete`)        |
| `--gpu-driver D`            | no        | `Install` or `None`; only set for GPU pools                   |
| `--taint KEY=VAL:EFFECT`    | no        | Node taint; repeatable                                        |
| `--label KEY=VAL`           | no        | Node label; repeatable                                        |
| `--zone Z`                  | no        | Availability zone; repeatable                                 |

Provide exactly one of `--node-count` or `--auto-scale`. `--auto-scale` requires both `--min-count` and `--max-count`.

## Examples

### List Current Pools

```bash
bash infrastructure/setup/optional/manage-node-pools.sh list
```

Output:

```text
NAME                 VM_SIZE                              PRIORITY   AUTOSCALE COUNT      TAINTS
gpu                  Standard_NV36ads_A10_v5              Spot       true     1-1        nvidia.com/gpu:NoSchedule,kubernetes.azure.com/scalesetpriority=spot:NoSchedule
```

### Add a CPU Pool for SDG

Add an 8-vCPU pool so workflows that require more than 4 vCPU can schedule:

```bash
bash infrastructure/setup/optional/manage-node-pools.sh add \
  --name sdgcpu \
  --vm-size Standard_D8ds_v5 \
  --subnet 10.0.12.0/24 \
  --node-count 1 \
  --osmo-args '--use-acr'
```

### Add a Spot H100 Pool with Autoscaling

```bash
bash infrastructure/setup/optional/manage-node-pools.sh add \
  --name h100spot \
  --vm-size Standard_NC40ads_H100_v5 \
  --subnet 10.0.13.0/24 \
  --priority Spot --eviction-policy Delete \
  --auto-scale --min-count 0 --max-count 2 \
  --taint 'nvidia.com/gpu=:NoSchedule' \
  --taint 'kubernetes.azure.com/scalesetpriority=spot:NoSchedule' \
  --label 'kubernetes.azure.com/scalesetpriority=spot' \
  --gpu-driver Install \
  --osmo-args '--use-acr'
```

### Remove a Pool

```bash
bash infrastructure/setup/optional/manage-node-pools.sh remove \
  --name h100spot \
  --osmo-args '--use-acr'
```

### Resync OSMO Configs Only

Use after manually editing `terraform.tfvars` or the managed overlay:

```bash
terraform -chdir=infrastructure/terraform apply
bash infrastructure/setup/optional/manage-node-pools.sh sync --osmo-args '--use-acr'
```

## Verification

After `add`:

```bash
kubectl get nodes -L agentpool
az aks nodepool list --resource-group <rg> --cluster-name <aks> -o table
osmo config show POOL
```

After `remove`, confirm the pool no longer appears in `az aks nodepool list` and is absent from `osmo config show POOL`.

## Operational Notes

- **Subnet planning.** Every pool gets its own subnet. Pick a CIDR that does not overlap `aks_subnet_config` or any other pool's `subnet_address_prefixes`.
- **Default pool.** If `DEFAULT_POOL` in `.env.local` points at the pool being removed, update it before running `remove`. The OSMO backend script will fail if the value no longer matches a configured pool.
- **Source-of-truth drift.** Once the overlay exists, continue using the script or edit the overlay directly. Mixing edits between `terraform.tfvars` and the overlay causes confusion because the overlay wins.
- **OSMO flags.** Pass the same flags you used for the initial `04-deploy-osmo-backend.sh` via `--osmo-args` (for example, `--use-acr`, `--use-access-keys`). Omitting them reverts the backend to defaults.
- **Spot constraints.** Azure rejects `upgrade_settings` for Spot pools; the Terraform module already handles this. `eviction_policy` applies only when `--priority Spot`.
- **Autoscaling.** `--min-count 0` is allowed; the pool scales up on demand from pending pods.

## 🔗 Related

- [Cluster Setup](cluster-setup.md) — initial deployment and scenarios
- [Cluster Operations](cluster-setup-advanced.md) — troubleshooting and optional scripts
- [Infrastructure Reference](infrastructure-reference.md) — `node_pools` variable schema

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
