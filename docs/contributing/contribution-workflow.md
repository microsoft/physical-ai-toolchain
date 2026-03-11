---
title: Contribution Workflow
description: How to contribute including legal requirements, bug reports, enhancement suggestions, and documentation improvements
author: Microsoft Robotics-AI Team
ms.date: 2026-02-08
ms.topic: how-to
keywords:
  - contributing
  - bugs
  - enhancements
  - documentation
  - workflow
---

> [!NOTE]
> This guide expands on the [I Want To Contribute](README.md#-i-want-to-contribute) section of the main contributing guide.

Contribution types, legal requirements, and workflows for bug reports, enhancements, code, and documentation.

## Legal Notice

When contributing to this project, you must agree that you have authored 100% of the content, that you have the necessary rights to the content, and that the content you contribute may be provided under the project license.

This project uses the Microsoft Contributor License Agreement (CLA) to define the terms under which intellectual property has been received. All contributions require acceptance of the CLA.

Visit <https://cla.opensource.microsoft.com> to sign the CLA electronically. When you submit a pull request, a bot will automatically determine whether you need to sign the CLA. Simply follow the instructions provided.

## Reporting Bugs

### Before Submitting a Bug Report

Before creating a bug report:

* Search [existing issues](https://github.com/microsoft/physical-ai-toolchain/issues) for similar deployment errors or problems
* Verify you are using tested versions: Terraform >= 1.9.8, Azure CLI >= 2.65.0
* Check Azure resource quotas and limits: `az vm list-usage --location <region>`
* Confirm network mode (private/hybrid/public) matches documented requirements
* Test with minimal configuration first (public network mode before private, single GPU node before multi-node)

### How to Submit a Bug Report

Create a [new issue](https://github.com/microsoft/physical-ai-toolchain/issues/new) with:

| Field                | Details                                                                |
|----------------------|------------------------------------------------------------------------|
| Title format         | `[Component][Subcomponent] Brief description`                          |
| Environment details  | Azure region, network mode, Terraform/CLI versions, GPU VM SKUs        |
| Expected vs. actual  | What should happen and what actually happened                          |
| Deployment logs      | `terraform apply` output, CLI errors, pod logs; sanitize secrets       |
| Azure resource state | `az resource show` output, provisioning state query                    |
| Reproduction steps   | Numbered commands from setup, config files (sanitize sensitive values) |
| Cost impact          | Resources deployed and hourly cost (if relevant)                       |

<details>
<summary>Bug Report Example (click to expand)</summary>

**Title:** \[Terraform\]\[SIL Module\] AKS cluster creation fails with subnet authorization error

**Environment:**

* Region: eastus2
* Network mode: private
* Terraform: 1.9.8
* Azure CLI: 2.65.0
* VM SKU: Standard_NC24ads_A100_v4

**Expected Behavior:**

`terraform apply` creates AKS cluster with GPU node pool using private network configuration.

**Actual Behavior:**

Deployment fails during AKS cluster creation with authorization error:

```text
Error: creating Managed Kubernetes Cluster: Code="LinkedAuthorizationFailed"
Message="The client has permission to perform action 'Microsoft.ContainerService/managedClusters/write'
on scope '/subscriptions/.../resourceGroups/.../providers/Microsoft.ContainerService/managedClusters/aks-cluster';
however, it does not have permission to perform action 'Microsoft.Network/virtualNetworks/subnets/join/action'
on the linked scope(s) '/subscriptions/.../resourceGroups/.../providers/Microsoft.Network/virtualNetworks/vnet/subnets/aks-subnet'"
```

**Resource State:**

```bash
az resource show --ids /subscriptions/.../resourceGroups/.../providers/Microsoft.Network/virtualNetworks/vnet/subnets/aks-subnet
```

Output shows subnet exists but lacks role assignment for AKS managed identity.

**Reproduction Steps:**

1. Set up `terraform.tfvars` with private network mode
2. Run `terraform init && terraform plan`
3. Run `terraform apply`
4. Observe failure at AKS cluster creation step

**Configuration:**

```hcl
network_mode = "private"
enable_private_cluster = true
aks_subnet_cidr = "10.0.2.0/24"
```

**Cost Impact:**

Reproduced the issue with prerequisite resources (VNet, Key Vault, Storage Account) deployed before AKS creation step failed. Incurred ~$0.10/hour while debugging. Destroyed all resources after confirming the issue.

**Additional Context:**

Deployment script creates VNet and subnet but appears to skip role assignment for AKS managed identity on subnet. Manually assigning `Network Contributor` role on subnet allows deployment to succeed.

</details>

After submission, expect initial acknowledgment within the timeframes documented in [SUPPORT.md](../../SUPPORT.md).

## Suggesting Enhancements

### Before Submitting an Enhancement

Before suggesting an enhancement:

* Determine if the enhancement is broadly applicable (blueprint improvement) or organization-specific (belongs in a fork)
* Search [existing issues](https://github.com/microsoft/physical-ai-toolchain/issues) and [pull requests](https://github.com/microsoft/physical-ai-toolchain/pulls) for similar proposals
* Consider cost implications if adding new Azure services or increasing resource scale
* Verify compatibility with all three network modes (private/hybrid/public) or document known limitations
* Check if enhancement aligns with reference architecture goals (generalized deployment patterns vs. specific use cases)

### How to Submit an Enhancement

Create a [new issue](https://github.com/microsoft/physical-ai-toolchain/issues/new) with:

* Clear title describing the enhancement
* Problem statement: What limitation or gap does this enhancement address?
* Proposed solution: Describe the technical approach
* Azure services: List new services required, with cost implications
* Breaking changes: Indicate if existing deployments require migration
* Contributor personas: Which personas benefit most (ML engineers, infrastructure engineers, DevOps/SRE, robotics developers)?
* Network mode compatibility: Specify if enhancement works in all modes or has limitations
* Alternatives considered: Other approaches evaluated and why this solution is preferred
* Reference architecture precedent: Similar patterns in other Azure reference architectures or Microsoft guidance

## Your First Code Contribution

This reference architecture validates through deployment rather than automated tests. The validation level depends on your contribution type.

### PR Workflow

1. Fork the repository to your GitHub account
2. Create a branch from `main` with descriptive name: `feature/private-endpoint-support` or `fix/gpu-scheduling-timeout`
3. Make changes following style guides and conventions
4. Open a draft PR early for maintainer feedback
5. Perform validation appropriate to your contribution type (see table below)
6. Mark PR ready for review after completing validation
7. Address review feedback promptly
8. Merge occurs after approval and passing maintainer integration tests

### Validation Expectations

| Contribution Type           | Expected Validation                                                                 |
|-----------------------------|-------------------------------------------------------------------------------------|
| Documentation               | Read-through, link check (`npm run lint:md`)                                        |
| Shell scripts               | ShellCheck validation, test in local/minimal environment                            |
| Terraform modules           | `terraform fmt`, `terraform validate`, `terraform plan` output attached to PR       |
| Full infrastructure changes | Deployment testing in dev subscription with cost estimate and teardown confirmation |
| Training scripts            | AzureML job submission in test workspace with logs                                  |
| Workflow templates          | Workflow execution validation with job outputs                                      |
| Configuration manifests     | Syntax validation, test deployment in non-production cluster                        |

### Testing Documentation

In your PR description, document:

* Validation performed: Commands run, deployments tested
* Environment used: Dev subscription, network mode, Azure region
* Cost incurred: Estimate for resources deployed during testing
* Known limitations: Untested scenarios or edge cases

Maintainers perform integration testing across multiple scenarios before merge. Contributors are not expected to test all permutations (different regions, network modes, SKU variations).

## Improving The Documentation

Documentation contributions improve the architecture for the entire robotics and AI community.

### High-Value Documentation Contributions

* Deployment troubleshooting guides: Expand [azureml-validation-job-debugging.md](../azureml-validation-job-debugging.md) with new scenarios
* Region/SKU compatibility matrices: Document tested combinations and known limitations
* Cost optimization strategies: Real-world cost profiles and reduction techniques
* Network architecture decisions: Guidance on when to use private vs. hybrid vs. public modes
* Migration guides: Instructions for handling breaking changes or infrastructure updates
* Architecture decision records (ADRs): Document major design choices and trade-offs

### Documentation Validation

Before submitting documentation changes:

* Run `npm run lint:md` to check formatting and style
* Verify internal links with `npm run lint:links`
* Test code samples in deployment environment
* Review against [docs-style-and-conventions.instructions.md](../../.github/instructions/docs-style-and-conventions.instructions.md)

## Related Documentation

* [Contributing Guide](README.md) - Main contributing guide with all sections
* [Pull Request Process](pull-request-process.md) - PR workflow, reviewers, approval criteria
* [Prerequisites and Build Validation](prerequisites.md) - Tools, Azure access, build commands
