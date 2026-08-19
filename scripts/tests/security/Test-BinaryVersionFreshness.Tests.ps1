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
        @($results | Where-Object { $_.Error }).Count | Should -Be 0
    }

    It 'normalizes a v-prefixed upstream tag before comparison' {
        Set-Content (Join-Path $TestDrive 'version.txt') 'VERSION=1.2.3'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.2.3' })[0]

        $result.LatestTag | Should -Be 'v1.2.3'
        $result.LatestVersion | Should -Be '1.2.3'
        $result.IsStale | Should -BeFalse
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
        $output = Get-Content $outputPath -Raw
        $output | Should -Match 'stale-count=0'
        $output | Should -Match 'attention-count=1'
        $output | Should -Match 'total-count=2'
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
                'PinnedVersions', 'IsStale', 'Inconsistent', 'Error', 'RequiresAttention', 'SourceFiles'
            )) {
            $serialized.PSObject.Properties.Name | Should -Contain $property
        }
    }

    It 'records an error when the latest release cannot be resolved' {
        Set-Content (Join-Path $TestDrive 'unresolved-version.txt') 'VERSION=1.0.0'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'unresolved-version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { $null })[0]

        $result.Error | Should -BeLike '*Could not fetch latest release for example/tool*'
        $result.RequiresAttention | Should -BeTrue
    }

    It 'records an error when a configured source is missing and continues checking' {
        Set-Content (Join-Path $TestDrive 'valid.txt') 'VERSION=1.0.0'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'missing.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }
        $validTool = @{
            Name = 'valid'
            Repo = 'valid/tool'
            Sources = @(@{ File = 'valid.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $results = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool, $validTool) `
            -LatestReleaseResolver { 'v1.0.0' })

        $results[0].Error | Should -BeLike '*File not found for example*'
        $results[0].RequiresAttention | Should -BeTrue
        $results[1].Error | Should -BeNullOrEmpty
        $results[1].RequiresAttention | Should -BeFalse
    }

    It 'records an error when a configured pattern does not match' {
        Set-Content (Join-Path $TestDrive 'version.txt') 'NO_VERSION=true'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'version.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.0.0' })[0]

        $result.Error | Should -BeLike '*Could not extract version for example*'
    }

    It 'records an error when a JSON manifest lacks the configured tool' {
        Set-Content (Join-Path $TestDrive 'checksums.json') '{"tools":[{"name":"other","version":"1.0.0"}]}'
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'checksums.json'; JsonTool = 'example' })
        }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.0.0' })[0]

        $result.Error | Should -BeLike '*Could not extract version for example*'
    }

    It 'records an error when a tool has no version sources' {
        $tool = @{ Name = 'example'; Repo = 'example/tool'; Sources = @() }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.0.0' })[0]

        $result.Error | Should -BeLike '*No version sources configured for example*'
    }

    It 'records an error when a pattern matches multiple values' {
        Set-Content (Join-Path $TestDrive 'versions.txt') "VERSION=1.0.0`nVERSION=1.0.1"
        $tool = @{
            Name = 'example'
            Repo = 'example/tool'
            Sources = @(@{ File = 'versions.txt'; Pattern = 'VERSION=([0-9.]+)' })
        }

        $result = @(Invoke-BinaryVersionFreshnessCheck -RepositoryRoot $TestDrive -Tools @($tool) `
            -LatestReleaseResolver { 'v1.0.0' })[0]

        $result.Error | Should -BeLike '*matched multiple values*'
    }
}
