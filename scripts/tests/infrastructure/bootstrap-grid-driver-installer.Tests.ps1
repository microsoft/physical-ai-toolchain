#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for infrastructure/setup/scripts/gpu-grid-driver-installer/
# bootstrap-grid-driver-installer.sh. The script hardcodes /scripts and /host,
# so these tests only run when the environment can safely create those paths.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
    $script:RootWritable = $false
    $script:BootstrapPathConflicts = $true

    if ($script:ToolsPresent) {
        & bash -lc 'test -w /'
        $script:RootWritable = $LASTEXITCODE -eq 0

        $script:BootstrapPathConflicts =
            (Test-Path '/scripts/install-grid-driver.sh') -or
            (Test-Path '/host/tmp/gpu-grid-driver-installer/install-grid-driver.sh')
    }

    $script:CanExerciseAbsolutePaths = $script:ToolsPresent -and $script:RootWritable -and (-not $script:BootstrapPathConflicts)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force

    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../infrastructure/setup/scripts/gpu-grid-driver-installer/bootstrap-grid-driver-installer.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/bootstrap-grid-driver-installer'

    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function New-TestWorkDir {
        $path = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
}

Describe 'bootstrap-grid-driver-installer.sh' -Tag 'Unit' -Skip:(-not $script:CanExerciseAbsolutePaths) {
    Context 'when /scripts and /host can be created safely' {
        It 'copies the installer onto the host, makes it executable, and calls nsenter' {
            $workDir = New-TestWorkDir
            $containerDir = '/scripts'
            $hostRoot = '/host'
            $hostScriptDir = '/host/tmp/gpu-grid-driver-installer'
            $containerScript = '/scripts/install-grid-driver.sh'
            $hostScript = '/host/tmp/gpu-grid-driver-installer/install-grid-driver.sh'
            $createdContainerDir = -not (Test-Path $containerDir)
            $createdHostRoot = -not (Test-Path $hostRoot)
            $installerBody = @'
#!/bin/bash
echo "placeholder installer"
'@

            try {
                New-Item -ItemType Directory -Path $containerDir -Force | Out-Null
                if ($createdHostRoot) {
                    New-Item -ItemType Directory -Path $hostRoot -Force | Out-Null
                }

                Set-Content -Path $containerScript -Value $installerBody -NoNewline

                $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -Stubs @{
                    'nsenter' = 'exit 0'
                } -WorkDir $workDir

                $result.ExitCode | Should -Be 0
                $result.StdOut | Should -Match 'Running GRID driver installer on host via nsenter'
                $result.Calls | Should -Be @(
                    'nsenter --target 1 --mount --uts --ipc --net --pid -- /bin/bash /tmp/gpu-grid-driver-installer/install-grid-driver.sh'
                )

                Test-Path $hostScript | Should -BeTrue
                (Get-Content -Path $hostScript -Raw) | Should -Be $installerBody

                & bash -lc "test -x '$hostScript'"
                $LASTEXITCODE | Should -Be 0
            }
            finally {
                Remove-Item -Force $containerScript -ErrorAction SilentlyContinue
                Remove-Item -Recurse -Force $hostScriptDir -ErrorAction SilentlyContinue

                if ($createdContainerDir) {
                    Remove-Item -Recurse -Force $containerDir -ErrorAction SilentlyContinue
                }

                if ($createdHostRoot) {
                    Remove-Item -Recurse -Force $hostRoot -ErrorAction SilentlyContinue
                }

                Remove-Item -Recurse -Force $workDir -ErrorAction SilentlyContinue
            }
        }
    }
}
