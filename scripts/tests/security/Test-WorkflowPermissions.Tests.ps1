#Requires -Modules Pester

# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

using module ../../security/Modules/SecurityClasses.psm1

BeforeAll {
    . (Join-Path $PSScriptRoot '../../security/Test-WorkflowPermissions.ps1')

    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    Import-Module (Join-Path $PSScriptRoot 'WorkflowSecurityTestHelpers.psm1') -Force

    Save-CIEnvironment

    $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
    $script:RepoWorkflowsPath = Join-Path $PSScriptRoot '../../../.github/workflows'
}

AfterAll {
    Restore-CIEnvironment
    Remove-Module WorkflowSecurityTestHelpers -ErrorAction SilentlyContinue

}

Describe 'Test-WorkflowPermissions' -Tag 'Unit' {
    Context 'File with top-level permissions block' {
        It 'Should return null for workflow with permissions' {
            $filePath = Join-Path $script:FixturesPath 'workflow-with-permissions.yml'
            Test-WorkflowPermissions -FilePath $filePath | Should -BeNullOrEmpty
        }
    }

    Context 'File with empty permissions block' {
        It 'Should return null for workflow with empty permissions' {
            $filePath = Join-Path $script:FixturesPath 'workflow-empty-permissions.yml'
            Test-WorkflowPermissions -FilePath $filePath | Should -BeNullOrEmpty
        }
    }

    Context 'File without permissions block' {
        BeforeAll {
            $script:MissingResult = Test-WorkflowPermissions -FilePath (Join-Path $script:FixturesPath 'workflow-without-permissions.yml')
        }

        It 'Should return a violation' {
            $script:MissingResult | Should -Not -BeNullOrEmpty
        }

        It 'Should set ViolationType to MissingPermissions' {
            $script:MissingResult.ViolationType | Should -Be 'MissingPermissions'
        }

        It 'Should set Severity to High' {
            $script:MissingResult.Severity | Should -Be 'High'
        }

        It 'Should set Type to workflow-permissions' {
            $script:MissingResult.Type | Should -Be 'workflow-permissions'
        }

        It 'Should set Line to 0 for file-level violation' {
            $script:MissingResult.Line | Should -Be 0
        }

        It 'Should include FullPath in Metadata' {
            $expected = Join-Path $script:FixturesPath 'workflow-without-permissions.yml'
            $script:MissingResult.Metadata.FullPath | Should -Be $expected
        }
    }
}

Describe 'ConvertTo-PermissionsSarif' -Tag 'Unit' {
    Context 'With violations' {
        BeforeAll {
            $violation = [DependencyViolation]::new()
            $violation.File = 'test.yml'
            $violation.Line = 0
            $violation.Type = 'workflow-permissions'
            $violation.Name = 'test.yml'
            $violation.Severity = 'High'
            $violation.ViolationType = 'MissingPermissions'
            $violation.Description = 'Missing top-level permissions'
            $violation.Remediation = 'Add permissions block'
            $script:Sarif = ConvertTo-PermissionsSarif -Violations @($violation)
        }

        It 'Should produce valid SARIF structure' {
            $script:Sarif.'$schema' | Should -Not -BeNullOrEmpty
            $script:Sarif.version | Should -Be '2.1.0'
            $script:Sarif.runs | Should -HaveCount 1
            $script:Sarif.runs[0].tool.driver.name | Should -Be 'Test-WorkflowPermissions'
        }

        It 'Should include missing-permissions rule and result' {
            $script:Sarif.runs[0].tool.driver.rules[0].id | Should -Be 'missing-permissions'
            $script:Sarif.runs[0].results | Should -HaveCount 1
        }
    }

    Context 'Without violations' {
        It 'Should produce valid SARIF with empty results' {
            $sarif = ConvertTo-PermissionsSarif -Violations @()
            $sarif.version | Should -Be '2.1.0'
            $sarif.runs[0].results | Should -HaveCount 0
        }
    }
}

Describe 'Invoke-WorkflowPermissionsCheck' -Tag 'Unit' {
    BeforeAll {
        Mock Write-CIAnnotation { } -ModuleName CIHelpers
        Mock Write-Host { }
    }

    It 'Should return 0 and detect missing permissions in soft-fail mode' {
        $testPath = Join-Path $TestDrive 'mixed-scan'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-with-permissions.yml') -Destination $testPath
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'mixed-results.json'
        $exitCode = Invoke-WorkflowPermissionsCheck -Path $testPath -OutputPath $outputPath

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
    }

    It 'Should fail with FailOnViolation when violations exist' {
        $testPath = Join-Path $TestDrive 'fail-scan'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $exitCode = Invoke-WorkflowPermissionsCheck -Path $testPath -OutputPath (Join-Path $TestDrive 'fail-results.json') -FailOnViolation
        $exitCode | Should -Be 1
    }

    It 'Should return exit code 0 when all workflows have permissions' {
        $testPath = Join-Path $TestDrive 'pass-scan'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-with-permissions.yml') -Destination $testPath
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-empty-permissions.yml') -Destination $testPath

        $exitCode = Invoke-WorkflowPermissionsCheck -Path $testPath -OutputPath (Join-Path $TestDrive 'pass-results.json') -FailOnViolation
        $exitCode | Should -Be 0
    }

    It 'Should exclude specified files' {
        $testPath = Join-Path $TestDrive 'exclude-scan'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $exitCode = Invoke-WorkflowPermissionsCheck -Path $testPath -OutputPath (Join-Path $TestDrive 'exclude-results.json') -ExcludePaths 'workflow-without-permissions.yml' -FailOnViolation
        $exitCode | Should -Be 0
    }

    It 'Should produce SARIF output' {
        $testPath = Join-Path $TestDrive 'sarif-scan'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'sarif-results.json'
        Invoke-WorkflowPermissionsCheck -Path $testPath -Format sarif -OutputPath $outputPath | Out-Null

        $content = Get-JsonReport -Path $outputPath
        $content.version | Should -Be '2.1.0'
        $content.'$schema' | Should -Not -BeNullOrEmpty
    }

    It 'Should write a console summary when all workflows have permissions' {
        $testPath = Join-Path $TestDrive 'console-clean'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-with-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'console-clean/nested/results.txt'
        Invoke-WorkflowPermissionsCheck -Path $testPath -Format console -OutputPath $outputPath | Out-Null

        $consoleOutput = Get-Content -Path $outputPath -Raw
        $consoleOutput | Should -Match 'workflow\(s\) have a top-level permissions block\.'
    }

    It 'Should write a console summary listing permissions violations' {
        $testPath = Join-Path $TestDrive 'console-violation'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'console-violation-results.txt'
        Invoke-WorkflowPermissionsCheck -Path $testPath -Format console -OutputPath $outputPath | Out-Null

        $consoleOutput = Get-Content -Path $outputPath -Raw
        $consoleOutput | Should -Match 'Workflow permissions violations found:'
        $consoleOutput | Should -Match 'Remediation:'
    }
}

Describe 'Test-WorkflowPermissions entry point' -Tag 'Unit' {
    BeforeAll {
        $script:ScriptPath = Join-Path $PSScriptRoot '../../security/Test-WorkflowPermissions.ps1'
    }

    It 'exits 0 when invoked as a script against compliant workflows' {
        $testPath = Join-Path $TestDrive 'entry-clean'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-with-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'entry-clean.json'
        $exitCode = Invoke-SecurityLinterScript -ScriptPath $script:ScriptPath -ArgumentList @('-Path', $testPath, '-Format', 'json', '-OutputPath', $outputPath, '-FailOnViolation')
        $exitCode | Should -Be 0
    }

    It 'exits 1 when invoked as a script with a non-existent path' {
        $missingPath = Join-Path $TestDrive 'does-not-exist'
        $outputPath = Join-Path $TestDrive 'entry-fatal.json'
        $exitCode = Invoke-SecurityLinterScript -ScriptPath $script:ScriptPath -ArgumentList @('-Path', $missingPath, '-Format', 'json', '-OutputPath', $outputPath)
        $exitCode | Should -Be 1
    }
}

Describe 'Repository workflow permissions invariant' -Tag 'Unit' {
    It 'Every workflow in .github/workflows declares a top-level permissions block' {
        $outputPath = Join-Path $TestDrive 'repo-permissions.json'
        $exitCode = Invoke-WorkflowPermissionsCheck -Path $script:RepoWorkflowsPath -Format json -OutputPath $outputPath -FailOnViolation

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
        $report.TotalFiles | Should -BeGreaterThan 0
        $report.ScannedFiles | Should -BeGreaterThan 0
    }
}
