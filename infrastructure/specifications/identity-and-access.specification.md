# Identity and Access

Managed identities, RBAC assignments, and workload identity federation for secure service-to-service authentication.

## Components

| Component                    | Purpose                                               |
|------------------------------|-------------------------------------------------------|
| AKS Managed Identity         | Cluster identity for Azure resource access            |
| AzureML Managed Identity     | Training job identity for storage and model registry  |
| OSMO Managed Identity        | Workflow orchestration identity                       |
| Workload Identity Federation | Kubernetes service account to Azure AD token exchange |
| Key Vault RBAC               | Secrets and certificate access control                |
| Storage RBAC                 | Blob data access for training data and checkpoints    |
| Entra ID Application         | VPN authentication and service principal              |

## Configuration

### Workload Identity Federation

Terraform creates managed identities and federated credentials linking Kubernetes service accounts to Azure AD:

| Identity           | Kubernetes Namespace | Service Accounts         |
|--------------------|----------------------|--------------------------|
| AzureML            | `azureml`            | `default`, `training`    |
| OSMO Control Plane | `osmo-control-plane` | OSMO service accounts    |
| OSMO Operator      | `osmo-operator`      | Backend operator account |
| OSMO Workflows     | `osmo-workflows`     | Job execution accounts   |

### RBAC Assignments

| Role                          | Scope              | Purpose                        |
|-------------------------------|--------------------|--------------------------------|
| Key Vault Secrets Officer     | Key Vault          | Current user secret management |
| Storage Blob Data Contributor | Storage Account    | Training data read/write       |
| AcrPull                       | Container Registry | Image pull from AKS            |
| Contributor                   | Resource Group     | AzureML workspace operations   |

### Key Vault Access

| Parameter                                 | Description                                      | Default |
|-------------------------------------------|--------------------------------------------------|---------|
| `should_add_current_user_key_vault_admin` | Grant current user Key Vault Secrets Officer     | `true`  |
| `should_add_current_user_storage_blob`    | Grant current user Storage Blob Data Contributor | `true`  |

### AzureML Identity Chain

Terraform-created managed identity → federated credentials → Kubernetes service accounts (`azureml:default`, `azureml:training`). Model data access uses `mode: download` to avoid authentication failures in the `data-capability` sidecar.

## Dependencies

- Azure Infrastructure: Key Vault, storage account, container registry
- Kubernetes Setup: AKS cluster provides the OIDC issuer for federation
- Network Topology: private endpoints require identity-based access (no connection strings)
