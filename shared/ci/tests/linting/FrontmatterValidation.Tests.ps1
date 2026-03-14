#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

using module ..\..\linting\Modules\FrontmatterValidation.psm1

BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '..' '..' 'linting' 'Modules' 'FrontmatterValidation.psm1'
    Import-Module $modulePath -Force
    $script:FVModule = Get-Module FrontmatterValidation
    $script:FixtureDir = Join-Path $PSScriptRoot '..' 'Fixtures' 'Frontmatter'

    #region Helper Functions

    function New-FileValidationResult {
        param(
            [string]$FilePath = 'test-file.md',
            [string]$RelativePath = 'docs/test-file.md'
        )
        & $script:FVModule {
            param($fp, $rp)
            [FileValidationResult]::new($fp, $rp)
        } $FilePath $RelativePath
    }

    function New-ValidationSummary {
        & $script:FVModule { [ValidationSummary]::new() }
    }

    function New-ValidationIssue {
        param(
            [string]$Type = 'Error',
            [string]$Field = 'test-field',
            [string]$Message = 'Test message'
        )
        & $script:FVModule {
            param($t, $f, $m)
            [ValidationIssue]::new($t, $f, $m)
        } $Type $Field $Message
    }

    function New-FileTypeInfo {
        param(
            [hashtable]$Properties = @{}
        )
        $info = & $script:FVModule { [FileTypeInfo]::new() }
        foreach ($key in $Properties.Keys) {
            $info.$key = $Properties[$key]
        }
        return $info
    }

    #endregion
}

#region ValidationIssue Class

Describe 'ValidationIssue Class' -Tag 'Unit' {
    Context 'Constructor with three parameters' {
        It 'creates an Error issue' {
            $issue = New-ValidationIssue -Type 'Error' -Field 'title' -Message 'Missing title'
            $issue.Type | Should -Be 'Error'
            $issue.Field | Should -Be 'title'
            $issue.Message | Should -Be 'Missing title'
            $issue.FilePath | Should -Be ''
            $issue.Line | Should -Be 0
        }

        It 'creates a Warning issue' {
            $issue = New-ValidationIssue -Type 'Warning' -Field 'author' -Message 'Author empty'
            $issue.Type | Should -Be 'Warning'
        }

        It 'creates an Information issue' {
            $issue = New-ValidationIssue -Type 'Information' -Field 'ms.date' -Message 'Date suggestion'
            $issue.Type | Should -Be 'Information'
        }
    }

    Context 'Constructor with four parameters' {
        It 'sets FilePath from constructor' {
            $issue = & $script:FVModule {
                param($t, $f, $m, $fp)
                [ValidationIssue]::new($t, $f, $m, $fp)
            } 'Error' 'title' 'Missing title' 'docs/test.md'
            $issue.FilePath | Should -Be 'docs/test.md'
            $issue.Line | Should -Be 0
        }
    }

    Context 'Constructor with five parameters' {
        It 'sets FilePath and Line from constructor' {
            $issue = & $script:FVModule {
                param($t, $f, $m, $fp, $l)
                [ValidationIssue]::new($t, $f, $m, $fp, $l)
            } 'Warning' 'description' 'Empty' 'docs/test.md' 5
            $issue.FilePath | Should -Be 'docs/test.md'
            $issue.Line | Should -Be 5
        }
    }

    Context 'ToString method' {
        It 'formats without file path' {
            $issue = New-ValidationIssue -Type 'Error' -Field 'title' -Message 'Missing'
            $issue.ToString() | Should -Be '[Error] title: Missing'
        }

        It 'formats with file path' {
            $issue = & $script:FVModule {
                param($t, $f, $m, $fp)
                [ValidationIssue]::new($t, $f, $m, $fp)
            } 'Warning' 'author' 'Empty author' 'docs/test.md'
            $issue.ToString() | Should -Be 'docs/test.md: [Warning] author: Empty author'
        }
    }

    Context 'ToString with Line property set' {
        It 'preserves line number on the object' {
            $issue = & $script:FVModule {
                param($t, $f, $m, $fp)
                $i = [ValidationIssue]::new($t, $f, $m, $fp)
                $i.Line = 42
                $i
            } 'Error' 'title' 'Missing title' 'docs/test.md'
            $issue.Line | Should -Be 42
        }

        It 'includes all components in formatted string' {
            $issue = & $script:FVModule {
                param($t, $f, $m, $fp)
                $i = [ValidationIssue]::new($t, $f, $m, $fp)
                $i.Line = 10
                $i
            } 'Warning' 'author' 'Empty author' 'README.md'
            $str = $issue.ToString()
            $str | Should -Match 'README\.md'
            $str | Should -Match 'Warning'
            $str | Should -Match 'author'
        }
    }
}

#endregion

#region FileValidationResult Class

Describe 'FileValidationResult Class' -Tag 'Unit' {
    Context 'Initialization' {
        It 'creates with file path and relative path' {
            $result = New-FileValidationResult -FilePath '/abs/path.md' -RelativePath 'docs/path.md'
            $result.FilePath | Should -Be '/abs/path.md'
            $result.RelativePath | Should -Be 'docs/path.md'
            $result.Issues.Count | Should -Be 0
        }

        It 'starts with no errors or warnings' {
            $result = New-FileValidationResult
            $result.HasErrors | Should -BeFalse
            $result.HasWarnings | Should -BeFalse
            $result.IsValid | Should -BeTrue
            $result.ErrorCount | Should -Be 0
            $result.WarningCount | Should -Be 0
        }
    }

    Context 'Issue tracking' {
        It 'tracks errors via AddIssue' {
            $result = New-FileValidationResult
            $error1 = New-ValidationIssue -Type 'Error' -Field 'title' -Message 'Missing'
            $result.AddIssue($error1)
            $result.HasErrors | Should -BeTrue
            $result.IsValid | Should -BeFalse
            $result.ErrorCount | Should -Be 1
        }

        It 'tracks warnings via AddIssue' {
            $result = New-FileValidationResult
            $warning1 = New-ValidationIssue -Type 'Warning' -Field 'author' -Message 'Empty'
            $result.AddIssue($warning1)
            $result.HasWarnings | Should -BeTrue
            $result.IsValid | Should -BeTrue
            $result.WarningCount | Should -Be 1
        }

        It 'AddError creates and adds an error issue' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing title')
            $result.ErrorCount | Should -Be 1
            $result.Issues[0].Type | Should -Be 'Error'
            $result.Issues[0].Field | Should -Be 'title'
            $result.Issues[0].Message | Should -Be 'Missing title'
        }

        It 'AddWarning creates and adds a warning issue' {
            $result = New-FileValidationResult
            $result.AddWarning('author', 'Author empty')
            $result.WarningCount | Should -Be 1
            $result.Issues[0].Type | Should -Be 'Warning'
        }

        It 'AddIssue sets FilePath from RelativePath' {
            $result = New-FileValidationResult -FilePath '/abs/test.md' -RelativePath 'docs/test.md'
            $issue = New-ValidationIssue -Type 'Error' -Field 'title' -Message 'Missing'
            $result.AddIssue($issue)
            $result.Issues[0].FilePath | Should -Be 'docs/test.md'
        }

        It 'counts multiple errors and warnings independently' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing')
            $result.AddError('description', 'Missing')
            $result.AddWarning('author', 'Empty')
            $result.ErrorCount | Should -Be 2
            $result.WarningCount | Should -Be 1
            $result.Issues.Count | Should -Be 3
        }
    }

    Context 'ScriptProperties' {
        It 'reports HasErrors true when errors exist' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing')
            $result.HasErrors | Should -BeTrue
        }

        It 'reports HasErrors false when no errors' {
            $result = New-FileValidationResult
            $result.AddWarning('author', 'Empty')
            $result.HasErrors | Should -BeFalse
        }

        It 'reports HasWarnings true when warnings exist' {
            $result = New-FileValidationResult
            $result.AddWarning('author', 'Empty')
            $result.HasWarnings | Should -BeTrue
        }

        It 'reports IsValid true when no errors' {
            $result = New-FileValidationResult
            $result.AddWarning('author', 'Empty')
            $result.IsValid | Should -BeTrue
        }

        It 'reports IsValid false when errors present' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing')
            $result.IsValid | Should -BeFalse
        }

        It 'returns correct ErrorCount' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing')
            $result.AddError('description', 'Empty')
            $result.AddWarning('author', 'Empty')
            $result.ErrorCount | Should -Be 2
        }

        It 'returns correct WarningCount' {
            $result = New-FileValidationResult
            $result.AddError('title', 'Missing')
            $result.AddWarning('author', 'Empty')
            $result.AddWarning('ms.date', 'Invalid')
            $result.WarningCount | Should -Be 2
        }
    }
}

#endregion

#region ValidationSummary Class

Describe 'ValidationSummary Class' -Tag 'Unit' {
    Context 'Aggregation' {
        It 'counts total files after Complete' {
            $summary = New-ValidationSummary
            $r1 = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $r2 = New-FileValidationResult -FilePath 'b.md' -RelativePath 'b.md'
            $summary.AddResult($r1)
            $summary.AddResult($r2)
            $summary.Complete()
            $summary.TotalFiles | Should -Be 2
        }

        It 'counts passed and failed files' {
            $summary = New-ValidationSummary
            $passed = New-FileValidationResult -FilePath 'good.md' -RelativePath 'good.md'
            $failed = New-FileValidationResult -FilePath 'bad.md' -RelativePath 'bad.md'
            $failed.AddError('title', 'Missing')
            $summary.AddResult($passed)
            $summary.AddResult($failed)
            $summary.Complete()
            $summary.PassedFiles | Should -Be 1
            $summary.FailedFiles | Should -Be 1
        }

        It 'aggregates total errors and warnings' {
            $summary = New-ValidationSummary
            $r1 = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $r1.AddError('title', 'Missing')
            $r1.AddWarning('author', 'Empty')
            $r2 = New-FileValidationResult -FilePath 'b.md' -RelativePath 'b.md'
            $r2.AddError('description', 'Missing')
            $summary.AddResult($r1)
            $summary.AddResult($r2)
            $summary.Complete()
            $summary.TotalErrors | Should -Be 2
            $summary.TotalWarnings | Should -Be 1
        }

        It 'computes duration between start and end' {
            $summary = New-ValidationSummary
            Start-Sleep -Milliseconds 50
            $summary.Complete()
            $summary.Duration.TotalMilliseconds | Should -BeGreaterThan 0
        }
    }

    Context 'Exit code' {
        It 'returns 0 when no errors' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'ok.md' -RelativePath 'ok.md'
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 0
        }

        It 'returns 1 when errors exist' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'bad.md' -RelativePath 'bad.md'
            $r.AddError('title', 'Missing')
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 1
        }

        It 'returns 0 when only warnings exist' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'warn.md' -RelativePath 'warn.md'
            $r.AddWarning('author', 'Empty')
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 0
        }
    }

    Context 'Passed property' {
        It 'is true when no errors' {
            $summary = New-ValidationSummary
            $summary.Complete()
            $summary.Passed | Should -BeTrue
        }

        It 'is false when errors exist' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'bad.md' -RelativePath 'bad.md'
            $r.AddError('title', 'Missing')
            $summary.AddResult($r)
            $summary.Complete()
            $summary.Passed | Should -BeFalse
        }
    }

    Context 'Serialization' {
        It 'converts to hashtable with all fields' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $r.AddError('title', 'Missing')
            $r.AddWarning('author', 'Empty')
            $summary.AddResult($r)
            $summary.Complete()
            $ht = $summary.ToHashtable()
            $ht.TotalFiles | Should -Be 1
            $ht.PassedFiles | Should -Be 0
            $ht.FailedFiles | Should -Be 1
            $ht.TotalErrors | Should -Be 1
            $ht.TotalWarnings | Should -Be 1
            $ht.Passed | Should -BeFalse
            $ht.Duration | Should -BeOfType [double]
        }

        It 'returns empty summary when no results' {
            $summary = New-ValidationSummary
            $summary.Complete()
            $ht = $summary.ToHashtable()
            $ht.TotalFiles | Should -Be 0
            $ht.Passed | Should -BeTrue
        }
    }

    Context 'Complete with zero results' {
        It 'returns passed state with zero counts' {
            $summary = New-ValidationSummary
            $summary.Complete()
            $summary.TotalFiles | Should -Be 0
            $summary.PassedFiles | Should -Be 0
            $summary.FailedFiles | Should -Be 0
            $summary.Passed | Should -BeTrue
        }
    }

    Context 'GetExitCode behavior' {
        It 'returns 0 when no errors' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 0
        }

        It 'returns 1 when errors present' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $r.AddError('title', 'Missing')
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 1
        }

        It 'returns 0 when only warnings present' {
            $summary = New-ValidationSummary
            $r = New-FileValidationResult -FilePath 'a.md' -RelativePath 'a.md'
            $r.AddWarning('author', 'Missing')
            $summary.AddResult($r)
            $summary.Complete()
            $summary.GetExitCode() | Should -Be 0
        }
    }

    Context 'ToHashtable field completeness' {
        It 'includes all expected keys' {
            $summary = New-ValidationSummary
            $summary.Complete()
            $ht = $summary.ToHashtable()
            $ht.Keys | Should -Contain 'TotalFiles'
            $ht.Keys | Should -Contain 'PassedFiles'
            $ht.Keys | Should -Contain 'FailedFiles'
            $ht.Keys | Should -Contain 'TotalErrors'
            $ht.Keys | Should -Contain 'TotalWarnings'
            $ht.Keys | Should -Contain 'Passed'
            $ht.Keys | Should -Contain 'Duration'
        }
    }
}

#endregion

#region Get-FileTypeInfo

Describe 'Get-FileTypeInfo' -Tag 'Unit' {
    It 'classifies docs/ files as documentation' {
        $info = Get-FileTypeInfo -RelativePath 'docs/guide.md'
        $info.IsDocumentation | Should -BeTrue
        $info.RequiresFrontmatter | Should -BeTrue
    }

    It 'classifies .instructions.md files as instruction' {
        $info = Get-FileTypeInfo -RelativePath '.github/instructions/style.instructions.md'
        $info.IsInstruction | Should -BeTrue
        $info.RequiresFrontmatter | Should -BeTrue
    }

    It 'classifies .prompt.md files as prompt' {
        $info = Get-FileTypeInfo -RelativePath '.github/prompts/summarize.prompt.md'
        $info.IsPrompt | Should -BeTrue
        $info.RequiresFrontmatter | Should -BeTrue
    }

    It 'classifies root *.md files as root community' {
        $info = Get-FileTypeInfo -RelativePath 'README.md'
        $info.IsRootCommunity | Should -BeTrue
        $info.RequiresFrontmatter | Should -BeTrue
    }

    It 'classifies CONTRIBUTING.md as root community' {
        $info = Get-FileTypeInfo -RelativePath 'CONTRIBUTING.md'
        $info.IsRootCommunity | Should -BeTrue
    }

    It 'does not require frontmatter for non-matching files' {
        $info = Get-FileTypeInfo -RelativePath 'shared/ci/linting/README.md'
        $info.IsDocumentation | Should -BeFalse
        $info.IsInstruction | Should -BeFalse
        $info.IsPrompt | Should -BeFalse
        $info.IsRootCommunity | Should -BeFalse
        $info.RequiresFrontmatter | Should -BeFalse
    }

    It 'normalizes backslashes to forward slashes' {
        $info = Get-FileTypeInfo -RelativePath 'docs\subfolder\guide.md'
        $info.IsDocumentation | Should -BeTrue
    }

    It 'can match both docs and instruction for docs/instructions file' {
        $info = Get-FileTypeInfo -RelativePath 'docs/style.instructions.md'
        $info.IsDocumentation | Should -BeTrue
        $info.IsInstruction | Should -BeTrue
    }
}

#endregion

#region Test-FrontmatterPresence

Describe 'Test-FrontmatterPresence' -Tag 'Unit' {
    It 'returns no issues for file with valid frontmatter' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $fileType = New-FileTypeInfo -Properties @{ RequiresFrontmatter = $true }
        $issues = Test-FrontmatterPresence -FilePath $filePath -FileTypeInfo $fileType
        $issues | Should -HaveCount 0
    }

    It 'returns error for file missing frontmatter when required' {
        $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
        $fileType = New-FileTypeInfo -Properties @{ RequiresFrontmatter = $true }
        $issues = Test-FrontmatterPresence -FilePath $filePath -FileTypeInfo $fileType
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
        $issues[0].Field | Should -Be 'frontmatter'
    }

    It 'returns no issues when frontmatter is not required' {
        $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
        $fileType = New-FileTypeInfo -Properties @{ RequiresFrontmatter = $false }
        $issues = Test-FrontmatterPresence -FilePath $filePath -FileTypeInfo $fileType
        $issues | Should -HaveCount 0
    }
}

#endregion

#region Get-FrontmatterFromFile

Describe 'Get-FrontmatterFromFile' -Tag 'Unit' {
    It 'parses valid YAML frontmatter' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $fm = Get-FrontmatterFromFile -FilePath $filePath
        $fm | Should -Not -BeNullOrEmpty
        $fm['title'] | Should -Not -BeNullOrEmpty
        $fm['description'] | Should -Not -BeNullOrEmpty
    }

    It 'returns null for file without frontmatter' {
        $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
        $fm = Get-FrontmatterFromFile -FilePath $filePath
        $fm | Should -BeNullOrEmpty
    }

    It 'parses instruction file frontmatter' {
        $filePath = Join-Path $script:FixtureDir 'valid-instruction.md'
        $fm = Get-FrontmatterFromFile -FilePath $filePath
        $fm | Should -Not -BeNullOrEmpty
        $fm['description'] | Should -Not -BeNullOrEmpty
        $fm['applyTo'] | Should -Not -BeNullOrEmpty
    }
}

#endregion

#region Test-TitleField

Describe 'Test-TitleField' -Tag 'Unit' {
    It 'returns no issues when title is present and valid' {
        $fm = @{ title = 'My Document' }
        $issues = Test-TitleField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns no issues when title is missing and not required' {
        $fm = @{ description = 'Some description' }
        $issues = Test-TitleField -Frontmatter $fm -Required $false
        $issues | Should -HaveCount 0
    }

    It 'returns error when title is missing and required' {
        $fm = @{ description = 'Some description' }
        $issues = Test-TitleField -Frontmatter $fm -Required $true
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
        $issues[0].Field | Should -Be 'title'
    }

    It 'returns error when title is empty' {
        $fm = @{ title = '' }
        $issues = Test-TitleField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }

    It 'returns error when title is whitespace' {
        $fm = @{ title = '   ' }
        $issues = Test-TitleField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }
}

#endregion

#region Test-DescriptionField

Describe 'Test-DescriptionField' -Tag 'Unit' {
    It 'returns no issues when description is present and valid' {
        $fm = @{ description = 'A valid description' }
        $issues = Test-DescriptionField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns no issues when description is missing and not required' {
        $fm = @{ title = 'Title' }
        $issues = Test-DescriptionField -Frontmatter $fm -Required $false
        $issues | Should -HaveCount 0
    }

    It 'returns error when description is missing and required' {
        $fm = @{ title = 'Title' }
        $issues = Test-DescriptionField -Frontmatter $fm -Required $true
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
        $issues[0].Field | Should -Be 'description'
    }

    It 'returns error when description is empty' {
        $fm = @{ description = '' }
        $issues = Test-DescriptionField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }

    It 'returns error when description is whitespace' {
        $fm = @{ description = '   ' }
        $issues = Test-DescriptionField -Frontmatter $fm
        $issues | Should -HaveCount 1
    }
}

#endregion

#region Test-DateField

Describe 'Test-DateField' -Tag 'Unit' {
    It 'returns no issues for valid ISO 8601 date' {
        $fm = @{ 'ms.date' = '2025-01-15' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns no issues when ms.date is absent' {
        $fm = @{ title = 'Title' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns error for slash-delimited date' {
        $fm = @{ 'ms.date' = '01/15/2025' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
        $issues[0].Field | Should -Be 'ms.date'
    }

    It 'returns error for MM-DD-YYYY format' {
        $fm = @{ 'ms.date' = '01-15-2025' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }

    It 'returns error for text date' {
        $fm = @{ 'ms.date' = 'January 15, 2025' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }

    It 'returns error for invalid date values' {
        $fm = @{ 'ms.date' = '2025-13-45' }
        $issues = Test-DateField -Frontmatter $fm
        $issues | Should -HaveCount 1
    }

    Context 'Edge case date formats' {
        It 'rejects ISO date with time component' {
            $fm = @{ 'ms.date' = '2025-01-15T10:30:00' }
            $issues = Test-DateField -Frontmatter $fm
            $issues | Should -HaveCount 1
            $issues[0].Type | Should -Be 'Error'
        }

        It 'rejects date with timezone offset' {
            $fm = @{ 'ms.date' = '2025-01-15+05:00' }
            $issues = Test-DateField -Frontmatter $fm
            $issues | Should -HaveCount 1
            $issues[0].Type | Should -Be 'Error'
        }

        It 'rejects placeholder date string' {
            $fm = @{ 'ms.date' = 'YYYY-MM-DD' }
            $issues = Test-DateField -Frontmatter $fm
            $issues | Should -HaveCount 1
            $issues[0].Type | Should -Be 'Error'
        }

        It 'accepts valid ISO date' {
            $fm = @{ 'ms.date' = '2025-06-15' }
            $issues = Test-DateField -Frontmatter $fm
            $issues | Should -HaveCount 0
        }
    }
}

#endregion

#region Test-AuthorField

Describe 'Test-AuthorField' -Tag 'Unit' {
    It 'returns no issues when author is present and valid' {
        $fm = @{ author = 'john-doe' }
        $issues = Test-AuthorField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns no issues when author is absent' {
        $fm = @{ title = 'Title' }
        $issues = Test-AuthorField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns warning when author is empty' {
        $fm = @{ author = '' }
        $issues = Test-AuthorField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Warning'
        $issues[0].Field | Should -Be 'author'
    }

    It 'returns warning when author is whitespace' {
        $fm = @{ author = '   ' }
        $issues = Test-AuthorField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Warning'
    }
}

#endregion

#region Test-ApplyToField

Describe 'Test-ApplyToField' -Tag 'Unit' {
    It 'returns no issues when applyTo is present and valid' {
        $fm = @{ applyTo = '**/*.sh' }
        $issues = Test-ApplyToField -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns no issues when applyTo is missing and not required' {
        $fm = @{ description = 'Desc' }
        $issues = Test-ApplyToField -Frontmatter $fm -Required $false
        $issues | Should -HaveCount 0
    }

    It 'returns error when applyTo is missing and required' {
        $fm = @{ description = 'Desc' }
        $issues = Test-ApplyToField -Frontmatter $fm -Required $true
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
        $issues[0].Field | Should -Be 'applyTo'
    }

    It 'returns error when applyTo is empty' {
        $fm = @{ applyTo = '' }
        $issues = Test-ApplyToField -Frontmatter $fm
        $issues | Should -HaveCount 1
        $issues[0].Type | Should -Be 'Error'
    }

    Context 'Glob edge cases' {
        It 'accepts complex multi-pattern globs' {
            $fm = @{ applyTo = '**/*.{ts,tsx,js,jsx}' }
            $issues = Test-ApplyToField -Frontmatter $fm
            $issues | Should -HaveCount 0
        }

        It 'accepts negation pattern globs' {
            $fm = @{ applyTo = '!**/node_modules/**' }
            $issues = Test-ApplyToField -Frontmatter $fm
            $issues | Should -HaveCount 0
        }
    }
}

#endregion

#region Test-DocumentationFileFields

Describe 'Test-DocumentationFileFields' -Tag 'Unit' {
    It 'returns no issues for valid documentation frontmatter' {
        $fm = @{
            title       = 'Guide Title'
            description = 'A valid description'
            author      = 'author-name'
            'ms.date'   = '2025-01-15'
        }
        $issues = Test-DocumentationFileFields -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns errors for missing required fields' {
        $fm = @{ author = 'author-name' }
        $issues = Test-DocumentationFileFields -Frontmatter $fm
        $errors = $issues | Where-Object { $_.Type -eq 'Error' }
        $errors.Count | Should -BeGreaterOrEqual 2
        $errors.Field | Should -Contain 'title'
        $errors.Field | Should -Contain 'description'
    }

    It 'returns error for invalid date format' {
        $fm = @{
            title       = 'Title'
            description = 'Description'
            'ms.date'   = '01/15/2025'
        }
        $issues = Test-DocumentationFileFields -Frontmatter $fm
        $dateErrors = $issues | Where-Object { $_.Field -eq 'ms.date' }
        $dateErrors | Should -HaveCount 1
    }
}

#endregion

#region Test-InstructionFileFields

Describe 'Test-InstructionFileFields' -Tag 'Unit' {
    It 'returns no issues for valid instruction frontmatter' {
        $fm = @{
            description = 'Shell script instructions'
            applyTo     = '**/*.sh'
        }
        $issues = Test-InstructionFileFields -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns errors when required fields missing' {
        $fm = @{ name = 'test' }
        $issues = Test-InstructionFileFields -Frontmatter $fm
        $errors = $issues | Where-Object { $_.Type -eq 'Error' }
        $errors.Count | Should -BeGreaterOrEqual 1
        $errors.Field | Should -Contain 'description'
    }

    It 'returns error when description is empty' {
        $fm = @{
            description = ''
            applyTo     = '**/*.sh'
        }
        $issues = Test-InstructionFileFields -Frontmatter $fm
        $issues | Where-Object { $_.Field -eq 'description' } | Should -HaveCount 1
    }
}

#endregion

#region Test-GitHubResourceFileFields

Describe 'Test-GitHubResourceFileFields' -Tag 'Unit' {
    Context 'Instruction files' {
        It 'validates instruction fields' {
            $fm = @{
                description = 'Instructions'
                applyTo     = '**/*.sh'
            }
            $fileType = New-FileTypeInfo -Properties @{ IsInstruction = $true }
            $issues = Test-GitHubResourceFileFields -Frontmatter $fm -FileTypeInfo $fileType
            $issues | Should -HaveCount 0
        }

        It 'returns errors for invalid instruction' {
            $fm = @{ name = 'test' }
            $fileType = New-FileTypeInfo -Properties @{ IsInstruction = $true }
            $issues = Test-GitHubResourceFileFields -Frontmatter $fm -FileTypeInfo $fileType
            $issues | Where-Object { $_.Type -eq 'Error' } | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Prompt files' {
        It 'validates prompt description' {
            $fm = @{ description = 'A valid prompt' }
            $fileType = New-FileTypeInfo -Properties @{ IsPrompt = $true }
            $issues = Test-GitHubResourceFileFields -Frontmatter $fm -FileTypeInfo $fileType
            $issues | Should -HaveCount 0
        }

        It 'returns error when prompt missing description' {
            $fm = @{ name = 'summarize' }
            $fileType = New-FileTypeInfo -Properties @{ IsPrompt = $true }
            $issues = Test-GitHubResourceFileFields -Frontmatter $fm -FileTypeInfo $fileType
            $issues | Should -HaveCount 1
            $issues[0].Field | Should -Be 'description'
        }
    }
}

#endregion

#region Test-RootCommunityFileFields

Describe 'Test-RootCommunityFileFields' -Tag 'Unit' {
    It 'returns no issues for valid root community frontmatter' {
        $fm = @{
            title       = 'Project Title'
            description = 'A project description'
        }
        $issues = Test-RootCommunityFileFields -Frontmatter $fm
        $issues | Should -HaveCount 0
    }

    It 'returns errors for missing title and description' {
        $fm = @{ author = 'someone' }
        $issues = Test-RootCommunityFileFields -Frontmatter $fm
        $errors = $issues | Where-Object { $_.Type -eq 'Error' }
        $errors.Count | Should -Be 2
        $errors.Field | Should -Contain 'title'
        $errors.Field | Should -Contain 'description'
    }

    It 'returns error for missing title only' {
        $fm = @{ description = 'A description' }
        $issues = Test-RootCommunityFileFields -Frontmatter $fm
        $errors = $issues | Where-Object { $_.Type -eq 'Error' }
        $errors | Should -HaveCount 1
        $errors[0].Field | Should -Be 'title'
    }
}

#endregion

#region Test-SingleFileFrontmatter

Describe 'Test-SingleFileFrontmatter' -Tag 'Unit' {
    It 'validates a valid documentation file with no issues' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md'
        $result.IsValid | Should -BeTrue
        $result.ErrorCount | Should -Be 0
    }

    It 'validates a valid instruction file with no issues' {
        $filePath = Join-Path $script:FixtureDir 'valid-instruction.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath '.github/instructions/valid.instructions.md'
        $result.IsValid | Should -BeTrue
    }

    It 'validates a valid prompt file with no issues' {
        $filePath = Join-Path $script:FixtureDir 'valid-prompt.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath '.github/prompts/valid.prompt.md'
        $result.IsValid | Should -BeTrue
    }

    It 'validates a valid root community file with no issues' {
        $filePath = Join-Path $script:FixtureDir 'valid-root-community-with-footer.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md'
        $result.IsValid | Should -BeTrue
    }

    It 'returns error for file missing frontmatter' {
        $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/missing-frontmatter.md'
        $result.IsValid | Should -BeFalse
        $result.HasErrors | Should -BeTrue
    }

    It 'returns error for empty description field' {
        $filePath = Join-Path $script:FixtureDir 'empty-description.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/empty-description.md'
        $result.HasErrors | Should -BeTrue
    }

    It 'returns error for invalid date format' {
        $filePath = Join-Path $script:FixtureDir 'invalid-date.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/invalid-date.md'
        $dateIssues = $result.Issues | Where-Object { $_.Field -eq 'ms.date' }
        $dateIssues | Should -Not -BeNullOrEmpty
    }

    It 'returns valid for non-matching file types' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'shared/ci/linting/README.md'
        $result.IsValid | Should -BeTrue
        $result.Issues.Count | Should -Be 0
    }

    It 'sets file path and relative path on result' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $relPath = 'docs/valid-docs.md'
        $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath $relPath
        $result.FilePath | Should -Be $filePath
        $result.RelativePath | Should -Be $relPath
    }

    Context 'Non-requiring file types return empty results' {
        It 'returns valid with no issues for nested non-matching path' {
            $content = "# Test`nSome content"
            $tempFile = Join-Path $TestDrive 'helper.md'
            Set-Content -Path $tempFile -Value $content
            $result = Test-SingleFileFrontmatter -FilePath $tempFile -RelativePath 'src/utils/helper.md'
            $result.IsValid | Should -BeTrue
            $result.Issues.Count | Should -Be 0
        }

        It 'returns valid with no issues for deeply nested script path' {
            $content = "# Deep`nNested content"
            $tempFile = Join-Path $TestDrive 'deep.md'
            Set-Content -Path $tempFile -Value $content
            $result = Test-SingleFileFrontmatter -FilePath $tempFile -RelativePath 'deploy/modules/internal/deep.md'
            $result.IsValid | Should -BeTrue
            $result.Issues.Count | Should -Be 0
        }
    }
}

#endregion

#region New-ValidationSummary function

Describe 'New-ValidationSummary function' -Tag 'Unit' {
    It 'returns a ValidationSummary instance' {
        $summary = New-ValidationSummary
        $summary | Should -Not -BeNullOrEmpty
        $null -eq $summary.Results | Should -BeFalse
        $summary.Results.Count | Should -Be 0
    }
}

#endregion

#region Test-MarkdownFooter

Describe 'Test-MarkdownFooter' -Tag 'Unit' {
    It 'returns true for plain-text Copilot footer' {
        $content = @"
# Title

Some content here.

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for footer without "then"' {
        $content = @"
# Title

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, carefully refined by our team of discerning human reviewers.
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for footer with trailing period' {
        $content = @"
# Title

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for footer without trailing period' {
        $content = @"
# Title

🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns false for empty string' {
        Test-MarkdownFooter -Content '' | Should -BeFalse
    }

    It 'returns false for content without footer' {
        $content = "# Title`n`nJust regular content."
        Test-MarkdownFooter -Content $content | Should -BeFalse
    }

    It 'returns true for bold-wrapped footer' {
        $content = @"
# Title

**🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.**
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for italic-wrapped footer' {
        $content = @"
# Title

*🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.*
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for underscore-italic footer' {
        $content = @"
# Title

_🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers._
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for inline-code footer' {
        $content = @"
# Title

``🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.``
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true when HTML comments precede footer' {
        $content = @"
# Title

<!-- comment -->
🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for fixture file with footer' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs-with-footer.md'
        $content = Get-Content -Path $filePath -Raw
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns false for fixture file without footer' {
        $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
        $content = Get-Content -Path $filePath -Raw
        Test-MarkdownFooter -Content $content | Should -BeFalse
    }

    It 'returns true for fixture with bold formatting' {
        $filePath = Join-Path $script:FixtureDir 'footer-with-formatting.md'
        $content = Get-Content -Path $filePath -Raw
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns true for fixture with HTML comment' {
        $filePath = Join-Path $script:FixtureDir 'footer-with-html-comment.md'
        $content = Get-Content -Path $filePath -Raw
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }

    It 'returns false for partial/truncated footer' {
        $content = "# Title`n`n🤖 Crafted with precision by ✨Copilot"
        Test-MarkdownFooter -Content $content | Should -BeFalse
    }

    It 'returns true for strikethrough-wrapped footer' {
        $content = @"
# Title

~~🤖 Crafted with precision by ✨Copilot following brilliant human instruction, then carefully refined by our team of discerning human reviewers.~~
"@
        Test-MarkdownFooter -Content $content | Should -BeTrue
    }
}

#endregion

#region Test-FooterPresence

Describe 'Test-FooterPresence' -Tag 'Unit' {
    It 'returns null when footer is present' {
        $result = Test-FooterPresence -HasFooter $true -RelativePath 'docs/guide.md'
        $result | Should -BeNullOrEmpty
    }

    It 'returns ValidationIssue when footer is missing' {
        $result = Test-FooterPresence -HasFooter $false -RelativePath 'docs/guide.md'
        $result | Should -Not -BeNullOrEmpty
        $result.Field | Should -Be 'footer'
        $result.Type | Should -Be 'Warning'
    }

    It 'defaults to Warning severity' {
        $result = Test-FooterPresence -HasFooter $false -RelativePath 'docs/guide.md'
        $result.Type | Should -Be 'Warning'
    }

    It 'respects Error severity override' {
        $result = Test-FooterPresence -HasFooter $false -RelativePath 'README.md' -Severity 'Error'
        $result.Type | Should -Be 'Error'
    }

    It 'sets correct message' {
        $result = Test-FooterPresence -HasFooter $false -RelativePath 'docs/guide.md'
        $result.Message | Should -Be 'Missing standard Copilot footer'
    }

    It 'sets FilePath to RelativePath' {
        $result = Test-FooterPresence -HasFooter $false -RelativePath 'docs/test.md'
        $result.FilePath | Should -Be 'docs/test.md'
    }
}

#endregion

#region Test-SingleFileFrontmatter footer integration

Describe 'Test-SingleFileFrontmatter footer integration' -Tag 'Unit' {
    Context 'Footer detection in documentation files' {
        It 'reports no footer issue when footer is present' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs-with-footer.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'reports footer warning for documentation file without footer' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 1
            $footerIssues[0].Type | Should -Be 'Warning'
        }

        It 'reports footer error for root community file without footer' {
            $filePath = Join-Path $script:FixtureDir 'valid-root-community.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 1
            $footerIssues[0].Type | Should -Be 'Error'
        }

        It 'reports no footer error for root community file with footer' {
            $filePath = Join-Path $script:FixtureDir 'valid-root-community-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'CONTRIBUTING.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }
    }

    Context 'AI artifact exemptions' {
        It 'skips footer validation for instruction files' {
            $filePath = Join-Path $script:FixtureDir 'valid-instruction.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath '.github/instructions/style.instructions.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'skips footer validation for prompt files' {
            $filePath = Join-Path $script:FixtureDir 'valid-prompt.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath '.github/prompts/summarize.prompt.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }
    }

    Context 'SkipFooterValidation switch' {
        It 'skips footer check when switch is set' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md' -SkipFooterValidation
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'still validates frontmatter fields when footer is skipped' {
            $filePath = Join-Path $script:FixtureDir 'empty-description.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/empty-description.md' -SkipFooterValidation
            $result.HasErrors | Should -BeTrue
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }
    }

    Context 'FooterExcludePaths parameter' {
        It 'excludes files matching wildcard pattern' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md' -FooterExcludePaths @('docs/**')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'does not exclude non-matching files' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md' -FooterExcludePaths @('deploy/**')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 1
        }

        It 'handles multiple exclusion patterns' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md' -FooterExcludePaths @('deploy/**', 'docs/**')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'normalizes backslash separators in paths' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs\valid-docs.md' -FooterExcludePaths @('docs/**')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'accepts empty exclusion array' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/valid-docs.md' -FooterExcludePaths @()
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 1
        }
    }

    Context 'FrontmatterExcludePaths parameter' {
        It 'skips frontmatter validation for excluded file' {
            $filePath = Join-Path $script:FixtureDir 'no-frontmatter-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md' -FrontmatterExcludePaths @('README.md')
            $frontmatterIssues = $result.Issues | Where-Object { $_.Field -ne 'footer' }
            $frontmatterIssues | Should -HaveCount 0
        }

        It 'still validates footer for excluded file' {
            $filePath = Join-Path $script:FixtureDir 'no-frontmatter-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md' -FrontmatterExcludePaths @('README.md')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }

        It 'reports missing footer for excluded file without footer' {
            $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md' -FrontmatterExcludePaths @('README.md')
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 1
            $footerIssues[0].Type | Should -Be 'Error'
        }

        It 'does not exclude non-matching files' {
            $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/missing-frontmatter.md' -FrontmatterExcludePaths @('README.md')
            $result.HasErrors | Should -BeTrue
        }

        It 'handles wildcard patterns' {
            $filePath = Join-Path $script:FixtureDir 'no-frontmatter-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'README.md' -FrontmatterExcludePaths @('*.md')
            $frontmatterIssues = $result.Issues | Where-Object { $_.Field -ne 'footer' }
            $frontmatterIssues | Should -HaveCount 0
        }

        It 'normalizes backslash separators in paths' {
            $filePath = Join-Path $script:FixtureDir 'no-frontmatter-with-footer.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs\README.md' -FrontmatterExcludePaths @('docs/README.md')
            $frontmatterIssues = $result.Issues | Where-Object { $_.Field -ne 'footer' }
            $frontmatterIssues | Should -HaveCount 0
        }

        It 'accepts empty exclusion array' {
            $filePath = Join-Path $script:FixtureDir 'missing-frontmatter.md'
            $result = Test-SingleFileFrontmatter -FilePath $filePath -RelativePath 'docs/missing-frontmatter.md' -FrontmatterExcludePaths @()
            $result.HasErrors | Should -BeTrue
        }
    }

    Context 'Non-requiring file types skip footer' {
        It 'does not validate footer for non-matching paths' {
            $content = "# Test`nSome content"
            $tempFile = Join-Path $TestDrive 'helper.md'
            Set-Content -Path $tempFile -Value $content
            $result = Test-SingleFileFrontmatter -FilePath $tempFile -RelativePath 'shared/ci/linting/README.md'
            $footerIssues = $result.Issues | Where-Object { $_.Field -eq 'footer' }
            $footerIssues | Should -HaveCount 0
        }
    }
}

#endregion
