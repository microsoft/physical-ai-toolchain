"""Unified launcher for IsaacLab RSL-RL training and smoke-test workflows."""

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

from training.utils import AzureConfigError, AzureMLContext, bootstrap_azure_ml

_LOGGER = logging.getLogger("isaaclab.launch_rsl_rl")
_REQUIRED_MODULES = {
    "azure.identity": "azure-identity>=1.13.0",
    "azure.ai.ml": "azure-ai-ml",
    "azureml.mlflow": "azureml-mlflow",
    "mlflow": "mlflow",
}


def _optional_int(value_str: str | None) -> int | None:
    if value_str in (None, ""):
        return None
    return int(value_str)


def _optional_str(value_str: str | None) -> str | None:
    return None if value_str in (None, "") else value_str


def _parse_args(argv: Sequence[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="IsaacLab RSL-RL unified launcher")
    parser.add_argument("--mode", choices=("train", "smoke-test"), default="train", help="Execution mode")
    parser.add_argument("--task", type=_optional_str, default=None, help="IsaacLab task identifier")
    parser.add_argument("--num_envs", type=_optional_int, default=None, help="Number of simulated environments")
    parser.add_argument("--max_iterations", type=_optional_int, default=None, help="Maximum policy iterations")
    parser.add_argument("--headless", action="store_true", help="Run without viewer")
    parser.add_argument(
        "--experiment-name",
        type=_optional_str,
        default=None,
        help="Override Azure ML experiment name. Defaults to the IsaacLab task.",
    )
    parser.add_argument(
        "--disable-mlflow",
        action="store_true",
        help="Skip MLflow configuration for dry runs",
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
        help="Register the final checkpoint as this Azure ML model name",
    )
    args, remaining = parser.parse_known_args(argv)
    return args, list(remaining)


def _ensure_dependencies() -> None:
    missing: list[str] = []
    for module_name, package_name in _REQUIRED_MODULES.items():
        if importlib.util.find_spec(module_name) is None:
            missing.append(package_name)
    if missing:
        packages = ", ".join(sorted(set(missing)))
        message = (
            "Missing required Python packages for Azure ML integration: "
            f"{packages}. Install the listed packages in the training image."
        )
        raise SystemExit(message)


@contextmanager
def _materialized_checkpoint(artifact_uri: str | None) -> Iterator[str | None]:
    if not artifact_uri:
        yield None
        return

    try:
        import mlflow
    except ImportError as exc:
        raise SystemExit("mlflow is required to download checkpoint artifacts") from exc

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


def _initialize_mlflow_context(args: argparse.Namespace) -> tuple[AzureMLContext | None, str | None]:
    if args.disable_mlflow:
        _LOGGER.info("MLflow integration disabled")
        return None, None

    experiment_name = args.experiment_name or (f"isaaclab-rsl-rl-{args.task}" if args.task else "isaaclab-rsl-rl")
    context = bootstrap_azure_ml(experiment_name=experiment_name)
    _LOGGER.info("MLflow tracking configured: experiment=%s, uri=%s", experiment_name, context.tracking_uri)
    return context, experiment_name


def _run_training(
    args: argparse.Namespace,
    hydra_args: Sequence[str],
) -> None:
    cmd = [sys.executable, "-m", "training.rl.scripts.rsl_rl.train"]

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
    if args.disable_mlflow and args.checkpoint_uri:
        raise SystemExit("--checkpoint-uri requires MLflow integration; remove --disable-mlflow")


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    args, hydra_args = _parse_args(argv if argv is not None else sys.argv[1:])

    args.checkpoint = None

    cli_state = {"parsed": dict(vars(args)), "hydra": list(hydra_args)}
    _LOGGER.info("RSL-RL Launcher arguments: %s", cli_state)

    _ensure_dependencies()

    if args.mode == "smoke-test":
        _run_smoke_test()
        return

    _validate_mlflow_flags(args)

    try:
        _context, _experiment_name = _initialize_mlflow_context(args)
    except AzureConfigError as exc:
        raise SystemExit(str(exc)) from exc

    with _materialized_checkpoint(args.checkpoint_uri) as checkpoint_path:
        if checkpoint_path:
            args.checkpoint = checkpoint_path
            _LOGGER.info("Using checkpoint from URI: %s", checkpoint_path)
        _run_training(args=args, hydra_args=hydra_args)


if __name__ == "__main__":
    main()
