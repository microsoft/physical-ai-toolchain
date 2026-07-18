---
title: Ubuntu HiL Host and K3s Setup
description: Prepare Ubuntu and install one owned local K3s compute plane for the progressive T3 HiL journey.
author: Microsoft Robotics-AI Team
ms.date: 2026-07-17
ms.topic: how-to
---

Prepare Ubuntu 22.04 or 24.04 and install one pinned, owned K3s node. Local compute readiness does not depend on VPN, Azure Arc, GPU support, storage integration, or access to the remote OSMO environment.

## Prerequisites

| Requirement                              | Purpose                                                    |
|------------------------------------------|------------------------------------------------------------|
| Ubuntu 22.04 or 24.04 on x86_64 or ARM64 | Supported host and client packages                         |
| Repository checkout                      | Pinned scripts and configuration                           |
| Root access                              | Package, K3s binary, and systemd installation              |
| Transfer choice                          | Select Key Vault by default or SCP before host preparation |

The Key Vault path installs Azure CLI for later device-code authentication and exact secret transfer. The SCP opt-out omits Azure CLI unless another operation outside this journey requires it.

## Prepare Ubuntu

Preview the default Key Vault path:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh --config-preview
```

Prepare the host:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh
```

Select SCP before setup only when the environment owner provides the exact protected catalog and artifacts through that transport:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh \
  --transport scp \
  --config-preview

data-pipeline/setup/hil/00-prepare-ubuntu.sh \
  --transport scp
```

Host preparation installs the common Ubuntu packages, checksum-pinned Helm and OSMO clients, and Azure CLI only for Key Vault. It does not authenticate, access Key Vault, discover Azure resources, or change remote state.

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

## Choose Reachability

Skip VPN when the environment's approved OSMO endpoint and Key Vault are already reachable. Local K3s remains ready in either case.

When private routing is required, follow the optional VPN section in [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md#optional-private-reachability). The VPN sequence uses exact public inputs, keeps the Ubuntu private key on the host, and has a visible stop for private-only Key Vault restoration before connection.

## Next Step

Have the environment owner publish the host-bound HiL inputs, then continue with [Ubuntu HiL OSMO Backend](../recipes/tier-3-production/ubuntu-hil-osmo-backend.md).

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
