#Requires -Modules Pester
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '../../linting/Modules/LintingHelpers.psm1'
    Import-Module $modulePath -Force
}

#region Get-ChangedFilesFromGit Tests

Describe 'Get-ChangedFilesFromGit' -Tag 'Unit' {
    Context 'Merge-base succeeds' {
        BeforeEach {
            $changedFiles = @('scripts/test.ps1', 'docs/readme.md', 'config/settings.json')

            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return $changedFiles
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Returns changed files filtered by extension' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1')
            $result | Should -Contain 'scripts/test.ps1'
            $result | Should -Not -Contain 'docs/readme.md'
            $result | Should -Not -Contain 'config/settings.json'
        }

        It 'Returns all files with wildcard extension' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*')
            $result.Count | Should -Be 3
        }

        It 'Returns files matching multiple extension patterns' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1', '*.md')
            $result | Should -Contain 'scripts/test.ps1'
            $result | Should -Contain 'docs/readme.md'
            $result | Should -Not -Contain 'config/settings.json'
        }

        It 'Uses default extension pattern when not specified' {
            $result = Get-ChangedFilesFromGit
            $result.Count | Should -Be 3
        }
    }

    Context 'Merge-base fails, HEAD~1 fallback' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 128
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return 'HEAD~1-sha'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'rev-parse' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('fallback-file.ps1')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Falls back to HEAD~1 comparison and returns files' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1')
            $result | Should -Contain 'fallback-file.ps1'
        }
    }

    Context 'Both fallbacks fail, uses diff-filter ACMR on HEAD' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 128
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 128
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'rev-parse' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('unstaged-file.ps1')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Falls back to git diff --diff-filter=ACMR HEAD and returns files' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1')
            $result | Should -Contain 'unstaged-file.ps1'
        }
    }

    Context 'Empty results return empty array' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }
        }

        It 'Returns empty array when no files changed' {
            $result = Get-ChangedFilesFromGit
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'File existence filtering' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('exists.ps1', 'deleted.ps1')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path {
                param($Path)
                return $Path -eq 'exists.ps1'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Excludes files that no longer exist' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1')
            $result | Should -Contain 'exists.ps1'
            $result | Should -Not -Contain 'deleted.ps1'
        }
    }

    Context 'Returns safely when all files filtered by extension' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('readme.md', 'config.json')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Returns zero results when no files match extension' {
            $result = @(Get-ChangedFilesFromGit -FileExtensions @('*.ps1'))
            $result.Count | Should -Be 0
        }
    }

    Context 'Empty and whitespace file entries' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('valid.ps1', '', '   ', 'another.ps1')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Filters out empty and whitespace entries' {
            $result = Get-ChangedFilesFromGit -FileExtensions @('*.ps1')
            $result | Should -Contain 'valid.ps1'
            $result | Should -Contain 'another.ps1'
            $result | Should -Not -Contain ''
            $result | Should -Not -Contain '   '
        }
    }

    Context 'Git diff command fails' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 1
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }
        }

        It 'Returns empty array when git diff fails' {
            $result = Get-ChangedFilesFromGit
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Exception during execution' {
        BeforeEach {
            Mock git {
                throw "Simulated git failure"
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }
        }

        It 'Catches exceptions and returns empty array' {
            $result = Get-ChangedFilesFromGit
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Third fallback explicit staged+unstaged diff' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 128
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 128
                return $null
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'rev-parse' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('docs/new.md')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Falls through to staged+unstaged diff when merge-base and HEAD~1 both fail' {
            $result = Get-ChangedFilesFromGit -BaseBranch 'origin/main' -FileExtensions @('*.md')
            $result | Should -Contain 'docs/new.md'
        }
    }

    Context 'FileExtensions wildcard default' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('file.py', 'file.md', 'file.js')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Returns all file types when FileExtensions defaults to wildcard' {
            $result = Get-ChangedFilesFromGit -BaseBranch 'origin/main'
            $result | Should -HaveCount 3
        }
    }

    Context 'Absolute and relative path handling' {
        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return 'abc123def456789'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }

            Mock git {
                $global:LASTEXITCODE = 0
                return @('/absolute/path/file.md', 'relative/file.md')
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'diff' }

            Mock Test-Path { return $true } -ModuleName 'LintingHelpers' -ParameterFilter { $PathType -eq 'Leaf' }
        }

        It 'Handles mixed absolute and relative paths from git diff' {
            $result = Get-ChangedFilesFromGit -BaseBranch 'origin/main' -FileExtensions @('*.md')
            $result | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Git diff output parsing exception' {
        BeforeEach {
            Mock git {
                throw 'unexpected error during diff'
            } -ModuleName 'LintingHelpers' -ParameterFilter { $args[0] -eq 'merge-base' }
        }

        It 'Returns empty array when git diff output cannot be parsed' {
            $result = Get-ChangedFilesFromGit -BaseBranch 'origin/main'
            $result | Should -BeNullOrEmpty
        }
    }
}

#endregion

#region Get-FilesRecursive Tests

Describe 'Get-FilesRecursive' -Tag 'Unit' {
    Context 'Basic file enumeration' {
        BeforeEach {
            New-Item -Path 'TestDrive:/scripts' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/scripts/test.ps1' -ItemType File -Force | Out-Null
            New-Item -Path 'TestDrive:/scripts/readme.md' -ItemType File -Force | Out-Null
            New-Item -Path 'TestDrive:/scripts/sub' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/scripts/sub/nested.ps1' -ItemType File -Force | Out-Null
        }

        It 'Finds files matching Include pattern' {
            $result = Get-FilesRecursive -Path 'TestDrive:/scripts' -Include @('*.ps1')
            $result.Count | Should -Be 2
            $result.Name | Should -Contain 'test.ps1'
            $result.Name | Should -Contain 'nested.ps1'
        }

        It 'Finds files with multiple Include patterns' {
            $result = Get-FilesRecursive -Path 'TestDrive:/scripts' -Include @('*.ps1', '*.md')
            $result.Count | Should -Be 3
        }

        It 'Does not include directories in results' {
            $result = Get-FilesRecursive -Path 'TestDrive:/scripts' -Include @('*')
            $result | ForEach-Object { $_.PSIsContainer | Should -BeFalse }
        }
    }

    Context 'Returns empty array for non-existent path' {
        It 'Returns empty for non-existent path' {
            $result = Get-FilesRecursive -Path 'TestDrive:/nonexistent' -Include @('*.ps1')
            $result.Count | Should -Be 0
        }
    }

    Context 'Null-safe return when no files match' {
        BeforeEach {
            New-Item -Path 'TestDrive:/emptydir' -ItemType Directory -Force | Out-Null
        }

        It 'Returns safely usable result when no files match' {
            $result = @(Get-FilesRecursive -Path 'TestDrive:/emptydir' -Include @('*.xyz'))
            $result | Should -HaveCount 0
        }
    }

    Context 'Gitignore filtering' {
        BeforeEach {
            New-Item -Path 'TestDrive:/project' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/project/src' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/project/src/app.ps1' -ItemType File -Force | Out-Null
            New-Item -Path 'TestDrive:/project/node_modules' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/project/node_modules/pkg.ps1' -ItemType File -Force | Out-Null
            'node_modules/' | Set-Content 'TestDrive:/project/.gitignore'
        }

        It 'Excludes files matching gitignore patterns' {
            $result = Get-FilesRecursive -Path 'TestDrive:/project' `
                -Include @('*.ps1') `
                -GitIgnorePath 'TestDrive:/project/.gitignore'
            $result.Name | Should -Contain 'app.ps1'
            $result.Name | Should -Not -Contain 'pkg.ps1'
        }

        It 'Returns all files when gitignore path not provided' {
            $result = Get-FilesRecursive -Path 'TestDrive:/project' -Include @('*.ps1')
            $result.Count | Should -Be 2
        }
    }

    Context 'Non-existent gitignore file' {
        BeforeEach {
            New-Item -Path 'TestDrive:/simple' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/simple/file.ps1' -ItemType File -Force | Out-Null
        }

        It 'Returns files when gitignore does not exist' {
            $result = Get-FilesRecursive -Path 'TestDrive:/simple' `
                -Include @('*.ps1') `
                -GitIgnorePath 'TestDrive:/simple/.gitignore'
            $result.Count | Should -Be 1
        }
    }

    Context 'Empty Include array' {
        BeforeEach {
            New-Item -Path 'TestDrive:/emptyinc' -ItemType Directory -Force | Out-Null
            New-Item -Path 'TestDrive:/emptyinc/file.txt' -ItemType File -Force | Out-Null
        }

        It 'Throws when Include is empty' {
            { Get-FilesRecursive -Path 'TestDrive:/emptyinc' -Include @() } | Should -Throw
        }
    }

    Context 'File path instead of directory' {
        BeforeEach {
            New-Item -Path 'TestDrive:/single-file.ps1' -ItemType File -Force | Out-Null
        }

        It 'Handles file path gracefully' {
            { Get-FilesRecursive -Path 'TestDrive:/single-file.ps1' -Include @('*.ps1') } | Should -Not -Throw
        }
    }
}

#endregion

#region Get-GitIgnorePatterns Tests

Describe 'Get-GitIgnorePatterns' -Tag 'Unit' {
    Context 'Non-existent file' {
        It 'Returns empty for non-existent file' {
            $result = Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/nonexistent/.gitignore'
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Empty file' {
        BeforeEach {
            New-Item -Path 'TestDrive:/.gitignore-empty' -ItemType File -Force | Out-Null
        }

        It 'Returns empty for empty file' {
            $result = Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/.gitignore-empty'
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unreadable file' {
        It 'Returns empty array and warns on read failure' {
            Mock Get-Content { throw "Access denied" } -ModuleName 'LintingHelpers'
            Mock Test-Path { return $true } -ModuleName 'LintingHelpers'
            $result = Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/unreadable/.gitignore'
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Pattern parsing' {
        It 'Skips comments and empty lines' {
            @('# Comment', '', 'node_modules/', '  ', '*.log') | Set-Content 'TestDrive:/.gitignore'
            $result = Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/.gitignore'
            $result.Count | Should -Be 2
        }

        It 'Returns zero patterns for file with only comments' {
            @('# Comment', '', '  ') | Set-Content 'TestDrive:/.gitignore-comments'
            $result = @(Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/.gitignore-comments')
            $result.Count | Should -Be 0
        }

        It 'Converts directory patterns correctly' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-dir'
            'node_modules/' | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $result[0] | Should -Be "*${sep}node_modules${sep}*"
        }

        It 'Converts file patterns with paths correctly' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-path'
            'build/output.log' | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $result[0] | Should -Be "*${sep}build${sep}output.log*"
        }

        It 'Handles file-glob patterns without trailing separator' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-fileglob'
            '*.log' | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $result[0] | Should -Be "*${sep}*.log"
        }

        It 'Wraps plain directory names with separators' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-plaindir'
            'vendor' | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $sep = [System.IO.Path]::DirectorySeparatorChar
            $result[0] | Should -Be "*${sep}vendor${sep}*"
        }

        It 'Processes multiple patterns' {
            @('node_modules/', 'dist/', '*.tmp', 'logs/debug.log') | Set-Content 'TestDrive:/.gitignore-multi'
            $result = Get-GitIgnorePatterns -GitIgnorePath 'TestDrive:/.gitignore-multi'
            $result.Count | Should -Be 4
        }
    }

    Context 'Negation and special patterns' {
        It 'Handles negation patterns without error' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-negation'
            @('*.log', '!important.log') | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $result.Count | Should -BeGreaterOrEqual 1
        }

        It 'Handles nested glob patterns' {
            $gitignorePath = Join-Path $TestDrive '.gitignore-nested'
            @('**/build/**', 'src/**/temp') | Set-Content $gitignorePath
            $result = @(Get-GitIgnorePatterns -GitIgnorePath $gitignorePath)
            $result.Count | Should -Be 2
        }
    }
}

#endregion
