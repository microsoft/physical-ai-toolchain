#Requires -Version 7.0
#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0' }
# Copyright (c) Microsoft Corporation.
# SPDX-License-Identifier: MIT

# Behavioral coverage for infrastructure/local/enable-azure-access.sh. Stubbed az and osmo commands stand in
# for Azure and the local OSMO, so no test reads or changes either.

BeforeDiscovery {
    $script:ToolsPresent = [bool]((Get-Command bash -ErrorAction SilentlyContinue) -and (Get-Command jq -ErrorAction SilentlyContinue))
}

BeforeAll {
    Import-Module (Resolve-Path (Join-Path $PSScriptRoot '../Mocks/BashScriptHarness.psm1')) -Force
    $script:ScriptPath = (Resolve-Path (Join-Path $PSScriptRoot '../../../infrastructure/local/enable-azure-access.sh')).Path
    $script:ArtifactsRoot = Join-Path $PSScriptRoot '.artifacts/enable-azure-access'
    New-Item -ItemType Directory -Path $script:ArtifactsRoot -Force | Out-Null

    $script:Tenant = '00000000-0000-0000-0000-00000000000a'
    $script:Subscription = '00000000-0000-0000-0000-00000000000b'
    $resourceGroup = "/subscriptions/$script:Subscription/resourceGroups/rg-example/providers"
    $script:ArcCluster = "$resourceGroup/Microsoft.Kubernetes/connectedClusters/example-k3s"
    $script:WorkflowIdentity = "$resourceGroup/Microsoft.ManagedIdentity/userAssignedIdentities/id-workflow"
    $script:DataviewerIdentity = "$resourceGroup/Microsoft.ManagedIdentity/userAssignedIdentities/id-dataviewer"
    $script:WorkflowClientId = '11111111-1111-1111-1111-111111111111'
    $script:DataviewerClientId = '33333333-3333-3333-3333-333333333333'
    $script:Optional = @('--dataviewer-identity-id', $script:DataviewerIdentity, '--storage-account', 'examplestore',
        '--acr-name', 'exampleacr')

    # Answers each az call the script makes; AZ_* variables select the Azure state a test needs.
    $script:AzStub = @'
case "$*" in
  "account show"*) printf '{"tenantId":"%s","id":"%s"}\n' "$AZ_TENANT" "$AZ_SUBSCRIPTION" ;;
  "connectedk8s show"*)
    printf '{"securityProfile":{"workloadIdentity":{"enabled":%s}},"oidcIssuerProfile":{"issuerUrl":"https://oidc.example/issuer"}}\n' \
      "${AZ_WORKLOAD_IDENTITY:-true}" ;;
  "identity show"*id-dataviewer*)
    echo '{"name":"id-dataviewer","resourceGroup":"rg-example","clientId":"33333333-3333-3333-3333-333333333333","principalId":"44444444-4444-4444-4444-444444444444"}' ;;
  "identity show"*)
    echo '{"name":"id-workflow","resourceGroup":"rg-example","clientId":"11111111-1111-1111-1111-111111111111","principalId":"22222222-2222-2222-2222-222222222222"}' ;;
  "identity federated-credential show"*)
    state="${AZ_CREDENTIAL_STATE:-missing}"
    if [[ "$state" == missing ]]; then echo 'ERROR: (ResourceNotFound) Not found' >&2; exit 3; fi
    subject="system:serviceaccount:osmo-workflows:osmo-workflow"
    [[ "$*" == *dataviewer-local-* ]] && subject="system:serviceaccount:dataviewer:dataviewer-backend"
    [[ "$state" == mismatch ]] && subject="system:serviceaccount:other:other"
    printf '{"issuer":"https://oidc.example/issuer","subject":"%s","audiences":["api://AzureADTokenExchange"]}\n' "$subject" ;;
  "role assignment list"*) printf '%s\n' "${AZ_ROLES:-AzureML Data Scientist;Storage Blob Data Contributor}" | tr ';' '\n' ;;
  "acr show"*)
    printf '{"id":"/subscriptions/%s/resourceGroups/rg-example/providers/Microsoft.ContainerRegistry/registries/exampleacr","loginServer":"exampleacr.azurecr.io"}\n' "$AZ_SUBSCRIPTION" ;;
  "acr token show"*)
    state="${AZ_TOKEN_STATE:-missing}"
    if [[ "$state" == missing ]]; then echo 'ERROR: (ResourceNotFound) Not found' >&2; exit 3; fi
    if [[ "$state" == error ]]; then echo 'ERROR: (AuthorizationFailed) Forbidden' >&2; exit 1; fi
    if [[ "$state" == unreadable ]]; then echo '{"scopeMapId":null,"status":null}'; exit 0; fi
    registry="/subscriptions/$AZ_SUBSCRIPTION/resourceGroups/rg-example/providers/Microsoft.ContainerRegistry/registries/exampleacr"
    scope_map=_repositories_pull status=enabled
    case "$state" in
      push) scope_map=_repositories_push ;;
      admin) scope_map=_repositories_admin ;;
      disabled) status=disabled ;;
      uppercase) registry=$(printf '%s' "$registry" | tr '[:lower:]' '[:upper:]') ;;
    esac
    printf '{"scopeMapId":"%s/scopeMaps/%s","status":"%s"}\n' "$registry" "$scope_map" "$status" ;;
  "acr token create"*) echo '{"credentials":{"passwords":[{"name":"password1","value":"created-token-secret"}]}}' ;;
  "acr token credential generate"*) echo '{"passwords":[{"name":"password1","value":"rotated-token-secret"}]}' ;;
  *) exit 0 ;;
esac
'@

    # Captures the REGISTRY credential's auth value from its file descriptor, as the OSMO CLI would read it.
    $script:OsmoStub = @'
case "$1 $2" in
  "credential set") for a in "$@"; do [[ "$a" == auth=* ]] && cat "${a#auth=}" > "$OSMO_AUTH_CAPTURE"; done; true ;;
  "credential list") printf '%s\n' "${OSMO_CREDENTIALS:-}" ;;
  *) exit 0 ;;
esac
'@

    function Invoke-AzureAccess {
        param(
            [string[]]$Arguments = @(),
            [hashtable]$ExtraEnv = @{},
            [string]$WorkflowIdentity = $script:WorkflowIdentity
        )

        $workDir = Join-Path $script:ArtifactsRoot ([System.Guid]::NewGuid().ToString('N'))
        $configDir = Join-Path $workDir 'osmo-config'
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        Set-Content -Path (Join-Path $configDir 'login.yaml') -Value 'url: http://10.43.250.20'
        $vars = @{
            AZ_TENANT             = $script:Tenant
            AZ_SUBSCRIPTION       = $script:Subscription
            LOCAL_OSMO_CONFIG_DIR = $configDir
            OSMO_AUTH_CAPTURE     = (Join-Path $workDir 'osmo-auth.txt')
        }
        foreach ($key in $ExtraEnv.Keys) { $vars[$key] = $ExtraEnv[$key] }
        $required = @('--tenant-id', $script:Tenant, '--subscription', $script:Subscription,
            '--arc-cluster-resource-id', $script:ArcCluster, '--workflow-identity-id', $WorkflowIdentity)
        Invoke-BashEntryScript -ScriptPath $script:ScriptPath -WorkDir $workDir -EnvVars $vars `
            -Stubs @{ az = $script:AzStub; osmo = $script:OsmoStub } -ScriptArgs ($required + $Arguments)
    }

    function Get-WriteCalls {
        param([Parameter(Mandatory)][string[]]$Calls)
        @($Calls | Where-Object { $_ -match '^az .*\b(create|delete|generate)\b' -or $_ -match '^osmo credential (set|delete)' })
    }
}

Describe 'enable-azure-access.sh inputs' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Previews the credentials it derives from the cluster without calling Azure' {
        $result = Invoke-AzureAccess -Arguments (@('--config-preview') + $script:Optional)

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Mode:\s+plan'
        $result.StdOut | Should -Match 'Workflow Credential:\s+osmo-local-[0-9a-f]{16} for system:serviceaccount:osmo-workflows:osmo-workflow'
        $result.StdOut | Should -Match 'Dataviewer Credential:\s+dataviewer-local-[0-9a-f]{16} for system:serviceaccount:dataviewer:dataviewer-backend'
        $result.StdOut | Should -Match 'ACR Pull Token:\s+osmo-local-[0-9a-f]{16} on exampleacr \(password valid 30 days\)'
        $result.StdOut | Should -Match 'OSMO Credential:\s+acr-exampleacr \(REGISTRY\)'
        $result.StdOut | Should -Match 'Role Changes:\s+none'
        @($result.Calls | Where-Object { $_ -match '^(az|osmo) ' }).Count | Should -Be 0
    }

    It 'Rejects <Case>' -ForEach @(
        @{ Case = 'both --apply and --remove'; Arguments = @('--apply', '--remove'); Message = 'Use only one of --apply and --remove' }
        @{ Case = 'a dataviewer identity without a storage account'; Arguments = @('--dataviewer-identity-id', '/subscriptions/00000000-0000-0000-0000-00000000000b/resourceGroups/rg-example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-dataviewer'); Message = 'needs --storage-account' }
        @{ Case = 'a storage account without a dataviewer identity'; Arguments = @('--storage-account', 'examplestore'); Message = '--storage-account needs --dataviewer-identity-id' }
        @{ Case = 'a token lifetime that is not a whole number'; Arguments = @('--acr-token-days', '0'); Message = 'positive whole number' }
    ) {
        $result = Invoke-AzureAccess -Arguments (@('--config-preview') + $Arguments)

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ([regex]::Escape($Message))
    }

    It 'Rejects an identity outside --subscription' {
        $foreign = '/subscriptions/00000000-0000-0000-0000-00000000000c/resourceGroups/rg-example/providers/Microsoft.ManagedIdentity/userAssignedIdentities/id-workflow'
        $result = Invoke-AzureAccess -Arguments @('--config-preview') -WorkflowIdentity $foreign

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'user-assigned managed identity in --subscription'
    }
}

Describe 'enable-azure-access.sh plan' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Reports the missing access and the redeploy commands with read-only calls' {
        $result = Invoke-AzureAccess -Arguments $script:Optional

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'Arc OIDC Issuer:\s+https://oidc\.example/issuer'
        $result.StdOut | Should -Match 'Workflow Roles:\s+AzureML Data Scientist, Storage Blob Data Contributor'
        $result.StdOut | Should -Match 'Workflow Credential:\s+osmo-local-[0-9a-f]{16}: missing; --apply creates it'
        $result.StdOut | Should -Match 'Dataviewer Credential:\s+dataviewer-local-[0-9a-f]{16}: missing; --apply creates it'
        $result.StdOut | Should -Match 'ACR Pull Token:\s+osmo-local-[0-9a-f]{16} on exampleacr\.azurecr\.io: missing; --apply creates it \(pull only\)'
        $result.StdOut | Should -Match 'Plan only: nothing changed'
        $result.StdOut | Should -Match ('OSMO:\s+infrastructure/local/02-deploy-osmo\.sh --azure-client-id ' + $script:WorkflowClientId)
        $result.StdOut | Should -Match ('Dataviewer:\s+infrastructure/local/04-deploy-dataviewer\.sh --azure-storage-account examplestore --azure-client-id ' + $script:DataviewerClientId)
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
        @($result.Calls | Where-Object { $_ -match '^osmo ' }).Count | Should -Be 0
    }

    It 'Warns when the workflow identity lacks a role that local jobs need' {
        $result = Invoke-AzureAccess -ExtraEnv @{ AZ_ROLES = 'Storage Blob Data Contributor' }

        $result.ExitCode | Should -Be 0
        $result.StdErr | Should -Match 'lacks AzureML Data Scientist'
    }

    It 'Stops when <Case>' -ForEach @(
        @{ Case = 'the active account is another subscription'; ExtraEnv = @{ AZ_SUBSCRIPTION = '00000000-0000-0000-0000-00000000000c' }; Message = 'does not match --tenant-id and --subscription' }
        @{ Case = 'the Arc cluster has no workload identity'; ExtraEnv = @{ AZ_WORKLOAD_IDENTITY = 'false' }; Message = 'workload identity enabled' }
    ) {
        $result = Invoke-AzureAccess -Arguments @('--apply') -ExtraEnv $ExtraEnv

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ([regex]::Escape($Message))
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
    }

    It 'Reports a token that is not pull-only and changes nothing' {
        $result = Invoke-AzureAccess -Arguments @('--acr-name', 'exampleacr') -ExtraEnv @{ AZ_TOKEN_STATE = 'push' }

        $result.ExitCode | Should -Be 0
        $result.StdOut | Should -Match 'ACR Pull Token:\s+osmo-local-[0-9a-f]{16} on exampleacr\.azurecr\.io: not pull-only or disabled; inspect it before applying'
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
    }

    It 'Stops when the ACR token <Case>' -ForEach @(
        @{ Case = 'cannot be read'; State = 'error'; Message = 'Unable to read ACR token osmo-local-' }
        @{ Case = 'scope map and status cannot be read'; State = 'unreadable'; Message = 'Unable to read the scope map and status of ACR token' }
    ) {
        $result = Invoke-AzureAccess -Arguments @('--apply', '--acr-name', 'exampleacr') -ExtraEnv @{ AZ_TOKEN_STATE = $State }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match ([regex]::Escape($Message))
        $result.StdErr | Should -Match 'nothing was changed'
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
    }
}

Describe 'enable-azure-access.sh apply and remove' -Tag 'Unit' -Skip:(-not $script:ToolsPresent) {
    It 'Creates the federated credentials and stores a pull-only token without exposing it' {
        $result = Invoke-AzureAccess -Arguments (@('--apply') + $script:Optional)

        $result.ExitCode | Should -Be 0
        $creates = @($result.Calls | Where-Object { $_ -match '^az identity federated-credential create' })
        $creates.Count | Should -Be 2
        $creates[0] | Should -Match '--identity-name id-workflow .*--name osmo-local-[0-9a-f]{16} --issuer https://oidc\.example/issuer --subject system:serviceaccount:osmo-workflows:osmo-workflow --audiences api://AzureADTokenExchange'
        $creates[1] | Should -Match '--identity-name id-dataviewer .*--name dataviewer-local-[0-9a-f]{16} .*--subject system:serviceaccount:dataviewer:dataviewer-backend'
        @($result.Calls | Where-Object { $_ -match '^az acr token create --name osmo-local-[0-9a-f]{16} --registry exampleacr .*--scope-map _repositories_pull --expiration-in-days 30' }).Count | Should -Be 1
        $set = @($result.Calls | Where-Object { $_ -match '^osmo credential set' })
        $set.Count | Should -Be 1
        $set[0] | Should -Match '^osmo credential set acr-exampleacr --type REGISTRY --payload-file registry=/dev/fd/\d+ username=/dev/fd/\d+ auth=/dev/fd/\d+$'
        Get-Content -Path (Join-Path $result.WorkDir 'osmo-auth.txt') -Raw | Should -Be 'created-token-secret'
        ($result.Calls -join "`n") + $result.StdOut + $result.StdErr | Should -Not -Match 'created-token-secret'
        $result.StdOut | Should -Match 'Azure access is ready'
    }

    It 'Keeps present credentials and issues a new password for an existing token' {
        $result = Invoke-AzureAccess -Arguments @('--apply', '--acr-name', 'exampleacr') `
            -ExtraEnv @{ AZ_CREDENTIAL_STATE = 'present'; AZ_TOKEN_STATE = 'present' }

        $result.ExitCode | Should -Be 0
        @($result.Calls | Where-Object { $_ -match 'federated-credential create|acr token create' }).Count | Should -Be 0
        @($result.Calls | Where-Object { $_ -match '^az acr token credential generate .*--password1 --expiration-in-days 30' }).Count | Should -Be 1
        Get-Content -Path (Join-Path $result.WorkDir 'osmo-auth.txt') -Raw | Should -Be 'rotated-token-secret'
    }

    It 'Treats a pull-only scope map that differs only in letter case as present' {
        $result = Invoke-AzureAccess -Arguments @('--apply', '--acr-name', 'exampleacr') `
            -ExtraEnv @{ AZ_CREDENTIAL_STATE = 'present'; AZ_TOKEN_STATE = 'uppercase' }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        @($result.Calls | Where-Object { $_ -match '^az acr token credential generate ' }).Count | Should -Be 1
    }

    It 'Refuses to issue a password for a token that is <Case>, and changes nothing' -ForEach @(
        @{ Case = 'scoped to pushes'; State = 'push' }
        @{ Case = 'scoped to every repository operation, including deletes'; State = 'admin' }
        @{ Case = 'disabled'; State = 'disabled' }
    ) {
        $result = Invoke-AzureAccess -Arguments (@('--apply') + $script:Optional) -ExtraEnv @{ AZ_TOKEN_STATE = $State }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'is not limited to pulls from this registry, or is disabled; nothing was changed'
        $result.StdErr | Should -Match 'stored in the local OSMO is unchanged and may still work'
        $result.StdErr | Should -Match 'run --remove to delete the token and the stored credential'
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
        @($result.Calls | Where-Object { $_ -match '^osmo ' }).Count | Should -Be 0
        Test-Path -Path (Join-Path $result.WorkDir 'osmo-auth.txt') | Should -BeFalse
    }

    It 'Removes an ACR token whose <Case>' -ForEach @(
        @{ Case = 'scope drifted'; State = 'push' }
        @{ Case = 'scope map and status cannot be read'; State = 'unreadable' }
    ) {
        $result = Invoke-AzureAccess -Arguments @('--remove', '--acr-name', 'exampleacr') `
            -ExtraEnv @{ AZ_CREDENTIAL_STATE = 'present'; AZ_TOKEN_STATE = $State; OSMO_CREDENTIALS = 'acr-exampleacr REGISTRY' }

        $result.ExitCode | Should -Be 0 -Because $result.StdErr
        @($result.Calls | Where-Object { $_ -match '^az acr token delete --name osmo-local-[0-9a-f]{16} --registry exampleacr .*--yes' }).Count | Should -Be 1
        $result.Calls | Should -Contain 'osmo credential delete acr-exampleacr'
    }

    It 'Refuses to apply over a credential with a different subject' {
        $result = Invoke-AzureAccess -Arguments (@('--apply') + $script:Optional) -ExtraEnv @{ AZ_CREDENTIAL_STATE = 'mismatch' }

        $result.ExitCode | Should -Not -Be 0
        $result.StdErr | Should -Match 'different issuer, subject, or audience; nothing was changed'
        (Get-WriteCalls -Calls $result.Calls).Count | Should -Be 0
    }

    It 'Removes the credentials, the token, and the OSMO credential' {
        $result = Invoke-AzureAccess -Arguments (@('--remove') + $script:Optional) `
            -ExtraEnv @{ AZ_CREDENTIAL_STATE = 'present'; AZ_TOKEN_STATE = 'present'; OSMO_CREDENTIALS = 'acr-exampleacr REGISTRY' }

        $result.ExitCode | Should -Be 0
        @($result.Calls | Where-Object { $_ -match '^az identity federated-credential delete .*--yes' }).Count | Should -Be 2
        @($result.Calls | Where-Object { $_ -match '^az acr token delete --name osmo-local-[0-9a-f]{16} --registry exampleacr .*--yes' }).Count | Should -Be 1
        $result.Calls | Should -Contain 'osmo credential delete acr-exampleacr'
        @($result.Calls | Where-Object { $_ -match 'create|generate|credential set' }).Count | Should -Be 0
        $result.StdOut | Should -Match 'Redeploy without the Azure options'
        $result.StdOut | Should -Match 'OSMO:\s+infrastructure/local/02-deploy-osmo\.sh \(with LOCAL_OSMO_AZURE_CLIENT_ID unset\)'
    }
}
