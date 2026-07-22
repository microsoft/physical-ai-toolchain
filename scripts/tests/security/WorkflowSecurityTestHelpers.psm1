#Requires -Version 7.0
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Shared Pester test helpers for workflow security linters.
#>

function New-WorkflowFixture {
    <#
    .SYNOPSIS
        Creates a workflow YAML fixture directory.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $fixtureDir = Join-Path $Root $Name
    New-Item -ItemType Directory -Path $fixtureDir -Force | Out-Null

    $workflowPath = Join-Path $fixtureDir 'workflow.yml'
    Set-Content -Path $workflowPath -Value $Content -Encoding utf8

    return $fixtureDir
}

function Get-JsonReport {
    <#
    .SYNOPSIS
        Reads and parses a JSON report file.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    return Get-Content -Path $Path -Raw | ConvertFrom-Json
}

function New-RunInjectionWorkflowContent {
    <#
    .SYNOPSIS
        Returns canonical pull_request_target single-run-step workflow YAML containing a template-injection expression.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $false)]
        [string]$Expr = 'github.event.pull_request.title'
    )

    return @"
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "`${{ $Expr }}"
"@
}

function Invoke-SecurityLinterScript {
    <#
    .SYNOPSIS
        Runs a linter script in a child pwsh process to exercise its dot-source guard / CLI entry point, and returns the process exit code.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $false)]
        [string[]]$ArgumentList = @()
    )

    $pwshPath = (Get-Process -Id $PID).Path
    & $pwshPath -NoProfile -File $ScriptPath @ArgumentList *> $null
    return $LASTEXITCODE
}

Export-ModuleMember -Function @(
    'New-WorkflowFixture',
    'Get-JsonReport',
    'New-RunInjectionWorkflowContent',
    'Invoke-SecurityLinterScript'
)
