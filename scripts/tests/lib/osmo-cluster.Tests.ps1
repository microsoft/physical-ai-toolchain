#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT
# cspell:ignore riscv

# Behavioral coverage for scripts/lib/osmo-cluster.sh, shared by the AKS, HiL, and local OSMO deployments.
# Each case runs a bash driver that sources the library with helm, kubectl, and uname stubbed.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue) -and
        [bool](Get-Command openssl -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/osmo-cluster'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    # kubectl stub: the SchedulingShard appears on the second lookup; ConfigMap source files are copied out.
    $script:KubectlStub = @'
state="${STUB_STATE_DIR:?}"
args=" $* "
case "$args" in
  *" get configmap "*)
    case "${MEK_STUB:-absent}" in
      present) echo "configmap/osmo-mek" ;;
      error) echo "Unable to connect to the server" >&2; exit 1 ;;
    esac
    exit 0 ;;
  *" get schedulingshard/default "*)
    count=$(( $(cat "$state/shard-lookups" 2>/dev/null || echo 0) + 1 ))
    echo "$count" > "$state/shard-lookups"
    (( count >= 2 )) || exit 1
    exit 0 ;;
  *" create configmap "*)
    # Pipelines run two stubbed calls at once, so record this call's arguments in their own file.
    printf '%s\n' "$*" > "$state/create-configmap.args"
    for a in "$@"; do
      case "$a" in
        --from-file=*) spec="${a#--from-file=}"; cp "${spec#*=}" "$state/cm-${spec%%=*}" ;;
      esac
    done
    echo "kind: ConfigMap"
    exit 0 ;;
esac
exit 0
'@

    function Invoke-OsmoCluster {
        param(
            [Parameter(Mandatory)][string[]]$Lines,
            [string]$Machine = 'x86_64',
            [hashtable]$ExtraEnv = @{}
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $stateDir = Join-Path $workDir 'state'
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        $driver = Join-Path $workDir 'driver.sh'
        $content = @(
            'set -o errexit -o nounset -o pipefail'
            "source '$script:RepoRoot/scripts/lib/common.sh'"
            "source '$script:RepoRoot/infrastructure/setup/defaults.conf'"
            "source '$script:RepoRoot/scripts/lib/osmo-cluster.sh'"
        ) + $Lines
        Set-Content -Path $driver -Value ($content -join "`n")

        $envVars = @{ STUB_STATE_DIR = $stateDir; TIMEOUT_DEPLOY = '600s' }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }
        $result = Invoke-BashEntryScript -ScriptPath $driver -WorkDir $workDir `
            -EnvVars $envVars `
            -Stubs @{ kubectl = $script:KubectlStub; helm = 'exit 0'; sleep = 'exit 0'; uname = "echo $Machine" }
        $result | Add-Member -NotePropertyName StateDir -NotePropertyValue $stateDir
        $result
    }
}

Describe 'osmo_mek_configmap' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Prints a ConfigMap whose key1 is a 256-bit symmetric JWK' {
        $result = Invoke-OsmoCluster -Lines @('osmo_mek_configmap mek-config')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'name: mek-config'
        $result.StdOut | Should -Match 'currentMek: key1'
        $encoded = [regex]::Match($result.StdOut, 'key1: (\S+)').Groups[1].Value
        $jwk = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($encoded)) | ConvertFrom-Json
        $jwk.kty | Should -Be 'oct'
        $jwk.kid | Should -Be 'key1'
        [System.Convert]::FromBase64String($jwk.k).Length | Should -Be 32
    }

    It 'Generates a different key on every call' {
        $result = Invoke-OsmoCluster -Lines @('osmo_mek_configmap a', 'osmo_mek_configmap b')
        $keys = [regex]::Matches($result.StdOut, 'key1: (\S+)') | ForEach-Object { $_.Groups[1].Value }

        $keys.Count | Should -Be 2
        $keys[0] | Should -Not -Be $keys[1]
    }
}

Describe 'kai_scheduler_image_args' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Pins every KAI image to the <Machine> digests' -ForEach @(
        @{ Machine = 'x86_64'; OperatorVariable = 'KAI_OPERATOR_SHA256_AMD64' }
        @{ Machine = 'aarch64'; OperatorVariable = 'KAI_OPERATOR_SHA256_ARM64' }
    ) {
        $result = Invoke-OsmoCluster -Machine $Machine -Lines @(
            'kai_scheduler_image_args'
            "echo OPERATOR_SHA=`$$OperatorVariable"
        )
        $operatorSha = [regex]::Match($result.StdOut, 'OPERATOR_SHA=([0-9a-f]{64})').Groups[1].Value
        $lines = @($result.StdOut -split "`n" | Where-Object { $_ -match 'image\.tag=' })

        $result.ExitCode | Should -Be 0
        $lines.Count | Should -Be 5
        $lines[0] | Should -Be "operator.image.tag=v0.14.9@sha256:$operatorSha"
        $lines | ForEach-Object { $_ | Should -Match '@sha256:[0-9a-f]{64}$' }
    }

    It 'Rejects an unsupported architecture' {
        $result = Invoke-OsmoCluster -Machine 'riscv64' -Lines @('kai_scheduler_image_args')

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'Unsupported KAI image architecture: riscv64'
    }
}

Describe 'install_kai_scheduler' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Installs without Helm --wait, then waits on the operator and the SchedulingShard' {
        $result = Invoke-OsmoCluster -Lines @(
            'install_kai_scheduler /tmp/kubeconfig ctx /charts/kai.tgz /values/kai.yaml --set-string operator.image.tag=x'
        )
        $helm = @($result.Calls | Where-Object { $_ -like 'helm *' })
        $kubectl = @($result.Calls | Where-Object { $_ -like 'kubectl *' })

        $result.ExitCode | Should -Be 0
        $helm.Count | Should -Be 1
        $helm[0] | Should -Match 'upgrade --install kai-scheduler /charts/kai\.tgz --namespace kai-scheduler --create-namespace -f /values/kai\.yaml --timeout 600s --set-string operator\.image\.tag=x'
        $helm[0] | Should -Not -Match '--wait'
        $helm[0] | Should -Match '--kubeconfig /tmp/kubeconfig --kube-context ctx'
        ($kubectl | Where-Object { $_ -match 'rollout status deployment/kai-operator -n kai-scheduler' }).Count | Should -Be 1
        ($kubectl | Where-Object { $_ -match 'get schedulingshard/default' }).Count | Should -Be 2
        ($kubectl | Where-Object { $_ -match 'wait --for=condition=Available schedulingshard/default' }).Count | Should -Be 1
    }
}

Describe 'osmo_apply_operator_kubeconfig' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Publishes a kubeconfig whose exec plugin re-reads the projected token' {
        $result = Invoke-OsmoCluster -Lines @('osmo_apply_operator_kubeconfig /tmp/kubeconfig ctx osmo-operator')
        $config = Get-Content -Path (Join-Path $result.StateDir 'cm-config') -Raw
        $plugin = Get-Content -Path (Join-Path $result.StateDir 'cm-token-cred.py') -Raw

        $result.ExitCode | Should -Be 0
        Get-Content -Path (Join-Path $result.StateDir 'create-configmap.args') -Raw |
            Should -Match 'create configmap incluster-kubeconfig -n osmo-operator'
        $config | Should -Match 'command: python3'
        $config | Should -Match '/etc/kubeconfig/token-cred\.py'
        $config | Should -Not -Match 'tokenFile'
        $plugin | Should -Match '/var/run/secrets/kubernetes\.io/serviceaccount/token'
        $plugin | Should -Match 'ExecCredential'
    }
}

Describe 'osmo_mek_state' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Reports <Expected> when kubectl <Case>' -ForEach @(
        @{ Case = 'finds the ConfigMap'; Mode = 'present'; Expected = 'present' }
        @{ Case = 'reports it as not found'; Mode = 'absent'; Expected = 'absent' }
    ) {
        $result = Invoke-OsmoCluster -Lines @('state=$(osmo_mek_state osmo osmo-mek)', 'echo "state=$state"') `
            -ExtraEnv @{ MEK_STUB = $Mode }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match "state=$Expected"
        $result.Calls | Should -Contain 'kubectl get configmap osmo-mek -n osmo --ignore-not-found -o name'
    }

    It 'Fails instead of reporting absent when kubectl cannot read the ConfigMap' {
        $result = Invoke-OsmoCluster -Lines @('state=$(osmo_mek_state osmo osmo-mek) || exit 3', 'echo "state=$state"') `
            -ExtraEnv @{ MEK_STUB = 'error' }

        $result.ExitCode | Should -Be 3
        $result.StdOut | Should -Not -Match 'state='
        $result.StdErr | Should -Match 'whether the master encryption key exists is unknown'
    }
}
