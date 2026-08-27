#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for infrastructure/setup/scripts/gpu-grid-driver-installer/
# install-grid-driver.sh. These tests stub every non-trivial external command so
# they never unload modules, mount filesystems, install packages, or touch live
# GPU state on the test host.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force

    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../infrastructure/setup/scripts/gpu-grid-driver-installer/install-grid-driver.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/install-grid-driver'
    $script:DriverFile = '/tmp/NVIDIA-Linux-x86_64-580.105.08-grid-azure.run'
    $script:DriverUrl = 'https://download.microsoft.com/download/85beffdc-8361-4df4-a823-dcb1b230a7aa/NVIDIA-Linux-x86_64-580.105.08-grid-azure.run'
    $script:KernelRelease = (& bash -lc 'uname -r').Trim()

    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function New-TestWorkDir {
        $path = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        return $path
    }

    function Remove-TestArtifacts {
        param([string]$WorkDir)

        if ($WorkDir) {
            Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
        }

        Remove-Item -Force $script:DriverFile -ErrorAction SilentlyContinue
    }

    function New-NvidiaSmiSecondCallSuccessStub {
        return @'
count_file="$PWD/.nvidia-smi-count"
count=0
if [ -f "$count_file" ]; then
  count=$(cat "$count_file")
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
if [ "$count" -eq 1 ]; then
  echo "driver not ready" >&2
  exit 1
fi
echo "GRID ready"
exit 0
'@
    }

    function New-WgetInstallerStub {
        param(
            [Parameter(Mandatory)]
            [ValidateSet('ImmediateSuccess', 'RetryWithOpenModules')]
            [string]$Mode
        )

        if ($Mode -eq 'ImmediateSuccess') {
            return @'
cat <<'EOF' > "$3"
#!/bin/bash
exit 0
EOF
chmod +x "$3"
exit 0
'@
        }

        return @'
cat <<'EOF' > "$3"
#!/bin/bash
case " $* " in
  *" -M open "*) exit 0 ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$3"
exit 0
'@
    }

    function Get-InstallGridDriverStubs {
        param(
            [Parameter(Mandatory)][string]$NvidiaSmiBody,
            [Parameter()][string]$WgetBody = 'exit 0',
            [Parameter()][string]$MountpointBody = 'exit 1'
        )

        @{
            'nvidia-smi'          = $NvidiaSmiBody
            'rmmod'               = 'exit 1'
            'apt-get'             = 'exit 0'
            'wget'                = $WgetBody
            # The downloaded installer content is synthetic, so checksum verification
            # is simulated here. Revalidating the pinned upstream hash is a live check.
            'sha256sum'           = 'exit 0'
            'modprobe'            = 'exit 0'
            'nvidia-persistenced' = 'exit 0'
            'mountpoint'          = $MountpointBody
            'mount'               = 'exit 0'
            # Stub /run writes so tests never touch real host paths outside the sandbox.
            'mkdir'               = 'exit 0'
            'touch'               = 'exit 0'
        }
    }
}

AfterAll {
    Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
}

Describe 'install-grid-driver.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    Context 'when the GRID driver is already functional' {
        It 'short-circuits and only performs validation marker and mount setup' {
            $workDir = New-TestWorkDir

            try {
                $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -Stubs (Get-InstallGridDriverStubs -NvidiaSmiBody @'
echo "GRID ready"
exit 0
'@) -WorkDir $workDir

                $result.ExitCode | Should -Be 0
                $result.StdOut | Should -Match 'NVIDIA GRID driver already functional'
                $result.Calls | Should -Be @(
                    'nvidia-smi'
                    'nvidia-smi'
                    'mkdir -p /run/nvidia/validations'
                    'touch /run/nvidia/validations/.driver-ctr-ready'
                    'mountpoint -q /run/nvidia/driver'
                    'mount --bind / /run/nvidia/driver'
                )
                ($result.Calls | Where-Object { $_ -match '^(rmmod|apt-get|wget|sha256sum|modprobe|nvidia-persistenced)\b' }).Count |
                    Should -Be 0
            }
            finally {
                Remove-TestArtifacts -WorkDir $workDir
            }
        }
    }

    Context 'when the driver must be installed' {
        It 'downloads, verifies, installs, and validates the GRID driver' {
            $workDir = New-TestWorkDir

            try {
                $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -Stubs (Get-InstallGridDriverStubs `
                        -NvidiaSmiBody (New-NvidiaSmiSecondCallSuccessStub) `
                        -WgetBody (New-WgetInstallerStub -Mode ImmediateSuccess)) -WorkDir $workDir

                $result.ExitCode | Should -Be 0
                $result.StdOut | Should -Match '=== GRID driver installation complete ==='
                $result.StdOut | Should -Not -Match 'Retrying with open kernel modules'
                $result.Calls | Should -Be @(
                    'nvidia-smi'
                    'rmmod nvidia_uvm'
                    'rmmod nvidia_modeset'
                    'rmmod nvidia_drm'
                    'rmmod nvidia_peermem'
                    'rmmod nvidia'
                    'apt-get update -qq'
                    "apt-get install -y -qq linux-headers-$script:KernelRelease build-essential wget"
                    "wget -q -O $script:DriverFile $script:DriverUrl"
                    'sha256sum -c --quiet -'
                    'modprobe nvidia'
                    'modprobe nvidia-uvm'
                    'modprobe nvidia-modeset'
                    'nvidia-persistenced --persistence-mode'
                    'nvidia-smi'
                    'mkdir -p /run/nvidia/validations'
                    'touch /run/nvidia/validations/.driver-ctr-ready'
                    'mountpoint -q /run/nvidia/driver'
                    'mount --bind / /run/nvidia/driver'
                )
            }
            finally {
                Remove-TestArtifacts -WorkDir $workDir
            }
        }

        It 'retries with open kernel modules when the default installer mode fails' {
            $workDir = New-TestWorkDir

            try {
                $result = Invoke-BashEntryScript -ScriptPath $script:ScriptPath -Stubs (Get-InstallGridDriverStubs `
                        -NvidiaSmiBody (New-NvidiaSmiSecondCallSuccessStub) `
                        -WgetBody (New-WgetInstallerStub -Mode RetryWithOpenModules)) -WorkDir $workDir

                $result.ExitCode | Should -Be 0
                $result.StdOut | Should -Match 'Retrying with open kernel modules'
                $result.StdOut | Should -Match '=== GRID driver installation complete ==='
                $result.Calls | Should -Be @(
                    'nvidia-smi'
                    'rmmod nvidia_uvm'
                    'rmmod nvidia_modeset'
                    'rmmod nvidia_drm'
                    'rmmod nvidia_peermem'
                    'rmmod nvidia'
                    'apt-get update -qq'
                    "apt-get install -y -qq linux-headers-$script:KernelRelease build-essential wget"
                    "wget -q -O $script:DriverFile $script:DriverUrl"
                    'sha256sum -c --quiet -'
                    'modprobe nvidia'
                    'modprobe nvidia-uvm'
                    'modprobe nvidia-modeset'
                    'nvidia-persistenced --persistence-mode'
                    'nvidia-smi'
                    'mkdir -p /run/nvidia/validations'
                    'touch /run/nvidia/validations/.driver-ctr-ready'
                    'mountpoint -q /run/nvidia/driver'
                    'mount --bind / /run/nvidia/driver'
                )
            }
            finally {
                Remove-TestArtifacts -WorkDir $workDir
            }
        }
    }
}
