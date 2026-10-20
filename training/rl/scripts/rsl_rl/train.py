"""RSL-RL training orchestration with Isaac Lab environments and MLflow tracking.

This module provides the main training loop for reinforcement learning agents using
the RSL-RL library with Isaac Lab simulation environments. It handles:
- Environment and agent configuration via Hydra
- Checkpoint loading and model registration
- MLflow metric logging and artifact tracking
- Video recording of training rollouts
- Tracking and registration through the selected backend (Azure ML or an MLflow server)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from isaaclab.app import AppLauncher

from training.rl import cli_args
from training.rl.simulation_shutdown import prepare_for_shutdown
from training.stream import install_ansi_stripping
from training.utils.tracking import TRACKING_BACKENDS, TrackingBackend, create_tracking_backend

# add argparse arguments
parser = argparse.ArgumentParser(description="Train an RL agent with RSL-RL.")
parser.add_argument("--video", action="store_true", default=False, help="Record videos during training.")
parser.add_argument(
    "--video_length",
    type=int,
    default=200,
    help="Length of the recorded video (in steps).",
)
parser.add_argument(
    "--video_interval",
    type=int,
    default=2000,
    help="Interval between video recordings (in steps).",
)
parser.add_argument("--num_envs", type=int, default=None, help="Number of environments to simulate.")
parser.add_argument("--task", type=str, default=None, help="Name of the task.")
parser.add_argument(
    "--agent",
    type=str,
    default="rsl_rl_cfg_entry_point",
    help="Name of the RL agent configuration entry point.",
)
parser.add_argument("--seed", type=int, default=None, help="Seed used for the environment")
parser.add_argument("--max_iterations", type=int, default=None, help="RL Policy training iterations.")
parser.add_argument(
    "--distributed",
    action="store_true",
    default=False,
    help="Run training with multiple GPUs or nodes.",
)
parser.add_argument(
    "--export_io_descriptors",
    action="store_true",
    default=False,
    help="Export IO descriptors.",
)
parser.add_argument(
    "--tracking_backend",
    choices=TRACKING_BACKENDS,
    default=None,
    help="Where runs and checkpoints go: azureml, mlflow (MLFLOW_TRACKING_URI), or none. "
    "Defaults to TRACKING_BACKEND, then azureml.",
)
parser.add_argument(
    "--tracking_all_ranks",
    action="store_true",
    default=False,
    help="Track runs from every rank in distributed training instead of only the primary rank.",
)
parser.add_argument(
    "--register_checkpoint",
    type=str,
    default=None,
    help="Register the final checkpoint under this model name in the tracking backend's registry.",
)
# append RSL-RL cli arguments
cli_args.add_rsl_rl_args(parser)
# append AppLauncher cli args
AppLauncher.add_app_launcher_args(parser)
args_cli, hydra_args = parser.parse_known_args()

# always enable cameras to record video
if args_cli.video:
    args_cli.enable_cameras = True

# clear out sys.argv for Hydra
sys.argv = [sys.argv[0], *hydra_args]

# launch omniverse app
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

"""Check for minimum supported RSL-RL version."""

import importlib.metadata as metadata
import platform

from packaging import version

# check minimum supported rsl-rl version
RSL_RL_VERSION = "3.0.1"
installed_version = metadata.version("rsl-rl-lib")
if version.parse(installed_version) < version.parse(RSL_RL_VERSION):
    if platform.system() == "Windows":
        cmd = [
            r".\isaaclab.bat",
            "-p",
            "-m",
            "pip",
            "install",
            f"rsl-rl-lib=={RSL_RL_VERSION}",
        ]
    else:
        cmd = [
            "./isaaclab.sh",
            "-p",
            "-m",
            "pip",
            "install",
            f"rsl-rl-lib=={RSL_RL_VERSION}",
        ]
    print(
        f"Please install the correct version of RSL-RL.\nExisting version is: '{installed_version}'"
        f" and required version is: '{RSL_RL_VERSION}'.\nTo install the correct version, run:"
        f"\n\n\t{' '.join(cmd)}\n"
    )
    exit(1)

import contextlib
import statistics

import gymnasium as gym
import omni
import torch
from isaaclab.envs import (
    DirectMARLEnv,
    DirectMARLEnvCfg,
    DirectRLEnvCfg,
    ManagerBasedRLEnvCfg,
    multi_agent_to_single_agent,
)
from isaaclab.utils.dict import print_dict
from isaaclab.utils.io import dump_yaml
from isaaclab_rl.rsl_rl import RslRlOnPolicyRunnerCfg, RslRlVecEnvWrapper
from isaaclab_tasks.utils import get_checkpoint_path
from isaaclab_tasks.utils.hydra import hydra_task_config
from rsl_rl.runners import DistillationRunner, OnPolicyRunner
from tensordict import TensorDict

from training.utils.integrity import safe_load_rsl_rl_checkpoint

try:
    import isaaclab_aeon.tasks  # noqa: F401
except ImportError:
    print("[WARNING] Custom tasks module (isaaclab_aeon) not found")


try:
    from training.utils.metrics import SystemMetricsCollector
except ImportError:
    SystemMetricsCollector = None
    print("[WARNING] System metrics collector not available. Training will log without system metrics.")

torch.backends.cuda.matmul.allow_tf32 = True
torch.backends.cudnn.allow_tf32 = True
torch.backends.cudnn.deterministic = False
torch.backends.cudnn.benchmark = False


class RslRl3xCompatWrapper:
    """Compatibility wrapper for RSL-RL 3.x TensorDict observation format.

    RSL-RL 3.x expects observations to be a TensorDict with observation group keys.
    Older Isaac Lab wrappers may return raw tensors or dicts. This wrapper ensures
    get_observations() always returns a properly formatted TensorDict.
    """

    def __init__(self, env: RslRlVecEnvWrapper):
        self._env = env
        # Proxy all attributes to the underlying env
        for attr in dir(env):
            if not attr.startswith("_") and attr not in ("get_observations", "step", "reset"):
                with contextlib.suppress(AttributeError):
                    setattr(self, attr, getattr(env, attr))

    def __getattr__(self, name: str):
        return getattr(self._env, name)

    def _ensure_tensordict(self, obs) -> TensorDict:
        """Convert observations to TensorDict format expected by RSL-RL 3.x."""
        if isinstance(obs, TensorDict):
            return obs
        if isinstance(obs, dict):
            return TensorDict(obs, batch_size=[self._env.num_envs])
        if isinstance(obs, torch.Tensor):
            # Single tensor - wrap as 'policy' observation group
            return TensorDict({"policy": obs}, batch_size=[self._env.num_envs])
        if isinstance(obs, tuple):
            # Tuple (obs_tensor, extras) from older wrappers
            obs_data = obs[0]
            if isinstance(obs_data, dict):
                return TensorDict(obs_data, batch_size=[self._env.num_envs])
            return TensorDict({"policy": obs_data}, batch_size=[self._env.num_envs])
        raise TypeError(f"Unsupported observation type: {type(obs)}")

    def get_observations(self) -> TensorDict:
        """Get observations in RSL-RL 3.x TensorDict format."""
        obs = self._env.get_observations()
        return self._ensure_tensordict(obs)

    def step(self, actions: torch.Tensor):
        """Step the environment and return observations as TensorDict."""
        result = self._env.step(actions)
        # result is (obs, rew, dones, extras) - ensure obs is TensorDict
        obs, rew, dones, extras = result
        obs_td = self._ensure_tensordict(obs)
        return obs_td, rew, dones, extras

    def reset(self):
        """Reset the environment and return observations as TensorDict."""
        result = self._env.reset()
        if isinstance(result, tuple):
            obs, extras = result
            return self._ensure_tensordict(obs), extras
        return self._ensure_tensordict(result), {}


def _is_primary_rank(args_cli: argparse.Namespace, app_launcher: AppLauncher) -> bool:
    """Return True when current process is responsible for logging side effects."""

    if not args_cli.distributed:
        return True
    return getattr(app_launcher, "local_rank", 0) == 0


def _resolve_env_count(env_cfg: object) -> int | None:
    """Best-effort extraction of the configured number of environments."""

    scene = getattr(env_cfg, "scene", None)
    if scene is not None and hasattr(scene, "num_envs"):
        return scene.num_envs
    return getattr(env_cfg, "num_envs", None)


def _connect_tracking(backend_name: str | None, *, experiment_name: str) -> TrackingBackend | None:
    """Create and connect the selected tracking backend, or return None to train untracked.

    Tracking failures never stop training; the run continues with local outputs only.
    """
    try:
        tracking = create_tracking_backend(backend_name, experiment_name=experiment_name)
        if not tracking.enabled:
            print("[INFO] Tracking disabled; training writes local outputs only.")
            return None
        # Reading the URI connects to the backend (Azure ML bootstraps its workspace here).
        tracking_uri = tracking.tracking_uri
    except Exception as exc:
        print(f"[WARNING] Tracking backend unavailable: {exc}")
        print("[INFO] Training will proceed without tracking.")
        return None

    print(f"[INFO] Tracking backend '{tracking.name}' connected: {tracking_uri}")
    if tracking.storage:
        print("[INFO] Azure Storage enabled for checkpoint uploads")
    return tracking


def _start_mlflow_run(
    *,
    context: TrackingBackend,
    experiment_name: str,
    run_name: str,
    tags: dict[str, str],
    params: dict[str, Any],
) -> tuple[Any | None, bool]:
    """Start an MLflow run on the tracking backend and return the module with activation state."""
    try:
        import mlflow
    except ImportError as exc:
        print(f"[WARNING] MLflow not available: {exc}")
        return None, False

    try:
        context.configure_mlflow(mlflow, experiment_name=experiment_name)
        mlflow.start_run(run_name=run_name)
        if tags:
            mlflow.set_tags(tags)
        if params:
            serializable_params = {
                k: v for k, v in params.items() if isinstance(v, int | float | str | bool) or v is None
            }
            mlflow.log_params(serializable_params)
        print(f"[INFO] MLflow run started: experiment='{experiment_name}', run='{run_name}'")
        return mlflow, True
    except Exception as exc:
        print(f"[WARNING] Failed to start MLflow run: {exc}")
        return None, False


def _log_config_artifacts(mlflow_module: Any | None, log_dir: str) -> None:
    """Upload environment and agent configuration artifacts to MLflow."""

    if mlflow_module is None:
        return

    params_dir = Path(log_dir) / "params"
    if not params_dir.exists():
        return

    artifact_map = {
        "env.yaml": "config",
        "agent.yaml": "config",
    }

    for relative_path, artifact_path in artifact_map.items():
        candidate = params_dir / relative_path
        if candidate.exists():
            try:
                mlflow_module.log_artifact(str(candidate), artifact_path=artifact_path)
            except Exception as exc:
                print(f"[WARNING] Failed to log {relative_path}: {exc}")


def _sync_logs_to_storage(
    storage_context: Any | None,
    *,
    log_dir: str,
    experiment_name: str,
) -> None:
    """Upload log directory contents to Azure Storage when available."""

    if storage_context is None:
        return

    root = Path(log_dir)
    if not root.exists():
        return

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    files_to_upload = []

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue
        blob_name = f"training_logs/{experiment_name}/{timestamp}/{file_path.relative_to(root).as_posix()}"
        files_to_upload.append((str(file_path), blob_name))

    if files_to_upload:
        if hasattr(storage_context, "upload_files_batch"):
            uploaded = storage_context.upload_files_batch(files_to_upload)
            print(f"[INFO] Uploaded {len(uploaded)}/{len(files_to_upload)} log files to Azure Storage in batch")
        else:
            for local_path, blob_name in files_to_upload:
                try:
                    storage_context.upload_file(
                        local_path=local_path,
                        blob_name=blob_name,
                    )
                except Exception as exc:
                    print(f"[WARNING] Failed to upload log '{local_path}': {exc}")


def _register_final_model(
    *,
    context: TrackingBackend | None,
    model_path: str,
    model_name: str,
    tags: dict[str, str],
    properties: dict[str, str] | None = None,
    artifact_uri: str | None = None,
) -> bool:
    """Register a trained model in the tracking backend's model registry.

    Args:
        context: Tracking backend responsible for model registration.
        model_path: Path to model file or directory.
        model_name: Name for registered model.
        tags: Model tags (task, framework, etc.).
        properties: Model properties (success_threshold, etc.).
        artifact_uri: Run artifact URI of the already-logged model, so MLflow registers it without a second upload.

    Returns:
        True if registration succeeded, False otherwise.
    """
    if context is None:
        return False

    try:
        registered = context.register_checkpoint(
            name=model_name,
            local_path=Path(model_path),
            artifact_uri=artifact_uri,
            description="Isaac Lab RSL-RL checkpoint",
            tags=tags,
            properties=properties or {},
        )
    except ImportError as exc:
        print(f"[WARNING] Registry SDK not available for model registration: {exc}")
        return False
    except Exception as exc:
        print(f"[WARNING] Failed to register final model '{model_name}': {exc}")
        return False
    if registered is None:
        return False
    print(f"[INFO] Registered final model '{model_name}' as {registered.uri}")
    return True


def _create_enhanced_log(
    original_log,
    mlflow_module: Any | None,
    mlflow_run_active: bool,
    runner: Any,
    collect_system_metrics: bool = True,
):
    """Create wrapped log function that streams metrics to MLflow.

    Args:
        original_log: Original runner.log method to wrap.
        mlflow_module: MLflow module for logging metrics.
        mlflow_run_active: Whether MLflow run is currently active.
        runner: RSL-RL runner instance.
        collect_system_metrics: Enable system resource metrics collection (default: True).
    """
    system_metrics_collector = None
    if collect_system_metrics and SystemMetricsCollector is not None:
        try:
            system_metrics_collector = SystemMetricsCollector(
                collect_gpu=True,
                collect_disk=True,
            )
            print("[INFO] System metrics collection enabled (CPU, GPU, Memory, Disk)")
            if system_metrics_collector._gpu_available:
                print(f"[INFO] GPU metrics enabled for {len(system_metrics_collector._gpu_handles)} device(s)")
            else:
                print("[WARNING] GPU metrics unavailable - only CPU/Memory/Disk will be logged")
        except Exception as exc:
            print(f"[WARNING] Failed to initialize system metrics collector: {exc}")

    def enhanced_log(locs, *args, **kwargs):
        result = original_log(locs, *args, **kwargs)

        if mlflow_module and mlflow_run_active:
            try:
                current_iter = locs.get("it", 0)
                metrics_batch = {}

                if len(locs.get("rewbuffer", [])) > 0:
                    metrics_batch["mean_reward"] = statistics.mean(locs["rewbuffer"])
                    metrics_batch["mean_episode_length"] = statistics.mean(locs["lenbuffer"])

                    if "erewbuffer" in locs and len(locs["erewbuffer"]) > 0:
                        metrics_batch["mean_extrinsic_reward"] = statistics.mean(locs["erewbuffer"])
                    if "irewbuffer" in locs and len(locs["irewbuffer"]) > 0:
                        metrics_batch["mean_intrinsic_reward"] = statistics.mean(locs["irewbuffer"])

                if "loss_dict" in locs:
                    for key, value in locs["loss_dict"].items():
                        metrics_batch[f"loss_{key}"] = value

                if hasattr(runner, "alg") and hasattr(runner.alg, "learning_rate"):
                    metrics_batch["learning_rate"] = runner.alg.learning_rate

                if hasattr(runner, "alg") and hasattr(runner.alg, "policy"):
                    mean_std = runner.alg.policy.action_std.mean()
                    metrics_batch["mean_noise_std"] = mean_std.item()

                if locs.get("ep_infos"):
                    import torch

                    for key in locs["ep_infos"][0]:
                        infotensor = torch.tensor([], device=runner.device)
                        for ep_info in locs["ep_infos"]:
                            if key not in ep_info:
                                continue
                            if not isinstance(ep_info[key], torch.Tensor):
                                ep_info[key] = torch.Tensor([ep_info[key]])
                            if len(ep_info[key].shape) == 0:
                                ep_info[key] = ep_info[key].unsqueeze(0)
                            infotensor = torch.cat((infotensor, ep_info[key].to(runner.device)))
                        if infotensor.numel() > 0:
                            value = torch.mean(infotensor)
                            if key.startswith("logs_rew_"):
                                metric_name = f"reward_terms/{key[9:]}"
                            elif key.startswith("logs_cur_"):
                                metric_name = f"curriculum/{key[9:]}"
                            elif "/" in key:
                                metric_name = key
                            else:
                                metric_name = f"episode_{key}"
                            metrics_batch[metric_name] = value.item()

                system_metrics = {}
                if system_metrics_collector is not None:
                    try:
                        system_metrics = system_metrics_collector.collect_metrics()
                    except Exception as exc:
                        print(f"[WARNING] Failed to collect system metrics: {exc}")

                all_metrics = {**metrics_batch, **system_metrics}

                if all_metrics:
                    mlflow_module.log_metrics(all_metrics, step=current_iter, synchronous=False)
            except Exception as exc:
                print(f"[WARNING] Failed to log metrics to MLflow: {exc}")

        return result

    return enhanced_log


def _create_enhanced_save(
    original_save,
    mlflow_module: Any | None,
    mlflow_run_active: bool,
    storage_context: Any | None,
    log_dir: str,
    model_name: str,
    runner: Any,
):
    """Create wrapped save function that logs checkpoints to MLflow and uploads them to Azure Storage when set."""

    def enhanced_save(path, *args, **kwargs):
        result = original_save(path, *args, **kwargs)

        try:
            current_iter = getattr(runner, "current_learning_iteration", 0)
            full_path = path if os.path.isabs(path) else os.path.join(log_dir, path)
            tags_to_set = {}

            if mlflow_module and mlflow_run_active and os.path.isfile(full_path):
                mlflow_module.log_artifact(full_path, artifact_path="checkpoints")
                tags_to_set["last_checkpoint_path"] = full_path

            blob_name = None
            if storage_context and os.path.isfile(full_path):
                blob_name = storage_context.upload_checkpoint(
                    local_path=full_path,
                    model_name=model_name,
                    step=current_iter,
                )
                print(f"[INFO] Uploaded checkpoint to Azure Storage: {blob_name} (iteration {current_iter})")
            if mlflow_module and mlflow_run_active and blob_name:
                tags_to_set["last_checkpoint_blob"] = blob_name

            if mlflow_module and mlflow_run_active and tags_to_set:
                mlflow_module.set_tags(tags_to_set)
        except Exception as exc:
            print(f"[WARNING] Failed to save checkpoint artifacts: {exc}")

        return result

    return enhanced_save


@hydra_task_config(args_cli.task, args_cli.agent)
def main(
    env_cfg: ManagerBasedRLEnvCfg | DirectRLEnvCfg | DirectMARLEnvCfg,
    agent_cfg: RslRlOnPolicyRunnerCfg,
):
    """Train with RSL-RL agent."""
    is_primary_process = _is_primary_rank(args_cli, app_launcher)
    tracking_requested = args_cli.tracking_all_ranks or is_primary_process

    tracking: TrackingBackend | None = None
    mlflow_module: Any | None = None
    mlflow_run_active = False

    agent_cfg = cli_args.update_rsl_rl_cfg(agent_cfg, args_cli)
    env_cfg.scene.num_envs = args_cli.num_envs if args_cli.num_envs is not None else env_cfg.scene.num_envs
    agent_cfg.max_iterations = (
        args_cli.max_iterations if args_cli.max_iterations is not None else agent_cfg.max_iterations
    )

    env_cfg.seed = agent_cfg.seed
    env_cfg.sim.device = args_cli.device if args_cli.device is not None else env_cfg.sim.device

    if args_cli.distributed:
        env_cfg.sim.device = f"cuda:{app_launcher.local_rank}"
        agent_cfg.device = f"cuda:{app_launcher.local_rank}"

        seed = agent_cfg.seed + app_launcher.local_rank
        env_cfg.seed = seed
        agent_cfg.seed = seed

    if tracking_requested:
        tracking = _connect_tracking(args_cli.tracking_backend, experiment_name=agent_cfg.experiment_name)

    log_root_path = os.path.join("logs", "rsl_rl", agent_cfg.experiment_name)
    log_root_path = os.path.abspath(log_root_path)
    print(f"[INFO] Logging experiment in directory: {log_root_path}")
    log_dir = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    print(f"Exact experiment name requested from command line: {log_dir}")
    if agent_cfg.run_name:
        log_dir += f"_{agent_cfg.run_name}"
    log_dir = os.path.join(log_root_path, log_dir)

    resume_path: str | None = None
    if agent_cfg.resume or agent_cfg.algorithm.class_name == "Distillation":
        resume_path = get_checkpoint_path(log_root_path, agent_cfg.load_run, agent_cfg.load_checkpoint)

    if tracking is not None:
        env_count = _resolve_env_count(env_cfg)
        algorithm_name = getattr(
            getattr(agent_cfg, "algorithm", None),
            "class_name",
            getattr(agent_cfg, "class_name", "unknown"),
        )

        params = {
            "task": args_cli.task,
            "num_envs": env_count,
            "max_iterations": agent_cfg.max_iterations,
            "seed": agent_cfg.seed,
            "device": env_cfg.sim.device,
            "distributed": args_cli.distributed,
            "algorithm": algorithm_name,
            "clip_actions": agent_cfg.clip_actions,
            "resume": bool(agent_cfg.resume),
        }

        tags = {
            "entrypoint": "scripts/rsl_rl/train.py",
            "task": args_cli.task or "",
            "distributed": str(args_cli.distributed).lower(),
            "log_dir": log_dir,
            **tracking.run_tags(),
        }

        if correlation_id := os.environ.get("MLFLOW_CORRELATION_ID", "").strip():
            tags["correlation_id"] = correlation_id

        if tracking.storage:
            tags["storage_container"] = tracking.storage.container_name

        run_name = Path(log_dir).name
        mlflow_module, mlflow_run_active = _start_mlflow_run(
            context=tracking,
            experiment_name=agent_cfg.experiment_name,
            run_name=run_name,
            tags=tags,
            params=params,
        )
        if mlflow_run_active and mlflow_module is not None:
            if resume_path:
                mlflow_module.set_tag("resume_checkpoint_path", str(resume_path))
            mlflow_module.set_tag("is_primary_rank", str(is_primary_process).lower())

    storage_context = tracking.storage if tracking else None
    if tracking is not None and tracking.name == "azureml" and storage_context is None:
        print("[INFO] Azure Storage account not configured; checkpoint uploads will be skipped")

    # set the IO descriptors export flag if requested
    if isinstance(env_cfg, ManagerBasedRLEnvCfg):
        env_cfg.export_io_descriptors = args_cli.export_io_descriptors
    else:
        omni.log.warn(
            "IO descriptors are only supported for manager based RL environments. No IO descriptors will be exported."
        )

    env_cfg.log_dir = log_dir

    env = gym.make(args_cli.task, cfg=env_cfg, render_mode="rgb_array" if args_cli.video else None)

    # convert to single-agent instance if required by the RL algorithm
    if isinstance(env.unwrapped, DirectMARLEnv):
        env = multi_agent_to_single_agent(env)

    # wrap for video recording
    if args_cli.video:
        video_kwargs = {
            "video_folder": os.path.join(log_dir, "videos", "train"),
            "step_trigger": lambda step: step % args_cli.video_interval == 0,
            "video_length": args_cli.video_length,
            "disable_logger": True,
        }
        print("[INFO] Recording videos during training.")
        print_dict(video_kwargs, nesting=4)
        env = gym.wrappers.RecordVideo(env, **video_kwargs)

    # wrap around environment for rsl-rl
    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    # Apply RSL-RL 3.x compatibility wrapper to ensure TensorDict observations
    env = RslRl3xCompatWrapper(env)

    # Convert config to dict and ensure RSL-RL 3.x required fields are present
    agent_cfg_dict = agent_cfg.to_dict()
    # RSL-RL 3.x requires 'obs_groups' with a 'policy' key mapping to observation group names
    if "obs_groups" not in agent_cfg_dict or agent_cfg_dict["obs_groups"] is None:
        agent_cfg_dict["obs_groups"] = {"policy": ["policy"]}
    elif "policy" not in agent_cfg_dict["obs_groups"]:
        agent_cfg_dict["obs_groups"]["policy"] = ["policy"]
    # RSL-RL 3.x requires 'class_name' for runner selection
    runner_class_name = agent_cfg_dict.get("class_name", "OnPolicyRunner")

    if runner_class_name == "OnPolicyRunner":
        runner = OnPolicyRunner(env, agent_cfg_dict, log_dir=log_dir, device=agent_cfg.device)
    elif runner_class_name == "DistillationRunner":
        runner = DistillationRunner(env, agent_cfg_dict, log_dir=log_dir, device=agent_cfg.device)
    else:
        raise ValueError(f"Unsupported runner class: {runner_class_name}")
    runner.add_git_repo_to_log(__file__)
    if resume_path:
        print(f"[INFO]: Loading model checkpoint from: {resume_path}")
        safe_load_rsl_rl_checkpoint(resume_path, runner=runner)

    dump_yaml(os.path.join(log_dir, "params", "env.yaml"), env_cfg)
    dump_yaml(os.path.join(log_dir, "params", "agent.yaml"), agent_cfg)
    if is_primary_process and mlflow_module and mlflow_run_active:
        _log_config_artifacts(mlflow_module, log_dir)

    install_ansi_stripping()

    if is_primary_process and tracking is not None:
        runner.log = _create_enhanced_log(runner.log, mlflow_module, mlflow_run_active, runner)

        runner.save = _create_enhanced_save(
            runner.save,
            mlflow_module,
            mlflow_run_active,
            storage_context,
            log_dir,
            f"{args_cli.task}_{agent_cfg.experiment_name}",
            runner,
        )
        print(f"[INFO] Primary rank will stream metrics and checkpoints to the {tracking.name} tracking backend")

    training_outcome = "success"
    try:
        runner.learn(
            num_learning_iterations=agent_cfg.max_iterations,
            init_at_random_ep_len=True,
        )
    except Exception:
        training_outcome = "failed"
        if mlflow_module and mlflow_run_active:
            mlflow_module.set_tag("training_outcome", training_outcome)
        raise
    else:
        if mlflow_module and mlflow_run_active:
            mlflow_module.set_tag("training_outcome", training_outcome)
    finally:
        if is_primary_process:
            if storage_context:
                try:
                    _sync_logs_to_storage(
                        storage_context,
                        log_dir=log_dir,
                        experiment_name=agent_cfg.experiment_name,
                    )
                    print("[INFO] Uploaded training logs to Azure Storage")
                except Exception as exc:
                    print(f"[WARNING] Failed to upload training logs: {exc}")

            final_model_path = None
            final_artifact_uri = None
            model_candidates = list(Path(log_dir).glob("**/model_*.pt"))
            if not model_candidates:
                model_candidates = list(Path(log_dir).glob("**/policy_*.pt"))
            if model_candidates:
                final_model_path = str(max(model_candidates, key=os.path.getctime))
                print(f"[INFO] Found final model: {final_model_path}")

                if storage_context:
                    try:
                        final_blob = storage_context.upload_checkpoint(
                            local_path=final_model_path,
                            model_name=f"{args_cli.task}_{agent_cfg.experiment_name}_final",
                            step=None,
                        )
                        print(f"[INFO] Uploaded final model to Azure Storage: {final_blob}")
                    except Exception as exc:
                        print(f"[WARNING] Failed to upload final model to Azure Storage: {exc}")

                if mlflow_module and mlflow_run_active:
                    try:
                        mlflow_module.log_artifact(final_model_path, artifact_path="checkpoints/final")
                        active_run = mlflow_module.active_run()
                        if active_run is not None:
                            final_artifact_uri = (
                                f"runs:/{active_run.info.run_id}/checkpoints/final/{Path(final_model_path).name}"
                            )
                    except Exception as exc:
                        print(f"[WARNING] Failed to log final model artifact to MLflow: {exc}")

            if final_model_path and args_cli.register_checkpoint:
                _register_final_model(
                    context=tracking,
                    model_path=final_model_path,
                    model_name=args_cli.register_checkpoint,
                    artifact_uri=final_artifact_uri,
                    tags={
                        "task": args_cli.task or "",
                        "framework": "rsl_rl",
                        "algorithm": "PPO",
                        "experiment": agent_cfg.experiment_name,
                        "entrypoint": "scripts/rsl_rl/train.py",
                        "evaluated": "false",
                    },
                    properties={
                        "success_threshold": "0.7",
                    },
                )

        if mlflow_module and mlflow_run_active:
            mlflow_module.end_run()

    # close the simulator
    prepare_for_shutdown()
    env.close()


if __name__ == "__main__":
    main()
    os._exit(0)
