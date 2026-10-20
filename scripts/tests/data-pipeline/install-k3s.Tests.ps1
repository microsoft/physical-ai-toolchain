#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for data-pipeline/setup/hil/01-install-k3s.sh. Preview cases never need sudo and never
# inspect the host's own K3s: a stub binary reports the installed version through EDGE_K3S_BINARY. Install
# cases stub sudo, curl, and kubectl, so every privileged step is only logged and can be checked for order.

BeforeDiscovery {
    $script:ToolsPresent = [bool](Get-Command bash -ErrorAction SilentlyContinue)
    $script:InstallToolsPresent = $script:ToolsPresent -and [bool](Get-Command jq -ErrorAction SilentlyContinue) -and
        [bool](Get-Command sha256sum -ErrorAction SilentlyContinue)
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../data-pipeline/setup/hil/01-install-k3s.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/install-k3s'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    function Invoke-K3sPreview {
        param(
            [string]$InstalledVersion,
            [hashtable]$ExtraEnv = @{},
            [string]$SudoBody = 'exit 1',
            [string[]]$ExtraArgs = @()
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $workDir -Force | Out-Null
        $binary = Join-Path $workDir 'k3s'
        if ($InstalledVersion) {
            Set-Content -Path $binary -Value "#!/bin/bash`necho 'k3s version $InstalledVersion (0000000)'`necho 'go version go1.24'" -NoNewline
            & chmod +x $binary
        }

        $envVars = @{ EDGE_K3S_BINARY = $binary }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }

        Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workDir -EnvVars $envVars `
            -Stubs @{ sudo = $SudoBody; hostname = 'echo test-host' } -ScriptArgs (@('--config-preview') + $ExtraArgs)
    }

    # Preview a v1.32 node whose data directory holds the given files, with sudo running unprivileged.
    function Invoke-DatastorePreview {
        param([string[]]$Paths = @())

        $dataDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        foreach ($relative in $Paths) {
            $path = Join-Path $dataDir $relative
            if ($relative.EndsWith('/')) {
                New-Item -ItemType Directory -Path $path -Force | Out-Null
            }
            else {
                New-Item -ItemType Directory -Path (Split-Path $path -Parent) -Force | Out-Null
                New-Item -ItemType File -Path $path -Force | Out-Null
            }
        }
        New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

        Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1' -ExtraArgs @('--data-dir', $dataDir) `
            -SudoBody 'if [ "$1" = "-n" ]; then shift; fi; case "$1" in true|test) exec "$@" ;; *) exit 1 ;; esac'
    }

    $script:ReadyWorkloads = '{"kind":"List","items":[' +
        '{"kind":"Deployment","metadata":{"name":"coredns","generation":2},"spec":{"replicas":1},' +
        '"status":{"observedGeneration":2,"replicas":1,"updatedReplicas":1,"availableReplicas":1}},' +
        '{"kind":"DaemonSet","metadata":{"name":"nvidia-device-plugin-daemonset","generation":1},' +
        '"status":{"observedGeneration":1,"desiredNumberScheduled":1,"updatedNumberScheduled":1,"numberAvailable":1}}]}'
    $script:StuckWorkloads = $script:ReadyWorkloads -replace '"availableReplicas":1', '"availableReplicas":0'
    $script:NoWorkloads = '{"kind":"List","items":[]}'
    $script:FakeK3sSha = [System.Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes('fake-k3s'))).ToLowerInvariant()

    # sudo only logs, except where the script reads its output: cmp reports whether the managed files differ,
    # and each kube-system workload listing returns the next fixture, repeating the last one; a FAIL fixture
    # makes that listing fail.
    $script:SudoStub = @'
case "$1" in
  cmp) exit "$STUB_CMP_STATUS" ;;
  sha256sum) echo "$EDGE_K3S_SHA256_AMD64  $2"; exit 0 ;;
esac
if [[ "$1" == "$EDGE_K3S_BINARY" && "$*" == *" kubectl get deployments,daemonsets "* ]]; then
  count=$(( $(cat "$STUB_FIXTURES/.count" 2>/dev/null || echo 0) + 1 ))
  echo "$count" > "$STUB_FIXTURES/.count"
  fixture="$STUB_FIXTURES/workloads-$count.json"
  [[ -f "$fixture" ]] || fixture=$(ls "$STUB_FIXTURES"/workloads-*.json | sort -V | tail -n 1)
  [[ "$(cat "$fixture")" != FAIL ]] || exit 1
  cat "$fixture"
fi
exit 0
'@
    $script:CurlStub = @'
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--output" ]]; then printf 'fake-k3s' > "$2"; exit 0; fi
  shift
done
exit 1
'@

    # Run the install path against stubbed commands; every release pin matches the stub download.
    function Invoke-K3sInstall {
        param(
            [Parameter(Mandatory)][string]$InstalledVersion,
            [string[]]$Workloads = @($script:ReadyWorkloads),
            [switch]$ConfigDiffers,
            [switch]$DownloadFails,
            [string[]]$ExtraArgs = @(),
            [hashtable]$ExtraEnv = @{}
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $fixtureDir = Join-Path $workDir 'fixtures'
        New-Item -ItemType Directory -Path $fixtureDir -Force | Out-Null
        for ($index = 0; $index -lt $Workloads.Count; $index++) {
            Set-Content -Path (Join-Path $fixtureDir "workloads-$($index + 1).json") -Value $Workloads[$index] -NoNewline
        }
        $binary = Join-Path $workDir 'k3s'
        Set-Content -Path $binary -Value "#!/bin/bash`necho 'k3s version $InstalledVersion (0000000)'" -NoNewline
        & chmod +x $binary

        $sha = $script:FakeK3sSha
        $envVars = @{
            EDGE_K3S_BINARY                    = $binary
            EDGE_K3S_SHA256_AMD64              = $sha
            EDGE_K3S_SHA256_ARM64              = $sha
            EDGE_K3S_UPGRADE_STEPS             = "v1.33.13+k3s2|$sha|$sha v1.34.12+k3s1|$sha|$sha"
            EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS  = '30'
            EDGE_K3S_WORKLOAD_INTERVAL_SECONDS = '0'
            EDGE_STATE_DIR                     = Join-Path $workDir 'state'
            HIL_KUBECONFIG                     = Join-Path $workDir 'kube/kubeconfig.yaml'
            STUB_FIXTURES                      = $fixtureDir
            STUB_CMP_STATUS                    = if ($ConfigDiffers) { '1' } else { '0' }
        }
        foreach ($key in $ExtraEnv.Keys) { $envVars[$key] = $ExtraEnv[$key] }

        $stubs = @{
            sudo      = $script:SudoStub
            curl      = if ($DownloadFails) { 'echo "curl: (22) The requested URL returned error: 404" >&2; exit 22' } else { $script:CurlStub }
            kubectl   = 'exit 0'
            systemctl = 'exit 0'
            hostname  = 'echo test-host'
        }
        Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workDir -EnvVars $envVars -Stubs $stubs `
            -ScriptArgs (@('--node-name', 'test-node', '--data-dir', (Join-Path $workDir 'data')) + $ExtraArgs)
    }

    # Return the positions of the logged calls that match a pattern, in call order.
    function Get-CallIndex {
        param([string[]]$Calls, [string]$Pattern)

        @(for ($index = 0; $index -lt $Calls.Count; $index++) { if ($Calls[$index] -match $Pattern) { $index } })
    }
}

Describe '01-install-k3s.sh release planning' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Steps through every intermediate minor release to the pinned version' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1'

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Installed K3s:\s+v1\.32\.6\+k3s1'
        $result.StdOut | Should -Match 'Release Steps:\s+v1\.33\.13\+k3s2 v1\.34\.12\+k3s1 v1\.35\.9\+k3s1'
    }

    It 'Upgrades directly within the pinned minor' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.35.2+k3s1'

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Release Steps:\s+v1\.35\.9\+k3s1\s*\r?\n'
    }

    It 'Plans no release when the pinned version is installed' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.35.9+k3s1'

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Release Steps:\s+none \(already at pinned version\)'
    }

    It 'Installs the pinned version directly when K3s is absent' {
        $result = Invoke-K3sPreview

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Installed K3s:\s+none'
        $result.StdOut | Should -Match 'Release Steps:\s+v1\.35\.9\+k3s1'
        $result.StdOut | Should -Match 'Node:\s+test-host'
    }

    It 'Refuses to downgrade a newer installed release' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.36.1+k3s1'

        $result.ExitCode | Should -Not -Be 0
        ($result.StdOut + $result.StdErr) | Should -Match 'refusing to downgrade'
    }

    It 'Stops when an intermediate minor has no pinned release' {
        $steps = 'v1.33.13+k3s2|aaaa|bbbb'
        $result = Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1' -ExtraEnv @{ EDGE_K3S_UPGRADE_STEPS = $steps }

        $result.ExitCode | Should -Not -Be 0
        ($result.StdOut + $result.StdErr) | Should -Match 'No pinned K3s release for v1\.34'
    }

    It 'Stops when the installed version cannot be read' {
        $result = Invoke-K3sPreview -InstalledVersion 'unknown'

        $result.ExitCode | Should -Not -Be 0
        ($result.StdOut + $result.StdErr) | Should -Match 'Cannot determine the installed K3s version'
    }

    It 'Reports that an installed node keeps its registered name' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1'

        $result.StdOut | Should -Match 'Node:\s+existing node name'
    }

    It 'Uses an explicit node name override' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1' -ExtraEnv @{ EDGE_NODE_NAME = 'hil-lab-01' }

        $result.StdOut | Should -Match 'Node:\s+hil-lab-01'
    }
}

Describe '01-install-k3s.sh datastore backup planning' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Copies SQLite when an uninitialized etcd directory sits beside state.db' {
        $result = Invoke-DatastorePreview -Paths @('server/db/etcd/', 'server/db/state.db')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Datastore Backup:\s+SQLite, token, and certificate copy'
    }

    It 'Snapshots etcd when an etcd member is initialized' {
        $result = Invoke-DatastorePreview -Paths @('server/db/etcd/member/', 'server/db/state.db')

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Datastore Backup:\s+etcd snapshot'
    }

    It 'Reports that an upgrade without a datastore stops' {
        $result = Invoke-DatastorePreview

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Datastore Backup:\s+no datastore found'
    }

    It 'Defers detection to the install when sudo needs a password' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.32.6+k3s1'

        $result.StdOut | Should -Match 'Datastore Backup:\s+detected with sudo during install'
    }

    It 'Omits the backup line when no binary changes' {
        $result = Invoke-K3sPreview -InstalledVersion 'v1.35.9+k3s1'

        $result.StdOut | Should -Not -Match 'Datastore Backup'
    }
}

Describe '01-install-k3s.sh install and upgrade' -Tag 'Unit' -Skip:(-not $script:InstallToolsPresent) {
    It 'Waits for kube-system workloads to settle after each release before installing the next' {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.33.13+k3s2' `
            -Workloads @($script:NoWorkloads, $script:ReadyWorkloads, $script:StuckWorkloads, $script:ReadyWorkloads)

        $result.ExitCode | Should -Be 0
        # First release: none yet, ready, stuck, then ready twice. Second release and the final check: ready twice.
        $checks = Get-CallIndex $result.Calls ' kubectl get deployments,daemonsets '
        $checks.Count | Should -Be 9
        $nextRelease = (Get-CallIndex $result.Calls '^sudo install -m 0755 \S+/k3s-v1\.35\.9\+k3s1 ')[0]
        $nextRelease | Should -BeGreaterThan $checks[4]
        $nextRelease | Should -BeLessThan $checks[5]
    }

    It 'Stops and names the release when kube-system workloads never roll out' {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.33.13+k3s2' -Workloads @($script:StuckWorkloads) `
            -ExtraEnv @{ EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS = '1'; EDGE_K3S_WORKLOAD_INTERVAL_SECONDS = '0.1' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'K3s v1\.34\.12\+k3s1: kube-system workloads did not finish rolling out within 1s: Deployment/coredns'
        Get-CallIndex $result.Calls '/k3s-v1\.35\.9\+k3s1 ' | Should -BeNullOrEmpty
    }

    It 'Reports <Cause> when the deadline arrives' -ForEach @(
        @{ Cause = 'workloads that were ready but not yet confirmed'; Workloads = 'ready'; Message = 'they were ready on the last check, which a second check had not yet confirmed' }
        @{ Cause = 'a listing failure'; Workloads = 'FAIL'; Message = 'the workloads could not be listed' }
    ) {
        $fixture = if ($Workloads -eq 'ready') { $script:ReadyWorkloads } else { $Workloads }
        # A zero timeout makes the first check the last one, so its result decides the message.
        $result = Invoke-K3sInstall -InstalledVersion 'v1.33.13+k3s2' -Workloads @($fixture) `
            -ExtraEnv @{ EDGE_K3S_WORKLOAD_TIMEOUT_SECONDS = '0' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ('K3s v1\.34\.12\+k3s1: kube-system workloads did not finish rolling out within 0s: ' + [regex]::Escape($Message))
        Get-CallIndex $result.Calls '/k3s-v1\.35\.9\+k3s1 ' | Should -BeNullOrEmpty
    }

    It 'Leaves configuration, service, and datastore untouched when a release download fails' {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.35.2+k3s1' -ConfigDiffers -DownloadFails -ExtraArgs @('--allow-config-change')

        $result.ExitCode | Should -Not -Be 0
        Get-CallIndex $result.Calls '^curl ' | Should -Not -BeNullOrEmpty
        foreach ($change in @(
                '^sudo install -m 0600 \S+ /etc/rancher/k3s/config\.yaml$'
                '^sudo install -m 0644 \S+ /etc/systemd/system/k3s\.service$'
                '^sudo systemctl (daemon-reload|stop|restart)'
                ' etcd-snapshot '
                '^sudo install -m 0755 ')) {
            Get-CallIndex $result.Calls $change | Should -BeNullOrEmpty -Because "$change must not run before verified downloads"
        }
    }

    It 'Asks for consent before downloading when an existing node''s configuration would change' {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.35.2+k3s1' -ConfigDiffers

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'rerun with --allow-config-change'
        Get-CallIndex $result.Calls '^curl ' | Should -BeNullOrEmpty
    }

    It 'Changes managed configuration only after every release is verified and the datastore is saved' {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.33.13+k3s2' -ConfigDiffers -ExtraArgs @('--allow-config-change')

        $result.ExitCode | Should -Be 0
        $lastDownload = (Get-CallIndex $result.Calls '^curl ')[-1]
        $backup = (Get-CallIndex $result.Calls ' etcd-snapshot save ')[0]
        $config = (Get-CallIndex $result.Calls '^sudo install -m 0600 \S+ /etc/rancher/k3s/config\.yaml$')[0]
        $reload = (Get-CallIndex $result.Calls '^sudo systemctl daemon-reload$')[0]
        $firstRelease = (Get-CallIndex $result.Calls '^sudo install -m 0755 \S+/k3s-v1\.34\.12\+k3s1 ')[0]
        $lastDownload | Should -BeLessThan $backup
        $backup | Should -BeLessThan $config
        $config | Should -BeLessThan $reload
        $reload | Should -BeLessThan $firstRelease
    }

    It 'Keeps the pinned binary and restarts only for a configuration change (changed: <ConfigDiffers>)' -TestCases @(
        @{ ConfigDiffers = $true; Changes = 1 }
        @{ ConfigDiffers = $false; Changes = 0 }
    ) {
        $result = Invoke-K3sInstall -InstalledVersion 'v1.35.9+k3s1' -ConfigDiffers:$ConfigDiffers -ExtraArgs @('--allow-config-change')

        $result.ExitCode | Should -Be 0
        Get-CallIndex $result.Calls '^curl ' | Should -BeNullOrEmpty
        (Get-CallIndex $result.Calls '^sudo install -m 0600 \S+ /etc/rancher/k3s/config\.yaml$').Count | Should -Be $Changes
        (Get-CallIndex $result.Calls '^sudo systemctl restart k3s$').Count | Should -Be $Changes
    }
}
