---
title: Local OSMO Profile
description: Standalone OSMO, platform services, MLflow, and the dataviewer on a single-node K3s workstation with local storage and images
author: Microsoft Robotics-AI Team
ms.date: 2026-10-19
ms.topic: how-to
keywords:
  - local-profile
  - k3s
  - osmo
  - mlflow
  - dataviewer
---

<!-- cspell:ignore kubeconfig -->

Run the training, evaluation, and dataset workflows on one GPU workstation without Azure. The local profile deploys a standalone OSMO control plane and backend into the workstation's K3s cluster. Datasets live on the host disk, images in a loopback registry, and metrics in an in-cluster MLflow server.

## 📋 Prerequisites

| Requirement                       | Check                                                        |
|-----------------------------------|--------------------------------------------------------------|
| Ubuntu host with NVIDIA driver    | `nvidia-smi` lists the GPUs                                  |
| NVIDIA Container Toolkit          | `nvidia-ctk --version`                                       |
| K3s from this repository, or none | `data-pipeline/setup/hil/01-install-k3s.sh --config-preview` |
| `kubectl`, `helm`, `jq`, `git`    | On `PATH`                                                    |
| `sudo` rights                     | `00-prepare-host.sh` calls `sudo` for its privileged steps   |

The local profile does not need Azure, a VPN, or the AKS OSMO deployment. An Arc-connected K3s host keeps its Arc agents; the profile neither removes nor depends on them.

## 🚀 Quick Start

Run every script as your user from the repository root.

1. Preview the host changes, then prepare the host:

   ```bash
   infrastructure/local/00-prepare-host.sh --config-preview
   infrastructure/local/00-prepare-host.sh
   ```

   The script creates the data root, upgrades K3s to the pinned release one minor version at a time after a datastore backup, and makes the NVIDIA runtime the K3s default with the device plugin installed. It is the only step that uses `sudo`.

2. Confirm the node reports the pinned K3s version and allocatable GPUs:

   ```bash
   kubectl --kubeconfig "${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml" \
     --context physical-ai-edge get nodes \
     -o custom-columns='NAME:.metadata.name,VERSION:.status.nodeInfo.kubeletVersion,GPUS:.status.allocatable.nvidia\.com/gpu'
   ```

3. Deploy the platform services that OSMO and MLflow use:

   ```bash
   infrastructure/local/01-deploy-platform-services.sh
   ```

   The script generates credentials once, waits for each service, creates the databases and buckets, and finishes with a smoke test: an image pushed with Docker must run from the local registry, and single-part and multipart objects must round-trip byte-identical through the object store. Reruns keep data and credentials.

4. Deploy OSMO, connect the OSMO CLI, and run the OSMO smoke test:

   ```bash
   infrastructure/local/02-deploy-osmo.sh
   ```

   The script installs KAI Scheduler, the OSMO service, and the backend operator from SHA-256 pinned charts, then runs [connect-osmo-cli.sh](connect-osmo-cli.sh) and [smoke-test-osmo.sh](smoke-test-osmo.sh). Open the OSMO web UI at `http://10.43.250.20`.

5. Deploy MLflow for metrics, artifacts, and the model registry:

   ```bash
   infrastructure/local/03-deploy-mlflow.sh
   ```

   The script finishes with a smoke test that logs a run, registers its checkpoint, and downloads it back. Open the MLflow UI at `http://10.43.250.30:5000`.

6. Deploy the dataviewer on your local datasets:

   ```bash
   infrastructure/local/04-deploy-dataviewer.sh
   ```

   The script builds the dataviewer images into the local registry, deploys them, and checks that the dataviewer lists the datasets directory. Open it at `http://10.43.250.40/` or through [kubectl proxy](#-dataviewer).

## 🧱 Platform Services

Everything runs in the `local-platform` namespace on `local-path` volumes, and every image is pinned by digest.

| Service        | Endpoint                                         | Used by                                   | Credentials Secret     |
|----------------|--------------------------------------------------|-------------------------------------------|------------------------|
| PostgreSQL 16  | `postgres.local-platform.svc.cluster.local:5432` | OSMO (`osmo` database), MLflow (`mlflow`) | `postgres-credentials` |
| Valkey (Redis) | `redis.local-platform.svc.cluster.local:6379`    | OSMO, password auth without TLS           | `redis-credentials`    |
| RustFS (S3)    | `http://10.43.250.10:9000`, path-style           | OSMO data, code staging, MLflow artifacts | `s3-credentials`       |
| Image registry | `localhost:5000`                                 | Docker pushes, K3s pulls                  | none                   |

Each database is owned by a role with the same name, and the `osmo` and `mlflow` buckets hold OSMO workflow data and MLflow artifacts. Read a credential without printing others, for example the S3 access key:

```bash
kubectl --kubeconfig "${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml" \
  --context physical-ai-edge -n local-platform get secret s3-credentials \
  -o jsonpath='{.data.access-key-id}' | base64 -d
```

The registry pod uses host networking and binds `127.0.0.1:5000` only, so nothing outside the workstation reaches it. Docker pushes to loopback registries without TLS, and K3s containerd pulls them over HTTP without a `registries.yaml` entry. It hosts locally built images only and mirrors no upstream registry, so the cluster never needs it to start its own pods.

The [gpu-offload host registry](../../gpu-offload/registry/README.md) uses the same port. Stop it before deploying, because only one registry can bind `127.0.0.1:5000`.

## 🛰️ OSMO

OSMO runs entirely in this cluster with dev login, no identity provider, and no connection to any other OSMO deployment. The service, the backend operator (backend `default`), and KAI Scheduler use the same pinned charts and base values as the AKS deployment; [values/osmo-control-plane-local.yaml](values/osmo-control-plane-local.yaml) replaces only the Azure-specific parts.

| Setting          | Local value                                                                             |
|------------------|-----------------------------------------------------------------------------------------|
| Service URL      | `http://10.43.250.20`, the same address from the host and from pods                     |
| Pool             | `default`, with platforms `default` (CPU) and `gpu_platform` (one GPU per task)         |
| Task resources   | Capped at the node's allocatable CPU, memory, storage, and GPUs                         |
| Workflow storage | `s3://osmo/workflows`, `s3://osmo/apps`, and the `training` bucket `s3://osmo/datasets` |
| Task mounts      | `/mnt/datasets` (read-only) and `/mnt/cache` in every task; no other host paths         |

Every task in the local pool sees the host datasets directory read-only at `/mnt/datasets` and the cache directory at `/mnt/cache`, with `HF_HOME`, `UV_CACHE_DIR`, and `PIP_CACHE_DIR` pointing into the cache so model and package downloads persist across jobs. OSMO host mounts are always read-write, so these mounts come from the pool's pod template, and OSMO rejects workflows that ask for any other host path.

OSMO picks pod templates per platform, not per submission, so a workflow cannot mount a policy image as a volume of its choosing. Containerized policies run as the task image instead, pulled from `localhost:5000`. OSMO skips its submit-time registry check for the local registry, which only the host can reach; K3s still pulls the image.

### OSMO CLI

[connect-osmo-cli.sh](connect-osmo-cli.sh) logs the CLI in with a dev login as `admin`, sets `default` as its pool, and stores the DATA credential `local-object-store` so `osmo data upload` and `osmo data download` reach `s3://osmo`. The OSMO CLI keeps that credential in its config directory with mode 0600; the script passes the values through file descriptors, never arguments.

The CLI holds one login per config directory, `~/.config/osmo` by default. The script refuses to replace a login to another OSMO service unless you pass `--force`. To keep both, give the local login its own directory and select it per shell:

```bash
LOCAL_OSMO_CONFIG_DIR="$HOME/.config/osmo-local" infrastructure/local/connect-osmo-cli.sh
export OSMO_CONFIG_FILE_DIR="$HOME/.config/osmo-local"
osmo workflow list
```

### Smoke Test

[smoke-test-osmo.sh](smoke-test-osmo.sh) submits [workflows/osmo-smoke.yaml](workflows/osmo-smoke.yaml) and checks each result: the task mounts and cache environment, a GPU task that reports its GPU, an uploaded input processed into an output that downloads back, a task image from the local registry, and the rejection of [an ad-hoc host mount](workflows/osmo-mount-rejected.yaml). It removes its test objects unless you pass `--keep-data`.

## 📈 MLflow

MLflow 3.16.1, matching the repository's `mlflow-skinny` client pin, keeps runs, metrics, and registered models in the `mlflow` PostgreSQL database and proxies artifacts into `s3://mlflow/artifacts`. Clients need only a tracking URI; the server holds the object-store credential.

| Client               | `MLFLOW_TRACKING_URI`                         |
|----------------------|-----------------------------------------------|
| OSMO jobs and pods   | `http://mlflow.mlflow.svc.cluster.local:5000` |
| Host CLIs and the UI | `http://10.43.250.30:5000`                    |

The server accepts only these host names, plus `localhost` through `kubectl port-forward`. Training registers a checkpoint directory as a model version whose source is the run's artifact location, so `models:/<name>/<version>` downloads the checkpoint itself. MLflow 3's `mlflow.register_model` accepts only logged MLflow models, which plain checkpoints are not.

## 🔍 Dataviewer

The dataviewer runs in the `dataviewer` namespace with authentication off, because nothing outside this host can reach it. Pods inside the cluster can: any OSMO task can use its API to change labels or write exports, though the task's own mount is read-only. It writes the datasets directory as the directory's owner, so every file it writes, such as the episode clips it caches in `meta/videos/`, stays yours. Datasets you add or remove appear on the next listing without a restart.

Open it in one of three ways:

* At its pinned ClusterIP, `http://10.43.250.40/`.
* Through `kubectl proxy`, at `http://127.0.0.1:8001/api/v1/namespaces/dataviewer/services/dataviewer:http/proxy/`:

  ```bash
  kubectl --kubeconfig "${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml" \
    --context physical-ai-edge proxy
  ```

* Through a port-forward, at `http://localhost:8080/`:

  ```bash
  kubectl --kubeconfig "${XDG_DATA_HOME:-$HOME/.local/share}/physical-ai-toolchain/hil/kubeconfig.yaml" \
    --context physical-ai-edge -n dataviewer port-forward service/dataviewer 8080:80
  ```

**Export** writes the current episode, with its edits, as a new dataset in the datasets directory. The default name is `<dataset>-export`, and jobs can train from it with `--local-dataset`. Object detection answers 503, because the backend image leaves out the CUDA detection stack. To list Azure Blob datasets beside the local ones, still without authentication, see [Optional Azure Access](azure-access.md).

## 💾 Datasets

Each dataset is one LeRobot folder in the datasets directory, `/srv/physical-ai-toolchain/datasets` by default. Every OSMO task sees that directory read-only at `/mnt/datasets`, so a job trains from a dataset without copying it, and the dataviewer browses and edits it.

Manage the folders with [datasets.sh](datasets.sh), which writes as your user:

```bash
infrastructure/local/datasets.sh list
infrastructure/local/datasets.sh import <capture-dir> my-dataset
infrastructure/local/datasets.sh remove my-dataset
```

`derive` builds a learner dataset from simulation captures. It keeps the episodes that each capture's validation and label sidecars mark as passing, removes the features a learner must not see, and merges the rest into a new folder:

```bash
infrastructure/local/datasets.sh derive so101-learner \
  --source <captures>/so101-cube-box-overhead \
  --source <captures>/so101-pick-place-retry1 \
  --drop-feature observation.environment_state \
  --drop-feature observation.task_phase \
  --drop-feature observation.assistance
```

The sources stay unchanged. Derivation runs `lerobot-edit-dataset` from the IL LeRobot lock, so its first run downloads that environment, and the new folder appears only after the merge completes. Derivation copies each source into a `.staging` folder beside the datasets directory, and dropping features needs free space for one more copy of the source being processed.

`import` and `derive` refuse a dataset that holds a symbolic link or a special file, and `derive` refuses a capture sidecar that is one, so a dataset cannot bring other host files into the directory every task reads. [Data Management Tools](../../data-management/tools/README.md) describes the selection rules and [the UR10e derivation](../../data-management/tools/README.md#derive-the-ur10e-learner-dataset) for the IL test.

## 📤 Submitting Jobs

The OSMO submit scripts take `--profile local`, or read `PLATFORM_PROFILE=local` from the environment:

| Script                                                                                                      | Workload                                     |
|-------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| [submit-osmo-training.sh](../../training/rl/scripts/submit-osmo-training.sh)                                | Isaac Lab RL training                        |
| [submit-osmo-dataset-training.sh](../../training/rl/scripts/submit-osmo-dataset-training.sh)                | Isaac Lab RL training with code as a dataset |
| [submit-osmo-lerobot-training.sh](../../training/il/scripts/submit-osmo-lerobot-training.sh)                | LeRobot ACT, Diffusion, and SmolVLA training |
| [submit-osmo-lerobot-vla-fine-tuning.sh](../../training/vla/scripts/submit-osmo-lerobot-vla-fine-tuning.sh) | GR00T fine-tuning                            |
| [submit-osmo-eval.sh](../../evaluation/sil/scripts/submit-osmo-eval.sh)                                     | Isaac Lab policy evaluation                  |
| [submit-osmo-lerobot-eval.sh](../../evaluation/sil/scripts/submit-osmo-lerobot-eval.sh)                     | LeRobot policy evaluation                    |
| [submit-osmo-judge.sh](../../evaluation/vlm_judge/scripts/submit-osmo-judge.sh)                             | VLM-as-a-judge scoring                       |

With the local profile, a submit script:

* Submits through the OSMO CLI login in `LOCAL_OSMO_CONFIG_DIR` and stops when that login points at another OSMO service.
* Stages code and workflow data in the in-cluster object store.
* Logs runs and metrics and registers checkpoints in the local MLflow server.
* Requests `LOCAL_TASK_CPU`, `LOCAL_TASK_MEMORY`, and `LOCAL_TASK_STORAGE` unless you pass `--cpu`, `--memory`, or `--storage`.
* Sends no Azure subscription, workspace, or identity to the job, unless `TRACKING_BACKEND=azureml` and [Optional Azure Access](azure-access.md) are set up.

`--local-dataset NAME` trains or evaluates from `/mnt/datasets/NAME`. `TRACKING_BACKEND=none` runs a job without tracking and rejects options that need a model registry. Add `--config-preview` to print the resolved configuration without submitting:

```bash
training/il/scripts/submit-osmo-lerobot-training.sh --profile local \
  --local-dataset so101-learner \
  --training-steps 2000 \
  -r so101-act
```

## ✅ Validating the Stack

The OSMO lifecycle tests run against the local profile when `PLATFORM_PROFILE=local` is set. They need the datasets above in the data root and Docker with Buildx for the carrier images:

```bash
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_rl_lifecycle.py
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_il_lifecycle.py
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_vla_finetune.py
PLATFORM_PROFILE=local uv run pytest -vv -s -m e2e tests/e2e/test_e2e_osmo_vlm_judge.py
```

| Test              | Proves                                                                                                                                                         |
|-------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| RL lifecycle      | Anymal-C trains, logs to MLflow, and registers; the gated evaluation passes from the checkpoint and its carrier image; the VLM judge scores the rollout videos |
| IL lifecycle      | ACT trains on `ur10e-gear-to-bin-learner` and beats its untrained baseline on action error; the carrier image reproduces the checkpoint's metrics              |
| SmolVLA lifecycle | SmolVLA fine-tunes on `so101-learner` and beats the unadapted base; the carrier image reproduces the checkpoint's metrics                                      |
| VLM judge         | Qwen3-VL-4B scores every `so101-learner` episode with an outcome, a confidence, and per-frame progress                                                         |

The tests clean up the models, carrier manifests, and datasets they create, and leave their MLflow runs for review. [CONTRIBUTING.md](../../CONTRIBUTING.md#local-platform-profile) lists their acceptance criteria and dataset overrides.

## 🔐 Optional Azure Access

Local jobs can also use Azure ML tracking, Blob Storage, and ACR images, and the dataviewer can list Blob datasets, once Azure trusts this cluster's Arc workload identity. [Optional Azure Access](azure-access.md) explains what that takes; [enable-azure-access.sh](enable-azure-access.sh) previews the changes and applies them only with `--apply`.

## 🧹 Teardown

```bash
infrastructure/local/cleanup/uninstall-local.sh                 # remove workloads, keep data and credentials
infrastructure/local/cleanup/uninstall-local.sh --prune-cache   # also empty the job-owned cache directory
infrastructure/local/cleanup/uninstall-local.sh --purge-data    # also delete volumes and credentials
```

Teardown removes the dataviewer, MLflow, OSMO, KAI Scheduler, and the platform services, newest layer first, and never touches K3s, Arc, the GPU device plugin, or your datasets. Without `--purge-data` it keeps OSMO's master encryption key with the database, so credentials stored in OSMO stay readable after a redeploy. Job containers write `cache/` as root, so `--prune-cache` empties it from a pod instead of with `sudo`; run it when no jobs are running.

## ⚙️ Configuration

[defaults.conf](defaults.conf) holds every local profile value. Override any value through the environment.

| Variable                                          | Default                            | Purpose                                                                     |
|---------------------------------------------------|------------------------------------|-----------------------------------------------------------------------------|
| `LOCAL_KUBECONFIG`, `LOCAL_KUBE_CONTEXT`          | HiL kubeconfig, `physical-ai-edge` | Target cluster written by `01-install-k3s.sh`; scripts verify it is K3s     |
| `LOCAL_DATA_ROOT`                                 | `/srv/physical-ai-toolchain`       | Host data root owned by your user                                           |
| `LOCAL_DATASETS_DIR`                              | `$LOCAL_DATA_ROOT/datasets`        | LeRobot datasets that the dataviewer edits and jobs mount read-only         |
| `LOCAL_CACHE_DIR`                                 | `$LOCAL_DATA_ROOT/cache`           | Job-owned caches, pruned in-cluster                                         |
| `LOCAL_*_CLUSTER_IP`                              | `10.43.250.0/24` addresses         | Pinned service addresses inside the K3s service CIDR                        |
| `LOCAL_REGISTRY_HOST`                             | `localhost:5000`                   | Loopback registry that K3s containerd pulls from over HTTP                  |
| `LOCAL_TASK_CPU`, `LOCAL_TASK_MEMORY`             | `12`, `40Gi`                       | Per-task requests sized for one GPU job at a time                           |
| `LOCAL_TASK_STORAGE`                              | `80Gi`                             | Per-task ephemeral storage request                                          |
| `LOCAL_JOB_DATASETS_PATH`, `LOCAL_JOB_CACHE_PATH` | `/mnt/datasets`, `/mnt/cache`      | Where every OSMO task sees the datasets and cache directories               |
| `LOCAL_OSMO_CONFIG_DIR`                           | `~/.config/osmo`                   | OSMO CLI config directory that `connect-osmo-cli.sh` logs in                |
| `LOCAL_OSMO_AZURE_CLIENT_ID`                      | empty                              | Managed identity that workflow pods use for [Azure access](azure-access.md) |
| `LOCAL_DATAVIEWER_BACKEND_EXTRAS`                 | `azure,analysis,export,auth`       | Backend image extras; `yolo` adds the CUDA detection stack                  |

The workstation reaches ClusterIP addresses directly, so the same service URL works from host CLIs and from pods without exposing a port on the network.

### Platform Profiles

OSMO submit scripts and end-to-end fixtures read one platform profile. `azure` is the default and keeps the Terraform-driven behavior; `local` reads [defaults.conf](defaults.conf).

```bash
scripts/platform-profile.sh --profile local
PLATFORM_PROFILE=local scripts/platform-profile.sh --json
```

The resolver rejects a ClusterIP outside the K3s service CIDR before any script uses it. `TRACKING_BACKEND` overrides the profile's tracking backend, `mlflow` for `local` and `azureml` for `azure`; `none` turns tracking off.

## 🔗 Related Documentation

* [Optional Azure Access](azure-access.md): Arc workload identity, ACR pulls, and Azure ML tracking from local jobs
* [Edge K3s Setup](../../docs/data-pipeline/edge-k3s-setup.md): K3s install, upgrade path, and GPU enablement
* [Cluster Setup](../setup/README.md): the Azure AKS deployment that the local profile mirrors
