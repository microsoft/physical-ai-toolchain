#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for training/rl/scripts/osmo-train-dataset-entry.sh. The
# tests build a real mounted training tree and observe the arguments forwarded
# into the embedded train.sh entrypoint.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../training/rl/scripts/osmo-train-dataset-entry.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/osmo-train-dataset-entry'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function New-TestWorkspace {
        param([Parameter(Mandatory)][string]$Name)

        $path = Join-Path $script:ArtifactsRoot "$Name-$([System.Guid]::NewGuid().ToString('N'))"
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }

    function New-FakeTrainingTree {
        param(
            [Parameter(Mandatory)][string]$InputRoot,
            [Parameter(Mandatory)][string]$DatasetName
        )

        $trainingRoot = Join-Path $InputRoot "$DatasetName/training/rl"
        $trainScript = Join-Path $trainingRoot 'scripts/train.sh'
        New-Item -ItemType Directory -Path (Split-Path $trainScript -Parent) -Force | Out-Null
        @'
#!/bin/bash
printf '%s\n' "$@" > "${TRAIN_ARGS_LOG_PATH}"
printf 'PYTHONPATH=%s\n' "${PYTHONPATH:-}" >> "${TRAIN_ARGS_LOG_PATH}"
exit 0
'@ | Set-Content -Path $trainScript -NoNewline
        & chmod +x $trainScript
        return $trainingRoot
    }

    function Get-TrainInvocation {
        param([Parameter(Mandatory)][string]$LogPath)

        $lines = @(Get-Content -Path $LogPath)
        $pyPathLine = ($lines | Where-Object { $_ -like 'PYTHONPATH=*' } | Select-Object -First 1)
        $trainArgs = @($lines | Where-Object { $_ -notlike 'PYTHONPATH=*' })

        [PSCustomObject]@{
            Args       = $trainArgs
            PythonPath = if ($pyPathLine) { $pyPathLine.Substring('PYTHONPATH='.Length) } else { '' }
        }
    }

    function Assert-ExactSequence {
        param(
            [Parameter(Mandatory)][AllowEmptyString()][string[]]$Actual,
            [Parameter(Mandatory)][AllowEmptyString()][string[]]$Expected
        )

        $Actual | Should -HaveCount $Expected.Count
        for ($index = 0; $index -lt $Expected.Count; $index++) {
            $Actual[$index] | Should -Be $Expected[$index]
        }
    }
}

AfterAll {
    if ($script:ArtifactsRoot) {
        Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
    }
}

Describe 'osmo-train-dataset-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'training-root validation' {
        It 'fails when the mounted training root does not exist' {
            $workspace = New-TestWorkspace -Name 'missing-training-root'
            $inputRoot = Join-Path $workspace 'input'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0         = $inputRoot
                OSMO_DATASET_NAME    = 'training-code'
                TRAIN_ARGS_LOG_PATH  = $trainLog
            }

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'Error: Training root not found'
            Test-Path $trainLog | Should -BeFalse
        }
    }

    Context 'training invocation' {
        It 'exports the package root and keeps max_iterations present even when empty' {
            $workspace = New-TestWorkspace -Name 'dataset-default-mode'
            $inputRoot = Join-Path $workspace 'input'
            $trainLog = Join-Path $workspace 'train-args.log'
            $datasetName = 'training-code'
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null
            $trainingRoot = New-FakeTrainingTree -InputRoot $inputRoot -DatasetName $datasetName

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0         = $inputRoot
                OSMO_DATASET_NAME    = $datasetName
                TASK                 = 'Isaac-Reach-v0'
                NUM_ENVS             = '32'
                CHECKPOINT_URI       = 'azure://dataset/default.ckpt'
                REGISTER_CHECKPOINT  = 'latest'
                TRAIN_ARGS_LOG_PATH  = $trainLog
            }

            $invocation = Get-TrainInvocation -LogPath $trainLog

            $result.ExitCode | Should -Be 0
            $invocation.PythonPath | Should -Be "$(Join-Path $inputRoot $datasetName):"
            # This script always includes --max_iterations, even when MAX_ITERATIONS is unset.
            Assert-ExactSequence -Actual $invocation.Args -Expected @(
                '--mode'
                'train'
                '--task'
                'Isaac-Reach-v0'
                '--num_envs'
                '32'
                '--max_iterations'
                ''
                '--checkpoint-uri'
                'azure://dataset/default.ckpt'
                '--checkpoint-mode'
                'from-scratch'
                '--register-checkpoint'
                'latest'
                '--headless'
            )
            $trainingRoot | Should -Be (Join-Path $inputRoot "$datasetName/training/rl")
        }

        It 'switches to smoke-test mode and forwards the explicit iteration limit' {
            $workspace = New-TestWorkspace -Name 'dataset-smoke-mode'
            $inputRoot = Join-Path $workspace 'input'
            $trainLog = Join-Path $workspace 'train-args.log'
            $datasetName = 'training-code'
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null
            New-FakeTrainingTree -InputRoot $inputRoot -DatasetName $datasetName | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0         = $inputRoot
                OSMO_DATASET_NAME    = $datasetName
                RUN_AZURE_SMOKE_TEST = '1'
                TASK                 = 'Isaac-Humanoid-v0'
                NUM_ENVS             = '8'
                MAX_ITERATIONS       = '7'
                CHECKPOINT_URI       = 'azure://dataset/smoke.ckpt'
                REGISTER_CHECKPOINT  = 'smoke'
                TRAIN_ARGS_LOG_PATH  = $trainLog
            }

            $invocation = Get-TrainInvocation -LogPath $trainLog

            $result.ExitCode | Should -Be 0
            Assert-ExactSequence -Actual $invocation.Args -Expected @(
                '--mode'
                'smoke-test'
                '--task'
                'Isaac-Humanoid-v0'
                '--num_envs'
                '8'
                '--max_iterations'
                '7'
                '--checkpoint-uri'
                'azure://dataset/smoke.ckpt'
                '--checkpoint-mode'
                'from-scratch'
                '--register-checkpoint'
                'smoke'
                '--headless'
            )
        }
    }
}
