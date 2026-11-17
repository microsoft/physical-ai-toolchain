---
title: Optional Azure Access for the Local OSMO Profile
description: Let local OSMO workflows and the local dataviewer use Azure ML tracking, Blob Storage, and ACR images through Arc workload identity
author: Microsoft Robotics-AI Team
ms.date: 2026-10-19
ms.topic: how-to
keywords:
  - local-profile
  - workload identity
  - azure arc
  - federated credentials
---

<!-- cspell:ignore kubelet -->

The local OSMO profile runs without Azure. When a job needs Azure ML tracking, Blob Storage datasets, or images from Azure Container Registry, or the dataviewer needs Blob datasets, Azure must first trust this cluster. This guide covers what that takes and how [enable-azure-access.sh](enable-azure-access.sh) previews, applies, and removes it.

> [!WARNING]
> This path is prepared but not validated against Azure. Review the plan the script prints before you apply it.

## What Azure Access Needs

Pods authenticate through Arc workload identity: the cluster's Arc OIDC issuer signs ServiceAccount tokens, and Microsoft Entra ID exchanges them for a managed identity's tokens when a federated credential trusts that issuer and ServiceAccount.

| Access                                              | Azure side                                                                                    | Cluster side                                                                                                            |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Azure ML tracking and Blob data from OSMO workflows | Federated credential for `system:serviceaccount:osmo-workflows:osmo-workflow` on an identity  | `02-deploy-osmo.sh --azure-client-id <client-id>` annotates the ServiceAccount and opts workflow pods in to the webhook |
| Blob datasets in the dataviewer                     | Federated credential for `system:serviceaccount:dataviewer:dataviewer-backend` on an identity | `04-deploy-dataviewer.sh --azure-storage-account <account> --azure-client-id <client-id>`                               |
| ACR images in workflows                             | Pull-only ACR token                                                                           | OSMO REGISTRY credential that holds the token                                                                           |

Kubelet image pulls cannot use workload identity, which is why ACR images need a registry credential. The identities need their own roles; the script reports them but never changes them:

| Identity                                    | Roles the access needs                                       | In the cloud deployment                                      |
|---------------------------------------------|--------------------------------------------------------------|--------------------------------------------------------------|
| Workflows (`osmo_workload_identity` output) | AzureML Data Scientist, Storage Blob Data Contributor        | Also holds AcrPull and Key Vault Secrets User; lacks AcrPush |
| Dataviewer (`dataviewer.identity` output)   | Storage Blob Data Contributor on the dataset storage account | Holds Storage Blob Data Contributor and AcrPull              |

A job that pushes images to ACR also needs AcrPush, which the OSMO identity lacks. Federating the OSMO identity gives every workflow on this host its roles, including Key Vault Secrets User, and the local OSMO has no authentication, so anyone who can reach the host can submit such a workflow. A dedicated identity with only the roles your jobs need narrows that.

The dataviewer has no authentication either, and every pod in the cluster can reach its Service; the local profile defines no NetworkPolicy that limits it. Once the dataviewer uses an identity, any workflow can read and write the Blob datasets and annotations through its API, including a workflow that has no Azure access of its own.

## Preview the Changes

The script reads Azure and prints a plan by default. It needs the Arc-enabled Kubernetes resource for this cluster and the identities' resource IDs, all in one subscription:

```bash
az connectedk8s show --resource-group <arc-resource-group> --name <arc-cluster> --query id -o tsv

infrastructure/local/enable-azure-access.sh \
  --tenant-id <tenant-id> --subscription <subscription-id> \
  --arc-cluster-resource-id <arc-cluster-resource-id> \
  --workflow-identity-id <workflow-identity-resource-id> \
  --dataviewer-identity-id <dataviewer-identity-resource-id> --storage-account <storage-account> \
  --acr-name <registry>
```

The dataviewer and ACR options are optional. The plan stops when the active `az` account is another tenant or subscription, or when the Arc cluster does not have workload identity enabled. It shows each federated credential as present or missing and the ACR token as present, missing, or not pull-only or disabled, lists each identity's roles, and warns when a needed role is absent. `--config-preview` prints the derived names without calling Azure.

An existing ACR token counts as present only when it holds the registry's built-in `_repositories_pull` scope map and is enabled. The plan flags any other token for inspection, and it stops when it cannot read the token's scope map or status, except with `--remove`, which deletes the token either way.

## Apply and Switch the Cluster

Add `--apply` to create the missing federated credentials and the ACR token. The script stores the token in the local OSMO as the REGISTRY credential `acr-<registry>`, passing its password through file descriptors so it never appears in arguments or output. It stops without changes when a credential of the same name trusts a different issuer, subject, or audience, or when the ACR token is not pull-only or is disabled.

When the ACR token is not pull-only or is disabled, a password already stored in the local OSMO stays as it was and may still work. Inspect the token, or use `--remove` to delete both the token and the stored credential. Rerunning `--apply` on a pull-only token issues a new token password.

The cluster uses the identities only after you redeploy with the commands the script prints:

```bash
infrastructure/local/02-deploy-osmo.sh --azure-client-id <workflow-client-id>
infrastructure/local/04-deploy-dataviewer.sh --azure-storage-account <storage-account> \
  --azure-client-id <dataviewer-client-id>
```

`LOCAL_OSMO_AZURE_CLIENT_ID` keeps the OSMO option across later redeploys. The dataviewer then lists the account's `datasets` container beside the local datasets and saves annotations to `annotations`; change them with `--azure-dataset-container` and `--azure-annotation-container`. It still runs without authentication.

To track a local job in Azure ML, submit it with `TRACKING_BACKEND=azureml` and the workspace in `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZUREML_WORKSPACE_NAME`:

```bash
TRACKING_BACKEND=azureml AZURE_SUBSCRIPTION_ID=<subscription-id> \
  AZURE_RESOURCE_GROUP=<resource-group> AZUREML_WORKSPACE_NAME=<workspace> \
  training/il/scripts/submit-osmo-lerobot-training.sh --profile local \
  --local-dataset so101-learner -r so101-act
```

Workflows reference ACR images by digest, such as `<registry>.azurecr.io/<repository>@sha256:<digest>`, and OSMO pulls them with the stored credential.

## Remove Azure Access

`--remove` deletes the federated credentials, the ACR token, and the OSMO credential. Then redeploy without the Azure options, with `LOCAL_OSMO_AZURE_CLIENT_ID` unset, so workflow pods and the dataviewer stop using the identities:

```bash
infrastructure/local/02-deploy-osmo.sh
infrastructure/local/04-deploy-dataviewer.sh
```

The local profile's teardown does not touch Azure, so remove Azure access before you tear the profile down for good.
