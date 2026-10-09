#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    $script:ScriptPath = Join-Path $PSScriptRoot '../../security/Test-PublicDependencyFeeds.ps1'
    . $script:ScriptPath

    function New-PublicFeedTestRepository {
        param(
            [Parameter(Mandatory = $true)]
            [string]$Path,

            [Parameter(Mandatory = $false)]
            [hashtable]$Files = @{}
        )

        New-Item -ItemType Directory -Path $Path -Force | Out-Null
        & git -C $Path init --quiet
        if ($LASTEXITCODE -ne 0) {
            throw 'Unable to initialize the dependency feed test repository.'
        }

        foreach ($relativePath in $Files.Keys) {
            $fullPath = Join-Path $Path $relativePath
            $parent = Split-Path -Parent $fullPath
            if ($parent) {
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
            }
            Set-Content -LiteralPath $fullPath -Value $Files[$relativePath] -Encoding utf8
        }
    }
}

Describe 'Test-DependencySourceLine' -Tag 'Unit' {
    It 'Returns <Expected> for <Path>' -ForEach @(
        @{ Path = 'package.json'; Line = '"registry": "https://registry.npmjs.org/"'; Expected = $false }
        @{ Path = 'package.json'; Line = '"homepage": "https://docs.example.com/"'; Expected = $false }
        @{ Path = 'package-lock.json'; Line = '"resolved": "https://registry.npmjs.org/tool/-/tool-1.0.0.tgz"'; Expected = $true }
        @{ Path = 'npm-shrinkwrap.json'; Line = '"integrity": "sha512-value"'; Expected = $true }
        @{ Path = '.npmrc'; Line = '@scope:registry=https://registry.npmjs.org/'; Expected = $true }
        @{ Path = '.npmrc'; Line = 'save-exact=true'; Expected = $false }
        @{ Path = 'uv.lock'; Line = 'registry = "https://pypi.org/simple"'; Expected = $true }
        @{ Path = 'uv.lock'; Line = 'version = 1'; Expected = $false }
        @{ Path = 'pyproject.toml'; Line = 'index-url = "https://pypi.org/simple"'; Expected = $true }
        @{ Path = 'pyproject.toml'; Line = 'requires-python = ">=3.12"'; Expected = $false }
        @{ Path = 'requirements-dev.txt'; Line = '--extra-index-url https://pypi.org/simple'; Expected = $true }
        @{ Path = 'requirements.txt'; Line = 'pytest==9.0.0'; Expected = $false }
        @{ Path = 'unknown.yml'; Line = 'url: https://example.com'; Expected = $false }
    ) {
        Test-DependencySourceLine -Path $Path -Line $Line | Should -Be $Expected
    }
}

Describe 'Invoke-PublicDependencyFeedScan' -Tag 'Unit' {
    It 'Returns no violations for an empty repository' {
        $repoRoot = Join-Path $TestDrive 'empty'
        New-PublicFeedTestRepository -Path $repoRoot

        $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

        $result.violationCount | Should -Be 0
        $result.filesScanned | Should -Be 0
    }

    It 'Fails when dependency discovery cannot read the repository' {
        $repoRoot = Join-Path $TestDrive 'not-a-repository'
        New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null

        { Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot } |
        Should -Throw 'git ls-files failed while discovering dependency metadata.'
    }

    It 'Reports <Reason> for <FileName>' -ForEach @(
        @{
            FileName = 'package-lock.json'
            Content  = '{ "lockfileVersion": 3, "packages": { "node_modules/tool": { "version": "1.0.0", "resolved": "https://registry.npmjs.org/tool/-/tool-1.0.0.tgz", "integrity": "sha1-value" } } }'
            Reason   = 'lockfile integrity must use sha512'
        }
        @{
            FileName = 'package-lock.json'
            Content  = '{ "lockfileVersion": 3, "packages": { "node_modules/tool": { "version": "1.0.0", "resolved": "https://private-feed.example.com/tool.tgz", "integrity": "sha512-value" } } }'
            Reason   = 'not an approved public registry'
        }
        @{
            FileName = 'requirements.txt'
            Content  = 'tool @ https://private-feed.example.com/packaging/tool.whl'
            Reason   = 'not an approved public registry'
        }
        @{
            FileName = 'requirements.txt'
            Content  = 'tool @ http://pypi.org/example/tool.whl'
            Reason   = 'must use HTTPS'
        }
        @{
            FileName = 'requirements.txt'
            Content  = 'tool @ https://user:password@pypi.org/example/tool.whl'
            Reason   = 'must not contain credentials'
        }
        @{
            FileName = '.npmrc'
            Content  = 'registry=https://example.com/'
            Reason   = 'must use https://registry.npmjs.org/'
        }
        @{
            FileName = '.npmrc'
            Content  = 'registry=${NPM_REGISTRY}'
            Reason   = 'must be literal public HTTPS URLs'
        }
        @{
            FileName = 'requirements.txt'
            Content  = 'tool @ https://%'
            Reason   = 'dependency source URL is invalid'
        }
    ) {
        $repoRoot = Join-Path $TestDrive ([IO.Path]::GetRandomFileName())
        New-PublicFeedTestRepository -Path $repoRoot -Files @{ $FileName = $Content }

        $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

        $result.violationCount | Should -Be 1
        $result.violations[0].reason | Should -Match ([regex]::Escape($Reason))
    }

    It 'Accepts approved public hosts' {
        $repoRoot = Join-Path $TestDrive 'approved-hosts'
        New-PublicFeedTestRepository -Path $repoRoot -Files @{
            'requirements.txt' = @(
                'npm @ https://registry.npmjs.org/npm/-/npm-1.0.0.tgz'
                'project @ https://pypi.org/project/project/'
                'wheel @ https://files.pythonhosted.org/packages/project.whl'
                'crate @ https://crates.io/api/v1/crates/example/1.0.0/download'
            )
        }

        $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

        $result.violationCount | Should -Be 0
        $result.sourcesValidated | Should -Be 4
    }

    Context 'when lockfiles and settings weaken integrity or transport' {
        BeforeAll {
            $script:Sha512 = 'sha512-' + ('A' * 86) + '=='
            $script:Sha256Hex = 'a' * 64
            $script:NpmTarball = 'https://registry.npmjs.org/tool/-/tool-1.0.0.tgz'

            function New-NpmLockContent {
                param([hashtable]$Entry, [int]$LockfileVersion = 3)

                @{
                    name            = 'fixture'
                    lockfileVersion = $LockfileVersion
                    packages        = @{
                        ''                  = @{ name = 'fixture' }
                        'node_modules/tool' = $Entry
                    }
                } | ConvertTo-Json -Depth 5
            }
        }

        It 'Reports <Rule> for <Name>' -ForEach @(
            @{ Name = 'unparseable npm lock'; FileName = 'package-lock.json'; Rule = 'npm-lock-parse'; Content = '{ "lockfileVersion": 3, "packages": {' }
            @{ Name = 'npm lockfile version 1'; FileName = 'package-lock.json'; Rule = 'npm-lock-version'; Content = '{ "lockfileVersion": 1, "dependencies": {} }' }
            @{ Name = 'npm entry without integrity'; FileName = 'package-lock.json'; Rule = 'npm-integrity-missing'; Entry = @{ version = '1.0.0'; resolved = 'NPM_TARBALL' } }
            @{ Name = 'npm entry without resolved'; FileName = 'package-lock.json'; Rule = 'npm-resolved-missing'; Entry = @{ version = '1.0.0'; integrity = 'SHA512' } }
            @{ Name = 'npm mixed weak integrity'; FileName = 'npm-shrinkwrap.json'; Rule = 'npm-integrity-weak'; Entry = @{ version = '1.0.0'; resolved = 'NPM_TARBALL'; integrity = 'SHA512 sha1-AAAA' } }
            @{ Name = 'npm resolved on a Python host'; FileName = 'package-lock.json'; Rule = 'host-not-approved'; Entry = @{ version = '1.0.0'; resolved = 'https://files.pythonhosted.org/tool-1.0.0.tgz'; integrity = 'SHA512' } }
            @{ Name = 'npm resolved with query'; FileName = 'package-lock.json'; Rule = 'url-query'; Entry = @{ version = '1.0.0'; resolved = 'https://registry.npmjs.org/tool/-/tool-1.0.0.tgz?sig=abc'; integrity = 'SHA512' } }
            @{ Name = 'npm resolved with port'; FileName = 'package-lock.json'; Rule = 'url-port'; Entry = @{ version = '1.0.0'; resolved = 'https://registry.npmjs.org:8443/tool/-/tool-1.0.0.tgz'; integrity = 'SHA512' } }
            @{ Name = 'uv wheel without hash'; FileName = 'uv.lock'; Rule = 'uv-hash-invalid'; Content = 'wheels = [{ url = "https://files.pythonhosted.org/packages/tool-1.0-py3-none-any.whl", size = 10 }]' }
            @{ Name = 'uv wheel with sha1 hash'; FileName = 'uv.lock'; Rule = 'uv-hash-invalid'; Content = 'sdist = { url = "https://files.pythonhosted.org/packages/tool-1.0.tar.gz", hash = "sha1:aaaa" }' }
            @{ Name = 'uv sha256 with wrong length'; FileName = 'uv.lock'; Rule = 'uv-hash-invalid'; Content = 'sdist = { url = "https://files.pythonhosted.org/packages/tool-1.0.tar.gz", hash = "sha256:abcd" }' }
            @{ Name = 'uv index metadata on a private host'; FileName = 'uv.lock'; Rule = 'host-not-approved'; Content = '{ name = "torch", specifier = "==2.0", index = "https://private-feed.example.com/simple" },' }
            @{ Name = 'pyproject index on a non-Python host'; FileName = 'pyproject.toml'; Rule = 'host-not-approved'; Content = "[[tool.uv.index]]`nname = `"gh`"`nurl = `"https://github.com/example/simple`"" }
            @{ Name = 'pyproject index with query'; FileName = 'pyproject.toml'; Rule = 'url-query'; Content = "[[tool.uv.index]]`nname = `"pypi`"`nurl = `"https://pypi.org/simple?token=abc`"" }
            @{ Name = 'npmrc strict-ssl disabled'; FileName = '.npmrc'; Rule = 'insecure-setting'; Content = 'strict-ssl=false' }
            @{ Name = 'npmrc lockfile disabled'; FileName = '.npmrc'; Rule = 'insecure-setting'; Content = 'package-lock=false' }
            @{ Name = 'npmrc resolved omitted'; FileName = '.npmrc'; Rule = 'insecure-setting'; Content = 'omit-lockfile-registry-resolved=true' }
            @{ Name = 'npmrc auth token'; FileName = '.npmrc'; Rule = 'credential-setting'; Content = '//registry.npmjs.org/:_authToken=abc' }
            @{ Name = 'pyproject insecure host'; FileName = 'pyproject.toml'; Rule = 'insecure-setting'; Content = "[tool.uv]`nallow-insecure-host = [`"pypi.org`"]" }
            @{ Name = 'requirements trusted host'; FileName = 'requirements.txt'; Rule = 'insecure-setting'; Content = '--trusted-host pypi.org' }
            @{ Name = 'pyproject multi-line extra-index-url'; FileName = 'pyproject.toml'; Rule = 'host-not-approved'; Content = "[tool.uv]`nextra-index-url = [`n    `"https://pypi.org/simple`",`n    `"https://private-feed.example.com/simple`",`n]" }
            @{ Name = 'pyproject find-links'; FileName = 'pyproject.toml'; Rule = 'host-not-approved'; Content = "[tool.uv]`nfind-links = [`"https://private-feed.example.com/wheels/`"]" }
            @{ Name = 'pyproject dependency direct URL'; FileName = 'pyproject.toml'; Rule = 'host-not-approved'; Content = "[project]`ndependencies = [`"tool @ https://private-feed.example.com/tool-1.0-py3-none-any.whl`"]" }
            @{ Name = 'pyproject multi-line dependency group direct URL'; FileName = 'pyproject.toml'; Rule = 'url-scheme'; Content = "[dependency-groups]`ndev = [`n    `"pytest==9.0.0`",`n    `"tool @ git+ssh://git@private.example.com/tool.git`",`n]" }
        ) {
            $repoRoot = Join-Path $TestDrive ([IO.Path]::GetRandomFileName())
            $fileContent = if ($Entry) {
                $resolvedEntry = @{}
                foreach ($key in $Entry.Keys) {
                    $resolvedEntry[$key] = $Entry[$key] -replace 'NPM_TARBALL', $script:NpmTarball -replace 'SHA512', $script:Sha512
                }
                New-NpmLockContent -Entry $resolvedEntry
            }
            else {
                $Content
            }
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ $FileName = $fileContent }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violations.rule | Should -Contain $Rule
        }

        It 'Accepts approved multi-line pyproject sources, local find-links, and comments' {
            $repoRoot = Join-Path $TestDrive 'pyproject-multiline'
            $pyproject = @(
                '[tool.uv]'
                'extra-index-url = ['
                '    # mirror docs: https://private-feed.example.com/help'
                '    "https://download.pytorch.org/whl/cu130",'
                ']'
                'find-links = ["./wheels"]'
                '[project]'
                'dependencies = ['
                '    "torch @ https://download-r2.pytorch.org/whl/cu130/torch-2.0-cp312-none-any.whl",'
                '    "requests==2.34.2",'
                ']'
                '[project.urls]'
                'Homepage = "https://docs.example.com/"'
            )
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ 'pyproject.toml' = $pyproject }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violations | Should -BeNullOrEmpty
            $result.sourcesValidated | Should -Be 2
        }

        It 'Accepts canonical npm, uv, and settings metadata' {
            $repoRoot = Join-Path $TestDrive 'canonical-metadata'
            $lock = @{
                name            = 'fixture'
                lockfileVersion = 3
                packages        = @{
                    ''                                   = @{ name = 'fixture'; workspaces = @('frontend') }
                    'frontend'                           = @{ name = 'frontend'; version = '0.1.0' }
                    'node_modules/frontend'              = @{ resolved = 'frontend'; link = $true }
                    'node_modules/tool'                  = @{ version = '1.0.0'; resolved = $script:NpmTarball; integrity = $script:Sha512 }
                    'node_modules/tool/node_modules/dep' = @{ version = '1.0.0'; inBundle = $true }
                }
            } | ConvertTo-Json -Depth 5
            $uvLock = @(
                '[[package]]'
                'name = "tool"'
                'source = { registry = "https://pypi.org/simple" }'
                "sdist = { url = `"https://files.pythonhosted.org/packages/tool-1.0.tar.gz`", hash = `"sha256:$($script:Sha256Hex)`", size = 1 }"
                'wheels = ['
                "    { url = `"https://download-r2.pytorch.org/whl/cu130/torch-2.0-cp312-none-any.whl`", hash = `"sha256:$($script:Sha256Hex)`" },"
                ']'
                '[package.metadata]'
                'requires-dist = [{ name = "torch", specifier = "==2.0", index = "https://download.pytorch.org/whl/cu130" }]'
                '[[package]]'
                'name = "local"'
                'source = { editable = "../local" }'
            )
            $pyproject = @(
                '[[tool.uv.index]]'
                'name = "pypi"'
                'url = "https://pypi.org/simple"'
                'default = true'
                '[[tool.uv.index]]'
                'name = "pytorch-cu130"'
                'url = "https://download.pytorch.org/whl/cu130"'
                'explicit = true'
            )
            New-PublicFeedTestRepository -Path $repoRoot -Files @{
                'package-lock.json' = $lock
                'uv.lock'           = $uvLock
                'pyproject.toml'    = $pyproject
                '.npmrc'            = @('registry=https://registry.npmjs.org/', 'strict-ssl=true')
            }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violations | Should -BeNullOrEmpty
        }

        It 'Reports a git+ssh lockfile entry once' {
            $repoRoot = Join-Path $TestDrive 'lock-git-ssh'
            $lock = @{
                lockfileVersion = 3
                packages        = @{
                    'node_modules/tool' = @{ version = '1.0.0'; resolved = 'git+ssh://git@github.com/example/tool.git#abc'; integrity = $script:Sha512 }
                }
            } | ConvertTo-Json -Depth 5
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ 'package-lock.json' = $lock }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violationCount | Should -Be 1
            $result.violations[0].rule | Should -Be 'url-scheme'
        }
    }

    Context 'when package.json declares dependencies and metadata' {
        It 'Accepts metadata URLs on any host and registry dependency specs' {
            $repoRoot = Join-Path $TestDrive 'manifest-metadata'
            $manifest = @{
                name            = 'fixture'
                homepage        = 'https://docs.example.com/fixture'
                bugs            = @{ url = 'https://issues.example.com/fixture' }
                repository      = @{ type = 'git'; url = 'git+https://git.example.com/org/fixture.git' }
                funding         = @(@{ type = 'custom'; url = 'https://donate.example.com/' })
                dependencies    = @{ tool = '1.2.3'; alias = 'npm:@scope/tool@1.2.3'; local = 'file:../local' }
                devDependencies = @{ member = 'workspace:*' }
                publishConfig   = @{ registry = 'https://registry.npmjs.org/' }
            } | ConvertTo-Json -Depth 5
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ 'package.json' = $manifest }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violations | Should -BeNullOrEmpty
            $result.sourcesValidated | Should -Be 4
        }

        It 'Reports <Rule> for <Name>' -ForEach @(
            @{ Name = 'github shorthand dependency'; Rule = 'npm-spec-not-registry'; Manifest = @{ dependencies = @{ tool = 'github:example/tool' } } }
            @{ Name = 'bare owner/repo dependency'; Rule = 'npm-spec-not-registry'; Manifest = @{ dependencies = @{ tool = 'example/tool#main' } } }
            @{ Name = 'scp-style git dependency'; Rule = 'npm-spec-not-registry'; Manifest = @{ dependencies = @{ tool = 'git@git.example.com:org/tool.git' } } }
            @{ Name = 'git+ssh dependency'; Rule = 'url-scheme'; Manifest = @{ devDependencies = @{ tool = 'git+ssh://git@github.com/example/tool.git' } } }
            @{ Name = 'private-host tarball dependency'; Rule = 'host-not-approved'; Manifest = @{ dependencies = @{ tool = 'https://private-feed.example.com/tool-1.0.0.tgz' } } }
            @{ Name = 'git+https dependency on github'; Rule = 'host-not-approved'; Manifest = @{ optionalDependencies = @{ tool = 'git+https://github.com/example/tool.git' } } }
            @{ Name = 'nested override to git+ssh'; Rule = 'url-scheme'; Manifest = @{ overrides = @{ parent = @{ tool = 'git+ssh://git@github.com/example/tool.git' } } } }
            @{ Name = 'private publish registry'; Rule = 'npm-registry-not-canonical'; Manifest = @{ publishConfig = @{ registry = 'https://private-feed.example.com/npm/' } } }
            @{ Name = 'nonliteral publish registry'; Rule = 'npm-registry-nonliteral'; Manifest = @{ publishConfig = @{ registry = '${NPM_REGISTRY}' } } }
            @{ Name = 'credentials in metadata URL'; Rule = 'url-credentials'; Manifest = @{ repository = @{ url = 'https://user:token@github.com/example/tool.git' } } }
        ) {
            $repoRoot = Join-Path $TestDrive ([IO.Path]::GetRandomFileName())
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ 'package.json' = ($Manifest | ConvertTo-Json -Depth 5) }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violationCount | Should -Be 1
            $result.violations[0].rule | Should -Be $Rule
            $result.violations[0].line | Should -BeGreaterThan 0
        }

        It 'Reports an unparseable package.json' {
            $repoRoot = Join-Path $TestDrive 'manifest-invalid'
            New-PublicFeedTestRepository -Path $repoRoot -Files @{ 'package.json' = '{ "dependencies": {' }

            $result = Invoke-PublicDependencyFeedScan -RepoRoot $repoRoot

            $result.violations.rule | Should -Be 'npm-manifest-parse'
        }
    }
}

Describe 'Test-PublicDependencyFeeds main execution' -Tag 'Unit' {
    It 'Writes results and exits zero for a clean repository' {
        $repoRoot = Join-Path $TestDrive 'main-clean'
        $outputPath = Join-Path $TestDrive 'clean-results.json'
        New-PublicFeedTestRepository -Path $repoRoot -Files @{
            '.npmrc' = 'registry=https://registry.npmjs.org/'
        }

        & (Get-Process -Id $PID).Path -NoProfile -File $script:ScriptPath -RepoRoot $repoRoot -OutputPath $outputPath -FailOnViolation *> $null

        $LASTEXITCODE | Should -Be 0
        Test-Path -LiteralPath $outputPath | Should -BeTrue
        (Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json).violationCount | Should -Be 0
    }

    It 'Writes results and exits one for a prohibited source' {
        $repoRoot = Join-Path $TestDrive 'main-violation'
        $outputPath = Join-Path $TestDrive 'violation-results.json'
        New-PublicFeedTestRepository -Path $repoRoot -Files @{
            '.npmrc' = 'registry=https://private-feed.example.com/packaging/'
        }

        & (Get-Process -Id $PID).Path -NoProfile -File $script:ScriptPath -RepoRoot $repoRoot -OutputPath $outputPath -FailOnViolation *> $null

        $LASTEXITCODE | Should -Be 1
        Test-Path -LiteralPath $outputPath | Should -BeTrue
        (Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json).violationCount | Should -Be 1
    }

    It 'Reports violations without failing when enforcement is not requested' {
        $repoRoot = Join-Path $TestDrive 'main-advisory'
        $outputPath = Join-Path $TestDrive 'advisory-results.json'
        New-PublicFeedTestRepository -Path $repoRoot -Files @{
            '.npmrc' = 'registry=https://private-feed.example.com/packaging/'
        }

        & (Get-Process -Id $PID).Path -NoProfile -File $script:ScriptPath -RepoRoot $repoRoot -OutputPath $outputPath *> $null

        $LASTEXITCODE | Should -Be 0
        Test-Path -LiteralPath $outputPath | Should -BeTrue
        (Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json).violationCount | Should -Be 1
    }

    It 'Never writes credential values to output or results' {
        $repoRoot = Join-Path $TestDrive 'main-redaction'
        $outputPath = Join-Path $TestDrive 'redaction-results.json'
        $sentinel = 'S3NT1NEL-' + [guid]::NewGuid().ToString('N')
        New-PublicFeedTestRepository -Path $repoRoot -Files @{
            '.npmrc'           = "registry=https://user:$sentinel@private-feed.example.com/npm/"
            'requirements.txt' = "tool @ https://private-feed.example.com/tool.whl?token=$sentinel"
        }

        $output = & (Get-Process -Id $PID).Path -NoProfile -File $script:ScriptPath -RepoRoot $repoRoot -OutputPath $outputPath -FailOnViolation *>&1 | Out-String

        $LASTEXITCODE | Should -Be 1
        $output | Should -Not -Match $sentinel
        Get-Content -LiteralPath $outputPath -Raw | Should -Not -Match $sentinel
        $violations = (Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json).violations
        $violations.Count | Should -BeGreaterThan 0
        $violations[0].PSObject.Properties.Name | Should -Not -Contain 'source'
    }

    It 'Exits two when the scan cannot run' {
        $repoRoot = Join-Path $TestDrive 'main-error'
        New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null

        & (Get-Process -Id $PID).Path -NoProfile -File $script:ScriptPath -RepoRoot $repoRoot -OutputPath (Join-Path $TestDrive 'error-results.json') -FailOnViolation *> $null

        $LASTEXITCODE | Should -Be 2
    }
}
