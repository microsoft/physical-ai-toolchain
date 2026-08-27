---
title: Ubuntu HiL OSMO Backend
description: Prepare one Ubuntu T3 HiL node, optionally establish private reachability, connect it to an existing OSMO environment, and prove CPU and no-command outcomes.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-23
ms.topic: tutorial
---

Move one Ubuntu desktop through four T3 HiL milestones: host-ready, reachable when private routing is required, connected to an existing OSMO backend and pool, and validated for CPU and no-command workloads. Key Vault is the only scripted protected-artifact transfer.

> [!WARNING]
> The CPU and no-command proofs complete this journey. No command transport or physical motion is supported.

## Responsibilities

| Owner             | Responsibilities                                                                                                                                 | Excluded work                                                                                                   |
|-------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Environment owner | Verify the existing OSMO endpoint, backend, pool, charts, images, registry access, Key Vault secrets, per-secret roles, Arc workload identity, and coherent publication | Local K3s or Ubuntu mutation                                                                                    |
| Ubuntu user       | Prepare the host, install owned K3s, optionally connect VPN, consume exact Key Vault inputs, and run validation                                  | AKS credentials, Azure resource administration, Key Vault networking or RBAC changes, remote OSMO desired state |
| VPN CA owner      | Sign the Ubuntu CSR and publish only the signed leaf and public chain                                                                            | Moving the CA private key or Ubuntu private key                                                                 |

## Transfer Protected Artifacts

Repository HiL scripts publish and consume protected artifacts only through Key Vault.

| Transport | Host preparation   | Artifact source                                                       | Failure behavior                                                             |
|-----------|--------------------|-----------------------------------------------------------------------|------------------------------------------------------------------------------|
| Key Vault | Installs Azure CLI | Exact secret names and immutable versions from the host-bound catalog | Stop on login, access, network, target, catalog, token, or integrity failure |

A Key Vault failure stops the journey. The consumer validates the catalog, artifact digests, token metadata, token digest, backend binding, and expiry before any Kubernetes mutation.

Manual SCP is permitted only as an out-of-band operator procedure. It must not invoke repository HiL publisher, VPN, or consumer scripts, and it must not use retired transfer arguments. Re-establish the Key Vault catalog workflow before running repository HiL scripts.

## Prepare the Environment

Complete these actions from a trusted environment-operator host. The existing OSMO control plane must already contain the intended backend and pool.

### Create the Exchange Secrets

Pre-create the exact secret resources before assigning roles. Use these names, where `<environment>` and `<host>` use lowercase letters, numbers, and hyphens:

| Secret                                     | Ubuntu access                     | Content owner                              |
|--------------------------------------------|-----------------------------------|--------------------------------------------|
| `<environment>-<host>-hil-catalog`         | Secrets User                      | Environment owner; published last          |
| `<environment>-deployment`                 | Secrets User                      | Generic non-secret bundle publisher        |
| `<environment>-osmo-images`                | Secrets User                      | Generic non-secret bundle publisher        |
| `<environment>-<host>-osmo-token`          | Secrets User                      | Environment owner                          |
| `<environment>-<host>-osmo-token-metadata` | Secrets User                      | Environment owner                          |
| `<environment>-<host>-registry-config`     | Secrets User                      | Environment owner                          |
| `<environment>-<host>-osmo-artifacts`      | Secrets User                      | Environment owner                          |
| `<environment>-<host>-vpn-config`          | Secrets User when VPN is required | Environment owner                          |
| `<environment>-<host>-vpn-settings`        | Secrets User when VPN is required | Environment owner                          |
| `<environment>-<host>-vpn-server-root`     | Secrets User when VPN is required | Environment owner                          |
| `<environment>-<host>-vpn-client-root`     | Secrets User when VPN is required | Environment owner                          |
| `<environment>-<host>-vpn-csr`             | Secrets Officer only              | Ubuntu user                                |
| `<environment>-<host>-vpn-response`        | Secrets User when VPN is required | VPN CA owner through the trusted publisher |

Use Key Vault Secrets User only on each named inbound secret. Use Key Vault Secrets Officer only on the host-specific CSR secret. Verify the Ubuntu identity has no direct or inherited vault-wide data-plane role before onboarding.

Role assignment remains a manual environment-owner operation. The following shape scopes an assignment to one secret resource:

```bash
SECRET_ID="$(az keyvault secret show \
  --vault-name <vault> \
  --name <exact-secret-name> \
  --query id \
  --output tsv)"

az role assignment create \
  --assignee-object-id <ubuntu-user-object-id> \
  --assignee-principal-type User \
  --role 'Key Vault Secrets User' \
  --scope "$SECRET_ID"
```

Use `Key Vault Secrets Officer` only for the host-specific CSR secret. Review direct and inherited assignments separately before continuing.

### Publish the Host-Bound Artifacts

Generate the non-secret environment bundle under `infrastructure/setup/generated/<environment>/` with the `environment-deployment` skill. Prepare a protected pull-only registry configuration and, when VPN is required, a protected directory containing `vpn.json`, `VpnSettings.xml`, `VpnServerRoot.pem`, and `ClientRoot.pem`.

When `vpn.json` configures private DNS, use exactly `server`, `zones`, and `probes`. Each probe is an object such as `{"host":"vault.example","expected_cidr":"10.0.0.0/16"}`. The VPN connection rejects answers outside the expected private CIDR.

Preview publication:

```bash
infrastructure/setup/04-prepare-osmo-hil-node.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --bundle-dir infrastructure/setup/generated/<environment> \
  --service-url <approved-osmo-url> \
  --backend-name <existing-backend> \
  --pool-name <existing-pool> \
  --osmo-config-dir <protected-operator-osmo-profile> \
  --registry-config-file <protected-pull-config> \
  --token-expiry <yyyy-mm-dd> \
  --arc-cluster-resource-id /subscriptions/<subscription-id>/resourceGroups/<arc-resource-group>/providers/Microsoft.Kubernetes/connectedClusters/<arc-cluster-name> \
  --chart-version <deployed-chart-version> \
  --backend-chart-ref <approved-backend-chart-reference> \
  --backend-chart-sha256 <approved-backend-chart-sha256> \
  --image-version <deployed-image-version> \
  --image-location <approved-image-prefix> \
  --vpn-input-dir <protected-public-vpn-inputs> \
  --config-preview
```

Run the same command without `--config-preview`. Omit `--vpn-input-dir` when private routing is unnecessary.

The publisher:

* Verifies the active Azure account and existing OSMO backend and pool
* Reuses the exact catalog-pinned token and token metadata versions when they are valid and unexpired
* Issues a new `osmo-backend` token when the catalog is absent, valid metadata has expired, or `--renew-token` is supplied
* Preserves the generic non-secret environment-bundle allowlist
* Verifies the exact Arc resource, OIDC issuer, and workload-identity configuration
* Verifies the existing OSMO user-assigned managed identity and creates or verifies one host-bound federated credential for `osmo-workflow`
* Publishes credentials, registry access, immutable artifacts, and public VPN material through separate exact secrets
* Writes every artifact before the host-bound catalog
* Stops on malformed or inaccessible catalog data, or token-metadata binding or digest mismatch
* Never deletes token versions, assigns roles, or changes Key Vault networking

Record these environment gates separately as passed with authorization or not run:

1. Every named inbound secret and the CSR secret has the intended individual-secret role.
2. The Ubuntu identity has no inherited or direct vault-wide data-plane role.
3. The complete exact artifact set was published before the catalog.

## Prepare Ubuntu and K3s

Preview and run host preparation:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh \
  --config-preview

data-pipeline/setup/hil/00-prepare-ubuntu.sh
```

Host preparation always installs Azure CLI for the Key Vault connection.

Install the local compute plane without VPN:

```bash
data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name <host> \
  --config-preview

data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name <host>
```

## Optional Private Reachability

Run this branch only when the approved OSMO endpoint or private Key Vault requires private routing.

### Open a Bounded Key Vault Window

When the vault is private and the VPN is not yet available, record its current network state. Identify the Ubuntu desktop's current public egress IPv4 address, not its LAN address. Configure deny-by-default access with only that `/32` rule before enabling the public endpoint.

Portal sequence:

1. Record public access, firewall default, bypass, IP rules, and virtual-network rules.
2. Continue only when public access is disabled, bypass is `None`, and both rule lists are empty. Stop for an environment-specific restoration plan otherwise.
3. Select the option that permits public access only from selected networks.
4. Set the firewall default to deny.
5. Add only the Ubuntu public egress IPv4 address as a `/32` rule.
6. Verify the selected rule and deny-default posture.
7. Enable the public endpoint for the bounded transfer.

Manual Azure CLI sequence:

```bash
set -o errexit -o nounset -o pipefail
install -d -m 0700 "$HOME/.local/state/physical-ai-toolchain/hil"
az keyvault show \
  --name <vault> \
  --query 'properties.{publicNetworkAccess:publicNetworkAccess,defaultAction:networkAcls.defaultAction,bypass:networkAcls.bypass,ipRules:networkAcls.ipRules[].value,vnetRules:networkAcls.virtualNetworkRules[].id}' \
  --output json > "$HOME/.local/state/physical-ai-toolchain/hil/key-vault-network-before.json"

UBUNTU_PUBLIC_IPV4="<ubuntu-public-egress-ipv4>"
jq -e '
  .publicNetworkAccess == "Disabled" and .defaultAction == "Deny" and .bypass == "None" and
  ((.ipRules // []) | length) == 0 and ((.vnetRules // []) | length) == 0
' "$HOME/.local/state/physical-ai-toolchain/hil/key-vault-network-before.json" >/dev/null
az keyvault update --name <vault> --default-action Deny --output none
az keyvault network-rule add --name <vault> --ip-address "${UBUNTU_PUBLIC_IPV4}/32" --output none
WINDOW_STATE="$(az keyvault show \
  --name <vault> \
  --query 'properties.{publicNetworkAccess:publicNetworkAccess,defaultAction:networkAcls.defaultAction,bypass:networkAcls.bypass,ipRules:networkAcls.ipRules[].value,vnetRules:networkAcls.virtualNetworkRules[].id}' \
  --output json)"
jq -e --arg rule "${UBUNTU_PUBLIC_IPV4}/32" '
  .publicNetworkAccess == "Disabled" and .defaultAction == "Deny" and .bypass == "None" and .ipRules == [$rule] and
  ((.vnetRules // []) | length) == 0
' <<< "$WINDOW_STATE" >/dev/null
az keyvault update --name <vault> --public-network-access Enabled --output none
```

Verify `defaultAction` is `Deny` and the only temporary rule is the current Ubuntu public IPv4 `/32` before enabling public access. Setup scripts never execute these commands.

### Request VPN Access

Preview and run the request stage:

```bash
data-pipeline/setup/hil/vpn/00-request-vpn-access.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --config-preview

data-pipeline/setup/hil/vpn/00-request-vpn-access.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault>
```

The private key is generated on Ubuntu and never leaves it. Only the host-bound CSR request is published.

Close the public window immediately after this transfer:

```bash
set -o errexit -o nounset -o pipefail
az keyvault update --name <vault> --public-network-access Disabled --output none
[[ "$(az keyvault show --name <vault> --query properties.publicNetworkAccess --output tsv)" == "Disabled" ]]
az keyvault network-rule remove --name <vault> --ip-address "${UBUNTU_PUBLIC_IPV4}/32" --output none
FINAL_STATE="$(az keyvault show --name <vault> \
  --query 'properties.{publicNetworkAccess:publicNetworkAccess,defaultAction:networkAcls.defaultAction,bypass:networkAcls.bypass,ipRules:networkAcls.ipRules[].value,vnetRules:networkAcls.virtualNetworkRules[].id}' \
  --output json)"
jq -e '.publicNetworkAccess == "Disabled" and .defaultAction == "Deny" and .bypass == "None" and ((.ipRules // []) | length) == 0 and ((.vnetRules // []) | length) == 0' \
  <<< "$FINAL_STATE" >/dev/null
```

Continue only when verification returns `Disabled`. Remove the temporary rule or restore the recorded ACL only after public access is disabled and verified.

### Publish and Retrieve the Signed Response

The CA owner signs the CSR outside Ubuntu and creates a protected response JSON containing only `schema_version`, `kind`, `environment`, `host_name`, `csr_sha256`, `client_certificate_pem`, and `client_ca_certificate_pem`. Neither private key nor any additional field enters the response. The publisher validates the CSR, trust fingerprint, and leaf key, then publishes a sanitized target-bound response.

The environment owner validates and publishes it:

```bash
infrastructure/setup/04-prepare-osmo-hil-node.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --publish-vpn-response <protected-vpn-response.json>
```

Open the same bounded public window again when the vault is still unreachable. Retrieve the response:

```bash
data-pipeline/setup/hil/vpn/01-retrieve-vpn-certificate.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault>
```

This command validates and installs the public response, prints the required private-only checkpoint, and exits. Disable and verify public access before removing the temporary rule or restoring the recorded ACL.

### Connect After the Checkpoint

Run a separate command only after private-only access is verified:

```bash
data-pipeline/setup/hil/vpn/02-connect-vpn.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --private-vault-verified \
  --config-preview

data-pipeline/setup/hil/vpn/02-connect-vpn.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --private-vault-verified
```

The connection command performs no Key Vault access before VPN. It consumes local protected material, preserves the public default route, applies private routes and optional route-only DNS, verifies public DNS, and then checks private Key Vault reachability.

## Connect the Local Backend

```bash
data-pipeline/setup/hil/02-connect-osmo-backend.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault>
```

The stage authenticates the end user by OSMO code login, retrieves the exact catalog-bound artifacts from Key Vault, validates them before Kubernetes mutation, creates and verifies the local `osmo-workflow` ServiceAccount workload-identity metadata, and changes only the owned local K3s target. The non-secret connection receipt records the workflow-data URI, managed-identity client ID, and isolated Azure CLI path.

## Validate the Journey

Use the connection receipt printed by the connection stage.

CPU scheduling proof:

```bash
data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt> \
  --config-preview

data-pipeline/setup/hil/03-run-cpu-smoke.sh \
  --connection-file <connection-receipt>
```

The result must identify the connected backend and pool, request zero GPUs, report no GPU device, and complete on the owned local node.

No-command proof:

```bash
data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt> \
  --config-preview

data-pipeline/setup/hil/04-run-no-command-check.sh \
  --connection-file <connection-receipt>
```

The result must contain representative proposed actions, zero applied actions, `command_transport: none`, a passed negative probe, `NO_COMMAND_TRANSPORT`, and the owned local node identity. The no-command check also requires the managed-identity OSMO upload timestamp, retrieves its unique output URI with `osmo data download`, and verifies the exact result manifest.

Static validation covers the repository contracts only. Run live Arc federation, runtime upload, and OSMO download validation against the target environment before treating durable output as operational.

## Failure and Rerun Behavior

Each script stops at the first failed required operation, preserves the native command error, names the incomplete milestone, and exits nonzero. The scripts do not diagnose an unproven external cause or switch transport automatically.

Rerun with the same target. Owned matching K3s, VPN, and connection state is verified or reconciled within its local boundary. Foreign, partial, symlinked, identity-mismatched, or drifted state stops for inspection rather than destructive cleanup.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
