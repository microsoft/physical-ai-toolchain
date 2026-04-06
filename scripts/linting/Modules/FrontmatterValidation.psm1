# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

#region Classes

class ValidationIssue {
    [ValidateSet('Error', 'Warning', 'Information')]
    [string] $Type
    [string] $Field
    [string] $Message
    [string] $FilePath
    [int]    $Line

    ValidationIssue([string] $type, [string] $field, [string] $message) {
        $this.Type = $type
        $this.Field = $field
        $this.Message = $message
        $this.FilePath = ''
        $this.Line = 0
    }

    ValidationIssue([string] $type, [string] $field, [string] $message, [string] $filePath) {
        $this.Type = $type
        $this.Field = $field
        $this.Message = $message
        $this.FilePath = $filePath
        $this.Line = 0
    }

    ValidationIssue([string] $type, [string] $field, [string] $message, [string] $filePath, [int] $line) {
        $this.Type = $type
        $this.Field = $field
        $this.Message = $message
        $this.FilePath = $filePath
        $this.Line = $line
    }

    [string] ToString() {
        $prefix = if ($this.FilePath) { "$($this.FilePath): " } else { '' }
        return "${prefix}[$($this.Type)] $($this.Field): $($this.Message)"
    }
}

class FileTypeInfo {
    [bool] $IsDocumentation
    [bool] $IsInstruction
    [bool] $IsPrompt
    [bool] $IsRootCommunity
    [bool] $RequiresFrontmatter

    FileTypeInfo() {
        $this.IsDocumentation = $false
        $this.IsInstruction = $false
        $this.IsPrompt = $false
        $this.IsRootCommunity = $false
        $this.RequiresFrontmatter = $false
    }
}

class FileValidationResult {
    [string] $FilePath
    [string] $RelativePath
    [System.Collections.Generic.List[ValidationIssue]] $Issues

    FileValidationResult([string] $filePath, [string] $relativePath) {
        $this.FilePath = $filePath
        $this.RelativePath = $relativePath
        $this.Issues = [System.Collections.Generic.List[ValidationIssue]]::new()
    }

    [void] AddIssue([ValidationIssue] $issue) {
        $issue.FilePath = $this.RelativePath
        $this.Issues.Add($issue)
    }

    [void] AddError([string] $field, [string] $message) {
        $this.AddIssue([ValidationIssue]::new('Error', $field, $message))
    }

    [void] AddWarning([string] $field, [string] $message) {
        $this.AddIssue([ValidationIssue]::new('Warning', $field, $message))
    }
}

class ValidationSummary {
    [System.Collections.Generic.List[FileValidationResult]] $Results
    [int]      $TotalFiles
    [int]      $PassedFiles
    [int]      $FailedFiles
    [int]      $TotalErrors
    [int]      $TotalWarnings
    [bool]     $Passed
    [datetime] $StartTime
    [datetime] $EndTime
    [timespan] $Duration

    ValidationSummary() {
        $this.Results = [System.Collections.Generic.List[FileValidationResult]]::new()
        $this.StartTime = [datetime]::UtcNow
        $this.Passed = $true
    }

    [void] AddResult([FileValidationResult] $result) {
        $this.Results.Add($result)
    }

    [void] Complete() {
        $this.EndTime = [datetime]::UtcNow
        $this.Duration = $this.EndTime - $this.StartTime
        $this.TotalFiles = $this.Results.Count
        if ($this.TotalFiles -eq 0) {
            $this.TotalErrors = 0
            $this.TotalWarnings = 0
            $this.PassedFiles = 0
        } else {
            $errorMeasure = $this.Results | ForEach-Object { @($_.Issues | Where-Object { $_.Type -eq 'Error' }).Count } | Measure-Object -Sum
            $this.TotalErrors = if ($null -ne $errorMeasure.Sum) { [int]$errorMeasure.Sum } else { 0 }
            $warningMeasure = $this.Results | ForEach-Object { @($_.Issues | Where-Object { $_.Type -eq 'Warning' }).Count } | Measure-Object -Sum
            $this.TotalWarnings = if ($null -ne $warningMeasure.Sum) { [int]$warningMeasure.Sum } else { 0 }
            $this.PassedFiles = @($this.Results | Where-Object { @($_.Issues | Where-Object { $_.Type -eq 'Error' }).Count -eq 0 }).Count
        }
        $this.FailedFiles = $this.TotalFiles - $this.PassedFiles
        $this.Passed = $this.TotalErrors -eq 0
    }

    [int] GetExitCode() {
        if ($this.Passed) { return 0 } else { return 1 }
    }

    [hashtable] ToHashtable() {
        return @{
            TotalFiles    = $this.TotalFiles
            PassedFiles   = $this.PassedFiles
            FailedFiles   = $this.FailedFiles
            TotalErrors   = $this.TotalErrors
            TotalWarnings = $this.TotalWarnings
            Passed        = $this.Passed
            Duration      = $this.Duration.TotalSeconds
        }
    }
}

#endregion Classes

# ScriptProperty registrations for FileValidationResult computed members
Update-TypeData -TypeName FileValidationResult -MemberType ScriptProperty -MemberName HasErrors -Value { @($this.Issues | Where-Object { $_.Type -eq 'Error' }).Count -gt 0 } -Force
Update-TypeData -TypeName FileValidationResult -MemberType ScriptProperty -MemberName HasWarnings -Value { @($this.Issues | Where-Object { $_.Type -eq 'Warning' }).Count -gt 0 } -Force
Update-TypeData -TypeName FileValidationResult -MemberType ScriptProperty -MemberName IsValid -Value { -not $this.HasErrors } -Force
Update-TypeData -TypeName FileValidationResult -MemberType ScriptProperty -MemberName ErrorCount -Value { @($this.Issues | Where-Object { $_.Type -eq 'Error' }).Count } -Force
Update-TypeData -TypeName FileValidationResult -MemberType ScriptProperty -MemberName WarningCount -Value { @($this.Issues | Where-Object { $_.Type -eq 'Warning' }).Count } -Force

#region Shared Helpers

function Get-FrontmatterFromFile {
    <#
    .SYNOPSIS
        Extracts and parses YAML frontmatter from a markdown file.
    .DESCRIPTION
        Reads markdown file content, extracts YAML frontmatter delimited by triple-dash
        lines, and returns the parsed hashtable using ConvertFrom-Yaml.
    .PARAMETER FilePath
        Absolute path to the markdown file.
    .OUTPUTS
        System.Collections.Hashtable or $null if no frontmatter found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath
    )

    $content = Get-Content -Path $FilePath -Raw -ErrorAction Stop
    if ($content -notmatch '(?s)\A---\r?\n(.+?)\r?\n---') {
        return $null
    }

    $yamlBlock = $Matches[1]
    $frontmatter = ConvertFrom-Yaml -Yaml $yamlBlock -ErrorAction Stop
    return $frontmatter
}

function Get-FileTypeInfo {
    <#
    .SYNOPSIS
        Determines file type classification from a relative path.
    .DESCRIPTION
        Classifies a markdown file based on its relative path within the repository,
        setting boolean flags for documentation, instruction, prompt, and root community files.
    .PARAMETER RelativePath
        Repository-relative path using forward slashes.
    .OUTPUTS
        FileTypeInfo object with classification flags.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $RelativePath
    )

    $info = [FileTypeInfo]::new()
    $normalizedPath = $RelativePath -replace '\\', '/'

    if ($normalizedPath -match '^docs/') {
        $info.IsDocumentation = $true
        $info.RequiresFrontmatter = $true
    }

    if ($normalizedPath -match '\.instructions\.md$') {
        $info.IsInstruction = $true
        $info.RequiresFrontmatter = $true
    }

    if ($normalizedPath -match '\.prompt\.md$') {
        $info.IsPrompt = $true
        $info.RequiresFrontmatter = $true
    }

    if ($normalizedPath -match '^[^/\\]+\.md$') {
        $info.IsRootCommunity = $true
        $info.RequiresFrontmatter = $true
    }

    return $info
}

function Test-FrontmatterPresence {
    <#
    .SYNOPSIS
        Validates that a markdown file contains YAML frontmatter.
    .DESCRIPTION
        Checks whether the file content begins with a valid YAML frontmatter block
        delimited by triple-dash lines.
    .PARAMETER FilePath
        Absolute path to the markdown file.
    .PARAMETER FileTypeInfo
        FileTypeInfo classification for the file.
    .OUTPUTS
        ValidationIssue[] array of issues found, empty if valid.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [FileTypeInfo] $FileTypeInfo
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $FileTypeInfo.RequiresFrontmatter) {
        return , $issues.ToArray()
    }

    $content = Get-Content -Path $FilePath -Raw -ErrorAction Stop
    if ($content -notmatch '(?s)\A---\r?\n(.+?)\r?\n---') {
        $issues.Add([ValidationIssue]::new('Error', 'frontmatter', 'Missing YAML frontmatter block'))
    }

    return , $issues.ToArray()
}

#endregion Shared Helpers

#region Field Validators

function Test-TitleField {
    <#
    .SYNOPSIS
        Validates the title frontmatter field.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .PARAMETER Required
        Whether the field is required.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter,

        [Parameter(Mandatory = $false)]
        [bool] $Required = $false
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $Frontmatter.ContainsKey('title')) {
        if ($Required) {
            $issues.Add([ValidationIssue]::new('Error', 'title', 'Required field "title" is missing'))
        }
        return , $issues.ToArray()
    }

    $value = $Frontmatter['title']
    if ([string]::IsNullOrWhiteSpace($value)) {
        $issues.Add([ValidationIssue]::new('Error', 'title', 'Field "title" must not be empty'))
    }

    return , $issues.ToArray()
}

function Test-DescriptionField {
    <#
    .SYNOPSIS
        Validates the description frontmatter field.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .PARAMETER Required
        Whether the field is required.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter,

        [Parameter(Mandatory = $false)]
        [bool] $Required = $false
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $Frontmatter.ContainsKey('description')) {
        if ($Required) {
            $issues.Add([ValidationIssue]::new('Error', 'description', 'Required field "description" is missing'))
        }
        return , $issues.ToArray()
    }

    $value = $Frontmatter['description']
    if ([string]::IsNullOrWhiteSpace($value)) {
        $issues.Add([ValidationIssue]::new('Error', 'description', 'Field "description" must not be empty'))
    }

    return , $issues.ToArray()
}

function Test-DateField {
    <#
    .SYNOPSIS
        Validates the ms.date frontmatter field for ISO 8601 format.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $Frontmatter.ContainsKey('ms.date')) {
        return , $issues.ToArray()
    }

    $value = $Frontmatter['ms.date']
    $dateString = "$value"

    if ($dateString -notmatch '^\d{4}-\d{2}-\d{2}$') {
        $issues.Add([ValidationIssue]::new('Error', 'ms.date', "Date must use ISO 8601 format (YYYY-MM-DD), got: $dateString"))
        return , $issues.ToArray()
    }

    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($dateString, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None, [ref] $parsed)) {
        $issues.Add([ValidationIssue]::new('Error', 'ms.date', "Invalid date value: $dateString"))
    }

    return , $issues.ToArray()
}

function Test-AuthorField {
    <#
    .SYNOPSIS
        Validates the author frontmatter field when present.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $Frontmatter.ContainsKey('author')) {
        return , $issues.ToArray()
    }

    $value = $Frontmatter['author']
    if ([string]::IsNullOrWhiteSpace($value)) {
        $issues.Add([ValidationIssue]::new('Warning', 'author', 'Field "author" is present but empty'))
    }

    return , $issues.ToArray()
}

function Test-ApplyToField {
    <#
    .SYNOPSIS
        Validates the applyTo frontmatter field for instruction files.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .PARAMETER Required
        Whether the field is required.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter,

        [Parameter(Mandatory = $false)]
        [bool] $Required = $false
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if (-not $Frontmatter.ContainsKey('applyTo')) {
        if ($Required) {
            $issues.Add([ValidationIssue]::new('Error', 'applyTo', 'Required field "applyTo" is missing'))
        }
        return , $issues.ToArray()
    }

    $value = $Frontmatter['applyTo']
    if ([string]::IsNullOrWhiteSpace($value)) {
        $issues.Add([ValidationIssue]::new('Error', 'applyTo', 'Field "applyTo" must not be empty'))
    }

    return , $issues.ToArray()
}

#endregion Field Validators

#region Content-Type Validators

function Test-DocumentationFileFields {
    <#
    .SYNOPSIS
        Validates frontmatter fields for documentation files under docs/.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    $issues.AddRange([ValidationIssue[]] (Test-TitleField -Frontmatter $Frontmatter -Required $true))
    $issues.AddRange([ValidationIssue[]] (Test-DescriptionField -Frontmatter $Frontmatter -Required $true))
    $issues.AddRange([ValidationIssue[]] (Test-DateField -Frontmatter $Frontmatter))
    $issues.AddRange([ValidationIssue[]] (Test-AuthorField -Frontmatter $Frontmatter))

    return , $issues.ToArray()
}

function Test-InstructionFileFields {
    <#
    .SYNOPSIS
        Validates frontmatter fields for instruction files (.instructions.md).
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    $issues.AddRange([ValidationIssue[]] (Test-DescriptionField -Frontmatter $Frontmatter -Required $true))
    $issues.AddRange([ValidationIssue[]] (Test-ApplyToField -Frontmatter $Frontmatter -Required $false))

    return , $issues.ToArray()
}

function Test-GitHubResourceFileFields {
    <#
    .SYNOPSIS
        Validates frontmatter fields for GitHub resource files (.github/**/*.md).
    .DESCRIPTION
        Routes validation to the appropriate content-type validator based on
        file classification. Instruction files validate description + applyTo;
        prompt files validate description.
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .PARAMETER FileTypeInfo
        FileTypeInfo classification for the file.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter,

        [Parameter(Mandatory = $true)]
        [FileTypeInfo] $FileTypeInfo
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    if ($FileTypeInfo.IsInstruction) {
        $issues.AddRange([ValidationIssue[]] (Test-InstructionFileFields -Frontmatter $Frontmatter))
    }
    elseif ($FileTypeInfo.IsPrompt) {
        $issues.AddRange([ValidationIssue[]] (Test-DescriptionField -Frontmatter $Frontmatter -Required $true))
    }

    return , $issues.ToArray()
}

function Test-RootCommunityFileFields {
    <#
    .SYNOPSIS
        Validates frontmatter fields for root community files (README.md, CONTRIBUTING.md, etc.).
    .PARAMETER Frontmatter
        Parsed frontmatter hashtable.
    .OUTPUTS
        ValidationIssue[] array of issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $Frontmatter
    )

    $issues = [System.Collections.Generic.List[ValidationIssue]]::new()

    $issues.AddRange([ValidationIssue[]] (Test-TitleField -Frontmatter $Frontmatter -Required $true))
    $issues.AddRange([ValidationIssue[]] (Test-DescriptionField -Frontmatter $Frontmatter -Required $true))

    return , $issues.ToArray()
}

function Test-MarkdownFooter {
    <#
    .SYNOPSIS
        Checks if markdown content contains the standard Copilot attribution footer.
    .PARAMETER Content
        The markdown content string to validate.
    .OUTPUTS
        [bool] $true if valid footer present; $false otherwise.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory, ValueFromPipeline)]
        [AllowEmptyString()]
        [string]$Content
    )

    process {
        if ([string]::IsNullOrEmpty($Content)) {
            return $false
        }

        $normalized = $Content -replace '(?s)<!--.*?-->', ''
        $normalized = $normalized -replace '\*\*([^*]+)\*\*', '$1'
        $normalized = $normalized -replace '__([^_]+)__', '$1'
        $normalized = $normalized -replace '\*([^*]+)\*', '$1'
        $normalized = $normalized -replace '_([^_]+)_', '$1'
        $normalized = $normalized -replace '~~([^~]+)~~', '$1'
        $normalized = $normalized -replace '`([^`]+)`', '$1'
        $normalized = $normalized.TrimEnd()

        $pattern = '🤖\s*Crafted\s+with\s+precision\s+by\s+\u2728Copilot\s+following\s+brilliant\s+human\s+instruction[,\s]+(then\s+)?carefully\s+refined\s+by\s+our\s+team\s+of\s+discerning\s+human\s+reviewers\.?'

        return $normalized -match $pattern
    }
}

function Test-FooterPresence {
    <#
    .SYNOPSIS
        Validates Copilot attribution footer presence.
    .PARAMETER HasFooter
        Boolean result from Test-MarkdownFooter.
    .PARAMETER RelativePath
        Relative path to the file being validated.
    .PARAMETER Severity
        Issue severity: 'Error' or 'Warning'. Default: 'Warning'.
    .OUTPUTS
        ValidationIssue or $null if footer is present.
    #>
    [CmdletBinding()]
    [OutputType([ValidationIssue])]
    param(
        [Parameter(Mandatory)]
        [bool]$HasFooter,

        [Parameter(Mandatory)]
        [string]$RelativePath,

        [Parameter()]
        [ValidateSet('Error', 'Warning')]
        [string]$Severity = 'Warning'
    )

    if (-not $HasFooter) {
        return [ValidationIssue]::new($Severity, 'footer', 'Missing standard Copilot footer', $RelativePath)
    }

    return $null
}

#endregion Content-Type Validators

#region Orchestration

function Test-SingleFileFrontmatter {
    <#
    .SYNOPSIS
        Validates frontmatter for a single markdown file.
    .DESCRIPTION
        Runs the full validation pipeline: presence check, YAML parsing,
        and content-type-specific field validation.
    .PARAMETER FilePath
        Absolute path to the markdown file.
    .PARAMETER RelativePath
        Repository-relative path for classification and reporting.
    .PARAMETER FooterExcludePaths
        Array of wildcard patterns for files to exclude from footer validation.
        Uses PowerShell -like operator for matching against relative paths.
        Path separators are normalized to forward slashes for cross-platform support.
    .PARAMETER FrontmatterExcludePaths
        Array of wildcard patterns for files to exclude from frontmatter validation.
        Matched files skip frontmatter presence and field checks but still undergo
        footer validation. Uses PowerShell -like operator against relative paths.
    .PARAMETER SkipFooterValidation
        When set, skips footer validation entirely.
    .OUTPUTS
        FileValidationResult with all issues found.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [string] $RelativePath,

        [string[]]$FooterExcludePaths = @(),

        [string[]]$FrontmatterExcludePaths = @(),

        [switch]$SkipFooterValidation
    )

    $result = [FileValidationResult]::new($FilePath, $RelativePath)
    $fileType = Get-FileTypeInfo -RelativePath $RelativePath

    # Check if frontmatter validation is excluded for this file
    $skipFrontmatterForFile = $false
    $normalizedRelPath = $RelativePath -replace '\\', '/'
    foreach ($pattern in $FrontmatterExcludePaths) {
        $normalizedPattern = $pattern -replace '\\', '/'
        if ($normalizedRelPath -like $normalizedPattern) {
            $skipFrontmatterForFile = $true
            break
        }
    }

    if (-not $fileType.RequiresFrontmatter) {
        return $result
    }

    if (-not $skipFrontmatterForFile) {
        # Check frontmatter presence
        $presenceIssues = Test-FrontmatterPresence -FilePath $FilePath -FileTypeInfo $fileType
        foreach ($issue in $presenceIssues) {
            $result.AddIssue($issue)
        }

        if ($result.HasErrors) {
            return $result
        }

        # Parse frontmatter
        $frontmatter = Get-FrontmatterFromFile -FilePath $FilePath
        if ($null -eq $frontmatter) {
            $result.AddError('frontmatter', 'Failed to parse YAML frontmatter')
            return $result
        }

        # Content-type validation
        if ($fileType.IsDocumentation) {
            $fieldIssues = Test-DocumentationFileFields -Frontmatter $frontmatter
            foreach ($issue in $fieldIssues) { $result.AddIssue($issue) }
        }

        if ($fileType.IsInstruction -or $fileType.IsPrompt) {
            $fieldIssues = Test-GitHubResourceFileFields -Frontmatter $frontmatter -FileTypeInfo $fileType
            foreach ($issue in $fieldIssues) { $result.AddIssue($issue) }
        }

        if ($fileType.IsRootCommunity) {
            $fieldIssues = Test-RootCommunityFileFields -Frontmatter $frontmatter
            foreach ($issue in $fieldIssues) { $result.AddIssue($issue) }
        }
    }

    # Footer validation
    $isAiArtifact = $fileType.IsPrompt -or $fileType.IsInstruction
    $skipFooterForFile = $false
    $normalizedRelativePath = $RelativePath -replace '\\', '/'
    foreach ($pattern in $FooterExcludePaths) {
        $normalizedPattern = $pattern -replace '\\', '/'
        if ($normalizedRelativePath -like $normalizedPattern) {
            $skipFooterForFile = $true
            break
        }
    }

    if (-not $isAiArtifact -and -not $SkipFooterValidation -and -not $skipFooterForFile) {
        $footerSeverity = if ($fileType.IsRootCommunity) { 'Error' } else { 'Warning' }
        $content = Get-Content -Path $FilePath -Raw -ErrorAction SilentlyContinue
        if ($content) {
            $hasFooter = Test-MarkdownFooter -Content $content
            $footerIssue = Test-FooterPresence -HasFooter $hasFooter -RelativePath $RelativePath -Severity $footerSeverity
            if ($footerIssue) {
                $result.AddIssue($footerIssue)
            }
        }
    }

    return $result
}

function New-ValidationSummary {
    <#
    .SYNOPSIS
        Creates a new ValidationSummary instance.
    .OUTPUTS
        ValidationSummary object ready to accept results.
    #>
    [CmdletBinding()]
    param()

    return [ValidationSummary]::new()
}

#endregion Orchestration

#region Module Exports

Export-ModuleMember -Function @(
    'Get-FrontmatterFromFile',
    'Get-FileTypeInfo',
    'Test-FrontmatterPresence',
    'Test-DescriptionField',
    'Test-TitleField',
    'Test-DateField',
    'Test-AuthorField',
    'Test-ApplyToField',
    'Test-DocumentationFileFields',
    'Test-InstructionFileFields',
    'Test-GitHubResourceFileFields',
    'Test-RootCommunityFileFields',
    'Test-MarkdownFooter',
    'Test-FooterPresence',
    'Test-SingleFileFrontmatter',
    'New-ValidationSummary'
)

#endregion Module Exports
