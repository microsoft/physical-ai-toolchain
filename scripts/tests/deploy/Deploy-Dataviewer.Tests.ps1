#!/usr/bin/env pwsh
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
#
# Deploy-Dataviewer.Tests.ps1
#
# Purpose: Pester tests for data-management/setup/deploy-dataviewer.sh
#          Verifies the script no longer builds images, validates digest
#          formats, and aborts when verify-image.sh fails.

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

BeforeAll {
    $script:RepoRoot   = (Resolve-Path "$PSScriptRoot/../../..").Path
    $script:DeploySh   = Join-Path $script:RepoRoot 'data-management/setup/deploy-dataviewer.sh'
    $script:CommonSh   = Join-Path $script:RepoRoot 'scripts/lib/common.sh'
    $script:DeployText = Get-Content -Raw -LiteralPath $script:DeploySh
    $script:Bash       = (Get-Command bash -ErrorAction SilentlyContinue)?.Source
    $script:HexDigest  = ('a' * 64)
    $script:GoodDigest = "sha256:$script:HexDigest"

    function Invoke-DeployScript {
        param(
            [string[]] $ScriptArgs,
            [Parameter()]
            [int]      $StubVerifyExitCode = 0
        )
        if (-not $script:Bash) {
            throw 'bash not available'
        }

        # Build an isolated tmp tree mirroring the repo layout so that
        # REPO_ROOT (resolved via SCRIPT_DIR/../.. when git is absent)
        # points at the tmp dir. This lets us swap verify-image.sh and
        # capture az invocations without touching the real repo.
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ([guid]::NewGuid())
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        $setupDir   = Join-Path $tmp 'data-management/setup'
        $libDir     = Join-Path $tmp 'scripts/lib'
        $secDir     = Join-Path $tmp 'scripts/security'
        $stubBin    = Join-Path $tmp 'bin'
        $tfDir      = Join-Path $tmp 'tf'
        foreach ($d in $setupDir, $libDir, $secDir, $stubBin, $tfDir) {
            New-Item -ItemType Directory -Force -Path $d | Out-Null
        }

        Copy-Item -LiteralPath $script:DeploySh -Destination (Join-Path $setupDir 'deploy-dataviewer.sh')
        Copy-Item -LiteralPath (Join-Path (Split-Path $script:DeploySh -Parent) 'defaults.conf') -Destination (Join-Path $setupDir 'defaults.conf') -Force
        Copy-Item -LiteralPath $script:CommonSh -Destination (Join-Path $libDir 'common.sh')
        Set-Content -LiteralPath (Join-Path $tfDir 'terraform.tfstate') -Value '{}'

        $azLog = Join-Path $tmp 'az-calls.log'
        '' | Set-Content -LiteralPath $azLog -NoNewline

        # az stub: log every invocation, return success / empty output.
        $azStub = @"
#!/usr/bin/env bash
printf '%s\n' "`$*" >> "$azLog"
exit 0
"@
        Set-Content -LiteralPath (Join-Path $stubBin 'az') -Value $azStub -NoNewline
        @'
#!/usr/bin/env bash
cat <<'JSON'
{
  "resource_group":     {"value": {"name": "rg-test"}},
  "container_registry": {"value": {"name": "acrtest", "login_server": "acrtest.azurecr.io"}},
  "dataviewer":         {"value": {
    "backend":  {"name": "ca-backend"},
    "frontend": {"name": "ca-frontend", "url": "https://example.invalid"},
    "identity": {"id": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mi"},
    "entra_id": {"client_id": "", "tenant_id": ""}
  }}
}
JSON
'@ | Set-Content -LiteralPath (Join-Path $stubBin 'terraform') -NoNewline
        # jq is used to parse stubbed terraform JSON; rely on the real jq on PATH.

        # verify-image.sh stub honoring the requested exit code.
        $verifyStub = @"
#!/usr/bin/env bash
printf 'verify-image stub args: %s\n' "`$*" >&2
exit $StubVerifyExitCode
"@
        Set-Content -LiteralPath (Join-Path $secDir 'verify-image.sh') -Value $verifyStub -NoNewline

        foreach ($f in 'az', 'terraform') {
            & chmod +x (Join-Path $stubBin $f)
        }
        & chmod +x (Join-Path $secDir 'verify-image.sh')
        & chmod +x (Join-Path $setupDir 'deploy-dataviewer.sh')

        $copiedScript = Join-Path $setupDir 'deploy-dataviewer.sh'
        $env:PATH = "$stubBin$([IO.Path]::PathSeparator)$env:PATH"
        try {
            $cmdArgs = @('-c', "cd `"$tmp`" && exec bash `"$copiedScript`" --tf-dir `"$tfDir`" $($ScriptArgs -join ' ')")
            $stdout = & bash @cmdArgs 2>&1
            $exit   = $LASTEXITCODE
            $azCalls = if (Test-Path $azLog) { Get-Content -Raw -LiteralPath $azLog } else { '' }
            return [pscustomobject]@{
                Exit    = $exit
                Output  = ($stdout -join "`n")
                AzCalls = $azCalls
            }
        }
        finally {
            $env:PATH = ($env:PATH -split [IO.Path]::PathSeparator | Where-Object { $_ -ne $stubBin }) -join [IO.Path]::PathSeparator
            Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        }
    }
}

Describe 'deploy-dataviewer.sh static contract' -Tag 'Unit' {
    It 'Exists and is executable' {
        Test-Path $script:DeploySh | Should -BeTrue
        if ($IsLinux -or $IsMacOS) {
            $mode = (Get-Item $script:DeploySh).UnixMode
            $mode | Should -Match 'x'
        }
    }

    It 'Does not invoke az acr build (no in-script image builds)' {
        ($script:DeployText -split "`n" |
            Where-Object { $_ -match 'az\s+acr\s+build' -and $_ -notmatch '^\s*#' }
        ).Count | Should -Be 0
    }

    It 'Does not define a build_dataviewer_images function' {
        $script:DeployText | Should -Not -Match 'build_dataviewer_images\s*\('
    }

    It 'References scripts/security/verify-image.sh via VERIFY_SCRIPT' {
        $script:DeployText | Should -Match 'VERIFY_SCRIPT='
        $script:DeployText | Should -Match 'scripts/security/verify-image\.sh'
    }

    It 'Defines verify_signed_digest wrapper that aborts on non-zero return' {
        $script:DeployText | Should -Match 'verify_signed_digest\s*\(\)'
        $script:DeployText | Should -Match 'fatal\s+"\$label image signature verification failed'
    }

    It 'Validates digest format with strict sha256 regex' {
        $script:DeployText | Should -Match '\^sha256:\[a-f0-9\]\{64\}\$'
    }

    It 'Updates container apps via immutable digest reference' {
        $script:DeployText | Should -Match 'az containerapp update'
        $script:DeployText | Should -Match '--image\s+"\$(backend|frontend)_image"'
    }

    It 'Sets ACR registry with managed identity (not admin creds)' {
        $script:DeployText | Should -Match 'az containerapp registry set'
        $script:DeployText | Should -Match '--identity\s+"\$identity_id"'
    }

    It 'Exposes the documented signing-related flags' {
        foreach ($flag in '--backend-digest', '--frontend-digest', '--verify-mode',
                          '--offline', '--trusted-root', '--policy-file',
                          '--accept-public-rekor',
                          '--skip-backend', '--skip-frontend', '--skip-update',
                          '--config-preview') {
            $script:DeployText | Should -Match ([regex]::Escape($flag))
        }
    }
}

Describe 'deploy-dataviewer.sh dynamic behaviour' -Tag 'Unit' {
    BeforeAll {
        if (-not $script:Bash) {
            Set-ItResult -Skipped -Because 'bash not available on this host'
        }
    }

    It '--help exits 0 and prints usage' {
        $r = Invoke-DeployScript -ScriptArgs @('--help')
        $r.Exit   | Should -Be 0
        $r.Output | Should -Match 'Deploy signed dataviewer images'
    }

    It 'Aborts when backend digest is missing' {
        $r = Invoke-DeployScript -ScriptArgs @(
            '--frontend-digest', $script:GoodDigest, '--config-preview'
        )
        $r.Exit   | Should -Not -Be 0
        $r.Output | Should -Match 'Backend digest is required'
    }

    It 'Aborts when a digest is malformed' {
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', 'sha256:abc',
            '--frontend-digest', $script:GoodDigest,
            '--config-preview'
        )
        $r.Exit   | Should -Not -Be 0
        $r.Output | Should -Match 'must match sha256'
    }

    It 'Rejects an invalid --verify-mode value' {
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest,
            '--verify-mode', 'bogus',
            '--config-preview'
        )
        $r.Exit   | Should -Not -Be 0
        $r.Output | Should -Match 'verify-mode'
    }

    It 'Aborts and skips containerapp update when verify-image fails' {
        $r = Invoke-DeployScript -StubVerifyExitCode 1 -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest
        )
        $r.Exit    | Should -Not -Be 0
        $r.AzCalls | Should -Not -Match 'containerapp update'
    }

    It 'Accepts --verify-mode <Mode> under --config-preview' -TestCases @(
        @{ Mode = 'sigstore' }
        @{ Mode = 'notation' }
        @{ Mode = 'auto' }
    ) {
        param($Mode)
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest,
            '--verify-mode', $Mode,
            '--config-preview'
        )
        $r.Exit | Should -Be 0
    }

    It 'Accepts --accept-public-rekor under --config-preview' {
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest,
            '--accept-public-rekor',
            '--config-preview'
        )
        $r.Exit | Should -Be 0
    }

    It 'Honours <Skip> by invoking verify-image only for the unskipped image' -TestCases @(
        @{ Skip = '--skip-backend' }
        @{ Skip = '--skip-frontend' }
    ) {
        param($Skip)
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest,
            '--skip-update',
            $Skip
        )
        $r.Exit | Should -Be 0
        $verifyHits = ([regex]::Matches($r.Output, 'verify-image stub args')).Count
        $verifyHits | Should -Be 1
    }

    It '--config-preview makes no az calls' {
        $r = Invoke-DeployScript -ScriptArgs @(
            '--backend-digest', $script:GoodDigest,
            '--frontend-digest', $script:GoodDigest,
            '--config-preview'
        )
        $r.Exit    | Should -Be 0
        ($r.AzCalls -as [string]).Trim() | Should -BeNullOrEmpty
    }
}
