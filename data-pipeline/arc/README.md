# Arc Configuration

Kubernetes manifests and configuration for Arc-connected edge data pipeline components. Resources in this directory define the workloads, services, and policies that run on Arc-enabled edge clusters.

## 📋 Scope

| Area               | Description                                                    |
|--------------------|----------------------------------------------------------------|
| K8s manifests      | Deployments, services, and config maps for recording workloads |
| RBAC policies      | Service accounts and role bindings for edge agents             |
| Flux configuration | GitOps sync definitions for automated edge deployment          |
| ACSA manifests     | PVC and IngestSubvolume templates for cloud-backed edge storage |

## 📄 ACSA Manifests

| File                          | Description                                           |
|-------------------------------|-------------------------------------------------------|
| `acsa-pvc.yaml`               | ReadWriteMany PVC backed by ACSA `cloud-backed-sc`   |
| `acsa-ingest-subvolume.yaml`  | IngestSubvolume CRD defining Blob sync policy         |

These templates use `envsubst` variables rendered by `data-pipeline/setup/deploy-acsa.sh`. See the [ACSA setup guide](../../docs/data-pipeline/acsa-setup.md) for full deployment instructions.
