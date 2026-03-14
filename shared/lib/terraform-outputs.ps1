# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Shared library for reading Terraform outputs from deploy/001-iac
# Dot-source this file and call Read-TerraformOutputs to populate $Script:TfOutput

#Requires -Version 7.0

$Script:TfOutput = $null
$Script:DefaultTerraformDir = Join-Path $PSScriptRoot '..' '..' 'deploy' '001-iac'

<#
.SYNOPSIS
Reads Terraform outputs from the specified directory.

.PARAMETER TerraformDir
Path to the Terraform directory. Defaults to deploy/001-iac relative to this script.

.OUTPUTS
System.Boolean
#>
function Read-TerraformOutputs {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $false)]
        [string]$TerraformDir = $Script:DefaultTerraformDir
    )

    if (-not (Test-Path -Path $TerraformDir -PathType Container)) {
        return $false
    }

    $statePath = Join-Path $TerraformDir 'terraform.tfstate'
    $terraformPath = Join-Path $TerraformDir '.terraform'

    if (-not (Test-Path $statePath) -and -not (Test-Path $terraformPath -PathType Container)) {
        return $false
    }

    Push-Location $TerraformDir
    try {
        $json = & terraform output -json 2>$null | Out-String
        if (-not $json) {
            return $false
        }
        $Script:TfOutput = $json | ConvertFrom-Json
        return $true
    }
    catch {
        return $false
    }
    finally {
        Pop-Location
    }
}

<#
.SYNOPSIS
Gets a value from Terraform outputs using a dot-separated property path.

.PARAMETER Path
Dot-separated property path (e.g. 'resource_group.value.name').

.PARAMETER Default
Default value when the property is not found.

.OUTPUTS
System.String
#>
function Get-TerraformOutput {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$Path,

        [Parameter(Mandatory = $false, Position = 1)]
        [string]$Default = ''
    )

    if (-not $Script:TfOutput) {
        return $Default
    }

    $current = $Script:TfOutput
    foreach ($segment in $Path.Split('.')) {
        if ($null -eq $current -or -not ($current.PSObject.Properties.Name -contains $segment)) {
            return $Default
        }
        $current = $current.$segment
    }

    $value = [string]$current
    if ([string]::IsNullOrEmpty($value)) { return $Default }
    return $value
}

<#
.SYNOPSIS
Gets the current Azure subscription ID via az CLI.
#>
function Get-SubscriptionId {
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $result = az account show --query id -o tsv 2>$null
    if ($LASTEXITCODE -ne 0) {
        return ''
    }
    return $result
}

<#
.SYNOPSIS
Gets the resource group name from Terraform outputs.
#>
function Get-ResourceGroup {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'resource_group.value.name'
}

<#
.SYNOPSIS
Gets the AKS cluster name from Terraform outputs.
#>
function Get-AksClusterName {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'aks_cluster.value.name'
}

<#
.SYNOPSIS
Gets the AKS cluster resource ID from Terraform outputs.
#>
function Get-AksClusterId {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'aks_cluster.value.id'
}

<#
.SYNOPSIS
Gets the Azure ML workspace name from Terraform outputs.
#>
function Get-AzureMLWorkspace {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'azureml_workspace.value.name'
}

<#
.SYNOPSIS
Gets the ML workload identity resource ID from Terraform outputs.
#>
function Get-MLIdentityId {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'ml_workload_identity.value.id'
}

<#
.SYNOPSIS
Gets the Key Vault name from Terraform outputs.
#>
function Get-KeyVaultName {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'key_vault_name.value'
}

<#
.SYNOPSIS
Gets the container registry name from Terraform outputs.
#>
function Get-ContainerRegistry {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'container_registry.value.name'
}

<#
.SYNOPSIS
Gets the storage account name from Terraform outputs.
#>
function Get-StorageAccount {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'storage_account.value.name'
}

<#
.SYNOPSIS
Gets the Kubernetes compute target name derived from the AKS cluster name.
#>
function Get-ComputeTarget {
    [CmdletBinding()]
    [OutputType([string])]
    param()

    $aksName = Get-AksClusterName
    if ($aksName) {
        $suffix = $aksName -replace '^aks-', ''
        $suffix = $suffix.Substring(0, [Math]::Min(12, $suffix.Length))
        return "k8s-$suffix"
    }
    return ''
}

<#
.SYNOPSIS
Gets the PostgreSQL server FQDN from Terraform outputs.
#>
function Get-PostgreSQLFqdn {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'postgresql_connection_info.value.fqdn'
}

<#
.SYNOPSIS
Gets the PostgreSQL admin username from Terraform outputs.
#>
function Get-PostgreSQLAdmin {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'postgresql_connection_info.value.admin_username'
}

<#
.SYNOPSIS
Gets the Redis hostname from Terraform outputs.
#>
function Get-RedisHostname {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'managed_redis_connection_info.value.hostname'
}

<#
.SYNOPSIS
Gets the Redis port from Terraform outputs.
#>
function Get-RedisPort {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    return Get-TerraformOutput 'managed_redis_connection_info.value.port'
}
