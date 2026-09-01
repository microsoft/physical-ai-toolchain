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

    function New-AttestWorkspace {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
        $bin = Join-Path $root 'bin'
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        foreach ($tool in @('az', 'oras')) {
            Set-Content -Path (Join-Path $bin $tool) -Encoding utf8 -Value "#!/usr/bin/env bash`nexit 0`n"
        }
        Set-Content -Path (Join-Path $bin 'cosign') -Encoding utf8 -Value @'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$ATTEST_LOG"
'@
        & chmod +x (Join-Path $bin 'az') (Join-Path $bin 'oras') (Join-Path $bin 'cosign')
        @{
            Root = $root
            Bin = $bin
            Log = Join-Path $root 'attest.log'
            Vex = Join-Path $root 'document.openvex.json'
        }
    }

    function Set-ValidVexDocument {
        param(
            [Parameter(Mandatory)][string]$Path,
            [string]$Digest = ('a' * 64)
        )

        @{
            '@context' = 'https://openvex.dev/ns/v0.2.0'
            '@id' = 'https://example.test/vex/v1'
            author = 'Test'
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(
                        @{
                            '@id' = "pkg:oci/model@sha256:${Digest}?repository_url=example.azurecr.io"
                        }
                    )
                    status = 'under_investigation'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $Path -Encoding utf8
    }

    function Invoke-AttestImage {
        param(
            [string[]]$Arguments,
            [hashtable]$Workspace
        )

        $previousPath = $env:PATH
        $previousLog = $env:ATTEST_LOG
        try {
            if ($Workspace) {
                $env:PATH = "$($Workspace.Bin):$previousPath"
                $env:ATTEST_LOG = $Workspace.Log
            }
            $output = & bash $script:AttestImage --image $script:Image @Arguments 2>&1
            @{
                ExitCode = $LASTEXITCODE
                Output = $output -join "`n"
            }
        }
        finally {
            $env:PATH = $previousPath
            $env:ATTEST_LOG = $previousLog
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

    It 'rejects an invocation that skips every attestation' {
        $result = Invoke-AttestImage -Arguments @('--skip-sbom')

        $result.ExitCode | Should -Not -Be 0
        $result.Output | Should -Match 'Nothing to attest'
    }

    It 'attaches a valid explicit VEX document with cosign' {
        $workspace = New-AttestWorkspace
        try {
            Set-ValidVexDocument -Path $workspace.Vex

            $result = Invoke-AttestImage `
                -Workspace $workspace `
                -Arguments @('--skip-sbom', '--vex-file', $workspace.Vex)

            $result.ExitCode | Should -Be 0
            $log = Get-Content $workspace.Log -Raw
            $log | Should -Match ([regex]::Escape("attest --yes --predicate $($workspace.Vex) --type openvex $script:Image"))
        }
        finally {
            Remove-Item -Recurse -Force $workspace.Root
        }
    }

    It 'rejects a malformed VEX document before invoking cosign' {
        $workspace = New-AttestWorkspace
        try {
            Set-Content -Path $workspace.Vex -Value '{"statements":[]}' -Encoding utf8

            $result = Invoke-AttestImage `
                -Workspace $workspace `
                -Arguments @('--skip-sbom', '--vex-file', $workspace.Vex)

            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'OpenVEX document is invalid'
            Test-Path $workspace.Log | Should -BeFalse
        }
        finally {
            Remove-Item -Recurse -Force $workspace.Root
        }
    }

    It 'rejects a VEX document for another image digest' {
        $workspace = New-AttestWorkspace
        try {
            Set-ValidVexDocument -Path $workspace.Vex -Digest ('b' * 64)

            $result = Invoke-AttestImage `
                -Workspace $workspace `
                -Arguments @('--skip-sbom', '--vex-file', $workspace.Vex)

            $result.ExitCode | Should -Not -Be 0
            $result.Output | Should -Match 'does not identify image digest'
            Test-Path $workspace.Log | Should -BeFalse
        }
        finally {
            Remove-Item -Recurse -Force $workspace.Root
        }
    }

    It 'does not require a configured VEX document in notation mode' {
        $workspace = New-AttestWorkspace
        try {
            $missing = Join-Path $workspace.Root 'missing.openvex.json'
            $sbom = Join-Path $workspace.Root 'sbom.spdx.json'
            Set-Content -Path $sbom -Value '{}' -Encoding utf8

            $result = Invoke-AttestImage `
                -Workspace $workspace `
                -Arguments @('--mode', 'notation', '--sbom-file', $sbom, '--vex-file', $missing)

            $result.ExitCode | Should -Be 0
            $result.Output | Should -Match 'OpenVEX attestation is not implemented for notation mode'
        }
        finally {
            Remove-Item -Recurse -Force $workspace.Root
        }
    }
}
