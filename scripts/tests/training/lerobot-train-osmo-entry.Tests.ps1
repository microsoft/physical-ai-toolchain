#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for training/il/scripts/lerobot-train-osmo-entry.sh. The
# tests stub package installation and Python execution while keeping archive
# discovery and lockfile checks on the real filesystem.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command unzip -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../training/il/scripts/lerobot-train-osmo-entry.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/lerobot-train-osmo-entry'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function New-TestWorkspace {
        param([Parameter(Mandatory)][string]$Name)

        $path = Join-Path $script:ArtifactsRoot "$Name-$([System.Guid]::NewGuid().ToString('N'))"
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }

    function New-PayloadArchive {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter(Mandatory)][string]$ZipPath,
            [switch]$IncludeLockfile,
            [string]$LockProject = 'training/il/lerobot'
        )

        $zipSource = Join-Path $Workspace 'zip-source'
        New-Item -ItemType Directory -Path $zipSource -Force | Out-Null
        Set-Content -Path (Join-Path $zipSource 'README.txt') -Value 'payload' -Encoding utf8
        if ($IncludeLockfile) {
            $lockPath = Join-Path $zipSource "$LockProject/uv.lock"
            New-Item -ItemType Directory -Path (Split-Path $lockPath -Parent) -Force | Out-Null
            Set-Content -Path $lockPath -Value 'version = 1' -Encoding utf8
        }
        Compress-Archive -Path (Join-Path $zipSource '*') -DestinationPath $ZipPath -Force
    }

    function New-StubBashEnv {
        param([Parameter(Mandatory)][string]$Workspace)

        $bashEnvPath = Join-Path $Workspace 'bash-env.sh'
        @'
source() {
  return 0
}
'@ | Set-Content -Path $bashEnvPath -NoNewline
        return $bashEnvPath
    }

    function New-LerobotStubSet {
        return @{
            'apt-get' = @'
exit 0
'@
            'apt-cache' = @'
exit 0
'@
            'pip' = @'
exit 0
'@
            'uv' = @'
if [[ "$1" == "python" && "$2" == "install" ]]; then
  exit 0
fi
if [[ "$1" == "venv" ]]; then
  exit 0
fi
if [[ "$1" == "sync" ]]; then
  [[ " $* " == *" --active "* ]] || exit 91
  [[ " $* " == *" --frozen "* ]] || exit 92
  [[ " $* " == *" --no-config "* ]] || exit 93
  [[ " $* " == *" --no-install-project "* ]] || exit 94
  [[ " $* " == *" --project "*"${EXPECTED_LEROBOT_PROJECT:-training/il/lerobot}"* ]] || exit 95
  exit 0
fi
echo "unexpected uv invocation: $*" >&2
exit 1
'@
            'python' = @'
if [[ "$1" == "-c" && "$2" == *"dataset_source"* ]]; then
  echo "${PYTHON_DATASET_SOURCE:-hf}"
  exit 0
fi
if [[ "$1" == "-m" ]]; then
  exit 0
fi
exit 0
'@
        }
    }
}

AfterAll {
    if ($script:ArtifactsRoot) {
        Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
    }
}

Describe 'lerobot-train-osmo-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'archive and lockfile validation' {
        It 'fails when no archive exists under the input mount' {
            $workspace = New-TestWorkspace -Name 'missing-archive'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot -Force | Out-Null
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV                   = $bashEnvPath
                OSMO_INPUT_0               = $inputRoot
                PAYLOAD_ROOT               = $payloadRoot
                DATASET_REPO_ID            = 'org/dataset'
                POLICY_TYPE                = 'act'
                JOB_NAME                   = 'lerobot-job'
                OUTPUT_DIR                 = $outputRoot
                DATASET_ROOT               = (Join-Path $workspace 'datasets')
                PYTHON_DATASET_SOURCE      = 'hf'
            } -Stubs (New-LerobotStubSet)

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
        }

        It 'fails when the unpacked payload does not contain the lerobot lockfile' {
            $workspace = New-TestWorkspace -Name 'missing-lockfile'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip')
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV                   = $bashEnvPath
                OSMO_INPUT_0               = $inputRoot
                PAYLOAD_ROOT               = $payloadRoot
                DATASET_REPO_ID            = 'org/dataset'
                POLICY_TYPE                = 'act'
                JOB_NAME                   = 'lerobot-job'
                OUTPUT_DIR                 = $outputRoot
                DATASET_ROOT               = (Join-Path $workspace 'datasets')
                PYTHON_DATASET_SOURCE      = 'hf'
            } -Stubs (New-LerobotStubSet)

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: LeRobot lockfile not found'
        }
    }

    Context 'datasource selection and training invocation' {
        It 'downloads from Azure Blob URLs and adds blob-only train arguments' {
            $workspace = New-TestWorkspace -Name 'blob-datasource'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            $datasetRoot = Join-Path $workspace 'datasets'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot, $datasetRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV                   = $bashEnvPath
                OSMO_INPUT_0               = $inputRoot
                PAYLOAD_ROOT               = $payloadRoot
                DATASET_REPO_ID            = 'org/dataset'
                POLICY_TYPE                = 'act'
                JOB_NAME                   = 'lerobot-job'
                OUTPUT_DIR                 = $outputRoot
                DATASET_ROOT               = $datasetRoot
                PYTHON_DATASET_SOURCE      = 'blob'
            } -Stubs (New-LerobotStubSet)

            $trainCall = $result.Calls | Where-Object { $_ -like 'python -m training.il.scripts.lerobot.train*' } | Select-Object -First 1
            $downloadCall = $result.Calls | Where-Object { $_ -eq 'python -m training.il.scripts.lerobot.download_dataset' } | Select-Object -First 1
            $expectedDatasetArg = "--dataset.root=$(Join-Path $datasetRoot 'org/dataset')"

            $result.ExitCode | Should -Be 0
            $result.StdOut | Should -Match 'Data Source: Azure Blob URLs'
            $downloadCall | Should -Be 'python -m training.il.scripts.lerobot.download_dataset'
            $trainCall | Should -Match '--policy\.push_to_hub=false'
            $trainCall | Should -Match '--wandb\.enable=false'
            $trainCall | Should -Match '--dataset\.video_backend=pyav'
            $trainCall | Should -Match ([regex]::Escape($expectedDatasetArg))
            $trainCall | Should -Match '--dataset\.use_imagenet_stats=false'
            ([Array]::IndexOf($result.Calls, $downloadCall)) | Should -BeLessThan ([Array]::IndexOf($result.Calls, $trainCall))
        }

        It 'uses the HuggingFace branch without downloading or adding blob-only train arguments' {
            $workspace = New-TestWorkspace -Name 'huggingface-datasource'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            $datasetRoot = Join-Path $workspace 'datasets'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot, $datasetRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV                   = $bashEnvPath
                OSMO_INPUT_0               = $inputRoot
                PAYLOAD_ROOT               = $payloadRoot
                DATASET_REPO_ID            = 'org/dataset'
                POLICY_TYPE                = 'diffusion'
                JOB_NAME                   = 'lerobot-job'
                OUTPUT_DIR                 = $outputRoot
                DATASET_ROOT               = $datasetRoot
                PYTHON_DATASET_SOURCE      = 'hf'
            } -Stubs (New-LerobotStubSet)

            $trainCall = $result.Calls | Where-Object { $_ -like 'python -m training.il.scripts.lerobot.train*' } | Select-Object -First 1

            $result.ExitCode | Should -Be 0
            $result.StdOut | Should -Match 'Data Source: HuggingFace Hub'
            $result.Calls | Should -Not -Contain 'python -m training.il.scripts.lerobot.download_dataset'
            $trainCall | Should -Match '--policy\.push_to_hub=false'
            $trainCall | Should -Match '--wandb\.enable=false'
            $trainCall | Should -Match '--dataset\.video_backend=pyav'
            $trainCall | Should -Not -Match '--dataset\.root='
            $trainCall | Should -Not -Match '--dataset\.use_imagenet_stats=false'
        }

        It 'trains from a mounted local dataset without downloading' {
            $workspace = New-TestWorkspace -Name 'local-datasource'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            $datasetDir = Join-Path $workspace 'mnt/datasets/so101/pick'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot, (Join-Path $datasetDir 'meta') -Force | Out-Null
            Set-Content -Path (Join-Path $datasetDir 'meta/info.json') -Value '{}' -Encoding utf8
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV              = $bashEnvPath
                OSMO_INPUT_0          = $inputRoot
                PAYLOAD_ROOT          = $payloadRoot
                DATASET_REPO_ID       = 'local/so101-pick'
                DATASET_DIR           = $datasetDir
                POLICY_TYPE           = 'act'
                JOB_NAME              = 'lerobot-job'
                OUTPUT_DIR            = $outputRoot
                DATASET_ROOT          = (Join-Path $workspace 'datasets')
                TRACKING_BACKEND      = 'mlflow'
                PYTHON_DATASET_SOURCE = 'local'
            } -Stubs (New-LerobotStubSet)

            $trainCall = $result.Calls | Where-Object { $_ -like 'python -m training.il.scripts.lerobot.train*' } | Select-Object -First 1

            $result.ExitCode | Should -Be 0
            $result.StdOut | Should -Match 'Tracking: mlflow'
            $result.StdOut | Should -Match ([regex]::Escape("Data Source: local dataset at $datasetDir"))
            $result.Calls | Should -Not -Contain 'python -m training.il.scripts.lerobot.download_dataset'
            $trainCall | Should -Match ([regex]::Escape("--dataset.root=$datasetDir"))
            $trainCall | Should -Not -Match '--dataset\.use_imagenet_stats=false'
        }

        It 'fails when the local dataset directory has no LeRobot metadata' {
            $workspace = New-TestWorkspace -Name 'local-datasource-missing'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV              = $bashEnvPath
                OSMO_INPUT_0          = $inputRoot
                PAYLOAD_ROOT          = $payloadRoot
                DATASET_REPO_ID       = 'local/missing'
                DATASET_DIR           = (Join-Path $workspace 'mnt/datasets/missing')
                POLICY_TYPE           = 'act'
                JOB_NAME              = 'lerobot-job'
                OUTPUT_DIR            = $outputRoot
                PYTHON_DATASET_SOURCE = 'local'
            } -Stubs (New-LerobotStubSet)

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no LeRobot dataset at'
        }
    }

    Context 'LeRobot dependency project selection' {
        It 'installs from the project named by LEROBOT_PROJECT' {
            $workspace = New-TestWorkspace -Name 'vla-project'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile -LockProject 'training/vla/lerobot'
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV                 = $bashEnvPath
                OSMO_INPUT_0             = $inputRoot
                PAYLOAD_ROOT             = $payloadRoot
                DATASET_REPO_ID          = 'org/dataset'
                POLICY_TYPE              = 'smolvla'
                JOB_NAME                 = 'lerobot-job'
                OUTPUT_DIR               = $outputRoot
                LEROBOT_PROJECT          = 'training/vla/lerobot'
                EXPECTED_LEROBOT_PROJECT = 'training/vla/lerobot'
                PYTHON_DATASET_SOURCE    = 'hf'
            } -Stubs (New-LerobotStubSet)

            $result.ExitCode | Should -Be 0
            ($result.Calls | Where-Object { $_ -like 'uv sync*' }) | Should -Match ([regex]::Escape("--project $payloadRoot/training/vla/lerobot"))
        }

        It 'rejects a LEROBOT_PROJECT that leaves the payload' {
            $workspace = New-TestWorkspace -Name 'project-traversal'
            $inputRoot = Join-Path $workspace 'input'
            $payloadRoot = Join-Path $workspace 'payload'
            $outputRoot = Join-Path $workspace 'output'
            New-Item -ItemType Directory -Path $inputRoot, $payloadRoot, $outputRoot -Force | Out-Null
            New-PayloadArchive -Workspace $workspace -ZipPath (Join-Path $inputRoot 'payload.zip') -IncludeLockfile
            $bashEnvPath = New-StubBashEnv -Workspace $workspace

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workspace -EnvVars @{
                BASH_ENV              = $bashEnvPath
                OSMO_INPUT_0          = $inputRoot
                PAYLOAD_ROOT          = $payloadRoot
                DATASET_REPO_ID       = 'org/dataset'
                POLICY_TYPE           = 'act'
                JOB_NAME              = 'lerobot-job'
                OUTPUT_DIR            = $outputRoot
                LEROBOT_PROJECT       = '../outside'
                PYTHON_DATASET_SOURCE = 'hf'
            } -Stubs (New-LerobotStubSet)

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'LEROBOT_PROJECT must be a repo-relative path'
        }
    }
}
