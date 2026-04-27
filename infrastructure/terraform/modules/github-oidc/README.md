---
title: GitHub OIDC Module (Workload Identity for Publish Workflows)
description: Provisions the user-assigned managed identity and federated credentials that let this repository's GitHub Actions workflows push signed images to ACR.
author: Microsoft Robotics-AI Team
ms.date: 2026-04-25
ms.topic: reference
---

This module wires a single user-assigned managed identity (UAMI) to a configurable set of GitHub OIDC subject claims. The UAMI is the principal that the `container-publish.yml` and `container-publish-notation.yml` reusable workflows assume via `azure/login` to push signed images to ACR.

The module is scoped to this repository only. Forks and downstream consumers that publish their own images instantiate their own `github-oidc` module with their own `github_owner` / `github_repo` / `federated_subjects` values; the reference architecture does not federate cross-repository trust.

## 📖 Documentation

| Document                              | Description                                                                  |
|---------------------------------------|------------------------------------------------------------------------------|
| [Terraform Reference](TERRAFORM.md)   | Auto-generated inputs, outputs, and resources reference                      |
| [Container signing](../../../../docs/security/container-signing.md) | End-to-end signing reference architecture this module participates in        |

*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
