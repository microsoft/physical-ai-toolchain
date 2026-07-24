# Data Pipeline Setup

Direct setup scripts for one Ubuntu T3 HiL node. The scripts reconcile local resources so the same commands can be run again after partial or completed setup.

## 📋 Scope

| Milestone     | Outcome                                                                        |
|---------------|--------------------------------------------------------------------------------|
| Host-ready    | Install Ubuntu tooling, including Azure CLI, for Key Vault artifact retrieval  |
| Local compute | Reconcile one local K3s node without VPN, Arc, GPU, or storage dependencies    |
| Reachable     | Optionally exchange VPN material and reconcile the local strongSwan connection |
| Connected     | Retrieve inputs and reconcile the local OSMO backend resources                 |
| Workflows     | Submit CPU-only and no-command workflows                                       |

## 📜 Scripts

| Script                                   | Purpose                                                           |
|------------------------------------------|-------------------------------------------------------------------|
| `hil/00-prepare-ubuntu.sh`               | Install Ubuntu tools, including Azure CLI, for Key Vault transfer |
| `hil/01-install-k3s.sh`                  | Reconcile a pinned local K3s compute plane                        |
| `hil/02-connect-osmo-backend.sh`         | Retrieve inputs and reconcile the local OSMO backend              |
| `hil/03-run-cpu-smoke.sh`                | Submit and wait for a CPU-only workflow                           |
| `hil/04-run-no-command-check.sh`         | Submit and wait for a no-command workflow                         |
| `hil/vpn/00-request-vpn-access.sh`       | Retrieve VPN inputs, reuse the private key, and publish the CSR   |
| `hil/vpn/01-retrieve-vpn-certificate.sh` | Retrieve and install the signed public response                   |
| `hil/vpn/02-connect-vpn.sh`              | Reconcile the strongSwan connection and optional private DNS      |
| `deploy-acsa.sh`                         | Reconcile ACSA for a separate Arc-enabled storage workflow        |

The trusted environment owner publishes host-bound inputs to Key Vault with `infrastructure/setup/04-prepare-osmo-hil-node.sh`. The consumer validates the catalog, artifact digests, and token metadata before changing the local K3s target. Ubuntu scripts do not create remote desired state or change Key Vault networking or RBAC.

See [Ubuntu Edge K3s Setup](../../docs/data-pipeline/edge-k3s-setup.md) and [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md).
