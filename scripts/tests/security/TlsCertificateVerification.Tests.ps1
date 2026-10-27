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
        $arguments += $script:NginxImage

        Invoke-Docker -Arguments $arguments | Out-Null

        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        do {
            $portOutput = & docker port $Name '8080/tcp' 2>$null
            if ($LASTEXITCODE -eq 0 -and $portOutput) {
                return ($portOutput -split ':')[-1]
            }
            Start-Sleep -Milliseconds 100
        } while ([DateTime]::UtcNow -lt $deadline)

        throw 'Timed out waiting for the NGINX fixture port'
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
            Invoke-Docker -Arguments @('network', 'create', $network) | Out-Null
            Invoke-Docker -Arguments @(
                'run', '--pull', 'never', '--detach', '--name', $serverContainer,
                '--network', $network,
                '--network-alias', 'tls-valid',
                '--network-alias', 'tls-wrong',
                '--volume', "${fixtureDirectory}:/fixture",
                $script:NodeImage,
                'node', '/fixture/server.mjs'
            ) | Out-Null
            $containers.Add($serverContainer)

            $untrustedNginx = "tls-nginx-untrusted-$suffix"
            $untrustedPort = Invoke-NginxFixture `
                -Name $untrustedNginx `
                -BackendHost 'tls-valid' `
                -Network $network `
                -FixtureDirectory $fixtureDirectory
            $containers.Add($untrustedNginx)
            & curl --silent --fail --max-time 5 "http://127.0.0.1:${untrustedPort}/api/" 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse

            $wrongHostNginx = "tls-nginx-wrong-host-$suffix"
            $wrongHostPort = Invoke-NginxFixture `
                -Name $wrongHostNginx `
                -BackendHost 'tls-wrong' `
                -Network $network `
                -FixtureDirectory $fixtureDirectory `
                -TrustFixtureCertificate
            $containers.Add($wrongHostNginx)
            & curl --silent --fail --max-time 5 "http://127.0.0.1:${wrongHostPort}/api/" 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse

            & docker run --pull never --rm --network $network `
                --env REDIS_HOST=tls-valid `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse

            & docker run --pull never --rm --network $network `
                --volume "${fixtureDirectory}:/fixture:ro" `
                --env SSL_CERT_FILE=/fixture/server.crt `
                --env REDIS_HOST=tls-wrong `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse

            & docker run --pull never --rm --network $network `
                --volume "${fixtureDirectory}:/fixture:ro" `
                --env SSL_CERT_FILE=/fixture/server.crt `
                --env REDIS_HOST=tls-valid `
                --env REDIS_PORT=443 `
                --env REDIS_PASSWORD=test-secret `
                --env REDIS_OPERATION=PING `
                $script:PythonImage `
                python -c $script:RedisTlsClient 2>$null
            $LASTEXITCODE | Should -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeTrue
            (Get-Content -Raw -LiteralPath $requestPath) | Should -Match 'AUTH'
        }
        finally {
            foreach ($container in $containers) {
                & docker rm --force $container 2>$null | Out-Null
            }
            & docker network rm $network 2>$null | Out-Null
        }
    }
}
