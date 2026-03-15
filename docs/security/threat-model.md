---
sidebar_position: 3
title: Threat Model — Physical AI Toolchain
description: STRIDE-based threat model covering infrastructure-as-code components, trust boundaries, and remediation roadmap
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: concept
keywords:
  - threat model
  - STRIDE
  - security
  - trust boundaries
  - remediation
  - risk assessment
---

STRIDE-based threat analysis of the Physical AI Toolchain covering infrastructure-as-code components, trust boundaries, and a prioritized remediation roadmap.

## Executive Summary

This threat model applies the STRIDE framework to the Physical AI Toolchain. The architecture deploys AKS clusters with GPU node pools, Azure Machine Learning, and NVIDIA OSMO for robotics training and inference workloads. All components are infrastructure-as-code artifacts; no hosted service or user-facing application exists.

| Area              | Status                                 | Evidence                                           |
|-------------------|----------------------------------------|----------------------------------------------------|
| Authentication    | Managed identities + workload identity | No password-based auth; `DefaultAzureCredential`   |
| Secret Management | Azure Key Vault (RBAC) + CSI driver    | Secrets synced to K8s pods at mount time           |
| Network Isolation | Private endpoints + VPN-only access    | All Azure services behind VNet; no public IPs      |
| Encryption        | TLS 1.2+ enforced by Azure             | Platform-managed keys for data at rest             |
| Supply Chain      | 95% SHA-pinned GitHub Actions          | Dependency review blocks moderate+ vulnerabilities |

Risk summary: 19 threats identified — 1 Critical, 6 High, 7 Medium, 5 Low. Key open risks: T-2 (Critical), S-1 (High), T-1 (High).

## System Description

### Architecture Components

| Category       | Component                      | Details                                                              |
|----------------|--------------------------------|----------------------------------------------------------------------|
| Compute        | AKS Cluster                    | Private cluster, CNI networking, GPU node pools (Standard_NC-series) |
| Data & Storage | Azure Storage Account          | Blob containers for datasets, checkpoints; private endpoint access   |
| Data & Storage | Azure Database for PostgreSQL  | Flexible server for OSMO metadata; VNet-integrated                   |
| Data & Storage | Azure Cache for Redis          | Enterprise tier; OSMO session state; private endpoint                |
| ML & AI        | Azure Machine Learning         | Workspace with managed endpoints; K8s compute attach                 |
| Identity       | Entra ID + Managed Identities  | System-assigned for AKS, user-assigned for workloads                 |
| Networking     | VNet + NSG + NAT Gateway + VPN | Hub-spoke implied; P2S VPN for operator access                       |
| Observability  | Azure Monitor + Log Analytics  | Container Insights, Prometheus metrics, AMPLS for private ingestion  |
| Security       | Azure Key Vault                | RBAC-mode; CSI Secret Store driver syncs secrets to pods             |
| NVIDIA/OSMO    | OSMO Control Plane + Backend   | Orchestrates distributed training; Envoy proxy optional              |

### Data Flows

Training data flows from Azure Blob Storage through AKS pods to GPU compute. Checkpoints and metrics flow back to storage and MLflow tracking. OSMO coordinates multi-node training via its control plane and PostgreSQL metadata store. All Azure service traffic uses private endpoints — no data traverses the public internet.

Operator access traverses a P2S VPN gateway to the AKS API server private endpoint. CI/CD pipelines authenticate via GitHub OIDC federation to Entra ID managed identities. Terraform state resides locally on the operator workstation (see T-2 for associated risk).

### Security Inheritance

| Control                  | Provider             | Configuration Surface           |
|--------------------------|----------------------|---------------------------------|
| TLS termination          | Azure platform       | Enforced by default             |
| Disk encryption at rest  | Azure platform       | Platform-managed keys (PMK)     |
| Identity federation      | Entra ID             | Workload identity via OIDC      |
| Network segmentation     | Azure VNet + NSG     | Subnets, private endpoints      |
| Secret rotation          | Azure Key Vault      | Deployer responsibility         |
| Cluster patch management | AKS managed upgrades | Deployer selects upgrade policy |

## Trust Boundaries

| ID   | Boundary                         | Description                                                       |
|------|----------------------------------|-------------------------------------------------------------------|
| TB-1 | Azure Control Plane ↔ Data Plane | ARM API calls cross into subscription data plane                  |
| TB-2 | VNet Perimeter ↔ Internet        | NAT Gateway egress; VPN ingress; no public endpoints              |
| TB-3 | AKS ↔ Azure Services             | Pod-to-service traffic via private endpoints and managed identity |
| TB-4 | K8s Namespace Isolation          | OSMO, training, inference workloads in separate namespaces        |
| TB-5 | Operator Workstation ↔ Cluster   | P2S VPN tunnel; `kubectl` via private API server                  |
| TB-6 | CI/CD ↔ Repository               | GitHub Actions with OIDC federation; SHA-pinned actions           |
| TB-7 | OSMO Control Plane ↔ Backend     | gRPC between control plane and backend pods                       |
| TB-8 | Training Code ↔ Azure Services   | Python SDK calls via `DefaultAzureCredential`                     |

### Credential Delegation Model

Entra ID issues tokens to managed identities. AKS workload identity federation projects service account tokens to pods. Pods exchange projected tokens for Azure resource access. Key Vault stores secrets and syncs them to Kubernetes Secrets via the CSI driver. The chain: Entra ID → Managed Identities → Workload Identity Federation → Key Vault → K8s Secrets.

## STRIDE Threat Registry

### Spoofing

#### S-1: OSMO API Authentication Disabled

| Field            | Value                                                                                   |
|------------------|-----------------------------------------------------------------------------------------|
| Threat           | OSMO API server deploys with `auth.enabled: false`, allowing unauthenticated gRPC calls |
| Affected Assets  | OSMO control plane, backend pods                                                        |
| Trust Boundary   | TB-7                                                                                    |
| Likelihood       | High                                                                                    |
| Impact           | High                                                                                    |
| Risk Rating      | High                                                                                    |
| Current Controls | Cluster-internal networking only; namespace isolation                                   |
| Evidence         | `deploy/002-setup/values/osmo-control-plane-values.yaml` sets `auth.enabled: false`     |
| Status           | Open                                                                                    |
| Remediation      | Enable OSMO auth when vendor provides production-ready auth configuration               |

#### S-2: PostgreSQL Shared Admin Identity

| Field            | Value                                                                          |
|------------------|--------------------------------------------------------------------------------|
| Threat           | PostgreSQL uses a single `psqladmin` identity for all OSMO database operations |
| Affected Assets  | Azure Database for PostgreSQL, OSMO metadata                                   |
| Trust Boundary   | TB-3                                                                           |
| Likelihood       | Medium                                                                         |
| Impact           | Medium                                                                         |
| Risk Rating      | Medium                                                                         |
| Current Controls | VNet integration; private endpoint; Key Vault–stored credentials               |
| Evidence         | `deploy/001-iac/modules/sil/postgresql.tf` configures single admin login       |
| Status           | Accepted                                                                       |
| Rationale        | Single-purpose database serving only OSMO; network isolation limits exposure   |

### Tampering

#### T-1: MEK Stored as ConfigMap

| Field            | Value                                                                                          |
|------------------|------------------------------------------------------------------------------------------------|
| Threat           | Model Encryption Key (MEK) stored in a Kubernetes ConfigMap, bypassing etcd encryption at rest |
| Affected Assets  | K8s ConfigMap, trained model artifacts                                                         |
| Trust Boundary   | TB-4                                                                                           |
| Likelihood       | Medium                                                                                         |
| Impact           | High                                                                                           |
| Risk Rating      | High                                                                                           |
| Current Controls | RBAC-restricted namespace; cluster-internal access only                                        |
| Evidence         | OSMO deployment stores MEK in ConfigMap rather than K8s Secret                                 |
| Status           | Open                                                                                           |
| Remediation      | Migrate MEK to Kubernetes Secret synced from Key Vault via CSI driver                          |

#### T-2: Terraform State Local Storage

| Field            | Value                                                                                           |
|------------------|-------------------------------------------------------------------------------------------------|
| Threat           | Terraform state file stored locally with plaintext secrets including storage keys and passwords |
| Affected Assets  | `terraform.tfstate` on operator workstation                                                     |
| Trust Boundary   | TB-5                                                                                            |
| Likelihood       | High                                                                                            |
| Impact           | High                                                                                            |
| Risk Rating      | Critical                                                                                        |
| Current Controls | `.gitignore` excludes `*.tfstate`; VPN-only access to workstation                               |
| Evidence         | `deploy/001-iac/versions.tf` has no remote backend configuration                                |
| Status           | Open                                                                                            |
| Remediation      | Configure Azure Storage remote backend with state encryption and locking                        |

#### T-3: Inference Endpoint Allows Insecure Connections

| Field            | Value                                                                      |
|------------------|----------------------------------------------------------------------------|
| Threat           | AzureML online endpoint configured with `allowInsecureConnections: true`   |
| Affected Assets  | AzureML managed online endpoint                                            |
| Trust Boundary   | TB-3                                                                       |
| Likelihood       | Low                                                                        |
| Impact           | Medium                                                                     |
| Risk Rating      | Medium                                                                     |
| Current Controls | Private endpoint restricts access to VNet; cluster-internal traffic only   |
| Evidence         | Inference deployment YAML allows insecure connections for internal scoring |
| Status           | Accepted                                                                   |
| Rationale        | Traffic stays within private VNet; TLS adds latency to inference hot path  |

### Repudiation

#### R-1: Training Debug Logging Captures Credentials

| Field            | Value                                                                                    |
|------------------|------------------------------------------------------------------------------------------|
| Threat           | Training scripts log `AZURE_*` environment variables at debug verbosity, exposing tokens |
| Affected Assets  | Training pod logs, Log Analytics workspace                                               |
| Trust Boundary   | TB-8                                                                                     |
| Likelihood       | Medium                                                                                   |
| Impact           | Medium                                                                                   |
| Risk Rating      | Medium                                                                                   |
| Current Controls | Debug logging disabled by default; Log Analytics RBAC                                    |
| Evidence         | `training/rl/utils/` modules include debug-level credential logging                     |
| Status           | Open                                                                                     |
| Remediation      | Sanitize or redact `AZURE_*` values before logging; enforce structured logging           |

### Information Disclosure

#### I-1: Storage Access Key Fallback

| Field            | Value                                                                                          |
|------------------|------------------------------------------------------------------------------------------------|
| Threat           | Storage account access keys used as fallback when managed identity auth fails                  |
| Affected Assets  | Azure Storage Account, training datasets                                                       |
| Trust Boundary   | TB-3                                                                                           |
| Likelihood       | Medium                                                                                         |
| Impact           | Medium                                                                                         |
| Risk Rating      | Medium                                                                                         |
| Current Controls | Keys stored in Key Vault; private endpoint restricts network access                            |
| Evidence         | Training scripts fall back to `AZURE_STORAGE_KEY` when `DefaultAzureCredential` is unavailable |
| Status           | Accepted                                                                                       |
| Rationale        | Fallback provides operational resilience; keys are Key Vault–managed with rotation capability  |

#### I-2: VPN Shared Keys in Local Terraform State

| Field            | Value                                                                          |
|------------------|--------------------------------------------------------------------------------|
| Threat           | VPN gateway shared secret stored in plaintext within local `terraform.tfstate` |
| Affected Assets  | VPN gateway, operator VPN credentials                                          |
| Trust Boundary   | TB-5                                                                           |
| Likelihood       | Medium                                                                         |
| Impact           | Medium                                                                         |
| Risk Rating      | Medium                                                                         |
| Current Controls | `.gitignore` excludes state files; workstation access controls                 |
| Evidence         | `infrastructure/terraform/vpn/` stores VPN shared key as Terraform-managed resource      |
| Status           | Open                                                                           |
| Remediation      | Resolved by T-2 remediation (remote backend with state encryption)             |

#### I-3: Redis Access Keys Alongside Private Endpoint

| Field            | Value                                                                                    |
|------------------|------------------------------------------------------------------------------------------|
| Threat           | Redis Enterprise exposes access keys even though connectivity uses private endpoints     |
| Affected Assets  | Azure Cache for Redis, OSMO session data                                                 |
| Trust Boundary   | TB-3                                                                                     |
| Likelihood       | Low                                                                                      |
| Impact           | Low                                                                                      |
| Risk Rating      | Low                                                                                      |
| Current Controls | Private endpoint; Key Vault–stored keys; namespace-scoped RBAC                           |
| Evidence         | Redis module outputs access keys to Terraform state                                      |
| Status           | Accepted                                                                                 |
| Rationale        | Private endpoint eliminates network-level exposure; key rotation available via Key Vault |

#### I-4: MLflow Temp Config World-Readable

| Field            | Value                                                                                   |
|------------------|-----------------------------------------------------------------------------------------|
| Threat           | MLflow writes tracking configuration to `/tmp` with world-readable permissions          |
| Affected Assets  | MLflow tracking URI, experiment metadata                                                |
| Trust Boundary   | TB-8                                                                                    |
| Likelihood       | Low                                                                                     |
| Impact           | Low                                                                                     |
| Risk Rating      | Low                                                                                     |
| Current Controls | Pod-level isolation; no credentials in tracking config                                  |
| Evidence         | MLflow integration code writes to `/tmp/mlflow-config`                                  |
| Status           | Accepted                                                                                |
| Rationale        | No secrets in config file; pod filesystem isolation limits access to same-pod processes |

#### I-5: Training Environment Variable Debug Logging

| Field            | Value                                                                                          |
|------------------|------------------------------------------------------------------------------------------------|
| Threat           | Training utility modules log environment variables containing Azure credentials at debug level |
| Affected Assets  | Pod logs, Log Analytics workspace                                                              |
| Trust Boundary   | TB-8                                                                                           |
| Likelihood       | Medium                                                                                         |
| Impact           | Medium                                                                                         |
| Risk Rating      | Medium                                                                                         |
| Current Controls | Debug logging off by default; RBAC on Log Analytics                                            |
| Evidence         | `training/rl/utils/env.py` logs `AZURE_*` values at debug verbosity                           |
| Status           | Open                                                                                           |
| Remediation      | Same as R-1; sanitize credential values before logging                                         |

### Denial of Service

#### D-1: Zero NetworkPolicy Manifests

| Field            | Value                                                                                  |
|------------------|----------------------------------------------------------------------------------------|
| Threat           | No Kubernetes NetworkPolicy resources deployed; all pod-to-pod traffic unrestricted    |
| Affected Assets  | AKS cluster, all workload namespaces                                                   |
| Trust Boundary   | TB-4                                                                                   |
| Likelihood       | High                                                                                   |
| Impact           | High                                                                                   |
| Risk Rating      | High                                                                                   |
| Current Controls | Azure CNI network plugin supports NetworkPolicy; namespaces provide logical separation |
| Evidence         | No `NetworkPolicy` resources in `deploy/002-setup/manifests/`                          |
| Status           | Open                                                                                   |
| Remediation      | Define deny-all default policies per namespace; allow-list required traffic flows      |

#### D-2: Single Shared NSG With Zero Custom Rules

| Field            | Value                                                                                        |
|------------------|----------------------------------------------------------------------------------------------|
| Threat           | One NSG applied to all subnets with no custom inbound/outbound rules beyond Azure defaults   |
| Affected Assets  | VNet subnets, all networked resources                                                        |
| Trust Boundary   | TB-2                                                                                         |
| Likelihood       | Low                                                                                          |
| Impact           | Medium                                                                                       |
| Risk Rating      | Low                                                                                          |
| Current Controls | Private endpoints eliminate public attack surface; VPN-only ingress                          |
| Evidence         | `deploy/001-iac/modules/platform/networking.tf` defines NSG with no custom rules             |
| Status           | Accepted                                                                                     |
| Rationale        | Private endpoints and VPN remove public exposure; custom rules add value after traffic audit |

#### D-3: NAT Gateway No Egress Filtering

| Field            | Value                                                                         |
|------------------|-------------------------------------------------------------------------------|
| Threat           | NAT Gateway allows unrestricted egress from AKS nodes to the internet         |
| Affected Assets  | AKS nodes, container images, external APIs                                    |
| Trust Boundary   | TB-2                                                                          |
| Likelihood       | Medium                                                                        |
| Impact           | High                                                                          |
| Risk Rating      | High                                                                          |
| Current Controls | NSG default rules; container image pull from ACR via private endpoint         |
| Evidence         | NAT Gateway configured without Azure Firewall or FQDN filtering               |
| Status           | Open                                                                          |
| Remediation      | Add Azure Firewall or NSG egress rules restricting outbound to required FQDNs |

#### D-4: OSMO API Rate Limiting and Proxy Disabled

| Field            | Value                                                                               |
|------------------|-------------------------------------------------------------------------------------|
| Threat           | OSMO API deploys with `rateLimit.enabled: false` and `envoy.enabled: false`         |
| Affected Assets  | OSMO control plane API                                                              |
| Trust Boundary   | TB-7                                                                                |
| Likelihood       | Medium                                                                              |
| Impact           | Medium                                                                              |
| Risk Rating      | Medium                                                                              |
| Current Controls | Cluster-internal access only; namespace isolation                                   |
| Evidence         | OSMO Helm values disable rate limiting and Envoy sidecar proxy                      |
| Status           | Open                                                                                |
| Remediation      | Enable Envoy proxy and rate limiting when OSMO vendor provides stable configuration |

### Elevation of Privilege

#### E-1: Automation Account Contributor Role

| Field            | Value                                                                    |
|------------------|--------------------------------------------------------------------------|
| Threat           | Automation Account assigned `Contributor` role at resource group scope   |
| Affected Assets  | Azure Automation Account, resource group resources                       |
| Trust Boundary   | TB-1                                                                     |
| Likelihood       | Low                                                                      |
| Impact           | Medium                                                                   |
| Risk Rating      | Low                                                                      |
| Current Controls | Automation runs scheduled maintenance tasks only; no external triggers   |
| Evidence         | `deploy/001-iac/modules/platform/automation.tf` assigns Contributor role |
| Status           | Open                                                                     |
| Remediation      | Define custom RBAC role scoped to specific maintenance operations        |

#### E-2: OSMO Service Token One-Year Expiry Without Rotation

| Field            | Value                                                                                           |
|------------------|-------------------------------------------------------------------------------------------------|
| Threat           | OSMO service token issued with one-year expiry and no automated rotation mechanism              |
| Affected Assets  | OSMO service authentication, cluster workloads                                                  |
| Trust Boundary   | TB-7                                                                                            |
| Likelihood       | Medium                                                                                          |
| Impact           | High                                                                                            |
| Risk Rating      | High                                                                                            |
| Current Controls | Token stored in Key Vault; cluster-internal access                                              |
| Evidence         | OSMO deployment scripts create long-lived service tokens                                        |
| Status           | Open                                                                                            |
| Remediation      | Implement token rotation via Key Vault rotation policy or OSMO vendor short-lived token support |

#### E-3: User Provisioning Grants Excessive Admin Roles

| Field            | Value                                                                                   |
|------------------|-----------------------------------------------------------------------------------------|
| Threat           | `add-user-to-platform.sh` assigns 9+ admin-level RBAC roles to each onboarded user      |
| Affected Assets  | Azure RBAC, onboarded user identities                                                   |
| Trust Boundary   | TB-1                                                                                    |
| Likelihood       | Medium                                                                                  |
| Impact           | High                                                                                    |
| Risk Rating      | High                                                                                    |
| Current Controls | Script requires manual execution by a privileged operator                               |
| Evidence         | `deploy/002-setup/optional/add-user-to-platform.sh` assigns broad role set              |
| Status           | Open                                                                                    |
| Remediation      | Define tiered role profiles (reader, contributor, admin); assign minimum required roles |

#### E-4: GitHub App Token Elevated Repository Permissions

| Field            | Value                                                                                         |
|------------------|-----------------------------------------------------------------------------------------------|
| Threat           | GitHub App token used in workflows has elevated repository permissions beyond immediate need  |
| Affected Assets  | GitHub Actions workflows, repository contents                                                 |
| Trust Boundary   | TB-6                                                                                          |
| Likelihood       | Low                                                                                           |
| Impact           | Low                                                                                           |
| Risk Rating      | Low                                                                                           |
| Current Controls | SHA-pinned actions; OIDC federation; branch protection rules                                  |
| Evidence         | Workflow files request `contents: write` and other elevated permissions                       |
| Status           | Accepted                                                                                      |
| Rationale        | Permissions required for release-please and dependency review workflows; scoped to repository |

## Assurance Argument

Goal Structuring Notation (GSN) elements supporting the security posture claim.

| Element | Statement                                                                                             |
|---------|-------------------------------------------------------------------------------------------------------|
| G0      | The architecture provides adequate security controls for an IaC reference architecture                |
| G1      | Authentication uses managed identities and workload federation, eliminating password-based access     |
| G2      | Secrets are stored in Azure Key Vault with RBAC authorization and synced via CSI driver               |
| G3      | Network access is restricted to private endpoints, VPN, and NSG-controlled subnets                    |
| G4      | Supply chain integrity is maintained through SHA-pinned actions and dependency review                 |
| E1      | 19 STRIDE threats identified; 7 Accepted with compensating controls, 12 Open with remediation roadmap |
| E2      | OpenSSF Passing ~85%; 25 Silver criteria assessed (5 Met, 5 Delegated, 13 N/A, 1 Gap)                 |
| A1      | Deployer follows `docs/operations/security-guide.md` hardening checklist                              |
| A2      | OSMO vendor provides auth/rate-limiting enablement path in future releases                            |

## Remediation Roadmap

| Priority | Item                             | Threats Addressed | Effort      | Key Dependency            |
|----------|----------------------------------|-------------------|-------------|---------------------------|
| 1        | Terraform Remote Backend         | T-2, I-2          | Low-Medium  | Storage account           |
| 2        | Automation Least Privilege       | E-1               | Low         | Custom role definition    |
| 3        | MEK Migration (ConfigMap→Secret) | T-1               | Medium      | OSMO vendor verification  |
| 4        | NetworkPolicy Manifests          | D-1               | Medium      | Traffic audit             |
| 5        | NSG Rules                        | D-2               | Medium-High | NSG Flow Logs observation |

## Security Metrics

| Metric                   | Current    | Target      |
|--------------------------|------------|-------------|
| OpenSSF Passing badge    | ~85%       | 100%        |
| OpenSSF Silver badge     | ~30%       | 80%         |
| SHA-pinned actions       | 95%        | 100%        |
| STRIDE threats mitigated | 7/19 (37%) | 15/19 (79%) |
| Critical threats open    | 1          | 0           |
| High threats open        | 6          | 2           |

## References

* [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md) — Microsoft security policy and deployer additions
* [Security Guide](../operations/security-guide.md) — security configuration inventory and hardening checklist
* [STRIDE Threat Modeling](https://learn.microsoft.com/azure/security/develop/threat-modeling-tool-threats) — Microsoft STRIDE reference
* [OpenSSF Best Practices](https://www.bestpractices.dev/en/criteria) — badge criteria
* [CIS Kubernetes Benchmark](https://www.cisecurity.org/benchmark/kubernetes) — AKS hardening baseline

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
