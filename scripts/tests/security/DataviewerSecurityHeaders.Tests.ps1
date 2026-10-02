# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:NginxConfig = Get-Content -Raw (
        Join-Path $script:RepoRoot 'data-management/viewer/frontend/nginx.conf.template'
    )
    $script:ZapRules = Get-Content (
        Join-Path $script:RepoRoot '.zap/rules.tsv'
    ) | Where-Object { $_ -and -not $_.StartsWith('#') } | ForEach-Object {
        $fields = $_ -split "`t", 3
        [PSCustomObject]@{
            Id = $fields[0]
            Action = $fields[1]
            Rationale = $fields[2]
        }
    }

    $firstLocation = $script:NginxConfig.IndexOf('location ')
    if ($firstLocation -lt 0) {
        throw 'The frontend NGINX template does not contain a location block'
    }

    $serverPolicy = $script:NginxConfig.Substring(0, $firstLocation)
    $script:Headers = [regex]::Matches(
        $serverPolicy,
        '(?m)^\s*add_header\s+(?<name>[\w-]+)\s+(?<value>"[^"]*"|[^\s;]+)\s+(?<always>always)\s*;\s*$'
    ) | ForEach-Object {
        [PSCustomObject]@{
            Name = $_.Groups['name'].Value
            Value = $_.Groups['value'].Value.Trim('"')
            Always = $_.Groups['always'].Value
        }
    }
}

Describe 'Data Viewer browser security policy' -Tag 'Unit' {
    It 'defines each compatible header once at the shared server scope with always semantics' {
        $expectedHeaders = @{
            'X-Frame-Options' = 'DENY'
            'X-Content-Type-Options' = 'nosniff'
            'Strict-Transport-Security' = 'max-age=31536000; includeSubDomains'
            'Referrer-Policy' = 'strict-origin-when-cross-origin'
            'Permissions-Policy' = 'geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()'
        }

        foreach ($entry in $expectedHeaders.GetEnumerator()) {
            $headerMatches = @($script:Headers | Where-Object Name -EQ $entry.Key)
            $headerMatches.Count | Should -Be 1
            $headerMatches[0].Value | Should -BeExactly $entry.Value
            $headerMatches[0].Always | Should -BeExactly 'always'
        }

        $cspHeaders = @($script:Headers | Where-Object Name -EQ 'Content-Security-Policy')
        $cspHeaders.Count | Should -Be 1
        $cspHeaders[0].Always | Should -BeExactly 'always'

        $locationPolicy = $script:NginxConfig.Substring($firstLocation)
        $locationPolicy | Should -Not -Match '(?m)^\s*add_header\s+'
    }

    It 'defines the exact MSAL-compatible CSP directives' {
        $cspValue = ($script:Headers | Where-Object Name -EQ 'Content-Security-Policy').Value
        $directives = @{}
        foreach ($directive in $cspValue -split ';') {
            $parts = @($directive.Trim() -split '\s+')
            if ($parts[0]) {
                $directives[$parts[0]] = @($parts | Select-Object -Skip 1)
            }
        }

        $expectedDirectives = @{
            'default-src' = @("'self'")
            'base-uri' = @("'self'")
            'object-src' = @("'none'")
            'form-action' = @("'self'")
            'frame-ancestors' = @("'none'")
            'script-src' = @("'self'")
            'style-src' = @("'self'", "'unsafe-inline'")
            'img-src' = @("'self'", 'data:', 'blob:')
            'media-src' = @("'self'", 'blob:')
            'font-src' = @("'self'")
            'connect-src' = @("'self'", 'https://login.microsoftonline.com')
            'frame-src' = @("'self'", 'https://login.microsoftonline.com')
        }

        @($directives.Keys).Count | Should -Be $expectedDirectives.Count
        foreach ($entry in $expectedDirectives.GetEnumerator()) {
            $directives.ContainsKey($entry.Key) | Should -BeTrue
            Compare-Object $entry.Value $directives[$entry.Key] | Should -BeNullOrEmpty
        }
    }

    It 'does not enable cross-origin isolation headers' {
        $unsupportedHeaders = @(
            'Cross-Origin-Embedder-Policy'
            'Cross-Origin-Opener-Policy'
            'Cross-Origin-Resource-Policy'
        )

        foreach ($header in $unsupportedHeaders) {
            @($script:Headers | Where-Object Name -EQ $header).Count | Should -Be 0
        }
    }

    It 'records one explicit ZAP 90004 MSAL compatibility exception' {
        $ruleMatches = @($script:ZapRules | Where-Object Id -EQ '90004')

        $ruleMatches.Count | Should -Be 1
        $ruleMatches[0].Action | Should -BeExactly 'IGNORE'
        $ruleMatches[0].Rationale | Should -Match '(?i)MSAL.*silent.*iframe'
        $ruleMatches[0].Rationale | Should -Match '(?i)COOP.*CORP.*accepted residual risk'
    }

    It 'records one explicit ZAP 10055 inline-style compatibility exception' {
        $ruleMatches = @($script:ZapRules | Where-Object Id -EQ '10055')

        $ruleMatches.Count | Should -Be 1
        $ruleMatches[0].Action | Should -BeExactly 'IGNORE'
        $ruleMatches[0].Rationale | Should -Match "(?i)inline.*style.*unsafe-inline"
        $ruleMatches[0].Rationale | Should -Match '(?i)plugin.*other.*CSP.*sub-alert'
    }
}
