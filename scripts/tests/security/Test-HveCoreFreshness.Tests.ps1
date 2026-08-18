#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

BeforeAll {
    . $PSScriptRoot/../../security/Test-HveCoreFreshness.ps1
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    Import-Module powershell-yaml -Force

    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:BootstrapScriptPath = Join-Path $script:RepoRoot 'scripts/ci/bootstrap-hve-core-rpi-skills.sh'
    $script:SetupPath = Join-Path $TestDrive 'copilot-setup-steps.yml'
    @'
      - name: Bootstrap hve-core RPI skills
        env:
          # microsoft/hve-core derived-files release: hve-core-v3.2.2 (2026-03-23)
          HVE_CORE_DERIVED_FILES_REF: e69486a5f809ede45c63c0a31358c12912bd5168
        run: echo bootstrap
'@ | Set-Content -Path $script:SetupPath -Encoding utf8

    $script:ResolvedRpiSha = '130ab64338bb77e912e603693672c31f14bc60c6'
    $script:RequiredRpiSkills = @(
        'rpi-quick'
        'rpi-research'
        'rpi-plan'
        'rpi-implement'
        'rpi-review'
        'rpi-challenger'
        'rpi-plan-critique'
        'rpi-walkthrough'
    )

    function New-RpiTreeJson {
        param(
            [switch]$Truncated,
            [string]$OmitSkill,
            [string]$OverrideMode,
            [string]$ExtraPath,
            [string]$OverrideSha
        )

        $blobSha = (& bash -c "printf '# skill\n' | git hash-object --stdin").Trim()
        $tree = foreach ($skill in $script:RequiredRpiSkills) {
            if ($skill -eq $OmitSkill) {
                continue
            }
            [ordered]@{
                path = ".github/skills/rpi/$skill/SKILL.md"
                mode = if ($OverrideMode -and $skill -eq $script:RequiredRpiSkills[0]) { $OverrideMode } else { '100644' }
                type = 'blob'
                sha  = if ($OverrideSha -and $skill -eq $script:RequiredRpiSkills[0]) { $OverrideSha } else { $blobSha }
            }
        }
        if ($ExtraPath) {
            $tree += [ordered]@{
                path = $ExtraPath
                mode = '100644'
                type = 'blob'
                sha  = $blobSha
            }
        }

        return @{
            truncated = [bool]$Truncated
            tree      = @($tree)
        } | ConvertTo-Json -Depth 5 -Compress
    }

    function Invoke-RpiBootstrap {
        param(
            [Parameter(Mandatory)][string]$TreeJson,
            [string]$Workspace = (Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))),
            [string]$CommitSha = $script:ResolvedRpiSha,
            [string]$UpstreamRef = $script:ResolvedRpiSha,
            [string]$UpstreamSkillsPath = '.github/skills/rpi',
            [string]$Destination,
            [string]$GhStub,
            [string]$CurlStub,
            [hashtable]$AdditionalEnv = @{},
            [hashtable]$AdditionalStubs = @{}
        )

        $commitJsonPath = Join-Path $Workspace 'commit.json'
        $treeJsonPath = Join-Path $Workspace 'tree.json'
        if (-not $Destination) {
            $Destination = Join-Path $Workspace '.github/skills/rpi'
        }
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        @{ sha = $CommitSha } | ConvertTo-Json -Compress | Set-Content -Path $commitJsonPath -NoNewline
        $TreeJson | Set-Content -Path $treeJsonPath -NoNewline

        if (-not $GhStub) {
            $GhStub = @'
case "$*" in
    *commits/*) cat "$COMMIT_JSON_PATH" ;;
    *git/trees/*) cat "$TREE_JSON_PATH" ;;
    *) exit 1 ;;
esac
'@
        }
        if (-not $CurlStub) {
            $CurlStub = @'
output=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output="$2"; shift 2 ;;
        *) shift ;;
    esac
done
mkdir -p "$(dirname "$output")"
printf '# skill\n' > "$output"
'@
        }
        $stubs = @{
            gh   = $GhStub
            curl = $CurlStub
        }
        foreach ($name in $AdditionalStubs.Keys) {
            $stubs[$name] = $AdditionalStubs[$name]
        }

        $envVars = @{
            COMMIT_JSON_PATH    = $commitJsonPath
            TREE_JSON_PATH      = $treeJsonPath
            UPSTREAM_REPO       = 'microsoft/hve-core'
            UPSTREAM_REF        = $UpstreamRef
            UPSTREAM_SKILLS_PATH = $UpstreamSkillsPath
            DEST_DIR            = $Destination
        }
        foreach ($name in $AdditionalEnv.Keys) {
            $envVars[$name] = $AdditionalEnv[$name]
        }

        $result = Invoke-BashEntryScript -ScriptPath $script:BootstrapScriptPath -WorkDir $Workspace -EnvVars $envVars -Stubs $stubs

        return [pscustomobject]@{
            Result      = $result
            Destination = $Destination
            Workspace   = $Workspace
        }
    }
}

Describe 'Get-PinnedHveCoreRef' -Tag 'Unit' {
    It 'Extracts the pinned SHA and release tag' {
        $ref = Get-PinnedHveCoreRef -Path $script:SetupPath
        $ref.Sha | Should -Be 'e69486a5f809ede45c63c0a31358c12912bd5168'
        $ref.Tag | Should -Be 'hve-core-v3.2.2'
    }

    It 'Returns null for a missing file' {
        Get-PinnedHveCoreRef -Path (Join-Path $TestDrive 'missing.yml') | Should -BeNullOrEmpty
    }

    It 'Returns a null Sha when HVE_CORE_DERIVED_FILES_REF is absent' {
        $p = Join-Path $TestDrive 'no-ref.yml'
        "env:`n  FOO: bar" | Set-Content -Path $p -Encoding utf8
        $ref = Get-PinnedHveCoreRef -Path $p
        $ref.Sha | Should -BeNullOrEmpty
        $ref.Tag | Should -Be 'unknown'
    }
}

Describe 'RPI bootstrap workflow contract' -Tag 'Contract' {
    BeforeAll {
        $script:CheckedInSetupPath = Join-Path $script:RepoRoot '.github/workflows/copilot-setup-steps.yml'
        $script:CheckedInSetup = Get-Content -Path $script:CheckedInSetupPath -Raw
    }

    It 'Pins the checked-in bootstrap to an immutable commit' {
        $ref = Get-PinnedHveCoreRef -Path $script:CheckedInSetupPath

        $ref.Sha | Should -Match '^[0-9a-f]{40}$'
        $ref.Sha | Should -Be 'e69486a5f809ede45c63c0a31358c12912bd5168'
        $ref.Tag | Should -Be 'hve-core-v3.2.2'
    }

    It 'Invokes the standalone bootstrap as a fatal step' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup
        $step = @($workflow.jobs.'copilot-setup-steps'.steps) |
            Where-Object { $_.name -eq 'Bootstrap hve-core RPI skills' }

        $step | Should -HaveCount 1
        $step.run | Should -Be 'bash scripts/ci/bootstrap-hve-core-rpi-skills.sh'
        $step.PSObject.Properties.Name | Should -Not -Contain 'continue-on-error'
    }

    It 'Runs when the standalone bootstrap changes' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup

        @($workflow.on.push.paths) | Should -Contain 'scripts/ci/bootstrap-hve-core-rpi-skills.sh'
        @($workflow.on.pull_request.paths) | Should -Contain 'scripts/ci/bootstrap-hve-core-rpi-skills.sh'
    }

    It 'Keeps the runtime destination gitignored and untracked' {
        $gitignore = Get-Content -Path (Join-Path $script:RepoRoot '.gitignore') -Raw
        $trackedFiles = @(git -C $script:RepoRoot ls-files -- '.github/skills/rpi')

        $gitignore | Should -Match '(?m)^\.github/skills/rpi/$'
        $gitignore | Should -Match '(?m)^\.github/skills/rpi\.backup\.\*$'
        $gitignore | Should -Match '(?m)^\.github/skills/rpi\.staging\.\*$'
        $trackedFiles | Should -BeNullOrEmpty
    }
}

Describe 'bootstrap-hve-core-rpi-skills.sh' -Tag 'Unit' {
    It 'Installs the complete verified tree and replaces stale content' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $destination = Join-Path $workspace '.github/skills/rpi'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        'stale' | Set-Content -Path (Join-Path $destination 'stale.md')

        $nestedPath = '.github/skills/rpi/rpi-plan/references/checklist.md'
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath $nestedPath
        ) -Workspace $workspace

        $run.Result.ExitCode | Should -Be 0
        Test-Path (Join-Path $destination 'stale.md') | Should -BeFalse
        foreach ($skill in $script:RequiredRpiSkills) {
            Test-Path (Join-Path $destination "$skill/SKILL.md") | Should -BeTrue
        }
        Test-Path (Join-Path $destination 'rpi-plan/references/checklist.md') | Should -BeTrue
        $audit = Get-Content -Path (Join-Path $destination '_audit.json') -Raw | ConvertFrom-Json
        $audit.upstream_repo | Should -Be 'microsoft/hve-core'
        $audit.requested_ref | Should -Be $script:ResolvedRpiSha
        $audit.resolved_sha | Should -Be $script:ResolvedRpiSha
        @($audit.files) | Should -Contain $nestedPath
        @($audit.files).Count | Should -Be ($script:RequiredRpiSkills.Count + 1)
        $run.Result.StdOut | Should -Match 'Installed 8 RPI skills and 9 verified files'
    }

    It 'Downloads every file from the resolved commit SHA' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson)
        $curlCalls = @($run.Result.Calls | Where-Object { $_ -like 'curl *' })

        $run.Result.ExitCode | Should -Be 0
        $curlCalls | Should -HaveCount $script:RequiredRpiSkills.Count
        foreach ($call in $curlCalls) {
            $call | Should -Match "https://raw\.githubusercontent\.com/microsoft/hve-core/$($script:ResolvedRpiSha)/\.github/skills/rpi/"
        }
    }

    It 'Rejects a truncated tree before downloading files' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson -Truncated)

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'tree response was truncated'
        @($run.Result.Calls | Where-Object { $_ -like 'curl *' }) | Should -BeNullOrEmpty
    }

    It 'Rejects unsupported Git file modes' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson -OverrideMode '120000')

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Unsupported RPI skill file mode or type'
    }

    It 'Rejects non-Markdown upstream files' {
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath '.github/skills/rpi/rpi-quick/run.sh'
        )

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Unsupported RPI skill file type'
    }

    It 'Rejects unsafe characters in upstream paths' {
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath '.github/skills/rpi/bad$name.md'
        )

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Unsafe characters in RPI skill path'
    }

    It 'Rejects a non-immutable resolved SHA' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -CommitSha 'abc'

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Invalid resolved SHA'
        @($run.Result.Calls | Where-Object { $_ -like 'curl *' }) | Should -BeNullOrEmpty
    }

    It 'Requires an upstream ref before calling GitHub' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -UpstreamRef ''

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'UPSTREAM_REF is required'
        @($run.Result.Calls | Where-Object { $_ -like 'gh *' }) | Should -BeNullOrEmpty
    }

    It 'Refuses a destination outside the runtime discovery leaf' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $destination = Join-Path $workspace '.github/skills/other'
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -Destination $destination

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Refusing nonstandard RPI destination'
        @($run.Result.Calls | Where-Object { $_ -like 'gh *' }) | Should -BeNullOrEmpty
    }

    It 'Rejects an empty upstream tree' {
        $run = Invoke-RpiBootstrap -TreeJson '{"truncated":false,"tree":[]}'

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'No RPI skill files discovered'
    }

    It 'Normalizes a trailing slash in the upstream skills path' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -UpstreamSkillsPath '.github/skills/rpi/'

        $run.Result.ExitCode | Should -Be 0
    }

    It 'Rejects a missing required skill' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson -OmitSkill 'rpi-review')

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Missing required RPI skill: rpi-review'
    }

    It 'Rejects unsafe upstream paths' {
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath '.github/skills/rpi/../outside.md'
        )

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Unsafe RPI skill path'
        Test-Path (Join-Path $run.Workspace '.github/skills/outside.md') | Should -BeFalse
    }

    It 'Rejects content that does not match the pinned blob SHA and preserves the prior install' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $destination = Join-Path $workspace '.github/skills/rpi'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        'prior' | Set-Content -Path (Join-Path $destination 'prior.md')

        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -OverrideSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        ) -Workspace $workspace

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'integrity check failed'
        Test-Path (Join-Path $destination 'prior.md') | Should -BeTrue
    }

    It 'Retries transient GitHub API failures' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $counterPath = Join-Path $workspace 'gh-counter'
        $ghStub = @'
count=0
[ ! -f "$GH_COUNTER_PATH" ] || count="$(cat "$GH_COUNTER_PATH")"
count=$((count + 1))
printf '%s' "$count" > "$GH_COUNTER_PATH"
case "$*" in
    *commits/*)
        [ "$count" -ge 3 ] || { echo transient >&2; exit 1; }
        cat "$COMMIT_JSON_PATH"
        ;;
    *git/trees/*) cat "$TREE_JSON_PATH" ;;
    *) exit 1 ;;
esac
'@
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -GhStub $ghStub -AdditionalEnv @{
            GH_COUNTER_PATH = $counterPath
        } -AdditionalStubs @{
            sleep = 'exit 0'
        }

        $run.Result.ExitCode | Should -Be 0
        @($run.Result.Calls | Where-Object { $_ -like 'gh api repos/*commits/*' }) | Should -HaveCount 3
        $run.Result.StdErr | Should -Match 'attempt 1/3 failed'
    }

    It 'Fails after exhausting GitHub API retries' {
        $ghStub = 'echo unavailable >&2; exit 1'
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -GhStub $ghStub -AdditionalStubs @{
            sleep = 'exit 0'
        }

        $run.Result.ExitCode | Should -Not -Be 0
        @($run.Result.Calls | Where-Object { $_ -like 'gh api repos/*commits/*' }) | Should -HaveCount 3
        $run.Result.StdErr | Should -Match 'attempt 3/3 failed'
        $run.Result.StdErr | Should -Match 'Failed to resolve'
    }

    It 'Restores the prior install when the final move fails' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $destination = Join-Path $workspace '.github/skills/rpi'
        $counterPath = Join-Path $workspace 'mv-counter'
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        'prior' | Set-Content -Path (Join-Path $destination 'prior.md')
        $mvStub = @'
count=0
[ ! -f "$MV_COUNTER_PATH" ] || count="$(cat "$MV_COUNTER_PATH")"
count=$((count + 1))
printf '%s' "$count" > "$MV_COUNTER_PATH"
[ "$count" -ne 2 ] || exit 1
/bin/mv "$@"
'@
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -AdditionalEnv @{
            MV_COUNTER_PATH = $counterPath
        } -AdditionalStubs @{
            mv = $mvStub
        }

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Failed to install RPI skills'
        Test-Path (Join-Path $destination 'prior.md') | Should -BeTrue
        @(Get-ChildItem (Split-Path $destination) -Filter 'rpi.backup.*') | Should -BeNullOrEmpty
    }
}

Describe 'Select-LatestRelease' -Tag 'Unit' {
    It 'Picks the newest non-draft release by created_at' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'old'; draft = $false; created_at = '2026-01-01T00:00:00Z'; html_url = 'u-old' }
            [pscustomobject]@{ tag_name = 'newest'; draft = $false; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-new' }
            [pscustomobject]@{ tag_name = 'mid'; draft = $false; created_at = '2026-02-01T00:00:00Z'; html_url = 'u-mid' }
        )
        (Select-LatestRelease -Releases $releases).tag_name | Should -Be 'newest'
    }

    It 'Skips drafts even when a draft is newer' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'stable'; draft = $false; created_at = '2026-02-01T00:00:00Z'; html_url = 'u-stable' }
            [pscustomobject]@{ tag_name = 'draft'; draft = $true; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-draft' }
        )
        (Select-LatestRelease -Releases $releases).tag_name | Should -Be 'stable'
    }

    It 'Returns null when there are no non-draft releases' {
        $releases = @(
            [pscustomobject]@{ tag_name = 'draft'; draft = $true; created_at = '2026-03-01T00:00:00Z'; html_url = 'u-draft' }
        )
        Select-LatestRelease -Releases $releases | Should -BeNullOrEmpty
    }

    It 'Returns null for an empty collection' {
        Select-LatestRelease -Releases @() | Should -BeNullOrEmpty
    }
}

Describe 'Get-DriftState' -Tag 'Unit' {
    It 'Returns current when pinned and latest upstream SHAs match' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns current when SHAs match case-insensitively' {
        Get-DriftState -PinnedUpstreamSha 'ABC123' -LatestUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns drift when SHAs differ' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha 'def456' | Should -Be 'drift'
    }

    It 'Returns missing-upstream when the latest upstream SHA is empty' {
        Get-DriftState -PinnedUpstreamSha 'abc123' -LatestUpstreamSha '' | Should -Be 'missing-upstream'
    }
}

Describe 'Get-HveCoreBlobSha' -Tag 'Unit' {
    It 'Returns the trimmed blob SHA on success' {
        Mock gh { $global:LASTEXITCODE = 0; 'abc123def456' }
        Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' | Should -Be 'abc123def456'
    }

    It 'Returns empty string on a genuine 404 (file absent at ref)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
        Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' | Should -Be ''
    }

    It 'Throws on a non-404 gh api failure (transient/auth/rate limit)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Internal Server Error (HTTP 500)' }
        { Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' } | Should -Throw
    }
}

Describe 'Resolve-HveCoreCommitSha' -Tag 'Unit' {
    It 'Returns the trimmed commit SHA when the ref resolves' {
        Mock gh { $global:LASTEXITCODE = 0; 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef' }
        Resolve-HveCoreCommitSha -Repo 'o/r' -Ref 'v1' | Should -Be 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    }

    It 'Throws when the ref does not resolve (invalid tag or pinned SHA)' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
        { Resolve-HveCoreCommitSha -Repo 'o/r' -Ref 'definitely-not-a-ref' } | Should -Throw
    }
}

Describe 'Get-HveCoreFileDrift' -Tag 'Unit' {
    It 'Reports drift when the upstream blob changed between refs' {
        Mock gh {
            $global:LASTEXITCODE = 0
            if ("$args" -match 'ref=PIN') { 'aaaaaaa' } else { 'bbbbbbb' }
        }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST'
        $r.State | Should -Be 'drift'
        $r.Drift | Should -BeTrue
        $r.PinnedUpstreamSha | Should -Be 'aaaaaaa'
        $r.LatestUpstreamSha | Should -Be 'bbbbbbb'
    }

    It 'Reports current when the upstream blob is unchanged' {
        Mock gh { $global:LASTEXITCODE = 0; 'samesha' }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST'
        $r.State | Should -Be 'current'
        $r.Drift | Should -BeFalse
    }

    It 'Reports missing-upstream when the file is absent at the latest ref' {
        Mock gh {
            if ("$args" -match 'ref=LATEST') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'aaaaaaa' }
        }
        (Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -PinnedRef 'PIN' -LatestRef 'LATEST').State | Should -Be 'missing-upstream'
    }
}

Describe 'Format-HveCoreIssueBody' -Tag 'Unit' {
    It 'Formats the issue body with correct markers and links' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match '<!-- automation:hve-core-freshness -->'
        $body | Should -Match 'hve-core-v9'
        $body | Should -Match 'scripts/x\.psm1'
        $body | Should -Match 'compare/hve-core-v1\.\.\.hve-core-v9'
        $body | Should -Not -Match '[Pp]ersona'
    }

    It 'Falls back to the pinned SHA in the compare link when the tag is unknown' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            Pin = [pscustomobject]@{
                PinnedTag = 'unknown'
                PinnedSha = 'abcdef1234567890'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match 'compare/abcdef1234567890\.\.\.hve-core-v9'
        $body | Should -Not -Match 'compare/unknown'
        $body | Should -Match 'Drift baseline: `abcdef1234567890`'
    }
}

Describe 'Format-HveCoreJobSummary' -Tag 'Unit' {
    It 'Formats the job summary correctly' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'http://u'
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path = 'scripts/x.psm1'
                    PinnedUpstreamSha = '1111111'
                    LatestUpstreamSha = '2222222'
                    Drift = $true
                    State = 'drift'
                }
            )
        }
        $summary = Format-HveCoreJobSummary -Result $r

        $summary | Should -Match 'hve-core-v9'
        $summary | Should -Match 'scripts/x\.psm1'
        $summary | Should -Match '⚠️ Upstream advanced'
        $summary | Should -Match '\| Pinned blob \| Latest blob \|'
        $summary | Should -Match '\| 1111111 \| 2222222 \|'
    }

    It 'Uses the pinned SHA when the bootstrap ref has no release tag' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            Pin = [pscustomobject]@{
                PinnedTag = 'unknown'
                PinnedSha = 'abcdef1234567890'
            }
            Files = @()
        }

        (Format-HveCoreJobSummary -Result $r) | Should -Match 'Drift baseline: abcdef1234567890'
    }
}
