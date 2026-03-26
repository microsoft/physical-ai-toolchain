<#
.SYNOPSIS
    Starts Azure resources (PostgreSQL and AKS) for daily operations.
.DESCRIPTION
    This runbook starts a PostgreSQL Flexible Server first, waits for it
    to become available, then starts an AKS cluster.
    Uses system-assigned managed identity for authentication.
.PARAMETER ResourceGroupName
    The resource group containing the resources.
.PARAMETER PostgresServerName
    The PostgreSQL Flexible Server name (optional, empty string to skip).
.PARAMETER AksClusterName
    The AKS cluster name.
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroupName,

    [Parameter(Mandatory = $false)]
    [string]$PostgresServerName,

    [Parameter(Mandatory = $true)]
    [string]$AksClusterName
)

$ErrorActionPreference = 'Stop'

Import-Module Az.Accounts -ErrorAction Stop
Import-Module Az.Aks -ErrorAction Stop
if ($PostgresServerName -and $PostgresServerName -ne "") {
    Import-Module Az.PostgreSql -ErrorAction Stop
}

Disable-AzContextAutosave -Scope Process | Out-Null

try {
    $postgresDisplay = if ($PostgresServerName -and $PostgresServerName -ne "") { $PostgresServerName } else { '(not configured)' }
    Write-Output "=========================================="
    Write-Output "Start Azure Resources Runbook"
    Write-Output "=========================================="
    Write-Output "Resource Group: $ResourceGroupName"
    Write-Output "PostgreSQL:     $postgresDisplay"
    Write-Output "AKS Cluster:    $AksClusterName"
    Write-Output ""

    Write-Output "Connecting to Azure using managed identity..."
    $AzureContext = (Connect-AzAccount -Identity).Context
    Set-AzContext -SubscriptionId $AzureContext.Subscription.Id | Out-Null
    Write-Output "Connected to subscription: $($AzureContext.Subscription.Name)"

    # Start PostgreSQL first (dependency for AKS workloads)
    if ($PostgresServerName -and $PostgresServerName -ne "") {
        Write-Output ""
        Write-Output "Checking PostgreSQL Flexible Server '$PostgresServerName' status..."
        $pgServer = Get-AzPostgreSqlFlexibleServer -ResourceGroupName $ResourceGroupName -Name $PostgresServerName

        if ($pgServer.State -eq "Stopped") {
            Write-Output "Starting PostgreSQL Flexible Server..."
            Start-AzPostgreSqlFlexibleServer -ResourceGroupName $ResourceGroupName -Name $PostgresServerName

            # Poll for Ready state with timeout
            $maxWaitSeconds = 300
            $pollInterval = 15
            $elapsed = 0
            while ($elapsed -lt $maxWaitSeconds) {
                $pgServer = Get-AzPostgreSqlFlexibleServer -ResourceGroupName $ResourceGroupName -Name $PostgresServerName
                if ($pgServer.State -eq "Ready") {
                    Write-Output "PostgreSQL server is now Ready."
                    break
                }
                Write-Output "PostgreSQL state: $($pgServer.State). Waiting..."
                Start-Sleep -Seconds $pollInterval
                $elapsed += $pollInterval
            }
            if ($pgServer.State -ne "Ready") {
                Write-Warning "PostgreSQL did not reach Ready state within $maxWaitSeconds seconds. Current state: $($pgServer.State)"
            }
        }
        elseif ($pgServer.State -eq "Ready") {
            Write-Output "PostgreSQL server is already running."
        }
        else {
            Write-Warning "PostgreSQL server is in unexpected state: $($pgServer.State)"
        }
    }

    # Start AKS cluster
    Write-Output ""
    Write-Output "Starting AKS cluster '$AksClusterName'..."
    Start-AzAksCluster -Name $AksClusterName -ResourceGroupName $ResourceGroupName
    Write-Output "AKS cluster start initiated."

    Write-Output ""
    Write-Output "=========================================="
    Write-Output "Resource startup completed successfully!"
    Write-Output "=========================================="
}
catch {
    Write-Error "Runbook failed: $_"
    throw
}
