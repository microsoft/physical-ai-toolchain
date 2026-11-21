#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for gpu-offload/examples/so101-real-hardware/scripts/update_lerobot_version.sh.
# Each case copies the script and its common.sh into an isolated workspace with
# fixture pyproject.toml and .lerobot-version files, and stubs uv and git.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:SourceScriptsDir = (Resolve-Path (Join-Path $PSScriptRoot '../../../gpu-offload/examples/so101-real-hardware/scripts')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/update-lerobot-version'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    $script:Extras = 'core-scripts,feetech,training,evaluation,smolvla,pusht'

    function New-PyprojectFixture {
        param(
            [Parameter(Mandatory)][string]$Indent,
            [Parameter(Mandatory)][string]$Version,
            [switch]$OmitLerobot
        )

        $lines = @(
            '[project]'
            'name = "lerobot-so101-runtime"'
            'dependencies = ['
        )
        if (-not $OmitLerobot) {
            $lines += "$Indent`"lerobot[$script:Extras]==$Version`","
        }
        $lines += @(
            "$Indent`"Pillow==13.0.0`","
            ']'
        )
        return ($lines -join "`n") + "`n"
    }

    function New-So101Workspace {
        param(
            [Parameter(Mandatory)][string]$Name,
            [Parameter(Mandatory)][string]$Pyproject,
            [string]$PinnedVersion = '0.6.1'
        )

        $root = Join-Path $script:ArtifactsRoot "$Name-$([System.Guid]::NewGuid().ToString('N'))"
        $exampleDir = Join-Path $root 'so101'
        $scriptsDir = Join-Path $exampleDir 'scripts'
        New-Item -ItemType Directory -Path $scriptsDir -Force | Out-Null
        foreach ($file in 'update_lerobot_version.sh', 'common.sh') {
            Copy-Item -Path (Join-Path $script:SourceScriptsDir $file) -Destination $scriptsDir
        }
        $pyprojectPath = Join-Path $exampleDir 'pyproject.toml'
        $versionPath = Join-Path $exampleDir '.lerobot-version'
        Set-Content -Path $pyprojectPath -Value $Pyproject -NoNewline
        Set-Content -Path $versionPath -Value "LEROBOT_VERSION=$PinnedVersion`n" -NoNewline

        return [PSCustomObject]@{
            Root          = $root
            ExampleDir    = $exampleDir
            ScriptPath    = Join-Path $scriptsDir 'update_lerobot_version.sh'
            PyprojectPath = $pyprojectPath
            VersionPath   = $versionPath
        }
    }

    function Invoke-UpdateLerobotVersion {
        param(
            [Parameter(Mandatory)][PSCustomObject]$Workspace,
            [string[]]$ScriptArgs = @()
        )

        $stubs = @{
            'uv'  = @'
[[ "$(pwd -P)" == "$(cd "${EXPECTED_LEROBOT_DIR}" && pwd -P)" ]] || exit 97
exit 0
'@
            'git' = @'
if [[ " $* " == *" rev-parse --show-toplevel "* ]]; then
  printf '%s\n' "${TEST_REPO_ROOT}"
fi
exit 0
'@
        }

        return Invoke-BashEntryScript -ScriptPath $Workspace.ScriptPath `
            -WorkDir (Join-Path $Workspace.Root 'run') `
            -EnvVars @{
                EXPECTED_LEROBOT_DIR = $Workspace.ExampleDir
                TEST_REPO_ROOT       = $Workspace.Root
            } `
            -Stubs $stubs `
            -ScriptArgs $ScriptArgs
    }

    function Get-UvCall {
        param([AllowEmptyCollection()][string[]]$Calls = @())

        return @($Calls | Where-Object { $_ -like 'uv*' })
    }
}

AfterAll {
    if ($script:ArtifactsRoot) {
        Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
    }
}

Describe 'update_lerobot_version.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'four-space-indented manifest' {
        BeforeAll {
            $script:FourSpaceWorkspace = New-So101Workspace -Name 'four-space' -Pyproject (New-PyprojectFixture -Indent '    ' -Version '0.6.1')
            $script:FourSpaceResult = Invoke-UpdateLerobotVersion -Workspace $script:FourSpaceWorkspace -ScriptArgs @('0.7.0')
        }

        It 'exits successfully' {
            $script:FourSpaceResult.ExitCode | Should -Be 0 -Because $script:FourSpaceResult.StdErr
        }

        It 'rewrites only the lerobot version and keeps indentation and extras' {
            Get-Content -Path $script:FourSpaceWorkspace.PyprojectPath -Raw |
                Should -BeExactly (New-PyprojectFixture -Indent '    ' -Version '0.7.0')
        }

        It 'rewrites .lerobot-version' {
            Get-Content -Path $script:FourSpaceWorkspace.VersionPath -Raw | Should -BeExactly "LEROBOT_VERSION=0.7.0`n"
        }

        It 'runs uv lock exactly once from the example directory' {
            Get-UvCall -Calls $script:FourSpaceResult.Calls | Should -Be @('uv lock')
        }
    }

    Context 'two-space-indented manifest' {
        It 'rewrites the lerobot version and keeps the two-space indent' {
            $workspace = New-So101Workspace -Name 'two-space' -Pyproject (New-PyprojectFixture -Indent '  ' -Version '0.6.1')

            $result = Invoke-UpdateLerobotVersion -Workspace $workspace -ScriptArgs @('0.7.0rc1')

            $result.ExitCode | Should -Be 0 -Because $result.StdErr
            Get-Content -Path $workspace.PyprojectPath -Raw |
                Should -BeExactly (New-PyprojectFixture -Indent '  ' -Version '0.7.0rc1')
            Get-UvCall -Calls $result.Calls | Should -Be @('uv lock')
        }
    }

    Context 'version already pinned' {
        It 'succeeds, leaves the manifest unchanged, and re-locks' {
            $pyproject = New-PyprojectFixture -Indent '    ' -Version '0.6.1'
            $workspace = New-So101Workspace -Name 'same-version' -Pyproject $pyproject

            $result = Invoke-UpdateLerobotVersion -Workspace $workspace -ScriptArgs @('0.6.1')

            $result.ExitCode | Should -Be 0 -Because $result.StdErr
            Get-Content -Path $workspace.PyprojectPath -Raw | Should -BeExactly $pyproject
            Get-Content -Path $workspace.VersionPath -Raw | Should -BeExactly "LEROBOT_VERSION=0.6.1`n"
            Get-UvCall -Calls $result.Calls | Should -Be @('uv lock')
        }
    }

    Context 'manifest without a lerobot pin' {
        It 'fails without re-locking' {
            $pyproject = New-PyprojectFixture -Indent '    ' -Version '0.6.1' -OmitLerobot
            $workspace = New-So101Workspace -Name 'no-lerobot' -Pyproject $pyproject

            $result = Invoke-UpdateLerobotVersion -Workspace $workspace -ScriptArgs @('0.7.0')

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'no lerobot dependency line matched'
            Get-Content -Path $workspace.PyprojectPath -Raw | Should -BeExactly $pyproject
            Get-Content -Path $workspace.VersionPath -Raw | Should -BeExactly "LEROBOT_VERSION=0.6.1`n"
            Get-UvCall -Calls $result.Calls | Should -BeNullOrEmpty
        }
    }

    Context 'malformed version argument' {
        It 'rejects <_> before writing any file' -ForEach @('0.7.0/evil', 'latest', '0.7.0&') {
            $pyproject = New-PyprojectFixture -Indent '    ' -Version '0.6.1'
            $workspace = New-So101Workspace -Name 'bad-version' -Pyproject $pyproject

            $result = Invoke-UpdateLerobotVersion -Workspace $workspace -ScriptArgs @($_)

            $result.ExitCode | Should -Not -Be 0
            $result.StdErr | Should -Match 'invalid LeRobot version'
            Get-Content -Path $workspace.PyprojectPath -Raw | Should -BeExactly $pyproject
            Get-Content -Path $workspace.VersionPath -Raw | Should -BeExactly "LEROBOT_VERSION=0.6.1`n"
            Get-UvCall -Calls $result.Calls | Should -BeNullOrEmpty
        }
    }

    Context 'usage errors' {
        It 'exits 2 when no version is given' {
            $workspace = New-So101Workspace -Name 'usage' -Pyproject (New-PyprojectFixture -Indent '    ' -Version '0.6.1')

            $result = Invoke-UpdateLerobotVersion -Workspace $workspace

            $result.ExitCode | Should -Be 2
            $result.StdErr | Should -Match 'Usage: update_lerobot_version.sh VERSION'
        }
    }
}
