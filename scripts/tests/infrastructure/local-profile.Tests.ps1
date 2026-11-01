#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for the infrastructure/local entry points through their configuration previews,
# which never contact a cluster or change the host. Tool checks pass through stubs.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:LocalDir = (Resolve-Path (Join-Path $PSScriptRoot '../../../infrastructure/local')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/local-profile'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function Invoke-LocalScript {
        param(
            [Parameter(Mandatory)][string]$RelativePath,
            [string[]]$Arguments = @(),
            [hashtable]$ExtraEnv = @{}
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $stubs = @{}
        foreach ($tool in 'kubectl', 'helm', 'curl', 'osmo', 'envsubst', 'openssl', 'ss', 'sudo', 'docker') { $stubs[$tool] = 'exit 0' }
        Invoke-BashEntryScript -ScriptPath (Join-Path $script:LocalDir $RelativePath) -WorkDir $workDir `
            -EnvVars $ExtraEnv -Stubs $stubs -ScriptArgs $Arguments
    }
}

Describe '01-deploy-platform-services.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Shows the pinned endpoints, loopback registry, databases, and buckets' {
        $result = Invoke-LocalScript -RelativePath '01-deploy-platform-services.sh' -Arguments @('--config-preview')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'S3 Endpoint:\s+http://10\.43\.250\.10:9000'
        $result.StdOut | Should -Match 'Registry Address:\s+127\.0\.0\.1:5000 \(host network\)'
        $result.StdOut | Should -Match 'Databases:\s+osmo, mlflow'
        $result.StdOut | Should -Match 'Buckets:\s+osmo, mlflow'
        $result.StdOut | Should -Match 'PostgreSQL:\s+postgres:16@sha256:[0-9a-f]{64}'
        ($result.Calls | Where-Object { $_ -match '^kubectl' }).Count | Should -Be 0
    }

    It 'Rejects a registry host that is not on the loopback' {
        $result = Invoke-LocalScript -RelativePath '01-deploy-platform-services.sh' -Arguments @('--config-preview') `
            -ExtraEnv @{ LOCAL_REGISTRY_HOST = 'registry.example.com:5000' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'LOCAL_REGISTRY_HOST must be a loopback host:port'
    }
}

Describe 'cleanup/uninstall-local.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Keeps volumes, credentials, caches, and datasets by default' {
        $result = Invoke-LocalScript -RelativePath 'cleanup/uninstall-local.sh' -Arguments @('--config-preview') `
            -ExtraEnv @{ LOCAL_DATA_ROOT = '/srv/example' }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Remove Dataviewer:\s+dataviewer'
        $result.StdOut | Should -Match 'Remove Platform Services:\s+local-platform'
        $result.StdOut | Should -Match 'Volumes, Secrets:\s+kept'
        $result.StdOut | Should -Match 'Cache Directory:\s+kept'
        $result.StdOut | Should -Match 'Datasets:\s+kept: /srv/example/datasets'
    }

    It 'Describes the purge and cache prune when requested' {
        $result = Invoke-LocalScript -RelativePath 'cleanup/uninstall-local.sh' `
            -Arguments @('--config-preview', '--purge-data', '--prune-cache') -ExtraEnv @{ LOCAL_DATA_ROOT = '/srv/example' }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Volumes, Secrets:\s+deleted with their namespaces'
        $result.StdOut | Should -Match 'Cache Directory:\s+emptied: /srv/example/cache'
    }
}

Describe '00-prepare-host.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Plans a missing data root and the profile kubeconfig without touching K3s' {
        $dataRoot = Join-Path $script:ArtifactsRoot ('missing-' + [System.Guid]::NewGuid().ToString('N'))
        $result = Invoke-LocalScript -RelativePath '00-prepare-host.sh' -Arguments @('--config-preview', '--skip-k3s') `
            -ExtraEnv @{ LOCAL_DATA_ROOT = $dataRoot; LOCAL_KUBE_CONTEXT = 'example-context' }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'missing \(will be created\)'
        $result.StdOut | Should -Match 'Context:\s+example-context'
        $result.StdOut | Should -Match 'K3s Step:\s+skipped'
        $result.StdOut | Should -Match 'GPU Step:\s+05-enable-k3s-gpu\.sh'
        Test-Path $dataRoot | Should -BeFalse
    }
}

Describe '02-deploy-osmo.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Shows the gateway URL, pinned charts, pool, and task mounts' {
        $result = Invoke-LocalScript -RelativePath '02-deploy-osmo.sh' -Arguments @('--config-preview')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Service URL:\s+http://10\.43\.250\.20'
        $result.StdOut | Should -Match 'OSMO Charts:\s+service, backend-operator 1\.3\.1 \(SHA-256 pinned\)'
        $result.StdOut | Should -Match 'Pool:\s+default \(platforms default, gpu_platform\)'
        $result.StdOut | Should -Match 'Task Mounts:\s+/mnt/datasets \(read-only\), /mnt/cache'
        $result.StdOut | Should -Match 'Smoke Test:\s+smoke-test-osmo\.sh'
        $result.StdOut | Should -Match 'Azure Identity:\s+none; workflow pods stay off Azure'
        ($result.Calls | Where-Object { $_ -match '^(kubectl|helm)' }).Count | Should -Be 0
    }

    It 'Lets workflow pods use Azure only with a managed identity client ID' {
        $enabled = Invoke-LocalScript -RelativePath '02-deploy-osmo.sh' `
            -Arguments @('--config-preview', '--azure-client-id', '00000000-0000-0000-0000-000000000000')
        $enabled.ExitCode | Should -Be 0
        $enabled.StdOut | Should -Match 'Azure Identity:\s+workload identity 00000000-0000-0000-0000-000000000000 for system:serviceaccount:osmo-workflows:osmo-workflow'

        $invalid = Invoke-LocalScript -RelativePath '02-deploy-osmo.sh' -Arguments @('--config-preview', '--azure-client-id', 'not-a-guid')
        $invalid.ExitCode | Should -Not -Be 0
        $invalid.StdErr | Should -Match 'managed identity client ID \(GUID\)'
    }
}

Describe 'OSMO workflow identity templates' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Renders use "<Enabled>" and client ID "<ClientId>" into the pool template and ServiceAccount' -ForEach @(
        @{ ClientId = ''; Enabled = 'false' }
        @{ ClientId = '11111111-2222-3333-4444-555555555555'; Enabled = 'true' }
    ) {
        $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
        $render = @'
set -euo pipefail
REPO_ROOT="$1"
source "$REPO_ROOT/scripts/lib/common.sh"
source "$REPO_ROOT/data-pipeline/setup/defaults.conf"
source "$REPO_ROOT/infrastructure/local/defaults.conf"
source "$REPO_ROOT/scripts/lib/local-cluster.sh"
LOCAL_OSMO_AZURE_CLIENT_ID="$2"
LOCAL_OSMO_WORKLOAD_IDENTITY="$3"
LOCAL_REGISTRY_HOSTNAME=localhost
local_export_profile
local_render_manifest "$REPO_ROOT/infrastructure/local/manifests/osmo-workflow-sa.yaml"
local_render_manifest "$REPO_ROOT/infrastructure/local/values/osmo-control-plane-local.yaml"
'@
        $output = (& bash -c $render render $repoRoot $ClientId $Enabled 2>&1) -join "`n"

        $LASTEXITCODE | Should -Be 0
        $output | Should -Match ('azure\.workload\.identity/client-id: "' + [regex]::Escape($ClientId) + '"')
        $output | Should -Match ('azure\.workload\.identity/use: "' + $Enabled + '"')
    }
}

Describe 'connect-osmo-cli.sh login guard' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:ConfigDir = Join-Path $script:ArtifactsRoot ('osmo-config-' + [System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:ConfigDir -Force | Out-Null
        Set-Content -Path (Join-Path $script:ConfigDir 'login.yaml') -Value "url: http://osmo.example.com`nusername: admin"
    }

    It 'Reports the current login in the preview' {
        $result = Invoke-LocalScript -RelativePath 'connect-osmo-cli.sh' -Arguments @('--config-preview') `
            -ExtraEnv @{ LOCAL_OSMO_CONFIG_DIR = $script:ConfigDir }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Current Login:\s+http://osmo\.example\.com'
    }

    It 'Refuses to replace a login to another OSMO service' {
        $result = Invoke-LocalScript -RelativePath 'connect-osmo-cli.sh' -ExtraEnv @{ LOCAL_OSMO_CONFIG_DIR = $script:ConfigDir }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'logged in to http://osmo\.example\.com; rerun with --force'
        ($result.Calls | Where-Object { $_ -match '^osmo' }).Count | Should -Be 0
    }
}

Describe 'smoke-test-osmo.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Plans a unique object-store prefix and the local registry image' {
        $result = Invoke-LocalScript -RelativePath 'smoke-test-osmo.sh' -Arguments @('--config-preview')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Data Prefix:\s+s3://osmo/smoke/smoke-\d{8}T\d{6}Z'
        $result.StdOut | Should -Match 'Registry Image:\s+localhost:5000/physical-ai-local/registry-smoke-test'
    }
}

Describe '03-deploy-mlflow.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Shows both tracking URIs, the database, and the proxied artifact store' {
        $result = Invoke-LocalScript -RelativePath '03-deploy-mlflow.sh' -Arguments @('--config-preview')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Tracking URI \(pods\):\s+http://mlflow\.mlflow\.svc\.cluster\.local:5000'
        $result.StdOut | Should -Match 'Tracking URI \(host\):\s+http://10\.43\.250\.30:5000'
        $result.StdOut | Should -Match 'Backend Store:\s+postgres\.local-platform \(database mlflow\)'
        $result.StdOut | Should -Match 'Artifacts:\s+s3://mlflow/artifacts, proxied by the server'
        $result.StdOut | Should -Match 'Image:\s+ghcr\.io/mlflow/mlflow:v3\.16\.1-full@sha256:[0-9a-f]{64}'
    }
}

Describe '04-deploy-dataviewer.sh preview' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:DataRoot = Join-Path $script:ArtifactsRoot ('data-' + [System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $script:DataRoot 'datasets') -Force | Out-Null
    }

    It 'Runs on local datasets as their owner with authentication off and no network exposure' {
        $result = Invoke-LocalScript -RelativePath '04-deploy-dataviewer.sh' -Arguments @('--config-preview') `
            -ExtraEnv @{ LOCAL_DATA_ROOT = $script:DataRoot }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match ('Datasets:\s+' + [regex]::Escape((Join-Path $script:DataRoot 'datasets')) + ' \(uid \d+, gid \d+\), read-write')
        $result.StdOut | Should -Match '(?m)Storage:\s+local datasets\s*$'
        $result.StdOut | Should -Match 'Authentication:\s+disabled'
        $result.StdOut | Should -Match 'Backend Image:\s+localhost:5000/physical-ai-local/dataviewer-backend:sha-\S+ \(extras azure,analysis,export,auth\)'
        $result.StdOut | Should -Match 'ClusterIP URL:\s+http://10\.43\.250\.40/'
        $result.StdOut | Should -Match 'kubectl proxy URL:\s+http://127\.0\.0\.1:8001/api/v1/namespaces/dataviewer/services/dataviewer:http/proxy/'
        ($result.Calls | Where-Object { $_ -match '^(kubectl|docker)' }).Count | Should -Be 0
    }

    It 'Adds Azure Blob datasets only with a workload identity client ID' {
        $missing = Invoke-LocalScript -RelativePath '04-deploy-dataviewer.sh' `
            -Arguments @('--config-preview', '--azure-storage-account', 'examplestore') -ExtraEnv @{ LOCAL_DATA_ROOT = $script:DataRoot }
        $missing.ExitCode | Should -Not -Be 0
        $missing.StdErr | Should -Match 'needs --azure-client-id'

        $result = Invoke-LocalScript -RelativePath '04-deploy-dataviewer.sh' `
            -Arguments @('--config-preview', '--azure-storage-account', 'examplestore', '--azure-client-id', '00000000-0000-0000-0000-000000000000') `
            -ExtraEnv @{ LOCAL_DATA_ROOT = $script:DataRoot }
        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Storage:\s+local datasets and Azure Blob examplestore/datasets \(annotations in annotations\)'
        $result.StdOut | Should -Match 'Authentication:\s+disabled'
    }
}
