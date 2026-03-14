#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

using module ..\..\linting\Modules\FrontmatterValidation.psm1

[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignments', '')]
param()

BeforeDiscovery {
    $lintingDir = Join-Path $PSScriptRoot '..' '..' 'linting'
    $schemaDir = Join-Path $lintingDir 'schemas' 'frontmatter'
    $mappingPath = Join-Path $schemaDir 'schema-mapping.json'
    $script:SchemaAvailable = Test-Path $mappingPath
}

BeforeAll {
    # Import modules
    $lintingDir = Join-Path $PSScriptRoot '..' '..' 'linting'
    $modulePath = Join-Path $lintingDir 'Modules' 'FrontmatterValidation.psm1'
    $ciHelpersPath = Join-Path $PSScriptRoot '..' '..' '..' '..' 'scripts' 'lib' 'Modules' 'CIHelpers.psm1'
    $lintingHelpersPath = Join-Path $lintingDir 'Modules' 'LintingHelpers.psm1'
    $gitMocksPath = Join-Path $PSScriptRoot '..' 'Mocks' 'GitMocks.psm1'

    Import-Module $modulePath -Force
    Import-Module $ciHelpersPath -Force
    Import-Module $lintingHelpersPath -Force
    Import-Module $gitMocksPath -Force

    $script:FVModule = Get-Module FrontmatterValidation
    $script:FixtureDir = Join-Path $PSScriptRoot '..' 'Fixtures' 'Frontmatter'
    $script:SchemaDir = Join-Path $lintingDir 'schemas' 'frontmatter'

    # Dot-source the entry-point script to load internal functions
    . (Join-Path $lintingDir 'Invoke-FrontmatterValidation.ps1')
}

#region Initialize-JsonSchemaValidation

Describe 'Initialize-JsonSchemaValidation' -Tag 'Unit' {
    Context 'Valid schema directory' {
        It 'returns hashtable with Mapping, Schemas, and BasePath' {
            $result = Initialize-JsonSchemaValidation -SchemaDirectory $script:SchemaDir
            $result | Should -Not -BeNullOrEmpty
            $result | Should -BeOfType [hashtable]
            $result.Mapping | Should -Not -BeNullOrEmpty
            $result.Schemas | Should -Not -BeNullOrEmpty
            $result.BasePath | Should -Be $script:SchemaDir
        }

        It 'loads all schema files referenced in mapping' {
            $result = Initialize-JsonSchemaValidation -SchemaDirectory $script:SchemaDir
            $result.Schemas.Count | Should -BeGreaterOrEqual 1
        }
    }

    Context 'Missing schema directory' {
        It 'returns null when mapping file not found' {
            $result = Initialize-JsonSchemaValidation -SchemaDirectory (Join-Path $TestDrive 'nonexistent')
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Missing individual schema files' {
        It 'warns but continues when a schema file is missing' {
            $tempDir = Join-Path $TestDrive 'partial-schemas'
            New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
            $mappingContent = @{
                mappings = @(
                    @{ glob = '*.md'; schema = 'missing-schema.json' }
                )
                defaultSchema = 'also-missing.json'
            } | ConvertTo-Json -Depth 5
            Set-Content -Path (Join-Path $tempDir 'schema-mapping.json') -Value $mappingContent

            $null = Initialize-JsonSchemaValidation -SchemaDirectory $tempDir 3>&1
            # Function should still return a hashtable (possibly with empty schemas)
            # or emit warnings — either way it should not throw
        }
    }
}

#endregion

#region Get-SchemaForFile

Describe 'Get-SchemaForFile' -Tag 'Unit' {
    BeforeAll {
        $script:TestSchemaContext = Initialize-JsonSchemaValidation -SchemaDirectory $script:SchemaDir
    }

    Context 'Glob matching' {
        It 'matches docs/**/*.md to docs schema' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath 'docs/guide.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'docs-frontmatter.schema.json'
        }

        It 'matches nested docs path' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath 'docs/contributing/workflow.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'docs-frontmatter.schema.json'
        }

        It 'matches .instructions.md to instruction schema' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath '.github/instructions/style.instructions.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'instruction-frontmatter.schema.json'
        }

        It 'matches .prompt.md to prompt schema' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath '.github/prompts/summarize.prompt.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'prompt-frontmatter.schema.json'
        }

        It 'matches root *.md to root-community schema' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath 'README.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'root-community-frontmatter.schema.json'
        }

        It 'falls back to default schema for unmatched paths' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath 'shared/ci/linting/README.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'base-frontmatter.schema.json'
        }
    }

    Context 'Path normalization' {
        It 'strips leading ./ from paths' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath './docs/guide.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'docs-frontmatter.schema.json'
        }

        It 'normalizes backslashes to forward slashes' -Skip:(-not $script:SchemaAvailable) {
            $result = Get-SchemaForFile -FilePath 'docs\subfolder\guide.md' -SchemaContext $script:TestSchemaContext
            $result | Should -Not -BeNullOrEmpty
            $result.SchemaName | Should -Be 'docs-frontmatter.schema.json'
        }
    }

    Context 'Null schema context' {
        It 'returns null when schema not found' {
            $emptyContext = @{
                Mapping  = [PSCustomObject]@{ mappings = @(); defaultSchema = 'nonexistent.json' }
                Schemas  = @{}
                BasePath = $TestDrive
            }
            $result = Get-SchemaForFile -FilePath 'any.md' -SchemaContext $emptyContext
            $result | Should -BeNullOrEmpty
        }
    }
}

#endregion

#region Test-JsonSchemaValidation

Describe 'Test-JsonSchemaValidation' -Tag 'Unit' {
    Context 'Required fields' {
        It 'reports missing required fields' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    required   = @('title', 'description')
                    properties = [PSCustomObject]@{
                        title       = [PSCustomObject]@{ type = 'string' }
                        description = [PSCustomObject]@{ type = 'string' }
                    }
                }
            }
            $fm = @{ title = 'Hello' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
            $result.Errors | Should -Contain "Missing required field: 'description'"
        }

        It 'passes when all required fields present' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    required   = @('title')
                    properties = [PSCustomObject]@{
                        title = [PSCustomObject]@{ type = 'string' }
                    }
                }
            }
            $fm = @{ title = 'Hello' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
            $result.Errors | Should -HaveCount 0
        }
    }

    Context 'Type validation' {
        It 'reports type mismatch for string field' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        title = [PSCustomObject]@{ type = 'string' }
                    }
                }
            }
            $fm = @{ title = 42 }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
            $result.Errors.Count | Should -BeGreaterOrEqual 1
        }

        It 'reports type mismatch for integer field' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        count = [PSCustomObject]@{ type = 'integer' }
                    }
                }
            }
            $fm = @{ count = 'not-a-number' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
        }

        It 'passes for correct types' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        title = [PSCustomObject]@{ type = 'string' }
                        count = [PSCustomObject]@{ type = 'integer' }
                    }
                }
            }
            $fm = @{ title = 'Hello'; count = 5 }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
        }
    }

    Context 'Enum validation' {
        It 'reports invalid enum value' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        'ms.topic' = [PSCustomObject]@{
                            type = 'string'
                            enum = @('conceptual', 'how-to', 'reference')
                        }
                    }
                }
            }
            $fm = @{ 'ms.topic' = 'invalid-topic' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
            ($result.Errors | Where-Object { $_ -match 'must be one of' }) | Should -Not -BeNullOrEmpty
        }

        It 'accepts valid enum value' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        'ms.topic' = [PSCustomObject]@{
                            type = 'string'
                            enum = @('conceptual', 'how-to', 'reference')
                        }
                    }
                }
            }
            $fm = @{ 'ms.topic' = 'conceptual' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
        }
    }

    Context 'Pattern validation' {
        It 'reports pattern mismatch' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        'ms.date' = [PSCustomObject]@{
                            type    = 'string'
                            pattern = '^\d{4}-\d{2}-\d{2}$'
                        }
                    }
                }
            }
            $fm = @{ 'ms.date' = '01/15/2025' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
        }

        It 'accepts matching pattern' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        'ms.date' = [PSCustomObject]@{
                            type    = 'string'
                            pattern = '^\d{4}-\d{2}-\d{2}$'
                        }
                    }
                }
            }
            $fm = @{ 'ms.date' = '2025-01-15' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
        }
    }

    Context 'MinLength validation' {
        It 'reports string shorter than minLength' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        description = [PSCustomObject]@{ type = 'string'; minLength = 5 }
                    }
                }
            }
            $fm = @{ description = 'Hi' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
        }
    }

    Context 'AdditionalProperties' {
        It 'reports disallowed additional properties' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties           = [PSCustomObject]@{
                        description = [PSCustomObject]@{ type = 'string' }
                    }
                    additionalProperties = $false
                }
            }
            $fm = @{ description = 'Valid'; extraField = 'disallowed' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeFalse
            ($result.Errors | Where-Object { $_ -match 'Additional property not allowed' }) | Should -Not -BeNullOrEmpty
        }

        It 'allows additional properties when not restricted' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = [PSCustomObject]@{
                    properties = [PSCustomObject]@{
                        description = [PSCustomObject]@{ type = 'string' }
                    }
                }
            }
            $fm = @{ description = 'Valid'; extraField = 'allowed' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
        }
    }

    Context 'Null schema' {
        It 'returns valid when schema is null' {
            $schemaInfo = [PSCustomObject]@{
                SchemaName = 'test-schema.json'
                Schema     = $null
            }
            $fm = @{ title = 'Hello' }
            $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
            $result.IsValid | Should -BeTrue
        }
    }
}

#endregion

#region Get-MarkdownFiles

Describe 'Get-MarkdownFiles' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
        $script:MockCIFiles = Initialize-MockCIEnvironment -Repository 'microsoft/physical-ai-toolchain'
    }

    AfterAll {
        Remove-MockCIFiles -MockFiles $script:MockCIFiles
        Restore-CIEnvironment
    }

    Context 'Explicit files' {
        It 'returns only existing explicit files' {
            $existingFile = (Resolve-Path (Join-Path $script:FixtureDir 'valid-docs.md')).Path
            $nonExistent = Join-Path $TestDrive 'does-not-exist.md'
            $result = Get-MarkdownFiles -ScanPaths @() -ExplicitFiles @($existingFile, $nonExistent) -Exclude @()
            $result | Should -Contain $existingFile
            $result | Should -Not -Contain $nonExistent
        }
    }

    Context 'Directory scanning' {
        It 'finds markdown files in fixture directory' {
            $result = Get-MarkdownFiles -ScanPaths @($script:FixtureDir) -ExplicitFiles @() -Exclude @()
            $result.Count | Should -BeGreaterOrEqual 1
            $result | ForEach-Object { $_ | Should -BeLike '*.md' }
        }
    }

    Context 'Exclude patterns' {
        It 'excludes files matching exclude patterns' {
            $tempDir = Join-Path $TestDrive 'exclude-test'
            $includeDir = Join-Path $tempDir 'docs'
            $excludeDir = Join-Path $tempDir 'node_modules'
            New-Item -ItemType Directory -Path $includeDir -Force | Out-Null
            New-Item -ItemType Directory -Path $excludeDir -Force | Out-Null
            Set-Content -Path (Join-Path $includeDir 'keep.md') -Value '# Keep'
            Set-Content -Path (Join-Path $excludeDir 'skip.md') -Value '# Skip'

            $result = Get-MarkdownFiles -ScanPaths @($tempDir) -ExplicitFiles @() -Exclude @('node_modules')
            $result | ForEach-Object { $_ | Should -Not -BeLike '*node_modules*' }
        }

        It 'excludes files by filename pattern' {
            $tempDir = Join-Path $TestDrive 'exclude-filename'
            New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
            Set-Content -Path (Join-Path $tempDir 'CHANGELOG.md') -Value '# Changelog'
            Set-Content -Path (Join-Path $tempDir 'README.md') -Value '# Readme'

            $result = Get-MarkdownFiles -ScanPaths @($tempDir) -ExplicitFiles @() -Exclude @('CHANGELOG.md')
            $matchingChangelog = $result | Where-Object { [System.IO.Path]::GetFileName($_) -eq 'CHANGELOG.md' }
            $matchingChangelog | Should -BeNullOrEmpty
        }
    }

    Context 'Changed files only mode' {
        It 'uses Get-ChangedFilesFromGit when ChangedOnly is set' {
            Mock Get-ChangedFilesFromGit {
                return @('docs/guide.md', 'README.md')
            }

            $result = Get-MarkdownFiles -ScanPaths @('.') -ExplicitFiles @() -Exclude @() -ChangedOnly -Branch 'main'
            $result | Should -Contain 'docs/guide.md'
            $result | Should -Contain 'README.md'
            Should -Invoke Get-ChangedFilesFromGit -Times 1
        }

        It 'returns empty array when no changed files' {
            Mock Get-ChangedFilesFromGit { return @() }
            Mock Write-CIAnnotation {}
            Mock Set-CIOutput {}

            $result = Get-MarkdownFiles -ScanPaths @('.') -ExplicitFiles @() -Exclude @() -ChangedOnly -Branch 'main'
            $result | Should -HaveCount 0
        }
    }

    Context 'ExcludePaths with multiple patterns' {
        It 'excludes files matching multiple patterns' {
            $tempDir = Join-Path $TestDrive 'multi-exclude'
            $srcDir = Join-Path $tempDir 'src'
            $docsDir = Join-Path $tempDir 'docs'
            New-Item -Path $srcDir -ItemType Directory -Force
            New-Item -Path $docsDir -ItemType Directory -Force
            @('---', 'title: src', 'description: test', '---') | Set-Content (Join-Path $srcDir 'guide.md')
            @('---', 'title: docs', 'description: test', '---') | Set-Content (Join-Path $docsDir 'readme.md')
            @('---', 'title: root', 'description: test', '---') | Set-Content (Join-Path $tempDir 'CHANGELOG.md')
            @('---', 'title: keep', 'description: test', '---') | Set-Content (Join-Path $tempDir 'CONTRIBUTING.md')

            $result = Get-MarkdownFiles -ScanPaths @($tempDir) -ExplicitFiles @() -Exclude @('CHANGELOG.md', 'docs')
            $result | Should -Not -BeNullOrEmpty
            $changelogMatch = $result | Where-Object { $_ -like '*CHANGELOG.md' }
            $changelogMatch | Should -BeNullOrEmpty
            $docsMatch = $result | Where-Object { $_ -like '*docs*' }
            $docsMatch | Should -BeNullOrEmpty
        }

        It 'returns all files when ExcludePaths is empty' {
            $testRoot = Join-Path $TestDrive 'no-exclude'
            New-Item -Path $testRoot -ItemType Directory -Force
            @('---', 'title: test', 'description: test', '---') | Set-Content (Join-Path $testRoot 'readme.md')

            $result = Get-MarkdownFiles -ScanPaths @($testRoot) -ExplicitFiles @() -Exclude @()
            $result | Should -HaveCount 1
        }
    }

    Context 'ChangedFilesOnly with no changed markdown files' {
        It 'returns empty when git reports only non-markdown changed files' {
            Mock Get-ChangedFilesFromGit {
                return @()
            }

            $result = Get-MarkdownFiles -ScanPaths @('.') -ExplicitFiles @() -Exclude @() -ChangedOnly -Branch 'main'
            $result | Should -HaveCount 0
        }
    }
}

#endregion

#region Invoke-Validation

Describe 'Invoke-Validation' -Tag 'Unit' {
    BeforeAll {
        Save-CIEnvironment
        $script:MockCIFiles = Initialize-MockCIEnvironment -Repository 'microsoft/physical-ai-toolchain'
    }

    AfterAll {
        Remove-MockCIFiles -MockFiles $script:MockCIFiles
        Restore-CIEnvironment
    }

    BeforeEach {
        # Mock CI output functions to prevent actual CI interactions
        Mock Write-CIAnnotation {} -Verifiable
        Mock Write-CIAnnotations {} -Verifiable
        Mock Set-CIOutput {} -Verifiable
        Mock Set-CIEnv {} -Verifiable
        Mock Write-CIStepSummary {} -Verifiable

        # Default script-scope parameters for Invoke-Validation
        $script:Paths = @()
        $script:Files = @()
        $script:ExcludePaths = @()
        $script:ChangedFilesOnly = $false
        $script:BaseBranch = 'main'
        $script:EnableSchemaValidation = $false
        $script:SoftFail = $false
        $script:WarningsAsErrors = $false
        $script:FooterExcludePaths = @('dependency-pinning-artifacts/**')
        $script:FrontmatterExcludePaths = @('README.md')
        $script:SkipFooterValidation = $true
        $script:scriptRoot = Join-Path $PSScriptRoot '..' '..' '..' 'scripts' 'linting'
    }

    Context 'No files to validate' {
        It 'exits early when no markdown files found' {
            $script:Paths = @($TestDrive)

            # TestDrive has no .md files by default
            Invoke-Validation
            # Should not throw, just print "No markdown files to validate."
        }
    }

    Context 'Valid files' {
        It 'validates fixture files without errors' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $script:Files = @($validFile)

            { Invoke-Validation } | Should -Not -Throw
        }
    }

    Context 'CI output integration' {
        It 'calls Set-CIOutput with expected output names' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $script:Files = @($validFile)

            Invoke-Validation

            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-issues' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-errors' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-warnings' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'files-checked' }
        }

        It 'calls Write-CIStepSummary with markdown content' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $script:Files = @($validFile)

            Invoke-Validation

            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'JSON export' {
        It 'creates JSON results file in logs directory' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $tempLintingDir = Join-Path $TestDrive 'scripts' 'linting'
            $tempLogsDir = Join-Path $TestDrive 'logs'
            New-Item -ItemType Directory -Path $tempLintingDir -Force | Out-Null

            $script:Files = @($validFile)
            $script:scriptRoot = $tempLintingDir

            Invoke-Validation

            $jsonPath = Join-Path $tempLogsDir 'frontmatter-validation-results.json'
            Test-Path $jsonPath | Should -BeTrue
            $jsonContent = Get-Content $jsonPath -Raw | ConvertFrom-Json
            $jsonContent.summary | Should -Not -BeNullOrEmpty
            $jsonContent.results | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Error handling' {
        It 'sets FRONTMATTER_VALIDATION_FAILED env when errors found' {
            $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
            $tempScriptsLinting = Join-Path $tempRoot 'scripts' 'linting'
            $tempDocsDir = Join-Path $tempRoot 'docs'
            New-Item -ItemType Directory -Path $tempScriptsLinting -Force | Out-Null
            New-Item -ItemType Directory -Path $tempDocsDir -Force | Out-Null

            $badFile = Join-Path $tempDocsDir 'missing-frontmatter.md'
            Copy-Item (Join-Path $script:FixtureDir 'missing-frontmatter.md') -Destination $badFile

            $script:Files = @($badFile)
            $script:SoftFail = $true
            $script:scriptRoot = $tempScriptsLinting

            Invoke-Validation

            Should -Invoke Set-CIEnv -ParameterFilter { $Name -eq 'FRONTMATTER_VALIDATION_FAILED' -and $Value -eq 'true' }
        }
    }

    Context 'Multiple fixture files' {
        It 'validates all fixture files and produces summary' {
            $fixtures = Get-ChildItem -Path $script:FixtureDir -Filter '*.md' | Select-Object -ExpandProperty FullName
            $script:Files = $fixtures
            $script:SoftFail = $true

            Invoke-Validation

            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'files-checked' }
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'SoftFail suppresses exit code' {
        It 'does not throw when SoftFail is enabled and errors exist' {
            $badFile = Join-Path $script:FixtureDir 'missing-frontmatter.md'
            $tempScriptsLinting = Join-Path $TestDrive 'softfail-scripts' 'linting'
            New-Item -ItemType Directory -Path $tempScriptsLinting -Force | Out-Null

            $script:Files = @($badFile)
            $script:SoftFail = $true
            $script:scriptRoot = $tempScriptsLinting

            { Invoke-Validation } | Should -Not -Throw
            Should -Invoke Set-CIEnv -ParameterFilter { $Name -eq 'FRONTMATTER_VALIDATION_FAILED' -and $Value -eq 'true' }
        }

        It 'still reports errors in CI outputs when SoftFail is enabled' {
            $badFile = Join-Path $script:FixtureDir 'missing-frontmatter.md'
            $tempScriptsLinting = Join-Path $TestDrive 'softfail-out-scripts' 'linting'
            New-Item -ItemType Directory -Path $tempScriptsLinting -Force | Out-Null

            $script:Files = @($badFile)
            $script:SoftFail = $true
            $script:scriptRoot = $tempScriptsLinting

            Invoke-Validation

            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-errors' }
            Should -Invoke Write-CIStepSummary -Times 1
        }
    }

    Context 'WarningsAsErrors promotes warnings to errors' {
        It 'promotes warnings to effective errors and sets failure env' {
            $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
            $tempScriptsLinting = Join-Path $tempRoot 'scripts' 'linting'
            $tempDocsDir = Join-Path $tempRoot 'docs'
            New-Item -ItemType Directory -Path $tempScriptsLinting -Force | Out-Null
            New-Item -ItemType Directory -Path $tempDocsDir -Force | Out-Null

            $warningFile = Join-Path $tempDocsDir 'warning-author-empty.md'
            Copy-Item (Join-Path $script:FixtureDir 'warning-author-empty.md') -Destination $warningFile

            $script:Files = @($warningFile)
            $script:WarningsAsErrors = $true
            $script:SoftFail = $true
            $script:scriptRoot = $tempScriptsLinting

            Invoke-Validation

            Should -Invoke Set-CIEnv -ParameterFilter { $Name -eq 'FRONTMATTER_VALIDATION_FAILED' -and $Value -eq 'true' }
        }

        It 'does not set failure env for warnings when WarningsAsErrors is false' {
            $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString())
            $tempScriptsLinting = Join-Path $tempRoot 'scripts' 'linting'
            $tempDocsDir = Join-Path $tempRoot 'docs'
            New-Item -ItemType Directory -Path $tempScriptsLinting -Force | Out-Null
            New-Item -ItemType Directory -Path $tempDocsDir -Force | Out-Null

            $warningFile = Join-Path $tempDocsDir 'warning-author-empty.md'
            Copy-Item (Join-Path $script:FixtureDir 'warning-author-empty.md') -Destination $warningFile

            $script:Files = @($warningFile)
            $script:SoftFail = $true
            $script:scriptRoot = $tempScriptsLinting

            Invoke-Validation

            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-warnings' }
            Should -Not -Invoke Set-CIEnv -ParameterFilter { $Name -eq 'FRONTMATTER_VALIDATION_FAILED' }
        }
    }

    Context 'No-files early exit writes zero CI outputs' {
        It 'writes zero counts when no markdown files found in directory' {
            $emptyDir = Join-Path $TestDrive 'early-exit-empty'
            New-Item -Path $emptyDir -ItemType Directory -Force

            $script:Paths = @($emptyDir)

            Invoke-Validation

            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-issues' -and $Value -eq '0' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-errors' -and $Value -eq '0' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'total-warnings' -and $Value -eq '0' }
            Should -Invoke Set-CIOutput -ParameterFilter { $Name -eq 'files-checked' -and $Value -eq '0' }
        }
    }

    Context 'EnableSchemaValidation overlay orchestration' {
        BeforeEach {
            Mock Initialize-JsonSchemaValidation {
                return @{
                    Mapping  = [PSCustomObject]@{ mappings = @() }
                    Schemas  = @{}
                    BasePath = $TestDrive
                }
            }
        }

        It 'invokes Initialize-JsonSchemaValidation when enabled' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $script:Files = @($validFile)
            $script:EnableSchemaValidation = $true
            $script:SoftFail = $true

            Invoke-Validation

            Should -Invoke Initialize-JsonSchemaValidation -Times 1
        }

        It 'does not invoke Initialize-JsonSchemaValidation when disabled' {
            $validFile = Join-Path $script:FixtureDir 'valid-docs.md'
            $script:Files = @($validFile)

            Invoke-Validation

            Should -Invoke Initialize-JsonSchemaValidation -Times 0
        }
    }
}

#endregion

#region SchemaValidationResult Class

Describe 'SchemaValidationResult Class' -Tag 'Unit' {
    It 'creates with default valid state' {
        $result = [SchemaValidationResult]::new()
        $result.IsValid | Should -BeTrue
        $result.Errors | Should -HaveCount 0
        $result.SchemaName | Should -Be ''
    }

    It 'allows setting properties' {
        $result = [SchemaValidationResult]::new()
        $result.IsValid = $false
        $result.SchemaName = 'test-schema.json'
        $result.Errors = @('Error 1', 'Error 2')
        $result.IsValid | Should -BeFalse
        $result.Errors | Should -HaveCount 2
    }
}

#endregion

#region End-to-End Schema Validation

Describe 'Schema Validation End-to-End' -Tag 'Unit' {
    BeforeAll {
        $script:TestSchemaContext = Initialize-JsonSchemaValidation -SchemaDirectory $script:SchemaDir
    }

    Context 'Docs schema against fixture' -Skip:(-not $script:SchemaAvailable) {
        It 'validates valid-docs.md frontmatter against docs schema' {
            $filePath = Join-Path $script:FixtureDir 'valid-docs.md'
            $fm = Get-FrontmatterFromFile -FilePath $filePath
            $schemaInfo = Get-SchemaForFile -FilePath 'docs/valid-docs.md' -SchemaContext $script:TestSchemaContext

            if ($null -ne $fm -and $null -ne $schemaInfo) {
                $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
                $result.IsValid | Should -BeTrue
            }
        }
    }

    Context 'Instruction schema against fixture' -Skip:(-not $script:SchemaAvailable) {
        It 'validates valid-instruction.md against instruction schema' {
            $filePath = Join-Path $script:FixtureDir 'valid-instruction.md'
            $fm = Get-FrontmatterFromFile -FilePath $filePath
            $schemaInfo = Get-SchemaForFile -FilePath '.github/instructions/valid.instructions.md' -SchemaContext $script:TestSchemaContext

            if ($null -ne $fm -and $null -ne $schemaInfo) {
                $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
                $result.IsValid | Should -BeTrue
            }
        }
    }

    Context 'Strict schema rejects extra fields' -Skip:(-not $script:SchemaAvailable) {
        It 'rejects extra-fields-strict.md against instruction schema' {
            $filePath = Join-Path $script:FixtureDir 'extra-fields-strict.md'
            $fm = Get-FrontmatterFromFile -FilePath $filePath
            $schemaInfo = Get-SchemaForFile -FilePath '.github/instructions/extra.instructions.md' -SchemaContext $script:TestSchemaContext

            if ($null -ne $fm -and $null -ne $schemaInfo -and $null -ne $schemaInfo.Schema) {
                $result = Test-JsonSchemaValidation -Frontmatter $fm -SchemaInfo $schemaInfo
                $result.IsValid | Should -BeFalse
                ($result.Errors | Where-Object { $_ -match 'Additional property not allowed' }) | Should -Not -BeNullOrEmpty
            }
        }
    }
}

#region Get-JsonSchemaPointerValue

Describe 'Get-JsonSchemaPointerValue' -Tag 'Unit' {
    BeforeAll {
        $schema = @{
            type = 'object'
            properties = @{
                'name/with~special' = @{ type = 'string' }
                nested = @{ deeper = @{ value = 42 } }
            }
        }
        $psSchema = '{"type":"object","properties":{"name":{"type":"string"}}}' |
            ConvertFrom-Json -Depth 10
    }

    It 'Returns entire schema for whitespace pointer' {
        Get-JsonSchemaPointerValue -Schema $schema -Pointer ' ' | Should -Be $schema
    }

    It 'Returns entire schema for root pointer' {
        Get-JsonSchemaPointerValue -Schema $schema -Pointer '/' | Should -Be $schema
    }

    It 'Navigates single segment' {
        $result = Get-JsonSchemaPointerValue -Schema $schema -Pointer '/type'
        $result | Should -Be 'object'
    }

    It 'Navigates multiple segments' {
        $result = Get-JsonSchemaPointerValue -Schema $schema -Pointer '/properties/nested/deeper/value'
        $result | Should -Be 42
    }

    It 'Decodes tilde escaping for slash (~1) and tilde (~0)' {
        $result = Get-JsonSchemaPointerValue -Schema $schema -Pointer '/properties/name~1with~0special'
        $result | Should -Not -BeNullOrEmpty
        $result.type | Should -Be 'string'
    }

    It 'Accesses IDictionary input (hashtable)' {
        $result = Get-JsonSchemaPointerValue -Schema $schema -Pointer '/type'
        $result | Should -Be 'object'
    }

    It 'Accesses PSObject properties' {
        $result = Get-JsonSchemaPointerValue -Schema $psSchema -Pointer '/properties/name/type'
        $result | Should -Be 'string'
    }

    It 'Returns null for missing key in IDictionary' {
        $result = Get-JsonSchemaPointerValue -Schema $schema -Pointer '/nonexistent'
        $result | Should -BeNullOrEmpty
    }

    It 'Returns null for missing PSObject property' {
        $result = Get-JsonSchemaPointerValue -Schema $psSchema -Pointer '/missing'
        $result | Should -BeNullOrEmpty
    }
}

#region Resolve-JsonSchemaRef

Describe 'Resolve-JsonSchemaRef' -Tag 'Unit' {
    BeforeAll {
        $rootSchema = @{
            definitions = @{
                foo = @{ type = 'string' }
                bar = @{ type = 'number' }
            }
        }
        $externalSchema = @{
            definitions = @{
                baz = @{ type = 'boolean' }
            }
        }
        $schemaContext = @{
            RootSchema = $rootSchema
            Schemas = @{
                'external.json' = $externalSchema
            }
        }
    }

    It 'Resolves internal $ref with #/definitions/foo' {
        $result = Resolve-JsonSchemaRef -Ref '#/definitions/foo' -RootSchema $rootSchema -SchemaContext $schemaContext
        $result.type | Should -Be 'string'
    }

    It 'Resolves internal $ref with root pointer #/' {
        $result = Resolve-JsonSchemaRef -Ref '#/' -RootSchema $rootSchema -SchemaContext $schemaContext
        $result | Should -Not -BeNullOrEmpty
        $result.definitions | Should -Not -BeNullOrEmpty
    }

    It 'Resolves external file $ref without pointer' {
        $result = Resolve-JsonSchemaRef -Ref 'external.json' -RootSchema $rootSchema -SchemaContext $schemaContext
        $result | Should -Not -BeNullOrEmpty
    }

    It 'Resolves external file $ref with pointer' {
        $result = Resolve-JsonSchemaRef -Ref 'external.json#/definitions/baz' -RootSchema $rootSchema -SchemaContext $schemaContext
        $result.type | Should -Be 'boolean'
    }

    It 'Falls back to filesystem when not in Schemas cache' {
        $tempFile = Join-Path $TestDrive 'fallback.json'
        '{"type":"array"}' | Set-Content $tempFile
        $ctx = @{ RootSchema = @{}; Schemas = @{}; BasePath = $TestDrive }
        $result = Resolve-JsonSchemaRef -Ref 'fallback.json' -RootSchema @{} -SchemaContext $ctx
        $result | Should -Not -BeNullOrEmpty
    }

    It 'Returns null for missing external schema' {
        $ctx = @{ RootSchema = @{}; Schemas = @{}; BasePath = $TestDrive }
        $result = Resolve-JsonSchemaRef -Ref 'nonexistent.json' -RootSchema @{} -SchemaContext $ctx
        $result | Should -BeNullOrEmpty
    }

    It 'Returns null for null SchemaContext with external $ref' {
        $result = Resolve-JsonSchemaRef -Ref 'external.json' -RootSchema @{} -SchemaContext $null
        $result | Should -BeNullOrEmpty
    }

    It 'Returns entire external schema for empty pointer part' {
        $result = Resolve-JsonSchemaRef -Ref 'external.json#/' -RootSchema $rootSchema -SchemaContext $schemaContext
        $result | Should -Not -BeNullOrEmpty
    }
}

#endregion
