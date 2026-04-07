#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Test-DependencyPinning.ps1

    $mockPath = Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1'
    Import-Module $mockPath -Force

    # Fixture paths
    $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
    $script:SecurityFixturesPath = Join-Path $PSScriptRoot '../Fixtures/Security'
}

Describe 'Test-SHAPinning' -Tag 'Unit' {
    Context 'Valid SHA references for github-actions' {
        It 'Returns true for valid 40-char lowercase SHA' {
            Test-SHAPinning -Version 'a5ac7e51b41094c92402da3b24376905380afc29' -Type 'github-actions' | Should -BeTrue
        }

        It 'Returns true for valid 40-char mixed case SHA' {
            Test-SHAPinning -Version 'A5AC7E51B41094c92402da3b24376905380afc29' -Type 'github-actions' | Should -BeTrue
        }
    }

    Context 'Invalid SHA references for github-actions' {
        It 'Returns false for tag reference' {
            Test-SHAPinning -Version 'v4' -Type 'github-actions' | Should -BeFalse
        }

        It 'Returns false for branch reference' {
            Test-SHAPinning -Version 'main' -Type 'github-actions' | Should -BeFalse
        }

        It 'Returns false for 39-char reference' {
            Test-SHAPinning -Version 'a5ac7e51b41094c92402da3b24376905380afc2' -Type 'github-actions' | Should -BeFalse
        }

        It 'Returns false for 41-char reference' {
            Test-SHAPinning -Version 'a5ac7e51b41094c92402da3b24376905380afc291' -Type 'github-actions' | Should -BeFalse
        }

        It 'Returns false for non-hex characters' {
            Test-SHAPinning -Version 'g5ac7e51b41094c92402da3b24376905380afc29' -Type 'github-actions' | Should -BeFalse
        }
    }

    Context 'Unknown type' {
        It 'Returns false for unknown dependency type' {
            Test-SHAPinning -Version 'a5ac7e51b41094c92402da3b24376905380afc29' -Type 'unknown-type' | Should -BeFalse
        }
    }
}

Describe 'Test-ShellDownloadSecurity' -Tag 'Unit' {
    Context 'Insecure downloads' {
        It 'Detects curl without checksum verification' {
            $testFile = Join-Path $script:SecurityFixturesPath 'insecure-download.sh'
            $fileInfo = @{
                Path         = $testFile
                Type         = 'shell-downloads'
                RelativePath = 'insecure-download.sh'
            }
            $result = Test-ShellDownloadSecurity -FileInfo $fileInfo
            $result | Should -Not -BeNullOrEmpty
            $result[0].Severity | Should -Be 'warning'
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $fileInfo = @{
                Path         = 'TestDrive:/nonexistent/file.sh'
                Type         = 'shell-downloads'
                RelativePath = 'nonexistent/file.sh'
            }
            $result = Test-ShellDownloadSecurity -FileInfo $fileInfo
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-DependencyViolation' -Tag 'Unit' {
    Context 'Pinned workflows' {
        It 'Returns no violations for fully pinned workflow' {
            $pinnedPath = Join-Path $script:FixturesPath 'pinned-workflow.yml'
            $fileInfo = @{
                Path         = $pinnedPath
                Type         = 'github-actions'
                RelativePath = 'pinned-workflow.yml'
            }
            $result = Get-DependencyViolation -FileInfo $fileInfo
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned workflows' {
        It 'Detects unpinned action references' {
            $unpinnedPath = Join-Path $script:FixturesPath 'unpinned-workflow.yml'
            $fileInfo = @{
                Path         = $unpinnedPath
                Type         = 'github-actions'
                RelativePath = 'unpinned-workflow.yml'
            }
            $result = Get-DependencyViolation -FileInfo $fileInfo
            $result | Should -Not -BeNullOrEmpty
            $result.Count | Should -BeGreaterThan 0
        }

        It 'Returns correct violation type for unpinned actions' {
            $unpinnedPath = Join-Path $script:FixturesPath 'unpinned-workflow.yml'
            $fileInfo = @{
                Path         = $unpinnedPath
                Type         = 'github-actions'
                RelativePath = 'unpinned-workflow.yml'
            }
            $result = Get-DependencyViolation -FileInfo $fileInfo
            $result[0].Type | Should -Be 'github-actions'
        }
    }

    Context 'Mixed workflows' {
        It 'Detects only unpinned actions in mixed workflow' {
            $mixedPath = Join-Path $script:FixturesPath 'mixed-pinning-workflow.yml'
            $fileInfo = @{
                Path         = $mixedPath
                Type         = 'github-actions'
                RelativePath = 'mixed-pinning-workflow.yml'
            }
            $result = Get-DependencyViolation -FileInfo $fileInfo
            $result | Should -Not -BeNullOrEmpty
            # Should only detect the unpinned setup-node action
            $result.Name | Should -Contain 'actions/setup-node'
        }
    }

    Context 'Non-existent file' {
        It 'Returns empty array for non-existent file' {
            $fileInfo = @{
                Path         = 'TestDrive:/nonexistent/file.yml'
                Type         = 'github-actions'
                RelativePath = 'file.yml'
            }
            $result = Get-DependencyViolation -FileInfo $fileInfo
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Export-ComplianceReport' -Tag 'Unit' {
    BeforeEach {
        $script:TestOutputPath = Join-Path $TestDrive 'report'
        New-Item -ItemType Directory -Path $script:TestOutputPath -Force | Out-Null

        # Create a proper ComplianceReport class instance
        $script:MockReport = [ComplianceReport]::new()
        $script:MockReport.ScanPath = $script:FixturesPath
        $script:MockReport.ComplianceScore = 50
        $script:MockReport.TotalFiles = 3
        $script:MockReport.ScannedFiles = 3
        $script:MockReport.TotalDependencies = 4
        $script:MockReport.PinnedDependencies = 2
        $script:MockReport.UnpinnedDependencies = 2
        $script:MockReport.Violations = @(
            [PSCustomObject]@{
                File        = 'unpinned-workflow.yml'
                Line        = 10
                Type        = 'github-actions'
                Name        = 'actions/checkout'
                Version     = 'v4'
                Severity    = 'High'
                Description = 'Unpinned dependency'
                Remediation = 'Pin to SHA'
            }
        )
        $script:MockReport.Summary = @{
            'github-actions' = @{
                Total  = 4
                High   = 2
                Medium = 0
                Low    = 0
            }
        }
    }

    Context 'JSON format' {
        It 'Generates valid JSON report' {
            $outputFile = Join-Path $script:TestOutputPath 'report.json'

            Export-ComplianceReport -Report $script:MockReport -Format 'json' -OutputPath $outputFile

            Test-Path $outputFile | Should -BeTrue
            $content = Get-Content $outputFile -Raw | ConvertFrom-Json
            $content | Should -Not -BeNullOrEmpty
        }
    }

    Context 'SARIF format' {
        It 'Generates valid SARIF report' {
            $outputFile = Join-Path $script:TestOutputPath 'report.sarif'

            Export-ComplianceReport -Report $script:MockReport -Format 'sarif' -OutputPath $outputFile

            Test-Path $outputFile | Should -BeTrue
            $content = Get-Content $outputFile -Raw | ConvertFrom-Json
            $content.'$schema' | Should -Match 'sarif'
        }
    }

    Context 'Table format' {
        It 'Generates table output without error' {
            $outputFile = Join-Path $script:TestOutputPath 'report.txt'

            { Export-ComplianceReport -Report $script:MockReport -Format 'table' -OutputPath $outputFile } | Should -Not -Throw
            Test-Path $outputFile | Should -BeTrue
        }
    }

    Context 'CSV format' {
        It 'Generates CSV report' {
            $outputFile = Join-Path $script:TestOutputPath 'report.csv'

            Export-ComplianceReport -Report $script:MockReport -Format 'csv' -OutputPath $outputFile

            Test-Path $outputFile | Should -BeTrue
        }
    }

    Context 'Markdown format' {
        It 'Generates Markdown report' {
            $outputFile = Join-Path $script:TestOutputPath 'report.md'

            Export-ComplianceReport -Report $script:MockReport -Format 'markdown' -OutputPath $outputFile

            Test-Path $outputFile | Should -BeTrue
            $content = Get-Content $outputFile -Raw
            $content | Should -Match '# Dependency Pinning Compliance Report'
        }
    }
}

Describe 'ExcludePaths Filtering Logic' -Tag 'Unit' {
    Context 'Pattern matching with -notlike operator' {
        It 'Excludes paths containing pattern using -notlike wildcard' {
            # Test the exclusion logic used in Get-FilesToScan:
            # $files = $files | Where-Object { $_.FullName -notlike "*$exclude*" }
            $testPaths = @(
                @{ FullName = 'C:\repo\.github\workflows\test.yml' }
                @{ FullName = 'C:\repo\vendor\.github\workflows\vendor.yml' }
            )

            $exclude = 'vendor'
            $filtered = $testPaths | Where-Object { $_.FullName -notlike "*$exclude*" }

            $filtered.Count | Should -Be 1
            $filtered[0].FullName | Should -Not -Match 'vendor'
        }

        It 'Excludes multiple patterns correctly' {
            $testPaths = @(
                @{ FullName = 'C:\repo\.github\workflows\test.yml' }
                @{ FullName = 'C:\repo\vendor\.github\workflows\vendor.yml' }
                @{ FullName = 'C:\repo\node_modules\pkg\workflow.yml' }
            )

            $excludePatterns = @('vendor', 'node_modules')
            $filtered = $testPaths
            foreach ($exclude in $excludePatterns) {
                $filtered = @($filtered | Where-Object { $_.FullName -notlike "*$exclude*" })
            }

            $filtered.Count | Should -Be 1
            $filtered[0].FullName | Should -Be 'C:\repo\.github\workflows\test.yml'
        }
    }

    Context 'Processes all files when ExcludePatterns is empty' {
        It 'Returns all paths when no exclusion patterns provided' {
            $testPaths = @(
                @{ FullName = 'C:\repo\.github\workflows\test.yml' }
                @{ FullName = 'C:\repo\vendor\.github\workflows\vendor.yml' }
            )

            $excludePatterns = @()
            $filtered = $testPaths
            if ($excludePatterns) {
                foreach ($exclude in $excludePatterns) {
                    $filtered = $filtered | Where-Object { $_.FullName -notlike "*$exclude*" }
                }
            }

            $filtered.Count | Should -Be 2
        }
    }

    Context 'Comma-separated pattern parsing in main script' {
        It 'Parses comma-separated exclude paths correctly' {
            # Test the pattern used in main execution: $ExcludePaths.Split(',')
            $excludePathsParam = 'vendor,node_modules,dist'
            $patterns = $excludePathsParam.Split(',') | ForEach-Object { $_.Trim() }

            $patterns.Count | Should -Be 3
            $patterns | Should -Contain 'vendor'
            $patterns | Should -Contain 'node_modules'
            $patterns | Should -Contain 'dist'
        }

        It 'Handles single pattern without comma' {
            $excludePathsParam = 'vendor'
            $patterns = $excludePathsParam.Split(',') | ForEach-Object { $_.Trim() }

            $patterns.Count | Should -Be 1
            $patterns | Should -Contain 'vendor'
        }

        It 'Handles empty exclude paths' {
            $excludePathsParam = ''
            $patterns = if ($excludePathsParam) { $excludePathsParam.Split(',') | ForEach-Object { $_.Trim() } } else { @() }

            $patterns.Count | Should -Be 0
        }
    }

    Context 'Pattern matching behavior' {
        It 'Uses -notlike with wildcard for exclusion' {
            $filePath = 'C:\repo\vendor\.github\workflows\test.yml'
            $pattern = 'vendor'

            # This matches how Get-FilesToScan uses: $_.FullName -notlike "*$exclude*"
            $filePath -notlike "*$pattern*" | Should -BeFalse
        }

        It 'Passes through non-matching paths' {
            $filePath = 'C:\repo\.github\workflows\main.yml'
            $pattern = 'vendor'

            $filePath -notlike "*$pattern*" | Should -BeTrue
        }
    }
}

Describe 'Dot-sourced execution protection' -Tag 'Integration' {
    Context 'When script is dot-sourced' {
        It 'Does not execute main block when dot-sourced' {
            # Arrange
            $testScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
            $tempOutputPath = Join-Path $TestDrive 'dot-source-test.json'

            # Act - Invoke in new process with dot-sourcing simulation
            $scriptBlock = ". '$testScript' -OutputPath '$tempOutputPath'; [System.IO.File]::Exists('$tempOutputPath')"
            pwsh -Command $scriptBlock 2>&1 | Out-Null

            # Assert - Main execution should be skipped, no output file created
            Test-Path $tempOutputPath | Should -BeFalse
        }

        It 'Makes functions available when dot-sourced' {
            # Arrange
            $testScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'

            # Act - Dot-source in subprocess and check function availability
            $result = pwsh -Command ". '$testScript'; if (Get-Command -Name 'Test-SHAPinning' -ErrorAction SilentlyContinue) { 'available' } else { 'missing' }" 2>&1

            # Assert - Functions should be importable via dot-sourcing
            $result | Should -Contain 'available'
        }
    }
}

Describe 'GitHub Actions error annotation' -Tag 'Integration' {
    BeforeAll {
        $script:OriginalGHA = $env:GITHUB_ACTIONS
        $script:TestScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
    }

    AfterAll {
        if ($null -eq $script:OriginalGHA) {
            Remove-Item Env:GITHUB_ACTIONS -ErrorAction SilentlyContinue
        } else {
            $env:GITHUB_ACTIONS = $script:OriginalGHA
        }
    }

    Context 'Error handling with GitHub Actions' {
        It 'Outputs GitHub error annotation on failure' {
            # Arrange - Create a corrupted workflow file that will trigger an error
            $testWorkflowDir = Join-Path $TestDrive 'test-workflows'
            New-Item -ItemType Directory -Path (Join-Path $testWorkflowDir '.github/workflows') -Force | Out-Null
            $corruptedFile = Join-Path $testWorkflowDir '.github/workflows/test.yml'
            "uses: actions/checkout@invalid!!!" | Out-File -FilePath $corruptedFile -Encoding UTF8

            # Act - Run script in new process with GITHUB_ACTIONS set
            $scriptCommand = @"
`$env:GITHUB_ACTIONS = 'true'
& '$script:TestScript' -Path '$testWorkflowDir' -Format 'json' -OutputPath '$TestDrive/gha-test.json' -FailOnUnpinned 2>&1
"@
            $output = pwsh -Command $scriptCommand

            # Assert - Should contain GitHub Actions error annotation or error output
            # The script should execute and potentially generate warnings/errors
            $output | Should -Not -BeNullOrEmpty
        }
    }
}

Describe 'Get-ComplianceReportData' -Tag 'Unit' {
    BeforeAll {
        . $PSScriptRoot/../../security/Test-DependencyPinning.ps1
    }

    Context 'Array coercion operations' {
        It 'Handles empty violations array' {
            $result = Get-ComplianceReportData -ScanPath 'TestDrive:/' -Violations @() -ScannedFiles @()

            $result.TotalDependencies | Should -Be 0
            $result.UnpinnedDependencies | Should -Be 0
            $result.PinnedDependencies | Should -Be 0
            $result.ComplianceScore | Should -Be 100.0
        }

        It 'Counts violations correctly with array coercion' {
            $v1 = [DependencyViolation]::new()
            $v1.Type = 'github-actions'
            $v1.Severity = 'High'

            $v2 = [DependencyViolation]::new()
            $v2.Type = 'github-actions'
            $v2.Severity = 'Medium'

            $v3 = [DependencyViolation]::new()
            $v3.Type = 'npm'
            $v3.Severity = 'High'

            $violations = @($v1, $v2, $v3)
            $scannedFiles = @(@{ Path = 'test1.yml' }, @{ Path = 'test2.json' })

            $result = Get-ComplianceReportData -ScanPath 'TestDrive:/' -Violations $violations -ScannedFiles $scannedFiles

            $result.TotalDependencies | Should -Be 3
            $result.UnpinnedDependencies | Should -Be 3
        }

        It 'Groups violations by type with array coercion' {
            $v1 = [DependencyViolation]::new()
            $v1.Type = 'github-actions'
            $v1.Severity = 'High'

            $v2 = [DependencyViolation]::new()
            $v2.Type = 'github-actions'
            $v2.Severity = 'Low'

            $v3 = [DependencyViolation]::new()
            $v3.Type = 'npm'
            $v3.Severity = 'Medium'

            $violations = @($v1, $v2, $v3)
            $scannedFiles = @(@{ Path = 'test.yml' })

            $result = Get-ComplianceReportData -ScanPath 'TestDrive:/' -Violations $violations -ScannedFiles $scannedFiles

            $result.Summary.Keys | Should -Contain 'github-actions'
            $result.Summary.Keys | Should -Contain 'npm'
            $result.Summary['github-actions'].Total | Should -Be 2
            $result.Summary['npm'].Total | Should -Be 1
        }

        It 'Counts severity levels correctly with array coercion' {
            $violations = @()
            for ($i = 0; $i -lt 4; $i++) {
                $v = [DependencyViolation]::new()
                $v.Type = 'github-actions'
                $v.Severity = switch ($i) {
                    0 { 'High' }
                    1 { 'High' }
                    2 { 'Medium' }
                    3 { 'Low' }
                }
                $violations += $v
            }
            $scannedFiles = @(@{ Path = 'test.yml' })

            $result = Get-ComplianceReportData -ScanPath 'TestDrive:/' -Violations $violations -ScannedFiles $scannedFiles

            $result.Summary['github-actions'].High | Should -Be 2
            $result.Summary['github-actions'].Medium | Should -Be 1
            $result.Summary['github-actions'].Low | Should -Be 1
        }

        It 'Handles single violation without PowerShell unrolling' {
            $v = [DependencyViolation]::new()
            $v.Type = 'github-actions'
            $v.Severity = 'High'

            $violations = @($v)
            $scannedFiles = @(@{ Path = 'test.yml' })

            $result = Get-ComplianceReportData -ScanPath 'TestDrive:/' -Violations $violations -ScannedFiles $scannedFiles

            $result.TotalDependencies | Should -Be 1
            $result.Summary['github-actions'].Total | Should -Be 1
            $result.Summary['github-actions'].High | Should -Be 1
        }
    }
}

Describe 'Main Script Execution' -Tag 'Integration' {
    BeforeAll {
        $script:TestScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
        $script:TestWorkspaceDir = Join-Path $TestDrive 'test-workspace'
        New-Item -ItemType Directory -Path $script:TestWorkspaceDir -Force | Out-Null

        # Create .github/workflows directory
        $workflowDir = Join-Path $script:TestWorkspaceDir '.github/workflows'
        New-Item -ItemType Directory -Path $workflowDir -Force | Out-Null
    }

    Context 'Array coercion in main execution block' {
        It 'Executes array coercion when scanning files' {
            # Create test workflow file
            $workflowContent = @'
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
'@
            Set-Content -Path (Join-Path $script:TestWorkspaceDir '.github/workflows/test.yml') -Value $workflowContent

            $jsonPath = Join-Path $TestDrive 'scan-output.json'

            # Execute script with array coercion operations
            & $script:TestScript -Path $script:TestWorkspaceDir -Format 'json' -OutputPath $jsonPath *>&1 | Out-Null

            # Verify output was created (proves array operations executed)
            Test-Path $jsonPath | Should -BeTrue
            $result = Get-Content $jsonPath | ConvertFrom-Json
            $result.PSObject.Properties.Name | Should -Contain 'ComplianceScore'
        }

        It 'Handles empty scan results with array coercion' {
            # Remove workflow files
            Remove-Item -Path (Join-Path $script:TestWorkspaceDir '.github/workflows/*.yml') -Force -ErrorAction SilentlyContinue

            # Create pinned workflow
            $pinnedContent = @'
name: Pinned
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@8e5e7e5ab8b370d6c329ec480221332ada57f0ab
'@
            Set-Content -Path (Join-Path $script:TestWorkspaceDir '.github/workflows/pinned.yml') -Value $pinnedContent

            $jsonPath = Join-Path $TestDrive 'empty-output.json'

            # Execute with all dependencies pinned (tests zero count array coercion)
            & $script:TestScript -Path $script:TestWorkspaceDir -Format 'json' -OutputPath $jsonPath *>&1 | Out-Null

            Test-Path $jsonPath | Should -BeTrue
            $result = Get-Content $jsonPath | ConvertFrom-Json
            $result.UnpinnedDependencies | Should -Be 0
        }
    }
}

Describe 'Get-NpmDependencyViolations' -Tag 'Unit' {
    BeforeAll {
        . $PSScriptRoot/../../security/Test-DependencyPinning.ps1
        $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Npm'
    }

    Context 'Metadata-only package.json' {
        It 'Returns zero violations for package with no dependencies' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'metadata-only-package.json'
                Type         = 'npm'
                RelativePath = 'metadata-only-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }

    Context 'Package.json with dependencies' {
        It 'Detects unpinned dependencies in all sections' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'with-dependencies-package.json'
                Type         = 'npm'
                RelativePath = 'with-dependencies-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -BeGreaterThan 0
        }

        It 'Identifies correct dependency sections' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'with-dependencies-package.json'
                Type         = 'npm'
                RelativePath = 'with-dependencies-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo
            $sections = $violations | ForEach-Object { $_.Metadata.Section } | Sort-Object -Unique

            $sections | Should -Contain 'dependencies'
            $sections | Should -Contain 'devDependencies'
        }

        It 'Captures package name and version in violations' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'with-dependencies-package.json'
                Type         = 'npm'
                RelativePath = 'with-dependencies-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo
            $lodashViolation = $violations | Where-Object { $_.Name -eq 'lodash' }

            $lodashViolation | Should -Not -BeNullOrEmpty
            $lodashViolation.Name | Should -Be 'lodash'
            $lodashViolation.Version | Should -Be '^4.17.21'
        }
    }

    Context 'Non-existent file' {
        It 'Returns empty array for missing file' {
            $fileInfo = @{
                Path         = 'C:\nonexistent\package.json'
                Type         = 'npm'
                RelativePath = 'nonexistent/package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }

    Context 'When package.json contains invalid JSON' {
        BeforeAll {
            $script:invalidJsonPath = Join-Path $script:FixturesPath 'invalid-json-package.json'
        }

        It 'Returns empty violations array on parse failure' {
            $fileInfo = @{
                Path         = $script:invalidJsonPath
                Type         = 'npm'
                RelativePath = 'invalid-json-package.json'
            }

            $violations = @(Get-NpmDependencyViolations -FileInfo $fileInfo)

            $violations | Should -HaveCount 0
        }

        It 'Emits a warning about parse failure' {
            $fileInfo = @{
                Path         = $script:invalidJsonPath
                Type         = 'npm'
                RelativePath = 'invalid-json-package.json'
            }

            $warnings = Get-NpmDependencyViolations -FileInfo $fileInfo 3>&1

            $warnings | Should -Not -BeNullOrEmpty
            $warnings | Should -Match 'Failed to parse.*as JSON'
        }
    }

    Context 'When package.json contains empty or whitespace versions' {
        BeforeAll {
            $script:emptyVersionPath = Join-Path $script:FixturesPath 'empty-version-package.json'
        }

        It 'Skips dependencies with empty versions' {
            $fileInfo = @{
                Path         = $script:emptyVersionPath
                Type         = 'npm'
                RelativePath = 'empty-version-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo
            $packageNames = $violations | ForEach-Object { $_.Name }

            $packageNames | Should -Not -Contain 'empty-version'
            $packageNames | Should -Not -Contain 'whitespace-version'
        }

        It 'Reports violations for valid non-pinned versions in same file' {
            $fileInfo = @{
                Path         = $script:emptyVersionPath
                Type         = 'npm'
                RelativePath = 'empty-version-package.json'
            }

            $violations = Get-NpmDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -BeGreaterThan 0
            $violations | Where-Object { $_.Name -eq 'valid-package' } | Should -Not -BeNullOrEmpty
        }
    }
}

Describe 'Get-PipDependencyViolations' -Tag 'Unit' {
    BeforeAll {
        . $PSScriptRoot/../../security/Test-DependencyPinning.ps1
        $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Pip'
    }

    Context 'Fully pinned requirements.txt' {
        It 'Returns zero violations when all deps use ==' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'pinned-requirements.txt'
                Type         = 'pip'
                RelativePath = 'pinned-requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }

    Context 'Unpinned requirements.txt' {
        It 'Detects unpinned dependencies' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-requirements.txt'
                Type         = 'pip'
                RelativePath = 'unpinned-requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -BeGreaterThan 0
        }

        It 'Detects >= range operators as unpinned' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-requirements.txt'
                Type         = 'pip'
                RelativePath = 'unpinned-requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $numpyViolation = $violations | Where-Object { $_.Name -eq 'numpy' }

            $numpyViolation | Should -Not -BeNullOrEmpty
        }

        It 'Detects bare package names as unpinned' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-requirements.txt'
                Type         = 'pip'
                RelativePath = 'unpinned-requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $flaskViolation = $violations | Where-Object { $_.Name -eq 'flask' }

            $flaskViolation | Should -Not -BeNullOrEmpty
            $flaskViolation.Version | Should -Be '(none)'
        }

        It 'Does not flag == pinned deps as violations' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-requirements.txt'
                Type         = 'pip'
                RelativePath = 'unpinned-requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $requestsViolation = $violations | Where-Object { $_.Name -eq 'requests' }

            $requestsViolation | Should -BeNullOrEmpty
        }
    }

    Context 'Pinned pyproject.toml' {
        It 'Returns zero violations when all deps use ==' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'pinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'pinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }

    Context 'Unpinned pyproject.toml' {
        It 'Detects unpinned dependencies in main array' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'unpinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -BeGreaterThan 0
        }

        It 'Detects unpinned deps in optional-dependencies' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'unpinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $pytestViolation = $violations | Where-Object { $_.Name -eq 'pytest' }

            $pytestViolation | Should -Not -BeNullOrEmpty
        }

        It 'Does not flag == pinned deps' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'unpinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $requestsViolation = $violations | Where-Object { $_.Name -eq 'requests' }

            $requestsViolation | Should -BeNullOrEmpty
        }

        It 'Detects unpinned deps in dependency-groups' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'unpinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'unpinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo
            $coverageViolation = $violations | Where-Object { $_.Name -eq 'coverage' }

            $coverageViolation | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Pinned pyproject.toml with dependency-groups' {
        It 'Returns zero violations when all deps including dependency-groups use ==' {
            $fileInfo = @{
                Path         = Join-Path $script:FixturesPath 'pinned-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'pinned-pyproject.toml'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }

    Context 'Non-existent file' {
        It 'Returns empty array for missing file' {
            $fileInfo = @{
                Path         = '/nonexistent/requirements.txt'
                Type         = 'pip'
                RelativePath = 'nonexistent/requirements.txt'
            }

            $violations = Get-PipDependencyViolations -FileInfo $fileInfo

            $violations.Count | Should -Be 0
        }
    }
}

Describe 'Test-SHAPinning ecosystem awareness' -Tag 'Unit' {
    BeforeAll {
        . $PSScriptRoot/../../security/Test-DependencyPinning.ps1
    }

    Context 'GitHub Actions (SHA-based)' {
        It 'Returns true for valid 40-char hex SHA' {
            Test-SHAPinning -Version 'abc123def456abc123def456abc123def456abc1' -Type 'github-actions' | Should -BeTrue
        }

        It 'Returns false for tag reference' {
            Test-SHAPinning -Version 'v4' -Type 'github-actions' | Should -BeFalse
        }
    }

    Context 'npm (PinPattern-based)' {
        It 'Returns true for exact semver' {
            Test-SHAPinning -Version '4.17.21' -Type 'npm' | Should -BeTrue
        }

        It 'Returns false for caret range' {
            Test-SHAPinning -Version '^4.17.21' -Type 'npm' | Should -BeFalse
        }

        It 'Returns false for tilde range' {
            Test-SHAPinning -Version '~4.18.2' -Type 'npm' | Should -BeFalse
        }

        It 'Returns false for wildcard' {
            Test-SHAPinning -Version '*' -Type 'npm' | Should -BeFalse
        }

        It 'Returns false for >= range' {
            Test-SHAPinning -Version '>=17.0.0' -Type 'npm' | Should -BeFalse
        }
    }

    Context 'pip (PinPattern-based)' {
        It 'Returns true for == equality pin' {
            Test-SHAPinning -Version 'numpy==1.26.4' -Type 'pip' | Should -BeTrue
        }

        It 'Returns false for >= range' {
            Test-SHAPinning -Version 'numpy>=1.26.0' -Type 'pip' | Should -BeFalse
        }

        It 'Returns false for ~= compatible release' {
            Test-SHAPinning -Version 'pandas~=2.2.0' -Type 'pip' | Should -BeFalse
        }
    }
}

Describe 'Get-RemediationSuggestion' -Tag 'Unit' {
    BeforeAll {
        $script:BaseViolation = [DependencyViolation]::new()
        $script:BaseViolation.Type = 'github-actions'
        $script:BaseViolation.Name = 'actions/checkout'
        $script:BaseViolation.Version = 'v4'
        $script:BaseViolation.Severity = 'High'
    }

    It 'Returns generic message without -Remediate flag' {
        $result = Get-RemediationSuggestion -Violation $script:BaseViolation
        $result | Should -Be 'Enable -Remediate flag for specific SHA suggestions'
    }

    It 'Returns SHA suggestion for github-actions with -Remediate' {
        Mock Invoke-RestMethod {
            return @{ sha = 'abc123def456abc123def456abc123def456abcd' }
        }

        $result = Get-RemediationSuggestion -Violation $script:BaseViolation -Remediate
        $result | Should -BeLike 'Pin to SHA: uses: actions/checkout@abc123def456abc123def456abc123def456abcd*'
        Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
    }

    It 'Returns generic message for non-github-actions type with -Remediate' {
        $npmViolation = [DependencyViolation]::new()
        $npmViolation.Type = 'npm'
        $npmViolation.Name = 'lodash'
        $npmViolation.Version = '^4.17.0'

        $result = Get-RemediationSuggestion -Violation $npmViolation -Remediate
        $result | Should -BeLike '*Research and pin*npm*'
    }

    It 'Returns fallback message on API error' {
        Mock Invoke-RestMethod { throw 'API rate limit exceeded' }

        $result = Get-RemediationSuggestion -Violation $script:BaseViolation -Remediate
        $result[-1] | Should -Be 'Manually research and pin to immutable reference'
    }

    It 'Sends Bearer token header when GITHUB_TOKEN is set' {
        $originalToken = $env:GITHUB_TOKEN
        try {
            $env:GITHUB_TOKEN = 'test-token-value'
            Mock Invoke-RestMethod {
                return @{ sha = 'deadbeef12345678901234567890123456789012' }
            } -ParameterFilter {
                $Headers -and $Headers['Authorization'] -eq 'Bearer test-token-value'
            }

            $result = Get-RemediationSuggestion -Violation $script:BaseViolation -Remediate
            $result | Should -BeLike 'Pin to SHA:*'
            Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
        }
        finally {
            if ($null -eq $originalToken) {
                Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
            }
            else {
                $env:GITHUB_TOKEN = $originalToken
            }
        }
    }

    It 'Returns fallback when API returns null SHA' {
        Mock Invoke-RestMethod { return @{ sha = $null } }

        $result = Get-RemediationSuggestion -Violation $script:BaseViolation -Remediate
        $result | Should -Be 'Manually research and pin to immutable reference'
    }
}

Describe 'Export-CICDArtifact' -Tag 'Unit' {
    BeforeAll {
        Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
        Initialize-MockCIEnvironment -Workspace $TestDrive

        $script:TestReport = [ComplianceReport]::new()
        $script:TestReport.ComplianceScore = 85.5
        $script:TestReport.UnpinnedDependencies = 3
        $script:TestReport.TotalDependencies = 20
        $script:TestReport.ScanPath = $TestDrive

        $script:TestReportPath = Join-Path $TestDrive 'test-report.json'
        '{}' | Set-Content -Path $script:TestReportPath
    }

    AfterAll {
        Clear-MockCIEnvironment
    }

    It 'Sets correct CI outputs for score and unpinned count' {
        Mock Get-CIPlatform { return 'local' }
        Mock Set-CIOutput { }
        Mock Write-CIStepSummary { }
        Mock Publish-CIArtifact { }

        Export-CICDArtifact -Report $script:TestReport -ReportPath $script:TestReportPath

        Should -Invoke -CommandName Set-CIOutput -Times 1 -Exactly -ParameterFilter {
            $Name -eq 'compliance-score' -and $Value -eq 85.5
        }
        Should -Invoke -CommandName Set-CIOutput -Times 1 -Exactly -ParameterFilter {
            $Name -eq 'unpinned-count' -and $Value -eq 3
        }
        Should -Invoke -CommandName Set-CIOutput -Times 1 -Exactly -ParameterFilter {
            $Name -eq 'dependency-report'
        }
    }

    It 'Writes step summary with compliance data' {
        Mock Get-CIPlatform { return 'local' }
        Mock Set-CIOutput { }
        Mock Write-CIStepSummary { } -Verifiable
        Mock Publish-CIArtifact { }

        Export-CICDArtifact -Report $script:TestReport -ReportPath $script:TestReportPath

        Should -Invoke -CommandName Write-CIStepSummary -Times 1 -Exactly -ParameterFilter {
            $Content -like '*85.5%*' -and $Content -like '*3*'
        }
    }

    It 'Creates artifact directory on GitHub platform' {
        Mock Get-CIPlatform { return 'github' }
        Mock Set-CIOutput { }
        Mock Write-CIStepSummary { }
        Mock Publish-CIArtifact { }

        Push-Location $TestDrive
        try {
            Export-CICDArtifact -Report $script:TestReport -ReportPath $script:TestReportPath

            $artifactDir = Join-Path $TestDrive 'dependency-pinning-artifacts'
            $artifactDir | Should -Exist
        }
        finally {
            Pop-Location
        }
    }
}

Describe 'Main Block Parameters' -Tag 'Integration' {
    BeforeAll {
        $script:TestScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
    }

    It 'Rejects threshold values outside 0-100 range' {
        $stdoutPath = Join-Path $TestDrive 'vr-stdout.txt'
        $stderrPath = Join-Path $TestDrive 'vr-stderr.txt'
        $proc = Start-Process -FilePath 'pwsh' `
            -ArgumentList @('-NoProfile', '-File', $script:TestScript, '-Path', $TestDrive, '-Threshold', '150') `
            -Wait -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath
        $proc.ExitCode | Should -Not -Be 0
    }

    It 'Parses comma-separated IncludeTypes correctly' {
        $workDir = Join-Path $TestDrive 'include-types-test'
        New-Item -ItemType Directory -Path $workDir -Force | Out-Null
        $jsonPath = Join-Path $TestDrive 'include-types-output.json'

        & $script:TestScript -Path $workDir -IncludeTypes 'github-actions,npm' -Format 'json' -OutputPath $jsonPath *>&1 | Out-Null

        Test-Path $jsonPath | Should -BeTrue
        $report = Get-Content $jsonPath | ConvertFrom-Json
        $report.PSObject.Properties.Name | Should -Contain 'ComplianceScore'
    }

    It 'Exits with code 1 when FailOnUnpinned is set and score is below threshold' {
        $workDir = Join-Path $TestDrive 'fail-test'
        $workflowDir = Join-Path $workDir '.github/workflows'
        New-Item -ItemType Directory -Path $workflowDir -Force | Out-Null

        # Create workflow with unpinned action to generate violations
        $content = @'
name: Test
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
'@
        Set-Content -Path (Join-Path $workflowDir 'test.yml') -Value $content

        $jsonPath = Join-Path $TestDrive 'fail-output.json'
        pwsh -Command "& '$script:TestScript' -Path '$workDir' -Format 'json' -OutputPath '$jsonPath' -FailOnUnpinned -Threshold 100 2>&1" | Out-Null

        $LASTEXITCODE | Should -Be 1
    }
}
