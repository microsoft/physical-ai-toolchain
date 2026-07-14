# HiL Evaluation Specification

Hardware-in-the-loop (HiL) evaluation validates trained policies on physical robot hardware. The first supported deployment uses an Ubuntu desktop or workstation running K3s as the compute plane and an OSMO control plane hosted in Azure AKS.

## Topology

| Component          | Location                             | Responsibility                                                     |
|--------------------|--------------------------------------|--------------------------------------------------------------------|
| OSMO control plane | Azure AKS                            | Workflow API, backend registration, configuration, and user access |
| HiL compute plane  | Ubuntu K3s cluster                   | External OSMO backend operator and HiL workloads                   |
| Robot and sensors  | Physical site                        | Policy observations, actions, state, and safety signals            |
| Storage            | Azure Blob or approved local staging | Episode recordings, logs, metrics, and run artifacts               |

The edge backend operator initiates an outbound connection to the OSMO control plane. The control plane does not require an inbound route to the robot site.

## Network Modes

| Mode         | Requirement                                                     | Constraint                                                                                                                |
|--------------|-----------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------|
| Public       | Dedicated trusted HTTPS OSMO endpoint                           | Use endpoint policy, source restrictions where available, and an expiring OSMO token                                      |
| VPN lab      | Internal HTTP OSMO endpoint over certificate-authenticated VPN  | Keep the load balancer private and validate host and pod routing; HTTPS is optional for the accepted single-user lab risk |
| VPN hardened | Internal HTTPS OSMO endpoint over certificate-authenticated VPN | Use for production, additional users/sites, or untrusted network paths                                                    |

The AKS API endpoint and OSMO application endpoint are separate. Public AKS API access does not make an internal OSMO load balancer reachable. VPN-lab HTTP is permitted only while the endpoint remains private and the user accepts that application traffic is plaintext after the Azure VPN Gateway.

## Identity and Storage

- Use a dedicated Arc onboarding service principal with only the Arc server and Arc Kubernetes onboarding roles. Use it only during registration.
- Use the Arc server system-assigned managed identity for host-side Azure access when the upload process runs directly on Ubuntu.
- Use an Arc Kubernetes OIDC issuer, workload identity webhook, user-assigned managed identity, and federated credential for Kubernetes workloads that access Azure Blob.
- Use an expiring OSMO service token with the `osmo-backend` role for the external backend operator. Store it in a Kubernetes Secret created through a protected handoff.
- Support a container-scoped HTTPS user-delegation SAS as a fallback for upload processes that cannot use managed identity. Do not use storage account keys.

## Execution Modes

| Mode                      | Status      | Safety requirement                                                    | Expected use                                                  |
|---------------------------|-------------|-----------------------------------------------------------------------|---------------------------------------------------------------|
| Dry run                   | Implemented | No command-capable transport exists; negative command probe must fail | Validate scheduling, observation shape, timing, and artifacts |
| Operator-confirmed motion | Deferred    | Operator confirms workspace, robot state, and E-stop readiness        | First physical validation                                     |
| Bounded physical run      | Deferred    | Independent E-stop and configured action/workspace limits             | Short reproducible evaluation episode                         |

## Required Inputs

- Policy image digest and model version
- Robot identifier and hardware configuration
- Sensor and observation configuration
- OSMO backend name and endpoint mode
- Kubernetes namespace and ServiceAccount configuration
- Storage account/container and selected authentication mode
- Safety limits, operator identity, and E-stop procedure
- Run duration, task definition, and success criteria

## Required Artifacts

Each run records:

- Policy image digest and configuration
- Robot state and sensor health
- Observations, actions, and timestamps
- Safety events, E-stop events, and operator confirmations
- Per-episode outcome metrics
- Storage upload status and artifact checksums

Write artifacts to local durable storage first. Upload only after files are complete and quiescent. Redact tokens, SAS query strings, private keys, and other credentials from logs and metadata.

## Acceptance Criteria

- The Ubuntu host and K3s cluster report healthy Arc status when Arc is enabled.
- The external OSMO backend connects through the selected private VPN endpoint and uses an expiring `osmo-backend` token. Public endpoints require HTTPS.
- A CPU-only workflow runs on the edge before GPU or robot tests.
- A GPU workflow runs only after the actual driver, device plugin, and scheduler resources pass validation.
- A dry-run policy evaluation completes without physical motion.
- An operator confirms the first bounded physical run.
- E-stop behavior is verified independently of the software pipeline.
- The result identifies the exact policy, robot, sensors, safety events, and artifact locations.

## Status

The repository implements:

- Ubuntu host preflight and CIDR safety checks
- Certificate strongSwan setup with an external CA handoff
- Checksum-pinned single-node K3s installation
- Optional Arc server and Arc Kubernetes onboarding
- Stable private OSMO endpoint and additive HiL backend/pool desired state
- Token-authenticated OSMO external backend deployment to K3s
- CPU-only OSMO smoke workflow
- UR10E-shaped no-command dry run with local artifact checksums

Local no-command behavior is validated. VPN, K3s, external backend, Arc, GPU, and robot validation require user-owned infrastructure and hardware. GPU and physical motion remain deferred.
