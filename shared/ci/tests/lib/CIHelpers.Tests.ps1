#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    $ciHelpersPath = Join-Path $PSScriptRoot '..' '..' '..' '..' 'scripts' 'lib' 'Modules' 'CIHelpers.psm1'
    Import-Module $ciHelpersPath -Force

    $gitMocksPath = Join-Path $PSScriptRoot '..' 'Mocks' 'GitMocks.psm1'
    Import-Module $gitMocksPath -Force
}

#region ConvertTo-GitHubActionsEscaped

Describe 'ConvertTo-GitHubActionsEscaped' -Tag 'Unit' {
    It 'Returns empty string unchanged' {
        ConvertTo-GitHubActionsEscaped -Value '' | Should -BeExactly ''
    }

    It 'Returns null unchanged' {
        ConvertTo-GitHubActionsEscaped -Value $null | Should -BeNullOrEmpty
    }

    It 'Escapes percent character' {
        ConvertTo-GitHubActionsEscaped -Value '100%' | Should -BeExactly '100%25'
    }

    It 'Escapes carriage return' {
        ConvertTo-GitHubActionsEscaped -Value "line1`rline2" | Should -BeExactly 'line1%0Dline2'
    }

    It 'Escapes newline' {
        ConvertTo-GitHubActionsEscaped -Value "line1`nline2" | Should -BeExactly 'line1%0Aline2'
    }

    It 'Escapes double-colon sequence' {
        ConvertTo-GitHubActionsEscaped -Value 'key::value' | Should -BeExactly 'key%3A%3Avalue'
    }

    It 'Does not escape colon without ForProperty' {
        ConvertTo-GitHubActionsEscaped -Value 'file:line' | Should -BeExactly 'file:line'
    }

    It 'Escapes colon with ForProperty' {
        ConvertTo-GitHubActionsEscaped -Value 'file:line' -ForProperty | Should -BeExactly 'file%3Aline'
    }

    It 'Escapes comma with ForProperty' {
        ConvertTo-GitHubActionsEscaped -Value 'a,b' -ForProperty | Should -BeExactly 'a%2Cb'
    }

    It 'Does not escape comma without ForProperty' {
        ConvertTo-GitHubActionsEscaped -Value 'a,b' | Should -BeExactly 'a,b'
    }

    It 'Escapes multiple special characters in sequence' {
        $result = ConvertTo-GitHubActionsEscaped -Value "100%`n::"
        $result | Should -BeExactly '100%25%0A%3A%3A'
    }

    It 'Leaves plain text unchanged' {
        ConvertTo-GitHubActionsEscaped -Value 'hello world' | Should -BeExactly 'hello world'
    }
}

#endregion

#region ConvertTo-AzureDevOpsEscaped

Describe 'ConvertTo-AzureDevOpsEscaped' -Tag 'Unit' {
    It 'Returns empty string unchanged' {
        ConvertTo-AzureDevOpsEscaped -Value '' | Should -BeExactly ''
    }

    It 'Returns null unchanged' {
        ConvertTo-AzureDevOpsEscaped -Value $null | Should -BeNullOrEmpty
    }

    It 'Escapes percent character' {
        ConvertTo-AzureDevOpsEscaped -Value '100%' | Should -BeExactly '100%AZP25'
    }

    It 'Escapes carriage return' {
        ConvertTo-AzureDevOpsEscaped -Value "line1`rline2" | Should -BeExactly 'line1%AZP0Dline2'
    }

    It 'Escapes newline' {
        ConvertTo-AzureDevOpsEscaped -Value "line1`nline2" | Should -BeExactly 'line1%AZP0Aline2'
    }

    It 'Escapes open bracket' {
        ConvertTo-AzureDevOpsEscaped -Value 'msg[1]' | Should -BeExactly 'msg%AZP5B1%AZP5D'
    }

    It 'Escapes close bracket' {
        ConvertTo-AzureDevOpsEscaped -Value ']end' | Should -BeExactly '%AZP5Dend'
    }

    It 'Does not escape semicolon without ForProperty' {
        ConvertTo-AzureDevOpsEscaped -Value 'a;b' | Should -BeExactly 'a;b'
    }

    It 'Escapes semicolon with ForProperty' {
        ConvertTo-AzureDevOpsEscaped -Value 'a;b' -ForProperty | Should -BeExactly 'a%AZP3Bb'
    }

    It 'Leaves plain text unchanged' {
        ConvertTo-AzureDevOpsEscaped -Value 'hello world' | Should -BeExactly 'hello world'
    }
}

#endregion

#region Get-CIPlatform

Describe 'Get-CIPlatform' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        Clear-MockCIEnvironment
    }

    It 'Returns github when GITHUB_ACTIONS is true' {
        $env:GITHUB_ACTIONS = 'true'
        Get-CIPlatform | Should -BeExactly 'github'
    }

    It 'Returns azdo when TF_BUILD is True' {
        $env:TF_BUILD = 'True'
        Get-CIPlatform | Should -BeExactly 'azdo'
    }

    It 'Returns azdo when AZURE_PIPELINES is True' {
        $env:AZURE_PIPELINES = 'True'
        Get-CIPlatform | Should -BeExactly 'azdo'
    }

    It 'Returns local when no CI variables are set' {
        Get-CIPlatform | Should -BeExactly 'local'
    }

    It 'Prefers github over azdo when both are set' {
        $env:GITHUB_ACTIONS = 'true'
        $env:TF_BUILD = 'True'
        Get-CIPlatform | Should -BeExactly 'github'
    }
}

#endregion

#region Test-CIEnvironment

Describe 'Test-CIEnvironment' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    BeforeEach {
        Clear-MockCIEnvironment
    }

    It 'Returns true in GitHub Actions' {
        $env:GITHUB_ACTIONS = 'true'
        Test-CIEnvironment | Should -BeTrue
    }

    It 'Returns true in Azure DevOps' {
        $env:TF_BUILD = 'True'
        Test-CIEnvironment | Should -BeTrue
    }

    It 'Returns false when local' {
        Test-CIEnvironment | Should -BeFalse
    }

    It 'Returns boolean type' {
        Test-CIEnvironment | Should -BeOfType [bool]
    }
}

#endregion

#region Test-PowerShellVersion

Describe 'Test-PowerShellVersion' -Tag 'Unit' {
    BeforeAll {
        Mock Write-CIAnnotation { } -ModuleName CIHelpers
    }

    It 'Returns true when current version meets minimum' {
        $result = Test-PowerShellVersion -MinimumVersion '7.0'
        $result | Should -BeTrue
    }

    It 'Returns true when current version exceeds minimum' {
        $result = Test-PowerShellVersion -MinimumVersion '5.1'
        $result | Should -BeTrue
    }

    It 'Returns false when minimum exceeds current version' {
        $result = Test-PowerShellVersion -MinimumVersion '99.0'
        $result | Should -BeFalse
    }

    It 'Emits warning annotation when version is below minimum' {
        Test-PowerShellVersion -MinimumVersion '99.0'
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -Times 1 -ParameterFilter {
            $Level -eq 'Warning'
        }
    }

    It 'Does not emit annotation when version meets minimum' {
        Test-PowerShellVersion -MinimumVersion '7.0'
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -Times 0
    }

    It 'Uses 7.0 as default minimum version' {
        $result = Test-PowerShellVersion
        $result | Should -BeTrue
    }

    It 'Returns boolean type' {
        $result = Test-PowerShellVersion
        $result | Should -BeOfType [bool]
    }
}

#endregion

#region Set-CIOutput

Describe 'Set-CIOutput' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions' {
        BeforeEach {
            $script:mockFiles = Initialize-MockCIEnvironment
        }

        AfterEach {
            Remove-MockCIFiles -MockFiles $script:mockFiles
            Clear-MockCIEnvironment
        }

        It 'Appends name=value to GITHUB_OUTPUT' {
            Set-CIOutput -Name 'result' -Value 'pass'
            $content = Get-Content $script:mockFiles.Output -Raw
            $content | Should -Match 'result=pass'
        }

        It 'Handles empty value' {
            Set-CIOutput -Name 'empty' -Value ''
            $content = Get-Content $script:mockFiles.Output -Raw
            $content | Should -Match 'empty='
        }

        It 'Escapes special characters in name and value' {
            Set-CIOutput -Name "key`n" -Value "val`n"
            $content = Get-Content $script:mockFiles.Output -Raw
            $content | Should -Match 'key%0A=val%0A'
        }
    }

    Context 'GitHub Actions - null environment variable fallback' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:GITHUB_ACTIONS = 'true'
            Mock Write-Warning { } -ModuleName CIHelpers
            Mock Write-Verbose { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits warning when GITHUB_OUTPUT is not set' {
            Set-CIOutput -Name 'test' -Value 'value'
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'GITHUB_OUTPUT is not set'
            }
        }

        It 'Falls back to verbose logging' {
            Set-CIOutput -Name 'test' -Value 'value'
            Should -Invoke -CommandName Write-Verbose -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'CI output: test=value'
            }
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits setvariable logging command' {
            Set-CIOutput -Name 'result' -Value 'pass'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.setvariable variable=result\]pass'
            }
        }

        It 'Includes isOutput flag when specified' {
            Set-CIOutput -Name 'result' -Value 'pass' -IsOutput
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'isOutput=true'
            }
        }
    }

    Context 'Local' {
        BeforeEach {
            Clear-MockCIEnvironment
        }

        It 'Does not throw in local mode' {
            { Set-CIOutput -Name 'x' -Value 'y' } | Should -Not -Throw
        }
    }
}

#endregion

#region Set-CIEnv

Describe 'Set-CIEnv' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions' {
        BeforeEach {
            $script:mockFiles = Initialize-MockCIEnvironment
        }

        AfterEach {
            Remove-MockCIFiles -MockFiles $script:mockFiles
            Clear-MockCIEnvironment
        }

        It 'Writes heredoc format to GITHUB_ENV' {
            Set-CIEnv -Name 'MY_VAR' -Value 'hello'
            $content = Get-Content $script:mockFiles.Env -Raw
            $content | Should -Match 'MY_VAR<<EOF_'
            $content | Should -Match 'hello'
        }

        It 'Handles multiline values' {
            Set-CIEnv -Name 'MULTI' -Value "line1`nline2"
            $content = Get-Content $script:mockFiles.Env -Raw
            $content | Should -Match 'MULTI<<EOF_'
        }
    }

    Context 'GitHub Actions - null environment variable fallback' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:GITHUB_ACTIONS = 'true'
            Mock Write-Warning { } -ModuleName CIHelpers
            Mock Write-Verbose { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits warning when GITHUB_ENV is not set' {
            Set-CIEnv -Name 'MY_VAR' -Value 'value'
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'GITHUB_ENV is not set'
            }
        }

        It 'Falls back to verbose logging' {
            Set-CIEnv -Name 'MY_VAR' -Value 'value'
            Should -Invoke -CommandName Write-Verbose -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'CI env: MY_VAR=value'
            }
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits setvariable logging command' {
            Set-CIEnv -Name 'MY_VAR' -Value 'hello'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.setvariable variable=MY_VAR\]hello'
            }
        }
    }

    Context 'Validation' {
        BeforeEach {
            Clear-MockCIEnvironment
        }

        It 'Rejects invalid variable names' {
            Set-CIEnv -Name '123-bad' -Value 'x' -WarningVariable w 3>$null
            $w | Should -Not -BeNullOrEmpty
        }

        It 'Accepts underscored names' {
            { Set-CIEnv -Name 'GOOD_NAME_1' -Value 'x' } | Should -Not -Throw
        }

        It 'Accepts leading underscore' {
            { Set-CIEnv -Name '_PRIVATE' -Value 'x' } | Should -Not -Throw
        }
    }
}

#endregion

#region Write-CIStepSummary

Describe 'Write-CIStepSummary' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions - Content parameter' {
        BeforeEach {
            $script:mockFiles = Initialize-MockCIEnvironment
        }

        AfterEach {
            Remove-MockCIFiles -MockFiles $script:mockFiles
            Clear-MockCIEnvironment
        }

        It 'Appends markdown content to step summary file' {
            Write-CIStepSummary -Content '## Results'
            $content = Get-Content $script:mockFiles.Summary -Raw
            $content | Should -Match '## Results'
        }
    }

    Context 'GitHub Actions - Path parameter' {
        BeforeEach {
            $script:mockFiles = Initialize-MockCIEnvironment
            $script:summarySource = Join-Path ([System.IO.Path]::GetTempPath()) "summary_src_$(New-Guid).md"
            '## From File' | Set-Content $script:summarySource
        }

        AfterEach {
            Remove-Item $script:summarySource -Force -ErrorAction SilentlyContinue
            Remove-MockCIFiles -MockFiles $script:mockFiles
            Clear-MockCIEnvironment
        }

        It 'Reads content from file path' {
            Write-CIStepSummary -Path $script:summarySource
            $content = Get-Content $script:mockFiles.Summary -Raw
            $content | Should -Match '## From File'
        }

        It 'Warns when path does not exist' {
            Write-CIStepSummary -Path 'TestDrive:/nonexistent.md' -WarningVariable w 3>$null
            $w | Should -Not -BeNullOrEmpty
        }
    }

    Context 'GitHub Actions - null environment variable fallback' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:GITHUB_ACTIONS = 'true'
            Mock Write-Warning { } -ModuleName CIHelpers
            Mock Write-Verbose { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits warning when GITHUB_STEP_SUMMARY is not set' {
            Write-CIStepSummary -Content '## Results'
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'GITHUB_STEP_SUMMARY is not set'
            }
        }

        It 'Falls back to verbose logging' {
            Write-CIStepSummary -Content '## Results'
            Should -Invoke -CommandName Write-Verbose -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'Step summary: ## Results'
            }
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits section header and sanitized content' {
            Write-CIStepSummary -Content '## Results'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -Times 2 -Exactly
        }

        It 'Handles content containing vso-like commands without error' {
            { Write-CIStepSummary -Content '##vso[task.setvariable]hack' } | Should -Not -Throw
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -Times 2 -Exactly
        }
    }

    Context 'Local' {
        BeforeEach {
            Clear-MockCIEnvironment
        }

        It 'Does not throw in local mode' {
            { Write-CIStepSummary -Content '## Test' } | Should -Not -Throw
        }
    }
}

#endregion

#region Write-CIAnnotation

Describe 'Write-CIAnnotation' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:GITHUB_ACTIONS = 'true'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits warning annotation' {
            Write-CIAnnotation -Message 'test warning' -Level 'Warning'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '^::warning::test warning$'
            }
        }

        It 'Emits error annotation' {
            Write-CIAnnotation -Message 'test error' -Level 'Error'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '^::error::test error$'
            }
        }

        It 'Emits notice annotation' {
            Write-CIAnnotation -Message 'test notice' -Level 'Notice'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '^::notice::test notice$'
            }
        }

        It 'Defaults to Warning level' {
            Write-CIAnnotation -Message 'default level'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '^::warning::default level$'
            }
        }

        It 'Includes file property' {
            Write-CIAnnotation -Message 'msg' -File 'src/app.ps1'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'file=src/app\.ps1'
            }
        }

        It 'Includes line and column properties' {
            Write-CIAnnotation -Message 'msg' -File 'src/app.ps1' -Line 10 -Column 5
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'line=10' -and $Object -match 'col=5'
            }
        }

        It 'Normalizes backslashes in file path' {
            Write-CIAnnotation -Message 'msg' -File 'src\app.ps1'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'file=src/app\.ps1'
            }
        }

        It 'Handles empty message' {
            { Write-CIAnnotation -Message '' } | Should -Not -Throw
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits logissue command for warning' {
            Write-CIAnnotation -Message 'adowarn' -Level 'Warning'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.logissue type=warning\]adowarn'
            }
        }

        It 'Emits logissue command for error' {
            Write-CIAnnotation -Message 'adoerr' -Level 'Error'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.logissue type=error\]adoerr'
            }
        }

        It 'Maps Notice to info type' {
            Write-CIAnnotation -Message 'adonotice' -Level 'Notice'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'type=info'
            }
        }

        It 'Includes sourcepath property' {
            Write-CIAnnotation -Message 'msg' -Level 'Warning' -File 'src/app.ps1'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'sourcepath=src/app\.ps1'
            }
        }

        It 'Includes linenumber and columnnumber properties' {
            Write-CIAnnotation -Message 'msg' -Level 'Warning' -File 'f.ps1' -Line 3 -Column 7
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'linenumber=3' -and $Object -match 'columnnumber=7'
            }
        }
    }

    Context 'Local' {
        BeforeEach {
            Clear-MockCIEnvironment
            Mock Write-Warning { } -ModuleName CIHelpers
        }

        It 'Emits Write-Warning with level prefix' {
            Write-CIAnnotation -Message 'local msg' -Level 'Error'
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match '\[Error\].*local msg'
            }
        }

        It 'Includes file location in warning' {
            Write-CIAnnotation -Message 'msg' -Level 'Warning' -File 'test.ps1' -Line 5
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'test\.ps1:5'
            }
        }

        It 'Includes column in file location' {
            Write-CIAnnotation -Message 'msg' -Level 'Warning' -File 'test.ps1' -Line 5 -Column 3
            Should -Invoke -CommandName Write-Warning -ModuleName CIHelpers -ParameterFilter {
                $Message -match 'test\.ps1:5:3'
            }
        }
    }
}

#endregion

#region Write-CIAnnotations

Describe 'Write-CIAnnotations' -Tag 'Unit' {
    BeforeAll {
        Mock Write-CIAnnotation { } -ModuleName CIHelpers
    }

    It 'Calls Write-CIAnnotation for each issue' {
        $summary = [PSCustomObject]@{
            Results = @(
                [PSCustomObject]@{
                    Issues = @(
                        [PSCustomObject]@{ Message = 'err1'; Severity = 'Error'; File = 'a.ps1'; Line = 1; Column = 2 },
                        [PSCustomObject]@{ Message = 'warn1'; Severity = 'Warning'; File = 'b.ps1'; Line = 3; Column = 4 }
                    )
                }
            )
        }
        Write-CIAnnotations -Summary $summary
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -Times 2 -Exactly
    }

    It 'Maps Error severity to Error level' {
        $summary = [PSCustomObject]@{
            Results = @(
                [PSCustomObject]@{
                    Issues = @(
                        [PSCustomObject]@{ Message = 'err'; Severity = 'Error'; File = $null; Line = $null; Column = $null }
                    )
                }
            )
        }
        Write-CIAnnotations -Summary $summary
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -ParameterFilter {
            $Level -eq 'Error'
        }
    }

    It 'Maps non-Error severity to Warning level' {
        $summary = [PSCustomObject]@{
            Results = @(
                [PSCustomObject]@{
                    Issues = @(
                        [PSCustomObject]@{ Message = 'info'; Severity = 'Information'; File = $null; Line = $null; Column = $null }
                    )
                }
            )
        }
        Write-CIAnnotations -Summary $summary
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -ParameterFilter {
            $Level -eq 'Warning'
        }
    }

    It 'Skips issues with empty or whitespace messages' {
        $summary = [PSCustomObject]@{
            Results = @(
                [PSCustomObject]@{
                    Issues = @(
                        [PSCustomObject]@{ Message = ''; Severity = 'Warning'; File = $null; Line = $null; Column = $null },
                        [PSCustomObject]@{ Message = '   '; Severity = 'Warning'; File = $null; Line = $null; Column = $null },
                        [PSCustomObject]@{ Message = 'valid'; Severity = 'Warning'; File = $null; Line = $null; Column = $null }
                    )
                }
            )
        }
        Write-CIAnnotations -Summary $summary
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -Times 1 -Exactly
    }

    It 'Handles empty Results array' {
        $summary = [PSCustomObject]@{ Results = @() }
        { Write-CIAnnotations -Summary $summary } | Should -Not -Throw
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -Times 0 -Exactly
    }

    It 'Forwards file, line, and column to annotation' {
        $summary = [PSCustomObject]@{
            Results = @(
                [PSCustomObject]@{
                    Issues = @(
                        [PSCustomObject]@{ Message = 'msg'; Severity = 'Error'; File = 'x.ps1'; Line = 42; Column = 8 }
                    )
                }
            )
        }
        Write-CIAnnotations -Summary $summary
        Should -Invoke -CommandName Write-CIAnnotation -ModuleName CIHelpers -ParameterFilter {
            $File -eq 'x.ps1' -and $Line -eq 42 -and $Column -eq 8
        }
    }
}

#endregion

#region Set-CITaskResult

Describe 'Set-CITaskResult' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:GITHUB_ACTIONS = 'true'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits error annotation for Failed' {
            Set-CITaskResult -Result 'Failed'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '::error::Task failed'
            }
        }

        It 'Does not emit error for Succeeded' {
            Set-CITaskResult -Result 'Succeeded'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -Times 0
        }

        It 'Does not emit error for SucceededWithIssues' {
            Set-CITaskResult -Result 'SucceededWithIssues'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -Times 0
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Clear-MockCIEnvironment
        }

        It 'Emits task.complete for Succeeded' {
            Set-CITaskResult -Result 'Succeeded'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.complete result=Succeeded\]'
            }
        }

        It 'Emits task.complete for Failed' {
            Set-CITaskResult -Result 'Failed'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.complete result=Failed\]'
            }
        }

        It 'Emits task.complete for SucceededWithIssues' {
            Set-CITaskResult -Result 'SucceededWithIssues'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[task\.complete result=SucceededWithIssues\]'
            }
        }
    }

    Context 'Local' {
        BeforeEach {
            Clear-MockCIEnvironment
        }

        It 'Does not throw for any result' {
            { Set-CITaskResult -Result 'Succeeded' } | Should -Not -Throw
            { Set-CITaskResult -Result 'Failed' } | Should -Not -Throw
        }
    }
}

#endregion

#region Publish-CIArtifact

Describe 'Publish-CIArtifact' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
    }

    AfterAll {
        Restore-CIEnvironment
    }

    Context 'GitHub Actions' {
        BeforeEach {
            $script:mockFiles = Initialize-MockCIEnvironment
            $script:tempArtifact = Join-Path ([System.IO.Path]::GetTempPath()) "artifact_$(New-Guid).txt"
            'artifact content' | Set-Content $script:tempArtifact
        }

        AfterEach {
            Remove-Item $script:tempArtifact -Force -ErrorAction SilentlyContinue
            Remove-MockCIFiles -MockFiles $script:mockFiles
            Clear-MockCIEnvironment
        }

        It 'Sets output variables for artifact path and name' {
            Publish-CIArtifact -Path $script:tempArtifact -Name 'test-artifact'
            $content = Get-Content $script:mockFiles.Output -Raw
            $content | Should -Match 'artifact-path-test-artifact='
            $content | Should -Match 'artifact-name-test-artifact=test-artifact'
        }
    }

    Context 'Azure DevOps' {
        BeforeEach {
            Clear-MockCIEnvironment
            $env:TF_BUILD = 'True'
            $script:tempArtifact = Join-Path ([System.IO.Path]::GetTempPath()) "artifact_$(New-Guid).txt"
            'artifact content' | Set-Content $script:tempArtifact
            Mock Write-Host { } -ModuleName CIHelpers
        }

        AfterEach {
            Remove-Item $script:tempArtifact -Force -ErrorAction SilentlyContinue
            Clear-MockCIEnvironment
        }

        It 'Emits artifact.upload command' {
            Publish-CIArtifact -Path $script:tempArtifact -Name 'test-artifact'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match '##vso\[artifact\.upload'
            }
        }

        It 'Uses Name as default ContainerFolder' {
            Publish-CIArtifact -Path $script:tempArtifact -Name 'myart'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'containerfolder=myart'
            }
        }

        It 'Uses explicit ContainerFolder when provided' {
            Publish-CIArtifact -Path $script:tempArtifact -Name 'myart' -ContainerFolder 'custom'
            Should -Invoke -CommandName Write-Host -ModuleName CIHelpers -ParameterFilter {
                $Object -match 'containerfolder=custom'
            }
        }
    }

    Context 'Missing artifact path' {
        BeforeEach {
            Clear-MockCIEnvironment
        }

        It 'Warns and returns when path does not exist' {
            Publish-CIArtifact -Path 'TestDrive:/nonexistent.txt' -Name 'missing' -WarningVariable w 3>$null
            $w | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Local' {
        BeforeEach {
            Clear-MockCIEnvironment
            $script:tempArtifact = Join-Path ([System.IO.Path]::GetTempPath()) "artifact_$(New-Guid).txt"
            'artifact content' | Set-Content $script:tempArtifact
        }

        AfterEach {
            Remove-Item $script:tempArtifact -Force -ErrorAction SilentlyContinue
        }

        It 'Does not throw in local mode' {
            { Publish-CIArtifact -Path $script:tempArtifact -Name 'local-art' } | Should -Not -Throw
        }
    }
}

#endregion
