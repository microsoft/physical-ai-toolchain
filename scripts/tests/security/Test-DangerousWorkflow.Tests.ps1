#Requires -Modules Pester

# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

using module ../../security/Modules/SecurityClasses.psm1

BeforeAll {
    . (Join-Path $PSScriptRoot '../../security/Test-DangerousWorkflow.ps1')

    Import-Module powershell-yaml -ErrorAction Stop
    Import-Module (Join-Path $PSScriptRoot '../Mocks/GitMocks.psm1') -Force
    Import-Module (Join-Path $PSScriptRoot 'WorkflowSecurityTestHelpers.psm1') -Force

    Save-CIEnvironment

    Mock Write-Host {}
    Mock Write-CIAnnotation {} -ModuleName CIHelpers

    $script:RepoWorkflowsPath = Join-Path $PSScriptRoot '../../../.github/workflows'

    function Invoke-DangerousWorkflowFixture {
        param(
            [Parameter(Mandatory = $true)]
            [string]$FixturePath,

            [Parameter(Mandatory = $false)]
            [ValidateSet('json', 'sarif', 'console')]
            [string]$Format = 'json',

            [Parameter(Mandatory = $false)]
            [string]$OutputPath = '',

            [Parameter(Mandatory = $false)]
            [switch]$FailOnViolation
        )

        if ([string]::IsNullOrWhiteSpace($OutputPath)) {
            $OutputPath = Join-Path $TestDrive ([System.Guid]::NewGuid().ToString() + '.out')
        }

        $params = @{
            Path       = $FixturePath
            Format     = $Format
            OutputPath = $OutputPath
        }

        if ($FailOnViolation) {
            $params.FailOnViolation = $true
        }

        return Invoke-DangerousWorkflowCheck @params
    }
}

AfterAll {
    Restore-CIEnvironment
    Remove-Module WorkflowSecurityTestHelpers -ErrorAction SilentlyContinue

}

Describe 'Test-DangerousWorkflow template injection' -Tag 'Unit' {
    It 'flags template injection in run blocks for <Expr>' -ForEach @(
        @{Expr='github.head_ref'}
        @{Expr='github.event.pull_request.body'}
        @{Expr='github.event.pull_request.head.ref'}
        @{Expr='github.event.pull_request.head.label'}
        @{Expr='github.event.issue.body'}
        @{Expr='github.event.comment.body'}
        @{Expr='github.event.review.body'}
        @{Expr='github.event.review_comment.body'}
        @{Expr='github.event.discussion.title'}
        @{Expr='github.event.discussion.body'}
        @{Expr='github.event.pages.0.page_name'}
        @{Expr='github.event.head_commit.message'}
        @{Expr='github.event.head_commit.author.email'}
        @{Expr='github.event.head_commit.author.name'}
        @{Expr='github.event.commits.0.message'}
        @{Expr='github.event.commits.0.author.email'}
        @{Expr='github.event.commits.0.author.name'}
        @{Expr='github.event.workflow_run.head_branch'}
        @{Expr='github.event.workflow_run.display_title'}
        @{Expr="github.event.pull_request['title']"}
        @{Expr="github['event']['issue']['title']"}
    ) {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name "template-injection-$( [System.Guid]::NewGuid().ToString() )" -Content @"
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo `"\`${{ $Expr }}\`"
"@

        $outputPath = Join-Path $TestDrive "template-injection-$( [System.Guid]::NewGuid().ToString() ).json"
        $exitCode = Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/template-injection'
        $report.Violations[0].Line | Should -Be 8
    }

    It 'flags multiline run-block template injection expressions' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'multiline-template-injection' -Content @'
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "before"
          echo "${{ github.event.pull_request.title }}"
'@

        $outputPath = Join-Path $TestDrive 'multiline-template-injection.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/template-injection'
        $report.Violations[0].Line | Should -Be 10
    }

    It 'flags template injection inside github-script blocks' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'github-script-injection' -Content @'
name: test
on:
  issues:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            console.log("${{ github.event.issue.title }}")
      - name: later
        run: echo hi
'@

        $outputPath = Join-Path $TestDrive 'github-script-injection.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/template-injection'
        # The injection is on line 11 (the script interpolation), not the innocent 'run: echo hi' at line 13.
        $report.Violations[0].Line | Should -Be 11
    }

    It 'reports the correct line for an injection in a later job regardless of job order' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'multi-job-order' -Content @'
name: test
on:
  pull_request_target:
jobs:
  alpha:
    runs-on: ubuntu-latest
    steps:
      - run: echo "safe"
  beta:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.event.pull_request.title }}"
'@

        $outputPath = Join-Path $TestDrive 'multi-job-order.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/template-injection'
        $report.Violations[0].Line | Should -Be 12
    }

    It 'does not flag a with.script input on a non-github-script action' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'non-github-script-with-script' -Content @'
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: some/other-action@v1
        with:
          script: echo "${{ github.event.pull_request.title }}"
'@

        $outputPath = Join-Path $TestDrive 'non-github-script-with-script.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }

    It 'does not flag trusted expressions in run blocks: <Expr>' -ForEach @(
        @{Expr='github.sha'}
        @{Expr='github.repository'}
        @{Expr='github.run_id'}
    ) {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name "trusted-expression-$( [System.Guid]::NewGuid().ToString() )" -Content @"
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo `"\`${{ $Expr }}\`"
"@

        $outputPath = Join-Path $TestDrive "trusted-expression-$( [System.Guid]::NewGuid().ToString() ).json"
        $exitCode = Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }

    It 'does not flag output-derived expressions as out-of-scope indirect derivations' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'output-derived-injection' -Content @'
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: setup
        run: echo "value=x" >> "$GITHUB_OUTPUT"
      - run: echo "${{ steps.setup.outputs.value }}"
'@

        $outputPath = Join-Path $TestDrive 'output-derived-injection.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }

    It 'continues scanning when one workflow file is malformed YAML' {
        $fixturePath = Join-Path $TestDrive 'malformed-yaml'
        New-Item -ItemType Directory -Path $fixturePath -Force | Out-Null

        $badWorkflowPath = Join-Path $fixturePath 'bad.yml'
        Set-Content -Path $badWorkflowPath -Value @'
name: broken
on:
  pull_request_target:
    jobs:
      build:
        runs-on: ubuntu-latest
        steps:
          - run: echo "${{ github.event.pull_request.title }}"
              bad: [unterminated
'@ -Encoding utf8

        $validWorkflowPath = Join-Path $fixturePath 'good.yml'
        Set-Content -Path $validWorkflowPath -Value (New-RunInjectionWorkflowContent) -Encoding utf8

        $outputPath = Join-Path $TestDrive 'malformed-yaml.json'
        { Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath } | Should -Not -Throw

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/template-injection'
    }

    It 'writes console output for violations' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'console-output' -Content (New-RunInjectionWorkflowContent)

        $outputPath = Join-Path $TestDrive 'console-output.txt'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format console -OutputPath $outputPath | Out-Null

        $consoleOutput = Get-Content -Path $outputPath -Raw
        $consoleOutput | Should -Match 'Dangerous workflow findings found:'
        $consoleOutput | Should -Match 'dangerous-workflow/template-injection'
    }

    It 'reports no findings for a clean workflow' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'clean-workflow' -Content @'
name: test
on:
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "no interpolation here"
'@

        $outputPath = Join-Path $TestDrive 'clean-workflow.txt'
        $exitCode = Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format console -OutputPath $outputPath

        $exitCode | Should -Be 0
        $consoleOutput = Get-Content -Path $outputPath -Raw
        $consoleOutput | Should -Match 'No dangerous workflow findings were detected.'
    }

    It 'returns a non-zero exit code when FailOnViolation is used' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'fail-on-violation' -Content (New-RunInjectionWorkflowContent)

        $outputPath = Join-Path $TestDrive 'fail-on-violation.json'
        $exitCode = Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath -FailOnViolation

        $exitCode | Should -Be 1
    }
}

Describe 'Test-DangerousWorkflow untrusted checkout' -Tag 'Unit' {
    It 'flags a pull_request_target checkout of <Expr>' -ForEach @(
        @{Expr='github.event.pull_request.head.sha'}
        @{Expr='github.head_ref'}
        @{Expr='github.event.pull_request.head.ref'}
        @{Expr='github.event.pull_request.merge_commit_sha'}
        @{Expr="github.event.pull_request.head['sha']"}
        @{Expr='refs/pull/${{ github.event.number }}/merge'}
    ) {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name "untrusted-checkout-sha-$( [System.Guid]::NewGuid().ToString() )" -Content @"
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: `${{ $Expr }}
      - run: ./build.sh
"@

        $outputPath = Join-Path $TestDrive "untrusted-checkout-sha-$( [System.Guid]::NewGuid().ToString() ).json"
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 1
        $report.Violations[0].Metadata.RuleId | Should -Be 'dangerous-workflow/untrusted-checkout'
        $report.Violations[0].Line | Should -Be 10
    }

    It 'does not flag an untrusted checkout ref under the pull_request trigger' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'safe-trigger-checkout' -Content @'
name: test
on:
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
'@

        $outputPath = Join-Path $TestDrive 'safe-trigger-checkout.json'
        $exitCode = Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }

    It 'does not flag a pull_request_target checkout without an untrusted ref' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'pr-target-safe-ref' -Content @'
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.base.sha }}
'@

        $outputPath = Join-Path $TestDrive 'pr-target-safe-ref.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }

    It 'does not flag a pull_request_target checkout with no explicit ref' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'pr-target-no-ref' -Content @'
name: test
on:
  pull_request_target:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "safe base checkout"
'@

        $outputPath = Join-Path $TestDrive 'pr-target-no-ref.json'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format json -OutputPath $outputPath | Out-Null

        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
    }
}

Describe 'ConvertTo-DangerousWorkflowSarif' -Tag 'Unit' {
    It 'declares both dangerous-workflow rules in the SARIF driver' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'sarif-output' -Content (New-RunInjectionWorkflowContent)

        $outputPath = Join-Path $TestDrive 'sarif-output.sarif'
        Invoke-DangerousWorkflowFixture -FixturePath $fixturePath -Format sarif -OutputPath $outputPath | Out-Null

        $sarif = Get-JsonReport -Path $outputPath
        $sarif.version | Should -Be '2.1.0'
        $sarif.runs[0].tool.driver.name | Should -Be 'Test-DangerousWorkflow'
        $ruleIds = $sarif.runs[0].tool.driver.rules.id
        $ruleIds | Should -Contain 'dangerous-workflow/template-injection'
        $ruleIds | Should -Contain 'dangerous-workflow/untrusted-checkout'
        $sarif.runs[0].results | Should -HaveCount 1
        $sarif.runs[0].results[0].ruleId | Should -Be 'dangerous-workflow/template-injection'
        $sarif.runs[0].results[0].level | Should -Be 'error'
    }
}

Describe 'Test-DangerousWorkflow node helpers' -Tag 'Unit' {
    Context 'Get-NodeMember' {
        It 'returns null for a null node' {
            Get-NodeMember -Node $null -Key 'on' | Should -BeNullOrEmpty
        }

        It 'reads a present key from a PSObject node' {
            $node = [PSCustomObject]@{ ref = 'main' }
            Get-NodeMember -Node $node -Key 'ref' | Should -Be 'main'
        }

        It 'returns null for an absent key on a PSObject node' {
            $node = [PSCustomObject]@{ ref = 'main' }
            Get-NodeMember -Node $node -Key 'missing' | Should -BeNullOrEmpty
        }
    }

    Context 'Get-ExpressionMatches' {
        It 'returns nothing for empty text' {
            Get-ExpressionMatches -Text '' | Should -BeNullOrEmpty
        }

        It 'extracts trimmed expressions from interpolations in order' {
            $expressions = Get-ExpressionMatches -Text 'a ${{ github.head_ref }} b ${{ github.sha }}'
            $expressions | Should -Be @('github.head_ref', 'github.sha')
        }
    }

    Context 'Test-IsUntrustedInjectionExpression' {
        It 'returns false for a whitespace-only expression' {
            Test-IsUntrustedInjectionExpression -Expression '   ' | Should -BeFalse
        }
    }

    Context 'Test-IsUntrustedCheckoutRef' {
        It 'returns false for a whitespace-only ref' {
            Test-IsUntrustedCheckoutRef -Ref '   ' | Should -BeFalse
        }
    }

    Context 'Test-HasPullRequestTargetTrigger' {
        It 'returns false when no pull_request_target trigger is present' {
            Test-HasPullRequestTargetTrigger -Yaml @{ jobs = @{} } | Should -BeFalse
        }

        It 'detects the boolean-folded on key' {
            Test-HasPullRequestTargetTrigger -Yaml @{ $true = @{ pull_request_target = $null } } | Should -BeTrue
        }

        It 'detects a string-scalar on trigger' {
            Test-HasPullRequestTargetTrigger -Yaml @{ 'on' = 'pull_request_target' } | Should -BeTrue
        }

        It 'does not flag an unrelated string-scalar on trigger' {
            Test-HasPullRequestTargetTrigger -Yaml @{ 'on' = 'push' } | Should -BeFalse
        }

        It 'detects a list-form on trigger' {
            Test-HasPullRequestTargetTrigger -Yaml @{ 'on' = @('push', 'pull_request_target') } | Should -BeTrue
        }
    }

    Context 'Find-NextMatchingLine' {
        It 'returns 0 when no line matches the pattern' {
            Find-NextMatchingLine -Lines @('alpha', 'beta') -Pattern 'zzz' | Should -Be 0
        }

        It 'returns the 1-based index of the first matching line' {
            Find-NextMatchingLine -Lines @('alpha', 'beta', 'gamma') -Pattern 'beta' | Should -Be 2
        }
    }
}

Describe 'Invoke-DangerousWorkflowCheck output handling' -Tag 'Unit' {
    It 'writes to the default json output path when none is supplied' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'default-json' -Content (New-RunInjectionWorkflowContent)

        Push-Location $TestDrive
        try {
            $exitCode = Invoke-DangerousWorkflowCheck -Path $fixturePath -Format json
            $exitCode | Should -Be 0
            Test-Path (Join-Path $TestDrive 'logs/dangerous-workflow-results.json') | Should -BeTrue
        }
        finally {
            Pop-Location
        }
    }

    It 'writes to the default sarif output path when none is supplied' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'default-sarif' -Content (New-RunInjectionWorkflowContent)

        Push-Location $TestDrive
        try {
            Invoke-DangerousWorkflowCheck -Path $fixturePath -Format sarif | Out-Null
            Test-Path (Join-Path $TestDrive 'logs/dangerous-workflow-results.sarif') | Should -BeTrue
        }
        finally {
            Pop-Location
        }
    }

    It 'returns exit code 1 when the powershell-yaml module is unavailable' {
        Mock Get-Command { $null } -ParameterFilter { $Name -eq 'ConvertFrom-Yaml' }

        Invoke-DangerousWorkflowCheck -Path $TestDrive -Format json -OutputPath (Join-Path $TestDrive 'no-yaml.json') | Should -Be 1
    }
}

Describe 'Test-DangerousWorkflow entry point' -Tag 'Unit' {
    BeforeAll {
        $script:ScriptPath = Join-Path $PSScriptRoot '../../security/Test-DangerousWorkflow.ps1'
    }

    It 'exits 0 when invoked as a script against a clean workflow' {
        $fixturePath = New-WorkflowFixture -Root $TestDrive -Name 'entry-clean' -Content @'
name: test
on:
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: echo "safe"
'@

        $outputPath = Join-Path $TestDrive 'entry-clean.json'
        $exitCode = Invoke-SecurityLinterScript -ScriptPath $script:ScriptPath -ArgumentList @('-Path', $fixturePath, '-Format', 'json', '-OutputPath', $outputPath)
        $exitCode | Should -Be 0
    }

    It 'exits 1 when invoked as a script with a non-existent path' {
        $missingPath = Join-Path $TestDrive 'does-not-exist'
        $outputPath = Join-Path $TestDrive 'entry-fatal.json'
        $exitCode = Invoke-SecurityLinterScript -ScriptPath $script:ScriptPath -ArgumentList @('-Path', $missingPath, '-Format', 'json', '-OutputPath', $outputPath)
        $exitCode | Should -Be 1
    }
}

Describe 'Repository dangerous workflow invariant' -Tag 'Unit' {
    It 'No workflow in .github/workflows contains a dangerous pattern' {
        $outputPath = Join-Path $TestDrive 'repo-dangerous.json'
        $exitCode = Invoke-DangerousWorkflowCheck -Path $script:RepoWorkflowsPath -Format json -OutputPath $outputPath -FailOnViolation

        $exitCode | Should -Be 0
        $report = Get-JsonReport -Path $outputPath
        $report.Violations | Should -HaveCount 0
        $report.TotalFiles | Should -BeGreaterThan 0
        $report.ScannedFiles | Should -BeGreaterThan 0
    }
}
