#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Appends artifact verification instructions to GitHub release notes.
.PARAMETER Tag
    Release tag to update.
.PARAMETER Repository
    Repository in owner/name format.
#>
[CmdletBinding()]
param(
    [string]$Tag = $env:TAG,
    [string]$Repository = $env:GITHUB_REPOSITORY
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Add-ReleaseVerificationNotesCore {
    [CmdletBinding()]
    param(
        [string]$Tag,
        [string]$Repository
    )

    if ([string]::IsNullOrWhiteSpace($Tag)) {
        throw 'TAG is required.'
    }

    if ([string]::IsNullOrWhiteSpace($Repository)) {
        throw 'GITHUB_REPOSITORY is required.'
    }

    $body = ((& gh release view $Tag --repo $Repository --json body --jq '.body') -join "`n").TrimEnd("`n")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read release notes for $Tag."
    }

    $verification = @'

---

## Artifact Verification

All release artifacts include [Sigstore](https://www.sigstore.dev/) provenance attestations. Verify with the [GitHub CLI](https://cli.github.com/):

```bash
# Download the source archive
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern 'source-TAG_PLACEHOLDER.tar.gz'

# Verify build provenance
gh attestation verify source-TAG_PLACEHOLDER.tar.gz --repo microsoft/physical-ai-toolchain

# Verify SBOM attestation
gh attestation verify source-TAG_PLACEHOLDER.tar.gz --repo microsoft/physical-ai-toolchain --predicate-type https://spdx.dev/Document

# Download and verify the signed wheel (identity ends @refs/heads/main: the pipeline runs on push to main)
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern '*.whl' --pattern '*.whl.sigstore.json'
sigstore verify identity *.whl --bundle *.whl.sigstore.json --cert-identity 'https://github.com/microsoft/physical-ai-toolchain/.github/workflows/main.yml@refs/heads/main' --cert-oidc-issuer 'https://token.actions.githubusercontent.com'

# Download the CycloneDX SBOMs (SPDX equivalents also attached)
gh release download TAG_PLACEHOLDER --repo microsoft/physical-ai-toolchain --pattern 'sbom.cdx.json' --pattern 'dependencies.cdx.json'
```
'@

    $verification = $verification.Replace('TAG_PLACEHOLDER', $Tag)

    ($body + "`n" + $verification + "`n") | Set-Content -LiteralPath 'notes.md' -NoNewline -Encoding utf8NoBOM
    & gh release edit $Tag --repo $Repository --notes-file notes.md
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update release notes for $Tag."
    }

    Write-Output "Verification instructions appended to $Tag"
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Add-ReleaseVerificationNotesCore -Tag $Tag -Repository $Repository)
    }
    catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
