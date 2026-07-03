#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0

<#
.SYNOPSIS
    Verifies and reports on SHA pinning compliance for supply chain security.

.DESCRIPTION
    Cross-platform PowerShell script that analyzes GitHub Actions workflows, package manifests,
    workflow-YAML container image references, and other dependency declarations to verify compliance
    with SHA pinning security practices. Identifies unpinned dependencies and provides remediation
    guidance. Dockerfile base-image pinning is covered by OpenSSF Scorecard, not this scanner.

.PARAMETER Path
    Root path to scan for dependency files. Defaults to current directory.

.PARAMETER Recursive
    Scan recursively through subdirectories. Default is true.

.PARAMETER Format
    Output format for compliance report. Options: json, sarif, csv, markdown, table.
    Default is 'json' for programmatic processing.

.PARAMETER OutputPath
    Path where compliance results should be saved. Defaults to 'dependency-pinning-report.json'
    in the current directory.

.PARAMETER FailOnUnpinned
    Exit with error code if pinning violations are found. Default is false for reporting mode.

.PARAMETER ExcludePaths
    Additional comma-separated paths to exclude from scanning (glob patterns supported).

.PARAMETER IncludeTypes
    Comma-separated list of dependency types to check. Options include: github-actions, npm,
    pip, shell-downloads, shell-inline-pip, gh-extension, powershell-modules, docker. Default is all types.

.PARAMETER Threshold
    Minimum compliance score percentage required for passing grade (0-100).
    Script will exit with code 1 if compliance falls below threshold when -FailOnUnpinned is set.
    Default is 95%.

.PARAMETER Remediate
    Generate remediation suggestions with specific SHA pins for unpinned dependencies.

.EXAMPLE
    ./Test-DependencyPinning.ps1
    Scan current directory for dependency pinning compliance.

.EXAMPLE
    ./Test-DependencyPinning.ps1 -Path "/workspace" -Format "sarif" -FailOnUnpinned
    Scan workspace directory, output SARIF format, fail on violations.

.EXAMPLE
    ./Test-DependencyPinning.ps1 -IncludeTypes "github-actions,pip" -Remediate
    Check only GitHub Actions and pip dependencies with remediation suggestions.

.EXAMPLE
    ./Test-DependencyPinning.ps1 -Threshold 90 -FailOnUnpinned
    Enforce 90% compliance threshold and fail build if not met.

.EXAMPLE
    ./Test-DependencyPinning.ps1 -Threshold 100 -IncludeTypes "github-actions"
    Require 100% SHA pinning for GitHub Actions only.

.EXAMPLE
    ./Test-DependencyPinning.ps1 -Threshold 80
    Report compliance against 80% threshold but continue on violations.

.NOTES
    Requires:
    - PowerShell 7.0 or later for cross-platform compatibility
    - Internet connectivity for SHA resolution (with -Remediate)
    - GitHub API access for action SHA resolution (optional)

    Compatible with:
    - Windows PowerShell 5.1+ (limited cross-platform features)
    - PowerShell 7.x on Windows, Linux, macOS
    - GitHub Actions runners (ubuntu-latest, windows-latest, macos-latest)
    - Azure DevOps agents (Microsoft-hosted and self-hosted)

.LINK
    https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions#using-third-party-actions
#>

# Import security classes from shared module
using module ./Modules/SecurityClasses.psm1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Path = ".",

    [Parameter(Mandatory = $false)]
    [switch]$Recursive,

    [Parameter(Mandatory = $false)]
    [ValidateSet('json', 'sarif', 'csv', 'markdown', 'table')]
    [string]$Format = 'json',

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = 'logs/dependency-pinning-results.json',

    [Parameter(Mandatory = $false)]
    [switch]$FailOnUnpinned,

    [Parameter(Mandatory = $false)]
    [string]$ExcludePaths = "",

    [Parameter(Mandatory = $false)]
    [string]$IncludeTypes = "github-actions,npm,pip,shell-downloads,shell-inline-pip,gh-extension,powershell-modules,docker",

    [Parameter(Mandatory = $false)]
    [ValidateRange(0, 100)]
    [int]$Threshold = 95,

    [Parameter(Mandatory = $false)]
    [switch]$Remediate
)

$ErrorActionPreference = 'Stop'

# Import CIHelpers for workflow command escaping
Import-Module (Join-Path $PSScriptRoot '../lib/Modules/CIHelpers.psm1') -Force

# Define dependency patterns for different ecosystems
$DependencyPatterns = @{
    'github-actions' = @{
        FilePatterns    = @('**/.github/workflows/*.yml', '**/.github/workflows/*.yaml',
            '**/.github/actions/**/*.yml', '**/.github/actions/**/*.yaml')
        VersionPatterns = @(
            @{
                Pattern     = 'uses:\s*([^@\s]+)@([^#\s]+)'
                Groups      = @{ Action = 1; Version = 2 }
                Description = 'GitHub Actions uses statements'
            }
        )
        SHAPattern      = '^[a-fA-F0-9]{40}$'
        RemediationUrl  = 'https://api.github.com/repos/{0}/commits/{1}'
    }

    'npm'            = @{
        FilePatterns   = @('**/package.json')
        ValidationFunc = 'Get-NpmDependencyViolations'
        PinPattern     = '^\d+\.\d+\.\d+'
        RemediationUrl = 'https://registry.npmjs.org/{0}/{1}'
    }

    'pip'              = @{
        FilePatterns   = @('**/requirements*.txt', '**/Pipfile', '**/pyproject.toml', '**/setup.py')
        ValidationFunc = 'Get-PipDependencyViolations'
        PinPattern     = '^.+==.+'
        RemediationUrl = 'https://pypi.org/pypi/{0}/{1}/json'
    }

    'shell-downloads'  = @{
        FilePatterns   = @('**/*.sh', '**/workflows/**/*.yml', '**/workflows/**/*.yaml')
        ValidationFunc = 'Test-ShellDownloadSecurity'
        Description    = 'Shell script and workflow run: downloads must include checksum verification'
    }

    'gh-extension'     = @{
        FilePatterns   = @('**/.github/workflows/*.yml', '**/.github/workflows/*.yaml', '**/*.sh')
        ValidationFunc = 'Get-GhExtensionPinViolations'
        Description    = 'gh extension install must pin a released ref with --pin'
    }

    'powershell-modules' = @{
        FilePatterns   = @('**/.github/workflows/*.yml', '**/.github/workflows/*.yaml', '**/*.ps1', '**/*.psm1')
        ValidationFunc = 'Get-PowerShellModuleViolations'
        Description    = 'Install-Module must pin an exact version with -RequiredVersion'
    }

    'shell-inline-pip' = @{
        FilePatterns   = @('**/workflows/**/*.yaml', '**/workflows/**/*.yml', '**/*.sh')
        ValidationFunc = 'Get-ShellInlinePipViolations'
        PinPattern     = '^.+==.+'
        RemediationUrl = 'https://pypi.org/pypi/{0}/{1}/json'
        Description    = 'Inline pip/uv pip installs in workflow YAML and shell scripts must be exact-pinned (==) or lock-derived'
    }

    'docker'           = @{
        FilePatterns   = @('**/workflows/**/*.yaml', '**/workflows/**/*.yml',
            '**/infrastructure/setup/manifests/*.yaml', '**/infrastructure/setup/manifests/*.yml',
            '**/infrastructure/setup/values/*.yaml', '**/infrastructure/setup/values/*.yml')
        ValidationFunc = 'Get-DockerImageViolations'
        Description    = 'Container image references in workflow YAML, Kubernetes manifests, and Helm values must be digest-pinned (@sha256)'
    }
}

# DependencyViolation and ComplianceReport classes moved to ./Modules/SecurityClasses.psm1

function Test-ShellDownloadSecurity {
    <#
    .SYNOPSIS
        Scans shell scripts for curl/wget downloads lacking checksum verification.

    .DESCRIPTION
        Analyzes shell scripts to detect download commands (curl/wget) that do not
        have corresponding checksum verification (sha256sum/shasum) within the
        following lines.

    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $FilePath = $FileInfo.Path

    if (-not (Test-Path $FilePath)) {
        return @()
    }

    $lines = @(Get-Content -Path $FilePath)
    if ($null -eq $lines -or $lines.Count -eq 0) {
        return @()
    }
    
    # Process logical lines to handle split commands cleanly
    $logicalLines = @(Join-LineContinuations -Lines @($lines) -ContinuationPattern '\\\s*$')
    if ($null -eq $logicalLines) {
        return @()
    }
    $violations = @()

    # Pattern to match curl/wget download commands
    $downloadPattern = '(curl|wget)\s+.*https?://[^\s]+'
    $checksumPattern = 'sha256sum|shasum|Get-FileHash|openssl\s+dgst\s+-sha256|sha256sum\s+-c|verify_sha256'

    $prevWasIgnoreComment = $false
    for ($i = 0; $i -lt $logicalLines.Count; $i++) {
        $logical = $logicalLines[$i]
        $lineText = $logical.Text

        # Inline exemption: a `# pinning-ignore` comment trailing the download line, or a
        # dedicated `# pinning-ignore` comment line directly above it. The `#` must start the
        # comment (at line start or after whitespace), so the marker can't be smuggled in via a
        # `#` inside a URL/filename on a non-comment line. Lets an intentionally unchecksummed
        # download (e.g. a GPG-signed apt repo fetched over a dynamic, distro-dependent URL
        # with no stable digest to verify) opt out, mirroring the shell-inline-pip exemption.
        $hasIgnore = $lineText -match '(^|\s)#[^\n]*pinning-ignore'
        $exempt = $hasIgnore -or $prevWasIgnoreComment
        $prevWasIgnoreComment = $hasIgnore -and $lineText.TrimStart().StartsWith('#')

        # A pure comment line is never an executed download (mirrors Get-ShellInlinePipViolations).
        if ($lineText.TrimStart().StartsWith('#')) { continue }
        if ($lineText -notmatch $downloadPattern) { continue }
        if ($exempt) { continue }

        # Check the download line and the next 5 lines for checksum verification
        $hasChecksum = $false
        $searchEnd = [Math]::Min($i + 5, $logicalLines.Count - 1)

        for ($j = $i; $j -le $searchEnd; $j++) {
            if ($logicalLines[$j].Text -match $checksumPattern) {
                $hasChecksum = $true
                break
            }
        }

        if (-not $hasChecksum) {
            $violation = [DependencyViolation]::new()
            $violation.File = $FileInfo.RelativePath
            $violation.Line = $logical.Line
            $violation.Type = $FileInfo.Type
            $violation.Name = $lineText.Trim()
            $violation.Severity = 'warning'
            $violation.Description = 'Download without checksum verification'
            $violation.Metadata = @{ Pattern = $lineText.Trim() }
            $violations += $violation
        }
    }

    return $violations
}

function Get-PyprojectSpecViolation {
    <#
    .SYNOPSIS
        Builds pip pinning violations for every quoted specifier in a pyproject array segment.
    .DESCRIPTION
        Parses each double-quoted specifier found in the supplied text and returns a
        [DependencyViolation] for any that is not exactly == pinned. Handles multiple
        specifiers on one line (single-line arrays such as fuzz = ["atheris>=3.1.0"]),
        bare package names, and the self-referencing project name.
    .PARAMETER LineText
        Array segment to scan (the remainder after [ on an opener line, or a full array line).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$LineText,
        [Parameter(Mandatory)]
        [int]$LineNumber,
        [Parameter(Mandatory)]
        [string]$SectionName,
        [string]$ProjectName,
        [Parameter(Mandatory)]
        [string]$RelativePath,
        [Parameter(Mandatory)]
        [string]$Type
    )

    $violations = @()

    foreach ($specMatch in [regex]::Matches($LineText, '"([^"]+)"')) {
        $spec = $specMatch.Groups[1].Value.Trim()

        # Extract package name and version spec
        if ($spec -notmatch '^([a-zA-Z0-9_][\w.\-]*)(.*)$') {
            continue
        }

        $packageName = $Matches[1]
        $versionSpec = $Matches[2].Trim()

        # Skip self-referencing extras (e.g. "mypackage[dev,test]")
        if ($ProjectName -and $packageName -eq $ProjectName) {
            continue
        }

        $violation = [DependencyViolation]::new()
        $violation.File = $RelativePath
        $violation.Line = $LineNumber
        $violation.Type = $Type
        $violation.Name = $packageName
        $violation.Severity = 'warning'
        $violation.Metadata = @{ Section = $SectionName; Format = 'pyproject.toml' }

        # Entries with no version constraint (bare package names) are unpinned
        if ([string]::IsNullOrWhiteSpace($versionSpec)) {
            $violation.Version = '(none)'
            $violation.Description = "Unpinned pip dependency in $SectionName (no version specified)"
            $violations += $violation
            continue
        }

        # Pinned means exactly ==version (may include extras like [extra])
        if ($versionSpec -notmatch '^(\[[\w,]+\])?\s*==\s*\S+') {
            $violation.Version = $versionSpec
            $violation.Description = "Unpinned pip dependency in $SectionName"
            $violations += $violation
        }
    }

    return $violations
}

function Get-PipDependencyViolations {
    <#
    .SYNOPSIS
        Analyzes Python dependency files for unpinned pip dependencies.
    .DESCRIPTION
        Handles requirements*.txt (line-based), pyproject.toml (TOML array),
        Pipfile, and setup.py. Checks that each dependency uses the ==
        equality pin operator.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    $lines = @(Get-Content -Path $filePath)
    $fileName = Split-Path $filePath -Leaf

    if ($fileName -match 'pyproject\.toml$') {
        # Extract project name to skip self-referencing extras groups
        $projectName = ''
        foreach ($l in $lines) {
            if ($l -match '^\s*name\s*=\s*"([^"]+)"') {
                $projectName = $Matches[1]
                break
            }
        }

        # Parse pyproject.toml dependency arrays
        $inDependencySection = $false
        $inArray = $false
        $sectionName = ''

        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]

            # Detect dependency array start: dependencies = [ or optional-dependencies.*= [
            if ($line -match '^\s*(dependencies)\s*=\s*\[(.*)$' -or
                ($line -match '^\s*(\w[\w-]*)\s*=\s*\[(.*)$' -and $inDependencySection)) {
                $inArray = $true
                $sectionName = $Matches[1]
                $remainder = $Matches[2]

                # Parse specifiers on the opener line itself (single-line arrays)
                $violations += Get-PyprojectSpecViolation -LineText $remainder -LineNumber ($i + 1) `
                    -SectionName $sectionName -ProjectName $projectName -RelativePath $relativePath -Type $type

                # A closing bracket on the same line terminates the array
                if ($remainder -match '\]') {
                    $inArray = $false
                }
                continue
            }

            # Detect dependency-bearing sections
            if ($line -match '^\s*\[(project\.optional-dependencies)\]') {
                $inDependencySection = $true
                continue
            }
            elseif ($line -match '^\s*\[project\]') {
                $inDependencySection = $true
                continue
            }
            elseif ($line -match '^\s*\[dependency-groups\]') {
                $inDependencySection = $true
                continue
            }
            elseif ($line -match '^\s*\[' -and $line -notmatch '^\s*\[project' -and $line -notmatch '^\s*\[dependency-groups') {
                $inDependencySection = $false
                $inArray = $false
                continue
            }

            # End of array
            if ($inArray -and $line -match '^\s*\]') {
                $inArray = $false
                continue
            }

            # Inside array — parse every quoted dependency specifier on the line
            if ($inArray -and $line -match '"') {
                $violations += Get-PyprojectSpecViolation -LineText $line -LineNumber ($i + 1) `
                    -SectionName $sectionName -ProjectName $projectName -RelativePath $relativePath -Type $type
            }
        }
    }
    else {
        # requirements*.txt, Pipfile, setup.py — line-based format
        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = $lines[$i].Trim()

            # Skip comments and blank lines
            if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#') -or $line.StartsWith('-')) {
                continue
            }

            # Extract package name and version spec
            if ($line -match '^([a-zA-Z0-9_][\w.\-]*)(.*)$') {
                $packageName = $Matches[1]
                $versionSpec = $Matches[2].Trim()

                # Skip bare package names with no version
                if ([string]::IsNullOrWhiteSpace($versionSpec)) {
                    $violation = [DependencyViolation]::new()
                    $violation.File = $relativePath
                    $violation.Line = $i + 1
                    $violation.Type = $type
                    $violation.Name = $packageName
                    $violation.Version = '(none)'
                    $violation.Severity = 'warning'
                    $violation.Description = 'Unpinned pip dependency (no version specified)'
                    $violation.Metadata = @{ Format = $fileName }
                    $violations += $violation
                    continue
                }

                # Pinned means ==version
                $isPinned = $versionSpec -match '^(\[[\w,]+\])?\s*==\s*\S+'

                if (-not $isPinned) {
                    $violation = [DependencyViolation]::new()
                    $violation.File = $relativePath
                    $violation.Line = $i + 1
                    $violation.Type = $type
                    $violation.Name = $packageName
                    $violation.Version = $versionSpec
                    $violation.Severity = 'warning'
                    $violation.Description = 'Unpinned pip dependency'
                    $violation.Metadata = @{ Format = $fileName }
                    $violations += $violation
                }
            }
        }
    }

    return $violations
}

function Join-LineContinuations {
    <#
    .SYNOPSIS
        Folds shell / PowerShell line continuations into logical lines, tracking each line's start.
    .DESCRIPTION
        A trailing continuation marker (bash `\`, PowerShell backtick) joins a command split across
        physical lines into one logical line so a single-line scan sees the whole command. Each
        result records the 1-based physical line where the logical line began, for violation
        reporting.
    .PARAMETER Lines
        The physical lines of a file.
    .PARAMETER ContinuationPattern
        Regex matching a trailing continuation marker (e.g. '\\\s*$' for bash only, or '[\\`]\s*$'
        for bash and PowerShell).
    .OUTPUTS
        Array of @{ Text = <joined logical line>; Line = <1-based start line> }.
    #>
    [CmdletBinding()]
    param(
        [string[]]$Lines = @(),

        [Parameter(Mandatory)]
        [string]$ContinuationPattern
    )

    $logicalLines = @()
    $buffer = ''
    $startLine = 0
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($buffer -eq '') { $startLine = $i + 1 }
        $current = $Lines[$i]
        if ($current -match $ContinuationPattern) {
            $buffer += ($current -replace $ContinuationPattern, ' ')
        }
        else {
            $buffer += $current
            $logicalLines += @{ Text = $buffer; Line = $startLine }
            $buffer = ''
        }
    }
    if ($buffer -ne '') { $logicalLines += @{ Text = $buffer; Line = $startLine } }

    return $logicalLines
}

function Remove-ShellTrailingComment {
    <#
    .SYNOPSIS
        Removes a trailing shell comment from one command segment.
    .DESCRIPTION
        Strips comments that begin after whitespace so scanners do not treat pinning flags
        mentioned in comments as command arguments.
    .PARAMETER Command
        Command segment to normalize.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Command
    )

    return ($Command -split '\s+#', 2)[0].Trim()
}

function Get-ShellInlinePipViolations {
    <#
    .SYNOPSIS
        Detects unpinned inline pip / uv pip install invocations embedded in workflow YAML and shell scripts.
    .DESCRIPTION
        OSMO (and similar) workflow YAML embed shell commands in task args that run
        'pip install' / 'uv pip install'. Each installed package must be exact-pinned
        with == (including shell-variable pins like name=="${VAR}"), installed from a
        lock/requirement (-r FILE, -r -, --requirement, or a 'uv export | uv pip install'
        pipe), or be an editable local project (-e .). Extra package arguments after a
        requirement file are still checked. Bare names and range specifiers are violations.
        Build-frontend tools (pip/setuptools/wheel) are allowlisted.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    # Build-frontend tools tolerated unpinned (idiomatic `pip install --upgrade pip ...`)
    $allowlist = @('pip', 'setuptools', 'wheel')
    # Flags that consume the following token as their value
    $valueFlags = @('--index-url', '--extra-index-url', '-i', '--timeout', '--retries',
        '-c', '--constraint', '-r', '--requirement', '--find-links', '-f', '-e',
        '--editable', '--prefix', '--target', '--index-strategy', '--python-version',
        '--project', '-p', '--torch-backend')

    $lines = @(Get-Content -Path $filePath)

    # Bash continuation only ('\'): a trailing backtick here is command substitution, not a join.
    $logicalLines = Join-LineContinuations -Lines @($lines) -ContinuationPattern '\\\s*$'

    $installRegex = '(?:uv\s+pip|pip3?|python3?\s+-m\s+pip)\s+install\b'
    # uv run / uvx / uv tool run, anchored to command position so binary-install
    # lines like `install /tmp/.../uvx /usr/local/bin/uvx` do not match.
    $uvRunAnchored = '^(?:sudo\s+)?(?:uvx|uv\s+tool\s+run|uv\s+run)\b'

    # Shared compliance check for a single package spec. Emits a violation object
    # (or $null when compliant). Build-frontend tools are allowlisted; exact ==
    # pins (including name=="${VAR}") pass; bare names and ranges fail.
    $testSpec = {
        param([string]$specRaw, [int]$lineNo, [string]$cmdText, [string]$source)

        $spec = $specRaw.Trim('"', "'").Trim()
        if ([string]::IsNullOrWhiteSpace($spec)) { return $null }
        if ($spec.StartsWith('-')) { return $null }
        if ($spec -match '^\$' -or $spec -match '^https?://' -or
            $spec -match '^[./~]' -or $spec -match '^[<>|&]') {
            return $null
        }
        if ($spec -notmatch '^([A-Za-z0-9][A-Za-z0-9._-]*)') { return $null }
        $packageName = $Matches[1]
        if ($allowlist -contains $packageName.ToLower()) { return $null }
        if ($spec -match '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?==') { return $null }

        $versionSpec = ($spec -replace '^[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]+\])?', '')
        if ([string]::IsNullOrWhiteSpace($versionSpec)) { $versionSpec = '(none)' }

        $v = [DependencyViolation]::new()
        $v.File = $relativePath
        $v.Line = $lineNo
        $v.Type = $type
        $v.Name = $packageName
        $v.Version = $versionSpec
        $v.Severity = 'warning'
        $v.Description = "Unpinned inline pip dependency via $source (use ==, -r/--requirement lockfile, or uv export)"
        $v.Metadata = @{ Format = (Split-Path $filePath -Leaf); LineContent = $cmdText }
        return $v
    }

    $prevWasIgnoreComment = $false
    foreach ($logical in $logicalLines) {
        $lineText = $logical.Text
        # The marker must sit in a `#` comment (the `#` at line start or after whitespace), not
        # merely appear after a `#` inside an install argument/URL.
        $hasIgnore = $lineText -match '(^|\s)#[^\n]*pinning-ignore'

        # Inline exemption: `# pinning-ignore` trailing the install (or its continued lines),
        # or a dedicated `# pinning-ignore` comment line directly above the install. Lets an
        # intentional non-pin opt out of this check (e.g. an Isaac-ABI numpy range).
        $exempt = $hasIgnore -or $prevWasIgnoreComment
        $prevWasIgnoreComment = $hasIgnore -and $lineText.TrimStart().StartsWith('#')

        if ($lineText -notmatch 'install|uv\s+run|uvx|uv\s+tool\s+run') { continue }
        if ($exempt) { continue }

        # Split into shell commands on &&, ||, ; (NOT single | so 'uv export | uv pip install' stays intact)
        $commands = $lineText -split '\s*(?:&&|\|\||;)\s*'

        foreach ($command in $commands) {
            $cmdTrim = $command.TrimStart()
            if ($cmdTrim.StartsWith('#')) { continue }

            # Strip inline comments to prevent comment words from being tokenized as packages
            $command = $command -replace '\s+#.*$', ''

            if ($command -match $installRegex) {
                # Isolate the package arguments (everything after the install keyword)
                $installArgs = ($command -replace '^.*?\b(?:uv\s+pip|pip3?|python3?\s+-m\s+pip)\s+install\b', '').Trim()
                $tokens = [regex]::Matches($installArgs, '"[^"]*"|''[^'']*''|\S+') | ForEach-Object { $_.Value }

                $skipNext = $false
                foreach ($rawToken in $tokens) {
                    if ($skipNext) { $skipNext = $false; continue }
                    $token = $rawToken.Trim('"', "'").Trim()
                    if ($token.StartsWith('-')) {
                        if ($valueFlags -contains $token) { $skipNext = $true }
                        continue
                    }
                    $result = & $testSpec $rawToken $logical.Line $command.Trim() 'pip install'
                    if ($null -ne $result) { $violations += $result }
                }
            }
            elseif ($cmdTrim -match $uvRunAnchored) {
                # uv run / uvx / uv tool run with ephemeral deps: each --with SPEC must be pinned.
                # --with-requirements FILE is lock-derived (compliant); --with-editable is a local project (compliant).
                $tokens = [regex]::Matches($command, '"[^"]*"|''[^'']*''|\S+') | ForEach-Object { $_.Value }
                for ($t = 0; $t -lt $tokens.Count; $t++) {
                    $tok = $tokens[$t].Trim('"', "'")
                    if ($tok -eq '--with' -and ($t + 1) -lt $tokens.Count) {
                        $result = & $testSpec $tokens[$t + 1] $logical.Line $command.Trim() 'uv run --with'
                        if ($null -ne $result) { $violations += $result }
                    }
                    elseif ($tok -match '^--with=(.+)$') {
                        $result = & $testSpec $Matches[1] $logical.Line $command.Trim() 'uv run --with'
                        if ($null -ne $result) { $violations += $result }
                    }
                }
            }
        }
    }

    return $violations
}

function Get-GhExtensionPinViolations {
    <#
    .SYNOPSIS
        Detects `gh extension install` invocations that do not pin a released ref with --pin.
    .DESCRIPTION
        `gh extension install <owner>/<repo>` tracks the extension's default-branch HEAD. A
        released tag or commit must be pinned with `--pin <ref>`. Comment lines and a trailing
        `# pinning-ignore` opt out. Lives in CI/agent workflow YAML and shell setup scripts.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    $installRegex = '\bgh\s+ext(?:ension)?\s+install\b'
    # Bash continuation only ('\'): gh runs in .sh files and default-shell run: blocks, where a
    # trailing backtick is command substitution, not a join (mirrors Get-ShellInlinePipViolations).
    $logicalLines = Join-LineContinuations -Lines @(Get-Content -Path $filePath) -ContinuationPattern '\\\s*$'

    $prevWasIgnoreComment = $false
    foreach ($logical in $logicalLines) {
        $line = $logical.Text
        
        $hasIgnore = $line -match '(^|\s)#[^\n]*pinning-ignore'
        $exempt = $hasIgnore -or $prevWasIgnoreComment
        $prevWasIgnoreComment = $hasIgnore -and $line.TrimStart().StartsWith('#')

        if ($line -notmatch $installRegex) { continue }
        if ($line.TrimStart().StartsWith('#')) { continue }
        if ($exempt) { continue }

        $segments = $line -split '\s*(?:;|\|\||&&)\s*'
        foreach ($segment in $segments) {
            $commandText = Remove-ShellTrailingComment -Command $segment
            if ($commandText -notmatch $installRegex) { continue }
            if ($commandText -match '(?:^|\s)--pin(?:=|\s+)\S+') { continue }

            # First non-flag token after `install` is the extension reference.
            $afterInstall = ($commandText -replace ('^.*?' + $installRegex), '').Trim()
            $name = ($afterInstall -split '\s+' | Where-Object { $_ -and -not $_.StartsWith('-') } | Select-Object -First 1)
            if (-not $name) { $name = 'gh-extension' }

            $v = [DependencyViolation]::new()
            $v.File = $relativePath
            $v.Line = $logical.Line
            $v.Type = $type
            $v.Name = $name
            $v.Version = '(unpinned)'
            $v.Severity = 'warning'
            $v.Description = 'gh extension install without --pin (tracks extension HEAD)'
            $v.Metadata = @{ Format = (Split-Path $filePath -Leaf); LineContent = $commandText }
            $violations += $v
        }
    }

    return $violations
}

function Get-PowerShellModuleViolations {
    <#
    .SYNOPSIS
        Detects `Install-Module` invocations that do not pin an exact version with -RequiredVersion.
    .DESCRIPTION
        Install-Module without -RequiredVersion resolves the latest gallery version at run time.
        Only command-position invocations are flagged: a `Mock Install-Module`, a line comment,
        an inline string mention (e.g. a Write-Warning hint), and any mention inside a block
        comment or a single- or double-quoted here-string are ignored. A trailing `# pinning-ignore`
        opts out. -MinimumVersion/-MaximumVersion are ranges, not pins, so they still fail.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    # Coalesce bash (\) and PowerShell (`) continuations so a -RequiredVersion on the next line is seen.
    $logicalLines = Join-LineContinuations -Lines @(Get-Content -Path $filePath) -ContinuationPattern '[\\`]\s*$'

    $prevWasIgnoreComment = $false
    $inBlockComment = $false
    $hereTerminator = $null
    foreach ($logical in $logicalLines) {
        $line = $logical.Text

        # Skip here-string bodies (@'...'@ / @"..."@): their content is string data, not a
        # command. The closing sequence must begin the line per PowerShell parsing rules.
        if ($null -ne $hereTerminator) {
            if ($line.TrimStart().StartsWith($hereTerminator)) { $hereTerminator = $null }
            continue
        }
        if ($line -match "@['""]\s*$") {
            $hereTerminator = if ($line -match "@'\s*$") { "'@" } else { '"@' }
            continue
        }

        # Skip block-comment bodies (<# ... #>) so documentation mentioning Install-Module is
        # not treated as a live invocation.
        if ($inBlockComment) {
            if ($line -match '#>') { $inBlockComment = $false }
            continue
        }
        if ($line -match '<#') {
            if ($line -notmatch '<#.*#>') { $inBlockComment = $true }
            continue
        }

        $hasIgnore = $line -match '(^|\s)#[^\n]*pinning-ignore'
        $exempt = $hasIgnore -or $prevWasIgnoreComment
        $prevWasIgnoreComment = $hasIgnore -and $line.TrimStart().StartsWith('#')

        if ($line -notmatch '\bInstall-Module\b') { continue }
        if ($line.TrimStart().StartsWith('#')) { continue }
        if ($exempt) { continue }

        # Split into statement segments so a string mention or a `Mock Install-Module` (which
        # does not start a segment with Install-Module) is not treated as a real invocation.
        $segments = $line -split '\s*(?:;|\|\||\||&&)\s*'
        foreach ($segment in $segments) {
            # Strip a YAML list marker and/or `run:` step prefix so flow-style
            # `- run: Install-Module ...` and `run: Install-Module ...` are recognized.
            $seg = ($segment.TrimStart() -replace '^-\s+', '' -replace '^run:\s*', '')
            $commandText = Remove-ShellTrailingComment -Command $seg
            if ($commandText -notmatch '^Install-Module\b') { continue }
            if ($commandText -match '(?:^|\s)-RequiredVersion(?::|\s+)\S+') { continue }

            if ($commandText -match '-Name\s+[''"]?([A-Za-z0-9_][A-Za-z0-9_.-]*)') {
                $name = $Matches[1]
            }
            elseif ($commandText -match '^Install-Module\s+[''"]?([A-Za-z0-9_][A-Za-z0-9_.-]*)') {
                $name = $Matches[1]
            }
            else {
                $name = '(module)'
            }

            $v = [DependencyViolation]::new()
            $v.File = $relativePath
            $v.Line = $logical.Line
            $v.Type = $type
            $v.Name = $name
            $v.Version = '(unpinned)'
            $v.Severity = 'warning'
            $v.Description = 'Install-Module without -RequiredVersion (resolves latest at run time)'
            $v.Metadata = @{ Format = (Split-Path $filePath -Leaf); LineContent = $commandText }
            $violations += $v
        }
    }

    return $violations
}

function Get-DockerImageViolations {
    <#
    .SYNOPSIS
        Detects unpinned OCI container image references in workflow YAML, Kubernetes
        manifests, and Helm values.
    .DESCRIPTION
        OSMO and AzureML workflow YAML, Kubernetes manifests, and Helm values reference
        runtime container images via 'image:' fields; Helm values additionally carry OCI
        references under 'init:'/'client:' keys (the OSMO backend_images block). Each concrete
        OCI reference must be pinned by an immutable '@sha256:<digest>' so pulls are
        reproducible and tamper-evident; the human-readable tag may be kept alongside the
        digest. A value under 'init:'/'client:' is treated as an image only when it carries a
        registry/namespace path, so plain configuration scalars are left untouched.
        Submission-time templated ('{{ ... }}') and shell-variable ('$VAR' / '${VAR}')
        references are injected at submit time and skipped, as are AzureML asset references
        ('azureml:<name>:<version>'), which are versioned assets rather than OCI images.
        Dockerfile 'FROM' pinning is out of scope (covered by OpenSSF Scorecard). An
        intentional non-pin opts out with a '# pinning-ignore' comment on the image line or a
        dedicated comment line directly above it.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    $lines = @(Get-Content -Path $filePath)
    $digestPattern = '@sha256:[a-fA-F0-9]{64}'

    $prevWasIgnoreComment = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        $hasIgnore = $line -match '(^|\s)#[^\n]*pinning-ignore'
        $exempt = $hasIgnore -or $prevWasIgnoreComment
        $prevWasIgnoreComment = $hasIgnore -and $line.TrimStart().StartsWith('#')

        # Match an image-bearing field (optionally a YAML list item), capturing key and value.
        # 'image:' is the canonical field; Helm values also express OCI references under
        # 'init:'/'client:' (the OSMO backend_images block), so those keys are inspected too.
        if ($line -notmatch '^\s*(?:-\s*)?(image|init|client):\s*(.+?)\s*$') { continue }
        if ($exempt) { continue }

        $key = $Matches[1]
        # Drop any trailing YAML comment, then surrounding quotes.
        $ref = ($Matches[2] -replace '\s+#.*$', '').Trim().Trim('"', "'").Trim()

        # Skip empties, submission-time templates/variables, AzureML assets, and prose.
        if ([string]::IsNullOrWhiteSpace($ref)) { continue }
        if ($ref -match '\{\{' -or $ref -match '^\$' -or $ref -match '\$\{') { continue }
        if ($ref -match '^azureml:') { continue }
        if ($ref -match '\s') { continue }

        # 'init:'/'client:' are generic keys that only sometimes carry an OCI reference;
        # require a registry/namespace path ('/') before treating the value as an image, so
        # plain scalars (e.g. 'init: true', 'client: guest') are not misread as unpinned.
        if ($key -ne 'image' -and $ref -notmatch '/') { continue }

        # Already digest-pinned -> compliant.
        if ($ref -match $digestPattern) { continue }

        # Split the digest-free reference into repository and tag for reporting.
        $name = $ref
        $version = '(none)'
        if ($ref -match '^(.*):([^:/]+)$') {
            $name = $Matches[1]
            $version = $Matches[2]
        }

        $v = [DependencyViolation]::new()
        $v.File = $relativePath
        $v.Line = $i + 1
        $v.Type = $type
        $v.Name = $name
        $v.Version = $version
        $v.Severity = 'warning'
        $v.Description = 'Unpinned container image (missing @sha256 digest)'
        $v.Metadata = @{ Format = (Split-Path $filePath -Leaf); LineContent = $line.Trim() }
        $violations += $v
    }

    return $violations
}

function Get-NpmOverrideViolation {
    <#
    .SYNOPSIS
        Recursively scans an npm overrides / resolutions object for unpinned specifiers.
    .DESCRIPTION
        overrides entries may nest: a package key can map to a child object that overrides
        that package's own transitive dependencies, with an optional "." key pinning the
        package itself. resolutions (Yarn) entries are flat. Every string leaf is a version
        specifier checked for exact pinning; nested objects are walked, attributing a "."
        entry to the enclosing package name.
    .PARAMETER Node
        The overrides / resolutions object (or a nested override object) to scan.
    .PARAMETER ParentName
        Enclosing package name, used to attribute a "." self-pin during recursion.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [AllowNull()]
        $Node,
        [Parameter(Mandatory)]
        [string]$Section,
        [Parameter(Mandatory)]
        [string]$Type,
        [Parameter(Mandatory)]
        [string]$RelativePath,
        [string]$ParentName = ''
    )

    $violations = @()

    if ($null -eq $Node -or $null -eq $Node.PSObject) {
        return $violations
    }

    foreach ($prop in $Node.PSObject.Properties) {
        # A "." key pins the enclosing package itself; attribute it to the parent
        $packageName = if ($prop.Name -eq '.') { $ParentName } else { $prop.Name }
        $value = $prop.Value

        if ($value -is [string]) {
            if ([string]::IsNullOrWhiteSpace($value)) {
                continue
            }

            if (-not (Test-SHAPinning -Version $value -Type $Type)) {
                $violation = [DependencyViolation]::new()
                $violation.File = $RelativePath
                $violation.Line = 0
                $violation.Type = $Type
                $violation.Name = $packageName
                $violation.Version = $value
                $violation.Severity = 'warning'
                $violation.Description = "Unpinned npm dependency in $Section"
                $violation.Metadata = @{ Section = $Section }
                $violations += $violation
            }
        }
        elseif ($null -ne $value -and $null -ne $value.PSObject) {
            # Nested override object — recurse, tracking the enclosing package name
            $violations += Get-NpmOverrideViolation -Node $value -Section $Section -Type $Type `
                -RelativePath $RelativePath -ParentName $packageName
        }
    }

    return $violations
}

function Get-NpmDependencyViolations {
    <#
    .SYNOPSIS
        Analyzes package.json files for unpinned npm dependencies.
    .DESCRIPTION
        Parses package.json as JSON and checks the dependency sections
        (dependencies, devDependencies, peerDependencies, optionalDependencies) and
        the override sections (overrides, resolutions) for exact version pinning.
        Rejects range operators (^, ~, >=, *, etc.). Ignores metadata fields like
        name, version, description, scripts, etc.
    .PARAMETER FileInfo
        Hashtable with Path, Type, and RelativePath keys from Get-FilesToScan.
    .OUTPUTS
        Array of PSCustomObjects representing dependency violations.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [hashtable]$FileInfo
    )

    $filePath = $FileInfo.Path
    $relativePath = $FileInfo.RelativePath
    $type = $FileInfo.Type
    $violations = @()

    if (-not (Test-Path -Path $filePath -PathType Leaf)) {
        return $violations
    }

    try {
        $content = Get-Content -Path $filePath -Raw -ErrorAction Stop
        $packageJson = $content | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        Write-Warning "Failed to parse $relativePath as JSON: $_"
        return $violations
    }

    $dependencySections = @('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies')

    foreach ($section in $dependencySections) {
        $sectionProperty = $packageJson.PSObject.Properties[$section]
        if ($null -eq $sectionProperty) {
            continue
        }
        $deps = $sectionProperty.Value

        foreach ($prop in $deps.PSObject.Properties) {
            $packageName = $prop.Name
            $version = $prop.Value

            if ([string]::IsNullOrWhiteSpace($version)) {
                continue
            }

            $isPinned = Test-SHAPinning -Version $version -Type $type

            if (-not $isPinned) {
                $violation = [DependencyViolation]::new()
                $violation.File = $relativePath
                $violation.Line = 0
                $violation.Type = $type
                $violation.Name = $packageName
                $violation.Version = $version
                $violation.Severity = 'warning'
                $violation.Description = "Unpinned npm dependency in $section"
                $violation.Metadata = @{ Section = $section }
                $violations += $violation
            }
        }
    }

    # overrides / resolutions may nest arbitrarily; walk them recursively
    foreach ($section in @('overrides', 'resolutions')) {
        $node = $packageJson.$section
        if ($null -eq $node) {
            continue
        }

        $violations += Get-NpmOverrideViolation -Node $node -Section $section -Type $type -RelativePath $relativePath
    }

    return $violations
}

function Write-PinningLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter(Mandatory = $false)]
        [ValidateSet('Info', 'Warning', 'Error', 'Success')]
        [string]$Level = 'Info'
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Output "[$timestamp] [$Level] $Message"
}

function Get-FilesToScan {
    <#
    .SYNOPSIS
    Discovers files to scan based on dependency type patterns.
    .DESCRIPTION
    Converts glob patterns (e.g., **/.github/workflows/*.yml) into
    PowerShell Get-ChildItem calls. The leading **/ prefix is stripped
    because Get-ChildItem does not support ** as a path segment; recursion
    is handled via the -Recurse parameter instead.
    #>
    param(
        [string]$ScanPath,
        [string[]]$Types,
        [string[]]$ExcludePatterns,
        [switch]$Recursive
    )

    $allFiles = @()

    foreach ($type in $Types) {
        if ($DependencyPatterns.ContainsKey($type)) {
            $patterns = $DependencyPatterns[$type].FilePatterns

            foreach ($pattern in $patterns) {
                # Strip leading **/ — Get-ChildItem cannot resolve ** as a directory segment
                $hasRecursiveGlob = $pattern.StartsWith('**/')
                $effectivePattern = if ($hasRecursiveGlob) { $pattern.Substring(3) } else { $pattern }

                # Handle an interior ** (e.g. workflows/**/*.yaml): recurse for the leaf
                # filter, then keep files whose relative path contains the required segment.
                # Get-ChildItem cannot resolve ** as a mid-path directory segment.
                # -Force is required so recursion descends into the dot-prefixed .github
                # directory, where GitHub Actions workflows live; without it -Recurse skips
                # hidden trees and .github/workflows/* is never scanned. Vendored hidden
                # trees (.git, .venv, …) are pruned by the relative-path filter below.
                if ($effectivePattern -match '/\*\*/') {
                    $segments = $effectivePattern -split '/\*\*/'
                    $requiredSegment = $segments[0]
                    $leafFilter = Split-Path $segments[-1] -Leaf
                    try {
                        $interiorFiles = Get-ChildItem -Path $ScanPath -Filter $leafFilter -Recurse -File -Force -ErrorAction SilentlyContinue |
                            Where-Object {
                                $rel = ([System.IO.Path]::GetRelativePath($ScanPath, $_.FullName)) -replace '\\', '/'
                                ($rel -match "(^|/)$([regex]::Escape($requiredSegment))/") -and
                                ($_.FullName -notmatch '[/\\](\.git|\.venv|node_modules|external|tests[/\\]Fixtures)[/\\]')
                            }

                        if ($ExcludePatterns) {
                            foreach ($exclude in $ExcludePatterns) {
                                $interiorFiles = $interiorFiles | Where-Object { (($_.FullName -replace '\\', '/') -notlike "*$exclude*") }
                            }
                        }

                        $allFiles += $interiorFiles | ForEach-Object {
                            @{
                                Path         = $_.FullName
                                Type         = $type
                                RelativePath = [System.IO.Path]::GetRelativePath($ScanPath, $_.FullName)
                            }
                        }
                    }
                    catch {
                        Write-PinningLog "Error scanning for $type files with pattern $pattern`: $($_.Exception.Message)" -Level Warning
                    }
                    continue
                }

                $dirPart = Split-Path $effectivePattern -Parent
                $filePart = Split-Path $effectivePattern -Leaf

                try {
                    $gciParams = @{ File = $true; ErrorAction = 'SilentlyContinue' }

                    if ($dirPart) {
                        $gciParams['Path'] = Join-Path $ScanPath $dirPart
                        $gciParams['Filter'] = $filePart
                    }
                    else {
                        $gciParams['Path'] = $ScanPath
                        $gciParams['Filter'] = $filePart
                    }

                    if ($hasRecursiveGlob -or $Recursive) {
                        $gciParams['Recurse'] = $true
                    }

                    $files = Get-ChildItem @gciParams

                    # Always skip vendored / VCS / virtualenv trees and test-fixture data
                    # (never lint dependencies' own files or intentionally-unpinned fixtures)
                    $files = $files | Where-Object {
                        $_.FullName -notmatch '[/\\](\.git|\.venv|node_modules|external|tests[/\\]Fixtures)[/\\]'
                    }

                    # Apply exclusion filters
                    if ($ExcludePatterns) {
                        foreach ($exclude in $ExcludePatterns) {
                            $files = $files | Where-Object { (($_.FullName -replace '\\', '/') -notlike "*$exclude*") }
                        }
                    }

                    $allFiles += $files | ForEach-Object {
                        @{
                            Path         = $_.FullName
                            Type         = $type
                            RelativePath = [System.IO.Path]::GetRelativePath($ScanPath, $_.FullName)
                        }
                    }
                }
                catch {
                    Write-PinningLog "Error scanning for $type files with pattern $pattern`: $($_.Exception.Message)" -Level Warning
                }
            }
        }
    }

    # Dedup per (Path, Type): a file may legitimately be scanned by several validators
    # (e.g. a workflow YAML checked for github-actions, gh-extension and powershell-modules).
    # Deduping by Path alone would collapse those to one entry and run only a single validator.
    return $allFiles | Sort-Object -Property Path, Type -Unique
}

function Test-SHAPinning {
    <#
    .SYNOPSIS
    Tests if a version reference is properly pinned for its ecosystem.
    .DESCRIPTION
    For github-actions: checks for 40-character hex SHA.
    For npm: checks for exact semver (no range operators like ^, ~, >=, *, x).
    For pip: checks for == equality pin operator.
    Falls back to PinPattern from DependencyPatterns when defined.
    #>
    param(
        [string]$Version,
        [string]$Type
    )

    if (-not $DependencyPatterns.ContainsKey($Type)) {
        return $false
    }

    $config = $DependencyPatterns[$Type]

    # Use SHAPattern for ecosystems that require commit SHA pinning (github-actions)
    if ($config.ContainsKey('SHAPattern') -and $config.SHAPattern) {
        return $Version -match $config.SHAPattern
    }

    # Use PinPattern for ecosystems with version-based pinning (npm, pip)
    if ($config.ContainsKey('PinPattern') -and $config.PinPattern) {
        return $Version -match $config.PinPattern
    }

    return $false
}

function Get-DependencyViolation {
    <#
    .SYNOPSIS
    Scans a file for dependency pinning violations.
    #>
    param(
        [hashtable]$FileInfo
    )

    $violations = @()
    $filePath = $FileInfo.Path
    $fileType = $FileInfo.Type

    if (!(Test-Path $filePath)) {
        return $violations
    }

    $config = $DependencyPatterns[$fileType]

    # Check if this type uses a validation function instead of regex patterns
    if ($config.ContainsKey('ValidationFunc') -and $null -ne $config.ValidationFunc) {
        $funcName = $config.ValidationFunc
        $rawViolations = & $funcName -FileInfo $FileInfo

        if ($null -eq $rawViolations) {
            return @()
        }

        foreach ($v in $rawViolations) {
            if ($null -eq $v) {
                continue
            }

            if (-not ($v -is [DependencyViolation])) {
                $actualType = $v.GetType().FullName
                throw "Validation function '$funcName' must return [DependencyViolation] objects, got '$actualType'."
            }

            if (-not $v.File) {
                $v.File = $FileInfo.RelativePath
            }

            if ($v.Line -lt 1) {
                $v.Line = 0
            }

            if (-not $v.Type) {
                $v.Type = $fileType
            }
        }

        return $rawViolations
    }

    try {
        $content = Get-Content -Path $filePath -Raw
        $lines = @(Get-Content -Path $filePath)

        $patterns = $config.VersionPatterns

        foreach ($patternInfo in $patterns) {
            $pattern = $patternInfo.Pattern
            $description = $patternInfo.Description

            $regexMatches = [regex]::Matches($content, $pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)

            foreach ($match in $regexMatches) {
                # Find line number
                $lineNumber = 1
                $position = $match.Index
                for ($i = 0; $i -lt $position; $i++) {
                    if ($content[$i] -eq "`n") {
                        $lineNumber++
                    }
                }

                # Extract dependency information
                $dependencyName = $match.Groups[1].Value
                $version = $match.Groups[2].Value

                # Check if properly pinned
                if (!(Test-SHAPinning -Version $version -Type $fileType)) {
                    $violation = [DependencyViolation]::new()
                    $violation.File = $FileInfo.RelativePath
                    $violation.Line = $lineNumber
                    $violation.Type = $fileType
                    $violation.Name = $dependencyName
                    $violation.Version = $version
                    $violation.CurrentRef = $match.Value
                    $violation.Description = "Unpinned dependency: $description"
                    $violation.Severity = if ($fileType -eq 'github-actions') { 'High' } else { 'Medium' }
                    $violation.Metadata['PatternDescription'] = $description
                    $violation.Metadata['LineContent'] = $lines[$lineNumber - 1]

                    $violations += $violation
                }
            }
        }
    }
    catch {
        Write-PinningLog "Error scanning file $filePath`: $($_.Exception.Message)" -Level Warning
    }

    return $violations
}

function Get-RemediationSuggestion {
    <#
    .SYNOPSIS
    Generates remediation suggestions for unpinned dependencies.
    #>
    param(
        [DependencyViolation]$Violation,

        [switch]$Remediate
    )

    $type = $Violation.Type
    $name = $Violation.Name
    $version = $Violation.Version

    if (!$Remediate) {
        return "Enable -Remediate flag for specific SHA suggestions"
    }

    try {
        switch ($type) {
            'github-actions' {
                # For GitHub Actions, resolve tag to commit SHA
                $apiUrl = "https://api.github.com/repos/$name/commits/$version"
                $headers = @{}

                if ($env:GITHUB_TOKEN) {
                    $headers['Authorization'] = "Bearer $env:GITHUB_TOKEN"
                }

                $response = Invoke-RestMethod -Uri $apiUrl -Headers $headers -TimeoutSec 30
                $sha = $response.sha

                if ($sha) {
                    return "Pin to SHA: uses: $name@$sha # $version"
                }
            }

            default {
                return "Research and pin to specific commit SHA or content hash for $type dependencies"
            }
        }
    }
    catch {
        Write-PinningLog "Could not generate automatic remediation for $($Violation.Name): $($_.Exception.Message)" -Level Warning
    }

    return "Manually research and pin to immutable reference"
}

function Get-ComplianceReportData {
    <#
    .SYNOPSIS
    Generates a comprehensive compliance report.
    #>
    param(
        [DependencyViolation[]]$Violations,
        [hashtable[]]$ScannedFiles,
        [string]$ScanPath,
        [switch]$Remediate
    )

    $report = [ComplianceReport]::new()
    $report.ScanPath = $ScanPath
    $report.ScannedFiles = $ScannedFiles.Count
    $report.Violations = $Violations

    # Calculate metrics
    $totalDeps = @($Violations).Count
    $unpinnedDeps = @($Violations | Where-Object { $_.Severity -ne 'Info' }).Count
    $pinnedDeps = $totalDeps - $unpinnedDeps

    $report.TotalDependencies = $totalDeps
    $report.PinnedDependencies = $pinnedDeps
    $report.UnpinnedDependencies = $unpinnedDeps

    if ($totalDeps -gt 0) {
        $report.ComplianceScore = [math]::Round(($pinnedDeps / $totalDeps) * 100, 2)
    }
    else {
        $report.ComplianceScore = 100.0
    }

    # Generate summary by type
    $report.Summary = @{}
    foreach ($type in @($Violations | Group-Object Type)) {
        $report.Summary[$type.Name] = @{
            Total  = $type.Count
            High   = @($type.Group | Where-Object { $_.Severity -eq 'High' }).Count
            Medium = @($type.Group | Where-Object { $_.Severity -eq 'Medium' }).Count
            Low    = @($type.Group | Where-Object { $_.Severity -eq 'Low' }).Count
        }
    }

    # Add metadata
    $report.Metadata = @{
        PowerShellVersion  = $PSVersionTable.PSVersion.ToString()
        Platform           = $PSVersionTable.Platform
        ScanTimestamp      = $report.Timestamp.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        IncludedTypes      = $IncludeTypes
        ExcludedPaths      = $ExcludePaths
        RemediationEnabled = $Remediate.IsPresent
        ComplianceThreshold = $Threshold
    }

    return $report
}

function Export-ComplianceReport {
    <#
    .SYNOPSIS
    Exports compliance report in specified format.
    #>
    param(
        # Use duck typing to avoid class type collision during code coverage instrumentation
        $Report,
        [string]$Format,
        [string]$OutputPath
    )

    # Validate required properties on duck-typed $Report parameter (ComplianceReport schema)
    $requiredProperties = @('ComplianceScore', 'Violations', 'TotalDependencies', 'UnpinnedDependencies', 'Metadata')
    foreach ($prop in $requiredProperties) {
        if ($null -eq $Report.PSObject.Properties[$prop]) {
            throw "Report object missing required property: $prop"
        }
    }

    # Ensure parent directory exists
    $parentDir = Split-Path -Path $OutputPath -Parent
    if ($parentDir -and -not (Test-Path $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
    }

    switch ($Format.ToLower()) {
        'json' {
            $Report | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding UTF8
        }

        'sarif' {
            $sarif = @{
                version    = "2.1.0"
                "`$schema" = "https://json.schemastore.org/sarif-2.1.0.json"
                runs       = @(@{
                        tool    = @{
                            driver = @{
                                name           = "dependency-pinning-analyzer"
                                version        = "1.0.0"
                                informationUri = "https://github.com/microsoft/hve-core"
                            }
                        }
                        results = @($Report.Violations | ForEach-Object {
                                @{
                                    ruleId     = "dependency-not-pinned"
                                    level      = switch ($_.Severity) { 'High' { 'error' } 'Medium' { 'warning' } default { 'note' } }
                                    message    = @{ text = $_.Description }
                                    locations  = @(@{
                                            physicalLocation = @{
                                                artifactLocation = @{ uri = $_.File }
                                                region           = @{ startLine = [Math]::Max(1, [int]$_.Line) }
                                            }
                                        })
                                    properties = @{
                                        dependencyName = $_.Name
                                        currentVersion = $_.Version
                                        remediation    = $_.Remediation
                                    }
                                }
                            })
                    })
            }
            $sarif | ConvertTo-Json -Depth 10 | Out-File -FilePath $OutputPath -Encoding UTF8
        }

        'csv' {
            $Report.Violations | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8
        }

        'markdown' {
            $markdown = @"
# Dependency Pinning Compliance Report

**Scan Date:** $($Report.Timestamp.ToString('yyyy-MM-dd HH:mm:ss'))
**Scan Path:** $($Report.ScanPath)
**Compliance Score:** $($Report.ComplianceScore)%

## Summary

| Metric | Count |
|--------|--------|
| Total Files Scanned | $($Report.ScannedFiles) |
| Total Dependencies | $($Report.TotalDependencies) |
| Pinned Dependencies | $($Report.PinnedDependencies) |
| Unpinned Dependencies | $($Report.UnpinnedDependencies) |

## Violations by Type

"@
            foreach ($type in $Report.Summary.Keys) {
                $summary = $Report.Summary[$type]
                $markdown += @"

### $type
- **Total:** $($summary.Total)
- **High Severity:** $($summary.High)
- **Medium Severity:** $($summary.Medium)
- **Low Severity:** $($summary.Low)

"@
            }

            if ($Report.Violations.Count -gt 0) {
                $markdown += @"

## Detailed Violations

| File | Line | Type | Dependency | Current Version | Severity | Remediation |
|------|------|------|------------|----------------|----------|-------------|
"@
                foreach ($violation in $Report.Violations) {
                    $markdown += "|$($violation.File)|$($violation.Line)|$($violation.Type)|$($violation.Name)|$($violation.Version)|$($violation.Severity)|$($violation.Remediation)|`n"
                }
            }

            $markdown | Out-File -FilePath $OutputPath -Encoding UTF8
        }

        'table' {
            # Display formatted table to console and save simple text format
            if ($Report.Violations.Count -gt 0) {
                $Report.Violations | Format-Table -Property File, Line, Type, Name, Version, Severity -AutoSize | Out-File -FilePath $OutputPath -Encoding UTF8 -Width 200
            }
            else {
                "No dependency pinning violations found." | Out-File -FilePath $OutputPath -Encoding UTF8
            }
        }
    }

    Write-PinningLog "Compliance report exported to: $OutputPath" -Level Success
}

function Export-CICDArtifact {
    <#
    .SYNOPSIS
    Exports compliance report as CI/CD artifacts for both GitHub Actions and Azure DevOps.
    #>
    param(
        [ComplianceReport]$Report,
        [string]$ReportPath
    )

    Write-PinningLog "Preparing compliance artifacts for CI/CD systems..." -Level Info

    $platform = Get-CIPlatform
    Write-PinningLog "Detected $platform environment - setting up artifacts" -Level Info

    # Set CI outputs (works for both GitHub Actions and Azure DevOps)
    Set-CIOutput -Name 'dependency-report' -Value $ReportPath -IsOutput
    Set-CIOutput -Name 'compliance-score' -Value $Report.ComplianceScore -IsOutput
    Set-CIOutput -Name 'unpinned-count' -Value $Report.UnpinnedDependencies -IsOutput

    # Create summary content
    $summaryContent = @"
# 📌 Dependency Pinning Analysis

**Compliance Score:** $($Report.ComplianceScore)%
**Unpinned Dependencies:** $($Report.UnpinnedDependencies)
**Total Dependencies Scanned:** $($Report.TotalDependencies)

$(if ($Report.UnpinnedDependencies -gt 0) { "⚠️ **Action Required:** $($Report.UnpinnedDependencies) dependencies are not properly pinned to immutable references." } else { "✅ **All Clear:** All dependencies are properly pinned!" })
"@

    # Write step summary
    Write-CIStepSummary -Content $summaryContent

    # Publish artifact
    Publish-CIArtifact -Path $ReportPath -Name 'dependency-pinning-report' -ContainerFolder 'dependency-pinning'

    # Set up local artifact directory for GitHub Actions upload-artifact action
    if ($platform -eq 'github') {
        $artifactDir = Join-Path $PWD "dependency-pinning-artifacts"
        New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
        Copy-Item -Path $ReportPath -Destination $artifactDir -Force
    }

    Write-PinningLog "Compliance artifacts prepared for CI/CD consumption" -Level Success
}

#region Main Execution

# Only execute when invoked directly (not dot-sourced)
try {
    if ($MyInvocation.InvocationName -ne '.') {
        Write-PinningLog "Starting dependency pinning compliance analysis..." -Level Info
        Write-PinningLog "PowerShell Version: $($PSVersionTable.PSVersion)" -Level Info
        Write-PinningLog "Platform: $($PSVersionTable.Platform)" -Level Info

        # Parse include types and exclude paths
        $typesToCheck = $IncludeTypes.Split(',') | ForEach-Object { $_.Trim() }
        $defaultExcludePatterns = @('scripts/tests/Fixtures', 'shared/ci/tests/Fixtures')
        $userExcludePatterns = if ($ExcludePaths) { $ExcludePaths.Split(',') | ForEach-Object { $_.Trim() } } else { @() }
        $excludePatterns = @($defaultExcludePatterns + $userExcludePatterns) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            ForEach-Object { $_ -replace '\\', '/' } |
            Select-Object -Unique

        Write-PinningLog "Scanning path: $Path" -Level Info
        Write-PinningLog "Include types: $($typesToCheck -join ', ')" -Level Info
        if ($excludePatterns) { Write-PinningLog "Exclude patterns: $($excludePatterns -join ', ')" -Level Info }

        # Discover files to scan
        $filesToScan = @(Get-FilesToScan -ScanPath $Path -Types $typesToCheck -ExcludePatterns $excludePatterns -Recursive:$Recursive)
        Write-PinningLog "Found $(@($filesToScan).Count) files to scan" -Level Info

        # Scan for violations
        $allViolations = @()
        foreach ($fileInfo in $filesToScan) {
            Write-PinningLog "Scanning: $($fileInfo.RelativePath)" -Level Info
            $violations = @(Get-DependencyViolation -FileInfo $fileInfo)

            # Add remediation suggestions
            foreach ($violation in $violations) {
                $violation.Remediation = Get-RemediationSuggestion -Violation $violation -Remediate:$Remediate
            }

            $allViolations += $violations
        }

        Write-PinningLog "Found $(@($allViolations).Count) dependency pinning violations" -Level Info

        # Generate compliance report
        $report = Get-ComplianceReportData -Violations $allViolations -ScannedFiles $filesToScan -ScanPath $Path -Remediate:$Remediate

        # Export report
        Export-ComplianceReport -Report $report -Format $Format -OutputPath $OutputPath

        # Export CI/CD artifacts
        Export-CICDArtifact -Report $report -ReportPath $OutputPath

        # Display summary
        Write-PinningLog "Compliance Analysis Complete!" -Level Success
        Write-PinningLog "Compliance Score: $($report.ComplianceScore)%" -Level Info
        Write-PinningLog "Total Dependencies: $($report.TotalDependencies)" -Level Info
        Write-PinningLog "Unpinned Dependencies: $($report.UnpinnedDependencies)" -Level Info

        if ($report.UnpinnedDependencies -gt 0) {
            Write-PinningLog "$($report.UnpinnedDependencies) dependencies require SHA pinning for security compliance" -Level Warning

            # Check threshold compliance
            if ($report.ComplianceScore -lt $Threshold) {
                Write-PinningLog "Compliance score $($report.ComplianceScore)% is below threshold $Threshold%" -Level Error

                if ($FailOnUnpinned) {
                    Write-PinningLog "Failing build due to compliance threshold violation (-FailOnUnpinned enabled)" -Level Error
                    exit 1
                }
                else {
                    Write-PinningLog "Threshold violation detected but continuing (soft-fail mode)" -Level Warning
                }
            }
            else {
                Write-PinningLog "Compliance score $($report.ComplianceScore)% meets threshold $Threshold%" -Level Info
            }
        }
        else {
            Write-PinningLog "All dependencies are properly pinned! ✅ (100% compliance, exceeds $Threshold% threshold)" -Level Success
            exit 0
        }
    }
    else {
        # Dot-sourced: functions are available in caller scope, skip main execution
        return
    }
}
catch {
    Write-PinningLog "Dependency pinning analysis failed: $($_.Exception.Message)" -Level Error
    Write-CIAnnotation -Message $_.Exception.Message -Level Error
    exit 1
}

#endregion
