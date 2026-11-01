#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for scripts/lib/platform-profile.sh through its CLI, scripts/platform-profile.sh.
# Each case sets every selector it depends on, so the caller's environment never changes the result.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command jq -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:CliPath = Join-Path $script:RepoRoot 'scripts/platform-profile.sh'
    $script:LibPath = Join-Path $script:RepoRoot 'scripts/lib/platform-profile.sh'
    $script:CommonPath = Join-Path $script:RepoRoot 'scripts/lib/common.sh'
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/platform-profile'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function Invoke-PlatformProfile {
        param(
            [string[]]$Arguments = @(),
            [hashtable]$ExtraEnv = @{}
        )

        $envVars = @{
            PLATFORM_PROFILE           = ''
            AZURE_STORAGE_ACCOUNT_NAME = ''
            OSMO_SERVICE_URL           = ''
            TRACKING_BACKEND           = ''
            MLFLOW_TRACKING_URI        = ''
            LOCAL_OSMO_CONFIG_DIR      = ''
            LOCAL_KUBECONFIG           = ''
            LOCAL_KUBE_CONTEXT         = ''
        }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        Invoke-BashEntryScript -ScriptPath $script:CliPath -WorkDir $workDir -EnvVars $envVars -ScriptArgs $Arguments
    }

    function ConvertFrom-ProfileLines {
        param([string]$Text)

        $values = @{}
        foreach ($line in ($Text -split "`r?`n")) {
            if ($line -match '^(PROFILE_[A-Z0-9_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
        $values
    }

    function Test-Ipv4InCidr {
        param([string]$Address, [string]$Cidr)

        & bash -c 'source "$1"; source "$2"; ipv4_in_cidr "$3" "$4"' _ $script:CommonPath $script:LibPath $Address $Cidr
        $LASTEXITCODE -eq 0
    }
}

Describe 'platform profile resolution' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Defaults to the Azure profile with AzureML tracking and Blob-staged code' {
        $result = Invoke-PlatformProfile -ExtraEnv @{ AZURE_STORAGE_ACCOUNT_NAME = 'example' }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_NAME | Should -Be 'azure'
        $values.PROFILE_TRACKING_BACKEND | Should -Be 'azureml'
        $values.PROFILE_OSMO_CODE_STORAGE_URL | Should -Be 'azure://example/osmo/osmo-code'
        $values.PROFILE_DATASETS_HOST_DIR | Should -BeNullOrEmpty
        $values.PROFILE_DATASETS_JOB_DIR | Should -BeNullOrEmpty
        $values.PROFILE_TASK_MEMORY | Should -BeNullOrEmpty
        $values.PROFILE_OSMO_CONFIG_DIR | Should -BeNullOrEmpty
        $values.PROFILE_MLFLOW_TRACKING_URI | Should -BeNullOrEmpty
        $values.ContainsKey('PROFILE_KUBECONFIG') | Should -BeTrue
        $values.PROFILE_KUBECONFIG | Should -BeNullOrEmpty
        $values.PROFILE_KUBE_CONTEXT | Should -BeNullOrEmpty
    }

    It 'Leaves Azure code staging unset when no storage account is known' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'azure')
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_OSMO_CODE_STORAGE_URL | Should -BeNullOrEmpty
    }

    It 'Resolves local services to pinned ClusterIPs and host directories' {
        $result = Invoke-PlatformProfile -ExtraEnv @{ PLATFORM_PROFILE = 'local'; LOCAL_DATA_ROOT = '/srv/example' }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_NAME | Should -Be 'local'
        $values.PROFILE_TRACKING_BACKEND | Should -Be 'mlflow'
        $values.PROFILE_OSMO_URL | Should -Be 'http://10.43.250.20'
        $values.PROFILE_OSMO_CODE_STORAGE_URL | Should -Be 's3://osmo/osmo-code'
        $values.PROFILE_S3_ENDPOINT | Should -Be 'http://10.43.250.10:9000'
        $values.PROFILE_MLFLOW_HOST_URI | Should -Be 'http://10.43.250.30:5000'
        $values.PROFILE_MLFLOW_TRACKING_URI | Should -Be 'http://mlflow.mlflow.svc.cluster.local:5000'
        $values.PROFILE_DATASETS_HOST_DIR | Should -Be '/srv/example/datasets'
        $values.PROFILE_DATASETS_JOB_DIR | Should -Be '/mnt/datasets'
        $values.PROFILE_CACHE_HOST_DIR | Should -Be '/srv/example/cache'
        $values.PROFILE_IMAGE_REGISTRY | Should -Be 'localhost:5000'
        $values.PROFILE_TASK_GPU | Should -Be '1'
    }

    It 'Points the OSMO CLI at the local login directory' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{ LOCAL_OSMO_CONFIG_DIR = '/tmp/osmo-local' }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_OSMO_CONFIG_DIR | Should -Be '/tmp/osmo-local'
    }

    It 'Targets the local cluster through its protected kubeconfig and context' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{
            LOCAL_KUBECONFIG   = '/tmp/example/kubeconfig.yaml'
            LOCAL_KUBE_CONTEXT = 'example-edge'
        }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_KUBECONFIG | Should -Be '/tmp/example/kubeconfig.yaml'
        $values.PROFILE_KUBE_CONTEXT | Should -Be 'example-edge'
    }

    It 'Lets TRACKING_BACKEND turn local tracking off' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{ TRACKING_BACKEND = 'None' }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_TRACKING_BACKEND | Should -Be 'none'
    }

    It 'Sends Azure profile jobs to a named MLflow server' {
        $result = Invoke-PlatformProfile -ExtraEnv @{ TRACKING_BACKEND = 'mlflow'; MLFLOW_TRACKING_URI = 'http://mlflow.example:5000' }
        $values = ConvertFrom-ProfileLines $result.StdOut

        $result.ExitCode | Should -Be 0
        $values.PROFILE_TRACKING_BACKEND | Should -Be 'mlflow'
        $values.PROFILE_MLFLOW_TRACKING_URI | Should -Be 'http://mlflow.example:5000'
    }

    It 'Rejects the MLflow backend without a tracking URI' {
        $result = Invoke-PlatformProfile -ExtraEnv @{ TRACKING_BACKEND = 'mlflow' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'TRACKING_BACKEND=mlflow needs MLFLOW_TRACKING_URI for the azure profile'
    }

    It 'Rejects an unknown tracking backend' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{ TRACKING_BACKEND = 'wandb' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'Unknown TRACKING_BACKEND: wandb'
    }

    It 'Prints the same values as one JSON object' {
        $lines = ConvertFrom-ProfileLines (Invoke-PlatformProfile -Arguments @('-p', 'local')).StdOut
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local', '--json')

        $result.ExitCode | Should -Be 0
        $json = $result.StdOut | ConvertFrom-Json -AsHashtable
        $json.Keys.Count | Should -Be $lines.Keys.Count
        foreach ($key in $lines.Keys) { $json[$key] | Should -Be $lines[$key] }
    }

    It 'Rejects an unknown profile' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'unknown')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'Unknown platform profile: unknown'
    }

    It 'Rejects a ClusterIP outside the K3s service CIDR' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{ LOCAL_MLFLOW_CLUSTER_IP = '10.99.0.1' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match '10\.99\.0\.1 is outside the K3s service CIDR 10\.43\.0\.0/16'
    }

    It 'Validates ClusterIPs against an overridden service CIDR' {
        $result = Invoke-PlatformProfile -Arguments @('--profile', 'local') -ExtraEnv @{ EDGE_K3S_SERVICE_CIDR = '10.96.0.0/12' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'outside the K3s service CIDR 10\.96\.0\.0/12'
    }
}

Describe 'platform_local_dataset_path' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:DataRoot = Join-Path $script:ArtifactsRoot ("data-" + [System.Guid]::NewGuid().ToString('N'))
        $infoPath = Join-Path $script:DataRoot 'datasets/so101/pick-place/meta/info.json'
        New-Item -ItemType Directory -Path (Split-Path $infoPath -Parent) -Force | Out-Null
        Set-Content -Path $infoPath -Value '{}' -Encoding utf8
        New-Item -ItemType Directory -Path (Join-Path $script:DataRoot 'datasets/empty') -Force | Out-Null

        function Resolve-LocalDataset {
            param([string]$ProfileName, [string]$Name)

            $output = & env "LOCAL_DATA_ROOT=$($script:DataRoot)" "REPO_ROOT=$($script:RepoRoot)" TRACKING_BACKEND= bash -c `
                'source "$1"; source "$2"; load_platform_profile "$3"; platform_local_dataset_path "$4"' `
                _ $script:CommonPath $script:LibPath $ProfileName $Name 2>&1
            [PSCustomObject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
        }
    }

    It 'Maps a local dataset to its read-only job mount' {
        $result = Resolve-LocalDataset -ProfileName 'local' -Name 'so101/pick-place'

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Be '/mnt/datasets/so101/pick-place'
    }

    It 'Rejects <Name> under the <ProfileName> profile' -ForEach @(
        @{ ProfileName = 'local'; Name = 'empty'; Message = 'No LeRobot dataset' }
        @{ ProfileName = 'local'; Name = '../outside'; Message = 'Invalid dataset name' }
        @{ ProfileName = 'local'; Name = 'so101/./pick-place'; Message = 'Invalid dataset name' }
        @{ ProfileName = 'azure'; Name = 'so101/pick-place'; Message = 'the azure profile does not' }
    ) {
        $result = Resolve-LocalDataset -ProfileName $ProfileName -Name $Name

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match $Message
    }
}

Describe 'ipv4_in_cidr' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Returns <Expected> for <Address> in <Cidr>' -ForEach @(
        @{ Address = '10.43.250.10'; Cidr = '10.43.0.0/16'; Expected = $true }
        @{ Address = '10.44.0.1'; Cidr = '10.43.0.0/16'; Expected = $false }
        @{ Address = '10.43.250.10'; Cidr = '10.43.250.10/32'; Expected = $true }
        @{ Address = '192.168.1.1'; Cidr = '0.0.0.0/0'; Expected = $true }
        @{ Address = 'not-an-ip'; Cidr = '10.43.0.0/16'; Expected = $false }
    ) {
        Test-Ipv4InCidr -Address $Address -Cidr $Cidr | Should -Be $Expected
    }
}
