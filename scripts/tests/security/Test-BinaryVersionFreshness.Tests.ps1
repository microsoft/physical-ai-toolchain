# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../security/Test-BinaryVersionFreshness.ps1
    $script:RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '../../..')
}

Describe 'Binary version freshness' -Tag 'Unit' {
    It 'extracts one consistent version from every configured source' {
        $latestVersions = @{
            'astral-sh/uv' = 'v0.12.5'
            'terraform-docs/terraform-docs' = 'v0.24.0'
            'terraform-linters/tflint' = 'v0.64.0'
            'rhysd/actionlint' = 'v1.7.12'
            'golangci/golangci-lint' = 'v2.12.2'
        }

        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $script:RepoRoot -LatestReleaseResolver {
            param([string]$Repository)
            $latestVersions[$Repository]
        })

        $results.Count | Should -Be 5
        @($results | Where-Object { $_.Inconsistent }).Count | Should -Be 0
        @($results | Where-Object { $_.IsStale }).Count | Should -Be 0
    }

    It 'requires attention when source versions are inconsistent' {
        Set-Content (Join-Path $TestDrive 'one.txt') 'VERSION=1.0.0'
        Set-Content (Join-Path $TestDrive 'two.txt') 'VERSION=1.0.1'
        $tools = @(
            @{
                Name = 'example'
                Repo = 'example/tool'
                Sources = @(
                    @{ File = 'one.txt'; Pattern = 'VERSION=([0-9.]+)' }
                    @{ File = 'two.txt'; Pattern = 'VERSION=([0-9.]+)' }
                )
            }
        )

        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools $tools `
            -LatestReleaseResolver { 'v1.0.0' })

        $results[0].IsStale | Should -BeFalse
        $results[0].Inconsistent | Should -BeTrue
        $results[0].RequiresAttention | Should -BeTrue
    }

    It 'writes attention count independently from stale count' {
        $results = @(
            [pscustomobject]@{ IsStale = $false; RequiresAttention = $true }
            [pscustomobject]@{ IsStale = $false; RequiresAttention = $false }
        )
        $resultsPath = Join-Path $TestDrive 'results.json'
        $outputPath = Join-Path $TestDrive 'github-output.txt'

        $summary = Write-BinaryVersionFreshnessResult -Results $results -ResultsPath $resultsPath `
            -GitHubOutputPath $outputPath

        $summary.StaleCount | Should -Be 0
        $summary.AttentionCount | Should -Be 1
        Get-Content $outputPath -Raw | Should -Match 'attention-count=1'
    }

    It 'throws when a configured source is missing' {
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'missing.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        {
            Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
                -LatestReleaseResolver { 'v1.0.0' }
        } | Should -Throw '*File not found for example*'
    }

    It 'throws when a configured pattern does not match' {
        Set-Content (Join-Path $TestDrive 'version.txt') 'NO_VERSION=true'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        {
            Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
                -LatestReleaseResolver { 'v1.0.0' }
        } | Should -Throw '*Could not extract version for example*'
    }
}
