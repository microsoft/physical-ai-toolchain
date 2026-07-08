#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

<#
.SYNOPSIS
    Creates the Sigstore bundle artifacts consumed by release verification.
.PARAMETER BundlePath
    Source archive Sigstore bundle path.
.PARAMETER WheelBundlePath
    Wheel Sigstore bundle path.
.PARAMETER Tag
    Release tag.
#>
[CmdletBinding()]
param(
    [string]$BundlePath = $env:BUNDLE_PATH,
    [string]$WheelBundlePath = $env:WHEEL_BUNDLE_PATH,
    [string]$Tag = $env:TAG
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-DsseEnvelopeLine {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [string]$BundlePath
    )

    $bundle = Get-Content -Raw -LiteralPath $BundlePath | ConvertFrom-Json
    return ($bundle.dsseEnvelope | ConvertTo-Json -Depth 100 -Compress)
}

function New-SigningArtifactsCore {
    [CmdletBinding()]
    param(
        [string]$BundlePath,
        [string]$WheelBundlePath,
        [string]$Tag
    )

    if ([string]::IsNullOrWhiteSpace($BundlePath)) {
        throw 'BUNDLE_PATH is required.'
    }

    if ([string]::IsNullOrWhiteSpace($WheelBundlePath)) {
        throw 'WHEEL_BUNDLE_PATH is required.'
    }

    if ([string]::IsNullOrWhiteSpace($Tag)) {
        throw 'TAG is required.'
    }

    Copy-Item -LiteralPath $BundlePath -Destination "source-$Tag.sigstore.json"
    ConvertTo-DsseEnvelopeLine -BundlePath $BundlePath |
        Set-Content -LiteralPath "source-$Tag.intoto.jsonl" -Encoding utf8NoBOM
    Copy-Item -LiteralPath $WheelBundlePath -Destination "wheels-$Tag.sigstore.json"
    ConvertTo-DsseEnvelopeLine -BundlePath $WheelBundlePath |
        Set-Content -LiteralPath "wheels-$Tag.intoto.jsonl" -Encoding utf8NoBOM

    Write-Output "Signing artifacts created for $Tag"
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        exit (New-SigningArtifactsCore -BundlePath $BundlePath -WheelBundlePath $WheelBundlePath -Tag $Tag)
    }
    catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
        exit 1
    }
}
