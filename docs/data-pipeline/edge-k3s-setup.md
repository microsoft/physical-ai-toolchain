---
title: Ubuntu Edge K3s Setup
description: Configure certificate VPN access, pinned K3s, and optional Azure Arc registration on an Ubuntu HiL host.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-15
ms.topic: how-to
---

<!-- cspell:ignore inotify nofile -->

Configure an Ubuntu 22.04 or 24.04 desktop as a private K3s compute plane. This guide covers host preflight, strongSwan certificate VPN access, pinned K3s installation, and optional Azure Arc registration.

## Prerequisites

| Requirement                       | Purpose                                                       |
|-----------------------------------|---------------------------------------------------------------|
| Ubuntu 22.04 or 24.04             | Supported edge host                                           |
| 4 GiB memory and 20 GiB free disk | K3s, Arc agents, and OSMO workloads                           |
| Repository checkout               | Setup scripts and pinned configuration                        |
| VPN root CA owner                 | Signs the host-generated client CSR outside the Ubuntu host   |
| Azure VPN Generic profile         | Supplies `VpnSettings.xml` and `VpnServerRoot.cer`            |
| Azure CLI                         | Required only when enabling Azure Arc                         |
| Root access                       | Installs strongSwan, K3s, and the Arc Connected Machine agent |

> [!IMPORTANT]
> Keep the VPN root CA private key off the Ubuntu host. Copy only the host-generated CSR to the CA owner and return only the signed client certificate and public CA chain.

Set site-specific values on the Ubuntu host:

```bash
export AZURE_VNET_CIDR="<azure-vnet-cidr>"
export P2S_CLIENT_CIDR="<p2s-client-cidr>"
export OSMO_PRIVATE_URL="http://<osmo-private-ip>"
export EDGE_NODE_NAME="<unique-edge-node-name>"
export EDGE_K3S_VERSION="v1.32.6+k3s1"
export EDGE_KUBECONFIG="/var/lib/physical-ai-toolchain/kubeconfigs/${EDGE_NODE_NAME}.yaml"
export VPN_CONNECTION_NAME="${EDGE_NODE_NAME}-azure"
export VPN_CSR_DIR="$HOME/.local/share/physical-ai-toolchain/vpn-csr"
```

Use CIDRs that do not overlap the desktop LAN, K3s pod range `10.42.0.0/16`, or K3s service range `10.43.0.0/16`.

## Run Host Preflight

Run on the Ubuntu host:

```bash
data-pipeline/setup/edge/01-preflight.sh \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --config-preview
```

Run the checks and write a protected inventory:

```bash
sudo -v
data-pipeline/setup/edge/01-preflight.sh \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --inventory /var/lib/physical-ai-toolchain/edge-inventory.json
```

The script rejects:

- Unsupported Ubuntu releases
- Non-unified cgroup configuration
- Insufficient memory, disk, or file-system entries
- System time that is not synchronized
- Overlapping LAN, Azure, P2S, pod, or service CIDRs
- Unknown kubeadm, MicroK8s, K3s, containerd, or CNI ownership
- Occupied K3s API or kubelet ports

Active Ubuntu host swap is supported. Kubelet uses `failSwapOn: false` with `memorySwap.swapBehavior: NoSwap`, so host services can use swap while Pods cannot. Kubernetes recommends encrypted swap for any host that keeps swap enabled.

## Configure Certificate VPN

### Generate the client CSR

Run on the Ubuntu host:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --generate-csr \
  --connection-name "$VPN_CONNECTION_NAME" \
  --csr-dir "$VPN_CSR_DIR" \
  --config-preview
```

Generate the private key and CSR:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --generate-csr \
  --connection-name "$VPN_CONNECTION_NAME" \
  --csr-dir "$VPN_CSR_DIR"
```

Send `${VPN_CSR_DIR}/${VPN_CONNECTION_NAME}.csr` to the VPN CA owner. The returned leaf certificate must:

- Match the CSR private key
- Use `CN=${VPN_CONNECTION_NAME}`
- Include `CA:FALSE`
- Include the TLS client authentication extended key usage
- Chain to the public root certificate configured on Azure VPN Gateway

### Collect the Azure profile values

Extract the Azure VPN client profile downloaded from the gateway. Record:

| File or value                         | Use                                               |
|---------------------------------------|---------------------------------------------------|
| `Generic/VpnSettings.xml` `VpnServer` | `--gateway` value                                 |
| `Generic/VpnServerRoot.cer`           | Azure VPN server trust certificate                |
| Signed client certificate             | Client authentication                             |
| Client CA chain                       | Client certificate validation before installation |

Convert DER certificates to PEM when required:

```bash
openssl x509 -inform der -in Generic/VpnServerRoot.cer -out "$HOME/VpnServerRoot.pem"
```

Protect all certificate inputs before installation:

```bash
chmod 0600 \
  "$VPN_CSR_DIR/${VPN_CONNECTION_NAME}.key" \
  "$HOME/<signed-client-certificate>.pem" \
  "$HOME/<client-ca-chain>.pem" \
  "$HOME/VpnServerRoot.pem"
```

### Install strongSwan

Preview from the Ubuntu host:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --install \
  --connection-name "$VPN_CONNECTION_NAME" \
  --gateway "<VpnServer-from-VpnSettings.xml>" \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --osmo-url "$OSMO_PRIVATE_URL" \
  --client-certificate "$HOME/<signed-client-certificate>.pem" \
  --client-key "$VPN_CSR_DIR/${VPN_CONNECTION_NAME}.key" \
  --client-ca-certificate "$HOME/<client-ca-chain>.pem" \
  --vpn-server-ca "$HOME/VpnServerRoot.pem" \
  --config-preview
```

Install and connect:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --install \
  --connection-name "$VPN_CONNECTION_NAME" \
  --gateway "<VpnServer-from-VpnSettings.xml>" \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --osmo-url "$OSMO_PRIVATE_URL" \
  --client-certificate "$HOME/<signed-client-certificate>.pem" \
  --client-key "$VPN_CSR_DIR/${VPN_CONNECTION_NAME}.key" \
  --client-ca-certificate "$HOME/<client-ca-chain>.pem" \
  --vpn-server-ca "$HOME/VpnServerRoot.pem"
```

Validate the assigned P2S address, Azure route, and private OSMO endpoint:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --status \
  --connection-name "$VPN_CONNECTION_NAME" \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --osmo-url "$OSMO_PRIVATE_URL"
```

The strongSwan configuration installs only the Azure VNet route. The normal Internet default route remains unchanged.

## Install K3s

Preview from the Ubuntu host:

```bash
data-pipeline/setup/edge/03-install-k3s.sh \
  --node-name "$EDGE_NODE_NAME" \
  --kubeconfig-out "$EDGE_KUBECONFIG" \
  --config-preview
```

Install the pinned K3s binary and run the local-path PVC smoke test:

```bash
data-pipeline/setup/edge/03-install-k3s.sh \
  --node-name "$EDGE_NODE_NAME" \
  --kubeconfig-out "$EDGE_KUBECONFIG"
```

The script configures:

| Setting            | Value              |
|--------------------|--------------------|
| K3s version        | `v1.32.6+k3s1`     |
| Pod CIDR           | `10.42.0.0/16`     |
| Service CIDR       | `10.43.0.0/16`     |
| Kubeconfig mode    | `0600`             |
| Secrets encryption | Enabled            |
| Host swap          | Allowed             |
| Pod swap           | `NoSwap`            |
| Inotify instances  | At least `8192`     |
| Inotify watches    | At least `524288`   |
| System file handles | At least `100000`  |
| Per-process file ceiling | At least `1048576` |
| K3s `LimitNOFILE`  | `1048576`           |
| Traefik            | Disabled           |
| ServiceLB          | Disabled           |
| Local-path storage | Enabled and tested |

Validate the explicit context:

```bash
kubectl --kubeconfig "$EDGE_KUBECONFIG" \
  --context physical-ai-edge \
  get nodes -o wide
```

Prove pod traffic reaches OSMO with the assigned P2S source address:

```bash
data-pipeline/setup/edge/02-configure-vpn.sh \
  --status \
  --connection-name "$VPN_CONNECTION_NAME" \
  --azure-vnet-cidr "$AZURE_VNET_CIDR" \
  --p2s-cidr "$P2S_CLIENT_CIDR" \
  --osmo-url "$OSMO_PRIVATE_URL" \
  --edge-kubeconfig "$EDGE_KUBECONFIG" \
  --edge-context physical-ai-edge \
  --pod-probe
```

K3s does not depend on the VPN systemd unit. VPN interruptions stop the external OSMO connection but do not stop the local cluster.

## Enable Azure Arc

Arc is optional for the private OSMO backend. Enable it for Azure management, Arc extensions, or workload identity after VPN, K3s, and the external backend pass their CPU validation.

### Register Azure providers

Run from the Azure operator workstation before onboarding:

```bash
source infrastructure/terraform/prerequisites/az-sub-init.sh
```

The canonical provider list includes:

- Microsoft.HybridCompute
- Microsoft.GuestConfiguration
- Microsoft.HybridConnectivity
- Microsoft.AzureArcData
- Microsoft.Kubernetes
- Microsoft.KubernetesConfiguration
- Microsoft.ExtendedLocation

### Authenticate the Ubuntu host

Install Azure CLI using the Microsoft signed apt repository, then authenticate with the target tenant and subscription. Do not place a service-principal secret in shell history.

```bash
az login --tenant "<tenant-id>" --use-device-code
az account set --subscription "<subscription-id>"
```

### Connect Arc server and Arc Kubernetes

Preview from the Ubuntu host:

```bash
data-pipeline/setup/edge/04-connect-arc.sh \
  --subscription-id "<subscription-id>" \
  --tenant-id "<tenant-id>" \
  --resource-group "<arc-resource-group>" \
  --location "<azure-location>" \
  --server-name "$EDGE_NODE_NAME" \
  --cluster-name "${EDGE_NODE_NAME}-k3s" \
  --kubeconfig "$EDGE_KUBECONFIG" \
  --context physical-ai-edge \
  --enable-server \
  --enable-kubernetes \
  --config-preview
```

Connect both resources:

```bash
data-pipeline/setup/edge/04-connect-arc.sh \
  --subscription-id "<subscription-id>" \
  --tenant-id "<tenant-id>" \
  --resource-group "<arc-resource-group>" \
  --location "<azure-location>" \
  --server-name "$EDGE_NODE_NAME" \
  --cluster-name "${EDGE_NODE_NAME}-k3s" \
  --kubeconfig "$EDGE_KUBECONFIG" \
  --context physical-ai-edge \
  --enable-server \
  --enable-kubernetes
```

Add `--enable-workload-identity` only when a named K3s workload needs direct Azure SDK access. This option configures the Arc OIDC issuer in K3s and restarts K3s.

Verify independently:

```bash
sudo azcmagent show --json | jq '{status, resourceId}'
```

```bash
az connectedk8s show \
  --name "${EDGE_NODE_NAME}-k3s" \
  --resource-group "<arc-resource-group>" \
  --query '{id:id,state:provisioningState,connectivity:connectivityStatus}'
```

> [!NOTE]
> `create-arc-onboarding-principal.sh` creates RG-scoped onboarding credentials for approved headless automation. Current Arc CLI secret flags expose the secret in process arguments, so the supported local-host path uses device-code authentication instead.

## Next Step

Connect the K3s compute plane to OSMO and run the CPU/no-command gates in [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md).
