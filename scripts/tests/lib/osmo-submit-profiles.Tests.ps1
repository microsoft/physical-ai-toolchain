#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for the platform profile in every OSMO submit script, through --config-preview.
# Terraform and Azure CLI lookups are stubbed empty, so the local profile proves it needs no Azure value
# and the Azure profile proves today's defaults, with each case setting every selector it depends on.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command python3 -ErrorAction SilentlyContinue)
    $script:ArchiveToolsPresent = $script:ToolsPresent -and [bool](Get-Command zip -ErrorAction SilentlyContinue) -and
        [bool](Get-Command unzip -ErrorAction SilentlyContinue)

    $script:SubmitScripts = @(
        @{
            Name         = 'RL training'
            Path         = 'training/rl/scripts/submit-osmo-training.sh'
            LocalArgs    = @('--task', 'Isaac-Cartpole-v0')
            AzureArgs    = @('--task', 'Isaac-Cartpole-v0')
            AzureCpu     = '30'
            ShowsStaging = $true
        }
        @{
            Name         = 'RL dataset training'
            Path         = 'training/rl/scripts/submit-osmo-dataset-training.sh'
            LocalArgs    = @('--task', 'Isaac-Cartpole-v0')
            AzureArgs    = @('--task', 'Isaac-Cartpole-v0')
            AzureCpu     = '30'
            ShowsStaging = $true
        }
        @{
            Name         = 'LeRobot training'
            Path         = 'training/il/scripts/submit-osmo-lerobot-training.sh'
            LocalArgs    = @('--local-dataset', 'so101/pick')
            AzureArgs    = @('--dataset-repo-id', 'org/dataset')
            AzureCpu     = '8'
            ShowsStaging = $true
        }
        @{
            Name         = 'GR00T fine-tuning'
            Path         = 'training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh'
            LocalArgs    = @('--local-dataset', 'so101/pick', '--data-config', 'example')
            AzureArgs    = @('--blob-url', 'https://example.blob.core.windows.net/datasets/pick', '--data-config', 'example')
            AzureCpu     = '14'
            ShowsStaging = $false
        }
        @{
            Name         = 'Isaac Lab SiL evaluation'
            Path         = 'evaluation/sil/scripts/submit-osmo-eval.sh'
            LocalArgs    = @('--checkpoint-uri', 'models:/cartpole/1')
            AzureArgs    = @('--checkpoint-uri', 'models:/cartpole/1')
            AzureCpu     = '20'
            ShowsStaging = $true
        }
        @{
            Name         = 'LeRobot evaluation'
            Path         = 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh'
            LocalArgs    = @('--builtin-policy', '--local-dataset', 'so101/pick')
            AzureArgs    = @('--policy-repo-id', 'policy:3', '--from-blob-dataset', '--storage-account', 'example', '--blob-prefix', 'pick')
            AzureCpu     = '4'
            ShowsStaging = $true
        }
        @{
            Name         = 'VLM judge'
            Path         = 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh'
            LocalArgs    = @('--local-dataset', 'so101/pick')
            AzureArgs    = @('--input-dataset', 'cartpole-eval', '--instruction', 'Keep the pole upright')
            AzureCpu     = '8'
            ShowsStaging = $true
        }
    )
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/osmo-submit-profiles'
    $script:DataRoot = Join-Path $script:ArtifactsRoot 'data-root'
    $infoPath = Join-Path $script:DataRoot 'datasets/so101/pick/meta/info.json'
    New-Item -ItemType Directory -Path (Split-Path $infoPath -Parent) -Force | Out-Null
    Set-Content -Path $infoPath -Value '{}' -Encoding utf8

    # Environment shared by every submission: the local profile's data root and OSMO config, and no Azure values.
    function Get-SubmitEnvironment {
        param([hashtable]$ExtraEnv = @{})

        $envVars = @{
            PLATFORM_PROFILE           = ''
            TRACKING_BACKEND           = ''
            MLFLOW_TRACKING_URI        = ''
            LOCAL_DATA_ROOT            = $script:DataRoot
            LOCAL_OSMO_CONFIG_DIR      = (Join-Path $script:ArtifactsRoot 'osmo-config')
            AZURE_SUBSCRIPTION_ID      = ''
            AZURE_RESOURCE_GROUP       = ''
            AZUREML_WORKSPACE_NAME     = ''
            AZURE_STORAGE_ACCOUNT_NAME = ''
            OSMO_CPU                   = ''
            OSMO_MEMORY                = ''
            OSMO_STORAGE               = ''
            OSMO_GPU                   = ''
            OSMO_PLATFORM              = ''
            PLATFORM                   = ''
        }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }
        $envVars
    }

    function Invoke-SubmitPreview {
        param(
            [Parameter(Mandatory)][string]$ScriptPath,
            [Parameter(Mandatory)][string[]]$Arguments,
            [hashtable]$ExtraEnv = @{}
        )

        $envVars = Get-SubmitEnvironment -ExtraEnv $ExtraEnv
        $stubs = @{
            'terraform' = 'exit 1'
            'az'        = 'exit 1'
            'osmo'      = 'echo "osmo must not run during a preview" >&2; exit 97'
            'zip'       = 'exit 0'
            'rsync'     = 'exit 0'
        }
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        Invoke-BashEntryScript -ScriptPath (Join-Path $script:RepoRoot $ScriptPath) -WorkDir $workDir `
            -EnvVars $envVars -Stubs $stubs -ScriptArgs ($Arguments + '--config-preview')
    }

    # Run a submission past its preview under the local profile. The OSMO CLI is a logging stub that reports a
    # login to the profile's gateway and accepts uploads and submissions; the code archive is built for real.
    function Invoke-SubmitWithStubbedOsmo {
        param(
            [Parameter(Mandatory)][string]$ScriptPath,
            [Parameter(Mandatory)][string[]]$Arguments,
            [hashtable]$ExtraEnv = @{}
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $configDir = Join-Path $workDir 'osmo-config'
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        $gatewayIp = '10.43.250.20'
        Set-Content -Path (Join-Path $configDir 'login.yaml') -Value "url: http://$gatewayIp"
        $submitEnv = @{
            LOCAL_OSMO_CONFIG_DIR         = $configDir
            LOCAL_OSMO_GATEWAY_CLUSTER_IP = $gatewayIp
        }
        foreach ($key in $ExtraEnv.Keys) { $submitEnv[$key] = $ExtraEnv[$key] }
        $envVars = Get-SubmitEnvironment -ExtraEnv $submitEnv
        $stubs = @{
            'terraform' = 'exit 1'
            'az'        = 'exit 1'
            'osmo'      = 'case "$1 $2" in "data list") exit 1 ;; "data upload") [[ -z "${OSMO_ARCHIVE_CAPTURE:-}" ]] || cp "$4" "$OSMO_ARCHIVE_CAPTURE" ;; "workflow submit") echo "Workflow ID - test-workflow" ;; esac; exit 0'
        }
        Invoke-BashEntryScript -ScriptPath (Join-Path $script:RepoRoot $ScriptPath) -WorkDir $workDir `
            -EnvVars $envVars -Stubs $stubs -ScriptArgs $Arguments
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
}

Describe 'OSMO submit scripts under the local profile' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It '<Name> resolves local services, resources, and datasets without Azure values' -ForEach $script:SubmitScripts {
        $result = Invoke-SubmitPreview -ScriptPath $Path -Arguments (@('--profile', 'local') + $LocalArgs)

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Platform Profile:\s+local'
        $result.StdOut | Should -Match 'OSMO Service:\s+http://10\.43\.250\.20'
        $result.StdOut | Should -Match 'CPU:\s+12'
        $result.StdOut | Should -Match 'Memory:\s+40Gi'
        $result.StdOut | Should -Not -Match 'Subscription:'
        $result.StdOut | Should -Not -Match 'azure://'
        if ($ShowsStaging) {
            $result.StdOut | Should -Match 'Code Storage:\s+s3://osmo/osmo-code'
            $result.StdOut | Should -Match 'Tracking Backend:\s+mlflow'
            $result.StdOut | Should -Match 'MLflow URI:\s+http://mlflow\.mlflow\.svc\.cluster\.local:5000'
        }
    }
}

Describe 'OSMO submit scripts under the azure profile' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It '<Name> keeps its Azure defaults' -ForEach $script:SubmitScripts {
        $result = Invoke-SubmitPreview -ScriptPath $Path -Arguments $AzureArgs -ExtraEnv @{
            AZURE_STORAGE_ACCOUNT_NAME = 'example'
            AZURE_SUBSCRIPTION_ID      = '00000000-0000-0000-0000-000000000000'
            AZURE_RESOURCE_GROUP       = 'rg-example'
            AZUREML_WORKSPACE_NAME     = 'mlw-example'
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Platform Profile:\s+azure'
        $result.StdOut | Should -Match "CPU:\s+$AzureCpu\b"
        if ($ShowsStaging) {
            $result.StdOut | Should -Match 'Code Storage:\s+azure://example/osmo/osmo-code'
            $result.StdOut | Should -Match 'Tracking Backend:\s+azureml'
        }
    }
}

Describe 'Tracking backend selection at submission' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Rejects checkpoint registration when tracking is off' {
        $result = Invoke-SubmitPreview -ScriptPath 'training/il/scripts/submit-osmo-lerobot-training.sh' `
            -Arguments @('--profile', 'local', '--local-dataset', 'so101/pick', '--register-checkpoint', 'policy') `
            -ExtraEnv @{ TRACKING_BACKEND = 'none' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '--register-checkpoint needs a tracking backend'
    }

    It 'Skips automatic RL registration when tracking is off' {
        $result = Invoke-SubmitPreview -ScriptPath 'training/rl/scripts/submit-osmo-training.sh' `
            -Arguments @('--profile', 'local', '--task', 'Isaac-Cartpole-v0') -ExtraEnv @{ TRACKING_BACKEND = 'none' }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Tracking Backend:\s+none'
        $result.StdOut | Should -Match 'Register Model:\s+<none>'
    }

    It 'Requires a local dataset to exist in the data root' {
        $result = Invoke-SubmitPreview -ScriptPath 'training/il/scripts/submit-osmo-lerobot-training.sh' `
            -Arguments @('--profile', 'local', '--local-dataset', 'so101/missing')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'No LeRobot dataset at'
    }
}

Describe 'Evaluation policy sources at submission' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:Carrier = 'localhost:5000/policy@sha256:' + ('a' * 64)
    }

    It 'Runs an Isaac Lab carrier as the task image and gates the policy it holds' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-eval.sh' -Arguments @(
            '--profile', 'local', '--policy-image', $script:Carrier, '--framework', 'rsl_rl',
            '--eval-episodes', '20', '--success-threshold', '0.8', '--output-dataset', 'cartpole-eval')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Checkpoint URI:\s+/policy'
        $result.StdOut | Should -Match ('Image:\s+' + [regex]::Escape($script:Carrier))
        $result.StdOut | Should -Match 'Framework:\s+rsl_rl'
        $result.StdOut | Should -Match 'Eval Episodes:\s+20'
        $result.StdOut | Should -Match 'Success Threshold:\s+0\.8'
        $result.StdOut | Should -Match 'Output Dataset:\s+training/cartpole-eval'
    }

    It 'Rejects an Isaac Lab evaluation without a policy' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-eval.sh' -Arguments @('--profile', 'local')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '--checkpoint-uri or --policy-image is required'
    }

    It 'Names the Isaac Lab evaluation experiment, or derives it from the task' {
        $named = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-eval.sh' -Arguments @(
            '--profile', 'local', '--checkpoint-uri', 'models:/cartpole/1', '--experiment-name', 'cartpole-gate')
        $derived = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-eval.sh' -Arguments @(
            '--profile', 'local', '--checkpoint-uri', 'models:/cartpole/1', '--task', 'Isaac-Cartpole-Direct-v0')

        $named.ExitCode | Should -Be 0 -Because $named.StdErr
        $named.StdOut | Should -Match 'Experiment:\s+cartpole-gate'
        $derived.ExitCode | Should -Be 0 -Because $derived.StdErr
        $derived.StdOut | Should -Match 'Experiment:\s+inference-Isaac-Cartpole-Direct-v0'
    }

    It 'Evaluates a LeRobot policy from the tracking backend registry' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' -Arguments @(
            '--profile', 'local', '--policy-uri', 'models:/so101-act/2', '--local-dataset', 'so101/pick')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Policy:\s+models:/so101-act/2'
        $result.StdOut | Should -Match 'LeRobot Project:\s+training/il/lerobot'
    }

    It 'Uses the pinned SmolVLA base as the untrained baseline from the VLA lock' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' -Arguments @(
            '--profile', 'local', '-p', 'smolvla', '--builtin-policy', '--local-dataset', 'so101/pick',
            '--rename-map', '{"observation.images.external": "observation.images.camera1"}')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Policy:\s+lerobot/smolvla_base@[0-9a-f]{40}'
        $result.StdOut | Should -Match 'Policy Source:\s+untrained baseline \(pinned base, unadapted\)'
        $result.StdOut | Should -Match 'LeRobot Project:\s+training/vla/lerobot'
        $result.StdOut | Should -Match 'Rename Map:\s+\{"observation\.images\.external": "observation\.images\.camera1"\}'
    }

    It 'Runs a LeRobot carrier as the task image' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' -Arguments @(
            '--profile', 'local', '--policy-image', $script:Carrier, '--local-dataset', 'so101/pick')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Policy:\s+/policy'
        $result.StdOut | Should -Match ('Image:\s+' + [regex]::Escape($script:Carrier))
    }

    It 'Rejects two policy sources' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' -Arguments @(
            '--profile', 'local', '--policy-image', $script:Carrier, '--policy-uri', 'models:/x/1', '--local-dataset', 'so101/pick')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '--policy-image cannot be combined with another policy source'
    }

    It 'Needs a tracking backend for a registry policy' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' -Arguments @(
            '--profile', 'local', '--policy-uri', 'models:/x/1', '--local-dataset', 'so101/pick') -ExtraEnv @{ TRACKING_BACKEND = 'none' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '--policy-uri needs a tracking backend'
    }

    Context 'Azure ML registry policy with logging and registration off' {
        BeforeAll {
            $script:PolicyUriArgs = @('--profile', 'local', '--policy-uri', 'models:/so101-act/2', '--local-dataset', 'so101/pick')
            $script:NoLogging = @{ TRACKING_BACKEND = 'azureml'; MLFLOW_ENABLE = ''; REGISTER_MODEL = '' }
            $script:Workspace = @{
                AZURE_SUBSCRIPTION_ID  = '00000000-0000-0000-0000-000000000000'
                AZURE_RESOURCE_GROUP   = 'rg-example'
                AZUREML_WORKSPACE_NAME = 'mlw-example'
            }
        }

        It 'Forwards the Azure workspace the policy download needs' -Skip:(-not $script:ArchiveToolsPresent) {
            $result = Invoke-SubmitWithStubbedOsmo -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' `
                -Arguments $script:PolicyUriArgs -ExtraEnv ($script:NoLogging + $script:Workspace)

            $result.ExitCode | Should -Be 0 -Because $result.StdErr
            $submit = @($result.Calls | Where-Object { $_ -match '^osmo workflow submit ' })
            $submit.Count | Should -Be 1
            $submit[0] | Should -Match ' policy_uri=models:/so101-act/2( |$)'
            $submit[0] | Should -Match ' azure_subscription_id=00000000-0000-0000-0000-000000000000( |$)'
            $submit[0] | Should -Match ' azure_resource_group=rg-example( |$)'
            $submit[0] | Should -Match ' azure_workspace_name=mlw-example( |$)'
            $submit[0] | Should -Not -Match 'mlflow_enable=true'
        }

        It 'Shows the Azure workspace in the preview' {
            $result = Invoke-SubmitPreview -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' `
                -Arguments $script:PolicyUriArgs -ExtraEnv ($script:NoLogging + $script:Workspace)

            $result.ExitCode | Should -Be 0 -Because $result.StdErr
            $result.StdOut | Should -Match 'Workspace:\s+mlw-example'
        }

        It 'Stops before submitting when the workspace is not set' {
            $result = Invoke-SubmitWithStubbedOsmo -ScriptPath 'evaluation/sil/scripts/submit-osmo-lerobot-eval.sh' `
                -Arguments $script:PolicyUriArgs -ExtraEnv $script:NoLogging

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'Azure subscription ID required for the model registry, MLflow, or --policy-uri'
            @($result.Calls | Where-Object { $_ -match '^osmo (workflow submit|data upload) ' }).Count | Should -Be 0
        }
    }
}

Describe 'VLM judge inputs at submission' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Stages the judge project and every path dependency its lock installs' -Skip:(-not $script:ArchiveToolsPresent) {
        $capture = Join-Path $script:ArtifactsRoot ('judge-payload-' + [guid]::NewGuid().ToString('N') + '.zip')
        $result = Invoke-SubmitWithStubbedOsmo -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' `
            -Arguments @('--profile', 'local', '--local-dataset', 'so101/pick') -ExtraEnv @{ OSMO_ARCHIVE_CAPTURE = $capture }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($capture)
        try {
            $names = @($archive.Entries | ForEach-Object { $_.FullName })
        }
        finally {
            $archive.Dispose()
        }
        $names | Should -Contain 'pyproject.toml'
        $names | Should -Contain 'uv.lock'
        foreach ($member in @('evaluation/vlm_judge', 'data-management/dataset-io',
                'data-management/curation', 'data-management/viewer/backend')) {
            $names | Should -Contain "$member/pyproject.toml"
            $names | Should -Not -Contain "$member/uv.lock"
        }
        $names | Should -Contain 'evaluation/vlm_judge/service.py'
        $names | Should -Contain 'data-management/dataset-io/physical_ai_dataset_io/lerobot.py'
        @($names | Where-Object { $_ -match '(^|/)(build|\.venv|\.env)(/|$|\.)' }).Count | Should -Be 0
    }

    It 'Sends the instruction base64-encoded, so quotes, backslashes, and a leading dash reach the workflow' -Skip:(-not $script:ArchiveToolsPresent) {
        $instruction = '-place the "red" cube \ in the bin'
        $result = Invoke-SubmitWithStubbedOsmo -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--instruction', $instruction)

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $submit = @($result.Calls | Where-Object { $_ -match '^osmo workflow submit ' })
        $submit.Count | Should -Be 1
        $submit[0] -match ' instruction_b64=([A-Za-z0-9+/=]+)( |$)' | Should -BeTrue
        [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Matches[1])) |
            Should -BeExactly $instruction
    }
    It 'Pins the default judge model and judges a local dataset' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--limit', '1', '--output-dataset', 'so101-verdicts')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Input:\s+local dataset /mnt/datasets/so101/pick'
        $result.StdOut | Should -Match 'Model:\s+Qwen/Qwen3-VL-4B-Instruct@[0-9a-f]{40}'
        $result.StdOut | Should -Match 'Output Dataset:\s+training/so101-verdicts'
    }

    It 'Requires exactly one input' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--input-url', 's3://osmo/rollouts')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'Choose exactly one input'
    }

    It 'Requires a pinned revision for another Hub model' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--model-id', 'Qwen/Qwen3-VL-8B-Instruct')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '--model-revision'
    }

    It 'Loads a model directory in the task without a revision' {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--model-id', '/mnt/cache/models/qwen3-vl')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Model:\s+/mnt/cache/models/qwen3-vl\s*\r?\n'
    }

    It 'Rejects a revision for a model directory, which the judge would refuse at run time' -ForEach @(
        @{ ModelId = '/mnt/cache/models/qwen3-vl' }
        @{ ModelId = './models/qwen3-vl' }
    ) {
        $result = Invoke-SubmitPreview -ScriptPath 'evaluation/vlm_judge/scripts/submit-osmo-judge.sh' -Arguments @(
            '--profile', 'local', '--local-dataset', 'so101/pick', '--model-id', $ModelId,
            '--model-revision', 'ebb281ec70b05090aa6165b016eac8ec08e71b17')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ([regex]::Escape("--model-revision does not apply to the model directory $ModelId"))
    }
}
