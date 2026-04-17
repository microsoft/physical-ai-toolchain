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
NVIDIA_CTK_GPG_SHA256="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
'@ | Set-Content -Path $script:DevDepsPath -Encoding utf8

    $script:ThinlincPath = Join-Path $script:FixturesRoot 'install-thinlinc-silent.sh'
    @'
#!/usr/bin/env bash
TL_VERSION="4.17.0"
TL_SHA256="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
'@ | Set-Content -Path $script:ThinlincPath -Encoding utf8

    $script:DevcontainerPath = Join-Path $script:FixturesRoot 'devcontainer.json'
    @'
{
  "postCreateCommand": "TFLINT_VERSION=v0.58.0 TFLINT_SHA256=deadbeef OSMO_VERSION=0.5.0 OSMO_INSTALLER_SHA256=feedface NGC_CLI_VERSION=3.50.0 NGC_CLI_SHA256=cafebabe install.sh"
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
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, $script:FakeBytes)
        }
        $result = Invoke-HashCheck -Name 'test' -Url 'https://example/test' `
            -Expected $script:KnownHash -File 'some.sh'
        $result.Status | Should -Be 'Match'
    }

    It 'Returns Mismatch when expected hash differs' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
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
            $null = $Uri
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

Describe 'Resolve-RepoRoot' -Tag 'Unit' {
    It 'Returns the resolved -Hint path when provided' {
        (Resolve-RepoRoot -Hint $script:FixturesRoot) | Should -Be (Resolve-Path $script:FixturesRoot).Path
    }

    It 'Falls back to a non-empty path when no hint is provided' {
        Resolve-RepoRoot | Should -Not -BeNullOrEmpty
    }

    It 'Falls back to script-relative path when git throws' {
        Mock git { throw 'git not available' }
        Resolve-RepoRoot | Should -Not -BeNullOrEmpty
    }
}

Describe 'Resolve-Repository' -Tag 'Unit' {
    BeforeEach {
        $script:OrigRepo = $env:GITHUB_REPOSITORY
        Remove-Item Env:GITHUB_REPOSITORY -ErrorAction SilentlyContinue
    }
    AfterEach {
        if ($script:OrigRepo) {
            $env:GITHUB_REPOSITORY = $script:OrigRepo
        } else {
            Remove-Item Env:GITHUB_REPOSITORY -ErrorAction SilentlyContinue
        }
    }

    It 'Prefers $env:GITHUB_REPOSITORY when set' {
        $env:GITHUB_REPOSITORY = 'owner/repo'
        Resolve-Repository -RepoRoot $script:FixturesRoot | Should -Be 'owner/repo'
    }

    It 'Parses https github remote URLs' {
        Mock git { 'https://github.com/octocat/robotics.git' }
        Resolve-Repository -RepoRoot $script:FixturesRoot | Should -Be 'octocat/robotics'
    }

    It 'Parses ssh github remote URLs' {
        Mock git { 'git@github.com:octocat/robotics.git' }
        Resolve-Repository -RepoRoot $script:FixturesRoot | Should -Be 'octocat/robotics'
    }

    It 'Returns unknown/unknown when no source is available' {
        Mock git { $null }
        Resolve-Repository -RepoRoot $script:FixturesRoot | Should -Be 'unknown/unknown'
    }

    It 'Returns unknown/unknown when git throws' {
        Mock git { throw 'git not available' }
        Resolve-Repository -RepoRoot $script:FixturesRoot | Should -Be 'unknown/unknown'
    }
}

Describe 'Get-BinaryCheckDefinitions' -Tag 'Unit' {
    It 'Returns exactly 8 check definitions' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath
        $defs.Count | Should -Be 8
    }

    It 'Each definition has the required keys' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath
        foreach ($def in $defs) {
            $def.Keys | Should -Contain 'Name'
            $def.Keys | Should -Contain 'Url'
            $def.Keys | Should -Contain 'Expected'
            $def.Keys | Should -Contain 'File'
        }
    }

    It 'Resolves NodeSource GPG entry from dev-deps' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath
        $defs[0].Name | Should -Be 'NodeSource GPG Key'
        $defs[0].Expected | Should -Be 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $defs[0].File | Should -Be $script:DevDepsPath
    }

    It 'Resolves ThinLinc entry from thinlinc script' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath
        $defs[4].Name | Should -BeLike 'ThinLinc Server*'
        $defs[4].Expected | Should -Be 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        $defs[4].File | Should -Be $script:ThinlincPath
    }

    It 'Resolves devcontainer entries with correct file reference' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath
        $defs[5].Name | Should -BeLike 'TFLint*'
        $defs[5].File | Should -Be $script:DevcontainerPath
        $defs[6].Name | Should -BeLike 'OSMO Installer*'
        $defs[7].Name | Should -BeLike 'NGC CLI*'
    }
}

Describe 'ConvertTo-HashCheckSarifResult' -Tag 'Unit' {
    It 'Returns null for a Match result' {
        $result = @{ Status = 'Match'; Message = 'hashes match' }
        ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh' | Should -BeNullOrEmpty
    }

    It 'Returns error-level result for DownloadFailed' {
        $result = @{ Status = 'DownloadFailed'; Message = 'connection refused' }
        $sarif = ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/download-failure'
        $sarif.level | Should -Be 'error'
    }

    It 'Returns warning-level result for Mismatch' {
        $result = @{ Status = 'Mismatch'; Message = 'expected aaa got bbb' }
        $sarif = ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/hash-mismatch'
        $sarif.level | Should -Be 'warning'
    }

    It 'Returns null for an unrecognized status' {
        $result = @{ Status = 'Unknown'; Message = 'mystery' }
        ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh' | Should -BeNullOrEmpty
    }
}

Describe 'ConvertTo-HelmCheckSarifResult' -Tag 'Unit' {
    It 'Returns lookup-failure warning when Latest is null' {
        $check = @{ Name = 'test-chart'; Source = 'oci://registry'; Latest = $null; Pinned = '1.0.0' }
        $sarif = ConvertTo-HelmCheckSarifResult -Check $check -File 'defaults.conf'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/lookup-failure'
        $sarif.level | Should -Be 'warning'
    }

    It 'Returns version-drift warning when pinned differs from latest' {
        $check = @{ Name = 'test-chart'; Source = 'oci://registry'; Latest = '2.0.0'; Pinned = '1.0.0' }
        $sarif = ConvertTo-HelmCheckSarifResult -Check $check -File 'defaults.conf'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/version-drift'
        $sarif.level | Should -Be 'warning'
    }

    It 'Returns null when version is current' {
        $check = @{ Name = 'test-chart'; Source = 'oci://registry'; Latest = '1.0.0'; Pinned = '1.0.0' }
        ConvertTo-HelmCheckSarifResult -Check $check -File 'defaults.conf' | Should -BeNullOrEmpty
    }
}

Describe 'Get-HelmRepoLatestVersion' -Tag 'Unit' {
    It 'Returns the latest version from helm search JSON' {
        $invoker = {
            param($HelmArgs)
            if ($HelmArgs[0] -eq 'search') {
                '[{"version":"3.2.1"}]'
            }
        }
        Get-HelmRepoLatestVersion -RepoName 'test' -RepoUrl 'https://example.com' -Chart 'test/chart' -HelmInvoker $invoker | Should -Be '3.2.1'
    }

    It 'Returns null when search produces no output' {
        $invoker = { param($HelmArgs); $null = $HelmArgs }
        Get-HelmRepoLatestVersion -RepoName 'test' -RepoUrl 'https://example.com' -Chart 'test/chart' -HelmInvoker $invoker | Should -BeNullOrEmpty
    }

    It 'Returns null when search returns empty array' {
        $invoker = {
            param($HelmArgs)
            if ($HelmArgs[0] -eq 'search') {
                '[]'
            }
        }
        Get-HelmRepoLatestVersion -RepoName 'test' -RepoUrl 'https://example.com' -Chart 'test/chart' -HelmInvoker $invoker | Should -BeNullOrEmpty
    }
}

Describe 'Get-HelmOciLatestVersion' -Tag 'Unit' {
    It 'Parses version from helm show chart output' {
        $invoker = {
            param($HelmArgs)
            $null = $HelmArgs
            @('apiVersion: v2', 'name: test-chart', 'version: 4.5.6', 'description: test')
        }
        Get-HelmOciLatestVersion -Chart 'oci://registry/chart' -HelmInvoker $invoker | Should -Be '4.5.6'
    }

    It 'Returns null when helm produces no output' {
        $invoker = { param($HelmArgs); $null = $HelmArgs }
        Get-HelmOciLatestVersion -Chart 'oci://registry/chart' -HelmInvoker $invoker | Should -BeNullOrEmpty
    }

    It 'Returns null when output contains no version line' {
        $invoker = {
            param($HelmArgs)
            $null = $HelmArgs
            @('apiVersion: v2', 'name: test-chart', 'description: test')
        }
        Get-HelmOciLatestVersion -Chart 'oci://registry/chart' -HelmInvoker $invoker | Should -BeNullOrEmpty
    }
}
