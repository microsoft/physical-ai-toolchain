---
sidebar_position: 8
title: Deployment Validation Guide
description: Validation levels, testing templates, and cost optimization for contribution testing
author: Microsoft Robotics-AI Team
ms.date: 2026-09-19
ms.topic: how-to
---

> [!NOTE]
> This guide expands on the [Deployment Validation](README.md#-deployment-validation) section of the main contributing guide.

Automated linting, component tests, Terraform tests, and smoke checks provide the validation baseline. Deployment and workflow validation supplement those checks for environment-dependent changes. Select additional validation based on contribution scope and cost constraints.

Costs below are illustrative estimates, not current quotes or measurements for the default deployment. Confirm the selected resources and regional rates before deploying.

## Validation Levels

### Level 1: Static Validation

Required for all contributions before submitting PR.

**Commands:**

```bash
# Install validation dependencies from the repository root
npm ci

# Terraform formatting and validation
npm run lint:tf:validate

# Shell script linting
npm run lint:sh

# Documentation validation
npm run lint:md
```

Run the applicable component tests as described in [Testing Requirements](../../CONTRIBUTING.md#testing-requirements). Static checks alone do not replace tests for code changes.

**When to use:** Every contribution (documentation, code, infrastructure).

**Cost:** $0

### Level 2: Plan Validation

Required for Terraform module or configuration changes.

**Commands:**

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your subscription details

terraform init
terraform plan -var-file=terraform.tfvars -out=tfplan
terraform show tfplan
```

**Documentation required in PR:**

* Resource changes: X resources to add, Y to change, Z to destroy
* Verification: no unexpected deletions or replacements
* Attach plan output (redact sensitive information)

**When to use:** Terraform changes (modules, variables, resources).

**Cost:** $0 (plan only, no deployment)

### Level 3: Deployment Testing

Optional for most PRs due to cost (~$25-50 for 8-hour session). Required for significant infrastructure changes.

**Minimal test deployment:**

```bash
# Initialize subscription context from the repository root
source infrastructure/terraform/prerequisites/az-sub-init.sh
cd infrastructure/terraform
terraform apply -var-file=terraform.tfvars

# Test specific functionality
# Connect the VPN first when should_enable_private_aks_cluster is true
az aks get-credentials --resource-group <rg> --name <aks-name>
kubectl get nodes  # Verify GPU nodes
helm list -A  # Verify Helm chart deployments

# Destroy promptly
terraform destroy -var-file=terraform.tfvars
```

The default example uses private endpoints and a private AKS API. For an approved public test environment, set both `should_enable_private_endpoint` and `should_enable_private_aks_cluster` to `false` in `terraform.tfvars`; review `should_enable_public_network_access` and access restrictions as well. Keep the same variable file for plan, apply, and destroy. Terraform provisions infrastructure; install GPU Operator and OSMO with the ordered scripts in `infrastructure/setup/` before checking Helm releases.

**Documentation required in PR:**

* What was tested: deployment components and validation steps
* What was not tested: scenarios deferred to maintainer validation
* Cost incurred: estimated total cost for testing session

**Example:**

> Example report: Deployed infrastructure in the selected region with a private AKS API. Verified one Standard_NV36ads_A10_v5 GPU node. Connected the VPN and ran the setup scripts, then confirmed GPU Operator and OSMO backend were available. Record the actual duration, cost estimate, and untested scenarios for your run.

**When to use:** Major infrastructure changes, new modules, network architecture changes.

**Cost:** $25-50 (8-hour session with GPU VMs and managed services)

### Level 4: Workflow Validation

Required for changes to training scripts, workflow templates, or AzureML integration.

**Commands:**

Run these RL submission examples from the repository root after deploying and configuring the selected platform:

```bash
# AzureML training job
./training/rl/scripts/submit-azureml-training.sh

# OSMO workflow
./training/rl/scripts/submit-osmo-training.sh
```

**Documentation required in PR:**

* Job completion status: success or failure
* Training duration: total runtime
* Logs excerpt: key outputs demonstrating expected behavior
* Cost: GPU VM time and storage costs

**When to use:** Training scripts, workflow templates, AzureML/OSMO integration changes.

**Cost:** Variable (depends on training duration and GPU SKU)

## Testing Documentation Template

Copy this template to PR description:

```markdown
## Validation Performed

**Static Validation:**
- [ ] npm run lint:tf:validate
- [ ] shellcheck (if applicable)
- [ ] npm run lint:md (if docs changed)

**Plan Validation (Terraform changes):**
- [ ] terraform plan executed
- [ ] Plan output attached (see below)
- [ ] Expected changes: X to add, Y to change, Z to destroy
- [ ] No unexpected resource replacements

**Deployment Testing:**
- [ ] Deployed in dev subscription: (subscription ID or description)
- [ ] Region: (e.g., eastus)
- [ ] Network mode: public | hybrid | private
- [ ] Tested components: (list components validated)
- [ ] Cost incurred: (estimated cost)
- [ ] Resources destroyed: Yes | No

**Workflow Testing:**
- [ ] Training job submitted: azureml | osmo
- [ ] Job status: success | failed
- [ ] Duration: (e.g., 45 minutes)
- [ ] Logs: (attach or link to logs)

## Untested Scenarios

Document scenarios not validated:
- (e.g., Private network mode - maintainer will validate)
- (e.g., Multi-GPU training - tested single GPU only)
- (e.g., Production-scale node pools - tested 1 node)
```

## Cost Optimization

Strategies for minimizing testing costs while maintaining validation quality.

### Cost by Validation Level

| Validation Level             | Typical Cost | Duration         |
|------------------------------|--------------|------------------|
| Static validation            | $0           | 5-10 minutes     |
| Plan validation              | $0           | 5-15 minutes     |
| Deployment testing (minimal) | $10-25       | 2-4 hours        |
| Deployment testing (full)    | $25-50       | 6-8 hours        |
| Workflow validation          | $5-30        | 30 min - 2 hours |

### Cost Control Strategies

**Use smaller deployments:**

Edit `node_pools` in `terraform.tfvars`. For an autoscaled pool, set `should_enable_auto_scaling = true` and `min_count = max_count = 1`; for a fixed pool, set `should_enable_auto_scaling = false` and `node_count = 1`. Preserve its VM size, subnet, GPU driver, labels, and taints. The checked-in single-pool example already limits the A10 pool to one node.

Do not make networking public solely to reduce cost. Use the approved network mode and account for VPN and managed-service costs.

**Set spending alerts:**

```bash
# Create budget for testing
az consumption budget create \
  --budget-name "PR-Testing" \
  --amount 100 \
  --time-grain Monthly \
  --resource-group <rg>
```

**Monitor costs:**

```bash
# Check recent usage
az consumption usage list \
  --start-date $(date -d '1 day ago' +%Y-%m-%d) \
  --output table
```

**Immediate cleanup:**

```bash
# Destroy all resources after validation
terraform destroy -auto-approve -var-file=terraform.tfvars

# Verify resource deletion
az resource list --resource-group <rg> --output table
```

### Regional Pricing Considerations

GPU VM costs vary by region. Use [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) to estimate costs for your target region.

| Region      | Standard_NC24ads_A100_v4 Hourly Cost |
|-------------|--------------------------------------|
| East US     | ~$3.06/hour                          |
| West US 2   | ~$3.06/hour                          |
| West Europe | ~$3.67/hour                          |

> [!TIP]
> Test in regions with lower GPU costs when region-specific features are not being validated.

## Related Documentation

* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages
* [Cost Considerations](cost-considerations.md) - Detailed cost tracking and budgeting
* [Infrastructure Style](infrastructure-style.md) - Terraform and shell conventions
