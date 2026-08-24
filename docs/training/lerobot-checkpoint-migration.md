---
sidebar_position: 6
title: Migrate LeRobot Checkpoints
description: Convert LeRobot checkpoints trained before 0.6 to the processor-based checkpoint format
author: Microsoft Robotics-AI Team
ms.date: 2026-07-14
ms.topic: how-to
keywords:
  - lerobot
  - checkpoint
  - migration
  - processor pipeline
  - warm-start
  - evaluation
---

Convert checkpoints trained with LeRobot versions before 0.6 before using them for evaluation, deployment, or warm-start training on LeRobot 0.6 or later. The migration extracts normalization statistics from the model and saves them as external preprocessor and postprocessor artifacts.

## 📋 Prerequisites

| Component         | Requirement                                                               |
|-------------------|---------------------------------------------------------------------------|
| Source checkpoint | `config.json` and `model.safetensors`                                     |
| Python            | 3.12 or later                                                             |
| Runtime           | Frozen `training/il/lerobot/uv.lock` environment                          |
| Output storage    | A new writable directory; never overwrite the only copy of the checkpoint |

`train_config.json` is optional. Retain it when present to preserve training and dataset metadata.

## 🔍 Check Checkpoint Format

List the source checkpoint artifacts:

```bash
find <legacy-checkpoint> -maxdepth 1 -type f -printf '%f\n' | sort
```

A processor-format checkpoint contains these files:

```text
config.json
model.safetensors
policy_preprocessor.json
policy_preprocessor_step_3_normalizer_processor.safetensors
policy_postprocessor.json
policy_postprocessor_step_0_unnormalizer_processor.safetensors
```

Do not migrate a checkpoint that already contains both processor JSON files. Validate it directly instead.

## 🔄 Migrate a Local Checkpoint

Prepare the frozen LeRobot 0.6 environment from the repository root:

```bash
uv sync --project training/il/lerobot --frozen
```

Run the upstream LeRobot migration utility and write to a new directory:

```bash
uv run --project training/il/lerobot --frozen \
  python -m lerobot.processor.migrate_policy_normalization \
  --pretrained-path <legacy-checkpoint> \
  --output-dir <migrated-checkpoint>
```

The migration performs these operations:

1. Loads the policy configuration and model state
2. Extracts mean, standard deviation, minimum, and maximum normalization tensors
3. Removes legacy normalization modules from the core model state
4. Creates external preprocessor and postprocessor pipelines
5. Saves cleaned weights, updated configuration, processor artifacts, and a model card

Stop the migration when it reports unexpected missing or unexpected model keys. Review policy-specific architecture changes before accepting those artifacts.

> [!CAUTION]
> Preserve the source checkpoint. Migration transforms the model artifact and does not convert optimizer, scheduler, random number generator, or training-step state.

## ☁️ Migrate a Hugging Face Checkpoint

Pin the source to an immutable 40-character commit SHA:

```bash
uv run --project training/il/lerobot --frozen \
  python -m lerobot.processor.migrate_policy_normalization \
  --pretrained-path <owner/model> \
  --revision <commit-sha> \
  --output-dir <migrated-checkpoint>
```

Validate the local output before publishing it. Publish to a new repository, model version, or branch instead of replacing the legacy artifact.

## ✅ Validate Migrated Artifacts

Confirm the generated files:

```bash
find <migrated-checkpoint> -maxdepth 1 -type f -printf '%f\n' | sort
```

Load the policy and both processors:

```bash
MIGRATED_CHECKPOINT=<migrated-checkpoint> \
uv run --project training/il/lerobot --frozen python - <<'PY'
import os

from lerobot.policies.act.modeling_act import ACTPolicy
from lerobot.processor.pipeline import PolicyProcessorPipeline

checkpoint = os.environ["MIGRATED_CHECKPOINT"]
policy = ACTPolicy.from_pretrained(checkpoint)
preprocessor = PolicyProcessorPipeline.from_pretrained(
    checkpoint,
    "policy_preprocessor.json",
)
postprocessor = PolicyProcessorPipeline.from_pretrained(
    checkpoint,
    "policy_postprocessor.json",
)

print(type(policy).__name__)
print(type(preprocessor).__name__)
print(type(postprocessor).__name__)
PY
```

Run at least one processor-aware inference step with a real observation matching the feature keys and shapes in `config.json`:

```python
processed_observation = preprocessor(observation)

with torch.inference_mode():
    action = policy.select_action(processed_observation)

final_action = postprocessor({"action": action})["action"]
```

Verify the action shape matches `output_features.action.shape` and all values are finite.

## ⚖️ Compare Policy Outputs

Run the same observation through the source checkpoint in its pinned legacy environment and through the migrated checkpoint in the LeRobot 0.6 environment. Compare final unnormalized actions with explicit tolerances:

```python
np.testing.assert_allclose(
    migrated_action,
    legacy_action,
    rtol=1e-4,
    atol=1e-5,
)
```

Investigate material differences before changing tolerances. Torch, CUDA, and convolution implementation changes can introduce small numerical differences.

## 🔥 Validate Warm-Start Training

Use `--policy.path` without `--policy.type`. LeRobot reconstructs the policy type from the migrated `config.json`.

```bash
uv run --project training/il/lerobot --frozen lerobot-train \
  --dataset.repo_id=<dataset-id> \
  --dataset.root=<dataset-root> \
  --dataset.episodes='[0]' \
  --policy.path=<migrated-checkpoint> \
  --policy.device=cuda \
  --policy.push_to_hub=false \
  --steps=1 \
  --batch_size=1 \
  --num_workers=0 \
  --save_freq=1 \
  --log_freq=1 \
  --output_dir=<validation-output> \
  --wandb.enable=false
```

Confirm that training:

* Loads the migrated source weights
* Creates a fresh optimizer and scheduler
* Completes one update with finite loss and gradient norm
* Saves a new checkpoint with all processor artifacts
* Reloads the new checkpoint for processor-aware inference

## 🧾 Record Migration Lineage

Record these values before registering or publishing the migrated checkpoint:

| Field                  | Required value                                             |
|------------------------|------------------------------------------------------------|
| Source identifier      | Local path, Azure ML model version, or Hugging Face SHA    |
| Source artifact hash   | SHA-256 of the original `model.safetensors`                |
| Source LeRobot version | Version used to train the source checkpoint                |
| Target LeRobot version | Version used by the migration environment                  |
| Migrated artifact hash | SHA-256 of the migrated `model.safetensors`                |
| Model-key validation   | Missing and unexpected key counts                          |
| Inference comparison   | Maximum action difference and tolerances                   |
| Warm-start validation  | Training result and generated checkpoint identifier        |
| Dataset identity       | Dataset name, version, and immutable source when available |

Generate hashes with:

```bash
sha256sum <legacy-checkpoint>/model.safetensors
sha256sum <migrated-checkpoint>/model.safetensors
```

Register the migrated output as a new immutable model version. Keep its source identifier and hash in Azure ML tags or equivalent registry metadata.

## 🔧 Troubleshooting

| Symptom                                     | Action                                                                                      |
|---------------------------------------------|---------------------------------------------------------------------------------------------|
| `ProcessorMigrationError`                   | Migrate the checkpoint and load the generated processor artifacts                           |
| No normalization statistics found           | Verify the source model contains legacy normalization tensors and uses a supported policy   |
| Unexpected missing or unexpected model keys | Stop and inspect architecture differences between the source and target LeRobot versions    |
| Processor configuration not found           | Verify both processor JSON files exist in the same directory as `model.safetensors`         |
| Dataset video dependency import fails       | Sync from `training/il/lerobot/uv.lock`; the project requires `lerobot[dataset]`            |
| Warm-start rejects policy arguments         | Remove `--policy.type` when using `--policy.path`                                           |
| Actions differ materially after migration   | Confirm both paths apply equivalent normalization and compare the same unnormalized actions |

## 🔗 Related Documentation

* [LeRobot Training](lerobot-training.md) for Azure ML and OSMO training workflows
* [LeRobot ACT Policy Evaluation](../evaluation/lerobot-evaluation.md) for processor-aware inference
* [Experiment Tracking](experiment-tracking.md) for model registration and lineage
