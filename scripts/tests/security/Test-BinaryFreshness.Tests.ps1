#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore feedface nonliteral octocat

BeforeAll {
    . $PSScriptRoot/../../security/Test-BinaryFreshness.ps1

    $script:FixturesRoot = Join-Path $TestDrive 'repo'
    New-Item -ItemType Directory -Path $script:FixturesRoot -Force | Out-Null

    $script:DevDepsPath = Join-Path $script:FixturesRoot 'install-dev-deps.sh'
    @'
#!/usr/bin/env bash
NODESOURCE_GPG_SHA256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
UV_VERSION="0.4.18"
UV_SHA256="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
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

    $script:SetupDevPath = Join-Path $script:FixturesRoot 'setup-dev.ps1'
    @'
$UvVersion = '0.11.21'
$UvInstallerSha256 = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
'@ | Set-Content -Path $script:SetupDevPath -Encoding utf8
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

Describe 'Get-PowerShellVariable' -Tag 'Unit' {
    It 'Extracts literal PowerShell assignments' {
        Get-PowerShellVariable -Path $script:SetupDevPath -Name 'UvVersion' | Should -Be '0.11.21'
    }

    It 'Returns null for missing or non-literal assignments' {
        $path = Join-Path $TestDrive 'nonliteral.ps1'
        '$Value = $env:VALUE' | Set-Content -Path $path

        Get-PowerShellVariable -Path $script:SetupDevPath -Name 'Missing' | Should -BeNullOrEmpty
        Get-PowerShellVariable -Path $path -Name 'Value' | Should -BeNullOrEmpty
    }

    It 'Throws for duplicate assignments' {
        $path = Join-Path $TestDrive 'duplicate.ps1'
        "`$Value = 'one'`n`$Value = 'two'" | Set-Content -Path $path

        { Get-PowerShellVariable -Path $path -Name 'Value' } |
            Should -Throw "Expected exactly one assignment for 'Value'*"
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
        $output = [System.IO.MemoryStream]::new()
        try {
            $gzip = [System.IO.Compression.GZipStream]::new(
                $output, [System.IO.Compression.CompressionMode]::Compress, $true
            )
            try { $gzip.Write($script:FakeBytes, 0, $script:FakeBytes.Length) }
            finally { $gzip.Dispose() }
            $script:GzipBytes = $output.ToArray()
            $script:GzipHash = [Convert]::ToHexString(
                [System.Security.Cryptography.SHA256]::HashData($script:GzipBytes)
            ).ToLowerInvariant()
        }
        finally { $output.Dispose() }
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

    It 'Treats a JSON response from a ZIP URL as a download failure, not a hash mismatch' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllText($OutFile, '{"requestStatus":{"statusCode":"SUCCESS"}}')
        }
        $result = Invoke-HashCheck -Name 'NGC CLI' -Url 'https://example/ngccli_linux.zip' `
            -Expected $script:KnownHash -File 'devcontainer.json'
        $result.Status | Should -Be 'DownloadFailed'
        $result.Message | Should -Match 'Unexpected content'
    }

    It 'Treats JSON from an extensionless key URL as a download failure' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllText($OutFile, '{"error":"temporarily unavailable"}')
        }
        $result = Invoke-HashCheck -Name 'GPG Key' -Url 'https://example/gpgkey' `
            -Expected $script:KnownHash -File 'install.sh'
        $result.Status | Should -Be 'DownloadFailed'
    }

    It 'Treats HTML from a script URL as a download failure' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllText($OutFile, '<html><body>Unavailable</body></html>')
        }
        $result = Invoke-HashCheck -Name 'Installer' -Url 'https://example/install.ps1' `
            -Expected $script:KnownHash -File 'setup.ps1'
        $result.Status | Should -Be 'DownloadFailed'
    }

    It 'Preserves a genuine ZIP hash mismatch as an integrity failure' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            $stream = [System.IO.File]::Open($OutFile, [System.IO.FileMode]::Create)
            $archive = [System.IO.Compression.ZipArchive]::new(
                $stream, [System.IO.Compression.ZipArchiveMode]::Create, $false
            )
            try {
                $entry = $archive.CreateEntry('test.txt')
                $writer = [System.IO.StreamWriter]::new($entry.Open())
                try { $writer.Write('artifact') }
                finally { $writer.Dispose() }
            }
            finally { $archive.Dispose() }
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.zip' `
            -Expected $script:KnownHash -File 'devcontainer.json'
        $result.Status | Should -Be 'Mismatch'
    }

    It 'Treats a truncated ZIP as an unavailable download' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0x50, 0x4b, 0x03, 0x04, 0x01))
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.zip' `
            -Expected $script:KnownHash -File 'devcontainer.json'
        $result.Status | Should -Be 'DownloadFailed'
    }

    It 'Rejects a non-GZIP payload from a tar.gz URL' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, $script:FakeBytes)
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:KnownHash -File 'install.sh'
        $result.Status | Should -Be 'DownloadFailed'
    }

    It 'Preserves a genuine GZIP hash mismatch as an integrity failure' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, $script:GzipBytes)
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:KnownHash -File 'install.sh'
        $result.Status | Should -Be 'Mismatch'
    }

    It 'Matches a complete GZIP archive with its pinned hash' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, $script:GzipBytes)
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:GzipHash -File 'install.sh'
        $result.Status | Should -Be 'Match'
    }

    It 'Treats a header-only GZIP response as unavailable' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            [System.IO.File]::WriteAllBytes($OutFile, [byte[]]@(0x1f, 0x8b, 0x08, 0x00))
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:GzipHash -File 'install.sh'
        $result.Status | Should -Be 'DownloadFailed'
    }

    It 'Treats a GZIP missing <TruncatedBytes> trailer byte(s) as unavailable' -ForEach @(
        @{ TruncatedBytes = 1 }
        @{ TruncatedBytes = 4 }
        @{ TruncatedBytes = 8 }
    ) {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            $last = $script:GzipBytes.Length - $TruncatedBytes - 1
            [System.IO.File]::WriteAllBytes($OutFile, [byte[]]$script:GzipBytes[0..$last])
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:GzipHash -File 'install.sh'
        $result.Status | Should -Be 'DownloadFailed'
        (ConvertTo-HashCheckSarifResult -Result $result -File 'install.sh').level | Should -Be 'warning'
    }

    It 'Treats a corrupt GZIP checksum as unavailable' {
        Mock Invoke-WebRequest -MockWith {
            param($Uri, $OutFile)
            $null = $Uri
            $bytes = [byte[]]$script:GzipBytes.Clone()
            $bytes[$bytes.Length - 8] = $bytes[$bytes.Length - 8] -bxor 0x01
            [System.IO.File]::WriteAllBytes($OutFile, $bytes)
        }
        $result = Invoke-HashCheck -Name 'Archive' -Url 'https://example/download.tar.gz' `
            -Expected $script:GzipHash -File 'install.sh'
        $result.Status | Should -Be 'DownloadFailed'
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

    It 'Retries a failed registry request and returns its successful result' {
        $script:retryCalls = 0
        Mock Start-Sleep {}
        $result = Invoke-WithRetry -MaxAttempts 3 -Action {
            $script:retryCalls++
            if ($script:retryCalls -eq 1) { throw 'temporary registry failure' }
            'v0.20.1'
        }
        $result | Should -Be 'v0.20.1'
        $script:retryCalls | Should -Be 2
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
    It 'Returns exactly 9 check definitions' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        $defs.Count | Should -Be 9
    }

    It 'Each definition has the required keys' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        foreach ($def in $defs) {
            $def.Keys | Should -Contain 'Name'
            $def.Keys | Should -Contain 'Url'
            $def.Keys | Should -Contain 'Expected'
            $def.Keys | Should -Contain 'File'
        }
    }

    It 'Resolves NodeSource GPG entry from dev-deps' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        $nodeSource = $defs | Where-Object Name -eq 'NodeSource GPG Key'
        $nodeSource.Expected | Should -Be 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $nodeSource.File | Should -Be $script:DevDepsPath
    }

    It 'Resolves the Linux uv archive pin' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        $uv = $defs | Where-Object Name -eq 'uv Linux Archive (v0.4.18)'
        $uv.Url | Should -Be 'https://github.com/astral-sh/uv/releases/download/0.4.18/uv-x86_64-unknown-linux-gnu.tar.gz'
        $uv.Expected | Should -Be ('b' * 64)
        $uv.File | Should -Be $script:DevDepsPath
    }

    It 'Resolves ThinLinc entry from thinlinc script' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        $thinLinc = $defs | Where-Object Name -like 'ThinLinc Server*'
        $thinLinc.Expected | Should -Be 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
        $thinLinc.File | Should -Be $script:ThinlincPath
    }

    It 'Resolves the PowerShell uv installer pin' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        $uv = $defs | Where-Object Name -eq 'uv PowerShell Installer (v0.11.21)'
        $uv.Url | Should -Be 'https://astral.sh/uv/0.11.21/install.ps1'
        $uv.Expected | Should -Be ('f' * 64)
        $uv.File | Should -Be $script:SetupDevPath
    }

    It 'Rejects missing PowerShell uv installer pins' {
        $path = Join-Path $TestDrive 'missing-uv-pin.ps1'
        '$UvVersion = ''0.11.21''' | Set-Content -Path $path

        {
            Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $path
        } | Should -Throw 'UvInstallerSha256*missing or is not a string literal*'
    }

    It 'Rejects a non-literal PowerShell uv version' {
        $path = Join-Path $TestDrive 'nonliteral-uv-version.ps1'
        @'
$UvVersion = $env:UV_VERSION
$UvInstallerSha256 = 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
'@ | Set-Content -Path $path

        {
            Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $path
        } | Should -Throw 'UvVersion*missing or is not a string literal*'
    }

    It 'Rejects a malformed PowerShell uv installer hash' {
        $path = Join-Path $TestDrive 'malformed-uv-hash.ps1'
        @'
$UvVersion = '0.11.21'
$UvInstallerSha256 = 'deadbeef'
'@ | Set-Content -Path $path

        {
            Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $path
        } | Should -Throw 'UvInstallerSha256*is not a SHA-256 digest*'
    }

    It 'Resolves devcontainer entries with correct file reference' {
        $defs = Get-BinaryCheckDefinitions -DevDeps $script:DevDepsPath -Thinlinc $script:ThinlincPath -Devcontainer $script:DevcontainerPath -SetupDev $script:SetupDevPath
        ($defs | Where-Object Name -like 'TFLint*').File | Should -Be $script:DevcontainerPath
        $defs.Name | Should -Contain 'OSMO Installer (0.5.0)'
        $defs.Name | Should -Contain 'NGC CLI (3.50.0)'
    }
}

Describe 'ConvertTo-HashCheckSarifResult' -Tag 'Unit' {
    It 'Returns null for a Match result' {
        $result = @{ Status = 'Match'; Message = 'hashes match' }
        ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh' | Should -BeNullOrEmpty
    }

    It 'Returns warning-level result for DownloadFailed' {
        $result = @{ Status = 'DownloadFailed'; Message = 'connection refused' }
        $sarif = ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/download-failure'
        $sarif.level | Should -Be 'warning'
    }

    It 'Returns warning-level result for Mismatch' {
        $result = @{ Status = 'Mismatch'; Message = 'expected aaa got bbb' }
        $sarif = ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh'
        $sarif | Should -Not -BeNullOrEmpty
        $sarif.ruleId | Should -Be 'binary-freshness/hash-mismatch'
        $sarif.level | Should -Be 'warning'
    }

    It 'Rejects an unrecognized status' {
        $result = @{ Status = 'Unknown'; Message = 'mystery' }
        { ConvertTo-HashCheckSarifResult -Result $result -File 'test.sh' } |
            Should -Throw 'Unknown binary check status: Unknown'
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
        $invoker = { }
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
    It 'Returns the highest stable semver tag across GHCR pages' {
        $invoker = {
            param($Uri, $Headers)
            if ($Uri -like 'https://ghcr.io/token?*') {
                return @{ token = 'test-token' }
            }
            if ($Uri -notmatch '&last=') {
                $tags = @('v0.20.1', 'v0.9.0', 'v0.21.0-rc1') + @(1..97 | ForEach-Object { "0.0.0-$($_)" })
                return @{ tags = $tags }
            }
            if ($Headers.Authorization -ne 'Bearer test-token') { throw 'Missing pull token' }
            return @{ tags = @('v0.19.0', 'v0.18.0') }
        }
        Get-HelmOciLatestVersion -Chart 'oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler' `
            -RequestInvoker $invoker | Should -Be 'v0.20.1'
    }

    It 'Throws when the anonymous pull token is unavailable' {
        $invoker = { @{ token = $null } }
        {
            Get-HelmOciLatestVersion -Chart 'oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler' -RequestInvoker $invoker
        } | Should -Throw '*did not provide a pull token*'
    }

    It 'Rejects registry responses without a tag list' {
        $invoker = {
            param($Uri)
            if ($Uri -like 'https://ghcr.io/token?*') { return @{ token = 'test-token' } }
            return @{ status = 'unavailable' }
        }
        {
            Get-HelmOciLatestVersion -Chart 'oci://ghcr.io/nvidia/kai-scheduler/kai-scheduler' -RequestInvoker $invoker
        } | Should -Throw '*did not return a tag list*'
    }

    It 'Rejects a chart source outside the supported public registry' {
        { Get-HelmOciLatestVersion -Chart 'oci://example/chart' } |
            Should -Throw 'Unsupported OCI chart source*'
    }
}

Describe 'Get-BinaryCheckExitCode' -Tag 'Unit' {
    It 'Exits successfully without a confirmed hash mismatch' {
        Get-BinaryCheckExitCode -IntegrityFailures 0 | Should -Be 0
    }

    It 'Exits with integrity failure when a hash mismatch is confirmed' {
        Get-BinaryCheckExitCode -IntegrityFailures 1 | Should -Be 1
    }

    It 'Exits with a distinct code for fatal setup or reporting errors' {
        Get-BinaryCheckExitCode -IntegrityFailures 1 -Fatal | Should -Be 2
    }
}

Describe 'Invoke-BinaryFreshnessCheck' -Tag 'Unit' {
    BeforeAll {
        $defaultsDir = Join-Path $script:FixturesRoot 'infrastructure/setup'
        New-Item -ItemType Directory -Path $defaultsDir -Force | Out-Null
        @'
GPU_OPERATOR_VERSION="${GPU_OPERATOR_VERSION:-v1.0.0}"
KAI_SCHEDULER_VERSION="${KAI_SCHEDULER_VERSION:-v0.20.1}"
OSMO_CHART_VERSION="${OSMO_CHART_VERSION:-1.0.0}"
HELM_REPO_GPU_OPERATOR="${HELM_REPO_GPU_OPERATOR:-https://example.com/gpu}"
HELM_REPO_KAI="${HELM_REPO_KAI:-oci://ghcr.io/nvidia/kai-scheduler}"
HELM_REPO_OSMO="${HELM_REPO_OSMO:-https://example.com/osmo}"
'@ | Set-Content -Path (Join-Path $defaultsDir 'defaults.conf')
    }

    BeforeEach {
        $script:HashStatuses = @('Match', 'Match')
        $script:GpuLatest = 'v1.0.0'
        $script:KaiLatest = 'v0.20.1'
        $script:OsmoLatest = '1.0.0'
        $script:SarifOutput = Join-Path $TestDrive 'check.sarif'

        Mock Get-BinaryCheckDefinitions {
            @(
                @{ Name = 'Binary A'; Expected = ('a' * 64); Url = 'https://example/a.zip'; File = 'a.sh' }
                @{ Name = 'Binary B'; Expected = ('b' * 64); Url = 'https://example/b.zip'; File = 'b.sh' }
            )
        }
        Mock Invoke-HashCheck {
            param($Name)
            $status = if ($Name -eq 'Binary A') { $script:HashStatuses[0] } else { $script:HashStatuses[1] }
            return @{ Status = $status; Message = "$Name $status" }
        }
        Mock Get-HelmRepoLatestVersion {
            param($Chart)
            if ($Chart -eq 'nvidia/gpu-operator') { return $script:GpuLatest }
            return $script:OsmoLatest
        }
        Mock Get-HelmOciLatestVersion { $script:KaiLatest }
        Mock Start-Sleep {}
    }

    It 'Produces clean SARIF with exit 0 when all checks match' {
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        $outcome.IntegrityFailures | Should -Be 0
        $outcome.VersionDrift | Should -Be 0
        $outcome.LookupFailures | Should -Be 0
        $outcome.Findings | Should -Be 0
        $outcome.ExitCode | Should -Be 0
        (Get-Content $script:SarifOutput -Raw | ConvertFrom-Json).runs[0].results.Count | Should -Be 0
    }

    It 'Fails only for a confirmed hash mismatch while retaining its SARIF warning' {
        $script:HashStatuses = @('Mismatch', 'Match')
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        $outcome.IntegrityFailures | Should -Be 1
        $outcome.ExitCode | Should -Be 1
        $sarif = Get-Content $script:SarifOutput -Raw | ConvertFrom-Json
        $sarif.runs[0].results[0].ruleId | Should -Be 'binary-freshness/hash-mismatch'
        $sarif.runs[0].results[0].level | Should -Be 'warning'
    }

    It 'Keeps chart version drift advisory with exit 0' {
        $script:GpuLatest = 'v1.1.0'
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        $outcome.VersionDrift | Should -Be 1
        $outcome.ExitCode | Should -Be 0
        (Get-Content $script:SarifOutput -Raw | ConvertFrom-Json).runs[0].results[0].ruleId |
            Should -Be 'binary-freshness/version-drift'
    }

    It 'Reports failed binary and OCI lookups without a false integrity exit' {
        $script:HashStatuses = @('DownloadFailed', 'Match')
        $script:KaiLatest = $null
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        $outcome.LookupFailures | Should -Be 2
        $outcome.IntegrityFailures | Should -Be 0
        $outcome.ExitCode | Should -Be 0
        $results = (Get-Content $script:SarifOutput -Raw | ConvertFrom-Json).runs[0].results
        @($results | Where-Object level -eq 'warning').Count | Should -Be 2
        $results.ruleId | Should -Contain 'binary-freshness/download-failure'
        $results.ruleId | Should -Contain 'binary-freshness/lookup-failure'
    }

    It 'Gates mixed findings on the confirmed integrity mismatch only' {
        $script:HashStatuses = @('Mismatch', 'DownloadFailed')
        $script:GpuLatest = 'v1.1.0'
        $script:KaiLatest = $null
        $script:OsmoLatest = '1.1.0'
        $outcome = Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        $outcome.IntegrityFailures | Should -Be 1
        $outcome.VersionDrift | Should -Be 2
        $outcome.LookupFailures | Should -Be 2
        $outcome.Findings | Should -Be 5
        $outcome.ExitCode | Should -Be 1
        (Get-Content $script:SarifOutput -Raw | ConvertFrom-Json).runs[0].results.Count | Should -Be 5
    }

    It 'Rejects a missing pin instead of reporting a hash mismatch' {
        Mock Get-BinaryCheckDefinitions {
            @(@{ Name = 'Binary A'; Expected = ''; Url = 'https://example/a.zip'; File = 'a.sh' })
        }
        {
            Invoke-BinaryFreshnessCheck -RepoRoot $script:FixturesRoot -SarifFile $script:SarifOutput -Repository 'owner/repo'
        } | Should -Throw 'Invalid SHA-256 pin*'
    }
}
