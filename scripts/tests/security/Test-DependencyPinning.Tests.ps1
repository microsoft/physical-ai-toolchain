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

        It 'Returns false for validation-function types without pin patterns' {
            { Test-SHAPinning -Version 'ignored' -Type 'shell-downloads' } | Should -Not -Throw
            Test-SHAPinning -Version 'ignored' -Type 'shell-downloads' | Should -BeFalse
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

    Context 'Checksum helpers' {
        It 'Treats the portable verify_sha256 helper as checksum verification' {
            $content = "curl -fsSL https://example.com/uv.tar.gz -o /tmp/uv.tar.gz`nverify_sha256 `"deadbeef`" /tmp/uv.tar.gz"
            $tmp = Join-Path $TestDrive 'verify-helper.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'verify-helper.sh' })
            $result | Should -BeNullOrEmpty
        }

        It 'Flags the same download when the verify_sha256 line is absent (negative control)' {
            $content = 'curl -fsSL https://example.com/uv.tar.gz -o /tmp/uv.tar.gz'
            $tmp = Join-Path $TestDrive 'verify-helper-missing.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'verify-helper-missing.sh' })
            $result.Count | Should -Be 1
        }
    }

    Context 'pinning-ignore directive' {
        It 'Exempts a same-line marked download but still flags the next one' {
            $content = "curl -fsSL https://example.com/a.tgz -o /tmp/a  # pinning-ignore`ncurl -fsSL https://example.com/b.tgz -o /tmp/b"
            $tmp = Join-Path $TestDrive 'dl-ignore-sameline.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-ignore-sameline.sh' })
            $result.Count | Should -Be 1
            $result[0].Line | Should -Be 2
        }

        It 'Exempts a download preceded by a dedicated pinning-ignore comment line' {
            $content = "# pinning-ignore: trusted GPG-signed apt repo`ncurl -fsSL https://example.com/repo.list -o /tmp/repo.list"
            $tmp = Join-Path $TestDrive 'dl-ignore-prevline.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-ignore-prevline.sh' })
            $result | Should -BeNullOrEmpty
        }

        It 'Still flags a download whose URL contains the marker outside a comment' {
            $content = 'curl -fsSL https://example.com/pinning-ignore-tool.tgz -o /tmp/x'
            $tmp = Join-Path $TestDrive 'dl-marker-in-url.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-marker-in-url.sh' })
            $result.Count | Should -Be 1
        }

        It 'Still flags a download when the marker follows a # embedded in a URL (not a comment)' {
            $content = 'curl -fsSL "https://example.com/a#b" -o /tmp/pinning-ignore-out.tgz'
            $tmp = Join-Path $TestDrive 'dl-hash-in-url.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-hash-in-url.sh' })
            $result.Count | Should -Be 1
        }

        It 'Flags a download when the marker is not on the immediately preceding line' {
            $content = "# pinning-ignore: x`n# intervening comment`ncurl -fsSL https://example.com/a.tgz -o /tmp/a"
            $tmp = Join-Path $TestDrive 'dl-ignore-gap.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-ignore-gap.sh' })
            $result.Count | Should -Be 1
            $result[0].Line | Should -Be 3
        }
    }

    Context 'Comment lines' {
        It 'Does not flag a curl/wget inside a comment line' {
            $content = '# example: curl -fsSL https://example.com/install.sh | bash'
            $tmp = Join-Path $TestDrive 'dl-comment.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-comment.sh' })
            $result | Should -BeNullOrEmpty
        }

        It 'Returns no violations for a file of only comments and blank lines' {
            $content = "#!/usr/bin/env bash`n`n# no downloads here`n"
            $tmp = Join-Path $TestDrive 'dl-comments-only.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'dl-comments-only.sh' })
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-DependencyViolation' -Tag 'Unit' {
    Context 'NPM manifests' {
        It 'Handles missing optional dependency sections under strict mode' {
            $testFile = Join-Path $TestDrive 'package.json'
            @'
{
  "dependencies": {
    "left-pad": "1.3.0"
  }
}
'@ | Set-Content -Path $testFile

            $fileInfo = @{
                Path         = $testFile
                Type         = 'npm'
                RelativePath = 'package.json'
            }

            { Get-DependencyViolation -FileInfo $fileInfo } | Should -Not -Throw
            Get-DependencyViolation -FileInfo $fileInfo | Should -BeNullOrEmpty
        }
    }

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

Describe 'Get-ShellInlinePipViolations' -Tag 'Unit' {
    Context 'Compliant inline installs' {
        It 'Returns no violations when every install is pinned or lock-derived' {
            $testFile = Join-Path $script:FixturesPath 'inline-pip-compliant-workflow.yaml'
            $fileInfo = @{
                Path         = $testFile
                Type         = 'shell-inline-pip'
                RelativePath = 'inline-pip-compliant-workflow.yaml'
            }
            $result = @(Get-ShellInlinePipViolations -FileInfo $fileInfo)
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned inline installs' {
        BeforeAll {
            $testFile = Join-Path $script:FixturesPath 'inline-pip-unpinned-workflow.yaml'
            $fileInfo = @{
                Path         = $testFile
                Type         = 'shell-inline-pip'
                RelativePath = 'inline-pip-unpinned-workflow.yaml'
            }
            $script:InlineResult = @(Get-ShellInlinePipViolations -FileInfo $fileInfo)
        }

        It 'Flags every bare or range-specified package' {
            $script:InlineResult.Count | Should -Be 5
        }

        It 'Flags the expected package names' {
            $names = $script:InlineResult.Name | Sort-Object
            $names | Should -Be @('gpustat', 'matplotlib', 'mlflow', 'packaging', 'requests')
        }

        It 'Does not flag the exact-pinned package on a mixed line' {
            $script:InlineResult.Name | Should -Not -Contain 'wandb'
        }

        It 'Reports violations as shell-inline-pip type with warning severity' {
            $script:InlineResult[0].Type | Should -Be 'shell-inline-pip'
            $script:InlineResult[0].Severity | Should -Be 'warning'
        }
    }

    Context 'Compliance-preserving patterns' {
        It 'Treats shell-variable pins (name=="$VAR") as compliant' {
            $content = 'pip install torch=="${TORCH_VER}"'
            $tmp = Join-Path $TestDrive 'var-pin.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'var-pin.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats uv export pipes as compliant' {
            $content = 'uv export --frozen | uv pip install --no-deps -r -'
            $tmp = Join-Path $TestDrive 'pipe.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'pipe.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats uv pip option values in lock-derived installs as compliant' {
            $content = 'uv export --frozen | uv pip install --torch-backend cpu --no-deps --requirement -'
            $tmp = Join-Path $TestDrive 'uv-pip-option-value.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'uv-pip-option-value.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats requirement-only installs as compliant' {
            $content = 'pip install -r requirements.txt'
            $tmp = Join-Path $TestDrive 'requirement-only.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'requirement-only.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Allows extra exact-pinned packages with requirement installs' {
            $content = 'pip install --requirement requirements.txt requests==2.32.0'
            $tmp = Join-Path $TestDrive 'requirement-pinned-extra.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'requirement-pinned-extra.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats editable installs (-e .) as compliant' {
            $content = 'pip install -e .[base]'
            $tmp = Join-Path $TestDrive 'editable.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'editable.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Still validates extra package args after a requirement file' {
            $content = 'pip install -r requirements.txt requests'
            $tmp = Join-Path $TestDrive 'mixed-requirement.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'mixed-requirement.yaml' })
            $result.Count | Should -Be 1
            $result.Name | Should -Be 'requests'
        }

        It 'Still validates extra uv pip args after a long requirement flag' {
            $content = 'uv pip install --requirement req.txt matplotlib'
            $tmp = Join-Path $TestDrive 'mixed-uv-requirement.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'mixed-uv-requirement.yaml' })
            $result.Count | Should -Be 1
            $result.Name | Should -Be 'matplotlib'
        }

        It 'Treats pinned uv run --with specs as compliant' {
            $content = 'uv run --with azure-identity==1.25.3 python /tmp/job.py'
            $tmp = Join-Path $TestDrive 'uvrun-pinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'uvrun-pinned.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats uv run --with-requirements as compliant' {
            $content = 'uv run --with-requirements /tmp/reqs.txt python /tmp/job.py'
            $tmp = Join-Path $TestDrive 'uvrun-reqs.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'uvrun-reqs.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Allows unpinned build-frontend tools (pip/setuptools/wheel)' {
            $content = 'pip install --upgrade pip setuptools wheel'
            $tmp = Join-Path $TestDrive 'tools.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'tools.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats a wheel URL install as compliant' {
            $content = "uv pip install requests==2.31.0`nuv pip install https://example.com/pkg-1.0-py3-none-any.whl"
            $tmp = Join-Path $TestDrive 'url-spec.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'url-spec.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Treats a local path install as compliant' {
            $content = "pip install numpy==1.26.4`npip install ./local-project"
            $tmp = Join-Path $TestDrive 'path-spec.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'path-spec.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Ignores an empty-string package argument' {
            $content = "pip install requests==2.31.0`npip install ''"
            $tmp = Join-Path $TestDrive 'empty-spec.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'empty-spec.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Ignores a token that is not a package specifier' {
            $content = "pip install requests==2.31.0`npip install _internal_tool"
            $tmp = Join-Path $TestDrive 'nonpkg-spec.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'nonpkg-spec.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Ignores a flag supplied as a uv run --with value' {
            $content = "uv run --with requests==2.8.0 python a.py`nuvx --with -U sometool"
            $tmp = Join-Path $TestDrive 'with-flag.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'with-flag.yaml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Mixed requirement and inline installs' {
        It 'Flags bare packages mixed with requirement files' {
            $content = 'pip install -r requirements.txt requests'
            $tmp = Join-Path $TestDrive 'mixed-req.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'mixed-req.yaml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'requests'
        }

        It 'Flags bare packages mixed with uv export pipes' {
            $content = 'uv export --frozen | uv pip install --no-deps -r - requests'
            $tmp = Join-Path $TestDrive 'mixed-uv-export.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'mixed-uv-export.yaml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'requests'
        }
    }

    Context 'Unpinned uv run --with' {
        It 'Flags unpinned --with specs' {
            $content = "uv run --with requests python s.py`nuvx --with 'mlflow>=2.8,<3' tool"
            $tmp = Join-Path $TestDrive 'uvrun-unpinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'uvrun-unpinned.yaml' })
            $result.Count | Should -Be 2
            ($result.Name | Sort-Object) | Should -Be @('mlflow', 'requests')
        }

        It 'Flags unpinned --with=SPEC (equals form)' {
            $content = "uv run --with=requests python s.py`nuvx --with=mlflow tool"
            $tmp = Join-Path $TestDrive 'uvrun-equals.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'uvrun-equals.yaml' })
            $result.Count | Should -Be 2
            ($result.Name | Sort-Object) | Should -Be @('mlflow', 'requests')
        }

        It 'Flags extra unpinned packages with requirement installs' {
            $content = 'pip install -r requirements.txt requests'
            $tmp = Join-Path $TestDrive 'requirement-unpinned-extra.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'requirement-unpinned-extra.yaml' })
            $result.Count | Should -Be 1
            $result.Name | Should -Be 'requests'
        }
    }

    Context 'Line continuations' {
        It 'Flushes a dangling backslash continuation on the final line' {
            $content = "echo done`npip install foo \"
            $tmp = Join-Path $TestDrive 'dangling-continuation.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'dangling-continuation.yaml' })
            $result.Name | Should -Be 'foo'
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $fileInfo = @{
                Path         = 'TestDrive:/nonexistent/workflow.yaml'
                Type         = 'shell-inline-pip'
                RelativePath = 'nonexistent/workflow.yaml'
            }
            $result = Get-ShellInlinePipViolations -FileInfo $fileInfo
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Single-line files' {
        It 'Flags an unpinned install in a single-line file' {
            $content = 'pip install requests'
            $tmp = Join-Path $TestDrive 'single-line-install.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'single-line-install.yaml' })
            $result.Name | Should -Be 'requests'
        }
    }
}

Describe 'Get-ShellInlinePipViolations (.sh files)' -Tag 'Unit' {
    Context 'Compliant shell script' {
        It 'Returns no violations when every install is pinned, lock-derived, or exempted' {
            $testFile = Join-Path $script:SecurityFixturesPath 'inline-pip-compliant.sh'
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $testFile; Type = 'shell-inline-pip'; RelativePath = 'inline-pip-compliant.sh' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned shell script' {
        It 'Flags bare names, ranges, and unpinned uv run --with' {
            $testFile = Join-Path $script:SecurityFixturesPath 'inline-pip-unpinned.sh'
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $testFile; Type = 'shell-inline-pip'; RelativePath = 'inline-pip-unpinned.sh' })
            $result.Count | Should -Be 3
            ($result.Name | Sort-Object) | Should -Be @('mlflow', 'requests', 'torch')
        }
    }

    Context 'Binary-install false-positive defense' {
        It 'Does not flag `install ... /usr/local/bin/uvx` (coreutils install, not a package)' {
            $content = 'sudo install -m 0755 /tmp/uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/uvx'
            $tmp = Join-Path $TestDrive 'bininstall.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'bininstall.sh' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'pinning-ignore directive' {
        It 'Exempts a same-line marked install but still flags the next line' {
            $content = "pip install `"numpy>=1.26,<2`"  # pinning-ignore`npip install requests"
            $tmp = Join-Path $TestDrive 'ignore-sameline.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'ignore-sameline.sh' })
            $result.Name | Should -Be 'requests'
        }

        It 'Exempts an install preceded by a dedicated pinning-ignore comment line' {
            $content = "# pinning-ignore: deliberate`npip install `"flask>=2,<3`""
            $tmp = Join-Path $TestDrive 'ignore-prevline.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'ignore-prevline.sh' })
            $result | Should -BeNullOrEmpty
        }

        It 'Still flags an install whose argument contains the marker outside a comment' {
            $content = 'pip install pinning-ignore-pkg'
            $tmp = Join-Path $TestDrive 'inline-marker-in-arg.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'inline-marker-in-arg.sh' })
            $result.Count | Should -Be 1
        }

        It 'Still flags an install when the marker follows a # embedded in the spec (not a comment)' {
            $content = 'pip install requests#pinning-ignore'
            $tmp = Join-Path $TestDrive 'inline-hash-in-spec.sh'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-ShellInlinePipViolations -FileInfo @{ Path = $tmp; Type = 'shell-inline-pip'; RelativePath = 'inline-hash-in-spec.sh' })
            $result.Count | Should -Be 1
        }
    }
}

Describe 'Get-FilesToScan shell-inline-pip discovery' -Tag 'Unit' {
    BeforeAll {
        $script:ScanRoot = Join-Path $TestDrive 'scan-root'
        foreach ($d in @('.github/workflows', 'workflows/nested', 'scripts', 'external/x/workflows')) {
            New-Item -ItemType Directory -Path (Join-Path $script:ScanRoot $d) -Force | Out-Null
        }
        $body = "steps:`n  - run: uv pip install requests"
        Set-Content -Path (Join-Path $script:ScanRoot '.github/workflows/ci.yml') -Value $body
        Set-Content -Path (Join-Path $script:ScanRoot 'workflows/nested/deep.yaml') -Value $body
        Set-Content -Path (Join-Path $script:ScanRoot 'scripts/shared.sh') -Value 'uv pip install requests'
        Set-Content -Path (Join-Path $script:ScanRoot 'external/x/workflows/vendor.yaml') -Value $body
    }

    It 'Discovers .yml under the hidden .github directory and .yaml under interior workflows' {
        $rels = @(Get-FilesToScan -ScanPath $script:ScanRoot -Types @('shell-inline-pip') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain '.github/workflows/ci.yml'
        $rels | Should -Contain 'workflows/nested/deep.yaml'
    }

    It 'Prunes vendored trees (external/)' {
        $rels = @(Get-FilesToScan -ScanPath $script:ScanRoot -Types @('shell-inline-pip') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Not -Contain 'external/x/workflows/vendor.yaml'
    }

    It 'Honors ExcludePatterns in the interior-glob handler' {
        $rels = @(Get-FilesToScan -ScanPath $script:ScanRoot -Types @('shell-inline-pip') -ExcludePatterns @('nested') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Not -Contain 'workflows/nested/deep.yaml'
        $rels | Should -Contain '.github/workflows/ci.yml'
    }

    It 'Keeps each scan type for files matched by multiple validators' {
        $results = @(Get-FilesToScan -ScanPath $script:ScanRoot -Types @('shell-downloads', 'shell-inline-pip') -Recursive)
        $types = @(
            $results |
                Where-Object { ($_.RelativePath -replace '\\', '/') -eq 'scripts/shared.sh' } |
                ForEach-Object Type |
                Sort-Object
        )

        $types | Should -Be @('shell-downloads', 'shell-inline-pip')
    }
}

Describe 'Get-FilesToScan shell-downloads discovery' -Tag 'Unit' {
    BeforeAll {
        $script:DlScanRoot = Join-Path $TestDrive 'dl-scan-root'
        foreach ($d in @('scripts', 'training/component/scripts', 'infrastructure/setup/optional', 'external/vendor')) {
            New-Item -ItemType Directory -Path (Join-Path $script:DlScanRoot $d) -Force | Out-Null
        }
        $body = 'curl -fsSL https://example.com/tool.tgz -o /tmp/tool.tgz'
        Set-Content -Path (Join-Path $script:DlScanRoot 'scripts/root.sh') -Value $body
        Set-Content -Path (Join-Path $script:DlScanRoot 'training/component/scripts/nested.sh') -Value $body
        Set-Content -Path (Join-Path $script:DlScanRoot 'infrastructure/setup/optional/install.sh') -Value $body
        Set-Content -Path (Join-Path $script:DlScanRoot 'external/vendor/vendor.sh') -Value $body
    }

    It 'Discovers .sh outside the root scripts/ directory (nested and non-scripts paths)' {
        $rels = @(Get-FilesToScan -ScanPath $script:DlScanRoot -Types @('shell-downloads') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain 'scripts/root.sh'
        $rels | Should -Contain 'training/component/scripts/nested.sh'
        $rels | Should -Contain 'infrastructure/setup/optional/install.sh'
    }

    It 'Prunes vendored trees (external/)' {
        $rels = @(Get-FilesToScan -ScanPath $script:DlScanRoot -Types @('shell-downloads') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Not -Contain 'external/vendor/vendor.sh'
    }
}

Describe 'Get-DockerImageViolations' -Tag 'Unit' {
    Context 'Compliant image references' {
        It 'Returns no violations for digest-pinned, templated, variable, or exempted images' {
            $testFile = Join-Path $script:FixturesPath 'docker-image-compliant-workflow.yaml'
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $testFile; Type = 'docker'; RelativePath = 'docker-image-compliant-workflow.yaml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned image references' {
        BeforeAll {
            $testFile = Join-Path $script:FixturesPath 'docker-image-unpinned-workflow.yaml'
            $script:DockerResult = @(Get-DockerImageViolations -FileInfo @{ Path = $testFile; Type = 'docker'; RelativePath = 'docker-image-unpinned-workflow.yaml' })
        }

        It 'Flags every tag-only OCI image' {
            $script:DockerResult.Count | Should -Be 3
        }

        It 'Flags the expected image repositories' {
            ($script:DockerResult.Name | Sort-Object) | Should -Be @('nvcr.io/nvidia/isaac-lab', 'python', 'pytorch/pytorch')
        }

        It 'Records the tag as the unpinned version' {
            ($script:DockerResult | Where-Object { $_.Name -eq 'python' }).Version | Should -Be '3.11-slim'
        }

        It 'Reports violations as docker type with warning severity' {
            $script:DockerResult[0].Type | Should -Be 'docker'
            $script:DockerResult[0].Severity | Should -Be 'warning'
        }
    }

    Context 'Reference-shape handling' {
        It 'Treats a @sha256 digest pin as compliant' {
            $content = "workflow:`n  image: nvcr.io/nvidia/isaac-lab:2.3.2@sha256:388dbc806f48359a964cb9f807feb226da95d0a107f470fdcad9780ea10fe6f2"
            $tmp = Join-Path $TestDrive 'pinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'pinned.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Skips submission-time templated images ({{ image }})' {
            $content = '  image: "{{ image }}"'
            $tmp = Join-Path $TestDrive 'templated.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'templated.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Skips shell-variable images ($VAR and ${VAR})' {
            $content = "  image: `$IMAGE`n  image: `${CONTAINER_IMAGE}"
            $tmp = Join-Path $TestDrive 'var.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'var.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Skips AzureML asset references on an image: field' {
            $content = '  image: azureml:some-asset:latest'
            $tmp = Join-Path $TestDrive 'aml-image.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'aml-image.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Does not inspect environment: fields (AzureML :latest is left untouched)' {
            $content = '  environment: azureml:lerobot-training-env:latest'
            $tmp = Join-Path $TestDrive 'env.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'env.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Ignores a YAML comment that mentions image:' {
            $content = '  # image: example.com/repo:tag is just prose'
            $tmp = Join-Path $TestDrive 'comment.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'comment.yaml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Single-line files' {
        It 'Flags an unpinned image in a single-line file' {
            $content = '  image: python:3.11-slim'
            $tmp = Join-Path $TestDrive 'single-line-image.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'single-line-image.yaml' })
            $result.Name | Should -Be 'python'
        }
    }

    Context 'Helm values init/client keys' {
        It 'Flags an unpinned image under an init: key' {
            $content = '  init: "nvcr.io/nvidia/osmo/init-container:6.3.0"'
            $tmp = Join-Path $TestDrive 'init-unpinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'init-unpinned.yaml' })
            $result.Name | Should -Be 'nvcr.io/nvidia/osmo/init-container'
            $result.Version | Should -Be '6.3.0'
        }

        It 'Flags an unpinned image under a client: key' {
            $content = '  client: "nvcr.io/nvidia/osmo/client:6.3.0"'
            $tmp = Join-Path $TestDrive 'client-unpinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'client-unpinned.yaml' })
            $result.Name | Should -Be 'nvcr.io/nvidia/osmo/client'
        }

        It 'Treats a digest-pinned init: image as compliant' {
            $content = '  init: "nvcr.io/nvidia/osmo/init-container:6.3.0@sha256:1071863497eba749e4f680a336a08e0fe48cba7d0ddea402fecb732bb6de2041"'
            $tmp = Join-Path $TestDrive 'init-pinned.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'init-pinned.yaml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Ignores plain scalars under init:/client: (no registry path)' {
            $content = "  init: true`n  client: guest"
            $tmp = Join-Path $TestDrive 'scalar-init-client.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'scalar-init-client.yaml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'pinning-ignore directive' {
        It 'Exempts a same-line marked image but still flags the next' {
            $content = "  image: busybox:latest  # pinning-ignore`n  image: redis:7"
            $tmp = Join-Path $TestDrive 'ignore-sameline.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'ignore-sameline.yaml' })
            $result.Name | Should -Be 'redis'
        }

        It 'Exempts an image preceded by a dedicated pinning-ignore comment line' {
            $content = "  # pinning-ignore: local dev only`n  image: busybox:latest"
            $tmp = Join-Path $TestDrive 'ignore-prevline.yaml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-DockerImageViolations -FileInfo @{ Path = $tmp; Type = 'docker'; RelativePath = 'ignore-prevline.yaml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $result = Get-DockerImageViolations -FileInfo @{ Path = 'TestDrive:/nope/wf.yaml'; Type = 'docker'; RelativePath = 'nope/wf.yaml' }
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-FilesToScan docker discovery' -Tag 'Unit' {
    BeforeAll {
        $script:DockerScanRoot = Join-Path $TestDrive 'docker-scan-root'
        New-Item -ItemType Directory -Path (Join-Path $script:DockerScanRoot 'training/workflows/osmo') -Force | Out-Null
        Set-Content -Path (Join-Path $script:DockerScanRoot 'training/workflows/osmo/t.yaml') -Value 'image: foo:bar'
        Set-Content -Path (Join-Path $script:DockerScanRoot 'training/workflows/osmo/notes.sh') -Value 'echo hi'
        New-Item -ItemType Directory -Path (Join-Path $script:DockerScanRoot 'infrastructure/setup/manifests') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $script:DockerScanRoot 'infrastructure/setup/values') -Force | Out-Null
        Set-Content -Path (Join-Path $script:DockerScanRoot 'infrastructure/setup/manifests/m.yaml') -Value 'image: foo:bar'
        Set-Content -Path (Join-Path $script:DockerScanRoot 'infrastructure/setup/values/v.yaml') -Value 'init: reg.io/x:1'
    }

    It 'Discovers workflow YAML but not .sh under the docker type' {
        $rels = @(Get-FilesToScan -ScanPath $script:DockerScanRoot -Types @('docker') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain 'training/workflows/osmo/t.yaml'
        $rels | Should -Not -Contain 'training/workflows/osmo/notes.sh'
    }

    It 'Keeps one scan entry per path and type for overlapping scanners' {
        $files = @(Get-FilesToScan -ScanPath $script:DockerScanRoot -Types @('shell-inline-pip', 'docker') -Recursive)
        $workflowScans = @($files | Where-Object { $_.RelativePath -replace '\\', '/' -eq 'training/workflows/osmo/t.yaml' })
        $workflowScans.Count | Should -Be 2
        ($workflowScans.Type | Sort-Object) | Should -Be @('docker', 'shell-inline-pip')
    }

    It 'Discovers Kubernetes manifests and Helm values under the docker type' {
        $rels = @(Get-FilesToScan -ScanPath $script:DockerScanRoot -Types @('docker') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain 'infrastructure/setup/manifests/m.yaml'
        $rels | Should -Contain 'infrastructure/setup/values/v.yaml'
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

Describe 'Default fixture exclusions' -Tag 'Integration' {
    It 'Excludes intentionally insecure fixtures from direct scans' {
        $scanRoot = Join-Path $TestDrive 'scan-root'
        $scriptDir = Join-Path $scanRoot 'app'
        $fixtureDir = Join-Path $scanRoot 'scripts/tests/Fixtures/Security'
        New-Item -ItemType Directory -Path $scriptDir, $fixtureDir -Force | Out-Null
        Set-Content -Path (Join-Path $scriptDir 'run.sh') -Value 'pip install requests'
        Set-Content -Path (Join-Path $fixtureDir 'inline-pip-unpinned.sh') -Value 'pip install flask'

        $scriptPath = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
        $reportPath = Join-Path $TestDrive 'dependency-pinning-results.json'

        pwsh -NoProfile -File $scriptPath `
            -Path $scanRoot `
            -Recursive `
            -IncludeTypes 'shell-inline-pip' `
            -Format json `
            -OutputPath $reportPath `
            -Threshold 0 | Out-Null

        $report = Get-Content -Path $reportPath -Raw | ConvertFrom-Json
        $report.UnpinnedDependencies | Should -Be 1
        ($report.Violations[0].File -replace '\\', '/') | Should -Be 'app/run.sh'
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

    Context 'Package.json with overrides and resolutions' {
        BeforeAll {
            $script:overridesFileInfo = @{
                Path         = Join-Path $script:FixturesPath 'overrides-package.json'
                Type         = 'npm'
                RelativePath = 'overrides-package.json'
            }
        }

        It 'Flags a range specifier in overrides' {
            $violations = Get-NpmDependencyViolations -FileInfo $script:overridesFileInfo
            $followRedirects = $violations | Where-Object { $_.Name -eq 'follow-redirects' }

            $followRedirects | Should -Not -BeNullOrEmpty
            $followRedirects.Version | Should -Be '>=1.16.0 <2.0.0'
            $followRedirects.Metadata.Section | Should -Be 'overrides'
        }

        It 'Flags a range specifier in resolutions' {
            $violations = Get-NpmDependencyViolations -FileInfo $script:overridesFileInfo
            $globParent = $violations | Where-Object { $_.Name -eq 'glob-parent' }

            $globParent | Should -Not -BeNullOrEmpty
            $globParent.Metadata.Section | Should -Be 'resolutions'
        }

        It 'Flags a range specifier in a nested override object' {
            $violations = Get-NpmDependencyViolations -FileInfo $script:overridesFileInfo
            $bar = $violations | Where-Object { $_.Name -eq 'bar' }

            $bar | Should -Not -BeNullOrEmpty
            $bar.Version | Should -Be '^2.0.0'
        }

        It 'Does not flag exact-pinned overrides or resolutions' {
            $violations = Get-NpmDependencyViolations -FileInfo $script:overridesFileInfo
            $names = $violations | ForEach-Object { $_.Name }

            $names | Should -Not -Contain 'semver'
            $names | Should -Not -Contain 'minimatch'
            $names | Should -Not -Contain 'foo'
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

    Context 'Single-line pyproject.toml arrays' {
        BeforeAll {
            $script:singleLineFileInfo = @{
                Path         = Join-Path $script:FixturesPath 'single-line-pyproject.toml'
                Type         = 'pip'
                RelativePath = 'single-line-pyproject.toml'
            }
        }

        It 'Detects an unpinned dep in a single-line main dependencies array' {
            $violations = Get-PipDependencyViolations -FileInfo $script:singleLineFileInfo
            $numpy = $violations | Where-Object { $_.Name -eq 'numpy' }

            $numpy | Should -Not -BeNullOrEmpty
            $numpy.Version | Should -Be '>=1.26.0'
            $numpy.Metadata.Section | Should -Be 'dependencies'
        }

        It 'Detects an unpinned dep in a single-line optional-dependencies array' {
            $violations = Get-PipDependencyViolations -FileInfo $script:singleLineFileInfo
            $atheris = $violations | Where-Object { $_.Name -eq 'atheris' }

            $atheris | Should -Not -BeNullOrEmpty
            $atheris.Metadata.Section | Should -Be 'fuzz'
        }

        It 'Does not flag exact-pinned deps in single-line arrays' {
            $violations = Get-PipDependencyViolations -FileInfo $script:singleLineFileInfo
            $names = $violations | ForEach-Object { $_.Name }

            $names | Should -Not -Contain 'requests'
            $names | Should -Not -Contain 'sphinx'
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

    It 'Returns an npm ci suggestion for workflow-npm-commands regardless of the -Remediate flag' {
        $npmCmd = [DependencyViolation]::new()
        $npmCmd.Type = 'workflow-npm-commands'
        $npmCmd.Name = 'npm install'

        Mock Invoke-RestMethod { throw 'must not be called for npm-command remediation' }

        (Get-RemediationSuggestion -Violation $npmCmd) | Should -BeLike "*Replace 'npm install' with 'npm ci'*"
        (Get-RemediationSuggestion -Violation $npmCmd -Remediate) | Should -BeLike "*Replace 'npm install' with 'npm ci'*"
        Should -Invoke -CommandName Invoke-RestMethod -Times 0 -Exactly
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

    It 'Returns fallback when the API returns a value that is not a commit SHA' {
        $violation = [DependencyViolation]::new()
        $violation.Type = 'github-actions'
        $violation.Name = 'actions/checkout'
        $violation.Version = 'v4'

        Mock Invoke-RestMethod { return @{ sha = 'not-a-valid-sha' } }

        $result = Get-RemediationSuggestion -Violation $violation -Remediate
        $result | Should -Be 'Manually research and pin to immutable reference'
        $violation.Metadata.ContainsKey('ResolvedSha') | Should -BeFalse
    }

    It 'Resolves subpath actions against the owner/repo slug' {
        $subpath = [DependencyViolation]::new()
        $subpath.Type = 'github-actions'
        $subpath.Name = 'github/codeql-action/init'
        $subpath.Version = 'v3'

        Mock Invoke-RestMethod {
            return @{ sha = 'abc123def456abc123def456abc123def456abcd' }
        } -ParameterFilter {
            $Uri -eq 'https://api.github.com/repos/github/codeql-action/commits/v3'
        }

        $result = Get-RemediationSuggestion -Violation $subpath -Remediate
        $result | Should -Be 'Pin to SHA: uses: github/codeql-action/init@abc123def456abc123def456abc123def456abcd # v3'
        Should -Invoke -CommandName Invoke-RestMethod -Times 1 -Exactly
    }

    It 'Stashes the resolved SHA in violation metadata' {
        $violation = [DependencyViolation]::new()
        $violation.Type = 'github-actions'
        $violation.Name = 'actions/checkout'
        $violation.Version = 'v4'

        Mock Invoke-RestMethod {
            return @{ sha = 'abc123def456abc123def456abc123def456abcd' }
        }

        $null = Get-RemediationSuggestion -Violation $violation -Remediate
        $violation.Metadata['ResolvedSha'] | Should -Be 'abc123def456abc123def456abc123def456abcd'
    }
}

Describe 'Invoke-ActionShaPinApply' -Tag 'Unit' {
    BeforeAll {
        $script:Sha = 'abc123def456abc123def456abc123def456abcd'

        function New-ActionViolation {
            param(
                [string]$Name,
                [string]$Version,
                [int]$Line,
                [string]$Sha,
                [string]$File = 'ci.yml',
                [string]$Type = 'github-actions'
            )
            $violation = [DependencyViolation]::new()
            $violation.Type = $Type
            $violation.File = $File
            $violation.Line = $Line
            $violation.Name = $Name
            $violation.Version = $Version
            if ($Sha) { $violation.Metadata['ResolvedSha'] = $Sha }
            return $violation
        }
    }

    It 'Rewrites a tag-pinned action to its SHA with a trailing ref comment' {
        $workflow = Join-Path $TestDrive 'ci.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: actions/checkout@v4`n")

        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha
        ) -BasePath $TestDrive

        $applied | Should -Be 1
        [System.IO.File]::ReadAllText($workflow) | Should -Be "steps:`n  - uses: actions/checkout@$script:Sha # v4`n"
    }

    It 'Does not confuse a v4 ref with a longer v40 ref on adjacent lines' {
        $workflow = Join-Path $TestDrive 'boundary.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: actions/checkout@v4`n  - uses: actions/setup-node@v40`n")

        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'boundary.yml'
            New-ActionViolation -Name 'actions/setup-node' -Version 'v40' -Line 3 -Sha ('b' * 40) -File 'boundary.yml'
        ) -BasePath $TestDrive

        $applied | Should -Be 2
        $content = [System.IO.File]::ReadAllText($workflow)
        $content | Should -BeLike "*actions/checkout@$script:Sha # v4*"
        $content | Should -BeLike ('*actions/setup-node@' + ('b' * 40) + ' # v40*')
    }

    It 'Preserves the full name for monorepo subpath actions' {
        $workflow = Join-Path $TestDrive 'subpath.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: github/codeql-action/init@v3`n")

        $null = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'github/codeql-action/init' -Version 'v3' -Line 2 -Sha $script:Sha -File 'subpath.yml'
        ) -BasePath $TestDrive

        [System.IO.File]::ReadAllText($workflow) | Should -Be "steps:`n  - uses: github/codeql-action/init@$script:Sha # v3`n"
    }

    It 'Preserves LF line endings and the trailing newline' {
        $workflow = Join-Path $TestDrive 'newline.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: actions/checkout@v4`n")

        $null = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'newline.yml'
        ) -BasePath $TestDrive

        $content = [System.IO.File]::ReadAllText($workflow)
        $content.Contains("`r") | Should -BeFalse
        $content.EndsWith("`n") | Should -BeTrue
    }

    It 'Preserves CRLF line endings' {
        $workflow = Join-Path $TestDrive 'crlf.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`r`n  - uses: actions/checkout@v4`r`n")

        $null = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'crlf.yml'
        ) -BasePath $TestDrive

        [System.IO.File]::ReadAllText($workflow) | Should -Be "steps:`r`n  - uses: actions/checkout@$script:Sha # v4`r`n"
    }

    It 'Preserves each line ending in a mixed CRLF/LF file' {
        $workflow = Join-Path $TestDrive 'mixed.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`r`n  - uses: actions/checkout@v4`n  - run: echo hi`r`n")

        $null = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'mixed.yml'
        ) -BasePath $TestDrive

        [System.IO.File]::ReadAllText($workflow) | Should -Be "steps:`r`n  - uses: actions/checkout@$script:Sha # v4`n  - run: echo hi`r`n"
    }

    It 'Is idempotent: a second apply rewrites nothing' {
        $workflow = Join-Path $TestDrive 'idempotent.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: actions/checkout@v4`n")
        $violations = @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'idempotent.yml'
        )

        (Invoke-ActionShaPinApply -Violations $violations -BasePath $TestDrive) | Should -Be 1
        $afterFirst = [System.IO.File]::ReadAllText($workflow)
        (Invoke-ActionShaPinApply -Violations $violations -BasePath $TestDrive) | Should -Be 0
        [System.IO.File]::ReadAllText($workflow) | Should -Be $afterFirst
    }

    It 'Writes refs containing regex substitution tokens verbatim' {
        $workflow = Join-Path $TestDrive 'dollar.yml'
        [System.IO.File]::WriteAllText($workflow, "steps:`n  - uses: foo/bar@v1`$&2`n")

        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'foo/bar' -Version 'v1$&2' -Line 2 -Sha $script:Sha -File 'dollar.yml'
        ) -BasePath $TestDrive

        $applied | Should -Be 1
        [System.IO.File]::ReadAllText($workflow) | Should -Be "steps:`n  - uses: foo/bar@$script:Sha # v1`$&2`n"
    }

    It 'Ignores violations without a resolved SHA and non-actions types' {
        $workflow = Join-Path $TestDrive 'skip.yml'
        $original = "steps:`n  - uses: actions/checkout@v4`n"
        [System.IO.File]::WriteAllText($workflow, $original)

        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha '' -File 'skip.yml'
            New-ActionViolation -Name 'lodash' -Version '^4.17.0' -Line 2 -Sha $script:Sha -File 'skip.yml' -Type 'npm'
        ) -BasePath $TestDrive

        $applied | Should -Be 0
        [System.IO.File]::ReadAllText($workflow) | Should -Be $original
    }

    It 'Returns zero when the target file is missing' {
        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 2 -Sha $script:Sha -File 'nope.yml'
        ) -BasePath $TestDrive

        $applied | Should -Be 0
    }

    It 'Skips violations whose line is past the end of the file' {
        $workflow = Join-Path $TestDrive 'short.yml'
        $original = "steps:`n  - uses: actions/checkout@v4`n"
        [System.IO.File]::WriteAllText($workflow, $original)

        $applied = Invoke-ActionShaPinApply -Violations @(
            New-ActionViolation -Name 'actions/checkout' -Version 'v4' -Line 999 -Sha $script:Sha -File 'short.yml'
        ) -BasePath $TestDrive

        $applied | Should -Be 0
        [System.IO.File]::ReadAllText($workflow) | Should -Be $original
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

Describe 'Get-GhExtensionPinViolations' -Tag 'Unit' {
    BeforeAll {
        $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
    }
    Context 'Compliant install' {
        It 'Returns no violations when --pin is present' {
            $testFile = Join-Path $script:FixturesPath 'gh-extension-compliant.yml'
            $result = @(Get-GhExtensionPinViolations -FileInfo @{ Path = $testFile; Type = 'gh-extension'; RelativePath = 'gh-extension-compliant.yml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned install' {
        It 'Flags an install without --pin and ignores a commented mention' {
            $testFile = Join-Path $script:FixturesPath 'gh-extension-unpinned.yml'
            $result = @(Get-GhExtensionPinViolations -FileInfo @{ Path = $testFile; Type = 'gh-extension'; RelativePath = 'gh-extension-unpinned.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'github/gh-aw'
            $result[0].Severity | Should -Be 'warning'
        }

        It 'Honors a trailing pinning-ignore directive' {
            $tmp = Join-Path $TestDrive 'gh-ignore.sh'
            Set-Content -Path $tmp -Value 'gh extension install github/gh-aw  # pinning-ignore'
            $result = @(Get-GhExtensionPinViolations -FileInfo @{ Path = $tmp; Type = 'gh-extension'; RelativePath = 'gh-ignore.sh' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $result = @(Get-GhExtensionPinViolations -FileInfo @{ Path = 'TestDrive:/nope.yml'; Type = 'gh-extension'; RelativePath = 'nope.yml' })
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-PowerShellModuleViolations' -Tag 'Unit' {
    BeforeAll {
        $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
    }
    Context 'Compliant install' {
        It 'Returns no violations when every Install-Module pins -RequiredVersion' {
            $testFile = Join-Path $script:FixturesPath 'install-module-compliant.yml'
            $result = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $testFile; Type = 'powershell-modules'; RelativePath = 'install-module-compliant.yml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned install' {
        It 'Flags installs missing -RequiredVersion and ignores Mock and string mentions' {
            $testFile = Join-Path $script:FixturesPath 'install-module-unpinned.yml'
            $result = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $testFile; Type = 'powershell-modules'; RelativePath = 'install-module-unpinned.yml' })
            $result.Count | Should -Be 2
            ($result.Name | Sort-Object) | Should -Be @('powershell-yaml', 'PSScriptAnalyzer' | Sort-Object)
        }

        It 'Treats -MinimumVersion as a range, not a pin' {
            $tmp = Join-Path $TestDrive 'minver.ps1'
            Set-Content -Path $tmp -Value 'Install-Module -Name powershell-yaml -MinimumVersion 0.4.0 -Force'
            $result = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $tmp; Type = 'powershell-modules'; RelativePath = 'minver.ps1' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'powershell-yaml'
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $result = @(Get-PowerShellModuleViolations -FileInfo @{ Path = 'TestDrive:/nope.yml'; Type = 'powershell-modules'; RelativePath = 'nope.yml' })
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Test-NpmCommandLine' -Tag 'Unit' {
    Context 'Mutating commands are matched' {
        It 'Matches <Command>' -TestCases @(
            @{ Command = 'npm install' }
            @{ Command = 'npm install express' }
            @{ Command = 'npm i' }
            @{ Command = 'npm update' }
            @{ Command = 'npm install-test' }
            @{ Command = 'npm  install' }
            @{ Command = 'npm.cmd install' }
            @{ Command = 'npm.cmd i' }
            @{ Command = 'npm.cmd update' }
            @{ Command = 'npm.cmd install-test' }
            @{ Command = 'run: npm install && npm run build' }
        ) {
            param($Command)
            Test-NpmCommandLine -Line $Command | Should -Not -BeNullOrEmpty
        }
    }

    Context 'Deterministic and non-installing commands are not matched' {
        It 'Does not match <Command>' -TestCases @(
            @{ Command = 'npm ci' }
            @{ Command = 'npm run build' }
            @{ Command = 'npm run install' }
            @{ Command = 'npm test' }
            @{ Command = 'npm audit' }
            @{ Command = 'npm init' }
            @{ Command = 'npm install-ci-test' }
            @{ Command = 'npx create-react-app' }
            @{ Command = 'echo installing packages' }
            @{ Command = '' }
        ) {
            param($Command)
            Test-NpmCommandLine -Line $Command | Should -BeNullOrEmpty
        }
    }
}

Describe 'Get-WorkflowNpmCommandViolations' -Tag 'Unit' {
    BeforeAll {
        $script:FixturesPath = Join-Path $PSScriptRoot '../Fixtures/Workflows'
    }

    Context 'Compliant workflow' {
        It 'Returns no violations when every install uses npm ci' {
            $testFile = Join-Path $script:FixturesPath 'npm-commands-compliant.yml'
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $testFile; Type = 'workflow-npm-commands'; RelativePath = 'npm-commands-compliant.yml' })
            $result | Should -BeNullOrEmpty
        }
    }

    Context 'Unpinned workflow' {
        BeforeAll {
            $testFile = Join-Path $script:FixturesPath 'npm-commands-unpinned.yml'
            $script:NpmResult = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $testFile; Type = 'workflow-npm-commands'; RelativePath = 'npm-commands-unpinned.yml' })
        }

        It 'Flags only the mutating commands, ignoring npm ci, step names, comments, and pinning-ignore' {
            $script:NpmResult.Count | Should -Be 3
            ($script:NpmResult.Name | Sort-Object) | Should -Be @('npm i', 'npm install', 'npm update')
        }

        It 'Reports violations as workflow-npm-commands type with Medium severity' {
            $script:NpmResult[0].Type | Should -Be 'workflow-npm-commands'
            ($script:NpmResult.Severity | Sort-Object -Unique) | Should -Be @('Medium')
        }

        It 'Reports the line number of each flagged command' {
            ($script:NpmResult | Sort-Object Line).Line | Should -Be @(16, 20, 21)
            ($script:NpmResult | Sort-Object Line).Name | Should -Be @('npm install', 'npm update', 'npm i')
        }
    }

    Context 'Indentation-aware run: block confinement' {
        It 'Does not flag an npm command once the run: block has closed' {
            $content = @'
jobs:
  build:
    steps:
      - name: install deps
        run: |
          npm ci
      - name: npm install
        uses: actions/setup-node@v4
'@
            $tmp = Join-Path $TestDrive 'closed-block.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'closed-block.yml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Flags an inline list-item run: step (- run: npm install)' {
            $tmp = Join-Path $TestDrive 'inline-dash.yml'
            Set-Content -Path $tmp -Value '      - run: npm install'
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'inline-dash.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }

        It 'Does not scan same-step sibling keys after a "- run: |" block scalar' {
            $content = @'
jobs:
  build:
    steps:
      - run: |
          echo hi
        name: run npm install here
        env:
          NOTE: please run npm install manually
'@
            $tmp = Join-Path $TestDrive 'dash-block-siblings.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'dash-block-siblings.yml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Flags an npm command inside a "- run: |" block scalar' {
            $content = @'
jobs:
  build:
    steps:
      - run: |
          npm install
        name: unrelated
'@
            $tmp = Join-Path $TestDrive 'dash-block-content.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'dash-block-content.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }

        It 'Keeps scanning after a block-scalar line that begins with run:' {
            $content = @'
jobs:
  build:
    steps:
      - name: build
        run: |
          run: echo starting
          npm install
'@
            $tmp = Join-Path $TestDrive 'block-run-prefixed-line.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'block-run-prefixed-line.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }

        It 'Reports every mutating command in a single block scalar with distinct lines' {
            $content = @'
jobs:
  build:
    steps:
      - name: multi
        run: |
          npm install
          npm update
'@
            $tmp = Join-Path $TestDrive 'multi-block.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'multi-block.yml' })
            $result.Count | Should -Be 2
            ($result | Sort-Object Line).Name | Should -Be @('npm install', 'npm update')
            ($result | Sort-Object Line).Line | Should -Be @(6, 7)
        }

        It 'Flags an npm command inside a <Indicator> block scalar' -TestCases @(
            @{ Indicator = '>' }
            @{ Indicator = '|-' }
            @{ Indicator = '>-' }
            @{ Indicator = '|+' }
        ) {
            param($Indicator)
            $content = @"
jobs:
  build:
    steps:
      - name: build
        run: $Indicator
          npm install
"@
            $tmp = Join-Path $TestDrive "block-indicator.yml"
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'block-indicator.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }
    }

    Context 'pinning-ignore directive' {
        It 'Honors a trailing pinning-ignore on the command line' {
            $tmp = Join-Path $TestDrive 'npm-ignore-sameline.yml'
            Set-Content -Path $tmp -Value '        run: npm install -g some-tool@1.2.3 # pinning-ignore'
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-sameline.yml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Honors a dedicated pinning-ignore comment directly above the command' {
            $content = @'
    run: |
      # pinning-ignore
      npm install
'@
            $tmp = Join-Path $TestDrive 'npm-ignore-prevline.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-prevline.yml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Honors a pinning-ignore comment directly above an inline run: command' {
            $content = @'
    steps:
      # pinning-ignore
      - run: npm install
'@
            $tmp = Join-Path $TestDrive 'npm-ignore-prevline-inline.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-prevline-inline.yml' })
            $result | Should -BeNullOrEmpty
        }

        It 'Does not leak an above-line pinning-ignore to a subsequent inline command' {
            $content = @'
    steps:
      # pinning-ignore
      - run: npm install
      - run: npm update
'@
            $tmp = Join-Path $TestDrive 'npm-ignore-noleak-inline.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-noleak-inline.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm update'
        }

        It 'Does not honor a pinning-ignore comment separated from the command by a blank line' {
            $content = @'
    run: |
      # pinning-ignore

      npm install
'@
            $tmp = Join-Path $TestDrive 'npm-ignore-blankline.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-blankline.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }

        It 'Does not leak a pinning-ignore from inside a block scalar to the next step' {
            $content = @'
jobs:
  build:
    steps:
      - run: |
          npm ci
          # pinning-ignore
      - run: npm install
'@
            $tmp = Join-Path $TestDrive 'npm-ignore-crossblock.yml'
            Set-Content -Path $tmp -Value $content
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = $tmp; Type = 'workflow-npm-commands'; RelativePath = 'npm-ignore-crossblock.yml' })
            $result.Count | Should -Be 1
            $result[0].Name | Should -Be 'npm install'
        }
    }

    Context 'File not found' {
        It 'Returns empty array for non-existent file' {
            $result = @(Get-WorkflowNpmCommandViolations -FileInfo @{ Path = 'TestDrive:/nope.yml'; Type = 'workflow-npm-commands'; RelativePath = 'nope.yml' })
            $result | Should -BeNullOrEmpty
        }
    }
}

Describe 'Test-ShellDownloadSecurity (workflow run: blocks)' -Tag 'Unit' {
    It 'Flags curl/wget without checksum inside a workflow run: block' {
        $content = @'
steps:
  - run: |
      curl -sLO https://example.com/oras.tar.gz
      tar xzf oras.tar.gz -C /usr/local/bin/ oras
'@
        $tmp = Join-Path $TestDrive 'insecure-dl.yaml'
        Set-Content -Path $tmp -Value $content
        $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'insecure-dl.yaml' })
        $result.Count | Should -Be 1
        $result[0].Severity | Should -Be 'warning'
    }

    It 'Does not flag a download followed by sha256sum verification' {
        $content = @'
steps:
  - run: |
      curl -sLO https://example.com/oras.tar.gz
      echo "abc  oras.tar.gz" | sha256sum -c -
'@
        $tmp = Join-Path $TestDrive 'verified-dl.yaml'
        Set-Content -Path $tmp -Value $content
        $result = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'verified-dl.yaml' })
        $result | Should -BeNullOrEmpty
    }
}

Describe 'Get-FilesToScan composite-action and workflow-download discovery' -Tag 'Unit' {
    BeforeAll {
        $script:ScanRoot2 = Join-Path $TestDrive 'scan-root-2'
        foreach ($d in @('.github/workflows', '.github/actions/foo', '.github/actions/bar', 'training/x/workflows/osmo', 'scripts')) {
            New-Item -ItemType Directory -Path (Join-Path $script:ScanRoot2 $d) -Force | Out-Null
        }
        Set-Content -Path (Join-Path $script:ScanRoot2 '.github/workflows/ci.yml') -Value "jobs: {}"
        Set-Content -Path (Join-Path $script:ScanRoot2 '.github/actions/foo/action.yml') -Value "runs: { using: composite }"
        Set-Content -Path (Join-Path $script:ScanRoot2 '.github/actions/bar/action.yaml') -Value "runs: { using: composite }"
        Set-Content -Path (Join-Path $script:ScanRoot2 'training/x/workflows/osmo/job.yaml') -Value "tasks: []"
        Set-Content -Path (Join-Path $script:ScanRoot2 'scripts/tool.sh') -Value "echo hi"
    }

    It 'Discovers composite actions under .github/actions for github-actions' {
        $rels = @(Get-FilesToScan -ScanPath $script:ScanRoot2 -Types @('github-actions') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain '.github/actions/foo/action.yml'
        $rels | Should -Contain '.github/actions/bar/action.yaml'
        $rels | Should -Contain '.github/workflows/ci.yml'
    }

    It 'Discovers workflow YAML and shell scripts for shell-downloads' {
        $rels = @(Get-FilesToScan -ScanPath $script:ScanRoot2 -Types @('shell-downloads') -Recursive).RelativePath -replace '\\', '/'
        $rels | Should -Contain '.github/workflows/ci.yml'
        $rels | Should -Contain 'training/x/workflows/osmo/job.yaml'
        $rels | Should -Contain 'scripts/tool.sh'
    }
}

Describe 'Join-LineContinuations' -Tag 'Unit' {
    It 'Preserves blank lines (empty-string elements) without a binding error' {
        $r = Join-LineContinuations -Lines @('a', '', 'b') -ContinuationPattern '\\\s*$'
        $r.Count | Should -Be 3
    }

    It 'Joins a bash backslash continuation and tracks the start line' {
        $r = @(Join-LineContinuations -Lines @('foo \', '  bar', 'baz') -ContinuationPattern '\\\s*$')
        $r.Count | Should -Be 2
        $r[0].Text | Should -Match 'foo\s+bar'
        $r[0].Line | Should -Be 1
        $r[1].Line | Should -Be 3
    }

    It 'Joins a PowerShell backtick continuation only when the pattern includes it' {
        $bt = [char]96
        $lines = @("foo $bt", '  bar')
        @(Join-LineContinuations -Lines $lines -ContinuationPattern '[\\`]\s*$').Count | Should -Be 1
        @(Join-LineContinuations -Lines $lines -ContinuationPattern '\\\s*$').Count | Should -Be 2
    }
}

Describe 'Get-FilesToScan multi-type routing' -Tag 'Unit' {
    BeforeAll {
        $script:MultiRoot = Join-Path $TestDrive 'multi-root'
        New-Item -ItemType Directory -Path (Join-Path $script:MultiRoot '.github/workflows') -Force | Out-Null
        Set-Content -Path (Join-Path $script:MultiRoot '.github/workflows/ci.yml') -Value "jobs: {}"
    }

    It 'Returns one entry per matching type for a workflow YAML (dedup by Path+Type, not Path)' {
        $entries = @(Get-FilesToScan -ScanPath $script:MultiRoot -Types @('github-actions', 'gh-extension', 'powershell-modules') -Recursive)
        $ciEntries = @($entries | Where-Object { ($_.RelativePath -replace '\\', '/') -eq '.github/workflows/ci.yml' })
        $ciEntries.Count | Should -Be 3
        ($ciEntries.Type | Sort-Object) | Should -Be @('gh-extension', 'github-actions', 'powershell-modules')
    }
}

Describe 'Test-DependencyPinning end-to-end routing' -Tag 'Unit' {
    BeforeAll {
        $script:TestScript = Join-Path $PSScriptRoot '../../security/Test-DependencyPinning.ps1'
    }

    It 'Reports every applicable validator for one workflow file (gh-extension and powershell-modules are not shadowed by github-actions)' {
        $root = Join-Path $TestDrive 'e2e-root'
        New-Item -ItemType Directory -Path (Join-Path $root '.github/workflows') -Force | Out-Null
        $wf = @'
name: bad
on: push
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: gh extension install github/gh-aw
      - shell: pwsh
        run: Install-Module -Name powershell-yaml -Force -Scope CurrentUser
'@
        Set-Content -Path (Join-Path $root '.github/workflows/bad.yml') -Value $wf

        $jsonPath = Join-Path $TestDrive 'e2e.json'
        & $script:TestScript -Path $root -Recursive -Format json -OutputPath $jsonPath 2>&1 | Out-Null
        $report = Get-Content $jsonPath -Raw | ConvertFrom-Json
        $types = @($report.Violations.Type | Sort-Object -Unique)
        $types | Should -Contain 'github-actions'
        $types | Should -Contain 'gh-extension'
        $types | Should -Contain 'powershell-modules'
    }

    It 'Still flags unpinned inline pip in a workflow YAML (shell-downloads glob does not shadow shell-inline-pip)' {
        $root = Join-Path $TestDrive 'e2e-pip-root'
        New-Item -ItemType Directory -Path (Join-Path $root 'training/x/workflows/osmo') -Force | Out-Null
        $wf = @'
tasks:
  - name: t
    args:
      - "-lc"
      - |
        curl -sLO https://example.com/x.tgz
        pip install requests
'@
        Set-Content -Path (Join-Path $root 'training/x/workflows/osmo/job.yaml') -Value $wf

        $jsonPath = Join-Path $TestDrive 'e2e-pip.json'
        & $script:TestScript -Path $root -Recursive -Format json -OutputPath $jsonPath 2>&1 | Out-Null
        $report = Get-Content $jsonPath -Raw | ConvertFrom-Json
        @($report.Violations | Where-Object { $_.Type -eq 'shell-inline-pip' -and $_.Name -eq 'requests' }).Count | Should -BeGreaterThan 0
    }

    It 'Routes an npm install command in a workflow run: step to the workflow-npm-commands validator' {
        $root = Join-Path $TestDrive 'e2e-npm-root'
        New-Item -ItemType Directory -Path (Join-Path $root '.github/workflows') -Force | Out-Null
        $wf = @'
name: npm
on: push
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - run: |
          npm install
'@
        Set-Content -Path (Join-Path $root '.github/workflows/npm.yml') -Value $wf

        $jsonPath = Join-Path $TestDrive 'e2e-npm.json'
        & $script:TestScript -Path $root -Recursive -Format json -OutputPath $jsonPath 2>&1 | Out-Null
        $report = Get-Content $jsonPath -Raw | ConvertFrom-Json
        @($report.Violations | Where-Object { $_.Type -eq 'workflow-npm-commands' -and $_.Name -eq 'npm install' }).Count | Should -BeGreaterThan 0
    }

    It 'Runs both github-actions and workflow-npm-commands validators on the same workflow file' {
        $root = Join-Path $TestDrive 'e2e-coexist-root'
        New-Item -ItemType Directory -Path (Join-Path $root '.github/workflows') -Force | Out-Null
        $wf = @'
name: coexist
on: push
jobs:
  x:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          npm install
'@
        Set-Content -Path (Join-Path $root '.github/workflows/coexist.yml') -Value $wf

        $jsonPath = Join-Path $TestDrive 'e2e-coexist.json'
        & $script:TestScript -Path $root -Recursive -Format json -OutputPath $jsonPath 2>&1 | Out-Null
        $report = Get-Content $jsonPath -Raw | ConvertFrom-Json
        @($report.Violations | Where-Object { $_.Type -eq 'github-actions' -and $_.File -like '*coexist.yml' }).Count | Should -BeGreaterThan 0
        @($report.Violations | Where-Object { $_.Type -eq 'workflow-npm-commands' -and $_.Name -eq 'npm install' }).Count | Should -BeGreaterThan 0
    }
}

Describe 'New validators: continuation and flow-style robustness' -Tag 'Unit' {
    It 'gh-extension: does not flag a pin split across a bash backslash continuation' {
        $tmp = Join-Path $TestDrive 'gh-cont.yml'
        Set-Content -Path $tmp -Value "      gh extension install github/gh-aw \`n        --pin v0.81.6"
        $r = @(Get-GhExtensionPinViolations -FileInfo @{ Path = $tmp; Type = 'gh-extension'; RelativePath = 'gh-cont.yml' })
        $r | Should -BeNullOrEmpty
    }

    It 'gh-extension: a bash backtick command-substitution is not a continuation (flags the install at its own line)' {
        $bt = [char]96
        $tmp = Join-Path $TestDrive 'gh-bash-subst.sh'
        Set-Content -Path $tmp -Value "msg=${bt}echo skip --pin${bt}`ngh extension install github/gh-aw"
        $r = @(Get-GhExtensionPinViolations -FileInfo @{ Path = $tmp; Type = 'gh-extension'; RelativePath = 'gh-bash-subst.sh' })
        $r.Count | Should -Be 1
        $r[0].Line | Should -Be 2
    }

    It 'powershell-modules: does not flag -RequiredVersion split across a backtick continuation' {
        $bt = [char]96
        $tmp = Join-Path $TestDrive 'pm-cont.yml'
        Set-Content -Path $tmp -Value "      Install-Module -Name powershell-yaml $bt`n        -RequiredVersion 0.4.12 -Force"
        $r = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $tmp; Type = 'powershell-modules'; RelativePath = 'pm-cont.yml' })
        $r | Should -BeNullOrEmpty
    }

    It 'powershell-modules: flags a flow-style `run: Install-Module ...` step (no run: prefix shadowing)' {
        $tmp = Join-Path $TestDrive 'pm-flow.yml'
        Set-Content -Path $tmp -Value '      - run: Install-Module -Name powershell-yaml -Force -Scope CurrentUser'
        $r = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $tmp; Type = 'powershell-modules'; RelativePath = 'pm-flow.yml' })
        $r.Count | Should -Be 1
        $r[0].Name | Should -Be 'powershell-yaml'
    }

    It 'powershell-modules: does not flag Install-Module mentioned inside a block comment' {
        $tmp = Join-Path $TestDrive 'pm-blockcomment.ps1'
        Set-Content -Path $tmp -Value @'
<#
    Install-Module without -RequiredVersion resolves latest at run time.
#>
Write-Host 'noop'
'@
        $r = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $tmp; Type = 'powershell-modules'; RelativePath = 'pm-blockcomment.ps1' })
        $r | Should -BeNullOrEmpty
    }

    It 'powershell-modules: does not flag Install-Module inside a here-string body' {
        $tmp = Join-Path $TestDrive 'pm-herestring.ps1'
        $content = @(
            "`$wf = @'"
            'run: Install-Module -Name powershell-yaml -Force'
            "'@"
        )
        Set-Content -Path $tmp -Value $content
        $r = @(Get-PowerShellModuleViolations -FileInfo @{ Path = $tmp; Type = 'powershell-modules'; RelativePath = 'pm-herestring.ps1' })
        $r | Should -BeNullOrEmpty
    }
}

Describe 'Test-ShellDownloadSecurity single-line file' -Tag 'Unit' {
    It 'Flags an unverified download in a single-line file (no scalar Get-Content trap)' {
        $tmp = Join-Path $TestDrive 'oneline.sh'
        Set-Content -Path $tmp -Value 'curl -sL https://example.com/install.sh | bash'
        $r = @(Test-ShellDownloadSecurity -FileInfo @{ Path = $tmp; Type = 'shell-downloads'; RelativePath = 'oneline.sh' })
        $r.Count | Should -Be 1
    }
}
