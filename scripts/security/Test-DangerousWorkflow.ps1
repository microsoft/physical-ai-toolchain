#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#Requires -Version 7.0
# Requirement lowered from upstream 7.4 to the repo standard 7.0 (cf. Test-BinaryFreshness.ps1);
# no 7.4-only syntax is used.

<#
.SYNOPSIS
    Detects dangerous patterns in GitHub Actions workflows.

.DESCRIPTION
    Scans GitHub Actions workflow YAML files for two classes of dangerous pattern:

    - Template injection: direct interpolation of attacker-controllable GitHub event
      values into run or github-script execution contexts.
    - Untrusted checkout: workflows triggered by 'pull_request_target' that check out
      the pull-request head ref, executing untrusted code in a privileged context.

    Adapted from microsoft/hve-core scripts/security/Test-DangerousWorkflow.ps1
    as of commit b70237d08d5caf6918b9de9952a243a8588b92dc (2026-07-02).

    Local divergences from upstream (each marked inline with a '# LOCAL' comment):

    - Untrusted-checkout rule. Upstream deliberately omits checkout detection,
      delegating broader dangerous-workflow coverage to the Poutine scanner in CI.
      This repository runs no Poutine scanner, so the rule is added here to close
      that gap rather than duplicate it.
    - A Get-NodeMember helper, used throughout Invoke-DangerousWorkflowCheck for
      hashtable/PSObject node access, and a ConvertTo-NormalizedExpression pass that
      rewrites bracket/index access to dot form before matching.

    These deltas restructure upstream function bodies rather than only appending to
    them, so a re-sync against a newer hve-core revision is a 3-way merge, not a
    mechanical diff.

.PARAMETER Path
    Directory containing workflow YAML files. Defaults to '.github/workflows'.

.PARAMETER Format
    Output format: 'console', 'json', or 'sarif'. Defaults to 'console'.

.PARAMETER OutputPath
    Path for result output file. Defaults to 'logs/dangerous-workflow-results.json'
    or 'logs/dangerous-workflow-results.sarif' for SARIF output.

.PARAMETER FailOnViolation
    When set, exits with non-zero code if any in-scope findings remain.

.EXAMPLE
    ./scripts/security/Test-DangerousWorkflow.ps1

.EXAMPLE
    ./scripts/security/Test-DangerousWorkflow.ps1 -FailOnViolation -Format sarif

.LINK
    https://github.com/microsoft/hve-core/blob/b70237d08d5caf6918b9de9952a243a8588b92dc/scripts/security/Test-DangerousWorkflow.ps1
#>

using module ./Modules/SecurityClasses.psm1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Path = '.github/workflows',

    [Parameter(Mandatory = $false)]
    [ValidateSet('json', 'sarif', 'console')]
    [string]$Format = 'console',

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = '',

    [Parameter(Mandatory = $false)]
    [switch]$FailOnViolation
)

$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot '../lib/Modules/CIHelpers.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'Modules/SecurityHelpers.psm1') -Force

#region Functions

# LOCAL: not in upstream - unifies hashtable/PSObject node access used throughout this file.
function Get-NodeMember {
    <#
    .SYNOPSIS
        Reads a key from a parsed-YAML node regardless of whether it is a hashtable or PSObject.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        $Node,

        [Parameter(Mandatory = $true)]
        $Key
    )

    if ($null -eq $Node) {
        return $null
    }
    if ($Node -is [System.Collections.IDictionary]) {
        if ($Node.Contains($Key)) {
            return $Node[$Key]
        }
        return $null
    }
    if ($Node.PSObject.Properties.Name -contains $Key) {
        return $Node.$Key
    }
    return $null
}

function Get-WorkflowFiles {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScanPath
    )

    $resolvedPath = Resolve-Path -Path $ScanPath -ErrorAction Stop
    return Get-ChildItem -Path $resolvedPath -File -Recurse | Where-Object { $_.Extension -in '.yml', '.yaml' } | Sort-Object -Property FullName
}

function Get-ExpressionMatches {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [string]$Text
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return @()
    }

    $expressionMatchList = [System.Text.RegularExpressions.Regex]::Matches($Text, '\$\{\{\s*(.*?)\s*\}\}')
    return @($expressionMatchList | ForEach-Object { $_.Groups[1].Value.Trim() })
}

# LOCAL: not in upstream - normalizes bracket/index expression access to dot form before matching.
function ConvertTo-NormalizedExpression {
    <#
    .SYNOPSIS
        Normalizes GitHub expression bracket-access to dot-access.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Expression
    )

    $normalized = $Expression
    $normalized = $normalized -replace "\[\s*'([^']*)'\s*\]", '.$1'
    $normalized = $normalized -replace '\[\s*"([^"]*)"\s*\]', '.$1'
    $normalized = $normalized -replace '\[\s*(\d+)\s*\]', '.$1'
    return $normalized
}

function Test-IsUntrustedInjectionExpression {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Expression
    )

    $expression = $Expression.Trim()
    $expression = ConvertTo-NormalizedExpression -Expression $expression # LOCAL: upstream matches the raw expression.
    if ([string]::IsNullOrWhiteSpace($expression)) {
        return $false
    }

    # Attacker-controllable free-text and ref contexts that a user without write
    # access can influence. Interpolating these directly into a run or script
    # body enables template injection. Indirect derivations through steps/needs/env
    # outputs are out of scope to keep detection focused on direct interpolation.
    $untrustedPatterns = @(
        '(^|\W)github\.head_ref(\W|$)'
        '(^|\W)github\.event\.pull_request\.(title|body)(\W|$)'
        '(^|\W)github\.event\.pull_request\.head\.(ref|label)(\W|$)'
        '(^|\W)github\.event\.issue\.(title|body)(\W|$)'
        '(^|\W)github\.event\.(comment|review|review_comment)\.body(\W|$)'
        '(^|\W)github\.event\.discussion\.(title|body)(\W|$)'
        '(^|\W)github\.event\.pages\.[^\s]*\.page_name(\W|$)'
        '(^|\W)github\.event\.head_commit\.(message|author\.(email|name))(\W|$)'
        '(^|\W)github\.event\.commits\.[^\s]*\.(message|author\.(email|name))(\W|$)'
        '(^|\W)github\.event\.workflow_run\.(head_branch|display_title)(\W|$)'
    )

    foreach ($pattern in $untrustedPatterns) {
        if ($expression -match $pattern) {
            return $true
        }
    }

    return $false
}

# LOCAL: not in upstream - supports the untrusted-checkout rule.
function Test-HasPullRequestTargetTrigger {
    <#
    .SYNOPSIS
        Returns true when the workflow's 'on' trigger includes pull_request_target.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        $Yaml
    )

    $onNode = Get-NodeMember -Node $Yaml -Key 'on'
    if ($null -eq $onNode) {
        # YAML 1.1 parsers may fold the bare 'on' key to boolean true.
        $onNode = Get-NodeMember -Node $Yaml -Key $true
    }
    if ($null -eq $onNode) {
        return $false
    }

    if ($onNode -is [string]) {
        return $onNode -eq 'pull_request_target'
    }
    if ($onNode -is [System.Collections.IDictionary]) {
        return @($onNode.Keys) -contains 'pull_request_target'
    }
    # List form, e.g. on: [push, pull_request_target]
    return @($onNode) -contains 'pull_request_target'
}

# LOCAL: not in upstream - supports the untrusted-checkout rule.
function Test-IsUntrustedCheckoutRef {
    <#
    .SYNOPSIS
        Returns true when a checkout ref resolves to untrusted pull-request head code.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [string]$Ref
    )

    if ([string]::IsNullOrWhiteSpace($Ref)) {
        return $false
    }

    # Explicit pull-request refs (refs/pull/<n>/merge or /head) fetch untrusted code.
    if ($Ref -match 'refs/pull/') {
        return $true
    }

    $untrustedRefPatterns = @(
        '(^|\W)github\.event\.pull_request\.head\.(sha|ref)(\W|$)'
        '(^|\W)github\.head_ref(\W|$)'
        '(^|\W)github\.event\.pull_request\.merge_commit_sha(\W|$)'
    )

    foreach ($rawExpr in Get-ExpressionMatches -Text $Ref) {
        $expression = ConvertTo-NormalizedExpression -Expression $rawExpr
        foreach ($pattern in $untrustedRefPatterns) {
            if ($expression -match $pattern) {
                return $true
            }
        }
    }

    return $false
}

function Find-NextMatchingLine {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string[]]$Lines,

        [Parameter(Mandatory = $true)]
        [string]$Pattern,

        [Parameter(Mandatory = $false)]
        [int]$StartIndex = 0
    )

    for ($i = $StartIndex; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match $Pattern) {
            return $i + 1
        }
    }

    return 0
}

function ConvertTo-DangerousWorkflowSarif {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [DependencyViolation[]]$Violations
    )

    $rules = @(
        @{
            id                   = 'dangerous-workflow/template-injection'
            name                 = 'DangerousWorkflowTemplateInjection'
            shortDescription     = @{ text = 'Untrusted expressions are interpolated into code execution contexts' }
            fullDescription      = @{ text = 'Untrusted GitHub event or workflow output expressions should not be interpolated directly into run or script blocks.' }
            defaultConfiguration = @{ level = 'error' }
        }
        @{
            id                   = 'dangerous-workflow/untrusted-checkout'
            name                 = 'DangerousWorkflowUntrustedCheckout'
            shortDescription     = @{ text = 'Pull-request head code is checked out in a pull_request_target context' }
            fullDescription      = @{ text = 'Workflows triggered by pull_request_target should not check out and execute the untrusted pull-request head ref in the privileged base context.' }
            defaultConfiguration = @{ level = 'error' }
        }
    )

    $results = @()
    foreach ($violation in $Violations) {
        $ruleId = $violation.Metadata.RuleId
        $ruleLevel = 'error'
        $results += @{
            ruleId    = $ruleId
            level     = $ruleLevel
            message   = @{ text = $violation.Description }
            locations = @(
                @{
                    physicalLocation = @{
                        artifactLocation = @{ uri = $violation.File }
                        region           = @{ startLine = [int]$violation.Line }
                    }
                }
            )
        }
    }

    return @{
        version   = '2.1.0'
        '$schema' = 'https://json.schemastore.org/sarif-2.1.0.json'
        runs      = @(
            @{
                tool    = @{
                    driver = @{
                        name           = 'Test-DangerousWorkflow'
                        version        = '1.0.0'
                        informationUri = 'https://github.com/microsoft/hve-core'
                        rules          = $rules
                    }
                }
                results = $results
            }
        )
    }
}

function New-DangerousWorkflowViolation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$File,

        [Parameter(Mandatory = $true)]
        [int]$Line,

        [Parameter(Mandatory = $true)]
        [string]$RuleId,

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [Parameter(Mandatory = $true)]
        [string]$Remediation,

        [Parameter(Mandatory = $false)]
        [string]$JobName = 'unknown',

        [Parameter(Mandatory = $false)]
        [string]$StepName = 'unknown'
    )

    $violation = [DependencyViolation]::new()
    $violation.File = $File
    $violation.Line = $Line
    $violation.Type = 'dangerous-workflow'
    $violation.Name = [System.IO.Path]::GetFileName($File)
    $violation.Severity = 'High'
    $violation.ViolationType = ''
    $violation.Description = $Description
    $violation.Remediation = $Remediation
    $violation.Metadata = @{
        RuleId = $RuleId
        Job    = $JobName
        Step   = $StepName
    }

    return $violation
}

function Invoke-DangerousWorkflowCheck {
    [OutputType([int])]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string]$Path = '.github/workflows',

        [Parameter(Mandatory = $false)]
        [ValidateSet('json', 'sarif', 'console')]
        [string]$Format = 'console',

        [Parameter(Mandatory = $false)]
        [string]$OutputPath = '',

        [Parameter(Mandatory = $false)]
        [switch]$FailOnViolation
    )

    Write-SecurityLog 'Starting dangerous workflow validation' -Level Info -CIAnnotation
    Write-SecurityLog "Scanning: $Path" -Level Info

    if (-not (Get-Command ConvertFrom-Yaml -ErrorAction SilentlyContinue)) {
        Write-SecurityLog 'PowerShell-Yaml module not found; cannot scan for dangerous workflow patterns. Install with: Install-Module powershell-yaml' -Level Error -CIAnnotation
        return 1
    }

    if ([string]::IsNullOrWhiteSpace($OutputPath)) {
        if ($Format -eq 'sarif') {
            $OutputPath = 'logs/dangerous-workflow-results.sarif'
        }
        else {
            $OutputPath = 'logs/dangerous-workflow-results.json'
        }
    }

    $resolvedPath = Resolve-Path -Path $Path -ErrorAction Stop
    Write-SecurityLog "Resolved path: $resolvedPath" -Level Info

    $workflowFiles = Get-WorkflowFiles -ScanPath $Path
    $totalFiles = @($workflowFiles).Count
    Write-SecurityLog "Found $totalFiles workflow file(s)" -Level Info

    $report = [ComplianceReport]::new($Path)
    $report.TotalFiles = $totalFiles
    $report.ScannedFiles = $totalFiles
    $report.TotalDependencies = $totalFiles
    $report.Metadata['ItemType'] = 'workflow'
    $report.Metadata['ItemLabel'] = 'workflows with dangerous patterns'

    $violations = @()
    foreach ($workflowFile in $workflowFiles) {
        $filePath = $workflowFile.FullName
        $relativePath = [System.IO.Path]::GetRelativePath((Get-Location).Path, $filePath)
        $workflowContent = ''
        try {
            $workflowContent = Get-Content -Path $filePath -Raw
        }
        catch {
            $workflowContent = ''
        }
        $rawLines = @($workflowContent -split "\r?\n")
        try {
            $yaml = $workflowContent | ConvertFrom-Yaml
        }
        catch {
            $parseErrorMessage = "Skipping workflow file '$relativePath' because YAML parsing failed: $($_.Exception.Message)"
            Write-SecurityLog $parseErrorMessage -Level Warning -CIAnnotation
            Write-CIAnnotation -Message $parseErrorMessage -Level 'Warning' -File $relativePath -Line 1
            continue
        }

        if ($null -eq $yaml) {
            continue
        }

        $jobsNode = Get-NodeMember -Node $yaml -Key 'jobs'
        if ($null -eq $jobsNode) {
            continue
        }

        $hasPullRequestTarget = Test-HasPullRequestTargetTrigger -Yaml $yaml

        $injectionSearchIndex = 0
        foreach ($jobEntry in $jobsNode.GetEnumerator()) {
            $jobName = [string]$jobEntry.Key
            $jobObject = $jobEntry.Value
            $steps = Get-NodeMember -Node $jobObject -Key 'steps'
            if ($null -eq $steps) {
                continue
            }

            $stepIndex = 0
            foreach ($step in @($steps)) {
                $stepId = Get-NodeMember -Node $step -Key 'id'
                $stepName = Get-NodeMember -Node $step -Key 'name'
                if ($null -eq $stepId -and $null -eq $stepName) {
                    $stepName = "step-$stepIndex"
                }
                elseif ($null -eq $stepId) {
                    $stepName = [string]$stepName
                }
                else {
                    $stepName = [string]$stepId
                }

                $runValue = Get-NodeMember -Node $step -Key 'run'
                $usesValue = Get-NodeMember -Node $step -Key 'uses'
                $withValue = Get-NodeMember -Node $step -Key 'with'

                # Only actions/github-script executes its with.script input as code.
                $scriptValue = $null
                if ($usesValue -and "$usesValue" -match '^actions/github-script(?:@|$)') {
                    $scriptValue = Get-NodeMember -Node $withValue -Key 'script'
                }

                # Each step carries exactly one code source (run or github-script). Track the
                # source kind so line resolution can anchor on the correct block.
                $codeCandidates = @()
                if ($null -ne $runValue) {
                    $codeCandidates += @{ Kind = 'run'; Text = [string]$runValue }
                }
                if ($null -ne $scriptValue) {
                    $codeCandidates += @{ Kind = 'script'; Text = [string]$scriptValue }
                }

                foreach ($candidate in $codeCandidates) {
                    foreach ($expression in Get-ExpressionMatches -Text $candidate.Text) {
                        if (Test-IsUntrustedInjectionExpression -Expression $expression) {
                            # Anchor on the actual interpolation so the reported line is the exact
                            # line containing the untrusted expression, independent of job/step
                            # iteration order (the parser returns an unordered hashtable).
                            $exprPattern = '\$\{\{\s*' + [regex]::Escape($expression) + '\s*\}\}'
                            $lineNumber = Find-NextMatchingLine -Lines $rawLines -Pattern $exprPattern -StartIndex $injectionSearchIndex
                            if ($lineNumber -eq 0) {
                                $lineNumber = Find-NextMatchingLine -Lines $rawLines -Pattern $exprPattern -StartIndex 0
                            }
                            if ($lineNumber -eq 0) {
                                $headerPattern = if ($candidate.Kind -eq 'script') { '^\s*script:\s*' } else { '^\s*run:\s*' }
                                $lineNumber = Find-NextMatchingLine -Lines $rawLines -Pattern $headerPattern -StartIndex 0
                            }
                            if ($lineNumber -eq 0) {
                                $lineNumber = 1
                            }
                            else {
                                $injectionSearchIndex = $lineNumber
                            }

                            $violation = New-DangerousWorkflowViolation -File $relativePath -Line $lineNumber -RuleId 'dangerous-workflow/template-injection' -Description "Untrusted expression '$expression' is interpolated into a code execution context in job '$jobName' step '$stepName'." -Remediation 'Avoid directly interpolating untrusted GitHub event or workflow-output values into shell or script blocks.' -JobName $jobName -StepName $stepName
                            $violations += $violation
                            break
                        }
                    }
                }

                # LOCAL (begin): untrusted pull_request_target checkout rule (not in upstream).
                # Untrusted checkout: a pull_request_target workflow that checks out the PR head ref
                # runs attacker-controlled code with the privileged base-repository token.
                if ($hasPullRequestTarget -and $usesValue -and "$usesValue" -match '^actions/checkout(?:@|/|$)') {
                    $refValue = Get-NodeMember -Node $withValue -Key 'ref'
                    if ($refValue -and (Test-IsUntrustedCheckoutRef -Ref "$refValue")) {
                        $refPattern = [regex]::Escape("$refValue")
                        $lineNumber = Find-NextMatchingLine -Lines $rawLines -Pattern $refPattern -StartIndex 0
                        if ($lineNumber -eq 0) {
                            $lineNumber = Find-NextMatchingLine -Lines $rawLines -Pattern '^\s*ref:\s*' -StartIndex 0
                        }
                        if ($lineNumber -eq 0) {
                            $lineNumber = 1
                        }

                        $violation = New-DangerousWorkflowViolation -File $relativePath -Line $lineNumber -RuleId 'dangerous-workflow/untrusted-checkout' -Description "Workflow triggered by 'pull_request_target' checks out untrusted pull-request code (ref '$refValue') in job '$jobName' step '$stepName'." -Remediation 'Do not check out the pull-request head ref in a pull_request_target workflow; use the pull_request trigger, or gate untrusted-code execution behind a separate unprivileged job.' -JobName $jobName -StepName $stepName
                        $violations += $violation
                    }
                }
                # LOCAL (end): untrusted pull_request_target checkout rule.

                $stepIndex++
            }
        }

    }

    $report.Violations = @($violations)
    $report.UnpinnedDependencies = $violations.Count
    $report.CalculateScore()

    $output = switch ($Format) {
        'console' {
            if ($violations.Count -eq 0) {
                "No dangerous workflow findings were detected."
            }
            else {
                $lines = @('Dangerous workflow findings found:')
                foreach ($violation in $violations) {
                    $lines += "  - $($violation.File):$($violation.Line) [$($violation.Metadata.RuleId)] $($violation.Description)"
                }
                $lines -join "`n"
            }
        }
        'sarif' {
            (ConvertTo-DangerousWorkflowSarif -Violations $violations) | ConvertTo-Json -Depth 10
        }
        'json' {
            $report.ToHashtable() | ConvertTo-Json -Depth 10
        }
    }

    $outputDir = [System.IO.Path]::GetDirectoryName($OutputPath)
    if ($outputDir -and -not (Test-Path $outputDir)) {
        New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    }

    $output | Out-File -FilePath $OutputPath -Encoding utf8 -Force
    Write-SecurityLog "Results written to: $OutputPath" -Level Info

    $summaryLines = @(
        '## Dangerous Workflow Validation',
        '',
        '| Metric | Value |',
        '|--------|-------|',
        "| Total Workflows | $totalFiles |",
        "| Findings | $($violations.Count) |"
    )

    if ($violations.Count -gt 0) {
        $summaryLines += @('', '### Findings', '')
        foreach ($violation in $violations) {
            $summaryLines += "| $($violation.File) | $($violation.Metadata.RuleId) |"
        }
    }

    Write-CIStepSummary -Content ($summaryLines -join "`n")
    $output | Out-Host

    $exitCode = 0
    if ($violations.Count -gt 0 -and $FailOnViolation) {
        Write-SecurityLog "$($violations.Count) violation(s) found - failing" -Level Error -CIAnnotation
        $exitCode = 1
    }
    elseif ($violations.Count -gt 0) {
        Write-SecurityLog "$($violations.Count) violation(s) found - soft fail mode" -Level Warning -CIAnnotation
    }
    else {
        Write-SecurityLog 'No dangerous workflow findings found' -Level Success
    }

    return $exitCode
}

#endregion Functions

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $exitCode = Invoke-DangerousWorkflowCheck @PSBoundParameters
        exit $exitCode
    }
    catch {
        Write-SecurityLog "Fatal error: $_" -Level Error -CIAnnotation
        Write-SecurityLog $_.ScriptStackTrace -Level Error
        exit 1
    }
}
