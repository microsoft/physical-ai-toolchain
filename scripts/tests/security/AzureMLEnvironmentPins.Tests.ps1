# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

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

        $parts = $Image -split '@sha256:', 2
        $parts.Count | Should -Be 2
        $parts[1] | Should -Match '^[0-9a-f]{64}$'
        $tag = ($parts[0] -split ':')[-1]
        return "$tag-sha256-$($parts[1])"
    }

    $script:PinCases = @(
        @{
            Variable    = 'DEFAULT_ISAAC_LAB_IMAGE'
            Environment = 'isaaclab-training-env'
            Path        = 'training/rl/workflows/azureml/train.yaml'
        },
        @{
            Variable    = 'DEFAULT_ISAAC_LAB_IMAGE'
            Environment = 'isaaclab-training-env'
            Path        = 'evaluation/sil/workflows/azureml/isaaclab-evaluation.yaml'
        },
        @{
            Variable    = 'DEFAULT_LEROBOT_TRAIN_IMAGE'
            Environment = 'lerobot-training-env'
            Path        = 'training/il/workflows/azureml/lerobot-train.yaml'
        },
        @{
            Variable    = 'DEFAULT_LEROBOT_EVAL_IMAGE'
            Environment = 'lerobot-inference-env'
            Path        = 'evaluation/sil/workflows/azureml/lerobot-eval.yaml'
        }
    )
}

Describe 'AzureML environment pins' -Tag 'Unit' {
    It 'matches <Variable> in <Path>' -ForEach $script:PinCases {
        $image = Get-CheckedInImageDefault -Variable $Variable
        $expected = "environment: azureml:${Environment}:$(Get-DerivedEnvironmentVersion -Image $image)"
        $workflowPath = Join-Path $script:RepoRoot $Path
        $environmentLines = @(Get-Content $workflowPath | Where-Object { $_ -match '^environment:' })

        $environmentLines.Count | Should -Be 1
        $environmentLines[0] | Should -BeExactly $expected
    }
}
