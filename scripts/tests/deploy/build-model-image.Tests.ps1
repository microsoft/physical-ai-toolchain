#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Source and target selection for build-model-image.sh. Stubbed az, docker, curl, and uv record their
# calls, so the tests never contact Azure, MLflow, or a registry.

BeforeDiscovery {
    $script:BashPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:BuildImage = (Resolve-Path (Join-Path $PSScriptRoot '../../../fleet-deployment/setup/build-model-image.sh')).Path
    $script:Digest = 'sha256:' + ('a' * 64)
    $script:AzureArguments = @(
        '--tf-dir', 'none', '--aml-subscription', 'aml-sub', '--aml-tenant', 'tenant-a', '--aml-rg', 'aml-rg',
        '--aml-workspace', 'aml-ws', '--acr-subscription', 'acr-sub', '--acr-tenant', 'tenant-a',
        '--acr-name', 'example', '--verify-mode', 'none'
    )

    function New-BuildWorkspace {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
        $bin = Join-Path $root 'bin'
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        Set-Content -Path (Join-Path $bin 'az') -Encoding utf8 -Value @'
#!/usr/bin/env bash
printf 'az %s\n' "$*" >> "$BUILD_LOG"
'@
        Set-Content -Path (Join-Path $bin 'docker') -Encoding utf8 -Value @"
#!/usr/bin/env bash
printf 'docker %s\n' "`$*" >> "`$BUILD_LOG"
while [[ `$# -gt 0 ]]; do
  if [[ "`$1" == "--metadata-file" ]]; then
    printf '{"containerimage.digest": "$($script:Digest)"}\n' > "`$2"
  fi
  shift
done
"@
        Set-Content -Path (Join-Path $bin 'curl') -Encoding utf8 -Value @'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$BUILD_LOG"
printf '{"registered_model": {"latest_versions": [{"version": "2"}, {"version": "5"}]}}\n'
'@
        Set-Content -Path (Join-Path $bin 'uv') -Encoding utf8 -Value @'
#!/usr/bin/env bash
printf 'uv %s\n' "$*" >> "$BUILD_LOG"
destination=""
while [[ $# -gt 0 ]]; do
  [[ "$1" == "--dst-path" ]] && destination="$2"
  shift
done
mkdir -p "$destination/probe"
printf '{}\n' > "$destination/probe/config.json"
printf '%s\n' "$destination/probe"
'@
        & chmod +x (Join-Path $bin 'az') (Join-Path $bin 'docker') (Join-Path $bin 'curl') (Join-Path $bin 'uv')
        $checkpoint = Join-Path $root 'checkpoint'
        New-Item -ItemType Directory -Path $checkpoint -Force | Out-Null
        Set-Content -Path (Join-Path $checkpoint 'model.pt') -Value 'weights'
        @{ Root = $root; Bin = $bin; Log = Join-Path $root 'build.log'; Checkpoint = $checkpoint }
    }

    function Invoke-BuildImage {
        param(
            [Parameter(Mandatory)][hashtable]$Workspace,
            [string[]]$Arguments,
            [hashtable]$Environment = @{}
        )

        $saved = @{}
        $names = @('PATH', 'BUILD_LOG', 'PLATFORM_PROFILE', 'TRACKING_BACKEND') + @($Environment.Keys)
        foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
        try {
            $env:PATH = "$($Workspace.Bin):$($saved['PATH'])"
            $env:BUILD_LOG = $Workspace.Log
            $env:PLATFORM_PROFILE = $null
            $env:TRACKING_BACKEND = $null
            foreach ($entry in $Environment.GetEnumerator()) {
                [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value)
            }
            $output = & bash $script:BuildImage @Arguments 2>&1
            @{
                ExitCode = $LASTEXITCODE
                Output = $output -join "`n"
                Calls = @(if (Test-Path $Workspace.Log) { Get-Content $Workspace.Log })
            }
        }
        finally {
            foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
        }
    }
}

Describe 'build-model-image.sh' -Tag 'Unit' -Skip:(-not $script:BashPresent) {
    BeforeEach {
        $script:Workspace = New-BuildWorkspace
    }

    AfterEach {
        Remove-Item -Recurse -Force $script:Workspace.Root -ErrorAction SilentlyContinue
    }

    It 'keeps the AzureML model to ACR route by default' {
        $result = Invoke-BuildImage -Workspace $script:Workspace `
            -Arguments (@('--model-name', 'policy', '--model-version', '3', '--config-preview') + $script:AzureArguments)

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'Platform Profile:\s+azure'
        $result.Output | Should -Match 'Model Source:\s+azureml'
        $result.Output | Should -Match 'Topology:\s+same-tenant'
        $result.Output | Should -Match 'Image Reference:\s+example\.azurecr\.io/policy:3-sha-'
    }

    It 'requires the AzureML workspace for the azure profile' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @('--tf-dir', 'none', '--model-name', 'policy', '--config-preview')

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'AML subscription not supplied'
    }

    It 'previews the local profile from the MLflow registry without Azure' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @('--profile', 'local', '--model-name', 'policy', '--config-preview')

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'Model Source:\s+mlflow'
        $result.Output | Should -Match 'MLflow Server:\s+http://10\.43\.250\.30:5000'
        $result.Output | Should -Match 'Registry:\s+localhost:5000 \(local profile\)'
        $result.Output | Should -Match 'Verify Mode:\s+none \(local registry; signing skipped\)'
        $result.Output | Should -Not -Match 'ACR'
        $result.Calls | Should -BeNullOrEmpty
    }

    It 'pushes the latest MLflow version to the local registry unsigned' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @('--profile', 'local', '--model-name', 'policy')

        $result.ExitCode | Should -Be 0
        $result.Calls | Where-Object { $_ -match '^az ' } | Should -BeNullOrEmpty
        $result.Calls | Where-Object { $_ -match '^uv run .* mlflow artifacts download --artifact-uri models:/policy/5 ' } | Should -Not -BeNullOrEmpty
        $build = $result.Calls | Where-Object { $_ -match '^docker buildx build' }
        $build | Should -Match '--tag localhost:5000/policy:5-sha-'
        $build | Should -Match 'BASE_IMAGE=scratch'
        $build | Should -Match 'MODEL_SOURCE=mlflow'
        $result.Output | Should -Match 'Signing skipped: only this host''s K3s pulls from localhost:5000'
        $result.Output | Should -Match ('Image \(digest\):\s+localhost:5000/policy@' + [regex]::Escape($script:Digest))
        $result.Output | Should -Not -Match 'attest-image\.sh'
    }

    It 'packages a checkpoint directory as a runnable carrier' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @(
            '--profile', 'local', '--model-name', 'policy', '--model-path', $script:Workspace.Checkpoint,
            '--base-image', 'example.com/runtime:1')

        $result.ExitCode | Should -Be 0
        $result.Calls | Where-Object { $_ -match '^(az|uv|curl) ' } | Should -BeNullOrEmpty
        $build = $result.Calls | Where-Object { $_ -match '^docker buildx build' }
        $build | Should -Match '--tag localhost:5000/policy:local-sha-'
        $build | Should -Match 'BASE_IMAGE=example\.com/runtime:1'
        $build | Should -Match 'MODEL_SOURCE=path'
    }

    It 'pushes a checkpoint directory to ACR without contacting AzureML' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments (@(
                '--model-name', 'policy', '--model-path', $script:Workspace.Checkpoint) + $script:AzureArguments)

        $result.ExitCode | Should -Be 0
        $result.Calls | Where-Object { $_ -match '^az ml ' } | Should -BeNullOrEmpty
        $result.Calls | Where-Object { $_ -match '^az acr login --name example' } | Should -Not -BeNullOrEmpty
        ($result.Calls | Where-Object { $_ -match '^docker buildx build' }) | Should -Match '--tag example\.azurecr\.io/policy:local-sha-'
        $result.Output | Should -Match 'attest-image\.sh --image ''example\.azurecr\.io/policy@'
    }

    It 'rejects a checkpoint directory for a registry source' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @(
            '--profile', 'local', '--model-name', 'policy', '--model-source', 'mlflow', '--model-path', $script:Workspace.Checkpoint, '--config-preview')

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'use it with --model-source path'
    }

    It 'needs a checkpoint directory when tracking is off' {
        $result = Invoke-BuildImage -Workspace $script:Workspace -Arguments @('--profile', 'local', '--model-name', 'policy', '--config-preview') `
            -Environment @{ TRACKING_BACKEND = 'none' }

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'TRACKING_BACKEND=none has no model registry; pass --model-path'
    }
}
