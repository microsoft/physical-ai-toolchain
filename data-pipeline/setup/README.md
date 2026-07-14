# Data Pipeline Setup

Deployment scripts for Ubuntu edge hosts, K3s clusters, optional Azure Arc registration, and ACSA storage.

## 📋 Scope

| Area            | Description                                                        |
|-----------------|--------------------------------------------------------------------|
| Host preflight  | Validate Ubuntu, capacity, CIDRs, runtime ownership, and time sync |
| Certificate VPN | Configure strongSwan IKEv2 access without copying the root CA key  |
| K3s             | Install a checksum-pinned single-node cluster                      |
| Arc onboarding  | Connect the host and K3s cluster independently                     |
| ACSA deployment | Install Azure Container Storage for Arc and configure Blob sync    |

## 📜 Scripts

| Script                               | Purpose                                                                                 |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `edge/01-preflight.sh`               | Validate Ubuntu host and network safety                                                 |
| `edge/02-configure-vpn.sh`           | Generate a client CSR and configure certificate-authenticated strongSwan                |
| `edge/03-install-k3s.sh`             | Install pinned K3s and validate local storage                                           |
| `edge/04-connect-arc.sh`             | Optionally connect Arc-enabled server and Arc-enabled Kubernetes                        |
| `create-arc-onboarding-principal.sh` | Create an RG-scoped onboarding principal for approved headless automation               |
| `deploy-acsa.sh`                     | Install cert-manager + ACSA extensions, assign Blob role, apply PVC/subvolume manifests |

See [Ubuntu Edge K3s Setup](../../docs/data-pipeline/edge-k3s-setup.md) and the [ACSA setup guide](../../docs/data-pipeline/acsa-setup.md).
