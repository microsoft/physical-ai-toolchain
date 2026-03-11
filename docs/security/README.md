---
title: Security Documentation
description: Index of security documentation including threat model and deployment security guide
author: Microsoft Robotics-AI Team
ms.date: 2026-02-22
ms.topic: overview
keywords:
  - security
  - threat model
  - deployment
  - vulnerability
  - compliance
---

## 📋 Overview

Security documentation for the Physical AI Toolchain covering threat analysis, deployment hardening, and vulnerability reporting.

## 📄 Documents

| Document                                                     | Description                                                      |
|--------------------------------------------------------------|------------------------------------------------------------------|
| [Threat Model](threat-model.md)                              | STRIDE-based threat analysis and remediation roadmap             |
| [Deployment Security Guide](../operations/security-guide.md) | Security configuration inventory and deployment responsibilities |
| [Release Verification](release-verification.md)              | Verify release artifact provenance and SBOM attestations         |
| [SECURITY.md](../../SECURITY.md)                             | Vulnerability disclosure and reporting process                   |

## 🔒 Security Posture

This reference architecture deploys AKS clusters with GPU node pools, Azure Machine Learning, and NVIDIA OSMO for robotics training and inference. All components are infrastructure-as-code artifacts; no hosted service or user-facing application exists.

The [threat model](threat-model.md) documents:

- 19 threats across STRIDE categories
- Security controls mapped to each threat
- Trust boundary analysis across IaC, cluster, and ML pipeline layers
- Prioritized remediation roadmap

The [security guide](../operations/security-guide.md) documents:

- Default security configurations shipped with the architecture
- Deployment team responsibilities before, during, and after provisioning
- Security considerations checklist with Azure documentation references

## 🔗 Related Resources

- [Contributing security review](../contributing/security-review.md): Contributor security checklist for pull requests
- [Azure security documentation](https://learn.microsoft.com/azure/security/): Authoritative security guidance for Azure services
- [AKS baseline architecture](https://learn.microsoft.com/azure/architecture/reference-architectures/containers/aks/baseline-aks): Production-ready AKS security patterns

---

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
