"""Mirror a completed training run to Azure ML and emit lifecycle runhooks.

Framework-agnostic post-training uploader and minimal runhook emitter. Reads files from disk only.

Auth: DefaultAzureCredential (Workload Identity on Kubernetes, az login locally).

Required env (mirror mode):
  AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, AZUREML_WORKSPACE_NAME,
  AZUREML_MODEL_NAME, RUN_ID, OUTPUT_DIR

Optional env:
  AZUREML_ARTIFACTS_DEFAULT_TIMEOUT (default 7200), AZUREML_EXPERIMENT_NAME,
  AZURE_CLIENT_ID, AZURE_AUTHORITY_HOST, REPLAY_RUN_ID,
  AML_SOURCE, TRAINING_FRAMEWORK, AZUREML_RESOURCE_GROUP,
  PROJECT_ID, EXTERNAL_JOB_ID, SLURM_JOB_ID, AML_EXTRA_TAGS,
  DATASET_VERSION, DATASET_MANIFEST_SHA256,
  AZUREML_DATASET_ASSET, CHECKPOINT_BLOB_URL, VERIFY_BLOB_READBACK,
  RUNHOOK_URL, RUNHOOK_BEARER_SCOPE, RUNHOOK_STRICT,
  DATA_CONFIG, BASE_MODEL, BASE_MODEL_REVISION, BATCH_SIZE, MAX_STEPS, SAVE_STEPS,
  DATALOADER_WORKERS, EMBODIMENT_TAG, ISAAC_GROOT_REF
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import pathlib
import platform
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

try:
    import mlflow
except ImportError:  # pragma: no cover - allows --emit-runhook before mlflow install
    mlflow = None  # type: ignore[assignment]

try:
    from azure.ai.ml import MLClient
except ImportError:  # pragma: no cover
    MLClient = None  # type: ignore[assignment,misc]

try:
    from azure.identity import DefaultAzureCredential
except ImportError:  # pragma: no cover
    DefaultAzureCredential = None  # type: ignore[assignment,misc]

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
    "BASE_MODEL_REVISION",
    "BATCH_SIZE",
    "MAX_STEPS",
    "SAVE_STEPS",
    "DATALOADER_WORKERS",
    "EMBODIMENT_TAG",
    "ISAAC_GROOT_REF",
)

_LINEAGE_TAG_ENV = {
    "PROJECT_ID": "project_id",
    "EXTERNAL_JOB_ID": "external.job_id",
    "SLURM_JOB_ID": "slurm.job_id",
    "DATASET_VERSION": "dataset.version",
    "DATASET_MANIFEST_SHA256": "dataset.manifest_sha256",
    "AZUREML_DATASET_ASSET": "dataset.asset",
}

_HASH_CHUNK_BYTES = 1024 * 1024


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _log_trainer_state_metrics(output_dir: pathlib.Path, final_ckpt: pathlib.Path) -> int:
    """Fallback scalar extractor reading HuggingFace/GR00T trainer_state.json log_history."""
    candidates = (final_ckpt / "trainer_state.json", output_dir / "trainer_state.json")
    state_file = next((p for p in candidates if p.is_file()), None)
    if state_file is None:
        return 0

    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"aml_mirror: failed to parse {state_file}: {exc}", file=sys.stderr)
        return 0

    history = payload.get("log_history")
    if not isinstance(history, list):
        return 0

    count = 0
    for entry in history:
        if not isinstance(entry, dict):
            continue
        raw_step = entry.get("step", 0)
        step = int(raw_step) if isinstance(raw_step, (int, float)) else 0
        for key, val in entry.items():
            if key == "step" or isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            mlflow.log_metric(key, float(val), step=step)
            count += 1
    return count


def _write_checkpoint_integrity_files(
    staged: pathlib.Path,
    *,
    run_id: str,
    checkpoint_name: str,
) -> str:
    """Write checksums.sha256 and ready.json (last) into staged model directory."""
    files = sorted(
        (p for p in staged.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(staged).as_posix(),
    )
    lines: list[str] = []
    total_bytes = 0
    for file_path in files:
        rel = file_path.relative_to(staged).as_posix()
        lines.append(f"{_sha256_file(file_path)}  {rel}")
        total_bytes += file_path.stat().st_size

    checksums_path = staged / "checksums.sha256"
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")
    manifest_digest = _sha256_file(checksums_path)

    step_str = checkpoint_name.rsplit("-", 1)[-1]
    ready_payload = {
        "run_id": run_id,
        "checkpoint": checkpoint_name,
        "step": int(step_str) if step_str.isdigit() else None,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "checksums_sha256": manifest_digest,
        "created_at": datetime.now(UTC).isoformat(),
    }
    (staged / "ready.json").write_text(json.dumps(ready_payload, indent=2) + "\n", encoding="utf-8")
    return manifest_digest


def _build_credential() -> Any:
    if DefaultAzureCredential is None:
        raise RuntimeError("azure-identity is required for Azure authentication")
    cred_kwargs: dict[str, str] = {}
    if os.environ.get("AZURE_CLIENT_ID"):
        cred_kwargs["managed_identity_client_id"] = os.environ["AZURE_CLIENT_ID"]
    if os.environ.get("AZURE_AUTHORITY_HOST"):
        cred_kwargs["authority"] = os.environ["AZURE_AUTHORITY_HOST"]
    return DefaultAzureCredential(**cred_kwargs) if cred_kwargs else DefaultAzureCredential()


def _redact_url(url: str) -> str:
    return urlparse(url)._replace(query="").geturl()


def _read_json_if_exists(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(f"aml_mirror: ignoring unreadable JSON {path}: {exc}", file=sys.stderr)
        return {}


def _atomic_write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def emit_runhook(
    output_dir: pathlib.Path,
    *,
    phase: str,
    exit_code: int | None = None,
    timings: dict[str, float] | None = None,
    aml_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update run-manifest.json and optionally POST lifecycle state to RUNHOOK_URL (API-2)."""
    manifest_path = output_dir / "run-manifest.json"
    existing = _read_json_if_exists(manifest_path)
    hydration = _read_json_if_exists(output_dir / "hydration-receipt.json")

    phase_upper = phase.strip().upper()
    status_map = {
        "PREFLIGHT": "SUBMITTED",
        "HYDRATING": "PREPARING",
        "RUNNING": "RUNNING",
        "MIRRORING": "FINALIZING",
        "COMPLETED": "COMPLETED",
        "FAILED": "FAILED",
    }
    status = status_map.get(phase_upper, phase_upper)
    run_id = os.environ.get("RUN_ID") or existing.get("run_id") or "unknown-run"
    attempt = int(os.environ.get("RUN_ATTEMPT", existing.get("attempt", 1)))
    now_iso = datetime.now(UTC).isoformat()

    merged_timings: dict[str, float] = dict(existing.get("timings_s") or {})
    if timings:
        for key, val in timings.items():
            if val is not None:
                merged_timings[key] = round(float(val), 3)

    dataset_block: dict[str, Any] = dict(existing.get("dataset") or {})
    if os.environ.get("AZUREML_DATASET_ASSET"):
        dataset_block["dataset_asset"] = os.environ["AZUREML_DATASET_ASSET"]
    if os.environ.get("DATASET_VERSION") or hydration.get("dataset_version"):
        dataset_block["dataset_version"] = os.environ.get("DATASET_VERSION") or hydration.get("dataset_version")
    if os.environ.get("DATASET_MANIFEST_SHA256") or hydration.get("manifest_sha256"):
        dataset_block["manifest_sha256"] = os.environ.get("DATASET_MANIFEST_SHA256") or hydration.get("manifest_sha256")
    if hydration.get("throughput_mb_s") is not None:
        dataset_block["hydration_throughput_mb_s"] = hydration["throughput_mb_s"]
    if hydration.get("source_kind"):
        dataset_block["source_kind"] = hydration["source_kind"]

    payload: dict[str, Any] = {
        "schema": "pai-training-run-manifest/v1",
        "project_id": os.environ.get("PROJECT_ID") or existing.get("project_id"),
        "run_id": run_id,
        "external_job_id": os.environ.get("EXTERNAL_JOB_ID") or existing.get("external_job_id"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID") or existing.get("slurm_job_id"),
        "attempt": attempt,
        "phase": phase_upper,
        "status": status,
        "exit_code": exit_code if exit_code is not None else existing.get("exit_code"),
        "created_at": existing.get("created_at") or now_iso,
        "updated_at": now_iso,
        "compute": {
            "target": os.environ.get("COMPUTE_TARGET", "external-gpu"),
            "node": os.environ.get("SLURMD_NODENAME") or platform.node(),
            "gpu_product": os.environ.get("DETECTED_GPU_PRODUCT") or existing.get("compute", {}).get("gpu_product"),
        },
        "dataset": dataset_block,
        "runtime": {
            "framework": os.environ.get("TRAINING_FRAMEWORK", "groot"),
            "base_model": os.environ.get("BASE_MODEL"),
            "base_model_revision": os.environ.get("BASE_MODEL_REVISION"),
            "isaac_groot_ref": os.environ.get("ISAAC_GROOT_REF"),
            "max_steps": os.environ.get("MAX_STEPS"),
            "batch_size": os.environ.get("BATCH_SIZE"),
        },
        "timings_s": merged_timings,
        "aml": aml_result if aml_result is not None else existing.get("aml"),
    }
    _atomic_write_json(manifest_path, payload)

    hook_url = os.environ.get("RUNHOOK_URL", "").strip()
    if hook_url:
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": f"{run_id}-{phase_upper.lower()}-{attempt}",
        }
        scope = os.environ.get("RUNHOOK_BEARER_SCOPE", "").strip()
        try:
            if scope:
                token = _build_credential().get_token(scope).token
                headers["Authorization"] = f"Bearer {token}"
            req = urllib.request.Request(
                hook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"runhook: POST {_redact_url(hook_url)} phase={phase_upper} -> HTTP {resp.status}")
        except Exception as exc:
            msg = f"runhook: POST {_redact_url(hook_url)} phase={phase_upper} failed: {exc}"
            if os.environ.get("RUNHOOK_STRICT", "false").lower() == "true":
                raise RuntimeError(msg) from exc
            print(msg, file=sys.stderr)

    return payload


def _ingest_provenance_files(output_dir: pathlib.Path) -> None:
    """Stamp hydration-receipt.json and run-manifest.json metadata onto the active MLflow run."""
    hydration_path = output_dir / "hydration-receipt.json"
    manifest_path = output_dir / "run-manifest.json"
    hydration = _read_json_if_exists(hydration_path)
    manifest = _read_json_if_exists(manifest_path)

    if hydration:
        if hydration.get("dataset_version") and not os.environ.get("DATASET_VERSION"):
            mlflow.set_tag("dataset.version", str(hydration["dataset_version"]))
        if hydration.get("manifest_sha256") and not os.environ.get("DATASET_MANIFEST_SHA256"):
            mlflow.set_tag("dataset.manifest_sha256", str(hydration["manifest_sha256"]))
        if hydration.get("dataset_asset") and not os.environ.get("AZUREML_DATASET_ASSET"):
            mlflow.set_tag("dataset.asset", str(hydration["dataset_asset"]))
        if hydration.get("source_kind"):
            mlflow.set_tag("dataset.source_kind", str(hydration["source_kind"]))
        if isinstance(hydration.get("throughput_mb_s"), (int, float)):
            mlflow.log_metric("hydration/throughput_mb_s", float(hydration["throughput_mb_s"]), step=0)
        if isinstance(hydration.get("elapsed_s"), (int, float)):
            mlflow.log_metric("hydration/elapsed_s", float(hydration["elapsed_s"]), step=0)

    if manifest:
        compute = manifest.get("compute") or {}
        if compute.get("node"):
            mlflow.set_tag("compute.node", str(compute["node"]))
        if compute.get("gpu_product"):
            mlflow.set_tag("compute.gpu", str(compute["gpu_product"]))
        if manifest.get("slurm_job_id") and not os.environ.get("SLURM_JOB_ID"):
            mlflow.set_tag("slurm.job_id", str(manifest["slurm_job_id"]))
        if manifest.get("external_job_id") and not os.environ.get("EXTERNAL_JOB_ID"):
            mlflow.set_tag("external.job_id", str(manifest["external_job_id"]))
        for timing_key, timing_val in (manifest.get("timings_s") or {}).items():
            if isinstance(timing_val, (int, float)):
                mlflow.log_metric(f"timing/{timing_key}", float(timing_val), step=0)

    provenance_files = [
        p for p in (hydration_path, manifest_path, output_dir / "runtime-provenance.json") if p.is_file()
    ]
    if provenance_files:
        prov_dir = pathlib.Path(tempfile.mkdtemp(prefix="aml-prov-", dir=str(output_dir)))
        try:
            for src in provenance_files:
                shutil.copy2(src, prov_dir / src.name)
            mlflow.log_artifacts(str(prov_dir), artifact_path="provenance")
        finally:
            shutil.rmtree(prov_dir, ignore_errors=True)


def _upload_checkpoint_to_blob_url(
    staged: pathlib.Path,
    checkpoint_blob_url: str,
    credential: Any,
    *,
    verify_readback: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Publish a write-once marker only after the remote payload matches its checksum manifest."""
    from azure.storage.blob import ContainerClient

    if overwrite or not verify_readback:
        raise ValueError("Sealed Blob publication requires read-back and a fresh, write-once prefix")
    parsed = urlparse(checkpoint_blob_url)
    redacted = _redact_url(checkpoint_blob_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Invalid CHECKPOINT_BLOB_URL: {redacted}")
    if parsed.query:
        raise ValueError(f"SAS query string is not permitted in CHECKPOINT_BLOB_URL: {redacted}")

    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"CHECKPOINT_BLOB_URL must be https://<account>.blob.core.windows.net/<container>/<prefix>: {redacted}"
        )

    container, prefix = parts[0], parts[1].rstrip("/")
    endpoint = f"https://{parsed.netloc}"
    container_client = ContainerClient(endpoint, container, credential=credential)

    manifest_path = staged / "checksums.sha256"
    ready_path = staged / "ready.json"
    manifest = manifest_path.read_bytes()
    marker = ready_path.read_bytes()
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    if not manifest or json.loads(marker)["checksums_sha256"] != manifest_sha:
        raise ValueError("Invalid checkpoint checksum manifest or ready marker")
    payload = sorted(
        (p for p in staged.rglob("*") if p.is_file() and p not in (manifest_path, ready_path)),
        key=lambda p: p.relative_to(staged).as_posix(),
    )

    def remote_sha256(name: str) -> str:
        digest = hashlib.sha256()
        for chunk in container_client.download_blob(name).chunks():
            digest.update(chunk)
        return digest.hexdigest()

    # Creating the manifest first reserves this attempt prefix, including across competing writers.
    manifest_name = f"{prefix}/checksums.sha256"
    container_client.upload_blob(name=manifest_name, data=io.BytesIO(manifest), overwrite=False)
    if remote_sha256(manifest_name) != manifest_sha:
        raise RuntimeError("Checkpoint Blob read-back SHA-256 mismatch for checksums.sha256")

    readback_manifest = hashlib.sha256()
    for path in payload:
        rel = path.relative_to(staged).as_posix()
        name = f"{prefix}/{rel}"
        with path.open("rb") as fh:
            container_client.upload_blob(name=name, data=fh, overwrite=False)
        readback_manifest.update(f"{remote_sha256(name)}  {rel}\n".encode())
    if readback_manifest.hexdigest() != manifest_sha:
        raise RuntimeError("Checkpoint Blob read-back SHA-256 mismatch against the sealed payload manifest")

    marker_name = f"{prefix}/ready.json"
    container_client.upload_blob(name=marker_name, data=io.BytesIO(marker), overwrite=False)
    if remote_sha256(marker_name) != hashlib.sha256(marker).hexdigest():
        raise RuntimeError("Checkpoint Blob read-back SHA-256 mismatch for ready.json")

    return {
        "blob_url": redacted,
        "uploaded_files": len(payload) + 2,
        "ready_written_last": True,
        "readback_verified": True,
    }


def _parse_cli_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror training artifacts to Azure ML or emit lifecycle runhooks")
    parser.add_argument(
        "--emit-runhook",
        action="store_true",
        help="Write run-manifest.json and optional RUNHOOK_URL event",
    )
    parser.add_argument("--phase", default="RUNNING", help="Lifecycle phase for --emit-runhook")
    parser.add_argument("--exit-code", type=int, default=None, help="Process exit code for terminal runhook")
    parser.add_argument("--hydrate-s", type=float, default=None, help="Hydration duration in seconds")
    parser.add_argument("--bootstrap-s", type=float, default=None, help="Runtime bootstrap duration in seconds")
    parser.add_argument("--train-s", type=float, default=None, help="Training duration in seconds")
    parser.add_argument("--mirror-s", type=float, default=None, help="AML mirror duration in seconds")
    return parser.parse_args(list(argv) if argv is not None else [])


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    if args.emit_runhook:
        out_raw = os.environ.get("OUTPUT_DIR", "")
        if not out_raw:
            print("aml_mirror --emit-runhook: OUTPUT_DIR is required", file=sys.stderr)
            return 1
        timings = {
            k: v
            for k, v in {
                "hydrate_s": args.hydrate_s,
                "bootstrap_s": args.bootstrap_s,
                "train_s": args.train_s,
                "mirror_s": args.mirror_s,
            }.items()
            if v is not None
        }
        emit_runhook(
            pathlib.Path(out_raw),
            phase=args.phase,
            exit_code=args.exit_code,
            timings=timings or None,
        )
        return 0

    if mlflow is None or MLClient is None or DefaultAzureCredential is None:
        print("aml_mirror: mlflow, azure-ai-ml, and azure-identity must be installed", file=sys.stderr)
        return 1

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

    cred = _build_credential()
    ml_client = MLClient(
        credential=cred,
        subscription_id=os.environ["AZURE_SUBSCRIPTION_ID"],
        resource_group_name=os.environ["AZURE_RESOURCE_GROUP"],
        workspace_name=os.environ["AZUREML_WORKSPACE_NAME"],
    )
    ws = ml_client.workspaces.get(os.environ["AZUREML_WORKSPACE_NAME"])
    mlflow.set_tracking_uri(ws.mlflow_tracking_uri)
    if hasattr(mlflow, "set_registry_uri"):
        mlflow.set_registry_uri(ws.mlflow_tracking_uri)
    experiment_name = os.environ.get("AZUREML_EXPERIMENT_NAME") or os.environ["AZUREML_MODEL_NAME"]
    mlflow.set_experiment(experiment_name)
    print(f"connected to workspace: {ws.name} ({ws.location})")

    run_name = os.environ["RUN_ID"]
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tag("osmo.run_id", run_name)
        mlflow.set_tag("osmo.output_dir", str(output_dir))
        if os.environ.get("REPLAY_RUN_ID"):
            mlflow.set_tag("osmo.replay", "true")
            mlflow.set_tag("osmo.replay_source", os.environ["REPLAY_RUN_ID"])
        for env_key, tag_key in _LINEAGE_TAG_ENV.items():
            if os.environ.get(env_key):
                mlflow.set_tag(tag_key, os.environ[env_key])
        for raw_pair in os.environ.get("AML_EXTRA_TAGS", "").split(","):
            pair = raw_pair.strip()
            if "=" in pair:
                extra_k, extra_v = pair.split("=", 1)
                if extra_k.strip():
                    mlflow.set_tag(extra_k.strip(), extra_v.strip())
        mlflow.set_tag("framework", os.environ.get("TRAINING_FRAMEWORK", "lerobot"))
        mlflow.set_tag("source", os.environ.get("AML_SOURCE", "osmo-replay"))

        params = {k: os.environ[k] for k in _PARAM_ENV if os.environ.get(k)}
        if params:
            mlflow.log_params(params)

        _ingest_provenance_files(output_dir)

        metric_count = 0
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
        final_step_text = final.name.rsplit("-", 1)[-1]
        mlflow.log_metric("training.checkpoint_count", len(ckpts))
        if final_step_text.isdigit():
            mlflow.log_metric("training.final_checkpoint_step", int(final_step_text))

        if metric_count == 0:
            state_metrics = _log_trainer_state_metrics(output_dir, final)
            if state_metrics > 0:
                metric_count = state_metrics
                print(f"logged {metric_count} metric points from trainer_state.json")

        blob_receipt: dict[str, Any] | None = None
        checkpoint_blob_url = os.environ.get("CHECKPOINT_BLOB_URL", "").strip()
        verify_readback = os.environ.get("VERIFY_BLOB_READBACK", "true").lower() != "false"
        blob_overwrite = os.environ.get("CHECKPOINT_BLOB_OVERWRITE", "false").lower() == "true"

        staging_root = output_dir / ".aml-staging"
        staging_root.mkdir(exist_ok=True)
        staged = pathlib.Path(tempfile.mkdtemp(prefix="aml-upload-", dir=str(staging_root)))
        try:
            for src in final.rglob("*"):
                if src.is_dir() or src.name in SKIP_FILES:
                    continue
                relative = src.relative_to(final)
                if relative.as_posix() in ("checksums.sha256", "ready.json"):
                    continue
                dst = staged / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)

            manifest_digest = _write_checkpoint_integrity_files(
                staged,
                run_id=run_name,
                checkpoint_name=final.name,
            )
            mlflow.set_tag("checkpoint.name", final.name)
            mlflow.set_tag("checkpoint.checksums_sha256", manifest_digest)

            size_gib = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file()) / 1024**3
            mlflow.log_metric("training.model_size_gib", size_gib)
            print(f"staged {final.name} ({size_gib:.2f} GiB, checksums={manifest_digest[:12]}...)")

            if checkpoint_blob_url:
                blob_receipt = _upload_checkpoint_to_blob_url(
                    staged,
                    checkpoint_blob_url,
                    cred,
                    verify_readback=verify_readback,
                    overwrite=blob_overwrite,
                )
                mlflow.set_tag("checkpoint.blob_url", blob_receipt["blob_url"])
                mlflow.set_tag("checkpoint.blob_readback_verified", str(blob_receipt["readback_verified"]).lower())
                print(
                    f"uploaded write-once checkpoint to {blob_receipt['blob_url']} "
                    f"(files={blob_receipt['uploaded_files']}, readback_verified={blob_receipt['readback_verified']})"
                )

            mlflow.log_artifacts(str(staged), artifact_path="model")
        finally:
            shutil.rmtree(staged, ignore_errors=True)

        registry_client = mlflow.MlflowClient()
        model_name = os.environ["AZUREML_MODEL_NAME"]
        try:
            registry_client.create_registered_model(model_name)
        except mlflow.exceptions.MlflowException as error:
            if getattr(error, "error_code", None) != "RESOURCE_ALREADY_EXISTS":
                raise
        registered = registry_client.create_model_version(
            name=model_name,
            source=mlflow.get_artifact_uri("model"),
            run_id=run.info.run_id,
        )
        result_summary: dict[str, Any] = {
            "mlflow_run_id": run.info.run_id,
            "model_name": registered.name,
            "model_version": registered.version,
            "checkpoint": str(final),
            "metrics_logged": metric_count,
            "checksums_sha256": manifest_digest,
        }
        if blob_receipt is not None:
            result_summary["checkpoint_blob"] = blob_receipt

        if (output_dir / "run-manifest.json").is_file() or os.environ.get("RUNHOOK_URL"):
            emit_runhook(output_dir, phase="COMPLETED", exit_code=0, aml_result=result_summary)

        print(json.dumps(result_summary, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
