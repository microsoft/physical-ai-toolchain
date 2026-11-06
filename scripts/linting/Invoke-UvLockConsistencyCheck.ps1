#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Verifies committed uv.lock files stay consistent with their pyproject.toml manifests.
.DESCRIPTION
    Discovers tracked pyproject.toml projects (excluding .venv/, external/, node_modules/,
    .git/, .copilot-tracking/, docs/docusaurus/), resolves explicit workspace lock owners, and runs
    `uv lock --check`. The script writes results to logs/. Missing or stale locks are
    surfaced as CI annotations and a GitHub step summary.
.PARAMETER OutputPath
    Path for the JSON results file. Defaults to logs/uv-lock-consistency-results.json.
.PARAMETER Projects
    Explicit list of repository-relative project directories to check. When omitted, every
    directory containing a tracked pyproject.toml is discovered automatically.
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

function Get-UvProjectOwner {
    <#
    .SYNOPSIS
        Resolves tracked manifests to their explicit workspace or standalone lock owner.
    .OUTPUTS
        [hashtable] Repository-relative manifest directories mapped to lock owners.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$RepoRoot
    )

    $excludeDirs = @('.venv', 'external', 'node_modules', '.git', '.copilot-tracking', 'docs/docusaurus')

    $PSNativeCommandUseErrorActionPreference = $false
    $manifestFiles = @(& git -C $RepoRoot ls-files -- 'pyproject.toml' '**/pyproject.toml')
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to discover tracked project manifests.'
    }
    $manifestFiles = @($manifestFiles | Where-Object {
            $relativePath = $_ -replace '\\', '/'
            $excluded = $false
            foreach ($dir in $excludeDirs) {
                if ($relativePath -like "$dir/*" -or $relativePath -like "*/$dir/*") {
                    $excluded = $true
                    break
                }
            }
            -not $excluded
        })

    if ($manifestFiles.Count -eq 0) {
        return @{}
    }

    $parser = @'
import json
import sys
import tomllib
from pathlib import Path, PurePosixPath

root = Path(sys.argv[1])
manifests = {}
for filename in json.load(sys.stdin):
    directory = PurePosixPath(filename).parent.as_posix()
    with (root / filename).open("rb") as source:
        manifests[directory] = tomllib.load(source)
owners = {directory: directory for directory in manifests}
claimed = set()
for directory, manifest in manifests.items():
    workspace = manifest.get("tool", {}).get("uv", {}).get("workspace")
    if workspace is None:
        continue
    if not isinstance(workspace, dict) or not isinstance(workspace.get("members"), list):
        raise ValueError(f"Invalid workspace membership in {directory}")
    for member in workspace["members"]:
        if not isinstance(member, str) or not member or any(char in member for char in "*?[]"):
            raise ValueError(f"Workspace members must be explicit relative directories: {directory}")
        relative = PurePosixPath(member)
        if relative.is_absolute() or ".." in relative.parts or member == ".":
            raise ValueError(f"Invalid workspace member: {member}")
        target = (PurePosixPath(directory) / relative).as_posix()
        if target not in manifests:
            raise ValueError(f"Missing tracked workspace member manifest: {target}")
        if target in claimed or manifests[target].get("tool", {}).get("uv", {}).get("workspace") is not None:
            raise ValueError(f"Conflicting workspace member ownership: {target}")
        if (root / target / "uv.lock").exists():
            raise ValueError(f"Workspace member has an orphan lock: {target}/uv.lock")
        claimed.add(target)
        owners[target] = directory
print(json.dumps(owners))
'@
    $uvExecutable = (Get-Command uv -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $python = & $uvExecutable python find --offline
    if ($LASTEXITCODE -ne 0) {
        throw 'Workspace discovery requires an available Python interpreter with tomllib.'
    }
    $ownerJson = $manifestFiles | ConvertTo-Json -AsArray -Compress | & $python -I -c $parser $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to resolve explicit workspace member and lock ownership.'
    }
    return ($ownerJson | ConvertFrom-Json -AsHashtable)
}

function Get-UvProject {
    <#
    .SYNOPSIS
        Discovers each standalone or explicit workspace lock owner once.
    .OUTPUTS
        [string[]] Sorted, unique repository-relative lock-owner directories.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$RepoRoot
    )

    $owners = Get-UvProjectOwner -RepoRoot $RepoRoot
    return @($owners.Values | Sort-Object -Unique)
}

function Get-CommittedUvLockProject {
    <#
    .SYNOPSIS
        Discovers repository-relative directories containing committed uv.lock files.
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

    $lockFiles = @(& git -C $RepoRoot ls-files -- 'uv.lock' ':(glob)**/uv.lock')
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to list committed uv.lock files'
    }

    $excludeDirs = @('.venv', 'external', 'node_modules', '.git', '.copilot-tracking', 'docs/docusaurus')
    $projects = foreach ($lockFile in $lockFiles) {
        $normalized = $lockFile -replace '\\', '/'
        if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $normalized) -PathType Leaf)) {
            continue
        }
        if ($excludeDirs | Where-Object { $normalized -like "$_/*" -or $normalized -like "*/$_/*" }) {
            continue
        }
        $parent = Split-Path $normalized -Parent
        if ([string]::IsNullOrEmpty($parent)) { '.' } else { $parent -replace '\\', '/' }
    }

    return @($projects | Sort-Object -Unique)
}

function Get-UvDependabotProject {
    <#
    .SYNOPSIS
        Reads uv project directories from a Dependabot configuration.
    .OUTPUTS
        [string[]] Sorted, unique repository-relative directory paths.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Path
    )

    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "Dependabot configuration not found: $Path"
    }

    $projects = [System.Collections.Generic.List[string]]::new()
    $ecosystem = ''
    foreach ($line in Get-Content -Path $Path) {
        if ($line -match '^\s*-\s+package-ecosystem:\s*["'']?([^"'']+)["'']?\s*$') {
            $ecosystem = $matches[1].Trim()
            continue
        }
        if ($ecosystem -eq 'uv' -and $line -match '^\s+directory:\s*["'']?([^"'']+)["'']?\s*$') {
            $directory = $matches[1].Trim().Trim('/')
            $projects.Add($(if ($directory) { $directory } else { '.' }))
            $ecosystem = ''
        }
    }

    return @($projects | Sort-Object -Unique)
}

function Get-MissingUvDependabotProject {
    <#
    .SYNOPSIS
        Returns committed uv projects without a matching Dependabot uv entry.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$LockProjects,

        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$DependabotProjects
    )

    return @($LockProjects | Where-Object { $_ -notin $DependabotProjects } | Sort-Object -Unique)
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
    $manifestPath = Join-Path $absDir 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        return [pscustomobject]@{ Project = $Project; Passed = $false; Detail = 'pyproject.toml is missing' }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $absDir 'uv.lock') -PathType Leaf)) {
        return [pscustomobject]@{ Project = $Project; Passed = $false; Detail = 'uv.lock is missing' }
    }
    $lockPath = if ($Project -eq '.') { 'uv.lock' } else { "$Project/uv.lock" }
    $PSNativeCommandUseErrorActionPreference = $false
    $trackedLock = @(& git -C $RepoRoot ls-files --error-unmatch -- $lockPath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $trackedLock.Count -ne 1) {
        return [pscustomobject]@{
            Project = $Project
            Passed  = $false
            Detail  = 'Project requires its tracked uv.lock and pyproject.toml.'
        }
    }
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
        [string]$Base = 'origin/main',

        [Parameter(Mandatory = $false)]
        [string]$RepoRoot
    )

    $changedFiles = @(Get-ChangedFilesFromGit -BaseBranch $Base -FileExtensions @('*uv.lock', '*pyproject.toml'))
    if ($changedFiles.Count -eq 0) {
        return @()
    }

    $owners = if ($RepoRoot) { Get-UvProjectOwner -RepoRoot $RepoRoot } else { @{} }
    $changedDirs = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($file in $changedFiles) {
        $normalized = $file -replace '\\', '/'
        $dir = Split-Path $normalized -Parent
        if ([string]::IsNullOrEmpty($dir)) { $dir = '.' }
        $dir = $dir -replace '\\', '/'
        $owner = if ($owners.ContainsKey($dir)) { $owners[$dir] } else { $dir }
        [void]$changedDirs.Add($owner)
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

    $status = if ($allPassed) { '✅ Passed' } else { "❌ Failed ($($driftResults.Count) failed checks)" }
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('### uv Lock Consistency Results')
    $lines.Add('')
    $lines.Add("**Status:** $status")
    $lines.Add('')
    $lines.Add('| Metric | Count |')
    $lines.Add('|--------|-------|')
    $lines.Add("| Projects Checked | $totalProjects |")
    $lines.Add("| Failed Checks | $($driftResults.Count) |")

    if ($driftResults.Count -gt 0) {
        $lines.Add('')
        $lines.Add('## 🚨 Failed Projects')
        $lines.Add('')
        $lines.Add('Review each failure below. Regenerate a lock only when its manifest has changed.')
        $lines.Add('')
        $lines.Add('| Project | Detail |')
        $lines.Add('|---------|--------|')
        foreach ($drift in ($driftResults | Sort-Object -Property Project)) {
            $detail = ($drift.Detail -replace '\r?\n', ' ').Trim()
            if ([string]::IsNullOrEmpty($detail)) { $detail = 'uv lock check failed without diagnostic output' }
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

    $dependabotPath = Join-Path $repoRoot '.github/dependabot.yml'
    $lockProjects = @(Get-CommittedUvLockProject -RepoRoot $repoRoot)
    $dependabotProjects = @(Get-UvDependabotProject -Path $dependabotPath)
    $missingDependabotProjects = @(
        Get-MissingUvDependabotProject -LockProjects $lockProjects -DependabotProjects $dependabotProjects
    )
    if ($missingDependabotProjects.Count -gt 0) {
        foreach ($project in $missingDependabotProjects) {
            Write-CIAnnotation -Level Error -File '.github/dependabot.yml' `
                -Message "Committed uv project '$project' requires a Dependabot uv entry."
        }
        return 1
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-CIAnnotation -Level Error -Message 'uv not found on PATH. Install from https://docs.astral.sh/uv/.'
        return 1
    }

    if ($Projects.Count -eq 0) {
        $Projects = @(Get-UvProject -RepoRoot $repoRoot)
        if ($Projects.Count -gt 0) {
            $Projects = @($Projects + $lockProjects | Sort-Object -Unique)
        }
    }

    if ($ChangedFilesOnly) {
        $Projects = @(Select-ChangedProject -Project $Projects -Base $BaseBranch -RepoRoot $repoRoot)
        if ($Projects.Count -eq 0) {
            Write-Host 'No uv.lock or pyproject.toml files changed — skipping check'
            New-UvLockReport -Results @() -OutputPath $OutputPath | Out-Null
            return 0
        }
    }

    if ($Projects.Count -eq 0) {
        Write-CIAnnotation -Level Error -Message 'Full lock validation has no tracked project manifests.'
        return 1
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
                -Message "uv.lock check failed in $project`: $($result.Detail)"
        }
    }

    $report = New-UvLockReport -Results $results -OutputPath $OutputPath
    Write-Host "Results written to $($report.JsonPath)"

    if ($report.DriftCount -gt 0) {
        Write-Host "`n❌ $($report.DriftCount) project(s) failed the uv lock check"
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
