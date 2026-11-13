# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

# cspell:ignore addext keyout newkey

BeforeAll {
    $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
    $script:DeployScript = Get-Content -Raw (Join-Path $script:RepoRoot 'infrastructure/setup/03-deploy-osmo.sh')
    $script:CleanupScript = Get-Content -Raw (Join-Path $script:RepoRoot 'infrastructure/setup/cleanup/uninstall-osmo.sh')
    $script:OsmoValues = Get-Content -Raw (
        Join-Path $script:RepoRoot 'infrastructure/setup/values/osmo-control-plane.yaml'
    )
    $script:NginxConfig = Get-Content -Raw (
        Join-Path $script:RepoRoot 'data-management/viewer/frontend/nginx.conf.template'
    )
    $script:DataviewerTerraform = Get-Content -Raw (
        Join-Path $script:RepoRoot 'infrastructure/terraform/modules/dataviewer/container-apps.tf'
    )
    $frontendDockerfile = Get-Content -Raw (
        Join-Path $script:RepoRoot 'data-management/viewer/frontend/Dockerfile'
    )
    $script:NginxImage = [regex]::Match(
        $frontendDockerfile,
        '(?m)^FROM\s+(nginxinc/nginx-unprivileged:\S+)\s+AS\s+serve$'
    ).Groups[1].Value
    $script:NodeImage = [regex]::Match(
        $frontendDockerfile,
        '(?m)^FROM\s+(node:\S+)\s+AS\s+build$'
    ).Groups[1].Value
    $script:PythonImage = [regex]::Match(
        $script:CleanupScript,
        '(?m)^python_image="([^"]+)"$'
    ).Groups[1].Value
    $script:RedisTlsClient = Get-Content -Raw (
        Join-Path $script:RepoRoot 'infrastructure/setup/cleanup/redis_tls_client.py'
    )
    $script:ProductionClients = @(
        Get-ChildItem (Join-Path $script:RepoRoot 'infrastructure/setup') -Recurse -File -Filter '*.sh'
        Get-ChildItem (Join-Path $script:RepoRoot 'data-management/viewer/backend/src') -Recurse -File -Filter '*.py'
        Get-Item (Join-Path $script:RepoRoot 'data-management/viewer/frontend/nginx.conf.template')
    )

    function Invoke-Docker {
        param(
            [Parameter(Mandatory)]
            [string[]] $Arguments,

            [ValidateRange(0, 3600)]
            [int] $TimeoutSeconds = 0
        )

        if ($TimeoutSeconds -gt 0) {
            $Process = Start-Process -FilePath 'docker' -ArgumentList $Arguments -NoNewWindow -PassThru
            try {
                if (-not $Process.WaitForExit($TimeoutSeconds * 1000)) {
                    $Process.Kill($true)
                    throw "docker $($Arguments -join ' ') timed out after $TimeoutSeconds seconds"
                }
                if ($Process.ExitCode -ne 0) {
                    throw "docker $($Arguments -join ' ') failed with exit code $($Process.ExitCode)"
                }
                return
            }
            finally {
                $Process.Dispose()
            }
        }

        $output = & docker @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "docker $($Arguments -join ' ') failed: $($output -join "`n")"
        }
        return $output
    }

    function Initialize-DockerImage {
        param(
            [Parameter(Mandatory)]
            [string] $Image
        )

        try {
            Invoke-Docker -Arguments @('image', 'inspect', $Image) | Out-Null
            return
        }
        catch {
            for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
                try {
                    Invoke-Docker -Arguments @('pull', $Image) -TimeoutSeconds 120 | Out-Null
                    return
                }
                catch {
                    if ($Attempt -eq 3) {
                        throw
                    }
                }
            }
        }
    }

    function Set-TlsFixtureStage {
        param(
            [hashtable] $Status,
            [ValidateSet(
                'initialization', 'untrusted-readiness', 'untrusted-rejection',
                'hostname-readiness', 'hostname-rejection', 'trusted-readiness',
                'trusted-http', 'redis-untrusted', 'redis-hostname', 'redis-trusted'
            )]
            [string] $Stage
        )

        $Status.Clear()
        $Status.Stage = $Stage
    }

    function Assert-TlsFixtureValue {
        param(
            [hashtable] $Status,
            [ValidateSet('http-status', 'running', 'tls-error', 'backend-request', 'receipt-reset', 'redis-auth', 'exit-code')]
            [string] $Check,
            [int] $Expected,
            [int] $Actual,
            [switch] $Nonzero,
            [switch] $ObserveOnly
        )

        $Status.Check = $Check
        $Status.Expected = if ($Nonzero) { 'nonzero' } else { [string]$Expected }
        $Status.Actual = [string]$Actual
        if (-not $ObserveOnly) {
            if ($Nonzero) {
                $Actual | Should -Not -Be 0
            }
            else {
                $Actual | Should -Be $Expected
            }
        }
    }

    function Get-TlsFixtureFailureSummary {
        param([hashtable] $Status)

        $summary = 'TLS fixture verification failed.'
        $stages = (Get-Command Set-TlsFixtureStage).Parameters['Stage'].Attributes.ValidValues
        if ($Status.Stage -cin $stages) {
            $summary += " stage=$($Status.Stage)"
        }
        if ($Status.Check -cin @('http-status', 'running', 'tls-error', 'backend-request', 'receipt-reset', 'redis-auth', 'exit-code')) {
            $summary += " check=$($Status.Check)"
            foreach ($key in @('Expected', 'Actual')) {
                if ($Status[$key] -cmatch '\A(?:-?[0-9]{1,10}|nonzero)\z') {
                    $summary += " $($key.ToLowerInvariant())=$($Status[$key])"
                }
            }
        }
        return $summary
    }

    function Invoke-NginxFixture {
        param(
            [Parameter(Mandatory)]
            [string] $Name,

            [Parameter(Mandatory)]
            [string] $BackendHost,

            [Parameter(Mandatory)]
            [string] $Network,

            [Parameter(Mandatory)]
            [string] $FixtureDirectory,

            [Parameter(Mandatory)]
            [AllowEmptyCollection()]
            [System.Collections.Generic.List[string]] $Containers,

            [hashtable] $Status = @{},

            [Parameter()]
            [switch] $TrustFixtureCertificate
        )

        $arguments = @(
            'run', '--pull', 'never', '--detach', '--name', $Name,
            '--network', $Network,
            '--publish', '127.0.0.1::8080',
            '--env', "NGINX_BACKEND_HOST=$BackendHost",
            '--env', 'NGINX_BACKEND_SCHEME=https',
            '--env', 'NGINX_RESOLVER=127.0.0.11',
            '--entrypoint', '/bin/sh',
            '--volume', (
                "$(Join-Path $script:RepoRoot 'data-management/viewer/frontend/start-nginx.sh')" +
                ':/usr/local/bin/start-nginx.sh:ro'
            ),
            '--volume', (
                "$(Join-Path $script:RepoRoot 'data-management/viewer/frontend/nginx.conf.template')" +
                ':/etc/nginx/templates/default.conf.template:ro'
            )
        )
        if ($TrustFixtureCertificate) {
            $arguments += @(
                '--volume',
                "${FixtureDirectory}/server.crt:/etc/ssl/certs/ca-certificates.crt:ro"
            )
        }
        $arguments += @($script:NginxImage, '/usr/local/bin/start-nginx.sh')

        $Containers.Add($Name)
        Invoke-Docker -Arguments $arguments | Out-Null

        return Wait-NginxFixture -Name $Name -Status $Status
    }

    function Wait-NginxFixture {
        param(
            [Parameter(Mandatory)]
            [string] $Name,

            [hashtable] $Status = @{},

            [ValidateRange(0, 60)]
            [int] $TimeoutSeconds = 10
        )

        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        do {
            $running = Invoke-Docker -Arguments @('inspect', '--format', '{{.State.Running}}', $Name)
            Assert-TlsFixtureValue -Status $Status -Check 'running' -Expected 1 `
                -Actual ([int]($running -eq 'true')) -ObserveOnly
            if ($running -ne 'true') {
                throw 'NGINX fixture exited before HTTP readiness'
            }
            $portOutput = Invoke-Docker -Arguments @('port', $Name, '8080/tcp')
            if ($portOutput) {
                $port = ($portOutput -split ':')[-1]
                Assert-TlsFixtureValue -Status $Status -Check 'http-status' -Expected 200 -Actual 0 -ObserveOnly
                try {
                    $response = Invoke-WebRequest -Uri "http://127.0.0.1:${port}/" `
                        -NoProxy -SkipHttpErrorCheck -TimeoutSec 2
                }
                catch {
                    # A published port may precede the HTTP listener.
                    $response = $null
                }
                if ($response) {
                    Assert-TlsFixtureValue -Status $Status -Check 'http-status' -Expected 200 `
                        -Actual ([int]$response.StatusCode) -ObserveOnly
                }
                if ($response -and $response.StatusCode -eq 200) {
                    return $port
                }
            }
            Start-Sleep -Milliseconds 100
        } while ([DateTime]::UtcNow -lt $deadline)

        throw 'Timed out waiting for NGINX fixture HTTP readiness'
    }

    function Assert-NginxTlsRejection {
        param(
            [Parameter(Mandatory)]
            [string] $Name,

            [Parameter(Mandatory)]
            [string] $Port,

            [Parameter(Mandatory)]
            [string] $RequestPath,

            [Parameter(Mandatory)]
            [string] $TlsError,
            [hashtable] $Status = @{}
        )

        Assert-TlsFixtureValue -Status $Status -Check 'http-status' -Expected 502 -Actual 0 -ObserveOnly
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:${Port}/api/" `
            -NoProxy -SkipHttpErrorCheck -TimeoutSec 5
        Assert-TlsFixtureValue -Status $Status -Check 'http-status' -Expected 502 -Actual ([int]$response.StatusCode)
        Assert-TlsFixtureValue -Status $Status -Check 'tls-error' -Expected 1 -Actual 0 -ObserveOnly
        $logs = Invoke-Docker -Arguments @('logs', $Name)
        Assert-TlsFixtureValue -Status $Status -Check 'tls-error' -Expected 1 `
            -Actual ([int](($logs -join "`n") -match $TlsError))
        Assert-TlsFixtureValue -Status $Status -Check 'backend-request' -Expected 0 `
            -Actual ([int](Test-Path -LiteralPath $RequestPath))
    }

    function Get-TlsFixtureDiagnostics {
        param(
            [Parameter(Mandatory)]
            [AllowEmptyCollection()]
            [System.Collections.Generic.List[string]] $Containers
        )

        foreach ($container in $Containers) {
            # Never dump inspect JSON, environment values, or raw request/startup logs.
            try {
                $state = Invoke-Docker -Arguments @(
                    'inspect', '--format',
                    'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}', $container
                )
            }
            catch {
                $state = ''
            }
            if ($state -match '^status=\w+ exit=\d+ oom=(true|false)$') {
                "${container}: $state"
            }
            else {
                "${container}: status unavailable"
            }
            try {
                $logs = (Invoke-Docker -Arguments @('logs', '--tail', '50', $container)) -join "`n"
            }
            catch {
                "${container}: logs unavailable"
                continue
            }
            foreach ($indicator in @(
                'start worker processes', 'emerg', 'Permission denied', 'not found',
                'invalid', 'upstream SSL certificate verify error',
                'upstream SSL certificate does not match', 'connect\(\) failed',
                'self-signed certificate', 'certificate has expired', 'certificate is not yet valid',
                'unable to get local issuer certificate'
            )) {
                if ($logs -match $indicator) {
                    "${container}: log indicator: $indicator"
                }
            }
        }
    }
}

Describe 'Bounded Docker commands' -Tag 'Unit' {
    BeforeEach {
        $script:DockerProcess = [pscustomobject]@{
            ExitCode         = 0
            TimedOut         = $false
            Killed           = $false
            Disposed         = $false
            WaitMilliseconds = 0
        }
        $script:DockerProcess | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {
            param([int] $Milliseconds)
            $this.WaitMilliseconds = $Milliseconds
            return -not $this.TimedOut
        }
        $script:DockerProcess | Add-Member -MemberType ScriptMethod -Name Kill -Value {
            param([bool] $EntireProcessTree)
            $this.Killed = $EntireProcessTree
        }
        $script:DockerProcess | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
            $this.Disposed = $true
        }
        Mock Start-Process { return $script:DockerProcess }
    }

    It 'waits only for the configured deadline and disposes a successful process' {
        Invoke-Docker -Arguments @('pull', $script:NginxImage) -TimeoutSeconds 120

        $script:DockerProcess.WaitMilliseconds | Should -Be 120000
        $script:DockerProcess.Killed | Should -BeFalse
        $script:DockerProcess.Disposed | Should -BeTrue
        Should -Invoke Start-Process -Times 1 -Exactly -ParameterFilter {
            $FilePath -eq 'docker' -and $ArgumentList[0] -eq 'pull' -and
            $ArgumentList[1] -eq $script:NginxImage -and $NoNewWindow -and $PassThru
        }
    }

    It 'kills the process tree and disposes the client when the deadline expires' {
        $script:DockerProcess.TimedOut = $true

        { Invoke-Docker -Arguments @('pull', $script:NginxImage) -TimeoutSeconds 120 } |
        Should -Throw '*timed out after 120 seconds*'

        $script:DockerProcess.WaitMilliseconds | Should -Be 120000
        $script:DockerProcess.Killed | Should -BeTrue
        $script:DockerProcess.Disposed | Should -BeTrue
    }

    It 'reports nonzero exit codes without killing a completed process' {
        $script:DockerProcess.ExitCode = 1

        { Invoke-Docker -Arguments @('pull', $script:NginxImage) -TimeoutSeconds 120 } |
        Should -Throw '*failed with exit code 1*'

        $script:DockerProcess.Killed | Should -BeFalse
        $script:DockerProcess.Disposed | Should -BeTrue
    }
}

Describe 'TLS fixture image preparation' -Tag 'Unit' {
    It 'reuses a cached pinned image without pulling' {
        Mock Invoke-Docker {}

        Initialize-DockerImage -Image $script:NginxImage

        Should -Invoke Invoke-Docker -Times 1 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'image' -and $Arguments[1] -eq 'inspect' -and
            $Arguments[2] -eq $script:NginxImage
        }
        Should -Invoke Invoke-Docker -Times 0 -Exactly -ParameterFilter { $Arguments[0] -eq 'pull' }
    }

    It 'retries a transient pull failure using the same pinned image' {
        $script:PullAttempts = 0
        Mock Invoke-Docker { throw 'Image not cached' } -ParameterFilter { $Arguments[0] -eq 'image' }
        Mock Invoke-Docker {
            $script:PullAttempts++
            if ($script:PullAttempts -eq 1) {
                throw '502 Bad Gateway'
            }
        } -ParameterFilter { $Arguments[0] -eq 'pull' }

        Initialize-DockerImage -Image $script:NginxImage

        Should -Invoke Invoke-Docker -Times 2 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'pull' -and $Arguments[1] -eq $script:NginxImage -and $TimeoutSeconds -eq 120
        }
    }

    It 'fails after three unsuccessful pulls' {
        Mock Invoke-Docker { throw 'Image not cached' } -ParameterFilter { $Arguments[0] -eq 'image' }
        Mock Invoke-Docker { throw '502 Bad Gateway' } -ParameterFilter { $Arguments[0] -eq 'pull' }

        { Initialize-DockerImage -Image $script:NginxImage } | Should -Throw '*502 Bad Gateway*'

        Should -Invoke Invoke-Docker -Times 3 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'pull' -and $Arguments[1] -eq $script:NginxImage -and $TimeoutSeconds -eq 120
        }
    }

    It 'fails after three timed-out pulls' {
        Mock Invoke-Docker { throw 'Image not cached' } -ParameterFilter { $Arguments[0] -eq 'image' }
        Mock Invoke-Docker { throw 'docker pull timed out after 120 seconds' } -ParameterFilter {
            $Arguments[0] -eq 'pull'
        }

        { Initialize-DockerImage -Image $script:NginxImage } | Should -Throw '*timed out after 120 seconds*'

        Should -Invoke Invoke-Docker -Times 3 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'pull' -and $Arguments[1] -eq $script:NginxImage -and $TimeoutSeconds -eq 120
        }
    }
}

Describe 'TLS fixture readiness and rejection guards' -Tag 'Unit' {
    BeforeEach {
        Mock Start-Sleep {}
        Mock Invoke-Docker {
            switch ($Arguments[0]) {
                'inspect' { 'true' }
                'port' { '127.0.0.1:12345' }
                'logs' { 'upstream SSL certificate verify error: (18:self-signed certificate)' }
            }
        }
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 200 } }
    }

    It 'requires a successful HTTP response from the published port' {
        Wait-NginxFixture -Name 'fixture' -TimeoutSeconds 0 | Should -Be '12345'
        Should -Invoke Invoke-WebRequest -Times 1 -Exactly -ParameterFilter {
            $Uri -eq 'http://127.0.0.1:12345/' -and $NoProxy -and $TimeoutSec -eq 2
        }
    }

    It 'rejects an exited proxy even when its port remains published' {
        Mock Invoke-Docker { 'false' } -ParameterFilter { $Arguments[0] -eq 'inspect' }
        { Wait-NginxFixture -Name 'fixture' -TimeoutSeconds 0 } | Should -Throw '*exited*'
        Should -Invoke Invoke-WebRequest -Times 0
    }

    It 'rejects a published but unreachable proxy' {
        Mock Invoke-WebRequest { throw 'Connection refused' }
        { Wait-NginxFixture -Name 'fixture' -TimeoutSeconds 0 } | Should -Throw '*HTTP readiness*'
    }

    It 'rejects an HTTP error during readiness' {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 502 } }
        { Wait-NginxFixture -Name 'fixture' -TimeoutSeconds 0 } | Should -Throw '*HTTP readiness*'
    }

    It 'registers the proxy for cleanup before readiness fails' {
        $containers = [System.Collections.Generic.List[string]]::new()
        Mock Wait-NginxFixture { throw 'HTTP readiness failed' }
        {
            Invoke-NginxFixture -Name 'fixture' -BackendHost 'tls-valid' -Network 'fixture-network' `
                -FixtureDirectory $TestDrive -Containers $containers
        } | Should -Throw '*HTTP readiness failed*'
        $containers | Should -Contain 'fixture'
    }

    It 'registers the proxy even if Docker fails after creating it' {
        $containers = [System.Collections.Generic.List[string]]::new()
        Mock Invoke-Docker { throw 'Container startup failed' } -ParameterFilter { $Arguments[0] -eq 'run' }
        {
            Invoke-NginxFixture -Name 'fixture' -BackendHost 'tls-valid' -Network 'fixture-network' `
                -FixtureDirectory $TestDrive -Containers $containers
        } | Should -Throw '*Container startup failed*'
        $containers | Should -Contain 'fixture'
    }

    It 'accepts only HTTP 502 with certificate verification evidence and no backend request' {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 502 } }
        Assert-NginxTlsRejection -Name 'fixture' -Port '12345' `
            -RequestPath (Join-Path $TestDrive 'requests') -TlsError 'upstream SSL certificate verify error'
    }

    It 'does not treat connection refusal as certificate rejection' {
        Mock Invoke-WebRequest { throw 'Connection refused' }
        {
            Assert-NginxTlsRejection -Name 'fixture' -Port '12345' `
                -RequestPath (Join-Path $TestDrive 'requests') -TlsError 'upstream SSL certificate verify error'
        } | Should -Throw '*Connection refused*'
    }

    It 'rejects unexpected HTTP status <StatusCode>' -ForEach @(
        @{ StatusCode = 200 }
        @{ StatusCode = 503 }
    ) {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = $StatusCode } }
        {
            Assert-NginxTlsRejection -Name 'fixture' -Port '12345' `
                -RequestPath (Join-Path $TestDrive 'requests') -TlsError 'upstream SSL certificate verify error'
        } | Should -Throw
    }

    It 'does not accept an unrelated upstream failure as certificate rejection' {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 502 } }
        Mock Invoke-Docker { 'connect() failed (111: Connection refused)' }
        {
            Assert-NginxTlsRejection -Name 'fixture' -Port '12345' `
                -RequestPath (Join-Path $TestDrive 'requests') -TlsError 'upstream SSL certificate verify error'
        } | Should -Throw
    }

    It 'rejects certificate failures that still sent an application request' {
        Mock Invoke-WebRequest { [pscustomobject]@{ StatusCode = 502 } }
        $requestPath = Join-Path $TestDrive 'requests'
        'GET /api/' | Set-Content -LiteralPath $requestPath
        {
            Assert-NginxTlsRejection -Name 'fixture' -Port '12345' `
                -RequestPath $requestPath -TlsError 'upstream SSL certificate verify error'
        } | Should -Throw
    }

    It 'reports only allowlisted status and startup indicators' {
        Mock Invoke-Docker { 'status=exited exit=1 oom=false' } -ParameterFilter { $Arguments[0] -eq 'inspect' }
        Mock Invoke-Docker {
            'nginx: [emerg] invalid configuration /private/path Authorization: sensitive-fixture-value'
        } -ParameterFilter { $Arguments[0] -eq 'logs' }
        $containers = [System.Collections.Generic.List[string]]::new()
        $containers.Add('fixture')

        $diagnostics = (Get-TlsFixtureDiagnostics -Containers $containers) -join "`n"
        $diagnostics | Should -Match 'status=exited exit=1 oom=false'
        $diagnostics | Should -Match 'log indicator: emerg'
        $diagnostics | Should -Match 'log indicator: invalid'
        $diagnostics | Should -Not -Match 'sensitive-fixture-value|Authorization|/private/path'
    }

    It 'keeps diagnostics available when inspect and logs fail' {
        Mock Invoke-Docker { throw 'sensitive-fixture-value' }
        $containers = [System.Collections.Generic.List[string]]::new()
        $containers.Add('fixture')

        $diagnostics = (Get-TlsFixtureDiagnostics -Containers $containers) -join "`n"
        $diagnostics | Should -Match 'status unavailable'
        $diagnostics | Should -Match 'logs unavailable'
        $diagnostics | Should -Not -Match 'sensitive-fixture-value'
    }

    It 'sanitizes the complete failure report when diagnostics are <DiagnosticState>' -ForEach @(
        @{ DiagnosticState = 'available' }
        @{ DiagnosticState = 'unavailable' }
    ) {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $PSCommandPath, [ref]$null, [ref]$null
        )
        $fixtureTry = @($ast.FindAll({
                    param($node)
                    $node -is [System.Management.Automation.Language.TryStatementAst] -and
                    $node.CatchClauses.Extent.Text -match 'Get-TlsFixtureDiagnostics -Containers'
                }, $true))
        $fixtureTry.Count | Should -Be 1
        $fixture = [scriptblock]::Create($fixtureTry[0].Extent.Text)
        $network = 'fixture-network'
        $serverContainer = 'fixture-server'
        $fixtureDirectory = $TestDrive
        $containers = [System.Collections.Generic.List[string]]::new()
        Mock Invoke-Docker {
            throw 'docker run --env AUTHORIZATION=sensitive-fixture-value --volume /private/local-fixture:/fixture failed'
        } -ParameterFilter { $Arguments[0] -eq 'run' }
        Mock Invoke-Docker {
            if ($DiagnosticState -eq 'unavailable') {
                throw 'sensitive-fixture-value /private/local-fixture'
            }
            'status=exited exit=1 oom=false'
        } -ParameterFilter { $Arguments[0] -eq 'inspect' }
        Mock Invoke-Docker {
            if ($DiagnosticState -eq 'unavailable') {
                throw 'sensitive-fixture-value /private/local-fixture'
            }
            'nginx: [emerg] invalid configuration /private/local-fixture AUTHORIZATION=sensitive-fixture-value'
        } -ParameterFilter { $Arguments[0] -eq 'logs' }
        Mock docker {}

        $failure = { & $fixture } | Should -Throw -PassThru
        $report = ($failure | Out-String) + $failure.Exception.ToString()
        $report | Should -Match 'TLS fixture verification failed'
        $report | Should -Not -Match 'sensitive-fixture-value|AUTHORIZATION|/private/local-fixture|docker run|--volume'
        if ($DiagnosticState -eq 'available') {
            $report | Should -Match 'fixture-server: status=exited exit=1 oom=false'
            $report | Should -Match 'log indicator: emerg'
        }
        else {
            $report | Should -Match 'status unavailable'
            $report | Should -Match 'logs unavailable'
        }
        $containers | Should -Contain $serverContainer
        Should -Invoke Invoke-Docker -Times 1 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'run' -and $Arguments -contains "${fixtureDirectory}:/fixture"
        }
        Should -Invoke docker -Times 1 -Exactly -ParameterFilter {
            ($args -join ' ') -eq "rm --force $serverContainer"
        }
        Should -Invoke docker -Times 1 -Exactly -ParameterFilter {
            ($args -join ' ') -eq "network rm $network"
        }
    }

    It 'reports sanitized structured details for <Stage> <Check>' -ForEach @(
        @{ Stage = 'untrusted-readiness'; Check = 'running'; Expected = '1'; Actual = '0' }
        @{ Stage = 'hostname-readiness'; Check = 'running'; Expected = '1'; Actual = '0' }
        @{ Stage = 'trusted-readiness'; Check = 'running'; Expected = '1'; Actual = '0' }
        @{ Stage = 'untrusted-rejection'; Check = 'http-status'; Expected = '502'; Actual = '503' }
        @{ Stage = 'hostname-rejection'; Check = 'http-status'; Expected = '502'; Actual = '503' }
        @{ Stage = 'trusted-http'; Check = 'http-status'; Expected = '204'; Actual = '503' }
        @{ Stage = 'trusted-http'; Check = 'receipt-reset'; Expected = '0'; Actual = '1' }
        @{ Stage = 'redis-untrusted'; Check = 'exit-code'; Expected = 'nonzero'; Actual = '0' }
        @{ Stage = 'redis-hostname'; Check = 'exit-code'; Expected = 'nonzero'; Actual = '0' }
        @{ Stage = 'redis-trusted'; Check = 'exit-code'; Expected = '0'; Actual = '7' }
    ) {
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            $PSCommandPath, [ref]$null, [ref]$null
        )
        $fixtureTry = $ast.Find({
                param($node)
                $node -is [System.Management.Automation.Language.TryStatementAst] -and
                $node.CatchClauses.Extent.Text -match 'Get-TlsFixtureDiagnostics -Containers'
            }, $true)
        $fixture = [scriptblock]::Create($fixtureTry.Extent.Text)
        $network = 'fixture-network'
        $serverContainer = 'fixture-server'
        $fixtureDirectory = $TestDrive
        $requestPath = Join-Path $TestDrive 'requests'
        $suffix = 'fixture'
        $containers = [System.Collections.Generic.List[string]]::new()
        Mock Start-Sleep {}
        Mock Invoke-Docker {
            switch ($Arguments[0]) {
                'inspect' {
                    if ($Arguments -contains '{{.State.Running}}') {
                        if ($status.Stage -eq $Stage) { 'false' } else { 'true' }
                    }
                    else { 'status=running exit=0 oom=false' }
                }
                'port' { '127.0.0.1:12345' }
                'logs' {
                    'upstream SSL certificate verify error; upstream SSL certificate does not match'
                    'Authorization: sensitive-fixture-value /private/local-fixture'
                }
            }
        }
        Mock Invoke-WebRequest {
            $code = if ($status.Stage -eq $Stage -and $Check -eq 'http-status') { 503 }
            elseif ($status.Stage -like '*readiness') { 200 }
            elseif ($status.Stage -eq 'trusted-http') { 204 }
            else { 502 }
            [pscustomobject]@{ StatusCode = $code }
        }
        Mock docker {
            if ($args[0] -eq 'run') {
                $global:LASTEXITCODE = if ($status.Stage -eq $Stage) { [int]$Actual }
                elseif ($status.Stage -eq 'redis-trusted') { 0 }
                else { 1 }
            }
        }
        Mock Test-Path {
            $status.Stage -in @('trusted-http', 'redis-trusted') -and $status.Check -ne 'receipt-reset'
        } -ParameterFilter {
            $LiteralPath -eq $requestPath
        }
        Mock Get-Content { 'GET /api/ AUTH' } -ParameterFilter { $LiteralPath -eq $requestPath }
        Mock Remove-Item {
            if ($Check -eq 'receipt-reset') {
                throw 'Permission denied /private/local-fixture Authorization: sensitive-fixture-value'
            }
        } -ParameterFilter { $LiteralPath -eq $requestPath }

        $failure = { & $fixture } | Should -Throw -PassThru
        $report = ($failure | Out-String) + $failure.Exception.ToString()
        $report | Should -Match "stage=$Stage check=$Check expected=$Expected actual=$Actual"
        $report | Should -Not -Match 'sensitive-fixture-value|Authorization|/private/local-fixture'
        $containers | Should -Contain $serverContainer
        Should -Invoke Invoke-Docker -Times 1 -Exactly -ParameterFilter {
            $Arguments[0] -eq 'run' -and $Arguments -contains "${fixtureDirectory}:/fixture"
        }
        Should -Invoke docker -Times 1 -Exactly -ParameterFilter {
            ($args -join ' ') -eq "network rm $network"
        }
        Should -Invoke docker -Times 1 -Exactly -ParameterFilter {
            ($args -join ' ') -eq "rm --force tls-nginx-untrusted-$suffix"
        }
        if ($Stage -in @('redis-hostname', 'redis-trusted') -or $Check -eq 'receipt-reset') {
            Should -Invoke Remove-Item -Times 1 -Exactly -ParameterFilter {
                $LiteralPath -eq $requestPath -and $Force
            }
        }
    }

    It 'omits non-allowlisted stages and structured values' {
        $status = @{
            Stage = '/private/local-fixture'
            Check = 'Authorization'
            Expected = 'sensitive-fixture-value'
            Actual = '/private/local-fixture'
        }
        Get-TlsFixtureFailureSummary -Status $status | Should -Be 'TLS fixture verification failed.'
        $status.Stage = 'trusted-http'
        $status.Check = 'http-status'
        Get-TlsFixtureFailureSummary -Status $status |
            Should -Be 'TLS fixture verification failed. stage=trusted-http check=http-status'
    }
}

Describe 'Production TLS certificate verification' -Tag 'Unit' {
    It 'contains no certificate verification bypasses in applicable production clients' {
        $productionClients = $script:ProductionClients | ForEach-Object {
            Get-Content -Raw -LiteralPath $_.FullName
        }
        $productionClients = $productionClients -join "`n"

        $productionClients | Should -Not -Match 'ssl\.CERT_NONE'
        $productionClients | Should -Not -Match 'check_hostname\s*=\s*False'
        $productionClients | Should -Not -Match '(?m)(?:^|[\s"''=])--insecure(?:[\s"'']|$)'
        $productionClients | Should -Not -Match 'proxy_ssl_verify\s+off'
        $productionClients | Should -Not -Match 'verify\s*=\s*False'
    }

    It 'bootstraps OSMO authorization without a privileged post-deploy HTTPS request' {
        $script:DeployScript | Should -Not -Match 'x-osmo-user'
        $script:DeployScript | Should -Not -Match '/api/auth/user/admin/roles'
        $script:DeployScript | Should -Not -Match 'https://localhost:8000'

        $script:OsmoValues | Should -Match '(?m)^\s+user:\s+"admin"$'
        $script:OsmoValues | Should -Match '(?m)^\s+roles:\s+"osmo-admin,osmo-backend,osmo-ctrl"$'
    }

    It 'verifies Azure Managed Redis for preflight and destructive cleanup' {
        $script:RedisTlsClient | Should -Match 'ssl\.create_default_context\(\)'
        $script:RedisTlsClient | Should -Match 'wrap_socket\(raw_socket, server_hostname=host\)'
        $script:RedisTlsClient | Should -Match '_encode_command\("AUTH", password\)'
        ([regex]::Matches($script:CleanupScript, 'command: \["python", "-c", \$client\]')).Count | Should -Be 2
        $script:CleanupScript | Should -Match 'redis_hostname=\$\(tf_get .*managed_redis_connection_info\.value\.hostname'
    }

    It 'verifies the Data Viewer backend chain and service identity' {
        $script:NginxConfig | Should -Match 'proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates\.crt;'
        $script:NginxConfig | Should -Match 'proxy_ssl_verify on;'
        $script:NginxConfig | Should -Match 'proxy_ssl_server_name on;'
        $script:NginxConfig | Should -Match 'proxy_ssl_name \$\{NGINX_BACKEND_HOST\};'
        $script:DataviewerTerraform | Should -Match (
            'name\s*=\s*"NGINX_BACKEND_HOST"\s+value\s*=\s*' +
            'azurerm_container_app\.backend\.ingress\[0\]\.fqdn'
        )
        $script:DataviewerTerraform | Should -Match 'name\s*=\s*"NGINX_BACKEND_SCHEME"\s+value\s*=\s*"https"'
    }

    It 'rejects invalid certificates before production clients send authenticated requests' -Skip:(
        -not (Get-Command docker -ErrorAction SilentlyContinue)
    ) {
        foreach ($Image in @($script:NodeImage, $script:NginxImage, $script:PythonImage)) {
            Initialize-DockerImage -Image $Image
        }

        $fixtureDirectory = Join-Path $TestDrive 'tls-fixture'
        $certificatePath = Join-Path $fixtureDirectory 'server.crt'
        $keyPath = Join-Path $fixtureDirectory 'server.key'
        $requestPath = Join-Path $fixtureDirectory 'requests'
        $serverPath = Join-Path $fixtureDirectory 'server.mjs'
        $suffix = [Guid]::NewGuid().ToString('N')
        $network = "tls-verification-$suffix"
        $serverContainer = "tls-server-$suffix"
        $containers = [System.Collections.Generic.List[string]]::new()
        New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null

        & openssl req -x509 -newkey rsa:2048 -nodes -days 1 `
            -subj '/CN=tls-valid' -addext 'subjectAltName=DNS:tls-valid' `
            -keyout $keyPath -out $certificatePath 2>$null
        $LASTEXITCODE | Should -Be 0

        @'
import { appendFileSync, readFileSync } from 'node:fs'
import { createServer } from 'node:tls'

const server = createServer(
  {
    cert: readFileSync('/fixture/server.crt'),
    key: readFileSync('/fixture/server.key'),
  },
  (socket) => {
    let authenticated = false
    socket.on('data', (data) => {
      appendFileSync('/fixture/requests', data)
      if (data.includes(Buffer.from('AUTH'))) {
        authenticated = true
        socket.write('+OK\r\n')
      } else if (authenticated) {
        socket.end('+PONG\r\n')
      } else {
        socket.end('HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
      }
    })
  },
)

server.listen(443, '0.0.0.0')
'@ | Set-Content -LiteralPath $serverPath

        try {
            $status = @{}
            Set-TlsFixtureStage -Status $status -Stage 'initialization'
            Invoke-Docker -Arguments @('network', 'create', $network) | Out-Null
            $containers.Add($serverContainer)
            Invoke-Docker -Arguments @(
                'run', '--pull', 'never', '--detach', '--name', $serverContainer,
                '--network', $network,
                '--network-alias', 'tls-valid',
                '--network-alias', 'tls-wrong',
                '--volume', "${fixtureDirectory}:/fixture",
                $script:NodeImage,
                'node', '/fixture/server.mjs'
            ) | Out-Null
            Set-TlsFixtureStage -Status $status -Stage 'untrusted-readiness'
            $untrustedNginx = "tls-nginx-untrusted-$suffix"
            $untrustedPort = Invoke-NginxFixture `
                -Name $untrustedNginx `
                -BackendHost 'tls-valid' `
                -Network $network `
                -FixtureDirectory $fixtureDirectory `
                -Containers $containers -Status $status
            Set-TlsFixtureStage -Status $status -Stage 'untrusted-rejection'
            Assert-NginxTlsRejection -Name $untrustedNginx -Port $untrustedPort `
                -RequestPath $requestPath -TlsError 'upstream SSL certificate verify error' -Status $status

            Set-TlsFixtureStage -Status $status -Stage 'hostname-readiness'
            $wrongHostNginx = "tls-nginx-wrong-host-$suffix"
            $wrongHostPort = Invoke-NginxFixture `
                -Name $wrongHostNginx `
                -BackendHost 'tls-wrong' `
                -Network $network `
                -FixtureDirectory $fixtureDirectory `
                -Containers $containers -Status $status `
                -TrustFixtureCertificate
            Set-TlsFixtureStage -Status $status -Stage 'hostname-rejection'
            Assert-NginxTlsRejection -Name $wrongHostNginx -Port $wrongHostPort `
                -RequestPath $requestPath -TlsError 'upstream SSL certificate does not match' -Status $status

            Set-TlsFixtureStage -Status $status -Stage 'redis-untrusted'
            & docker run --pull never --rm --network $network `
                --env REDIS_HOST=tls-valid `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            Assert-TlsFixtureValue -Status $status -Check 'exit-code' -Actual $LASTEXITCODE -Nonzero
            Assert-TlsFixtureValue -Status $status -Check 'backend-request' -Expected 0 `
                -Actual ([int](Test-Path -LiteralPath $requestPath))

            Set-TlsFixtureStage -Status $status -Stage 'trusted-readiness'
            $trustedNginx = "tls-nginx-trusted-$suffix"
            $trustedPort = Invoke-NginxFixture `
                -Name $trustedNginx `
                -BackendHost 'tls-valid' `
                -Network $network `
                -FixtureDirectory $fixtureDirectory `
                -Containers $containers -Status $status `
                -TrustFixtureCertificate
            Set-TlsFixtureStage -Status $status -Stage 'trusted-http'
            Assert-TlsFixtureValue -Status $status -Check 'http-status' -Expected 204 -Actual 0 -ObserveOnly
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:${trustedPort}/api/" `
                -NoProxy -SkipHttpErrorCheck -TimeoutSec 5
            Assert-TlsFixtureValue -Status $status -Check 'http-status' -Expected 204 -Actual ([int]$response.StatusCode)
            Assert-TlsFixtureValue -Status $status -Check 'backend-request' -Expected 1 `
                -Actual ([int]((Get-Content -Raw -LiteralPath $requestPath) -match 'GET /api/'))
            Assert-TlsFixtureValue -Status $status -Check 'receipt-reset' -Expected 0 -Actual 1 -ObserveOnly
            Remove-Item -LiteralPath $requestPath -Force
            Assert-TlsFixtureValue -Status $status -Check 'receipt-reset' -Expected 0 `
                -Actual ([int](Test-Path -LiteralPath $requestPath))

            Set-TlsFixtureStage -Status $status -Stage 'redis-hostname'
            & docker run --pull never --rm --network $network `
                --volume "${fixtureDirectory}:/fixture:ro" `
                --env SSL_CERT_FILE=/fixture/server.crt `
                --env REDIS_HOST=tls-wrong `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            Assert-TlsFixtureValue -Status $status -Check 'exit-code' -Actual $LASTEXITCODE -Nonzero
            Assert-TlsFixtureValue -Status $status -Check 'backend-request' -Expected 0 `
                -Actual ([int](Test-Path -LiteralPath $requestPath))

            Set-TlsFixtureStage -Status $status -Stage 'redis-trusted'
            & docker run --pull never --rm --network $network `
                --volume "${fixtureDirectory}:/fixture:ro" `
                --env SSL_CERT_FILE=/fixture/server.crt `
                --env REDIS_HOST=tls-valid `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            Assert-TlsFixtureValue -Status $status -Check 'exit-code' -Expected 0 -Actual $LASTEXITCODE
            Assert-TlsFixtureValue -Status $status -Check 'backend-request' -Expected 1 `
                -Actual ([int](Test-Path -LiteralPath $requestPath))
            Assert-TlsFixtureValue -Status $status -Check 'redis-auth' -Expected 1 `
                -Actual ([int]((Get-Content -Raw -LiteralPath $requestPath) -match 'AUTH'))
        }
        catch {
            $diagnostics = Get-TlsFixtureDiagnostics -Containers $containers
            $summary = Get-TlsFixtureFailureSummary -Status $status
            throw "$summary`n$($diagnostics -join "`n")"
        }
        finally {
            foreach ($container in $containers) {
                & docker rm --force $container 2>$null | Out-Null
            }
            & docker network rm $network 2>$null | Out-Null
        }
    }
}

Describe 'Data Viewer caller authentication gate' -Tag 'Unit' {
    BeforeAll {
        $script:StartScript = Get-Content -Raw (
            Join-Path $script:RepoRoot 'data-management/viewer/frontend/start-nginx.sh'
        )
        $script:FrontendDirectory = Join-Path $script:RepoRoot 'data-management/viewer/frontend'
    }

    It 'rejects nonempty legacy proxy keys without reflecting their value' {
        $script:StartScript | Should -Match 'case "\$\{DATAVIEWER_PROXY_KEY-\}"'
    }

    It 'preserves real caller transport and fails closed before protected upstream work' {
        $fixture = Join-Path $PSScriptRoot 'fixtures/caller-proxy-fixture.mjs'
        $output = & node $fixture run $script:RepoRoot 2>&1
        $LASTEXITCODE | Should -Be 0 -Because ($output -join "`n")
        ($output -join "`n") | Should -Match 'caller transport: \d+ assertions passed'
    }

    It 'renders caller includes for each mode' {
        $script:StartScript | Should -Match 'caller-api\.inc'
        $script:StartScript | Should -Match 'caller-health\.inc'
        $script:StartScript | Should -Match 'mode="\$\{DATAVIEWER_CALLER_MODE-off\}"'
    }

    It 'keeps internal caller proof locations unreachable from clients' {
        $script:NginxConfig | Should -Match 'location = /_caller_proof'
        $script:NginxConfig | Should -Match 'location = /_caller_ready'
        $script:NginxConfig | Should -Match 'proxy_hide_header X-Dataviewer-Caller-Token;'
    }

    It 'rejects encoded or trailing-slash variants of /health' {
        $script:NginxConfig | Should -Match 'limit_except GET HEAD'
        $script:NginxConfig | Should -Match 'if \(\$request_uri !~ "\^/health'
    }

    It 'renders a valid NGINX configuration in <Mode> mode' -ForEach @(
        @{ Mode = 'off' }
        @{ Mode = 'pending' }
        @{ Mode = 'managed_identity' }
    ) {
        $envArgs = @(
            '--env', 'NGINX_BACKEND_HOST=tls-valid',
            '--env', 'NGINX_BACKEND_SCHEME=https',
            '--env', 'NGINX_RESOLVER=127.0.0.11'
        )
        if ($Mode -ne 'off') {
            $envArgs += @('--env', "DATAVIEWER_CALLER_MODE=$Mode")
        }
        $startVolume = "$(Join-Path $script:FrontendDirectory 'start-nginx.sh'):/s.sh:ro"
        $templateVolume = "$(Join-Path $script:FrontendDirectory 'nginx.conf.template'):/etc/nginx/templates/default.conf.template:ro"
        $output = Invoke-Docker -Arguments (
            @('run', '--rm', '--entrypoint', '/bin/sh', '--volume', $startVolume, '--volume', $templateVolume) +
            $envArgs +
            @($script:NginxImage, '/s.sh', '-t')
        )
        ($output -join "`n") | Should -Match 'test is successful'
    }
}
