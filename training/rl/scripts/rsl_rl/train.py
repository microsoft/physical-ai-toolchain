"""RSL-RL training orchestration with IsaacLab environments and Azure MLflow integration.

This module provides the main training loop for reinforcement learning agents using
the RSL-RL library with IsaacLab simulation environments. It handles:
- Environment and agent configuration via Hydra
- Checkpoint loading and model registration
- MLflow metric logging and artifact tracking
- Video recording of training rollouts
- Integration with Azure ML workspaces
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from isaaclab.app import AppLauncher

from common import cli_args
from training.simulation_shutdown import prepare_for_shutdown
from training.stream import install_ansi_stripping

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
    "--disable_azure",
    action="store_true",
    default=False,
    help="Disable Azure ML integration for training.",
)
parser.add_argument(
    "--azure_primary_rank_only",
    action="store_true",
    default=True,
    help="Only primary rank logs to Azure (default: True).",
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

try:
    import isaaclab_aeon.tasks  # noqa: F401
except ImportError:
    print("[WARNING] Custom tasks module (isaaclab_aeon) not found")


# Import Azure utilities
try:
    from training.utils import AzureConfigError, bootstrap_azure_ml
    from training.utils.metrics import SystemMetricsCollector
except ImportError:
    AzureConfigError = None
    AzureMLContext = None
    bootstrap_azure_ml = None
    SystemMetricsCollector = None
    print("[WARNING] Azure utilities not available. Training will proceed without Azure integration.")

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


def _start_mlflow_run(
    *,
    context: Any,
    experiment_name: str,
    run_name: str,
    tags: dict[str, str],
    params: dict[str, Any],
) -> tuple[Any | None, bool]:
    """Start an MLflow run and return the module with activation state."""
    try:
        import mlflow
    except ImportError as exc:
        print(f"[WARNING] MLflow not available: {exc}")
        return None, False

    try:
        mlflow.set_tracking_uri(context.tracking_uri)
        mlflow.set_experiment(experiment_name)
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
    context: Any | None,
    model_path: str,
    model_name: str,
    tags: dict[str, str],
    properties: dict[str, str] | None = None,
) -> bool:
    """Register a trained model in Azure ML if dependencies are available.

    Args:
        context: Azure ML context with client.
        model_path: Path to model file or directory.
        model_name: Name for registered model.
        tags: Model tags (task, framework, etc.).
        properties: Model properties (success_threshold, etc.).

    Returns:
        True if registration succeeded, False otherwise.
    """
    if context is None:
        return False

    try:
        from azure.ai.ml.entities import Model
    except ImportError as exc:
        print(f"[WARNING] azure-ai-ml not available for model registration: {exc}")
        return False

    try:
        model = Model(
            name=model_name,
            path=model_path,
            type="custom_model",
            description="RSL-RL checkpoint registered via Azure ML",
            tags=tags,
            properties=properties or {},
        )
        context.client.models.create_or_update(model)
        print(f"[INFO] Registered final model '{model_name}' with Azure ML")
        return True
    except Exception as exc:
        print(f"[WARNING] Failed to register final model '{model_name}': {exc}")
        return False


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
    """Create wrapped save function that uploads checkpoints to MLflow and Azure Storage."""

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
    azure_enabled = not args_cli.disable_azure and (not args_cli.azure_primary_rank_only or is_primary_process)

    azure_context: Any | None = None
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

    if azure_enabled and bootstrap_azure_ml is not None:
        try:
            azure_context = bootstrap_azure_ml(experiment_name=agent_cfg.experiment_name)
            if azure_context is None:
                print("[WARNING] Azure ML bootstrap returned None - missing or invalid configuration.")
                print("[INFO] Training will proceed without Azure integration.")
            else:
                print(f"[INFO] Azure ML workspace connected: {azure_context.workspace_name}")
                if azure_context.storage:
                    print("[INFO] Azure Storage enabled for checkpoint uploads")
        except Exception as exc:
            if AzureConfigError and isinstance(exc, AzureConfigError):
                print(f"[WARNING] Azure ML bootstrap failed: {exc}")
            else:
                print(f"[WARNING] Unexpected error during Azure bootstrap: {exc}")
            azure_context = None

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

    if azure_context is not None:
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
            "azureml_workspace": azure_context.workspace_name,
        }
        if azure_context.storage:
            tags["storage_container"] = azure_context.storage.container_name

        run_name = Path(log_dir).name
        mlflow_module, mlflow_run_active = _start_mlflow_run(
            context=azure_context,
            experiment_name=agent_cfg.experiment_name,
            run_name=run_name,
            tags=tags,
            params=params,
        )
        if mlflow_run_active and mlflow_module is not None:
            if resume_path:
                mlflow_module.set_tag("resume_checkpoint_path", str(resume_path))
            mlflow_module.set_tag("is_primary_rank", str(is_primary_process).lower())

    storage_context = azure_context.storage if azure_context else None
    if azure_context and storage_context is None:
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
        runner.load(resume_path)

    dump_yaml(os.path.join(log_dir, "params", "env.yaml"), env_cfg)
    dump_yaml(os.path.join(log_dir, "params", "agent.yaml"), agent_cfg)
    if is_primary_process and mlflow_module and mlflow_run_active:
        _log_config_artifacts(mlflow_module, log_dir)

    install_ansi_stripping()

    if is_primary_process and azure_context is not None:
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
        print("[INFO] Primary rank will stream metrics, checkpoints to Azure and MLflow")

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
                    except Exception as exc:
                        print(f"[WARNING] Failed to log final model artifact to MLflow: {exc}")

                _register_final_model(
                    context=azure_context,
                    model_path=final_model_path,
                    model_name=f"rsl_rl_model_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                    tags={
                        "task": args_cli.task or "",
                        "framework": "rsl_rl",
                        "algorithm": "PPO",
                        "experiment": agent_cfg.experiment_name,
                        "entrypoint": "scripts/rsl_rl/train.py",
                        "validated": "false",
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
