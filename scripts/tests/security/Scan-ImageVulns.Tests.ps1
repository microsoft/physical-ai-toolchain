#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Scan-ImageVulns.Tests.ps1
#
# Pester tests for scripts/security/scan-image-vulns.sh

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:RepoRoot   = (Resolve-Path "$PSScriptRoot/../../..").Path
    $script:ScriptPath = Join-Path $script:RepoRoot 'scripts/security/scan-image-vulns.sh'
    $script:SavedPath  = $env:PATH
    $script:NoColor    = $env:NO_COLOR
    $env:NO_COLOR      = '1'

    function New-StubDir {
        $dir = Join-Path ([IO.Path]::GetTempPath()) ("scan-stub-" + [Guid]::NewGuid().ToString('N'))
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

    function Invoke-Scan {
        param(
            [string[]] $ScriptArgs,
            [string]   $StubDir,
            [string]   $WorkingDir
        )
        $env:PATH = "$StubDir" + [IO.Path]::PathSeparator + $script:SavedPath
        Push-Location $WorkingDir
        try {
            $stdout   = & bash $script:ScriptPath @ScriptArgs 2>&1
            $exitCode = $LASTEXITCODE
        } finally {
            Pop-Location
            $env:PATH = $script:SavedPath
        }
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

Describe 'scan-image-vulns.sh' -Tag 'Unit' {

    Context '--help' {
        It 'Exits 0 and prints usage' {
            $stub = New-StubDir
            try {
                $result = Invoke-Scan -ScriptArgs @('--help') -StubDir $stub -WorkingDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Usage: scan-image-vulns\.sh'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Argument validation' {
        It 'Exits 2 when --image is missing' {
            $stub = New-StubDir
            try {
                $result = Invoke-Scan -ScriptArgs @() -StubDir $stub -WorkingDir $stub
                $result.ExitCode | Should -Be 2
                $result.Output   | Should -Match '--image is required'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }

        It 'Exits 1 when --format is not table or sarif' {
            $stub = New-StubDir
            try {
                $result = Invoke-Scan `
                    -ScriptArgs @('--image','foo','--format','json') `
                    -StubDir   $stub `
                    -WorkingDir $stub
                $result.ExitCode | Should -Be 1
                $result.Output   | Should -Match '--format must be table or sarif'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context '--config-preview' {
        It 'Prints resolved config and exits 0 without invoking trivy' {
            $stub = New-StubDir
            try {
                $result = Invoke-Scan `
                    -ScriptArgs @('--config-preview','--image','ghcr.io/o/r:tag') `
                    -StubDir   $stub `
                    -WorkingDir $stub
                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'Image:\s+ghcr.io/o/r:tag'
                $result.Output   | Should -Match 'Format:\s+table'
                $result.Output   | Should -Match 'Severity:\s+HIGH,CRITICAL'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Trivy scan without VEX' {
        It 'Skips vexctl when --vex-dir is empty and emits unfiltered scan' {
            $stub  = New-StubDir
            $vex   = Join-Path $stub 'empty-vex'
            New-Item -ItemType Directory -Path $vex | Out-Null
            $trivyArgs = Join-Path $stub 'trivy.args'
            try {
                # trivy writes a marker into the --output file and records args.
                New-StubBinary -Dir $stub -Name 'trivy' -Body @"
printf '%s\n' "`$@" > '$trivyArgs'
output=''
while [[ `$# -gt 0 ]]; do
  case "`$1" in
    --output) output="`$2"; shift 2 ;;
    *) shift ;;
  esac
done
echo 'TRIVY_REPORT' > "`$output"
exit 0
"@ | Out-Null
                # Stub vexctl that fails if invoked.
                New-StubBinary -Dir $stub -Name 'vexctl' -Body 'echo "vexctl should not run"; exit 99' | Out-Null

                $result = Invoke-Scan `
                    -ScriptArgs @('--image','ghcr.io/o/r:tag','--vex-dir',$vex) `
                    -StubDir   $stub `
                    -WorkingDir $stub

                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'TRIVY_REPORT'
                $result.Output   | Should -Match 'No VEX statements found'
                $result.Output   | Should -Match 'VEX applied:\s+false'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Trivy scan with VEX filter' {
        It 'Invokes vexctl filter with --vex-dir, --input and --output' {
            $stub  = New-StubDir
            $vex   = Join-Path $stub 'vex'
            New-Item -ItemType Directory -Path $vex | Out-Null
            Set-Content -Path (Join-Path $vex 'test.openvex.json') -Value '{}'

            $trivyArgs  = Join-Path $stub 'trivy.args'
            $vexArgs    = Join-Path $stub 'vexctl.args'
            try {
                New-StubBinary -Dir $stub -Name 'trivy' -Body @"
printf '%s\n' "`$@" > '$trivyArgs'
output=''
while [[ `$# -gt 0 ]]; do
  case "`$1" in
    --output) output="`$2"; shift 2 ;;
    *) shift ;;
  esac
done
echo 'TRIVY_REPORT' > "`$output"
exit 0
"@ | Out-Null
                New-StubBinary -Dir $stub -Name 'vexctl' -Body @"
printf '%s\n' "`$@" > '$vexArgs'
output=''
while [[ `$# -gt 0 ]]; do
  case "`$1" in
    --output) output="`$2"; shift 2 ;;
    *) shift ;;
  esac
done
echo 'VEX_FILTERED' > "`$output"
exit 0
"@ | Out-Null

                $result = Invoke-Scan `
                    -ScriptArgs @('--image','ghcr.io/o/r:tag','--vex-dir',$vex) `
                    -StubDir   $stub `
                    -WorkingDir $stub

                $result.ExitCode | Should -Be 0
                $result.Output   | Should -Match 'VEX_FILTERED'
                $result.Output   | Should -Match 'VEX applied:\s+true'

                Test-Path $vexArgs | Should -BeTrue
                $captured = Get-Content $vexArgs -Raw
                $captured | Should -Match 'filter'
                $captured | Should -Match '--vex-dir'
                $captured | Should -Match ([regex]::Escape($vex))
                $captured | Should -Match '--input'
                $captured | Should -Match '--output'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }

    Context 'Severity and format propagation' {
        It 'Passes --severity and --format sarif to trivy' {
            $stub      = New-StubDir
            $vex       = Join-Path $stub 'empty-vex'
            New-Item -ItemType Directory -Path $vex | Out-Null
            $trivyArgs = Join-Path $stub 'trivy.args'
            try {
                New-StubBinary -Dir $stub -Name 'trivy' -Body @"
printf '%s\n' "`$@" > '$trivyArgs'
output=''
while [[ `$# -gt 0 ]]; do
  case "`$1" in
    --output) output="`$2"; shift 2 ;;
    *) shift ;;
  esac
done
echo '{}' > "`$output"
exit 0
"@ | Out-Null

                $result = Invoke-Scan `
                    -ScriptArgs @(
                        '--image','ghcr.io/o/r:tag',
                        '--vex-dir',$vex,
                        '--format','sarif',
                        '--severity','CRITICAL'
                    ) `
                    -StubDir   $stub `
                    -WorkingDir $stub

                $result.ExitCode | Should -Be 0
                $captured = Get-Content $trivyArgs -Raw
                $captured | Should -Match '--severity'
                $captured | Should -Match 'CRITICAL'
                $captured | Should -Match '--format'
                $captured | Should -Match 'sarif'
            } finally {
                Remove-Item -Recurse -Force $stub
            }
        }
    }
}
