---
sidebar_position: 9
title: Cost Considerations
description: Testing budgets, cost tracking, and optimization strategies for contribution validation
author: Microsoft Robotics-AI Team
ms.date: 2026-02-03
ms.topic: concept
---

> [!NOTE]
> This guide expands on the [Cost Considerations](README.md#-cost-considerations) section of the main contributing guide.

Full deployment testing incurs Azure costs. This guide provides cost transparency and optimization strategies.

> [!NOTE]
> Cost estimates in this document were captured on 2026-02-03 and are subject to change.
> Use the [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for current rates.

## Testing Budget

### Full Deployment Cost Estimate

* GPU VMs: ~$3.06/hour per Standard_NC24ads_A100_v4 node
* Managed services: ~$50-100/month (Storage, Key Vault, PostgreSQL, Redis)
* Total for 8-hour testing session: ~$25-50

### Cost by Component

| Component         | Estimated Cost                        | Notes                           |
|-------------------|---------------------------------------|---------------------------------|
| AKS Control Plane | Free (tier) or ~$0.10/hour (standard) | Standard tier for production    |
| GPU Node Pool     | $3.06/hour per node                   | Standard_NC24ads_A100_v4        |
| System Node Pool  | ~$0.50/hour                           | Standard_D4s_v3                 |
| Storage Account   | ~$20-30/month                         | General Purpose v2              |
| Key Vault         | ~$0.05/month + operations             | Secrets storage                 |
| PostgreSQL        | ~$15-25/month                         | Flexible Server, Burstable tier |
| Redis Cache       | ~$15-20/month                         | Basic tier                      |
| Log Analytics     | ~$5-15/month                          | Based on ingestion volume       |

## Cost-Effective Testing

### Strategies for Minimizing Testing Costs

**Use smaller deployments:**

```bash
# Single GPU node instead of default pool size
terraform apply -var="gpu_node_count=1"

# Public network mode (simpler, faster)
terraform apply -var="network_mode=public"

# Burstable database tiers
terraform apply -var="postgres_sku=B_Standard_B1ms"
```

**Time-bound testing:**

```bash
# Set auto-shutdown timer
az vm auto-shutdown -g <rg> -n <vm> --time 2200

# Use Azure Dev/Test subscription pricing (if available)
# 40-60% savings on compute resources
```

**Immediate cleanup:**

```bash
# Destroy all resources after validation
terraform destroy -auto-approve -var-file=terraform.tfvars

# Verify resource deletion
az group list --query "[?starts_with(name,'rg-robotics')].name" -o table
az group delete -n <rg> --yes --no-wait
```

## Cost Tracking

### Monitor Spending During Testing

```bash
# Check recent usage
az consumption usage list \
  --start-date $(date -d '7 days ago' +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d) \
  --query "[].{Date:usageStart, Amount:pretaxCost, Currency:currency}" \
  --output table

# Create budget alert
az consumption budget create \
  --budget-name "PR-Testing-Monthly" \
  --amount 100 \
  --time-grain Monthly \
  --category Cost \
  --resource-group <rg>
```

### Cost by Contribution Type

| Contribution Type   | Typical Cost | Recommended Testing               |
|---------------------|--------------|-----------------------------------|
| Documentation       | $0           | Read-through, link check          |
| Shell scripts       | $0-10        | ShellCheck, minimal deployment    |
| Terraform modules   | $10-25       | Plan validation, short deployment |
| Training scripts    | $15-30       | Single training job               |
| Full infrastructure | $25-50       | Complete deployment cycle         |
| Workflow templates  | $15-40       | Workflow execution                |

## Regional Pricing

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
* [Deployment Validation](deployment-validation.md) - Validation levels and testing templates
* [Security Review](security-review.md) - Security checklist and reporting
