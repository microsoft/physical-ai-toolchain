# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignments', '')]
param()

BeforeDiscovery {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $updateScript = Get-Content (Join-Path $repoRoot 'scripts/update-image-digests.sh')
    $inPinSpecs = $false
    $pinCases = @(
        foreach ($line in $updateScript) {
            if ($line.Trim() -eq 'azureml_pin_specs=(') {
                $inPinSpecs = $true
                continue
            }
            if ($inPinSpecs -and $line.Trim() -eq ')') {
                break
            }
            if ($inPinSpecs -and $line -match "^\s*'(?<Variable>[^|]+)\|(?<Environment>[^|]+)\|(?<Path>[^']+)'\s*$") {
                @{
                    Variable    = $Matches['Variable']
                    Environment = $Matches['Environment']
                    Path        = $Matches['Path']
                }
            }
        }
    )
}

BeforeAll {
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $updateScript = Get-Content (Join-Path $script:RepoRoot 'scripts/update-image-digests.sh')
    $inPinSpecs = $false
    $script:PinCases = @(
        foreach ($line in $updateScript) {
            if ($line.Trim() -eq 'azureml_pin_specs=(') {
                $inPinSpecs = $true
                continue
            }
            if ($inPinSpecs -and $line.Trim() -eq ')') {
                break
            }
            if ($inPinSpecs -and $line -match "^\s*'(?<Variable>[^|]+)\|(?<Environment>[^|]+)\|(?<Path>[^']+)'\s*$") {
                @{
                    Variable    = $Matches['Variable']
                    Environment = $Matches['Environment']
                    Path        = $Matches['Path']
                }
            }
        }
    )

    function Get-CheckedInImageDefault {
        param([string]$Variable)

        $commonPath = Join-Path $script:RepoRoot 'scripts/lib/common.sh'
        $line = Get-Content $commonPath | Where-Object { $_ -match "^$([regex]::Escape($Variable))=" }
        $line.Count | Should -Be 1
        $pattern = '^' + [regex]::Escape($Variable) + '="\$\{' +
            [regex]::Escape($Variable) + ':-(?<Image>[^}]+)\}"$'
        $match = [regex]::Match([string]$line, $pattern)
        $match.Success | Should -BeTrue
        return $match.Groups['Image'].Value
    }

    function Get-DerivedEnvironmentVersion {
        param([string]$Image)

        $commonPath = Join-Path $script:RepoRoot 'scripts/lib/common.sh'
        $version = & bash -c 'source "$1"; derive_azureml_environment_version_from_image "$2"' _ $commonPath $Image
        $LASTEXITCODE | Should -Be 0
        return $version
    }

    function New-UpdateScriptSandbox {
        $sandbox = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        & git clone --quiet --shared $script:RepoRoot $sandbox
        $LASTEXITCODE | Should -Be 0
        Copy-Item (Join-Path $script:RepoRoot 'scripts/update-image-digests.sh') `
            (Join-Path $sandbox 'scripts/update-image-digests.sh') -Force
        return $sandbox
    }

    function New-FakeCurl {
        $fakeBin = Join-Path $TestDrive ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $fakeBin | Out-Null
        $fakeCurl = Join-Path $fakeBin 'curl'
        @'
#!/usr/bin/env bash
if [[ "$*" == *"auth.docker.io"* || "$*" == *"proxy_auth"* ]]; then
  printf '{"token":"test"}\n'
else
  case "$*" in
    *2.3.2*)  digest="$(printf 'a%.0s' {1..64})" ;;
    *2.11.0*) digest="$(printf 'b%.0s' {1..64})" ;;
    *2.4.1*)  digest="$(printf 'c%.0s' {1..64})" ;;
    *)        digest="$(printf 'd%.0s' {1..64})" ;;
  esac
  printf 'Docker-Content-Digest: sha256:%s\r\n' "$digest"
fi
'@ | Set-Content -Path $fakeCurl
        & chmod +x $fakeCurl
        return $fakeBin
    }

    function Invoke-UpdateScript {
        param(
            [string]$Sandbox,
            [string]$FakeBin,
            [string[]]$Arguments
        )

        $originalPath = $env:PATH
        try {
            $env:PATH = "$FakeBin$([IO.Path]::PathSeparator)$originalPath"
            Push-Location $Sandbox
            $output = & bash 'scripts/update-image-digests.sh' @Arguments 2>&1
            return @{
                ExitCode = $LASTEXITCODE
                Output   = $output -join "`n"
            }
        }
        finally {
            Pop-Location
            $env:PATH = $originalPath
        }
    }
}

Describe 'AzureML environment pins' -Tag 'Unit' {
    It 'discovers every AzureML workflow pin' {
        $workflowPaths = @(
            & git -C $script:RepoRoot grep -l '^environment: azureml:' -- ':(glob)**/workflows/azureml/*.yaml'
        )
        $LASTEXITCODE | Should -Be 0

        @($script:PinCases.Path | Sort-Object) | Should -Be @($workflowPaths | Sort-Object)
    }

    It 'matches <Variable> in <Path>' -ForEach $pinCases {
        $image = Get-CheckedInImageDefault -Variable $Variable
        $expected = "environment: azureml:${Environment}:$(Get-DerivedEnvironmentVersion -Image $image)"
        $workflowPath = Join-Path $script:RepoRoot $Path
        $environmentLines = @(Get-Content $workflowPath | Where-Object { $_ -match '^environment:' })

        $environmentLines.Count | Should -Be 1
        $environmentLines[0] | Should -BeExactly $expected
    }

    It 'uses the production derivation function for digest-pinned images' {
        $digest = 'A' * 64
        $image = "example.com/repo:1.2.3@sha256:$digest"

        Get-DerivedEnvironmentVersion -Image $image | Should -Be "1.2.3-sha256-$($digest.ToLowerInvariant())"
    }

    It 'uses the image tag when no digest is present' {
        Get-DerivedEnvironmentVersion -Image 'example.com/repo:1.2.3' | Should -Be '1.2.3'
    }

    It 'rejects images without a tag and malformed digests' -ForEach @(
        @{ Image = 'example.com/repo' }
        @{ Image = 'localhost:5000/repo' }
        @{ Image = 'example.com/repo:1.0@sha256:zzzz' }
    ) {
        $commonPath = Join-Path $script:RepoRoot 'scripts/lib/common.sh'

        $output = & bash -c 'source "$1"; derive_azureml_environment_version_from_image "$2"' _ $commonPath $Image 2>&1

        $LASTEXITCODE | Should -Not -Be 0
        $output | Should -Not -BeNullOrEmpty
    }

    It 'dry-runs synchronized environment updates without modifying its sandbox' {
        $sandbox = New-UpdateScriptSandbox
        $fakeBin = New-FakeCurl
        $before = & git -C $sandbox status --porcelain

        $result = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @('--dry-run')

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match "Environment References Changed:\s+$($script:PinCases.Count)"
        $result.Output | Should -Match '\[dry-run\] Changed AzureML environment versions would require registration'
        (& git -C $sandbox status --porcelain) | Should -BeExactly $before
    }

    It 'reports environment drift as a SARIF finding in check mode' {
        $sandbox = New-UpdateScriptSandbox
        $fakeBin = New-FakeCurl
        $targetPath = Join-Path $sandbox $script:PinCases[0].Path
        $stale = (Get-Content -Raw $targetPath) -replace '(?m)^environment: azureml:[^\r\n]+$',
            "environment: azureml:$($script:PinCases[0].Environment):stale"
        Set-Content -Path $targetPath -Value $stale -NoNewline
        $sarifPath = Join-Path $sandbox 'environment-drift.sarif'

        $result = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $result.ExitCode | Should -Be 2 -Because $result.Output
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json
        $finding = @($sarif.runs[0].results | Where-Object {
                $_.ruleId -eq 'azureml-environment-version-drift' -and
                $_.locations[0].physicalLocation.artifactLocation.uri -eq $script:PinCases[0].Path
            })

        $finding | Should -HaveCount 1
        $finding[0].locations[0].physicalLocation.artifactLocation.uri |
            Should -Be $script:PinCases[0].Path
        $finding[0].message.text | Should -Match 'Pinned Azure ML environment version stale'
    }

    It 'writes the derived environment version into each workflow and is idempotent' {
        $sandbox = New-UpdateScriptSandbox
        $fakeBin = New-FakeCurl
        $expectedFiles = @{}

        foreach ($case in $script:PinCases) {
            $path = Join-Path $sandbox $case.Path
            $stale = (Get-Content -Raw $path) -replace '(?m)^environment: azureml:[^\r\n]+$', "environment: azureml:$($case.Environment):stale"
            Set-Content -Path $path -Value $stale -NoNewline

            $image = Get-CheckedInImageDefault -Variable $case.Variable
            $ref = $image -replace '@sha256:[0-9a-fA-F]{64}$', ''
            $digest = switch -Wildcard ($ref) {
                '*:2.3.2' { 'a' * 64 }
                '*:2.11.0-*' { 'b' * 64 }
                '*:2.4.1-*' { 'c' * 64 }
                default { 'd' * 64 }
            }
            $expectedVersion = Get-DerivedEnvironmentVersion -Image "${ref}@sha256:${digest}"
            $expectedFiles[$path] = $stale -replace '(?m)^environment: azureml:[^\r\n]+$',
                "environment: azureml:$($case.Environment):$expectedVersion"
        }

        $result = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @()

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match "Environment References Changed:\s+$($script:PinCases.Count)"
        foreach ($path in $expectedFiles.Keys) {
            Get-Content -Raw $path | Should -BeExactly $expectedFiles[$path]
        }

        $secondResult = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @()
        $secondResult.ExitCode | Should -Be 0
        $secondResult.Output | Should -Match 'Environment References Changed:\s+0'
        $secondResult.Output | Should -Match 'Files Updated:\s+0'
    }

    It 'updates an indented environment target' {
        $sandbox = New-UpdateScriptSandbox
        $fakeBin = New-FakeCurl
        $targetPath = Join-Path $sandbox $script:PinCases[0].Path
        $indented = (Get-Content -Raw $targetPath) -replace '(?m)^environment:', '  environment:'
        Set-Content -Path $targetPath -Value $indented -NoNewline

        $result = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @()

        $result.ExitCode | Should -Be 0
        (Get-Content $targetPath | Where-Object { $_ -match 'environment:' }) | Should -Match '^  environment: azureml:'
    }

    It 'rejects discovered AzureML workflow pins missing from the synchronization map' {
        $sandbox = New-UpdateScriptSandbox
        $fakeBin = New-FakeCurl
        $extraPath = Join-Path $sandbox 'training/rl/workflows/azureml/nested/extra.yaml'
        New-Item -ItemType Directory -Path (Split-Path $extraPath) -Force | Out-Null
        Set-Content -Path $extraPath -Value '  environment: azureml:extra-env:stale'

        $result = Invoke-UpdateScript -Sandbox $sandbox -FakeBin $fakeBin -Arguments @()

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'AzureML environment pin targets missing from azureml_pin_specs'
    }
}
