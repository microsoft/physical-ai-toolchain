---
title: ARC Runners Module (GitHub Actions Runner Scale Set on AKS)
description: Installs the GitHub Actions Runner Controller (ARC) gha-runner-scale-set on AKS with workload-identity federation and a Sigstore-aware egress allowlist.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-25
ms.topic: reference
---

This module installs the GitHub Actions Runner Controller (ARC) `gha-runner-scale-set` on the existing AKS cluster and federates a user-assigned managed identity to the runner ServiceAccount via AKS workload identity. Runners that build container images can therefore push to ACR and read the GitHub App private key from Key Vault without static credentials.

When `should_enable_sigstore_egress = true` (default), the module also installs a NetworkPolicy and a hostname allowlist ConfigMap restricting runner egress to the endpoints required for Sigstore keyless signing (Fulcio, Rekor, TUF), GitHub control plane, ACR, and Key Vault. The hostname allowlist is consumed by the cluster CNI / egress-gateway layer.

## 📖 Documentation

| Document                                                               | Purpose                                                   |
|------------------------------------------------------------------------|-----------------------------------------------------------|
| [TERRAFORM.md](TERRAFORM.md)                                           | Auto-generated input / output / resource reference.       |
| [container-signing.md](../../../../docs/security/container-signing.md) | End-to-end container supply-chain reference architecture. |

*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
