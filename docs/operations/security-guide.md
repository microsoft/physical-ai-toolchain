---
title: Deployment Security Guide
description: Security configuration inventory, deployment responsibilities, and considerations for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-02-10
ms.topic: concept
keywords:
  - security
  - deployment
  - network
  - identity
  - kubernetes
---

Security configurations included in this reference architecture and responsibilities for teams operating in production environments.

> [!IMPORTANT]
> This document provides security guidance for informational purposes only. It does
> not constitute professional security advice and is not a substitute for your own
> security assessment. This reference architecture is licensed under the
> [MIT License](../../LICENSE), provided "AS IS" without warranty of any kind. You are
> solely responsible for the security of your deployment, including configuration,
> operational practices, and compliance with applicable regulations. The project
> maintainers accept no liability for security incidents arising from the use of
> this architecture. Refer to official [Azure security documentation](https://learn.microsoft.com/azure/security/)
> for authoritative, current guidance.

## Security Configuration Included in This Architecture

This architecture ships with these security configurations enabled by default.
They represent a reasonable starting point for development and testing, not a
production-ready security posture.

### Network Security

| Configuration          | Default                                 | Reference                                                                          |
|------------------------|-----------------------------------------|------------------------------------------------------------------------------------|
| Private AKS cluster    | Enabled by default (Terraform variable) | [AKS private cluster](https://learn.microsoft.com/azure/aks/private-clusters)      |
| Azure CNI networking   | Enabled                                 | [Azure CNI overview](https://learn.microsoft.com/azure/aks/configure-azure-cni)    |
| Network policy support | Enabled                                 | [AKS network policies](https://learn.microsoft.com/azure/aks/use-network-policies) |
| NAT Gateway for egress | Configured                              | [AKS outbound connectivity](https://learn.microsoft.com/azure/aks/nat-gateway)     |

### Identity and Access

| Configuration        | Default                        | Reference                                                                                 |
|----------------------|--------------------------------|-------------------------------------------------------------------------------------------|
| Managed identities   | User-assigned for AKS          | [AKS managed identity](https://learn.microsoft.com/azure/aks/use-managed-identity)        |
| Workload identity    | Federated credentials for OSMO | [AKS workload identity](https://learn.microsoft.com/azure/aks/workload-identity-overview) |
| Entra ID integration | RBAC enabled                   | [AKS Entra integration](https://learn.microsoft.com/azure/aks/managed-azure-ad)           |

### Secret Management

| Configuration   | Default                       | Reference                                                                                     |
|-----------------|-------------------------------|-----------------------------------------------------------------------------------------------|
| Azure Key Vault | CSI driver configured         | [Key Vault CSI driver](https://learn.microsoft.com/azure/aks/csi-secrets-store-driver)        |
| Terraform state | Local backend (not encrypted) | [Terraform Azure backend](https://developer.hashicorp.com/terraform/language/backend/azurerm) |

### Container Security

| Configuration        | Default                                           | Reference                                                                                                            |
|----------------------|---------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Microsoft Defender   | Configurable (`should_enable_microsoft_defender`) | [Defender for Containers](https://learn.microsoft.com/azure/defender-for-cloud/defender-for-containers-introduction) |
| Azure Policy for AKS | Enabled                                           | [Azure Policy for AKS](https://learn.microsoft.com/azure/aks/use-azure-policy)                                       |

### Kubernetes Security

| Configuration | Default                    | Reference                                                               |
|---------------|----------------------------|-------------------------------------------------------------------------|
| RBAC          | Enabled                    | [AKS RBAC](https://learn.microsoft.com/azure/aks/manage-azure-rbac)     |
| Pod security  | Default namespace policies | [Pod security standards](https://learn.microsoft.com/azure/aks/use-psa) |

## Your Deployment Responsibilities

### Before Deployment

- Conduct a security assessment for your target environment
- Review all Terraform variables and override defaults inappropriate for your security posture
- Evaluate network topology (private vs. public endpoints) for your requirements
- Establish secret management policies (rotation schedules, access controls)
- Verify Azure subscription security baselines (Azure Policy, Defender for Cloud)

### During Operation

- Monitor AKS cluster security events through Azure Monitor
- Review Kubernetes RBAC bindings and service account permissions
- Manage container image provenance and vulnerability scanning
- Maintain network policy definitions appropriate for running workloads

### Ongoing Maintenance

- Update Terraform provider versions and module references
- Patch base container images and NVIDIA runtime components
- Review Azure Advisor security recommendations
- Reassess security posture when adding workloads or scaling

## Security Considerations Checklist

> [!NOTE]
> This checklist highlights common security considerations for Azure and Kubernetes
> deployments. It is not exhaustive. Your organization's security requirements,
> compliance obligations, and threat model determine the complete set of controls
> you need.

| Category   | Consideration                                                       | Reference                                                                                                                       |
|------------|---------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| Network    | Evaluate private vs. public AKS API server                          | [AKS private cluster](https://learn.microsoft.com/azure/aks/private-clusters)                                                   |
| Network    | Define Kubernetes network policies for workload isolation           | [AKS network policies](https://learn.microsoft.com/azure/aks/use-network-policies)                                              |
| Identity   | Review managed identity permissions and scope                       | [AKS managed identity](https://learn.microsoft.com/azure/aks/use-managed-identity)                                              |
| Identity   | Verify workload identity audience restrictions                      | [Workload identity](https://learn.microsoft.com/azure/aks/workload-identity-overview)                                           |
| Secrets    | Configure Key Vault access policies and rotation                    | [Key Vault rotation](https://learn.microsoft.com/azure/key-vault/keys/how-to-configure-key-rotation)                            |
| Secrets    | Migrate Terraform state to a remote encrypted backend               | [Terraform Azure backend](https://developer.hashicorp.com/terraform/language/backend/azurerm)                                   |
| Compute    | Enable Defender for Containers (`should_enable_microsoft_defender`) | [Defender for Containers](https://learn.microsoft.com/azure/defender-for-cloud/defender-for-containers-introduction)            |
| Compute    | Scan container images for vulnerabilities                           | [Container image scanning](https://learn.microsoft.com/azure/defender-for-cloud/defender-for-container-registries-introduction) |
| Monitoring | Enable diagnostic settings on AKS and Key Vault                     | [AKS diagnostics](https://learn.microsoft.com/azure/aks/monitor-aks)                                                            |
| Compliance | Review Azure compliance offerings for your industry                 | [Azure compliance](https://learn.microsoft.com/azure/compliance/)                                                               |

## Terraform State Security

This architecture uses a local Terraform state backend by default. Local state stores infrastructure details including resource IDs, network addresses, and configuration values in an unencrypted file on disk.

For team environments or production deployments, consider migrating to a remote backend with encryption. Refer to the [Terraform Azure backend documentation](https://developer.hashicorp.com/terraform/language/backend/azurerm) for configuration details.

## References

| Resource                                                                                                                        | Description                                           |
|---------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| [Azure security documentation](https://learn.microsoft.com/azure/security/)                                                     | Authoritative security guidance for Azure services    |
| [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks) | Production-ready AKS security and networking patterns |
| [Azure compliance documentation](https://learn.microsoft.com/azure/compliance/)                                                 | Compliance offerings and certifications               |
| [Terraform Azure backend](https://developer.hashicorp.com/terraform/language/backend/azurerm)                                   | Remote state backend configuration                    |
| [Threat Model](../security/threat-model.md)                                                                                     | STRIDE-based threat analysis and remediation roadmap  |
| [Contributing security review](../contributing/security-review.md)                                                              | Contributor security checklist for pull requests      |

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
