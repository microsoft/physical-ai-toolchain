#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Test-BinaryFreshness.ps1

    $script:FixturesRoot = Join-Path $TestDrive 'repo'
    New-Item -ItemType Directory -Path $script:FixturesRoot -Force | Out-Null

    $script:DevDepsPath = Join-Path $script:FixturesRoot 'install-dev-deps.sh'
    @'
#!/usr/bin/env bash
NODESOURCE_GPG_SHA256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
UV_VERSION="0.4.18"
UV_INSTALLER_SHA256="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
MICROSOFT_GPG_SHA256="${MICROSOFT_GPG_OVERRIDE:-cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc}"
'@ | Set-Content -Path $script:DevDepsPath -Encoding utf8

    $script:DevcontainerPath = Join-Path $script:FixturesRoot 'devcontainer.json'
    @'
{
  "postCreateCommand": "TFLINT_VERSION=v0.58.0 TFLINT_SHA256=deadbeef OSMO_INSTALLER_SHA256=feedface NGC_CLI_SHA256=cafebabe install.sh"
}
'@ | Set-Content -Path $script:DevcontainerPath -Encoding utf8
}

Describe 'Get-ShellVariable' -Tag 'Unit' {
    It 'Extracts a quoted shell assignment' {
        Get-ShellVariable -Path $script:DevDepsPath -Name 'UV_VERSION' | Should -Be '0.4.18'
    }

    It 'Unwraps ${VAR:-default} to the default literal' {
        Get-ShellVariable -Path $script:DevDepsPath -Name 'MICROSOFT_GPG_SHA256' |
            Should -Be 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc'
    }

    It 'Returns null for missing files' {
        Get-ShellVariable -Path (Join-Path $TestDrive 'missing.sh') -Name 'FOO' | Should -BeNullOrEmpty
    }

    It 'Returns null for missing variables' {
        Get-ShellVariable -Path $script:DevDepsPath -Name 'DOES_NOT_EXIST' | Should -BeNullOrEmpty
    }
}

Describe 'Get-JsonVariable' -Tag 'Unit' {
    It 'Extracts inline NAME=value tokens from devcontainer.json' {
        Get-JsonVariable -Path $script:DevcontainerPath -Name 'TFLINT_VERSION' | Should -Be 'v0.58.0'
        Get-JsonVariable -Path $script:DevcontainerPath -Name 'OSMO_INSTALLER_SHA256' | Should -Be 'feedface'
        Get-JsonVariable -Path $script:DevcontainerPath -Name 'NGC_CLI_SHA256' | Should -Be 'cafebabe'
    }

    It 'Returns null for missing tokens' {
        Get-JsonVariable -Path $script:DevcontainerPath -Name 'NOPE' | Should -BeNullOrEmpty
    }
}

Describe 'Test-HelmVersionCurrent' -Tag 'Unit' {
    It 'Treats matching versions as current' {
        $r = Test-HelmVersionCurrent -Pinned '1.2.3' -Latest '1.2.3'
        $r.IsCurrent | Should -BeTrue
    }

    It 'Strips leading v from pinned and latest' {
        $r = Test-HelmVersionCurrent -Pinned 'v1.2.3' -Latest '1.2.3'
        $r.IsCurrent | Should -BeTrue
        $r.Pinned | Should -Be '1.2.3'
        $r.Latest | Should -Be '1.2.3'
    }

    It 'Reports drift when versions differ' {
        $r = Test-HelmVersionCurrent -Pinned '1.2.3' -Latest '1.2.4'
        $r.IsCurrent | Should -BeFalse
    }
}

Describe 'New-SarifResult' -Tag 'Unit' {
    It 'Builds a SARIF result with required fields' {
        $r = New-SarifResult -RuleId 'binary-freshness/hash-mismatch' `
            -Message 'Test mismatch' -File 'path/to/file.sh' -Level 'warning'

        $r.ruleId | Should -Be 'binary-freshness/hash-mismatch'
        $r.level | Should -Be 'warning'
        $r.message.text | Should -Be 'Test mismatch'
        $r.locations[0].physicalLocation.artifactLocation.uri | Should -Be 'path/to/file.sh'
        $r.locations[0].physicalLocation.artifactLocation.uriBaseId | Should -Be '%SRCROOT%'
    }
}

Describe 'New-SarifReport' -Tag 'Unit' {
    BeforeAll {
        $script:Report = New-SarifReport -Repository 'octocat/robotics' -Results @()
    }

    It 'Uses the SARIF 2.1.0 schema URL' {
        $script:Report.'$schema' | Should -Be 'https://docs.oasis-open.org/sarif/sarif/v2.1.0/errata01/os/schemas/sarif-schema-2.1.0.json'
    }

    It 'Declares version 2.1.0' {
        $script:Report.version | Should -Be '2.1.0'
    }

    It 'Publishes exactly four rule definitions' {
        $script:Report.runs[0].tool.driver.rules.Count | Should -Be 4
    }

    It 'Includes id, shortDescription, and helpUri on every rule' {
        foreach ($rule in $script:Report.runs[0].tool.driver.rules) {
            $rule.id | Should -Not -BeNullOrEmpty
            $rule.shortDescription.text | Should -Not -BeNullOrEmpty
            $rule.helpUri | Should -Match '^https://github\.com/octocat/robotics/blob/main/'
        }
    }

    It 'Points version-drift helpUri at the update-chart-hashes script (B1 fix)' {
        $rule = $script:Report.runs[0].tool.driver.rules | Where-Object { $_.id -eq 'binary-freshness/version-drift' }
        $rule.helpUri | Should -Be 'https://github.com/octocat/robotics/blob/main/scripts/update-chart-hashes.sh'
    }

    It 'Points hash-related helpUris at the freshness checker script (B1 fix)' {
        $hashRules = $script:Report.runs[0].tool.driver.rules | Where-Object {
            $_.id -in @('binary-freshness/download-failure', 'binary-freshness/hash-mismatch', 'binary-freshness/lookup-failure')
        }
        $hashRules | ForEach-Object {
            $_.helpUri | Should -Be 'https://github.com/octocat/robotics/blob/main/scripts/security/Test-BinaryFreshness.ps1'
        }
    }

    It 'Embeds results passed in' {
        $finding = New-SarifResult -RuleId 'binary-freshness/hash-mismatch' `
            -Message 'bad' -File 'x.sh' -Level 'warning'
        $report = New-SarifReport -Repository 'octocat/robotics' -Results @($finding)
        $report.runs[0].results.Count | Should -Be 1
        $report.runs[0].results[0].ruleId | Should -Be 'binary-freshness/hash-mismatch'
    }

    It 'Round-trips through ConvertTo-Json without loss' {
        $json = $script:Report | ConvertTo-Json -Depth 20
        $parsed = $json | ConvertFrom-Json
        $parsed.version | Should -Be '2.1.0'
        $parsed.runs[0].tool.driver.rules.Count | Should -Be 4
    }
}

Describe 'Invoke-HashCheck' -Tag 'Unit' {
    BeforeAll {
        $script:FakeBytes = [System.Text.Encoding]::UTF8.GetBytes('hello world')
        # SHA-256 of 'hello world'
        $script:KnownHash = 'b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9'
    }

    It 'Returns Match when expected hash equals computed hash' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            [System.IO.File]::WriteAllBytes($OutFile, $script:FakeBytes)
        }
        $result = Invoke-HashCheck -Name 'test' -Url 'https://example/test' `
            -Expected $script:KnownHash -File 'some.sh'
        $result.Status | Should -Be 'Match'
    }

    It 'Returns Mismatch when expected hash differs' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            [System.IO.File]::WriteAllBytes($OutFile, $script:FakeBytes)
        }
        $result = Invoke-HashCheck -Name 'test' -Url 'https://example/test' `
            -Expected ('0' * 64) -File 'some.sh'
        $result.Status | Should -Be 'Mismatch'
        $result.Actual | Should -Be $script:KnownHash
    }

    It 'Returns DownloadFailed when the request throws' {
        Mock Invoke-WebRequest -MockWith { throw 'network down' }
        $result = Invoke-HashCheck -Name 'test' -Url 'https://example/test' `
            -Expected $script:KnownHash -File 'some.sh'
        $result.Status | Should -Be 'DownloadFailed'
        $result.Message | Should -Match 'Failed to download test'
    }

    It 'Is case-insensitive on the expected hash' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            [System.IO.File]::WriteAllBytes($OutFile, $script:FakeBytes)
        }
        $upper = $script:KnownHash.ToUpperInvariant()
        $result = Invoke-HashCheck -Name 'test' -Url 'https://example/test' `
            -Expected $upper -File 'some.sh'
        $result.Status | Should -Be 'Match'
    }
}

Describe 'Invoke-WithRetry' -Tag 'Unit' {
    It 'Returns the first non-empty result' {
        $calls = 0
        $result = Invoke-WithRetry -MaxAttempts 3 -Action {
            $script:calls++
            'ok'
        }
        $result | Should -Be 'ok'
    }

    It 'Returns null when every attempt yields empty output' {
        $result = Invoke-WithRetry -MaxAttempts 2 -Action { '' }
        $result | Should -BeNullOrEmpty
    }
}
