# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }

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
    $script:ProductionClients = @(
        Get-ChildItem (Join-Path $script:RepoRoot 'infrastructure/setup') -Recurse -File -Filter '*.sh'
        Get-ChildItem (Join-Path $script:RepoRoot 'data-management/viewer/backend/src') -Recurse -File -Filter '*.py'
        Get-Item (Join-Path $script:RepoRoot 'data-management/viewer/frontend/nginx.conf.template')
    )
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
        ([regex]::Matches($script:CleanupScript, '"--tls"')).Count | Should -Be 2
        ([regex]::Matches($script:CleanupScript, 'args: \["-h", \$host, "-p", \$port')).Count | Should -Be 2
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

    It 'prevents authenticated HTTP requests before invalid TLS handshakes complete' {
        $fixtureDirectory = Join-Path $TestDrive 'tls-fixture'
        $certificatePath = Join-Path $fixtureDirectory 'server.crt'
        $keyPath = Join-Path $fixtureDirectory 'server.key'
        $portPath = Join-Path $fixtureDirectory 'port'
        $requestPath = Join-Path $fixtureDirectory 'requests'
        $serverPath = Join-Path $fixtureDirectory 'server.py'
        New-Item -ItemType Directory -Path $fixtureDirectory | Out-Null

        & openssl req -x509 -newkey rsa:2048 -nodes -days 1 `
            -subj '/CN=valid.local' -addext 'subjectAltName=DNS:valid.local' `
            -keyout $keyPath -out $certificatePath 2>$null
        $LASTEXITCODE | Should -Be 0

        @'
from __future__ import annotations

import http.server
import ssl
import sys
from pathlib import Path


class RequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        Path(sys.argv[4]).write_text(self.headers.get("Authorization", ""), encoding="utf-8")
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(sys.argv[1], sys.argv[2])
server.socket = context.wrap_socket(server.socket, server_side=True)
Path(sys.argv[3]).write_text(str(server.server_port), encoding="utf-8")
server.serve_forever()
'@ | Set-Content -LiteralPath $serverPath

        $server = Start-Process python3 `
            -ArgumentList @($serverPath, $certificatePath, $keyPath, $portPath, $requestPath) `
            -PassThru
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            while (-not (Test-Path -LiteralPath $portPath) -and [DateTime]::UtcNow -lt $deadline) {
                Start-Sleep -Milliseconds 100
            }
            Test-Path -LiteralPath $portPath | Should -BeTrue
            $port = Get-Content -Raw -LiteralPath $portPath

            & curl --silent --show-error --fail --max-time 5 --noproxy '*' `
                --header 'Authorization: test-secret' `
                --resolve "valid.local:${port}:127.0.0.1" "https://valid.local:${port}/" 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse

            & curl --silent --show-error --fail --max-time 5 --noproxy '*' `
                --cacert $certificatePath `
                --header 'Authorization: test-secret' `
                --resolve "wrong.local:${port}:127.0.0.1" "https://wrong.local:${port}/" 2>$null
            $LASTEXITCODE | Should -Not -Be 0
            Test-Path -LiteralPath $requestPath | Should -BeFalse
        }
        finally {
            $server.Kill()
            $server.WaitForExit()
        }
    }
}
