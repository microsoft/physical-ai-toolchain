#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for training/rl/scripts/osmo-train-entry.sh. These tests
# exercise the real archive discovery and unzip flow while replacing only
# heavyweight or environment-dependent commands.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command unzip -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../training/rl/scripts/osmo-train-entry.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/osmo-train-entry'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function New-TestWorkspace {
        param([Parameter(Mandatory)][string]$Name)

        $path = Join-Path $script:ArtifactsRoot "$Name-$([System.Guid]::NewGuid().ToString('N'))"
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }

    function New-DummyZip {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter(Mandatory)][string]$ZipPath
        )

        $zipSource = Join-Path $Workspace 'zip-source'
        New-Item -ItemType Directory -Path $zipSource -Force | Out-Null
        Set-Content -Path (Join-Path $zipSource 'payload.txt') -Value 'payload' -Encoding utf8
        Compress-Archive -Path (Join-Path $zipSource '*') -DestinationPath $ZipPath -Force
    }

    function New-FakeTrainScript {
        param([Parameter(Mandatory)][string]$PayloadRoot)

        $trainScript = Join-Path $PayloadRoot 'training/rl/scripts/train.sh'
        New-Item -ItemType Directory -Path (Split-Path $trainScript -Parent) -Force | Out-Null
        @'
#!/bin/bash
printf '%s\n' "$@" > "${TRAIN_ARGS_LOG_PATH}"
exit 0
'@ | Set-Content -Path $trainScript -NoNewline
        & chmod +x $trainScript
        return $trainScript
    }

    function Get-LoggedTrainArgs {
        param([Parameter(Mandatory)][string]$LogPath)

        if (-not (Test-Path $LogPath)) {
            return @()
        }

        return @(Get-Content -Path $LogPath)
    }

    function Assert-ExactSequence {
        param(
            [Parameter(Mandatory)][string[]]$Actual,
            [Parameter(Mandatory)][string[]]$Expected
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

Describe 'osmo-train-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'archive validation' {
        It 'fails when no archive exists under the input mount' {
            $workspace = New-TestWorkspace -Name 'missing-archive'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot -Force | Out-Null
            New-FakeTrainScript -PayloadRoot $payloadRoot | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0        = $inputRoot
                PAYLOAD_ROOT        = $payloadRoot
                TRAIN_ARGS_LOG_PATH = $trainLog
            }

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
            Test-Path $trainLog | Should -BeFalse
        }

        It 'fails when the discovered zip is empty' {
            $workspace = New-TestWorkspace -Name 'empty-archive'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot -Force | Out-Null
            New-FakeTrainScript -PayloadRoot $payloadRoot | Out-Null
            New-Item -ItemType File -Path (Join-Path $inputRoot 'payload.zip') -Force | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0        = $inputRoot
                PAYLOAD_ROOT        = $payloadRoot
                TRAIN_ARGS_LOG_PATH = $trainLog
            }

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
            Test-Path $trainLog | Should -BeFalse
        }
    }

    Context 'post-unpack sleep mode' {
        It 'sleeps and exits without invoking train.sh' {
            $workspace = New-TestWorkspace -Name 'sleep-after-unpack'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot -Force | Out-Null
            New-FakeTrainScript -PayloadRoot $payloadRoot | Out-Null
            New-DummyZip -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip')

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0        = $inputRoot
                PAYLOAD_ROOT        = $payloadRoot
                SLEEP_AFTER_UNPACK  = '0'
                TRAIN_ARGS_LOG_PATH = $trainLog
            } -Stubs @{
                sleep = 'exit 0'
            }

            $result.ExitCode | Should -Be 0
            $result.Calls | Should -Contain 'sleep 0'
            Test-Path $trainLog | Should -BeFalse
        }
    }

    Context 'training invocation' {
        It 'passes the default train arguments and omits max_iterations when unset' {
            $workspace = New-TestWorkspace -Name 'train-default-mode'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot -Force | Out-Null
            New-FakeTrainScript -PayloadRoot $payloadRoot | Out-Null
            New-DummyZip -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip')

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0         = $inputRoot
                PAYLOAD_ROOT         = $payloadRoot
                TASK                 = 'Isaac-Cartpole-Direct-v0'
                NUM_ENVS             = '128'
                CHECKPOINT_URI       = 'azure://checkpoints/train.ckpt'
                REGISTER_CHECKPOINT  = 'best'
                TRAIN_ARGS_LOG_PATH  = $trainLog
            }

            $result.ExitCode | Should -Be 0
            Assert-ExactSequence -Actual (Get-LoggedTrainArgs -LogPath $trainLog) -Expected @(
                '--mode'
                'train'
                '--task'
                'Isaac-Cartpole-Direct-v0'
                '--num_envs'
                '128'
                '--checkpoint-uri'
                'azure://checkpoints/train.ckpt'
                '--checkpoint-mode'
                'from-scratch'
                '--register-checkpoint'
                'best'
                '--headless'
            )
        }

        It 'switches to smoke-test mode and includes max_iterations when set' {
            $workspace = New-TestWorkspace -Name 'train-smoke-mode'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $trainLog = Join-Path $workspace 'train-args.log'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot -Force | Out-Null
            New-FakeTrainScript -PayloadRoot $payloadRoot | Out-Null
            New-DummyZip -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip')

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                OSMO_INPUT_0         = $inputRoot
                PAYLOAD_ROOT         = $payloadRoot
                RUN_AZURE_SMOKE_TEST = '1'
                TASK                 = 'Isaac-Ant-v0'
                NUM_ENVS             = '16'
                MAX_ITERATIONS       = '25'
                CHECKPOINT_URI       = 'azure://checkpoints/smoke.ckpt'
                REGISTER_CHECKPOINT  = 'smoke'
                TRAIN_ARGS_LOG_PATH  = $trainLog
            }

            $result.ExitCode | Should -Be 0
            Assert-ExactSequence -Actual (Get-LoggedTrainArgs -LogPath $trainLog) -Expected @(
                '--mode'
                'smoke-test'
                '--task'
                'Isaac-Ant-v0'
                '--num_envs'
                '16'
                '--checkpoint-uri'
                'azure://checkpoints/smoke.ckpt'
                '--checkpoint-mode'
                'from-scratch'
                '--register-checkpoint'
                'smoke'
                '--headless'
                '--max_iterations'
                '25'
            )
        }
    }
}
