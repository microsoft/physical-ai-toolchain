#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Stamps CHANGELOG.md front matter with today's UTC date and pushes the update.
.PARAMETER Path
    Changelog path relative to the repository root.
.PARAMETER Today
    Date string to write to ms.date.
.PARAMETER GitHubToken
    Token used to push the release PR branch.
#>
[CmdletBinding()]
param(
    [string]$Path = 'CHANGELOG.md',
    [string]$Today = [datetime]::UtcNow.ToString('yyyy-MM-dd'),
    [string]$GitHubToken = $env:GH_TOKEN
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Update-ChangelogMsDateCore {
    [CmdletBinding()]
    param(
        [string]$Path = 'CHANGELOG.md',
        [string]$Today = [datetime]::UtcNow.ToString('yyyy-MM-dd'),
        [string]$GitHubToken = $env:GH_TOKEN
    )

    $content = Get-Content -Raw -LiteralPath $Path
    $updated = [regex]::Replace($content, '(?m)^ms\.date:\s.*$', "ms.date: $Today")
    if ($updated -ceq $content) {
        Write-Host "CHANGELOG.md ms.date already set to $Today"
        return 0
    }

    if ([string]::IsNullOrWhiteSpace($GitHubToken)) {
        throw 'GH_TOKEN is required to push the changelog update.'
    }

    if ([string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY)) {
        throw 'GITHUB_REPOSITORY is required to push the changelog update.'
    }

    Set-Content -LiteralPath $Path -Value $updated -NoNewline -Encoding utf8NoBOM
    & git config user.name 'github-actions[bot]'
    & git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
    & git remote set-url origin "https://x-access-token:${GitHubToken}@github.com/$env:GITHUB_REPOSITORY.git"
    & git add $Path
    & git commit -m 'chore: stamp ms.date in CHANGELOG.md'
    & git push
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (Update-ChangelogMsDateCore -Path $Path -Today $Today -GitHubToken $GitHubToken)
    }
    catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
