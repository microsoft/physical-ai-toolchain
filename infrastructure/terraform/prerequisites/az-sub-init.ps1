# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Initialize Azure subscription for Terraform.
.DESCRIPTION
    Sets ARM_SUBSCRIPTION_ID by logging into Azure and validating the session.
    Optionally targets a specific tenant.
.PARAMETER Tenant
    Azure AD tenant domain (e.g., your-tenant.onmicrosoft.com).
.EXAMPLE
    ./az-sub-init.ps1
.EXAMPLE
    ./az-sub-init.ps1 -Tenant 'your-tenant.onmicrosoft.com'
#>

[CmdletBinding()]
param(
    [Parameter()]
    [string]$Tenant
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Error 'Azure CLI (az) is required but not found. Install from https://aka.ms/install-azure-cli'
}

function Get-CurrentSubscriptionId {
    try {
        $result = az account show -o tsv --query 'id' 2>$null
        return $result
    }
    catch {
        return $null
    }
}

function Test-AzureToken {
    $null = az account get-access-token --query 'accessToken' -o tsv 2>$null
    return $LASTEXITCODE -eq 0
}

function Test-CorrectTenant([string]$Tenant) {
    if ([string]::IsNullOrEmpty($Tenant)) {
        return $true
    }

    $currentTenant = az rest --method get --url 'https://graph.microsoft.com/v1.0/domains' `
        --query 'value[?isDefault].id' -o tsv 2>$null

    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    return $Tenant -eq $currentTenant
}

function Invoke-AzureLogin([string]$Tenant) {
    Write-Host 'Logging into Azure...'

    if ($Tenant) {
        az login --tenant $Tenant
    }
    else {
        az login
    }

    if ($LASTEXITCODE -ne 0) {
        $msg = if ($Tenant) { "Failed to login to Azure with tenant $Tenant" } else { 'Failed to login to Azure' }
        Write-Error $msg
    }
}

$currentSubscriptionId = Get-CurrentSubscriptionId

if ($currentSubscriptionId -and -not (Test-AzureToken)) {
    Write-Host 'Azure CLI session expired. Re-authenticating...'
    $currentSubscriptionId = $null
}

if (-not $currentSubscriptionId -or -not (Test-CorrectTenant $Tenant)) {
    Invoke-AzureLogin $Tenant

    $currentSubscriptionId = Get-CurrentSubscriptionId
    if (-not $currentSubscriptionId) {
        Write-Error 'Login succeeded but could not retrieve subscription ID'
    }
}

$env:ARM_SUBSCRIPTION_ID = $currentSubscriptionId
Write-Host "ARM_SUBSCRIPTION_ID set to: $env:ARM_SUBSCRIPTION_ID"
