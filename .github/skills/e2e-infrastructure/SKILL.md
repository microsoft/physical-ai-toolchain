---
name: e2e-infrastructure
description: "Deploy or undeploy the complete Physical AI E2E test infrastructure, including Terraform, robotics charts, Azure ML, and OSMO."
compatibility: "Linux with Bash 4+, Azure CLI, Terraform, kubectl, Helm, jq, envsubst, Python 3, and OSMO CLI."
---

<!-- cspell:ignore tfvars undeploying worktree -->

# E2E Test Infrastructure

Deploy or undeploy the dedicated cloud infrastructure used by the Physical AI E2E tests. This skill manages infrastructure only; invoke the `e2e-tests` skill separately to run tests.

## Fixed Paths

Resolve paths from the repository checkout that contains this skill:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
SKILL_DIR="$REPO_ROOT/.github/skills/e2e-infrastructure"
TF_DIR="$REPO_ROOT/infrastructure/terraform"
TFVARS="$TF_DIR/terraform.tfvars"
SETUP_DIR="$REPO_ROOT/infrastructure/setup"
DRIVER="$SKILL_DIR/scripts/manage-e2e-infrastructure.sh"
```

Do not operate on another checkout, Terraform directory, variable file, workspace, or state. Do not create a remote backend. The canonical E2E variable template is bundled at `templates/terraform.tfvars.example`. Pass the resolved `REPO_ROOT` to the driver so worktree sessions use their own checkout and Terraform state.

## Operating Contract

Use the bundled Bash driver for both interactive and Azure Automation Hybrid Runbook Worker execution:

```bash
bash "$DRIVER" deploy --repo-root "$REPO_ROOT"
bash "$DRIVER" undeploy \
  --repo-root "$REPO_ROOT" \
  --confirm-resource-group <terraform-resolved-resource-group>
```

Create `infrastructure/terraform/terraform.tfvars` before deployment to override the bundled E2E defaults. When the file is absent, the driver copies `templates/terraform.tfvars.example` and selects an unused instance.

### Personal environment overrides

Keep user-specific subscription, governance, expiry, and naming requirements in personal Copilot instructions rather than the tracked skill or template. Personal instructions complement this skill by preparing its supported inputs before the driver runs; they must not replace the driver or weaken its state, retry, verification, or undeploy checks.

Use this pattern in a user-level instruction:

```markdown
Scope: `/e2e-infrastructure deploy` in `microsoft/physical-ai-toolchain`.

- Before invoking the bundled driver, ensure the checkout-local, gitignored `infrastructure/terraform/terraform.tfvars` exists. If absent, copy `.github/skills/e2e-infrastructure/templates/terraform.tfvars.example`.
- Add the organization-required entries to the existing `tags` map. Preserve every existing setting and tag, and do not edit the tracked template for personal policy.
- Compute any environment-specific expiry tag in the personal instruction and add it to the local `tags` map before invoking the driver.
- Follow `.github/skills/e2e-infrastructure/SKILL.md` for all deployment and undeployment behavior.
```

For example, a personal instruction can require this checkout-local configuration without recording the real tag names or values in the repository:

```hcl
tags = {
  Purpose     = "e2e-testing"
  RequiredTag = "<organization-required-value>"
}
```

The driver reads `var.tags`, merges the configured tags onto the external resource group, and passes the variable file to Terraform. An existing `terraform.tfvars` remains the configuration baseline for resumed deployments.

Optional deployment controls:

| Input                      | Environment variable | Default                  | Purpose                                                                  |
|----------------------------|----------------------|--------------------------|--------------------------------------------------------------------------|
| `--resource-owner`         | `E2E_RESOURCE_OWNER` | Signed-in Azure username | Set the owner appended when the driver creates the final resource prefix |
| `--confirm-resource-group` | None                 | Empty                    | Exact Terraform-resolved resource group required for undeploy            |

The worker must provide Bash, Azure CLI, Terraform, kubectl, Helm, jq, OSMO CLI, access to this checkout, and an authenticated Azure CLI session. For managed identity authentication, run `az login --identity` before the driver.

When Copilot invokes the driver, request one confirmation for the complete deploy or undeploy operation. For undeploy, pass the exact resource-group name resolved from Terraform state or the saved destroy context through `--confirm-resource-group`; the driver rejects missing or mismatched values before changing state or deleting resources.

The confirmed driver owns the complete sequence, including bounded Terraform retries and ordered setup scripts; do not request confirmations between its internal steps.

The driver:

1. Sources `prerequisites/az-sub-init.sh`.
2. Creates a missing `terraform.tfvars` atomically from the bundled template, appends the normalized resource owner to `resource_prefix`, then changes only its final prefix and `instance = "NNN"` assignments. An existing file remains the configuration baseline.
3. Reuses the current instance when Terraform state is non-empty.
4. Selects the first globally available username-scoped instance from `001` through `999` when state is empty, failing immediately on Azure API errors.
5. Creates the selected resource group for Terraform to consume as an existing group and purges soft-deleted Azure ML workspaces and Key Vaults retained from an earlier use of that group.
6. Merges the configured Terraform tags onto the external resource group while preserving existing tags. Terraform reads the same configured tags from `terraform.tfvars`.
7. Runs Terraform init, plan, and apply with three bounded attempts against the same state and configuration.
   A failed Azure Managed Redis shell is removed before planning or retrying so a transient capacity failure receives a fresh create request.
8. Selects an available private IP from the AKS system subnet and runs all three platform setup scripts in numeric order.
9. Verifies Terraform outputs, live Azure resource IDs, Helm releases, Azure ML extension and compute state, and Kubernetes readiness; then logs the OSMO CLI into the private gateway directly or through a temporary Kubernetes tunnel, selects the default pool, ensures the generic `huggingface` credential exists, and lists one workflow as a smoke test.
10. Supports deterministic undeploy from local Terraform state, including partial deployments, destroy retries, resource-group-wide soft-delete purging, and targeted kubeconfig cleanup.
11. Uses an exclusive Terraform-directory lock so concurrent Automation jobs cannot share the same state or variable file.
12. Writes timestamped JSONL events and a terminal JSON summary under `infrastructure/setup/generated/<environment>/e2e-operations/<operation-id>/`.

Use `--config-preview` for read-only resolution:

```bash
bash "$DRIVER" deploy --repo-root "$REPO_ROOT" --config-preview
```

## Instance Selection

The driver generates `terraform.tfvars` from the bundled E2E template when the file is absent. It derives the owner from the signed-in Azure username, removes the domain and non-alphanumeric characters, lowercases the result, appends it to the template's `resource_prefix`, and persists the combined value as the final `resource_prefix`. Use `--resource-owner` when the derived value is unsuitable.

The data-lake storage account sets the owner-length limit because `stdl<resource-prefix><owner><environment><NNN>` must fit Azure's 24-character storage-account limit. The default `e2e`, `dev`, and three-digit instance leave 11 owner characters. The driver calculates the limit from the configured base prefix and environment and truncates the normalized owner from the right with a warning.

An existing `terraform.tfvars` treats `resource_prefix` as the final owner-inclusive prefix and reuses it unchanged. The driver migrates the earlier `resource_owner` assignment into that final prefix without changing deployed resource names. If local state is non-empty, it recovers the three-digit instance from the Terraform resource-group output. For empty state, it searches `001` through `999` in ascending order and chooses the first candidate for which all checks pass:

Both Azure ML compute pools use `identity_type = "UserAssigned"`. The platform module binds them to the Terraform-created Azure ML user-assigned managed identity.

| Resource           | Candidate name                             | Required live result                                                    |
|--------------------|--------------------------------------------|-------------------------------------------------------------------------|
| Resource group     | `rg-<resource-prefix>-<environment>-<NNN>` | `az group exists` is `false`                                            |
| Key Vault          | `kv<resource-prefix><environment><NNN>`    | Microsoft.KeyVault `checkNameAvailability` reports `nameAvailable=true` |
| Primary storage    | `st<resource-prefix><environment><NNN>`    | `az storage account check-name` reports `nameAvailable=true`            |
| Data lake storage  | `stdl<resource-prefix><environment><NNN>`  | `az storage account check-name` reports `nameAvailable=true`            |
| Container registry | `acr<resource-prefix><environment><NNN>`   | `az acr check-name` reports `nameAvailable=true`                        |

Use the subscription-scoped Key Vault REST endpoint with resource type `Microsoft.KeyVault/vaults`; `az keyvault check-name` checks managed HSM names and is not valid for this test. A Key Vault name retained by soft delete is unavailable and therefore rejects the candidate.

Name-availability API errors are failures, not evidence that a name is available. The driver normalizes the instance line before and after its edit and stops if any other byte changes.

## Deploy Behavior

The deploy operation requires empty state for a new instance or resumes the instance already represented by non-empty state. It fails closed when Terraform state cannot be read or when the resource-group output conflicts with `terraform.tfvars`.

The template sets `should_create_resource_group = false`. For a new instance, the driver creates the resource group before plan and apply. It then permanently deletes any Azure ML workspace that ARM reports as `SoftDeleted` in that group and any deleted Key Vault whose retained resource ID belongs to the group. An unsupported soft-deleted ARM resource type stops deployment rather than being ignored.

The driver merges every configured Terraform tag onto the externally managed group, and Terraform reads the same tag map from `terraform.tfvars`. Personal instructions may add environment-specific governance or expiry tags to the gitignored variable file before deployment.

Azure ML can retain a soft-deleted workspace that is absent from `az resource list`, while its create operation still rejects the name. Confirm the purge with `DELETE .../workspaces/<name>?api-version=2024-04-01&forceToPurge=true`. If the endpoint returns `204` but creation remains blocked, set the existing `workspace_name_suffix` Terraform input to a unique suffix and resume the same state. Do not abandon the instance or infer another environment.

After Terraform succeeds, the driver previews and runs these scripts from `infrastructure/setup/`:

1. `01-deploy-robotics-charts.sh`
2. `02-deploy-azureml-extension.sh`
3. `03-deploy-osmo.sh`

When subscription policy disables the Key Vault public data plane and no private endpoint is configured, Terraform writes the OSMO, PostgreSQL, and Redis secrets through the ARM plane. The OSMO setup script initializes static Kubernetes secrets from sensitive Terraform outputs and disables Key Vault CSI mounts for that deployment. It never prints the secret values.

The OSMO script receives a stable private service IP selected through Azure's VNet availability API. Existing deployments retain the service annotation's current address.

The driver first connects directly to the private address. When the host has no VNet route, it temporarily runs `kubectl port-forward service/osmo-gateway 9000:80 --namespace osmo-control-plane` through the AKS API and performs verification through `http://127.0.0.1:9000`. Deployment verification fails unless OSMO login, `osmo profile set pool default`, credential verification, and `osmo workflow list --count 1 --format-type json` all succeed. The driver terminates its tunnel after verification.

For a new E2E environment, the driver creates `huggingface` as a `GENERIC` OSMO credential with a deliberately invalid dummy `hf_token`. If a generic credential with that name already exists, the driver preserves it rather than overwriting a potentially real token.

## Regional Capacity Fallback

Use this fallback only when the driver exhausts its configured attempts and every terminal Terraform error for a resource is an Azure regional capacity or SKU-availability failure such as `InsufficientCapacity` or `SkuNotAvailable`. Do not use it for authorization, quota, configuration, validation, networking, or provider errors.

1. Preserve the current instance, Terraform state, variable file, resource group, workspace, and all unaffected resource locations.
2. Identify the exact failing Terraform resource address, Azure resource type, configured SKU, and current location from Terraform and the Azure error. Do not infer them from naming conventions.
3. Query the live subscription-scoped provider, SKU, or resource-specific availability API for that exact resource type and SKU. Select a different supported location with no relevant restriction. Do not use a memorized region list, and stop if the API fails or returns no unrestricted alternative.
4. Temporarily hardcode only the failing resource's `location` argument to the selected location. Change a dependent resource only when the Azure API explicitly requires it to be colocated with the failing resource. Do not change the global `var.location`, resource-group location, SKU, `terraform.tfvars`, Terraform state, or unrelated resources.
5. Run `terraform fmt -check` on every changed Terraform file, then resume the same deployment through the bundled driver. The driver remains responsible for planning, applying, setup, and verification.
6. Keep the override in place across any resume needed before the driver reports a fully successful deployment. Restore every changed source line to its exact original value after successful deployment, or after undeploying a failed environment. Never commit a temporary capacity override.

For Azure Managed Redis, query the subscription-scoped `Microsoft.Cache/skus` endpoint and filter for the configured `redisEnterprise` SKU:

```bash
subscription_id=$(az account show --query id --output tsv)
az rest \
    --method get \
    --url "https://management.azure.com/subscriptions/${subscription_id}/providers/Microsoft.Cache/skus?api-version=2024-11-01"
```

This is the sole exception to the general prohibition on switching regions. It moves only capacity-constrained resources to live available capacity and does not create a second environment.

## Undeploy Behavior

Undeploy requires non-empty local Terraform state for its first invocation and never discovers a destroy target by enumerating Azure resources. It captures available Terraform outputs under `infrastructure/setup/generated/<environment>/` before making changes and reuses that context when resuming cleanup after Terraform state becomes empty.

Before any state removal, destroy, purge, or resource-group deletion, `--confirm-resource-group` must exactly match `rg-<resource-prefix>-<environment>-<instance>` and any Terraform-resolved resource-group name. The driver writes destroy context immediately after creating the external resource group so cleanup can resume even when deployment stops before Terraform creates state.

Before destroy, the driver removes E2E resources protected by `prevent_destroy` from Terraform state so the final resource-group deletion retains responsibility for those resources. It also removes ARM-plane Key Vault secret children because that resource API does not support `DELETE`. The driver destroys the remaining state with bounded retries, verifies that state is empty, purges soft-deleted Azure ML workspaces retained in the group, and deletes the externally managed resource group.

The driver checks the Terraform-resolved Key Vault again after resource-group deletion to handle delayed soft deletion, purging it when Azure reports that exact vault as deleted. Missing outputs are permitted for partial failed deployments. It removes only the captured AKS kubeconfig and generated destroy context; it retains `terraform.tfvars` and empty Terraform state.

## Failure Handling

- Preserve the selected instance and Terraform state across every retry.
- Stop after the configured attempt limit.
- Do not switch region, SKU, variable file, workspace, or checkout except for the regional capacity fallback above.
- Treat setup-script and verification failures as deployment failures.
- Never print Terraform-sensitive outputs, credentials, or generated secrets.
- Preserve each operation's `events.jsonl` and `summary.json` as the sanitized retry, target, and outcome record.
