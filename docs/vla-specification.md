# Vision-Language-Action Models: Integration Specification

Specification for integrating Vision-Language-Action (VLA) models into the training, inference, and orchestration infrastructure. This document covers SOTA VLA model selection, architecture patterns, training and inference integration, data pipeline extensions, workflow templates, and a phased implementation roadmap.

## 📋 Current Training Capabilities

The repository supports reinforcement learning and imitation learning across two orchestration platforms (OSMO, AzureML) with MLflow experiment tracking, checkpoint management, and GPU-accelerated Isaac Lab environments.

### Training Frameworks

| Framework | Approach | Algorithms            | Key Files                                                       | MLflow Integration                                     |
| --------- | -------- | --------------------- | --------------------------------------------------------------- | ------------------------------------------------------ |
| SKRL      | RL       | PPO, IPPO, MAPPO, AMP | `src/training/scripts/skrl_training.py`, `skrl_mlflow_agent.py` | Monkey-patches `agent._update` for metric interception |
| RSL-RL    | RL       | PPO, Distillation     | `src/training/scripts/rsl_rl/train.py`, `play.py`               | Wraps `log` and `save` for MLflow streaming            |
| LeRobot   | IL       | ACT, Diffusion Policy | `src/training/scripts/lerobot/train.py`, `checkpoints.py`       | Subprocess stdout parsing for real-time metrics        |

Both RL frameworks run on Isaac Lab with Hydra configuration and support distributed training. LeRobot wraps `lerobot-train` as a subprocess with regex-based metric extraction.

### Inference Tracks

| Track            | Implementation                                | Control Frequency       | Deployment       |
| ---------------- | --------------------------------------------- | ----------------------- | ---------------- |
| ACT (IL)         | `PolicyRunner` + ROS2 `act_inference_node.py` | 30 Hz on UR10E 6-DOF    | Edge (ROS2 node) |
| RL (SKRL/RSL-RL) | JIT/ONNX export via `export_policy.py`        | Isaac Sim internal rate | Sim playback     |

The `PolicyRunner` class in `src/inference/policy_runner.py` handles model loading from HuggingFace, observation preprocessing, action postprocessing, and action-chunk queue management. The ROS2 node subscribes to `/joint_states` and `/camera/color/image_raw`, publishing `JointTrajectory` commands at 30 Hz.

### Data Pipeline

| Component  | Implementation                                                                            | Format                              |
| ---------- | ----------------------------------------------------------------------------------------- | ----------------------------------- |
| Recording  | ROS2 topics (`/joint_states` 100 Hz, `/camera/color/image_raw` 30 Hz, `/imu/data` 200 Hz) | ROS2 bags                           |
| Conversion | `convert_hdf5_to_lerobot.py`                                                              | HDF5 → LeRobot v3.0 (parquet + MP4) |
| Download   | `download_dataset.py` with Azure Blob + v3.0→v2.1 patching                                | LeRobot v3.0                        |
| Schema     | `config/recording_config.yaml` with Pydantic validation (`config_models.py`)              | YAML + JSON Schema                  |

### Policy Evaluation

`policy_evaluation.py` provides cross-framework evaluation with auto task/framework detection, parallel vectorized environments, and success threshold gating. Supports both SKRL and RSL-RL agent loading.

### VLA Status

Zero VLA code exists. The architecture roadmap allocates `training/vla/` and the warm-start strategies document (`docs/il-to-rl-warm-start-strategies.md`) details VLA-RL as Strategy 7, but no implementation exists.

## 🔬 SOTA VLA Models

### Tier 1: Primary Candidates

These models have the strongest alignment with this repository's LeRobot data pipeline, OSMO/AzureML orchestration, and GPU infrastructure.

#### pi0 / pi0.5 via OpenPI (Physical Intelligence)

Single transformer with two sets of expert weights: a PaliGemma 3B VLM backbone for vision-language processing and a 300M parameter flow-matching network for continuous action prediction. Flow matching produces continuous actions at up to 50 Hz without discrete tokenization bottlenecks.

| Property              | Value                                        |
| --------------------- | -------------------------------------------- |
| Parameters            | 3.3B (3B VLM + 300M action expert)           |
| Action type           | Continuous via flow matching                 |
| Inference speed       | Up to 50 Hz                                  |
| Training data         | Cross-embodiment (8+ robots) + OpenX         |
| Fine-tuning           | 1–10h task-specific data; LoRA on single GPU |
| LeRobot compatibility | Native (OpenPI consumes LeRobot format)      |
| License               | Open-source                                  |
| Repository            | `Physical-Intelligence/openpi`               |

pi0.5 adds "Knowledge Insulation" via stop gradient between VLM backbone and action expert, preventing catastrophic forgetting during co-training on heterogeneous data sources. Released via OpenPI as an upgraded pi0 checkpoint.

#### SmolVLA (HuggingFace)

Compact 450M parameter VLA with a small VLM backbone and 100M parameter action expert transformer. Trained exclusively on LeRobot community datasets (<30k episodes). Asynchronous inference pipelines execution and inference for 2x throughput over synchronous approaches.

| Property              | Value                                |
| --------------------- | ------------------------------------ |
| Parameters            | 450M (350M VLM + 100M action expert) |
| Action type           | Transformer action expert            |
| Inference speed       | >50 Hz (async)                       |
| Training data         | LeRobot community datasets           |
| Fine-tuning           | Single consumer GPU                  |
| LeRobot compatibility | Native (built for LeRobot format)    |
| License               | Open-source                          |
| Repository            | `huggingface/lerobot` (SmolVLA-base) |

Pre-training on community datasets raises SO-100 success from 51.7% → 78.3%. Complete training, deployment, and hardware instructions are open-sourced.

#### OpenVLA-OFT (UC Berkeley)

7B parameter VLA built on Prismatic-7B (fused DINOv2 + SigLIP vision encoders + LLaMA 2 7B). The OFT (Optimized Fine-Tuning) recipe adds FiLM conditioning for language grounding and FAST tokenization for efficient action decoding.

| Property              | Value                                            |
| --------------------- | ------------------------------------------------ |
| Parameters            | 7B                                               |
| Action type           | Discrete tokens (FAST DCT tokenization)          |
| Inference speed       | 5–10 Hz (quantized)                              |
| Training data         | ~970k episodes from Open X-Embodiment            |
| Fine-tuning           | LoRA rank 32 with 4-bit QLoRA on single RTX 3090 |
| LeRobot compatibility | Requires RLDS conversion                         |
| License               | MIT                                              |
| Repository            | `openvla/openvla` (5.5k stars)                   |

Achieves 97.1% LIBERO success rate, outperforming pi0, MDT, Seer, DiT Policy, Octo, and Diffusion Policy on the same benchmark.

### Tier 2: Domain-Specific Options

| Model                       | Parameters | Architecture                                                  | Key Innovation                                                         | Best For                          |
| --------------------------- | ---------- | ------------------------------------------------------------- | ---------------------------------------------------------------------- | --------------------------------- |
| CogACT (Microsoft Research) | 7B         | DINOv2 + SigLIP → LLaMA-2 → Diffusion Transformer action head | Adaptive Action Ensemble (AAE) inference strategy                      | Multi-modal action prediction     |
| RDT-1B (Tsinghua)           | 1.2B       | SigLIP + T5 → Diffusion Transformer with PIUAS                | Physically Interpretable Unified Action Space for heterogeneous robots | Multi-embodiment fleets           |
| Cosmos Policy (NVIDIA)      | Large      | Fine-tunes Cosmos Predict-2 video foundation model            | Action chunks + value function injected into video latent tokens       | World model + control             |
| GR00T N1.5 (NVIDIA)         | VLA        | Co-training on web + synthetic Isaac Sim + real robot data    | Isaac Sim co-training, TensorRT deployment on Jetson                   | Humanoid robots, NVIDIA ecosystem |

### Comprehensive Comparison

| Model         | Parameters | Action Type                | Inference Speed | LeRobot Compatible | License  | Key Innovation                         |
| ------------- | ---------- | -------------------------- | --------------- | ------------------ | -------- | -------------------------------------- |
| pi0/pi0.5     | 3.3B       | Flow matching (continuous) | Up to 50 Hz     | Native             | Open     | Dual-expert with knowledge insulation  |
| SmolVLA       | 450M       | Transformer action expert  | >50 Hz (async)  | Native             | Open     | Consumer GPU training, async inference |
| OpenVLA-OFT   | 7B         | Discrete (FAST tokens)     | 5–10 Hz         | RLDS conversion    | MIT      | 97.1% LIBERO, FiLM conditioning        |
| CogACT        | 7B         | Diffusion Transformer      | ~10 Hz          | RLDS conversion    | Open     | AAE inference, componentized modules   |
| RDT-1B        | 1.2B       | Diffusion Transformer      | 5–10 Hz         | Custom conversion  | MIT      | Unified action space (PIUAS)           |
| Cosmos Policy | Large      | Video prediction + action  | Varies          | Custom             | Research | 98.5% LIBERO SOTA, video foundation    |
| GR00T N1.5    | VLA        | VLA tokens                 | Real-time       | Custom             | Open     | Isaac Sim co-training                  |
| DeeR-VLA      | Variable   | Dynamic early-exit         | Adaptive        | Custom             | Open     | Adjusts model size per situation       |
| Octo          | 93M        | Diffusion                  | Moderate        | RLDS               | Open     | Flexible observations and actions      |

## 🏗️ VLA Architecture Patterns

### Vision Encoders

| Encoder                  | Used By                 | Strengths                                    | Pretraining            |
| ------------------------ | ----------------------- | -------------------------------------------- | ---------------------- |
| SigLIP                   | OpenVLA, CogACT, RDT-1B | Semantic understanding, web-scale knowledge  | Contrastive image-text |
| DINOv2                   | OpenVLA, CogACT         | Self-supervised spatial features             | Self-supervised visual |
| SigLIP + DINOv2 (fused)  | OpenVLA, CogACT         | Best of both: semantic + spatial             | Dual encoder fusion    |
| PaliGemma vision         | pi0, pi0.5              | Integrated VLM approach                      | VLM pretraining        |
| Cosmos video encoder     | Cosmos Policy           | Temporal and video understanding             | Video foundation model |
| Spatial Foundation Model | SpatialVLA / Evo-0      | 3D geometry from 2D images (no depth sensor) | Visual geometry model  |

Dual-encoder fusion (semantic + geometric) is the dominant pattern. SigLIP provides semantic grounding while DINOv2 provides spatial features for precise manipulation.

### Language Backbones

| Model       | Size               | Used By         | Trade-off                                 |
| ----------- | ------------------ | --------------- | ----------------------------------------- |
| LLaMA 2     | 7B                 | OpenVLA, CogACT | Strong reasoning, high compute            |
| PaliGemma   | 3B                 | pi0, pi0.5      | Good balance of capability and efficiency |
| SmolLM      | ~350M              | SmolVLA         | Minimal compute, edge-friendly            |
| T5-v1_1-xxl | 11B (encoder only) | RDT-1B          | Strong encoding, no generation            |

Smaller VLM backbones (3B) with separate action experts outperform monolithic 7B+ VLAs on many benchmarks while requiring significantly less compute.

### Action Head Designs

| Design                              | Models               | Pros                                              | Cons                                                       |
| ----------------------------------- | -------------------- | ------------------------------------------------- | ---------------------------------------------------------- |
| Autoregressive (discrete bins)      | OpenVLA, RT-2        | Leverages LLM architecture directly               | Low resolution for dexterous tasks, slow at high frequency |
| Diffusion Transformer (DiT)         | CogACT, RDT-1B, Octo | Multi-modal distributions, continuous, expressive | Multiple denoising steps increase latency                  |
| Flow matching                       | pi0, pi0.5           | Fast inference, continuous actions, multi-modal   | Requires separate action expert network                    |
| MLP                                 | Simple baselines     | Fast, simple                                      | Limited expressiveness                                     |
| Video prediction + action injection | Cosmos Policy        | World model capability, temporal reasoning        | Large compute requirements                                 |

### Action Tokenization Strategies

| Strategy                         | Models                | Description                                       | Token Compression                                       |
| -------------------------------- | --------------------- | ------------------------------------------------- | ------------------------------------------------------- |
| Per-dimension binning (256 bins) | OpenVLA, RT-2         | Each action dimension discretized independently   | None (1 token per DOF per step)                         |
| FAST (DCT-based)                 | pi0-FAST, OpenVLA-OFT | Frequency-space compression of action chunks      | 1.75–13.2x (700 → 53 tokens for shirt-folding at 50 Hz) |
| FAST+                            | Universal tokenizer   | Trained on 1M real robot action trajectories      | Plug-and-play for any embodiment                        |
| FSQ (Finite Scalar Quantization) | Experimental          | Learned quantized representation of action chunks | Variable                                                |
| Continuous (flow matching)       | pi0, pi0.5            | Direct continuous prediction, no tokenization     | N/A                                                     |

FAST tokenization is a breakthrough from ICLR 2026: DCT-based compression reduces token count dramatically, enabling autoregressive VLAs to match diffusion VLA performance while training 5x faster.

### Multi-Modal Fusion Approaches

| Approach              | Models        | Mechanism                                                       |
| --------------------- | ------------- | --------------------------------------------------------------- |
| Early fusion          | OpenVLA, RT-2 | Vision + language tokens concatenated into single sequence      |
| Dual-expert           | pi0.5         | Separate VLM backbone + action expert with stop gradient        |
| Componentized modules | CogACT        | Vision → Language → Action pipeline with conditional generation |
| Cross-attention       | RDT-1B        | Action model cross-attends to VLM features                      |
| Latent injection      | Cosmos Policy | Actions injected into video latent space                        |

## 🎯 Recommended VLA Architecture

### Three-Tier Selection

| Tier                 | Model                  | Use Case                             | Rationale                                                     |
| -------------------- | ---------------------- | ------------------------------------ | ------------------------------------------------------------- |
| 1 (Prototype / Edge) | SmolVLA                | Rapid validation, edge deployment    | Zero data conversion, single GPU, fastest path to working VLA |
| 2 (Production)       | pi0 / pi0.5 via OpenPI | Production manipulation at scale     | 50 Hz flow matching, LeRobot compatible, LoRA fine-tuning     |
| 3 (Advanced)         | OpenVLA-OFT + VLA-RL   | Strongest benchmarks, RL fine-tuning | 97.1% LIBERO, PPO fine-tuning, RPRM reward densification      |

### Tier 1: SmolVLA — Prototype and Edge

SmolVLA provides the fastest path to a working VLA with existing infrastructure:

- 450M parameters — trains and runs on a single consumer GPU
- Native LeRobot format means zero data conversion from current `datasets/` structure
- Asynchronous inference achieves >50 Hz on consumer hardware
- Suitable for edge deployment on Jetson platforms
- Pre-trained checkpoint available on HuggingFace, fine-tune with existing LeRobot datasets

### Tier 2: pi0 / pi0.5 — Production

OpenPI provides production-grade manipulation performance:

- Flow matching generates continuous actions at up to 50 Hz, meeting real-time control requirements
- LeRobot format support for fine-tuning data means current datasets work directly
- LoRA fine-tuning on a single GPU; full fine-tuning on 4–8x GPUs via FSDP
- Knowledge Insulation (pi0.5) prevents catastrophic forgetting during multi-task co-training
- 1–10 hours of task-specific data sufficient for fine-tuning

### Tier 3: OpenVLA-OFT + VLA-RL — Advanced

OpenVLA-OFT + VLA-RL enables the most advanced capabilities:

- FAST tokenization reduces action tokens from 700 → 53, enabling efficient autoregressive decoding
- VLA-RL fine-tuning with PPO improves over SFT by 4.5% on 40 LIBERO tasks
- RPRM reward densification eliminates the need for hand-crafted dense reward functions
- Curriculum selection prioritizes tasks at ~50% success rate: $P(\text{task}_j) \propto \exp((0.5 - s_j) / \tau)$
- Requires RLDS data conversion from LeRobot format and Ray + FSDP infrastructure

### Rejected Alternatives

| Model         | Reason for Rejection                                                     |
| ------------- | ------------------------------------------------------------------------ |
| CogACT        | Microsoft Research but not production-ready; requires RLDS conversion    |
| RDT-1B        | Best for heterogeneous fleets but current scope is UR10E + Hexagarm only |
| Cosmos Policy | Requires video foundation model infrastructure not yet deployed          |
| GR00T N1.5    | Humanoid-focused, not manipulation-focused                               |
| Octo          | Superseded by pi0 family and SmolVLA on same benchmarks                  |

## ⚙️ Training Integration

### Directory Structure

VLA training follows the planned `training/vla/` directory allocation from the architecture roadmap:

```text
src/training/scripts/vla/
├── train.py                           # VLA supervised fine-tuning orchestrator
├── train_rl.py                        # VLA-RL (PPO + RPRM) training orchestrator
├── bootstrap.py                       # Azure ML + HuggingFace auth bootstrap
├── checkpoints.py                     # LoRA adapter checkpoint management
├── reward_model.py                    # RPRM training and inference
└── utils/
    ├── tokenizer.py                   # FAST action tokenizer wrapper
    └── curriculum.py                  # Task selection by success rate
```

This mirrors the existing `src/training/scripts/lerobot/` pattern: subprocess-based training with stdout parsing for MLflow metrics, checkpoint upload, and AzureML model registration.

### Training Orchestrator Architecture

| Model       | Orchestration Pattern                        | Training API                                        |
| ----------- | -------------------------------------------- | --------------------------------------------------- |
| SmolVLA     | Native Python API (`transformers` + LeRobot) | `SmolVLA.from_pretrained()` + custom training loop  |
| pi0 / pi0.5 | OpenPI native API                            | OpenPI fine-tuning scripts with LeRobot data loader |
| OpenVLA-OFT | HuggingFace `transformers`                   | `AutoModelForVision2Seq` + LoRA via PEFT            |

All three patterns use the same MLflow wrapper approach as LeRobot: bootstrap Azure ML connection, run training, parse metrics, upload checkpoints periodically, register final model.

### MLflow Metrics

VLA training extends the existing metric categories:

| Category         | Metrics                                                                 | Source               |
| ---------------- | ----------------------------------------------------------------------- | -------------------- |
| Training loss    | Token-level cross-entropy, flow matching loss, action MSE               | Training loop        |
| Action accuracy  | Per-dimension accuracy, chunk accuracy, FAST token accuracy             | Validation           |
| Reward model     | RPRM score, progress prediction accuracy, milestone detection F1        | RPRM inference       |
| RL (VLA-RL only) | PPO surrogate loss, value loss, KL divergence, entropy, GAE returns     | PPO training loop    |
| System           | CPU/GPU utilization, memory, power, disk (via `SystemMetricsCollector`) | `psutil` + `pynvml`  |
| Episode          | Success rate, episode length, cumulative reward                         | Environment rollouts |

### Checkpoint Management

| Component               | Storage                                   | Versioning                                     |
| ----------------------- | ----------------------------------------- | ---------------------------------------------- |
| Base model weights      | HuggingFace Hub (read-only reference)     | Model card version tag                         |
| LoRA adapters           | AzureML model registry + MLflow artifacts | Step-indexed, tagged with base model reference |
| Full fine-tuned weights | Azure Blob Storage (large files)          | AzureML model registry with `uri_folder`       |
| RPRM weights            | AzureML model registry                    | Tagged with training data hash                 |

LoRA adapter registration uses the existing `_register_model_via_aml()` pattern from `src/training/scripts/lerobot/checkpoints.py`, tagging models with `framework=vla`, `policy_type`, `base_model`, and `lora_rank`.

### VLA-RL Pipeline

VLA-RL fine-tuning follows the architecture documented in `docs/il-to-rl-warm-start-strategies.md` (Strategy 7):

| Stage                | Description                                                               | Compute                          |
| -------------------- | ------------------------------------------------------------------------- | -------------------------------- |
| SFT pre-training     | Fine-tune base VLA on task demonstrations via LoRA                        | 1x GPU (LoRA) or 4–8x GPU (full) |
| Critic warmup        | Train value model on frozen SFT policy rollouts for several iterations    | 1x GPU                           |
| Joint PPO training   | Alternate rollouts and PPO updates on actor (VLA) + critic                | 4–8x GPU via Ray + FSDP          |
| RPRM training        | Fine-tune frozen VLM on pseudo-reward labels from successful trajectories | 1x GPU                           |
| Curriculum selection | Prioritize tasks at ~50% success rate boundary                            | CPU (scheduling)                 |

Critic warmup is essential: without it, success rate drops from 90.2% to 80.0% due to inaccurate early value estimates destabilizing policy gradients.

### Dependencies and Container Requirements

```text
# VLA core dependencies (add to existing training container)
transformers>=4.40.0
peft>=0.10.0              # LoRA/QLoRA adapters
flash-attn>=2.5.0         # Flash Attention 2
accelerate>=0.28.0        # FSDP/DDP orchestration
bitsandbytes>=0.43.0      # 4-bit quantization for QLoRA
vllm>=0.4.0               # Inference serving (VLA-RL rollouts)
lerobot>=0.3.0            # Data loading (SmolVLA, pi0)
openpi                    # pi0/pi0.5 fine-tuning (pip install from source)

# VLA-RL additional dependencies
ray[default]>=2.10.0      # Distributed PPO orchestration
trl>=0.8.0                # PPO trainer utilities
```

Container image extends the existing PyTorch base with these dependencies installed via `uv pip install` at workflow runtime, matching the LeRobot container pattern.

## 🤖 Inference Integration

### VLARunner Class Design

The `VLARunner` extends the existing `PolicyRunner` pattern from `src/inference/policy_runner.py`:

```python
class VLARunner:
    """Framework-agnostic VLA policy runner.

    Bridges multi-modal observations (images + joint states + language instruction)
    to action commands via VLA model inference.
    """

    def __init__(
        self,
        model_repo: str,
        language_instruction: str,
        device: str = "cuda",
        model_type: str = "smolvla",  # smolvla | pi0 | openvla
    ): ...

    def predict(self, observation: VLARobotObservation) -> JointPositionCommand:
        """Generate action from multi-modal observation + language goal."""
        ...
```

### Multi-Modal Observation

| Input                | Current (`RobotObservation`) | VLA Extension (`VLARobotObservation`)             |
| -------------------- | ---------------------------- | ------------------------------------------------- |
| Joint positions      | 6-DOF array                  | 6-DOF array (unchanged)                           |
| Color image          | 480×848 RGB                  | 480×848 RGB (unchanged)                           |
| Language instruction | Not present                  | Natural language string (per-episode or per-step) |
| Timestamp            | Float                        | Float (unchanged)                                 |

### Action Generation by Model Type

| Model       | Generation Method                      | Decoding                                | Post-Processing               |
| ----------- | -------------------------------------- | --------------------------------------- | ----------------------------- |
| SmolVLA     | Transformer action expert forward pass | Single forward pass (no autoregressive) | Unnormalize via dataset stats |
| pi0 / pi0.5 | Flow matching with N denoising steps   | Continuous output, no tokenization      | Unnormalize, action chunking  |
| OpenVLA-OFT | Autoregressive FAST token generation   | Token decode → DCT inverse → continuous | De-tokenize, unnormalize      |

### ROS2 VLA Inference Node

The VLA inference node extends `act_inference_node.py` with a language goal subscription:

| Topic                     | Type              | Direction | Purpose                           |
| ------------------------- | ----------------- | --------- | --------------------------------- |
| `/joint_states`           | `JointState`      | Subscribe | Current joint positions           |
| `/camera/color/image_raw` | `Image`           | Subscribe | RGB observation                   |
| `/language_goal`          | `String`          | Subscribe | Natural language task instruction |
| `/vla/joint_commands`     | `JointTrajectory` | Publish   | Predicted joint position commands |
| `/vla/status`             | `String`          | Publish   | Inference status and diagnostics  |

The node maintains the current language instruction in memory and re-uses it across inference steps until a new instruction arrives on `/language_goal`. Safety gate via `enable_control` parameter matches the existing ACT node pattern.

### Inference Serving

| Backend               | Use Case                       | Latency Target          | Hardware              |
| --------------------- | ------------------------------ | ----------------------- | --------------------- |
| Native PyTorch        | Development, single-model      | <100 ms/action          | Any GPU               |
| vLLM (PagedAttention) | Multi-model serving, flexible  | <50 ms/token            | Any GPU on AKS        |
| TensorRT-LLM (FP8)    | Production, maximum throughput | <20 ms/token            | H100 on AKS           |
| ONNX Runtime          | Edge deployment                | <50 ms/action (SmolVLA) | RTX PRO 6000 / Jetson |

For OpenVLA-OFT in production, vLLM with PagedAttention provides efficient KV cache management for autoregressive decoding. For pi0, native PyTorch with Flash Attention 2 is sufficient given the non-autoregressive flow matching head.

### Latency Analysis

| Model                     | Hardware     | Batch Size | Latency (ms/action) | Control Frequency |
| ------------------------- | ------------ | ---------- | ------------------- | ----------------- |
| SmolVLA                   | RTX PRO 6000 | 1          | ~15                 | >50 Hz            |
| SmolVLA (async)           | RTX PRO 6000 | 1          | ~10 effective       | >50 Hz            |
| pi0 (flow matching)       | H100         | 1          | ~20                 | 50 Hz             |
| pi0 (flow matching)       | RTX PRO 6000 | 1          | ~30                 | 30 Hz             |
| OpenVLA-OFT (FP16)        | H100         | 1          | ~100–200            | 5–10 Hz           |
| OpenVLA-OFT (INT4 AWQ)    | RTX PRO 6000 | 1          | ~100                | 10 Hz             |
| OpenVLA-OFT (TRT-LLM FP8) | H100         | 1          | ~50                 | 20 Hz             |

For UR10E manipulation at 10–30 Hz control, SmolVLA and pi0 meet real-time requirements on existing GPU hardware. OpenVLA requires quantization or TensorRT-LLM optimization.

## 📦 Data Pipeline Extensions

### Language Annotation Format

Each episode requires a natural language instruction describing the task. Add a `language_instruction` field to episode metadata:

```json
{
  "language_instruction": "Pick up the red bolt from the tray and place it in the assembly fixture",
  "language_instruction_type": "free_text"
}
```

LeRobot v3.0 `tasks.parquet` already contains a `task_index` column. Extend with a `language_instruction` string column mapping task indices to natural language descriptions.

### Recording Config Extension

Add a language instruction capture mechanism to `config/recording_config.yaml`:

```yaml
language:
  source: "operator_input"          # operator_input | ros_topic | predefined
  ros_topic: "/language_instruction"
  predefined_instructions:
    - "Pick up the bolt and place it in the fixture"
    - "Tighten the assembly screw to 5 Nm"
  annotation_mode: "per_episode"    # per_episode | per_step
```

Extend the Pydantic config models in `src/common/config_models.py` with a `LanguageConfig` model for validation.

### LeRobot Format Compatibility

| Model                | Data Format                  | Conversion Required                     |
| -------------------- | ---------------------------- | --------------------------------------- |
| SmolVLA              | LeRobot v3.0 (parquet + MP4) | None — native format                    |
| pi0 / pi0.5 (OpenPI) | LeRobot v3.0                 | None — OpenPI consumes LeRobot natively |
| OpenVLA-OFT          | RLDS (TensorFlow Datasets)   | LeRobot → RLDS conversion pipeline      |
| RDT-1B               | Custom format                | Custom conversion pipeline              |
| CogACT               | OXE / RLDS                   | LeRobot → RLDS conversion pipeline      |

For Tier 1 and Tier 2 models (SmolVLA, pi0), existing datasets in `datasets/` work without modification beyond adding language annotations. Tier 3 (OpenVLA-OFT) requires a conversion script from LeRobot v3.0 to RLDS format.

### RPRM Training Data Generation

VLA-RL requires pseudo-reward labels extracted from successful demonstration trajectories:

| Data Type        | Source                                     | Extraction Method                                     |
| ---------------- | ------------------------------------------ | ----------------------------------------------------- |
| Milestone labels | Gripper state changes in existing episodes | Binary segmentation on gripper open/close transitions |
| Progress labels  | End-effector velocity keyframes            | Velocity magnitude thresholding for phase transitions |
| Success labels   | Episode-level task completion              | Binary label from operator annotation or heuristic    |

The RPRM training pipeline fine-tunes a frozen VLM on these pseudo-labels, producing a reward model that provides dense progress signals during VLA-RL training.

### Curriculum Data

VLA-RL curriculum selection requires per-task success rate tracking:

| Data                    | Format                                    | Update Frequency      |
| ----------------------- | ----------------------------------------- | --------------------- |
| Task success rates      | JSON mapping `task_id → success_rate`     | Per evaluation epoch  |
| Task difficulty ranking | Sorted task list by 50% success proximity | Per curriculum update |
| Sampling weights        | Softmax-weighted distribution over tasks  | Per training batch    |

## 📝 Workflow Templates

### VLA Supervised Fine-Tuning (OSMO)

New OSMO workflow template `workflows/osmo/vla-train.yaml`:

```yaml
name: vla-fine-tuning
resources:
  gpu: 1                              # SmolVLA/pi0 LoRA; increase for full fine-tuning
  cpu: 16
  memory: "96Gi"
  storage: "200Gi"
env:
  ACCEPT_EULA: "Y"
  PRIVACY_CONSENT: "Y"
  VLA_MODEL: "smolvla"                # smolvla | pi0 | openvla
  VLA_BASE_MODEL: "HuggingFaceTB/SmolVLA-base"
  DATASET_REPO_ID: "hexagon_episodes"
  LORA_RANK: "32"
  TRAINING_STEPS: "50000"
  MLFLOW_TRACKING_URI: "{{ mlflow_tracking_uri }}"
```

### VLA-RL Training (OSMO)

New OSMO workflow template `workflows/osmo/vla-rl-train.yaml`:

```yaml
name: vla-rl-training
resources:
  gpu: 4                              # Multi-GPU for FSDP
  cpu: 32
  memory: "320Gi"
  storage: "500Gi"
env:
  ACCEPT_EULA: "Y"
  PRIVACY_CONSENT: "Y"
  VLA_MODEL: "openvla-oft"
  VLA_BASE_MODEL: "openvla/openvla-7b-finetuned"
  RL_ALGORITHM: "ppo"
  RPRM_MODEL: "rprm-checkpoint-latest"
  CURRICULUM_ENABLED: "true"
  CURRICULUM_TEMPERATURE: "0.5"
  PPO_EPOCHS: "4"
  PPO_CLIP_RANGE: "0.2"
```

### VLA Inference (OSMO)

New OSMO workflow template `workflows/osmo/vla-infer.yaml`:

```yaml
name: vla-inference
resources:
  gpu: 1
  cpu: 8
  memory: "48Gi"
  storage: "80Gi"
env:
  VLA_MODEL: "smolvla"
  VLA_CHECKPOINT: "{{ checkpoint_path }}"
  SERVING_BACKEND: "native"           # native | vllm | tensorrt
  LANGUAGE_INSTRUCTION: "Pick up the object and place it in the bin"
```

### AzureML Workflow Templates

| Template                              | Purpose                            | GPU Allocation               |
| ------------------------------------- | ---------------------------------- | ---------------------------- |
| `workflows/azureml/vla-train.yaml`    | VLA supervised fine-tuning job     | 1x GPU (LoRA) or 4–8x (full) |
| `workflows/azureml/vla-rl-train.yaml` | VLA-RL training with PPO + RPRM    | 4–8x GPU                     |
| `workflows/azureml/vla-validate.yaml` | VLA policy validation in Isaac Sim | 1x GPU                       |

AzureML workflows follow the existing `$schema: commandJob.schema.json` pattern with managed identity, `uri_folder` outputs, and `--set` overrides for submission scripts.

### Submission Script Patterns

New submission scripts follow the existing pattern in `scripts/`:

| Script                                      | Purpose                            |
| ------------------------------------------- | ---------------------------------- |
| `scripts/submit-osmo-vla-training.sh`       | Submit OSMO VLA fine-tuning job    |
| `scripts/submit-osmo-vla-rl-training.sh`    | Submit OSMO VLA-RL training job    |
| `scripts/submit-osmo-vla-inference.sh`      | Submit OSMO VLA inference job      |
| `scripts/submit-azureml-vla-training.sh`    | Submit AzureML VLA fine-tuning job |
| `scripts/submit-azureml-vla-rl-training.sh` | Submit AzureML VLA-RL training job |

Each script sources `scripts/lib/terraform-outputs.sh` for infrastructure values and supports `--config-preview` for dry-run validation.

## 📈 Scalable Implementation

### Distributed Training

| Strategy                           | Target Models                | Infrastructure                   | Use Case                              |
| ---------------------------------- | ---------------------------- | -------------------------------- | ------------------------------------- |
| LoRA + Single GPU                  | All VLA models               | 1x RTX PRO 6000 or H100          | Task-specific fine-tuning             |
| DDP (Distributed Data Parallel)    | All VLA models               | Multi-GPU via OSMO KAI Scheduler | Data parallel training                |
| FSDP (Fully Sharded Data Parallel) | 7B+ models (OpenVLA, CogACT) | 4–8x GPU OSMO workflows          | Memory-efficient full fine-tuning     |
| DeepSpeed ZeRO-3                   | 7B+ models                   | Alternative to FSDP              | FP16 + offloading for memory pressure |
| Ray + FSDP                         | VLA-RL pipeline              | 4–8x GPU with Ray orchestration  | PPO + FSDP combined                   |

FSDP is the standard for 7B+ VLA training. OSMO KAI Scheduler with coscheduling (gang-scheduling) reserves all GPUs atomically for multi-GPU jobs.

### Quantization

| Method | Precision             | Memory Savings | Quality Impact                       | Best For                        |
| ------ | --------------------- | -------------- | ------------------------------------ | ------------------------------- |
| FP8    | W8A8                  | ~2x            | Very minimal on Hopper/Ada           | H100 training and inference     |
| AWQ    | INT4 W4A16            | ~4x            | Minimal for inference                | Production edge deployment      |
| GPTQ   | INT4 W4A16            | ~4x            | Minimal, calibration-based           | Offline quantization            |
| INT8   | W8A16                 | ~2x            | Low                                  | General-purpose inference       |
| QLoRA  | INT4 base + FP16 LoRA | ~4x            | Enables single GPU fine-tuning of 7B | Fine-tuning OpenVLA on RTX 3090 |

### Compute Requirements

| Operation                    | GPU          | Count | VRAM      | Training Time       |
| ---------------------------- | ------------ | ----- | --------- | ------------------- |
| SmolVLA LoRA fine-tuning     | RTX PRO 6000 | 1     | 24 GB     | ~8h                 |
| SmolVLA full fine-tuning     | H100         | 1     | 48 GB     | ~16h                |
| pi0 LoRA fine-tuning         | H100         | 1     | 40 GB     | ~4–8h               |
| pi0 full fine-tuning         | H100         | 4–8   | 80 GB/GPU | ~24h                |
| OpenVLA-OFT LoRA (QLoRA)     | RTX PRO 6000 | 1     | 24 GB     | ~6–12h              |
| OpenVLA-OFT full fine-tuning | H100         | 8     | 80 GB/GPU | ~48h                |
| VLA-RL (PPO + RPRM)          | H100         | 4–8   | 80 GB/GPU | ~48 GPU hours total |
| RPRM reward model training   | RTX PRO 6000 | 1     | 24 GB     | ~4h                 |
| vLLM inference serving       | H100         | 1     | 40 GB     | N/A (serving)       |
| SmolVLA inference            | RTX PRO 6000 | 1     | 8 GB      | N/A (serving)       |

All configurations fit within existing OSMO and AzureML H100 and RTX PRO 6000 node pools.

### Inference Optimization Strategies

| Strategy                   | Latency Reduction                     | Hardware     | Applicable Models |
| -------------------------- | ------------------------------------- | ------------ | ----------------- |
| TensorRT-LLM compilation   | 2–4x speedup                          | H100         | OpenVLA-OFT       |
| vLLM PagedAttention        | Efficient KV cache, higher throughput | Any GPU      | OpenVLA-OFT       |
| Flash Attention 2          | 2x attention speedup                  | Any GPU      | All VLA models    |
| INT4 quantization (AWQ)    | 2x memory reduction, 1.5x speedup     | RTX PRO 6000 | OpenVLA-OFT       |
| Async inference pipelining | 2x effective throughput               | Any GPU      | SmolVLA           |
| DeeR-VLA dynamic exit      | Variable compute per token            | Any GPU      | Compatible VLAs   |

## 🗺️ Implementation Roadmap

### Phase 1: SmolVLA Prototype (2–3 weeks)

| Step | Task                                                                   | Output                                                     |
| ---- | ---------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1.1  | Add language annotations to existing LeRobot datasets in `datasets/`   | Annotated `tasks.parquet` with language instruction column |
| 1.2  | Implement `src/training/scripts/vla/train.py` with SmolVLA fine-tuning | Training orchestrator with MLflow integration              |
| 1.3  | Fine-tune SmolVLA on annotated Hexagarm episodes                       | LoRA adapter checkpoint in AzureML registry                |
| 1.4  | Implement `VLARunner` in `src/inference/`                              | VLA inference runner with SmolVLA support                  |
| 1.5  | Validate end-to-end: data → training → inference                       | Working prototype with logged metrics                      |

### Phase 2: pi0 Production (3–4 weeks)

| Step | Task                                                              | Output                                   |
| ---- | ----------------------------------------------------------------- | ---------------------------------------- |
| 2.1  | Integrate OpenPI fine-tuning API into training orchestrator       | pi0 training support in `vla/train.py`   |
| 2.2  | Create OSMO workflow template for pi0 LoRA fine-tuning            | `workflows/osmo/vla-train.yaml`          |
| 2.3  | Fine-tune pi0 on LeRobot datasets with LoRA                       | pi0 LoRA checkpoint at 50 Hz inference   |
| 2.4  | Implement vLLM serving integration for production inference       | Serving endpoint on AKS                  |
| 2.5  | Create ROS2 VLA inference node with `/language_goal` subscription | `vla_inference_node.py` deployed on edge |
| 2.6  | Validate 50 Hz control on UR10E hardware                          | Production inference at target frequency |

### Phase 3: VLA-RL Pipeline (4–6 weeks)

| Step | Task                                                          | Output                               |
| ---- | ------------------------------------------------------------- | ------------------------------------ |
| 3.1  | Implement RPRM training pipeline from successful trajectories | Reward model checkpoint              |
| 3.2  | Implement VLA-RL training orchestrator with PPO + RPRM        | `vla/train_rl.py` with critic warmup |
| 3.3  | Create multi-GPU OSMO workflow for VLA-RL                     | `workflows/osmo/vla-rl-train.yaml`   |
| 3.4  | Fine-tune OpenVLA-OFT with SFT → VLA-RL pipeline              | RL-improved VLA checkpoint           |
| 3.5  | Implement curriculum selection with per-task success tracking | Curriculum scheduler                 |
| 3.6  | Validate VLA-RL improvements over SFT baseline                | Benchmark results in MLflow          |

### Phase 4: Fleet Deployment (4–6 weeks)

| Step | Task                                                              | Output                      |
| ---- | ----------------------------------------------------------------- | --------------------------- |
| 4.1  | Quantize production VLA models (AWQ/GPTQ for edge, FP8 for cloud) | Quantized model artifacts   |
| 4.2  | TensorRT-LLM compilation for OpenVLA-OFT serving                  | Compiled engine on AKS      |
| 4.3  | Edge deployment container with quantized SmolVLA or pi0           | Jetson-compatible container |
| 4.4  | Monitoring and policy drift detection for deployed VLAs           | Azure Monitor integration   |
| 4.5  | A/B testing framework for VLA vs. ACT comparison                  | Evaluation pipeline         |

## 📚 References

### VLA Models

| Model          | Paper / Reference                                    | Repository                                                                 |
| -------------- | ---------------------------------------------------- | -------------------------------------------------------------------------- |
| pi0            | Physical Intelligence, 2024                          | `Physical-Intelligence/openpi`                                             |
| pi0.5          | Physical Intelligence, 2025 (Knowledge Insulation)   | `Physical-Intelligence/openpi`                                             |
| pi*0.6 (RECAP) | Physical Intelligence, 2025–2026                     | —                                                                          |
| SmolVLA        | HuggingFace, 2025                                    | `huggingface/lerobot`                                                      |
| OpenVLA        | Prismatic-7B VLM + action tokens (UC Berkeley, 2024) | `openvla/openvla`                                                          |
| OpenVLA-OFT    | Optimized fine-tuning + FAST tokenization, 2025      | `openvla/openvla`                                                          |
| CogACT         | Microsoft Research Asia, 2024–2025                   | [cogact.github.io](https://cogact.github.io)                               |
| RDT-1B         | Tsinghua, 2024 (Diffusion Transformer + PIUAS)       | `thu-ml/RoboticsDiffusionTransformer`                                      |
| Cosmos Policy  | NVIDIA, 2025 (video foundation model → control)      | [research.nvidia.com](https://research.nvidia.com/labs/dir/cosmos-policy/) |
| GR00T N1.5     | NVIDIA, 2024–2025 (humanoid VLA)                     | `NVIDIA/Isaac-GR00T`                                                       |
| DeeR-VLA       | Tsinghua/ByteDance, 2024 (dynamic early-exit)        | `yueyang130/DeeR-VLA`                                                      |
| Octo           | UC Berkeley, 2024 (transformer diffusion policy)     | [octo-models.github.io](https://octo-models.github.io)                     |
| RT-2           | Google DeepMind, 2023 (pioneered VLA paradigm)       | Closed-source                                                              |

### Key Papers

| Paper                                                    | Year      | Venue            | Relevance                                |
| -------------------------------------------------------- | --------- | ---------------- | ---------------------------------------- |
| FAST: Efficient Action Tokenization for VLAs             | 2025      | ICLR 2026        | DCT-based action token compression       |
| VLA-RL: Scalable RL for Robotic Manipulation (Lu et al.) | 2025      | arXiv:2505.18719 | PPO fine-tuning of VLAs                  |
| SpatialVLA / Evo-0: 3D spatial understanding for VLAs    | 2025–2026 | —                | RGB-only 3D geometry for manipulation    |
| RL-Co: Sim-Real Co-Training for VLAs                     | 2025      | ICLR 2026        | +24% on OpenVLA, +20% on pi0.5           |
| APO: Action Preference Optimization                      | 2025      | —                | Human-in-the-loop VLA refinement         |
| DPPO: Diffusion Policy Policy Optimization (Ren et al.)  | 2025      | ICLR 2025        | PPO for diffusion-based policies         |
| HPT: Heterogeneous Pre-trained Transformers              | 2024      | NeurIPS 2024     | Cross-embodiment representation learning |

### Existing Repository Documents

| Document                                 | Relevance                                                  |
| ---------------------------------------- | ---------------------------------------------------------- |
| `docs/il-to-rl-warm-start-strategies.md` | VLA-RL strategy (Strategy 7), warm-start patterns          |
| `docs/contributing/architecture.md`      | Planned `training/vla/` directory, 8-domain reorganization |
| `docs/contributing/ROADMAP.md`           | Q2 2026 VLA domain allocation                              |
| `docs/gpu-configuration.md`              | H100 and RTX PRO 6000 driver and MIG configuration         |
| `docs/mlflow-integration.md`             | MLflow tracking infrastructure                             |
