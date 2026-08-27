#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for evaluation/sil/scripts/osmo-lerobot-eval-entry.sh.
# Tests stub package/bootstrap commands and assert archive discovery, lockfile
# validation, optional AML/blob/MLflow branches, registration gating, and the
# builtin-policy dataset guard without real installs or network access.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command unzip -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../evaluation/sil/scripts/osmo-lerobot-eval-entry.sh')).Path
    $script:ScratchRoot = Join-Path $PSScriptRoot '.scratch-osmo-lerobot-eval-entry'
    New-Item -ItemType Directory -Path $script:ScratchRoot -Force | Out-Null

    function New-TestWorkspace {
        param([Parameter(Mandatory)][string]$Name)

        $workspace = Join-Path $script:ScratchRoot ("{0}-{1}" -f $Name, [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workspace -Force | Out-Null
        return $workspace
    }

    function New-BashEnvOverride {
        param([Parameter(Mandatory)][string]$Workspace)

        $bashEnvPath = Join-Path $Workspace '.bash_env'
        @(
            'source() {'
            '  if [[ "$1" == "/opt/lerobot-venv/bin/activate" ]]; then'
            '    return 0'
            '  fi'
            '  builtin source "$@"'
            '}'
        ) | Set-Content -Path $bashEnvPath -Encoding utf8

        return $bashEnvPath
    }

    function New-PayloadArchive {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter()][bool]$IncludeLockfile = $true
        )

        $inputRoot = Join-Path $Workspace 'osmo-input'
        $archiveSource = Join-Path $Workspace 'archive-source'
        $archivePath = Join-Path $inputRoot 'payload.zip'

        New-Item -ItemType Directory -Path $inputRoot, $archiveSource -Force | Out-Null
        Set-Content -Path (Join-Path $archiveSource 'payload.txt') -Value 'payload' -Encoding utf8

        if ($IncludeLockfile) {
            $lockfilePath = Join-Path $archiveSource 'training/il/lerobot/uv.lock'
            New-Item -ItemType Directory -Path (Split-Path $lockfilePath -Parent) -Force | Out-Null
            Set-Content -Path $lockfilePath -Value 'version = 1' -Encoding utf8
        }

        Compress-Archive -Path (Join-Path $archiveSource '*') -DestinationPath $archivePath -Force

        return [PSCustomObject]@{
            InputRoot   = $inputRoot
            ArchivePath = $archivePath
        }
    }

    function Get-BaseStubs {
        return @{
            'apt-get' = 'exit 0'
            'apt-cache' = 'exit 0'
            'pip' = 'exit 0'
            'uv' = @'
if [[ "$1" == "python" && "$2" == "install" ]]; then
  exit 0
fi
if [[ "$1" == "venv" ]]; then
  exit 0
fi
if [[ "$1" == "export" ]]; then
  printf "dummy==1.0\n"
  exit 0
fi
if [[ "$1" == "pip" ]]; then
  cat >/dev/null
  exit 0
fi

echo "unexpected uv invocation: $*" >&2
exit 1
'@
            'python3' = @'
case "$1" in
  *download_aml_model.py)
    cat > /tmp/aml_model_path.env <<EOF
AML_MODEL_PATH=/models/aml-policy
EOF
    ;;
  *download_blob_dataset.py)
    mkdir -p "$HARNESS_DATASET_DIR"
    cat > /tmp/dataset_path.env <<EOF
DATASET_DIR=$HARNESS_DATASET_DIR
EOF
    ;;
  *bootstrap_mlflow.py)
    mkdir -p "$HARNESS_MLFLOW_DIR"
    cat > /tmp/mlflow_config.env <<EOF
MLFLOW_TRACKING_URI=file://$HARNESS_MLFLOW_DIR
EOF
    ;;
  *run_evaluation.py)
    {
      printf "POLICY_REPO_ID=%s\n" "${POLICY_REPO_ID:-}"
      printf "DATASET_DIR=%s\n" "${DATASET_DIR:-}"
      printf "MLFLOW_TRACKING_URI=%s\n" "${MLFLOW_TRACKING_URI:-}"
    } > "$HARNESS_ENV_SNAPSHOT"
    ;;
  *register_model.py)
    ;;
  -c)
    ;;
esac
exit 0
'@
            'lerobot-train' = 'exit 0'
        }
    }

    function Invoke-LerobotEntry {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter(Mandatory)][bool]$IncludeLockfile,
            [hashtable]$EnvOverrides = @{},
            [hashtable]$StubOverrides = @{}
        )

        $archive = New-PayloadArchive -Workspace $Workspace -IncludeLockfile:$IncludeLockfile
        $bashEnvPath = New-BashEnvOverride -Workspace $Workspace
        $payloadRoot = Join-Path $Workspace 'payload-root'
        $outputDir = Join-Path $Workspace 'output'
        $datasetDir = Join-Path $Workspace 'dataset'
        $mlflowDir = Join-Path $Workspace 'mlflow'
        $envSnapshot = Join-Path $Workspace 'evaluation-env.txt'

        $envVars = @{
            BASH_ENV             = $bashEnvPath
            OSMO_INPUT_0         = $archive.InputRoot
            PAYLOAD_ROOT         = $payloadRoot
            OUTPUT_DIR           = $outputDir
            POLICY_TYPE          = 'act'
            EVAL_EPISODES        = '2'
            HARNESS_DATASET_DIR  = $datasetDir
            HARNESS_MLFLOW_DIR   = $mlflowDir
            HARNESS_ENV_SNAPSHOT = $envSnapshot
        }
        foreach ($key in $EnvOverrides.Keys) {
            $envVars[$key] = $EnvOverrides[$key]
        }

        $stubs = Get-BaseStubs
        foreach ($key in $StubOverrides.Keys) {
            $stubs[$key] = $StubOverrides[$key]
        }

        $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars $envVars -Stubs $stubs -WorkDir $Workspace
        return [PSCustomObject]@{
            Result         = $result
            PayloadRoot    = $payloadRoot
            OutputDir      = $outputDir
            EnvSnapshot    = $envSnapshot
            DatasetDir     = $datasetDir
            MlflowDir      = $mlflowDir
        }
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ScratchRoot -ErrorAction SilentlyContinue
}

Describe 'osmo-lerobot-eval-entry.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'runtime payload validation' {
        It 'fails when no zip archive exists under OSMO_INPUT_0' {
            $workspace = New-TestWorkspace -Name 'missing-archive'
            $inputRoot = Join-Path $workspace 'osmo-input'
            $bashEnvPath = New-BashEnvOverride -Workspace $workspace
            New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null

            $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -EnvVars @{
                BASH_ENV             = $bashEnvPath
                OSMO_INPUT_0         = $inputRoot
                PAYLOAD_ROOT         = (Join-Path $workspace 'payload-root')
                OUTPUT_DIR           = (Join-Path $workspace 'output')
                POLICY_TYPE          = 'act'
                EVAL_EPISODES        = '2'
                HARNESS_DATASET_DIR  = (Join-Path $workspace 'dataset')
                HARNESS_MLFLOW_DIR   = (Join-Path $workspace 'mlflow')
                HARNESS_ENV_SNAPSHOT = (Join-Path $workspace 'evaluation-env.txt')
            } -Stubs (Get-BaseStubs) -WorkDir $workspace

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'ERROR: no code archive found'
        }

        It 'fails when the runtime payload omits the LeRobot lockfile' {
            $workspace = New-TestWorkspace -Name 'missing-lockfile'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $false

            $invocation.Result.ExitCode | Should -Not -Be 0
            $invocation.Result.StdErr | Should -Match 'ERROR: LeRobot lockfile not found'
        }
    }

    Context 'optional download branches' {
        It 'skips the AML model download when AML_MODEL_NAME is unset' {
            $workspace = New-TestWorkspace -Name 'skip-aml-download'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true

            $invocation.Result.ExitCode | Should -Be 0
            ($invocation.Result.Calls -join "`n") | Should -Not -Match 'download_aml_model\.py'
            ($invocation.Result.Calls -join "`n") | Should -Match 'python3 .*/run_evaluation\.py'
        }

        It 'downloads the AML model when AML_MODEL_NAME and AML_MODEL_VERSION are set' {
            $workspace = New-TestWorkspace -Name 'download-aml-model'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{
                AML_MODEL_NAME    = 'policy-model'
                AML_MODEL_VERSION = '7'
            }

            $invocation.Result.ExitCode | Should -Be 0
            ($invocation.Result.Calls -join "`n") | Should -Match 'python3 .*/download_aml_model\.py'
            Get-Content -Path $invocation.EnvSnapshot | Should -Contain 'POLICY_REPO_ID=/models/aml-policy'
        }

        It 'skips the blob dataset download when blob inputs are unset' {
            $workspace = New-TestWorkspace -Name 'skip-blob-download'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true

            $invocation.Result.ExitCode | Should -Be 0
            ($invocation.Result.Calls -join "`n") | Should -Not -Match 'download_blob_dataset\.py'
        }

        It 'downloads the blob dataset when storage account and prefix are set' {
            $workspace = New-TestWorkspace -Name 'download-blob-dataset'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{
                BLOB_STORAGE_ACCOUNT   = 'roboticsstore'
                BLOB_STORAGE_CONTAINER = 'datasets'
                BLOB_PREFIX            = 'eval/run-001'
            }

            $invocation.Result.ExitCode | Should -Be 0
            ($invocation.Result.Calls -join "`n") | Should -Match 'python3 .*/download_blob_dataset\.py'
            Get-Content -Path $invocation.EnvSnapshot | Should -Contain ("DATASET_DIR={0}" -f $invocation.DatasetDir)
        }
    }

    Context 'MLflow configuration' {
        It 'fails fast when MLflow is enabled without the required Azure variables' {
            $workspace = New-TestWorkspace -Name 'mlflow-missing-azure'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{ MLFLOW_ENABLE = 'true' }

            $invocation.Result.ExitCode | Should -Not -Be 0
            $invocation.Result.StdErr | Should -Match 'ERROR: MLflow requires AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, and AZUREML_WORKSPACE_NAME'
            ($invocation.Result.Calls -join "`n") | Should -Not -Match 'bootstrap_mlflow\.py'
        }
    }

    Context 'builtin policy guard' {
        It 'fails when BUILTIN_POLICY=true without a local dataset directory' {
            $workspace = New-TestWorkspace -Name 'builtin-policy-missing-dataset'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{ BUILTIN_POLICY = 'true' }

            $invocation.Result.ExitCode | Should -Not -Be 0
            $invocation.Result.StdErr | Should -Match 'ERROR: --builtin-policy requires a local dataset; set --from-blob-dataset\.'
        }
    }

    Context 'model registration gating' {
        It 'skips registration with a warning when Azure variables are missing' {
            $workspace = New-TestWorkspace -Name 'register-model-missing-azure'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{ REGISTER_MODEL = 'registered-policy' }

            $invocation.Result.ExitCode | Should -Be 0
            $invocation.Result.StdOut | Should -Match 'Warning: Azure ML variables not set, skipping registration'
            ($invocation.Result.Calls -join "`n") | Should -Not -Match 'register_model\.py'
        }

        It 'registers the model when requested and Azure variables are set' {
            $workspace = New-TestWorkspace -Name 'register-model'
            $invocation = Invoke-LerobotEntry -Workspace $workspace -IncludeLockfile $true -EnvOverrides @{
                REGISTER_MODEL         = 'registered-policy'
                AZURE_SUBSCRIPTION_ID  = '00000000-0000-0000-0000-000000000000'
                AZURE_RESOURCE_GROUP   = 'rg-eval'
                AZUREML_WORKSPACE_NAME = 'aml-eval'
            }

            $invocation.Result.ExitCode | Should -Be 0
            ($invocation.Result.Calls -join "`n") | Should -Match 'python3 .*/register_model\.py'
        }
    }
}
