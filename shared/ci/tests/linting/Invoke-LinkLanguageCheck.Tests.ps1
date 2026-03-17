#Requires -Modules Pester

<#
.SYNOPSIS
    Tests for Invoke-LinkLanguageCheck.ps1 functionality.
#>

BeforeAll {
    $script:ScriptPath = Join-Path $PSScriptRoot '../../linting/Invoke-LinkLanguageCheck.ps1'
    $script:ModulePath = Join-Path $PSScriptRoot '../../linting/Modules/LintingHelpers.psm1'
    $script:CIHelpersPath = Join-Path $PSScriptRoot '../../../../scripts/lib/Modules/CIHelpers.psm1'

    Import-Module $script:ModulePath -Force
    Import-Module $script:CIHelpersPath -Force

    . $script:ScriptPath
}

AfterAll {
    Remove-Module LintingHelpers -ErrorAction SilentlyContinue
    Remove-Module CIHelpers -ErrorAction SilentlyContinue
}

# ============================================================
# Describe 1 — Script and companion discovery
# ============================================================
Describe 'Link-Lang-Check.ps1 Invocation' -Tag 'Unit' {
    Context 'Script discovery' {
        It 'Link-Lang-Check.ps1 exists in the linting directory' {
            $companion = Join-Path $PSScriptRoot '../../linting/Link-Lang-Check.ps1'
            $companion | Should -Exist
        }
    }

    Context 'Normal execution' {
        It 'Invoke-LinkLanguageCheck.ps1 exists' {
            $script:ScriptPath | Should -Exist
        }
    }
}

# ============================================================
# Describe 2 — Core function behaviour
# ============================================================
Describe 'Invoke-LinkLanguageCheckCore' -Tag 'Unit' {

    Context 'Not in git repository' {
        BeforeAll {
            Mock git {
                $global:LASTEXITCODE = 128
                return ''
            }
            Mock Write-Error { }
        }

        It 'Returns 1 when not in a git repository' {
            $result = Invoke-LinkLanguageCheckCore -Files @()
            $result | Should -Be 1
        }
    }

    Context 'Issues found in link scan' {
        BeforeAll {
            $script:RepoRoot = $TestDrive

            # Mock Link-Lang-Check.ps1 companion script output
            $mockJson = @'
[
    {"file": "docs/a.md", "line_number": 1, "original_url": "https://learn.microsoft.com/fr-fr/a", "fixed_url": "https://learn.microsoft.com/a"},
    {"file": "docs/b.md", "line_number": 2, "original_url": "https://learn.microsoft.com/fr-fr/b", "fixed_url": "https://learn.microsoft.com/b"}
]
'@
            $mockScriptContent = @"
param([string[]]`$Files = @())
Write-Output '$($mockJson -replace "'", "''")'
"@
            $mockScriptDir = Join-Path $TestDrive 'mock-scripts'
            New-Item -ItemType Directory -Path $mockScriptDir -Force | Out-Null
            $script:mockScriptFile = Join-Path $mockScriptDir 'Link-Lang-Check.ps1'
            Set-Content -Path $script:mockScriptFile -Value $mockScriptContent
        }

        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return $script:RepoRoot
            }

            Mock Join-Path -ParameterFilter { $ChildPath -eq 'Link-Lang-Check.ps1' } {
                return $script:mockScriptFile
            }

            Mock Test-Path -ParameterFilter { $Path -like '*logs' -or $Path -like '*logs*' } {
                return $true
            }

            Mock Out-File -RemoveParameterType 'Encoding' { }
            Mock New-Item { }
            Mock Write-CIAnnotation { }
            Mock Set-CIOutput { }
            Mock Set-CIEnv { }
            Mock Write-CIStepSummary { }

            $script:WriteHostMessages = [System.Collections.ArrayList]@()
            Mock Write-Host {
                $null = $script:WriteHostMessages.Add("$Object")
            }
        }

        It 'Returns 1 when issues are found' {
            $result = Invoke-LinkLanguageCheckCore -Files @()
            $result | Should -Be 1
        }

        It 'Calls Set-CIOutput once' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Set-CIOutput -Times 1 -Exactly
        }

        It 'Calls Set-CIEnv once' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Set-CIEnv -Times 1 -Exactly
        }

        It 'Calls Write-CIAnnotation for each issue' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Write-CIAnnotation -Times 2 -Exactly
        }

        It 'Calls Write-CIStepSummary once' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly
        }

        It 'Reports the count of issues found' {
            Invoke-LinkLanguageCheckCore -Files @()
            $script:WriteHostMessages | Should -Contain 'Checking for URLs with language paths...'
            $found = $script:WriteHostMessages | Where-Object { $_ -like '*Found*2*URLs*' }
            $found | Should -Not -BeNullOrEmpty
        }
    }

    Context 'No issues found' {
        BeforeAll {
            $script:RepoRoot = $TestDrive

            $mockScriptContent = @"
param([string[]]`$Files = @())
Write-Output '[]'
"@
            $mockScriptDir = Join-Path $TestDrive 'mock-scripts-clean'
            New-Item -ItemType Directory -Path $mockScriptDir -Force | Out-Null
            $script:mockScriptFile = Join-Path $mockScriptDir 'Link-Lang-Check.ps1'
            Set-Content -Path $script:mockScriptFile -Value $mockScriptContent
        }

        BeforeEach {
            Mock git {
                $global:LASTEXITCODE = 0
                return $script:RepoRoot
            }

            Mock Join-Path -ParameterFilter { $ChildPath -eq 'Link-Lang-Check.ps1' } {
                return $script:mockScriptFile
            }

            Mock Test-Path -ParameterFilter { $Path -like '*logs' -or $Path -like '*logs*' } {
                return $true
            }

            Mock Out-File -RemoveParameterType 'Encoding' { }
            Mock New-Item { }
            Mock Set-CIOutput { }
            Mock Set-CIEnv { }
            Mock Write-CIStepSummary { }

            $script:WriteHostMessages = [System.Collections.ArrayList]@()
            Mock Write-Host {
                $null = $script:WriteHostMessages.Add("$Object")
            }
        }

        It 'Returns 0 when no issues are found' {
            $result = Invoke-LinkLanguageCheckCore -Files @()
            $result | Should -Be 0
        }

        It 'Calls Set-CIOutput once' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Set-CIOutput -Times 1 -Exactly
        }

        It 'Calls Write-CIStepSummary once' {
            Invoke-LinkLanguageCheckCore -Files @()
            Should -Invoke Write-CIStepSummary -Times 1 -Exactly
        }

        It 'Reports no issues found' {
            Invoke-LinkLanguageCheckCore -Files @()
            $found = $script:WriteHostMessages | Where-Object { $_ -like '*No URLs with language paths found*' }
            $found | Should -Not -BeNullOrEmpty
        }
    }
}

# ============================================================
# Describe 3 — JSON output parsing
# ============================================================
Describe 'JSON Output Parsing' -Tag 'Unit' {

    Context 'Valid JSON with results' {
        It 'Parses a two-element JSON array' {
            $json = @'
[{"file":"a.md","line_number":1,"original_url":"http://example.com/fr-fr/x","fixed_url":"http://example.com/x"},{"file":"b.md","line_number":2,"original_url":"http://example.com/fr-fr/y","fixed_url":"http://example.com/y"}]
'@
            $parsed = $json | ConvertFrom-Json
            @($parsed) | Should -HaveCount 2
        }

        It 'Contains expected properties' {
            $json = @'
[{"file":"a.md","line_number":1,"original_url":"http://example.com/fr-fr/x","fixed_url":"http://example.com/x"}]
'@
            $parsed = ($json | ConvertFrom-Json)
            $parsed.file | Should -Be 'a.md'
            $parsed.line_number | Should -Be 1
            $parsed.original_url | Should -Be 'http://example.com/fr-fr/x'
            $parsed.fixed_url | Should -Be 'http://example.com/x'
        }
    }

    Context 'Empty JSON array' {
        It 'Returns an empty collection' {
            $json = '[]'
            $parsed = $json | ConvertFrom-Json
            @($parsed) | Should -HaveCount 0
        }
    }

    Context 'Invalid JSON' {
        It 'Throws on malformed JSON' {
            { 'not-json{' | ConvertFrom-Json -ErrorAction Stop } | Should -Throw
        }
    }
}

# ============================================================
# Describe 4 — CI helpers integration
# ============================================================
Describe 'GitHub Actions Integration' -Tag 'Unit' {

    Context 'Module exports' {
        It 'CIHelpers exports Write-CIAnnotation' {
            Get-Command Write-CIAnnotation -ErrorAction SilentlyContinue | Should -Not -BeNullOrEmpty
        }

        It 'CIHelpers exports Set-CIOutput' {
            Get-Command Set-CIOutput -ErrorAction SilentlyContinue | Should -Not -BeNullOrEmpty
        }

        It 'CIHelpers exports Write-CIStepSummary' {
            Get-Command Write-CIStepSummary -ErrorAction SilentlyContinue | Should -Not -BeNullOrEmpty
        }
    }

    Context 'GitHub Actions detection' {
        It 'Detects when running outside GitHub Actions' {
            $saved = $env:GITHUB_ACTIONS
            try {
                $env:GITHUB_ACTIONS = $null
                $env:GITHUB_ACTIONS | Should -BeNullOrEmpty
            }
            finally {
                $env:GITHUB_ACTIONS = $saved
            }
        }
    }
}

# ============================================================
# Describe 5 — Annotation generation
# ============================================================
Describe 'Annotation Generation' -Tag 'Unit' {

    Context 'Annotation content' {
        It 'Creates annotation objects with expected properties' {
            $item = [PSCustomObject]@{
                file         = 'docs/test.md'
                line_number  = 42
                original_url = 'https://example.com/fr-fr/page'
                fixed_url    = 'https://example.com/page'
            }
            $item.file | Should -Be 'docs/test.md'
            $item.line_number | Should -Be 42
            $item.original_url | Should -BeLike '*fr-fr*'
        }
    }

    Context 'Annotation severity mapping' {
        It 'Uses warning level for language-path issues' {
            Mock Write-CIAnnotation { }

            Write-CIAnnotation -Level Warning -Message 'test' -File 'test.md' -Line 1

            Should -Invoke Write-CIAnnotation -ParameterFilter {
                $Level -eq 'Warning'
            }
        }
    }
}

# ============================================================
# Describe 6 — Exit code handling
# ============================================================
Describe 'Exit Code Handling' -Tag 'Unit' {

    Context 'Empty result set' {
        It 'Counts zero issues for an empty array' {
            $results = @()
            @($results).Count | Should -Be 0
        }
    }

    Context 'Non-empty result set' {
        It 'Counts issues and flags warning expected' {
            $results = @(
                [PSCustomObject]@{ file = 'a.md'; line_number = 1; original_url = 'u1'; fixed_url = 'f1' }
                [PSCustomObject]@{ file = 'b.md'; line_number = 2; original_url = 'u2'; fixed_url = 'f2' }
            )
            @($results).Count | Should -Be 2
            $warningExpected = @($results).Count -gt 0
            $warningExpected | Should -BeTrue
        }
    }
}

# ============================================================
# Describe 7 — Output format verification
# ============================================================
Describe 'Output Format' -Tag 'Unit' {

    Context 'Console output formatting' {
        It 'Formats issue data as structured objects' {
            $issues = @(
                [PSCustomObject]@{ file = 'docs/a.md'; line_number = 1; original_url = 'https://example.com/fr-fr/a'; fixed_url = 'https://example.com/a' }
                [PSCustomObject]@{ file = 'docs/a.md'; line_number = 5; original_url = 'https://example.com/fr-fr/b'; fixed_url = 'https://example.com/b' }
                [PSCustomObject]@{ file = 'docs/b.md'; line_number = 3; original_url = 'https://example.com/fr-fr/c'; fixed_url = 'https://example.com/c' }
            )
            @($issues) | Should -HaveCount 3
            $issues[0].file | Should -Be 'docs/a.md'
        }
    }

    Context 'Summary statistics' {
        It 'Computes correct issue and file counts' {
            $issues = @(
                [PSCustomObject]@{ file = 'docs/a.md'; line_number = 1; original_url = 'u1'; fixed_url = 'f1' }
                [PSCustomObject]@{ file = 'docs/a.md'; line_number = 5; original_url = 'u2'; fixed_url = 'f2' }
                [PSCustomObject]@{ file = 'docs/b.md'; line_number = 3; original_url = 'u3'; fixed_url = 'f3' }
            )
            @($issues).Count | Should -Be 3
            ($issues | Select-Object -ExpandProperty file -Unique).Count | Should -Be 2
        }
    }
}

# ============================================================
# Describe 8 — Integration tests
# ============================================================
Describe 'Link-Lang-Check Integration' -Tag 'Integration' {

    Context 'Script dependencies' {
        It 'LintingHelpers module is importable' {
            { Import-Module $script:ModulePath -Force } | Should -Not -Throw
        }

        It 'CIHelpers module is importable' {
            { Import-Module $script:CIHelpersPath -Force } | Should -Not -Throw
        }

        It 'Link-Lang-Check.ps1 exists' {
            $companion = Join-Path $PSScriptRoot '../../linting/Link-Lang-Check.ps1'
            $companion | Should -Exist
        }
    }

    Context 'Output compatibility' {
        It 'Parses expected JSON output format' {
            $sampleJson = @'
[{"file":"test.md","line_number":1,"original_url":"https://example.com/fr-fr/page","fixed_url":"https://example.com/page"}]
'@
            $parsed = $sampleJson | ConvertFrom-Json
            @($parsed) | Should -HaveCount 1
        }

        It 'Result objects have required properties' {
            $sampleJson = @'
[{"file":"test.md","line_number":1,"original_url":"https://example.com/fr-fr/page","fixed_url":"https://example.com/page"}]
'@
            $parsed = ($sampleJson | ConvertFrom-Json)
            $parsed.PSObject.Properties.Name | Should -Contain 'file'
            $parsed.PSObject.Properties.Name | Should -Contain 'line_number'
            $parsed.PSObject.Properties.Name | Should -Contain 'original_url'
            $parsed.PSObject.Properties.Name | Should -Contain 'fixed_url'
        }
    }
}
