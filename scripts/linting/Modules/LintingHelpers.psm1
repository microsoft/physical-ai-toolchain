# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# LintingHelpers.psm1
#
# Purpose: Shared helper functions for linting scripts and workflows
# Author: HVE Core Team

Import-Module (Join-Path $PSScriptRoot "../../lib/Modules/CIHelpers.psm1") -Force

function Get-ChangedFilesFromGit {
    <#
    .SYNOPSIS
    Gets existing changed files relative to a verified Git merge base.

    .DESCRIPTION
    Fails when the comparison cannot be established instead of reporting no changes.

    .PARAMETER BaseBranch
    The base branch to compare against (default: origin/main).

    .PARAMETER FileExtensions
    Array of file extensions to filter (e.g., @('*.ps1', '*.md')).

    .OUTPUTS
    Array of changed file paths.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string]$BaseBranch = "origin/main",

        [Parameter(Mandatory = $false)]
        [string[]]$FileExtensions = @('*')
    )

    $changedFiles = @()

    try {
        $PSNativeCommandUseErrorActionPreference = $false
        $mergeBase = git merge-base HEAD $BaseBranch 2>$null

        if ($LASTEXITCODE -eq 0 -and $mergeBase) {
            Write-Verbose "Using merge-base: $mergeBase"
            $changedFiles = git diff --name-only --diff-filter=ACMR $mergeBase HEAD 2>$null
        }
        else {
            throw "Unable to determine Git merge base against '$BaseBranch'."
        }

        if ($LASTEXITCODE -ne 0) {
            throw "Unable to compare Git changes against '$BaseBranch'."
        }
        if (-not $changedFiles) {
            return @()
        }

        # Filter by extensions and verify files exist
        $filteredFiles = $changedFiles | Where-Object {
            if ([string]::IsNullOrEmpty($_)) { return $false }

            # Check if file matches any of the allowed extensions
            $currentFile = $_
            $matchesExtension = $false
            foreach ($pattern in $FileExtensions) {
                if ($currentFile -like $pattern) {
                    $matchesExtension = $true
                    break
                }
            }

            $matchesExtension -and (Test-Path $currentFile -PathType Leaf)
        }

        Write-Verbose "Found $(@($filteredFiles).Count) changed files matching extensions: $($FileExtensions -join ', ')"
        return @($filteredFiles)
    }
    catch {
        throw
    }
}

function Get-FilesRecursive {
    <#
    .SYNOPSIS
    Gets files recursively with gitignore filtering.

    .DESCRIPTION
    Recursively finds files by extension, respecting .gitignore patterns.

    .PARAMETER Path
    Root path to search from.

    .PARAMETER Include
    File patterns to include (e.g., @('*.ps1', '*.psm1')).

    .PARAMETER GitIgnorePath
    Path to .gitignore file for exclusion patterns.

    .OUTPUTS
    Array of FileInfo objects.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string[]]$Include,

        [Parameter(Mandatory = $false)]
        [string]$GitIgnorePath
    )

    $files = @(Get-ChildItem -Path $Path -Recurse -Include $Include -File -ErrorAction SilentlyContinue)

    # Apply gitignore filtering if provided
    if ($GitIgnorePath -and (Test-Path $GitIgnorePath)) {
        $gitignorePatterns = Get-GitIgnorePatterns -GitIgnorePath $GitIgnorePath

        $files = @($files | Where-Object {
            $file = $_
            $excluded = $false

            foreach ($pattern in $gitignorePatterns) {
                if ($file.FullName -like $pattern) {
                    $excluded = $true
                    break
                }
            }

            -not $excluded
        })
    }

    return $files
}

function Get-GitIgnorePatterns {
    <#
    .SYNOPSIS
    Parses .gitignore into PowerShell wildcard patterns.

    .PARAMETER GitIgnorePath
    Path to .gitignore file.

    .OUTPUTS
    Array of wildcard patterns using platform-appropriate separators.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitIgnorePath
    )

    if (-not (Test-Path $GitIgnorePath)) {
        return @()
    }

    $sep = [System.IO.Path]::DirectorySeparatorChar

    try {
        $lines = Get-Content $GitIgnorePath -ErrorAction Stop
    }
    catch {
        Write-Warning "Unable to read gitignore file '$GitIgnorePath': $($_.Exception.Message)"
        return @()
    }

    $patterns = $lines | Where-Object {
        $_ -and -not $_.StartsWith('#') -and $_.Trim() -ne ''
    } | ForEach-Object {
        $pattern = $_.Trim()

        # Normalize to platform separator
        $normalizedPattern = $pattern.Replace('/', $sep).Replace('\', $sep)

        if ($pattern.EndsWith('/')) {
            "*$sep$($normalizedPattern.TrimEnd($sep))$sep*"
        }
        elseif ($pattern.Contains('/') -or $pattern.Contains('\')) {
            "*$sep$normalizedPattern*"
        }
        elseif ($pattern -match '[\*\?\.]') {
            "*$sep$normalizedPattern"
        }
        else {
            "*$sep$normalizedPattern$sep*"
        }
    }

    return @($patterns)
}

# Export local functions only - CIHelpers functions are used via direct import
Export-ModuleMember -Function @(
    'Get-ChangedFilesFromGit',
    'Get-FilesRecursive',
    'Get-GitIgnorePatterns'
)
