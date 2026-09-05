#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore pscustomobject subshell subshells tignored

BeforeAll {
    Import-Module $PSScriptRoot/../../security/Modules/PinnedToolVersions.psm1 -Force
}

Describe 'Get-PinnedToolVersionAssignments' -Tag 'Unit' {
    BeforeEach {
        $script:RepoRoot = Join-Path $TestDrive 'repo'
        New-Item -ItemType Directory -Path $script:RepoRoot -Force | Out-Null
        $script:Parameters = @{
            ShellVariable      = 'UV_VERSION'
            PowerShellVariable = 'UvVersion'
            RepoRoot           = $script:RepoRoot
        }
    }

    It 'Returns every unique file and version pair' {
        @'
UV_VERSION="0.12.8"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'first.sh')
        @'
UV_VERSION="0.12.8"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'second.sh')
        @'
$UvVersion = '0.12.8'
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'third.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('first.sh', 'second.sh', 'third.ps1')

        $pins.Count | Should -Be 3
        @($pins.File | Sort-Object) | Should -Be @('first.sh', 'second.sh', 'third.ps1')
        @($pins.Version | Sort-Object -Unique) | Should -Be @('0.12.8')
    }

    It 'Extracts assignments embedded in JSON commands' {
        @'
{"command": "UV_VERSION=v0.12.8"}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'command.json')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('command.json')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts assignments after JSON command separators' {
        @'
{"command": "echo ready && UV_VERSION=0.12.8"}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'command.json')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('command.json')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Reads wildcard characters in file names literally' {
        $file = '[pin].sh'
        'UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot $file)

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @($file)

        $pins.Count | Should -Be 1
        $pins[0].File | Should -Be $file
    }

    It 'Skips empty files and assignments in comments' {
        New-Item -ItemType File -Path (Join-Path $script:RepoRoot 'empty.sh') | Out-Null
        '# UV_VERSION="0.1.0"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'comment.sh')
        'echo "example"; # run with; UV_VERSION="0.1.1"' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'inline-comment.sh')
        @'
{
  // "UV_VERSION=0.2.0",
  /* "UV_VERSION=0.3.0", */
  "url": "https://example.com//UV_VERSION=0.4.0"
}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'block-comment.jsonc')
        @'
<#
$UvVersion = '0.5.0'
#>
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'block-comment.ps1')
        'UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'pin.sh')

        $files = @(
            'empty.sh',
            'comment.sh',
            'inline-comment.sh',
            'block-comment.jsonc',
            'block-comment.ps1',
            'pin.sh'
        )
        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files $files

        $pins.Count | Should -Be 1
        $pins[0].File | Should -Be 'pin.sh'
    }

    It 'Preserves hash characters in shell strings and parameter expansions' {
        @'
printf '#; UV_VERSION=0.1.0'
suffix="${value#prefix}"
UV_VERSION="0.12.8"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'hashes.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('hashes.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts shell default expansion assignments' {
        'ACTIONLINT_VERSION="${ACTIONLINT_VERSION:-1.7.12}"' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'install.sh')

        $pins = Get-PinnedToolVersionAssignments `
            -ShellVariable 'ACTIONLINT_VERSION' `
            -Files @('install.sh') `
            -RepoRoot $script:RepoRoot

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '1.7.12'
    }

    It 'Extracts unquoted shell default expansion assignments' {
        'UV_VERSION=${UV_VERSION:-0.12.8}' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'install.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('install.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts exported shell assignments' {
        'export UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'export.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('export.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts assignments after leading environment assignments' {
        'MODE=ci UV_VERSION="0.12.8" run-tool' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'prefixed.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('prefixed.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts assignments after shell control keywords' {
        'if true; then UV_VERSION="0.12.8"; fi' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'conditional.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('conditional.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts assignments inside shell subshells' {
        '( UV_VERSION="0.12.8"; install_uv )' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'subshell.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('subshell.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Rejects non-literal assignment suffixes' {
        @'
UV_VERSION=0.12.8invalid
UV_VERSION="0.12.8${SUFFIX}"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'dynamic.sh')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('dynamic.sh')
        } | Should -Throw "*not a supported literal version*"
    }

    It 'Rejects unsupported assignments when the same file contains a supported assignment' {
        @'
UV_VERSION=0.12.8
UV_VERSION=$(cat version.txt)
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'mixed.sh')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('mixed.sh')
        } | Should -Throw "*not a supported literal version*"
    }

    It 'Parses decoded JSON command strings' {
        @'
{"command": "MODE=ci UV_VERSION=\"0.12.8\" run-tool"}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'decoded.json')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('decoded.json')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Ignores PowerShell assignments without a configured variable' {
        '$UvVersion = ''0.12.8''' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'pin.ps1')

        $pins = @(
            Get-PinnedToolVersionAssignments `
                -ShellVariable 'UV_VERSION' `
                -Files @('pin.ps1') `
                -RepoRoot $script:RepoRoot
        )

        $pins.Count | Should -Be 0
    }

    It 'Rejects non-literal PowerShell assignments' {
        @'
$UvVersion = $env:UV_VERSION
$UvVersion = "0.12.$suffix"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'dynamic.ps1')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('dynamic.ps1')
        } | Should -Throw "*not a supported literal version*"
    }

    It 'Rejects unsupported PowerShell assignments when other files contain supported assignments' {
        '$UvVersion = ''0.12.8''' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'literal.ps1')
        '$UvVersion = "0.12.$patch"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'dynamic.ps1')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('literal.ps1', 'dynamic.ps1')
        } | Should -Throw "*not a supported literal version*"
    }

    It 'Ignores compound PowerShell assignments' {
        @'
$UvVersion = '0.12.8'
$UvVersion += '.1'
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'compound.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('compound.ps1')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts PowerShell assignments inside functions' {
        @'
$UvVersion = '0.12.8'
function Test-Version {
    $uvVersion = '0.11.21'
}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'scopes.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('scopes.ps1')

        $pins.Count | Should -Be 2
        @($pins.Version) | Should -Be @('0.11.21', '0.12.8')
    }

    It 'Extracts explicitly scoped PowerShell assignments' {
        @'
$script:UvVersion = '0.10.0'
$global:UvVersion = '0.11.0'
$local:UvVersion = '0.12.8'
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'scoped.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('scoped.ps1')

        @($pins.Version) | Should -Be @('0.10.0', '0.11.0', '0.12.8')
    }

    It 'Extracts PowerShell environment assignments for the shell variable' {
        '$env:UV_VERSION = ''0.12.8''' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'environment.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('environment.ps1')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Restricts environment discovery to environment-scoped assignments' {
        @'
$UV_VERSION = '0.11.0'
$env:UV_VERSION = '0.12.8'
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'environment.ps1')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('environment.ps1')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Throws when a PowerShell file cannot be parsed' {
        '$UvVersion = {' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'invalid.ps1')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('invalid.ps1')
        } | Should -Throw "Could not parse 'invalid.ps1'*"
    }

    It 'Ignores assignments mentioned in shell prose' {
        'echo "install UV_VERSION=0.2.0 docs"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'prose.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('prose.sh')

        $pins.Count | Should -Be 0
    }

    It 'Ignores assignment-looking heredoc content' {
        @'
cat <<'EOF'
UV_VERSION=0.1.0
EOF
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'heredoc.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('heredoc.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Does not treat quoted heredoc markers as declarations' {
        @'
printf '%s\n' '<<EOF'
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'quoted-heredoc-marker.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('quoted-heredoc-marker.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Does not treat arithmetic shifts as heredoc declarations' {
        @'
mask=$((1 << 4))
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'arithmetic.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('arithmetic.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Rejects unterminated heredocs' {
        @'
cat <<EOF
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'unterminated.sh')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('unterminated.sh')
        } | Should -Throw "*Unterminated shell heredoc with delimiter 'EOF'*"
    }

    It 'Extracts assignments after tab-stripped heredocs' {
        "cat <<-EOF`n`tignored`n`tEOF`nUV_VERSION=0.12.8" |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'tabbed-heredoc.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('tabbed-heredoc.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Extracts assignments after backslash-escaped heredocs' {
        @'
cat <<\EOF
UV_VERSION=0.1.0
EOF
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'escaped-heredoc.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('escaped-heredoc.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Rejects unterminated quoted heredoc delimiters' {
        "cat <<'EOF`nUV_VERSION=0.12.8" |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'quoted-delimiter.sh')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('quoted-delimiter.sh')
        } | Should -Throw '*Unterminated quoted shell heredoc delimiter*'
    }

    It 'Accepts readonly declarations with options' {
        'declare -r UV_VERSION=0.12.8' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'readonly.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('readonly.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Ignores every body when one command declares multiple heredocs' {
        @'
cat <<FIRST <<SECOND
UV_VERSION=0.1.0
FIRST
UV_VERSION=0.2.0
SECOND
UV_VERSION=0.12.8
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'multiple-heredocs.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('multiple-heredocs.sh')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Throws when a candidate is missing from the working tree' {
        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('missing.sh')
        } | Should -Throw "*missing from the working tree: 'missing.sh'*"
    }

    It 'Rejects a candidate that resolves outside the repository' {
        $outside = Join-Path $TestDrive 'outside.sh'
        'UV_VERSION=0.12.8' | Set-Content -LiteralPath $outside
        New-Item -ItemType SymbolicLink -Path (Join-Path $script:RepoRoot 'link.sh') -Target $outside | Out-Null

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('link.sh')
        } | Should -Throw "*resolves outside the repository: 'link.sh'*"
    }

    It 'Accepts a candidate in a nested repository directory' {
        New-Item -ItemType Directory -Path (Join-Path $script:RepoRoot 'nested') | Out-Null
        'UV_VERSION=0.12.8' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'nested/pin.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('nested/pin.sh')

        $pins.Count | Should -Be 1
        $pins[0].File | Should -Be 'nested/pin.sh'
    }

    It 'Throws when a JSON candidate cannot be parsed' {
        '{ "command": "UV_VERSION=0.12.8"' |
            Set-Content -LiteralPath (Join-Path $script:RepoRoot 'bad.json')

        {
            Get-PinnedToolVersionAssignments @script:Parameters -Files @('bad.json')
        } | Should -Throw "*Could not parse 'bad.json' as JSON*"
    }

    It 'Preserves comment markers inside escaped JSON strings' {
        @'
{"command": "printf \"a//b\" && UV_VERSION=0.12.8"}
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'escaped.json')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('escaped.json')

        $pins.Count | Should -Be 1
        $pins[0].Version | Should -Be '0.12.8'
    }

    It 'Preserves distinct versions declared in one file' {
        @'
UV_VERSION="0.11.21"
UV_VERSION="0.12.8"
UV_VERSION="0.12.8"
'@ | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'versions.sh')

        $pins = Get-PinnedToolVersionAssignments @script:Parameters -Files @('versions.sh')

        $pins.Count | Should -Be 2
        @($pins.Version) | Should -Be @('0.11.21', '0.12.8')
    }

    It 'Preserves file and version pairs through JSON serialization' {
        'UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'first.sh')
        'UV_VERSION="0.11.21"' | Set-Content -LiteralPath (Join-Path $script:RepoRoot 'second.sh')
        $pins = @(Get-PinnedToolVersionAssignments @script:Parameters -Files @('first.sh', 'second.sh'))

        $roundTrip = @(@{ Pins = $pins } | ConvertTo-Json -Depth 5 | ConvertFrom-Json)

        $roundTrip[0].Pins.Count | Should -Be 2
        @($roundTrip[0].Pins.File | Sort-Object) | Should -Be @('first.sh', 'second.sh')
        @($roundTrip[0].Pins.Version | Sort-Object) | Should -Be @('0.11.21', '0.12.8')
    }
}

Describe 'Get-PinCandidateFiles' -Tag 'Unit' {
    It 'Excludes fixtures and Pester tests but retains executable test helpers' {
        $repoRoot = Join-Path $TestDrive 'repo'
        New-Item -ItemType Directory -Path (Join-Path $repoRoot 'scripts/tests/Fixtures') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $repoRoot 'scripts/tests/security') -Force | Out-Null
        'UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $repoRoot 'setup.sh')
        'UV_VERSION="0.1.0"' | Set-Content -LiteralPath (Join-Path $repoRoot 'scripts/tests/Fixtures/fixture.ps1')
        'UV_VERSION="0.2.0"' | Set-Content -LiteralPath (Join-Path $repoRoot 'scripts/tests/security/Pin.Tests.ps1')
        'UV_VERSION="0.12.8"' | Set-Content -LiteralPath (Join-Path $repoRoot 'scripts/tests/Invoke-PesterTests.ps1')
        git -C $repoRoot init --quiet
        git -C $repoRoot add .

        $files = Get-PinCandidateFiles -RepoRoot $repoRoot

        @($files | Sort-Object) | Should -Be @('scripts/tests/Invoke-PesterTests.ps1', 'setup.sh')
    }

    It 'Returns every supported tracked source extension' {
        $repoRoot = Join-Path $TestDrive 'extensions'
        New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null
        foreach ($file in @('pin.sh', 'pin.ps1', 'pin.json', 'pin.jsonc', 'ignored.yml')) {
            'content' | Set-Content -LiteralPath (Join-Path $repoRoot $file)
        }
        git -C $repoRoot init --quiet
        git -C $repoRoot add .

        $files = Get-PinCandidateFiles -RepoRoot $repoRoot

        @($files | Sort-Object) | Should -Be @('pin.json', 'pin.jsonc', 'pin.ps1', 'pin.sh')
    }

    It 'Throws when no candidate files are tracked' {
        $repoRoot = Join-Path $TestDrive 'empty-repo'
        New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null
        'content' | Set-Content -LiteralPath (Join-Path $repoRoot 'README.md')
        git -C $repoRoot init --quiet
        git -C $repoRoot add README.md

        { Get-PinCandidateFiles -RepoRoot $repoRoot } | Should -Throw 'Could not enumerate tracked source files'
    }

    It 'Throws when git enumeration fails after emitting partial output' {
        Mock git {
            'pin.sh'
            $global:LASTEXITCODE = 1
        } -ModuleName PinnedToolVersions

        { Get-PinCandidateFiles -RepoRoot $TestDrive } |
            Should -Throw '*Could not enumerate tracked source files (git exit code 1)*'
    }
}

Describe 'Repository pin discovery' -Tag 'Integration' {
    It 'Discovers every uv assignment in the repository with one consistent version' {
        $repoRoot = git rev-parse --show-toplevel
        $files = Get-PinCandidateFiles -RepoRoot $repoRoot

        $pins = Get-PinnedToolVersionAssignments `
            -ShellVariable 'UV_VERSION' `
            -PowerShellVariable 'UvVersion' `
            -Files $files `
            -RepoRoot $repoRoot

        @($pins.File) | Should -Contain 'setup-dev.ps1'
        @($pins.File) | Should -Contain 'shared/ci/smoke-import.sh'
        @($pins.File) | Should -Contain 'training/rl/scripts/setup_isaac_runtime.sh'
        @($pins.File) | Should -Contain 'infrastructure/setup/optional/isaac-sim-vm/scripts/install-dev-deps.sh'
        @($pins.Version | Sort-Object -Unique).Count | Should -Be 1
    }
}

Describe 'Get-PinnedToolFreshness' -Tag 'Unit' {
    It 'Rejects an empty assignment set' {
        { Get-PinnedToolFreshness -Assignments @() -LatestVersion '0.12.8' } |
            Should -Throw '*argument is null, empty*'
    }

    It 'Marks one current assignment as consistent' {
        $assignments = @([pscustomobject]@{ File = 'pin.sh'; Version = '0.12.8' })

        $result = Get-PinnedToolFreshness -Assignments $assignments -LatestVersion '0.12.8'

        $result.IsStale | Should -BeFalse
        $result.IsInconsistent | Should -BeFalse
        @($result.PinnedVersions) | Should -Be @('0.12.8')
    }

    It 'Marks mixed pins stale and inconsistent' {
        $assignments = @(
            [pscustomobject]@{ File = 'old.sh'; Version = '0.11.21' }
            [pscustomobject]@{ File = 'current.sh'; Version = '0.12.8' }
        )

        $result = Get-PinnedToolFreshness -Assignments $assignments -LatestVersion '0.12.8'

        $result.IsStale | Should -BeTrue
        $result.IsInconsistent | Should -BeTrue
        @($result.PinnedVersions) | Should -Be @('0.11.21', '0.12.8')
    }

    It 'Marks matching pins current and consistent' {
        $assignments = @(
            [pscustomobject]@{ File = 'first.sh'; Version = '0.12.8' }
            [pscustomobject]@{ File = 'second.sh'; Version = '0.12.8' }
        )

        $result = Get-PinnedToolFreshness -Assignments $assignments -LatestVersion '0.12.8'

        $result.IsStale | Should -BeFalse
        $result.IsInconsistent | Should -BeFalse
    }

    It 'Marks uniformly old pins stale but consistent' {
        $assignments = @(
            [pscustomobject]@{ File = 'first.sh'; Version = '0.11.21' }
            [pscustomobject]@{ File = 'second.sh'; Version = '0.11.21' }
        )

        $result = Get-PinnedToolFreshness -Assignments $assignments -LatestVersion '0.12.8'

        $result.IsStale | Should -BeTrue
        $result.IsInconsistent | Should -BeFalse
    }
}
