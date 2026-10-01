---
name: azureml-k3s-compute-target-setup
description: "Set up a K3s cluster on an NVIDIA GPU host, connect it to Azure Arc, and configure Azure ML to use it as a Kubernetes compute target. Includes GPU smoke-test and validation instructions for Azure ML jobs on the Arc-connected cluster - Brought to you by microsoft/physical-ai-toolchain"
---

# Azure ML K3s Compute Target Setup

Set up K3s on an Ubuntu host with an NVIDIA GPU, connect the host and cluster to Azure Arc, and attach the cluster to Azure ML as a Kubernetes compute target. Then prove real CUDA execution with a bounded smoke job before submitting a long-running training job. A GPU reservation in the Azure ML job spec is not proof the container can see the device; validate at the container level every time the runtime stack changes.

## Prerequisites

| Requirement                                                                                                  | Purpose                                                                                             |
|--------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Ubuntu host with an NVIDIA GPU and driver installed                                                          | K3s workload target                                                                                 |
| Existing Arc resource group, subscription ID, tenant ID                                                      | `azcmagent` and `connectedk8s` registration targets                                                 |
| Azure ML workspace deployed by this repository's Terraform, with its outputs on the workstation              | `05-attach-hil-azureml-compute.sh` reads the workspace from those outputs                           |
| Contributor on the Arc cluster's resource group, and rights to assign roles on the workspace and its storage | The attach creates an Azure Relay and grants the compute identity access                            |
| `az` CLI with `connectedk8s`, `k8s-extension`, `ml`, and `ssh` extensions                                    | Arc and Azure ML operations, and remote access to the host                                          |
| NVIDIA Container Toolkit installed on the host                                                               | Provides the `nvidia-container-runtime` binary K3s detects                                          |
| HuggingFace account with access to any gated base model                                                      | Required only when warm-starting from a gated repository (for example `google/paligemma-3b-pt-224`) |

## Choose Where Each Command Runs

Host setup and the `kubectl` checks in this skill run on the GPU host, because the K3s kubeconfig points at the host-local API server. The attach script reaches the cluster through Arc cluster connect, so it runs from a workstation, as do job submission commands.

| Step                                                                                           | Runs on                                              |
|------------------------------------------------------------------------------------------------|------------------------------------------------------|
| Connect the host to Arc, install K3s, enable the GPU, connect the cluster to Arc               | GPU host, from a clone of this repository            |
| `kubectl` checks against the host's K3s                                                        | GPU host                                             |
| `05-attach-hil-azureml-compute.sh`: extension, InstanceTypes, compute attach, role assignments | Workstation with this repository's Terraform outputs |
| Job submission, `az ml job show`, and `az ml job stream`                                       | Any workstation                                      |

Before running a host step, confirm you are on the target host. Run these checks and compare the hostname with the intended host:

```bash
hostname
nvidia-smi -L
systemctl is-active k3s
```

If `nvidia-smi` is missing or the hostname does not match, you are not on the GPU host. Connect to it before continuing:

* Before the host is Arc-connected, use regular SSH or console access to reach it, then clone this repository on the host.
* After [Connect the Host to Arc](#connect-the-host-to-arc) completes, use `az ssh arc`. It needs no public IP address or inbound port, and requires Owner or Contributor on the Arc-enabled server and the `Microsoft.HybridConnectivity` resource provider.

Enable SSH on the Arc-enabled server once, from a workstation:

```bash
az extension add --name ssh
az provider register -n Microsoft.HybridConnectivity

machine_id=/subscriptions/<subscription-id>/resourceGroups/<arc-resource-group>/providers/Microsoft.HybridCompute/machines/<arc-server-name>
az rest --method put \
  --uri "https://management.azure.com${machine_id}/providers/Microsoft.HybridConnectivity/endpoints/default?api-version=2023-03-15" \
  --body '{"properties": {"type": "default"}}'
az rest --method put \
  --uri "https://management.azure.com${machine_id}/providers/Microsoft.HybridConnectivity/endpoints/default/serviceconfigurations/SSH?api-version=2023-03-15" \
  --body '{"properties": {"serviceName": "SSH", "port": 22}}'
```

Open a session on the host, then run the host steps inside it:

```bash
az ssh arc --resource-group <arc-resource-group> --name <arc-server-name> --local-user <host-user>
```

Inside the session, point `kubectl` at the protected kubeconfig written by the K3s installer:

```bash
export KUBECONFIG="$HOME/.local/share/physical-ai-toolchain/hil/kubeconfig.yaml"
```

## Connect the Host to Arc

Run on the GPU host. Preview, then connect the Ubuntu server:

```bash
data-pipeline/setup/edge/03-connect-arc-server.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --server-name <arc-server-name> --config-preview

data-pipeline/setup/edge/03-connect-arc-server.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --server-name <arc-server-name>
```

## Install K3s and Configure the GPU Default Runtime

Run on the GPU host. Install the pinned, owned K3s compute plane, passing `--default-runtime nvidia` on any host where GPU-backed Azure ML jobs will run:

```bash
data-pipeline/setup/hil/01-install-k3s.sh --default-runtime nvidia --config-preview
data-pipeline/setup/hil/01-install-k3s.sh --default-runtime nvidia
```

> [!IMPORTANT]
> Azure ML InstanceType `limits.nvidia.com/gpu` only reserves the device through the Kubernetes device plugin. It does not set a pod `runtimeClassName`, so on a K3s host whose default runtime is not NVIDIA, the container starts with no `/dev/nvidia*` devices and `torch.cuda.device_count()` returns `0` even though the job reports a reserved GPU. Setting `default-runtime: nvidia` in the K3s config is required whenever the Azure ML Kubernetes extension does not expose per-pod runtime-class configuration.

Then run [05-enable-k3s-gpu.sh](../../../data-pipeline/setup/hil/05-enable-k3s-gpu.sh) on the host. Its preview checks the driver, the NVIDIA Container Toolkit, the K3s service and API, and the `nvidia` RuntimeClass, and says whether the run will restart K3s:

```bash
data-pipeline/setup/hil/05-enable-k3s-gpu.sh --config-preview
data-pipeline/setup/hil/05-enable-k3s-gpu.sh
```

The script installs the digest-pinned NVIDIA device plugin so the node advertises `nvidia.com/gpu`. When nvidia is already the default runtime, as after `--default-runtime nvidia`, it leaves K3s running. On hosts installed without that option, it writes the K3s `default-runtime: nvidia` drop-in and restarts K3s once, so run it when no job containers are running.

Verify the NVIDIA device plugin is healthy and the node advertises allocatable GPUs before attaching Azure ML:

```bash
kubectl get pods -n kube-system -l name=nvidia-device-plugin-ds
kubectl get node <node-name> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'
```

## Connect the K3s Cluster to Arc

Run on the GPU host. Connect the cluster with the OIDC issuer and workload identity enabled, and grant yourself `cluster-admin` so Arc cluster connect accepts your identity:

```bash
data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --cluster-name <arc-cluster-name> --kubeconfig <kubeconfig-path> \
  --enable-workload-identity --cluster-admin-signed-in-user --config-preview

data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --cluster-name <arc-cluster-name> --kubeconfig <kubeconfig-path> \
  --enable-workload-identity --cluster-admin-signed-in-user
```

The attach script applies InstanceTypes through Arc cluster connect, which checks K3s RBAC for the identity that runs it. When someone else runs the attach, grant them with `--cluster-admin-object-id <object-id>` instead.

## Attach the Cluster to Azure ML

`infrastructure/setup/02-deploy-azureml-extension.sh` targets the Terraform-managed AKS cluster. For an Arc-connected K3s cluster, run [05-attach-hil-azureml-compute.sh](../../../infrastructure/setup/05-attach-hil-azureml-compute.sh) from a workstation that has this repository's Terraform outputs. Preview, then attach:

```bash
infrastructure/setup/05-attach-hil-azureml-compute.sh \
  --arc-cluster-resource-id /subscriptions/<subscription-id>/resourceGroups/<arc-resource-group>/providers/Microsoft.Kubernetes/connectedClusters/<arc-cluster-name> \
  --compute-name <compute-name> --config-preview

infrastructure/setup/05-attach-hil-azureml-compute.sh \
  --arc-cluster-resource-id /subscriptions/<subscription-id>/resourceGroups/<arc-resource-group>/providers/Microsoft.Kubernetes/connectedClusters/<arc-cluster-name> \
  --compute-name <compute-name> --require-gpu
```

The script:

1. Installs a training-only Azure ML extension from [azureml-arc-config.template.json](../../../infrastructure/setup/config/azureml-arc-config.template.json), with the extension's own device plugin and DCGM exporter off, or updates only the settings that differ on an existing extension.
2. Waits for the `InstanceType` CRD and applies [azureml-instance-types-hil.yaml](../../../infrastructure/setup/manifests/azureml-instance-types-hil.yaml) through Arc cluster connect: `defaultinstancetype` for CPU jobs and `gpu` for one GPU. The `gpu` type has no node selector, so the node needs no label.
3. Attaches the cluster as a Kubernetes compute with a system-assigned identity in the `azureml` namespace.
4. Grants that identity AzureML Data Scientist on the workspace and Storage Blob Data Contributor on its storage account.

Compute names are 16 characters at most, and the default, `k8s-<cluster>`, is truncated, so pass `--compute-name`. `--require-gpu` stops before any change unless a node reports allocatable `nvidia.com/gpu`. For InstanceTypes that request more GPUs, pass your own manifest with `--instance-types-manifest`, and request only what the node advertises.

The extension creates an Azure Relay namespace and hybrid connection in the Arc cluster's resource group. Don't modify them, because the compute depends on them.

## Azure ML Credentials and Dataset Inputs

| Requirement                                                                                                                   | Where it applies                                                                                   |
|-------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| `az login` session with rights to the target subscription and workspace                                                       | All `az ml` submission commands                                                                    |
| `AzureML Data Scientist` on the workspace and `Storage Blob Data Contributor` on its storage account for the compute identity | Granted by `05-attach-hil-azureml-compute.sh`; required for data asset mounts, outputs, and MLflow |
| `HF_TOKEN` with access to the gated base repository                                                                           | Only when `--policy-repo-id` or the HuggingFace dataset path resolves to a gated repository        |
| Datastore-backed Azure ML data asset, referenced with an explicit numeric version (`azureml:NAME:VERSION`)                    | `--dataset-asset`; shorthand references without a version are rejected to keep runs reproducible   |

Store `HF_TOKEN` in the untracked repository-root `.env.local`, never as a CLI argument or in chat. The submission script loads `.env.local` and forwards `HF_TOKEN` to the job, so `--hf-token` is not needed. Tokens passed as CLI arguments are visible to any process inspecting the host, so rotate a token immediately if it was ever exposed that way. `.amlignore` already excludes `.env` and `.env.*` from the Azure ML code snapshot.

Data asset mount failures during job start often mean the registered asset version does not resolve against its backing datastore. Register a new datastore-backed version and reference that explicit version rather than reusing a broken one:

```bash
az ml data create --name <dataset-name> --version <next-version> \
  --type uri_folder --path azureml://datastores/<datastore>/paths/<path>
```

## Submit a Bounded GPU Smoke Test

To check the GPU and the services training depends on before any model runs, submit `training/smoke/scripts/submit-azureml-gpu-smoke.sh --compute <compute-name> --instance-type gpu --stream` first. See [Smoke-Test a GPU Target](../../../docs/training/azureml-training.md#-smoke-test-a-gpu-target).

Submit a short run (10 to 20 steps) before committing to a full training job. Keep `--save-freq` at or below the step count so at least one checkpoint round-trips. Pass `--compute` with the attached compute name. When Terraform outputs are unavailable, set `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and `AZUREML_WORKSPACE_NAME` in `.env.local` or pass `--subscription-id`, `--resource-group`, and `--workspace-name`:

```bash
training/vla/scripts/submit-azureml-vla-pi0-training.sh \
  -d <dataset-repo-id> --dataset-asset azureml:<dataset-name>:<version> \
  -p pi05 --policy-repo-id lerobot/pi05_base \
  --training-steps 10 --batch-size 16 --save-freq 10 --log-freq 1 \
  --train-expert-only --mixed-precision bf16 \
  --compute <compute-name> --instance-type gpu \
  -j <job-name> --config-preview

training/vla/scripts/submit-azureml-vla-pi0-training.sh \
  -d <dataset-repo-id> --dataset-asset azureml:<dataset-name>:<version> \
  -p pi05 --policy-repo-id lerobot/pi05_base \
  --training-steps 10 --batch-size 16 --save-freq 10 --log-freq 1 \
  --train-expert-only --mixed-precision bf16 \
  --compute <compute-name> --instance-type gpu \
  -j <job-name>
```

## Validate GPU Usage Without Interrupting the Job

Never cancel a running job to validate it. Use read-only checks against the live pod and a bounded log stream instead.

The `az ml` commands run from any workstation. The `kubectl` commands run on the GPU host; when you submitted the job from another machine, open an `az ssh arc` session on the host as described in [Choose Where Each Command Runs](#choose-where-each-command-runs) and set `KUBECONFIG` there first.

Confirm the job status from the workstation, then find its pod on the host:

```bash
az ml job show --name <job-name> --query '{status:status}' -o json
```

```bash
kubectl get pods -n azureml -l azureml.job.name=<job-name>
```

Confirm NVIDIA devices and live utilization inside the execution-wrapper container, on the host:

```bash
kubectl exec -n azureml <pod-name> -c <pod-name-execution-wrapper> -- \
  sh -c 'ls /dev/nvidia*; nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader'
```

Confirm PyTorch itself reports the device, using the job's installed virtual environment rather than a system Python:

```bash
kubectl exec -n azureml <pod-name> -c <pod-name-execution-wrapper> -- \
  /opt/lerobot-venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
```

Stream a bounded window of user logs from the workstation and look for the training entrypoint's own detection line rather than relying on `nvidia-smi` alone, because it confirms the training process itself, not just the container, sees CUDA:

```bash
timeout --signal=INT 20s az ml job stream --name <job-name>
```

The training entrypoint logs `[GPU-DETECT] torch.cuda.device_count()=<n>, CUDA_VISIBLE_DEVICES=<value>` from [train.py](../../../training/il/scripts/lerobot/train.py) before invoking `lerobot-train`. A count of `0` on a job with a reserved GPU means the runtime injection is broken, not that the GPU is unavailable. `timeout` only stops the local log viewer; it does not cancel the Azure ML job.

`[GPU-DETECT]` only proves the container sees a device, not that training is using it. Confirm real GPU execution from the `lerobot-train` step log lines themselves: a rising `mem_gb` value across steps, a `step:<n>` counter advancing toward `--steps`, and a `Checkpoint policy after step <n>` line once the run finishes.

A smoke run reaching all configured steps within tens of seconds with `mem_gb` in the low double digits confirms the GPU did the work. A run stuck at step 0 or taking hours per step, even with `device_count=1`, still indicates the GPU is not actually being used.

An Azure ML job can stay in `Running` after the training loop finishes while large checkpoints upload; a `pretrained_model` payload of several gigabytes can take many minutes after the last `step:<n>` log line. Do not treat this as a hang.

A `[MLflow] Failed to log artifacts for <step>` message citing a task-queue flush timeout is a transient MLflow tracking-API timeout, not a training or upload failure, as long as the raw upload progress bar that follows it reaches 100%.

## Troubleshooting

| Symptom                                                                                            | Likely Cause                                                                                      | Resolution                                                                                                                                                  |
|----------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Job reserves one GPU but `[GPU-DETECT] torch.cuda.device_count()=0`                                | Pod has no `runtimeClassName` and K3s default runtime is not NVIDIA                               | Run `data-pipeline/setup/hil/05-enable-k3s-gpu.sh` on the host once no job containers are active                                                            |
| `nvidia-smi` missing or `/dev/nvidia*` absent inside the container                                 | Same root cause as above                                                                          | Confirm with the `kubectl exec` device checks in the validation section, then apply the K3s default runtime fix                                             |
| `403` fetching a gated HuggingFace repository                                                      | Account lacks gated-repo access, or `HF_TOKEN` is stale                                           | Sign in to huggingface.co as the account that owns `HF_TOKEN`, request access on the model page, refresh the token in `.env.local` if needed, then resubmit |
| Data asset mount fails at job start                                                                | Asset version not backed by a resolvable datastore path                                           | Register a new datastore-backed asset version and reference it explicitly                                                                                   |
| `05-attach-hil-azureml-compute.sh` stops at Arc cluster connect                                    | Your identity has no K3s RBAC on the cluster, or the proxy port is in use                         | Grant access with `05-connect-arc-kubernetes.sh --cluster-admin-signed-in-user` or `--cluster-admin-object-id`, or pass `--proxy-port`                      |
| Job stays `Queued` with the `gpu` instance type                                                    | The node reports no allocatable `nvidia.com/gpu`, usually because the device plugin isn't running | Run `05-enable-k3s-gpu.sh` on the host, then check `kubectl get node <node-name> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'`                       |
| Training runs entirely on CPU with no error                                                        | Same GPU runtime-injection root cause; PyTorch silently falls back                                | Apply the K3s default runtime fix before assuming a code-level bug                                                                                          |
| Job stays `Running` well after the last `step:<n>` log line                                        | Large checkpoint still uploading to blob storage                                                  | Check for an active upload progress bar in the log before assuming a hang                                                                                   |
| `[MLflow] Failed to log artifacts for <step>: ... Failed to flush task queue within 300.0 seconds` | Transient MLflow tracking-API timeout, unrelated to the checkpoint data itself                    | Confirm the raw upload progress bar immediately after it reaches 100%; no data is lost                                                                      |

> Brought to you by microsoft/physical-ai-toolchain
