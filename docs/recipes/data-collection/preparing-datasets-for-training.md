# Preparing Datasets for Training

Download a dataset from Azure Blob Storage or HuggingFace, inspect its structure, validate format compliance, and connect it to a LeRobot training workflow. By the end of this recipe, you will have a training-ready dataset on your local machine or in a cloud-accessible location.

> [!NOTE]
> This recipe covers dataset preparation. For training with the prepared dataset, continue to [Your First LeRobot Training Job](../training/your-first-lerobot-training-job.md).

## 📋 Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.11+ with `uv` or `pip` |
| Azure CLI | Authenticated (`az login`) — for Azure Blob datasets |
| Azure Storage | Storage account with dataset container — for Azure Blob datasets |
| HuggingFace CLI | `pip install huggingface-hub` — for HuggingFace datasets |

## 🚀 Steps

### Step 1: Choose a dataset source

LeRobot datasets come from two sources:

| Source | When to use | Example |
|--------|-------------|---------|
| HuggingFace Hub | Public community datasets, quick experimentation | `lerobot/aloha_sim_insertion_human` |
| Azure Blob Storage | Private datasets, recorded edge data uploaded to Azure | Custom organization datasets |

### Step 2a: Download from HuggingFace

For public datasets, use the HuggingFace CLI:

```bash
pip install huggingface-hub
huggingface-cli download \
  lerobot/aloha_sim_insertion_human \
  --repo-type dataset \
  --local-dir ./datasets/lerobot/aloha_sim_insertion_human
```

### Step 2b: Download from Azure Blob Storage

For datasets stored in Azure, use the download utility:

```bash
cd training/il/scripts/lerobot
python download_dataset.py \
  --storage-account <your-storage-account> \
  --storage-container datasets \
  --blob-prefix my-dataset/v1 \
  --dataset-root ./datasets \
  --dataset-repo-id my-org/my-dataset
```

The script uses `DefaultAzureCredential` for authentication. It downloads all dataset files, skipping cache and lock files, and preserves the directory structure.

### Step 3: Inspect the dataset structure

A valid LeRobot dataset follows this directory layout:

```text
datasets/lerobot/aloha_sim_insertion_human/
├── meta/
│   └── info.json              # Dataset metadata (features, shapes, fps)
├── data/
│   ├── chunk-000/
│   │   ├── episode_000000.parquet
│   │   ├── episode_000001.parquet
│   │   └── ...
├── videos/                    # Optional video observations
│   └── chunk-000/
│       ├── episode_000000.mp4
│       └── ...
└── stats.json                 # Feature statistics for normalization
```

Verify the structure:

```bash
# Check info.json exists and has expected fields
python -c "
import json
from pathlib import Path

info = json.loads(Path('datasets/lerobot/aloha_sim_insertion_human/meta/info.json').read_text())
print(f'Dataset: {info.get(\"repo_id\", \"unknown\")}')
print(f'Episodes: {info.get(\"total_episodes\", \"unknown\")}')
print(f'Frames: {info.get(\"total_frames\", \"unknown\")}')
print(f'FPS: {info.get(\"fps\", \"unknown\")}')
"
```

### Step 4: Validate episode files

Check that parquet episode files are readable and contain expected columns:

```bash
python -c "
import pyarrow.parquet as pq
from pathlib import Path

data_dir = Path('datasets/lerobot/aloha_sim_insertion_human/data/chunk-000')
episodes = sorted(data_dir.glob('episode_*.parquet'))
print(f'Found {len(episodes)} episode files')

# Inspect the first episode
table = pq.read_table(episodes[0])
print(f'Columns: {table.column_names}')
print(f'Rows: {table.num_rows}')
"
```

### Step 5: Browse with the Dataset Viewer (optional)

Launch the Dataset Analysis Tool for visual episode inspection:

```bash
cd data-management/viewer
./start.sh
```

Open `http://localhost:5173` in a browser. The viewer provides episode browsing, frame-level annotation, trajectory visualization, and data quality metrics.

### Step 6: Connect to training

With the dataset validated, submit a training job using the dataset path or repository ID:

```bash
# From HuggingFace (dataset downloaded on-the-fly by the training container)
cd training/il/scripts
./submit-osmo-lerobot-training.sh -d lerobot/aloha_sim_insertion_human

# From Azure Blob (dataset downloaded at job start)
./submit-osmo-lerobot-training.sh \
  -d my-org/my-dataset \
  --from-blob \
  --storage-account <your-storage-account> \
  --blob-prefix my-dataset/v1
```

See [Your First LeRobot Training Job](../training/your-first-lerobot-training-job.md) for the full training recipe.

## ✅ Verify

The recipe succeeded when:

- Dataset directory contains `meta/info.json` with valid metadata
- Episode parquet files are readable with expected columns
- `info.json` reports expected episode and frame counts
- (Optional) Dataset Viewer displays episodes without errors

## ⚙️ Configuration Reference

`download_dataset.py` parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `--storage-account` | yes | Azure Storage account name |
| `--storage-container` | yes | Blob container name |
| `--blob-prefix` | yes | Blob path prefix for dataset files |
| `--dataset-root` | yes | Local root directory for datasets |
| `--dataset-repo-id` | yes | Dataset identifier (e.g., `user/dataset`) |

## 🔗 Related Recipes

- [Configuring Edge Data Recording](configuring-edge-data-recording.md) — capture your own training data
- [Your First LeRobot Training Job](../training/your-first-lerobot-training-job.md) — train with the prepared dataset
- [End-to-End LeRobot Pipeline](../training/end-to-end-lerobot-pipeline.md) — automated train → evaluate → register

<!-- markdownlint-disable MD036 -->
*🤖 Crafted with precision by ✨Copilot following brilliant human instruction,
then carefully refined by our team of discerning human reviewers.*
<!-- markdownlint-enable MD036 -->
