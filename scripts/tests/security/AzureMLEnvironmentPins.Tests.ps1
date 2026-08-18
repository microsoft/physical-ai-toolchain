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
}

Describe 'AzureML environment pins' -Tag 'Unit' {
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

    It 'dry-runs synchronized environment updates without modifying workflow files' {
        $fakeBin = Join-Path $TestDrive 'bin'
        New-Item -ItemType Directory -Path $fakeBin | Out-Null
        $fakeCurl = Join-Path $fakeBin 'curl'
        @'
#!/usr/bin/env bash
if [[ "$*" == *"auth.docker.io"* || "$*" == *"proxy_auth"* ]]; then
  printf '{"token":"test"}\n'
else
  printf 'Docker-Content-Digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\r\n'
fi
'@ | Set-Content -Path $fakeCurl
        & chmod +x $fakeCurl

        $originals = @{}
        foreach ($case in $pinCases) {
            $path = Join-Path $script:RepoRoot $case.Path
            $originals[$path] = Get-Content -Raw $path
        }

        $originalPath = $env:PATH
        try {
            $env:PATH = "$fakeBin$([IO.Path]::PathSeparator)$originalPath"
            $output = & bash (Join-Path $script:RepoRoot 'scripts/update-image-digests.sh') --dry-run 2>&1
            $LASTEXITCODE | Should -Be 0
        }
        finally {
            $env:PATH = $originalPath
        }

        $output -join "`n" | Should -Match 'Environment Versions Changed:\s+4'
        foreach ($case in $pinCases) {
            $path = Join-Path $script:RepoRoot $case.Path
            Get-Content -Raw $path | Should -BeExactly $originals[$path]
        }
    }
}
