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
    issue steps and, under GitHub Actions, emits attention-count, drift-count, and
    error-count to GITHUB_OUTPUT.

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

class HveCoreFileValidationException : System.Exception {
    HveCoreFileValidationException([string]$message) : base($message) {}
}

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
    $sha = if ($content -match '(?m)^\s*UPSTREAM_REF:\s*[''"]?([0-9a-fA-F]{40})[''"]?\s*(?:#.*)?$') {
        $Matches[1]
    }
    else {
        $null
    }
    $tag = if ($content -match 'hve-core release:\s*([A-Za-z0-9._/+.-]{1,128})(?:\s|$)') { $Matches[1] } else { 'unknown' }

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
        throw [HveCoreFileValidationException]::new("Derived file not found locally: $Path")
    }

    $content = Get-Content -Path $Path -Raw
    if ([string]::IsNullOrWhiteSpace($content)) {
        throw [HveCoreFileValidationException]::new("Derived file is empty: $Path")
    }
    $headerEnd = $content.IndexOf('#>')
    if ($headerEnd -lt 0) {
        throw [HveCoreFileValidationException]::new("Could not find a comment-based help header in $Path")
    }
    $header = $content.Substring(0, $headerEnd + 2)
    $pattern = 'Adapted from\s+microsoft/hve-core\s+(\S+)\s+as of commit\s+([0-9a-fA-F]{40})(?:\.|\s|$)'
    $sourceMatches = [regex]::Matches($header, $pattern)
    if ($sourceMatches.Count -eq 0) {
        throw [HveCoreFileValidationException]::new("Could not extract hve-core source revision from $Path")
    }
    if ($sourceMatches.Count -gt 1) {
        throw [HveCoreFileValidationException]::new("Found multiple hve-core source revisions in $Path")
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
        Returns 'missing-both', 'missing-baseline', 'missing-target', 'drift', or 'current'.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$BaselineUpstreamSha,
        [Parameter(Mandatory)][AllowEmptyString()][string]$TargetUpstreamSha
    )

    $baseline = $BaselineUpstreamSha.ToLowerInvariant()
    $target = $TargetUpstreamSha.ToLowerInvariant()
    if (-not $baseline -and -not $target) { return 'missing-both' }
    if (-not $baseline) { return 'missing-baseline' }
    if (-not $target) { return 'missing-target' }
    if ($baseline -ne $target) { return 'drift' }
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

    $out = gh api --method GET "repos/$Repo/contents/$Path" -f "ref=$Ref" --jq '.sha' 2>&1
    if ($LASTEXITCODE -eq 0) {
        $sha = ("$out").Trim()
        if ($sha -notmatch $script:FullShaPattern) {
            throw "gh api returned an invalid blob SHA for ${Path}@${Ref}: $out"
        }
        return $sha
    }

    # A genuine 404 means the file is absent at that ref.
    # Any other failure (transient, auth, rate limit) must fail loudly rather than
    # masquerade as drift and file a false tracking issue.
    if ("$out" -match 'HTTP 404') { return '' }
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
        [Parameter(Mandatory)][string]$BaselineRef,
        [Parameter(Mandatory)][string]$TargetRef
    )

    $baselineUpstreamSha = Get-HveCoreBlobSha -Repo $Repo -Path $Path -Ref $BaselineRef
    $targetUpstreamSha = Get-HveCoreBlobSha -Repo $Repo -Path $Path -Ref $TargetRef
    $state = Get-DriftState -BaselineUpstreamSha $baselineUpstreamSha -TargetUpstreamSha $targetUpstreamSha
    if ($state -eq 'missing-both') {
        throw [HveCoreFileValidationException]::new(
            "Upstream path '$Path' is absent at both baseline ref '$BaselineRef' and target ref '$TargetRef'"
        )
    }
    $baselineRefUrl = [uri]::EscapeDataString($BaselineRef)
    $targetRefUrl = [uri]::EscapeDataString($TargetRef)

    return [ordered]@{
        Path                = $Path
        BaselineUpstreamSha = $baselineUpstreamSha
        TargetUpstreamSha   = $targetUpstreamSha
        BaselineRef         = $BaselineRef
        TargetRef           = $TargetRef
        ComparisonUrl       = "https://github.com/$Repo/compare/$baselineRefUrl...$targetRefUrl"
        Drift               = ($state -ne 'current')
        State               = $state
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
        'missing-target'   { return '❓ Not found at target ref' }
        'error'            { return '❌ Check failed' }
        'current'          { return '✅ Current' }
        default {
            $stateText = ConvertTo-HveCoreMarkdownText -Value $State
            return "❓ Unknown state: $stateText"
        }
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

function Get-HveCoreShortSha {
    <#
    .SYNOPSIS
        Shortens a SHA for display while preserving empty and short values.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Value
    )

    return $Value.Substring(0, [Math]::Min($script:ShortShaLength, $Value.Length))
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

    $baselineSha = Get-HveCoreShortSha -Value $File.BaselineUpstreamSha
    $targetSha = Get-HveCoreShortSha -Value $File.TargetUpstreamSha
    $baselineRef = if ($File.BaselineRef -match $script:FullShaPattern) {
        Get-HveCoreShortSha -Value $File.BaselineRef
    }
    else {
        $File.BaselineRef
    }
    $targetRef = if ($File.TargetRef -match $script:FullShaPattern) {
        Get-HveCoreShortSha -Value $File.TargetRef
    }
    else {
        $File.TargetRef
    }
    $baselineRef = ConvertTo-HveCoreMarkdownText -Value $baselineRef
    $targetRef = ConvertTo-HveCoreMarkdownText -Value $targetRef

    $status = Get-HveCoreStateLabel -State $File.State
    if ($File.State -eq 'error' -and $File.Error) {
        $message = "$($File.Error)" -replace '[\r\n]+', ' '
        $message = ConvertTo-HveCoreMarkdownText -Value $message
        $status = "$status — <code>$message</code>"
    }

    $baseline = switch ($File.Baseline) {
        'source-header' { 'Source header' }
        'release' { 'Release' }
        default {
            $baselineText = if ($File.Baseline) { "$($File.Baseline)" } else { 'unknown' }
            ConvertTo-HveCoreMarkdownText -Value $baselineText
        }
    }

    return [ordered]@{
        Baseline    = $baseline
        Status      = $status
        BaselineSha = $baselineSha
        TargetSha   = $targetSha
        Comparison  = if ($File.ComparisonUrl) { "[$baselineRef → $targetRef]($($File.ComparisonUrl))" } else { '' }
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
    return "| ``$($File.Path)`` | $($cells.Baseline) | $($cells.Comparison) | $($cells.BaselineSha) | $($cells.TargetSha) | $($cells.Status) |"
}

function Get-HveCoreTrustedReleaseUrl {
    <#
    .SYNOPSIS
        Validates that a release URL is an absolute HTTPS URL on github.com.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowNull()][AllowEmptyString()][string]$Value
    )

    $uri = $null
    if (-not [uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or
        $uri.Host -ne 'github.com') {
        throw "Unexpected hve-core release URL: $Value"
    }
    return $uri.AbsoluteUri
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
    $latestUrl = Get-HveCoreTrustedReleaseUrl -Value $Result.LatestUrl
    $latestLink = "[$latestTagText]($latestUrl)"
    $pinnedTagText = ConvertTo-HveCoreMarkdownText -Value $pin.PinnedTag
    $latestMainShaText = ConvertTo-HveCoreMarkdownText -Value $Result.LatestMainSha

    $fileRows = ($files | ForEach-Object { Format-HveCoreDriftRow -File $_ }) -join "`n"

    $compareBase = if ($pin.PinnedTag -ne 'unknown') { $pin.PinnedTag } else { $pin.PinnedSha }
    $compareBase = [uri]::EscapeDataString($compareBase)
    $latestTagUrl = [uri]::EscapeDataString($Result.LatestTag)

    return @"
## hve-core Upstream Freshness Report

Latest hve-core release: $latestLink
Release-file baseline: ``$pinnedTagText`` (``UPSTREAM_REF`` in ``$($pin.File)``)
Source-header target: ``$latestMainShaText``
Action required: $($Result.DriftCount) drifted, $($Result.ErrorCount) check errors

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

    $latestTagText = ConvertTo-HveCoreMarkdownText -Value $Result.LatestTag
    $pinnedTagText = ConvertTo-HveCoreMarkdownText -Value $pin.PinnedTag
    $latestMainShaText = ConvertTo-HveCoreMarkdownText -Value $Result.LatestMainSha
    $fileRows = $files | ForEach-Object { Format-HveCoreDriftRow -File $_ }

    return @"
## hve-core Upstream Freshness

Latest release: $latestTagText
Release-file baseline: $pinnedTagText
Source-header target: $latestMainShaText
Action required: $($Result.DriftCount) drifted, $($Result.ErrorCount) check errors

| File | Baseline | Upstream comparison | Baseline upstream blob | Target upstream blob | Status |
|------|----------|---------------------|------------------------|----------------------|--------|
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

    $n = gh issue list --search $script:IssueSearch --limit 1 --json number --jq '.[0].number // empty' 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not query existing hve-core freshness issue: $n"
    }
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

    $encodedRef = [uri]::EscapeDataString($Ref)
    $sha = gh api "repos/$Repo/commits/$encodedRef" --jq '.sha' 2>&1
    if ($LASTEXITCODE -ne 0 -or -not $sha) {
        throw "Could not resolve ref '$Ref' in ${Repo}: $sha"
    }
    $sha = ("$sha").Trim()
    if ($sha -notmatch $script:FullShaPattern) {
        throw "Could not resolve ref '$Ref' in ${Repo}: invalid commit SHA '$sha'"
    }
    return $sha
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
        [Parameter(Mandatory)][string]$LatestReleaseSha,
        [Parameter(Mandatory)][string]$LatestMainSha
    )

    $path = $File.Path
    if (-not (Test-Path $path)) {
        throw [HveCoreFileValidationException]::new("Derived file not found locally: $path")
    }

    switch ($File.Baseline) {
        'source-header' {
            $source = Get-HveCoreFileSource -Path $path
            if ($source.Path -cne $path) {
                throw [HveCoreFileValidationException]::new(
                    "Vendored path '$path' must match its recorded hve-core source path, but the header records '$($source.Path)'"
                )
            }
            $result = Get-HveCoreFileDrift -Repo $script:UpstreamRepo -Path $source.Path -BaselineRef $source.Sha -TargetRef $LatestMainSha
            if ($result.State -eq 'missing-baseline') {
                throw [HveCoreFileValidationException]::new(
                    "Recorded source path '$($source.Path)' is absent at its recorded commit '$($source.Sha)'"
                )
            }
            return $result
        }
        'release' {
            return Get-HveCoreFileDrift -Repo $script:UpstreamRepo -Path $path -BaselineRef $PinnedReleaseSha -TargetRef $LatestReleaseSha
        }
        default {
            throw [HveCoreFileValidationException]::new("Unsupported hve-core baseline '$($File.Baseline)' for $path")
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
                $r = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha $pinRef.Sha -LatestReleaseSha $latestSha -LatestMainSha $latestMainSha
                $r['Baseline'] = $file.Baseline
            }
            catch [HveCoreFileValidationException] {
                $r = [ordered]@{
                    Path                = $path
                    Baseline            = $file.Baseline
                    BaselineUpstreamSha = ''
                    TargetUpstreamSha   = ''
                    BaselineRef         = ''
                    TargetRef           = ''
                    ComparisonUrl       = ''
                    Drift               = $false
                    State               = 'error'
                    Error               = $_.Exception.Message
                }
            }

            $status = if ($r.State -eq 'error') { 'ERROR' } elseif ($r.Drift) { 'DRIFT' } else { 'OK' }
            Write-Host "$status $path : $($r.State)"
            $r
        }
        $fileResults = @($fileResults)

        $driftCount = @($fileResults | Where-Object { $_.Drift }).Count
        $errorCount = @($fileResults | Where-Object { $_.State -eq 'error' }).Count
        $attentionCount = $driftCount + $errorCount
        Write-Host "`nAction required: $driftCount drifted, $errorCount check errors"

        [ordered]@{
            LatestTag     = $latestTag
            LatestReleaseSha = $latestSha
            LatestUrl     = $latestUrl
            LatestMainSha = $latestMainSha
            DriftCount    = $driftCount
            ErrorCount    = $errorCount
            Pin           = $pin
            Files         = $fileResults
        } | ConvertTo-Json -Depth 5 | Set-Content $ResultsFile

        return [ordered]@{
            AttentionCount = $attentionCount
            DriftCount     = $driftCount
            ErrorCount     = $errorCount
            LatestTag      = $latestTag
        }
    }
    finally {
        Pop-Location
    }
}

function Write-HveCoreGitHubOutputs {
    <#
    .SYNOPSIS
        Appends freshness counters to a GitHub Actions output file.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Outcome,
        [Parameter(Mandatory)][string]$Path
    )

    "attention-count=$($Outcome.AttentionCount)" >> $Path
    "drift-count=$($Outcome.DriftCount)" >> $Path
    "error-count=$($Outcome.ErrorCount)" >> $Path
}

function Get-HveCoreFreshnessExitCode {
    <#
    .SYNOPSIS
        Returns a non-zero exit code for validation errors while leaving drift as
        an issue-triaged signal.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Outcome
    )

    if ($Outcome.ErrorCount -gt 0) { return 2 }
    return 0
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
            Write-HveCoreGitHubOutputs -Outcome $outcome -Path $env:GITHUB_OUTPUT
        }
        $exitCode = Get-HveCoreFreshnessExitCode -Outcome $outcome
        if ($exitCode -ne 0) {
            Write-Error -ErrorAction Continue "hve-core freshness check failed with $($outcome.ErrorCount) validation error(s)"
        }
        exit $exitCode
    }
    catch {
        Write-Error $_
        exit 2
    }
}
