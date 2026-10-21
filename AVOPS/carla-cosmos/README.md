# CARLA + Cosmos Transfer pipeline

This component generates multi-view driving videos from ODD gaps with CARLA scenarios and
Cosmos Transfer 2.5. It is adapted from the separate `carla-cosmos` project. CARLA and
Cosmos run as separate containers:

- CARLA is based on the official `carlasim/carla:0.9.15` registry image and
  extended with the version-matched official AdditionalMaps archive required
  by the source scenarios.
- Cosmos Transfer 2.5 is cloned from NVIDIA's
  [official source repository](https://github.com/nvidia-cosmos/cosmos-transfer2.5)
  and built with its upstream Dockerfile. Its source is not vendored here.
- The pipeline image layers the CARLA capture/control code, the task runner, the scenarios and
  prompts, and ScenarioRunner v0.9.15 on top of the built Cosmos image.
- CARLA's Python client and ScenarioRunner dependencies use an isolated
  Python 3.10 environment; the current Cosmos source environment remains
  independent.

## Requirements

- Linux with Docker Engine and Docker Compose v2
- NVIDIA Container Toolkit and a compatible NVIDIA driver
- `git` and `git-lfs` for the upstream Cosmos build
- A Hugging Face read token authorized to download the Cosmos Transfer 2.5
  checkpoint
- Sufficient GPU memory for the configured Cosmos multi-view inference. The
  source workflow is configured for up to eight GPUs.
- The official CARLA image extension downloads the version-matched
  AdditionalMaps archive during its first build.

The first CARLA build also pulls the `carlasim/carla:0.9.15` image (about 8.9 GB
compressed) and downloads the AdditionalMaps archive (about 7.4 GB compressed)
before extracting it into the image. Cosmos Transfer 2.5's source checkout and
Git LFS files add variable storage use. On first inference, the `hf-cache`
Docker volume downloads the gated `Cosmos-Transfer2.5-2B` and
`Cosmos-Predict2.5-2B` checkpoints (about 4.8 GB each). Reserve at least 50 GB
free for known downloads, extracted assets, and build overhead; allow more for
the source/LFS checkout, Docker layers and cache, and generated videos.

Before building, accept the access terms on the Hugging Face pages for
[`Cosmos-Transfer2.5-2B`](https://huggingface.co/nvidia/Cosmos-Transfer2.5-2B)
and [`Cosmos-Predict2.5-2B`](https://huggingface.co/nvidia/Cosmos-Predict2.5-2B).
Set `HF_TOKEN` in `.env` for an account with access. If guardrails are enabled,
also accept the terms for
[`Cosmos-Guardrail1`](https://huggingface.co/nvidia/Cosmos-Guardrail1); its
download size depends on the model revision and configuration. See the [AVOPS
download and disk guide](../README.md#downloads-model-access-and-disk) for the
combined first-run estimate and the ODD VLM model requirements.

## Layout

`src/` is installed as the `carla_cosmos` package in the pipeline image.

| Path                                          | Contents                                                                                                         |
|-----------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| `src/pipeline/`                               | `run_tasks.py` (entry point), `task_plan.py` (ODD gaps to tasks), `task_stages.py` (one task)                    |
| `src/tasks/`                                  | The stages: `run_carla_capture.py`, `prepare_cosmos_inputs.py`, `run_cosmos_inference.py`, and `smoke_checks.py` |
| `src/utils/carla/`                            | CARLA capture implementation and the camera rig                                                                  |
| `src/utils/cosmos/`                           | Cosmos annotation, parquet, patch, multi-view spec, launcher, and GPU planning helpers                           |
| `src/utils/pipeline_io.py`                    | Capture bundle paths and JSON helpers shared by the stages                                                       |
| `scenario_map.json`, `scenarios/`, `prompts/` | ODD gap to CARLA scenario and prompt mapping, described below                                                    |

## Scenarios and prompts

[scenario_map.json](./scenario_map.json) maps each ODD gap (`attribute` and `value`) to a CARLA
scenario file in [scenarios/](./scenarios), a prompt file in [prompts/](./prompts) with one
caption per line, and the CARLA parameters for the run (town, vehicles, ego speed). Gaps
without an entry, such as `weatherType`, are skipped. Add an entry and its files to support a
new gap.

## Build and run

Notes from building against upstream Cosmos Transfer 2.5 `2ff49d0` (June 2026):

- `build-cosmos-image.sh` pins the Cosmos Python environment to 3.10 (`COSMOS_PYTHON_VERSION`)
  because upstream's own 3.13 pin cannot install its locked `flash-attn` wheels.
- The pipeline image also adds `libegl1` (headless control-video rendering), `setuptools<81`
  (ScenarioRunner needs `pkg_resources`), and CARLA's `agents` package (from the 0.9.15 tag).
- Multi-view inference downloads gated checkpoints (`nvidia/Cosmos-Transfer2.5-2B`,
  `nvidia/Cosmos-Predict2.5-2B`); the Hugging Face account behind `HF_TOKEN` must have access.

From this directory:

```bash
cp .env.example .env
# Set HF_TOKEN in .env, then build the upstream Cosmos image and pipeline image.
./build-cosmos-image.sh

# Run an ODD wfm_generation_input.json placed under data/; CARLA is stopped on exit.
python3 run_pipeline.py --input /workspace/data/odd_input/wfm_generation_input.json
```

The runner builds the images automatically if they are missing. To explicitly
pull and rebuild Cosmos from the selected upstream ref:

```bash
COSMOS_REF=main python3 run_pipeline.py --rebuild-cosmos --input <path>
```

`data/` is mounted at `/workspace/data` in the pipeline container, so put the input there and
pass its container path. `sdg.sh carla` does this for you from an ODD output directory.
Other options, such as `--views`, `--seed`, `--seconds`, `--num-steps` and `--guidance`, are
passed to [run_tasks.py](./src/pipeline/run_tasks.py).

Each run is written to `data/runs/<run-id>/`: `tasks.json` indexes the tasks, and
`tasks/<task>/` holds `multiview_grid.mp4`, `caption.txt`, `metadata.json`,
`carla_simulation/` and `cosmos_synthesized/`. Passing the same `--run-dir` again skips the
finished tasks. Use `--keep-services` to leave CARLA running afterwards. The containers write
as root.

## Views and GPUs

The default rig has seven cameras. Cosmos Transfer 2.5 multi-view encodes one view
per GPU rank, so the rank count must be at least the view count, and its attention
splits the 2B model's 16 heads across ranks, so the rank count must divide 16. In
practice that means 4 GPUs for up to four views and 8 GPUs for five to eight
views (skipping the narrow-FOV `front_tele` camera still needs 8). To use four
GPUs, select four views:

```bash
CARLA_COSMOS_VIEWS=front_wide,cross_left,cross_right,rear \
COSMOS_GPU_IDS=0,1,2,3 CARLA_GPU_ID=4 python3 run_pipeline.py
```

`CARLA_COSMOS_VIEWS` (comma separated, or `all`) selects the views and
`COSMOS_GPU_IDS` the CUDA devices for Cosmos; both are passed into the pipeline
container by `compose.yaml` (see `.env.example`). The context-parallel size is
chosen from the view count (1, 2, 4, or 8), and the GPU list must have at least
that many entries. A six-view run used about 42 GB per GPU.

`AVOPS/scripts/sdg.sh --backend carla pipeline` drives this pipeline from
ODD gaps; see the [AVOPS README](../README.md).

## Workflow

`run_tasks.py` plans one task per required ODD gap variation. For each task, in order:

1. Picks the scenario, CARLA parameters and a prompt from `scenario_map.json`.
2. Connects to the official CARLA container at `carla:2000`, runs the
   synchronous scenario, and captures the selected calibrated camera streams
   (seven by default), per-frame annotations, and map geometry.
3. Converts capture annotations into Cosmos control videos and a multi-view
   runtime specification.
4. Runs Cosmos Transfer 2.5 multi-view inference with `torchrun`.
5. Trims and assembles synchronized CARLA and generated video grids, and saves
   task metadata and captions.

A failed task is recorded in `tasks.json` and the remaining tasks still run. CARLA's RPC and
streaming ports are published as 2000 and 2001. The CARLA image build downloads and
imports the official 0.9.15 AdditionalMaps package so the mapped Town04,
Town07, and Town10HD scenarios are available.

The source implementation is adapted from the separate `carla-cosmos`
workspace. NVIDIA Cosmos and CARLA ScenarioRunner source are fetched from their
official upstream repositories at image-build time; their licenses and
upstream notices remain with those distributions.
