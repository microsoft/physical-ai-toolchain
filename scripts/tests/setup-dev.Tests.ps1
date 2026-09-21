#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore pscustomobject

Describe 'setup-dev uv bootstrap' -Tag 'Unit' {
    BeforeAll {
        $script:SetupDevPath = Join-Path $PSScriptRoot '../../setup-dev.ps1'
        $tokens = $null
        $parseErrors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:SetupDevPath,
            [ref]$tokens,
            [ref]$parseErrors
        )
        $parseErrors.Count | Should -Be 0
        $functionAst = $ast.Find({
                param($node)
                $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Install-Uv'
            }, $false)
        . ([scriptblock]::Create($functionAst.Extent.Text))

        function Write-Info {
            param([string]$Message)
            $null = $Message
        }

        function Invoke-VerifiedDownload {
            param(
                [string]$Url,
                [string]$DestinationDirectory,
                [string]$FileName,
                [string]$ExpectedHash
            )
            $null = $Url, $DestinationDirectory, $FileName, $ExpectedHash
        }

        function uv {
            'uv 0.11.21'
        }
    }

    BeforeEach {
        $script:OriginalPath = $env:PATH
        $script:OriginalUserProfile = $env:USERPROFILE
        $script:InstallerDirectory = $null
    }

    AfterEach {
        $env:PATH = $script:OriginalPath
        $env:USERPROFILE = $script:OriginalUserProfile
    }

    It 'Does not execute the installer and removes temporary files when verification fails' {
        $marker = Join-Path $TestDrive 'executed'
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            "Set-Content -LiteralPath '$marker' -Value executed" |
                Set-Content -LiteralPath (Join-Path $DestinationDirectory $FileName)
            throw 'hash mismatch'
        }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $false
        } | Should -Throw 'hash mismatch'

        Test-Path -LiteralPath $marker | Should -BeFalse
        Test-Path -LiteralPath $script:InstallerDirectory | Should -BeFalse
    }

    It 'Executes only the verified installer path and removes temporary files' {
        $marker = Join-Path $TestDrive 'executed'
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            "Set-Content -LiteralPath '$marker' -Value executed" | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }
        Install-Uv `
            -Version '0.11.21' `
            -ExpectedHash ('a' * 64) `
            -IsWindowsPlatform $false

        Test-Path -LiteralPath $marker | Should -BeTrue
        Test-Path -LiteralPath $script:InstallerDirectory | Should -BeFalse
        Should -Invoke Invoke-VerifiedDownload -Exactly 1 -ParameterFilter {
            $Url -eq 'https://astral.sh/uv/0.11.21/install.ps1' -and
            $ExpectedHash -eq ('a' * 64)
        }
    }

    It 'Adds the Windows uv installation directories before command validation' {
        $env:USERPROFILE = 'C:\Users\tester'
        $script:ValidationPath = $null
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 0' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }
        Mock Get-Command {
            $script:ValidationPath = $env:PATH
            [pscustomobject]@{ Name = 'uv' }
        } -ParameterFilter {
            $Name -eq 'uv'
        }
        Install-Uv `
            -Version '0.11.21' `
            -ExpectedHash ('a' * 64) `
            -IsWindowsPlatform $true

        $script:ValidationPath.StartsWith(
            'C:\Users\tester\.local\bin;C:\Users\tester\.cargo\bin;'
        ) | Should -BeTrue
    }

    It 'Rejects a missing Windows user profile before downloading' {
        $env:USERPROFILE = ''
        Mock Invoke-VerifiedDownload {
            throw 'download should not run'
        }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $true
        } | Should -Throw 'USERPROFILE is required to locate the Windows uv installation'

        Should -Invoke Invoke-VerifiedDownload -Exactly 0
    }

    It 'Restricts the installer directory before downloading' {
        $script:InstallerMode = $null
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $script:InstallerMode = [System.IO.File]::GetUnixFileMode($DestinationDirectory)
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 0' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }

        Install-Uv `
            -Version '0.11.21' `
            -ExpectedHash ('a' * 64) `
            -IsWindowsPlatform $false

        [int]$script:InstallerMode | Should -Be 448
    }

    It 'Adds the POSIX uv installation directories before command validation' {
        $script:ValidationPath = $null
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 0' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }
        Mock Get-Command {
            $script:ValidationPath = $env:PATH
            [pscustomobject]@{ Name = 'uv' }
        } -ParameterFilter {
            $Name -eq 'uv'
        }

        Install-Uv `
            -Version '0.11.21' `
            -ExpectedHash ('a' * 64) `
            -IsWindowsPlatform $false

        $script:ValidationPath.StartsWith("$HOME/.local/bin:$HOME/.cargo/bin:") | Should -BeTrue
    }

    It 'Does not download when installer directory hardening fails' {
        Mock chmod {
            $global:LASTEXITCODE = 1
        }
        Mock Invoke-VerifiedDownload {
            throw 'download should not run'
        }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $false
        } | Should -Throw 'Failed to restrict uv installer directory permissions'

        Should -Invoke Invoke-VerifiedDownload -Exactly 0
    }

    It 'Fails when the verified installer returns a nonzero exit code' {
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 17' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $false
        } | Should -Throw 'Failed to install uv v0.11.21'

        Test-Path -LiteralPath $script:InstallerDirectory | Should -BeFalse
    }

    It 'Fails when the installer succeeds without making uv available' {
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 0' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }
        Mock Get-Command { [pscustomobject]@{ Name = 'Invoke-VerifiedDownload' } } -ParameterFilter {
            $Name -eq 'Invoke-VerifiedDownload'
        }
        Mock Get-Command { $null } -ParameterFilter {
            $Name -eq 'uv'
        }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $false
        } | Should -Throw 'Failed to install uv v0.11.21'

        Test-Path -LiteralPath $script:InstallerDirectory | Should -BeFalse
    }

    It 'Fails when the installed uv version does not match the requested version' {
        Mock Invoke-VerifiedDownload {
            $script:InstallerDirectory = $DestinationDirectory
            $path = Join-Path $DestinationDirectory 'verified.ps1'
            '$global:LASTEXITCODE = 0' | Set-Content -LiteralPath $path
            [pscustomobject]@{ Path = $path }
        }
        Mock uv { 'uv 0.10.0' }

        {
            Install-Uv `
                -Version '0.11.21' `
                -ExpectedHash ('a' * 64) `
                -IsWindowsPlatform $false
        } | Should -Throw 'Failed to install uv v0.11.21'
    }
}

$script:GitBashPath = Join-Path $env:ProgramFiles 'Git/bin/bash.exe'
$script:BashAvailable = (Test-Path -LiteralPath $script:GitBashPath) -or
    $null -ne (Get-Command bash -ErrorAction SilentlyContinue)

Describe 'Bash launcher accessibility contracts' -Tag 'Unit' {
    BeforeAll {
        $script:RepositoryRoot = Resolve-Path (Join-Path $PSScriptRoot '../..')
        $gitBash = if ($env:ProgramFiles) {
            Join-Path $env:ProgramFiles 'Git/bin/bash.exe'
        }
        else {
            $null
        }
        $script:BashExecutable = if ($gitBash -and (Test-Path -LiteralPath $gitBash)) {
            $gitBash
        }
        else {
            'bash'
        }
        function ConvertTo-BashPath {
            param([string]$Path)

            $normalized = $Path.Replace('\', '/')
            if ($script:BashExecutable -ne 'bash') {
                return $normalized
            }
            if ($normalized -match '^([A-Za-z]):/(.*)$') {
                return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2])"
            }
            return $normalized
        }

        $script:SetupDevBashPath = ConvertTo-BashPath (Join-Path $script:RepositoryRoot 'setup-dev.sh')
        $script:ViewerStartPath = ConvertTo-BashPath (
            Join-Path $script:RepositoryRoot 'data-management/viewer/start.sh'
        )
    }

    It 'Provides setup help without requiring deployment tools' -Skip:(-not $script:BashAvailable) {
        $output = & $script:BashExecutable $script:SetupDevBashPath --help 2>&1

        $LASTEXITCODE | Should -Be 0
        $output -join "`n" | Should -Match 'Usage:'
        $output -join "`n" | Should -Match '--config-preview'
    }

    It 'Previews setup configuration without mutation' -Skip:(-not $script:BashAvailable) {
        $output = & $script:BashExecutable $script:SetupDevBashPath --config-preview 2>&1

        $LASTEXITCODE | Should -Be 0
        $output -join "`n" | Should -Match 'Configuration Preview'
        $output -join "`n" | Should -Match 'Mutation.*None'
    }

    It 'Previews Viewer configuration without ANSI output when NO_COLOR is present' -Skip:(-not $script:BashAvailable) {
        $originalNoColor = $env:NO_COLOR
        try {
            $env:NO_COLOR = '1'
            $output = & $script:BashExecutable $script:ViewerStartPath --config-preview 2>&1
        }
        finally {
            $env:NO_COLOR = $originalNoColor
        }

        $LASTEXITCODE | Should -Be 0
        $text = $output -join "`n"
        $text | Should -Match 'Configuration Preview'
        $text | Should -Match 'Mutation.*None'
        $text | Should -Not -Match ([regex]::Escape([char]27))
    }

    It 'Rejects an unknown Viewer option with visible recovery guidance' -Skip:(-not $script:BashAvailable) {
        $output = & $script:BashExecutable $script:ViewerStartPath --not-a-real-option 2>&1

        $LASTEXITCODE | Should -Not -Be 0
        $output -join "`n" | Should -Match 'Unknown option.*--not-a-real-option'
        $output -join "`n" | Should -Match 'Usage:'
    }
}
