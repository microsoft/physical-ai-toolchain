#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for training/rl/scripts/setup_isaac_runtime.sh, sourced as RL training and Isaac Lab
# evaluation source it. uv and the Isaac interpreter are stubs, so the tests check the install commands and the
# requirements the locked export hands to them.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:SetupScript = (Resolve-Path (Join-Path $PSScriptRoot '../../../training/rl/scripts/setup_isaac_runtime.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/setup-isaac-runtime'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    # The export carries a container-provided torch stack that the install must leave to the image.
    $script:UvStub = @'
if [[ "$1" == "export" ]]; then
  printf '%s\n' "numpy==2.5.3 ; platform_machine == 'x86_64'" "torch==2.10.0" "nvidia-cudnn-cu12==9.10.2.21" "skrl==1.4.3"
  exit 0
fi
while [[ $# -gt 0 ]]; do
  [[ "$1" == "--requirement" ]] && cp "$2" "$PWD/requirements.seen"
  shift
done
exit 0
'@
    $script:PythonStub = @'
case "$*" in
  *sys.prefix*) echo "$PWD" ;;
  *getsitepackages*) echo "$FAKE_SITE_PACKAGES" ;;
esac
exit 0
'@

    function Invoke-SetupIsaacRuntime {
        param([hashtable]$ExtraEnv = @{})

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workDir -Force | Out-Null
        $wrapper = Join-Path $workDir 'source-runtime.sh'
        Set-Content -Path $wrapper -NoNewline -Value @'
#!/bin/bash
set -euo pipefail
source "$SETUP_ISAAC_RUNTIME"
'@
        $envVars = @{
            SETUP_ISAAC_RUNTIME          = $script:SetupScript
            ISAAC_PROJECT_DIR            = $workDir
            ISAAC_PYTHONPATH_ROOT        = $workDir
            ISAAC_RUNTIME_SITE_PACKAGES  = (Join-Path $workDir 'overlay')
            PYTHON                       = 'fake-python'
            FAKE_SITE_PACKAGES           = $workDir
            VIRTUAL_ENV                  = ''
        }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }
        Invoke-BashEntryScript -ScriptPath $wrapper -WorkDir $workDir -EnvVars $envVars `
            -Stubs @{ 'uv' = $script:UvStub; 'fake-python' = $script:PythonStub }
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
}

Describe 'setup_isaac_runtime.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'installs the locked export into <Target> without reading uv configuration from the working directory' -ForEach @(
        @{ Target = 'an active virtual environment'; Env = @{ VIRTUAL_ENV = '/opt/venv' }; Flag = '--requirement' }
        @{ Target = 'the interpreter site-packages'; Env = @{}; Flag = '--system' }
        @{ Target = 'a writable overlay'; Env = @{ FAKE_SITE_PACKAGES = '/proc/not-writable' }; Flag = '--target' }
    ) {
        $result = Invoke-SetupIsaacRuntime -ExtraEnv $Env

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        $installs = @($result.Calls | Where-Object { $_ -like 'uv pip install*' })
        $installs.Count | Should -Be 1
        # The Isaac Lab image starts in its own uv workspace, whose overrides would replace locked pins.
        $installs[0] | Should -Match '(^| )--no-config( |$)'
        $installs[0] | Should -Match ('(^| )' + [regex]::Escape($Flag) + '( |$)')
        @($result.Calls | Where-Object { $_ -like 'uv export*' })[0] | Should -Match '--frozen'

        $requirements = Get-Content -Path (Join-Path $result.WorkDir 'requirements.seen')
        $requirements | Should -Contain "numpy==2.5.3 ; platform_machine == 'x86_64'"
        $requirements | Should -Contain 'skrl==1.4.3'
        $requirements | Should -Not -Contain 'torch==2.10.0'
        $requirements | Should -Not -Contain 'nvidia-cudnn-cu12==9.10.2.21'
    }
}
