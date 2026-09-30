---
sidebar_position: 11
title: Manage Node Pools
description: Add, remove, and resize AKS node pools on an existing cluster
author: Microsoft Robotics-AI Team
ms.date: 2026-09-29
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

All node pools are driven by the `node_pools` Terraform variable in `infrastructure/terraform/`. The variable is a map keyed by pool name; Terraform uses `for_each` over the map to manage each pool, its subnet, NSG associations, and NAT gateway associations independently. A pool that shares another pool's subnet through `subnet_pool_key` has no subnet or associations of its own.

Pool changes follow the standard repo flow:

1. Edit `node_pools` in `infrastructure/terraform/terraform.tfvars`.
2. Run `terraform apply` to create, destroy, or update pool resources.
3. Regenerate the environment bundle so `infrastructure/setup/generated/<environment>/osmo-platforms.yaml` matches the applied pool configuration.
4. If the new pool requires different `nodeSelector`, tolerations, or resource overrides, rerun `infrastructure/setup/03-deploy-osmo.sh` with the generated platform values.

Script `03` deploys the selected `osmo-platforms.yaml` as a Helm values overlay. Pass the generated file with `--platform-values`; the checked-in `infrastructure/setup/values/osmo-platforms.yaml` is an instructional fallback, not environment-specific desired state. Rerun is only needed when platform configuration changes (nodeSelector, tolerations, resource limits) — not for count-only scaling changes.

> [!NOTE]
> Single-node multi-GPU jobs require an OSMO platform whose pod-template `nodeSelector` targets a multi-GPU node SKU.
> The shipped `gpu_platform_2x` platform (pod template `gpu_tpl_2x`) binds a 2x A100 pool and is selected via the workflow `platform` field — for example `submit-osmo-lerobot-training.sh --num-gpus 2 --platform gpu_platform_2x`.
> The single-GPU `gpu_platform` cannot satisfy a 2-GPU request because its node SKU exposes one GPU. Add further multi-GPU platforms by copying this pair in `osmo-platforms.yaml`.

## Prerequisites

- Terraform state in `infrastructure/terraform/` matches the deployed cluster.
- `kubectl`, `terraform`, `az`, `helm`, `osmo`, and `jq` available on `PATH`.
- Active Azure CLI session (`az login`) with rights to modify the cluster resource group.
- VPN connection if the cluster is private (default).
- The same flags you originally passed to `03-deploy-osmo.sh` (for example, `--use-acr`).
- A refreshed environment bundle under `infrastructure/setup/generated/<environment>/` when pool scheduling properties change.

## What Can and Cannot Change in Place

These fields on a `node_pools` entry are `ForceNew` — editing them destroys and recreates the pool under the same name:

| Field                          | In-place? | Notes                                                                |
|--------------------------------|-----------|----------------------------------------------------------------------|
| `vm_size`                      | No        | VMSS SKU is immutable; AKS rejects in-place SKU changes              |
| `subnet_address_prefixes`      | No        | The subnet itself is also a `ForceNew` resource                      |
| `subnet_pool_key`              | No        | Moves the pool to another subnet; add a new pool instead             |
| `zones`                        | No        | Availability zone is set at pool creation                            |
| `priority`                     | No        | `Regular` vs `Spot` is set at pool creation                          |
| `eviction_policy`              | No        | Tied to `priority`; only valid for `Spot`                            |
| `gpu_driver`                   | No        | Affects pool creation flags                                          |
| `node_count`                   | Yes       | When autoscaler is disabled                                          |
| `min_count`, `max_count`       | Yes       | When autoscaler is enabled                                           |
| `should_enable_auto_scaling`   | Yes       | Toggling on/off updates the existing pool                            |
| `node_labels`                  | Yes       | Applied to existing nodes                                            |
| `node_taints`                  | Yes       | Applied to existing nodes (workloads may be evicted)                 |
| `max_surge`, `max_unavailable` | Yes       | Non-Spot pools only; set at most one                                 |
| `undrainable_node_behavior`    | Yes       | Non-Spot pools only; removing it after it was set recreates the pool |

Anything in the "No" rows means choosing between two flows:

- **Add new pool, then remove old** (recommended for SKU upgrades): zero capacity gap, no forced eviction. Workloads migrate at your pace.
- **In-place replace** (simpler, but pool goes away before the new one is ready): brief capacity gap, all pods on the pool evicted at once.

## Workflows

### List Current Pools

```bash
terraform -chdir=infrastructure/terraform output -json | \
  jq -r '.node_pools.value | to_entries[] | "\(.key)\t\(.value.vm_size)\t\(.value.priority)\tautoscale=\(.value.should_enable_auto_scaling)\tcount=\(.value.node_count)\tmin=\(.value.min_count)\tmax=\(.value.max_count)"'
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

3. Rerun the OSMO control-plane script if taints or labels changed (not needed for count-only changes):

   ```bash
   bash infrastructure/setup/03-deploy-osmo.sh \
     --platform-values infrastructure/setup/generated/<environment>/osmo-platforms.yaml \
     --use-acr
   ```

### Park a Pool Without Nodes

Park a pool when its VM size has no quota or capacity yet but you want to keep its definition. A parked pool has autoscaling off and no nodes, so no `terraform apply`, cluster start, or pending pod tries to allocate a node.

```hcl
node_pools = {
  rtxprogpu = {
    vm_size                    = "Standard_NC144ds_xl_RTXPRO6000BSE_v6"
    subnet_address_prefixes    = ["10.0.7.0/24"]
    node_taints                = ["nvidia.com/gpu:NoSchedule"]
    gpu_driver                 = "Install"
    should_enable_auto_scaling = false
    node_count                 = 0
  }
}
```

Parking an existing pool changes only its autoscaling and node count, so Terraform updates it in place without creating VMs. Leave `vm_size`, `gpu_driver`, and the subnet as they are; changing any of them replaces the pool.

Nothing can run on a parked pool, so remove what targets it:

| Target                                      | How to remove it                                                                                                                                                                  |
|---------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Azure ML InstanceType that selects the pool | `kubectl delete instancetype <name>`. Applying a regenerated manifest doesn't delete it, and with resource validation skipped, Azure ML jobs for it wait in Pending indefinitely. |
| OSMO pool, platform, and pod template       | Regenerate the environment bundle, which leaves parked pools out, then rerun `03-deploy-osmo.sh` with its `--platform-values` file.                                               |

The `node_pools` Terraform output reports `should_enable_auto_scaling` and `node_count`, which is how bundle generation recognizes a parked pool. To bring the pool back once quota exists, restore its autoscaling or node count, apply, and regenerate the bundle.

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

3. Regenerate the environment bundle so its OSMO platform values include the new pool.

4. Rerun the OSMO control-plane script so the new pool appears in `POOL`, `PLATFORM`, and `POD_TEMPLATE` configs:

   ```bash
   bash infrastructure/setup/03-deploy-osmo.sh \
     --platform-values infrastructure/setup/generated/<environment>/osmo-platforms.yaml \
     --use-acr
   ```

5. Verify:

   ```bash
   kubectl get nodes -l agentpool=sdgcpu
   az aks nodepool list --resource-group <rg> --cluster-name <aks> -o table
   ```

   The OSMO-side pool/platform configuration is applied by the rerun in step 4; a successful run is the confirmation. The environment-specific pool definition lives in the generated bundle. The checked-in [`infrastructure/setup/values/osmo-platforms.yaml`](../../infrastructure/setup/values/osmo-platforms.yaml) remains an instructional fallback.

### Share a Subnet with Another Pool

Set `subnet_pool_key` to another entry's key when a pool should run in that entry's subnet instead of its own. The sharing entry omits `subnet_address_prefixes` and creates no subnet, NSG association, or NAT gateway association. The entry it names must own its subnet; it can't share one itself.

```hcl
node_pools = {
  h100spot = {
    vm_size                    = "Standard_NC40ads_H100_v5"
    subnet_address_prefixes    = ["10.0.8.0/24"]
    priority                   = "Spot"
    eviction_policy            = "Delete"
    should_enable_auto_scaling = true
    min_count                  = 0
    max_count                  = 1
    node_taints                = ["nvidia.com/gpu:NoSchedule", "kubernetes.azure.com/scalesetpriority=spot:NoSchedule"]
    node_labels                = { accelerator = "nvidia" }
    gpu_driver                 = "Install"
  }
  h100ondemand = {
    vm_size                   = "Standard_NC40ads_H100_v5"
    subnet_pool_key           = "h100spot"
    node_count                = 1
    gpu_driver                = "Install"
    undrainable_node_behavior = "Schedule"
  }
}
```

Size the owner's subnet for the nodes of every pool that uses it. Sharing also lets Terraform import a pool that was created in another pool's subnet without moving it.

### Control Pool Upgrades

Non-Spot pools accept three upgrade settings. When none is set, the module uses `max_surge = "10%"` with no drain timeout or soak time, as before.

| Field                       | Values                   | Effect                                                                                                                                                       |
|-----------------------------|--------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `max_surge`                 | Node count or percentage | Extra nodes AKS adds during an upgrade. Each surge node counts against quota.                                                                                |
| `max_unavailable`           | Node count or percentage | Nodes AKS takes offline instead of adding surge nodes. `"1"` lets a single-node GPU pool upgrade without surge quota, with downtime while its node upgrades. |
| `undrainable_node_behavior` | `Cordon` or `Schedule`   | What AKS does with a node it can't drain. Removing the setting after it was set recreates the pool.                                                          |

Set at most one of `max_surge` or `max_unavailable`. Spot pools don't support upgrade settings, and the module rejects them on Spot entries.

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

3. Regenerate the environment bundle to remove the pool, then rerun the OSMO control-plane script:

   ```bash
   bash infrastructure/setup/03-deploy-osmo.sh \
     --platform-values infrastructure/setup/generated/<environment>/osmo-platforms.yaml \
     --use-acr
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

3. Regenerate the environment bundle, then rerun the OSMO control-plane script:

   ```bash
   bash infrastructure/setup/03-deploy-osmo.sh \
     --platform-values infrastructure/setup/generated/<environment>/osmo-platforms.yaml \
     --use-acr
   ```

## Operational Notes

| Topic                       | Guidance                                                                                                                                                                                                                                                                                                                                                                                               |
|-----------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Subnet planning             | Every pool gets its own subnet unless it sets `subnet_pool_key`. Pick a CIDR that does not overlap `aks_subnet_config` or any other pool's `subnet_address_prefixes`, and size a shared subnet for every pool in it. AKS Overlay mode applies to pods; size the node IP space here.                                                                                                                    |
| OSMO flag parity            | Pass the same flags used for the initial `03-deploy-osmo.sh` run, including `--platform-values` and options such as `--use-acr`. Omitting them reverts the deployment to defaults.                                                                                                                                                                                                                     |
| Spot constraints            | Azure rejects `upgrade_settings` for Spot pools; the Terraform module omits them and rejects `max_surge`, `max_unavailable`, and `undrainable_node_behavior` on Spot entries. `eviction_policy` applies only when `priority = "Spot"`.                                                                                                                                                                 |
| Autoscaling                 | `min_count = 0` is allowed; the pool scales up on demand from pending pods. KAI/Volcano coscheduling requires whole-pool capacity for gang-scheduled jobs.                                                                                                                                                                                                                                             |
| Scale-from-zero for AzureML | GPU pools used by AzureML jobs must declare `node_labels = { accelerator = "nvidia" }`. Without this static label, the cluster autoscaler cannot prove a from-zero scale-up would satisfy AzureML InstanceTypes that select on `accelerator: nvidia`, and refuses to scale. See [Azure ML Training Workflows — Scale-from-zero GPU Pools](../training/azureml-training.md#-scale-from-zero-gpu-pools). |
| OSMO reconciliation         | Regenerate the environment bundle after scheduling-property changes, then rerun `03-deploy-osmo.sh` with its `--platform-values` file to reconcile pool, platform, and backend configuration.                                                                                                                                                                                                          |

## 🔗 Related

- [Cluster Setup](cluster-setup.md) — initial deployment and scenarios
- [Cluster Operations](cluster-setup-advanced.md) — troubleshooting and optional scripts
- [Infrastructure Reference](infrastructure-reference.md) — `node_pools` variable schema

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
