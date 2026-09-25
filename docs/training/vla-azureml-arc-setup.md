---
sidebar_position: 6
title: Azure ML Arc VLA Setup and Operations
description: Prepare an Ubuntu K3s GPU host, connect it through Azure Arc, attach it to Azure ML, and run PI 0.5 VLA training
author: Microsoft Robotics-AI Team
ms.date: 2026-09-25
ms.topic: how-to
keywords:
  - vla
  - pi05
  - lerobot
  - azureml
  - azure arc
  - k3s
  - gpu
---

Use this guide to prepare an Ubuntu GPU host as Azure ML compute through K3s and Azure Arc, submit PI 0.5 VLA training, and monitor the run from Azure ML and the host. Keep environment-specific identifiers, generated InstanceTypes, endpoints, and credentials outside tracked source.

## Architecture and Automation Boundary

The execution path has two independent network legs:

1. The operator workstation uploads the code asset and submits the job to Azure ML. A private workspace requires workstation access to the workspace storage private endpoint, provided by the environment's point-to-site VPN.
2. The K3s host maintains outbound Azure Arc and Azure ML connectivity, pulls images and models, mounts data, emits MLflow metrics, and uploads outputs. The accepted job does not depend on the workstation remaining connected.

Azure Arc-enabled Server and Azure Arc-enabled Kubernetes are separate resources:

| Resource                      | Purpose                                               | Required for Azure ML compute |
|-------------------------------|-------------------------------------------------------|-------------------------------|
| Arc-enabled Server            | Host inventory, policy, and server management         | No                            |
| Arc-enabled Kubernetes        | Kubernetes control-plane reach and extension delivery | Yes                           |
| Azure ML Kubernetes extension | Azure ML training services inside K3s                 | Yes                           |
| Azure ML Kubernetes compute   | Workspace-visible job placement target                | Yes                           |

> [!IMPORTANT]
> `infrastructure/setup/02-deploy-azureml-extension.sh` supports `Microsoft.ContainerService/managedClusters` only. Do not run it against Arc K3s or change its cluster type at runtime. Use `data-pipeline/setup/edge/06-deploy-azureml-extension.sh` for Arc K3s.

## Prerequisites

| Requirement                         | Purpose                                                                 |
|-------------------------------------|-------------------------------------------------------------------------|
| Ubuntu 22.04 or 24.04 on x86_64     | Supported host for the Azure ML Kubernetes extension                    |
| NVIDIA driver and container runtime | Expose the GPU to Kubernetes workloads                                  |
| Repository checkout                 | Provide pinned K3s, Arc, training, and validation scripts               |
| Azure CLI authentication            | Create Arc, extension, identity, and Azure ML resources                 |
| Azure resource permissions          | Register providers, connect Arc, install extensions, and attach compute |
| Outbound TCP 443 and DNS            | Reach Azure Arc, Azure ML, storage, registry, and model services        |
| Authorized Hugging Face token       | Load the gated PaliGemma tokenizer used by PI 0.5                       |

Register the Azure providers required by Arc-enabled Kubernetes:

```bash
az provider register --namespace Microsoft.Kubernetes
az provider register --namespace Microsoft.KubernetesConfiguration
az provider register --namespace Microsoft.ExtendedLocation
```

Wait until each provider reports `Registered` before connecting the cluster.

## Prepare the Ubuntu K3s Host

Preview and run the checked-in host preparation:

```bash
data-pipeline/setup/hil/00-prepare-ubuntu.sh --config-preview
data-pipeline/setup/hil/00-prepare-ubuntu.sh
```

Preview and install the repository-owned K3s version:

```bash
data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name "<host-name>" \
  --config-preview

data-pipeline/setup/hil/01-install-k3s.sh \
  --node-name "<host-name>"
```

Record the protected kubeconfig path and context printed by the deployment summary. The default context is `physical-ai-edge`.

Validate the local cluster:

```bash
KUBECONFIG="<protected-kubeconfig>"
KUBE_CONTEXT="physical-ai-edge"

kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" get --raw=/readyz
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" get nodes -o wide
```

### Validate GPU readiness

The K3s installer does not install the NVIDIA host driver, container toolkit, or Kubernetes device plugin. Configure those components through the approved host GPU procedure before installing the Azure ML extension.

Validate the host driver:

```bash
nvidia-smi
```

Validate Kubernetes GPU discovery:

```bash
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" \
  get nodes \
  -o custom-columns='NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu'
```

Do not continue until the expected `nvidia.com/gpu` capacity appears. For RTX PRO 6000 vGPU behavior, driver constraints, and MIG requirements, see [GPU Configuration](../reference/gpu-configuration.md).

## Establish Network Connectivity

The K3s host does not require inbound public access for Azure ML job control. Azure Arc agents initiate outbound connections. Permit required outbound DNS and TLS traffic through the host firewall, proxy, and upstream network controls.

Validate these dependency categories:

| Dependency             | Required operation                                 |
|------------------------|----------------------------------------------------|
| Azure Resource Manager | Arc and extension resource management              |
| Azure Arc services     | Agent heartbeat, configuration, and extension sync |
| Azure ML               | Job control, data-capability, and MLflow           |
| Workspace storage      | Dataset reads, code and checkpoint transfers       |
| Container registry     | Digest-pinned training image pull                  |
| Hugging Face           | Revision-pinned policy and gated tokenizer access  |
| DNS and NTP            | Endpoint resolution and certificate validation     |

Use the current Microsoft network-requirements documentation for the complete endpoint list. Do not copy a temporary allowlist from one environment into tracked defaults.

Confirm the Azure CLI can reach the selected subscription:

```bash
az account show --query '{subscription:id,tenant:tenantId}' --output yaml
```

Confirm host time and DNS are healthy before Arc onboarding:

```bash
timedatectl status
getent hosts management.azure.com
```

## Connect K3s to Azure Arc

Preview the checked-in Arc-enabled Kubernetes onboarding:

```bash
data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id "<subscription-id>" \
  --tenant-id "<tenant-id>" \
  --resource-group "<arc-resource-group>" \
  --location "<azure-region>" \
  --cluster-name "<arc-cluster-name>" \
  --kubeconfig "$KUBECONFIG" \
  --context "$KUBE_CONTEXT" \
  --enable-workload-identity \
  --config-preview
```

Remove `--config-preview` after reviewing the target. The script creates or updates the Arc-enabled Kubernetes resource without duplicating it and configures the Arc OIDC issuer when workload identity is enabled.

Validate Arc connectivity:

```bash
az connectedk8s show \
  --name "<arc-cluster-name>" \
  --resource-group "<arc-resource-group>" \
  --subscription "<subscription-id>" \
  --query '{state:connectivityStatus,distribution:distribution,version:kubernetesVersion}' \
  --output yaml

kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" \
  get pods --namespace azure-arc
```

The connectivity state must be `Connected`, and the Arc agents must be healthy before installing Azure ML.

### Optional Arc-enabled Server onboarding

Connect the Ubuntu host as an Arc-enabled Server only when host management requires it:

```bash
data-pipeline/setup/edge/03-connect-arc-server.sh \
  --subscription-id "<subscription-id>" \
  --tenant-id "<tenant-id>" \
  --resource-group "<arc-resource-group>" \
  --location "<azure-region>" \
  --server-name "<arc-server-name>" \
  --config-preview
```

Arc-enabled Server does not replace Arc-enabled Kubernetes and is not sufficient for Azure ML compute attachment.

## Install the Azure ML Kubernetes Extension

Preview the Arc extension, InstanceType, identity-role, and compute attachment configuration:

```bash
data-pipeline/setup/edge/06-deploy-azureml-extension.sh \
  --subscription-id "<arc-subscription-id>" \
  --cluster-resource-group "<arc-resource-group>" \
  --cluster-name "<arc-cluster-name>" \
  --workspace-subscription-id "<workspace-subscription-id>" \
  --workspace-resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --extension-name "<azureml-extension-name>" \
  --compute-name "<compute-name>" \
  --identity-resource-id "<compute-identity-resource-id>" \
  --kubeconfig "$KUBECONFIG" \
  --context "$KUBE_CONTEXT" \
  --bundle-dir "infrastructure/setup/generated/<environment>" \
  --config-preview
```

Remove `--config-preview` after reviewing the target. The script creates or updates the training-only extension, applies the reviewed InstanceTypes, attaches the Kubernetes compute, reconciles required workspace and storage roles, and writes a sanitized deployment receipt under the ignored bundle directory.

Use the following raw commands only to diagnose or recover an incomplete automated deployment.

Create the Azure ML workload namespace before attachment:

```bash
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" \
  create namespace azureml \
  --dry-run=client \
  --output yaml |
  kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" apply -f -
```

Install the training-only extension against the Arc connected-cluster resource:

```bash
az k8s-extension create \
  --name "<azureml-extension-name>" \
  --extension-type Microsoft.AzureML.Kubernetes \
  --cluster-type connectedClusters \
  --cluster-name "<arc-cluster-name>" \
  --resource-group "<arc-resource-group>" \
  --scope cluster \
  --release-namespace azureml \
  --release-train stable \
  --config enableTraining=True enableInference=False
```

Training-only compute does not require an inference router. Keep `enableInference=False` unless a separate inference design is approved.

Validate the Azure resource and in-cluster workloads:

```bash
az k8s-extension show \
  --name "<azureml-extension-name>" \
  --cluster-type connectedClusters \
  --cluster-name "<arc-cluster-name>" \
  --resource-group "<arc-resource-group>" \
  --query '{state:provisioningState,version:version}' \
  --output yaml

kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" \
  get pods --namespace azureml
```

Wait for extension provisioning to succeed and required pods to become ready.

## Configure Azure ML InstanceTypes

InstanceTypes must not request more CPU, memory, ephemeral storage, or GPUs than the live host can provide. Do not apply AKS-only selectors such as `kubernetes.azure.com/scalesetpriority` to K3s.

Inspect live capacity and existing InstanceTypes:

```bash
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" describe node
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" get instancetypes
```

Generate target-specific InstanceTypes under:

```text
infrastructure/setup/generated/<environment>/
```

Keep generated manifests out of Git. Apply the reviewed manifest:

```bash
kubectl --kubeconfig "$KUBECONFIG" --context "$KUBE_CONTEXT" \
  apply -f "infrastructure/setup/generated/<environment>/azureml-instance-types.yaml"
```

Confirm that the `gpu` InstanceType requests exactly one GPU and fits the host's allocatable capacity.

## Attach the Arc Cluster to Azure ML

The Arc setup script performs this attachment during the primary workflow. Use the following commands only to diagnose or recover the attachment manually.

Use an immutable Azure ML compute name. Do not repoint an existing compute name to another Kubernetes cluster.

Resolve the Arc cluster and user-assigned identity resource IDs:

```bash
ARC_CLUSTER_ID=$(az connectedk8s show \
  --name "<arc-cluster-name>" \
  --resource-group "<arc-resource-group>" \
  --query id \
  --output tsv)

COMPUTE_IDENTITY_ID=$(az identity show \
  --name "<compute-identity-name>" \
  --resource-group "<identity-resource-group>" \
  --query id \
  --output tsv)
```

Attach the compute:

```bash
az ml compute attach \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --type Kubernetes \
  --name "<compute-name>" \
  --resource-id "$ARC_CLUSTER_ID" \
  --namespace azureml \
  --identity-type UserAssigned \
  --user-assigned-identities "$COMPUTE_IDENTITY_ID"
```

Grant the compute identity only the permissions required to pull the image, read inputs, write outputs, and emit Azure ML and MLflow data. Do not grant model-registry mutation to the training identity.

Validate attachment:

```bash
az ml compute show \
  --name "<compute-name>" \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --query '{state:provisioning_state,type:type,resource:resource_id}' \
  --output yaml
```

Do not submit VLA training until the provisioning state is `Succeeded`.

## Submit PI 0.5 Training

Connect the operator workstation to the environment's point-to-site VPN when workspace storage uses private endpoints. Store `HF_TOKEN` in an ignored local environment file or retrieve it from an approved secret store; never pass a real token in tracked YAML.

Preview the validated conservative configuration:

```bash
CODE_REVISION=$(git rev-parse HEAD)
RENAME_MAP_B64=$(printf '%s' \
  '{"observation.images.d435":"observation.images.base_0_rgb","observation.images.d405":"observation.images.left_wrist_0_rgb"}' |
  base64 --wrap=0)

az ml job create \
  --file training/vla/workflows/azureml/vla-training-pipeline.yaml \
  --resource-group "<workspace-resource-group>" \
  --workspace-name "<workspace-name>" \
  --set inputs.dataset_repo_id="<hugging-face-dataset>" \
  --set inputs.dataset_revision="<dataset-commit-sha>" \
  --set inputs.policy_type=pi05 \
  --set inputs.init_from_policy_hf_repo_id=lerobot/pi05_base \
  --set inputs.init_from_policy_hf_revision=b211f3d44c36b6acfcf7ae94a64e8e96f75a64ba \
  --set inputs.adapter_name=lerobot-pi \
  --set inputs.code_repository=https://github.com/microsoft/physical-ai-toolchain.git \
  --set inputs.code_revision="$CODE_REVISION" \
  --set inputs.train_expert_only=true \
  --set inputs.gradient_checkpointing=true \
  --set inputs.rename_map_b64="$RENAME_MAP_B64" \
  --set inputs.candidate_batch_sizes=1,2,4 \
  --set inputs.training_steps=40000 \
  --set inputs.save_freq=1000 \
  --set inputs.compute_calibrate="azureml:<compute-name>" \
  --set inputs.compute_train="azureml:<compute-name>" \
  --set inputs.subscription_id="<workspace-subscription-id>" \
  --set inputs.resource_group="<workspace-resource-group>" \
  --set inputs.workspace_name="<workspace-name>" \
  --set inputs.hf_key_vault_url="<key-vault-url>" \
  --set inputs.hf_token_secret_name="<secret-name>"
```

Review the pinned dataset and model revisions, code revision, compute target, candidate batch sizes, training duration, camera mapping, and Key Vault reference before submission. The calibration job measures the candidate micro-batches and passes its generated workload contract and report directly to training.

Azure CLI prints the accepted pipeline name and portal URL. If the command is interrupted before it prints the job name, check the Azure ML jobs page before resubmitting.

## Monitor Azure ML

Set local variables from the submission summary:

```bash
AZUREML_JOB_NAME="<job-name>"
AZURE_RESOURCE_GROUP="<workspace-resource-group>"
AZUREML_WORKSPACE_NAME="<workspace-name>"
```

Query control-plane status:

```bash
az ml job show \
  --name "$AZUREML_JOB_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$AZUREML_WORKSPACE_NAME" \
  --query '{name:name,status:status,compute:compute,created:creation_context.created_at}' \
  --output yaml
```

Stream logs:

```bash
az ml job stream \
  --name "$AZUREML_JOB_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --workspace-name "$AZUREML_WORKSPACE_NAME"
```

Pressing `Ctrl+C` stops the local stream but does not cancel an accepted job. Use the portal URL to inspect job properties, outputs, and MLflow metrics.

Monitor these MLflow series:

| Metric                | Interpretation                                      |
|-----------------------|-----------------------------------------------------|
| `train/loss`          | Optimization progress and exact update-record count |
| `train/grad_norm`     | Gradient stability; investigate non-finite values   |
| `train/learning_rate` | Scheduler behavior                                  |
| `train/update_time_s` | Optimizer update duration                           |
| `train/data_time_s`   | Input-pipeline delay                                |
| `train/samples`       | Cumulative processed samples                        |
| `train/episodes`      | Cumulative processed episodes                       |

For a run created before the exact-step fix, count `train/loss` history records when `--log-freq 1`; one record corresponds to one completed optimizer update.

## Monitor the K3s Host

List the newest Azure ML pods:

```bash
sudo k3s kubectl get pods \
  --namespace azureml \
  --output wide \
  --sort-by=.metadata.creationTimestamp
```

Inspect the selected pod and available log containers:

```bash
POD="<training-pod>"

sudo k3s kubectl get pod \
  --namespace azureml \
  "$POD" \
  --output jsonpath='{.spec.containers[*].name}{"\n"}'

sudo k3s kubectl describe pod --namespace azureml "$POD"
```

Stream the confirmed user-training container:

```bash
CONTAINER="<user-training-container>"

sudo k3s kubectl logs \
  --namespace azureml \
  "$POD" \
  --container "$CONTAINER" \
  --follow \
  --timestamps
```

Monitor the GPU interactively:

```bash
nvtop
```

The blue `GPU0 %` line in `nvtop` is utilization, not clock speed. Use NVIDIA device monitoring for processor and memory clocks:

```bash
nvidia-smi dmon -s pucm -d 2
```

Interpret Azure ML, MLflow, pod, and host signals together:

| Observation                       | Interpretation or action                                                     |
|-----------------------------------|------------------------------------------------------------------------------|
| High GPU utilization              | Training kernels are actively executing                                      |
| Brief utilization drops           | Check for normal loading, synchronization, logging, or checkpoint boundaries |
| Sustained zero utilization        | Inspect pod logs, CPU, storage, DNS, and network dependencies                |
| Stable memory with variable usage | Model state remains resident while compute alternates between phases         |
| Rising memory each step           | Investigate retention or leaks across checkpoint and evaluation boundaries   |
| Clock decline at high temperature | Inspect NVIDIA power, thermal, and throttling reasons                        |

Do not cancel or resubmit solely because utilization dips. Confirm that logs and MLflow metrics have stopped advancing beyond the normal model-download, checkpoint, or logging interval.

## Expected Outcome

The environment is ready when:

- Arc-enabled Kubernetes reports `Connected`.
- Azure ML extension provisioning reports `Succeeded`.
- Required `azureml` pods are ready.
- The node exposes the expected `nvidia.com/gpu` capacity.
- The `gpu` InstanceType fits live allocatable capacity.
- Azure ML compute provisioning reports `Succeeded`.
- A PI 0.5 job reaches checkpoint loading, dataset construction, and optimizer updates.
- MLflow metrics and checkpoint outputs advance.

For observed failures, diagnostic signatures, and recovery actions, see [VLA Full-Run Troubleshooting](vla-full-run-troubleshooting.md).

## References

- [Ubuntu HiL Host and K3s Setup](../data-pipeline/edge-k3s-setup.md)
- [Azure ML Training Workflows](azureml-training.md)
- [Attach a Kubernetes cluster to Azure ML](https://learn.microsoft.com/azure/machine-learning/how-to-attach-kubernetes-to-workspace)
- [Deploy the Azure ML Kubernetes extension](https://learn.microsoft.com/azure/machine-learning/how-to-deploy-kubernetes-extension)
- [Connect an existing Kubernetes cluster to Azure Arc](https://learn.microsoft.com/azure/azure-arc/kubernetes/quickstart-connect-cluster)
