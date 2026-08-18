# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $PSScriptRoot '../../setup-dev.ps1'),
        [ref]$tokens,
        [ref]$errors
    )
    $functionNames = @('Write-Warn', 'Get-UvTarget', 'Expand-UvArchive', 'Install-Uv')
    $functions = $ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -in $functionNames
        }, $true)
    foreach ($function in $functions) {
        . ([scriptblock]::Create($function.Extent.Text))
    }
}

Describe 'setup-dev uv installation' -Tag 'Unit' {
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
        Should -Invoke Invoke-WebRequest -Times 0
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
        $originalPath = $env:PATH
        $expectedUrl = "https://github.com/astral-sh/uv/releases/download/1.0.0/uv-$Target.$Extension"

        try {
            Install-Uv -Version '1.0.0' -Digests @{ $Target = 'abc123' } -Target $Target -BinDir $binDir

            Test-Path (Join-Path $binDir $UvExecutable) | Should -BeTrue
            Test-Path (Join-Path $binDir $UvxExecutable) | Should -BeTrue
            $env:PATH.StartsWith("$binDir$([System.IO.Path]::PathSeparator)") | Should -BeTrue
            Should -Invoke Invoke-WebRequest -Times 1 -ParameterFilter {
                $Uri -eq $expectedUrl
            }
        }
        finally {
            $env:PATH = $originalPath
        }
    }

    It 'keeps the shell and PowerShell Linux uv pins consistent' {
        $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $powerShellContent = Get-Content (Join-Path $repoRoot 'setup-dev.ps1') -Raw
        $shellContent = Get-Content (Join-Path $repoRoot 'setup-dev.sh') -Raw

        $powerShellVersionMatch = [regex]::Match($powerShellContent, "\`$UvVersion = '([^']+)'")
        $shellVersionMatch = [regex]::Match($shellContent, 'UV_VERSION="([^"]+)"')
        $powerShellVersionMatch.Success | Should -BeTrue
        $shellVersionMatch.Success | Should -BeTrue
        $powerShellVersion = $powerShellVersionMatch.Groups[1].Value
        $shellVersion = $shellVersionMatch.Groups[1].Value
        $powerShellVersion | Should -Be $shellVersion

        foreach ($target in @('x86_64-unknown-linux-gnu', 'aarch64-unknown-linux-gnu')) {
            $powerShellDigestMatch = [regex]::Match($powerShellContent, "'$target'\s*=\s*'([^']+)'")
            $architecture = if ($target.StartsWith('x86_64')) { 'x86_64' } else { 'aarch64' }
            $shellDigestMatch = [regex]::Match(
                $shellContent,
                "(?m)^\s*$architecture\).*UV_SHA256=`"([^`"]+)`""
            )
            $powerShellDigestMatch.Success | Should -BeTrue
            $shellDigestMatch.Success | Should -BeTrue
            $powerShellDigestMatch.Groups[1].Value | Should -Be $shellDigestMatch.Groups[1].Value
        }
    }
}
