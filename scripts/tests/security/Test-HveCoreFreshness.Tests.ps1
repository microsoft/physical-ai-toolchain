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
            [string]$OverrideSha,
            [string]$ExtraSha
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
                sha  = if ($ExtraSha) { $ExtraSha } else { $blobSha }
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
            [string]$SkillsRoot,
            [string]$GhStub,
            [string]$CurlStub,
            [hashtable]$AdditionalEnv = @{},
            [hashtable]$AdditionalStubs = @{}
        )

        $commitJsonPath = Join-Path $Workspace 'commit.json'
        $treeJsonPath = Join-Path $Workspace 'tree.json'
        if (-not $SkillsRoot) {
            $SkillsRoot = Join-Path $Workspace '.github/skills'
        }
        New-Item -ItemType Directory -Path $SkillsRoot -Force | Out-Null
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
            SKILLS_ROOT         = $SkillsRoot
        }
        foreach ($name in $AdditionalEnv.Keys) {
            $envVars[$name] = $AdditionalEnv[$name]
        }

        $result = Invoke-BashEntryScript -ScriptPath $script:BootstrapScriptPath -WorkDir $Workspace -EnvVars $envVars -Stubs $stubs

        return [pscustomobject]@{
            Result     = $result
            SkillsRoot = $SkillsRoot
            Workspace  = $Workspace
        }
    }
}

Describe 'DerivedFiles configuration' -Tag 'Unit' {
    It 'Tracks the complete derived-file manifest' {
        $expected = @(
            'scripts/security/Modules/SecurityHelpers.psm1|release'
            'scripts/security/Modules/SecurityClasses.psm1|release'
            'scripts/linting/Modules/LintingHelpers.psm1|release'
            'scripts/tests/Mocks/GitMocks.psm1|release'
            'scripts/lib/Modules/CIHelpers.psm1|release'
            'scripts/linting/Modules/FrontmatterValidation.psm1|release'
            'scripts/security/Test-WorkflowPermissions.ps1|source-header'
            'scripts/security/Test-DangerousWorkflow.ps1|source-header'
        )
        $actual = @($script:DerivedFiles | ForEach-Object { "$($_.Path)|$($_.Baseline)" })

        $actual | Should -Be $expected
    }

    It 'Tracks both security linters with source-header baselines' {
        $sourceFiles = @($script:DerivedFiles | Where-Object { $_.Baseline -eq 'source-header' })

        $sourceFiles.Count | Should -Be 2
        $sourceFiles.Path | Should -Contain 'scripts/security/Test-WorkflowPermissions.ps1'
        $sourceFiles.Path | Should -Contain 'scripts/security/Test-DangerousWorkflow.ps1'
    }

    It 'Parses the provenance header of every source-header entry' {
        $sourceFiles = @($script:DerivedFiles | Where-Object { $_.Baseline -eq 'source-header' })

        foreach ($file in $sourceFiles) {
            $source = Get-HveCoreFileSource -Path (Join-Path $script:RepoRoot $file.Path)
            $source.Path | Should -Be $file.Path
            $source.Sha | Should -Match '^[0-9a-f]{40}$'
        }
    }

    It 'Tracks every security script with an hve-core provenance header' {
        $provenancePattern = 'Adapted from\s+microsoft/hve-core\s+\S+\s+as of commit\s+[0-9a-fA-F]{40}'
        $securityRoot = Join-Path $script:RepoRoot 'scripts/security'
        $provenanceFiles = @(Get-ChildItem -Path $securityRoot -Filter '*.ps1' -Recurse |
                Where-Object { (Get-Content -Path $_.FullName -Raw) -match $provenancePattern } |
                ForEach-Object { [IO.Path]::GetRelativePath($script:RepoRoot, $_.FullName).Replace('\', '/') } |
                Sort-Object)
        $sourceFiles = @($script:DerivedFiles |
                Where-Object { $_.Baseline -eq 'source-header' } |
                ForEach-Object { $_.Path } |
                Sort-Object)

        $sourceFiles | Should -Be $provenanceFiles
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

    It 'Selects the derived-files ref when the runtime pin is also present' {
        $p = Join-Path $TestDrive 'two-refs.yml'
        @"
env:
  UPSTREAM_REF: $($script:ResolvedRpiSha)
  # microsoft/hve-core derived-files release: hve-core-v3.2.2
  HVE_CORE_DERIVED_FILES_REF: e69486a5f809ede45c63c0a31358c12912bd5168
"@ | Set-Content -Path $p -Encoding utf8

        $ref = Get-PinnedHveCoreRef -Path $p

        $ref.Sha | Should -Be 'e69486a5f809ede45c63c0a31358c12912bd5168'
    }

    It 'Rejects a shortened or suffixed HVE_CORE_DERIVED_FILES_REF' {
        $p = Join-Path $TestDrive 'invalid-ref.yml'
        "env:`n  HVE_CORE_DERIVED_FILES_REF: deadbeef-fix" | Set-Content -Path $p -Encoding utf8

        (Get-PinnedHveCoreRef -Path $p).Sha | Should -BeNullOrEmpty
    }

    It 'Returns unknown for an invalid release tag' {
        $p = Join-Path $TestDrive 'invalid-tag.yml'
        "env:`n  # microsoft/hve-core derived-files release: bad](tag`n  HVE_CORE_DERIVED_FILES_REF: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" |
            Set-Content -Path $p -Encoding utf8

        (Get-PinnedHveCoreRef -Path $p).Tag | Should -Be 'unknown'
    }
}

Describe 'RPI bootstrap workflow contract' -Tag 'Contract' {
    BeforeAll {
        $script:CheckedInSetupPath = Join-Path $script:RepoRoot '.github/workflows/copilot-setup-steps.yml'
        $script:CheckedInSetup = Get-Content -Path $script:CheckedInSetupPath -Raw
    }

    It 'Pins the checked-in runtime bootstrap to an immutable commit' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup
        $step = @($workflow.jobs.'copilot-setup-steps'.steps) |
            Where-Object { $_.name -eq 'Bootstrap hve-core RPI skills' }
        $runtimeRef = $step.env.UPSTREAM_REF

        $runtimeRef | Should -Match '^[0-9a-f]{40}$'
        $runtimeRef | Should -Be $script:ResolvedRpiSha
    }

    It 'Exposes a parseable derived-files drift baseline' {
        $ref = Get-PinnedHveCoreRef -Path $script:CheckedInSetupPath

        $ref.Sha | Should -Match '^[0-9a-f]{40}$'
        $ref.Tag | Should -Match '^hve-core-v'
    }

    It 'Invokes the standalone bootstrap as a fatal step' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup
        $step = @($workflow.jobs.'copilot-setup-steps'.steps) |
            Where-Object { $_.name -eq 'Bootstrap hve-core RPI skills' }

        $step | Should -HaveCount 1
        $step.run | Should -Be 'bash scripts/ci/bootstrap-hve-core-rpi-skills.sh'
        $step.PSObject.Properties.Name | Should -Not -Contain 'continue-on-error'
    }

    It 'Runs the external-content bootstrap after all other setup steps' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup
        $steps = @($workflow.jobs.'copilot-setup-steps'.steps)

        $steps[-1].name | Should -Be 'Bootstrap hve-core RPI skills'
    }

    It 'Runs when the standalone bootstrap changes' {
        $workflow = ConvertFrom-Yaml $script:CheckedInSetup

        @($workflow.on.push.paths) | Should -Contain 'scripts/ci/bootstrap-hve-core-rpi-skills.sh'
        @($workflow.on.pull_request.paths) | Should -Contain 'scripts/ci/bootstrap-hve-core-rpi-skills.sh'
    }

    It 'Keeps the runtime destination gitignored and untracked' {
        $gitignore = Get-Content -Path (Join-Path $script:RepoRoot '.gitignore') -Raw
        $trackedFiles = @(git -C $script:RepoRoot ls-files -- '.github/skills/rpi-*')

        $gitignore | Should -Match '(?m)^\.github/skills/rpi-\*/$'
        $gitignore | Should -Match '(?m)^\.github/skills/\.rpi-audit\.json$'
        $gitignore | Should -Match '(?m)^\.github/skills/\.rpi-backup\.\*$'
        $gitignore | Should -Match '(?m)^\.github/skills/\.rpi-staging\.\*$'
        $trackedFiles | Should -BeNullOrEmpty
    }
}

Describe 'bootstrap-hve-core-rpi-skills.sh' -Tag 'Unit' {
    It 'Installs the complete verified tree and replaces stale content' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $skillsRoot = Join-Path $workspace '.github/skills'
        $staleSkill = Join-Path $skillsRoot 'rpi-plan'
        New-Item -ItemType Directory -Path $staleSkill -Force | Out-Null
        'stale' | Set-Content -Path (Join-Path $staleSkill 'stale.md')

        $nestedPath = '.github/skills/rpi/rpi-plan/references/checklist.md'
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath $nestedPath
        ) -Workspace $workspace

        $run.Result.ExitCode | Should -Be 0
        Test-Path (Join-Path $staleSkill 'stale.md') | Should -BeFalse
        foreach ($skill in $script:RequiredRpiSkills) {
            Test-Path (Join-Path $skillsRoot "$skill/SKILL.md") | Should -BeTrue
        }
        Test-Path (Join-Path $skillsRoot 'rpi-plan/references/checklist.md') | Should -BeTrue
        $audit = Get-Content -Path (Join-Path $skillsRoot '.rpi-audit.json') -Raw | ConvertFrom-Json
        $audit.upstream_repo | Should -Be 'microsoft/hve-core'
        $audit.requested_ref | Should -Be $script:ResolvedRpiSha
        $audit.resolved_sha | Should -Be $script:ResolvedRpiSha
        @($audit.files.path) | Should -Contain $nestedPath
        @($audit.files.blob_sha) | Should -Not -Contain $null
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
        $run.Result.StdErr | Should -Match 'Unsupported characters in RPI skill path'
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

    It 'Rejects a mutable upstream ref before calling GitHub' {
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -UpstreamRef 'main'

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'UPSTREAM_REF must be a 40-character lowercase commit SHA'
        @($run.Result.Calls | Where-Object { $_ -like 'gh *' }) | Should -BeNullOrEmpty
    }

    It 'Refuses a skills root outside the runtime discovery directory' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $skillsRoot = Join-Path $workspace '.github/other'
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -SkillsRoot $skillsRoot

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Refusing nonstandard skills root'
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
        $skillsRoot = Join-Path $workspace '.github/skills'
        $priorSkill = Join-Path $skillsRoot 'rpi-quick'
        New-Item -ItemType Directory -Path $priorSkill -Force | Out-Null
        'prior' | Set-Content -Path (Join-Path $priorSkill 'prior.md')

        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -OverrideSha 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        ) -Workspace $workspace

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'integrity check failed'
        Test-Path (Join-Path $priorSkill 'prior.md') | Should -BeTrue
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-staging.*') | Should -BeNullOrEmpty
    }

    It 'Rejects unexpected top-level RPI content' {
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath '.github/skills/rpi/README.md'
        )

        $run.Result.ExitCode | Should -Not -Be 0
        $run.Result.StdErr | Should -Match 'Unexpected top-level RPI skill path'
    }

    It 'Accepts empty ancillary Markdown files' {
        $emptyBlobSha = (& bash -c "printf '' | git hash-object --stdin").Trim()
        $extraPath = '.github/skills/rpi/rpi-plan/references/empty.md'
        $curlStub = @'
output=""
url=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output="$2"; shift 2 ;;
        http*) url="$1"; shift ;;
        *) shift ;;
    esac
done
mkdir -p "$(dirname "$output")"
case "$url" in
    */references/empty.md) : > "$output" ;;
    *) printf '# skill\n' > "$output" ;;
esac
'@
        $run = Invoke-RpiBootstrap -TreeJson (
            New-RpiTreeJson -ExtraPath $extraPath -ExtraSha $emptyBlobSha
        ) -CurlStub $curlStub

        $run.Result.ExitCode | Should -Be 0
        (Get-Item (Join-Path $run.SkillsRoot 'rpi-plan/references/empty.md')).Length | Should -Be 0
        $audit = Get-Content -Path (Join-Path $run.SkillsRoot '.rpi-audit.json') -Raw | ConvertFrom-Json
        @($audit.files.path) | Should -Contain $extraPath
    }

    It 'Preserves prior content when a download fails' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $skillsRoot = Join-Path $workspace '.github/skills'
        $priorSkill = Join-Path $skillsRoot 'rpi-quick'
        New-Item -ItemType Directory -Path $priorSkill -Force | Out-Null
        'prior' | Set-Content -Path (Join-Path $priorSkill 'prior.md')

        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -CurlStub 'exit 22'

        $run.Result.ExitCode | Should -Not -Be 0
        Test-Path (Join-Path $priorSkill 'prior.md') | Should -BeTrue
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-staging.*') | Should -BeNullOrEmpty
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
        $skillsRoot = Join-Path $workspace '.github/skills'
        $priorSkill = Join-Path $skillsRoot 'rpi-quick'
        $counterPath = Join-Path $workspace 'mv-counter'
        New-Item -ItemType Directory -Path $priorSkill -Force | Out-Null
        'prior' | Set-Content -Path (Join-Path $priorSkill 'prior.md')
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
        $run.Result.StdErr | Should -Match 'Failed to install RPI artifact'
        Test-Path (Join-Path $priorSkill 'prior.md') | Should -BeTrue
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-backup.*') | Should -BeNullOrEmpty
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-staging.*') | Should -BeNullOrEmpty
    }

    It 'Restores all prior artifacts when the audit install fails' {
        $workspace = Join-Path $TestDrive ([guid]::NewGuid().ToString('N'))
        $skillsRoot = Join-Path $workspace '.github/skills'
        $counterPath = Join-Path $workspace 'mv-counter'
        foreach ($skill in $script:RequiredRpiSkills) {
            $priorSkill = Join-Path $skillsRoot $skill
            New-Item -ItemType Directory -Path $priorSkill -Force | Out-Null
            "prior-$skill" | Set-Content -Path (Join-Path $priorSkill 'prior.md')
        }
        '{"prior":true}' | Set-Content -Path (Join-Path $skillsRoot '.rpi-audit.json')
        $mvStub = @'
count=0
[ ! -f "$MV_COUNTER_PATH" ] || count="$(cat "$MV_COUNTER_PATH")"
count=$((count + 1))
printf '%s' "$count" > "$MV_COUNTER_PATH"
[ "$count" -ne 18 ] || exit 1
/bin/mv "$@"
'@
        $run = Invoke-RpiBootstrap -TreeJson (New-RpiTreeJson) -Workspace $workspace -AdditionalEnv @{
            MV_COUNTER_PATH = $counterPath
        } -AdditionalStubs @{
            mv = $mvStub
        }

        $run.Result.ExitCode | Should -Not -Be 0
        foreach ($skill in $script:RequiredRpiSkills) {
            Test-Path (Join-Path $skillsRoot "$skill/prior.md") | Should -BeTrue
        }
        (Get-Content -Path (Join-Path $skillsRoot '.rpi-audit.json') -Raw) | Should -Match '"prior":true'
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-backup.*') | Should -BeNullOrEmpty
        @(Get-ChildItem $skillsRoot -Force -Directory -Filter '.rpi-staging.*') | Should -BeNullOrEmpty
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

Describe 'Get-HveCoreFileSource' -Tag 'Unit' {
    It 'Extracts the source path and normalizes the commit SHA' {
        $path = Join-Path $TestDrive 'derived.ps1'
        @'
<#
Adapted from microsoft/hve-core scripts/security/Test-DangerousWorkflow.ps1
as of commit ABCDEF1234567890ABCDEF1234567890ABCDEF12.
#>
'@ | Set-Content -Path $path -Encoding utf8

        $source = Get-HveCoreFileSource -Path $path

        $source.Path | Should -Be 'scripts/security/Test-DangerousWorkflow.ps1'
        $source.Sha | Should -Be 'abcdef1234567890abcdef1234567890abcdef12'
    }

    It 'Accepts a source path outside scripts/security' {
        $path = Join-Path $TestDrive 'derived.psm1'
        @'
<#
Adapted from microsoft/hve-core scripts/linting/Modules/Example.psm1
as of commit ABCDEF1234567890ABCDEF1234567890ABCDEF12.
#>
'@ | Set-Content -Path $path -Encoding utf8

        (Get-HveCoreFileSource -Path $path).Path | Should -Be 'scripts/linting/Modules/Example.psm1'
    }

    It 'Throws when multiple source headers are present' {
        $path = Join-Path $TestDrive 'duplicate-header.ps1'
        @'
<#
Adapted from microsoft/hve-core scripts/security/First.ps1 as of commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.
Adapted from microsoft/hve-core scripts/security/Second.ps1 as of commit bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.
#>
'@ | Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*multiple*'
    }

    It 'Throws when the source header is missing' {
        $path = Join-Path $TestDrive 'missing-header.ps1'
        '<# no provenance header #>' | Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*Could not extract*'
    }

    It 'Ignores provenance text outside the comment-based help header' {
        $path = Join-Path $TestDrive 'body-provenance.ps1'
        @'
<#
.SYNOPSIS
    No provenance record.
#>
Write-Host 'Adapted from microsoft/hve-core scripts/security/Fake.ps1 as of commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.'
'@ | Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*Could not extract*'
    }

    It 'Rejects a truncated commit SHA' {
        $path = Join-Path $TestDrive 'short-sha.ps1'
        '<# Adapted from microsoft/hve-core scripts/security/Fake.ps1 as of commit abcdef1. #>' |
            Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*Could not extract*'
    }

    It 'Rejects a provenance header from another repository' {
        $path = Join-Path $TestDrive 'foreign-repo.ps1'
        '<# Adapted from example/hve-core scripts/security/Fake.ps1 as of commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa. #>' |
            Set-Content -Path $path -Encoding utf8

        { Get-HveCoreFileSource -Path $path } | Should -Throw '*Could not extract*'
    }

    It 'Throws when the file does not exist' {
        { Get-HveCoreFileSource -Path (Join-Path $TestDrive 'missing.ps1') } | Should -Throw '*not found locally*'
    }

    It 'Classifies an empty file as a validation failure' {
        $path = Join-Path $TestDrive 'empty.ps1'
        '' | Set-Content -Path $path -NoNewline

        { Get-HveCoreFileSource -Path $path } | Should -Throw -ExceptionType ([HveCoreFileValidationException])
    }
}

Describe 'Get-DriftState' -Tag 'Unit' {
    It 'Returns current when baseline and target upstream SHAs match' {
        Get-DriftState -BaselineUpstreamSha 'abc123' -TargetUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns current when SHAs match case-insensitively' {
        Get-DriftState -BaselineUpstreamSha 'ABC123' -TargetUpstreamSha 'abc123' | Should -Be 'current'
    }

    It 'Returns drift when SHAs differ' {
        Get-DriftState -BaselineUpstreamSha 'abc123' -TargetUpstreamSha 'def456' | Should -Be 'drift'
    }

    It 'Returns missing-target when the target upstream SHA is empty' {
        Get-DriftState -BaselineUpstreamSha 'abc123' -TargetUpstreamSha '' | Should -Be 'missing-target'
    }

    It 'Returns missing-baseline when the baseline upstream SHA is empty' {
        Get-DriftState -BaselineUpstreamSha '' -TargetUpstreamSha 'abc123' | Should -Be 'missing-baseline'
    }

    It 'Returns missing-both when neither upstream SHA exists' {
        Get-DriftState -BaselineUpstreamSha '' -TargetUpstreamSha '' | Should -Be 'missing-both'
    }
}

Describe 'Get-HveCoreBlobSha' -Tag 'Unit' {
    It 'Returns the trimmed blob SHA on success' {
        Mock gh { $global:LASTEXITCODE = 0; 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' |
            Should -Be 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    }

    It 'Throws when a successful response is not exactly one blob SHA' {
        Mock gh { $global:LASTEXITCODE = 0; "warning`naaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }

        { Get-HveCoreBlobSha -Repo 'o/r' -Path 'p' -Ref 'ref' } | Should -Throw '*invalid blob SHA*'
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

    It 'Throws when a successful response is not exactly one commit SHA' {
        Mock gh { $global:LASTEXITCODE = 0; "warning`ndeadbeefdeadbeefdeadbeefdeadbeefdeadbeef" }

        { Resolve-HveCoreCommitSha -Repo 'o/r' -Ref 'v1' } | Should -Throw '*invalid commit SHA*'
    }
}

Describe 'Get-HveCoreFileDrift' -Tag 'Unit' {
    It 'Reports drift when the upstream blob changed between refs' {
        Mock gh {
            $global:LASTEXITCODE = 0
            if ("$args" -match 'ref=PIN') { 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
            else { 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }
        }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'PIN' -TargetRef 'LATEST'
        $r.State | Should -Be 'drift'
        $r.Drift | Should -BeTrue
        $r.BaselineUpstreamSha | Should -Be 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $r.TargetUpstreamSha | Should -Be 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        $r.BaselineRef | Should -Be 'PIN'
        $r.TargetRef | Should -Be 'LATEST'
        $r.ComparisonUrl | Should -Be 'https://github.com/o/r/compare/PIN...LATEST'
    }

    It 'Reports current when the upstream blob is unchanged' {
        Mock gh { $global:LASTEXITCODE = 0; 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'PIN' -TargetRef 'LATEST'
        $r.State | Should -Be 'current'
        $r.Drift | Should -BeFalse
    }

    It 'Reports missing-target when the file is absent at the target ref' {
        Mock gh {
            if ("$args" -match 'ref=LATEST') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        }
        (Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'PIN' -TargetRef 'LATEST').State | Should -Be 'missing-target'
    }

    It 'Reports missing-baseline when the file is absent at the baseline ref' {
        Mock gh {
            if ("$args" -match 'ref=PIN') { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }
            else { $global:LASTEXITCODE = 0; 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }
        }

        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'PIN' -TargetRef 'LATEST'
        $r.State | Should -Be 'missing-baseline'
        $r.Drift | Should -BeTrue
    }

    It 'Classifies a path missing at both refs as a validation failure' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: Not Found (HTTP 404)' }

        {
            Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'PIN' -TargetRef 'LATEST'
        } | Should -Throw -ExceptionType ([HveCoreFileValidationException])
    }

    It 'URL-encodes refs in comparison links' {
        Mock gh { $global:LASTEXITCODE = 0; 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }

        $r = Get-HveCoreFileDrift -Repo 'o/r' -Path 'p' -BaselineRef 'v1](https://evil.example)' -TargetRef 'main'

        $r.ComparisonUrl | Should -Not -Match '\]\('
        $r.ComparisonUrl | Should -Match 'v1%5D%28https%3A%2F%2Fevil\.example%29'
    }
}

Describe 'Get-HveCoreFileDriftForBaseline' -Tag 'Unit' {
    BeforeEach {
        Mock Test-Path { $true }
    }

    It 'Uses the source-header SHA and resolved main SHA' {
        $path = 'scripts/security/Test-DangerousWorkflow.ps1'
        $sourceSha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $mainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'source-header' }
        Mock Get-HveCoreFileSource { [pscustomobject]@{ Path = $path; Sha = $sourceSha } }
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha $mainSha

        Should -Invoke Get-HveCoreFileDrift -Times 1 -Exactly -ParameterFilter {
            $Path -eq $path -and $BaselineRef -eq $sourceSha -and $TargetRef -eq $mainSha
        }
    }

    It 'Uses the release pin and resolved latest release SHA' {
        $path = 'scripts/security/Modules/SecurityHelpers.psm1'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'release' }
        Mock Get-HveCoreFileSource { throw 'source parser must not run' }
        Mock Get-HveCoreFileDrift { [pscustomobject]@{ State = 'current'; Drift = $false } }

        $null = Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'

        Should -Invoke Get-HveCoreFileDrift -Times 1 -Exactly -ParameterFilter {
            $Path -eq $path -and $BaselineRef -eq 'pin' -and $TargetRef -eq 'release-sha'
        }
        Should -Invoke Get-HveCoreFileSource -Times 0 -Exactly
    }

    It 'Throws when the recorded upstream path differs from the local path' {
        $file = [pscustomobject]@{
            Path = 'scripts/security/Test-DangerousWorkflow.ps1'
            Baseline = 'source-header'
        }
        Mock Get-HveCoreFileSource {
            [pscustomobject]@{
                Path = 'scripts/security/Other.ps1'
                Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }
        }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'
        } | Should -Throw '*must match its recorded*'
    }

    It 'Rejects a case-mismatched recorded upstream path' {
        $file = [pscustomobject]@{
            Path = 'scripts/security/Test-DangerousWorkflow.ps1'
            Baseline = 'source-header'
        }
        Mock Get-HveCoreFileSource {
            [pscustomobject]@{
                Path = 'scripts/security/test-dangerousworkflow.ps1'
                Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }
        }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'
        } | Should -Throw '*must match its recorded*'
    }

    It 'Throws on an unsupported baseline' {
        $file = [pscustomobject]@{ Path = 'scripts/security/Test-DangerousWorkflow.ps1'; Baseline = 'bogus' }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'
        } | Should -Throw '*Unsupported hve-core baseline*'
    }

    It 'Throws when the derived file is missing locally' {
        Mock Test-Path { $false }
        $file = [pscustomobject]@{ Path = 'scripts/security/Missing.ps1'; Baseline = 'release' }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'
        } | Should -Throw '*not found locally*'
    }

    It 'Rejects a source header whose recorded path is absent at its recorded commit' {
        $path = 'scripts/security/Test-DangerousWorkflow.ps1'
        $file = [pscustomobject]@{ Path = $path; Baseline = 'source-header' }
        Mock Get-HveCoreFileSource {
            [pscustomobject]@{ Path = $path; Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        }
        Mock Get-HveCoreFileDrift {
            [pscustomobject]@{ State = 'missing-baseline'; Drift = $true }
        }

        {
            Get-HveCoreFileDriftForBaseline -File $file -PinnedReleaseSha 'pin' -LatestReleaseSha 'release-sha' -LatestMainSha 'main'
        } | Should -Throw '*absent at its recorded commit*'
    }
}

Describe 'Format-HveCoreDriftCells' -Tag 'Unit' {
    It 'Renders missing-target records with short and empty SHAs' {
        $file = [pscustomobject]@{
            Baseline            = 'source-header'
            BaselineUpstreamSha = 'abc'
            TargetUpstreamSha   = ''
            BaselineRef         = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
            TargetRef           = 'main'
            State               = 'missing-target'
            ComparisonUrl       = ''
        }

        $cells = Format-HveCoreDriftCells -File $file

        $cells.Baseline | Should -Be 'Source header'
        $cells.BaselineSha | Should -Be 'abc'
        $cells.TargetSha | Should -BeExactly ''
        $cells.Comparison | Should -BeExactly ''
        $cells.Status | Should -Match 'Not found at target ref'
    }

    It 'Escapes error details for markdown output' {
        $file = [pscustomobject]@{
            Baseline            = 'release'
            BaselineUpstreamSha = ''
            TargetUpstreamSha   = ''
            BaselineRef         = ''
            TargetRef           = ''
            State               = 'error'
            Error               = "bad | value`nnext"
            ComparisonUrl       = ''
        }

        (Format-HveCoreDriftCells -File $file).Status | Should -Be '❌ Check failed — <code>bad &#124; value next</code>'
    }

    It 'Escapes ref text in comparison links' {
        $file = [pscustomobject]@{
            Baseline            = 'release'
            BaselineUpstreamSha = 'abc123'
            TargetUpstreamSha   = 'def456'
            BaselineRef         = 'v1](https://evil.example)'
            TargetRef           = 'main'
            State               = 'drift'
            ComparisonUrl       = 'https://github.com/o/r/compare/v1%5D%28https%3A%2F%2Fevil.example%29...main'
        }

        $comparison = (Format-HveCoreDriftCells -File $file).Comparison

        $comparison | Should -Not -Match '\]\(https://evil'
        $comparison | Should -Match 'v1&#93;&#40;https://evil\.example&#41;'
    }

    It 'Escapes an unrecognized baseline label' {
        $file = [pscustomobject]@{
            Baseline            = 'weird](https://evil.example)'
            BaselineUpstreamSha = ''
            TargetUpstreamSha   = ''
            BaselineRef         = ''
            TargetRef           = ''
            State               = 'error'
            ComparisonUrl       = ''
        }

        $baseline = (Format-HveCoreDriftCells -File $file).Baseline

        $baseline | Should -Match '&#93;&#40;'
        $baseline | Should -Not -Match '\]\(https://evil'
    }
}

Describe 'ConvertTo-HveCoreMarkdownText' -Tag 'Unit' {
    It 'Encodes HTML and markdown metacharacters exactly once' {
        $encoded = ConvertTo-HveCoreMarkdownText -Value '<b>a|b`c[d](e)</b>'

        $encoded | Should -Be '&lt;b&gt;a&#124;b&#96;c&#91;d&#93;&#40;e&#41;&lt;/b&gt;'
        $encoded | Should -Not -Match '&amp;#'
    }
}

Describe 'Format-HveCoreIssueBody' -Tag 'Unit' {
    It 'Formats the issue body with correct markers and links' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'https://github.com/microsoft/hve-core/releases/tag/hve-core-v9'
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            DriftCount = 1
            ErrorCount = 0
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path                = 'scripts/x.psm1'
                    Baseline            = 'release'
                    BaselineUpstreamSha = '1111111'
                    TargetUpstreamSha   = '2222222'
                    BaselineRef         = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
                    TargetRef           = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
                    ComparisonUrl       = 'https://github.com/microsoft/hve-core/compare/a...b'
                    Drift               = $true
                    State               = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match '<!-- automation:hve-core-freshness -->'
        $body | Should -Match 'hve-core-v9'
        $body | Should -Match 'scripts/x\.psm1'
        $body | Should -Match 'compare/hve-core-v1\.\.\.hve-core-v9'
        $body | Should -Match 'Release-file baseline: `hve-core-v1` \(`HVE_CORE_DERIVED_FILES_REF`'
        $body | Should -Match '\[aaaaaaa → bbbbbbb\]\(https://github\.com/microsoft/hve-core/compare/a\.\.\.b\)'
        $body | Should -Match 'Action required: 1 drifted, 0 check errors'
        $body | Should -Match '\| File \| Baseline \| Upstream comparison \| Baseline upstream blob \| Target upstream blob \| Status \|'
        $body | Should -Not -Match '[Pp]ersona'
    }

    It 'Falls back to the pinned SHA in the compare link when the tag is unknown' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'https://github.com/microsoft/hve-core/releases/tag/hve-core-v9'
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            DriftCount = 1
            ErrorCount = 0
            Pin = [pscustomobject]@{
                PinnedTag = 'unknown'
                PinnedSha = 'abcdef1234567890'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path                = 'scripts/x.psm1'
                    Baseline            = 'source-header'
                    BaselineUpstreamSha = '1111111'
                    TargetUpstreamSha   = '2222222'
                    BaselineRef         = 'hve-core-v1'
                    TargetRef           = 'main'
                    ComparisonUrl       = 'https://github.com/microsoft/hve-core/compare/hve-core-v1...main'
                    Drift               = $true
                    State               = 'drift'
                }
            )
        }
        $body = Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'

        $body | Should -Match 'compare/abcdef1234567890\.\.\.hve-core-v9'
        $body | Should -Not -Match 'compare/unknown'
        $body | Should -Match 'Release-file baseline: `abcdef1234567890`'
    }

    It 'Rejects a release URL outside GitHub' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'https://evil.example/release'
            DriftCount = 0
            ErrorCount = 0
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @()
        }

        {
            Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'
        } | Should -Throw '*Unexpected hve-core release URL*'
    }

    It 'Rejects non-HTTPS and lookalike release URLs' -ForEach @(
        'http://github.com/microsoft/hve-core/releases/tag/v1'
        'https://github.com.evil.example/microsoft/hve-core/releases/tag/v1'
        'not-a-url'
        '//github.com/microsoft/hve-core/releases/tag/v1'
    ) {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = $_
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            DriftCount = 0
            ErrorCount = 0
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @()
        }

        {
            Format-HveCoreIssueBody -Result $r -RunUrl 'http://run' -CheckDate '2026-01-01'
        } | Should -Throw '*Unexpected hve-core release URL*'
    }
}

Describe 'Format-HveCoreJobSummary' -Tag 'Unit' {
    It 'Formats the job summary correctly' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestUrl = 'https://github.com/microsoft/hve-core/releases/tag/hve-core-v9'
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            DriftCount = 1
            ErrorCount = 0
            Pin = [pscustomobject]@{
                PinnedTag = 'hve-core-v1'
                File = '.github/workflows/copilot-setup-steps.yml'
            }
            Files = @(
                [pscustomobject]@{
                    Path                = 'scripts/x.psm1'
                    Baseline            = 'source-header'
                    BaselineUpstreamSha = '1111111'
                    TargetUpstreamSha   = '2222222'
                    BaselineRef         = 'hve-core-v1'
                    TargetRef           = 'main'
                    ComparisonUrl       = 'https://github.com/microsoft/hve-core/compare/hve-core-v1...main'
                    Drift               = $true
                    State               = 'drift'
                }
            )
        }
        $summary = Format-HveCoreJobSummary -Result $r

        $summary | Should -Match 'hve-core-v9'
        $summary | Should -Match 'scripts/x\.psm1'
        $summary | Should -Match '⚠️ Upstream advanced'
        $summary | Should -Match '\| File \| Baseline \| Upstream comparison \| Baseline upstream blob \| Target upstream blob \| Status \|'
        $summary | Should -Match '\| Source header \|'
        $summary | Should -Match '\| 1111111 \| 2222222 \|'
        $summary | Should -Match '\[hve-core-v1 → main\]\(https://github\.com/microsoft/hve-core/compare/hve-core-v1\.\.\.main\)'
        $summary | Should -Match 'Source-header target: b{40}'
        $summary | Should -Match 'Action required: 1 drifted, 0 check errors'
    }

    It 'Escapes untrusted refs in the job summary' {
        $r = [pscustomobject]@{
            LatestTag = 'v1|spoof'
            LatestMainSha = 'main](https://evil.example)'
            DriftCount = 0
            ErrorCount = 0
            Pin = [pscustomobject]@{ PinnedTag = 'pin`value' }
            Files = @()
        }

        $summary = Format-HveCoreJobSummary -Result $r

        $summary | Should -Match 'v1&#124;spoof'
        $summary | Should -Match 'main&#93;&#40;https://evil\.example&#41;'
        $summary | Should -Match 'pin&#96;value'
    }
}

Describe 'Get-HveCoreReleases' -Tag 'Unit' {
    It 'Parses release objects from the GitHub API' {
        Mock gh {
            $global:LASTEXITCODE = 0
            '[{"tag_name":"v1","draft":false},{"tag_name":"v2","draft":true}]'
        }

        $releases = @(Get-HveCoreReleases -Repo 'o/r')

        $releases.Count | Should -Be 2
        $releases[0].tag_name | Should -Be 'v1'
        Should -Invoke gh -Times 1 -Exactly -ParameterFilter {
            "$args" -match 'repos/o/r/releases\?per_page=30'
        }
    }

    It 'Throws when the GitHub API fails' {
        Mock gh { $global:LASTEXITCODE = 1; 'HTTP 403 rate limit' }

        { Get-HveCoreReleases -Repo 'o/r' } | Should -Throw '*cannot produce reliable results*'
    }

    It 'Throws when the GitHub API returns no payload' {
        Mock gh { $global:LASTEXITCODE = 0; '' }

        { Get-HveCoreReleases -Repo 'o/r' } | Should -Throw '*cannot produce reliable results*'
    }
}

Describe 'Invoke-HveCoreFreshnessCheck' -Tag 'Unit' {
    BeforeEach {
        Mock Get-HveCoreReleases {
            @([pscustomobject]@{
                    tag_name = 'hve-core-v9'
                    draft = $false
                    created_at = '2026-08-01T00:00:00Z'
                    html_url = 'https://github.com/microsoft/hve-core/releases/tag/hve-core-v9'
                })
        }
        Mock Resolve-HveCoreCommitSha {
            if ($Ref -eq 'main') { 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }
            else { 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }
        }
        Mock Get-PinnedHveCoreRef {
            [ordered]@{
                Tag = 'hve-core-v1'
                Sha = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            }
        }

        Mock Get-HveCoreFileDriftForBaseline {
            [ordered]@{
                Path                = $File.Path
                BaselineUpstreamSha = '1111111'
                TargetUpstreamSha   = '1111111'
                BaselineRef         = 'pin'
                TargetRef           = 'target'
                ComparisonUrl       = 'https://example.test/compare'
                Drift               = $false
                State               = 'current'
            }
        }
    }

    It 'Writes the resolved main SHA and all configured files to results JSON' {
        $resultsFile = Join-Path $TestDrive 'results.json'

        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        $result = Get-Content -Path $resultsFile -Raw | ConvertFrom-Json

        $outcome.AttentionCount | Should -Be 0
        $outcome.DriftCount | Should -Be 0
        $outcome.ErrorCount | Should -Be 0
        $result.LatestMainSha | Should -Be 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
        $result.LatestReleaseSha | Should -Be 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        $result.DriftCount | Should -Be 0
        $result.ErrorCount | Should -Be 0
        @($result.Files).Count | Should -Be $script:DerivedFiles.Count
        @($result.Files | Where-Object { $_.Baseline -eq 'source-header' }).Count | Should -Be 2
    }

    It 'Records a file-level error and continues checking remaining files' {
        $resultsFile = Join-Path $TestDrive 'results-with-error.json'
        Mock Get-HveCoreFileDriftForBaseline {
            if ($File.Path -eq 'scripts/security/Test-DangerousWorkflow.ps1') {
                throw [HveCoreFileValidationException]::new('invalid provenance header')
            }
            [ordered]@{
                Path                = $File.Path
                BaselineUpstreamSha = '1111111'
                TargetUpstreamSha   = '1111111'
                BaselineRef         = 'pin'
                TargetRef           = 'target'
                ComparisonUrl       = 'https://example.test/compare'
                Drift               = $false
                State               = 'current'
            }
        }

        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        $result = Get-Content -Path $resultsFile -Raw | ConvertFrom-Json
        $failed = $result.Files | Where-Object { $_.Path -eq 'scripts/security/Test-DangerousWorkflow.ps1' }

        $outcome.AttentionCount | Should -Be 1
        $outcome.DriftCount | Should -Be 0
        $outcome.ErrorCount | Should -Be 1
        $result.DriftCount | Should -Be 0
        $result.ErrorCount | Should -Be 1
        @($result.Files).Count | Should -Be $script:DerivedFiles.Count
        $failed.State | Should -Be 'error'
        $failed.Error | Should -Be 'invalid provenance header'
        $failed.Drift | Should -BeFalse
    }

    It 'Counts drift and validation errors independently' {
        $resultsFile = Join-Path $TestDrive 'results-with-drift-and-error.json'
        Mock Get-HveCoreFileDriftForBaseline {
            if ($File.Path -eq 'scripts/security/Test-DangerousWorkflow.ps1') {
                throw [HveCoreFileValidationException]::new('invalid provenance header')
            }
            $isDrift = $File.Path -eq 'scripts/security/Test-WorkflowPermissions.ps1'
            [ordered]@{
                Path                = $File.Path
                BaselineUpstreamSha = '1111111111111111111111111111111111111111'
                TargetUpstreamSha   = if ($isDrift) { '2222222222222222222222222222222222222222' } else { '1111111111111111111111111111111111111111' }
                BaselineRef         = 'pin'
                TargetRef           = 'target'
                ComparisonUrl       = 'https://example.test/compare'
                Drift               = $isDrift
                State               = if ($isDrift) { 'drift' } else { 'current' }
            }
        }

        $outcome = Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        $result = Get-Content -Path $resultsFile -Raw | ConvertFrom-Json

        $outcome.AttentionCount | Should -Be 2
        $outcome.DriftCount | Should -Be 1
        $outcome.ErrorCount | Should -Be 1
        $result.DriftCount | Should -Be 1
        $result.ErrorCount | Should -Be 1
    }

    It 'Throws before checking files when HVE_CORE_DERIVED_FILES_REF is unavailable' {
        $resultsFile = Join-Path $TestDrive 'results-without-pin.json'
        Mock Get-PinnedHveCoreRef { $null }

        {
            Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        } | Should -Throw '*Could not extract HVE_CORE_DERIVED_FILES_REF*'
        Should -Invoke Get-HveCoreFileDriftForBaseline -Times 0 -Exactly
        Test-Path $resultsFile | Should -BeFalse
    }

    It 'Throws before checking files when the pinned ref does not resolve' {
        $resultsFile = Join-Path $TestDrive 'results-with-invalid-pin.json'
        Mock Resolve-HveCoreCommitSha {
            if ($Ref -eq 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') {
                throw 'invalid pinned ref'
            }
            if ($Ref -eq 'main') { 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb' }
            else { 'cccccccccccccccccccccccccccccccccccccccc' }
        }

        {
            Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        } | Should -Throw '*invalid pinned ref*'
        Should -Invoke Get-HveCoreFileDriftForBaseline -Times 0 -Exactly
        Test-Path $resultsFile | Should -BeFalse
    }

    It 'Propagates upstream failures instead of reporting false drift' {
        $resultsFile = Join-Path $TestDrive 'results-with-api-error.json'
        Mock Get-HveCoreFileDriftForBaseline {
            throw 'gh api failed for scripts/security/Test-DangerousWorkflow.ps1@main'
        }

        {
            Invoke-HveCoreFreshnessCheck -RepoRoot $script:RepoRoot -ResultsFile $resultsFile
        } | Should -Throw '*gh api failed*'
        Test-Path $resultsFile | Should -BeFalse
    }
}

Describe 'Get-HveCoreTrackingIssue' -Tag 'Unit' {
    It 'Returns the trimmed issue number' {
        Mock gh { $global:LASTEXITCODE = 0; " 42 `n" }

        Get-HveCoreTrackingIssue | Should -Be '42'
    }

    It 'Returns null when no issue exists' {
        Mock gh { $global:LASTEXITCODE = 0; '' }

        Get-HveCoreTrackingIssue | Should -BeNullOrEmpty
    }

    It 'Throws when the issue lookup fails' {
        Mock gh { $global:LASTEXITCODE = 1; 'gh: API rate limit exceeded' }

        { Get-HveCoreTrackingIssue } | Should -Throw '*Could not query existing*'
    }
}

Describe 'Write-HveCoreGitHubOutputs' -Tag 'Unit' {
    It 'Emits all freshness counters exactly once' {
        $outputPath = Join-Path $TestDrive 'github-output.txt'
        $outcome = [pscustomobject]@{
            AttentionCount = 2
            DriftCount = 1
            ErrorCount = 1
        }

        Write-HveCoreGitHubOutputs -Outcome $outcome -Path $outputPath
        $lines = @(Get-Content -Path $outputPath)

        $lines | Should -Be @(
            'attention-count=2'
            'drift-count=1'
            'error-count=1'
        )
    }

    It 'Matches every workflow output consumer' {
        $outputPath = Join-Path $TestDrive 'github-output-contract.txt'
        $outcome = [pscustomobject]@{
            AttentionCount = 0
            DriftCount = 0
            ErrorCount = 0
        }
        Write-HveCoreGitHubOutputs -Outcome $outcome -Path $outputPath
        $emitted = @(Get-Content -Path $outputPath | ForEach-Object { ($_ -split '=', 2)[0] })
        $workflowPath = Join-Path $script:RepoRoot '.github/workflows/check-hve-core-freshness.yml'
        $workflow = Get-Content -Path $workflowPath -Raw
        $referenced = @([regex]::Matches($workflow, 'steps\.check\.outputs\.([a-z-]+)') |
                ForEach-Object { $_.Groups[1].Value } |
                Select-Object -Unique)

        $referenced | Should -Be @('attention-count', 'drift-count', 'error-count')
        foreach ($name in $referenced) {
            $emitted | Should -Contain $name
        }
    }

    It 'Updates the tracking issue when the check step reports validation errors' {
        $workflowPath = Join-Path $script:RepoRoot '.github/workflows/check-hve-core-freshness.yml'
        $workflow = Get-Content -Path $workflowPath -Raw

        $workflow | Should -Match "if:\s*[""']?!cancelled\(\)\s*&&\s*steps\.check\.outputs\.attention-count\s*!=\s*''\s*&&\s*steps\.check\.outputs\.attention-count\s*!=\s*'0'"
    }
}

Describe 'Get-HveCoreFreshnessExitCode' -Tag 'Unit' {
    It 'Returns success for current and drift-only outcomes' -ForEach @(
        @{ DriftCount = 0; ErrorCount = 0 }
        @{ DriftCount = 2; ErrorCount = 0 }
    ) {
        $outcome = [pscustomobject]@{
            DriftCount = $_.DriftCount
            ErrorCount = $_.ErrorCount
        }

        Get-HveCoreFreshnessExitCode -Outcome $outcome | Should -Be 0
    }

    It 'Returns failure when validation errors are present' {
        $outcome = [pscustomobject]@{
            DriftCount = 1
            ErrorCount = 1
        }

        Get-HveCoreFreshnessExitCode -Outcome $outcome | Should -Be 2
    }
}

Describe 'Test-HveCoreFreshness entry point' -Tag 'Unit' {
    It 'Lists derived files and baselines in config preview' {
        $scriptPath = Join-Path $script:RepoRoot 'scripts/security/Test-HveCoreFreshness.ps1'

        $output = & pwsh -NoProfile -File $scriptPath -ConfigPreview 2>&1

        $LASTEXITCODE | Should -Be 0
        "$output" | Should -Match 'Test-DangerousWorkflow\.ps1 \[source-header\]'
        "$output" | Should -Match 'SecurityHelpers\.psm1 \[release\]'
        "$output" | Should -Not -Match 'OrderedDictionary'
    }

    It 'Uses the pinned SHA when the bootstrap ref has no release tag' {
        $r = [pscustomobject]@{
            LatestTag = 'hve-core-v9'
            LatestMainSha = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            Pin = [pscustomobject]@{
                PinnedTag = 'unknown'
                PinnedSha = 'abcdef1234567890'
            }
            Files = @()
        }

        (Format-HveCoreJobSummary -Result $r) | Should -Match 'Release-file baseline: abcdef1234567890'
    }
}
