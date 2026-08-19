# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Compares exact binary version pins against upstream releases.

.DESCRIPTION
    Reads exact installation pins from repository files, detects inconsistent copies,
    and writes JSON and GitHub Actions outputs for check-binary-freshness.yml.
    Test-BinaryFreshness.ps1 separately validates checksums and Helm chart versions.
.PARAMETER RepositoryRoot
    Repository root containing the registered pin sources.
.PARAMETER ResultsPath
    JSON output path for freshness results.
.PARAMETER GitHubOutputPath
    Optional GitHub Actions output file.
.EXAMPLE
    ./scripts/security/Test-BinaryVersionFreshness.ps1
#>

[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Split-Path $PSScriptRoot -Parent | Split-Path -Parent),
    [string]$ResultsPath = 'freshness-results.json',
    [string]$GitHubOutputPath = $env:GITHUB_OUTPUT
)

function Get-BinaryVersionToolDefinitions {
    [CmdletBinding()]
    param()

    return @(
        @{
            Name = 'uv'
            Repo = 'astral-sh/uv'
            Sources = @(
                @{ File = 'setup-dev.sh'; Pattern = 'UV_VERSION="([^"]+)"' }
                @{ File = 'setup-dev.ps1'; Pattern = '\$UvVersion = ''([^'']+)''' }
                @{ File = 'training/rl/scripts/setup_isaac_runtime.sh'; Pattern = 'UV_VERSION="([^"]+)"' }
                @{ File = 'infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh'; Pattern = 'UV_VERSION="([^"]+)"' }
                @{ File = 'shared/ci/smoke-import.sh'; Pattern = 'UV_VERSION="([^"]+)"' }
                @{ File = 'docs/contributing/prerequisites.md'; Pattern = '\| uv\s+\|\s+([0-9][^\s|]+)' }
                @{
                    File = '.github/workflows/copilot-setup-steps.yml'
                    Pattern = '(?ms)^\s*- name: Setup uv\s+.*?^\s+version:\s*''([0-9][^'']+)'''
                }
            )
        }
        @{
            Name = 'osv-scanner'
            Repo = 'google/osv-scanner'
            Sources = @(
                @{ File = 'setup-dev.sh'; Pattern = 'OSV_SCANNER_VERSION="([^"]+)"' }
                @{ File = 'setup-dev.ps1'; Pattern = '\$OsvScannerVersion = ''([^'']+)''' }
                @{
                    File = '.github/workflows/copilot-setup-steps.yml'
                    Pattern = 'OSV_SCANNER_VERSION:\s*([0-9][^\s]+)'
                }
                @{ File = 'docs/contributing/prerequisites.md'; Pattern = '\| OSV-Scanner\s+\|\s+([0-9][^\s|]+)' }
                @{ File = 'CONTRIBUTING.md'; Pattern = 'OSV-Scanner v([0-9][^\s]+)' }
            )
        }
        @{
            Name = 'terraform-docs'
            Repo = 'terraform-docs/terraform-docs'
            Sources = @(
                @{ File = 'setup-dev.sh'; Pattern = 'TERRAFORM_DOCS_VERSION="([^"]+)"' }
                @{ File = 'setup-dev.ps1'; Pattern = '\$TerraformDocsVersion = ''([^'']+)''' }
                @{
                    File = '.github/workflows/go-tests.yml'
                    Pattern = '(?ms)^\s*- name: Install terraform-docs\s+.*?^\s*\$version = ''v?([^'']+)'''
                }
                @{
                    File = '.github/workflows/terraform-docs-check.yml'
                    # Workflow input default under terraform-docs-version.
                    Pattern = '(?ms)^\s*terraform-docs-version:\s*.*?^\s*default:\s*''v?([0-9][^'']+)'''
                }
                @{
                    File = 'docs/contributing/infrastructure-style.md'
                    # Linked terraform-docs release version in the style guide.
                    Pattern = 'terraform-docs\]\([^)]+\) v([0-9][^\s.]*\.[0-9][^\s.]*\.[0-9][^\s.]*)'
                }
            )
        }
        @{
            Name = 'tflint'
            Repo = 'terraform-linters/tflint'
            Sources = @(
                @{ File = '.devcontainer/devcontainer.json'; Pattern = 'TFLINT_VERSION=v?([0-9][^\s&"]+)' }
                @{ File = '.github/workflows/terraform-lint.yml'; Pattern = 'tflint_version: v?([0-9][^\s]+)' }
            )
        }
        @{
            Name = 'actionlint'
            Repo = 'rhysd/actionlint'
            Sources = @(
                @{
                    File = 'scripts/setup/install-actionlint.sh'
                    Pattern = 'ACTIONLINT_VERSION="\$\{ACTIONLINT_VERSION:-([0-9][^}]+)\}"'
                }
                @{
                    File = '.github/workflows/yaml-lint.yml'
                    Pattern = '(?ms)^\s*- name: Install actionlint\s+.*?^\s*\$version = ''([0-9][^'']+)'''
                }
                @{ File = 'scripts/security/tool-checksums.json'; JsonTool = 'actionlint' }
            )
        }
        @{
            Name = 'golangci-lint'
            Repo = 'golangci/golangci-lint'
            Sources = @(
                @{ File = '.devcontainer/devcontainer.json'; Pattern = 'GOLANGCI_LINT_VERSION=([0-9][^\s&"]+)' }
                @{ File = 'scripts/linting/Invoke-GoLint.ps1'; Pattern = '\$lintInstallVersion = ''([^'']+)''' }
            )
        }
        @{
            Name = 'gitleaks'
            Repo = 'gitleaks/gitleaks'
            Sources = @(
                @{ File = 'scripts/security/tool-checksums.json'; JsonTool = 'gitleaks' }
                @{ File = '.github/workflows/gitleaks-scan.yml'; Pattern = 'GITLEAKS_VERSION="([^"]+)"' }
            )
        }
        @{
            Name = 'oras'
            Repo = 'oras-project/oras'
            Sources = @(
                @{ File = 'scripts/security/tool-checksums.json'; JsonTool = 'oras' }
                @{ File = 'training/vla/scripts/groot/osmo-train-entry.sh'; Pattern = 'ORAS_VERSION="([^"]+)"' }
            )
        }
        @{
            Name = 'gh-aw'
            Repo = 'github/gh-aw'
            Sources = @(
                @{
                    File = '.github/workflows/copilot-setup-steps.yml'
                    Pattern = 'gh extension install github/gh-aw --pin v?([0-9][^\s]+)'
                }
            )
        }
        @{
            Name = 'osmo'
            Repo = 'NVIDIA/OSMO'
            Sources = @(
                @{ File = '.devcontainer/devcontainer.json'; Pattern = 'OSMO_VERSION=([0-9][^\s&"]+)' }
            )
        }
    )
}

function Get-PinnedBinaryVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [Parameter(Mandatory)][string]$ToolName,
        [Parameter(Mandatory)][hashtable]$Source
    )

    $path = Join-Path $RepositoryRoot $Source.File
    if (-not (Test-Path $path)) {
        throw "File not found for ${ToolName}: $($Source.File)"
    }

    $content = Get-Content $path -Raw
    if ($Source.JsonTool) {
        $manifest = $content | ConvertFrom-Json
        $entry = @($manifest.tools).Where({ $_.name -eq $Source.JsonTool }, 'First')
        if (-not $entry -or -not $entry.version) {
            throw "Could not extract version for $ToolName from $($Source.File)"
        }
        return [string]$entry.version
    }

    $regexMatches = [regex]::Matches($content, $Source.Pattern)
    if ($regexMatches.Count -eq 0) {
        throw "Could not extract version for $ToolName from $($Source.File)"
    }
    if ($regexMatches.Count -gt 1) {
        throw "Version pattern for $ToolName matched multiple values in $($Source.File)"
    }
    return $regexMatches[0].Groups[1].Value
}

function Invoke-BinaryVersionFreshnessCheck {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$RepositoryRoot,
        [hashtable[]]$Tools = (Get-BinaryVersionToolDefinitions),
        [scriptblock]$LatestReleaseResolver = {
            param([string]$Repository)
            $response = gh api "repos/$Repository/releases/latest" --jq '.tag_name' 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw "GitHub release lookup failed for ${Repository}: $response"
            }
            return $response
        }
    )

    $results = foreach ($tool in $Tools) {
        $pinnedVersions = @()
        $pinnedVersion = $null
        $latestVersion = $null
        $latestTag = $null
        $inconsistent = $false
        try {
            if (-not $tool.Sources -or $tool.Sources.Count -eq 0) {
                throw "No version sources configured for $($tool.Name)"
            }

            $pinnedVersions = @(foreach ($source in $tool.Sources) {
                [ordered]@{
                    File = $source.File
                    Version = Get-PinnedBinaryVersion `
                        -RepositoryRoot $RepositoryRoot `
                        -ToolName $tool.Name `
                        -Source $source
                }
            })

            $pinnedVersion = $pinnedVersions[0].Version
            $inconsistent = @($pinnedVersions | Where-Object { $_.Version -ne $pinnedVersion }).Count -gt 0
            $latestTag = & $LatestReleaseResolver $tool.Repo
            if (-not $latestTag) {
                throw "Could not fetch latest release for $($tool.Repo) - freshness check cannot produce reliable results"
            }

            $latestTag = ([string]$latestTag).Trim()
            if ($latestTag -notmatch '^v?([0-9]+(?:\.[0-9]+){1,3}(?:[-+][0-9A-Za-z.-]+)?)$') {
                throw "Invalid latest release tag for $($tool.Repo): $latestTag"
            }
            $latestVersion = $Matches[1]
            $isStale = $pinnedVersion -ne $latestVersion
            [ordered]@{
                Name = $tool.Name
                Repo = $tool.Repo
                PinnedVersion = $pinnedVersion
                PinnedVersions = $pinnedVersions
                LatestVersion = $latestVersion
                LatestTag = $latestTag
                IsStale = $isStale
                Inconsistent = $inconsistent
                Error = $null
                RequiresAttention = $isStale -or $inconsistent
                SourceFiles = @($tool.Sources.File)
            }
        }
        catch {
            [ordered]@{
                Name = $tool.Name
                Repo = $tool.Repo
                PinnedVersion = $pinnedVersion
                PinnedVersions = $pinnedVersions
                LatestVersion = $latestVersion
                LatestTag = $latestTag
                IsStale = $false
                Inconsistent = $inconsistent
                Error = $_.Exception.Message
                RequiresAttention = $true
                SourceFiles = @($tool.Sources.File)
            }
        }
    }

    return @($results)
}

function Write-BinaryVersionFreshnessResult {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Results,
        [Parameter(Mandatory)][string]$ResultsPath,
        [string]$GitHubOutputPath
    )

    $staleCount = @($Results | Where-Object { $_.IsStale }).Count
    $attentionCount = @($Results | Where-Object { $_.RequiresAttention }).Count
    $Results | ConvertTo-Json -Depth 5 | Set-Content $ResultsPath

    if ($GitHubOutputPath) {
        "stale-count=$staleCount" | Add-Content $GitHubOutputPath
        "attention-count=$attentionCount" | Add-Content $GitHubOutputPath
        "total-count=$($Results.Count)" | Add-Content $GitHubOutputPath
    }

    return @{
        StaleCount = $staleCount
        AttentionCount = $attentionCount
        TotalCount = $Results.Count
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $results = Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $RepositoryRoot
    foreach ($result in $results) {
        $status = if ($result.Error) { 'ERROR' } elseif ($result.RequiresAttention) { 'WARNING' } else { 'CURRENT' }
        $extra = if ($result.Inconsistent) { ' [INCONSISTENT]' } else { '' }
        $detail = if ($result.Error) { $result.Error } else { "pinned=$($result.PinnedVersion) latest=$($result.LatestVersion)$extra" }
        Write-Output "$status $($result.Name): $detail"
    }

    $summary = Write-BinaryVersionFreshnessResult `
        -Results $results `
        -ResultsPath $ResultsPath `
        -GitHubOutputPath $GitHubOutputPath
    Write-Output "`nStale: $($summary.StaleCount) / $($summary.TotalCount)"
    Write-Output "Attention required: $($summary.AttentionCount) / $($summary.TotalCount)"
}
