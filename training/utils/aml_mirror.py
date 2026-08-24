"""Mirror a completed training run to Azure ML.

Framework-agnostic post-training uploader. Reads files from disk only.

Auth: DefaultAzureCredential (Workload Identity on AKS, az login locally).

Required env:
  AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME,
  AZUREML_MODEL_NAME, RUN_ID, OUTPUT_DIR

Optional env:
  AZUREML_ARTIFACTS_DEFAULT_TIMEOUT (default 7200), REPLAY_RUN_ID,
  AML_SOURCE, TRAINING_FRAMEWORK, AZUREML_RESOURCE_GROUP,
  DATA_CONFIG, BASE_MODEL, BATCH_SIZE, MAX_STEPS, SAVE_STEPS,
  DATALOADER_WORKERS, EMBODIMENT_TAG, ISAAC_GROOT_REF
"""

from __future__ import annotations

import glob
import json
import os
import pathlib
import shutil
import sys
import tempfile

import mlflow
from azure.ai.ml import MLClient
from azure.identity import DefaultAzureCredential

REQUIRED_ENV = (
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZUREML_WORKSPACE_NAME",
    "AZUREML_MODEL_NAME",
    "RUN_ID",
    "OUTPUT_DIR",
)
SKIP_FILES = frozenset(
    {
        "optimizer.pt",
        "optimizer.bin",
        "scheduler.pt",
        "rng_state.pth",
        "training_args.bin",
    }
)

_PARAM_ENV = (
    "DATA_CONFIG",
    "BASE_MODEL",
    "BATCH_SIZE",
    "MAX_STEPS",
    "SAVE_STEPS",
    "DATALOADER_WORKERS",
    "EMBODIMENT_TAG",
    "ISAAC_GROOT_REF",
)


def _log_tensorboard_metrics(tb_dir: pathlib.Path) -> int:
    """Parse tensorboard event files and log scalars as MLflow metrics."""
    try:
        from tbparse import SummaryReader
    except ImportError:
        print("tbparse not installed, skipping metric extraction from tensorboard")
        return 0

    reader = SummaryReader(str(tb_dir))
    scalars = reader.scalars
    if scalars.empty:
        return 0

    count = 0
    for tag in scalars["tag"].unique():
        tag_data = scalars[scalars["tag"] == tag].sort_values("step")
        for _, row in tag_data.iterrows():
            mlflow.log_metric(tag, row["value"], step=int(row["step"]))
            count += 1
    return count


def main() -> int:
    if not os.environ.get("AZURE_RESOURCE_GROUP") and os.environ.get("AZUREML_RESOURCE_GROUP"):
        os.environ["AZURE_RESOURCE_GROUP"] = os.environ["AZUREML_RESOURCE_GROUP"]

    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"aml_mirror: missing env vars: {missing}", file=sys.stderr)
        return 1

    os.environ.setdefault("AZUREML_ARTIFACTS_DEFAULT_TIMEOUT", "7200")

    output_dir = pathlib.Path(os.environ["OUTPUT_DIR"])
    if not output_dir.exists():
        print(f"aml_mirror: {output_dir} does not exist", file=sys.stderr)
        return 1

    cred = DefaultAzureCredential()
    ml_client = MLClient(
        credential=cred,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group_name=os.environ["AZURE_RESOURCE_GROUP"],
        workspace_name=os.environ["AZUREML_WORKSPACE_NAME"],
    )
    ws = ml_client.workspaces.get(os.environ["AZUREML_WORKSPACE_NAME"])
    mlflow.set_tracking_uri(ws.mlflow_tracking_uri)
    mlflow.set_experiment(os.environ["AZUREML_MODEL_NAME"])
    print(f"connected to workspace: {ws.name} ({ws.location})")

    run_name = os.environ["RUN_ID"]
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tag("osmo.run_id", run_name)
        mlflow.set_tag("osmo.output_dir", str(output_dir))
        if os.environ.get("REPLAY_RUN_ID"):
            mlflow.set_tag("osmo.replay", "true")
            mlflow.set_tag("osmo.replay_source", os.environ["REPLAY_RUN_ID"])
        mlflow.set_tag("framework", os.environ.get("TRAINING_FRAMEWORK", "lerobot"))
        mlflow.set_tag("source", os.environ.get("AML_SOURCE", "osmo-replay"))

        params = {k: os.environ[k] for k in _PARAM_ENV if os.environ.get(k)}
        if params:
            mlflow.log_params(params)

        tb_dir = output_dir / "runs"
        if tb_dir.exists():
            mlflow.log_artifacts(str(tb_dir), artifact_path="tensorboard")
            metric_count = _log_tensorboard_metrics(tb_dir)
            print(f"logged tensorboard from {tb_dir} ({metric_count} metric points)")

        ckpts = sorted(
            glob.glob(str(output_dir / "checkpoint-*")),
            key=lambda p: int(p.rsplit("-", 1)[-1]) if p.rsplit("-", 1)[-1].isdigit() else -1,
        )
        if not ckpts:
            print("aml_mirror: no checkpoint-* dirs found", file=sys.stderr)
            mlflow.end_run(status="FAILED")
            return 1
        final = pathlib.Path(ckpts[-1])

        staging_root = output_dir / ".aml-staging"
        staging_root.mkdir(exist_ok=True)
        staged = pathlib.Path(tempfile.mkdtemp(prefix="aml-upload-", dir=str(staging_root)))
        try:
            for src in final.rglob("*"):
                if src.is_dir() or src.name in SKIP_FILES:
                    continue
                dst = staged / src.relative_to(final)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)

            size_gib = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file()) / 1024**3
            print(f"staged {final.name} ({size_gib:.2f} GiB)")
            mlflow.log_artifacts(str(staged), artifact_path="model")
        finally:
            shutil.rmtree(staged, ignore_errors=True)

        registered = mlflow.register_model(
            model_uri=f"runs:/{run.info.run_id}/model",
            name=os.environ["AZUREML_MODEL_NAME"],
        )
        print(
            json.dumps(
                {
                    "mlflow_run_id": run.info.run_id,
                    "model_name": registered.name,
                    "model_version": registered.version,
                    "checkpoint": str(final),
                },
                indent=2,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
