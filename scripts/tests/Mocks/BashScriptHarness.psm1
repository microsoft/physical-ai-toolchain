# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# BashScriptHarness.psm1
#
# Purpose: Reusable Pester helpers for behaviorally testing standalone bash entry scripts
#          (OSMO/K8s workflow bootstrap scripts) by stubbing external commands (apt-get, uv,
#          wget, nvidia-smi, ...) via a fake PATH directory, without running real installs,
#          downloads, or GPU/ML workloads.
# Author: Robotics-AI Team

function ConvertTo-BashSingleQuoted {
    <#
    .SYNOPSIS
    Single-quotes a string for safe literal interpolation into a generated bash script.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string]$Value
    )

    return "'" + ($Value -replace "'", "'\''") + "'"
}

function New-BashCommandStub {
    <#
    .SYNOPSIS
    Writes an executable stub for one external command into a stub directory.
    .DESCRIPTION
    Every invocation of the stub appends a line "<name> <args...>" to the shared call log
    (space-joined, one call per line) before running the caller-supplied body, so tests can
    assert both that a command was invoked and with what arguments.
    .PARAMETER Body
    Bash source executed after the call is logged. Defaults to a no-op success (`exit 0`).
    Use this to simulate output (`echo ...`), failure (`exit 1`), or conditional behavior.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StubDir,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$CallLogPath,
        [string]$Body = 'exit 0'
    )

    $stubPath = Join-Path $StubDir $Name
    $lines = @(
        '#!/bin/bash'
        "printf '%s' $(ConvertTo-BashSingleQuoted $Name) >> $(ConvertTo-BashSingleQuoted $CallLogPath)"
        "for a in `"`$@`"; do printf ' %s' `"`$a`" >> $(ConvertTo-BashSingleQuoted $CallLogPath); done"
        "printf '\n' >> $(ConvertTo-BashSingleQuoted $CallLogPath)"
        $Body
    )
    Set-Content -Path $stubPath -Value ($lines -join "`n") -NoNewline
    if (Get-Command chmod -ErrorAction SilentlyContinue) {
        & chmod +x $stubPath
    }
}

function Invoke-BashEntryScript {
    <#
    .SYNOPSIS
    Runs a bash entry script with stubbed external commands and captured stdout/stderr/exit code.
    .PARAMETER ScriptPath
    Absolute path of the real script under test (e.g. resolved from $PSScriptRoot).
    .PARAMETER EnvVars
    Hashtable of environment variables to export before running the script. Values are
    single-quote-escaped, not shell-interpreted, so callers never need their own escaping.
    .PARAMETER Stubs
    Hashtable mapping external command name (e.g. 'apt-get', 'uv', 'wget') to a stub body
    (bash source string; defaults to 'exit 0' success when the value is $null or empty).
    Every command the script under test invokes MUST be listed here, or bash will fall
    through to any same-named binary already on PATH (or fail with "command not found").
    .PARAMETER WorkDir
    Directory the script is run from (its $PWD). A fresh temp directory is used if omitted.
    .OUTPUTS
    PSCustomObject with ExitCode (int), StdOut (string), StdErr (string), Calls (string[],
    one call-log line per stubbed-command invocation, in invocation order), and WorkDir.
    #>
    [CmdletBinding()]
    [OutputType([PSCustomObject])]
    param(
        [Parameter(Mandatory)][string]$ScriptPath,
        [hashtable]$EnvVars = @{},
        [hashtable]$Stubs = @{},
        [string]$WorkDir
    )

    if (-not $WorkDir) {
        $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
    }
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

    $stubDir = Join-Path $WorkDir '.stubs'
    New-Item -ItemType Directory -Path $stubDir -Force | Out-Null
    $callLog = Join-Path $stubDir '.calls.log'
    New-Item -ItemType File -Path $callLog -Force | Out-Null

    foreach ($name in $Stubs.Keys) {
        $body = $Stubs[$name]
        if ([string]::IsNullOrEmpty($body)) { $body = 'exit 0' }
        New-BashCommandStub -StubDir $stubDir -Name $name -CallLogPath $callLog -Body $body
    }

    $wrapperPath = Join-Path $WorkDir '.run-entry.sh'
    $wrapperLines = [System.Collections.Generic.List[string]]::new()
    $wrapperLines.Add('#!/bin/bash')
    foreach ($key in $EnvVars.Keys) {
        $wrapperLines.Add("export $key=$(ConvertTo-BashSingleQuoted ([string]$EnvVars[$key]))")
    }
    $wrapperLines.Add("export PATH=$(ConvertTo-BashSingleQuoted $stubDir):`$PATH")
    $wrapperLines.Add("cd $(ConvertTo-BashSingleQuoted $WorkDir) || exit 1")
    $wrapperLines.Add("bash $(ConvertTo-BashSingleQuoted $ScriptPath)")
    Set-Content -Path $wrapperPath -Value ($wrapperLines -join "`n") -NoNewline
    if (Get-Command chmod -ErrorAction SilentlyContinue) {
        & chmod +x $wrapperPath
    }

    $stdoutPath = Join-Path $WorkDir '.stdout.log'
    $stderrPath = Join-Path $WorkDir '.stderr.log'
    & bash $wrapperPath 1> $stdoutPath 2> $stderrPath
    $exitCode = $LASTEXITCODE

    $calls = @()
    if (Test-Path $callLog) {
        $calls = @(Get-Content -Path $callLog | Where-Object { $_ -ne '' })
    }

    [PSCustomObject]@{
        ExitCode = $exitCode
        StdOut   = if (Test-Path $stdoutPath) { Get-Content -Path $stdoutPath -Raw } else { '' }
        StdErr   = if (Test-Path $stderrPath) { Get-Content -Path $stderrPath -Raw } else { '' }
        Calls    = $calls
        WorkDir  = $WorkDir
    }
}

Export-ModuleMember -Function ConvertTo-BashSingleQuoted, New-BashCommandStub, Invoke-BashEntryScript
