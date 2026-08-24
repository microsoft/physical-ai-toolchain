---
sidebar_position: 2
title: Release Verification
description: Verify release artifact provenance and SBOM attestations for the Physical AI Toolchain
author: Microsoft Robotics-AI Team
ms.date: 2026-06-22
ms.topic: reference
---

Verify the provenance and integrity of release artifacts published by this repository. Each release includes cryptographic attestations generated through GitHub Actions using Sigstore keyless signing, providing tamper-evident proof that artifacts were built from this repository's source code.

## Prerequisites

| Requirement     | Minimum Version | Purpose                                            |
|-----------------|-----------------|----------------------------------------------------|
| GitHub CLI      | 2.49.0+         | `gh attestation verify` subcommand for validation  |
| sigstore-python | 4.3.0+          | `sigstore verify identity` for wheel signatures    |
| gitsign         | 0.13.0+         | `gitsign verify-tag` for constrained tag identity  |
| jq              | 1.6+            | Inspect SBOM JSON contents                         |

Install or update GitHub CLI: <https://cli.github.com/>

Install sigstore-python: `python -m pip install sigstore`. Install gitsign: <https://docs.sigstore.dev/cosign/signing/gitsign/>.

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

## Verify Wheel Signatures

Each release attaches the root `physical_ai_toolchain-<version>-py3-none-any.whl` wheel and a matching `physical_ai_toolchain-<version>-py3-none-any.whl.sigstore.json` Sigstore bundle. Verify the wheel against the CI workflow identity that signed it:

```bash
sigstore verify identity physical_ai_toolchain-1.2.3-py3-none-any.whl \
  --bundle physical_ai_toolchain-1.2.3-py3-none-any.whl.sigstore.json \
  --cert-identity 'https://github.com/microsoft/physical-ai-toolchain/.github/workflows/main.yml@refs/heads/main' \
  --cert-oidc-issuer 'https://token.actions.githubusercontent.com'
```

The certificate identity ends in `@refs/heads/main` because the release pipeline runs `on: push: branches: [main]`, so the wheel is signed inside that run's `attest-release` job rather than on a tag ref.

> [!NOTE]
> The release publishes a single root wheel, so the `*.whl` / `*.whl.sigstore.json` globs resolve to one file each. If a future release attaches multiple wheels, verify each wheel against its own bundle individually.

## Verify Build Provenance

The release wheel carries an `actions/attest-build-provenance` attestation served through the GitHub attestations API. Verify it with the GitHub CLI:

```bash
gh attestation verify physical_ai_toolchain-1.2.3-py3-none-any.whl \
  --repo microsoft/physical-ai-toolchain
```

To verify from a locally downloaded bundle rather than querying the GitHub attestations API, download the wheel's Sigstore bundle (`wheels-v1.2.3.sigstore.json`) and pass it with `--bundle`:

```bash
gh release download v1.2.3 --repo microsoft/physical-ai-toolchain \
  --pattern 'physical_ai_toolchain-*.whl' \
  --pattern 'wheels-v1.2.3.sigstore.json'

gh attestation verify physical_ai_toolchain-1.2.3-py3-none-any.whl \
  --bundle wheels-v1.2.3.sigstore.json \
  --repo microsoft/physical-ai-toolchain
```

> [!NOTE]
> The wheel provenance is produced by `actions/attest-build-provenance` (an in-job Sigstore signer), so verify it with `gh attestation verify`. `slsa-verifier verify-artifact` matches against the `slsa-github-generator` builder ID and will reject this bundle. Because the release pipeline runs `on: push: branches: [main]`, the provenance binds to the `main` branch ref rather than a tag ref.

## Inspect CycloneDX SBOMs

Each release attaches CycloneDX SBOMs (`sbom.cdx.json` for the source tree, `dependencies.cdx.json` for resolved dependencies) alongside the SPDX equivalents (`sbom.spdx.json`, `dependencies.spdx.json`). Download and inspect the CycloneDX assets with `jq`:

```bash
gh release download v1.2.3 --repo microsoft/physical-ai-toolchain \
  --pattern 'sbom.cdx.json' --pattern 'dependencies.cdx.json'

jq '.components[] | {name, version}' dependencies.cdx.json
```

CycloneDX feeds SCA and vulnerability tooling such as Dependency-Track, while SPDX targets license-compliance and SBOM-exchange consumers; the release publishes both so downstream tools can pick their native format.

## Verify Constrained Tag Signatures

CI gates pushed `v*` tags with constrained `gitsign verify-tag`, which binds the signature to the pinned CI workflow identity instead of accepting any valid Sigstore signature. Run the same constrained check locally:

```bash
gitsign verify-tag \
  --certificate-identity 'https://github.com/microsoft/physical-ai-toolchain/.github/workflows/main.yml@refs/heads/main' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  v1.2.3
```

> [!NOTE]
> `git tag -v` confirms cryptographic integrity and a Rekor entry but does not validate the signer identity. Prefer `gitsign verify-tag` with the pinned identity and issuer for release gating.

## Related Documentation

- [Deployment Security Guide](../operations/security-guide.md): Security configuration inventory and deployment responsibilities
- [Threat Model](threat-model.md): STRIDE-based threat analysis and remediation roadmap
- [SECURITY.md](https://github.com/microsoft/physical-ai-toolchain/blob/main/SECURITY.md): Vulnerability disclosure and reporting process
- [GitHub attestation verification](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations/using-artifact-attestations-to-establish-provenance-for-builds): GitHub documentation on artifact attestations
