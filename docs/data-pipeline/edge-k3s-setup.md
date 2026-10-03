---
title: Ubuntu HiL Host and K3s Setup
description: Prepare Ubuntu and install one owned local K3s compute plane for the progressive T3 HiL journey.
author: Microsoft Robotics-AI Team
ms.date: 2026-09-29
ms.topic: how-to
---

Prepare Ubuntu 22.04 or 24.04 and install one pinned, owned K3s node. Local compute readiness does not depend on VPN, Azure Arc, GPU support, storage integration, or access to the remote OSMO environment.

## Prerequisites

| Requirement                              | Purpose                                             |
|------------------------------------------|-----------------------------------------------------|
| Ubuntu 22.04 or 24.04 on x86_64 or ARM64 | Supported host and client packages                  |
| Repository checkout                      | Pinned scripts and configuration                    |
| Root access                              | Package, K3s binary, and systemd installation       |
| Key Vault access                         | Retrieves the exact protected catalog and artifacts |

Host preparation always installs Azure CLI for later device-code authentication and exact Key Vault transfer. Repository HiL scripts do not support SCP as a transfer path.

## Prepare Ubuntu

Preview host preparation:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh --config-preview
```

Prepare the host:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh
```

Host preparation installs the common Ubuntu packages, checksum-pinned Helm and OSMO clients, and Azure CLI. It does not authenticate, access Key Vault, discover Azure resources, or change remote state.

## Install Local K3s

Preview the local compute target:

```bash
data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name <host-name> \
  --config-preview
```

Install or verify the owned cluster:

```bash
data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name <host-name>
```

The script:

* Verifies the selected Pod and Service CIDRs do not overlap
* Refuses kubeadm, MicroK8s, unmanaged K3s, unmanaged kubelet, and unmanaged CNI state
* Verifies the pinned K3s binary before installation
* Writes one root-owned ownership marker and exact K3s configuration
* Creates one current-user kubeconfig with mode `0600`
* Verifies the explicit context, node identity, version, and readiness

Rerunning the same target verifies owned state. A changed or foreign target stops before mutation.

## Connect to Azure Arc

Connect K3s to Azure Arc when the environment attaches the host as an OSMO backend or an Azure ML compute. Sign in with Azure CLI, then preview the connection:

```bash
data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id <subscription-id> \
  --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> \
  --location <azure-region> \
  --cluster-name <arc-cluster-name> \
  --kubeconfig <protected-k3s-kubeconfig> \
  --cluster-admin-signed-in-user \
  --config-preview
```

Run the same command without `--config-preview`. Add `--enable-workload-identity` when OSMO workloads on the host use Arc workload identity; that option restarts K3s once.

Arc cluster connect (`az connectedk8s proxy`) authenticates operators with Microsoft Entra ID, but K3s authorizes each request with its own RBAC. Grant access while connecting so the first proxy session works:

| Option                           | Grants `cluster-admin` to                               |
|----------------------------------|---------------------------------------------------------|
| `--cluster-admin-signed-in-user` | The signed-in Azure CLI user                            |
| `--cluster-admin-object-id <id>` | A Microsoft Entra user or service principal; repeatable |
| `--cluster-admin-group-id <id>`  | Members of a Microsoft Entra group; repeatable          |

Arc presents identities by object ID, including guest accounts, so the grants take object IDs rather than user principal names. Each subject gets one ClusterRoleBinding named `arc-cluster-admin-user-<id>` or `arc-cluster-admin-group-<id>`, and reruns leave existing bindings unchanged.

Operators also need the Azure Arc Enabled Kubernetes Cluster User Role on the Arc resource to open the proxy. For read-only access, bind the built-in `view` ClusterRole plus a ClusterRole that can read nodes instead of granting `cluster-admin`.

## Choose Reachability

Skip VPN when the environment's approved OSMO endpoint and Key Vault are already reachable. Local K3s remains ready in either case.

When private routing is required, follow the optional VPN section in [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md#optional-private-reachability). The VPN sequence uses exact public inputs, keeps the Ubuntu private key on the host, and has a visible stop for private-only Key Vault restoration before connection.

## Next Step

Have the environment owner publish the host-bound HiL inputs, then continue with [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
