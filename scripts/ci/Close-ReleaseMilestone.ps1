#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Closes the GitHub milestone matching the published release tag.
.PARAMETER TagName
    Release tag and milestone title.
.PARAMETER Repository
    GitHub repository in owner/name format.
#>
[CmdletBinding()]
param(
    [string]$TagName = $env:TAG_NAME,
    [string]$Repository = $env:GITHUB_REPOSITORY
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "../lib/Modules/CIHelpers.psm1") -Force

function Close-ReleaseMilestoneCore {
    [CmdletBinding()]
    param(
        [string]$TagName,
        [string]$Repository
    )

    if ([string]::IsNullOrWhiteSpace($TagName)) {
        throw 'TAG_NAME is required.'
    }

    if ([string]::IsNullOrWhiteSpace($Repository)) {
        throw 'GITHUB_REPOSITORY is required.'
    }

    $milestones = & gh api --paginate "repos/$Repository/milestones?per_page=100&state=all" --jq '.[]' 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-CIAnnotation -Message "API call failed while querying milestones for $TagName" -Level Warning
        return 0
    }

    $match = @($milestones | ConvertFrom-Json) | Where-Object { $_.title -eq $TagName }
    if (-not $match) {
        Write-CIAnnotation -Message "No milestone found matching $TagName" -Level Warning
        return 0
    }

    $msNumber = $match.number
    $msState = $match.state

    if ($msState -eq 'closed') {
        Write-Output "Milestone $TagName (#$msNumber) already closed"
        return 0
    }

    $detail = & gh api "repos/$Repository/milestones/$msNumber" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-CIAnnotation -Message "API call failed while querying milestone details for $TagName (#$msNumber)" -Level Warning
        return 0
    }

    $openIssues = ($detail | ConvertFrom-Json).open_issues
    if ($openIssues -gt 0) {
        Write-CIAnnotation -Message "Milestone $TagName has $openIssues open issue(s) -- triage after closure" -Level Warning
    }

    $null = & gh api "repos/$Repository/milestones/$msNumber" --method PATCH -f state=closed 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-CIAnnotation -Message "Failed to close milestone $TagName (#$msNumber)" -Level Warning
        return 0
    }

    Write-Output "Closed milestone $TagName (#$msNumber)"
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Close-ReleaseMilestoneCore -TagName $TagName -Repository $Repository)
    }
    catch {
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
