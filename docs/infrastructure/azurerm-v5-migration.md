---
title: AzureRM v5 Migration
description: Safe state migration procedure for existing AzureRM v4 infrastructure deployments
author: Microsoft Robotics-AI Team
ms.date: 2026-08-31
ms.topic: how-to
keywords:
  - terraform
  - azurerm
  - migration
  - state
---

Migrate existing AzureRM v4 deployments with state-backed plans and secure backups. Complete this procedure for each deployed Terraform root before using AzureRM v5 to change Azure resources.

> [!CAUTION]
> Do not use fresh-deployment commands against existing infrastructure. AzureRM v5 can refresh state into a format that AzureRM v4 cannot read. A provider downgrade over v5-refreshed state is unsupported.

## Identify Deployed Roots

Treat each deployment root as an independent state owner. Migrate only roots that have authentic state and preserve the original variables and command arguments used for each deployment.

| Root       | Directory                             | Required original input                                       |
|------------|---------------------------------------|---------------------------------------------------------------|
| Main       | `infrastructure/terraform`            | `terraform.tfvars` and any CLI variable overrides             |
| VPN        | `infrastructure/terraform/vpn`        | `terraform.tfvars` and any CLI variable overrides             |
| DNS        | `infrastructure/terraform/dns`        | Current `osmo_loadbalancer_ip` and any DNS variable overrides |
| Automation | `infrastructure/terraform/automation` | `terraform.tfvars` and any CLI variable overrides             |

From the workstation and backend that own a root's state, inspect its workspace and managed resources before selecting it for migration:

```bash
cd <deployed-root>
terraform workspace show
terraform state list
```

An empty or unavailable state is not migration evidence. Locate the authentic backend and workspace instead of creating replacement state or importing resources during this procedure.

## Back Up Pre-v5 State

Back up every selected root before running `terraform init -upgrade`. Terraform state and saved plans can contain secrets. Store both outside the repository in access-controlled, encrypted storage, and never commit them to Git.

```bash
umask 077
export MIGRATION_DIR=/secure/path/azurerm-v5-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$MIGRATION_DIR"

cd <deployed-root>
terraform state pull > "$MIGRATION_DIR/<root>-pre-v5.tfstate"
terraform workspace show > "$MIGRATION_DIR/<root>-workspace.txt"
terraform state list > "$MIGRATION_DIR/<root>-resources.txt"
```

Copy the matching pre-v5 repository revision, variable files, backend configuration, and CLI arguments into the protected migration record. Verify that the state backup is non-empty and restrict access before continuing.

## Verify Subscription and Providers

Return to the repository root, initialize the Azure CLI context, and verify the selected subscription before registration or planning:

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
az account show --query "{name:name, id:id, tenantId:tenantId}" -o table
```

Stop if the subscription is not the one that owns the selected state. Register the complete prerequisite manifest with an identity authorized to register resource providers:

```bash
bash infrastructure/terraform/prerequisites/register-azure-providers.sh
```

The registration script is idempotent. Do not proceed until every required namespace reports `Registered`.

## Initialize and Save a Plan

Run the upgrade and plan from one selected root. Supply the original backend, workspace, variables, and DNS load balancer IP exactly as used by the existing deployment.

```bash
cd <deployed-root>
terraform workspace select <workspace>
terraform init -upgrade
export PLAN_FILE="$MIGRATION_DIR/<root>-v5.tfplan"
terraform plan <original-plan-arguments> -out="$PLAN_FILE"
terraform show -no-color "$PLAN_FILE" > "$MIGRATION_DIR/<root>-v5-plan.txt"
```

For the DNS root, `<original-plan-arguments>` must include the current value, for example `-var="osmo_loadbalancer_ip=10.0.x.x"`. For roots managed with a variable file, include `-var-file=terraform.tfvars`.

Review the complete saved plan. Reject the migration if Terraform proposes replacement or deletion of any of these resources:

* AKS clusters or node pools
* Log Analytics workspaces
* Private DNS zones, links, or records
* Storage accounts or containers
* Container Apps environments
* Container registries
* VPN gateways or their public IP addresses
* Automation accounts

Investigate any other unexpected deletion, replacement, permission change, network change, or material drift before applying. Do not apply a plan created from missing inputs, a different workspace, or a different subscription.

## Apply One Root at a Time

Apply only the reviewed saved plan. Do not create a new plan between approval and application.

```bash
cd <deployed-root>
terraform apply "$PLAN_FILE"
```

Complete service checks and a clean follow-up plan before starting another root:

```bash
terraform output
terraform plan <original-plan-arguments> -detailed-exitcode
```

Exit code `0` confirms a clean follow-up plan. Exit code `2` indicates remaining changes and blocks the next root. Exit code `1` indicates an error.

Use the checks that match the migrated root:

| Root       | Required service checks                                                                                          |
|------------|------------------------------------------------------------------------------------------------------------------|
| Main       | Confirm AKS health and node readiness, Log Analytics access, registry and storage access, and application health |
| VPN        | Confirm gateway provisioning, download a current client profile, connect, and resolve private endpoints          |
| DNS        | Resolve the OSMO hostname through the VPN and confirm it maps to the current internal load balancer IP           |
| Automation | Confirm the account, runbooks, schedules, managed identity, and target-resource permissions                      |

Record the approved plan, apply output, checks, and clean follow-up plan in the protected migration record. Repeat the initialization, review, apply, and verification sequence for the next independently deployed root.

## Roll Back

Prefer correcting the v5 configuration and moving forward. Do not install AzureRM v4 over state that AzureRM v5 refreshed, planned, or applied.

Rollback requires the matching pre-v5 configuration and pre-v5 state backup together:

1. Stop all Terraform operations for the affected root.
2. Record the failed operation and inspect the actual Azure resources before changing state.
3. Restore the exact pre-v5 repository revision, backend configuration, workspace, variables, and CLI arguments.
4. Restore the matching pre-v5 state backup through the state backend's approved recovery process.
5. Initialize the pre-v5 configuration and verify that its plan matches the actual Azure resources before any apply.

Restoring state does not reverse Azure mutations. Reconcile partial Azure changes with the deployment owner before restoring or applying state. If the pre-v5 configuration and its matching state backup are unavailable, provider downgrade is not a rollback path.

## Related Documentation

* [Infrastructure Deployment](infrastructure.md)
* [Infrastructure Reference](infrastructure-reference.md)
* [Prerequisites](prerequisites.md)

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
