"""Upload inference artifacts to MLflow and/or Azure Blob Storage.

Reads environment variables to configure upload destination and uses
the same bootstrap_azure_ml utility as training.
"""

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def load_metrics(metrics_dir: Path) -> tuple[dict, dict]:
    """Load ONNX and JIT metrics from JSON files."""
    onnx_metrics = {}
    jit_metrics = {}

    onnx_path = metrics_dir / "onnx_metrics.json"
    jit_path = metrics_dir / "jit_metrics.json"

    if onnx_path.exists():
        with open(onnx_path) as f:
            onnx_metrics = json.load(f)
        print(f"[Metrics] Loaded ONNX metrics: {len(onnx_metrics)} fields")

    if jit_path.exists():
        with open(jit_path) as f:
            jit_metrics = json.load(f)
        print(f"[Metrics] Loaded JIT metrics: {len(jit_metrics)} fields")

    return onnx_metrics, jit_metrics


def get_video_search_paths(export_dir: Path) -> list[Path]:
    """Return list of directories to search for video files."""
    return [
        export_dir / "videos",
        export_dir.parent / "videos",
        Path("/isaac-sim/videos"),
        Path.home() / "videos",
        Path("/tmp/videos"),
    ]


def upload_to_mlflow(
    task: str,
    export_dir: Path,
    metrics_dir: Path,
    checkpoint_uri: str,
    onnx_success: bool,
    jit_success: bool,
    onnx_metrics: dict,
    jit_metrics: dict,
    timestamp: str,
) -> bool:
    """Upload artifacts to MLflow via Azure ML integration."""
    try:
        import mlflow

        from training.utils import AzureConfigError, bootstrap_azure_ml
    except ImportError as e:
        print(f"[WARNING] training.utils not available: {e}")
        return False

    try:
        experiment_name = f"inference-{task}"
        context = bootstrap_azure_ml(experiment_name=experiment_name)
        print(f"[MLflow] Connected to workspace: {context.workspace_name}")
        print(f"[MLflow] Tracking URI: {context.tracking_uri}")
    except AzureConfigError as e:
        print(f"[WARNING] Azure ML not configured: {e}")
        return False
    except Exception as e:
        print(f"[WARNING] MLflow connection failed: {e}")
        return False

    run_name = f"inference-{task}-{timestamp}"
    try:
        with mlflow.start_run(run_name=run_name) as run:
            mlflow.set_tags(
                {
                    "task": task,
                    "checkpoint_uri": checkpoint_uri,
                    "inference_type": "policy_validation",
                    "onnx_success": str(onnx_success),
                    "jit_success": str(jit_success),
                }
            )
            mlflow.log_params(
                {
                    "num_envs": os.environ["NUM_ENVS"],
                    "max_steps": os.environ["MAX_STEPS"],
                    "video_length": os.environ["VIDEO_LENGTH"],
                    "inference_format": os.environ["INFERENCE_FORMAT"],
                }
            )

            if onnx_metrics:
                mlflow.log_metrics(
                    {
                        "onnx/mean_episode_reward": onnx_metrics.get("mean_episode_reward", 0),
                        "onnx/std_episode_reward": onnx_metrics.get("std_episode_reward", 0),
                        "onnx/total_episodes": onnx_metrics.get("total_episodes", 0),
                        "onnx/mean_inference_time_ms": onnx_metrics.get("mean_inference_time_ms", 0),
                        "onnx/p95_inference_time_ms": onnx_metrics.get("p95_inference_time_ms", 0),
                        "onnx/throughput_steps_per_sec": onnx_metrics.get("throughput_steps_per_sec", 0),
                    }
                )
                mlflow.log_artifact(str(metrics_dir / "onnx_metrics.json"), artifact_path="metrics")

            if jit_metrics:
                mlflow.log_metrics(
                    {
                        "jit/mean_episode_reward": jit_metrics.get("mean_episode_reward", 0),
                        "jit/std_episode_reward": jit_metrics.get("std_episode_reward", 0),
                        "jit/total_episodes": jit_metrics.get("total_episodes", 0),
                        "jit/mean_inference_time_ms": jit_metrics.get("mean_inference_time_ms", 0),
                        "jit/p95_inference_time_ms": jit_metrics.get("p95_inference_time_ms", 0),
                        "jit/throughput_steps_per_sec": jit_metrics.get("throughput_steps_per_sec", 0),
                    }
                )
                mlflow.log_artifact(str(metrics_dir / "jit_metrics.json"), artifact_path="metrics")

            best_reward = max(
                onnx_metrics.get("mean_episode_reward", 0),
                jit_metrics.get("mean_episode_reward", 0),
            )
            total_episodes = onnx_metrics.get("total_episodes", 0) + jit_metrics.get("total_episodes", 0)
            mlflow.log_metrics(
                {
                    "mean_reward": best_reward,
                    "eval_episodes": total_episodes,
                    "success_rate": 1.0 if (onnx_success or jit_success) else 0.0,
                }
            )

            for model_file in export_dir.glob("policy.*"):
                mlflow.log_artifact(str(model_file), artifact_path="exported_models")
                print(f"[MLflow] Logged model: {model_file.name}")

            videos_logged = 0
            for video_dir in get_video_search_paths(export_dir):
                if video_dir.exists():
                    for video_file in video_dir.rglob("*.mp4"):
                        if "onnx" in str(video_file).lower():
                            artifact_subpath = "videos/onnx"
                        elif "jit" in str(video_file).lower():
                            artifact_subpath = "videos/jit"
                        else:
                            artifact_subpath = "videos"
                        mlflow.log_artifact(str(video_file), artifact_path=artifact_subpath)
                        print(f"[MLflow] Logged video: {video_file.name} -> {artifact_subpath}/")
                        videos_logged += 1

            print(f"\n{'=' * 60}")
            print("[MLflow] Artifacts logged to Azure ML Studio!")
            print(f"[MLflow] Run ID: {run.info.run_id}")
            print(f"[MLflow] Experiment: {experiment_name}")
            print(f"[MLflow] Run name: {run_name}")
            print(f"[MLflow] Total videos: {videos_logged}")
            print(f"{'=' * 60}\n")

            if context.storage:
                base_path = f"inference_outputs/{task}/{timestamp}"
                files_to_upload = []

                for model_file in export_dir.glob("policy.*"):
                    files_to_upload.append((str(model_file), f"{base_path}/models/{model_file.name}"))

                for video_dir in get_video_search_paths(export_dir):
                    if video_dir.exists():
                        for video_file in video_dir.rglob("*.mp4"):
                            files_to_upload.append((str(video_file), f"{base_path}/videos/{video_file.name}"))

                if files_to_upload:
                    uploaded = context.storage.upload_files_batch(files_to_upload)
                    print(f"[Blob] Uploaded {len(uploaded)} files to storage")

            return True
    except Exception as e:
        print(f"[WARNING] MLflow logging failed: {e}")
        traceback.print_exc()
        return False


def upload_to_blob_fallback(
    task: str,
    export_dir: Path,
    blob_account: str,
    blob_container: str,
    checkpoint_uri: str,
    timestamp: str,
) -> bool:
    """Upload artifacts directly to Azure Blob Storage as fallback."""
    if not blob_account and checkpoint_uri.startswith("https://"):
        parsed = urlparse(checkpoint_uri)
        if parsed.netloc.endswith(".blob.core.windows.net"):
            blob_account = parsed.netloc.split(".")[0]
            path_parts = parsed.path.lstrip("/").split("/", 1)
            blob_container = path_parts[0] if path_parts else "inference-outputs"

    if not blob_account:
        blob_account = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
        blob_container = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "isaaclab-training-logs")

    if not blob_account:
        print("Warning: No storage configured; videos only saved locally")
        return False

    print(f"[Blob] Uploading to storage account: {blob_account}/{blob_container}")

    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as e:
        print(f"[WARNING] Azure SDK not available: {e}")
        return False

    try:
        credential = DefaultAzureCredential()
        blob_service = BlobServiceClient(
            account_url=f"https://{blob_account}.blob.core.windows.net",
            credential=credential,
        )
        container_client = blob_service.get_container_client(blob_container)

        base_path = f"inference_outputs/{task}/{timestamp}"
        uploaded = 0

        for model_file in export_dir.glob("policy.*"):
            blob_name = f"{base_path}/models/{model_file.name}"
            try:
                with open(model_file, "rb") as f:
                    container_client.upload_blob(name=blob_name, data=f, overwrite=True)
                print(f"[Blob] Uploaded model: {blob_name}")
                uploaded += 1
            except Exception as e:
                print(f"Warning: Failed to upload {model_file}: {e}")

        for video_dir in get_video_search_paths(export_dir):
            if video_dir.exists():
                for video_file in video_dir.rglob("*.mp4"):
                    blob_name = f"{base_path}/videos/{video_file.name}"
                    try:
                        with open(video_file, "rb") as f:
                            container_client.upload_blob(name=blob_name, data=f, overwrite=True)
                        print(f"[Blob] Uploaded video: {blob_name}")
                        uploaded += 1
                    except Exception as e:
                        print(f"Warning: Failed to upload {video_file}: {e}")

        print(f"[Blob] Total files uploaded: {uploaded}")
        if uploaded > 0:
            print(f"[Blob] Direct URL: https://{blob_account}.blob.core.windows.net/{blob_container}/{base_path}/")
        return uploaded > 0
    except Exception as e:
        print(f"[WARNING] Blob upload failed: {e}")
        traceback.print_exc()
        return False


def main() -> None:
    """Main entry point for artifact upload."""
    from training.utils import set_env_defaults

    set_env_defaults(
        {
            "TASK": "unknown",
            "EXPORT_DIR": "/tmp/exported",
            "ONNX_SUCCESS": "0",
            "JIT_SUCCESS": "0",
            "NUM_ENVS": "4",
            "MAX_STEPS": "500",
            "VIDEO_LENGTH": "200",
            "INFERENCE_FORMAT": "both",
        }
    )

    task = os.environ["TASK"]
    export_dir = Path(os.environ["EXPORT_DIR"])
    metrics_dir = Path(os.environ.get("METRICS_DIR", str(export_dir / "metrics")))
    checkpoint_uri = os.environ.get("CHECKPOINT_URI", "")
    blob_account = os.environ.get("BLOB_STORAGE_ACCOUNT", "")
    blob_container = os.environ.get("BLOB_CONTAINER", "")
    onnx_success = os.environ["ONNX_SUCCESS"] == "1"
    jit_success = os.environ["JIT_SUCCESS"] == "1"
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    onnx_metrics, jit_metrics = load_metrics(metrics_dir)

    mlflow_success = upload_to_mlflow(
        task=task,
        export_dir=export_dir,
        metrics_dir=metrics_dir,
        checkpoint_uri=checkpoint_uri,
        onnx_success=onnx_success,
        jit_success=jit_success,
        onnx_metrics=onnx_metrics,
        jit_metrics=jit_metrics,
        timestamp=timestamp,
    )

    if not mlflow_success:
        print("[INFO] Falling back to direct blob storage upload...")
        upload_to_blob_fallback(
            task=task,
            export_dir=export_dir,
            blob_account=blob_account,
            blob_container=blob_container,
            checkpoint_uri=checkpoint_uri,
            timestamp=timestamp,
        )


if __name__ == "__main__":
    main()
