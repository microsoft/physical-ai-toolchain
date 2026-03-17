#Requires -Modules Pester
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
<#
.SYNOPSIS
    Pester tests for Link-Lang-Check.ps1 script
.DESCRIPTION
    Tests for language path link checker functions:
    - Get-GitTextFile
    - Find-LinksInFile
    - Repair-LinksInFile
    - Repair-AllLink
    - ConvertTo-JsonOutput
#>

BeforeAll {
    $script:ScriptPath = Join-Path $PSScriptRoot '../../linting/Link-Lang-Check.ps1'
    . $script:ScriptPath

    $script:FixtureDir = Join-Path $PSScriptRoot '../Fixtures/Linting'
    $script:EnUs = 'en' + '-us'
}

AfterAll {
}

#region Get-GitTextFile Tests

Describe 'Get-GitTextFile' -Tag 'Unit' {
    Context 'Git command succeeds' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return @('file1.md', 'file2.ps1', 'subdir/file3.txt')
            } -ParameterFilter { $args -contains '--name-only' }
        }

        It 'Returns array of file paths' {
            $result = Get-GitTextFile
            $result | Should -BeOfType [string]
            $result.Count | Should -Be 3
        }

        It 'Includes all returned files' {
            $result = Get-GitTextFile
            $result | Should -Contain 'file1.md'
            $result | Should -Contain 'file2.ps1'
            $result | Should -Contain 'subdir/file3.txt'
        }
    }

    Context 'Git command fails' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 128
                return 'fatal: not a git repository'
            } -ParameterFilter { $args -contains '--name-only' }

            Mock Write-Error {}
        }

        It 'Returns empty array on git error' {
            $result = Get-GitTextFile
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Empty repository' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return @()
            } -ParameterFilter { $args -contains '--name-only' }
        }

        It 'Returns empty array for empty repo' {
            $result = Get-GitTextFile
            $result | Should -BeNullOrEmpty
        }
    }
}

#endregion

#region Find-LinksInFile Tests

Describe 'Find-LinksInFile' -Tag 'Unit' {
    BeforeAll {
        $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $script:TempDir -Force | Out-Null
    }

    AfterAll {
        Remove-Item -Path $script:TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Context 'File with en-us links' {
        BeforeEach {
            $script:TestFile = Join-Path $script:TempDir 'test-links.md'
            @"
# Test Document

Visit https://docs.microsoft.com/$($script:EnUs)/azure for Azure docs.
Also see https://learn.microsoft.com/$($script:EnUs)/dotnet/api for .NET API.
"@ | Set-Content -Path $script:TestFile
        }

        It 'Finds all en-us links' {
            $result = Find-LinksInFile -FilePath $script:TestFile
            $result.Count | Should -Be 2
        }

        It 'Returns correct file path' {
            $result = Find-LinksInFile -FilePath $script:TestFile
            $result[0].File | Should -Be $script:TestFile
        }

        It 'Returns correct line numbers' {
            $result = Find-LinksInFile -FilePath $script:TestFile
            $result[0].LineNumber | Should -Be 3
            $result[1].LineNumber | Should -Be 4
        }

        It 'Provides fixed URL without en-us' {
            $result = Find-LinksInFile -FilePath $script:TestFile
            $result[0].FixedUrl | Should -Not -Match 'en-us/'
            $result[0].FixedUrl | Should -Be 'https://docs.microsoft.com/azure'
        }
    }

    Context 'File without en-us links' {
        BeforeEach {
            $script:CleanFile = Join-Path $script:TempDir 'clean-links.md'
            @'
# Clean Document

Visit https://docs.microsoft.com/azure for docs.
'@ | Set-Content -Path $script:CleanFile
        }

        It 'Returns empty array when no en-us links found' {
            $result = Find-LinksInFile -FilePath $script:CleanFile
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Nonexistent file' {
        It 'Returns empty array for nonexistent file' {
            $result = Find-LinksInFile -FilePath '/nonexistent/file.md'
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Multiple links on same line' {
        BeforeEach {
            $script:MultiLinkFile = Join-Path $script:TempDir 'multi-links.md'
            "See https://docs.microsoft.com/$($script:EnUs)/a and https://docs.microsoft.com/$($script:EnUs)/b here." |
                Set-Content -Path $script:MultiLinkFile
        }

        It 'Finds all links on same line' {
            $result = Find-LinksInFile -FilePath $script:MultiLinkFile
            $result.Count | Should -Be 2
            $result[0].LineNumber | Should -Be 1
            $result[1].LineNumber | Should -Be 1
        }
    }
}

#endregion

#region Repair-LinksInFile Tests

Describe 'Repair-LinksInFile' -Tag 'Unit' {
    BeforeAll {
        $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $script:TempDir -Force | Out-Null
    }

    AfterAll {
        Remove-Item -Path $script:TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Context 'File with links to repair' {
        BeforeEach {
            $script:RepairFile = Join-Path $script:TempDir 'repair-test.md'
            "Visit https://docs.microsoft.com/$($script:EnUs)/azure for docs." |
                Set-Content -Path $script:RepairFile

            $script:Links = @(
                [PSCustomObject]@{
                    OriginalUrl = "https://docs.microsoft.com/$($script:EnUs)/azure"
                    FixedUrl    = 'https://docs.microsoft.com/azure'
                }
            )
        }

        It 'Returns true when file is modified' {
            $result = Repair-LinksInFile -FilePath $script:RepairFile -Links $script:Links
            $result | Should -BeTrue
        }

        It 'Replaces en-us in file content' {
            Repair-LinksInFile -FilePath $script:RepairFile -Links $script:Links
            $content = Get-Content -Path $script:RepairFile -Raw
            $content | Should -Not -Match 'en-us/'
            $content | Should -Match 'https://docs.microsoft.com/azure'
        }
    }

    Context 'File with no changes needed' {
        BeforeEach {
            $script:NoChangeFile = Join-Path $script:TempDir 'no-change.md'
            'Visit https://docs.microsoft.com/azure for docs.' |
                Set-Content -Path $script:NoChangeFile

            $script:NoMatchLinks = @(
                [PSCustomObject]@{
                    OriginalUrl = "https://example.com/$($script:EnUs)/page"
                    FixedUrl    = 'https://example.com/page'
                }
            )
        }

        It 'Returns false when no changes made' {
            $result = Repair-LinksInFile -FilePath $script:NoChangeFile -Links $script:NoMatchLinks
            $result | Should -BeFalse
        }
    }

    Context 'Nonexistent file' {
        It 'Returns false for nonexistent file' {
            $links = @([PSCustomObject]@{ OriginalUrl = 'a'; FixedUrl = 'b' })
            $result = Repair-LinksInFile -FilePath '/nonexistent/file.md' -Links $links
            $result | Should -BeFalse
        }
    }
}

#endregion

#region Repair-AllLink Tests

Describe 'Repair-AllLink' -Tag 'Unit' {
    BeforeAll {
        $script:TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $script:TempDir -Force | Out-Null
    }

    AfterAll {
        Remove-Item -Path $script:TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Context 'Multiple files with links' {
        BeforeEach {
            $script:File1 = Join-Path $script:TempDir 'file1.md'
            $script:File2 = Join-Path $script:TempDir 'file2.md'

            "Link: https://docs.microsoft.com/$($script:EnUs)/a" | Set-Content -Path $script:File1
            "Link: https://docs.microsoft.com/$($script:EnUs)/b" | Set-Content -Path $script:File2

            $script:AllLinks = @(
                [PSCustomObject]@{
                    File        = $script:File1
                    LineNumber  = 1
                    OriginalUrl = "https://docs.microsoft.com/$($script:EnUs)/a"
                    FixedUrl    = 'https://docs.microsoft.com/a'
                },
                [PSCustomObject]@{
                    File        = $script:File2
                    LineNumber  = 1
                    OriginalUrl = "https://docs.microsoft.com/$($script:EnUs)/b"
                    FixedUrl    = 'https://docs.microsoft.com/b'
                }
            )
        }

        It 'Returns count of modified files' {
            $result = Repair-AllLink -AllLinks $script:AllLinks
            $result | Should -Be 2
        }

        It 'Modifies all files' {
            Repair-AllLink -AllLinks $script:AllLinks
            (Get-Content $script:File1 -Raw) | Should -Not -Match 'en-us/'
            (Get-Content $script:File2 -Raw) | Should -Not -Match 'en-us/'
        }
    }

    Context 'Empty links array' {
        It 'Returns zero for empty input' {
            $result = Repair-AllLink -AllLinks @()
            $result | Should -Be 0
        }
    }
}

#endregion

#region ConvertTo-JsonOutput Tests

Describe 'ConvertTo-JsonOutput' -Tag 'Unit' {
    Context 'Valid link objects' {
        BeforeEach {
            $script:Links = @(
                [PSCustomObject]@{
                    File        = 'test.md'
                    LineNumber  = 5
                    OriginalUrl = "https://example.com/$($script:EnUs)/page"
                    FixedUrl    = 'https://example.com/page'
                }
            )
        }

        It 'Returns array of objects' {
            $result = ConvertTo-JsonOutput -Links $script:Links
            $result | Should -BeOfType [PSCustomObject]
        }

        It 'Uses snake_case property names' {
            $result = ConvertTo-JsonOutput -Links $script:Links
            $result[0].PSObject.Properties.Name | Should -Contain 'file'
            $result[0].PSObject.Properties.Name | Should -Contain 'line_number'
            $result[0].PSObject.Properties.Name | Should -Contain 'original_url'
        }

        It 'Excludes FixedUrl from output' {
            $result = ConvertTo-JsonOutput -Links $script:Links
            $result[0].PSObject.Properties.Name | Should -Not -Contain 'FixedUrl'
            $result[0].PSObject.Properties.Name | Should -Not -Contain 'fixed_url'
        }

        It 'Preserves values correctly' {
            $result = ConvertTo-JsonOutput -Links $script:Links
            $result[0].file | Should -Be 'test.md'
            $result[0].line_number | Should -Be 5
            $result[0].original_url | Should -Be "https://example.com/$($script:EnUs)/page"
        }
    }

    Context 'Empty input' {
        It 'Returns empty array for empty input' {
            $result = ConvertTo-JsonOutput -Links @()
            $result | Should -BeNullOrEmpty
        }
    }
}

#endregion

#region Invoke-LinkLanguageCheck Tests

Describe 'Invoke-LinkLanguageCheck' -Tag 'Unit' {
    BeforeAll {
        Mock Get-GitTextFile { return @('file1.md', 'file2.md') }
        Mock Test-Path { return $true } -ParameterFilter { $PathType -eq 'Leaf' }
    }

    Context 'No links found' {
        BeforeAll {
            Mock Find-LinksInFile { return @() }
        }

        It 'Outputs empty JSON array when -Fix is not set' {
            $result = Invoke-LinkLanguageCheck
            $result | Should -Be '[]'
        }

        It 'Outputs no-links message when -Fix is set' {
            $result = Invoke-LinkLanguageCheck -Fix
            $result | Should -Be "No URLs containing 'en-us' were found."
        }
    }

    Context 'Links found with -Fix' {
        BeforeAll {
            $script:mockLinks = @(
                @{ File = 'file1.md'; LineNumber = 5; OriginalUrl = "https://learn.microsoft.com/$($script:EnUs)/docs"; FixedUrl = 'https://learn.microsoft.com/docs' }
            )
            Mock Find-LinksInFile { return $script:mockLinks }
            Mock Repair-AllLink { return 1 }
        }

        It 'Calls Repair-AllLink and reports fix count' {
            $result = Invoke-LinkLanguageCheck -Fix
            $result | Should -BeLike 'Fixed * URLs in 1 files*'
            Should -Invoke Repair-AllLink -Times 1
        }
    }

    Context 'Links found without -Fix' {
        BeforeAll {
            $script:mockLinks = @(
                @{ File = 'file1.md'; LineNumber = 5; OriginalUrl = "https://learn.microsoft.com/$($script:EnUs)/docs"; FixedUrl = 'https://learn.microsoft.com/docs' }
            )
            Mock Find-LinksInFile { return $script:mockLinks }
            Mock ConvertTo-JsonOutput { return @(@{ File = 'file1.md'; Line = 5 }) }
        }

        It 'Outputs JSON via ConvertTo-JsonOutput' {
            $result = Invoke-LinkLanguageCheck
            $result | Should -Not -BeNullOrEmpty
            Should -Invoke ConvertTo-JsonOutput -Times 1
        }
    }

    Context 'Files parameter' {
        BeforeAll {
            Mock Find-LinksInFile { return @() }
        }

        It 'Uses provided Files instead of Get-GitTextFile' {
            Invoke-LinkLanguageCheck -Files @('src/file.md')
            Should -Invoke Get-GitTextFile -Times 0
            Should -Invoke Find-LinksInFile -Times 1
        }
    }

    Context 'Non-file paths are skipped' {
        BeforeAll {
            Mock Get-GitTextFile { return @('not-a-file') }
            Mock Test-Path { return $false } -ParameterFilter { $PathType -eq 'Leaf' }
            Mock Find-LinksInFile { return @() }
        }

        It 'Does not call Find-LinksInFile for non-files' {
            Invoke-LinkLanguageCheck
            Should -Invoke Find-LinksInFile -Times 0
        }
    }
}

#endregion
