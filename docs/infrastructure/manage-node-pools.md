---
sidebar_position: 11
title: Manage Node Pools
description: Add, remove, and resize AKS node pools on an existing cluster
author: Microsoft Robotics-AI Team
ms.date: 2026-05-12
ms.topic: how-to
keywords:
  - node-pools
  - aks
  - osmo
  - scaling
---

Add, remove, and resize AKS GPU and CPU node pools on a running cluster, then reconcile OSMO pool, platform, and pod-template configs without redeploying infrastructure.

> [!NOTE]
> This workflow is for adjusting pool composition after initial deployment. For first-time cluster provisioning, see [Cluster Setup](cluster-setup.md).

## When to Use

Use this when a workload requires resources the existing pools cannot provide. An AKS node pool has a single VM SKU, so changing the SKU means provisioning a new pool — node pool resources cannot be edited in place beyond a few mutable fields (see [What Can and Cannot Change in Place](#what-can-and-cannot-change-in-place)).

Examples:

- An SDG workflow requires `>= 6.5` vCPU but the initial pool uses `Standard_B4` (4 vCPU). Add a new pool with a larger SKU.
- A new model needs H100 GPUs, but only A10 Spot nodes exist. Add a new H100 pool alongside the existing A10 pool.
- A pool is no longer used and should be removed to reclaim quota.

## How It Works

All node pools are driven by the `node_pools` Terraform variable in `infrastructure/terraform/`. The variable is a map keyed by pool name; Terraform uses `for_each` over the map to manage each pool, its subnet, NSG associations, and NAT gateway associations independently.

Pool changes follow the standard repo flow:

1. Edit `node_pools` in `infrastructure/terraform/terraform.tfvars`.
2. Run `terraform apply` to create, destroy, or update pool resources.
3. Rerun `infrastructure/setup/04-deploy-osmo-backend.sh` to regenerate OSMO `POD_TEMPLATE`, `POOL`, and `BACKEND` configs against the new pool list.

Script `04` reads `node_pools` from Terraform state and embeds per-pool values (VM size in `nodeSelector`, taints as tolerations) into OSMO configs. **Skipping the rerun leaves stale OSMO configs**: workflow pods pin `nodeSelector` to the previous VM SKU and stay `Pending`.

## Prerequisites

- Terraform state in `infrastructure/terraform/` matches the deployed cluster.
- `kubectl`, `terraform`, `az`, `helm`, `osmo`, and `jq` available on `PATH`.
- Active Azure CLI session (`az login`) with rights to modify the cluster resource group.
- VPN connection if the cluster is private (default).
- The same flags you originally passed to `04-deploy-osmo-backend.sh` (for example, `--use-acr`).

## What Can and Cannot Change in Place

These fields on a `node_pools` entry are `ForceNew` — editing them destroys and recreates the pool under the same name:

| Field                        | In-place? | Notes                                                   |
|------------------------------|-----------|---------------------------------------------------------|
| `vm_size`                    | No        | VMSS SKU is immutable; AKS rejects in-place SKU changes |
| `subnet_address_prefixes`    | No        | The subnet itself is also a `ForceNew` resource         |
| `zones`                      | No        | Availability zone is set at pool creation               |
| `priority`                   | No        | `Regular` vs `Spot` is set at pool creation             |
| `eviction_policy`            | No        | Tied to `priority`; only valid for `Spot`               |
| `gpu_driver`                 | No        | Affects pool creation flags                             |
| `node_count`                 | Yes       | When autoscaler is disabled                             |
| `min_count`, `max_count`     | Yes       | When autoscaler is enabled                              |
| `should_enable_auto_scaling` | Yes       | Toggling on/off updates the existing pool               |
| `node_labels`                | Yes       | Applied to existing nodes                               |
| `node_taints`                | Yes       | Applied to existing nodes (workloads may be evicted)    |

Anything in the "No" rows means choosing between two flows:

- **Add new pool, then remove old** (recommended for SKU upgrades): zero capacity gap, no forced eviction. Workloads migrate at your pace.
- **In-place replace** (simpler, but pool goes away before the new one is ready): brief capacity gap, all pods on the pool evicted at once.

## Workflows

### List Current Pools

```bash
terraform -chdir=infrastructure/terraform output -json | \
  jq -r '.node_pools.value | to_entries[] | "\(.key)\t\(.value.vm_size)\t\(.value.priority)"'
```

### Resize an Existing Pool (In-Place)

Resizing means changing `node_count`, `min_count`, `max_count`, `node_labels`, or `node_taints`. None of these recreate the pool.

1. Edit `infrastructure/terraform/terraform.tfvars`:

   ```hcl
   node_pools = {
     gpu = {
       vm_size                    = "Standard_NV36ads_A10_v5"
       subnet_address_prefixes    = ["10.0.7.0/24"]
       priority                   = "Spot"
       should_enable_auto_scaling = true
       min_count                  = 1
       max_count                  = 4   # changed from 1
       eviction_policy            = "Delete"
       node_taints                = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
       gpu_driver                 = "Install"
     }
   }
   ```

2. Apply:

   ```bash
   source infrastructure/terraform/prerequisites/az-sub-init.sh
   terraform -chdir=infrastructure/terraform apply
   ```

3. Rerun the OSMO backend script if taints or labels changed (not needed for count-only changes):

   ```bash
   bash infrastructure/setup/04-deploy-osmo-backend.sh --use-acr
   ```

### Add a New Pool

Use this to add capacity (different SKU, different priority, different zones) without disturbing existing pools.

1. Add a new map entry in `terraform.tfvars` alongside the existing pools. Pick a non-overlapping subnet:

   ```hcl
   node_pools = {
     gpu = { ... }                                     # existing - unchanged
     sdgcpu = {                                        # new
       vm_size                    = "Standard_D8ds_v5"
       subnet_address_prefixes    = ["10.0.12.0/24"]
       priority                   = "Regular"
       should_enable_auto_scaling = false
       node_count                 = 1
     }
   }
   ```

2. Apply Terraform (`for_each` creates only the new pool, its subnet, and NSG/NAT associations):

   ```bash
   terraform -chdir=infrastructure/terraform apply
   ```

3. Rerun the OSMO backend script so the new pool appears in `POOL` and `POD_TEMPLATE` configs:

   ```bash
   bash infrastructure/setup/04-deploy-osmo-backend.sh --use-acr
   ```

4. Verify:

   ```bash
   kubectl get nodes -l agentpool=sdgcpu
   az aks nodepool list --resource-group <rg> --cluster-name <aks> -o table
   osmo config show POOL
   ```

### Remove a Pool

1. Drain workloads off the pool. For OSMO workflows, stop submitting to that pool and let active workflows finish, or cordon the nodes:

   ```bash
   kubectl get nodes -l agentpool=<pool> -o name | xargs -I {} kubectl cordon {}
   kubectl get nodes -l agentpool=<pool> -o name | xargs -I {} kubectl drain {} --ignore-daemonsets --delete-emptydir-data
   ```

2. Delete the map entry from `terraform.tfvars` and apply:

   ```bash
   terraform -chdir=infrastructure/terraform apply
   ```

3. Update `DEFAULT_POOL` in `infrastructure/setup/.env.local` if it pointed at the removed pool, then rerun the OSMO backend script:

   ```bash
   bash infrastructure/setup/04-deploy-osmo-backend.sh --use-acr
   ```

### Replace a Pool SKU (Two-Step, No Capacity Gap)

Recommended path for upgrading from one SKU to another without evicting workloads.

1. Add the new pool with a different name (see [Add a New Pool](#add-a-new-pool)).
2. Migrate workloads. For OSMO, submit new workflows targeting the new pool; let active workflows on the old pool drain.
3. Remove the old pool (see [Remove a Pool](#remove-a-pool)).

### Replace a Pool SKU (In-Place, With Capacity Gap)

Faster but disruptive. Use only when no workloads are running on the pool, or when downtime is acceptable.

1. Edit `vm_size` on the existing map entry:

   ```hcl
   node_pools = {
     gpu = {
       vm_size = "Standard_NC40ads_H100_v5"   # changed
       # ...
     }
   }
   ```

2. Apply - Terraform plans `-/+ destroy and replace`:

   ```bash
   terraform -chdir=infrastructure/terraform apply
   ```

   All nodes in the pool are evicted at once; new nodes come up under the same pool name.

3. Rerun the OSMO backend script:

   ```bash
   bash infrastructure/setup/04-deploy-osmo-backend.sh --use-acr
   ```

## Operational Notes

- **Subnet planning.** Every pool gets its own subnet. Pick a CIDR that does not overlap `aks_subnet_config` or any other pool's `subnet_address_prefixes`. AKS Overlay mode applies to pods; the node IP space is what you size here.
- **Default pool.** `DEFAULT_POOL` in `infrastructure/setup/.env.local` must reference a configured pool. `04-deploy-osmo-backend.sh` auto-selects the first pool alphabetically when the variable is unset and warns.
- **OSMO flag parity.** Pass the same flags you used for the initial `04-deploy-osmo-backend.sh` run (for example, `--use-acr`, `--use-access-keys`). Omitting them reverts the backend to defaults.
- **Spot constraints.** Azure rejects `upgrade_settings` for Spot pools; the Terraform module already handles this. `eviction_policy` applies only when `priority = "Spot"`.
- **Autoscaling.** `min_count = 0` is allowed; the pool scales up on demand from pending pods. KAI/Volcano coscheduling requires whole-pool capacity for gang-scheduled jobs.
- **Script 03 is not affected.** Only `04-deploy-osmo-backend.sh` reads pool data. `03-deploy-osmo-control-plane.sh` does not need to be rerun for pool changes.

## 🔗 Related

- [Cluster Setup](cluster-setup.md) — initial deployment and scenarios
- [Cluster Operations](cluster-setup-advanced.md) — troubleshooting and optional scripts
- [Infrastructure Reference](infrastructure-reference.md) — `node_pools` variable schema

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
