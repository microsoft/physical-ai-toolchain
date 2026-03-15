"""Unified launcher for IsaacLab training and smoke-test workflows."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import shutil
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from training.utils import AzureConfigError, AzureMLContext, bootstrap_azure_ml

_LOGGER = logging.getLogger("isaaclab.launch")
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
    """Convert empty, 'none', or None to None for optional string arguments."""
    if value_str is None or value_str == "" or value_str.lower() == "none":
        return None
    return value_str


def _parse_args(argv: Sequence[str] | None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="IsaacLab unified launcher")
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


_CHECKPOINT_MODE_MAP = {
    "fresh": "from-scratch",
    "from-scratch": "from-scratch",
    "warm-start": "warm-start",
    "resume": "resume",
}


def _normalize_checkpoint_mode(value: str | None) -> str:
    if not value:
        return "from-scratch"
    normalized = value.lower()
    mode = _CHECKPOINT_MODE_MAP.get(normalized)
    if mode is None:
        raise SystemExit(f"Unsupported checkpoint mode: {value}")
    return mode


@contextmanager
def _materialized_checkpoint(artifact_uri: str | None) -> Iterator[str | None]:
    if not artifact_uri:
        yield None
        return

    try:
        import mlflow
    except ImportError as exc:
        raise SystemExit("mlflow is required to download checkpoint artifacts") from exc

    download_root = tempfile.mkdtemp(prefix="skrl-ckpt-")
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

    experiment_name = args.experiment_name or (f"isaaclab-{args.task}" if args.task else "isaaclab-training")
    context = bootstrap_azure_ml(experiment_name=experiment_name)
    _LOGGER.info("MLflow tracking configured: experiment=%s, uri=%s", experiment_name, context.tracking_uri)
    return context, experiment_name


def _run_training(
    args: argparse.Namespace,
    hydra_args: Sequence[str],
    context: AzureMLContext | None,
) -> None:
    try:
        from training.scripts import skrl_training
    except ImportError as exc:
        raise SystemExit(
            "training.scripts.skrl_training module is unavailable. Ensure training payload includes SKRL training code."
        ) from exc

    skrl_training.run_training(args=args, hydra_args=hydra_args, context=context)


def _run_smoke_test() -> None:
    _LOGGER.info("Running Azure connectivity smoke test")
    from training.scripts import smoke_test_azure

    smoke_test_azure.main([])


def _mlflow_required_for_checkpoint_uri(args: argparse.Namespace) -> bool:
    return args.disable_mlflow and args.checkpoint_uri


def _mlflow_required_for_registration(args: argparse.Namespace) -> bool:
    return args.disable_mlflow and args.register_checkpoint


def _validate_mlflow_flags(args: argparse.Namespace) -> None:
    if _mlflow_required_for_checkpoint_uri(args):
        raise SystemExit("--checkpoint-uri requires MLflow integration; remove --disable-mlflow")
    if _mlflow_required_for_registration(args):
        raise SystemExit("--register-checkpoint requires MLflow integration; remove --disable-mlflow")


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    args, hydra_args = _parse_args(argv if argv is not None else sys.argv[1:])

    args.checkpoint = None

    cli_state = {"parsed": dict(vars(args)), "hydra": list(hydra_args)}
    _LOGGER.info("Launcher arguments: %s", cli_state)

    _ensure_dependencies()

    if args.mode == "smoke-test":
        _run_smoke_test()
        return

    args.checkpoint_mode = _normalize_checkpoint_mode(args.checkpoint_mode)
    _validate_mlflow_flags(args)

    try:
        context, _experiment_name = _initialize_mlflow_context(args)
    except AzureConfigError as exc:
        raise SystemExit(str(exc)) from exc

    with _materialized_checkpoint(args.checkpoint_uri) as checkpoint_path:
        if checkpoint_path:
            args.checkpoint = checkpoint_path
            _LOGGER.info("Using checkpoint: mode=%s, path=%s", args.checkpoint_mode, checkpoint_path)
        elif args.checkpoint_mode != "from-scratch":
            _LOGGER.info("No checkpoint provided, mode=%s", args.checkpoint_mode)
        _run_training(args=args, hydra_args=hydra_args, context=context)


if __name__ == "__main__":
    main()
