#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Checks pinned binary hashes and Helm chart versions against upstream sources.

.DESCRIPTION
    Downloads each pinned binary/GPG key, computes SHA-256, and compares against the
    pinned hash in source files. Queries Helm repositories (HTTPS and OCI) for latest
    chart versions and compares against pinned versions. Emits a SARIF 2.1.0 report
    with per-finding rule IDs for GitHub Security tab integration.

.PARAMETER SarifFile
    Output SARIF file path. Default: binary-freshness-results.sarif.

.PARAMETER ConfigPreview
    Print configuration and exit without performing checks.

.PARAMETER RepoRoot
    Repository root. Defaults to `git rev-parse --show-toplevel`.

.EXAMPLE
    ./Test-BinaryFreshness.ps1
    Run all checks and write SARIF to default path.

.EXAMPLE
    ./Test-BinaryFreshness.ps1 -SarifFile results.sarif
    Run all checks and write SARIF to the given path.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$SarifFile = 'binary-freshness-results.sarif',

    [Parameter(Mandatory = $false)]
    [switch]$ConfigPreview,

    [Parameter(Mandatory = $false)]
    [string]$RepoRoot
)

$ErrorActionPreference = 'Stop'

# ============================================================
#Constants
# ============================================================
$script:SarifSchema = 'https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json'

$script:RuleDefinitions = @(
    @{
        Id        = 'binary-freshness/download-failure'
        ShortText = 'Binary download failed'
        HelpPath  = 'scripts/security/Test-BinaryFreshness.ps1'
    }
    @{
        Id        = 'binary-freshness/hash-mismatch'
        ShortText = 'Pinned hash does not match upstream'
        HelpPath  = 'scripts/security/Test-BinaryFreshness.ps1'
    }
    @{
        Id        = 'binary-freshness/version-drift'
        ShortText = 'Pinned chart version differs from latest'
        HelpPath  = 'scripts/update-chart-hashes.sh'
    }
    @{
        Id        = 'binary-freshness/lookup-failure'
        ShortText = 'Chart version lookup failed after retries'
        HelpPath  = 'scripts/security/Test-BinaryFreshness.ps1'
    }
)

# ============================================================
# Variable extraction
# ============================================================

function Get-ShellVariable {
    <#
    .SYNOPSIS
        Extract a shell-style assignment `VAR="value"` from a file.
        Unwraps `${OTHER:-default}` to the default literal.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $match = Select-String -Path $Path -Pattern "^$([regex]::Escape($Name))=" -SimpleMatch:$false |
        Select-Object -First 1

    if (-not $match) { return $null }

    $value = $match.Line -replace '^[^=]*="', '' -replace '"$', ''
    if ($value -match '^\$\{[^:]+:-(.+)\}$') {
        return $Matches[1]
    }
    return $value
}

function Get-JsonVariable {
    <#
    .SYNOPSIS
        Extract an inline `NAME=value` token from a JSON-embedded shell snippet
        (devcontainer.json post-create script).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $content = Get-Content -Path $Path -Raw
    $pattern = "(?<=$([regex]::Escape($Name))=)[^ \`"\\]+"
    $m = [regex]::Match($content, $pattern)
    if ($m.Success) { return $m.Value }
    return $null
}

# ============================================================
# SARIF helpers
# ============================================================

function New-SarifResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RuleId,
        [Parameter(Mandatory)][string]$Message,
        [Parameter(Mandatory)][string]$File,
        [Parameter(Mandatory)][ValidateSet('error', 'warning', 'note')][string]$Level
    )

    return [ordered]@{
        ruleId    = $RuleId
        level     = $Level
        message   = [ordered]@{ text = $Message }
        locations = @(
            [ordered]@{
                physicalLocation = [ordered]@{
                    artifactLocation = [ordered]@{
                        uri       = $File
                        uriBaseId = '%SRCROOT%'
                    }
                }
            }
        )
    }
}

function New-SarifReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Repository,
        [Parameter()][object[]]$Results = @()
    )

    $infoUri = "https://github.com/$Repository"
    $rules = foreach ($rule in $script:RuleDefinitions) {
        [ordered]@{
            id               = $rule.Id
            shortDescription = [ordered]@{ text = $rule.ShortText }
            helpUri          = "$infoUri/blob/main/$($rule.HelpPath)"
        }
    }

    return [ordered]@{
        '$schema' = $script:SarifSchema
        version   = '2.1.0'
        runs      = @(
            [ordered]@{
                tool = [ordered]@{
                    driver = [ordered]@{
                        name           = 'binary-freshness-check'
                        informationUri = $infoUri
                        rules          = @($rules)
                    }
                }
                results = @($Results)
            }
        )
    }
}

# ============================================================
# Hash & version checks
# ============================================================

function Invoke-HashCheck {
    <#
    .SYNOPSIS
        Download a URL, compute SHA-256, and compare against expected.
        Returns a hashtable describing the outcome.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Expected,
        [Parameter(Mandatory)][string]$File
    )

    $tmp = New-TemporaryFile
    try {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $tmp.FullName -UseBasicParsing -ErrorAction Stop | Out-Null
        }
        catch {
            return @{
                Status  = 'DownloadFailed'
                Name    = $Name
                Url     = $Url
                File    = $File
                Message = "Failed to download $Name from $Url"
            }
        }

        $actual = (Get-FileHash -Path $tmp.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $expectedLower = $Expected.ToLowerInvariant()

        if ($actual -ne $expectedLower) {
            return @{
                Status   = 'Mismatch'
                Name     = $Name
                File     = $File
                Expected = $expectedLower
                Actual   = $actual
                Message  = "Hash mismatch for ${Name}: expected $expectedLower, got $actual. The upstream binary has changed."
            }
        }

        return @{ Status = 'Match'; Name = $Name; File = $File }
    }
    finally {
        Remove-Item -Path $tmp.FullName -Force -ErrorAction SilentlyContinue
    }
}

function Test-HelmVersionCurrent {
    <#
    .SYNOPSIS
        Compare pinned vs latest chart version (strips leading 'v').
        Returns @{ IsCurrent=<bool>; Pinned=<string>; Latest=<string> }.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Pinned,
        [Parameter(Mandatory)][string]$Latest
    )

    $p = $Pinned.TrimStart('v')
    $l = $Latest.TrimStart('v')
    return @{
        IsCurrent = ($p -eq $l)
        Pinned    = $p
        Latest    = $l
    }
}

function Invoke-WithRetry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][int]$MaxAttempts,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $result = & $Action
        if ($null -ne $result -and "$result" -ne '') {
            return $result
        }
        if ($attempt -lt $MaxAttempts) {
            Write-Warning "  Attempt $attempt/$MaxAttempts failed, retrying in ${attempt}s..."
            Start-Sleep -Seconds $attempt
        }
    }
    return $null
}

function Get-HelmRepoLatestVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepoName,
        [Parameter(Mandatory)][string]$RepoUrl,
        [Parameter(Mandatory)][string]$Chart
    )

    helm repo add $RepoName $RepoUrl --force-update *> $null
    helm repo update $RepoName *> $null
    $json = helm search repo $Chart --versions -o json 2>$null
    if (-not $json) { return $null }
    $parsed = $json | ConvertFrom-Json
    if (-not $parsed -or $parsed.Count -eq 0) { return $null }
    return $parsed[0].version
}

function Get-HelmOciLatestVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Chart
    )

    $output = helm show chart $Chart 2>$null
    if (-not $output) { return $null }
    $line = $output | Where-Object { $_ -match '^version:' } | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -split ':', 2)[1].Trim()
}

# ============================================================
# Main orchestration
# ============================================================

function Invoke-BinaryFreshnessCheck {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$SarifFile,
        [Parameter(Mandatory)][string]$Repository
    )

    Push-Location $RepoRoot
    try {
        $devDeps = 'infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh'
        $thinlinc = 'infrastructure/setup/optional/isaac-sim-vm/scripts/install-thinlinc-silent.sh'
        $devcontainer = '.devcontainer/devcontainer.json'
        $defaultsConf = 'infrastructure/setup/defaults.conf'

        $sarifResults = [System.Collections.Generic.List[object]]::new()
        $mismatch = 0

        # ---- Binary hash checks ----
        Write-Host ''
        Write-Host '=== Binary Hash Freshness Check ==='

        $binaryChecks = @(
            @{
                Name     = 'NodeSource GPG Key'
                Url      = 'https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key'
                Expected = (Get-ShellVariable -Path $devDeps -Name 'NODESOURCE_GPG_SHA256')
                File     = $devDeps
            }
            @{
                Name     = "uv Installer (v$(Get-ShellVariable -Path $devDeps -Name 'UV_VERSION'))"
                Url      = "https://astral.sh/uv/$(Get-ShellVariable -Path $devDeps -Name 'UV_VERSION')/install.sh"
                Expected = (Get-ShellVariable -Path $devDeps -Name 'UV_INSTALLER_SHA256')
                File     = $devDeps
            }
            @{
                Name     = 'Microsoft GPG Key'
                Url      = 'https://packages.microsoft.com/keys/microsoft.asc'
                Expected = (Get-ShellVariable -Path $devDeps -Name 'MICROSOFT_GPG_SHA256')
                File     = $devDeps
            }
            @{
                Name     = 'NVIDIA Container Toolkit GPG Key'
                Url      = 'https://nvidia.github.io/libnvidia-container/gpgkey'
                Expected = (Get-ShellVariable -Path $devDeps -Name 'NVIDIA_CTK_GPG_SHA256')
                File     = $devDeps
            }
            @{
                Name     = "ThinLinc Server (v$(Get-ShellVariable -Path $thinlinc -Name 'TL_VERSION'))"
                Url      = "https://www.cendio.com/downloads/server/tl-$(Get-ShellVariable -Path $thinlinc -Name 'TL_VERSION')-server.zip"
                Expected = (Get-ShellVariable -Path $thinlinc -Name 'TL_SHA256')
                File     = $thinlinc
            }
            @{
                Name     = "TFLint ($(Get-JsonVariable -Path $devcontainer -Name 'TFLINT_VERSION'))"
                Url      = "https://github.com/terraform-linters/tflint/releases/download/$(Get-JsonVariable -Path $devcontainer -Name 'TFLINT_VERSION')/tflint_linux_amd64.zip"
                Expected = (Get-JsonVariable -Path $devcontainer -Name 'TFLINT_SHA256')
                File     = $devcontainer
            }
            @{
                Name     = "OSMO Installer ($(Get-JsonVariable -Path $devcontainer -Name 'OSMO_VERSION'))"
                Url      = "https://raw.githubusercontent.com/NVIDIA/OSMO/refs/tags/$(Get-JsonVariable -Path $devcontainer -Name 'OSMO_VERSION')/install.sh"
                Expected = (Get-JsonVariable -Path $devcontainer -Name 'OSMO_INSTALLER_SHA256')
                File     = $devcontainer
            }
            @{
                Name     = "NGC CLI ($(Get-JsonVariable -Path $devcontainer -Name 'NGC_CLI_VERSION'))"
                Url      = "https://api.ngc.nvidia.com/v2/resources/nvidia/ngc-apps/ngc_cli/versions/$(Get-JsonVariable -Path $devcontainer -Name 'NGC_CLI_VERSION')/files/ngccli_linux.zip"
                Expected = (Get-JsonVariable -Path $devcontainer -Name 'NGC_CLI_SHA256')
                File     = $devcontainer
            }
        )

        foreach ($check in $binaryChecks) {
            Write-Host "Checking $($check.Name)..."
            $result = Invoke-HashCheck -Name $check.Name -Url $check.Url -Expected ($check.Expected ?? '') -File $check.File

            switch ($result.Status) {
                'Match' {
                    Write-Host "  [OK] $($check.Name) hash matches"
                }
                'DownloadFailed' {
                    Write-Host "::error file=$($check.File)::$($result.Message)"
                    $mismatch++
                    $sarifResults.Add((New-SarifResult -RuleId 'binary-freshness/download-failure' `
                        -Message $result.Message -File $check.File -Level 'error'))
                }
                'Mismatch' {
                    Write-Host "::warning file=$($check.File)::$($result.Message)"
                    $mismatch++
                    $sarifResults.Add((New-SarifResult -RuleId 'binary-freshness/hash-mismatch' `
                        -Message $result.Message -File $check.File -Level 'warning'))
                }
            }
        }

        # ---- Helm chart version checks ----
        Write-Host ''
        Write-Host '=== Helm Chart Version Freshness ==='

        $gpuPinned = Get-ShellVariable -Path $defaultsConf -Name 'GPU_OPERATOR_VERSION'
        $kaiPinned = Get-ShellVariable -Path $defaultsConf -Name 'KAI_SCHEDULER_VERSION'
        $osmoPinned = Get-ShellVariable -Path $defaultsConf -Name 'OSMO_CHART_VERSION'
        $gpuRepo = Get-ShellVariable -Path $defaultsConf -Name 'HELM_REPO_GPU_OPERATOR'
        $osmoRepo = Get-ShellVariable -Path $defaultsConf -Name 'HELM_REPO_OSMO'

        $helmChecks = @(
            @{
                Name   = 'GPU Operator'
                Pinned = $gpuPinned
                Latest = (Invoke-WithRetry -MaxAttempts 3 -Action { Get-HelmRepoLatestVersion -RepoName 'nvidia' -RepoUrl $gpuRepo -Chart 'nvidia/gpu-operator' })
                Source = 'Helm repository'
            }
            @{
                Name   = 'KAI Scheduler'
                Pinned = $kaiPinned
                Latest = (Invoke-WithRetry -MaxAttempts 3 -Action { Get-HelmOciLatestVersion -Chart 'oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler' })
                Source = 'OCI registry'
            }
            @{
                Name   = 'OSMO Operator'
                Pinned = $osmoPinned
                Latest = (Invoke-WithRetry -MaxAttempts 3 -Action { Get-HelmRepoLatestVersion -RepoName 'osmo' -RepoUrl $osmoRepo -Chart 'osmo/backend-operator' })
                Source = 'Helm repository'
            }
        )

        foreach ($check in $helmChecks) {
            if (-not $check.Latest) {
                $msg = "Failed to query $($check.Name) chart version from $($check.Source) after retries."
                Write-Host "::warning::$msg"
                $mismatch++
                $sarifResults.Add((New-SarifResult -RuleId 'binary-freshness/lookup-failure' `
                    -Message $msg -File $defaultsConf -Level 'warning'))
                continue
            }

            $cmp = Test-HelmVersionCurrent -Pinned $check.Pinned -Latest $check.Latest
            Write-Host "Checking $($check.Name) (pinned: $($cmp.Pinned), latest: $($cmp.Latest))..."
            if (-not $cmp.IsCurrent) {
                $msg = "$($check.Name) pinned at $($cmp.Pinned) but latest is $($cmp.Latest). Run scripts/update-chart-hashes.sh to update pinned hashes."
                Write-Host "::warning file=${defaultsConf}::$msg"
                $mismatch++
                $sarifResults.Add((New-SarifResult -RuleId 'binary-freshness/version-drift' `
                    -Message $msg -File $defaultsConf -Level 'warning'))
            }
            else {
                Write-Host "  [OK] $($check.Name) version is current"
            }
        }

        # ---- SARIF report ----
        Write-Host ''
        Write-Host '=== SARIF Report ==='

        $report = New-SarifReport -Repository $Repository -Results $sarifResults.ToArray()
        $report | ConvertTo-Json -Depth 20 | Set-Content -Path $SarifFile -Encoding utf8

        Write-Host "SARIF results written to $SarifFile ($($sarifResults.Count) finding(s))"

        # ---- Summary ----
        Write-Host ''
        Write-Host '=== Summary ==='
        Write-Host "SARIF File       : $SarifFile"
        Write-Host "Mismatches       : $mismatch"
        Write-Host "SARIF Findings   : $($sarifResults.Count)"

        if ($mismatch -gt 0) {
            Write-Host "::warning::$mismatch pinned hash(es) differ from upstream. Review warnings above and update the affected scripts."
        }
        else {
            Write-Host 'All pinned hashes match upstream.'
        }

        return @{ Mismatch = $mismatch; Findings = $sarifResults.Count }
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

function Resolve-Repository {
    [CmdletBinding()]
    param([string]$RepoRoot)

    if ($env:GITHUB_REPOSITORY) { return $env:GITHUB_REPOSITORY }

    try {
        $url = git -C $RepoRoot remote get-url origin 2>$null
        if ($url) {
            return ($url -replace '.*github\.com[:/]', '' -replace '\.git$', '').Trim()
        }
    }
    catch { Write-Verbose "git remote get-url failed: $_" }

    return 'unknown/unknown'
}

# ============================================================
# Entry point (skipped when dot-sourced by tests)
# ============================================================

if ($MyInvocation.InvocationName -ne '.') {
    $resolvedRoot = Resolve-RepoRoot -Hint $RepoRoot
    $repository = Resolve-Repository -RepoRoot $resolvedRoot

    if ($ConfigPreview) {
        Write-Host '=== Configuration Preview ==='
        Write-Host "SARIF File         : $SarifFile"
        Write-Host "Repo Root          : $resolvedRoot"
        Write-Host "GitHub Repository  : $repository"
        exit 0
    }

    foreach ($tool in @('helm')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Write-Error "Required tool not found: $tool"
            exit 2
        }
    }

    try {
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $resolvedRoot -SarifFile $SarifFile -Repository $repository
        exit ([int]($outcome.Mismatch -gt 0))
    }
    catch {
        Write-Error $_
        exit 2
    }
}
