# Operational Design Domain (ODD) Gap Analysis Component

Docker container component of AVOPS, built from this directory. `AVOPS/scripts/sdg.sh` builds and runs it between curation and generation (see the [AVOPS README](../README.md)); the sections below describe running it on its own.

Standalone Docker container component for **ODD Coverage Analysis & Generative Scene Variation Gap Detection**.

This component ingests raw driving videos (and optional pre-existing annotations), evaluates Operational Design Domain (ODD) completeness and distribution entropy against target thresholds using VLM perception, detects environmental and operational coverage gaps, and produces generative variation specifications alongside comprehensive final gap output reports.

---

## 1. Component Architecture & Flow

```text
                      ┌──────────────────────────────────────────────┐
                      │            Input Directory (/input)          │
                      │  - Video clips (.mp4, .mov, .avi, etc.)      │
                      │  - (Optional) Pre-existing Text_Annotations/ │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │          Phase 1: VLM Perception            │
                      │  - Keyframe extraction (PyAV / OpenCV)       │
                      │  - Batch VLM query (Cosmos-Reason2-2B)       │
                      │  - Fallback / Mock perception support        │
                      └──────────────────────┬───────────────────────┘
                                             │ Saves base annotations
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │        Phase 2: ODD Coverage Engine          │
                      │  - Attribute entropy & shortfall computation │
                      │  - Category-level score aggregation          │
                      │  - Gap detection against target threshold    │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │   Phase 3: Entropy-Delta Variation Solver    │
                      │  - Base candidate video selection            │
                      │  - Variation config synthesis                │
                      │  - Artifact compilation                      │
                      └──────────────────────┬───────────────────────┘
                                             │
                                             ▼
                      ┌──────────────────────────────────────────────┐
                      │           Output Directory (/output)         │
                      │  ├── variation_summary.json                  │
                      │  ├── variations_video_input.json             │
                      │  ├── wfm_generation_input.json               │
                      │  ├── final_gap_output.json                   │
                      │  ├── Text_Annotations/                       │
                      │  └── <scene_id>/<scene_id>_var_...json       │
                      └──────────────────────────────────────────────┘
```

---

## 2. Directory Structure

```text
AVOPS/ODD/
├── Dockerfile                  # Lightweight container definition (Python 3.11-slim + ffmpeg)
├── docker-compose.yml          # Ready-to-run Docker Compose definition
├── entrypoint.sh               # Container execution entrypoint
├── requirements.txt            # Python dependencies (OpenAI, OpenCV, PyAV, PyYAML, etc.)
├── run_odd_pipeline.py         # Main CLI pipeline orchestration entrypoint
├── README.md                   # Complete component documentation
├── configs/
│   ├── odd_coverage_spec.json  # Single ODD specification (categories, weights, expected values)
│   └── qa_template.json        # Visual QA template for perception attributes
└── odd_core/
    ├── __init__.py             # Public API exports
    ├── annotator.py            # Keyframe extractor & OpenAI/VLM batch client
    ├── engine.py               # ODD coverage engine & entropy-delta variation solver
    └── prompt_writer.py        # Variation -> single text paragraph for Cosmos generation models
```

---

## 3. Input & Output Specifications

### Inputs

* **Input Directory (`/input` or `--input-dir`)**:
  * Contains driving video files (`.mp4`, `.mov`, `.avi`, `.mkv`, `.webm`).
  * Can optionally contain an existing `Text_Annotations/` directory with pre-computed scene perception JSONs.
* **Perception Endpoint (Optional)**:
  * OpenAI-compatible multimodal endpoint (e.g., `nvidia/Cosmos-Reason2-2B` on port `8010`).
  * If offline, `--mock-vlm` or auto-fallback generates heuristic perception annotations for offline testing.

### Outputs

The output directory will contain:

| File / Folder                                  | Purpose                                                                                                                                                           |
|------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **`variation_summary.json`**                   | Overall scene counts, threshold, and detected gap counts per attribute.                                                                                           |
| **`variations_video_input.json`**              | Maps each missing ODD condition to suitable candidate base videos.                                                                                                |
| **`wfm_generation_input.json`**                | Master scene synthesis specification passed to video transfer backends.                                                                                           |
| **`final_gap_output.json`**                    | Executive summary containing overall ODD score, category scores, detected gaps, needed variation counts, and recommendations.                                     |
| **`generation_prompts.json`**                  | One text paragraph per (gap value, base video) for text-prompted Cosmos models, with its `source` (`llm` or `template`) and the variation file it was built from. |
| **`<scene_id>/<scene_id>_var_..._prompt.txt`** | The same paragraph as plain text, next to the variation JSON it summarizes.                                                                                       |
| **`Text_Annotations/`**                        | Directory containing base scene perception annotations extracted from videos.                                                                                     |
| **`<scene_id>/` subfolders**                   | Folders for each base scene containing synthesized variation JSON files (e.g. `<scene_id>_var_sceneDetails_weatherType_foggy_run1.json`).                         |

### Generation prompts

Variation JSONs are structured question/answer data, but Cosmos generation models take a
single text prompt. After the variations are written, the module summarizes each one into
one paragraph (50-90 words, ending with a statement that camera viewpoint, road layout and
vehicle motion are preserved):

* **LLM (default when the endpoint is healthy):** the scene's answers and the target change
  are sent to the same OpenAI-compatible endpoint used for perception (`--vlm-url`,
  `--vlm-model`). `<think>` blocks are stripped.
* **Template fallback:** with `--mock-vlm`, an offline endpoint, or an unusable LLM answer
  (empty, too short or long, or cut off mid-reasoning), a deterministic paragraph built from
  the same answers is written instead. Each entry's `source` in `generation_prompts.json`
  tells which was used.

---

## 4. Quick Start: Building & Running

### Step 1: Build the Docker Image

From the repo root:

```bash
docker build -t odd-component:latest -f ODD/Dockerfile ODD
```

*Build time is under 1 minute; image size is ~350MB.*

---

### Step 2: Run the Docker Container

#### Option A: Running with Pre-Existing Dataset / Annotations

If your dataset already has `Text_Annotations/` (for example, a dataset that was annotated earlier):

```bash
docker run --rm \
  -v /path/to/dataset:/input:ro \
  -v /path/to/my_output:/output \
  odd-component:latest \
  --input-dir /input \
  --output-dir /output \
  --threshold 0.8
```

#### Option B: Running with Live Cosmos-Reason2-2B VLM Endpoint

When starting from raw videos without annotations, start the VLM adapter on host port 8010:

```bash
# 1. Start VLM Adapter on host (port 8010), from AVOPS/ODD
uv run odd_core/cosmos_reason_vlm_adapter.py --port 8010 &

# 2. Run ODD container with host network
docker run --rm --network host \
  -v /path/to/videos:/input:ro \
  -v /path/to/output:/output \
  -e VLM_URL="http://localhost:8010/v1" \
  odd-component:latest \
  --input-dir /input \
  --output-dir /output
```

#### Option C: Running in Offline / Simulation Mode (Mock VLM)

To run end-to-end tests without a live VLM or GPU:

```bash
docker run --rm \
  -v /path/to/dataset/physical_ai_videos:/input:ro \
  -v /path/to/my_output:/output \
  odd-component:latest \
  --input-dir /input \
  --output-dir /output \
  --mock-vlm
```

#### Option D: Preserving Host Paths for Downstream Video Transfer

When feeding the generated `wfm_generation_input.json` to Cosmos Transfer NIM on the host, pass `--host-video-dir` to replace `/input` paths with the real host video paths:

```bash
docker run --rm \
  -v /path/to/dataset:/input:ro \
  -v /path/to/my_output:/output \
  odd-component:latest \
  --input-dir /input \
  --output-dir /output \
  --host-video-dir /path/to/dataset/physical_ai_videos
```

#### Option E: Using Docker Compose

Set `ODD_INPUT_DIR` to the dataset directory and `ODD_HOST_VIDEO_DIR` to the host path of its videos; `ODD_OUTPUT_DIR` defaults to `./output`.

```bash
cd AVOPS/ODD
ODD_INPUT_DIR=/path/to/dataset ODD_HOST_VIDEO_DIR=/path/to/dataset/physical_ai_videos docker compose up
```

---

## 5. CLI Arguments & Environment Variables

| CLI Argument              | Environment Variable  | Default                               | Description                                                |
|---------------------------|-----------------------|---------------------------------------|------------------------------------------------------------|
| `--input-dir`, `-i`       | `INPUT_DIR`           | `/input`                              | Directory containing video files / annotations             |
| `--output-dir`, `-o`      | `OUTPUT_DIR`          | `/output`                             | Destination directory for ODD outputs                      |
| `--annotations-dir`, `-a` | `ANNOTATIONS_DIR`     | `None` (auto-detect)                  | Path to existing annotations directory                     |
| `--threshold`, `-t`       | `ODD_SCORE_THRESHOLD` | `0.8`                                 | Minimum ODD score threshold                                |
| `--vlm-url`               | `VLM_URL`             | `http://host.docker.internal:8010/v1` | URL of OpenAI-compatible VLM endpoint                      |
| `--vlm-model`             | `VLM_MODEL`           | `nvidia/Cosmos-Reason2-2B`            | VLM model identifier                                       |
| `--vlm-timeout`           | `VLM_TIMEOUT`         | `120.0`                               | Timeout in seconds for VLM inference                       |
| `--qa-template`           | -                     | `configs/qa_template.json`            | Path to QA template JSON                                   |
| `--spec-file`             | -                     | `configs/odd_coverage_spec.json`      | Path to ODD specification JSON                             |
| `--host-video-dir`        | `HOST_VIDEO_DIR`      | `None`                                | Host video path prefix to write to output configs          |
| `--mock-vlm`              | `MOCK_VLM`            | `False`                               | Force heuristic mock perception for offline testing        |
| `--no-auto-fallback`      | -                     | `False`                               | Fail if VLM endpoint is offline instead of fallback        |
| `--no-generation-prompts` | -                     | `False`                               | Skip writing one text prompt paragraph per scene variation |

---

## 6. Example Final Gap Output (`final_gap_output.json`)

```json
{
    "status": "COMPLETED",
    "dataset_summary": {
        "total_scenes": 20,
        "overall_odd_score": 0.8251,
        "odd_score_threshold": 0.8,
        "overall_status": "GAPS_DETECTED",
        "total_gaps_detected": 2,
        "total_variations_generated": 31
    },
    "category_scores": {
        "sceneDetails": 0.8136,
        "roadDetails": 0.8427,
        "dynamicAgentDetails": 0.8464,
        "otherAgentDetails": 0.7977
    },
    "detected_gaps": [
        {
            "category": "sceneDetails",
            "attribute": "weatherType",
            "odd_score": 0.6933,
            "threshold": 0.8,
            "status": "GAP",
            "needed_variations": {
                "rainy": 4,
                "snowy": 5,
                "foggy": 5,
                "windy": 5,
                "hazy": 4,
                "stormy": 5
            },
            "total_needed": 28,
            "recommendation": "Augment dataset with 28 variation(s) across: rainy, snowy, foggy, windy, hazy, stormy"
        },
        {
            "category": "otherAgentDetails",
            "attribute": "pedestrianDetails",
            "odd_score": 0.7977,
            "threshold": 0.8,
            "status": "GAP",
            "needed_variations": {
                "yes": 3
            },
            "total_needed": 3,
            "recommendation": "Augment dataset with 3 variation(s) across: yes"
        }
    ]
}
```
