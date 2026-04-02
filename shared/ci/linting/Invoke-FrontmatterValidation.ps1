#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Validates YAML frontmatter in markdown files against schema and structural rules.

.DESCRIPTION
    Scans markdown files for valid YAML frontmatter, applying file-type-specific rules
    and optional JSON schema validation. Integrates with CI platforms via CIHelpers for
    annotations, outputs, and step summaries.

.PARAMETER Paths
    Directories to scan for markdown files. Defaults to repository root.

.PARAMETER Files
    Explicit file paths to validate instead of scanning directories.

.PARAMETER ExcludePaths
    Directory names to exclude from scanning.

.PARAMETER WarningsAsErrors
    Treat warnings as errors for CI exit code purposes.

.PARAMETER ChangedFilesOnly
    Validate only files changed relative to BaseBranch.

.PARAMETER BaseBranch
    Base branch for changed-file detection. Defaults to 'main'.

.PARAMETER EnableSchemaValidation
    Enable JSON schema validation against schema-mapping.json definitions.

.PARAMETER SoftFail
    Emit annotations and outputs without failing the CI step on errors.
#>

#Requires -Version 7.0

using namespace System.Collections.Generic
using module .\Modules\FrontmatterValidation.psm1

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', '', Justification = 'Parameters consumed via script scope in Invoke-Validation')]
[CmdletBinding()]
param(
    [Parameter()]
    [string[]]$Paths = @('.'),

    [Parameter()]
    [string[]]$Files = @(),

    [Parameter()]
    [string[]]$ExcludePaths = @('node_modules', '.git', 'logs', '.copilot-tracking', 'CHANGELOG.md'),

    [Parameter()]
    [switch]$WarningsAsErrors,

    [Parameter()]
    [switch]$ChangedFilesOnly,

    [Parameter()]
    [string]$BaseBranch = 'origin/main',

    [Parameter()]
    [switch]$EnableSchemaValidation,

    [Parameter()]
    [string[]]$FooterExcludePaths = @('dependency-pinning-artifacts/**'),

    [Parameter()]
    [string[]]$FrontmatterExcludePaths = @('README.md'),

    [Parameter()]
    [switch]$SkipFooterValidation,

    [Parameter()]
    [switch]$SoftFail
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
Import-Module (Join-Path $scriptRoot 'Modules' 'LintingHelpers.psm1') -Force
Import-Module (Join-Path $scriptRoot '..' '..' '..' 'shared' 'lib' 'Modules' 'CIHelpers.psm1') -Force

#region Schema Validation

class SchemaValidationResult {
    [bool]$IsValid
    [string[]]$Errors
    [string]$SchemaName

    SchemaValidationResult() {
        $this.IsValid = $true
        $this.Errors = @()
        $this.SchemaName = ''
    }
}

function Initialize-JsonSchemaValidation {
    [CmdletBinding()]
    [OutputType([hashtable])]
    param(
        [Parameter(Mandatory)]
        [string]$SchemaDirectory
    )

    $mappingPath = Join-Path $SchemaDirectory 'schema-mapping.json'
    if (-not (Test-Path $mappingPath)) {
        Write-Warning "Schema mapping not found: $mappingPath"
        return $null
    }

    $mapping = Get-Content $mappingPath -Raw | ConvertFrom-Json
    $schemas = @{}

    foreach ($entry in $mapping.mappings) {
        $schemaPath = Join-Path $SchemaDirectory $entry.schema
        if (Test-Path $schemaPath) {
            $schemas[$entry.schema] = Get-Content $schemaPath -Raw | ConvertFrom-Json
        } else {
            Write-Warning "Schema file not found: $schemaPath"
        }
    }

    # Load default schema
    if ($mapping.PSObject.Properties['defaultSchema']) {
        $defaultPath = Join-Path $SchemaDirectory $mapping.defaultSchema
        if ((Test-Path $defaultPath) -and -not $schemas.ContainsKey($mapping.defaultSchema)) {
            $schemas[$mapping.defaultSchema] = Get-Content $defaultPath -Raw | ConvertFrom-Json
        }
    }

    return @{
        Mapping  = $mapping
        Schemas  = $schemas
        BasePath = $SchemaDirectory
    }
}

function Get-SchemaForFile {
    [CmdletBinding()]
    [OutputType([PSCustomObject])]
    param(
        [Parameter(Mandatory)]
        [string]$FilePath,

        [Parameter(Mandatory)]
        [hashtable]$SchemaContext
    )

    $mapping = $SchemaContext.Mapping
    $relativePath = $FilePath -replace '\\', '/'

    # Strip leading ./ if present
    if ($relativePath.StartsWith('./')) {
        $relativePath = $relativePath.Substring(2)
    }

    foreach ($entry in $mapping.mappings) {
        $glob = $entry.glob
        $matched = $false

        if ($glob.Contains('**')) {
            # Recursive glob: convert **/ to optional path prefix, then remaining * to segment match
            $regexPattern = [regex]::Escape($glob) -replace '\\\*\\\*/', '(.*/)?'  -replace '\\\*\\\*', '.*' -replace '\\\*', '[^/]*'
            if ($relativePath -match "^$regexPattern$") {
                $matched = $true
            }
        } elseif ($glob.Contains('|')) {
            # Alternation: split on | and check exact match
            $alternatives = $glob -split '\|'
            foreach ($alt in $alternatives) {
                $fileName = [System.IO.Path]::GetFileName($relativePath)
                if ($fileName -eq $alt.Trim()) {
                    $matched = $true
                    break
                }
            }
        } elseif ($glob.Contains('*')) {
            # Simple glob
            $regexPattern = [regex]::Escape($glob) -replace '\\\*', '[^/]*'
            if ($relativePath -match "^$regexPattern$") {
                $matched = $true
            }
        } else {
            # Exact match
            $fileName = [System.IO.Path]::GetFileName($relativePath)
            if ($fileName -eq $glob -or $relativePath -eq $glob) {
                $matched = $true
            }
        }

        if ($matched) {
            return [PSCustomObject]@{
                SchemaName = $entry.schema
                Schema     = $SchemaContext.Schemas[$entry.schema]
            }
        }
    }

    # Default to base schema from mapping configuration
    $defaultSchema = if ($mapping.PSObject.Properties['defaultSchema']) { $mapping.defaultSchema } else { 'base-frontmatter.schema.json' }
    if ($SchemaContext.Schemas.ContainsKey($defaultSchema)) {
        return [PSCustomObject]@{
            SchemaName = $defaultSchema
            Schema     = $SchemaContext.Schemas[$defaultSchema]
        }
    }

    return $null
}

function Get-JsonSchemaPointerValue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Schema,

        [Parameter(Mandatory)]
        [string]$Pointer
    )

    if ([string]::IsNullOrWhiteSpace($Pointer) -or $Pointer -eq '/') {
        return $Schema
    }

    $segments = $Pointer.TrimStart('/') -split '/'
    $current = $Schema

    foreach ($segment in $segments) {
        $key = $segment -replace '~1', '/' -replace '~0', '~'
        if ($current -is [System.Collections.IDictionary]) {
            if (-not $current.Contains($key)) { return $null }
            $current = $current[$key]
            continue
        }
        $prop = $current.PSObject.Properties[$key]
        if ($null -eq $prop) { return $null }
        $current = $prop.Value
    }

    return $current
}

function Resolve-JsonSchemaRef {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Ref,

        [Parameter(Mandatory)]
        [object]$RootSchema,

        [Parameter()]
        [hashtable]$SchemaContext = $null
    )

    if ($Ref.StartsWith('#')) {
        $pointer = $Ref.Substring(1)
        return Get-JsonSchemaPointerValue -Schema $RootSchema -Pointer $pointer
    }

    $filePart = $Ref
    $pointerPart = $null
    if ($Ref -like '*#*') {
        $split = $Ref.Split('#', 2)
        $filePart = $split[0]
        $pointerPart = $split[1]
    }

    $externalSchema = $null
    if ($null -ne $SchemaContext -and $SchemaContext.Schemas.ContainsKey($filePart)) {
        $externalSchema = $SchemaContext.Schemas[$filePart]
    } elseif ($null -ne $SchemaContext) {
        $resolvedPath = Join-Path $SchemaContext.BasePath $filePart
        if (Test-Path $resolvedPath) {
            $externalSchema = Get-Content $resolvedPath -Raw | ConvertFrom-Json -Depth 64
        }
    }

    if ($null -eq $externalSchema) { return $null }
    if ([string]::IsNullOrWhiteSpace($pointerPart)) { return $externalSchema }
    return Get-JsonSchemaPointerValue -Schema $externalSchema -Pointer $pointerPart
}

function Test-JsonSchemaValidation {
    [CmdletBinding()]
    [OutputType([SchemaValidationResult])]
    param(
        [Parameter(Mandatory)]
        [hashtable]$Frontmatter,

        [Parameter(Mandatory)]
        [PSCustomObject]$SchemaInfo,

        [Parameter()]
        [hashtable]$SchemaContext = $null
    )

    $result = [SchemaValidationResult]::new()
    $result.SchemaName = $SchemaInfo.SchemaName
    $schema = $SchemaInfo.Schema

    if ($null -eq $schema) {
        return $result
    }

    $errors = [List[string]]::new()

    # Resolve allOf references (including external $ref targets)
    $allSchemas = @($schema)
    if ($schema.PSObject.Properties['allOf']) {
        foreach ($subSchema in $schema.allOf) {
            if ($subSchema.PSObject.Properties['$ref']) {
                $resolved = Resolve-JsonSchemaRef -Ref $subSchema.'$ref' -RootSchema $schema -SchemaContext $SchemaContext
                if ($null -ne $resolved) {
                    $allSchemas += $resolved
                }
            } else {
                $allSchemas += $subSchema
            }
        }
    }

    foreach ($s in $allSchemas) {
        # Check required fields
        if ($s.PSObject.Properties['required']) {
            foreach ($field in $s.required) {
                if (-not $Frontmatter.ContainsKey($field)) {
                    $errors.Add("Missing required field: '$field'")
                }
            }
        }

        # Check property types and constraints
        if ($s.PSObject.Properties['properties']) {
            foreach ($prop in $s.properties.PSObject.Properties) {
                $propName = $prop.Name
                $propSchema = $prop.Value

                if (-not $Frontmatter.ContainsKey($propName)) {
                    continue
                }

                $value = $Frontmatter[$propName]

                # Resolve property-level $ref
                if ($propSchema.PSObject.Properties['$ref']) {
                    $resolvedProp = Resolve-JsonSchemaRef -Ref $propSchema.'$ref' -RootSchema $schema -SchemaContext $SchemaContext
                    if ($null -ne $resolvedProp) {
                        $propSchema = $resolvedProp
                    }
                }

                # Type validation
                if ($propSchema.PSObject.Properties['type']) {
                    switch ($propSchema.type) {
                        'string' {
                            if ($value -isnot [string]) {
                                $errors.Add("Field '$propName' must be a string")
                            }
                        }
                        'integer' {
                            if ($value -isnot [int] -and $value -isnot [long]) {
                                $errors.Add("Field '$propName' must be an integer")
                            }
                        }
                        'boolean' {
                            if ($value -isnot [bool]) {
                                $errors.Add("Field '$propName' must be a boolean")
                            }
                        }
                        'array' {
                            if ($value -isnot [array] -and $value -isnot [System.Collections.IList]) {
                                $errors.Add("Field '$propName' must be an array")
                            }
                        }
                    }
                }

                # Pattern validation
                if ($propSchema.PSObject.Properties['pattern'] -and $value -is [string]) {
                    if ($value -notmatch $propSchema.pattern) {
                        $errors.Add("Field '$propName' does not match pattern: $($propSchema.pattern)")
                    }
                }

                # Enum validation
                if ($propSchema.PSObject.Properties['enum']) {
                    if ($value -notin $propSchema.enum) {
                        $errors.Add("Field '$propName' must be one of: $($propSchema.enum -join ', ')")
                    }
                }

                # MinLength validation
                if ($propSchema.PSObject.Properties['minLength'] -and $value -is [string]) {
                    if ($value.Length -lt $propSchema.minLength) {
                        $errors.Add("Field '$propName' must have minimum length of $($propSchema.minLength)")
                    }
                }
            }
        }

        # Check additionalProperties
        if ($s.PSObject.Properties['additionalProperties'] -and $s.additionalProperties -eq $false) {
            $allowedProps = @()
            if ($s.PSObject.Properties['properties']) {
                $allowedProps = $s.properties.PSObject.Properties.Name
            }
            foreach ($key in $Frontmatter.Keys) {
                if ($key -notin $allowedProps) {
                    $errors.Add("Additional property not allowed: '$key'")
                }
            }
        }
    }

    if ($errors.Count -gt 0) {
        $result.IsValid = $false
        $result.Errors = $errors.ToArray()
    }

    return $result
}

#endregion

#region File Collection

function Get-MarkdownFiles {
    [CmdletBinding()]
    [OutputType([string[]])]
    param(
        [string[]]$ScanPaths,
        [string[]]$ExplicitFiles,
        [string[]]$Exclude,
        [switch]$ChangedOnly,
        [string]$Branch
    )

    $files = @()

    if ($ChangedOnly) {
        $files = @(Get-ChangedFilesFromGit -FileExtensions @('*.md') -BaseBranch $Branch)

        # Normalize to an array of strings and remove empties
        $files = @($files | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })

        if ($files.Count -eq 0) {
            Write-CIAnnotation -Message 'No changed markdown files detected' -Level 'Notice'
            Set-CIOutput -Name 'total-issues' -Value '0' -IsOutput
            return @()
        }
    } elseif (@($ExplicitFiles).Count -gt 0) {
        $files = $ExplicitFiles | Where-Object { Test-Path $_ } | ForEach-Object { (Resolve-Path $_).Path }
    } else {
        foreach ($scanPath in $ScanPaths) {
            $resolved = Resolve-Path $scanPath -ErrorAction SilentlyContinue
            if ($resolved) {
                $found = Get-FilesRecursive -Path $resolved.Path -Include @('*.md')
                $files += $found
            }
        }
    }

    # Apply exclude patterns
    if (@($Exclude).Count -gt 0) {
        $files = @($files)
        if ($files.Count -gt 0) {
            $files = $files | Where-Object {
                $filePath = $_
                $excluded = $false
                foreach ($pattern in $Exclude) {
                    if ($filePath -like "*/$pattern/*" -or $filePath -like "*\$pattern\*" -or
                        $filePath -like "*/$pattern" -or $filePath -like "*\$pattern" -or
                        [System.IO.Path]::GetFileName($filePath) -eq $pattern) {
                        $excluded = $true
                        break
                    }
                }
                -not $excluded
            }
        }
    }

    return @($files)
}

#endregion

#region Main Execution

function Invoke-Validation {
    [CmdletBinding()]
    param()

    if (-not $script:scriptRoot) { $script:scriptRoot = (Get-Location).Path }

    $mdFiles = @(Get-MarkdownFiles `
        -ScanPaths $script:Paths `
        -ExplicitFiles $script:Files `
        -Exclude $script:ExcludePaths `
        -ChangedOnly:$script:ChangedFilesOnly `
        -Branch $script:BaseBranch)

    if ($mdFiles.Count -eq 0) {
        Write-Host 'No markdown files to validate.'
        Set-CIOutput -Name 'total-issues' -Value '0' -IsOutput
        Set-CIOutput -Name 'total-errors' -Value '0' -IsOutput
        Set-CIOutput -Name 'total-warnings' -Value '0' -IsOutput
        Set-CIOutput -Name 'files-checked' -Value '0' -IsOutput
        $summaryMd = @"
## Frontmatter Validation Results

| Metric | Count |
|--------|-------|
| Files checked | 0 |
| Files passed | 0 |
| Files failed | 0 |
| Errors | 0 |
| Warnings | 0 |
"@
        Write-CIStepSummary -Content $summaryMd
        $repoRoot = Split-Path (Split-Path $script:scriptRoot -Parent) -Parent
        $earlyLogsDir = Join-Path $repoRoot 'logs'
        if (-not (Test-Path $earlyLogsDir)) {
            New-Item -ItemType Directory -Path $earlyLogsDir -Force | Out-Null
        }
        $emptyResults = [PSCustomObject]@{
            timestamp = (Get-Date -Format 'o')
            summary   = @{ totalFiles = 0; passedFiles = 0; failedFiles = 0; errorCount = 0; warningCount = 0 }
            results   = @()
        }
        $jsonPath = Join-Path $earlyLogsDir 'frontmatter-validation-results.json'
        $emptyResults | ConvertTo-Json -Depth 10 | Set-Content -Path $jsonPath -Encoding utf8
        Write-Host "Results exported to $jsonPath"
        return
    }

    Write-Host "Validating frontmatter in $($mdFiles.Count) file(s)..." -ForegroundColor Cyan

    # Per-file validation
    $results = [List[FileValidationResult]]::new()
    $repoRoot = $null
    try {
        $repoRoot = (Resolve-Path (Join-Path $script:scriptRoot '..' '..')).Path
        if (-not $repoRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
            $repoRoot += [System.IO.Path]::DirectorySeparatorChar
        }
    } catch {
        $repoRoot = $null
    }
    foreach ($file in $mdFiles) {
        $filePath = if ($file -is [System.IO.FileInfo]) { $file.FullName } else { [string]$file }
        $relativePath = $filePath
        if ([System.IO.Path]::IsPathRooted($filePath) -and $null -ne $repoRoot -and $filePath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            $relativePath = $filePath.Substring($repoRoot.Length)
        } elseif ([System.IO.Path]::IsPathRooted($filePath)) {
            $relativePath = [System.IO.Path]::GetFileName($filePath)
        }
        $fileResult = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath $relativePath -FooterExcludePaths $script:FooterExcludePaths -FrontmatterExcludePaths $script:FrontmatterExcludePaths -SkipFooterValidation:$script:SkipFooterValidation
        $results.Add($fileResult)
    }

    # Schema validation overlay
    $schemaContext = $null
    if ($script:EnableSchemaValidation) {
        $schemaDir = Join-Path $script:scriptRoot 'schemas' 'frontmatter'
        $schemaContext = Initialize-JsonSchemaValidation -SchemaDirectory $schemaDir

        if ($null -ne $schemaContext) {
            foreach ($fileResult in $results) {
                $fm = Get-FrontmatterFromFile -FilePath $fileResult.FilePath
                if ($null -eq $fm -or $fm.Count -eq 0) {
                    continue
                }

                $schemaInfo = Get-SchemaForFile -FilePath $fileResult.RelativePath -SchemaContext $schemaContext
                if ($null -eq $schemaInfo -or $null -eq $schemaInfo.Schema) {
                    continue
                }

                $schemaResult = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo -SchemaContext $schemaContext
                if (-not $schemaResult.IsValid) {
                    foreach ($err in $schemaResult.Errors) {
                        $fileResult.AddError("schema/$($schemaResult.SchemaName)", $err)
                    }
                }
            }
        }
    }

    # Build summary
    $summary = New-ValidationSummary
    foreach ($fileResult in $results) {
        $summary.AddResult($fileResult)
    }
    $summary.Complete()

    # Console output
    $invalidFiles = @($results | Where-Object { -not $_.IsValid })
    if ($invalidFiles.Count -gt 0) {
        Write-Host "`nValidation issues found:" -ForegroundColor Yellow
        foreach ($file in $invalidFiles) {
            Write-Host "  $($file.FilePath)" -ForegroundColor Red
            foreach ($issue in $file.Issues) {
                $prefix = if ($issue.Type -eq 'Error') { '    ERROR' } else { '    WARN ' }
                Write-Host "$prefix [$($issue.Field)] $($issue.Message)" -ForegroundColor $(
                    if ($issue.Type -eq 'Error') { 'Red' } else { 'Yellow' }
                )
            }
        }
    }

    $totalErrors = $summary.TotalErrors
    $totalWarnings = $summary.TotalWarnings
    $totalIssues = $totalErrors + $totalWarnings

    Write-Host "`nSummary: $($summary.TotalFiles) files, $totalErrors error(s), $totalWarnings warning(s)" -ForegroundColor $(
        if ($totalErrors -gt 0) { 'Red' } elseif ($totalWarnings -gt 0) { 'Yellow' } else { 'Green' }
    )

    # CI annotations
    $ciSummary = [PSCustomObject]@{
        Results = $results | ForEach-Object {
            [PSCustomObject]@{
                File   = $_.FilePath
                Issues = $_.Issues | ForEach-Object {
                    [PSCustomObject]@{
                        Message  = $_.Message
                        Severity = $_.Type
                        File     = $_.FilePath
                        Line     = $_.Line
                        Column   = 1
                    }
                }
            }
        }
    }
    Write-CIAnnotations -Summary $ciSummary

    # CI outputs
    Set-CIOutput -Name 'total-issues' -Value "$totalIssues" -IsOutput
    Set-CIOutput -Name 'total-errors' -Value "$totalErrors" -IsOutput
    Set-CIOutput -Name 'total-warnings' -Value "$totalWarnings" -IsOutput
    Set-CIOutput -Name 'files-checked' -Value "$($summary.TotalFiles)" -IsOutput

    # Step summary markdown
    $summaryMd = @"
## Frontmatter Validation Results

| Metric | Count |
|--------|-------|
| Files checked | $($summary.TotalFiles) |
| Files passed | $($summary.PassedFiles) |
| Files failed | $($summary.FailedFiles) |
| Errors | $totalErrors |
| Warnings | $totalWarnings |
"@

    Write-CIStepSummary -Content $summaryMd

    # Promote warnings to errors when WarningsAsErrors is set
    $effectiveErrors = $totalErrors
    if ($script:WarningsAsErrors) {
        $effectiveErrors += $totalWarnings
    }

    # Environment variable for downstream jobs
    if ($effectiveErrors -gt 0) {
        Set-CIEnv -Name 'FRONTMATTER_VALIDATION_FAILED' -Value 'true'
    }

    # JSON export
    $repoRoot = Split-Path (Split-Path $script:scriptRoot -Parent) -Parent
    $logsDir = Join-Path $repoRoot 'logs'
    if (-not (Test-Path $logsDir)) {
        New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
    }

    $jsonOutput = [PSCustomObject]@{
        timestamp = (Get-Date -Format 'o')
        summary   = @{
            totalFiles   = $summary.TotalFiles
            passedFiles  = $summary.PassedFiles
            failedFiles  = $summary.FailedFiles
            errorCount   = $totalErrors
            warningCount = $totalWarnings
        }
        results   = $results | ForEach-Object {
            @{
                file    = $_.FilePath
                isValid = $_.IsValid
                issues  = @($_.Issues | ForEach-Object {
                    @{
                        line    = $_.Line
                        type    = $_.Type
                        field   = $_.Field
                        message = $_.Message
                    }
                })
            }
        }
    }

    $jsonPath = Join-Path $logsDir 'frontmatter-validation-results.json'
    $jsonOutput | ConvertTo-Json -Depth 10 | Set-Content -Path $jsonPath -Encoding utf8
    Write-Host "Results exported to $jsonPath"

    # Exit logic
    if ($effectiveErrors -gt 0 -and -not $script:SoftFail) {
        exit 1
    }
}

# Dot-source guard: only run main when executed directly
if ($MyInvocation.InvocationName -ne '.' -and $MyInvocation.InvocationName -ne 'Import-Module') {
    try {
        Invoke-Validation
    } catch {
        Write-CIAnnotation -Message "Frontmatter validation failed: $_" -Level 'Error'
        Write-Host "Stack trace: $($_.ScriptStackTrace)" -ForegroundColor Red
        if (-not $SoftFail) {
            exit 1
        }
    }
}
