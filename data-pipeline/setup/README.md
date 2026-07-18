# Data Pipeline Setup

Direct setup scripts for one Ubuntu T3 HiL node. The journey prepares the host, installs one owned K3s compute plane, optionally establishes private reachability, connects the local backend to an existing OSMO environment, and validates CPU and no-command behavior.

## 📋 Scope

| Milestone     | Outcome                                                                                        |
|---------------|------------------------------------------------------------------------------------------------|
| Host-ready    | Install only the tools needed by the preselected Key Vault or SCP path                         |
| Local compute | Install or verify one repository-owned K3s node without VPN, Arc, GPU, or storage dependencies |
| Reachable     | Optionally exchange public VPN material and establish bounded private routes                   |
| Connected     | Consume exact host-bound inputs and deploy only the local OSMO backend resources               |
| Validated     | Prove CPU-only scheduling and zero applied actions with no command transport                   |

## 📜 Scripts

| Script                                   | Purpose                                                                              |
|------------------------------------------|--------------------------------------------------------------------------------------|
| `hil/00-prepare-ubuntu.sh`               | Prepare Ubuntu for Key Vault by default or a deliberate SCP opt-out                  |
| `hil/01-install-k3s.sh`                  | Install or verify one pinned, owned local K3s compute plane                          |
| `hil/02-connect-osmo-backend.sh`         | Retrieve exact inputs and connect only the local backend                             |
| `hil/03-run-cpu-smoke.sh`                | Prove a CPU-only workflow completes on the owned local node                          |
| `hil/04-run-no-command-check.sh`         | Prove representative actions are rejected with zero applied actions                  |
| `hil/vpn/00-request-vpn-access.sh`       | Retrieve public VPN inputs, generate the local private key, and publish only the CSR |
| `hil/vpn/01-retrieve-vpn-certificate.sh` | Retrieve and validate the signed public response, then exit for vault closure        |
| `hil/vpn/02-connect-vpn.sh`              | Connect after private-only Key Vault access is restored and verified                 |
| `deploy-acsa.sh`                         | Install ACSA for a separate Arc-enabled storage workflow                             |

The trusted environment owner publishes host-bound inputs with `infrastructure/setup/04-prepare-osmo-hil-node.sh`. Ubuntu scripts do not create remote desired state or change Key Vault networking or RBAC.

See [Ubuntu Edge K3s Setup](../../docs/data-pipeline/edge-k3s-setup.md) and [Ubuntu HiL OSMO Backend](../../docs/recipes/tier-3-production/ubuntu-hil-osmo-backend.md).
