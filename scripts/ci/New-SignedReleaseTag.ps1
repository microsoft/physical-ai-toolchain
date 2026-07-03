#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Creates the signed release tag required for draft release discoverability.
.PARAMETER TagName
    Release tag to create.
.PARAMETER GitHubToken
    Token used to push the tag.
.PARAMETER Repository
    GitHub repository in owner/name format.
.PARAMETER CommitSha
    Commit SHA to tag.
#>
[CmdletBinding()]
param(
    [string]$TagName = $env:TAG_NAME,
    [string]$GitHubToken = $env:GH_TOKEN,
    [string]$Repository = $env:GITHUB_REPOSITORY,
    [string]$CommitSha = $env:GITHUB_SHA
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function New-SignedReleaseTagCore {
    [CmdletBinding()]
    param(
        [string]$TagName,
        [string]$GitHubToken,
        [string]$Repository,
        [string]$CommitSha
    )

    if ([string]::IsNullOrWhiteSpace($TagName)) {
        throw 'TAG_NAME is required.'
    }

    if ([string]::IsNullOrWhiteSpace($GitHubToken)) {
        throw 'GH_TOKEN is required.'
    }

    if ([string]::IsNullOrWhiteSpace($Repository)) {
        throw 'GITHUB_REPOSITORY is required.'
    }

    if ([string]::IsNullOrWhiteSpace($CommitSha)) {
        throw 'GITHUB_SHA is required.'
    }

    & git config --global gpg.format x509
    & git config --global gpg.x509.program gitsign
    & git config --global tag.gpgSign true
    & git config user.name 'github-actions[bot]'
    & git config user.email '41898282+github-actions[bot]@users.noreply.github.com'

    & git fetch --tags --force

    $existingTag = & git tag --list $TagName
    if ($existingTag) {
        Write-Output "Git tag $TagName already exists"
        return 0
    }

    & git remote set-url origin "https://x-access-token:${GitHubToken}@github.com/$Repository.git"
    & git tag -s $TagName $CommitSha -m "Release $TagName"
    & git push origin "refs/tags/$TagName"
    Write-Output "Created signed git tag $TagName -> $CommitSha"
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (New-SignedReleaseTagCore -TagName $TagName -GitHubToken $GitHubToken -Repository $Repository -CommitSha $CommitSha)
    }
    catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
