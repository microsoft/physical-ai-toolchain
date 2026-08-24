#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Verifies committed uv.lock files stay consistent with their pyproject.toml manifests.
.DESCRIPTION
    Discovers every directory carrying a uv.lock (excluding .venv/, external/, node_modules/,
    .git/, .copilot-tracking/, docs/docusaurus/), runs `uv lock --check` in each, and writes
    results to logs/. Drift is surfaced as CI annotations and a GitHub step summary, and the
    script exits non-zero when any lock drifts from its manifest.
.PARAMETER OutputPath
    Path for the JSON results file. Defaults to logs/uv-lock-consistency-results.json.
.PARAMETER Projects
    Explicit list of repository-relative project directories to check. When omitted, every
    directory containing a uv.lock is discovered automatically.
.PARAMETER ChangedFilesOnly
    When set, only check projects whose uv.lock or pyproject.toml changed relative to BaseBranch.
.PARAMETER BaseBranch
    Base branch for changed-file detection. Defaults to origin/main.
.EXAMPLE
    ./Invoke-UvLockConsistencyCheck.ps1
.EXAMPLE
    ./Invoke-UvLockConsistencyCheck.ps1 -ChangedFilesOnly
.NOTES
    Runs via: npm run lint:uvlock
#>

[CmdletBinding()]
param(
    [string]$OutputPath,
    [string[]]$Projects = @(),
    [switch]$ChangedFilesOnly,
    [string]$BaseBranch = 'origin/main'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'Modules/LintingHelpers.psm1') -Force
Import-Module (Join-Path $PSScriptRoot '../lib/Modules/CIHelpers.psm1') -Force

#region Helper Functions

function Get-UvLockProject {
    <#
    .SYNOPSIS
        Discovers repository-relative directories that contain a uv.lock file.
    .OUTPUTS
        [string[]] Sorted, unique directory paths (forward-slash, '.' for the root).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$RepoRoot
    )

    $excludeDirs = @('.venv', 'external', 'node_modules', '.git', '.copilot-tracking', 'docs/docusaurus')

    $lockFiles = @(Get-ChildItem -Path $RepoRoot -Filter 'uv.lock' -Recurse -File -ErrorAction SilentlyContinue | Where-Object {
            $relativePath = $_.FullName.Substring($RepoRoot.Length + 1) -replace '\\', '/'
            $excluded = $false
            foreach ($dir in $excludeDirs) {
                if ($relativePath -like "$dir/*" -or $relativePath -like "*/$dir/*") {
                    $excluded = $true
                    break
                }
            }
            -not $excluded
        })

    $dirs = foreach ($lock in $lockFiles) {
        $parent = (Split-Path $lock.FullName -Parent).Substring($RepoRoot.Length).TrimStart([System.IO.Path]::DirectorySeparatorChar, '/', '\') -replace '\\', '/'
        if ([string]::IsNullOrEmpty($parent)) { '.' } else { $parent }
    }

    return @($dirs | Sort-Object -Unique)
}

function Invoke-UvLockCheck {
    <#
    .SYNOPSIS
        Runs `uv lock --check` for a single project directory.
    .OUTPUTS
        [pscustomobject] With ExitCode and Output members.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$ProjectDirectory
    )

    $output = (& uv --directory $ProjectDirectory lock --check 2>&1 | Out-String)
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output   = $output.Trim()
    }
}

function Test-UvLockProject {
    <#
    .SYNOPSIS
        Checks lock/manifest consistency for one project and returns a structured result.
    .OUTPUTS
        [pscustomobject] With Project, Passed, and Detail members.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Project,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$RepoRoot
    )

    $absDir = if ($Project -eq '.') { $RepoRoot } else { Join-Path $RepoRoot $Project }
    $check = Invoke-UvLockCheck -ProjectDirectory $absDir

    return [pscustomobject]@{
        Project = $Project
        Passed  = ($check.ExitCode -eq 0)
        Detail  = $check.Output
    }
}

function Select-ChangedProject {
    <#
    .SYNOPSIS
        Filters projects to those whose uv.lock or pyproject.toml changed relative to a base branch.
    .OUTPUTS
        [string[]] The subset of Projects with a changed manifest or lock.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Project,

        [Parameter(Mandatory = $false)]
        [string]$Base = 'origin/main'
    )

    $changedFiles = @(Get-ChangedFilesFromGit -BaseBranch $Base -FileExtensions @('*uv.lock', '*pyproject.toml'))
    if ($changedFiles.Count -eq 0) {
        return @()
    }

    $changedDirs = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $changedFiles) {
        $normalized = $file -replace '\\', '/'
        $dir = Split-Path $normalized -Parent
        if ([string]::IsNullOrEmpty($dir)) { $dir = '.' }
        [void]$changedDirs.Add(($dir -replace '\\', '/'))
    }

    return @($Project | Where-Object { $changedDirs.Contains($_) })
}

function New-UvLockReport {
    <#
    .SYNOPSIS
        Writes the JSON results and markdown summary for a uv lock consistency run.
    .OUTPUTS
        [hashtable] With JsonPath, MarkdownPath, and DriftCount members.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [array]$Results,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$OutputPath
    )

    $outputDir = Split-Path $OutputPath -Parent
    if ($outputDir -and -not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    }
    $markdownPath = Join-Path $outputDir 'uv-lock-consistency-summary.md'

    $driftResults = @($Results | Where-Object { -not $_.Passed })
    $totalProjects = @($Results).Count
    $allPassed = ($driftResults.Count -eq 0)

    $payload = @{
        timestamp      = (Get-Date -Format 'o')
        check_passed   = $allPassed
        projects_count = $totalProjects
        drift_count    = $driftResults.Count
        results        = @($Results)
        summary        = @{
            overall_passed = $allPassed
        }
    }
    $payload | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding utf8

    $status = if ($allPassed) { '✅ Passed' } else { "❌ Failed ($($driftResults.Count) drifted)" }
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('### uv Lock Consistency Results')
    $lines.Add('')
    $lines.Add("**Status:** $status")
    $lines.Add('')
    $lines.Add('| Metric | Count |')
    $lines.Add('|--------|-------|')
    $lines.Add("| Projects Checked | $totalProjects |")
    $lines.Add("| Drifted | $($driftResults.Count) |")

    if ($driftResults.Count -gt 0) {
        $lines.Add('')
        $lines.Add('## 🚨 Drifted Projects')
        $lines.Add('')
        $lines.Add('Run `uv lock` in each directory below to resync the lock with its manifest.')
        $lines.Add('')
        $lines.Add('| Project | Detail |')
        $lines.Add('|---------|--------|')
        foreach ($drift in ($driftResults | Sort-Object -Property Project)) {
            $detail = ($drift.Detail -replace '\r?\n', ' ').Trim()
            if ([string]::IsNullOrEmpty($detail)) { $detail = 'lock is out of date with pyproject.toml' }
            $lines.Add("| $($drift.Project) | $detail |")
        }
    }

    $markdown = $lines -join "`n"
    $markdown | Out-File -FilePath $markdownPath -Encoding utf8 -NoNewline
    Write-CIStepSummary -Content $markdown

    return @{
        JsonPath     = $OutputPath
        MarkdownPath = $markdownPath
        DriftCount   = $driftResults.Count
    }
}

function Invoke-UvLockConsistencyCheckCore {
    <#
    .SYNOPSIS
        Runs the uv lock consistency check across the discovered or supplied projects.
    .OUTPUTS
        [int] 0 when every lock is consistent, 1 when any lock drifts or uv is unavailable.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [string]$OutputPath,
        [string[]]$Projects = @(),
        [switch]$ChangedFilesOnly,
        [string]$BaseBranch = 'origin/main'
    )

    $repoRoot = & git rev-parse --show-toplevel 2>$null
    if (-not $repoRoot) {
        $repoRoot = (Get-Item $PSScriptRoot).Parent.Parent.Parent.FullName
    }

    if (-not $OutputPath) {
        $OutputPath = Join-Path $repoRoot 'logs/uv-lock-consistency-results.json'
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'uv not found on PATH. Install from https://docs.astral.sh/uv/.'
        return 1
    }

    if ($Projects.Count -eq 0) {
        $Projects = @(Get-UvLockProject -RepoRoot $repoRoot)
    }

    if ($ChangedFilesOnly) {
        $Projects = @(Select-ChangedProject -Project $Projects -Base $BaseBranch)
        if ($Projects.Count -eq 0) {
            Write-Host 'No uv.lock or pyproject.toml files changed — skipping check'
            New-UvLockReport -Results @() -OutputPath $OutputPath | Out-Null
            return 0
        }
    }

    if ($Projects.Count -eq 0) {
        Write-Host 'No uv.lock files found to check'
        New-UvLockReport -Results @() -OutputPath $OutputPath | Out-Null
        return 0
    }

    Write-Host "Checking $($Projects.Count) uv.lock project(s)"

    $results = [System.Collections.Generic.List[pscustomobject]]::new()
    foreach ($project in $Projects) {
        $result = Test-UvLockProject -Project $project -RepoRoot $repoRoot
        $results.Add($result)

        if ($result.Passed) {
            Write-Host "  ✅ $project"
        }
        else {
            Write-Host "  ❌ $project"
            $lockPath = if ($project -eq '.') { 'uv.lock' } else { "$project/uv.lock" }
            Write-CIAnnotation -Level Error -File $lockPath `
                -Message "uv.lock is out of date with pyproject.toml. Run 'uv lock' in $project to resync."
        }
    }

    $report = New-UvLockReport -Results $results -OutputPath $OutputPath
    Write-Host "Results written to $($report.JsonPath)"

    if ($report.DriftCount -gt 0) {
        Write-Host "`n❌ $($report.DriftCount) project(s) have a drifted uv.lock"
        return 1
    }

    Write-Host "`n✅ All uv.lock files are consistent with their manifests"
    return 0
}

#endregion Helper Functions

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-UvLockConsistencyCheckCore @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-Error -ErrorAction Continue "Invoke-UvLockConsistencyCheck failed: $($_.Exception.Message)"
        Write-CIAnnotation -Level Error -Message $_.Exception.Message
        exit 1
    }
}
#endregion Main Execution
