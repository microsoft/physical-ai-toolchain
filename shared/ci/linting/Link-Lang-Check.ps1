#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
<#
.SYNOPSIS
    Language Path Link Checker and Fixer

.DESCRIPTION
    Finds and optionally fixes URLs in git-tracked text files that contain
    the language path segment 'en-us'. Helps maintain links that work regardless
    of user language settings by removing unnecessary language path segments.

.PARAMETER Fix
    Fix URLs by removing "en-us/" instead of just reporting them

.EXAMPLE
    .\Link-Lang-Check.ps1

.EXAMPLE
    .\Link-Lang-Check.ps1 -Fix -Verbose
#>

[CmdletBinding()]
param(
    [switch]$Fix,
    [string[]]$Files
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot "../../../scripts/lib/Modules/CIHelpers.psm1") -Force

function Get-GitTextFile {
    <#
    .SYNOPSIS
        Get list of all text files under git source control, excluding binary files.
    #>

    try {
        $result = & git grep -I --name-only -e '' 2>&1

        if ($LASTEXITCODE -gt 1) {
            Write-Error "Error executing git grep: $result"
            return @()
        }

        if ($result -and $result.Count -gt 0) {
            return $result | Where-Object { $_ -is [string] -and $_.Trim() -ne '' }
        }

        return @()
    }
    catch {
        Write-Error "Error getting git text files: $_"
        return @()
    }
}

function Find-LinksInFile {
    <#
    .SYNOPSIS
        Find links with 'en-us' in them and return details.
    #>

    [CmdletBinding()]
    param(
        [string]$FilePath
    )

    $linksFound = @()

    try {
        $lines = @(Get-Content -Path $FilePath -Encoding UTF8 -ErrorAction Stop)
    }
    catch {
        Write-Verbose "Could not read $FilePath`: $_"
        return $linksFound
    }

    $urlPattern = 'https?://[^\s<>"'']+?en-us/[^\s<>"'']+'

    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        $urlMatches = [regex]::Matches($line, $urlPattern)

        foreach ($match in $urlMatches) {
            $linksFound += [PSCustomObject]@{
                File        = $FilePath
                LineNumber  = $i + 1
                OriginalUrl = $match.Value
                FixedUrl    = $match.Value -replace 'en-us/', ''
            }
        }
    }

    return $linksFound
}

function Repair-LinksInFile {
    <#
    .SYNOPSIS
        Fix links in a single file by removing 'en-us/' from URLs.
    #>

    [CmdletBinding()]
    param(
        [string]$FilePath,
        [PSCustomObject[]]$Links
    )

    try {
        $content = Get-Content -Path $FilePath -Raw -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        Write-Verbose "Could not read $FilePath`: $_"
        return $false
    }

    $modifiedContent = $content
    foreach ($link in $Links) {
        $modifiedContent = $modifiedContent -replace [regex]::Escape($link.OriginalUrl), $link.FixedUrl
    }

    if ($modifiedContent -ne $content) {
        try {
            Set-Content -Path $FilePath -Value $modifiedContent -Encoding UTF8 -NoNewline -ErrorAction Stop
            return $true
        }
        catch {
            Write-Verbose "Could not write to $FilePath`: $_"
            return $false
        }
    }
    return $false
}

function Repair-AllLink {
    <#
    .SYNOPSIS
        Fix all links in their respective files.
    #>

    [CmdletBinding()]
    param(
        [PSCustomObject[]]$AllLinks
    )

    $linksByFile = $AllLinks | Group-Object -Property File
    $filesModified = 0

    foreach ($fileGroup in $linksByFile) {
        $filePath = $fileGroup.Name
        $links = $fileGroup.Group

        Write-Verbose "Fixing links in $filePath..."

        if (Repair-LinksInFile -FilePath $filePath -Links $links) {
            $filesModified++
        }
    }

    return $filesModified
}

function ConvertTo-JsonOutput {
    <#
    .SYNOPSIS
        Prepare links for JSON output.
    #>

    [CmdletBinding()]
    param(
        [PSCustomObject[]]$Links
    )

    $jsonData = @()
    foreach ($link in $Links) {
        $jsonData += [PSCustomObject]@{
            file         = $link.File
            line_number  = $link.LineNumber
            original_url = $link.OriginalUrl
        }
    }
    return $jsonData
}

function Invoke-LinkLanguageCheck {
    <#
    .SYNOPSIS
        Main entry point for the link language checker.
    #>

    [CmdletBinding()]
    param(
        [switch]$Fix,
        [string[]]$Files
    )

    Write-Verbose "Getting list of git-tracked text files..."

    if ($Files -and $Files.Count -gt 0) {
        $files = $Files
    }
    else {
        $files = Get-GitTextFile
    }

    Write-Verbose "Found $($files.Count) git-tracked text files"

    $allLinks = @()

    foreach ($filePath in $files) {
        if (-not (Test-Path -Path $filePath -PathType Leaf)) {
            Write-Warning "Skipping $filePath`: not a regular file"
            continue
        }

        Write-Verbose "Processing $filePath..."

        $links = Find-LinksInFile -FilePath $filePath
        $allLinks += $links
    }

    if ($allLinks.Count -gt 0) {
        if ($Fix) {
            Write-Verbose "`nFound $($allLinks.Count) URLs containing 'en-us':`n"
            foreach ($linkInfo in $allLinks) {
                Write-Verbose "File: $($linkInfo.File), Line: $($linkInfo.LineNumber)"
                Write-Verbose "  URL: $($linkInfo.OriginalUrl)"
            }

            $filesModified = Repair-AllLink -AllLinks $allLinks
            Write-Output "Fixed $($allLinks.Count) URLs in $filesModified files."

            Write-Verbose "`nDetails of fixes:"
            foreach ($linkInfo in $allLinks) {
                Write-Verbose "File: $($linkInfo.File), Line: $($linkInfo.LineNumber)"
                Write-Verbose "  Original: $($linkInfo.OriginalUrl)"
                Write-Verbose "  Fixed: $($linkInfo.FixedUrl)"
            }
        }
        else {
            $jsonOutput = ConvertTo-JsonOutput -Links $allLinks
            Write-Output ($jsonOutput | ConvertTo-Json -Depth 3)
        }
    }
    else {
        if (-not $Fix) {
            Write-Output "[]"
        }
        else {
            Write-Output "No URLs containing 'en-us' were found."
        }
    }
}

#region Main Execution
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-LinkLanguageCheck -Fix:$Fix -Files $Files
    }
    catch {
        Write-Error -ErrorAction Continue "Link-Lang-Check failed: $($_.Exception.Message)"
        Write-CIAnnotation -Message $_.Exception.Message -Level Error
        exit 1
    }
}
#endregion Main Execution
