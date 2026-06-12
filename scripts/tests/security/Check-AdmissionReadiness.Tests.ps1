#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Check-AdmissionReadiness.Tests.ps1
#
# Pester tests for scripts/security/check-admission-readiness.sh

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:RepoRoot   = (Resolve-Path "$PSScriptRoot/../../..").Path
    $script:ScriptPath = Join-Path $script:RepoRoot 'scripts/security/check-admission-readiness.sh'
    $script:SavedPath  = $env:PATH
    $script:NoColor    = $env:NO_COLOR
    $env:NO_COLOR      = '1'

    function New-StubDir {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("admission-stub-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $dir | Out-Null
        return $dir
    }

    function New-StubBinary {
        param(
            [Parameter(Mandatory)] [string] $Dir,
            [Parameter(Mandatory)] [string] $Name,
            [Parameter(Mandatory)] [string] $Body
        )
        $path = Join-Path $Dir $Name
        $script = "#!/usr/bin/env bash`nset -e`n$Body`n"
        Set-Content -Path $path -Value $script -NoNewline
        chmod +x $path
        return $path
    }

    function Invoke-Readiness {
        param(
            [string[]] $ScriptArgs,
            [string]   $StubDir
        )
        $env:PATH = "$StubDir" + [IO.Path]::PathSeparator + $script:SavedPath
        $stdout   = & bash $script:ScriptPath @ScriptArgs 2>&1
        $exitCode = $LASTEXITCODE
        $env:PATH = $script:SavedPath
        return [pscustomobject]@{
            ExitCode = $exitCode
            Output   = ($stdout | Out-String)
        }
    }

    # Reusable kubectl stub generator. Accepts a hashtable of (matchPattern -> body)
    # and a fallthrough exit code. Each invocation matches argv against patterns
    # in order; first match wins.
    function New-KubectlStub {
        param(
            [Parameter(Mandatory)] [string] $Dir,
            [Parameter(Mandatory)] [hashtable] $Cases,
            [int] $DefaultExit = 1
        )
        $body = "args=`"`$*`"`n"
        foreach ($key in $Cases.Keys) {
            $escapedKey = $key -replace "'", "'\''"
            $body += "if [[ `"`$args`" == *'$escapedKey'* ]]; then`n"
            $body += "  $($Cases[$key])`n"
            $body += "fi`n"
        }
        $body += "exit $DefaultExit`n"
        New-StubBinary -Dir $Dir -Name 'kubectl' -Body $body | Out-Null
    }
}

AfterAll {
    $env:PATH     = $script:SavedPath
    $env:NO_COLOR = $script:NoColor
}

Describe 'check-admission-readiness.sh' -Tag 'Unit' {

    Context '--help' {
        It 'Exits 0 and prints usage' {
            $stub = New-StubDir
            try {
                $result = Invoke-Readiness -ScriptArgs @('--help') -StubDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Usage: check-admission-readiness\.sh'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context '--config-preview' {
        It 'Prints resolved config and exits 0 without contacting kubectl' {
            $stub = New-StubDir
            try {
                $result = Invoke-Readiness -ScriptArgs @('--config-preview') -StubDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Mode:\s+sigstore'
                $result.Output   | Should -Match 'Policy name:\s+verify-images-sigstore'
                $result.Output   | Should -Match 'Trust-root NS:\s+kyverno'
                $result.Output   | Should -Match 'Trust-root CM:\s+sigstore-trusted-root'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Switches policy name to verify-images-notation for --mode notation' {
            $stub = New-StubDir
            try {
                $result = Invoke-Readiness -ScriptArgs @('--mode','notation','--config-preview') -StubDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Policy name:\s+verify-images-notation'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Argument validation' {
        It 'Exits 1 when --mode is invalid' {
            $stub = New-StubDir
            try {
                $result = Invoke-Readiness -ScriptArgs @('--mode','bogus','--config-preview') -StubDir $stub
                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match '--mode must be sigstore or notation'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Cluster probes' {
        It 'Fatals when Kyverno CRD is missing' {
            $stub = New-StubDir
            try {
                # Every kubectl call exits non-zero.
                New-StubBinary -Dir $stub -Name 'kubectl' -Body 'exit 1' | Out-Null

                $result = Invoke-Readiness -ScriptArgs @() -StubDir $stub
                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match 'Kyverno CRD clusterpolicies\.kyverno\.io not found'
                $result.Output   | Should -Match 'Admission control is not ready'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Reports ready when CRD, ClusterPolicy and trusted-root are present and fresh' {
            $stub = New-StubDir
            try {
                $now = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
                # kubectl branches: get crd → 0, get clusterpolicy → 0,
                # get configmap (no jsonpath) → 0, get configmap with jsonpath → emit timestamp,
                # create --dry-run=server → 1 (rejected, expected for enforce mode).
                $body = @"
case "`$*" in
  *'get crd clusterpolicies.kyverno.io'*) exit 0 ;;
  *'get clusterpolicy'*) exit 0 ;;
  *'jsonpath={.metadata.creationTimestamp}'*) printf '%s' '$now'; exit 0 ;;
  *'get configmap'*) exit 0 ;;
  *'create --dry-run=server'*) exit 1 ;;
esac
exit 1
"@
                New-StubBinary -Dir $stub -Name 'kubectl' -Body $body | Out-Null

                $result = Invoke-Readiness -ScriptArgs @() -StubDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Kyverno present:\s+true'
                $result.Output   | Should -Match 'Policy loaded:\s+true'
                $result.Output   | Should -Match 'Trust-root fresh:\s+true'
                $result.Output   | Should -Match 'Admission control is ready'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Fatals when trusted-root ConfigMap is too old' {
            $stub = New-StubDir
            try {
                # 200h-old timestamp exceeds default 24h threshold.
                $stale = [DateTime]::UtcNow.AddHours(-200).ToString('yyyy-MM-ddTHH:mm:ssZ')
                $body = @"
case "`$*" in
  *'get crd clusterpolicies.kyverno.io'*) exit 0 ;;
  *'get clusterpolicy'*) exit 0 ;;
  *'jsonpath={.metadata.creationTimestamp}'*) printf '%s' '$stale'; exit 0 ;;
  *'get configmap'*) exit 0 ;;
  *'create --dry-run=server'*) exit 1 ;;
esac
exit 1
"@
                New-StubBinary -Dir $stub -Name 'kubectl' -Body $body | Out-Null

                $result = Invoke-Readiness -ScriptArgs @() -StubDir $stub
                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match 'older than 24h'
                $result.Output   | Should -Match 'Admission control is not ready'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }
}
