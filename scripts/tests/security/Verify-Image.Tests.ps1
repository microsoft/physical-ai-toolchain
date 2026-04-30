#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Verify-Image.Tests.ps1
#
# Pester tests for scripts/security/verify-image.sh

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:RepoRoot   = (Resolve-Path "$PSScriptRoot/../../..").Path
    $script:ScriptPath = Join-Path $script:RepoRoot 'scripts/security/verify-image.sh'
    $script:SavedPath  = $env:PATH
    $script:NoColor    = $env:NO_COLOR
    $env:NO_COLOR      = '1'

    function New-StubDir {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("verify-image-stub-" + [Guid]::NewGuid().ToString('N'))
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

    function Invoke-VerifyImage {
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
}

AfterAll {
    $env:PATH     = $script:SavedPath
    $env:NO_COLOR = $script:NoColor
}

Describe 'verify-image.sh' -Tag 'Unit' {

    Context '--help' {
        It 'Exits 0 and prints usage' {
            $stub = New-StubDir
            try {
                $result = Invoke-VerifyImage -ScriptArgs @('--help') -StubDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Usage: verify-image\.sh'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Argument validation' {
        It 'Exits 2 when --image is missing' {
            $stub = New-StubDir
            try {
                $result = Invoke-VerifyImage -ScriptArgs @() -StubDir $stub
                $result.ExitCode | Should -Be 2
                $result.Output   | Should -Match '--image is required'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Exits 1 when --mode value is invalid' {
            $stub = New-StubDir
            try {
                $result = Invoke-VerifyImage -ScriptArgs @('--image','foo','--mode','bogus') -StubDir $stub
                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match '--mode must be one of'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context '--config-preview' {
        It 'Prints resolved config and exits 0 without invoking cosign' {
            $stub = New-StubDir
            try {
                $result = Invoke-VerifyImage `
                    -ScriptArgs @('--config-preview','--image','ghcr.io/o/r:tag') `
                    -StubDir   $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Image:\s+ghcr.io/o/r:tag'
                $result.Output   | Should -Match 'Mode:\s+auto'
                $result.Output   | Should -Match 'Identity regexp:'
                $result.Output   | Should -Match 'OIDC issuer:'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Sigstore verification' {
        It 'Invokes cosign verify with pinned identity regexp and OIDC issuer' {
            $stub    = New-StubDir
            $argFile = Join-Path $stub 'cosign.args'
            try {
                # Stub cosign captures all args to a file and exits 0.
                New-StubBinary -Dir $stub -Name 'cosign' -Body @"
printf '%s\n' "`$@" > '$argFile'
exit 0
"@ | Out-Null

                $result = Invoke-VerifyImage `
                    -ScriptArgs @('--mode','sigstore','--image','ghcr.io/o/r@sha256:abc','--accept-public-rekor') `
                    -StubDir   $stub

                $result.ExitCode | Should -Be 0
                Test-Path $argFile | Should -BeTrue
                $captured = Get-Content $argFile -Raw
                $captured | Should -Match 'verify'
                $captured | Should -Match '--certificate-identity-regexp=\^https://github\\\.com/microsoft/physical-ai-toolchain/'
                $captured | Should -Match '--certificate-oidc-issuer=https://token\.actions\.githubusercontent\.com'
                $captured | Should -Match 'ghcr\.io/o/r@sha256:abc'
                $result.Output | Should -Match 'Result:\s+verified'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Adds --offline and --trusted-root when --offline is set' {
            $stub      = New-StubDir
            $argFile   = Join-Path $stub 'cosign.args'
            $rootFile  = Join-Path $stub 'trusted-root.json'
            Set-Content -Path $rootFile -Value '{}'
            try {
                New-StubBinary -Dir $stub -Name 'cosign' -Body @"
printf '%s\n' "`$@" > '$argFile'
exit 0
"@ | Out-Null

                $result = Invoke-VerifyImage `
                    -ScriptArgs @(
                        '--mode','sigstore',
                        '--image','ghcr.io/o/r:tag',
                        '--offline',
                        '--trusted-root',$rootFile
                    ) `
                    -StubDir $stub

                $result.ExitCode | Should -Be 0
                $captured = Get-Content $argFile -Raw
                $captured | Should -Match '--offline'
                $captured | Should -Match ([regex]::Escape("--trusted-root=$rootFile"))
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Fails fast when --offline trusted-root path does not exist' {
            $stub = New-StubDir
            try {
                New-StubBinary -Dir $stub -Name 'cosign' -Body 'exit 0' | Out-Null

                $result = Invoke-VerifyImage `
                    -ScriptArgs @(
                        '--mode','sigstore',
                        '--image','ghcr.io/o/r:tag',
                        '--offline',
                        '--trusted-root','/nonexistent/path/root.json'
                    ) `
                    -StubDir $stub

                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match 'trusted-root file not found'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Notation verification' {
        It 'Invokes notation verify with --policy-file when supplied' {
            $stub    = New-StubDir
            $argFile = Join-Path $stub 'notation.args'
            $policy  = Join-Path $stub 'trustpolicy.json'
            Set-Content -Path $policy -Value '{}'
            try {
                New-StubBinary -Dir $stub -Name 'notation' -Body @"
printf '%s\n' "`$@" > '$argFile'
exit 0
"@ | Out-Null

                $result = Invoke-VerifyImage `
                    -ScriptArgs @(
                        '--mode','notation',
                        '--image','ghcr.io/o/r:tag',
                        '--policy-file',$policy
                    ) `
                    -StubDir $stub

                $result.ExitCode | Should -Be 0
                $captured = Get-Content $argFile -Raw
                $captured | Should -Match 'verify'
                $captured | Should -Match '--policy-file'
                $captured | Should -Match ([regex]::Escape($policy))
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Auto-detect mode' {
        It 'Resolves to sigstore when cosign tree succeeds' {
            $stub = New-StubDir
            try {
                # cosign tree exits 0 → auto resolves to sigstore.
                New-StubBinary -Dir $stub -Name 'cosign' -Body 'exit 0' | Out-Null

                $result = Invoke-VerifyImage `
                    -ScriptArgs @('--mode','auto','--image','ghcr.io/o/r:tag','--accept-public-rekor') `
                    -StubDir $stub

                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Auto-detected signature mode: sigstore'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }
}
