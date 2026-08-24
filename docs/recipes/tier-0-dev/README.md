# T0 — Dev: The Local Training Lifecycle Loop

Walk the full training lifecycle loop, capture, curate, train, validate, and run on the robot, on **one laptop
and one robot**, with **zero cloud** and **zero Kubernetes**. Every step here runs as a plain local
process against local files. By the end you will have trained an ACT policy, validated it against
recorded episodes, and be ready to run the policy back on the robot.

This is the documented **default** starting path. T0 already exists in the code today; this recipe
surfaces it.

> [!NOTE]
> **Full training lifecycle:** capture demonstrations on a robot, train an imitation policy, validate it, and run that
> policy back on the robot, the full loop for one task. The full training lifecycle is fully achievable at T0 with manual
> deployment and no Kubernetes, Arc, or fleet infrastructure. For the canonical tier definitions and
> graduation boundaries, see the [tier model](../../design/tier-model.md) and the
> [architecture tier detail](../../contributing/architecture.md#t0--dev).

## 🧱 Minimum Infrastructure

| Concern     | What you need                                                                           |
|-------------|-----------------------------------------------------------------------------------------|
| Hardware    | One laptop or workstation, one robot. A local GPU is optional; CPU works.                |
| Edge infra  | ROS 2 and Docker only. **No** Kubernetes, **no** Arc, **no** Flux, **no** PVC.          |
| Cloud infra | **None.** No Azure subscription, no storage account, no AzureML workspace.              |
| Tooling     | Python 3.12+ with [`uv`](https://docs.astral.sh/uv/), Node.js 18+ (for the dataviewer). |
| Tracking    | Optional. Training outputs are written to local disk; hosted tracking enters at T2.     |

Everything below runs on the single machine in front of you. The only thing that leaves the laptop is
data you copy off the robot with `cp` or `rsync`.

## 🔁 The Loop at a Glance

```text
Capture ──► Move data ──► Curate ──────► Train ──────► Validate ────────► Run on robot
(ROS 2 bag)  (rsync/cp)  (dataviewer     (lerobot-     (run-local-        (inference node,
                          local mode)     train)        lerobot-eval)      plain process)
```

## 🚀 Steps

### Step 1: Capture demonstrations on the robot

Record human demonstrations to a ROS 2 bag on local disk, on the robot or directly on the laptop. No
edge storage service, no Arc, and no PVC are involved; the bag is just a file.

```bash
ros2 bag record -o demos/insertion-task /observations /actions
```

Convert the recordings into a LeRobot dataset on disk. See
[Configuring Edge Data Recording](../data-collection/configuring-edge-data-recording.md) for the
recording side and [Preparing Datasets for Training](../data-collection/preparing-datasets-for-training.md)
for converting and validating the dataset locally.

### Step 2: Move data to the laptop

If you recorded on the robot, copy the dataset to the laptop. This is a file copy, nothing more:

```bash
rsync -av robot@robot.local:~/demos/ ~/datasets/insertion-task/
```

### Step 3: Curate with the dataviewer in local mode

Launch the dataviewer against your local datasets directory. In local mode it reads from disk, with no
Azure Blob, no managed identity, no SAS token, and authentication is disabled for local development.

```bash
cd data-management/viewer && DATA_DIR=~/datasets ./start.sh
```

Wait for `[OK] Both services are running`, then open the printed `http://localhost:...` URL to browse
episodes, inspect frames, and drop bad demonstrations before training.

### Step 4: Train an imitation policy locally

Train an ACT policy with LeRobot's `lerobot-train` CLI. It runs as a plain local process against your
on-disk dataset, with no Azure and no cluster. Pick the device explicitly: `cpu` on a laptop with no GPU, or
`cuda` if you have one.

```bash
lerobot-train \
  --dataset.repo_id=local/insertion-task \
  --dataset.root=~/datasets/insertion-task \
  --policy.type=act \
  --policy.device=cpu \
  --wandb.enable=false \
  --output_dir=outputs/train/insertion-act \
  --steps=20000
```

Swap `--policy.device=cpu` for `--policy.device=cuda` to train on a local GPU. Checkpoints, the
resolved config, and training logs are written under `--output_dir`.

> [!NOTE]
> The repo also ships an orchestrator at
> [`training/il/scripts/lerobot/train.py`](https://github.com/microsoft/physical-ai-toolchain/blob/main/training/il/scripts/lerobot/train.py)
> that wraps `lerobot-train`, parses its metrics, and logs them to MLflow. That orchestrator connects
> to an AzureML workspace. It requires `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, and
> `AZUREML_WORKSPACE_NAME`, so it belongs to the cloud-backed path at
> [T2 — Pilot](../tier-2-pilot/README.md), not T0. At T0 you call `lerobot-train` directly.

### Step 5: Keep your run outputs locally

`lerobot-train` writes everything you need to inspect a run: checkpoints, the resolved training
config, and step-by-step logs under `--output_dir` on local disk. Nothing leaves the laptop, and no
tracking server is required to train or to compare runs by hand.

Hosted experiment tracking is a later concern. The repo's MLflow integration lives inside the
orchestrator from Step 4 and connects to an AzureML workspace, so managed tracking and a model
registry enter at [T2 — Pilot](../tier-2-pilot/README.md). If you want local run comparison at T0
without standing anything up, `lerobot-train` can log to Weights & Biases in offline mode
(`--wandb.enable=true` with `WANDB_MODE=offline`), which writes to local disk with no account or
server.

### Step 6: Validate the policy locally

Replay recorded episodes through the trained policy and compare predicted actions to ground truth
with
[`evaluation/sil/scripts/run-local-lerobot-eval.py`](https://github.com/microsoft/physical-ai-toolchain/blob/main/evaluation/sil/scripts/run-local-lerobot-eval.py).
It runs entirely locally against a local checkpoint and a local dataset, and defaults to CPU
inference:

```bash
uv run python evaluation/sil/scripts/run-local-lerobot-eval.py \
  --policy-path outputs/train/insertion-act/checkpoints/last/pretrained_model \
  --dataset-dir ~/datasets/insertion-task \
  --episodes 5 \
  --output-dir outputs/local-eval
```

| Flag            | Purpose                                                     |
|-----------------|-------------------------------------------------------------|
| `--policy-path` | Local checkpoint path (or a HuggingFace repo ID).           |
| `--dataset-dir` | Path to the local LeRobot dataset root.                     |
| `--episodes`    | Number of episodes to replay (default 5).                   |
| `--device`      | `cpu` (default), `cuda`, or `mps`.                          |
| `--output-dir`  | Where per-episode trajectory plots and metrics are written. |

The script writes aggregate metrics and per-episode trajectory plots into `--output-dir`, so you can
attribute a regression rather than guess at it.

> [!NOTE]
> For **RL** policies the analogous local playback entry point is
> [`evaluation/sil/play.py`](https://github.com/microsoft/physical-ai-toolchain/blob/main/evaluation/sil/play.py),
> which loads a trained RSL-RL checkpoint and runs it in the Isaac Sim simulator on the same machine.

### Step 7: Run the policy back on the robot

Close the loop: run the validated policy on the robot as a plain ACT inference process or container.
**No Flux, no gating, no GitOps:** you start the inference node by hand against the checkpoint from
Step 4. That manual run *is* the T0 deployment story; declarative GitOps deployment is the
[T3 — Production](../tier-3-production/README.md) concern.

## 🎓 Graduate When

Move up a tier when any of these become true:

- You have **no local GPU** and training is too slow: add cloud storage at
  [T1 — Lab](../tier-1-lab/README.md), or go straight to cloud training at
  [T2 — Pilot](../tier-2-pilot/README.md).
- The task needs **many training iterations** as conditions vary.
- **A second person needs the data:** shared storage starts at [T1 — Lab](../tier-1-lab/README.md).

## 🔗 Related Documentation

- [Tier model (canonical reference)](../../design/tier-model.md): tier IDs, boundaries, vocabulary.
- [Architecture: T0 — Dev](../../contributing/architecture.md#t0--dev): contributor-facing tier detail.
- [Recipe index](../README.md): all recipes organized by tier.
- [T1 — Lab](../tier-1-lab/README.md): the next tier, add cloud storage.
