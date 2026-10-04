#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Validates that committed dependency metadata uses canonical public feeds.

.DESCRIPTION
    Scans npm, Python, and uv dependency manifests and lockfiles for package
    source URLs. Fails when a source uses a non-public host, plain HTTP,
    embedded credentials, or a nonliteral npm registry value. Npm registry
    declarations must use the canonical public npm registry. Writes structured
    results to logs/public-dependency-feeds-results.json.

    Adapted from microsoft/hve-core scripts/security/Test-PublicDependencyFeeds.ps1
    as of commit a146d8d01ad1e38f82d5d8eebb7fa8d1d88c4092. The PowerShell
    requirement is lowered from 7.4 to this repository's 7.0 standard.

    Local adaptations: npm and Python lock and index URLs must use their own
    ecosystem's hosts without ports or query strings; npm lockfiles are parsed
    and every installed entry needs a resolved URL and sha512 integrity; uv.lock
    artifacts need sha256 or stronger hashes; package.json is parsed so only
    dependency fields must resolve from the npm registry; TLS, lockfile, and
    credential settings are rejected; diagnostics never echo source values;
    scanner errors exit 2.

.PARAMETER RepoRoot
    Repository root to scan. Defaults to the root containing this script.

.PARAMETER OutputPath
    JSON results path. Defaults to logs/public-dependency-feeds-results.json.

.PARAMETER FailOnViolation
    Exit nonzero when a prohibited source is found.

.EXAMPLE
    ./scripts/security/Test-PublicDependencyFeeds.ps1 -FailOnViolation

.NOTES
    Runs via: npm run lint:public-dependency-feeds

.LINK
    https://github.com/microsoft/hve-core/blob/a146d8d01ad1e38f82d5d8eebb7fa8d1d88c4092/scripts/security/Test-PublicDependencyFeeds.ps1
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$RepoRoot = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),

    [Parameter(Mandatory = $false)]
    [string]$OutputPath,

    [Parameter(Mandatory = $false)]
    [switch]$FailOnViolation
)

$ErrorActionPreference = 'Stop'

#region Functions

function Test-DependencySourceLine {
    <#
    .SYNOPSIS
        Determines whether a line can declare a dependency source URL.
    .OUTPUTS
        [bool]
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Line
    )

    $leafName = Split-Path -Leaf $Path
    switch -Regex ($leafName) {
        '^(package-lock|npm-shrinkwrap)\.json$' {
            return $Line -match '"resolved"\s*:' -or $Line -match '"integrity"\s*:'
        }
        '^\.npmrc$' {
            return $Line -match '^\s*(?:@[^:]+:)?registry\s*='
        }
        '^uv\.lock$' {
            return $Line -match '\b(?:registry|url|index|git)\s*='
        }
        '^pyproject\.toml$' {
            return $Line -match '\b(?:index-url|extra-index-url|registry|url|git)\s*='
        }
        '^requirements.*\.txt$' {
            return $Line -match '(?:https?|git\+https)://'
        }
        default {
            return $false
        }
    }
}

function Get-InsecureSettingRule {
    <#
    .SYNOPSIS
        Detects package-manager settings that weaken TLS, lockfiles, or commit credentials.
    .OUTPUTS
        [pscustomobject] with rule and reason, or $null.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$LeafName,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Line
    )

    $insecure = [pscustomobject]@{ rule = 'insecure-setting'; reason = 'package-manager settings must not disable TLS verification or lockfile integrity' }
    switch -Regex ($LeafName) {
        '^\.npmrc$' {
            if ($Line -match '^\s*[#;]' -or $Line -notmatch '^\s*(?<key>[^=]+?)\s*=\s*(?<value>.*?)\s*$') {
                return $null
            }
            $key = $Matches['key'].ToLowerInvariant()
            $value = $Matches['value'].Trim('"''').ToLowerInvariant()
            if ($key -match '(^|:)(_auth|_authtoken|_password|username|password|certfile|keyfile)$') {
                return [pscustomobject]@{ rule = 'credential-setting'; reason = 'npm credentials must not be committed' }
            }
            if (($key -eq 'strict-ssl' -and $value -eq 'false') -or
                ($key -eq 'package-lock' -and $value -eq 'false') -or
                ($key -eq 'omit-lockfile-registry-resolved' -and $value -eq 'true')) {
                return $insecure
            }
        }
        '^pyproject\.toml$' {
            if ($Line -match '^\s*(?:allow-insecure-host|trusted-host)\s*=') {
                return $insecure
            }
        }
        '^requirements.*\.txt$' {
            if ($Line -match '(^|\s)--trusted-host\b') {
                return $insecure
            }
        }
    }
    return $null
}

function Test-NpmLockIntegrity {
    <#
    .SYNOPSIS
        Validates that every installed npm lockfile entry has a resolved URL and sha512 integrity.
    .OUTPUTS
        [pscustomobject] with checked entry count and findings (line, rule, reason).
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Lines
    )

    $findings = [System.Collections.Generic.List[object]]::new()
    $checked = 0
    try {
        $lock = ($Lines -join "`n") | ConvertFrom-Json -AsHashtable
    }
    catch {
        $lock = $null
    }

    if ($lock -isnot [System.Collections.IDictionary]) {
        $findings.Add([pscustomobject]@{ line = 0; rule = 'npm-lock-parse'; reason = 'npm lockfile must be valid JSON' }) | Out-Null
        return [pscustomobject]@{ checked = $checked; findings = $findings }
    }

    $version = $lock['lockfileVersion']
    $packages = $lock['packages']
    if (($version -isnot [long] -and $version -isnot [int]) -or $version -lt 2 -or $packages -isnot [System.Collections.IDictionary]) {
        $findings.Add([pscustomobject]@{ line = 0; rule = 'npm-lock-version'; reason = 'npm lockfile must use lockfileVersion 2 or later with a packages map' }) | Out-Null
        return [pscustomobject]@{ checked = $checked; findings = $findings }
    }

    $keyLines = @{}
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        $keyMatch = [regex]::Match($Lines[$index], '^\s*"(?<key>[^"]*node_modules/[^"]*)"\s*:\s*\{')
        if ($keyMatch.Success -and -not $keyLines.ContainsKey($keyMatch.Groups['key'].Value)) {
            $keyLines[$keyMatch.Groups['key'].Value] = $index + 1
        }
    }

    foreach ($entry in $packages.GetEnumerator()) {
        if ($entry.Key -notmatch '(^|/)node_modules/') {
            continue
        }
        $line = if ($keyLines.ContainsKey($entry.Key)) { $keyLines[$entry.Key] } else { 0 }
        $package = $entry.Value
        if ($package -isnot [System.Collections.IDictionary]) {
            $findings.Add([pscustomobject]@{ line = $line; rule = 'npm-lock-parse'; reason = 'npm lockfile package entries must be objects' }) | Out-Null
            continue
        }
        # Workspace links and bundled dependencies are covered by their owning entry.
        if ($package['link'] -eq $true -or $package['inBundle'] -eq $true) {
            continue
        }

        $checked++
        $resolved = $package['resolved']
        if ($resolved -isnot [string] -or [string]::IsNullOrWhiteSpace($resolved)) {
            $findings.Add([pscustomobject]@{ line = $line; rule = 'npm-resolved-missing'; reason = 'npm lockfile entries must record a resolved registry URL' }) | Out-Null
        }
        elseif ($resolved -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') {
            # URL-shaped values are validated by the line scan; this catches shorthands such as github:owner/repo.
            $findings.Add([pscustomobject]@{ line = $line; rule = 'url-scheme'; reason = 'dependency sources must use HTTPS' }) | Out-Null
        }

        $integrity = $package['integrity']
        $tokens = if ($integrity -is [string]) { @($integrity -split '\s+' | Where-Object { $_ }) } else { @() }
        if ($tokens.Count -eq 0) {
            $findings.Add([pscustomobject]@{ line = $line; rule = 'npm-integrity-missing'; reason = 'npm lockfile entries must record sha512 integrity' }) | Out-Null
        }
        elseif (@($tokens | Where-Object { $_ -notmatch '^sha512-[A-Za-z0-9+/]+={0,2}$' }).Count -gt 0) {
            $findings.Add([pscustomobject]@{ line = $line; rule = 'npm-integrity-weak'; reason = 'lockfile integrity must use sha512' }) | Out-Null
        }
    }

    return [pscustomobject]@{ checked = $checked; findings = $findings }
}

function Get-SourceUrlFinding {
    <#
    .SYNOPSIS
        Validates one dependency source URL against transport, credential, and host rules.
    .OUTPUTS
        [pscustomobject] with rule and reason, or $null.
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$Url,

        [Parameter(Mandatory = $true)]
        [string[]]$HostSet,

        [Parameter(Mandatory = $false)]
        [switch]$RejectQuery,

        [Parameter(Mandatory = $false)]
        [switch]$NpmRegistryDeclaration
    )

    $uri = $null
    if (-not [uri]::TryCreate(($Url -replace '^git\+', ''), [UriKind]::Absolute, [ref]$uri)) {
        return [pscustomobject]@{ rule = 'url-invalid'; reason = 'dependency source URL is invalid' }
    }

    $finding = if ($uri.Scheme -ne 'https') {
        @('url-scheme', 'dependency sources must use HTTPS')
    }
    elseif (-not [string]::IsNullOrEmpty($uri.UserInfo)) {
        @('url-credentials', 'dependency source URLs must not contain credentials')
    }
    elseif (-not $uri.IsDefaultPort) {
        @('url-port', 'dependency source URLs must use the default HTTPS port')
    }
    elseif ($RejectQuery -and -not [string]::IsNullOrEmpty($uri.Query)) {
        @('url-query', 'registry and lockfile URLs must not carry query strings')
    }
    elseif ($NpmRegistryDeclaration -and $uri.Host.ToLowerInvariant() -notin $HostSet) {
        @('npm-registry-not-canonical', 'npm registry declarations must use https://registry.npmjs.org/')
    }
    elseif ($uri.Host.ToLowerInvariant() -notin $HostSet) {
        @('host-not-approved', 'dependency source host is not an approved public registry for this file type')
    }

    if ($finding) {
        return [pscustomobject]@{ rule = $finding[0]; reason = $finding[1] }
    }
    return $null
}

function Get-NpmManifestFindings {
    <#
    .SYNOPSIS
        Validates package.json dependency specs and registry settings while ignoring metadata URLs.
    .OUTPUTS
        [pscustomobject] with checked spec count and findings (line, rule, reason).
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Lines,

        [Parameter(Mandatory = $true)]
        [string[]]$NpmHosts
    )

    $findings = [System.Collections.Generic.List[object]]::new()
    $checked = 0
    try {
        $manifest = ($Lines -join "`n") | ConvertFrom-Json -AsHashtable
    }
    catch {
        $manifest = $null
    }

    if ($manifest -isnot [System.Collections.IDictionary]) {
        $findings.Add([pscustomobject]@{ line = 0; rule = 'npm-manifest-parse'; reason = 'package.json must be a valid JSON object' }) | Out-Null
        return [pscustomobject]@{ checked = $checked; findings = $findings }
    }

    $findLine = {
        param([string]$Key, [string]$Value)
        $pattern = '^\s*"' + [regex]::Escape($Key) + '"\s*:\s*"' + [regex]::Escape($Value) + '"'
        for ($index = 0; $index -lt $Lines.Count; $index++) {
            if ($Lines[$index] -match $pattern) {
                return $index + 1
            }
        }
        return 0
    }

    $dependencyFields = @('dependencies', 'devDependencies', 'optionalDependencies', 'peerDependencies', 'resolutions', 'overrides')
    $specs = [System.Collections.Generic.List[object]]::new()
    $pending = [System.Collections.Generic.Stack[object]]::new()
    foreach ($field in $dependencyFields) {
        if ($manifest[$field] -is [System.Collections.IDictionary]) {
            $pending.Push($manifest[$field])
        }
    }
    # Overrides nest package names to any depth.
    while ($pending.Count -gt 0) {
        foreach ($entry in $pending.Pop().GetEnumerator()) {
            if ($entry.Value -is [string]) {
                $specs.Add([pscustomobject]@{ name = [string]$entry.Key; spec = $entry.Value }) | Out-Null
            }
            elseif ($entry.Value -is [System.Collections.IDictionary]) {
                $pending.Push($entry.Value)
            }
        }
    }

    foreach ($item in $specs) {
        $checked++
        $line = & $findLine $item.name $item.spec
        $spec = $item.spec.Trim() -replace '^npm:@?[^@]+@?', ''
        if ($spec -match '^[A-Za-z][A-Za-z0-9+.-]*://') {
            $finding = Get-SourceUrlFinding -Url $spec -HostSet $NpmHosts -RejectQuery
            if ($finding) {
                $findings.Add([pscustomobject]@{ line = $line; rule = $finding.rule; reason = $finding.reason }) | Out-Null
            }
        }
        elseif ($spec -match '^(?:github|gitlab|bitbucket|gist|git)[:+]' -or
            $spec -match '^[^@\s/:]+@[^\s/:]+:' -or
            $spec -match '^[A-Za-z0-9][\w.-]*/[\w.-]+(?:#.*)?$') {
            $findings.Add([pscustomobject]@{ line = $line; rule = 'npm-spec-not-registry'; reason = 'npm dependencies must resolve from the public npm registry' }) | Out-Null
        }
    }

    $publishConfig = $manifest['publishConfig']
    if ($publishConfig -is [System.Collections.IDictionary] -and $publishConfig.Contains('registry')) {
        $registry = [string]$publishConfig['registry']
        $line = & $findLine 'registry' $registry
        $finding = if ($registry -notmatch '^[A-Za-z][A-Za-z0-9+.-]*://') {
            [pscustomobject]@{ rule = 'npm-registry-nonliteral'; reason = 'npm registry values must be literal public HTTPS URLs' }
        }
        else {
            Get-SourceUrlFinding -Url $registry -HostSet $NpmHosts -RejectQuery -NpmRegistryDeclaration
        }
        if ($finding) {
            $findings.Add([pscustomobject]@{ line = $line; rule = $finding.rule; reason = $finding.reason }) | Out-Null
        }
    }

    # Metadata URLs such as homepage and bugs may use any host but must not embed credentials.
    $metadata = [System.Collections.Generic.Stack[object]]::new()
    foreach ($entry in $manifest.GetEnumerator()) {
        if ($entry.Key -notin $dependencyFields -and $entry.Key -ne 'publishConfig') {
            $metadata.Push($entry)
        }
    }
    while ($metadata.Count -gt 0) {
        $entry = $metadata.Pop()
        if ($entry.Value -is [System.Collections.IDictionary]) {
            foreach ($child in $entry.Value.GetEnumerator()) { $metadata.Push($child) }
        }
        elseif ($entry.Value -is [System.Collections.IList]) {
            foreach ($item in $entry.Value) { $metadata.Push([pscustomobject]@{ Key = $entry.Key; Value = $item }) }
        }
        elseif ($entry.Value -is [string]) {
            $uri = $null
            if ([uri]::TryCreate(($entry.Value -replace '^git\+', ''), [UriKind]::Absolute, [ref]$uri) -and
                -not [string]::IsNullOrEmpty($uri.UserInfo)) {
                $line = & $findLine $entry.Key $entry.Value
                $findings.Add([pscustomobject]@{ line = $line; rule = 'url-credentials'; reason = 'dependency source URLs must not contain credentials' }) | Out-Null
            }
        }
    }

    return [pscustomobject]@{ checked = $checked; findings = $findings }
}

function Invoke-PublicDependencyFeedScan {
    <#
    .SYNOPSIS
        Scans dependency metadata and returns validation results.
    .OUTPUTS
        [pscustomobject]
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string]$RepoRoot
    )

    $allowedHosts = @(
        'api.github.com',
        'api.nuget.org',
        'crates.io',
        'download-r2.pytorch.org',
        'download.pytorch.org',
        'files.pythonhosted.org',
        'github.com',
        'github-releases.githubusercontent.com',
        'index.crates.io',
        'objects.githubusercontent.com',
        'powershellgallery.com',
        'proxy.golang.org',
        'pypi.org',
        'registry.npmjs.org',
        'repo.packagist.org',
        'rubygems.org',
        'static.crates.io',
        'sum.golang.org',
        'www.nuget.org',
        'www.powershellgallery.com'
    )
    $npmHosts = @('registry.npmjs.org')
    $pythonHosts = @('download-r2.pytorch.org', 'download.pytorch.org', 'files.pythonhosted.org', 'pypi.org')
    $npmLockNames = @('package-lock.json', 'npm-shrinkwrap.json')

    $pathPattern = '(^|/)(package\.json|package-lock\.json|npm-shrinkwrap\.json|uv\.lock|pyproject\.toml|requirements[^/]*\.txt|\.npmrc)$'
    $violations = [System.Collections.Generic.List[object]]::new()
    $sourceCount = 0

    # Violations never carry source values so credentials in URLs cannot leak into logs.
    $addViolation = {
        param([string]$File, [int]$Line, [string]$Rule, [string]$Reason)
        $violations.Add([pscustomobject]@{
                file   = $File
                line   = $Line
                rule   = $Rule
                reason = $Reason
            }) | Out-Null
    }

    $trackedFiles = @(& git -C $RepoRoot ls-files --cached --others --exclude-standard | Where-Object {
            $_ -match $pathPattern
        })
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files failed while discovering dependency metadata.'
    }

    foreach ($relativePath in $trackedFiles) {
        $fullPath = Join-Path $RepoRoot $relativePath
        if (-not (Test-Path -LiteralPath $fullPath)) {
            continue
        }

        $leafName = Split-Path -Leaf $relativePath
        $lines = @(Get-Content -LiteralPath $fullPath)
        $isNpmLock = $leafName -in $npmLockNames
        $ecosystemHosts = switch -Regex ($leafName) {
            '^(package-lock|npm-shrinkwrap)\.json$' { $npmHosts }
            '^(uv\.lock|pyproject\.toml)$' { $pythonHosts }
            default { $null }
        }

        if ($isNpmLock) {
            $lockResult = Test-NpmLockIntegrity -Lines $lines
            $sourceCount += $lockResult.checked
            foreach ($finding in $lockResult.findings) {
                & $addViolation $relativePath $finding.line $finding.rule $finding.reason
            }
        }

        if ($leafName -eq 'package.json') {
            $manifestResult = Get-NpmManifestFindings -Lines $lines -NpmHosts $npmHosts
            $sourceCount += $manifestResult.checked
            foreach ($finding in $manifestResult.findings) {
                & $addViolation $relativePath $finding.line $finding.rule $finding.reason
            }
            continue
        }

        for ($index = 0; $index -lt $lines.Count; $index++) {
            $line = $lines[$index]
            $lineNumber = $index + 1

            $setting = Get-InsecureSettingRule -LeafName $leafName -Line $line
            if ($setting) {
                & $addViolation $relativePath $lineNumber $setting.rule $setting.reason
            }

            if (-not (Test-DependencySourceLine -Path $relativePath -Line $line)) {
                continue
            }

            $urls = @([regex]::Matches($line, '[A-Za-z][A-Za-z0-9+.-]*://[^\s"''<>\)\],]+') | ForEach-Object {
                    $_.Value -replace '^git\+', ''
                })

            if ($leafName -eq 'uv.lock' -and $line -match '\burl\s*=' -and $line -notmatch '^\s*source\s*=') {
                $sourceCount++
                if ($line -notmatch 'hash\s*=\s*"(?:sha256:[0-9a-f]{64}|sha384:[0-9a-f]{96}|sha512:[0-9a-f]{128})"') {
                    & $addViolation $relativePath $lineNumber 'uv-hash-invalid' 'uv.lock artifacts must carry a sha256 or stronger hash'
                }
            }

            $isNpmRegistryDeclaration = $leafName -eq '.npmrc'
            if ($isNpmRegistryDeclaration -and $urls.Count -eq 0) {
                & $addViolation $relativePath $lineNumber 'npm-registry-nonliteral' 'npm registry values must be literal public HTTPS URLs'
                continue
            }

            $hostSet = if ($isNpmRegistryDeclaration) { $npmHosts } elseif ($ecosystemHosts) { $ecosystemHosts } else { $allowedHosts }
            $rejectQuery = $isNpmLock -or $isNpmRegistryDeclaration -or $null -ne $ecosystemHosts

            foreach ($url in $urls) {
                $sourceCount++
                $finding = Get-SourceUrlFinding -Url $url -HostSet $hostSet -RejectQuery:$rejectQuery -NpmRegistryDeclaration:$isNpmRegistryDeclaration
                if ($finding) {
                    & $addViolation $relativePath $lineNumber $finding.rule $finding.reason
                }
            }
        }
    }

    return [pscustomobject]@{
        filesScanned     = $trackedFiles.Count
        sourcesValidated = $sourceCount
        allowedHosts     = $allowedHosts
        violationCount   = $violations.Count
        violations       = $violations
    }
}

#endregion Functions

#region Main Execution

if ($MyInvocation.InvocationName -ne '.') {
    try {
        if (-not $OutputPath) {
            $OutputPath = Join-Path $RepoRoot 'logs/public-dependency-feeds-results.json'
        }

        $outputDirectory = Split-Path -Parent $OutputPath
        if ($outputDirectory -and -not (Test-Path -LiteralPath $outputDirectory)) {
            New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
        }

        $result = Invoke-PublicDependencyFeedScan -RepoRoot $RepoRoot
        $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $OutputPath -Encoding utf8

        if ($result.violationCount -gt 0) {
            Write-Host 'Dependency feed policy violations:' -ForegroundColor Red
            foreach ($violation in $result.violations) {
                Write-Host ("  {0}:{1} [{2}] {3}" -f $violation.file, $violation.line, $violation.rule, $violation.reason) -ForegroundColor Red
            }
            Write-Host "Results: $OutputPath" -ForegroundColor Yellow
            if ($FailOnViolation) {
                exit 1
            }
        }
        else {
            Write-Host ("OK: {0} dependency source(s) across {1} file(s) use approved public feeds." -f $result.sourcesValidated, $result.filesScanned) -ForegroundColor Green
            Write-Host "Results: $OutputPath"
        }

        exit 0
    }
    catch {
        Write-Error -ErrorAction Continue "Test-PublicDependencyFeeds failed: $($_.Exception.Message)"
        exit 2
    }
}

#endregion Main Execution
