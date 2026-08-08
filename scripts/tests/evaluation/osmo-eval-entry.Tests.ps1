#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for evaluation/sil/scripts/osmo-eval-entry.sh. Tests assert
# the required checkpoint guard, runtime payload archive discovery, and the final
# infer.sh invocation using both documented defaults and explicit env overrides.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command unzip -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../evaluation/sil/scripts/osmo-eval-entry.sh')).Path
    $script:ScratchRoot = Join-Path $PSScriptRoot '.scratch-osmo-eval-entry'
    New-Item -ItemType Directory -Path $script:ScratchRoot -Force | Out-Null

    function New-TestWorkspace {
        param([Parameter(Mandatory)][string]$Name)

        $workspace = Join-Path $script:ScratchRoot ("{0}-{1}" -f $Name, [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workspace -Force | Out-Null
        return $workspace
    }

    function New-ArchiveInput {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter(Mandatory)][string]$ArchiveName
        )

        $inputRoot = Join-Path $Workspace 'osmo-input'
        $payloadSource = Join-Path $Workspace 'archive-source'
        $archivePath = Join-Path $inputRoot $ArchiveName

        New-Item -ItemType Directory -Path $inputRoot, $payloadSource -Force | Out-Null
        Set-Content -Path (Join-Path $payloadSource 'payload.txt') -Value 'payload' -Encoding utf8
        Compress-Archive -Path (Join-Path $payloadSource '*') -DestinationPath $archivePath -Force

        return [PSCustomObject]@{
            InputRoot   = $inputRoot
            ArchivePath = $archivePath
        }
    }

    function New-FakeInferScript {
        # infer.sh writes its received args to the path given via the HARNESS_ARGS_LOG
        # env var (set by the caller in -EnvVars), not a parameter here.
        param(
            [Parameter(Mandatory)][string]$PayloadRoot
        )

        $inferenceRoot = Join-Path $PayloadRoot 'evaluation/sil'
        $inferPath = Join-Path $inferenceRoot 'infer.sh'
        New-Item -ItemType Directory -Path $inferenceRoot -Force | Out-Null

        @(
            '#!/bin/bash'
            'set -euo pipefail'
            'printf "%s\n" "$@" > "$HARNESS_ARGS_LOG"'
        ) | Set-Content -Path $inferPath -Encoding utf8
        & chmod +x $inferPath

        return $inferPath
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ScratchRoot -ErrorAction SilentlyContinue
}

Describe 'osmo-eval-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'checkpoint validation' {
        It 'fails before inspecting the input mount when CHECKPOINT_URI is missing' {
            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{} -Stubs @{} -WorkDir (New-TestWorkspace -Name 'missing-checkpoint')

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr.Trim() | Should -Be 'checkpoint_uri is required'
            $result.StdErr | Should -Not -Match 'no code archive found'
        }
    }

    Context 'runtime payload archive discovery' {
        It 'fails when no zip archive exists under OSMO_INPUT_0' {
            $workspace = New-TestWorkspace -Name 'missing-archive'
            $inputRoot = Join-Path $workspace 'osmo-input'
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{
                CHECKPOINT_URI = 'azureml://models/policy/1'
                OSMO_INPUT_0   = $inputRoot
                PAYLOAD_ROOT   = (Join-Path $workspace 'payload-root')
            } -Stubs @{} -WorkDir $workspace

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
        }

        It 'fails when the discovered zip archive is empty' {
            $workspace = New-TestWorkspace -Name 'empty-archive'
            $inputRoot = Join-Path $workspace 'osmo-input'
            $archivePath = Join-Path $inputRoot 'payload.zip'
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null
            New-Item -ItemType File -Path $archivePath -Force | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{
                CHECKPOINT_URI = 'azureml://models/policy/1'
                OSMO_INPUT_0   = $inputRoot
                PAYLOAD_ROOT   = (Join-Path $workspace 'payload-root')
            } -Stubs @{} -WorkDir $workspace

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
        }
    }

    Context 'infer.sh invocation' {
        It 'passes explicit evaluation arguments through to infer.sh' {
            $workspace = New-TestWorkspace -Name 'explicit-args'
            $payloadRoot = Join-Path $workspace 'payload-root'
            $argsLogPath = Join-Path $workspace 'infer-args.log'
            $archive = New-ArchiveInput -Workspace $workspace -ArchiveName 'payload.zip'
            New-FakeInferScript -PayloadRoot $payloadRoot | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{
                CHECKPOINT_URI    = 'azureml://models/policy/9'
                OSMO_INPUT_0      = $archive.InputRoot
                PAYLOAD_ROOT      = $payloadRoot
                HARNESS_ARGS_LOG  = $argsLogPath
                TASK              = 'Isaac-Cartpole-Direct-v0'
                NUM_ENVS          = '8'
                MAX_STEPS         = '1234'
                VIDEO_LENGTH      = '77'
                INFERENCE_FORMAT  = 'video'
            } -Stubs @{} -WorkDir $workspace

            $result.ExitCode | Should -Be 0
            Get-Content -Path $argsLogPath | Should -Be @(
                '--task'
                'Isaac-Cartpole-Direct-v0'
                '--num-envs'
                '8'
                '--max-steps'
                '1234'
                '--video-length'
                '77'
                '--inference-format'
                'video'
                '--checkpoint-uri'
                'azureml://models/policy/9'
                '--headless'
            )
        }

        It 'uses documented default values when optional env vars are unset' {
            $workspace = New-TestWorkspace -Name 'default-args'
            $payloadRoot = Join-Path $workspace 'payload-root'
            $argsLogPath = Join-Path $workspace 'infer-args.log'
            $archive = New-ArchiveInput -Workspace $workspace -ArchiveName 'payload.zip'
            New-FakeInferScript -PayloadRoot $payloadRoot | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{
                CHECKPOINT_URI   = 'azureml://models/policy/11'
                OSMO_INPUT_0     = $archive.InputRoot
                PAYLOAD_ROOT     = $payloadRoot
                HARNESS_ARGS_LOG = $argsLogPath
                TASK             = 'Isaac-Reach-Franka-v0'
            } -Stubs @{} -WorkDir $workspace

            $result.ExitCode | Should -Be 0
            Get-Content -Path $argsLogPath | Should -Be @(
                '--task'
                'Isaac-Reach-Franka-v0'
                '--num-envs'
                '4'
                '--max-steps'
                '500'
                '--video-length'
                '200'
                '--inference-format'
                'both'
                '--checkpoint-uri'
                'azureml://models/policy/11'
                '--headless'
            )
        }
    }
}
