---
title: Sigstore Mirror Module
description: Optional Storage Account static website serving an air-gapped Sigstore TUF mirror
author: Microsoft Robotics-AI Team
ms.date: 2026-04-25
ms.topic: reference
---

Provisions the storage surface for an air-gapped Sigstore TUF mirror. Disabled by default; enable when builders or verifiers cannot reach the public Sigstore good instance.

The module provisions infrastructure only. A separate scheduled refresh job (cron tracked via the `refresh_schedule_cron` tag) is responsible for syncing TUF metadata into the `$web` container.

## 📖 Documentation

| Document                                                            | Description                                                           |
|---------------------------------------------------------------------|-----------------------------------------------------------------------|
| [Terraform Reference](TERRAFORM.md)                                 | Auto-generated inputs, outputs, and resources reference               |
| [Container signing](../../../../docs/security/container-signing.md) | End-to-end signing reference architecture this module participates in |

*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
