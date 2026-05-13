# OpenVLA-OFT Fine-Tuning Pipeline

End-to-end pipeline to fine-tune [OpenVLA](https://github.com/openvla/openvla) with the [Optimized Fine-Tuning (OFT) recipe](https://openvla-oft.github.io/) on a LeRobot v3 dataset, submitted to Azure ML against an A100-backed compute target.

## Pipeline Stages

| Stage | Script | Description |
| --- | --- | --- |
| 1. Filter | [filter_dataset.py](../scripts/openvla_oft/filter_dataset.py) | Walk LeRobot v3 metadata, keep episodes that have all required video views (and optionally were judged successful by `evaluation/vlm_judge/run.py`). Emits a manifest JSON. |
| 2. Convert | [lerobot_to_rlds.py](../scripts/openvla_oft/lerobot_to_rlds.py) | Build an RLDS / TFDS dataset from the manifest (3 image views + 12-DOF proprio + 12-DOF action + per-episode language instruction). |
| 3. Register | [dataset_registration.py](../scripts/openvla_oft/dataset_registration.py) | Patch the `prismatic/vla/datasets/rlds/oxe/{configs,transforms,mixtures}.py` and `prismatic/vla/constants.py` files inside a clone of `moojink/openvla-oft` so the OFT data loader recognises our dataset. |
| 4. Train | [azureml-train-entry.sh](../scripts/openvla_oft/azureml-train-entry.sh) | Container entrypoint that runs stages 1-3 then launches `torchrun vla-scripts/finetune.py` with our hyperparameters. |
| 5. Submit | [submit-azureml-openvla-oft-training.sh](../scripts/submit-azureml-openvla-oft-training.sh) | Submission wrapper (mirrors `submit-azureml-lerobot-training.sh`) that registers the environment, builds the `--set` overrides, and invokes `az ml job create`. |

## Recipe (defaults)

OFT+ for ALOHA-style bimanual setups, adapted to the Schaeffler 12-DOF UR5e:

| Parameter | Value | Notes |
| --- | --- | --- |
| Base VLA | `openvla/openvla-7b` | 7B Llama-2 + Prismatic vision backbone |
| Images in input | 3 | primary (`d405_stationary_r_0`) + left wrist (`l_1`) + right wrist (`l_2`) |
| Proprio | enabled | 12-DOF joint state (R/L 1..6) |
| FiLM | enabled | needed for 11-sub-task language grounding |
| L1 regression head | enabled | OFT default; outperforms diffusion for our data shape |
| Action chunk | 25 | ~0.83 s at 30 Hz |
| LoRA rank | 32 | as in paper |
| Batch size / GPU | 4 | ~73 GB recommended footprint per A100 |
| Learning rate | 5e-4 | decay 10x after 50k steps |
| Max steps | 100,005 | save every 10k |
| GPUs | 2 | `Standard_NC48ads_A100_v4` (2x A100 80GB) |

## Compute

The pipeline runs on either:

- **AKS-backed AzureML compute** (Arc-attached AzureML extension): uses InstanceType CRDs
- **Managed AzureML compute clusters**: uses the VM size directly via `--compute <cluster>`

### Option A. AKS-backed (canonical pattern in `infrastructure/`)

| Asset | Location | Purpose |
| --- | --- | --- |
| `gpu` InstanceType | [azureml-instance-types.yaml](../../../infrastructure/setup/manifests/azureml-instance-types.yaml) | 1x A10 24 GB (already deployed on the current cluster); use with `--profile dryrun-a10` |
| `gpu-a100` InstanceType | [azureml-instance-types.yaml](../../../infrastructure/setup/manifests/azureml-instance-types.yaml) | Requests 2x `nvidia.com/gpu`, 220 GiB RAM; nodeSelector `accelerator: nvidia` + `gpu-class: a100`. |
| `a100gpu` node pool example | [terraform.tfvars.example](../../../infrastructure/terraform/terraform.tfvars.example) | Adds an `a100gpu` pool with `Standard_NC48ads_A100_v4` and the `gpu-class: a100` label. |

> [!IMPORTANT]
> The cluster has **no A100 pool** by default. To run this pipeline on AKS, uncomment the `a100gpu` block in `terraform.tfvars`, `terraform apply` from `infrastructure/terraform/`, then re-apply the instance-type manifest via `kubectl apply -f infrastructure/setup/manifests/azureml-instance-types.yaml`.

### Option B. Managed compute cluster (cross-region eastus, recommended for A100)

The project's workspace (`mlw-hex-osmo-hack-001`, westus3) has only 96 vCPU A100 quota. eastus has 400 vCPU available. Stand up a sibling workspace:

```bash
infrastructure/setup/setup-eastus-a100-workspace.sh
```

This creates `mlw-hex-train-eus-002` + `a100-cluster` (`Standard_NC24ads_A100_v4` = 1x A100 80GB, autoscale 0→2 nodes, 30 min idle scale-down). One A100 80GB is sufficient for the full OFT recipe (~73 GB VRAM use).

Alternative VM sizes for managed compute or AKS pool:

| VM size | A100 GPUs | Memory | Notes |
| --- | --- | --- | --- |
| `Standard_NC24ads_A100_v4` | 1 × 80 GB | 220 GiB | Default for managed compute; single-GPU OFT |
| `Standard_NC48ads_A100_v4` | 2 × 80 GB | 440 GiB | Cheapest 2-GPU; default for AKS pool |
| `Standard_NC96ads_A100_v4` | 4 × 80 GB | 880 GiB | Drop `--num-gpus 4` to use all 4 |
| `Standard_ND96amsr_A100_v4` | 8 × 80 GB | 1900 GiB | Multi-node OFT (paper config); `--num-gpus 8` |

## Usage

### 0. Connect to VPN

The workspace storage account is private — uploading datasets and submitting jobs requires the point-to-site VPN.

```bash
cd infrastructure/terraform/vpn
# follow docs/infrastructure/vpn.md to download the OpenVPN profile
nslookup stfyep5hexosmohack001.blob.core.windows.net  # must resolve before continuing
```

### 1. Register the dataset as an AzureML data asset

```bash
training/il/scripts/upload-dataset-to-aml.sh \
  --path datasets/schaeffler_sim_avc1/second_collection \
  --name schaeffler-sim-avc1-second \
  --version 1
```

The helper checks VPN connectivity, auto-bumps the version if you re-run, and prints the asset URI for the next step. The submit script then mounts the asset read-only at `${DATASET_MOUNT}` inside the container — no blob URL juggling required.

### 2. A10 smoke test (current cluster, ~1000 steps)

Use the `dryrun-a10` profile to validate the full pipeline (clone → RLDS build → torchrun → checkpoint upload) on the 1× A10 24 GB node already in the AKS cluster. This drops to `batch_size=1`, `num_images=1`, `lora_rank=16`, `num_actions_chunk=8`, no FiLM/proprio, `max_steps=1000`, `save_freq=500`.

```bash
training/il/scripts/submit-azureml-openvla-oft-training.sh \
  --profile dryrun-a10 \
  --dataset-asset schaeffler-sim-avc1-second:1 \
  --stream
```

Verify after the run:

- Two checkpoints land under `outputs/checkpoints/` of the AzureML run (step 500 + step 1000)
- Stdout streams `[Step N] L1 Loss ...` lines (OFT's native log format; captured by AzureML)
- The job's "Outputs + logs" tab shows `system_logs/` and `user_logs/` with the torchrun stderr

### 3. A100 production run (cross-region eastus)

The current westus3 workspace has no A100 node pool. eastus has 400 vCPU of `NCADS_A100_v4` quota available. Stand up a sibling workspace + managed compute cluster:

```bash
infrastructure/setup/setup-eastus-a100-workspace.sh   # idempotent; ~5 min
```

This creates `mlw-hex-train-eus-002` + `a100-cluster` (`Standard_NC24ads_A100_v4`, autoscale 0→2, 30 min idle scale-down). The 3 deallocated A100 VMs in `RG-HEX-TRAIN-EUS-001` can be deleted afterwards to recover disk costs — they're not used by AzureML.

Replicate the data asset to the eastus workspace, then submit:

```bash
training/il/scripts/upload-dataset-to-aml.sh \
  --path datasets/schaeffler_sim_avc1/second_collection \
  --name schaeffler-sim-avc1-second \
  --version 1 \
  --workspace-name mlw-hex-train-eus-002 \
  --resource-group rg-hex-train-eus-002

training/il/scripts/submit-azureml-openvla-oft-training.sh \
  --profile prod-a100 \
  --resource-group rg-hex-train-eus-002 \
  --workspace-name mlw-hex-train-eus-002 \
  --compute a100-cluster \
  --instance-type "" \
  --dataset-asset schaeffler-sim-avc1-second:1 \
  --num-gpus 1 \
  --batch-size 4 \
  --stream
```

> [!NOTE]
> `--instance-type ""` is correct for **managed** compute clusters — the InstanceType CRD is only needed for K8s-backed Arc-attached AzureML compute. For an AKS-backed A100 pool (the gpu-a100 InstanceType in this repo), pass `--instance-type gpu-a100` instead.

### 4. Override hyperparameters

All OFT flags exposed as CLI arguments on `submit-azureml-openvla-oft-training.sh`:

```bash
training/il/scripts/submit-azureml-openvla-oft-training.sh \
  -d schaeffler_sim_avc1/second_collection \
  --batch-size 8 \
  --max-steps 150005 \
  --num-steps-before-decay 100000 \
  --lora-rank 64 \
  --use-film False        # drop FiLM if language doesn't change
```

Recipe overrides (recipe ablations):

| Variant | Flags |
| --- | --- |
| OFT (no FiLM) | `--use-film False` |
| OFT 2-image (LIBERO-style) | `--num-images 2 --num-actions-chunk 8` |
| Single-GPU dev run | `--num-gpus 1 --batch-size 1 --max-steps 1000` |

### 5. Local inspection

Run the filter and dry-run the RLDS converter locally to verify the manifest before submission:

```bash
python -m training.il.scripts.openvla_oft.filter_dataset \
  --dataset datasets/schaeffler_sim_avc1/second_collection \
  --image-keys observation.images.d405_stationary_r_0 \
               observation.images.d405_stationary_l_1 \
               observation.images.d405_stationary_l_2 \
  --vlm-judge outputs/dataset-analysis/schaeffler_second_collection/vlm-judge.jsonl \
  --require-vlm-success \
  --output datasets/schaeffler_sim_avc1/second_collection/training_manifest.json

python -m training.il.scripts.openvla_oft.lerobot_to_rlds \
  --manifest datasets/schaeffler_sim_avc1/second_collection/training_manifest.json \
  --primary-camera observation.images.d405_stationary_r_0 \
  --left-wrist    observation.images.d405_stationary_l_1 \
  --right-wrist   observation.images.d405_stationary_l_2 \
  --dry-run
```

Result for `schaeffler_sim_avc1/second_collection`: **76 eligible episodes / 68,089 frames** (97 declared − 16 missing-views − 4 VLM-judged failures − 1 unjudged).

## Memory-aware Defaults

| GPU | Recommended config |
| --- | --- |
| 1 × A100 40GB | `--batch-size 1 --num-gpus 1` (75 GB recommended -> use grad accumulation) |
| 1 × A100 80GB | `--batch-size 4 --num-gpus 1` |
| 2 × A100 80GB | `--batch-size 4 --num-gpus 2` (default) |
| 4-8 × A100 80GB | `--batch-size 8 --num-gpus 4` (paper-style) |

> [!NOTE]
> OFT recommends merging the LoRA adapter into the base VLA **on the same GPU class used for inference**. If you train on A100 and deploy on H100, use `vla-scripts/merge_lora_weights_and_save.py` on the inference target. The entry script writes the LoRA adapter alongside the merged checkpoint under `$TRAINING_CHECKPOINT_OUTPUT/`.

## Related

- [evaluation/vlm_judge](../../../evaluation/vlm_judge) - generates the success labels consumed by `--require-vlm-success`
- [training/il/lerobot](../lerobot) - the ACT / Diffusion training pipeline (LeRobot, not OpenVLA)
- [docs/contributing/architecture.md](../../../docs/contributing/architecture.md) - overall toolchain architecture
