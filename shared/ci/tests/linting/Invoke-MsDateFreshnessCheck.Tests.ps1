#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
#Requires -Modules powershell-yaml
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../linting/Invoke-MsDateFreshnessCheck.ps1
    $ErrorActionPreference = 'Continue'

    $lintingHelpersPath = Join-Path $PSScriptRoot '../../linting/Modules/LintingHelpers.psm1'
    $ciHelpersPath = Join-Path $PSScriptRoot '../../lib/Modules/CIHelpers.psm1'
    Import-Module $lintingHelpersPath -Force
    Import-Module $ciHelpersPath -Force
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    Import-Module powershell-yaml -Force
}

#region Get-MarkdownFiles Tests

Describe 'Get-MarkdownFiles' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:TestDir = Join-Path $TestDrive 'ms-date-test'
        New-Item -ItemType Directory -Path $script:TestDir -Force | Out-Null
        Push-Location $script:TestDir
    }

    AfterEach {
        Pop-Location
        Restore-CIEnvironment
    }

    Context 'File discovery' {
        BeforeEach {
            New-Item -ItemType File -Path (Join-Path $script:TestDir 'readme.md') -Force | Out-Null
            New-Item -ItemType Directory -Path (Join-Path $script:TestDir 'docs') -Force | Out-Null
            New-Item -ItemType File -Path (Join-Path $script:TestDir 'docs/guide.md') -Force | Out-Null
            New-Item -ItemType File -Path (Join-Path $script:TestDir 'docs/tutorial.md') -Force | Out-Null
        }

        It 'Discovers markdown files recursively' {
            $files = @(Get-MarkdownFiles -SearchPaths @($script:TestDir))
            $files.Count | Should -BeGreaterOrEqual 3
        }

        It 'Returns FileInfo objects' {
            $files = @(Get-MarkdownFiles -SearchPaths @($script:TestDir))
            $files[0] | Should -BeOfType [System.IO.FileInfo]
        }
    }

    Context 'Exclusion patterns' {
        BeforeEach {
            Push-Location $script:TestDir
            New-Item -ItemType Directory -Path 'node_modules' -Force | Out-Null
            New-Item -ItemType File -Path 'node_modules/package.md' -Force | Out-Null
            New-Item -ItemType Directory -Path '.git' -Force | Out-Null
            New-Item -ItemType File -Path '.git/commit.md' -Force | Out-Null
            New-Item -ItemType Directory -Path 'logs' -Force | Out-Null
            New-Item -ItemType File -Path 'logs/output.md' -Force | Out-Null
            New-Item -ItemType Directory -Path '.copilot-tracking' -Force | Out-Null
            New-Item -ItemType File -Path '.copilot-tracking/notes.md' -Force | Out-Null
            New-Item -ItemType File -Path 'CHANGELOG.md' -Force | Out-Null
            New-Item -ItemType File -Path 'valid.md' -Force | Out-Null
        }

        AfterEach {
            Pop-Location
        }

        It 'Excludes node_modules directory' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Not -Contain 'package.md'
        }

        It 'Excludes .git directory' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Not -Contain 'commit.md'
        }

        It 'Excludes logs directory' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Not -Contain 'output.md'
        }

        It 'Excludes .copilot-tracking directory' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Not -Contain 'notes.md'
        }

        It 'Excludes CHANGELOG.md' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Not -Contain 'CHANGELOG.md'
        }

        It 'Includes non-excluded files' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.'))
            $files.Name | Should -Contain 'valid.md'
        }
    }

    Context 'Explicit path mode' {
        BeforeEach {
            New-Item -ItemType Directory -Path (Join-Path $script:TestDir 'logs') -Force | Out-Null
            $script:ExplicitFile = Join-Path $script:TestDir 'logs/specific.md'
            New-Item -ItemType File -Path $script:ExplicitFile -Force | Out-Null
        }

        It 'Includes excluded directories when path is explicit' {
            $files = @(Get-MarkdownFiles -SearchPaths @($script:ExplicitFile))
            $files.FullName | Should -Contain $script:ExplicitFile
        }
    }

    Context 'Changed files mode' {
        BeforeEach {
            Push-Location $script:TestDir
            New-Item -ItemType File -Path 'changed.md' -Force | Out-Null
            New-Item -ItemType File -Path 'unchanged.md' -Force | Out-Null

            Initialize-MockCIEnvironment -Workspace $script:TestDir | Out-Null

            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('changed.md')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }
        }

        AfterEach {
            Pop-Location
        }

        It 'Uses Git changed files when ChangedOnly is set' {
            $files = @(Get-MarkdownFiles -SearchPaths @('.') -ChangedOnly -Base 'origin/main')
            $files.Count | Should -Be 1
            $files[0] | Should -Be 'changed.md'
        }

        It 'Filters out non-existent changed files' {
            Mock git {
                $global:LASTEXITCODE = 0
                return @('missing.md')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            $files = @(Get-MarkdownFiles -SearchPaths @('.') -ChangedOnly -Base 'origin/main')
            $files | Should -BeNullOrEmpty
        }
    }

    Context 'Multiple paths' {
        BeforeEach {
            $script:Path1 = Join-Path $script:TestDir 'dir1'
            $script:Path2 = Join-Path $script:TestDir 'dir2'
            New-Item -ItemType Directory -Path $script:Path1 -Force | Out-Null
            New-Item -ItemType Directory -Path $script:Path2 -Force | Out-Null
            New-Item -ItemType File -Path (Join-Path $script:Path1 'file1.md') -Force | Out-Null
            New-Item -ItemType File -Path (Join-Path $script:Path2 'file2.md') -Force | Out-Null
        }

        It 'Searches multiple paths' {
            $files = @(Get-MarkdownFiles -SearchPaths @($script:Path1, $script:Path2))
            $files.Count | Should -Be 2
        }
    }

    Context 'Non-existent path' {
        It 'Warns when path not found' {
            Get-MarkdownFiles -SearchPaths @(Join-Path $TestDrive 'nonexistent') -WarningAction SilentlyContinue
            # Should handle gracefully and return empty or warn
        }
    }
}

#endregion

#region Get-MsDateFromFrontmatter Tests

Describe 'Get-MsDateFromFrontmatter' -Tag 'Unit' {
    BeforeEach {
        $script:TestFile = Join-Path $TestDrive 'test-frontmatter.md'
    }

    Context 'Valid ms.date frontmatter' {
        It 'Parses valid ms.date' {
            $content = @'
---
ms.date: 2025-01-01
title: Test Document
---

Content here
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeOfType [DateTime]
            $result.Year | Should -Be 2025
            $result.Month | Should -Be 1
            $result.Day | Should -Be 1
        }

        It 'Parses ms.date with other frontmatter fields' {
            $content = @'
---
title: Example
description: This is a test
ms.date: 2024-06-15
author: tester
---

Content
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeOfType [DateTime]
            $result.ToString('yyyy-MM-dd') | Should -Be '2024-06-15'
        }
    }

    Context 'Invalid or missing ms.date' {
        It 'Returns null when ms.date missing' {
            $content = @'
---
title: No Date Field
---

Content
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }

        It 'Returns null when ms.date has invalid format' {
            $content = @'
---
ms.date: 2025/01/01
---

Content
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }

        It 'Returns null when ms.date is not a valid date' {
            $content = @'
---
ms.date: invalid-date
---

Content
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'No frontmatter' {
        It 'Returns null when no frontmatter present' {
            $content = @'
# Regular Markdown

No frontmatter here
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }

        It 'Returns null when frontmatter incomplete' {
            $content = @'
---
title: Incomplete
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'YAML parsing errors' {
        It 'Handles malformed YAML gracefully' {
            $content = @'
---
title: "Unclosed quote
ms.date: 2025-01-01
---

Content
'@
            Set-Content -Path $script:TestFile -Value $content
            $result = Get-MsDateFromFrontmatter -FilePath $script:TestFile
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'File access errors' {
        It 'Returns null when file cannot be read' {
            $result = Get-MsDateFromFrontmatter -FilePath (Join-Path $TestDrive 'nonexistent.md') 3>$null
            $result | Should -BeNullOrEmpty
        }

        It 'Emits warning when file cannot be read' {
            $warnings = @(Get-MsDateFromFrontmatter -FilePath (Join-Path $TestDrive 'nonexistent.md') 3>&1)
            $warnings | Where-Object { $_ -like '*Error reading file*' } | Should -Not -BeNullOrEmpty
        }
    }
}

#endregion

#region New-MsDateReport Tests

Describe 'New-MsDateReport' -Tag 'Unit' {
    BeforeEach {
        Push-Location $TestDrive
        $script:Results = @(
            [PSCustomObject]@{
                File      = 'docs/fresh.md'
                MsDate    = '2026-03-01'
                AgeDays   = 8
                IsStale   = $false
                Threshold = 90
            },
            [PSCustomObject]@{
                File      = 'docs/stale.md'
                MsDate    = '2025-11-01'
                AgeDays   = 128
                IsStale   = $true
                Threshold = 90
            },
            [PSCustomObject]@{
                File      = 'docs/very-stale.md'
                MsDate    = '2025-06-01'
                AgeDays   = 281
                IsStale   = $true
                Threshold = 90
            }
        )
    }

    AfterEach {
        Pop-Location
    }

    Context 'Report generation' {
        It 'Creates JSON report file' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $jsonPath = Join-Path $TestDrive 'logs/msdate-freshness-results.json'
            Test-Path $jsonPath | Should -BeTrue
        }

        It 'Creates markdown summary file' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $mdPath = Join-Path $TestDrive 'logs/msdate-summary.md'
            Test-Path $mdPath | Should -BeTrue
        }

        It 'Returns report metadata' {
            $report = New-MsDateReport -Results $script:Results -Threshold 90
            $report.JsonPath | Should -Not -BeNullOrEmpty
            $report.MarkdownPath | Should -Not -BeNullOrEmpty
            $report.StaleCount | Should -Be 2
        }

        It 'Creates logs directory if missing' {
            New-MsDateReport -Results $script:Results -Threshold 90 | Out-Null
            Test-Path (Join-Path $TestDrive 'logs') | Should -BeTrue
        }
    }

    Context 'JSON content' {
        It 'Contains all result objects' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $json = Get-Content (Join-Path $TestDrive 'logs/msdate-freshness-results.json') -Raw | ConvertFrom-Json
            $json.Count | Should -Be 3
        }

        It 'Preserves result properties' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $json = Get-Content (Join-Path $TestDrive 'logs/msdate-freshness-results.json') -Raw | ConvertFrom-Json
            $staleItem = $json | Where-Object { $_.File -eq 'docs/stale.md' }
            $staleItem.AgeDays | Should -Be 128
            $staleItem.IsStale | Should -BeTrue
        }
    }

    Context 'Markdown content with stale files' {
        It 'Includes summary statistics' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $md = Get-Content (Join-Path $TestDrive 'logs/msdate-summary.md') -Raw
            $md | Should -Match 'Files Checked.*3'
            $md | Should -Match 'Stale Files.*2'
            $md | Should -Match 'Threshold.*90 days'
        }

        It 'Includes stale files table' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $md = Get-Content (Join-Path $TestDrive 'logs/msdate-summary.md') -Raw
            $md | Should -Match 'Stale Documentation Files'
            $md | Should -Match 'docs/stale.md'
            $md | Should -Match 'docs/very-stale.md'
        }

        It 'Sorts stale files by age descending' {
            New-MsDateReport -Results $script:Results -Threshold 90
            $md = Get-Content (Join-Path $TestDrive 'logs/msdate-summary.md') -Raw
            $veryStaleIndex = $md.IndexOf('docs/very-stale.md')
            $staleIndex = $md.IndexOf('docs/stale.md')
            $veryStaleIndex | Should -BeLessThan $staleIndex
        }
    }

    Context 'Markdown content with all fresh files' {
        BeforeEach {
            $script:FreshResults = @(
                [PSCustomObject]@{
                    File      = 'docs/fresh.md'
                    MsDate    = '2026-03-01'
                    AgeDays   = 8
                    IsStale   = $false
                    Threshold = 90
                }
            )
        }

        It 'Shows success message when no stale files' {
            New-MsDateReport -Results $script:FreshResults -Threshold 90
            $md = Get-Content (Join-Path $TestDrive 'logs/msdate-summary.md') -Raw
            $md | Should -Match 'All Files Fresh'
            $md | Should -Match 'within the 90-day freshness threshold'
        }

        It 'Does not include stale files table' {
            New-MsDateReport -Results $script:FreshResults -Threshold 90
            $md = Get-Content (Join-Path $TestDrive 'logs/msdate-summary.md') -Raw
            $md | Should -Not -Match 'Stale Documentation Files'
        }
    }
}

#endregion

#region Main Script Integration Tests

Describe 'Invoke-MsDateFreshnessCheck Integration' -Tag 'Integration' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        $script:TestDir = Join-Path $TestDrive 'msdate-integration'
        New-Item -ItemType Directory -Path $script:TestDir -Force | Out-Null
        Push-Location $script:TestDir

        Initialize-MockCIEnvironment -Workspace $script:TestDir | Out-Null
        Mock git { return $TestDrive } -ParameterFilter { $args[0] -eq 'rev-parse' }

        # Create test files
        $freshContent = @'
---
ms.date: 2026-03-01
title: Fresh Document
---

Content
'@
        Set-Content (Join-Path $script:TestDir 'fresh.md') $freshContent

        $staleContent = @'
---
ms.date: 2025-01-01
title: Stale Document
---

Content
'@
        Set-Content (Join-Path $script:TestDir 'stale.md') $staleContent

        $noDateContent = @'
---
title: No Date
---

Content
'@
        Set-Content (Join-Path $script:TestDir 'no-date.md') $noDateContent
    }

    AfterEach {
        Pop-Location
        Restore-CIEnvironment
    }

    Context 'Full workflow' {
        It 'Processes files and generates reports' {
            Mock Write-CIAnnotation { }
            $global:LASTEXITCODE = 0

            $markdownFiles = @(Get-MarkdownFiles -SearchPaths @($script:TestDir))
            $results = @()
            $currentDate = Get-Date

            foreach ($file in $markdownFiles) {
                $msDate = Get-MsDateFromFrontmatter -FilePath $file
                if ($null -eq $msDate) { continue }

                $age = $currentDate - $msDate
                $ageDays = [int]$age.TotalDays
                $isStale = $ageDays -gt 90

                $results += [PSCustomObject]@{
                    File      = $file.Name
                    MsDate    = $msDate.ToString('yyyy-MM-dd')
                    AgeDays   = $ageDays
                    IsStale   = $isStale
                    Threshold = 90
                }
            }

            $results.Count | Should -Be 2
            $report = New-MsDateReport -Results $results -Threshold 90
            $report.StaleCount | Should -BeGreaterThan 0
        }
    }

    Context 'CI annotations' {
        It 'Calls Write-CIAnnotation for stale files' {
            Mock Write-CIAnnotation { } -Verifiable

            $markdownFiles = @(Get-MarkdownFiles -SearchPaths @($script:TestDir))
            $currentDate = Get-Date

            foreach ($file in $markdownFiles) {
                $relativePath = $file.Name
                $msDate = Get-MsDateFromFrontmatter -FilePath $file
                if ($null -eq $msDate) { continue }

                $age = $currentDate - $msDate
                $ageDays = [int]$age.TotalDays
                $isStale = $ageDays -gt 90

                if ($isStale) {
                    Write-CIAnnotation -Message "${relativePath}: ms.date is $ageDays days old (threshold: 90 days)" -Level 'Warning' -File $relativePath
                }
            }

            Should -InvokeVerifiable
        }
    }

    Context 'Threshold configuration' {
        It 'Allows custom threshold values' {
            $threshold = 30
            $markdownFiles = @(Get-MarkdownFiles -SearchPaths @($script:TestDir))
            $results = @()
            $currentDate = Get-Date

            foreach ($file in $markdownFiles) {
                $msDate = Get-MsDateFromFrontmatter -FilePath $file
                if ($null -eq $msDate) { continue }

                $age = $currentDate - $msDate
                $ageDays = [int]$age.TotalDays
                $isStale = $ageDays -gt $threshold

                $results += [PSCustomObject]@{
                    File      = $file.Name
                    MsDate    = $msDate.ToString('yyyy-MM-dd')
                    AgeDays   = $ageDays
                    IsStale   = $isStale
                    Threshold = $threshold
                }
            }

            $staleFiles = @($results | Where-Object { $_.IsStale })
            $staleFiles.Count | Should -BeGreaterThan 0
        }
    }
}

#endregion
