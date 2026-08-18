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
    $functionNames = @('Write-Warn', 'Get-UvTarget', 'Install-Uv')
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
}
