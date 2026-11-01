#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for scripts/lib/local-cluster.sh. Each case runs a small bash driver that sources the
# library; a stateful kubectl stub stores applied objects under the test's work directory.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command jq -ErrorAction SilentlyContinue) -and
        [bool](Get-Command envsubst -ErrorAction SilentlyContinue) -and
        [bool](Get-Command openssl -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/local-cluster'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    # kubectl stub: secrets and jobs live as JSON files in STUB_STATE_DIR.
    $script:KubectlStub = @'
state="${STUB_STATE_DIR:?}"
args=" $* "
case "$args" in
  *" get secret "*)
    [[ -f "$state/secret.json" ]] && cat "$state/secret.json"
    exit 0 ;;
  *" create secret generic "*)
    data='{}'
    for a in "$@"; do
      case "$a" in
        --from-file=*)
          spec="${a#--from-file=}"
          data=$(jq --arg k "${spec%%=*}" --arg v "$(base64 -w0 < "${spec#*=}")" '. + {($k): $v}' <<< "$data") ;;
      esac
    done
    jq -n --argjson data "$data" '{apiVersion: "v1", kind: "Secret", metadata: {name: "creds", namespace: "ns"}, data: $data}'
    exit 0 ;;
  *" get job "*)
    [[ -f "$state/job.json" ]] && jq '.status.succeeded = 1' "$state/job.json"
    exit 0 ;;
  *" create --dry-run=client -o json -f - "*)
    cat
    exit 0 ;;
  *" delete job "*)
    rm -f "$state/job.json"
    exit 0 ;;
  *" replace -f - "*)
    cat > "$state/secret.json"
    exit 0 ;;
  *" apply -f - "*)
    input=$(cat)
    if [[ "$(jq -r .kind <<< "$input")" == "Job" ]]; then
      printf '%s' "$input" > "$state/job.json"
    else
      printf '%s' "$input" > "$state/secret.json"
    fi
    exit 0 ;;
esac
exit 0
'@

    # Run bash driver lines after sourcing the library, with kubectl stubbed against a fresh state dir.
    function Invoke-LocalCluster {
        param(
            [Parameter(Mandatory)][string[]]$Lines,
            [string]$WorkDir,
            [hashtable]$ExtraEnv = @{},
            [hashtable]$ExtraStubs = @{}
        )

        if (-not $WorkDir) {
            $WorkDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        }
        $stateDir = Join-Path $WorkDir 'state'
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $driver = Join-Path $WorkDir 'driver.sh'
        $content = @(
            'set -o errexit -o nounset -o pipefail'
            "source '$script:RepoRoot/scripts/lib/common.sh'"
            'LOCAL_PART_OF=physical-ai-local'
            "source '$script:RepoRoot/scripts/lib/local-cluster.sh'"
        ) + $Lines
        Set-Content -Path $driver -Value ($content -join "`n")

        $envVars = @{ STUB_STATE_DIR = $stateDir }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }
        $stubs = @{ kubectl = $script:KubectlStub; sleep = 'exit 0' }
        foreach ($key in $ExtraStubs.Keys) { $stubs[$key] = $ExtraStubs[$key] }
        $result = Invoke-BashEntryScript -ScriptPath $driver -WorkDir $WorkDir -EnvVars $envVars -Stubs $stubs
        $result | Add-Member -NotePropertyName StateDir -NotePropertyValue $stateDir
        $result
    }

    function Get-StateJson {
        param([string]$StateDir, [string]$Name)

        $path = Join-Path $StateDir $Name
        if (Test-Path $path) { Get-Content -Path $path -Raw | ConvertFrom-Json -AsHashtable } else { $null }
    }

    function ConvertFrom-Base64Text {
        param([string]$Value)
        [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Value))
    }
}

Describe 'local_render_manifest' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:TemplateDir = Join-Path $script:ArtifactsRoot 'templates'
        New-Item -ItemType Directory -Path $script:TemplateDir -Force | Out-Null
        $script:Template = Join-Path $script:TemplateDir 'sample.yaml'
        Set-Content -Path $script:Template -Value @'
name: ${LOCAL_NAME}
image: ${LOCAL_IMAGE}
shell: "$HOME ${HOME} $(VAR) ${OTHER_VALUE}"
'@
    }

    It 'Substitutes LOCAL_ placeholders and leaves shell and Kubernetes syntax intact' {
        $result = Invoke-LocalCluster -Lines @(
            'export LOCAL_NAME=postgres LOCAL_IMAGE=postgres:16 OTHER_VALUE=changed'
            "local_render_manifest '$script:Template'"
        )

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'name: postgres'
        $result.StdOut | Should -Match 'image: postgres:16'
        $result.StdOut | Should -Match ([regex]::Escape('shell: "$HOME ${HOME} $(VAR) ${OTHER_VALUE}"'))
    }

    It 'Stops when a placeholder is not exported' {
        $result = Invoke-LocalCluster -Lines @(
            'export LOCAL_NAME=postgres'
            "local_render_manifest '$script:Template'"
        )

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'sample\.yaml needs LOCAL_IMAGE'
    }
}

Describe 'local_ensure_generated_secret' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Creates a labeled Secret with random hex values that never appear in arguments' {
        $result = Invoke-LocalCluster -Lines @('local_ensure_generated_secret ns creds alpha=8 beta=4')
        $secret = Get-StateJson -StateDir $result.StateDir -Name 'secret.json'

        $result.ExitCode | Should -Be 0
        $secret.metadata.labels['app.kubernetes.io/part-of'] | Should -Be 'physical-ai-local'
        $alpha = ConvertFrom-Base64Text $secret.data.alpha
        $beta = ConvertFrom-Base64Text $secret.data.beta
        $alpha | Should -Match '^[0-9a-f]{16}$'
        $beta | Should -Match '^[0-9a-f]{8}$'
        ($result.Calls -join "`n") | Should -Not -Match $alpha
        ($result.Calls -join "`n") | Should -Not -Match $beta
        ($result.Calls -join "`n") | Should -Not -Match 'from-literal'
    }

    It 'Generates unpadded URL-safe base64 for urlsafe specs' {
        $result = Invoke-LocalCluster -Lines @('local_ensure_generated_secret ns creds token=urlsafe:32')
        $secret = Get-StateJson -StateDir $result.StateDir -Name 'secret.json'

        $result.ExitCode | Should -Be 0
        ConvertFrom-Base64Text $secret.data.token | Should -Match '^[A-Za-z0-9_-]{43}$'
    }

    It 'Adds only missing keys and keeps existing values' {
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $workDir 'state') -Force | Out-Null
        $existing = @{
            apiVersion = 'v1'; kind = 'Secret'
            metadata   = @{ name = 'creds'; namespace = 'ns'; resourceVersion = '7' }
            data       = @{ alpha = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes('kept-value')) }
        }
        $existing | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $workDir 'state/secret.json')

        $result = Invoke-LocalCluster -WorkDir $workDir -Lines @('local_ensure_generated_secret ns creds alpha=8 beta=4')
        $secret = Get-StateJson -StateDir $result.StateDir -Name 'secret.json'

        $result.ExitCode | Should -Be 0
        ($result.Calls | Where-Object { $_ -match ' replace -f -' }).Count | Should -Be 1
        ConvertFrom-Base64Text $secret.data.alpha | Should -Be 'kept-value'
        ConvertFrom-Base64Text $secret.data.beta | Should -Match '^[0-9a-f]{8}$'
        $secret.metadata.resourceVersion | Should -Be '7'
    }

    It 'Changes nothing when every key exists' {
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path (Join-Path $workDir 'state') -Force | Out-Null
        @{ kind = 'Secret'; metadata = @{ name = 'creds' }; data = @{ alpha = 'YQ=='; beta = 'Yg==' } } |
            ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $workDir 'state/secret.json')

        $result = Invoke-LocalCluster -WorkDir $workDir -Lines @('local_ensure_generated_secret ns creds alpha=8 beta=4')

        $result.ExitCode | Should -Be 0
        ($result.Calls | Where-Object { $_ -match ' (apply|replace|create) ' }).Count | Should -Be 0
    }
}

Describe 'local_derive_secret' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        function New-SourceSecretWorkDir {
            $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path (Join-Path $workDir 'state') -Force | Out-Null
            $encode = { param($text) [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($text)) }
            @{
                apiVersion = 'v1'; kind = 'Secret'; metadata = @{ name = 's3'; namespace = 'platform' }
                data       = @{ 'access-key-id' = (& $encode 'source-key-id'); 'secret-access-key' = (& $encode 'source-secret') }
            } | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $workDir 'state/secret.json')
            $workDir
        }
    }

    It 'Maps source keys to new names and adds literals without exposing values' {
        $result = Invoke-LocalCluster -WorkDir (New-SourceSecretWorkDir) -Lines @(
            'local_derive_secret platform s3 osmo osmo-storage access_key_id=access-key-id access_key=secret-access-key -- region=us-east-1 addressing_style=path'
        )
        $secret = Get-StateJson -StateDir $result.StateDir -Name 'secret.json'

        $result.ExitCode | Should -Be 0
        $secret.metadata.name | Should -Be 'osmo-storage'
        $secret.metadata.namespace | Should -Be 'osmo'
        $secret.metadata.labels['app.kubernetes.io/part-of'] | Should -Be 'physical-ai-local'
        ConvertFrom-Base64Text $secret.data.access_key_id | Should -Be 'source-key-id'
        ConvertFrom-Base64Text $secret.data.access_key | Should -Be 'source-secret'
        ConvertFrom-Base64Text $secret.data.region | Should -Be 'us-east-1'
        ConvertFrom-Base64Text $secret.data.addressing_style | Should -Be 'path'
        $secret.data.Keys.Count | Should -Be 4
        ($result.Calls -join "`n") | Should -Not -Match 'source-secret|source-key-id'
    }

    It 'Stops when the source Secret lacks a mapped key' {
        $result = Invoke-LocalCluster -WorkDir (New-SourceSecretWorkDir) -Lines @(
            'local_derive_secret platform s3 osmo osmo-storage password=missing-key'
        )

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'source Secret lacks key missing-key'
    }
}

Describe 'local_run_job' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        $script:JobManifest = '{"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "bootstrap", "namespace": "ns"}}'
    }

    It 'Starts the Job, then keeps it on a rerun with the same manifest and hash input' {
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $first = Invoke-LocalCluster -WorkDir $workDir -Lines @("local_run_job ns bootstrap '$script:JobManifest' volume-a")
        $job = Get-StateJson -StateDir $first.StateDir -Name 'job.json'
        Remove-Item -Path (Join-Path $workDir '.stubs/.calls.log') -Force

        $second = Invoke-LocalCluster -WorkDir $workDir -Lines @("local_run_job ns bootstrap '$script:JobManifest' volume-a")

        $first.ExitCode | Should -Be 0
        $job.metadata.annotations['physical-ai-toolchain/spec-hash'] | Should -Match '^[0-9a-f]{16}$'
        $second.ExitCode | Should -Be 0
        $second.StdOut | Should -Match 'already succeeded'
        ($second.Calls | Where-Object { $_ -match ' (apply|delete) ' }).Count | Should -Be 0
    }

    It 'Recreates the Job when the hash input changes' {
        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $first = Invoke-LocalCluster -WorkDir $workDir -Lines @("local_run_job ns bootstrap '$script:JobManifest' volume-a")
        $firstHash = (Get-StateJson -StateDir $first.StateDir -Name 'job.json').metadata.annotations['physical-ai-toolchain/spec-hash']
        Remove-Item -Path (Join-Path $workDir '.stubs/.calls.log') -Force

        $result = Invoke-LocalCluster -WorkDir $workDir -Lines @("local_run_job ns bootstrap '$script:JobManifest' volume-b")
        $job = Get-StateJson -StateDir $result.StateDir -Name 'job.json'

        $result.ExitCode | Should -Be 0
        ($result.Calls | Where-Object { $_ -match ' delete job bootstrap' }).Count | Should -Be 1
        # The piped create and apply stubs run concurrently and can interleave their call-log lines,
        # so the stub's stored Job proves the apply instead.
        $job.metadata.annotations['physical-ai-toolchain/spec-hash'] | Should -Match '^[0-9a-f]{16}$'
        $job.metadata.annotations['physical-ai-toolchain/spec-hash'] | Should -Not -Be $firstHash
    }
}

Describe 'local_build_and_push_image' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    BeforeAll {
        # docker stub: writes the BuildKit metadata file the way docker buildx does, or fails, per DOCKER_STUB_MODE.
        $script:DockerStub = @'
metadata=""
while [[ $# -gt 0 ]]; do
  [[ "$1" == "--metadata-file" ]] && metadata="$2"
  shift
done
case "${DOCKER_STUB_MODE:-ok}" in
  fail) echo "ERROR: failed to solve" >&2; exit 1 ;;
  nodigest) echo '{}' > "$metadata" ;;
  *) printf '{"containerimage.digest": "sha256:%064d"}\n' 7 > "$metadata" ;;
esac
'@
        # Call the helper the way the deploy script does: inside a command substitution.
        $script:BuildLines = @(
            'ref=$(local_build_and_push_image localhost:5000/viewer sha-1 Dockerfile ctx --build-arg X=1) || exit 3'
            'echo "ref=$ref"'
        )
    }

    It 'Prints the digest reference of the pushed image' {
        $result = Invoke-LocalCluster -Lines $script:BuildLines -ExtraStubs @{ docker = $script:DockerStub }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match ('ref=localhost:5000/viewer@sha256:' + ('0' * 63) + '7')
        @($result.Calls | Where-Object { $_ -match '^docker buildx build --provenance=false --push --metadata-file \S+ --tag localhost:5000/viewer:sha-1 --file Dockerfile --build-arg X=1 ctx$' }).Count | Should -Be 1
        @($result.Calls | Where-Object { $_ -match '^docker buildx build ' }).Count | Should -Be 1
    }

    It 'Fails with nothing on stdout when <Case>' -ForEach @(
        @{ Case = 'the build or push fails'; Mode = 'fail'; Message = 'Building or pushing localhost:5000/viewer:sha-1 failed' }
        @{ Case = 'the build reports no digest'; Mode = 'nodigest'; Message = 'reported no image digest' }
    ) {
        $result = Invoke-LocalCluster -Lines $script:BuildLines -ExtraStubs @{ docker = $script:DockerStub } `
            -ExtraEnv @{ DOCKER_STUB_MODE = $Mode }

        $result.ExitCode | Should -Be 3
        $result.StdOut | Should -Not -Match 'ref='
        $result.StdErr | Should -Match ([regex]::Escape($Message))
    }
}
