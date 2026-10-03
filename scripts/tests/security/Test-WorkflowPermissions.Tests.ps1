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

    function New-PermissionsWorkflowFixture {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Name,

            [Parameter(Mandatory = $true)]
            [string]$Content
        )

        $directory = Join-Path $TestDrive $Name
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $filePath = Join-Path $directory 'workflow.yml'
        Set-Content -Path $filePath -Value $Content -Encoding utf8
        return $filePath
    }
}

AfterAll {
    Restore-CIEnvironment
    Remove-Module WorkflowSecurityTestHelpers -ErrorAction SilentlyContinue

}

Describe 'Test-WorkflowPermissions' -Tag 'Unit' {
    Context 'File with populated top-level permissions block' {
        It 'Should report a job that inherits the workflow-level grant' {
            $filePath = Join-Path $script:FixturesPath 'workflow-with-permissions.yml'
            $result = Test-WorkflowPermissions -FilePath $filePath
            $result | Should -HaveCount 1
            $result.ViolationType | Should -Be 'MissingJobPermissions'
            $result.Type | Should -Be 'workflow-job-permissions'
            $result.Name | Should -Be 'build'
            $result.Line | Should -Be 7
            $result.Metadata.Job | Should -Be 'build'
        }
    }

    Context 'Four-state classification matrix' {
        It 'Treats absent workflow and job permissions as a file-level violation' {
            $filePath = New-PermissionsWorkflowFixture -Name 'matrix-absent' -Content @'
name: Missing Permissions
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].ViolationType | Should -Be 'MissingPermissions'
        }

        It 'Treats empty workflow permissions and absent job permissions as compliant' {
            $filePath = New-PermissionsWorkflowFixture -Name 'matrix-empty' -Content @'
name: Empty Permissions
on: push
permissions: {}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            @(Test-WorkflowPermissions -FilePath $filePath) | Should -HaveCount 0
        }

        It 'Treats populated workflow permissions and absent job permissions as a job violation' {
            $filePath = New-PermissionsWorkflowFixture -Name 'matrix-inherited' -Content @'
name: Inherited Permissions
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].ViolationType | Should -Be 'MissingJobPermissions'
            $result[0].Name | Should -Be 'build'
            $result[0].Line | Should -Be 6
        }

        It 'Ignores a same-named key before the jobs block when reporting the job line' {
            $filePath = New-PermissionsWorkflowFixture -Name 'duplicate-key-before-jobs' -Content @'
name: Duplicate Key Before Jobs
on:
  workflow_call:
    outputs:
      build:
        description: A key that shares the job name
        value: ${{ jobs.build.outputs.result }}
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)
            $rawLines = @((Get-Content -Path $filePath -Raw) -split "\r?\n")

            $result | Should -HaveCount 1
            $rawLines[$result[0].Line - 1] | Should -Match '^\s{2}build\s*:'
        }

        It 'Treats populated workflow and explicit job permissions as compliant' {
            $filePath = New-PermissionsWorkflowFixture -Name 'matrix-explicit' -Content @'
name: Explicit Job Permissions
on: push
permissions:
  contents: read
jobs:
  build:
    permissions:
      contents: read
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            @(Test-WorkflowPermissions -FilePath $filePath) | Should -HaveCount 0
        }
    }

    Context 'Permissions value shapes' {
        It 'Treats a null-valued permissions block as empty' {
            $filePath = New-PermissionsWorkflowFixture -Name 'null-permissions' -Content @'
name: Null Permissions
on: push
permissions:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            @(Test-WorkflowPermissions -FilePath $filePath) | Should -HaveCount 0
        }

        It 'Treats flow-style populated permissions as populated' {
            $filePath = New-PermissionsWorkflowFixture -Name 'flow-populated-permissions' -Content @'
name: Flow Populated Permissions
on: push
permissions: { contents: read }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].ViolationType | Should -Be 'MissingJobPermissions'
        }

        It 'Treats scalar read-all permissions as populated' {
            $filePath = New-PermissionsWorkflowFixture -Name 'scalar-permissions' -Content @'
name: Scalar Permissions
on: push
permissions: read-all
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].ViolationType | Should -Be 'MissingJobPermissions'
        }

        It 'Enumerates jobs indented with four spaces' {
            $filePath = New-PermissionsWorkflowFixture -Name 'four-space-jobs' -Content @'
name: Four Space Jobs
on: push
permissions:
    contents: read
jobs:
    build:
        runs-on: ubuntu-latest
        steps:
            - run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].Name | Should -Be 'build'
        }
    }

    Context 'Parser edge cases' {
        It 'Does not count a step-level permissions key as a job declaration' {
            $filePath = New-PermissionsWorkflowFixture -Name 'nested-permissions-key' -Content @'
name: Nested Permissions Key
on: push
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Render config
        with:
          permissions: read-all
        run: echo hello
'@

            $result = @(Test-WorkflowPermissions -FilePath $filePath)

            $result | Should -HaveCount 1
            $result[0].ViolationType | Should -Be 'MissingJobPermissions'
            $result[0].Name | Should -Be 'build'
        }

        It 'Returns no violations for malformed YAML without throwing' {
            $filePath = New-PermissionsWorkflowFixture -Name 'malformed' -Content @'
name: Malformed
on: push
permissions:
  contents: read
jobs:
  build:
   - this is not a mapping
     and: [unclosed
'@

            { Test-WorkflowPermissions -FilePath $filePath } | Should -Not -Throw
            @(Test-WorkflowPermissions -FilePath $filePath) | Should -HaveCount 0
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
        $report.Violations | Should -HaveCount 2
        $report.Violations.ViolationType | Should -Contain 'MissingPermissions'
        $report.Violations.ViolationType | Should -Contain 'MissingJobPermissions'
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
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-empty-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'console-clean/nested/results.txt'
        Invoke-WorkflowPermissionsCheck -Path $testPath -Format console -OutputPath $outputPath | Out-Null

        $consoleOutput = Get-Content -Path $outputPath -Raw
        $consoleOutput | Should -Match 'workflow\(s\) and 1 job\(s\) passed the permissions check\.'
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
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-empty-permissions.yml') -Destination $testPath

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

    It 'exits 1 when FailOnViolation finds a permissions violation' {
        $testPath = Join-Path $TestDrive 'entry-violation'
        New-Item -ItemType Directory -Path $testPath -Force | Out-Null
        Copy-Item -Path (Join-Path $script:FixturesPath 'workflow-without-permissions.yml') -Destination $testPath

        $outputPath = Join-Path $TestDrive 'entry-violation.json'
        $exitCode = Invoke-SecurityLinterScript -ScriptPath $script:ScriptPath -ArgumentList @('-Path', $testPath, '-Format', 'json', '-OutputPath', $outputPath, '-FailOnViolation')
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
