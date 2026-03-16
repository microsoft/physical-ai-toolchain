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
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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


def _build_train_params() -> dict[str, str]:
    """Build parameter dict from environment for MLflow logging."""
    return {
        "dataset_repo_id": os.environ.get("DATASET_REPO_ID", ""),
        "policy_type": os.environ.get("POLICY_TYPE", "act"),
        "job_name": os.environ.get("JOB_NAME", ""),
        "policy_repo_id": os.environ.get("POLICY_REPO_ID", ""),
        "training_steps": os.environ.get("TRAINING_STEPS", "100000"),
        "batch_size": os.environ.get("BATCH_SIZE", "32"),
        "learning_rate": os.environ.get("LEARNING_RATE", "1e-4"),
        "lr_warmup_steps": os.environ.get("LR_WARMUP_STEPS", "1000"),
        "save_freq": os.environ.get("SAVE_FREQ", "5000"),
        "val_split": os.environ.get("VAL_SPLIT", "0.1"),
        "system_metrics": os.environ.get("SYSTEM_METRICS", "true"),
    }


def run_training(cmd: list[str], source: str = "osmo-lerobot-training") -> int:
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

    with mlflow.start_run(run_name=os.environ.get("JOB_NAME", "lerobot-training")) as run:
        print(f"[MLflow] Run ID: {run.info.run_id}")

        params = _build_train_params()
        # Add extra params for azure-data variant
        if os.environ.get("STORAGE_ACCOUNT"):
            params["storage_account"] = os.environ.get("STORAGE_ACCOUNT", "")
            params["blob_prefix"] = os.environ.get("BLOB_PREFIX", "")
        mlflow.log_params(params)

        print(f"[MLflow] Starting training: {' '.join(cmd)}")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def signal_handler(signum: int, frame: object) -> None:
            print(f"[MLflow] Received signal {signum}, saving checkpoints...")
            upload_new_checkpoints(run, output_dir, uploaded_checkpoints, source=source)
            process.terminate()

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

    if "--policy.type" not in cli_text:
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
    }
    for env_var, arg_name in env_arg_map.items():
        if arg_name not in cli_text:
            value = os.environ.get(env_var, "")
            if value:
                cmd.append(f"{arg_name}={value}")

    # Determine source tag
    source = "osmo-lerobot-training"
    if os.environ.get("STORAGE_ACCOUNT"):
        source = "osmo-azure-data-training"

    # Run training
    exit_code = run_training(cmd, source=source)

    # Post-training checkpoint registration
    if exit_code == EXIT_SUCCESS and os.environ.get("REGISTER_CHECKPOINT"):
        register_final_checkpoint()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
