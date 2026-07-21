---
title: Ubuntu HiL OSMO Backend
description: Prepare one Ubuntu T3 HiL node, optionally establish private reachability, connect it to an existing OSMO environment, and prove CPU and no-command outcomes.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-21
ms.topic: tutorial
---

Move one Ubuntu desktop through four T3 HiL milestones: host-ready, reachable when private routing is required, connected to an existing OSMO backend and pool, and validated for CPU and no-command workloads. Key Vault is the default transfer. SCP is a deliberate preselected opt-out that supplies the same protected catalog and artifacts to the same consumers.

Complete the direct [T0 target-policy-hardware HiL](../tier-0-dev/README.md#step-7-run-target-policy-hardware-hil) workflow first. This T3 guide adds OSMO/K3s orchestration and passive artifact delivery; it does not change the selected policy, scene or task, ROS 2 semantics, or local result contract.

> [!WARNING]
> The CPU and no-command proofs complete this journey. No command transport or physical motion is supported.

## Responsibilities

| Owner             | Responsibilities                                                                                                                                 | Excluded work                                                                                                   |
|-------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| Environment owner | Verify the existing OSMO endpoint, backend, pool, charts, images, registry access, Key Vault secrets, per-secret roles, and coherent publication | Local K3s or Ubuntu mutation                                                                                    |
| Ubuntu user       | Select transport, prepare the host, install owned K3s, optionally connect VPN, consume exact inputs, and run validation                          | AKS credentials, Azure resource administration, Key Vault networking or RBAC changes, remote OSMO desired state |
| VPN CA owner      | Sign the Ubuntu CSR and publish only the signed leaf and public chain                                                                            | Moving the CA private key or Ubuntu private key                                                                 |

## Select the Transfer

Choose one transport before host preparation:

| Transport | Host preparation                    | Artifact source                                                       | Failure behavior                                              |
|-----------|-------------------------------------|-----------------------------------------------------------------------|---------------------------------------------------------------|
| Key Vault | Default; installs Azure CLI         | Exact secret names and immutable versions from the host-bound catalog | Stop on login, access, network, target, or integrity failure  |
| SCP       | Deliberate opt-out; omits Azure CLI | Exact catalog and artifact directory emitted by the trusted publisher | Stop on missing, protected-path, target, or integrity failure |

A Key Vault error never invokes SCP. Both transports use the same catalog schema, file names, digests, protected local directory, validation, and local mutation code. For SCP publication, pass `--transport scp --output-dir <protected-scp-handoff-directory>`.

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
  --chart-version <deployed-chart-version> \
  --backend-chart-ref <approved-backend-chart-reference> \
  --backend-chart-sha256 <approved-backend-chart-sha256> \
  --image-version <deployed-image-version> \
  --image-location <approved-image-prefix> \
  --vpn-input-dir <protected-public-vpn-inputs> \
  --output-dir <protected-scp-handoff-directory> \
  --config-preview
```

Run the same command without `--config-preview`. Omit `--vpn-input-dir` when private routing is unnecessary. Omit `--output-dir` unless a user deliberately selected SCP.

The publisher:

* Verifies the active Azure account and existing OSMO backend and pool
* Issues a current-identity service token with only the `osmo-backend` role
* Preserves the generic non-secret environment-bundle allowlist
* Publishes credentials, registry access, immutable artifacts, and public VPN material through separate exact secrets
* Writes every artifact before the host-bound catalog
* Never assigns roles or changes Key Vault networking

Record these environment gates separately as passed with authorization or not run:

1. Every named inbound secret and the CSR secret has the intended individual-secret role.
2. The Ubuntu identity has no inherited or direct vault-wide data-plane role.
3. The complete exact artifact set was published before the catalog.

## Prepare Ubuntu and K3s

Preview and run host preparation with the chosen transport:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh \
  --transport keyvault \
  --config-preview

data-pipeline/setup/hil/00-prepare-ubuntu.sh \
  --transport keyvault
```

Use `--transport scp` for the deliberate opt-out.

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
  --publish-vpn-response <protected-vpn-response.json> \
  --catalog-file <protected-current-catalog.json>
```

For the preselected SCP opt-out, `vpn/00-request-vpn-access.sh` writes `vpn-request.json` into the protected copied directory. Transfer only that request back to the environment owner. Publish the response with `--transport scp --csr-file <protected-vpn-request.json>` and the same `--output-dir <protected-scp-handoff-directory>` used during initial publication. Copy only the updated catalog and public response through the approved SSH channel. The Ubuntu private key is never part of the SCP handoff.

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
  --transport keyvault \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --private-vault-verified \
  --config-preview

data-pipeline/setup/hil/vpn/02-connect-vpn.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --transport keyvault \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --private-vault-verified
```

The connection command performs no Key Vault access before VPN. It consumes local protected material, preserves the public default route, applies private routes and optional route-only DNS, verifies public DNS, and then checks private Key Vault reachability.

## Connect the Local Backend

Key Vault path:

```bash
data-pipeline/setup/hil/02-connect-osmo-backend.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --config-preview

data-pipeline/setup/hil/02-connect-osmo-backend.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault>
```

SCP opt-out:

```bash
data-pipeline/setup/hil/02-connect-osmo-backend.sh \
  --environment <environment> \
  --host-name <host> \
  --tenant-id <tenant-id> \
  --subscription <subscription-id> \
  --vault-name <vault> \
  --transport scp \
  --scp-source-dir <protected-copied-artifact-directory>
```

The stage authenticates the end user by OSMO code login, retrieves or copies the exact catalog-bound artifacts, and changes only the owned local K3s target. It writes a non-secret connection receipt only after the backend reports online.

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

The result must contain representative proposed actions, zero applied actions, `command_transport: none`, a passed negative probe, `NO_COMMAND_TRANSPORT`, and the owned local node identity.

## Failure and Rerun Behavior

Each script stops at the first failed required operation, preserves the native command error, names the incomplete milestone, and exits nonzero. The scripts do not diagnose an unproven external cause or switch transport automatically.

Rerun with the same target. Owned matching K3s, VPN, and connection state is verified or reconciled within its local boundary. Foreign, partial, symlinked, identity-mismatched, or drifted state stops for inspection rather than destructive cleanup.

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
