"""LeRobot training orchestrator with Azure ML MLflow integration.

Runs lerobot-train as a subprocess while capturing stdout to parse training
metrics and log them to MLflow. Collects system metrics (CPU, GPU, memory, disk)
and uploads checkpoints periodically.

Usage:
    python -m training.il.scripts.lerobot.train [lerobot-train args...]

Environment variables:
    DATASET_REPO_ID: HuggingFace dataset repository.
    POLICY_TYPE: Policy architecture (act, diffusion).
    OUTPUT_DIR: Container output directory.
    JOB_NAME: Job identifier.
    SYSTEM_METRICS: Enable system metrics collection (default: true).
    REGISTER_CHECKPOINT: Model name for Azure ML registration.
    EXPERIMENT_NAME: MLflow experiment name.
    AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME: Azure context.
    MIXED_PRECISION: Accelerate mixed-precision mode (``no``/``fp16``/``bf16``).
        Only effective when more than one CUDA device is visible (multi-GPU
        Accelerate launch). Under Accelerate the lerobot ``--policy.use_amp``
        flag is ignored.

The number of GPUs is detected at runtime via ``torch.cuda.device_count()`` --
i.e., from the GPU devices the job container can see. On AzureML-on-Kubernetes
that is driven by the ``InstanceType``'s ``nvidia.com/gpu`` request; on managed
``AmlCompute`` it is the cluster VM SKU's GPU count. When detection returns
> 1, ``lerobot-train`` is launched via
``accelerate launch --multi_gpu --num_processes=N``.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from training.il.scripts.lerobot._env import has_blob_urls

EXIT_SUCCESS = 0
EXIT_FAILURE = 1

# LeRobot log line pattern:
# step:200 smpl:2K ep:4 epch:0.31 loss:6.938 grdn:155.563 lr:1.0e-05 updt_s:0.324 data_s:0.011
_LOG_PATTERN = re.compile(
    r"step:(\d+\.?\d*K?)\s+"
    r"smpl:(\d+\.?\d*K?)\s+"
    r"ep:(\d+\.?\d*K?)\s+"
    r"epch:([\d.]+)\s+"
    r"loss:([\d.]+)\s+"
    r"grdn:([\d.]+)\s+"
    r"lr:([\d.e+-]+)\s+"
    r"updt_s:([\d.]+)\s+"
    r"data_s:([\d.]+)"
)

_VAL_PATTERN = re.compile(r"val[_/]loss[:\s]+([\d.]+)")

CHECKPOINT_CHECK_INTERVAL = 60
SYSTEM_METRICS_INTERVAL = 30

# Grace window after SIGTERM/SIGINT before escalating to SIGKILL on the whole
# subprocess group. Accelerate spawns one worker per rank; without a process
# group kill the wrapper can return while ranks linger and hold GPU memory.
_PROCESS_GROUP_KILL_GRACE_S = 15

_VALID_MIXED_PRECISION = {"no", "fp16", "bf16"}


def _sync_checkpoint_output(output_dir: Path) -> None:
    """Mirror ``output_dir/checkpoints/`` into ``$AZURE_ML_OUTPUT_CHECKPOINTS`` if set.

    Azure ML exports ``AZURE_ML_OUTPUT_<NAME>`` for each ``uri_folder`` output
    declared on the job; whatever lands in that directory is uploaded to the
    named output's blob path at job end. No-op when the env var is unset
    (e.g., local runs) or when there is nothing to copy.
    """

    target = os.environ.get("AZURE_ML_OUTPUT_CHECKPOINTS")
    if not target:
        return

    source = output_dir / "checkpoints"
    if not source.exists():
        return

    destination = Path(target)
    try:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        print(f"[AzureML] Copied checkpoints to {destination}")
    except Exception as exc:
        print(f"[AzureML] Failed to copy checkpoints to {destination}: {exc}")


def _detect_num_gpus() -> int:
    """Detect the number of CUDA devices visible to the job container.

    ``torch.cuda.device_count()`` reports exactly what the container can see,
    which on AzureML-on-Kubernetes is the pod's ``nvidia.com/gpu`` request
    (set by the chosen ``InstanceType``) and on managed ``AmlCompute`` is the
    cluster VM SKU's GPU count. For MIG-sliced SKUs (e.g., RTX PRO 6000 with
    ``mig.strategy=single``) this counts slices, which is what Accelerate
    wants for ``--num_processes``.

    Returns 1 when ``torch`` is not importable (unit-test environments) so the
    rest of the wrapper still functions deterministically.
    """
    try:
        import torch
    except ImportError:
        print("[GPU-DETECT] torch not importable; assuming 1 GPU (single-process launch)")
        return 1

    count = torch.cuda.device_count()
    cuda_visible = os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>")
    print(f"[GPU-DETECT] torch.cuda.device_count()={count}, CUDA_VISIBLE_DEVICES={cuda_visible}")
    return max(count, 1)


def _read_mixed_precision() -> str:
    """Parse MIXED_PRECISION env var; default 'no'; raise on invalid input."""
    raw = os.environ.get("MIXED_PRECISION", "no").strip().lower() or "no"
    if raw not in _VALID_MIXED_PRECISION:
        raise RuntimeError(f"MIXED_PRECISION must be one of {sorted(_VALID_MIXED_PRECISION)} (got {raw!r})")
    return raw


def _resolve_lerobot_train() -> str:
    """Resolve the lerobot-train console script path.

    `accelerate launch` requires an absolute script path as its target. The
    venv is activated by azureml-train-entry.sh before this module runs, so
    `shutil.which` is the canonical lookup. Fall back to the documented venv
    location if PATH is unexpectedly clean.
    """
    resolved = shutil.which("lerobot-train")
    if resolved:
        return resolved
    fallback = Path("/opt/lerobot-venv/bin/lerobot-train")
    if fallback.exists():
        return str(fallback)
    raise RuntimeError(
        "lerobot-train console script not found on PATH or at /opt/lerobot-venv/bin. "
        "Verify the entrypoint activated the uv-managed venv."
    )


def _wrap_with_accelerate(cmd: list[str], num_gpus: int, mixed_precision: str) -> list[str]:
    """Prepend accelerate launch flags for single-node multi-GPU training.

    Assumes ``cmd[0] == 'lerobot-train'``. Replaces it with the resolved
    absolute path so accelerate launches the right entrypoint.
    """
    if not cmd or cmd[0] != "lerobot-train":
        raise RuntimeError(f"Expected cmd to start with 'lerobot-train' (got {cmd[:1]})")

    accelerate_args = [
        "accelerate",
        "launch",
        "--multi_gpu",
        f"--num_processes={num_gpus}",
    ]
    # Default mixed_precision is 'no' (script-level default); pass through
    # explicitly so accelerate's environment config never overrides it.
    accelerate_args.append(f"--mixed_precision={mixed_precision}")

    return [*accelerate_args, _resolve_lerobot_train(), *cmd[1:]]


def _strip_use_amp(args: list[str]) -> list[str]:
    """Drop --policy.use_amp[=...] when training under Accelerate.

    Per HF guide: ``--policy.use_amp`` is only honored when NOT using
    Accelerate; under Accelerate, mixed precision is controlled by
    ``--mixed_precision``. Stripping prevents lerobot-train from silently
    diverging between launch modes.
    """
    cleaned: list[str] = []
    skipped = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--policy.use_amp" or arg.startswith("--policy.use_amp="):
            skipped = True
            # Bare form is followed by a value; consume it.
            if arg == "--policy.use_amp" and i + 1 < len(args):
                i += 2
                continue
            i += 1
            continue
        cleaned.append(arg)
        i += 1
    if skipped:
        print("[ACCELERATE] WARNING: stripped --policy.use_amp; mixed precision is set via --mixed_precision")
    return cleaned


def _parse_k_value(val: str) -> float:
    """Parse values like '2K' to 2000."""
    if val.endswith("K"):
        return float(val[:-1]) * 1000
    return float(val)


def _init_system_collector() -> Any | None:
    """Initialize system metrics collector if enabled and dependencies available."""
    if os.environ.get("SYSTEM_METRICS", "true").lower() != "true":
        return None

    try:
        from training.utils.metrics import SystemMetricsCollector

        collector = SystemMetricsCollector(collect_gpu=True, collect_disk=True)
        print("[System] System metrics collection initialized")
        return collector
    except ImportError:
        pass

    # Fallback: try direct psutil/pynvml import (container without training.utils)
    try:
        import psutil

        class _FallbackCollector:
            def __init__(self) -> None:
                self._gpu_available = False
                self._gpu_handles: list = []
                try:
                    import pynvml

                    pynvml.nvmlInit()
                    count = pynvml.nvmlDeviceGetCount()
                    self._gpu_handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
                    self._gpu_available = True
                    print(f"[System] GPU metrics enabled for {count} device(s)")
                except Exception as exc:
                    print(f"[System] GPU metrics unavailable: {exc}")

            def collect_metrics(self) -> dict[str, float]:
                metrics: dict[str, float] = {}
                try:
                    metrics["system/cpu_utilization_percentage"] = psutil.cpu_percent(interval=None)
                    mem = psutil.virtual_memory()
                    metrics["system/memory_used_megabytes"] = mem.used / (1024 * 1024)
                    metrics["system/memory_percent"] = mem.percent
                    disk = psutil.disk_usage("/")
                    metrics["system/disk_used_gigabytes"] = disk.used / (1024**3)
                    metrics["system/disk_percent"] = disk.percent
                except Exception:
                    pass  # Non-critical: skip CPU/memory metrics if collection fails

                if self._gpu_available:
                    import pynvml

                    for i, handle in enumerate(self._gpu_handles):
                        try:
                            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                            metrics[f"system/gpu_{i}_utilization_percentage"] = float(util.gpu)
                            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                            metrics[f"system/gpu_{i}_memory_used_megabytes"] = mem_info.used / (1024 * 1024)
                            metrics[f"system/gpu_{i}_memory_percent"] = (mem_info.used / mem_info.total) * 100
                            power = pynvml.nvmlDeviceGetPowerUsage(handle)
                            metrics[f"system/gpu_{i}_power_watts"] = power / 1000
                        except Exception:
                            pass  # Non-critical: skip individual GPU metrics on transient NVML errors
                return metrics

        collector = _FallbackCollector()
        print("[System] System metrics collection initialized (fallback)")
        return collector

    except ImportError:
        print("[System] psutil not available, skipping system metrics")
        return None


def _build_train_params(num_gpus: int) -> dict[str, str]:
    """Build parameter dict from environment for MLflow logging.

    ``num_gpus`` is the runtime-detected device count (caller's responsibility);
    passed in so all callers in this module agree on the same value.
    """
    batch_size_raw = os.environ.get("BATCH_SIZE", "32")
    try:
        effective_bs = int(batch_size_raw) * num_gpus
    except ValueError:
        effective_bs = -1  # Non-numeric BATCH_SIZE (e.g., unset placeholder); skip computation.

    params = {
        "dataset_repo_id": os.environ.get("DATASET_REPO_ID", ""),
        "policy_type": os.environ.get("POLICY_TYPE", "act"),
        "job_name": os.environ.get("JOB_NAME", ""),
        "policy_repo_id": os.environ.get("POLICY_REPO_ID", ""),
        "training_steps": os.environ.get("TRAINING_STEPS", "100000"),
        "batch_size": batch_size_raw,
        "learning_rate": os.environ.get("LEARNING_RATE", "1e-4"),
        "lr_warmup_steps": os.environ.get("LR_WARMUP_STEPS", "1000"),
        "save_freq": os.environ.get("SAVE_FREQ", "5000"),
        "val_split": os.environ.get("VAL_SPLIT", "0.1"),
        "system_metrics": os.environ.get("SYSTEM_METRICS", "true"),
        "num_gpus": str(num_gpus),
        "mixed_precision": _read_mixed_precision(),
        "distributed": str(num_gpus > 1).lower(),
    }
    if effective_bs > 0:
        params["effective_batch_size"] = str(effective_bs)
    return params


def run_training(cmd: list[str], source: str = "osmo-lerobot-training", num_gpus: int = 1) -> int:
    """Execute lerobot-train and log metrics to MLflow.

    Args:
        cmd: Full command to execute (e.g., ["lerobot-train", "--dataset.repo_id=..."]).
        source: Source tag for checkpoint registration.

    Returns:
        Process exit code.
    """
    import mlflow

    from training.il.scripts.lerobot.checkpoints import upload_new_checkpoints

    system_collector = _init_system_collector()
    output_dir = Path(os.environ.get("OUTPUT_DIR", "/workspace/outputs/train"))
    uploaded_checkpoints: set[str] = set()
    last_checkpoint_check = 0.0
    last_system_check = 0.0

    # AzureML jobs auto-create an MLflow run and expose its ID in MLFLOW_RUN_ID.
    # mlflow.start_run() picks it up automatically when no args are passed; passing
    # a run_name in that case fails because the existing run is already named.
    start_run_kwargs: dict = {}
    if not os.environ.get("MLFLOW_RUN_ID"):
        start_run_kwargs["run_name"] = os.environ.get("JOB_NAME", "lerobot-training")

    with mlflow.start_run(**start_run_kwargs) as run:
        print(f"[MLflow] Run ID: {run.info.run_id}")

        params = _build_train_params(num_gpus)
        mlflow.log_params(params)

        # Lineage tags: dataset -> run -> registered model. These are visible
        # in the MLflow Run UI under "Tags" and are queryable via the SDK.
        mixed_precision_tag = _read_mixed_precision()
        lineage_tags: dict[str, str] = {
            "lerobot.framework": "lerobot",
            "lerobot.policy_type": os.environ.get("POLICY_TYPE", "act"),
            "lerobot.job_name": os.environ.get("JOB_NAME", ""),
            "lerobot.num_gpus": str(num_gpus),
            "lerobot.mixed_precision": mixed_precision_tag,
            "lerobot.distributed": str(num_gpus > 1).lower(),
        }
        if os.environ.get("DATASET_REPO_ID"):
            lineage_tags["dataset.repo_id"] = os.environ["DATASET_REPO_ID"]
        if has_blob_urls():
            lineage_tags["dataset.source"] = "azure-blob"
        elif os.environ.get("DATASET_REPO_ID"):
            lineage_tags["dataset.source"] = "huggingface"
        if os.environ.get("REGISTER_CHECKPOINT"):
            lineage_tags["model.register_name"] = os.environ["REGISTER_CHECKPOINT"]
        warm_start_source = os.environ.get("INIT_FROM_POLICY_MODEL_SOURCE", "")
        if warm_start_source:
            lineage_tags["warm_start.source"] = warm_start_source
            # Submission script enforces that azureml: URIs use NAME:VERSION
            # with a numeric version. Anything else is a fully-qualified URL
            # (azureml://... or https://...) and is left unparsed.
            match = re.match(r"^azureml:([^/:][^:]*):(\d+)$", warm_start_source)
            if match:
                lineage_tags["warm_start.model_name"] = match.group(1)
                lineage_tags["warm_start.model_version"] = match.group(2)
        mlflow.set_tags(lineage_tags)

        print(f"[MLflow] Starting training: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # Detach into a new session so the whole accelerate launch tree
            # (launcher + N worker ranks) can be reaped via os.killpg from the
            # signal handler. Without this, accelerate child ranks survive
            # process.terminate() and keep GPU memory pinned on preemption.
            start_new_session=True,
        )

        def signal_handler(signum: int, frame: object) -> None:
            print(f"[MLflow] Received signal {signum}, terminating subprocess group then mirroring checkpoints...")
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=_PROCESS_GROUP_KILL_GRACE_S)
            except subprocess.TimeoutExpired:
                print(
                    f"[MLflow] Subprocess group did not exit after "
                    f"{_PROCESS_GROUP_KILL_GRACE_S}s; escalating to SIGKILL"
                )
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
            # Workers are now quiescent; safe to read the on-disk checkpoint tree
            # without risking partial-file mirrors. Doing the copy before kill
            # would race accelerate workers still writing to checkpoint shards
            # and could mirror a half-written destination.
            with contextlib.suppress(Exception):
                upload_new_checkpoints(run, output_dir, uploaded_checkpoints, source=source)
            with contextlib.suppress(Exception):
                _sync_checkpoint_output(output_dir)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            current_time = time.time()

            match = _LOG_PATTERN.search(line)
            if match:
                step = int(_parse_k_value(match.group(1)))
                metrics = {
                    "train/samples": _parse_k_value(match.group(2)),
                    "train/episodes": _parse_k_value(match.group(3)),
                    "train/epoch": float(match.group(4)),
                    "train/loss": float(match.group(5)),
                    "train/grad_norm": float(match.group(6)),
                    "train/learning_rate": float(match.group(7)),
                    "train/update_time_s": float(match.group(8)),
                    "train/data_time_s": float(match.group(9)),
                }

                if system_collector and current_time - last_system_check > SYSTEM_METRICS_INTERVAL:
                    last_system_check = current_time
                    with contextlib.suppress(Exception):
                        metrics.update(system_collector.collect_metrics())

                mlflow.log_metrics(metrics, step=step)

                if current_time - last_checkpoint_check > CHECKPOINT_CHECK_INTERVAL:
                    last_checkpoint_check = current_time
                    upload_new_checkpoints(run, output_dir, uploaded_checkpoints, source=source)

            val_match = _VAL_PATTERN.search(line)
            if val_match:
                with contextlib.suppress(Exception):
                    mlflow.log_metric("val/loss", float(val_match.group(1)))

        process.wait()

        print("[MLflow] Uploading final checkpoints...")
        upload_new_checkpoints(run, output_dir, uploaded_checkpoints, source=source)
        _sync_checkpoint_output(output_dir)

        mlflow.log_param("output_dir", str(output_dir))

        if process.returncode != 0:
            mlflow.set_tag("training_status", "failed")
            print(f"[MLflow] Training failed with return code: {process.returncode}")
        else:
            mlflow.set_tag("training_status", "completed")

    print("[MLflow] Run completed")
    return process.returncode or EXIT_SUCCESS


def main() -> int:
    """Main entry point for LeRobot MLflow training wrapper.

    Bootstraps Azure ML, builds lerobot-train command from environment
    variables and CLI arguments, runs training, and registers checkpoints.
    """
    from training.il.scripts.lerobot.bootstrap import authenticate_huggingface, bootstrap_mlflow
    from training.il.scripts.lerobot.checkpoints import register_final_checkpoint

    # Bootstrap
    hf_user = authenticate_huggingface()

    policy_type = os.environ.get("POLICY_TYPE", "act")
    job_name = os.environ.get("JOB_NAME", "lerobot-act-training")

    bootstrap_mlflow(
        experiment_name=os.environ.get("EXPERIMENT_NAME", ""),
        policy_type=policy_type,
        job_name=job_name,
    )

    # Load MLflow config into environment
    config_path = Path("/tmp/mlflow_config.env")
    if config_path.exists():
        for line in config_path.read_text().strip().splitlines():
            key, _, value = line.partition("=")
            if key and value:
                os.environ[key] = value

    # Build training command
    cmd = ["lerobot-train"]

    # Forward CLI args (anything passed after script name)
    cmd.extend(sys.argv[1:])

    # Add env-based arguments if not already in CLI args
    cli_text = " ".join(sys.argv[1:])

    if "--dataset.repo_id" not in cli_text:
        dataset_repo_id = os.environ.get("DATASET_REPO_ID", "")
        if dataset_repo_id:
            cmd.append(f"--dataset.repo_id={dataset_repo_id}")

    # lerobot rejects --policy.path together with --policy.type; when warm-starting
    # (--policy.path is present) the policy type is read from the loaded config.json.
    if "--policy.type" not in cli_text and "--policy.path" not in cli_text:
        cmd.append(f"--policy.type={policy_type}")

    if "--output_dir" not in cli_text:
        output_dir = os.environ.get("OUTPUT_DIR", "/workspace/outputs/train")
        cmd.append(f"--output_dir={output_dir}")

    if "--job_name" not in cli_text:
        cmd.append(f"--job_name={job_name}")

    if "--policy.device" not in cli_text:
        cmd.append("--policy.device=cuda")

    if "--wandb" not in cli_text:
        cmd.append("--wandb.enable=false")

    # Policy repo ID
    if "--policy.repo_id" not in cli_text:
        policy_repo_id = os.environ.get("POLICY_REPO_ID", "")
        if policy_repo_id:
            cmd.append(f"--policy.repo_id={policy_repo_id}")
        elif hf_user:
            default_repo = f"{hf_user}/{job_name}"
            print(f"Auto-derived policy.repo_id: {default_repo}")
            cmd.append(f"--policy.repo_id={default_repo}")

    # Training hyperparameters from environment
    env_arg_map = {
        "TRAINING_STEPS": "--steps",
        "BATCH_SIZE": "--batch_size",
        "LEARNING_RATE": "--policy.optimizer_lr",
        "EVAL_FREQ": "--eval_freq",
        "SAVE_FREQ": "--save_freq",
        "LOG_FREQ": "--log_freq",
    }
    for env_var, arg_name in env_arg_map.items():
        if arg_name not in cli_text:
            value = os.environ.get(env_var, "")
            if value:
                cmd.append(f"{arg_name}={value}")

    # Source tag for MLflow lineage: {platform}-lerobot-{datasource}.
    # AZUREML_RUN_ID is set automatically by Azure ML on job pods; absent on OSMO.
    # BLOB_URLS discriminates blob-fed runs from HuggingFace downloads on either platform.
    platform = "azureml" if os.environ.get("AZUREML_RUN_ID") else "osmo"
    datasource = "blob" if has_blob_urls() else "hf"
    source = f"{platform}-lerobot-{datasource}"

    # Single-node multi-GPU: detect the GPU count visible to the job container
    # (AzureML-on-Kubernetes: pod's `nvidia.com/gpu` request via InstanceType;
    # AmlCompute: cluster VM SKU's GPU count) and, when > 1, wrap with
    # `accelerate launch` and strip --policy.use_amp (ignored under Accelerate
    # per the HF guide).
    num_gpus = _detect_num_gpus()
    mixed_precision = _read_mixed_precision()
    if num_gpus > 1:
        cmd = _strip_use_amp(cmd)
        cmd = _wrap_with_accelerate(cmd, num_gpus=num_gpus, mixed_precision=mixed_precision)
        print(f"[ACCELERATE] Multi-GPU run: num_gpus={num_gpus}, mixed_precision={mixed_precision}")
    else:
        print("[ACCELERATE] Single-GPU run: launching lerobot-train directly")

    # Run training
    exit_code = run_training(cmd, source=source, num_gpus=num_gpus)

    # Post-training checkpoint registration
    if exit_code == EXIT_SUCCESS and os.environ.get("REGISTER_CHECKPOINT"):
        exit_code = register_final_checkpoint()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
