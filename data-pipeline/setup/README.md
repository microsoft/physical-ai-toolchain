# Data Pipeline Setup

Deployment scripts for Arc-connected edge agents that run the ROS 2 recording service. Scripts in this directory handle agent provisioning, connectivity validation, and runtime configuration on edge devices.

## 📋 Scope

| Area                    | Description                                             |
|-------------------------|---------------------------------------------------------|
| Arc agent provisioning  | Connect edge devices to Azure Arc-enabled Kubernetes    |
| Connectivity validation | Verify cloud connectivity and service endpoints         |
| Runtime configuration   | Deploy recording configuration and service dependencies |
| ACSA deployment         | Install Azure Container Storage for Arc and configure Blob sync |

## 📜 Scripts

| Script             | Purpose                                                                          |
|--------------------|-----------------------------------------------------------------------------------|
| `deploy-acsa.sh`   | Install cert-manager + ACSA extensions, assign Blob role, apply PVC/subvolume manifests |

See the [ACSA setup guide](../../docs/data-pipeline/acsa-setup.md) for deployment instructions.
