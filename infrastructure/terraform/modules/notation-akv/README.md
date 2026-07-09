---
title: Notation AKV Module (Notation v1 Signing Key on Azure Key Vault Premium HSM)
description: Provisions a Notation v1 signing key in Azure Key Vault Premium HSM with federated managed identity for keyless OCI artifact signing.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-25
ms.topic: reference
---

This module provisions a Notation v1 signing key (RSA-HSM or EC-HSM) in an Azure Key Vault Premium HSM and a user-assigned managed identity federated to GitHub Actions tag releases and ARC runner ServiceAccounts. Workflows assume the identity via OIDC and invoke `notation sign` against the AKV plugin, producing CNCF Notary v2 signatures attached to OCI artifacts in ACR without exporting private key material.

The module is disabled by default; enable it at the root by setting `signing_mode = "notation"`. Callers may supply an existing Key Vault via `var.key_vault` or let the module provision a Premium HSM Key Vault scoped to the module. Federated credential subjects are caller-supplied to support both GitHub OIDC release tags and AKS workload-identity ServiceAccounts.

## 📖 Documentation

| Document                                                               | Purpose                                                   |
|------------------------------------------------------------------------|-----------------------------------------------------------|
| [TERRAFORM.md](TERRAFORM.md)                                           | Auto-generated input / output / resource reference.       |
| [container-signing.md](../../../../docs/security/container-signing.md) | End-to-end container supply-chain reference architecture. |

*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
