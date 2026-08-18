#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command jq -ErrorAction SilentlyContinue)
}

BeforeAll {
    $script:Generator = (Resolve-Path (Join-Path $PSScriptRoot '../../security/generate-vex.sh')).Path
    $script:Digest = 'b' * 64
    $script:Purl = "pkg:oci/test-product@sha256:${script:Digest}?repository_url=registry.example.com"

    function New-GeneratorWorkspace {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString('N'))
        $bin = Join-Path $root 'bin'
        $scan = Join-Path $root 'scan'
        New-Item -ItemType Directory -Path $bin, $scan -Force | Out-Null

        Set-Content -Path (Join-Path $bin 'crane') -Encoding utf8 -Value @"
#!/usr/bin/env bash
printf '%s\n' 'sha256:$script:Digest'
"@
        foreach ($tool in @('trivy', 'grype')) {
            Set-Content -Path (Join-Path $bin $tool) -Encoding utf8 -Value "#!/usr/bin/env bash`nexit 0`n"
        }
        & chmod +x (Join-Path $bin 'crane') (Join-Path $bin 'trivy') (Join-Path $bin 'grype')

        Set-Content -Path (Join-Path $scan 'trivy.json') -Encoding utf8 -Value @'
{"Results":[{"Vulnerabilities":[{"VulnerabilityID":"CVE-2026-0001","Severity":"HIGH"},{"VulnerabilityID":"CVE-2026-0002","Severity":"CRITICAL"}]}]}
'@
        Set-Content -Path (Join-Path $scan 'grype.json') -Encoding utf8 -Value @'
{"matches":[{"vulnerability":{"id":"CVE-2026-0002","severity":"Critical"}}]}
'@
        @{
            digest = "sha256:$script:Digest"
            severity_filter = 'HIGH,CRITICAL'
        } | ConvertTo-Json | Set-Content -Path (Join-Path $scan 'metadata.json') -Encoding utf8

        @{
            Root = $root
            Bin = $bin
            Scan = $scan
            Output = Join-Path $root 'output.openvex.json'
        }
    }

    function Invoke-Generator {
        param(
            [Parameter(Mandatory)][hashtable]$Workspace,
            [string]$Image = 'registry.example.com/test-product:1',
            [switch]$DeriveIdentity
        )

        $previousPath = $env:PATH
        try {
            $env:PATH = "$($Workspace.Bin):$previousPath"
            $arguments = @(
                '--image', $Image,
                '--scan-dir', $Workspace.Scan,
                '--output', $Workspace.Output,
                '--skip-scan'
            )
            if (-not $DeriveIdentity) {
                $arguments += @('--product', 'test-product', '--repo-url', 'registry.example.com')
            }
            $output = & bash $script:Generator @arguments 2>&1
            $script:GeneratorExit = $LASTEXITCODE
            $script:GeneratorOutput = $output -join "`n"
        }
        finally {
            $env:PATH = $previousPath
        }
    }
}

Describe 'generate-vex.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeEach {
        $script:Workspace = New-GeneratorWorkspace
    }

    AfterEach {
        Remove-Item -Recurse -Force $script:Workspace.Root -ErrorAction SilentlyContinue
    }

    It 'preserves all prior statements and appends unseen findings for the current product' {
        $otherPurl = 'pkg:oci/test-product@sha256:' + ('a' * 64) + '?repository_url=registry.example.com'
        @{
            '@context' = 'https://openvex.dev/ns/v0.2.0'
            '@id' = 'https://example.test/vex/v1'
            author = 'Test'
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            last_updated = '2026-01-01T00:00:00Z'
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = $script:Purl })
                    status = 'not_affected'
                    justification = 'component_not_present'
                    status_notes = 'Package inventory proves the component is absent.'
                },
                @{
                    vulnerability = @{ name = 'CVE-2025-9999' }
                    products = @(@{ '@id' = $otherPurl })
                    status = 'fixed'
                    status_notes = 'The prior digest contains the verified remediation.'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Be 0
        $document = Get-Content $script:Workspace.Output -Raw | ConvertFrom-Json
        $document.version | Should -Be 2
        $document.'@id' | Should -Match '/2026-\d{2}-\d{2}-v2-[0-9a-f]{32}$'
        $document.last_updated | Should -Be $document.timestamp
        $document.statements.Count | Should -Be 3

        $triaged = $document.statements | Where-Object { $_.vulnerability.name -eq 'CVE-2026-0001' }
        $triaged.status | Should -Be 'not_affected'
        $triaged.justification | Should -Be 'component_not_present'
        $triaged.timestamp.ToUniversalTime().ToString('o') | Should -Be '2026-01-01T00:00:00.0000000Z'

        $priorDigest = $document.statements | Where-Object { $_.vulnerability.name -eq 'CVE-2025-9999' }
        $priorDigest.status | Should -Be 'fixed'
        $priorDigest.timestamp.ToUniversalTime().ToString('o') | Should -Be '2026-01-01T00:00:00.0000000Z'

        $newFinding = $document.statements | Where-Object { $_.vulnerability.name -eq 'CVE-2026-0002' }
        $newFinding.status | Should -Be 'under_investigation'
        $script:GeneratorOutput | Should -Match 'Statements total:\s+3'
    }

    It 'rejects an untrusted existing version without evaluating it or changing the document' {
        $marker = Join-Path $script:Workspace.Root 'arithmetic-injection'
        $invalid = '{"timestamp":"2026-01-01T00:00:00Z","version":"x[$(touch arithmetic-injection)]","statements":[]}'
        Set-Content -Path $script:Workspace.Output -Value $invalid -Encoding utf8 -NoNewline
        $before = Get-Content $script:Workspace.Output -Raw

        Push-Location $script:Workspace.Root
        try {
            Invoke-Generator -Workspace $script:Workspace
        }
        finally {
            Pop-Location
        }

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
        Test-Path $marker | Should -BeFalse
    }

    It 'normalizes an integer-valued JSON version before incrementing it' {
        $existing = '{"timestamp":"2026-01-01T00:00:00Z","version":1.0,"statements":[]}'
        Set-Content -Path $script:Workspace.Output -Value $existing -Encoding utf8 -NoNewline

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Be 0
        (Get-Content $script:Workspace.Output -Raw | ConvertFrom-Json).version | Should -Be 2
    }

    It 'rejects a version that cannot be incremented safely' {
        $existing = '{"timestamp":"2026-01-01T00:00:00Z","version":9223372036854775807,"statements":[]}'
        Set-Content -Path $script:Workspace.Output -Value $existing -Encoding utf8 -NoNewline
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
    }

    It 'rejects malformed document and statement timestamps' {
        @{
            timestamp = 'not-a-timestamp'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = $script:Purl })
                    status = 'under_investigation'
                    timestamp = 'also-not-a-timestamp'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
    }

    It 'rejects scanner output cached for another digest' {
        @{
            digest = 'sha256:' + ('a' * 64)
            severity_filter = 'HIGH,CRITICAL'
        } | ConvertTo-Json | Set-Content -Path (Join-Path $script:Workspace.Scan 'metadata.json') -Encoding utf8

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Cached scanner output does not match digest'
        Test-Path $script:Workspace.Output | Should -BeFalse
    }

    It 'rejects duplicate vulnerability and product pairs without changing the document' {
        $statement = @{
            vulnerability = @{ name = 'CVE-2026-0001' }
            products = @(@{ '@id' = $script:Purl })
            status = 'under_investigation'
        }
        @{
            '@context' = 'https://openvex.dev/ns/v0.2.0'
            '@id' = 'https://example.test/vex/v1'
            author = 'Test'
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @($statement, $statement)
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'duplicate vulnerability/product pairs'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
    }

    It 'rejects a terminal status without product-specific evidence' {
        @{
            '@context' = 'https://openvex.dev/ns/v0.2.0'
            '@id' = 'https://example.test/vex/v1'
            author = 'Test'
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = $script:Purl })
                    status = 'fixed'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
    }

    It 'rejects not_affected without an allowed justification and status notes' {
        @{
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = $script:Purl })
                    status = 'not_affected'
                    justification = 'not_reachable'
                    status_notes = ''
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
    }

    It 'rejects affected without an action statement' {
        @{
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = $script:Purl })
                    status = 'affected'
                    status_notes = 'The vulnerable path is reachable.'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
    }

    It 'rejects a product that is not identified by a digest-pinned OCI purl' {
        @{
            timestamp = '2026-01-01T00:00:00Z'
            version = 1
            statements = @(
                @{
                    vulnerability = @{ name = 'CVE-2026-0001' }
                    products = @(@{ '@id' = 'pkg:oci/test-product:latest?repository_url=registry.example.com' })
                    status = 'under_investigation'
                }
            )
        } | ConvertTo-Json -Depth 10 | Set-Content -Path $script:Workspace.Output -Encoding utf8

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'Existing OpenVEX document is invalid'
    }

    It 'increments the version and issues a distinct revision ID on repeated runs' {
        Invoke-Generator -Workspace $script:Workspace
        $script:GeneratorExit | Should -Be 0
        $first = Get-Content $script:Workspace.Output -Raw | ConvertFrom-Json

        Invoke-Generator -Workspace $script:Workspace
        $second = Get-Content $script:Workspace.Output -Raw | ConvertFrom-Json

        $script:GeneratorExit | Should -Be 0
        $second.version | Should -Be ($first.version + 1)
        $second.'@id' | Should -Not -Be $first.'@id'
        $second.statements.Count | Should -Be 2
        Test-Path "$($script:Workspace.Output).lock" | Should -BeFalse
    }

    It 'derives product identity without dropping a registry port' {
        Invoke-Generator `
            -Workspace $script:Workspace `
            -Image 'registry.example.com:5000/nested/test-product:1' `
            -DeriveIdentity

        $script:GeneratorExit | Should -Be 0
        $document = Get-Content $script:Workspace.Output -Raw | ConvertFrom-Json
        $document.'_source'.image_ref | Should -Be "registry.example.com:5000/nested/test-product@sha256:$script:Digest"
        $document.statements[0].products[0].'@id' |
            Should -Be "pkg:oci/test-product@sha256:${script:Digest}?repository_url=registry.example.com:5000/nested"
    }

    It 'refuses a concurrent writer lock without changing the document' {
        Set-Content -Path $script:Workspace.Output -Value '{"timestamp":"2026-01-01T00:00:00Z","version":1,"statements":[]}' -Encoding utf8
        New-Item -ItemType Directory -Path "$($script:Workspace.Output).lock" | Out-Null
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        $script:GeneratorOutput | Should -Match 'already writing'
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
        Test-Path "$($script:Workspace.Output).lock" | Should -BeTrue
    }

    It 'leaves the existing document intact when the atomic replacement fails' {
        Set-Content -Path $script:Workspace.Output -Value '{"timestamp":"2026-01-01T00:00:00Z","version":1,"statements":[]}' -Encoding utf8
        Set-Content -Path (Join-Path $script:Workspace.Bin 'mv') -Encoding utf8 -Value "#!/usr/bin/env bash`nexit 1`n"
        & chmod +x (Join-Path $script:Workspace.Bin 'mv')
        $before = Get-Content $script:Workspace.Output -Raw

        Invoke-Generator -Workspace $script:Workspace

        $script:GeneratorExit | Should -Not -Be 0
        Get-Content $script:Workspace.Output -Raw | Should -BeExactly $before
        Get-ChildItem "$($script:Workspace.Output).tmp.*" -ErrorAction SilentlyContinue | Should -BeNullOrEmpty
    }
}

Describe 'committed OpenVEX documents' -Tag 'Unit' {
    It 'conforms to the repository status and mutation contract' {
        $allowedStatuses = @('under_investigation', 'not_affected', 'affected', 'fixed')
        $allowedJustifications = @(
            'component_not_present',
            'vulnerable_code_not_present',
            'vulnerable_code_not_in_execute_path',
            'vulnerable_code_cannot_be_controlled_by_adversary',
            'inline_mitigations_already_exist'
        )
        $documents = Get-ChildItem (Join-Path $PSScriptRoot '../../../security/vex') -Filter '*.openvex.json'
        $ids = @()

        if ($documents.Count -eq 0) {
            Set-ItResult -Skipped -Because 'No committed OpenVEX documents exist'
            return
        }

        foreach ($file in $documents) {
            $document = Get-Content $file.FullName -Raw | ConvertFrom-Json
            $document.version | Should -BeOfType [long]
            $document.version | Should -BeGreaterThan 0
            $document.timestamp.ToUniversalTime().ToString('o') | Should -Match 'Z$'
            $document.'@id' | Should -Not -BeNullOrEmpty
            $ids += $document.'@id'

            foreach ($statement in $document.statements) {
                $statement.status | Should -BeIn $allowedStatuses
                foreach ($product in $statement.products) {
                    $product.'@id' | Should -Match '^pkg:oci/.+@sha256:[0-9a-f]{64}\?.*repository_url=[^&]+'
                }
                if ($statement.status -eq 'not_affected') {
                    $statement.justification | Should -BeIn $allowedJustifications
                }
                if ($statement.status -eq 'affected') {
                    $statement.action_statement | Should -Not -BeNullOrEmpty
                }
                if ($statement.status -ne 'under_investigation') {
                    $statement.status_notes | Should -Not -BeNullOrEmpty
                }
                if ($null -ne $statement.timestamp) {
                    $statement.timestamp.ToUniversalTime().ToString('o') | Should -Match 'Z$'
                }
            }
        }

        $ids.Count | Should -Be (($ids | Sort-Object -Unique).Count)
    }
}
