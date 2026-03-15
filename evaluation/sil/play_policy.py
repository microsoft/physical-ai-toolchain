"""Script to play an exported policy (ONNX or TorchScript) in Isaac Sim environment.

This script runs inference using an exported policy model against the same
simulation environment used for training, enabling validation of the exported
model before Azure ML deployment.

Supports both ONNX and TorchScript (JIT) model formats.
"""

"""Launch Isaac Sim Simulator first."""

import argparse
import sys
from pathlib import Path

from isaaclab.app import AppLauncher

from common import cli_args  # isort: skip
from training.simulation_shutdown import prepare_for_shutdown

parser = argparse.ArgumentParser(description="Run inference using an exported ONNX or TorchScript policy.")
parser.add_argument("--video", action="store_true", default=False, help="Record videos during inference.")
parser.add_argument(
    "--video_length",
    type=int,
    default=200,
    help="Length of the recorded video (in steps).",
)
parser.add_argument(
    "--disable_fabric",
    action="store_true",
    default=False,
    help="Disable fabric and use USD I/O operations.",
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
parser.add_argument(
    "--model",
    type=str,
    required=True,
    help="Path to the exported policy model (.onnx or .pt).",
)
parser.add_argument(
    "--format",
    type=str,
    choices=["onnx", "jit"],
    default=None,
    help="Model format: 'onnx' or 'jit'. Auto-detected from file extension if not specified.",
)
parser.add_argument(
    "--real-time",
    action="store_true",
    default=False,
    help="Run in real-time, if possible.",
)
parser.add_argument(
    "--max-steps",
    type=int,
    default=1000,
    help="Maximum number of simulation steps (0 for unlimited).",
)
parser.add_argument(
    "--use-gpu",
    action="store_true",
    default=False,
    help="Use CUDA execution provider for ONNX Runtime (ONNX only).",
)
parser.add_argument(
    "--output-metrics",
    type=str,
    default=None,
    help="Path to save metrics JSON file (enables structured output for dashboard).",
)

cli_args.add_rsl_rl_args(parser)
AppLauncher.add_app_launcher_args(parser)
args_cli, hydra_args = parser.parse_known_args()

if args_cli.video:
    args_cli.enable_cameras = True

sys.argv = [sys.argv[0], *hydra_args]

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

"""Rest everything follows."""

import os
import time

import gymnasium as gym
import numpy as np
import torch
from isaaclab.envs import (
    DirectMARLEnv,
    DirectMARLEnvCfg,
    DirectRLEnvCfg,
    ManagerBasedRLEnvCfg,
    multi_agent_to_single_agent,
)
from isaaclab.utils.dict import print_dict

# Handle different versions of isaaclab_rl
try:
    from isaaclab_rl.rsl_rl import RslRlOnPolicyRunnerCfg as RslRlRunnerCfg
except ImportError:
    try:
        from isaaclab_rl.rsl_rl import RslRlBaseRunnerCfg as RslRlRunnerCfg
    except ImportError:
        from isaaclab_rl.rsl_rl import RslRlPpoRunnerCfg as RslRlRunnerCfg

import contextlib

import isaaclab_tasks  # noqa: F401
from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper
from isaaclab_tasks.utils.hydra import hydra_task_config
from tensordict import TensorDict


def detect_model_format(model_path: str) -> str:
    """Detect model format from file extension.

    Args:
        model_path: Path to the model file.

    Returns:
        Format string: 'onnx' or 'jit'.

    Raises:
        ValueError: If format cannot be determined from extension.
    """
    ext = Path(model_path).suffix.lower()
    if ext == ".onnx":
        return "onnx"
    if ext == ".pt":
        return "jit"
    raise ValueError(f"Cannot determine model format from extension '{ext}'. Use --format to specify.")


class RslRl3xCompatWrapper:
    """Compatibility wrapper for RSL-RL 3.x TensorDict observation format.

    RSL-RL 3.x expects observations to be a TensorDict with observation group keys.
    Older Isaac Lab wrappers may return raw tensors or dicts. This wrapper ensures
    get_observations() always returns a properly formatted TensorDict.
    """

    def __init__(self, env: RslRlVecEnvWrapper):
        self._env = env
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
            return TensorDict({"policy": obs}, batch_size=[self._env.num_envs])
        if isinstance(obs, tuple):
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


class JitPolicy:
    """Wrapper for JIT (TorchScript) policy inference compatible with IsaacLab environments."""

    def __init__(self, jit_path: str, device: str = "cpu"):
        """Initialize JIT model.

        Args:
            jit_path: Path to the JIT model file (.pt).
            device: Target device for inference.
        """
        self.device = device

        print(f"[INFO] Loading JIT model from: {jit_path}")
        self.model = torch.jit.load(jit_path, map_location=device)
        self.model.eval()
        print(f"[INFO] JIT model loaded on device: {device}")

    def __call__(self, obs: torch.Tensor) -> torch.Tensor:
        """Run inference on observations.

        Args:
            obs: Observation tensor of shape (num_envs, obs_dim), dict, or TensorDict with 'policy' key.

        Returns:
            Action tensor of shape (num_envs, action_dim).
        """
        if hasattr(obs, "__getitem__") and not isinstance(obs, torch.Tensor):
            obs = obs["policy"]
        with torch.inference_mode():
            return self.model(obs.to(self.device))


class OnnxPolicy:
    """Wrapper for ONNX policy inference compatible with IsaacLab environments."""

    def __init__(self, onnx_path: str, device: str = "cpu", use_gpu: bool = False):
        """Initialize ONNX inference session.

        Args:
            onnx_path: Path to the ONNX model file.
            device: Target device for output tensors.
            use_gpu: Whether to use CUDA execution provider.
        """
        import onnxruntime as ort

        self.device = device

        providers = ["CPUExecutionProvider"]
        if use_gpu:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        print(f"[INFO] Loading ONNX model from: {onnx_path}")
        self.session = ort.InferenceSession(onnx_path, providers=providers)

        active_provider = self.session.get_providers()[0]
        print(f"[INFO] ONNX Runtime using: {active_provider}")

        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name

        input_shape = self.session.get_inputs()[0].shape
        output_shape = self.session.get_outputs()[0].shape
        print(f"[INFO] Input shape: {input_shape}, Output shape: {output_shape}")

    def __call__(self, obs: torch.Tensor | dict) -> torch.Tensor:
        """Run inference on observations.

        Args:
            obs: Observation tensor of shape (num_envs, obs_dim), dict, or TensorDict with 'policy' key.

        Returns:
            Action tensor of shape (num_envs, action_dim).
        """
        if hasattr(obs, "__getitem__") and not isinstance(obs, torch.Tensor):
            obs = obs["policy"]
        obs_np = obs.cpu().numpy().astype(np.float32)
        actions_np = self.session.run([self.output_name], {self.input_name: obs_np})[0]
        return torch.from_numpy(actions_np).to(self.device)


def create_policy(model_path: str, model_format: str, device: str, use_gpu: bool):
    """Create policy wrapper based on model format.

    Args:
        model_path: Path to the model file.
        model_format: Format of the model ('onnx' or 'jit').
        device: Target device for inference.
        use_gpu: Whether to use GPU for ONNX inference.

    Returns:
        Policy wrapper instance.
    """
    if model_format == "onnx":
        return OnnxPolicy(model_path, device=device, use_gpu=use_gpu)
    return JitPolicy(model_path, device=device)


@hydra_task_config(args_cli.task, args_cli.agent)
def main(
    env_cfg: ManagerBasedRLEnvCfg | DirectRLEnvCfg | DirectMARLEnvCfg,
    agent_cfg: RslRlRunnerCfg,
):
    """Play with exported policy in Isaac Sim environment."""
    agent_cfg: RslRlRunnerCfg = cli_args.update_rsl_rl_cfg(agent_cfg, args_cli)
    env_cfg.scene.num_envs = args_cli.num_envs if args_cli.num_envs is not None else env_cfg.scene.num_envs

    env_cfg.seed = agent_cfg.seed
    env_cfg.sim.device = args_cli.device if args_cli.device is not None else env_cfg.sim.device

    model_path = os.path.abspath(args_cli.model)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path}")

    model_format = args_cli.format if args_cli.format else detect_model_format(model_path)
    print(f"[INFO] Using model format: {model_format.upper()}")

    log_dir = os.path.dirname(model_path)
    env_cfg.log_dir = log_dir

    env = gym.make(args_cli.task, cfg=env_cfg, render_mode="rgb_array" if args_cli.video else None)

    if isinstance(env.unwrapped, DirectMARLEnv):
        env = multi_agent_to_single_agent(env)

    if args_cli.video:
        video_kwargs = {
            "video_folder": os.path.join(log_dir, "videos", f"{model_format}_play"),
            "step_trigger": lambda step: step == 0,
            "video_length": args_cli.video_length,
            "disable_logger": True,
        }
        print(f"[INFO] Recording videos during {model_format.upper()} inference.")
        print_dict(video_kwargs, nesting=4)
        env = gym.wrappers.RecordVideo(env, **video_kwargs)

    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    env = RslRl3xCompatWrapper(env)

    policy = create_policy(model_path, model_format, device=env.unwrapped.device, use_gpu=args_cli.use_gpu)

    dt = env.unwrapped.step_dt

    obs = env.get_observations()
    timestep = 0
    total_reward = 0.0
    episode_rewards = []
    current_episode_reward = torch.zeros(env_cfg.scene.num_envs, device=env.unwrapped.device)

    inference_times = []

    print(f"\n[INFO] Starting {model_format.upper()} policy inference...")
    print(f"[INFO] Num envs: {env_cfg.scene.num_envs}")
    print(f"[INFO] Max steps: {args_cli.max_steps if args_cli.max_steps > 0 else 'unlimited'}")
    print("-" * 60)

    while simulation_app.is_running():
        start_time = time.time()

        inf_start = time.perf_counter()
        actions = policy(obs)
        inf_time = (time.perf_counter() - inf_start) * 1000
        inference_times.append(inf_time)

        obs, rewards, dones, _ = env.step(actions)

        current_episode_reward += rewards
        done_envs = dones.nonzero(as_tuple=False).squeeze(-1)
        if len(done_envs) > 0:
            for idx in done_envs:
                episode_rewards.append(current_episode_reward[idx].item())
            current_episode_reward[done_envs] = 0.0

        total_reward += rewards.sum().item()
        timestep += 1

        if timestep % 100 == 0:
            avg_inf_time = np.mean(inference_times[-100:])
            print(
                f"Step {timestep}: "
                f"avg_reward={total_reward / (timestep * env_cfg.scene.num_envs):.4f}, "
                f"episodes={len(episode_rewards)}, "
                f"inf_time={avg_inf_time:.2f}ms"
            )

        if args_cli.video and timestep == args_cli.video_length:
            break

        if args_cli.max_steps > 0 and timestep >= args_cli.max_steps:
            break

        sleep_time = dt - (time.time() - start_time)
        if args_cli.real_time and sleep_time > 0:
            time.sleep(sleep_time)

    print("-" * 60)
    print(f"\n[RESULTS] {model_format.upper()} Policy Inference Summary")
    print(f"  Total steps: {timestep}")
    print(f"  Total episodes completed: {len(episode_rewards)}")
    if episode_rewards:
        print(f"  Mean episode reward: {np.mean(episode_rewards):.4f}")
        print(f"  Std episode reward: {np.std(episode_rewards):.4f}")
    print(f"  Mean inference time: {np.mean(inference_times):.2f}ms")
    print(f"  Max inference time: {np.max(inference_times):.2f}ms")
    print(f"  Min inference time: {np.min(inference_times):.2f}ms")

    if args_cli.output_metrics:
        import json

        metrics = {
            "format": model_format,
            "task": args_cli.task,
            "num_envs": env_cfg.scene.num_envs,
            "total_steps": timestep,
            "total_episodes": len(episode_rewards),
            "mean_episode_reward": float(np.mean(episode_rewards)) if episode_rewards else 0.0,
            "std_episode_reward": float(np.std(episode_rewards)) if episode_rewards else 0.0,
            "min_episode_reward": float(np.min(episode_rewards)) if episode_rewards else 0.0,
            "max_episode_reward": float(np.max(episode_rewards)) if episode_rewards else 0.0,
            "mean_inference_time_ms": float(np.mean(inference_times)),
            "std_inference_time_ms": float(np.std(inference_times)),
            "min_inference_time_ms": float(np.min(inference_times)),
            "max_inference_time_ms": float(np.max(inference_times)),
            "p50_inference_time_ms": float(np.percentile(inference_times, 50)),
            "p95_inference_time_ms": float(np.percentile(inference_times, 95)),
            "p99_inference_time_ms": float(np.percentile(inference_times, 99)),
            "throughput_steps_per_sec": timestep / (sum(inference_times) / 1000) if inference_times else 0.0,
            "total_reward": float(total_reward),
        }
        if model_format == "onnx":
            metrics["use_gpu"] = args_cli.use_gpu
        metrics_path = os.path.abspath(args_cli.output_metrics)
        os.makedirs(os.path.dirname(metrics_path), exist_ok=True)
        with open(metrics_path, "w") as f:
            json.dump(metrics, f, indent=2)
        print(f"\n[INFO] Metrics saved to: {metrics_path}")

    prepare_for_shutdown()
    env.close()


if __name__ == "__main__":
    main()
    simulation_app.close()
