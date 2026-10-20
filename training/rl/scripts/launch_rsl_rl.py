"""Unified launcher for Isaac Lab RSL-RL training and smoke-test workflows."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING

from training.utils.tracking import (
    TRACKING_BACKENDS,
    AzureMLTracking,
    MLflowTracking,
    TrackingBackend,
    TrackingConfigError,
    backend_requirements,
    resolve_tracking_backend_name,
)

if TYPE_CHECKING:
    from training.utils.context import AzureMLContext

_LOGGER = logging.getLogger("isaaclab.launch_rsl_rl")


class AzureConfigError(RuntimeError):
    """Lazy launcher boundary for Azure configuration failures."""


def bootstrap_azure_ml(*, experiment_name: str) -> AzureMLContext:
    """Load Azure ML dependencies only when tracking is enabled."""
    from training.utils.context import AzureConfigError as ContextAzureConfigError
    from training.utils.context import bootstrap_azure_ml as bootstrap

    try:
        return bootstrap(experiment_name=experiment_name)
    except ContextAzureConfigError as exc:
        raise AzureConfigError(str(exc)) from exc


def _bootstrap_deferred(*, experiment_name: str) -> AzureMLContext:
    """Resolve ``bootstrap_azure_ml`` at call time so tests and callers can replace it."""
    return bootstrap_azure_ml(experiment_name=experiment_name)


def _optional_int(value_str: str | None) -> int | None:
    if value_str in (None, ""):
        return None
    return int(value_str)


def _optional_str(value_str: str | None) -> str | None:
    return None if value_str in (None, "") else value_str


def _parse_args(argv: Sequence[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Isaac Lab RSL-RL unified launcher")
    parser.add_argument("--mode", choices=("train", "smoke-test"), default="train", help="Execution mode")
    parser.add_argument("--task", type=_optional_str, default=None, help="Isaac Lab task identifier")
    parser.add_argument("--num_envs", type=_optional_int, default=None, help="Number of simulated environments")
    parser.add_argument("--max_iterations", type=_optional_int, default=None, help="Maximum policy iterations")
    parser.add_argument("--headless", action="store_true", help="Run without viewer")
    parser.add_argument(
        "--experiment-name",
        type=_optional_str,
        default=None,
        help="Override the tracking experiment name. Defaults to the Isaac Lab task.",
    )
    parser.add_argument(
        "--disable-mlflow",
        action="store_true",
        help="Skip MLflow configuration for dry runs",
    )
    parser.add_argument(
        "--tracking-backend",
        choices=TRACKING_BACKENDS,
        default=None,
        help="Where runs and checkpoints go: azureml, mlflow (MLFLOW_TRACKING_URI), or none. "
        "Defaults to TRACKING_BACKEND, then azureml.",
    )
    parser.add_argument(
        "--checkpoint-uri",
        type=_optional_str,
        default=None,
        help="MLflow artifact URI for the checkpoint to materialize before training",
    )
    parser.add_argument(
        "--checkpoint-mode",
        type=_optional_str,
        default="from-scratch",
        help="Checkpoint handling mode (fresh is treated as from-scratch)",
    )
    parser.add_argument(
        "--register-checkpoint",
        type=_optional_str,
        default=None,
        help="Register the final checkpoint under this model name in the tracking backend's registry",
    )
    args, remaining = parser.parse_known_args(argv)
    return args, list(remaining)


def _tracking_backend_name(args: argparse.Namespace) -> str:
    """Return the effective backend: --disable-mlflow turns tracking off, otherwise the selection."""
    if getattr(args, "disable_mlflow", False):
        return "none"
    return resolve_tracking_backend_name(getattr(args, "tracking_backend", None))


def _ensure_dependencies(backend: str = "azureml") -> None:
    missing: list[str] = []
    for module_name, package_name in backend_requirements(backend).items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    if missing:
        packages = ", ".join(sorted(set(missing)))
        message = (
            f"Missing required Python packages for the {backend} tracking backend: "
            f"{packages}. Install the listed packages in the training image."
        )
        raise SystemExit(message)


@contextmanager
def _materialized_checkpoint(artifact_uri: str | None, tracking: TrackingBackend | None = None) -> Iterator[str | None]:
    if not artifact_uri:
        yield None
        return

    try:
        import mlflow
    except ImportError as exc:
        raise SystemExit("mlflow is required to download checkpoint artifacts") from exc

    # runs:/ and models:/ URIs resolve against the tracking server. The Azure ML bootstrap configures
    # MLflow when it connects; an MLflow server is selected here.
    if isinstance(tracking, MLflowTracking):
        tracking.configure_mlflow(mlflow)

    download_root = tempfile.mkdtemp(prefix="rsl-rl-ckpt-")
    try:
        local_path = mlflow.artifacts.download_artifacts(artifact_uri=artifact_uri, dst_path=download_root)
    except Exception as exc:
        shutil.rmtree(download_root, ignore_errors=True)
        raise SystemExit(f"Failed to download checkpoint from {artifact_uri}: {exc}") from exc

    try:
        _LOGGER.info("Downloaded checkpoint from %s to %s", artifact_uri, local_path)
        yield local_path
    finally:
        shutil.rmtree(download_root, ignore_errors=True)


def _initialize_mlflow_context(args: argparse.Namespace) -> tuple[TrackingBackend | None, str | None]:
    backend = _tracking_backend_name(args)
    if backend == "none":
        _LOGGER.info("MLflow integration disabled")
        return None, None

    experiment_name = args.experiment_name or (f"isaaclab-rsl-rl-{args.task}" if args.task else "isaaclab-rsl-rl")
    if backend == "mlflow":
        context: TrackingBackend = MLflowTracking(experiment_name=experiment_name)
    else:
        context = AzureMLTracking(experiment_name=experiment_name, bootstrap=_bootstrap_deferred)
    # Reading the URI connects Azure ML now, so configuration errors stop the launch before Isaac Sim starts.
    _LOGGER.info(
        "MLflow tracking configured: backend=%s, experiment=%s, uri=%s", backend, experiment_name, context.tracking_uri
    )
    return context, experiment_name


def _run_training(
    args: argparse.Namespace,
    hydra_args: Sequence[str],
) -> None:
    cmd = [sys.executable, "-m", "training.rl.scripts.rsl_rl.train"]
    cmd.extend(["--tracking_backend", _tracking_backend_name(args)])

    if args.task:
        cmd.extend(["--task", args.task])
    if args.num_envs is not None:
        cmd.extend(["--num_envs", str(args.num_envs)])
    if args.max_iterations is not None:
        cmd.extend(["--max_iterations", str(args.max_iterations)])
    if args.headless:
        cmd.append("--headless")
    if args.checkpoint:
        cmd.extend(["--checkpoint", args.checkpoint])
    if register_checkpoint := getattr(args, "register_checkpoint", None):
        cmd.extend(["--register_checkpoint", register_checkpoint])

    cmd.extend(hydra_args)

    _LOGGER.info("Executing training script: %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise SystemExit(f"Training script failed with exit code {result.returncode}")


def _run_smoke_test() -> None:
    _LOGGER.info("Running Azure connectivity smoke test")
    from training.rl.scripts import smoke_test_azure

    smoke_test_azure.main([])


def _validate_mlflow_flags(args: argparse.Namespace) -> None:
    if _tracking_backend_name(args) != "none":
        return
    if args.checkpoint_uri:
        raise SystemExit(
            "--checkpoint-uri requires MLflow integration; remove --disable-mlflow or select a tracking backend"
        )
    if getattr(args, "register_checkpoint", None):
        raise SystemExit(
            "--register-checkpoint requires MLflow integration; remove --disable-mlflow or select a tracking backend"
        )


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    args, hydra_args = _parse_args(argv if argv is not None else sys.argv[1:])

    args.checkpoint = None

    cli_state = {"parsed": dict(vars(args)), "hydra": list(hydra_args)}
    _LOGGER.info("RSL-RL Launcher arguments: %s", cli_state)

    if args.mode == "smoke-test":
        # The smoke test checks Azure connectivity, so it always needs the Azure ML packages.
        _ensure_dependencies("azureml")
        _run_smoke_test()
        return

    try:
        _ensure_dependencies(_tracking_backend_name(args))
    except TrackingConfigError as exc:
        raise SystemExit(str(exc)) from exc

    _validate_mlflow_flags(args)

    try:
        context, _experiment_name = _initialize_mlflow_context(args)
    except (AzureConfigError, TrackingConfigError) as exc:
        raise SystemExit(str(exc)) from exc

    with _materialized_checkpoint(args.checkpoint_uri, context) as checkpoint_path:
        if checkpoint_path:
            args.checkpoint = checkpoint_path
            _LOGGER.info("Using checkpoint from URI: %s", checkpoint_path)
        _run_training(args=args, hydra_args=hydra_args)


if __name__ == "__main__":
    main()
