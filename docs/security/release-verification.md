---
sidebar_position: 2
title: Release Verification
description: Verify release artifact provenance and SBOM attestations for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-03-07
ms.topic: reference
---

Verify the provenance and integrity of release artifacts published by this repository. Each release includes cryptographic attestations generated through GitHub Actions using Sigstore keyless signing, providing tamper-evident proof that artifacts were built from this repository's source code.

## Prerequisites

| Requirement | Minimum Version | Purpose                                           |
|-------------|-----------------|---------------------------------------------------|
| GitHub CLI  | 2.49.0+         | `gh attestation verify` subcommand for validation |

Install or update GitHub CLI: <https://cli.github.com/>

## Verify Release Artifacts

Download the release artifact from the GitHub Releases page, then verify its provenance attestation:

```bash
gh attestation verify source-v1.2.3.tar.gz \
  --repo microsoft/physical-ai-toolchain
```

Replace `source-v1.2.3.tar.gz` with the actual release artifact filename.

To verify the SBOM attestation specifically:

```bash
gh attestation verify source-v1.2.3.tar.gz \
  --repo microsoft/physical-ai-toolchain \
  --predicate-type https://spdx.dev/Document
```

## What Verification Confirms

Successful verification proves three properties:

- The Sigstore certificate identity is bound to this repository's GitHub Actions workflow, confirming the artifact was produced by an authorized CI/CD pipeline
- A Rekor transparency log entry exists for the signing event, providing an immutable, publicly auditable record
- The artifact digest matches the signed attestation, confirming the file has not been modified since signing

## Inspect the SBOM

Each release includes an SPDX SBOM attestation. Download and inspect the SBOM contents using the GitHub CLI and `jq`:

```bash
gh attestation verify source-v1.2.3.tar.gz \
  --repo microsoft/physical-ai-toolchain \
  --predicate-type https://spdx.dev/Document \
  --format json | jq '.verificationResult.statement.predicate'
```

The SBOM follows the SPDX 2.3 specification and lists all package dependencies included in the release artifact.

## Related Documentation

- [Deployment Security Guide](../operations/security-guide.md): Security configuration inventory and deployment responsibilities
- [Threat Model](threat-model.md): STRIDE-based threat analysis and remediation roadmap
- [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md): Vulnerability disclosure and reporting process
- [GitHub attestation verification](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds): GitHub documentation on artifact attestations
