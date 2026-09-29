---
name: arc-k3s-azureml-gpu-training
description: "Connect a user-owned Ubuntu/K3s host to Azure Arc, attach it as an Azure ML Kubernetes GPU compute target, and validate GPU access with a bounded smoke training job before full training. Use when: onboarding a new Arc-enabled K3s GPU host to Azure ML; diagnosing Azure ML jobs that reserve a GPU but silently run on CPU; configuring HuggingFace gated-model credentials or Azure ML dataset inputs for a training submission; or running a GPU smoke test before a long training run - Brought to you by microsoft/physical-ai-toolchain"
---

# Arc, K3s, and Azure ML GPU Training

Onboard a user-owned Ubuntu host running K3s to Azure Arc, attach the cluster to Azure ML as a GPU compute target, and prove real CUDA execution with a bounded smoke job before submitting a long-running training job. A GPU reservation in the Azure ML job spec is not proof the container can see the device; validate at the container level every time the runtime stack changes.

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Ubuntu host with an NVIDIA GPU and driver installed | K3s workload target |
| Existing Arc resource group, subscription ID, tenant ID | `azcmagent` and `connectedk8s` registration targets |
| Azure ML workspace with a Kubernetes-attachable extension | Compute target for job submission |
| `az` CLI with `connectedk8s`, `k8s-extension`, and `ml` extensions | Arc and Azure ML operations |
| NVIDIA Container Toolkit installed on the host | Provides the `nvidia-container-runtime` binary K3s detects |
| HuggingFace account with access to any gated base model | Required only when warm-starting from a gated repository (for example `google/paligemma-3b-pt-224`) |

## Connect the Host to Arc

Preview, then connect the Ubuntu server:

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

Install the pinned, owned K3s compute plane. Pass `--default-runtime nvidia` on any host where GPU-backed Azure ML jobs will run:

```bash
data-pipeline/setup/hil/01-install-k3s.sh --default-runtime nvidia --config-preview
data-pipeline/setup/hil/01-install-k3s.sh --default-runtime nvidia
```

> [!IMPORTANT]
> Azure ML InstanceType `limits.nvidia.com/gpu` only reserves the device through the Kubernetes device plugin. It does not set a pod `runtimeClassName`, so on a K3s host whose default runtime is not NVIDIA, the container starts with no `/dev/nvidia*` devices and `torch.cuda.device_count()` returns `0` even though the job reports a reserved GPU. Setting `default-runtime: nvidia` in the K3s config is required whenever the Azure ML Kubernetes extension does not expose per-pod runtime-class configuration.

If K3s was installed before this option existed, apply the same setting as a drop-in without reinstalling, then restart K3s once no job containers are running on the host:

```bash
sudo install -d -m 0755 /etc/rancher/k3s/config.yaml.d
printf 'default-runtime: nvidia\n' | sudo tee /etc/rancher/k3s/config.yaml.d/95-nvidia-default-runtime.yaml
sudo systemctl restart k3s
```

Verify the NVIDIA device plugin is healthy and the node advertises allocatable GPUs before attaching Azure ML:

```bash
kubectl get pods -n kube-system -l name=nvidia-device-plugin-ds
kubectl get node <node-name> -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'
```

## Connect the K3s Cluster to Arc

Enable workload identity so Azure ML can federate to Kubernetes service accounts:

```bash
data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --cluster-name <arc-cluster-name> --kubeconfig <kubeconfig-path> \
  --enable-workload-identity --config-preview

data-pipeline/setup/edge/05-connect-arc-kubernetes.sh \
  --subscription-id <subscription-id> --tenant-id <tenant-id> \
  --resource-group <arc-resource-group> --location <location> \
  --cluster-name <arc-cluster-name> --kubeconfig <kubeconfig-path> \
  --enable-workload-identity
```

## Attach the Cluster to Azure ML

Install the AzureML Kubernetes extension, apply GPU `InstanceType`s, create the federated identity credentials, and attach the compute target:

```bash
infrastructure/setup/02-deploy-azureml-extension.sh \
  --kubeconfig <kubeconfig-path> --context <arc-cluster-name> --config-preview

infrastructure/setup/02-deploy-azureml-extension.sh \
  --kubeconfig <kubeconfig-path> --context <arc-cluster-name>
```

This creates federated credentials for the `azureml:default` and `azureml:training` service accounts and applies [azureml-instance-types.yaml](../../../infrastructure/setup/manifests/azureml-instance-types.yaml). Confirm the `gpu` `InstanceType` matches actual node capacity (one `nvidia.com/gpu` limit per pod) before submitting jobs; request a larger InstanceType only when the node advertises more allocatable GPUs.

## Azure ML Credentials and Dataset Inputs

| Requirement | Where it applies |
|-------------|-------------------|
| `az login` session with rights to the target subscription and workspace | All `az ml` submission commands |
| Federated identity credentials for `azureml:default` and `azureml:training` | Created automatically by `02-deploy-azureml-extension.sh`; required for workload-identity access to storage and MLflow |
| `HF_TOKEN` with access to the gated base repository | Only when `--pretrained-policy-repo-id` or the HuggingFace dataset path resolves to a gated repository |
| Datastore-backed Azure ML data asset, referenced with an explicit numeric version (`azureml:NAME:VERSION`) | `--dataset-asset`; shorthand references without a version are rejected to keep runs reproducible |

Store `HF_TOKEN` in an untracked `.env.local`, never as a CLI argument or in chat. Tokens passed as CLI arguments are visible to any process inspecting the host, so rotate a token immediately if it was ever exposed that way. `.amlignore` already excludes `.env` and `.env.*` from the Azure ML code snapshot.

Data asset mount failures during job start often mean the registered asset version does not resolve against its backing datastore. Register a new datastore-backed version and reference that explicit version rather than reusing a broken one:

```bash
az ml data create --name <dataset-name> --version <next-version> \
  --type uri_folder --path azureml://datastores/<datastore>/paths/<path>
```

## Submit a Bounded GPU Smoke Test

Submit a short run (10 to 20 steps) before committing to a full training job. Keep `--save-freq` at or below the step count so at least one checkpoint round-trips:

```bash
training/vla/scripts/submit-azureml-vla-pi0-training.sh \
  -d <dataset-repo-id> --dataset-asset azureml:<dataset-name>:<version> \
  -p pi05 --pretrained-policy-repo-id lerobot/pi05_base \
  --pretrained-policy-revision <40-char-commit-sha> \
  --training-steps 10 --batch-size 16 --save-freq 10 --log-freq 1 \
  --train-expert-only --mixed-precision bf16 \
  --instance-type gpu --hf-token "$HF_TOKEN" \
  -j <job-name> --config-preview

training/vla/scripts/submit-azureml-vla-pi0-training.sh \
  -d <dataset-repo-id> --dataset-asset azureml:<dataset-name>:<version> \
  -p pi05 --pretrained-policy-repo-id lerobot/pi05_base \
  --pretrained-policy-revision <40-char-commit-sha> \
  --training-steps 10 --batch-size 16 --save-freq 10 --log-freq 1 \
  --train-expert-only --mixed-precision bf16 \
  --instance-type gpu --hf-token "$HF_TOKEN" \
  -j <job-name>
```

## Validate GPU Usage Without Interrupting the Job

Never cancel a running job to validate it. Use read-only checks against the live pod and a bounded log stream instead.

Confirm the job status and its pod:

```bash
az ml job show --name <job-name> --query '{status:status}' -o json
kubectl get pods -n azureml -l azureml.job.name=<job-name>
```

Confirm NVIDIA devices and live utilization inside the execution-wrapper container:

```bash
kubectl exec -n azureml <pod-name> -c <pod-name-execution-wrapper> -- \
  sh -c 'ls /dev/nvidia*; nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu --format=csv,noheader'
```

Confirm PyTorch itself reports the device, using the job's installed virtual environment rather than a system Python:

```bash
kubectl exec -n azureml <pod-name> -c <pod-name-execution-wrapper> -- \
  /opt/lerobot-venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
```

Stream a bounded window of user logs and look for the training entrypoint's own detection line rather than relying on `nvidia-smi` alone, because it confirms the training process itself, not just the container, sees CUDA:

```bash
timeout --signal=INT 20s az ml job stream --name <job-name>
```

The training entrypoint logs `[GPU-DETECT] torch.cuda.device_count()=<n>, CUDA_VISIBLE_DEVICES=<value>` from [train.py](../../../training/il/scripts/lerobot/train.py) before invoking `lerobot-train`. A count of `0` on a job with a reserved GPU means the runtime injection is broken, not that the GPU is unavailable. `timeout` only stops the local log viewer; it does not cancel the Azure ML job.

`[GPU-DETECT]` only proves the container sees a device, not that training is using it. Confirm real GPU execution from the `lerobot-train` step log lines themselves: a rising `mem_gb` value across steps, a `step:<n>` counter advancing toward `--steps`, and a `Checkpoint policy after step <n>` line once the run finishes.

A smoke run reaching all configured steps within tens of seconds with `mem_gb` in the low double digits confirms the GPU did the work. A run stuck at step 0 or taking hours per step, even with `device_count=1`, still indicates the GPU is not actually being used.

An Azure ML job can stay in `Running` after the training loop finishes while large checkpoints upload; a `pretrained_model` payload of several gigabytes can take many minutes after the last `step:<n>` log line. Do not treat this as a hang.

A `[MLflow] Failed to log artifacts for <step>` message citing a task-queue flush timeout is a transient MLflow tracking-API timeout, not a training or upload failure, as long as the raw upload progress bar that follows it reaches 100%.

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|--------------|------------|
| Job reserves one GPU but `[GPU-DETECT] torch.cuda.device_count()=0` | Pod has no `runtimeClassName` and K3s default runtime is not NVIDIA | Set `default-runtime: nvidia` in K3s config and restart K3s once no job containers are active |
| `nvidia-smi` missing or `/dev/nvidia*` absent inside the container | Same root cause as above | Verify with the disposable pod check, then apply the K3s default runtime fix |
| `403` fetching a gated HuggingFace repository | Account lacks gated-repo access, or `HF_TOKEN` is stale | Request access on huggingface.co, confirm with `curl -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/models/<repo>`, then resubmit |
| Data asset mount fails at job start | Asset version not backed by a resolvable datastore path | Register a new datastore-backed asset version and reference it explicitly |
| `az ml compute attach` fails after Arc connects cleanly | AzureML extension or InstanceType CRD not ready yet | Re-run `02-deploy-azureml-extension.sh`; it is idempotent and waits for the CRD |
| Training runs entirely on CPU with no error | Same GPU runtime-injection root cause; PyTorch silently falls back | Apply the K3s default runtime fix before assuming a code-level bug |
| Job stays `Running` well after the last `step:<n>` log line | Large checkpoint still uploading to blob storage | Check for an active upload progress bar in the log before assuming a hang |
| `[MLflow] Failed to log artifacts for <step>: ... Failed to flush task queue within 300.0 seconds` | Transient MLflow tracking-API timeout, unrelated to the checkpoint data itself | Confirm the raw upload progress bar immediately after it reaches 100%; no data is lost |

> Brought to you by microsoft/physical-ai-toolchain
