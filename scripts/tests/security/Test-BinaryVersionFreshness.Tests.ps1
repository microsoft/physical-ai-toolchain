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
        $tools = @(Get-BinaryVersionToolDefinitions)
        $latestVersions = @{}
        foreach ($tool in $tools) {
            $latestVersions[$tool.Repo] = 'v' + (Get-PinnedBinaryVersion `
                -RepositoryRoot $script:RepoRoot `
                -ToolName $tool.Name `
                -Source $tool.Sources[0])
        }

        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $script:RepoRoot -Tools $tools -LatestReleaseResolver {
            param([string]$Repository)
            $latestVersions[$Repository]
        })

        $results.Count | Should -Be $tools.Count
        @($results | Where-Object { $_.Inconsistent }).Count | Should -Be 0
        @($results | Where-Object { $_.IsStale }).Count | Should -Be 0
    }

    It 'tracks exact TFLint installations without compatibility floors' {
        $tool = Get-BinaryVersionToolDefinitions | Where-Object { $_.Name -eq 'tflint' }
        $sourceFiles = @($tool.Sources.File)

        $sourceFiles | Should -HaveCount 2
        $sourceFiles | Should -Contain '.devcontainer/devcontainer.json'
        $sourceFiles | Should -Contain '.github/workflows/terraform-lint.yml'
        $sourceFiles | Should -Not -Contain '.tflint.hcl'
        $sourceFiles | Should -Not -Contain 'docs/contributing/prerequisites.md'
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

    It 'handles a single source and marks an older pin as stale' {
        Set-Content (Join-Path $TestDrive 'single-version.txt') 'VERSION=1.0.0'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'single-version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.1.0' })

        $results[0].PinnedVersion | Should -Be '1.0.0'
        $results[0].LatestVersion | Should -Be '1.1.0'
        $results[0].IsStale | Should -BeTrue
        $results[0].RequiresAttention | Should -BeTrue
    }

    It 'serializes every field consumed by the workflow' {
        Set-Content (Join-Path $TestDrive 'serialized-version.txt') 'VERSION=1.0.0'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'serialized-version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }
        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.1.0' })
        $resultsPath = Join-Path $TestDrive 'serialized-results.json'

        Write-BinaryVersionFreshnessResult -Results $results -ResultsPath $resultsPath | Out-Null
        $serialized = @(Get-Content $resultsPath -Raw | ConvertFrom-Json)[0]

        foreach ($property in @(
                'Name', 'Repo', 'PinnedVersion', 'LatestVersion', 'LatestTag',
                'IsStale', 'Inconsistent', 'RequiresAttention', 'SourceFiles'
            )) {
            $serialized.PSObject.Properties.Name | Should -Contain $property
        }
    }

    It 'throws when the latest release cannot be resolved' {
        Set-Content (Join-Path $TestDrive 'unresolved-version.txt') 'VERSION=1.0.0'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'unresolved-version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        {
            Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
                -LatestReleaseResolver { $null }
        } | Should -Throw '*Could not fetch latest release for example/tool*'
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

    It 'throws when a JSON manifest lacks the configured tool' {
        Set-Content (Join-Path $TestDrive 'checksums.json') '{"tools":[{"name":"other","version":"1.0.0"}]}'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'checksums.json'; JsonTool = 'example' })
        }

        {
            Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
                -LatestReleaseResolver { 'v1.0.0' }
        } | Should -Throw '*Could not extract version for example*'
    }

    It 'throws when a tool has no version sources' {
        $tool = @{ Name = 'example'; Repo = 'example/tool'; Sources = @() }

        {
            Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
                -LatestReleaseResolver { 'v1.0.0' }
        } | Should -Throw '*No version sources configured for example*'
    }
}
