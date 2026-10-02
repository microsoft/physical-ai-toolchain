#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Verifies the running Data Viewer frontend serves the expected security headers.

.DESCRIPTION
    Parses expected header values directly from the NGINX template so this check
    cannot drift from the actual server configuration (single source of truth;
    see also scripts/tests/security/DataviewerSecurityHeaders.Tests.ps1). Asserts
    the headers are present with unweakened values on the root document, a built
    static asset, an SPA route, and an API route.

.PARAMETER BaseUri
    Base URI of the running frontend. Default: http://localhost:5173.

.PARAMETER TemplatePath
    Path to the NGINX config template. Default: data-management/viewer/frontend/nginx.conf.template.

.EXAMPLE
    ./Test-DataviewerSecurityHeaders.ps1
    Verify headers against the default local frontend URL.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$BaseUri = 'http://localhost:5173',

    [Parameter(Mandatory = $false)]
    [string]$TemplatePath = 'data-management/viewer/frontend/nginx.conf.template'
)

$ErrorActionPreference = 'Stop'

function Get-ExpectedHeaders {
    param(
        [Parameter(Mandatory)]
        [string]$TemplatePath
    )

    $templateContent = Get-Content -Path $TemplatePath -Raw
    $expectedHeaders = [ordered]@{}
    $headerMatches = [regex]::Matches(
        $templateContent,
        'add_header\s+(?<name>[\w-]+)\s+(?:"(?<quoted>[^"]*)"|(?<bare>\S+))\s+always;'
    )
    foreach ($match in $headerMatches) {
        $name = $match.Groups['name'].Value
        $value = if ($match.Groups['quoted'].Success) { $match.Groups['quoted'].Value } else { $match.Groups['bare'].Value }
        $expectedHeaders[$name] = $value
    }
    if ($expectedHeaders.Count -eq 0) { throw "No add_header directives found in $TemplatePath" }
    return $expectedHeaders
}

function Assert-SecurityHeaders {
    param(
        [Parameter(Mandatory)]
        [string]$Route,

        [Parameter(Mandatory)]
        [Microsoft.PowerShell.Commands.WebResponseObject]$Response,

        [Parameter(Mandatory)]
        [System.Collections.Specialized.OrderedDictionary]$ExpectedHeaders
    )

    foreach ($expected in $ExpectedHeaders.GetEnumerator()) {
        if (-not $Response.Headers.ContainsKey($expected.Key)) {
            throw "$Route is missing $($expected.Key)"
        }

        $values = @($Response.Headers[$expected.Key])
        if ($values.Count -eq 0 -or @($values | Where-Object { $_ -cne $expected.Value }).Count -gt 0) {
            throw "$Route has a conflicting or weakened $($expected.Key)"
        }
    }
}

$expectedHeaders = Get-ExpectedHeaders -TemplatePath $TemplatePath

$rootRoute = '/'
$rootResponse = Invoke-WebRequest -Uri "$BaseUri$rootRoute" -UseBasicParsing
Assert-SecurityHeaders -Route $rootRoute -Response $rootResponse -ExpectedHeaders $expectedHeaders

$assetMatch = [regex]::Match($rootResponse.Content, '<script[^>]+\ssrc=["''](?<path>/assets/[^"'']+\.js)["'']')
if (-not $assetMatch.Success) { throw 'Root HTML does not reference a built JS asset via a <script> tag' }
$assetRoute = $assetMatch.Groups['path'].Value

$routes = @(
    @{ Route = $assetRoute; ExpectedStatus = 200 }
    @{ Route = '/security-policy-smoke'; ExpectedStatus = 200 }
    @{ Route = '/api/security-policy-smoke'; ExpectedStatus = 404 }
)

foreach ($request in $routes) {
    $response = Invoke-WebRequest `
        -Uri "$BaseUri$($request.Route)" `
        -UseBasicParsing `
        -SkipHttpErrorCheck
    if ($response.StatusCode -ne $request.ExpectedStatus) {
        throw "$($request.Route) returned status $($response.StatusCode), expected $($request.ExpectedStatus)"
    }
    Assert-SecurityHeaders -Route $request.Route -Response $response -ExpectedHeaders $expectedHeaders
}

Write-Host 'All routes served the expected security headers.'
