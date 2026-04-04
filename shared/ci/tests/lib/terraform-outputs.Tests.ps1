#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# terraform-outputs.Tests.ps1
#
# Purpose: Pester tests for Terraform output reading and accessor functions
# Author: Edge AI Team

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    . $PSScriptRoot/../../../lib/terraform-outputs.ps1
}

Describe 'Read-TerraformOutputs' -Tag 'Unit' {
    BeforeAll {
        function terraform { }
    }

    BeforeEach {
        $Script:TfOutput = $null
    }

    It 'Returns false when directory does not exist' {
        $result = Read-TerraformOutputs -TerraformDir (Join-Path $TestDrive 'nonexistent')
        $result | Should -BeFalse
    }

    It 'Returns false when no state file or .terraform directory exists' {
        $emptyDir = Join-Path $TestDrive "empty-tf-$(New-Guid)"
        New-Item -Path $emptyDir -ItemType Directory -Force | Out-Null

        $result = Read-TerraformOutputs -TerraformDir $emptyDir
        $result | Should -BeFalse
    }

    Context 'With state file present' {
        BeforeEach {
            $script:TfDir = Join-Path $TestDrive "tf-$(New-Guid)"
            New-Item -Path $script:TfDir -ItemType Directory -Force | Out-Null
            New-Item -Path (Join-Path $script:TfDir 'terraform.tfstate') -ItemType File -Force | Out-Null
        }

        It 'Returns true and populates TfOutput on valid JSON' {
            Mock terraform { return '{"resource_group":{"value":{"name":"rg-test"}}}' }

            $result = Read-TerraformOutputs -TerraformDir $script:TfDir
            $result | Should -BeTrue
            $Script:TfOutput | Should -Not -BeNullOrEmpty
        }

        It 'Returns true with null TfOutput when terraform output is empty' {
            Mock terraform { return '' }

            $result = Read-TerraformOutputs -TerraformDir $script:TfDir
            $result | Should -BeTrue
            $Script:TfOutput | Should -BeNullOrEmpty
        }

        It 'Returns false when terraform output throws' {
            Mock terraform { throw 'terraform error' }

            $result = Read-TerraformOutputs -TerraformDir $script:TfDir
            $result | Should -BeFalse
        }
    }

    Context 'With .terraform directory present' {
        BeforeEach {
            $script:TfDir = Join-Path $TestDrive "tf-$(New-Guid)"
            New-Item -Path $script:TfDir -ItemType Directory -Force | Out-Null
            New-Item -Path (Join-Path $script:TfDir '.terraform') -ItemType Directory -Force | Out-Null
        }

        It 'Returns true on valid JSON' {
            Mock terraform { return '{"aks_cluster":{"value":{"name":"aks-test"}}}' }

            $result = Read-TerraformOutputs -TerraformDir $script:TfDir
            $result | Should -BeTrue
        }
    }
}

Describe 'Get-TerraformOutput' -Tag 'Unit' {
    BeforeAll {
        $Script:TfOutput = [PSCustomObject]@{
            resource_group = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = 'rg-test' }
            }
            empty_value = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = '' }
            }
        }
    }

    It 'Returns value for valid dot-separated path' {
        Get-TerraformOutput 'resource_group.value.name' | Should -Be 'rg-test'
    }

    It 'Returns default for missing path segment' {
        Get-TerraformOutput 'nonexistent.value.name' | Should -Be ''
    }

    It 'Returns custom default for missing path' {
        Get-TerraformOutput 'nonexistent.value' 'fallback' | Should -Be 'fallback'
    }

    It 'Returns default for empty value' {
        Get-TerraformOutput 'empty_value.value.name' 'default-val' | Should -Be 'default-val'
    }

    Context 'When TfOutput is null' {
        BeforeAll {
            $script:PrevTfOutput = $Script:TfOutput
            $Script:TfOutput = $null
        }

        AfterAll {
            $Script:TfOutput = $script:PrevTfOutput
        }

        It 'Returns default' {
            Get-TerraformOutput 'resource_group.value.name' 'fallback' | Should -Be 'fallback'
        }
    }
}

Describe 'Get-SubscriptionId' -Tag 'Unit' {
    BeforeAll {
        function az { }
    }

    Context 'When az account show succeeds' {
        BeforeAll {
            Mock az {
                $global:LASTEXITCODE = 0
                return 'sub-id-123'
            }
        }

        It 'Returns the subscription ID' {
            Get-SubscriptionId | Should -Be 'sub-id-123'
        }
    }

    Context 'When az account show fails' {
        BeforeAll {
            Mock az { $global:LASTEXITCODE = 1 }
        }

        It 'Returns empty string' {
            Get-SubscriptionId | Should -Be ''
        }
    }
}

Describe 'Accessor functions' -Tag 'Unit' {
    BeforeAll {
        $Script:TfOutput = [PSCustomObject]@{
            resource_group = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = 'rg-robotics' }
            }
            aks_cluster = [PSCustomObject]@{
                value = [PSCustomObject]@{
                    name = 'aks-mytestcluster'
                    id   = '/subscriptions/00000000/resourceGroups/rg/providers/Microsoft.ContainerService/managedClusters/aks-mytestcluster'
                }
            }
            azureml_workspace = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = 'ws-robotics' }
            }
            ml_workload_identity = [PSCustomObject]@{
                value = [PSCustomObject]@{ id = 'identity-client-id' }
            }
            key_vault_name = [PSCustomObject]@{
                value = 'kv-robotics'
            }
            container_registry = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = 'crrobotics' }
            }
            storage_account = [PSCustomObject]@{
                value = [PSCustomObject]@{ name = 'sarobotics' }
            }
            postgresql_connection_info = [PSCustomObject]@{
                value = [PSCustomObject]@{
                    fqdn           = 'pg.example.com'
                    admin_username = 'pgadmin'
                }
            }
            managed_redis_connection_info = [PSCustomObject]@{
                value = [PSCustomObject]@{
                    hostname = 'redis.example.com'
                    port     = '6380'
                }
            }
        }
    }

    It 'Get-ResourceGroup returns resource group name' {
        Get-ResourceGroup | Should -Be 'rg-robotics'
    }

    It 'Get-AksClusterName returns AKS cluster name' {
        Get-AksClusterName | Should -Be 'aks-mytestcluster'
    }

    It 'Get-AksClusterId returns AKS cluster resource ID' {
        Get-AksClusterId | Should -Match 'managedClusters/aks-mytestcluster'
    }

    It 'Get-AzureMLWorkspace returns workspace name' {
        Get-AzureMLWorkspace | Should -Be 'ws-robotics'
    }

    It 'Get-MLIdentityId returns identity client ID' {
        Get-MLIdentityId | Should -Be 'identity-client-id'
    }

    It 'Get-KeyVaultName returns key vault name' {
        Get-KeyVaultName | Should -Be 'kv-robotics'
    }

    It 'Get-ContainerRegistry returns container registry name' {
        Get-ContainerRegistry | Should -Be 'crrobotics'
    }

    It 'Get-StorageAccount returns storage account name' {
        Get-StorageAccount | Should -Be 'sarobotics'
    }

    It 'Get-PostgreSQLFqdn returns PostgreSQL FQDN' {
        Get-PostgreSQLFqdn | Should -Be 'pg.example.com'
    }

    It 'Get-PostgreSQLAdmin returns admin username' {
        Get-PostgreSQLAdmin | Should -Be 'pgadmin'
    }

    It 'Get-RedisHostname returns Redis hostname' {
        Get-RedisHostname | Should -Be 'redis.example.com'
    }

    It 'Get-RedisPort returns Redis port' {
        Get-RedisPort | Should -Be '6380'
    }
}

Describe 'Get-ComputeTarget' -Tag 'Unit' {
    Context 'When AKS name has aks- prefix and exceeds 12 chars' {
        BeforeAll {
            $Script:TfOutput = [PSCustomObject]@{
                aks_cluster = [PSCustomObject]@{
                    value = [PSCustomObject]@{ name = 'aks-mytestcluster' }
                }
            }
        }

        It 'Strips prefix, truncates to 12 chars, prepends k8s-' {
            Get-ComputeTarget | Should -Be 'k8s-mytestcluste'
        }
    }

    Context 'When AKS name has aks- prefix and is short' {
        BeforeAll {
            $Script:TfOutput = [PSCustomObject]@{
                aks_cluster = [PSCustomObject]@{
                    value = [PSCustomObject]@{ name = 'aks-dev' }
                }
            }
        }

        It 'Strips prefix and prepends k8s-' {
            Get-ComputeTarget | Should -Be 'k8s-dev'
        }
    }

    Context 'When AKS name has no aks- prefix' {
        BeforeAll {
            $Script:TfOutput = [PSCustomObject]@{
                aks_cluster = [PSCustomObject]@{
                    value = [PSCustomObject]@{ name = 'mycluster' }
                }
            }
        }

        It 'Keeps full name and prepends k8s-' {
            Get-ComputeTarget | Should -Be 'k8s-mycluster'
        }
    }

    Context 'When AKS cluster name is empty' {
        BeforeAll {
            $Script:TfOutput = [PSCustomObject]@{
                aks_cluster = [PSCustomObject]@{
                    value = [PSCustomObject]@{ name = '' }
                }
            }
        }

        It 'Returns empty string' {
            Get-ComputeTarget | Should -Be ''
        }
    }

    Context 'When TfOutput has no AKS cluster' {
        BeforeAll {
            $Script:TfOutput = [PSCustomObject]@{}
        }

        It 'Returns empty string' {
            Get-ComputeTarget | Should -Be ''
        }
    }
}
