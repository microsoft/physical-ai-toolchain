---
sidebar_position: 10
title: Security Review Process
description: Security checklist, credential handling, and vulnerability reporting for contributions
author: Microsoft Robotics-AI Team
ms.date: 2026-02-03
ms.topic: how-to
---

> [!NOTE]
> This guide expands on the [Security Review Process](README.md#-security-review-process) section of the main contributing guide.

Security-sensitive contributions require additional review to ensure Azure security best practices.

## Security Checklist

Contributions touching these areas require security review:

### RBAC and Permissions

* [ ] Follows principle of least privilege
* [ ] Role assignments scoped to specific resources (not subscription-wide)
* [ ] Custom roles justified and documented
* [ ] No Owner role unless explicitly required

### Private Endpoints and Networking

* [ ] Private endpoints enabled for production network mode
* [ ] Network security group rules properly scoped
* [ ] Service endpoints used appropriately
* [ ] Public access disabled where not required

### Credentials and Secrets

* [ ] Uses managed identities or workload identity (no service principals/passwords)
* [ ] Secrets stored in Key Vault, never in code or terraform.tfvars
* [ ] No hardcoded connection strings or API keys
* [ ] Proper secret rotation mechanisms

### Network Policies

* [ ] Kubernetes network policies defined for workload isolation
* [ ] Pod security standards enforced (baseline or restricted)
* [ ] Ingress/egress rules properly configured
* [ ] No overly permissive firewall rules

### Workload Identity

* [ ] Federated credentials properly configured
* [ ] Service account annotations correct
* [ ] Token audience restrictions in place
* [ ] No long-lived credentials used

### Security Scanning

* [ ] Container images scanned with Trivy or Defender
* [ ] Terraform code scanned with Checkov or tfsec
* [ ] No critical or high vulnerabilities introduced
* [ ] Dependency versions current and patched

## How to Report Security Issues

> [!WARNING]
> **DO NOT** report security vulnerabilities through public GitHub issues.

Report security vulnerabilities to the Microsoft Security Response Center (MSRC). See [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md) for complete instructions.

For non-security bugs that have security implications (e.g., excessive permissions), use the standard bug reporting process but add the `security` label.

## Dependency Updates

Security patch PRs are encouraged and receive expedited review:

### Security Update Process

1. Create PR with dependency version bump
2. Document CVE or security advisory addressed
3. Provide validation evidence (vulnerability scan before/after)
4. Maintainers fast-track review and merge

### Example PR Description

```markdown
## Security Update: Upgrade Terraform AzureRM Provider

**CVE:** CVE-2024-XXXXX
**Severity:** High
**Advisory:** https://github.com/advisories/GHSA-xxxx-xxxx-xxxx

**Changes:**
- Upgraded `azurerm` provider from 3.75.0 to 3.76.0
- Addresses authentication bypass vulnerability in private endpoint configurations

**Validation:**
- terraform fmt/validate: ✅ Passed
- terraform plan: ✅ No unexpected changes
- Checkov scan: ✅ No new violations

**References:**
- [Provider Changelog](https://github.com/hashicorp/terraform-provider-azurerm/blob/main/CHANGELOG.md)
```

## Related Documentation

* [Contributing Guide](README.md) - Prerequisites, workflow, commit messages
* [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md) - Security vulnerability reporting
* [Deployment Validation](deployment-validation.md) - Validation levels and testing
* [Infrastructure Style](infrastructure-style.md) - Secure coding patterns
