# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:BashPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
    if ($script:BashPresent) {
        Import-Module (Resolve-Path (Join-Path $PSScriptRoot 'Mocks/BashScriptHarness.psm1')) -Force
    }
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot '../../setup-dev.ps1'),
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw "setup-dev.ps1 parser errors: $($errors.Message -join '; ')"
    }
    $functionNames = @(
        'Write-Info', 'Write-Warn', 'Get-UvTarget', 'Expand-UvArchive',
        'Install-Uv', 'Get-UvInstallation', 'Initialize-Uv'
    )
    $functions = $ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $functionNames
        }, $true)
    if ($functions.Count -ne $functionNames.Count) {
        throw "Expected $($functionNames.Count) uv helper functions, found $($functions.Count)"
    }
    foreach ($function in $functions) {
        . ([scriptblock]::Create($function.Extent.Text))
    }

    function New-UvShellSnippet {
        param([Parameter(Mandatory)][string]$Destination)

        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $content = Get-Content (Join-Path $repoRoot 'setup-dev.sh') -Raw
        $match = [regex]::Match(
            $content,
            '(?ms)^section "UV Package Manager Setup"\s+(.*?)^# ={10,}\s*^# Terraform-Docs'
        )
        if (-not $match.Success) {
            throw 'Could not extract the setup-dev.sh uv section'
        }

        @'
#!/usr/bin/env bash
set -euo pipefail
section() { :; }
info() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
error() { printf '%s\n' "$*" >&2; }
verify_sha256() { :; }
'@ + "`n" + $match.Groups[1].Value + "`n" | Set-Content $Destination -NoNewline
        & chmod +x $Destination
        & bash -n $Destination
        if ($LASTEXITCODE -ne 0) {
            throw "Generated uv shell snippet is invalid:`n$(Get-Content $Destination -Raw)"
        }
    }
}

Describe 'setup-dev uv installation' -Tag 'Unit' {
    BeforeEach {
        $script:OriginalPath = $env:PATH
    }

    AfterEach {
        $env:PATH = $script:OriginalPath
    }

    It 'maps <Architecture> on <OperatingSystem> to <Target>' -TestCases @(
        @{ Architecture = 'X64'; OperatingSystem = 'windows'; Target = 'x86_64-pc-windows-msvc' }
        @{ Architecture = 'Arm64'; OperatingSystem = 'windows'; Target = 'aarch64-pc-windows-msvc' }
        @{ Architecture = 'X64'; OperatingSystem = 'macos'; Target = 'x86_64-apple-darwin' }
        @{ Architecture = 'Arm64'; OperatingSystem = 'macos'; Target = 'aarch64-apple-darwin' }
        @{ Architecture = 'X64'; OperatingSystem = 'linux'; Target = 'x86_64-unknown-linux-gnu' }
        @{ Architecture = 'Arm64'; OperatingSystem = 'linux'; Target = 'aarch64-unknown-linux-gnu' }
    ) {
        param($Architecture, $OperatingSystem, $Target)

        Get-UvTarget -Architecture $Architecture -OperatingSystem $OperatingSystem | Should -Be $Target
    }

    It 'throws for an unsupported architecture' {
        { Get-UvTarget -Architecture 'S390x' -OperatingSystem 'linux' } |
            Should -Throw '*Unsupported architecture for uv*'
    }

    It 'throws for an unsupported operating system' {
        { Get-UvTarget -Architecture 'X64' -OperatingSystem 'freebsd' } |
            Should -Throw '*Unsupported operating system for uv*'
    }

    It 'throws before download when the target has no pinned digest' {
        Mock Invoke-WebRequest {}

        {
            Install-Uv -Version '1.0.0' -Digests @{} -Target 'x86_64-unknown-linux-gnu' `
                -BinDir (Join-Path $TestDrive 'bin')
        } | Should -Throw '*No pinned uv digest*'
        Should -Invoke Invoke-WebRequest -Times 0 -Exactly
    }

    It 'rejects an archive whose checksum does not match' {
        Mock Invoke-WebRequest {
            param($Uri, $OutFile, $UseBasicParsing)
            $null = $Uri
            $null = $UseBasicParsing
            Set-Content $OutFile 'archive'
        }
        Mock Get-FileHash { @{ Hash = 'bad' } }
        $binDir = Join-Path $TestDrive 'bin'

        {
            Install-Uv -Version '1.0.0' `
                -Digests @{ 'x86_64-unknown-linux-gnu' = 'expected' } `
                -Target 'x86_64-unknown-linux-gnu' `
                -BinDir $binDir
        } | Should -Throw '*expected expected, got bad*'
        Test-Path $binDir | Should -BeFalse
    }

    It 'installs both uv binaries from the pinned <Target> archive' -TestCases @(
        @{
            Target = 'x86_64-unknown-linux-gnu'
            Extension = 'tar.gz'
            UvExecutable = 'uv'
            UvxExecutable = 'uvx'
        }
        @{
            Target = 'x86_64-pc-windows-msvc'
            Extension = 'zip'
            UvExecutable = 'uv.exe'
            UvxExecutable = 'uvx.exe'
        }
    ) {
        param($Target, $Extension, $UvExecutable, $UvxExecutable)

        Mock Invoke-WebRequest {
            param($Uri, $OutFile, $UseBasicParsing)
            $null = $Uri
            $null = $UseBasicParsing
            Set-Content $OutFile 'archive'
        }
        Mock Get-FileHash { @{ Hash = 'ABC123' } }
        Mock Expand-UvArchive {
            param($Archive, $DestinationPath, $Target)
            $null = $Archive
            $null = $Target
            $extractDir = Join-Path $DestinationPath 'extracted'
            New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
            Set-Content (Join-Path $extractDir $UvExecutable) 'uv'
            Set-Content (Join-Path $extractDir $UvxExecutable) 'uvx'
            return $extractDir
        }
        $binDir = Join-Path $TestDrive "bin-$($Target -replace '[^a-z0-9]', '-')"
        $expectedUrl = "https://github.com/astral-sh/uv/releases/download/1.0.0/uv-$Target.$Extension"

        Install-Uv -Version '1.0.0' -Digests @{ $Target = 'abc123' } -Target $Target -BinDir $binDir

        Test-Path (Join-Path $binDir $UvExecutable) | Should -BeTrue
        Test-Path (Join-Path $binDir $UvxExecutable) | Should -BeTrue
        $env:PATH.StartsWith("$binDir$([System.IO.Path]::PathSeparator)") | Should -BeTrue
        Should -Invoke Invoke-WebRequest -Times 1 -Exactly -ParameterFilter {
            $Uri -eq $expectedUrl
        }
    }

    It 'extracts a real Windows zip archive' {
        $archiveRoot = Join-Path $TestDrive 'zip-root'
        $destination = Join-Path $TestDrive 'zip-destination'
        $archive = Join-Path $TestDrive 'uv.zip'
        New-Item -ItemType Directory -Path $archiveRoot, $destination | Out-Null
        Set-Content (Join-Path $archiveRoot 'uv.exe') 'uv'
        Set-Content (Join-Path $archiveRoot 'uvx.exe') 'uvx'
        Compress-Archive -Path (Join-Path $archiveRoot '*') -DestinationPath $archive

        $result = Expand-UvArchive -Archive $archive -DestinationPath $destination `
            -Target 'x86_64-pc-windows-msvc'

        $result | Should -Be $destination
        Join-Path $result 'uv.exe' | Should -Exist
        Join-Path $result 'uvx.exe' | Should -Exist
    }

    It 'extracts a real Unix tar archive' -Skip:(-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        $target = 'x86_64-unknown-linux-gnu'
        $archiveRoot = Join-Path $TestDrive 'tar-root'
        $sourceDir = Join-Path $archiveRoot "uv-$target"
        $destination = Join-Path $TestDrive 'tar-destination'
        $archive = Join-Path $TestDrive 'uv.tar.gz'
        New-Item -ItemType Directory -Path $sourceDir, $destination | Out-Null
        Set-Content (Join-Path $sourceDir 'uv') 'uv'
        Set-Content (Join-Path $sourceDir 'uvx') 'uvx'
        & tar -czf $archive -C $archiveRoot "uv-$target"
        $LASTEXITCODE | Should -Be 0

        $result = Expand-UvArchive -Archive $archive -DestinationPath $destination -Target $target

        $result | Should -Be (Join-Path $destination "uv-$target")
        Join-Path $result 'uv' | Should -Exist
        Join-Path $result 'uvx' | Should -Exist
    }

    It 'throws when a Unix archive cannot be extracted' -Skip:(-not (Get-Command tar -ErrorAction SilentlyContinue)) {
        $archive = Join-Path $TestDrive 'invalid.tar.gz'
        Set-Content $archive 'not an archive'

        {
            Expand-UvArchive -Archive $archive -DestinationPath $TestDrive `
                -Target 'x86_64-unknown-linux-gnu'
        } | Should -Throw '*Failed to extract uv archive*'
    }

    It 'throws and installs nothing when the archive lacks uvx' {
        Mock Invoke-WebRequest {
            param($Uri, $OutFile, $UseBasicParsing)
            $null = $Uri
            $null = $UseBasicParsing
            Set-Content $OutFile 'archive'
        }
        Mock Get-FileHash { @{ Hash = 'abc123' } }
        Mock Expand-UvArchive {
            param($Archive, $DestinationPath, $Target)
            $null = $Archive
            $null = $Target
            Set-Content (Join-Path $DestinationPath 'uv') 'uv'
            return $DestinationPath
        }
        $binDir = Join-Path $TestDrive 'missing-uvx-bin'

        {
            Install-Uv -Version '1.0.0' -Digests @{ 'x86_64-unknown-linux-gnu' = 'abc123' } `
                -Target 'x86_64-unknown-linux-gnu' -BinDir $binDir
        } | Should -Throw '*does not contain both uv and uvx*'
        Join-Path $binDir 'uv' | Should -Not -Exist
    }

    It 'skips installation when the active uv matches the pin' {
        Mock Get-UvInstallation { [pscustomobject]@{ Path = '/tools/uv'; Version = '1.0.0' } }
        Mock Install-Uv {}
        Mock Write-Info {}

        Initialize-Uv -Version '1.0.0' -Digests @{}

        Should -Invoke Install-Uv -Times 0 -Exactly
    }

    It 'installs a mismatched uv and verifies the activated version' {
        $script:UvLookupCount = 0
        Mock Get-UvInstallation {
            $script:UvLookupCount++
            if ($script:UvLookupCount -eq 1) {
                return [pscustomobject]@{ Path = '/old/uv'; Version = '0.9.0' }
            }
            return [pscustomobject]@{ Path = '/new/uv'; Version = '1.0.0' }
        }
        Mock Install-Uv {}
        Mock Write-Info {}
        Mock Write-Warn {}

        Initialize-Uv -Version '1.0.0' -Digests @{}

        Should -Invoke Install-Uv -Times 1 -Exactly
    }

    It 'throws when the pinned uv is not active after installation' {
        Mock Get-UvInstallation { [pscustomobject]@{ Path = '/old/uv'; Version = '0.9.0' } }
        Mock Install-Uv {}
        Mock Write-Info {}
        Mock Write-Warn {}

        {
            Initialize-Uv -Version '1.0.0' -Digests @{}
        } | Should -Throw '*Failed to activate pinned uv 1.0.0*'
    }

    It 'keeps uv versions and Linux digests consistent across consumers' {
        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $powerShellContent = Get-Content (Join-Path $repoRoot 'setup-dev.ps1') -Raw
        $shellContent = Get-Content (Join-Path $repoRoot 'setup-dev.sh') -Raw

        $powerShellVersionMatch = [regex]::Match($powerShellContent, "\`$UvVersion = '([^']+)'")
        $powerShellVersionMatch.Success | Should -BeTrue
        $powerShellVersion = $powerShellVersionMatch.Groups[1].Value
        $powerShellLinuxDigest = [regex]::Match(
            $powerShellContent,
            "'x86_64-unknown-linux-gnu'\s*=\s*'([^']+)'"
        )
        $powerShellLinuxDigest.Success | Should -BeTrue

        $linuxConsumers = @(
            @{
                File = 'setup-dev.sh'
                DigestPattern = '(?m)^\s*Linux-x86_64\).*UV_SHA256="([^"]+)"'
            }
            @{
                File = 'training/rl/scripts/setup_isaac_runtime.sh'
                DigestPattern = 'UV_SHA256="([^"]+)"'
            }
            @{
                File = 'infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh'
                DigestPattern = 'UV_SHA256="([^"]+)"'
            }
            @{
                File = 'shared/ci/smoke-import.sh'
                DigestPattern = 'UV_SHA256="([^"]+)"'
            }
        )
        foreach ($consumer in $linuxConsumers) {
            $content = Get-Content (Join-Path $repoRoot $consumer.File) -Raw
            $versionMatch = [regex]::Match($content, 'UV_VERSION="([^"]+)"')
            $digestMatch = [regex]::Match($content, $consumer.DigestPattern)
            $versionMatch.Success | Should -BeTrue -Because "$($consumer.File) must declare the uv version"
            $digestMatch.Success | Should -BeTrue -Because "$($consumer.File) must declare the uv digest"
            $versionMatch.Groups[1].Value | Should -Be $powerShellVersion
            $digestMatch.Groups[1].Value | Should -Be $powerShellLinuxDigest.Groups[1].Value
        }

        $powerShellArmDigest = [regex]::Match(
            $powerShellContent,
            "'aarch64-unknown-linux-gnu'\s*=\s*'([^']+)'"
        )
        $shellArmDigest = [regex]::Match(
            $shellContent,
            '(?m)^\s*Linux-aarch64\|Linux-arm64\).*UV_SHA256="([^"]+)"'
        )
        $powerShellArmDigest.Success | Should -BeTrue
        $shellArmDigest.Success | Should -BeTrue
        $shellArmDigest.Groups[1].Value | Should -Be $powerShellArmDigest.Groups[1].Value
    }

    It 'declares a digest for every supported PowerShell target' {
        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $powerShellContent = Get-Content (Join-Path $repoRoot 'setup-dev.ps1') -Raw
        $targets = @(
            'aarch64-apple-darwin',
            'aarch64-pc-windows-msvc',
            'aarch64-unknown-linux-gnu',
            'x86_64-apple-darwin',
            'x86_64-pc-windows-msvc',
            'x86_64-unknown-linux-gnu'
        )

        foreach ($target in $targets) {
            $powerShellContent | Should -Match "'$([regex]::Escape($target))'\s*=\s*'[0-9a-f]{64}'"
        }
    }

    It 'installs shell uv into the user bin directory' {
        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $shellContent = Get-Content (Join-Path $repoRoot 'setup-dev.sh') -Raw

        $shellContent | Should -Match 'UV_BIN_DIR="\$\{HOME\}/\.local/bin"'
        $shellContent | Should -Match 'export PATH="\$\{UV_BIN_DIR\}:\$\{PATH\}"'
    }

    It 'replaces a mismatched shell uv and activates the pin' -Skip:(-not (Get-Command bash -ErrorAction SilentlyContinue)) {
        $snippet = Join-Path $TestDrive 'uv-setup.sh'
        New-UvShellSnippet -Destination $snippet
        $installBody = @'
dest="${@: -1}"
mkdir -p "$(dirname "$dest")"
if [[ "$dest" == */uv ]]; then
  printf '#!/usr/bin/env bash\necho "uv 0.12.5"\n' > "$dest"
else
  printf '#!/usr/bin/env bash\nexit 0\n' > "$dest"
fi
chmod +x "$dest"
'@
        $result = Invoke-BashEntryScript -ScriptPath $snippet -WorkDir (Join-Path $TestDrive 'shell-success') `
            -EnvVars @{ HOME = (Join-Path $TestDrive 'shell-home') } -Stubs @{
            uname = 'if [[ "$1" == "-s" ]]; then echo Darwin; else echo arm64; fi'
            uv = 'echo "uv 0.9.0"'
            curl = 'exit 0'
            tar = 'exit 0'
            install = $installBody
        }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        @($result.Calls | Where-Object {
                $_ -like 'curl -LsSf *uv-aarch64-apple-darwin.tar.gz -o *'
            }).Count | Should -Be 1
        @($result.Calls | Where-Object { $_ -like 'install -m 0755*' }).Count | Should -Be 2
        $result.StdOut | Should -Match 'Using uv: uv 0.12.5'
    }

    It 'fails when the shell uv pin is not active after installation' -Skip:(-not (Get-Command bash -ErrorAction SilentlyContinue)) {
        $snippet = Join-Path $TestDrive 'uv-setup-failure.sh'
        New-UvShellSnippet -Destination $snippet
        $installBody = @'
dest="${@: -1}"
mkdir -p "$(dirname "$dest")"
printf '#!/usr/bin/env bash\necho "uv 0.9.0"\n' > "$dest"
chmod +x "$dest"
'@
        $result = Invoke-BashEntryScript -ScriptPath $snippet -WorkDir (Join-Path $TestDrive 'shell-failure') `
            -EnvVars @{ HOME = (Join-Path $TestDrive 'shell-failure-home') } -Stubs @{
            uname = 'if [[ "$1" == "-s" ]]; then echo Linux; else echo x86_64; fi'
            uv = 'echo "uv 0.9.0"'
            curl = 'exit 0'
            tar = 'exit 0'
            install = $installBody
        }

        $result.ExitCode | Should -Be 1 -Because $result.StdErr
        $result.StdErr | Should -Match 'Failed to activate pinned uv 0.12.5; found 0.9.0'
    }
}
