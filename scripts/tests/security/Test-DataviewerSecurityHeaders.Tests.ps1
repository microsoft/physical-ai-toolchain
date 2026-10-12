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

    $locationMatch = [regex]::Match($script:NginxConfig, '(?m)^\s*location\s')
    if (-not $locationMatch.Success) {
        throw 'The frontend NGINX template does not contain a location block'
    }
    $firstLocation = $locationMatch.Index

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

    function New-PolicyResponse {
        param([string]$Route)
        $isBridge = $Route -eq '/redirect.html'
        $ancestors = if ($isBridge) { "'self'" } else { "'none'" }
        $policy = @{
            'Content-Security-Policy' = "default-src 'self'; base-uri 'self'; object-src 'none'; form-action 'self'; frame-ancestors $ancestors; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self' https://login.microsoftonline.com; frame-src 'self' https://login.microsoftonline.com"
            'X-Frame-Options' = $(if ($isBridge) { 'SAMEORIGIN' } else { 'DENY' })
            'X-Content-Type-Options' = 'nosniff'
            'Strict-Transport-Security' = 'max-age=31536000; includeSubDomains'
            'Referrer-Policy' = 'strict-origin-when-cross-origin'
            'Permissions-Policy' = 'geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()'
        }
        if ($isBridge) { $policy['Cache-Control'] = 'no-store' }
        $mutation = $HeaderTestState.Mutation
        if ($mutation -and $mutation.Route -eq $Route) {
            if ($mutation.Values) {
                $policy[$mutation.Header] = $mutation.Values
            } else {
                $policy.Remove($mutation.Header)
            }
        }
        $status = if ($Route.StartsWith('/api/')) { 404 } else { 200 }
        $message = [System.Net.Http.HttpResponseMessage]::new($status)
        $message.Content = [System.Net.Http.StringContent]::new(
            '<!doctype html><script type="module" src="/assets/index.js"></script>',
            [System.Text.Encoding]::UTF8, 'text/html'
        )
        foreach ($entry in $policy.GetEnumerator()) {
            $null = $message.Headers.TryAddWithoutValidation($entry.Key, [string[]]@($entry.Value))
        }
        return [Microsoft.PowerShell.Commands.BasicHtmlWebResponseObject]::new(
            $message, $message.Content.ReadAsStreamAsync().GetAwaiter().GetResult(),
            [timespan]::Zero, [System.Threading.CancellationToken]::None
        )
    }
}

Describe 'Data Viewer browser security policy' -Tag 'Unit' {
    It 'builds a separate redirect bridge rather than falling back to the normal UI' {
        $frontend = Join-Path $script:RepoRoot 'data-management/viewer/frontend'
        Join-Path $frontend 'redirect.html' | Should -Exist
        Get-Content -Raw (Join-Path $frontend 'redirect.html') | Should -Match 'src="/src/redirect.ts"'
        Get-Content -Raw (Join-Path $frontend 'vite.config.ts') | Should -Match "redirect:.*redirect\.html"
    }

    Describe 'Independent live Data Viewer security verification' -Tag 'Unit' {
        BeforeEach {
            $script:HeaderTestState = @{
                Mutation = $null
                Routes = [System.Collections.Generic.List[string]]::new()
            }
            Mock Invoke-WebRequest {
                $route = ([uri]$Uri).AbsolutePath
                $HeaderTestState.Routes.Add($route)
                New-PolicyResponse -Route $route
            }
        }

        It 'accepts complete policies and checks normal, bridge, health and proxy-error routes' {
            & (Join-Path $script:RepoRoot 'scripts/security/Test-DataviewerSecurityHeaders.ps1')
            foreach ($route in @('/', '/assets/index.js', '/security-policy-smoke',
                    '/api/security-policy-smoke', '/health', '/redirect.html')) {
                $script:HeaderTestState.Routes | Should -Contain $route
            }
        }

        It 'rejects <Case> independently of the NGINX template' -TestCases @(
            @{ Case = 'missing headers'; Route = '/'; Header = 'X-Frame-Options'; Values = $null }
            @{ Case = 'weakened framing'; Route = '/'; Header = 'X-Frame-Options'; Values = @('SAMEORIGIN') }
            @{ Case = 'duplicate equal headers'; Route = '/'; Header = 'X-Frame-Options'; Values = @('DENY', 'DENY') }
            @{ Case = 'conflicting headers'; Route = '/'; Header = 'X-Frame-Options'; Values = @('DENY', 'SAMEORIGIN') }
            @{ Case = 'weakened CSP'; Route = '/'; Header = 'Content-Security-Policy'; Values = @("default-src 'self'") }
            @{ Case = 'a cacheable bridge'; Route = '/redirect.html'; Header = 'Cache-Control'; Values = @('max-age=600') }
            @{ Case = 'a blocked bridge'; Route = '/redirect.html'; Header = 'X-Frame-Options'; Values = @('DENY') }
            @{ Case = 'a frameable health route'; Route = '/health'; Header = 'X-Frame-Options'; Values = @('SAMEORIGIN') }
            @{ Case = 'weakened proxy errors'; Route = '/api/security-policy-smoke'; Header = 'X-Frame-Options'; Values = @('SAMEORIGIN') }
        ) {
            param($Case, $Route, $Header, $Values)
            $script:HeaderTestState.Mutation = @{ Route = $Route; Header = $Header; Values = $Values }
            { & (Join-Path $script:RepoRoot 'scripts/security/Test-DataviewerSecurityHeaders.ps1') } |
                Should -Throw -Because $Case
        }
    }

    It 'allows framing only the exact bridge URI and disables its caching' {
        foreach ($entry in @{
            'frame_ancestors' = @("'none'", "'self'")
            'frame_options' = @('DENY', 'SAMEORIGIN')
            'redirect_cache_control' = @('', 'no-store')
        }.GetEnumerator()) {
            $pattern = '(?s)map \$uri \$' + $entry.Key + '\s*\{\s*default "' +
                [regex]::Escape($entry.Value[0]) + '";\s*/redirect\.html "' +
                [regex]::Escape($entry.Value[1]) + '";\s*\}'
            $script:NginxConfig | Should -Match $pattern
        }
        $script:NginxConfig | Should -Match '(?s)location = /redirect\.html \{\s*try_files \$uri =404;\s*\}'
    }

    It 'defines each compatible header once at the shared server scope with always semantics' {
        $expectedHeaders = @{
            'X-Frame-Options' = '$frame_options'
            'Cache-Control' = '$redirect_cache_control'
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
        $cspValue = ($script:Headers | Where-Object Name -EQ 'Content-Security-Policy').Value.Replace(
            '$frame_ancestors', "'none'"
        )
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
