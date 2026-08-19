#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore redir Xcom

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command git -ErrorAction SilentlyContinue) -and
        [bool](Get-Command jq -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:SourceScript = (Resolve-Path (Join-Path $PSScriptRoot '../../update-image-digests.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/update-image-digests'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null
    $script:OldDigest = 'sha256:' + ('a' * 64)
    $script:NewDigest = 'sha256:' + ('b' * 64)
    $script:OtherDigest = 'sha256:' + ('c' * 64)

    function New-DigestTestRepository {
        param(
            [Parameter(Mandatory)][string]$Name,
            [Parameter(Mandatory)][string]$Content
        )

        $workspace = Join-Path $script:ArtifactsRoot "$Name-$([System.Guid]::NewGuid().ToString('N'))"
        $scriptDir = Join-Path $workspace 'scripts'
        $libDir = Join-Path $scriptDir 'lib'
        $configDir = Join-Path $workspace 'config'
        New-Item -ItemType Directory -Path $libDir, $configDir -Force | Out-Null
        Copy-Item $script:SourceScript (Join-Path $scriptDir 'update-image-digests.sh')
        @'
info() { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
error() { printf '[ERROR] %s\n' "$*" >&2; }
fatal() { error "$@"; exit 1; }
section() { printf '== %s ==\n' "$1"; }
print_kv() { printf '%s: %s\n' "$1" "$2"; }
require_tools() {
  local tool
  for tool in "$@"; do command -v "$tool" >/dev/null || fatal "Missing tool: $tool"; done
}
'@ | Set-Content -Path (Join-Path $libDir 'common.sh') -NoNewline
        Set-Content -Path (Join-Path $configDir 'images.yaml') -Value $Content -NoNewline
        & git -C $workspace init -q
        & git -C $workspace add .
        return $workspace
    }

    function Invoke-DigestScript {
        param(
            [Parameter(Mandatory)][string]$Workspace,
            [Parameter(Mandatory)]
            [AllowEmptyCollection()]
            [AllowEmptyString()]
            [string[]]$Arguments,
            [string]$CurlBody
        )

        if (-not $CurlBody) {
            $CurlBody = "printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: $($script:NewDigest)\n\n'"
        }
        return Invoke-BashEntryScript `
            -ScriptPath (Join-Path $Workspace 'scripts/update-image-digests.sh') `
            -WorkDir $Workspace `
            -ScriptArgs $Arguments `
            -EnvVars @{ NO_COLOR = '1' } `
            -Stubs @{ curl = $CurlBody }
    }
}

AfterAll {
    if ($script:ArtifactsRoot) {
        Remove-Item -Recurse -Force $script:ArtifactsRoot -ErrorAction SilentlyContinue
    }
}

Describe 'update-image-digests.sh' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'fails on drift in check mode without modifying source files' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'check-drift' -Content "image: $reference`n"
        $configPath = Join-Path $workspace 'config/images.yaml'
        $before = Get-Content -Path $configPath -Raw
        $sarifPath = Join-Path $workspace 'logs/drift.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json
        $finding = $sarif.runs[0].results[0]

        $result.ExitCode | Should -Be 2
        Get-Content -Path $configPath -Raw | Should -BeExactly $before
        $result.StdErr | Should -Match 'Digest drift detected'
        $result.StdOut | Should -Match 'Drift Findings: 1'
        $sarif.runs[0].tool.driver.rules[0].id | Should -Be 'container-image-digest-drift'
        $finding.ruleId | Should -Be 'container-image-digest-drift'
        $finding.level | Should -Be 'warning'
        $finding.message.text | Should -BeExactly (
            "Pinned digest $($script:OldDigest) for example.com/robot:1.0 " +
            "differs from the registry digest $($script:NewDigest)."
        )
        $finding.locations[0].physicalLocation.artifactLocation.uri | Should -Be 'config/images.yaml'
        $finding.locations[0].physicalLocation.region.startLine | Should -Be 1
        $finding.locations[0].physicalLocation.region.startColumn | Should -Be 8
        $finding.locations[0].physicalLocation.region.endColumn | Should -Be 101
    }

    It 'passes check mode and emits empty SARIF when pins are current' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'check-current' -Content "image: $reference`n"
        $sarifPath = Join-Path $workspace 'logs/current.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 0
        $sarif.version | Should -Be '2.1.0'
        @($sarif.runs[0].results) | Should -HaveCount 0
    }

    It 'reports every stale occurrence on one line' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'multiple-occurrences' -Content "images: [$reference, $reference]`n"
        $sarifPath = Join-Path $workspace 'logs/multiple.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 2
        @($sarif.runs[0].results | ForEach-Object {
            $_.locations[0].physicalLocation.region.startLine
        } | Select-Object -Unique) | Should -Be @(1)
        @($sarif.runs[0].results | ForEach-Object {
            $_.locations[0].physicalLocation.region.startColumn
        } | Select-Object -Unique) | Should -Be @(10, 105)
        @($sarif.runs[0].results | ForEach-Object {
            $_.locations[0].physicalLocation.region.endColumn
        } | Select-Object -Unique) | Should -Be @(103, 198)
    }

    It 'reports a stale occurrence after a current occurrence on the same line' {
        $currentReference = "example.com/robot:1.0@$($script:NewDigest)"
        $staleReference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'current-before-stale' -Content "images: [$currentReference, $staleReference]`n"
        $sarifPath = Join-Path $workspace 'logs/current-before-stale.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json
        $finding = $sarif.runs[0].results[0]

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 1
        $finding.locations[0].physicalLocation.region.startColumn | Should -Be 105
        $finding.locations[0].physicalLocation.region.endColumn | Should -Be 198
    }

    It 'does not report a concatenated reference without a left boundary' {
        $currentReference = "example.com/robot:1.0@$($script:NewDigest)"
        $staleReference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'concatenated-reference' -Content "$currentReference$staleReference`n"
        $sarifPath = Join-Path $workspace 'logs/concatenated.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 0
        @($sarif.runs[0].results) | Should -HaveCount 0
    }

    It 'reports drift on a final line without a trailing newline' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'no-trailing-newline' -Content "image: $reference"
        $sarifPath = Join-Path $workspace 'logs/no-trailing-newline.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 1
        $sarif.runs[0].results[0].locations[0].physicalLocation.region.startLine | Should -Be 1
    }

    It 'does not match a short reference inside a longer reference' {
        $longReference = "example.com/foo/bar:1.0@$($script:NewDigest)"
        $shortReference = "bar:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'reference-boundary' -Content @"
image: $longReference
image: $shortReference
"@
        $sarifPath = Join-Path $workspace 'logs/boundary.sarif'
        $curlBody = @"
case "`$*" in
  *"/v2/library/bar/manifests/1.0"*) digest="$($script:OtherDigest)" ;;
  *"/v2/foo/bar/manifests/1.0"*) digest="$($script:NewDigest)" ;;
  *) printf '{"token":"test"}\n'; exit 0 ;;
esac
printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: %s\n\n' "`$digest"
"@

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        ) -CurlBody $curlBody
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 1
        $sarif.runs[0].results[0].locations[0].physicalLocation.region.startLine | Should -Be 2
    }

    It 'checks ordinary GitHub workflows but excludes gh-aw compiled workflows' {
        $currentReference = "example.com/robot:1.0@$($script:NewDigest)"
        $staleReference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'github-workflows' -Content "image: $currentReference`n"
        $workflowDir = Join-Path $workspace '.github/workflows'
        $workflowPath = Join-Path $workflowDir 'manual.yml'
        $compiledPath = Join-Path $workflowDir 'generated.lock.yml'
        $sarifPath = Join-Path $workspace 'logs/github-workflows.sarif'
        New-Item -ItemType Directory -Path $workflowDir -Force | Out-Null
        Set-Content -Path $workflowPath -Value "container: $staleReference`n" -NoNewline
        Set-Content -Path $compiledPath -Value "container: $staleReference`n" -NoNewline
        & git -C $workspace add .

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 1
        $sarif.runs[0].results[0].locations[0].physicalLocation.artifactLocation.uri |
            Should -Be '.github/workflows/manual.yml'
    }

    It 'updates stale pins in default mode' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'update' -Content "image: $reference`n"
        $configPath = Join-Path $workspace 'config/images.yaml'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @()

        $result.ExitCode | Should -Be 0
        Get-Content -Path $configPath -Raw | Should -BeExactly (
            "image: example.com/robot:1.0@$($script:NewDigest)`n"
        )
        $result.StdOut | Should -Match 'Files Changed: 1'
    }

    It 'reports dry-run changes without modifying source files' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'dry-run' -Content "image: $reference`n"
        $configPath = Join-Path $workspace 'config/images.yaml'
        $before = Get-Content -Path $configPath -Raw

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--dry-run')

        $result.ExitCode | Should -Be 0
        Get-Content -Path $configPath -Raw | Should -BeExactly $before
        $result.StdOut | Should -Match '\[dry-run\] Would update'
        $result.StdOut | Should -Match 'Files To Update: 1'
    }

    It 'reports drift without SARIF when no output path is requested' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'check-without-sarif' -Content "image: $reference`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check')

        $result.ExitCode | Should -Be 2
        $result.StdOut | Should -Match 'Drift Findings: 1'
        $result.StdOut | Should -Not -Match 'SARIF Report'
    }

    It 'rejects SARIF output outside check mode' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'sarif-without-check' -Content "image: $reference`n"
        $sarifPath = Join-Path $workspace 'logs/report.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--sarif-output', $sarifPath
        )

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--sarif-output requires --check'
        Test-Path $sarifPath | Should -BeFalse
    }

    It 'rejects an empty SARIF output operand' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'empty-sarif-operand' -Content "image: $reference`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--sarif-output', ''
        )

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--sarif-output requires a non-empty file path'
    }

    It 'rejects a flag-shaped SARIF output operand' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'flag-sarif-operand' -Content "image: $reference`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--sarif-output', '--check'
        )

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--sarif-output requires a non-empty file path'
    }

    It 'rejects unknown options' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'unknown-option' -Content "image: $reference`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--bogus')

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match 'Unknown option: --bogus'
    }

    It 'rejects malformed registry digest headers' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'malformed-digest' -Content "image: $reference`n"
        $curlBody = "printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: sha256:not-a-digest\n\n'"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check') -CurlBody $curlBody

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match 'Could not resolve a valid digest'
    }

    It 'uses authenticated Docker Hub manifest resolution' {
        $reference = "robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'docker-hub-auth' -Content "image: $reference`n"
        $curlBody = @"
case "`$*" in
  *"auth.docker.io/token"*) printf '{"token":"test-token"}\n' ;;
  *"registry-1.docker.io/v2/library/robot/manifests/1.0"*) printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: $($script:NewDigest)\n\n' ;;
  *) exit 1 ;;
esac
"@

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check') -CurlBody $curlBody
        $manifestCalls = @($result.Calls | Where-Object {
            $_ -match 'registry-1\.docker\.io/v2/library/robot/manifests/1\.0'
        })

        $result.ExitCode | Should -Be 2
        ($result.Calls -join "`n") | Should -Match 'auth\.docker\.io/token'
        $manifestCalls | Should -HaveCount 1
        $manifestCalls[0] | Should -Match ([regex]::Escape('Authorization: Bearer test-token'))
        $manifestCalls[0] | Should -Match -- '--proto-redir =https'
    }

    It 'uses the NGC token endpoint for nvcr.io references' {
        $reference = "nvcr.io/nvidia/isaac:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'ngc-auth' -Content "image: $reference`n"
        $curlBody = @"
case "`$*" in
  *"nvcr.io/proxy_auth?scope=repository:nvidia/isaac:pull"*) printf '{"token":"ngc-token"}\n' ;;
  *"nvcr.io/v2/nvidia/isaac/manifests/1.0"*) printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: $($script:NewDigest)\n\n' ;;
  *) exit 1 ;;
esac
"@

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check') -CurlBody $curlBody
        $manifestCalls = @($result.Calls | Where-Object {
            $_ -match 'nvcr\.io/v2/nvidia/isaac/manifests/1\.0'
        })

        $result.ExitCode | Should -Be 2
        ($result.Calls -join "`n") | Should -Match 'nvcr\.io/proxy_auth\?scope=repository:nvidia/isaac:pull'
        $manifestCalls | Should -HaveCount 1
        $manifestCalls[0] | Should -Match ([regex]::Escape('Authorization: Bearer ngc-token'))
    }

    It 'routes references with registry ports to the complete host' {
        $reference = "registry.example.com:5000/team/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'registry-port' -Content "image: $reference`n"
        $curlBody = @"
case "`$*" in
  *"registry.example.com:5000/v2/team/robot/manifests/1.0"*) printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: $($script:NewDigest)\n\n' ;;
  *) exit 1 ;;
esac
"@

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check') -CurlBody $curlBody
        $manifestCalls = @($result.Calls | Where-Object {
            $_ -match 'registry\.example\.com:5000/v2/team/robot/manifests/1\.0'
        })

        $result.ExitCode | Should -Be 2
        $manifestCalls | Should -HaveCount 1
        ($result.Calls -join "`n") | Should -Not -Match 'registry-1\.docker\.io'
        ($result.Calls -join "`n") | Should -Not -Match 'auth\.docker\.io'
    }

    It 'treats dots in image references as literal characters' {
        $currentReference = "example.com/robot:1.0@$($script:NewDigest)"
        $lookalikeReference = "exampleXcom/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'literal-dots' -Content @"
image: $currentReference
image: $lookalikeReference
"@
        $sarifPath = Join-Path $workspace 'logs/literal-dots.sarif'
        $curlBody = @"
case "`$*" in
  *"example.com/v2/robot/manifests/1.0"*) digest="$($script:NewDigest)" ;;
  *"registry-1.docker.io/v2/exampleXcom/robot/manifests/1.0"*) digest="$($script:OtherDigest)" ;;
  *) exit 1 ;;
esac
printf 'HTTP/1.1 200 OK\nDocker-Content-Digest: %s\n\n' "`$digest"
"@

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        ) -CurlBody $curlBody
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 2
        @($sarif.runs[0].results) | Should -HaveCount 1
        $sarif.runs[0].results[0].locations[0].physicalLocation.region.startLine | Should -Be 2
        $sarif.runs[0].results[0].message.text | Should -Match 'exampleXcom/robot:1\.0'
    }

    It 'fails when the registry response has no digest header' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'missing-digest' -Content "image: $reference`n"
        $configPath = Join-Path $workspace 'config/images.yaml'
        $before = Get-Content -Path $configPath -Raw

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @() -CurlBody 'exit 22'

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match 'Could not resolve a valid digest'
        Get-Content -Path $configPath -Raw | Should -BeExactly $before
    }

    It 'writes no SARIF when a digest cannot be resolved' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'resolution-failure-sarif' -Content "image: $reference`n"
        $sarifPath = Join-Path $workspace 'logs/failure.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        ) -CurlBody 'exit 22'

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match 'Could not resolve a valid digest'
        Test-Path $sarifPath | Should -BeFalse
    }

    It 'counts drifted occurrences and files separately' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'drift-counts' -Content "images: [$reference, $reference]`n"
        Set-Content -Path (Join-Path $workspace 'config/other.yaml') -Value "image: $reference`n" -NoNewline
        & git -C $workspace add .

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check')

        $result.ExitCode | Should -Be 2
        $result.StdOut | Should -Match 'Drift Findings: 3'
        $result.StdOut | Should -Match 'Files With Drift: 2'
    }

    It 'rejects a tracked SARIF output path' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'tracked-sarif' -Content "image: $reference`n"
        $configPath = Join-Path $workspace 'config/images.yaml'
        $before = Get-Content -Path $configPath -Raw

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', 'config/images.yaml'
        )

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match 'Refusing to overwrite tracked file with SARIF'
        Get-Content -Path $configPath -Raw | Should -BeExactly $before
    }

    It 'rejects a SARIF output directory' {
        $reference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'sarif-directory' -Content "image: $reference`n"
        $sarifDirectory = Join-Path $workspace 'logs'
        New-Item -ItemType Directory -Path $sarifDirectory -Force | Out-Null

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifDirectory
        )

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--sarif-output must name a file, not a directory'
    }

    It 'rejects a missing SARIF output operand with a clear error' {
        $workspace = New-DigestTestRepository -Name 'missing-operand' -Content "image: example.com/robot:1.0@$($script:NewDigest)`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--sarif-output')

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--sarif-output requires a non-empty file path'
    }

    It 'rejects incompatible non-writing modes before discovery' {
        $workspace = New-DigestTestRepository -Name 'incompatible-modes' -Content "image: example.com/robot:1.0@$($script:NewDigest)`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--check', '--dry-run')

        $result.ExitCode | Should -Be 1
        $result.StdErr | Should -Match '--check and --dry-run cannot be combined'
    }

    It 'lists discovered inputs without contacting a registry in config preview' {
        $reference = "example.com/robot:1.0@$($script:NewDigest)"
        $workspace = New-DigestTestRepository -Name 'config-preview' -Content "image: $reference`n"

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @('--config-preview') -CurlBody 'exit 1'

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Image: example\.com/robot:1\.0'
        $result.StdOut | Should -Match 'File: config/images\.yaml'
        @($result.Calls) | Should -HaveCount 0
    }

    It 'excludes Dependabot-owned and test artifact files from discovery' {
        $currentReference = "example.com/robot:1.0@$($script:NewDigest)"
        $staleReference = "example.com/robot:1.0@$($script:OldDigest)"
        $workspace = New-DigestTestRepository -Name 'excluded-paths' -Content "image: $currentReference`n"
        $fixtureDirectory = Join-Path $workspace 'scripts/tests/Fixtures'
        New-Item -ItemType Directory -Path $fixtureDirectory -Force | Out-Null
        $excludedFiles = @{
            'Dockerfile' = "FROM $staleReference`n"
            'docker-compose.yml' = "image: $staleReference`n"
            'scripts/tests/Fixtures/sample.yaml' = "image: $staleReference`n"
            'scripts/tests/sample.Tests.ps1' = "`$image = '$staleReference'`n"
        }
        foreach ($entry in $excludedFiles.GetEnumerator()) {
            $path = Join-Path $workspace $entry.Key
            New-Item -ItemType Directory -Path (Split-Path $path) -Force | Out-Null
            Set-Content -Path $path -Value $entry.Value -NoNewline
        }
        & git -C $workspace add .
        $sarifPath = Join-Path $workspace 'logs/excluded.sarif'

        $result = Invoke-DigestScript -Workspace $workspace -Arguments @(
            '--check', '--sarif-output', $sarifPath
        )
        $sarif = Get-Content -Path $sarifPath -Raw | ConvertFrom-Json

        $result.ExitCode | Should -Be 0
        @($sarif.runs[0].results) | Should -HaveCount 0
    }
}
