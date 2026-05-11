---
sidebar_position: 2
title: ACSA Setup for ROS 2 Bag Sync
description: Deploy Azure Container Storage enabled by Azure Arc (ACSA) on edge clusters to sync ROS 2 bag files to Azure Blob Storage
author: Microsoft Robotics-AI Team
ms.date: 2026-04-13
ms.topic: how-to
keywords:
  - acsa
  - azure container storage
  - arc storage
  - ros2 bag
  - blob sync
  - edge storage
  - ingest subvolume
---

Deploy Azure Container Storage enabled by Azure Arc (ACSA) on Arc-connected edge clusters to automatically sync ROS 2 bag files to Azure Blob Storage. ACSA provides cloud-backed persistent volumes that handle ingest, caching, and eviction transparently — recording pods write to a local PVC and files sync to Blob Storage without application-level upload logic.

## 🏗️ Architecture

```text
┌─────────────────────────────────────────────┐
│  Edge Cluster (Arc-connected)               │
│                                             │
│  ┌──────────────┐    ┌───────────────────┐  │
│  │ ROS 2        │    │ ACSA Extension    │  │
│  │ Recording Pod│───▶│ (Edge Volume)     │  │
│  │              │    │                   │  │
│  │ writes to    │    │ IngestSubvolume   │  │
│  │ /recording   │    │ controller syncs  │  │
│  └──────────────┘    │ oldest-first      │  │
│         │            └─────────┬─────────┘  │
│         ▼                      │            │
│  ┌──────────────┐              │            │
│  │ PVC          │              │            │
│  │ recording-   │              │            │
│  │ data (50Gi)  │              │            │
│  └──────────────┘              │            │
└────────────────────────────────┼────────────┘
                                 │ HTTPS
                                 ▼
                    ┌────────────────────────┐
                    │ Azure Blob Storage     │
                    │ datasets/recordings/   │
                    └────────────────────────┘
```

Recording pods mount the `recording-data` PVC and write bag files to it. The ACSA `IngestSubvolume` controller detects new files and syncs them to a Blob Storage container using managed identity authentication. Local cache eviction removes files after a configurable delay, freeing disk space for continued recording.

## 📋 Prerequisites

| Requirement                     | Details                                                                                       |
|---------------------------------|-----------------------------------------------------------------------------------------------|
| Azure Arc-connected K8s cluster | Edge cluster registered with Azure Arc (`az connectedk8s show`)                               |
| Azure CLI 2.60+                 | With `k8s-extension` and `connectedk8s` extensions                                            |
| Terraform outputs               | Infrastructure deployed via `infrastructure/terraform/` with a storage account                |
| kubectl + envsubst              | For manifest rendering and application                                                        |
| Azure RBAC                      | Contributor on the Arc cluster resource group; Storage Blob Data Owner on the storage account |
| Network connectivity            | Direct kubectl access or Arc proxy for private clusters                                       |

> [!NOTE]
> The deploy script automatically installs missing Azure CLI extensions (`k8s-extension`, `connectedk8s`).

## 🚀 Quick Start

```bash
cd data-pipeline/setup

# Preview configuration without making changes
./deploy-acsa.sh --config-preview \
  --cluster-name <arc-cluster> \
  --cluster-resource-group <rg> \
  --storage-account <storage-account>

# Deploy ACSA with Terraform auto-discovery
./deploy-acsa.sh \
  --cluster-name <arc-cluster> \
  --cluster-resource-group <rg> \
  --storage-account <storage-account>
```

The script reads storage account details from `infrastructure/terraform/terraform.tfstate`. Override any value via CLI arguments or environment variables.

## ⚙️ Configuration

### Script Arguments

| Argument                   | Environment Variable             | Default                          | Description                              |
|----------------------------|----------------------------------|----------------------------------|------------------------------------------|
| `--cluster-name`           | `ARC_CLUSTER_NAME`               | (required)                       | Arc-connected cluster name               |
| `--cluster-resource-group` | `ARC_RESOURCE_GROUP`             | (required)                       | Resource group of the Arc cluster        |
| `-t, --tf-dir`             | `DEFAULT_TF_DIR`                 | `../../infrastructure/terraform` | Terraform directory for output discovery |
| `--storage-account`        | `STORAGE_ACCOUNT_NAME`           | Auto-discovered from Terraform   | Storage account name override            |
| `--storage-resource-group` | `STORAGE_ACCOUNT_RESOURCE_GROUP` | Same as cluster resource group   | Storage account resource group           |
| `--connectivity-mode`      | `ACSA_CONNECTIVITY_MODE`         | `direct`                         | `direct` or `proxy`                      |
| `--proxy-port`             | `ACSA_PROXY_PORT`                | `47011`                          | Arc proxy port (proxy mode only)         |
| `--config-preview`         | —                                | —                                | Print configuration and exit             |

### Defaults Configuration

Central defaults live in `data-pipeline/setup/defaults.conf`. Override any value via environment variables before running the script.

| Variable                      | Default                    | Description                                         |
|-------------------------------|----------------------------|-----------------------------------------------------|
| `ACSA_EXTENSION_VERSION`      | `2.11.2`                   | ACSA Arc extension version                          |
| `ACSA_RELEASE_TRAIN`          | `stable`                   | Extension release train                             |
| `ACSA_DISK_STORAGE_CLASS`     | `default,local-path`       | Backing disk storage classes                        |
| `ACSA_PVC_NAME`               | `recording-data`           | PVC name for recording volume                       |
| `ACSA_PVC_SIZE`               | `50Gi`                     | PVC storage request                                 |
| `ACSA_STORAGE_CLASS`          | `cloud-backed-sc`          | ACSA storage class name                             |
| `BLOB_CONTAINER_NAME`         | `datasets`                 | Target Blob Storage container                       |
| `SUBVOLUME_NAME`              | `recordings`               | IngestSubvolume resource name                       |
| `SUBVOLUME_PATH`              | `recordings`               | Path prefix within the Blob container               |
| `ACSA_INGEST_ORDER`           | `oldest-first`             | File ingest order (`oldest-first`)                  |
| `ACSA_INGEST_MIN_DELAY_SEC`   | `30`                       | Minimum delay before ingesting a file               |
| `ACSA_EVICTION_ORDER`         | `unordered`                | Cache eviction order                                |
| `ACSA_EVICTION_MIN_DELAY_SEC` | `600`                      | Minimum time (seconds) before evicting cached files |
| `ACSA_ON_DELETE`              | `trigger-immediate-ingest` | Behavior when the subvolume is deleted              |
| `EDGE_NAMESPACE`              | `data-pipeline`            | Kubernetes namespace for ACSA resources             |

### Sync Behavior

ACSA `IngestSubvolume` controls how files move from edge to cloud:

| Parameter      | Default                    | Behavior                                                            |
|----------------|----------------------------|---------------------------------------------------------------------|
| Ingest order   | `oldest-first`             | Oldest files sync first, preserving recording chronology            |
| Ingest delay   | 30 seconds                 | Wait before syncing — avoids uploading files still being written    |
| Eviction delay | 600 seconds (10 minutes)   | Keep cached files locally after upload for re-reads                 |
| On delete      | `trigger-immediate-ingest` | Upload all remaining data immediately when the subvolume is deleted |

## 📦 Deployment Steps

The `deploy-acsa.sh` script executes these steps in order:

1. Read Terraform outputs to discover the storage account and resource group
2. Validate cluster connectivity (direct kubectl or Arc proxy)
3. Create the `data-pipeline` namespace
4. Install the `arc-cert-manager` extension (ACSA dependency)
5. Wait for cert-manager to reach `Succeeded` state
6. Install the `azure-arc-containerstorage` extension
7. Wait for ACSA extension to reach `Succeeded` state
8. Retrieve the ACSA managed identity principal ID
9. Assign `Storage Blob Data Owner` role to the ACSA identity on the storage account
10. Create the `datasets` Blob container
11. Render and apply the PVC and IngestSubvolume manifests
12. Wait for the PVC to bind and EdgeVolume to deploy

## 🔌 Connectivity Modes

### Direct Mode (Default)

Use when `kubectl` can reach the cluster API server directly — either via VPN, public endpoint, or local network.

```bash
./deploy-acsa.sh \
  --cluster-name my-edge-cluster \
  --cluster-resource-group rg-edge \
  --storage-account mystorageaccount
```

### Proxy Mode

Use when the cluster API server is unreachable from the dev machine. The script starts an Arc proxy tunnel automatically and cleans it up on exit.

```bash
./deploy-acsa.sh \
  --connectivity-mode proxy \
  --cluster-name my-edge-cluster \
  --cluster-resource-group rg-edge \
  --storage-account mystorageaccount
```

> [!NOTE]
> Arc proxy requires the `connectedk8s` CLI extension and an authenticated Azure session. The proxy creates a temporary kubeconfig and listens on port 47011 by default.

## 📄 Manifest Templates

Two Kubernetes manifest templates in `data-pipeline/arc/` are rendered using `envsubst` during deployment.

### PVC Template (`acsa-pvc.yaml`)

Creates a `ReadWriteMany` PersistentVolumeClaim backed by the ACSA `cloud-backed-sc` storage class.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ${ACSA_PVC_NAME}
  namespace: ${EDGE_NAMESPACE}
spec:
  accessModes:
    - ReadWriteMany
  resources:
    requests:
      storage: ${ACSA_PVC_SIZE}
  storageClassName: ${ACSA_STORAGE_CLASS}
```

### IngestSubvolume Template (`acsa-ingest-subvolume.yaml`)

Defines the sync policy between the edge volume and Blob Storage.

```yaml
apiVersion: arccontainerstorage.azure.net/v1
kind: IngestSubvolume
metadata:
  name: ${SUBVOLUME_NAME}
  namespace: ${EDGE_NAMESPACE}
spec:
  edgevolume: ${ACSA_PVC_NAME}
  path: ${SUBVOLUME_PATH}
  authentication:
    authType: MANAGED_IDENTITY
  storageAccountEndpoint: "https://${STORAGE_ACCOUNT_NAME}.blob.core.windows.net/"
  containerName: ${BLOB_CONTAINER_NAME}
  ingest:
    order: ${ACSA_INGEST_ORDER}
    minDelaySec: ${ACSA_INGEST_MIN_DELAY_SEC}
  eviction:
    order: ${ACSA_EVICTION_ORDER}
    minDelaySec: ${ACSA_EVICTION_MIN_DELAY_SEC}
  onDelete: ${ACSA_ON_DELETE}
```

## 🔍 Verification

After deployment, verify the resources are healthy:

```bash
# Check PVC is bound
kubectl -n data-pipeline get pvc recording-data
# Expected: STATUS = Bound

# Check EdgeVolume is deployed
kubectl -n data-pipeline get edgevolumes recording-data
# Expected: STATE = deployed

# Check IngestSubvolume exists
kubectl -n data-pipeline get ingestsubvolumes recordings

# Check ACSA extension status
az k8s-extension show \
  --name azure-arc-containerstorage \
  --cluster-name <arc-cluster> \
  --resource-group <rg> \
  --cluster-type connectedClusters \
  --query provisioningState -o tsv
# Expected: Succeeded

# Verify blob container exists
az storage container show \
  --account-name <storage-account> \
  --name datasets \
  --auth-mode login \
  --query name -o tsv
# Expected: datasets
```

### Test Sync

Write a test file to the PVC and confirm it appears in Blob Storage:

```bash
# Create a test pod that writes to the PVC
kubectl -n data-pipeline run acsa-test \
  --image=busybox \
  --restart=Never \
  --overrides='{
    "spec": {
      "containers": [{
        "name": "acsa-test",
        "image": "busybox",
        "command": ["sh", "-c", "echo test > /data/test.txt && sleep 60"],
        "volumeMounts": [{"name": "recording", "mountPath": "/data"}]
      }],
      "volumes": [{
        "name": "recording",
        "persistentVolumeClaim": {"claimName": "recording-data"}
      }]
    }
  }'

# Wait for ingest delay (30s default), then check Blob Storage
az storage blob list \
  --account-name <storage-account> \
  --container-name datasets \
  --prefix recordings/ \
  --auth-mode login \
  --query "[].name" -o tsv

# Clean up test pod
kubectl -n data-pipeline delete pod acsa-test
```

## 🔧 Troubleshooting

### PVC Stuck in Pending

The ACSA extension may not have finished provisioning the storage class.

```bash
# Check storage classes
kubectl get storageclass cloud-backed-sc

# Check ACSA extension pods
kubectl -n azure-arc-containerstorage get pods

# Check extension events
kubectl -n azure-arc-containerstorage get events --sort-by='.lastTimestamp'
```

### Extension Provisioning Failed

```bash
# View extension details
az k8s-extension show \
  --name azure-arc-containerstorage \
  --cluster-name <arc-cluster> \
  --resource-group <rg> \
  --cluster-type connectedClusters \
  --query '{state: provisioningState, error: errorInfo}'
```

### Files Not Syncing

1. Confirm the IngestSubvolume exists and has the correct storage account endpoint
2. Verify the ACSA managed identity has `Storage Blob Data Owner` on the storage account
3. Check that files are older than `ACSA_INGEST_MIN_DELAY_SEC` (30s default)

```bash
# Check ACSA identity role assignment
az role assignment list \
  --scope "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account>" \
  --query "[?roleDefinitionName=='Storage Blob Data Owner'].{principal:principalId, role:roleDefinitionName}" \
  -o table
```

### Arc Proxy Connection Failures

```bash
# Verify Arc agent is connected
az connectedk8s show \
  --name <arc-cluster> \
  --resource-group <rg> \
  --query connectivityStatus -o tsv
# Expected: Connected

# Check port availability
lsof -i :47011
```

## 📚 Related Documentation

| Resource                                                                                                                              | Description                                                    |
|---------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| [Chunking and Compression Configuration](chunking-compression-config.md)                                                              | ROS 2 bag chunking and compression settings for edge recording |
| [Azure Container Storage enabled by Azure Arc](https://learn.microsoft.com/azure/azure-arc/container-storage/)                        | Microsoft documentation for ACSA                               |
| [IngestSubvolume specification](https://learn.microsoft.com/azure/azure-arc/container-storage/cloud-ingest-edge-volume-configuration) | CRD reference for `IngestSubvolume`                            |
