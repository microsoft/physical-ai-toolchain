---
name: environment-deployment
description: "Generate, transfer, and consume environment-specific Azure, AKS, OSMO, ACR, and Azure ML deployment bundles. Use when: discovering deployed environment details; creating OSMO image manifests or platform values; preparing a HiL host; uploading or downloading deployment files through Azure Key Vault; or deploying with generated environment configuration."
---

# Environment Deployment

Generate non-secret deployment details from Terraform desired state and read-only Azure, Kubernetes, and OSMO discovery. Store generated artifacts under the gitignored `infrastructure/setup/generated/<environment>/` directory.

## Safety Boundaries

Follow these rules for every environment bundle:

- Do not modify Azure resources, Kubernetes resources, OSMO configuration, or the active OSMO profile during discovery.
- Do not run `terraform apply`, `kubectl apply`, Helm upgrade/install, OSMO update/set/delete commands, or Azure create/update/delete commands while generating a bundle.
- Do not write discovered values into tracked files, documentation, examples, tests, or source defaults.
- Do not replace instructional RFC1918 addresses or example resource shapes solely because they differ from the deployed environment.
- Do not include Terraform state, secret values, kubeconfig contents, OSMO profiles, tokens, registry credentials, VPN keys, certificates, or absolute local paths in a bundle.
- Use an isolated kubeconfig and explicit context. Verify the AKS resource ID before reading cluster state.
- Ask before changing an Azure CLI subscription or OSMO profile when the requested task is discovery only.
- Use existing protected password or token files for non-interactive login. Never request secrets through chat.

## Bundle Layout

Create only the artifacts needed by the deployment:

```text
infrastructure/setup/generated/<environment>/
├── deployment.json
├── osmo-platforms.yaml
├── osmo-images.json
└── azureml-instance-types.yaml
```

`deployment.json` is required. The other files are optional when the corresponding component is not deployed.

Use lowercase letters, numbers, and hyphens for `<environment>`. Keep artifact filenames stable because the Key Vault transfer scripts use this allowlist.

## Prerequisites

Use every available discovery tool. Record unavailable tools and skipped checks in `deployment.json`.

| Tool | Purpose | Required |
|------|---------|----------|
| Terraform | Read desired resources and node-pool configuration | Yes |
| Azure CLI | Verify Azure identity and live resource metadata | Yes |
| jq | Select explicit non-secret fields and write JSON | Yes |
| kubectl | Verify AKS nodes, labels, taints, GPU capacity, and OSMO endpoint | When AKS is reachable |
| osmo | Verify the authenticated service and available pools | When an isolated profile is supplied |
| Helm | Read the deployed OSMO image version and values | When OSMO is deployed |

For private resources, connect to the VPN before live Azure, AKS, Key Vault, or OSMO checks.

## Generate a Bundle

This is an agent-led discovery workflow, not a static checked-in generator. The agent executes the available read-only commands below, renders files from the current Terraform and live resource data, validates the result, and reports every skipped probe. Do not reuse artifacts from another environment.

### 1. Establish the target

Collect or infer:

- Environment name
- Terraform directory, normally `infrastructure/terraform`
- Isolated kubeconfig path, normally `$HOME/.kube/physical-ai-toolchain/<aks-cluster>.yaml`
- Explicit Kubernetes context
- Optional isolated OSMO profile directory
- Optional OSMO image version when OSMO is not deployed yet

Create `infrastructure/setup/generated/<environment>/`. Do not create a tracked placeholder in this directory.

### 2. Read Terraform desired state

Run `terraform output -json` from the Terraform directory. Select only explicit output fields; never copy raw output or state into the bundle.

Read these values when present:

| Terraform output | Bundle use |
|------------------|------------|
| `resource_group` | Resource group name and location |
| `key_vault_name` | Key Vault bundle transfer |
| `aks_cluster` | AKS name and resource ID |
| `node_pools` | GPU pool VM sizes, priority, labels, and taints |
| `container_registry` | ACR name and login server |
| `storage_account` | Storage account name |
| `azureml_workspace` | Azure ML workspace name |

Fail when the resource group, Key Vault, or requested AKS/ACR values are missing. Do not infer names from naming conventions when Terraform exposes them.

### 3. Verify Azure

Use read-only Azure CLI calls:

1. Read the active account with `az account show`.
2. Confirm its subscription matches the subscription segment of the AKS resource ID.
3. Read the resource group with `az group show`.
4. Read AKS with `az aks show` and compare its normalized resource ID with Terraform.
5. Read ACR with `az acr show` and compare its name and login server with Terraform.
6. Read Key Vault metadata with `az keyvault show`; do not enumerate or read unrelated secret values.

Stop on any identity or resource mismatch. Do not switch subscriptions silently.

### 4. Verify Kubernetes

When kubectl is available and AKS is reachable:

1. Use `verify_existing_aks_kubeconfig` before refreshing credentials.
2. Use `connect_aks` from `scripts/lib/common.sh` only when the user requested credential setup.
3. Otherwise require an existing isolated kubeconfig and use `verify_kube_target`.
4. Read nodes with an explicit kubeconfig and context.
5. For each Terraform GPU pool, compare:
   - `agentpool` label
   - `node.kubernetes.io/instance-type` label
   - Terraform node labels
   - Terraform taints
   - `status.allocatable["nvidia.com/gpu"]`
6. Read `azureml/azureml-ingress-nginx-internal-lb` and form `http://<RFC1918-address>` from its assigned ingress IP.

Scale-to-zero pools may have no live nodes. Generate their configuration from Terraform and record live capacity as unavailable instead of omitting the pool.

### 5. Verify OSMO

Do not run `osmo login` during discovery. If the caller supplies an isolated authenticated profile, set `XDG_CONFIG_HOME` to it and run read-only checks:

- `osmo version`
- `osmo pool list --format-type json`

When Helm is available, read the deployed OSMO release with the same isolated kubeconfig and context. Prefer `global.osmoImageTag` from deployed values for the image version. Fall back to `OSMO_IMAGE_VERSION` from `infrastructure/setup/defaults.conf` only for a new deployment.

Record whether OSMO and Helm verification succeeded. Do not copy profile data into the bundle.

### 6. Generate OSMO platform values

Generate `osmo-platforms.yaml` from `node_pools`.

For each GPU pool:

- Create one pod template with `agentpool` and `node.kubernetes.io/instance-type` selectors.
- Add Terraform node labels that constrain scheduling.
- Convert each Terraform taint into a matching `Exists` or `Equal` toleration.
- Add the `nvidia.com/gpu` `NoSchedule` toleration when the pool uses it.
- Set requests and limits to the literal OSMO Jinja value `{{USER_GPU}}`.
- Create a platform under `services.configs.pools.default.platforms`.
- Use a default GPU count no greater than verified per-node allocatable capacity. Use `1` when the pool is scaled to zero and capacity cannot be verified.
- Keep `USER_CPU`, `USER_MEMORY`, `USER_STORAGE`, and `USER_SHM_SIZE` as instructional defaults unless the user supplies workload requirements.

Use unique lowercase identifiers derived from the pool key. Preserve both braces in every OSMO Jinja expression.

### 7. Generate Azure ML InstanceTypes

Generate `azureml-instance-types.yaml` from the same pool data:

- Always include `defaultinstancetype` for CPU workloads.
- Create one GPU InstanceType per pool with the pool selector and constraining Terraform labels.
- Use `gpuspot` for the first spot pool and `gpu` for the first regular pool when those names are unambiguous; otherwise use `gpu-<pool>`.
- Set the GPU limit to a value no greater than verified per-node capacity. Use `1` for scale-to-zero pools without live capacity.
- Do not generate multi-GPU InstanceTypes without verified capacity or an explicit user requirement.

The deployment consumes this file through `02-deploy-azureml-extension.sh --instance-types-manifest`.

### 8. Generate the immutable ACR image manifest

Generate `osmo-images.json` only when OSMO images are mirrored to ACR. Use this component allowlist:

- `agent`
- `backend-listener`
- `backend-worker`
- `client`
- `delayed-job-monitor`
- `init-container`
- `logger`
- `router`
- `service`
- `web-ui`
- `worker`

For each component, read the digest for `osmo/<component>:<image-version>` with `az acr manifest show-metadata`. Confirm the repository tag has writes and deletes disabled with `az acr repository show`. Fail if a component, digest, or immutability setting is missing.

Write this shape:

```json
{
  "schema_version": 1,
  "registry": "<acr-name>",
  "login_server": "<acr-login-server>",
  "image_version": "<version>",
  "images": {
    "<component>": {
      "repository": "osmo/<component>",
      "digest": "sha256:<64-lowercase-hex>"
    }
  }
}
```

### 9. Generate deployment metadata

Write `deployment.json` last. Use relative artifact filenames and SHA-256 digests. Include:

```json
{
  "schema_version": 1,
  "environment": "<environment>",
  "generated_at": "<UTC-RFC3339>",
  "subscription_id": "<subscription-id>",
  "tenant_id": "<tenant-id>",
  "resource_group": "<resource-group>",
  "location": "<azure-region>",
  "key_vault_name": "<key-vault>",
  "aks_cluster": "<aks-cluster>",
  "aks_resource_id": "<aks-resource-id>",
  "acr_name": "<acr-name-or-empty>",
  "acr_login_server": "<login-server-or-empty>",
  "azureml_workspace": "<workspace-or-empty>",
  "storage_account": "<storage-account-or-empty>",
  "osmo_service_url": "<private-osmo-url>",
  "osmo_chart_version": "<chart-version-or-empty>",
  "osmo_image_version": "<image-version-or-empty>",
  "artifacts": {
    "osmo_platforms": {"file": "osmo-platforms.yaml", "sha256": "<digest>"},
    "osmo_images": {"file": "osmo-images.json", "sha256": "<digest>"},
    "azureml_instance_types": {"file": "azureml-instance-types.yaml", "sha256": "<digest>"}
  },
  "verification": {
    "terraform": true,
    "azure_cli": true,
    "kubectl": true,
    "helm": true,
    "osmo": true
  }
}
```

Omit optional artifact entries when their files do not exist. Set unavailable verification tools to `false`; do not claim checks that did not run.

### 10. Validate

Before using or uploading the bundle:

- Confirm every artifact is a regular non-symlink file.
- Confirm `deployment.json` matches the active subscription, Terraform resource group, Key Vault, and AKS resource ID.
- Confirm artifact paths are relative filenames without `..`.
- Recalculate and compare every artifact SHA-256.
- Validate `osmo-images.json` against `verify_acr_image_manifest` when ACR is used.
- Run `kubectl apply --dry-run=client -f` for the Azure ML InstanceTypes when kubectl is available.
- Search the bundle for private keys, bearer tokens, password fields, Docker auth, kubeconfig client data, and absolute home-directory paths. Stop if any are found.
- Confirm `git check-ignore infrastructure/setup/generated/<environment>/deployment.json` succeeds.

## Use the Bundle

Pass generated artifacts explicitly; do not copy them back into tracked `values/` or `manifests/` directories.

| Deployment | Generated argument |
|------------|--------------------|
| Azure ML extension | `02-deploy-azureml-extension.sh --instance-types-manifest <bundle>/azureml-instance-types.yaml` |
| OSMO control plane | `03-deploy-osmo.sh --platform-values <bundle>/osmo-platforms.yaml --use-acr --image-manifest <bundle>/osmo-images.json` |
| External HiL backend | `04-deploy-osmo-external-backend.sh --image-manifest <bundle>/osmo-images.json` |

Read the image version, service URL, AKS resource ID, and resource names from `deployment.json`. Run each deployment script with `--config-preview` first. Deployment scripts may change Azure or Kubernetes resources; obtain user confirmation before continuing from discovery into deployment.

## Transfer Through Key Vault

Upload the allowlisted bundle from the trusted deployment host:

```bash
infrastructure/setup/upload-environment-bundle.sh --environment <environment> --config-preview
infrastructure/setup/upload-environment-bundle.sh --environment <environment>
```

The uploader requires Key Vault secret write permission. It never changes RBAC.

Each UTF-8 artifact must be no larger than 24,000 bytes. Omit an optional file and its `deployment.json` artifact entry together. Existing Key Vault versions may remain, but consumers follow the current `deployment.json` allowlist.

Do not run concurrent uploads for the same environment. The uploader publishes artifacts first and `deployment.json` last so downloaders either receive a coherent bundle or fail its digest checks.

On the HiL host, authenticate to Azure, connect to the private network or VPN, and download the bundle:

```bash
infrastructure/setup/download-environment-bundle.sh --environment <environment> --resource-group <resource-group> --config-preview
infrastructure/setup/download-environment-bundle.sh --environment <environment> --resource-group <resource-group>
```

The downloader requires the Key Vault Secrets User role and private endpoint connectivity when the vault is private.

Configure local clients from the protected bundle:

```bash
infrastructure/setup/connect-environment.sh --environment <environment> --config-preview
infrastructure/setup/connect-environment.sh --environment <environment>
```

Use `--osmo-method dev --osmo-username <user>` only for an explicitly approved development deployment. Use protected files with `--password-file` or `--token-file` for service authentication.
