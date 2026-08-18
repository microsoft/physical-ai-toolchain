#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeDiscovery {
    $script:BashPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:AttestImage = (Resolve-Path (Join-Path $PSScriptRoot '../../../fleet-deployment/setup/attest-image.sh')).Path
    $script:Image = 'example.azurecr.io/model@sha256:' + ('a' * 64)

    function Invoke-AttestImage {
        param([string[]]$Arguments)

        $output = & bash $script:AttestImage --image $script:Image @Arguments 2>&1
        @{
            ExitCode = $LASTEXITCODE
            Output = $output -join "`n"
        }
    }
}

Describe 'attest-image.sh' -Tag 'Unit' -Skip:(-not $script:BashPresent) {
    It 'skips VEX when no document is configured' {
        $result = Invoke-AttestImage -Arguments @('--config-preview')

        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'VEX file:\s+<unset>'
        $result.Output | Should -Match 'Skip VEX:\s+true'
    }

    It 'uses an explicit VEX document' {
        $vexFile = New-TemporaryFile
        try {
            $result = Invoke-AttestImage -Arguments @('--vex-file', $vexFile.FullName, '--config-preview')

            $result.ExitCode | Should -Be 0
            $result.Output | Should -Match ([regex]::Escape($vexFile.FullName))
            $result.Output | Should -Match 'Skip VEX:\s+false'
        }
        finally {
            Remove-Item $vexFile.FullName -Force
        }
    }

    It 'fails when an explicit VEX document does not exist' {
        $missing = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))

        $result = Invoke-AttestImage -Arguments @('--vex-file', $missing, '--skip-sbom')

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'VEX file not found'
    }
}
