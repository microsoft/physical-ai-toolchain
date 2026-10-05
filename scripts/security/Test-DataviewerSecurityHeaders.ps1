#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Verifies the running Data Viewer frontend serves the expected security headers.

.DESCRIPTION
    Checks responses against independent literal policy expectations, including
    duplicate-header rejection. Covers the root, built assets, SPA, API error,
    health, and redirect bridge. Only the bridge permits same-origin framing
    and must be served with Cache-Control: no-store.

.PARAMETER BaseUri
    Base URI of the running frontend. Default: http://localhost:5173.

.EXAMPLE
    ./Test-DataviewerSecurityHeaders.ps1
    Verify headers against the default local frontend URL.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$BaseUri = 'http://localhost:5173'
)

$ErrorActionPreference = 'Stop'

function Get-ExpectedHeaders {
    param(
        [Parameter(Mandatory)]
        [string]$Route
    )

    $isBridge = $Route -eq '/redirect.html'
    $ancestors = if ($isBridge) { "'self'" } else { "'none'" }
    $expectedHeaders = [ordered]@{
        'Content-Security-Policy' = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; frame-ancestors $ancestors; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self' https://login.microsoftonline.com; frame-src 'self' https://login.microsoftonline.com"
        'X-Frame-Options' = $(if ($isBridge) { 'SAMEORIGIN' } else { 'DENY' })
        'X-Content-Type-Options' = 'nosniff'
        'Strict-Transport-Security' = 'max-age=31536000; includeSubDomains'
        'Referrer-Policy' = 'strict-origin-when-cross-origin'
        'Permissions-Policy' = 'geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()'
    }
    if ($isBridge) { $expectedHeaders['Cache-Control'] = 'no-store' }
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
        if ($values.Count -ne 1 -or $values[0] -cne $expected.Value) {
            throw "$Route has a duplicate, conflicting or weakened $($expected.Key)"
        }
    }
    foreach ($header in @('Cross-Origin-Embedder-Policy', 'Cross-Origin-Opener-Policy', 'Cross-Origin-Resource-Policy')) {
        if ($Response.Headers.ContainsKey($header)) { throw "$Route has an unsupported $header" }
    }
}

$BaseUri = $BaseUri.TrimEnd('/')
$rootRoute = '/'
$rootResponse = Invoke-WebRequest -Uri "$BaseUri$rootRoute" -UseBasicParsing
$expectedHeaders = Get-ExpectedHeaders -Route $rootRoute
Assert-SecurityHeaders -Route $rootRoute -Response $rootResponse -ExpectedHeaders $expectedHeaders

$assetMatch = [regex]::Match($rootResponse.Content, '<script[^>]+\ssrc=["''](?<path>/assets/[^"'']+\.js)["'']')
if (-not $assetMatch.Success) { throw 'Root HTML does not reference a built JS asset via a <script> tag' }
$assetRoute = $assetMatch.Groups['path'].Value

$routes = @(
    @{ Route = $assetRoute; ExpectedStatus = 200 }
    @{ Route = '/security-policy-smoke'; ExpectedStatus = 200 }
    @{ Route = '/api/security-policy-smoke'; ExpectedStatus = 404 }
    @{ Route = '/health'; ExpectedStatus = 200 }
    @{ Route = '/redirect.html'; ExpectedStatus = 200 }
    @{ Route = '/redirect.html/missing'; ExpectedStatus = 200 }
)

foreach ($request in $routes) {
    $response = Invoke-WebRequest `
        -Uri "$BaseUri$($request.Route)" `
        -UseBasicParsing `
        -SkipHttpErrorCheck
    if ($response.StatusCode -ne $request.ExpectedStatus) {
        throw "$($request.Route) returned status $($response.StatusCode), expected $($request.ExpectedStatus)"
    }
    Assert-SecurityHeaders -Route $request.Route -Response $response `
        -ExpectedHeaders (Get-ExpectedHeaders -Route $request.Route)
}

Write-Host 'All routes served the expected security headers.'
