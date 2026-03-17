# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# CIHelpers.psm1
#
# Purpose: Shared CI platform detection and output utilities.

#Requires -Version 7.0

<#
.SYNOPSIS
Escapes a string for safe use in GitHub Actions workflow commands.

.DESCRIPTION
Percent-encodes characters that have special meaning in GitHub Actions workflow commands.
When ForProperty is specified, additional characters used in command properties are escaped.

.PARAMETER Value
The string value to escape.

.PARAMETER ForProperty
When specified, also escapes colon and comma characters used in workflow command properties.

.OUTPUTS
System.String
#>
function ConvertTo-GitHubActionsEscaped {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [AllowEmptyString()]
        [string]$Value,

        [Parameter(Mandatory = $false)]
        [switch]$ForProperty
    )

    if ([string]::IsNullOrEmpty($Value)) {
        return $Value
    }

    $escaped = $Value
    $escaped = $escaped.Replace('%', '%25')
    $escaped = $escaped.Replace("`r", '%0D')
    $escaped = $escaped.Replace("`n", '%0A')
    $escaped = $escaped.Replace('::', '%3A%3A')

    if ($ForProperty) {
        $escaped = $escaped.Replace(':', '%3A')
        $escaped = $escaped.Replace(',', '%2C')
    }

    return $escaped
}

<#
.SYNOPSIS
Escapes a string for safe use in Azure DevOps logging commands.

.DESCRIPTION
Encodes characters that have special meaning in Azure DevOps logging commands using
the AZP percent-encoding scheme. When ForProperty is specified, the semicolon character
is also escaped.

.PARAMETER Value
The string value to escape.

.PARAMETER ForProperty
When specified, also escapes semicolons used in logging command properties.

.OUTPUTS
System.String
#>
function ConvertTo-AzureDevOpsEscaped {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [AllowEmptyString()]
        [string]$Value,

        [Parameter(Mandatory = $false)]
        [switch]$ForProperty
    )

    if ([string]::IsNullOrEmpty($Value)) {
        return $Value
    }

    $escaped = $Value
    $escaped = $escaped.Replace('%', '%AZP25')
    $escaped = $escaped.Replace("`r", '%AZP0D')
    $escaped = $escaped.Replace("`n", '%AZP0A')
    $escaped = $escaped.Replace('[', '%AZP5B')
    $escaped = $escaped.Replace(']', '%AZP5D')

    if ($ForProperty) {
        $escaped = $escaped.Replace(';', '%AZP3B')
    }

    return $escaped
}

<#
.SYNOPSIS
Detects the current CI platform.

.DESCRIPTION
Returns the CI platform identifier based on environment variables. GitHub Actions is
checked first, followed by Azure DevOps. Returns 'local' when no CI environment is detected.

.OUTPUTS
System.String
#>
function Get-CIPlatform {
    [CmdletBinding()]
    [OutputType([string])]
    param()

    if ($env:GITHUB_ACTIONS -eq 'true') {
        return 'github'
    }

    if ($env:TF_BUILD -eq 'True' -or $env:AZURE_PIPELINES -eq 'True') {
        return 'azdo'
    }

    return 'local'
}

<#
.SYNOPSIS
Tests whether the script is running in a CI environment.

.DESCRIPTION
Returns true when running in GitHub Actions or Azure DevOps, false otherwise.

.OUTPUTS
System.Boolean
#>
function Test-CIEnvironment {
    [CmdletBinding()]
    [OutputType([bool])]
    param()

    return (Get-CIPlatform) -ne 'local'
}

<#
.SYNOPSIS
Validates the PowerShell runtime version meets a minimum requirement.

.DESCRIPTION
Compares the current PowerShell version against a minimum version. Emits a CI warning
annotation when the version is below the threshold.

.PARAMETER MinimumVersion
The minimum required PowerShell version. Defaults to 7.0.

.OUTPUTS
System.Boolean
#>
function Test-PowerShellVersion {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $false)]
        [version]$MinimumVersion = '7.0'
    )

    $currentVersion = $PSVersionTable.PSVersion

    if ($currentVersion -lt $MinimumVersion) {
        Write-CIAnnotation -Message "PowerShell $currentVersion is below minimum $MinimumVersion" -Level 'Warning'
        return $false
    }

    return $true
}

<#
.SYNOPSIS
Sets a CI output variable on the current platform.

.DESCRIPTION
Sets an output variable using the appropriate mechanism for the detected CI platform.
GitHub Actions appends to GITHUB_OUTPUT, Azure DevOps uses setvariable logging commands,
and local runs log via Write-Verbose.

.PARAMETER Name
The output variable name.

.PARAMETER Value
The output variable value.

.PARAMETER IsOutput
When specified, marks the variable as an output variable in Azure DevOps.

.OUTPUTS
System.Void
#>
function Set-CIOutput {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value,

        [Parameter(Mandatory = $false)]
        [switch]$IsOutput
    )

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            if ([string]::IsNullOrEmpty($env:GITHUB_OUTPUT)) {
                Write-Warning 'GITHUB_OUTPUT is not set; falling back to verbose logging'
                Write-Verbose "CI output: $Name=$Value"
                return
            }
            $escapedName = ConvertTo-GitHubActionsEscaped -Value $Name -ForProperty
            $escapedValue = ConvertTo-GitHubActionsEscaped -Value $Value
            "$escapedName=$escapedValue" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
        }
        'azdo' {
            $escapedName = ConvertTo-AzureDevOpsEscaped -Value $Name -ForProperty
            $escapedValue = ConvertTo-AzureDevOpsEscaped -Value $Value
            $outputFlag = if ($IsOutput) { ';isOutput=true' } else { '' }
            Write-Host "##vso[task.setvariable variable=$escapedName$outputFlag]$escapedValue"
        }
        default {
            Write-Verbose "CI output: $Name=$Value"
        }
    }
}

<#
.SYNOPSIS
Sets a CI environment variable on the current platform.

.DESCRIPTION
Sets an environment variable using the appropriate mechanism for the detected CI platform.
GitHub Actions uses heredoc syntax appended to GITHUB_ENV, Azure DevOps uses setvariable
logging commands, and local runs log via Write-Verbose.

.PARAMETER Name
The environment variable name. Must match the pattern [A-Za-z_][A-Za-z0-9_]*.

.PARAMETER Value
The environment variable value.

.OUTPUTS
System.Void
#>
function Set-CIEnv {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    if ($Name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        Write-Warning "Invalid environment variable name: $Name"
        return
    }

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            if ([string]::IsNullOrEmpty($env:GITHUB_ENV)) {
                Write-Warning 'GITHUB_ENV is not set; falling back to verbose logging'
                Write-Verbose "CI env: $Name=$Value"
                return
            }
            $delimiter = "EOF_$([guid]::NewGuid().ToString('N'))"
            "$Name<<$delimiter", $Value, $delimiter | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
        }
        'azdo' {
            $escapedName = ConvertTo-AzureDevOpsEscaped -Value $Name -ForProperty
            $escapedValue = ConvertTo-AzureDevOpsEscaped -Value $Value
            Write-Host "##vso[task.setvariable variable=$escapedName]$escapedValue"
        }
        default {
            Write-Verbose "CI env: $Name=$Value"
        }
    }
}

<#
.SYNOPSIS
Writes markdown content to the CI step summary.

.DESCRIPTION
Appends markdown content to the step summary for the detected CI platform. GitHub Actions
appends to GITHUB_STEP_SUMMARY, Azure DevOps writes a section header followed by content,
and local runs log via Write-Verbose.

.PARAMETER Content
Markdown content to append to the step summary.

.PARAMETER Path
Path to a file containing markdown content to append to the step summary.

.OUTPUTS
System.Void
#>
function Write-CIStepSummary {
    [CmdletBinding(DefaultParameterSetName = 'Content')]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true, ParameterSetName = 'Content')]
        [string]$Content,

        [Parameter(Mandatory = $true, ParameterSetName = 'Path')]
        [string]$Path
    )

    if ($PSCmdlet.ParameterSetName -eq 'Path') {
        if (-not (Test-Path -Path $Path)) {
            Write-Warning "Summary file not found: $Path"
            return
        }
        $Content = Get-Content -Path $Path -Raw
    }

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            if ([string]::IsNullOrEmpty($env:GITHUB_STEP_SUMMARY)) {
                Write-Warning 'GITHUB_STEP_SUMMARY is not set; falling back to verbose logging'
                Write-Verbose "Step summary: $Content"
                return
            }
            $Content | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
        }
        'azdo' {
            $safeContent = $Content -replace '##vso\[', '`##vso[' -replace '##\[', '`##['
            Write-Host "##[section]Step Summary"
            Write-Host $safeContent
        }
        default {
            Write-Verbose "Step summary: $Content"
        }
    }
}

<#
.SYNOPSIS
Writes a CI annotation (warning, error, or notice) on the current platform.

.DESCRIPTION
Emits an annotation using the appropriate syntax for the detected CI platform. GitHub Actions
uses workflow command syntax, Azure DevOps uses logissue commands, and local runs use
Write-Warning with a formatted prefix.

.PARAMETER Message
The annotation message text.

.PARAMETER Level
The annotation severity level. Valid values are Warning, Error, and Notice. Defaults to Warning.

.PARAMETER File
Optional file path associated with the annotation.

.PARAMETER Line
Optional line number associated with the annotation.

.PARAMETER Column
Optional column number associated with the annotation.

.OUTPUTS
System.Void
#>
function Write-CIAnnotation {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Message,

        [Parameter(Mandatory = $false)]
        [ValidateSet('Warning', 'Error', 'Notice')]
        [string]$Level = 'Warning',

        [Parameter(Mandatory = $false)]
        [string]$File,

        [Parameter(Mandatory = $false)]
        [int]$Line,

        [Parameter(Mandatory = $false)]
        [int]$Column
    )

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            $escapedMessage = ConvertTo-GitHubActionsEscaped -Value $Message
            $ghaLevel = $Level.ToLower()

            $properties = @()
            if ($File) {
                $normalizedFile = $File.Replace('\', '/')
                $properties += "file=$(ConvertTo-GitHubActionsEscaped -Value $normalizedFile -ForProperty)"
            }
            if ($Line -gt 0) {
                $properties += "line=$Line"
            }
            if ($Column -gt 0) {
                $properties += "col=$Column"
            }

            $propertyString = if ($properties.Count -gt 0) { " $($properties -join ',')" } else { '' }
            Write-Host "::${ghaLevel}${propertyString}::${escapedMessage}"
        }
        'azdo' {
            $typeMap = @{
                'Warning' = 'warning'
                'Error'   = 'error'
                'Notice'  = 'info'
            }
            $adoType = $typeMap[$Level]
            $escapedMessage = ConvertTo-AzureDevOpsEscaped -Value $Message

            $properties = "type=$adoType"
            if ($File) {
                $normalizedFile = $File.Replace('\', '/')
                $escapedFile = ConvertTo-AzureDevOpsEscaped -Value $normalizedFile -ForProperty
                $properties += ";sourcepath=$escapedFile"
            }
            if ($Line -gt 0) {
                $properties += ";linenumber=$Line"
            }
            if ($Column -gt 0) {
                $properties += ";columnnumber=$Column"
            }

            Write-Host "##vso[task.logissue $properties]$escapedMessage"
        }
        default {
            $prefix = "[$Level]"
            $location = ''
            if ($File) {
                $location = " $File"
                if ($Line -gt 0) {
                    $location += ":$Line"
                    if ($Column -gt 0) {
                        $location += ":$Column"
                    }
                }
            }
            Write-Warning "${prefix}${location}: $Message"
        }
    }
}

<#
.SYNOPSIS
Writes CI annotations from a PSScriptAnalyzer summary object.

.DESCRIPTION
Iterates through the results and issues in a summary object, mapping each issue to a
CI annotation. Error severity maps to Error level, all other severities map to Warning.

.PARAMETER Summary
A summary object containing Results with Issues arrays, typically from PSScriptAnalyzer.

.OUTPUTS
System.Void
#>
function Write-CIAnnotations {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [object]$Summary
    )

    foreach ($result in $Summary.Results) {
        foreach ($issue in $result.Issues) {
            if ([string]::IsNullOrWhiteSpace($issue.Message)) {
                continue
            }

            $level = if ($issue.Severity -eq 'Error') { 'Error' } else { 'Warning' }

            $annotationParams = @{
                Message = $issue.Message
                Level   = $level
            }

            if ($issue.File) {
                $annotationParams['File'] = $issue.File
            }
            if ($issue.Line) {
                $annotationParams['Line'] = $issue.Line
            }
            if ($issue.Column) {
                $annotationParams['Column'] = $issue.Column
            }

            Write-CIAnnotation @annotationParams
        }
    }
}

<#
.SYNOPSIS
Sets the CI task result on the current platform.

.DESCRIPTION
Reports the task outcome using the appropriate mechanism for the detected CI platform.
GitHub Actions emits an error annotation only for failed tasks. Azure DevOps uses the
task.complete logging command. Local runs log via Write-Verbose.

.PARAMETER Result
The task result. Valid values are Succeeded, SucceededWithIssues, and Failed.

.OUTPUTS
System.Void
#>
function Set-CITaskResult {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('Succeeded', 'SucceededWithIssues', 'Failed')]
        [string]$Result
    )

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            if ($Result -eq 'Failed') {
                Write-Host '::error::Task failed'
            }
            else {
                Write-Verbose "Task result: $Result"
            }
        }
        'azdo' {
            Write-Host "##vso[task.complete result=$Result]"
        }
        default {
            Write-Verbose "Task result: $Result"
        }
    }
}

<#
.SYNOPSIS
Publishes a CI artifact on the current platform.

.DESCRIPTION
Publishes an artifact using the appropriate mechanism for the detected CI platform.
GitHub Actions sets output variables for the artifact path and name. Azure DevOps uses
the artifact.upload logging command. Local runs log via Write-Verbose. Validates that the
artifact path exists before publishing.

.PARAMETER Path
The path to the artifact file or directory.

.PARAMETER Name
The artifact name.

.PARAMETER ContainerFolder
Optional container folder name for Azure DevOps. Defaults to the artifact name.

.OUTPUTS
System.Void
#>
function Publish-CIArtifact {
    [CmdletBinding()]
    [OutputType([void])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $false)]
        [string]$ContainerFolder
    )

    if (-not (Test-Path -Path $Path)) {
        Write-Warning "Artifact path not found: $Path"
        return
    }

    $platform = Get-CIPlatform

    switch ($platform) {
        'github' {
            Set-CIOutput -Name "artifact-path-$Name" -Value $Path
            Set-CIOutput -Name "artifact-name-$Name" -Value $Name
            Write-Verbose "Artifact published: $Name at $Path"
        }
        'azdo' {
            $container = if ($ContainerFolder) { $ContainerFolder } else { $Name }
            $escapedContainer = ConvertTo-AzureDevOpsEscaped -Value $container -ForProperty
            $escapedName = ConvertTo-AzureDevOpsEscaped -Value $Name -ForProperty
            $escapedPath = ConvertTo-AzureDevOpsEscaped -Value $Path
            Write-Host "##vso[artifact.upload containerfolder=$escapedContainer;artifactname=$escapedName]$escapedPath"
        }
        default {
            Write-Verbose "Artifact: $Name at $Path"
        }
    }
}

Export-ModuleMember -Function @(
    'ConvertTo-GitHubActionsEscaped',
    'ConvertTo-AzureDevOpsEscaped',
    'Get-CIPlatform',
    'Test-CIEnvironment',
    'Test-PowerShellVersion',
    'Set-CIOutput',
    'Set-CIEnv',
    'Write-CIStepSummary',
    'Write-CIAnnotation',
    'Write-CIAnnotations',
    'Set-CITaskResult',
    'Publish-CIArtifact'
)
