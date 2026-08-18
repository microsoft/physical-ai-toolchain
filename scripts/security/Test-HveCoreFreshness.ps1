#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Checks hve-core-derived files against their reviewed upstream baselines.

.DESCRIPTION
    Compares each hve-core-derived file's upstream blob SHA at its reviewed source
    revision against its current upstream target. Modules use the RPI bootstrap's
    pinned UPSTREAM_REF and the latest release. Security linters use the source
    revision recorded in their headers and upstream main, which also covers files
    not present in a release. Writes a JSON results file consumed by the tracking-
    issue steps and, under GitHub Actions, emits the stale item count to GITHUB_OUTPUT.

.PARAMETER ResultsFile
    Output JSON results path. Default: hve-core-freshness-results.json.

.PARAMETER ConfigPreview
    Print configuration and exit without performing checks.

.PARAMETER RepoRoot
    Repository root. Defaults to `git rev-parse --show-toplevel`.

.EXAMPLE
    ./Test-HveCoreFreshness.ps1
    Run all checks and write results to the default path.

.EXAMPLE
    ./Test-HveCoreFreshness.ps1 -ConfigPreview
    Print configuration and exit.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ResultsFile = 'hve-core-freshness-results.json',

    [Parameter(Mandatory = $false)]
    [switch]$ConfigPreview,

    [Parameter(Mandatory = $false)]
    [string]$RepoRoot
)

$ErrorActionPreference = 'Stop'

# ============================================================
# Constants
# ============================================================
$script:UpstreamRepo = 'microsoft/hve-core'
$script:SetupWorkflow = '.github/workflows/copilot-setup-steps.yml'
$script:IssueMarker = 'automation:hve-core-freshness'
$script:IssueSearch = "in:body $script:IssueMarker is:open"
$script:FullShaPattern = '^[0-9a-fA-F]{40}$'
$script:ShortShaLength = 7

# Release entries use the RPI pin; source-header entries require the exact header
# "Adapted from microsoft/hve-core <path> as of commit <40-hex SHA>" and track main.
# Blob-to-blob comparisons avoid false drift from intentional local adaptations.
$script:DerivedFiles = @(
    [ordered]@{ Path = 'scripts/security/Modules/SecurityHelpers.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/security/Modules/SecurityClasses.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/linting/Modules/LintingHelpers.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/tests/Mocks/GitMocks.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/lib/Modules/CIHelpers.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/linting/Modules/FrontmatterValidation.psm1'; Baseline = 'release' }
    [ordered]@{ Path = 'scripts/security/Test-WorkflowPermissions.ps1'; Baseline = 'source-header' }
    [ordered]@{ Path = 'scripts/security/Test-DangerousWorkflow.ps1'; Baseline = 'source-header' }
)

# ============================================================
# Pure helpers
# ============================================================

function Get-PinnedHveCoreRef {
    <#
    .SYNOPSIS
        Extract the pinned hve-core UPSTREAM_REF SHA and release tag from the RPI
        bootstrap workflow file - the last reviewed upstream ref used as the drift
        baseline. Returns null when the file is absent; members are null when the
        corresponding key is not present.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $content = Get-Content -Path $Path -Raw
    $sha = if ($content -match 'UPSTREAM_REF:\s*([0-9a-fA-F]{7,40})') { $Matches[1] } else { $null }
    $tag = if ($content -match 'hve-core release:\s*(\S+)') { $Matches[1] } else { 'unknown' }

    return [ordered]@{ Tag = $tag; Sha = $sha }
}

function Get-HveCoreFileSource {
    <#
    .SYNOPSIS
        Reads an hve-core source path and commit from a vendored file's header.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path
    )

    if (-not (Test-Path $Path)) {
        throw "Derived file not found locally: $Path"
    }

    $content = Get-Content -Path $Path -Raw
    $pattern = 'Adapted from\s+microsoft/hve-core\s+(\S+)\s+as of commit\s+([0-9a-fA-F]{40})(?:\.|\s|$)'
    $sourceMatches = [regex]::Matches($content, $pattern)
    if ($sourceMatches.Count -eq 0) {
        throw "Could not extract hve-core source revision from $Path"
    }
    if ($sourceMatches.Count -gt 1) {
        throw "Found multiple hve-core source revisions in $Path"
    }
    $match = $sourceMatches[0]

    return [ordered]@{
        Path = $match.Groups[1].Value
        Sha  = $match.Groups[2].Value.ToLowerInvariant()
    }
}

function Select-LatestRelease {
    <#
    .SYNOPSIS
        Pick the newest non-draft release from a GitHub releases API payload.
        hve-core publishes rolling prereleases and rarely flags a stable one, so
        'releases/latest' lags; the newest non-draft tag is authoritative here.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Releases
    )

    return @($Releases |
            Where-Object { -not $_.draft } |
            Sort-Object { [datetime]$_.created_at } -Descending)[0]
}

function Get-DriftState {
    <#
    .SYNOPSIS
        Classify a derived file's state from its baseline and target upstream blob SHAs.
        Returns 'missing-baseline', 'missing-upstream', 'drift', or 'current'.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$PinnedUpstreamSha,
        [Parameter(Mandatory = $false)][AllowEmptyString()][string]$LatestUpstreamSha
    )

    $p = $PinnedUpstreamSha.ToLowerInvariant()
    $l = $LatestUpstreamSha.ToLowerInvariant()
    if (-not $p) { return 'missing-baseline' }
    if (-not $l) { return 'missing-upstream' }
    if ($p -ne $l) { return 'drift' }
    return 'current'
}

function Get-HveCoreBlobSha {
    <#
    .SYNOPSIS
        Gets the git blob SHA for a specific path and ref from the remote repository.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Ref
    )

    $out = gh api "repos/$Repo/contents/$Path`?ref=$Ref" --jq '.sha' 2>&1
    if ($LASTEXITCODE -eq 0) { return ("$out").Trim() }

    # A genuine 404 means the file is absent at that ref (a real 'missing-upstream').
    # Any other failure (transient, auth, rate limit) must fail loudly rather than
    # masquerade as drift and file a false tracking issue.
    if ("$out" -match 'HTTP 404|Not Found') { return '' }
    throw "gh api failed for ${Path}@${Ref}: $out"
}

function Get-HveCoreFileDrift {
    <#
    .SYNOPSIS
        Compares a single upstream path's blob SHA at its baseline ref against its target
        ref and returns a drift record. Local copies are never consulted, so local
        adaptations cannot cause false drift.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$PinnedRef,
        [Parameter(Mandatory)][string]$LatestRef
    )

    $pinnedUp = Get-HveCoreBlobSha -Repo $Repo -Path $Path -Ref $PinnedRef
    $latestUp = Get-HveCoreBlobSha -Repo $Repo -Path $Path -Ref $LatestRef
    $state = Get-DriftState -PinnedUpstreamSha $pinnedUp -LatestUpstreamSha $latestUp
    $pinnedRefUrl = [uri]::EscapeDataString($PinnedRef)
    $latestRefUrl = [uri]::EscapeDataString($LatestRef)

    return [ordered]@{
        Path              = $Path
        PinnedUpstreamSha = if ($pinnedUp) { $pinnedUp } else { '' }
        LatestUpstreamSha = if ($latestUp) { $latestUp } else { '' }
        PinnedRef         = $PinnedRef
        LatestRef         = $LatestRef
        ComparisonUrl     = "https://github.com/$Repo/compare/$pinnedRefUrl...$latestRefUrl"
        Drift             = ($state -ne 'current')
        State             = $state
    }
}

function Get-HveCoreStateLabel {
    <#
    .SYNOPSIS
        Converts a drift state into a user-friendly label/icon.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$State
    )

    switch ($State) {
        'drift'            { return '⚠️ Upstream advanced' }
        'missing-baseline' { return '❓ Not found at baseline ref' }
        'missing-upstream' { return '❓ Not found at target ref' }
        'error'            { return '❌ Check failed' }
        default            { return '✅ Current' }
    }
}

function ConvertTo-HveCoreMarkdownText {
    <#
    .SYNOPSIS
        Encodes untrusted text for safe use inside a markdown table or link label.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )

    return ([System.Net.WebUtility]::HtmlEncode($Value) `
            -replace '\[', '&#91;' `
            -replace '\]', '&#93;' `
            -replace '\(', '&#40;' `
            -replace '\)', '&#41;' `
            -replace '\|', '&#124;' `
            -replace '`', '&#96;')
}

function Format-HveCoreDriftCells {
    <#
    .SYNOPSIS
        Formats the shared display values for a drift record.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$File
    )

    $pSha = if ($File.PinnedUpstreamSha) { $File.PinnedUpstreamSha.Substring(0, [Math]::Min($script:ShortShaLength, $File.PinnedUpstreamSha.Length)) } else { '' }
    $lSha = if ($File.LatestUpstreamSha) { $File.LatestUpstreamSha.Substring(0, [Math]::Min($script:ShortShaLength, $File.LatestUpstreamSha.Length)) } else { '' }
    $pRef = if ($File.PinnedRef -match $script:FullShaPattern) { $File.PinnedRef.Substring(0, $script:ShortShaLength) } else { $File.PinnedRef }
    $lRef = if ($File.LatestRef -match $script:FullShaPattern) { $File.LatestRef.Substring(0, $script:ShortShaLength) } else { $File.LatestRef }
    $pRef = ConvertTo-HveCoreMarkdownText -Value $pRef
    $lRef = ConvertTo-HveCoreMarkdownText -Value $lRef

    $status = Get-HveCoreStateLabel -State $File.State
    if ($File.State -eq 'error' -and $File.Error) {
        $message = "$($File.Error)" -replace '[\r\n]+', ' '
        $message = ConvertTo-HveCoreMarkdownText -Value $message
        $status = "$status — <code>$message</code>"
    }

    $baseline = switch ($File.Baseline) {
        'source-header' { 'Source header' }
        'release' { 'Release' }
        default { "$($File.Baseline)" }
    }

    return [ordered]@{
        Baseline   = $baseline
        Status     = $status
        PinnedSha  = $pSha
        LatestSha  = $lSha
        Comparison = if ($File.ComparisonUrl) { "[$pRef → $lRef]($($File.ComparisonUrl))" } else { '' }
    }
}

function Format-HveCoreDriftRow {
    <#
    .SYNOPSIS
        Formats one drift record as a markdown table row with its upstream comparison,
        short baseline/target blob SHAs, and status label.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$File
    )

    $cells = Format-HveCoreDriftCells -File $File
    return "| ``$($File.Path)`` | $($cells.Baseline) | $($cells.Comparison) | $($cells.PinnedSha) | $($cells.LatestSha) | $($cells.Status) |"
}

function Format-HveCoreIssueBody {
    <#
    .SYNOPSIS
        Formats the markdown body for the tracking issue.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Result,
        [Parameter(Mandatory)][string]$RunUrl,
        [Parameter(Mandatory)][string]$CheckDate
    )

    $pin = $Result.Pin
    $files = @($Result.Files)
    $latestTagText = ConvertTo-HveCoreMarkdownText -Value $Result.LatestTag
    $latestLink = "[$latestTagText]($($Result.LatestUrl))"

    $fileRows = ($files | ForEach-Object { Format-HveCoreDriftRow -File $_ }) -join "`n"

    $compareBase = if ($pin.PinnedTag -ne 'unknown') { $pin.PinnedTag } else { $pin.PinnedSha }
    $compareBase = [uri]::EscapeDataString($compareBase)
    $latestTagUrl = [uri]::EscapeDataString($Result.LatestTag)

    return @"
## hve-core Upstream Freshness Report

Latest reviewed hve-core release: $latestLink
Release-file baseline: ``$($pin.PinnedTag)`` (``UPSTREAM_REF`` in ``$($pin.File)``)
Source-header target: ``$($Result.LatestMainSha)``

### Derived Files

| File | Baseline | Upstream comparison | Baseline upstream blob | Target upstream blob | Status |
|------|----------|---------------------|------------------------|----------------------|--------|
$fileRows

### How to Refresh

Review the upstream changes and port any relevant ones into the locally-adapted copy; do not blindly overwrite (these files carry intentional local adaptations). Each row links to its exact baseline comparison. After refreshing a source-header file, update its ``as of commit`` SHA to the reviewed upstream revision. Release baseline: https://github.com/microsoft/hve-core/compare/$compareBase...$latestTagUrl

Run ``npm run lint:ps`` and ``npm run test:ps`` after changes.

---
**Workflow Run:** $RunUrl
**Detection Date:** $CheckDate

<!-- $($script:IssueMarker) -->
"@
}

function Format-HveCoreJobSummary {
    <#
    .SYNOPSIS
        Formats the markdown body for the GitHub Actions job summary.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Result
    )

    $pin = $Result.Pin
    $files = @($Result.Files)

    $fileRows = foreach ($f in $files) {
        $cells = Format-HveCoreDriftCells -File $f
        "| $($f.Path) | $($cells.Baseline) | $($cells.Comparison) | $($cells.PinnedSha) | $($cells.LatestSha) | $($cells.Status) |"
    }

    return @"
## hve-core Upstream Freshness

Latest release: $($Result.LatestTag)
Release-file baseline: $($pin.PinnedTag)
Source-header target: $($Result.LatestMainSha)

| Derived File | Baseline | Upstream comparison | Baseline blob | Target blob | Status |
|--------------|----------|---------------------|---------------|-------------|--------|
$($fileRows -join "`n")
"@
}

function Get-HveCoreTrackingIssue {
    <#
    .SYNOPSIS
        Retrieves the issue number of an existing open freshness tracking issue.
    #>
    [CmdletBinding()]
    param()

    $n = gh issue list --search $script:IssueSearch --limit 1 --json number --jq '.[0].number // empty'
    if ($LASTEXITCODE -ne 0) { return $null }
    return $(if ($n) { $n.Trim() } else { $null })
}

# ============================================================
# Upstream queries
# ============================================================

function Get-HveCoreReleases {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Repo)

    $raw = gh api "repos/$Repo/releases?per_page=30"
    if ($LASTEXITCODE -ne 0 -or -not $raw) {
        throw "Could not fetch releases for $Repo - freshness check cannot produce reliable results"
    }
    return @($raw | ConvertFrom-Json)
}

function Resolve-HveCoreCommitSha {
    <#
    .SYNOPSIS
        Resolves a ref (tag or SHA) to a full commit SHA in the remote repository.
        Throws when the ref does not resolve, so an invalid tag or pinned ref fails
        loudly instead of silently degrading blob lookups to false drift.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$Ref
    )

    $sha = gh api "repos/$Repo/commits/$Ref" --jq '.sha' 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $sha) {
        throw "Could not resolve ref '$Ref' in ${Repo}: $sha"
    }
    return ("$sha").Trim()
}

function Assert-HveCoreCommitOnMain {
    <#
    .SYNOPSIS
        Verifies that a recorded source commit is an ancestor of upstream main.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][string]$CommitSha,
        [Parameter(Mandatory)][string]$MainSha
    )

    $mergeBase = gh api "repos/$Repo/compare/$CommitSha...$MainSha" --jq '.merge_base_commit.sha' 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $mergeBase) {
        throw "Could not verify source commit '$CommitSha' against upstream main in ${Repo}: $mergeBase"
    }
    if (("$mergeBase").Trim() -ne $CommitSha) {
        throw "Recorded source commit '$CommitSha' is not an ancestor of upstream main '$MainSha'"
    }
}

function Get-HveCoreFileDriftForBaseline {
    <#
    .SYNOPSIS
        Resolves one derived-file baseline and returns its upstream drift record.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$File,
        [Parameter(Mandatory)][string]$PinnedReleaseSha,
        [Parameter(Mandatory)][string]$LatestReleaseTag,
        [Parameter(Mandatory)][string]$LatestMainSha
    )

    $path = $File.Path
    if (-not (Test-Path $path)) {
        throw "Derived file not found locally: $path"
    }

    switch ($File.Baseline) {
        'source-header' {
            $source = Get-HveCoreFileSource -Path $path
            if ($source.Path -ne $path) {
                throw "Vendored path '$path' must match its recorded hve-core source path, but the header records '$($source.Path)'"
            }
            Assert-HveCoreCommitOnMain -Repo $script:UpstreamRepo -CommitSha $source.Sha -MainSha $LatestMainSha
            return Get-HveCoreFileDrift -Repo $script:UpstreamRepo -Path $source.Path -PinnedRef $source.Sha -LatestRef $LatestMainSha
        }
        'release' {
            return Get-HveCoreFileDrift -Repo $script:UpstreamRepo -Path $path -PinnedRef $PinnedReleaseSha -LatestRef $LatestReleaseTag
        }
        default {
            throw "Unsupported hve-core baseline '$($File.Baseline)' for $path"
        }
    }
}

# ============================================================
# Orchestration
# ============================================================

function Invoke-HveCoreFreshnessCheck {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$ResultsFile
    )

    Push-Location $RepoRoot
    try {
        $latest = Select-LatestRelease -Releases (Get-HveCoreReleases -Repo $script:UpstreamRepo)
        if (-not $latest) {
            throw "No non-draft releases found for $script:UpstreamRepo"
        }
        $latestTag = $latest.tag_name
        $latestUrl = $latest.html_url

        $latestSha = Resolve-HveCoreCommitSha -Repo $script:UpstreamRepo -Ref $latestTag
        Write-Host "Latest hve-core release: $latestTag ($latestSha)"
        $latestMainSha = Resolve-HveCoreCommitSha -Repo $script:UpstreamRepo -Ref 'main'
        Write-Host "Latest hve-core main: $latestMainSha"

        $pinRef = Get-PinnedHveCoreRef -Path $script:SetupWorkflow
        if (-not $pinRef -or -not $pinRef.Sha) {
            throw "Could not extract UPSTREAM_REF from $script:SetupWorkflow"
        }

        # Fail loudly if the pinned ref itself does not resolve upstream; otherwise every
        # blob lookup at that ref 404s and is misreported as drift.
        $null = Resolve-HveCoreCommitSha -Repo $script:UpstreamRepo -Ref $pinRef.Sha

        $pin = [ordered]@{
            PinnedTag = $pinRef.Tag
            PinnedSha = $pinRef.Sha
            File      = $script:SetupWorkflow
        }

        $fileResults = foreach ($file in $script:DerivedFiles) {
            $path = $file.Path
            try {
                $r = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha $pinRef.Sha -LatestReleaseTag $latestTag -LatestMainSha $latestMainSha
                $r['Baseline'] = $file.Baseline
            }
            catch {
                $r = [ordered]@{
                    Path              = $path
                    Baseline          = $file.Baseline
                    PinnedUpstreamSha = ''
                    LatestUpstreamSha = ''
                    PinnedRef         = ''
                    LatestRef         = ''
                    ComparisonUrl     = ''
                    Drift             = $true
                    State             = 'error'
                    Error             = $_.Exception.Message
                }
            }

            $icon = if ($r.Drift) { 'DRIFT' } else { 'OK' }
            Write-Host "$icon $path : $($r.State)"
            $r
        }
        $fileResults = @($fileResults)

        $driftCount = @($fileResults | Where-Object { $_.Drift }).Count
        $staleCount = $driftCount
        Write-Host "`nStale: $driftCount / $($fileResults.Count) derived files drifted"

        [ordered]@{
            LatestTag     = $latestTag
            LatestUrl     = $latestUrl
            LatestMainSha = $latestMainSha
            Pin           = $pin
            Files         = $fileResults
        } | ConvertTo-Json -Depth 5 | Set-Content $ResultsFile

        return [ordered]@{ StaleCount = $staleCount; LatestTag = $latestTag }
    }
    finally {
        Pop-Location
    }
}

function Resolve-RepoRoot {
    [CmdletBinding()]
    param([string]$Hint)

    if ($Hint) { return (Resolve-Path $Hint).Path }

    try {
        $root = git rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -eq 0 -and $root) { return $root.Trim() }
    }
    catch { Write-Verbose "git rev-parse failed: $_" }

    return (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
}

# ============================================================
# Entry point (skipped when dot-sourced by tests)
# ============================================================

if ($MyInvocation.InvocationName -ne '.') {
    $resolvedRoot = Resolve-RepoRoot -Hint $RepoRoot

    if ($ConfigPreview) {
        Write-Host '=== Configuration Preview ==='
        Write-Host "Upstream Repo  : $script:UpstreamRepo"
        Write-Host "Setup Workflow : $script:SetupWorkflow"
        Write-Host "Results File   : $ResultsFile"
        Write-Host "Repo Root      : $resolvedRoot"
        Write-Host 'Derived Files :'
        $script:DerivedFiles | ForEach-Object { Write-Host "  - $($_.Path) [$($_.Baseline)]" }
        exit 0
    }

    foreach ($tool in @('gh', 'git')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Write-Error "Required tool not found: $tool"
            exit 2
        }
    }

    try {
        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $resolvedRoot -ResultsFile $ResultsFile
        if ($env:GITHUB_OUTPUT) {
            "stale-count=$($outcome.StaleCount)" >> $env:GITHUB_OUTPUT
        }
        exit 0
    }
    catch {
        Write-Error $_
        exit 2
    }
}
