#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for infrastructure/setup/connect-bastion.sh using stubbed CLI binaries.

BeforeDiscovery {
    $script:ToolsPresent = [bool]((Get-Command bash -ErrorAction SilentlyContinue) -and (Get-Command jq -ErrorAction SilentlyContinue))
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../infrastructure/setup/connect-bastion.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/connect-bastion'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    $script:SubId = '00000000-0000-0000-0000-000000000001'
    $script:ResourceGroup = 'rg-robotics-dev-001'
    $script:BastionName = 'bas-robotics-dev-001'
    $script:JumpboxId = "/subscriptions/$script:SubId/resourceGroups/$script:ResourceGroup/providers/Microsoft.Compute/virtualMachines/vm-jb-robotics-dev-001"

    $script:TerraformStub = @'
if [[ "$1" == "output" && "$2" == "-json" ]]; then
  cat <<'JSON'
{
  "connection_info": {
    "value": {
      "resource_group_name": "rg-tf-dev-001",
      "bastion_name": "bas-tf-dev-001",
      "bastion_dns_name": "bst-example.bastion.azure.com",
      "tunneling_enabled": true,
      "jumpbox_id": "/subscriptions/00000000-0000-0000-0000-000000000002/resourceGroups/rg-tf-dev-001/providers/Microsoft.Compute/virtualMachines/vm-jb-tf-dev-001",
      "jumpbox_name": "vm-jb-tf-dev-001",
      "jumpbox_private_ip": "10.0.1.10",
      "jumpbox_admin_username": "azureuser"
    }
  }
}
JSON
  exit 0
fi
exit 1
'@

    $script:AzStub = @'
case "$*" in
  "extension show --name bastion"*) exit 0 ;;
  *) exit 0 ;;
esac
'@

    function Invoke-ConnectBastion {
        param(
            [string[]]$Arguments = @(),
            [hashtable]$ExtraEnv = @{},
            [hashtable]$Stubs = @{ az = $script:AzStub; terraform = $script:TerraformStub }
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workDir -Force | Out-Null

        # Clear inherited ambient env vars for deterministic isolation
        $vars = @{
            AZURE_SUBSCRIPTION_ID  = ''
            AZURE_RESOURCE_GROUP   = ''
            BASTION_NAME           = ''
            JUMPBOX_RESOURCE_ID    = ''
            JUMPBOX_VM_NAME        = ''
            JUMPBOX_ADMIN_USERNAME = ''
        }
        foreach ($key in $ExtraEnv.Keys) { $vars[$key] = $ExtraEnv[$key] }

        Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workDir -EnvVars $vars `
            -Stubs $Stubs -ScriptArgs $Arguments
    }

    function New-ProtectedKeyFile {
        param([string]$Directory)
        $keyPath = Join-Path $Directory 'test_id_ed25519'
        Set-Content -Path $keyPath -Value 'synthetic-key-placeholder'
        & chmod 600 $keyPath
        return $keyPath
    }
}

AfterAll {
    if (Test-Path $script:ArtifactsRoot) {
        Remove-Item -Path $script:ArtifactsRoot -Recurse -Force
    }
}

Describe 'connect-bastion.sh help and configuration resolution' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Displays usage and commands with --help' {
        $result = Invoke-ConnectBastion -Arguments @('--help')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Usage: connect-bastion\.sh'
        $result.StdOut | Should -Match '\bssh\b'
        $result.StdOut | Should -Match '\btunnel\b'
        $result.StdOut | Should -Match '\bproxy\b'
    }

    It 'Previews configuration from explicit CLI flags without invoking az or terraform' {
        $result = Invoke-ConnectBastion -Arguments @(
            'ssh',
            '--subscription-id', $script:SubId,
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--config-preview'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Command:\s+ssh'
        $result.StdOut | Should -Match "Resource Group:\s+$script:ResourceGroup"
        $result.StdOut | Should -Match "Bastion Host:\s+$script:BastionName"
        $result.StdOut | Should -Match 'Auth Type:\s+AAD'
        @($result.Calls | Where-Object { $_ -match '^(az|terraform) ' }).Count | Should -Be 0
    }

    It 'Resolves Bastion outputs from --tf-dir terraform output -json' {
        $tfDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tfDir -Force | Out-Null
        Set-Content -Path (Join-Path $tfDir 'terraform.tfstate') -Value '{}'

        $result = Invoke-ConnectBastion -Arguments @('ssh', '--tf-dir', $tfDir, '--config-preview')

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'Resource Group:\s+rg-tf-dev-001'
        $result.StdOut | Should -Match 'Bastion Host:\s+bas-tf-dev-001'
        $result.StdOut | Should -Match 'vm-jb-tf-dev-001'
        @($result.Calls | Where-Object { $_ -match '^terraform output -json' }).Count | Should -Be 1
    }

    It 'Derives target resource ID from subscription, resource group, and --jumpbox-name' {
        $result = Invoke-ConnectBastion -Arguments @(
            'ssh',
            '--subscription-id', $script:SubId,
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--jumpbox-name', 'vm-jb-robotics-dev-001',
            '--config-preview'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match ([regex]::Escape($script:JumpboxId))
    }
}

Describe 'connect-bastion.sh dry-run command construction' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Builds AAD SSH command in --dry-run mode without invoking az' {
        $result = Invoke-ConnectBastion -Arguments @(
            'ssh',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--dry-run'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'az network bastion ssh'
        $result.StdOut | Should -Match '--auth-type AAD'
        @($result.Calls | Where-Object { $_ -match '^az ' }).Count | Should -Be 0
    }

    It 'Builds ssh-key command and validates protected key permissions' {
        $keyDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $keyDir -Force | Out-Null
        $keyPath = New-ProtectedKeyFile -Directory $keyDir

        $result = Invoke-ConnectBastion -Arguments @(
            'ssh',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--auth-type', 'ssh-key',
            '--username', 'azureuser',
            '--ssh-key', $keyPath,
            '--dry-run'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match '--auth-type ssh-key'
        $result.StdOut | Should -Match '--username azureuser'
        $result.StdOut | Should -Match '--ssh-key '
    }

    It 'Builds tunnel command with custom resource and local ports' {
        $result = Invoke-ConnectBastion -Arguments @(
            'tunnel',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--resource-port', '443',
            '--local-port', '5443',
            '--dry-run'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'az network bastion tunnel'
        $result.StdOut | Should -Match '--resource-port 443'
        $result.StdOut | Should -Match '--port 5443'
    }

    It 'Builds proxy tunnel and SSH port-forward commands for multiple --forward rules' {
        $keyDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $keyDir -Force | Out-Null
        $keyPath = New-ProtectedKeyFile -Directory $keyDir

        $result = Invoke-ConnectBastion -Arguments @(
            'proxy',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--username', 'azureuser',
            '--ssh-key', $keyPath,
            '--local-port', '50022',
            '--forward', '8080:osmo.internal.example:80',
            '--forward', '8000:dataviewer.internal.example:8000',
            '--dry-run'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match 'az network bastion tunnel.*--port 50022'
        $result.StdOut | Should -Match 'ssh -N.*-p 50022.*-L 8080:osmo\.internal\.example:80.*-L 8000:dataviewer\.internal\.example:8000.*azureuser@127\.0\.0\.1'
    }
}

Describe 'connect-bastion.sh input validation' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Rejects <Case>' -ForEach @(
        @{
            Case      = 'missing required resource group'
            Arguments = @('ssh', '--bastion-name', 'bas-test', '--target-resource-id', '/sub/vm', '--dry-run')
            Message   = 'Resource group is required'
        }
        @{
            Case      = 'invalid local port'
            Arguments = @('tunnel', '--resource-group', 'rg-test', '--bastion-name', 'bas-test', '--target-resource-id', '/sub/vm', '--local-port', '70000', '--dry-run')
            Message   = 'valid TCP port'
        }
        @{
            Case      = 'invalid auth-type'
            Arguments = @('ssh', '--resource-group', 'rg-test', '--bastion-name', 'bas-test', '--target-resource-id', '/sub/vm', '--auth-type', 'password', '--dry-run')
            Message   = "--auth-type must be 'AAD' or 'ssh-key'"
        }
        @{
            Case      = 'malformed --forward specification'
            Arguments = @('proxy', '--resource-group', 'rg-test', '--bastion-name', 'bas-test', '--target-resource-id', '/sub/vm', '--username', 'azureuser', '--forward', '8080:badspec', '--dry-run')
            Message   = 'Invalid --forward specification'
        }
        @{
            Case      = 'proxy without --forward'
            Arguments = @('proxy', '--resource-group', 'rg-test', '--bastion-name', 'bas-test', '--target-resource-id', '/sub/vm', '--username', 'azureuser', '--dry-run')
            Message   = 'At least one --forward'
        }
        @{
            Case      = 'missing value for option'
            Arguments = @('ssh', '--bastion-name')
            Message   = 'requires a value'
        }
        @{
            Case      = 'unknown option'
            Arguments = @('ssh', '--nonexistent-flag')
            Message   = 'Unknown option'
        }
    ) {
        $result = Invoke-ConnectBastion -Arguments $Arguments

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ([regex]::Escape($Message))
    }

    It 'Accepts leading-zero port numbers in base 10 without octal errors' {
        $result = Invoke-ConnectBastion -Arguments @(
            'tunnel',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--resource-port', '080',
            '--local-port', '08080',
            '--dry-run'
        )

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $result.StdOut | Should -Match '--resource-port 080'
        $result.StdOut | Should -Match '--port 08080'
    }

    It 'Rejects a group-readable SSH private key file' {
        $keyDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $keyDir -Force | Out-Null
        $badKey = Join-Path $keyDir 'bad_key'
        Set-Content -Path $badKey -Value 'synthetic-key-placeholder'
        & chmod 644 $badKey

        $result = Invoke-ConnectBastion -Arguments @(
            'ssh',
            '--resource-group', $script:ResourceGroup,
            '--bastion-name', $script:BastionName,
            '--target-resource-id', $script:JumpboxId,
            '--auth-type', 'ssh-key',
            '--username', 'azureuser',
            '--ssh-key', $badKey,
            '--dry-run'
        )

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'not be accessible by group or other users'
    }
}
