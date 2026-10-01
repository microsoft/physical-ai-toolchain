"""Azure ML GPU smoke test.

Runs a short GPU training loop inside an Azure ML job and checks the services a
training job depends on: identity and workspace access, MLflow metrics and
artifacts, checkpoints in the job output, Azure Storage uploads, and the model
registry. Every check is recorded in ``smoke-summary.json``. The process exits
non-zero when any check fails, after running every check it can.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

SUMMARY_FILENAME = "smoke-summary.json"
CHECKPOINT_PREFIX = "checkpoint-step-"
ARTIFACT_DIR = "smoke"
METRIC_LOSS = "smoke/loss"
METRIC_STEP_TIME = "smoke/step_time_ms"
METRIC_TFLOPS = "smoke/matmul_tflops"
DEFAULT_EXPERIMENT = "gpu-smoke"
DEFAULT_MODEL_NAME = "gpu-smoke-test"

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"

# Share of the initial loss the final loss must fall below for the training check to pass.
LOSS_REDUCTION_THRESHOLD = 0.5
METRIC_READ_BACK_TIMEOUT_S = 60.0
# Metrics are buffered and sent with log_batch; a per-step call to Azure ML's MLflow
# endpoint costs about a second each. MLflow accepts up to 1000 metrics per batch.
METRIC_BATCH_LIMIT = 500


@dataclass(frozen=True)
class SmokeConfig:
    """Options for one smoke test run."""

    steps: int
    checkpoint_interval: int
    batch_size: int
    matrix_size: int
    matmul_iterations: int
    output_dir: Path
    experiment_name: str
    model_name: str
    register_model: bool
    azure: bool
    allow_cpu: bool
    seed: int


@dataclass
class CheckResult:
    """Outcome of a single named check."""

    name: str
    status: str
    duration_s: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_s": round(self.duration_s, 3),
            "details": self.details,
            "error": self.error,
        }


class SkipCheck(Exception):
    """Raised by a check that doesn't apply to this run."""


@dataclass
class SmokeRunner:
    """Run named checks in order and record every outcome.

    A failing check doesn't stop the run. Checks that depend on it are skipped,
    so one run reports every problem it can reach.
    """

    results: list[CheckResult] = field(default_factory=list)

    def run(
        self,
        name: str,
        check: Callable[[], dict[str, Any] | None],
        *,
        requires: Sequence[str] = (),
    ) -> CheckResult:
        blocked = [dependency for dependency in requires if not self.passed(dependency)]
        if blocked:
            result = CheckResult(name=name, status=SKIPPED, duration_s=0.0, details={"blocked_by": blocked})
            self._record(result)
            return result

        started = time.monotonic()
        try:
            details = check() or {}
            result = CheckResult(name=name, status=PASSED, duration_s=time.monotonic() - started, details=details)
        except SkipCheck as exc:
            result = CheckResult(
                name=name,
                status=SKIPPED,
                duration_s=time.monotonic() - started,
                details={"reason": str(exc)},
            )
        except Exception as exc:  # Report every failure instead of stopping the run.
            _LOGGER.exception("Check %s failed", name)
            result = CheckResult(
                name=name,
                status=FAILED,
                duration_s=time.monotonic() - started,
                error=f"{type(exc).__name__}: {exc}",
            )
        self._record(result)
        return result

    def passed(self, name: str) -> bool:
        return any(result.name == name and result.status == PASSED for result in self.results)

    @property
    def succeeded(self) -> bool:
        return bool(self.results) and all(result.status != FAILED for result in self.results)

    def _record(self, result: CheckResult) -> None:
        self.results.append(result)
        suffix = f": {result.error}" if result.error else ""
        _LOGGER.info("[%s] %s (%.2fs)%s", result.status.upper(), result.name, result.duration_s, suffix)


def _default_bootstrap(experiment_name: str) -> Any:
    from training.utils.context import bootstrap_azure_ml

    return bootstrap_azure_ml(experiment_name=experiment_name)


def _load_mlflow() -> Any:
    import mlflow

    return mlflow


def _load_torch() -> Any:
    import torch

    return torch


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nvidia_smi_driver_version() -> str | None:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = completed.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


class GpuSmokeTest:
    """Stateful sequence of smoke checks sharing a device, model, and MLflow run."""

    def __init__(
        self,
        config: SmokeConfig,
        *,
        torch_module: Any | None = None,
        mlflow_module: Any | None = None,
        bootstrap: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config
        self.runner = SmokeRunner()
        self._torch = torch_module
        self._mlflow = mlflow_module
        self._bootstrap = bootstrap or _default_bootstrap
        self.device: Any = None
        self.model: Any = None
        self.context: Any = None
        self.run_id: str | None = None
        self.managed_run = False
        self.checkpoints: list[Path] = []
        self.final_step = 0
        self.logged_loss_steps = 0
        self.metric_log_failures = 0
        self.metric_log_seconds = 0.0
        self._metric_buffer: list[Any] = []
        self.matmul_tflops: float | None = None
        self.started_at = datetime.now(UTC)

    @property
    def torch(self) -> Any:
        if self._torch is None:
            self._torch = _load_torch()
        return self._torch

    @property
    def mlflow(self) -> Any:
        if self._mlflow is None:
            self._mlflow = _load_mlflow()
        return self._mlflow

    def execute(self) -> dict[str, Any]:
        run = self.runner.run
        run("device", self.check_device)
        run("matmul", self.check_matmul, requires=["device"])
        if self.config.azure:
            run("azure_workspace", self.check_azure_workspace)
            run("mlflow_run", self.check_mlflow_run, requires=["azure_workspace"])
        run("training", self.check_training, requires=["device"])
        if self.config.azure:
            run("mlflow_metrics", self.check_mlflow_metrics, requires=["mlflow_run", "training"])
        run("checkpoints", self.check_checkpoints, requires=["training"])
        if self.config.azure:
            run("mlflow_artifacts", self.check_mlflow_artifacts, requires=["mlflow_run", "checkpoints"])
            run("storage", self.check_storage, requires=["azure_workspace", "checkpoints"])
            run("model_registry", self.check_model_registry, requires=["azure_workspace", "checkpoints"])
        summary = self.build_summary()
        self.write_summary(summary)
        self.finish_mlflow_run()
        return summary

    # Checks -----------------------------------------------------------------

    def check_device(self) -> dict[str, Any]:
        torch = self.torch
        versions = {
            "torch_version": torch.__version__,
            "torch_cuda_version": torch.version.cuda,
            "python_version": platform.python_version(),
        }
        if torch.cuda.is_available():
            self.device = torch.device("cuda:0")
            properties = torch.cuda.get_device_properties(0)
            return {
                **versions,
                "device": "cuda:0",
                "device_count": torch.cuda.device_count(),
                "name": properties.name,
                "compute_capability": f"{properties.major}.{properties.minor}",
                "total_memory_gib": round(properties.total_memory / 1024**3, 2),
                "driver_version": _nvidia_smi_driver_version(),
                "cudnn_version": torch.backends.cudnn.version(),
            }
        if not self.config.allow_cpu:
            raise RuntimeError(
                "CUDA isn't available to PyTorch. Check the job's GPU request, the node's NVIDIA driver, "
                "and that the image's CUDA version is supported by that driver."
            )
        self.device = torch.device("cpu")
        return {**versions, "device": "cpu", "note": "CPU fallback allowed by --allow-cpu"}

    def check_matmul(self) -> dict[str, Any]:
        torch = self.torch
        size = self.config.matrix_size
        dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        generator = torch.Generator(device="cpu").manual_seed(self.config.seed)
        left = torch.randn(size, size, generator=generator).to(self.device, dtype)
        right = torch.randn(size, size, generator=generator).to(self.device, dtype)

        product = torch.matmul(left, right)
        self._synchronize()
        started = time.perf_counter()
        for _ in range(self.config.matmul_iterations):
            product = torch.matmul(left, right)
        self._synchronize()
        elapsed = time.perf_counter() - started
        tflops = 2 * size**3 * self.config.matmul_iterations / elapsed / 1e12

        # Compare a slice against a float64 CPU reference to catch silent compute errors.
        rows = min(size, 64)
        reference = left[:rows].double().cpu() @ right.double().cpu()
        observed = product[:rows].double().cpu()
        relative_error = float((observed - reference).abs().max() / reference.abs().max())
        tolerance = 1e-2 if dtype == torch.float16 else 1e-4
        if not relative_error <= tolerance:
            raise RuntimeError(f"matmul relative error {relative_error:.3e} exceeds {tolerance:.0e}")

        self.matmul_tflops = tflops
        return {
            "matrix_size": size,
            "dtype": str(dtype).removeprefix("torch."),
            "iterations": self.config.matmul_iterations,
            "seconds": round(elapsed, 4),
            "tflops": round(tflops, 3),
            "max_relative_error": relative_error,
        }

    def check_azure_workspace(self) -> dict[str, Any]:
        self.context = self._bootstrap(self.config.experiment_name)
        storage = getattr(self.context, "storage", None)
        return {
            "workspace": self.context.workspace_name,
            "tracking_uri_scheme": str(self.context.tracking_uri).split(":", 1)[0],
            "storage_container": storage.container_name if storage else None,
            "managed_identity_client_id_set": bool(os.environ.get("AZURE_CLIENT_ID")),
        }

    def check_mlflow_run(self) -> dict[str, Any]:
        mlflow = self.mlflow
        self.managed_run = bool(os.environ.get("MLFLOW_RUN_ID"))
        active = mlflow.active_run()
        if active is None:
            active = mlflow.start_run() if self.managed_run else mlflow.start_run(run_name="gpu-smoke")
        self.run_id = active.info.run_id
        params = {
            "smoke_steps": self.config.steps,
            "smoke_checkpoint_interval": self.config.checkpoint_interval,
            "smoke_batch_size": self.config.batch_size,
            "smoke_matrix_size": self.config.matrix_size,
            "smoke_device": str(self.device),
        }
        mlflow.log_params(params)
        mlflow.set_tags({"smoke_test": "azureml-gpu", "entrypoint": "training/smoke/scripts/azureml_gpu_smoke.py"})
        return {"run_id": self.run_id, "azureml_managed_run": self.managed_run}

    def check_training(self) -> dict[str, Any]:
        torch = self.torch
        torch.manual_seed(self.config.seed)
        features, hidden = 32, 128
        inputs = torch.randn(self.config.batch_size * 8, features)
        true_weights = torch.randn(features, 1)
        targets = inputs @ true_weights + 0.01 * torch.randn(inputs.shape[0], 1)
        inputs, targets = inputs.to(self.device), targets.to(self.device)

        model = torch.nn.Sequential(
            torch.nn.Linear(features, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(hidden, 1),
        ).to(self.device)
        optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = torch.nn.MSELoss()

        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        if self.matmul_tflops is not None:
            self._queue_metric(METRIC_TFLOPS, self.matmul_tflops, step=0)

        initial_loss = final_loss = None
        started = time.perf_counter()
        for step in range(1, self.config.steps + 1):
            step_started = time.perf_counter()
            batch = torch.randint(0, inputs.shape[0], (self.config.batch_size,), device=self.device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(inputs[batch]), targets[batch])
            loss.backward()
            optimizer.step()
            loss_value = float(loss.detach().cpu())
            if initial_loss is None:
                initial_loss = loss_value
            final_loss = loss_value
            step_time_ms = (time.perf_counter() - step_started) * 1000
            self._queue_metric(METRIC_LOSS, loss_value, step=step)
            self._queue_metric(METRIC_STEP_TIME, step_time_ms, step=step)
            if step % self.config.checkpoint_interval == 0 or step == self.config.steps:
                self.checkpoints.append(self._save_checkpoint(model, optimizer, step, loss_value))
                self._flush_metrics()
            elif len(self._metric_buffer) >= METRIC_BATCH_LIMIT:
                self._flush_metrics()
        self._flush_metrics()
        self._synchronize()

        self.model = model
        self.final_step = self.config.steps
        if final_loss is None or initial_loss is None or not final_loss < initial_loss * LOSS_REDUCTION_THRESHOLD:
            raise RuntimeError(
                f"loss didn't fall enough: initial {initial_loss}, final {final_loss}, "
                f"threshold {LOSS_REDUCTION_THRESHOLD} of initial"
            )
        return {
            "steps": self.config.steps,
            "initial_loss": initial_loss,
            "final_loss": final_loss,
            "seconds": round(time.perf_counter() - started, 3),
            "metric_log_seconds": round(self.metric_log_seconds, 3),
            "checkpoints_written": len(self.checkpoints),
            "metric_log_failures": self.metric_log_failures,
        }

    def check_mlflow_metrics(self) -> dict[str, Any]:
        if self.metric_log_failures:
            raise RuntimeError(f"{self.metric_log_failures} metric writes failed during training")
        client = self.mlflow.tracking.MlflowClient()
        expected = self.logged_loss_steps
        deadline = time.monotonic() + METRIC_READ_BACK_TIMEOUT_S
        while True:
            history = list(client.get_metric_history(self.run_id, METRIC_LOSS))
            if len(history) >= expected or time.monotonic() >= deadline:
                break
            time.sleep(5)
        if len(history) < expected:
            raise RuntimeError(f"read back {len(history)} of {expected} {METRIC_LOSS} values from MLflow")
        return {"metric": METRIC_LOSS, "logged": expected, "read_back": len(history)}

    def check_checkpoints(self) -> dict[str, Any]:
        torch = self.torch
        expected = self._expected_checkpoint_steps()
        found = sorted(path.name for path in self.config.output_dir.glob(f"{CHECKPOINT_PREFIX}*.pt"))
        missing = [
            f"{CHECKPOINT_PREFIX}{step:06d}.pt" for step in expected if f"{CHECKPOINT_PREFIX}{step:06d}.pt" not in found
        ]
        if missing:
            raise RuntimeError(f"missing checkpoints in {self.config.output_dir}: {missing}")

        final_path = self.checkpoints[-1]
        state = torch.load(final_path, map_location="cpu", weights_only=True)
        if state.get("step") != self.final_step:
            raise RuntimeError(f"final checkpoint step {state.get('step')} doesn't match {self.final_step}")
        for name, tensor in self.model.state_dict().items():
            if not torch.equal(state["model_state"][name], tensor.detach().cpu()):
                raise RuntimeError(f"reloaded checkpoint tensor {name} doesn't match the trained model")
        return {
            "output_dir": str(self.config.output_dir),
            "files": found,
            "final_checkpoint": final_path.name,
            "final_checkpoint_bytes": final_path.stat().st_size,
            "final_checkpoint_sha256": _sha256(final_path),
        }

    def check_mlflow_artifacts(self) -> dict[str, Any]:
        final_path = self.checkpoints[-1]
        self.mlflow.log_artifact(str(final_path), artifact_path=ARTIFACT_DIR)
        # Download through the runs:/ URI form training publishes for resume. MLflow 3 can't list
        # runs:/ paths on Azure ML (its logged-model search returns HTTP 404), but downloads work.
        artifact_uri = f"runs:/{self.run_id}/{ARTIFACT_DIR}/{final_path.name}"
        expected = _sha256(final_path)
        with tempfile.TemporaryDirectory(prefix="smoke-artifact-") as download_dir:
            downloaded = Path(
                self.mlflow.artifacts.download_artifacts(artifact_uri=artifact_uri, dst_path=download_dir)
            )
            actual = _sha256(downloaded)
        if actual != expected:
            raise RuntimeError(f"{artifact_uri} downloaded with sha256 {actual}, expected {expected}")
        return {"artifact_uri": artifact_uri, "sha256": expected}

    def check_storage(self) -> dict[str, Any]:
        storage = getattr(self.context, "storage", None)
        if storage is None:
            raise SkipCheck("AZURE_STORAGE_ACCOUNT_NAME isn't set, so training has no storage context")
        final_path = self.checkpoints[-1]
        blob_name = storage.upload_checkpoint(
            local_path=str(final_path),
            model_name=f"{self.config.model_name}-{uuid.uuid4().hex[:8]}",
            step=self.final_step,
        )
        blob = storage.blob_client.get_blob_client(container=storage.container_name, blob=blob_name)
        size = blob.get_blob_properties().size
        if size != final_path.stat().st_size:
            raise RuntimeError(f"uploaded blob is {size} bytes, expected {final_path.stat().st_size}")
        cleanup_error = None
        try:
            blob.delete_blob()
        except Exception as exc:  # Cleanup is best effort; the upload already succeeded.
            cleanup_error = f"{type(exc).__name__}: {exc}"
        return {
            "container": storage.container_name,
            "blob": blob_name,
            "bytes": size,
            "cleaned_up": cleanup_error is None,
            "cleanup_error": cleanup_error,
        }

    def check_model_registry(self) -> dict[str, Any]:
        if not self.config.register_model:
            raise SkipCheck("model registration disabled")
        from azure.ai.ml.entities import Model

        final_path = self.checkpoints[-1]
        model = Model(
            name=self.config.model_name,
            path=str(final_path),
            type="custom_model",
            description="Azure ML GPU smoke test checkpoint",
            tags={"smoke_test": "azureml-gpu", "run_id": self.run_id or "none", "step": str(self.final_step)},
        )
        registered = self.context.client.models.create_or_update(model)
        fetched = self.context.client.models.get(name=registered.name, version=registered.version)
        return {"name": fetched.name, "version": fetched.version}

    # Summary ----------------------------------------------------------------

    def build_summary(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": PASSED if self.runner.succeeded else FAILED,
            "started_at": self.started_at.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "azureml_run_id": os.environ.get("AZUREML_RUN_ID"),
            "mlflow_run_id": self.run_id,
            "checks": [result.to_dict() for result in self.runner.results],
        }

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.output_dir / SUMMARY_FILENAME
        path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
        if self.run_id:
            try:
                self.mlflow.log_dict(summary, f"{ARTIFACT_DIR}/{SUMMARY_FILENAME}")
            except Exception:
                _LOGGER.warning("Couldn't log %s to MLflow", SUMMARY_FILENAME, exc_info=True)

    def finish_mlflow_run(self) -> None:
        # Azure ML owns the lifecycle of the run it created for the job.
        if self.run_id and not self.managed_run:
            try:
                self.mlflow.end_run(status="FINISHED" if self.runner.succeeded else "FAILED")
            except Exception:
                _LOGGER.warning("Couldn't end the MLflow run", exc_info=True)

    # Helpers ----------------------------------------------------------------

    def _synchronize(self) -> None:
        if self.device is not None and self.device.type == "cuda":
            self.torch.cuda.synchronize()

    def _queue_metric(self, key: str, value: float, *, step: int) -> None:
        if self.run_id:
            self._metric_buffer.append(
                self.mlflow.entities.Metric(key=key, value=value, timestamp=int(time.time() * 1000), step=step)
            )

    def _flush_metrics(self) -> None:
        if not self._metric_buffer:
            return
        batch, self._metric_buffer = self._metric_buffer, []
        started = time.perf_counter()
        try:
            self.mlflow.tracking.MlflowClient().log_batch(self.run_id, metrics=batch)
            self.logged_loss_steps += sum(1 for metric in batch if metric.key == METRIC_LOSS)
        except Exception:
            self.metric_log_failures += len(batch)
            _LOGGER.warning("MLflow log_batch failed for %d metrics", len(batch), exc_info=True)
        finally:
            self.metric_log_seconds += time.perf_counter() - started

    def _save_checkpoint(self, model: Any, optimizer: Any, step: int, loss: float) -> Path:
        path = self.config.output_dir / f"{CHECKPOINT_PREFIX}{step:06d}.pt"
        state = {
            "step": step,
            "loss": loss,
            "model_state": {name: tensor.detach().cpu() for name, tensor in model.state_dict().items()},
            "optimizer_state": optimizer.state_dict(),
        }
        self.torch.save(state, path)
        return path

    def _expected_checkpoint_steps(self) -> list[int]:
        steps = list(range(self.config.checkpoint_interval, self.config.steps + 1, self.config.checkpoint_interval))
        if not steps or steps[-1] != self.config.steps:
            steps.append(self.config.steps)
        return steps


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def _bool_text(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--steps", type=_positive_int, default=200, help="Training steps (default: 200)")
    parser.add_argument(
        "--checkpoint-interval", type=_positive_int, default=50, help="Steps between checkpoints (default: 50)"
    )
    parser.add_argument("--batch-size", type=_positive_int, default=256, help="Training batch size (default: 256)")
    parser.add_argument("--matrix-size", type=_positive_int, default=4096, help="Matmul size (default: 4096)")
    parser.add_argument(
        "--matmul-iterations", type=_positive_int, default=20, help="Timed matrix multiplications (default: 20)"
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("AZURE_ML_OUTPUT_CHECKPOINTS") or "outputs/checkpoints",
        help="Checkpoint and summary directory (default: $AZURE_ML_OUTPUT_CHECKPOINTS or outputs/checkpoints)",
    )
    parser.add_argument(
        "--experiment-name",
        default=os.environ.get("MLFLOW_EXPERIMENT_NAME") or DEFAULT_EXPERIMENT,
        help="MLflow experiment (default: the Azure ML job's experiment)",
    )
    parser.add_argument(
        "--model-name", default=DEFAULT_MODEL_NAME, help=f"Registered model (default: {DEFAULT_MODEL_NAME})"
    )
    parser.add_argument(
        "--register-model", type=_bool_text, default=True, help="Register the final checkpoint (default: true)"
    )
    parser.add_argument("--skip-azure", action="store_true", help="Run only the GPU, training, and checkpoint checks")
    parser.add_argument("--allow-cpu", action="store_true", help="Run on CPU when CUDA isn't available")
    parser.add_argument("--seed", type=int, default=0, help="Random seed (default: 0)")
    return parser.parse_args(argv)


def _prepare_mlflow_registry_uri() -> None:
    # MLClient probes the MLflow registry when created, and Azure ML's MLflow
    # endpoint doesn't implement one, so point the registry at a local path first.
    registry_dir = Path(os.environ.get("MLFLOW_LOCAL_REGISTRY_DIR") or Path(tempfile.gettempdir()) / "mlflow_registry")
    registry_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MLFLOW_REGISTRY_URI", f"file://{registry_dir}")


def config_from_args(args: argparse.Namespace) -> SmokeConfig:
    return SmokeConfig(
        steps=args.steps,
        checkpoint_interval=args.checkpoint_interval,
        batch_size=args.batch_size,
        matrix_size=args.matrix_size,
        matmul_iterations=args.matmul_iterations,
        output_dir=Path(args.output_dir),
        experiment_name=args.experiment_name,
        model_name=args.model_name,
        register_model=args.register_model,
        azure=not args.skip_azure,
        allow_cpu=args.allow_cpu,
        seed=args.seed,
    )


def _print_report(summary: dict[str, Any]) -> None:
    print(f"\nAzure ML GPU smoke test: {summary['status'].upper()}")
    for check in summary["checks"]:
        line = f"  {check['status']:<8} {check['name']:<18} {check['duration_s']:>8.2f}s"
        if check["error"]:
            line += f"  {check['error']}"
        print(line)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    config = config_from_args(_parse_args(argv))
    if config.azure:
        _prepare_mlflow_registry_uri()
    summary = GpuSmokeTest(config).execute()
    _print_report(summary)
    return 0 if summary["status"] == PASSED else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
