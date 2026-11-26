# OSMO Closed-Loop AVOps Workflows

Zero-NGC, Airflow-free OSMO 6.x workflows for closed-loop autonomous vehicle synthetic data generation on Azure AKS using `nvidia/Cosmos-Reason2-2B` and `nvidia/Cosmos3-Nano-FP8` served by public `vllm/vllm-omni:cosmos3`.

## 📋 Prerequisites

| Requirement             | Purpose                                                                                                                                            |
|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| AKS GPU pools           | `gpu_platform` from `infrastructure/setup/values/osmo-platforms.yaml` (override `a10_platform` / `h100_platform` for dedicated A10 and H100 pools) |
| NVIDIA OSMO 6.x         | Workflow orchestration, `huggingface` credential injection, and dataset handoff                                                                    |
| `HF_TOKEN`              | Hugging Face access to `nvidia/Cosmos-Reason2-2B` and `nvidia/Cosmos3-Nano-FP8`                                                                    |
| Public container images | `vllm/vllm-omni:cosmos3@sha256:86fb713bf73e118e537a55fa013dcdbdf2468ed61b98f4f2e7934a9482181dc1` and locally built `odd-component:latest`          |

## 🏗️ Closed-Loop Stages

Stages 2 and 4 run as two-task OSMO groups (`vlm-server` serving `nvidia/Cosmos-Reason2-2B` and a `lead: true` `odd-analysis` task running `AVOPS/ODD`). Stage 3 runs `vllm/vllm-omni:cosmos3` serving `nvidia/Cosmos3-Nano-FP8` and generates missing weather and time-of-day clips.

| Stage | Workflow File                | Platform Default | Action                                                                           |
|-------|------------------------------|------------------|----------------------------------------------------------------------------------|
| 1     | `avops-closed-loop.yaml`     | `default`        | Probe baseline driving clips and write `clip_manifest.json`                      |
| 2     | `cosmos-reason2-odd.yaml`    | `gpu_platform`   | Run 2-task group (`vlm-server` + `odd-analysis`) for baseline ODD score and gaps |
| 3     | `cosmos3-nano-generate.yaml` | `gpu_platform`   | Synthesize missing weather and time-of-day clips with `nvidia/Cosmos3-Nano-FP8`  |
| 4     | `avops-closed-loop.yaml`     | `gpu_platform`   | Re-evaluate augmented clips in a 2-task group and compute Before-to-After delta  |

## 🚀 Quick Start

Copy the configuration template to a Git-ignored local file:

```bash
cp AVOPS/configs/closed-loop.env.example AVOPS/configs/closed-loop.env
```

Preview resolved settings or dry-run the four stages:

```bash
AVOPS/scripts/avops-closed-loop.sh --config-preview
AVOPS/scripts/avops-closed-loop.sh --dry-run -i ./clips -o ./work run-all
```

Run stages against port-forwarded endpoints or submit the unified workflow to OSMO:

```bash
# Run stages 1-4 against live endpoints
AVOPS/scripts/avops-closed-loop.sh -i ./clips -o ./work run-all

# Submit the unified closed-loop workflow via the wrapper script
AVOPS/scripts/avops-closed-loop.sh osmo-submit

# Or submit directly with osmo CLI and explicit platform overrides
osmo workflow submit AVOPS/workflows/osmo/avops-closed-loop.yaml \
  --pool default \
  --set-string odd_image=<acr>.azurecr.io/avops/odd-component:latest
```

## ⚙️ Parameters

Override any default parameter via `--set-string key=value` when submitting to OSMO:

| Parameter                    | Default                                                                                          | Description                                                                       |
|------------------------------|--------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------|
| `cpu_platform`               | `default`                                                                                        | OSMO CPU platform for Stage 1 and `odd-analysis` client tasks                     |
| `a10_platform`               | `gpu_platform`                                                                                   | OSMO GPU platform for `vlm-server` (override for a dedicated A10 pool)            |
| `h100_platform`              | `gpu_platform`                                                                                   | OSMO GPU platform for Stage 3 synthesis (override for a dedicated H100 pool)      |
| `odd_image`                  | `odd-component:latest`                                                                           | Locally built `AVOPS/ODD` container image (push to ACR for AKS execution)         |
| `vlm_image`                  | `vllm/vllm-omni:cosmos3@sha256:86fb713bf73e118e537a55fa013dcdbdf2468ed61b98f4f2e7934a9482181dc1` | Digest-pinned public vLLM-Omni image serving `Cosmos-Reason2-2B`                  |
| `cosmos3_image`              | `vllm/vllm-omni:cosmos3@sha256:86fb713bf73e118e537a55fa013dcdbdf2468ed61b98f4f2e7934a9482181dc1` | Digest-pinned public vLLM-Omni image serving `Cosmos3-Nano-FP8`                   |
| `vlm_model`                  | `nvidia/Cosmos-Reason2-2B`                                                                       | Vision-language model for ODD perception and verification                         |
| `vlm_revision`               | `9ce19a195e423419c349abfc86fd07178b230561`                                                       | Pinned Hugging Face commit SHA for `vlm_model`                                    |
| `vlm_max_model_len`          | `8192`                                                                                           | Maximum context length passed to `vllm serve`                                     |
| `vlm_extra_args`             | `""`                                                                                             | Optional whitespace-separated extra flags for `vllm serve`                        |
| `vlm_input`                  | `video`                                                                                          | ODD VLM input modality (`video` clip or first `frame`)                            |
| `odd_threshold`              | `0.8`                                                                                            | Target ODD coverage threshold                                                     |
| `cosmos3_model`              | `nvidia/Cosmos3-Nano-FP8`                                                                        | FP8 Cosmos 3 video generation model                                               |
| `cosmos3_revision`           | `9d45b0910219989a76c56aae82a08007bc1b597e`                                                       | Pinned Hugging Face commit SHA for `cosmos3_model`                                |
| `max_clips`                  | `6`                                                                                              | Maximum synthetic clips generated per loop                                        |
| `video_size`                 | `854x480`                                                                                        | Generated video resolution (`WxH`)                                                |
| `num_frames`                 | `97`                                                                                             | Frame count per synthetic clip                                                    |
| `fps`                        | `24`                                                                                             | Frame rate per synthetic clip                                                     |
| `steps`                      | `35`                                                                                             | Diffusion inference steps per clip                                                |
| `generation_mode`            | `auto`                                                                                           | Synthesis mode (`auto` switches extreme shifts to `t2v`, or explicit `i2v`/`t2v`) |
| `use_huggingface_credential` | `true`                                                                                           | Injects OSMO `huggingface` credential (`HF_TOKEN: hf_token`) when `"true"`        |

## 🔐 Security

Store `HF_TOKEN` in an OSMO `huggingface` credential (`hf_token` key) rather than workflow YAML or tracked files. Keep `AVOPS/configs/closed-loop.env` out of version control. No `NGC_API_KEY` or `nvcr.io` access is required.
