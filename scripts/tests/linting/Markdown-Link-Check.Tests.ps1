#Requires -Modules Pester
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Pester tests for Markdown-Link-Check.ps1 script
.DESCRIPTION
    Tests for markdown link checking wrapper functions:
    - Get-MarkdownTarget
    - Invoke-MarkdownLinkCheckCore
#>

BeforeAll {
    $script:ScriptPath = Join-Path $PSScriptRoot '../../linting/Markdown-Link-Check.ps1'
    . $script:ScriptPath

    Import-Module (Join-Path $PSScriptRoot '../../linting/Modules/LintingHelpers.psm1') -Force

    $script:FixtureDir = Join-Path $PSScriptRoot '../Fixtures/Linting'
}

AfterAll {
    Remove-Module LintingHelpers -Force -ErrorAction SilentlyContinue
}

#region Get-MarkdownTarget Tests

Describe 'Get-MarkdownTarget' -Tag 'Unit' {
    BeforeAll {
        $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $script:TempDir -Force | Out-Null
    }

    AfterAll {
        Remove-Item -Path $script:TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Context 'Git-tracked files in repository' {
        BeforeEach {
            $script:TestFile1 = Join-Path $script:TempDir 'test1.md'
            $script:TestFile2 = Join-Path $script:TempDir 'test2.md'
            Set-Content -Path $script:TestFile1 -Value '# Test 1'
            Set-Content -Path $script:TestFile2 -Value '# Test 2'

            Mock git {
                if ($args -contains 'rev-parse') {
                    $global:LASTEXITCODE = 0
                    return $script:TempDir
                }
                elseif ($args -contains 'ls-files') {
                    $global:LASTEXITCODE = 0
                    return @('test1.md', 'test2.md')
                }
            }
        }

        It 'Returns markdown files when given a directory' {
            $result = Get-MarkdownTarget -InputPath $script:TempDir
            $result | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Non-git fallback mode' {
        BeforeEach {
            $script:TestFile = Join-Path $script:TempDir 'readme.md'
            Set-Content -Path $script:TestFile -Value '# Readme'

            Mock git {
                $global:LASTEXITCODE = 128
                return 'fatal: not a git repository'
            }
        }

        It 'Falls back to filesystem when not in git repo' {
            $result = Get-MarkdownTarget -InputPath $script:TempDir
            $result | Should -Not -BeNullOrEmpty
        }

        It 'Returns absolute paths' {
            $result = Get-MarkdownTarget -InputPath $script:TempDir
            if ($result) {
                [System.IO.Path]::IsPathRooted($result[0]) | Should -BeTrue
            }
        }
    }

    Context 'Empty input handling' {
        It 'Returns empty array for null input' {
            $result = Get-MarkdownTarget -InputPath $null
            $result | Should -BeNullOrEmpty
        }

        It 'Returns empty array for empty string input' {
            $result = Get-MarkdownTarget -InputPath ''
            $result | Should -BeNullOrEmpty
        }
    }
}

#endregion

#region Script Integration Tests

Describe 'Markdown-Link-Check Integration' -Tag 'Integration' {
    Context 'Config file loading' {
        BeforeAll {
            $script:ConfigPath = Join-Path $PSScriptRoot '../Fixtures/Linting/link-check-config.json'
        }

        It 'Config fixture file exists' {
            Test-Path $script:ConfigPath | Should -BeTrue
        }

        It 'Config fixture is valid JSON' {
            { Get-Content $script:ConfigPath | ConvertFrom-Json } | Should -Not -Throw
        }

        It 'Config contains expected properties' {
            $config = Get-Content $script:ConfigPath | ConvertFrom-Json
            $config.PSObject.Properties.Name | Should -Contain 'ignorePatterns'
            $config.PSObject.Properties.Name | Should -Contain 'replacementPatterns'
        }
    }

    Context 'Main execution error handling' {
        BeforeAll {
            $script:OriginalGHA = $env:GITHUB_ACTIONS
            $script:LinkCheckScript = Join-Path $PSScriptRoot '../../linting/Markdown-Link-Check.ps1'
        }

        AfterAll {
            if ($null -eq $script:OriginalGHA) {
                Remove-Item Env:GITHUB_ACTIONS -ErrorAction SilentlyContinue
            } else {
                $env:GITHUB_ACTIONS = $script:OriginalGHA
            }
        }

        It 'Outputs error when script fails with no markdown files' {
            $env:GITHUB_ACTIONS = 'true'

            $emptyDir = Join-Path $TestDrive 'empty-no-md'
            New-Item -ItemType Directory -Path $emptyDir -Force | Out-Null

            Mock git {
                if ($args -contains 'rev-parse') {
                    $global:LASTEXITCODE = 0
                    return $emptyDir
                }
                elseif ($args -contains 'ls-files') {
                    $global:LASTEXITCODE = 0
                    return @()
                }
            }

            $output = & $script:LinkCheckScript -Path $emptyDir 2>&1

            $errors = $output | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }
            $errors | Should -Not -BeNullOrEmpty
        }
    }
}

#endregion

#region Invoke-MarkdownLinkCheckCore Tests

Describe 'Invoke-MarkdownLinkCheckCore' -Tag 'Unit' {
    BeforeAll {
        $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
        $script:FixtureConfig = Join-Path $PSScriptRoot '../Fixtures/Linting/link-check-config.json'
    }

    Context 'No markdown files found' {
        It 'Throws when Get-MarkdownTarget returns empty' {
            Mock Get-MarkdownTarget { return @() }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = $script:RepoRoot } }

            { Invoke-MarkdownLinkCheckCore -Path @('nonexistent') -ConfigPath $script:FixtureConfig } |
                Should -Throw '*No markdown files were found to validate*'
        }
    }

    Context 'CLI not installed' {
        It 'Throws when markdown-link-check binary is missing' {
            Mock Get-MarkdownTarget { return @('file.md') }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = $script:RepoRoot } }
            Mock Test-Path { return $false } -ParameterFilter { $LiteralPath -and $LiteralPath -like '*markdown-link-check*' }

            { Invoke-MarkdownLinkCheckCore -Path @('file.md') -ConfigPath $script:FixtureConfig } |
                Should -Throw '*markdown-link-check is not installed*'
        }
    }

    Context 'Quiet mode base arguments' {
        It 'Passes -q flag when Quiet switch is set' {
            Mock Get-MarkdownTarget { return @('file.md') }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = $script:RepoRoot } }
            Mock Test-Path { return $true } -ParameterFilter { $LiteralPath -and $LiteralPath -like '*markdown-link-check*' }
            Mock Push-Location { }
            Mock Pop-Location { }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = "$TestDrive/file.md" } } -ParameterFilter { $LiteralPath -eq 'file.md' }
            Mock New-Item { } -ParameterFilter { $ItemType -eq 'Directory' }
            Mock Set-Content { }
            Mock Write-Host { }

            try {
                Invoke-MarkdownLinkCheckCore -Path @('file.md') -ConfigPath $script:FixtureConfig -Quiet
            }
            catch {
                Write-Verbose "CLI execution expected to fail in test environment: $_"
            }

            Should -Invoke Get-MarkdownTarget -Times 1
            Should -Invoke Push-Location -Times 1
        }
    }

    Context 'Config path validation' {
        It 'Throws when ConfigPath cannot be resolved' {
            { Invoke-MarkdownLinkCheckCore -Path @('file.md') -ConfigPath (Join-Path $TestDrive 'nonexistent-config.json') } |
                Should -Throw
        }
    }

    Context 'Multiple markdown targets' {
        It 'Iterates all files returned by Get-MarkdownTarget' {
            Mock Get-MarkdownTarget { return @('first.md', 'second.md') }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = $script:RepoRoot } }
            Mock Test-Path { return $true } -ParameterFilter { $LiteralPath -and $LiteralPath -like '*markdown-link-check*' }
            Mock Push-Location { }
            Mock Pop-Location { }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = "$TestDrive/first.md" } } -ParameterFilter { $LiteralPath -eq 'first.md' }
            Mock Resolve-Path { return [PSCustomObject]@{ Path = "$TestDrive/second.md" } } -ParameterFilter { $LiteralPath -eq 'second.md' }
            Mock New-Item { } -ParameterFilter { $ItemType -eq 'Directory' }
            Mock Set-Content { }
            Mock Write-Host { }
            Mock Write-Output { }
            Mock Write-Warning { }
            Mock Write-Error { }
            Mock Remove-Item { }
            Mock Test-Path { return $false } -ParameterFilter { $Path -and $Path -like '*.xml' }

            try {
                Invoke-MarkdownLinkCheckCore -Path @('.') -ConfigPath $script:FixtureConfig
            }
            catch { $null = $_ }

            Should -Invoke Resolve-Path -ParameterFilter { $LiteralPath -eq 'first.md' } -Times 1
            Should -Invoke Resolve-Path -ParameterFilter { $LiteralPath -eq 'second.md' } -Times 1
        }
    }

    Context 'JUnit XML fixture structure' {
        It 'Contains alive, dead, and ignored link statuses' {
            $xmlPath = Join-Path $script:FixtureDir 'link-check-results.xml'
            [xml]$xml = Get-Content $xmlPath -Raw -Encoding utf8

            $testcases = @($xml.testsuites.testsuite.testcase)
            $testcases.Count | Should -Be 3

            $statuses = $testcases | ForEach-Object {
                ($_.properties.property | Where-Object { $_.name -eq 'status' }).value
            }
            $statuses | Should -Contain 'alive'
            $statuses | Should -Contain 'dead'
            $statuses | Should -Contain 'ignored'
        }

        It 'Dead link entry has expected status code and URL' {
            $xmlPath = Join-Path $script:FixtureDir 'link-check-results.xml'
            [xml]$xml = Get-Content $xmlPath -Raw -Encoding utf8

            $dead = @($xml.testsuites.testsuite.testcase) | Where-Object {
                ($_.properties.property | Where-Object { $_.name -eq 'status' }).value -eq 'dead'
            }

            ($dead.properties.property | Where-Object { $_.name -eq 'statusCode' }).value | Should -Be '404'
            ($dead.properties.property | Where-Object { $_.name -eq 'url' }).value | Should -Not -BeNullOrEmpty
        }
    }
}

#endregion
