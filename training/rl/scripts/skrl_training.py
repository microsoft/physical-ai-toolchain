"""SKRL training orchestration with IsaacLab environments and Azure MLflow integration.

This module provides the main training loop for reinforcement learning agents using
the SKRL library with IsaacLab simulation environments. It handles:
- Environment and agent configuration via Hydra
- Checkpoint loading and model registration
- MLflow metric logging and artifact tracking
- Video recording of training rollouts
- Integration with Azure ML workspaces
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import shutil
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from training.scripts.skrl_mlflow_agent import create_mlflow_logging_wrapper
from training.simulation_shutdown import prepare_for_shutdown
from training.stream import install_ansi_stripping
from training.utils import AzureMLContext, set_env_defaults

_LOGGER = logging.getLogger("isaaclab.skrl")

_DEFAULT_MLFLOW_INTERVAL = 10
_MLFLOW_INTERVAL_PRESETS = {
    "step": 1,
    "balanced": _DEFAULT_MLFLOW_INTERVAL,
}
_MLFLOW_ROLLOUT_PRESET = "rollout"

_AGENT_ENTRY_DEFAULT = "skrl_cfg_entry_point"
_AGENT_ENTRY_MAP = {
    "ippo": "skrl_ippo_cfg_entry_point",
    "mappo": "skrl_mappo_cfg_entry_point",
    "amp": "skrl_amp_cfg_entry_point",
}


def _parse_mlflow_log_interval(interval_arg: str, rollouts: int) -> int:
    """Parse mlflow_log_interval argument into integer interval.

    Args:
        interval_arg: CLI argument value (preset name or integer string)
        rollouts: Number of rollouts per iteration from agent config

    Returns:
        Integer interval for metric logging
    """
    normalized_arg = interval_arg.strip().lower()
    if not normalized_arg:
        return _DEFAULT_MLFLOW_INTERVAL

    preset_value = _MLFLOW_INTERVAL_PRESETS.get(normalized_arg)
    if preset_value is not None:
        return preset_value

    if normalized_arg == _MLFLOW_ROLLOUT_PRESET:
        return rollouts if rollouts > 0 else _DEFAULT_MLFLOW_INTERVAL
    try:
        interval = int(normalized_arg)
        return max(1, interval)
    except ValueError:
        _LOGGER.warning(
            "Invalid mlflow_log_interval '%s', using default (%d)",
            normalized_arg,
            _DEFAULT_MLFLOW_INTERVAL,
        )
        return _DEFAULT_MLFLOW_INTERVAL


def _build_parser(app_launcher_cls: Any) -> argparse.ArgumentParser:
    """Build argument parser for SKRL training with IsaacLab launcher args."""
    parser = argparse.ArgumentParser(description="Train IsaacLab SKRL policies")
    parser.add_argument("--task", type=str, default=None, help="IsaacLab task identifier")
    parser.add_argument("--agent", type=str, default=None, help="Override agent configuration entry point")
    parser.add_argument(
        "--algorithm",
        type=str,
        default="PPO",
        choices=["AMP", "PPO", "IPPO", "MAPPO"],
        help="RL algorithm",
    )
    parser.add_argument(
        "--ml_framework",
        type=str,
        default="torch",
        choices=["torch", "jax", "jax-numpy"],
        help="Numerical backend",
    )
    parser.add_argument("--num_envs", type=int, default=None, help="Override number of vectorized environments")
    parser.add_argument("--max_iterations", type=int, default=None, help="Maximum training iterations")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--distributed", action="store_true", help="Enable distributed execution")
    parser.add_argument("--checkpoint", type=str, default=None, help="Resume checkpoint path")
    parser.add_argument("--export_io_descriptors", action="store_true", help="Dump IO descriptors")
    parser.add_argument("--video", action="store_true", help="Record rollout videos")
    parser.add_argument("--video_length", type=int, default=200, help="Video duration in steps")
    parser.add_argument("--video_interval", type=int, default=2000, help="Video capture interval")
    parser.add_argument(
        "--mlflow_log_interval",
        type=str,
        default="balanced",
        help=(
            "MLflow metric logging interval: 'step' (every step),"
            " 'balanced' (every 10 steps), 'rollout' (per rollout), or integer"
        ),
    )
    app_launcher_cls.add_app_launcher_args(parser)
    return parser


def _sync_checkpoint_output(checkpoint_dir: Path) -> None:
    """Copy checkpoints into AzureML outputs when TRAINING_CHECKPOINT_OUTPUT is set."""

    target = os.environ.get("TRAINING_CHECKPOINT_OUTPUT")
    if not target or not checkpoint_dir.exists():
        return

    destination = Path(target)
    try:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(checkpoint_dir, destination, dirs_exist_ok=True)
        _LOGGER.info("Copied checkpoints to %s", destination)
    except Exception as exc:
        _LOGGER.warning("Failed to copy checkpoints to %s: %s", destination, exc)


def _get_agent_config_entry_point(cli_args: argparse.Namespace) -> str:
    """Resolve agent configuration entry point for selected algorithm."""
    if cli_args.agent:
        return cli_args.agent
    algorithm = (cli_args.algorithm or "").lower()
    return _AGENT_ENTRY_MAP.get(algorithm, _AGENT_ENTRY_DEFAULT)


def _prepare_log_paths(agent_cfg: dict[str, Any], cli_args: argparse.Namespace) -> Path:
    """Configure experiment metadata and create log directory for the run.

    Args:
        agent_cfg: Agent configuration dictionary to populate with experiment details.
        cli_args: Parsed CLI arguments that drive naming and algorithm metadata.

    Returns:
        Absolute path to the run-specific log directory.
    """
    experiment_cfg = agent_cfg.setdefault("agent", {}).setdefault("experiment", {})
    root_path = Path(experiment_cfg.get("directory") or Path("logs") / "skrl").resolve()
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    algorithm_label = (cli_args.algorithm or "rl").lower()
    run_name = f"{timestamp}_{algorithm_label}_{cli_args.ml_framework}"
    custom_name = experiment_cfg.get("experiment_name")
    if custom_name:
        run_name = f"{run_name}_{custom_name}"
    experiment_cfg["directory"] = str(root_path)
    experiment_cfg["experiment_name"] = run_name
    log_dir = root_path / run_name
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _wrap_with_video_recorder(gym_module: Any, env: Any, cli_args: argparse.Namespace, log_dir: Path) -> Any:
    """Wrap environment with video capture when video recording is enabled.

    Args:
        gym_module: Gymnasium module providing wrappers.
        env: Environment instance to optionally wrap.
        cli_args: Parsed CLI arguments containing video options.
        log_dir: Base directory for run artifacts.

    Returns:
        Environment wrapped with video recorder when requested; otherwise original env.
    """
    if not cli_args.video:
        return env
    video_dir = log_dir / "videos" / "train"
    video_dir.mkdir(parents=True, exist_ok=True)
    video_kwargs = {
        "video_folder": str(video_dir),
        "step_trigger": lambda step: step % cli_args.video_interval == 0,
        "video_length": cli_args.video_length,
        "disable_logger": True,
    }
    _LOGGER.info("Recording training videos to %s", video_dir)
    return gym_module.wrappers.RecordVideo(env, **video_kwargs)


def _log_artifacts(mlflow: Any, log_dir: Path, resume_path: str | None) -> str | None:
    """Log training artifacts to MLflow and derive latest checkpoint URI.

    Args:
        mlflow: MLflow module used for logging operations.
        log_dir: Log directory containing artifacts to upload.
        resume_path: Path to the resumed checkpoint, if any.

    Returns:
        URI to the most recent checkpoint artifact, or None when unavailable.
    """
    params_dir = log_dir / "params"
    for rel_path in ("env.yaml", "agent.yaml", "env.pkl", "agent.pkl"):
        candidate = params_dir / rel_path
        if candidate.exists():
            mlflow.log_artifact(str(candidate), artifact_path="skrl-run")
    if resume_path:
        mlflow.log_artifact(resume_path, artifact_path="skrl-run/checkpoints")
    checkpoint_dir = log_dir / "checkpoints"
    active_run = mlflow.active_run()
    latest_uri: str | None = None
    if checkpoint_dir.exists() and checkpoint_dir.is_dir():
        mlflow.log_artifacts(str(checkpoint_dir), artifact_path="skrl-run/checkpoints")
        latest_file: Path | None = None
        for candidate in checkpoint_dir.rglob("*"):
            if candidate.is_file() and (latest_file is None or candidate.stat().st_mtime > latest_file.stat().st_mtime):
                latest_file = candidate
        if active_run and latest_file:
            run_id = active_run.info.run_id
            relative_path = latest_file.relative_to(checkpoint_dir)
            directory_uri = f"runs:/{run_id}/skrl-run/checkpoints"
            latest_uri = f"{directory_uri}/{relative_path.as_posix()}"
            mlflow.set_tag("checkpoint_directory", directory_uri)
            mlflow.set_tag("checkpoint_latest", latest_uri)
            token = f"::checkpoint_uri::{latest_uri}"
            mlflow.set_tag("checkpoint_log_token", token)
            _LOGGER.info("Latest checkpoint: %s", latest_uri)
            print(token)
        _sync_checkpoint_output(checkpoint_dir)
    videos_dir = log_dir / "videos"
    if videos_dir.exists():
        mlflow.log_artifacts(str(videos_dir), artifact_path="videos")
    return latest_uri


def _register_checkpoint_model(
    *,
    context: AzureMLContext | None,
    model_name: str,
    checkpoint_uri: str,
    checkpoint_mode: str | None,
    task: str | None,
    algorithm: str | None = None,
) -> None:
    """Register a checkpoint artifact as an Azure ML model when context is available.

    Args:
        context: Azure ML context responsible for model registration.
        model_name: Target Azure ML model name.
        checkpoint_uri: MLflow URI for the checkpoint artifact.
        checkpoint_mode: Checkpoint mode metadata tag.
        task: IsaacLab task identifier for tagging.
        algorithm: RL algorithm (e.g., PPO, IPPO) for tagging.
    """
    if context is None:
        _LOGGER.info("Skipping checkpoint registration (no Azure ML context)")
        return
    try:
        from azure.ai.ml.entities import Model
    except ImportError as exc:
        _LOGGER.error("Azure ML SDK missing; cannot register checkpoint: %s", exc)
        return

    tags = {
        "checkpoint_mode": checkpoint_mode or "from-scratch",
        "framework": "skrl",
        "validated": "false",
    }
    if task:
        tags["task"] = task
    if algorithm:
        tags["algorithm"] = algorithm

    properties = {
        "success_threshold": "0.7",
    }

    try:
        model = Model(
            name=model_name,
            path=checkpoint_uri,
            type="custom_model",
            description="IsaacLab SKRL checkpoint artifact",
            tags=tags,
            properties=properties,
        )
        context.client.models.create_or_update(model)
        _LOGGER.info("Registered checkpoint as Azure ML model: %s", model_name)
    except Exception as exc:
        _LOGGER.error("Failed to register checkpoint model %s: %s", model_name, exc)


def _resolve_env_count(env_cfg: Any) -> int | None:
    """Extract environment count from configuration object regardless of env type."""
    scene = getattr(env_cfg, "scene", None)
    if scene and hasattr(scene, "env") and hasattr(scene.env, "num_envs"):
        return scene.env.num_envs
    return getattr(env_cfg, "num_envs", None)


def _resolve_checkpoint(retrieve_file_path: Any, checkpoint: str | None) -> str | None:
    """Resolve checkpoint location via IsaacLab asset resolver.

    Args:
        retrieve_file_path: Callable resolving checkpoint identifiers to absolute paths.
        checkpoint: User-specified checkpoint identifier or path.

    Returns:
        Resolved checkpoint path, or None when checkpoint not provided.

    Raises:
        SystemExit: If the checkpoint cannot be located.
    """
    if not checkpoint:
        return None
    try:
        return retrieve_file_path(checkpoint)
    except FileNotFoundError as exc:
        raise SystemExit(f"Checkpoint path not found: {checkpoint}") from exc


def _namespace_snapshot(namespace: argparse.Namespace) -> tuple[dict[str, object], Sequence[str]]:
    """Provide a serializable snapshot and CLI token list for a namespace."""

    payload: dict[str, object] = {}
    for key, value in vars(namespace).items():
        if isinstance(value, str | int | float | bool) or value is None:
            payload[key] = value
        else:
            payload[key] = str(value)

    tokens: list[str] = []
    task = payload.get("task")
    if task:
        tokens.extend(["--task", str(task)])
    num_envs = payload.get("num_envs")
    if num_envs is not None:
        tokens.extend(["--num_envs", str(num_envs)])
    max_iterations = payload.get("max_iterations")
    if max_iterations is not None:
        tokens.extend(["--max_iterations", str(max_iterations)])
    if payload.get("headless"):
        tokens.append("--headless")
    checkpoint = payload.get("checkpoint")
    if checkpoint:
        tokens.extend(["--checkpoint", str(checkpoint)])

    return payload, tokens


def _normalize_agent_config(agent_cfg: Any) -> dict[str, Any]:
    """Return agent configuration as a plain dictionary."""

    to_dict = getattr(agent_cfg, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return agent_cfg


def _set_num_envs_for_manager_cfg(env_cfg: Any, num_envs: int | None) -> None:
    env_cfg.scene.num_envs = num_envs or env_cfg.scene.num_envs


def _set_num_envs_for_direct_cfg(env_cfg: Any, num_envs: int | None) -> None:
    env_cfg.num_envs = num_envs or env_cfg.num_envs


def _configure_environment(
    env_cfg: Any,
    cli_args: argparse.Namespace,
    app_launcher,
    *,
    manager_cfg_type: Any,
    direct_cfg_type: Any,
    direct_mar_cfg_type: Any,
) -> int:
    """Update environment configuration with CLI overrides and return seed."""

    random_seed = cli_args.seed if cli_args.seed is not None else random.randint(1, 1_000_000)
    random.seed(random_seed)
    set_env_defaults(
        {
            "PYTHONHASHSEED": str(random_seed),
            "HYDRA_FULL_ERROR": "1",
        }
    )

    if isinstance(env_cfg, manager_cfg_type):
        _set_num_envs_for_manager_cfg(env_cfg, cli_args.num_envs)
    elif isinstance(env_cfg, direct_cfg_type | direct_mar_cfg_type):
        _set_num_envs_for_direct_cfg(env_cfg, cli_args.num_envs)

    if cli_args.distributed:
        env_cfg.sim.device = f"cuda:{app_launcher.local_rank}"

    env_cfg.seed = random_seed
    return random_seed


def _configure_agent_training(
    agent_dict: dict[str, Any],
    cli_args: argparse.Namespace,
    random_seed: int,
) -> int:
    """Align agent training configuration with CLI overrides."""

    trainer_cfg = agent_dict.setdefault("trainer", {})
    agent_section = agent_dict.setdefault("agent", {})
    rollouts = agent_section.get("rollouts", 1)

    if cli_args.max_iterations:
        trainer_cfg["timesteps"] = cli_args.max_iterations * rollouts

    trainer_cfg["close_environment_at_exit"] = False
    trainer_cfg["disable_progressbar"] = False
    agent_dict["seed"] = random_seed
    return rollouts


def _configure_jax_backend(ml_framework: str, skrl_module: Any) -> None:
    """Select JAX backend when running with a JAX-based framework."""

    if not ml_framework.startswith("jax"):
        return
    skrl_module.config.jax.backend = "jax" if ml_framework == "jax" else "numpy"


def _dump_config_files(
    log_dir: Path,
    env_cfg: Any,
    agent_dict: dict[str, Any],
    dump_yaml_func: Any,
    dump_pickle_func: Any | None,
) -> None:
    """Persist environment and agent configuration snapshots."""

    params_dir = log_dir / "params"
    params_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml_func(str(params_dir / "env.yaml"), env_cfg)
    dump_yaml_func(str(params_dir / "agent.yaml"), agent_dict)
    if dump_pickle_func:
        dump_pickle_func(str(params_dir / "env.pkl"), env_cfg)
        dump_pickle_func(str(params_dir / "agent.pkl"), agent_dict)


def _log_configuration_snapshot(
    cli_args: argparse.Namespace,
    env_cfg: Any,
    agent_dict: dict[str, Any],
    random_seed: int,
    rollouts: int,
) -> None:
    """Emit consolidated configuration details for the current run."""

    trainer_cfg = agent_dict.get("trainer", {})
    snapshot = {
        "algorithm": cli_args.algorithm,
        "ml_framework": cli_args.ml_framework,
        "num_envs": _resolve_env_count(env_cfg),
        "max_iterations": cli_args.max_iterations,
        "trainer_timesteps": trainer_cfg.get("timesteps"),
        "rollouts": rollouts,
        "distributed": cli_args.distributed,
        "seed": random_seed,
        "device": env_cfg.sim.device,
    }
    _LOGGER.info("SKRL training configuration: %s", snapshot)


def _validate_gym_registry(task: str | None, gym_module: Any) -> None:
    """Ensure the requested task is available in the Gymnasium registry."""

    if not task:
        raise ValueError("Task identifier is required for SKRL training")
    if task not in gym_module.envs.registry:
        isaac_envs = [name for name in gym_module.envs.registry if name.startswith("Isaac-")]
        raise ValueError(f"Task {task} not found in gym registry. Available Isaac tasks: {isaac_envs}")


def _create_gym_environment(task: str, env_cfg: Any, is_video_enabled: bool, gym_module: Any) -> Any:
    """Instantiate the IsaacLab task environment."""

    render_mode = "rgb_array" if is_video_enabled else None
    return gym_module.make(task, cfg=env_cfg, render_mode=render_mode)


def _wrap_environment(
    env: Any,
    *,
    cli_args: argparse.Namespace,
    log_dir: Path,
    gym_module: Any,
    multi_agent_to_single_agent: Any,
    direct_mar_env_type: Any,
    vec_wrapper_cls: Any,
) -> Any:
    """Apply optional transformations and SKRL vector environment wrapper."""

    if isinstance(env.unwrapped, direct_mar_env_type) and cli_args.algorithm.lower() == "ppo":
        env = multi_agent_to_single_agent(env)
    env = _wrap_with_video_recorder(gym_module, env, cli_args, log_dir)
    return vec_wrapper_cls(env, ml_framework=cli_args.ml_framework)


def _setup_agent_checkpoint(runner: Any, resume_path: str | None) -> None:
    """Load checkpoint into the runner agent when a resume path is provided."""

    if not resume_path:
        return
    runner.agent.load(resume_path)


def _apply_mlflow_logging(runner: Any, mlflow: Any | None) -> None:
    """Attach MLflow metric logging to the agent update loop."""

    if mlflow is None:
        return
    wrapper_func = create_mlflow_logging_wrapper(
        agent=runner.agent,
        mlflow_module=mlflow,
        metric_filter=None,
    )
    runner.agent._update = wrapper_func


@dataclass
class MLflowRunState:
    """Tracks MLflow run state for proper lifecycle management."""

    mlflow: Any
    log_interval: int
    owns_run: bool = False
    context: AzureMLContext | None = None
    args: argparse.Namespace | None = None
    cli_args: argparse.Namespace | None = None
    log_dir: Path | None = None
    resume_path: str | None = None
    outcome: str = field(default="success", init=False)


def _is_azureml_managed_run() -> bool:
    """Check if Azure ML has set up a managed MLflow run via environment variable."""
    return bool(os.environ.get("MLFLOW_RUN_ID"))


@contextmanager
def mlflow_run_context(
    mlflow: Any,
    *,
    context: AzureMLContext | None,
    args: argparse.Namespace,
    cli_args: argparse.Namespace,
    env_cfg: Any,
    log_dir: Path,
    resume_path: str | None,
    random_seed: int,
    rollouts: int,
) -> Iterator[MLflowRunState]:
    """Context manager for MLflow run lifecycle.

    Handles the case where Azure ML has already started a run (via MLFLOW_RUN_ID),
    or starts a new run if needed. Ensures proper cleanup on exit.

    When MLFLOW_RUN_ID is set by Azure ML, calling start_run() without arguments
    will resume that run. We track this so we don't call end_run() - Azure ML
    manages the run lifecycle.

    Yields:
        MLflowRunState with run configuration and log interval.
    """
    # Check if Azure ML is managing this run BEFORE any MLflow calls
    is_azureml_managed = _is_azureml_managed_run()
    env_run_id = os.environ.get("MLFLOW_RUN_ID")
    env_experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME")
    env_experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID")

    # Log all relevant MLflow environment variables for debugging
    mlflow_env_vars = {k: v for k, v in os.environ.items() if k.startswith(("MLFLOW_", "AZURE_"))}
    _LOGGER.debug("MLflow-related environment variables: %s", mlflow_env_vars)

    # When Azure ML manages the run, we must set the experiment BEFORE any other MLflow calls
    # Azure ML sets MLFLOW_EXPERIMENT_NAME and MLFLOW_RUN_ID - both must be respected
    if is_azureml_managed:
        if env_experiment_name:
            _LOGGER.info("Setting Azure ML experiment: %s", env_experiment_name)
            mlflow.set_experiment(experiment_name=env_experiment_name)
        elif env_experiment_id:
            _LOGGER.info("Setting Azure ML experiment by ID: %s", env_experiment_id)
            mlflow.set_experiment(experiment_id=env_experiment_id)

    # Enable autolog AFTER setting experiment to avoid conflicts
    _LOGGER.debug("Enabling MLflow autolog (log_models=False)")
    mlflow.autolog(log_models=False)

    # Start or resume the MLflow run
    try:
        if is_azureml_managed:
            _LOGGER.info("Azure ML managed run detected (MLFLOW_RUN_ID=%s)", env_run_id)
            # For Azure ML managed runs, start_run() with the run_id from environment
            # This resumes the run that Azure ML already created
            _LOGGER.debug("Calling mlflow.start_run(run_id=%s) to resume Azure ML managed run", env_run_id)
            mlflow.start_run(run_id=env_run_id)
            active = mlflow.active_run()
            _LOGGER.info("Resumed Azure ML managed run: %s", active.info.run_id if active else "unknown")
            owns_run = False
        else:
            _LOGGER.debug("No Azure ML managed run, starting new run with name: %s", log_dir.name)
            mlflow.start_run(run_name=log_dir.name)
            _LOGGER.info("Started new MLflow run: %s", log_dir.name)
            owns_run = True
    except Exception as exc:
        _LOGGER.error(
            "Failed to start/resume MLflow run: %s (type=%s)",
            exc,
            type(exc).__name__,
        )
        _LOGGER.error(
            "MLflow environment state: MLFLOW_RUN_ID=%s, MLFLOW_EXPERIMENT_NAME=%s",
            os.environ.get("MLFLOW_RUN_ID"),
            os.environ.get("MLFLOW_EXPERIMENT_NAME"),
        )
        _LOGGER.error("Active run before start_run: %s", mlflow.active_run())
        raise

    # Log parameters and tags
    log_interval = _parse_mlflow_log_interval(cli_args.mlflow_log_interval, rollouts)
    mlflow.log_params(
        {
            "algorithm": cli_args.algorithm,
            "ml_framework": cli_args.ml_framework,
            "num_envs": _resolve_env_count(env_cfg),
            "distributed": cli_args.distributed,
            "resume_checkpoint": bool(resume_path),
            "seed": random_seed,
            "mlflow_log_interval": log_interval,
        }
    )

    tags = {
        "log_dir": str(log_dir),
        "task": cli_args.task or "",
        "entrypoint": "training/scripts/train.py",
        "checkpoint_mode": args.checkpoint_mode,
    }
    if resume_path:
        tags["checkpoint_resume"] = resume_path
    if context:
        tags["azureml_workspace"] = context.workspace_name
    if args.checkpoint_uri:
        tags["checkpoint_source_uri"] = args.checkpoint_uri
    mlflow.set_tags(tags)

    state = MLflowRunState(
        mlflow=mlflow,
        log_interval=log_interval,
        owns_run=owns_run,
        context=context,
        args=args,
        cli_args=cli_args,
        log_dir=log_dir,
        resume_path=resume_path,
    )

    try:
        yield state
    finally:
        _finalize_mlflow_run(state)


def _finalize_mlflow_run(state: MLflowRunState) -> None:
    """Log artifacts, register models, and optionally close the MLflow run."""
    mlflow = state.mlflow
    mlflow.set_tag("training_outcome", state.outcome)

    latest_checkpoint_uri = _log_artifacts(mlflow, state.log_dir, state.resume_path)

    if state.args and state.args.register_checkpoint and latest_checkpoint_uri:
        _register_checkpoint_model(
            context=state.context,
            model_name=state.args.register_checkpoint,
            checkpoint_uri=latest_checkpoint_uri,
            checkpoint_mode=state.args.checkpoint_mode,
            task=state.cli_args.task if state.cli_args else None,
            algorithm=state.cli_args.algorithm if state.cli_args else None,
        )

    if state.owns_run:
        mlflow.end_run()
        _LOGGER.debug("Ended MLflow run")


def _execute_training_loop(runner: Any, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Run the SKRL training loop and record elapsed time."""
    start = time.perf_counter()
    try:
        runner.run()
    except Exception:
        descriptor["elapsed_seconds"] = round(time.perf_counter() - start, 2)
        _LOGGER.exception("Training failed after %.2fs", descriptor["elapsed_seconds"])
        raise
    descriptor["elapsed_seconds"] = round(time.perf_counter() - start, 2)
    _LOGGER.info("Training completed in %.2f seconds", descriptor["elapsed_seconds"])
    return descriptor


class TrainingModules(NamedTuple):
    """Aggregated imports and helpers required for training."""

    hydra_task_config: Any
    gym_module: Any
    skrl_module: Any
    runner_cls: Any
    manager_cfg_type: Any
    direct_cfg_type: Any
    direct_mar_cfg_type: Any
    direct_mar_env_type: Any
    multi_agent_to_single_agent: Any
    retrieve_file_path: Any
    print_dict: Any
    dump_yaml: Any
    dump_pickle: Any | None
    vec_env_wrapper: Any
    mlflow_module: Any | None


class LaunchState(NamedTuple):
    """Holds precomputed launch artifacts shared across training steps."""

    agent_dict: dict[str, Any]
    random_seed: int
    rollouts: int
    log_dir: Path
    resume_path: str | None


def _prepare_cli_arguments(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    hydra_args: Sequence[str],
) -> tuple[argparse.Namespace, Sequence[str]]:
    """Parse CLI inputs and emit launch argument logging."""

    _, base_tokens = _namespace_snapshot(args)
    tokens = list(base_tokens) + list(hydra_args)
    cli_args, unparsed_args = parser.parse_known_args(tokens)
    if cli_args.video:
        cli_args.enable_cameras = True
    parsed_payload, _ = _namespace_snapshot(cli_args)
    parse_report = {
        "parsed": parsed_payload,
        "hydra_overrides": list(unparsed_args),
        "launcher_hydra_args": list(hydra_args),
    }
    _LOGGER.info("SKRL runner arguments: %s", parse_report)
    return cli_args, unparsed_args


def _initialize_simulation(
    app_launcher_cls: Any, cli_args: argparse.Namespace, unparsed_args: Sequence[str]
) -> tuple[Any, Any]:
    """Launch IsaacLab simulation application using parsed arguments."""

    sys.argv = [sys.argv[0], *list(unparsed_args)]
    app_launcher = app_launcher_cls(cli_args)
    simulation_app = app_launcher.app
    kit_log_dir = getattr(getattr(simulation_app, "config", None), "log_dir", None)
    if kit_log_dir:
        _LOGGER.debug("Kit logs located at %s", kit_log_dir)
    return app_launcher, simulation_app


def _load_training_modules(
    cli_args: argparse.Namespace,
    context: AzureMLContext | None,
) -> TrainingModules:
    """Import IsaacLab, SKRL, and optional MLflow modules."""

    import gymnasium as gym_module
    import isaaclab_tasks  # noqa: F401
    import skrl as skrl_module
    from isaaclab.envs import (
        DirectMARLEnv,
        DirectMARLEnvCfg,
        DirectRLEnvCfg,
        ManagerBasedRLEnvCfg,
        multi_agent_to_single_agent,
    )
    from isaaclab.utils.assets import retrieve_file_path
    from isaaclab.utils.dict import print_dict
    from isaaclab.utils.io import dump_yaml
    from isaaclab_tasks.utils.hydra import hydra_task_config

    try:
        from isaaclab.utils.io import dump_pickle
    except ImportError:
        dump_pickle = None
    from isaaclab_rl.skrl import SkrlVecEnvWrapper

    if cli_args.ml_framework.startswith("torch"):
        from skrl.utils.runner.torch import Runner as runner_cls
    else:
        from skrl.utils.runner.jax import Runner as runner_cls

    mlflow_module = None
    if context:
        import mlflow as mlflow_module

    return TrainingModules(
        hydra_task_config=hydra_task_config,
        gym_module=gym_module,
        skrl_module=skrl_module,
        runner_cls=runner_cls,
        manager_cfg_type=ManagerBasedRLEnvCfg,
        direct_cfg_type=DirectRLEnvCfg,
        direct_mar_cfg_type=DirectMARLEnvCfg,
        direct_mar_env_type=DirectMARLEnv,
        multi_agent_to_single_agent=multi_agent_to_single_agent,
        retrieve_file_path=retrieve_file_path,
        print_dict=print_dict,
        dump_yaml=dump_yaml,
        dump_pickle=dump_pickle,
        vec_env_wrapper=SkrlVecEnvWrapper,
        mlflow_module=mlflow_module,
    )


def _close_simulation(simulation_app: Any | None) -> None:
    """Exit the process; Kit's shutdown hangs on vGPU nodes.

    See docs/gpu-configuration.md § "Isaac Sim 4.x Shutdown Fix".
    """
    os._exit(0)


def _build_run_descriptor(
    cli_args: argparse.Namespace,
    log_dir: Path,
    resume_path: str | None,
    agent_dict: dict[str, Any],
    rollouts: int,
    log_interval: int | None,
) -> dict[str, Any]:
    """Compose structured payload for runner logging."""

    descriptor: dict[str, Any] = {
        "algorithm": cli_args.algorithm,
        "ml_framework": cli_args.ml_framework,
        "log_dir": str(log_dir),
        "resume_checkpoint": bool(resume_path),
        "resume_path": resume_path,
        "trainer_timesteps": agent_dict.get("trainer", {}).get("timesteps"),
        "max_iterations": cli_args.max_iterations,
        "rollouts": rollouts,
    }
    if log_interval is not None:
        descriptor["mlflow_log_interval"] = log_interval
    return descriptor


def _prepare_launch_state(
    env_cfg: Any,
    agent_cfg: Any,
    cli_args: argparse.Namespace,
    app_launcher: Any,
    modules: TrainingModules,
) -> LaunchState:
    """Compute seed, agent config, and logging paths for a launch."""

    resume_path = _resolve_checkpoint(modules.retrieve_file_path, cli_args.checkpoint)
    agent_dict = _normalize_agent_config(agent_cfg)
    random_seed = _configure_environment(
        env_cfg,
        cli_args,
        app_launcher,
        manager_cfg_type=modules.manager_cfg_type,
        direct_cfg_type=modules.direct_cfg_type,
        direct_mar_cfg_type=modules.direct_mar_cfg_type,
    )
    rollouts = _configure_agent_training(agent_dict, cli_args, random_seed)
    _configure_jax_backend(cli_args.ml_framework, modules.skrl_module)

    log_dir = _prepare_log_paths(agent_dict, cli_args)
    _dump_config_files(log_dir, env_cfg, agent_dict, modules.dump_yaml, modules.dump_pickle)

    if isinstance(env_cfg, modules.manager_cfg_type) and cli_args.export_io_descriptors:
        env_cfg.export_io_descriptors = True
        env_cfg.io_descriptors_output_dir = str(log_dir)

    env_cfg.log_dir = str(log_dir)
    modules.print_dict(env_cfg.to_dict())
    modules.print_dict(agent_dict)
    _log_configuration_snapshot(cli_args, env_cfg, agent_dict, random_seed, rollouts)

    return LaunchState(
        agent_dict=agent_dict,
        random_seed=random_seed,
        rollouts=rollouts,
        log_dir=log_dir,
        resume_path=resume_path,
    )


def _instantiate_environment(
    env_cfg: Any,
    cli_args: argparse.Namespace,
    modules: TrainingModules,
    log_dir: Path,
) -> Any:
    """Create and wrap the target environment for training."""

    _validate_gym_registry(cli_args.task, modules.gym_module)
    _LOGGER.info("Creating environment for task %s", cli_args.task)
    env = _create_gym_environment(cli_args.task, env_cfg, cli_args.video, modules.gym_module)
    return _wrap_environment(
        env,
        cli_args=cli_args,
        log_dir=log_dir,
        gym_module=modules.gym_module,
        multi_agent_to_single_agent=modules.multi_agent_to_single_agent,
        direct_mar_env_type=modules.direct_mar_env_type,
        vec_wrapper_cls=modules.vec_env_wrapper,
    )


def _initialize_runner(env: Any, state: LaunchState, modules: TrainingModules) -> Any:
    """Instantiate the SKRL runner and apply optional checkpointing/logging."""
    runner = modules.runner_cls(env, state.agent_dict)
    _setup_agent_checkpoint(runner, state.resume_path)
    _apply_mlflow_logging(runner, modules.mlflow_module)
    return runner


def _run_hydra_training(
    *,
    args: argparse.Namespace,
    cli_args: argparse.Namespace,
    context: AzureMLContext | None,
    app_launcher: Any,
    modules: TrainingModules,
) -> None:
    """Execute hydra-configured IsaacLab training launch."""
    if cli_args.seed == -1:
        cli_args.seed = random.randint(0, 10000)

    agent_entry = _get_agent_config_entry_point(cli_args)
    _LOGGER.info("Starting training: task=%s, seed=%s", cli_args.task, cli_args.seed)

    @modules.hydra_task_config(cli_args.task, agent_entry)
    def _launch(env_cfg, agent_cfg):
        state = _prepare_launch_state(env_cfg, agent_cfg, cli_args, app_launcher, modules)
        env = _instantiate_environment(env_cfg, cli_args, modules, state.log_dir)

        try:
            runner = _initialize_runner(env, state, modules)
            _run_training_with_mlflow(
                runner=runner,
                state=state,
                env_cfg=env_cfg,
                args=args,
                cli_args=cli_args,
                context=context,
                modules=modules,
            )
        finally:
            prepare_for_shutdown()
            env.close()

    _launch()


def _run_training_with_mlflow(
    *,
    runner: Any,
    state: LaunchState,
    env_cfg: Any,
    args: argparse.Namespace,
    cli_args: argparse.Namespace,
    context: AzureMLContext | None,
    modules: TrainingModules,
) -> None:
    """Execute training loop with optional MLflow tracking."""
    if modules.mlflow_module is None:
        # No MLflow - just run training
        descriptor = _build_run_descriptor(
            cli_args, state.log_dir, state.resume_path, state.agent_dict, state.rollouts, None
        )
        _execute_training_loop(runner, descriptor)
        return

    # With MLflow tracking
    with mlflow_run_context(
        modules.mlflow_module,
        context=context,
        args=args,
        cli_args=cli_args,
        env_cfg=env_cfg,
        log_dir=state.log_dir,
        resume_path=state.resume_path,
        random_seed=state.random_seed,
        rollouts=state.rollouts,
    ) as mlflow_state:
        descriptor = _build_run_descriptor(
            cli_args, state.log_dir, state.resume_path, state.agent_dict, state.rollouts, mlflow_state.log_interval
        )
        try:
            _execute_training_loop(runner, descriptor)
            active_run = modules.mlflow_module.active_run()
            if active_run:
                descriptor["mlflow_run_id"] = active_run.info.run_id
            _LOGGER.info("Training complete: %s", descriptor)
        except Exception:
            mlflow_state.outcome = "failed"
            raise


def run_training(
    *,
    args: argparse.Namespace,
    hydra_args: Sequence[str],
    context: AzureMLContext | None,
) -> None:
    """Execute SKRL training with IsaacLab environment and optional Azure ML tracking.

    Args:
        args: Parsed launch arguments including checkpoint behavior.
        hydra_args: Sequence of Hydra overrides to forward to IsaacLab launcher.
        context: Azure ML context enabling MLflow tracking and model registration.

    Raises:
        SystemExit: If IsaacLab dependencies are missing or task is unavailable.
    """
    try:
        from isaaclab.app import AppLauncher
    except ImportError as exc:
        raise SystemExit("IsaacLab packages are required for SKRL training") from exc

    parser = _build_parser(AppLauncher)
    cli_args, unparsed_args = _prepare_cli_arguments(parser, args, hydra_args)

    app_launcher, simulation_app = _initialize_simulation(AppLauncher, cli_args, unparsed_args)
    install_ansi_stripping()
    try:
        modules = _load_training_modules(cli_args, context)
        _run_hydra_training(
            args=args,
            cli_args=cli_args,
            context=context,
            app_launcher=app_launcher,
            modules=modules,
        )
    finally:
        _close_simulation(simulation_app)
