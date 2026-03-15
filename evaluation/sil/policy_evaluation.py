"""Generic IsaacLab policy evaluation.

Evaluates any trained IsaacLab policy with automatic task/framework detection.
Supports SKRL and RSL-RL frameworks with CLI-based configuration.

The validation job receives model metadata (task, framework, threshold) via CLI
arguments that are populated from Azure ML model tags by the submission script.
This approach follows Azure ML best practices where model metadata is stored in
model tags/properties rather than separate files.

Usage:
    python -m training.scripts.policy_evaluation \
        --model-path /mnt/azureml/model \
        --task Isaac-Velocity-Rough-Anymal-C-v0 \
        --framework skrl \
        --eval-episodes 100 \
        --headless

Exit Codes:
    0 - Validation passed (success_rate >= threshold)
    1 - Validation failed or error
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch

from training.simulation_shutdown import prepare_for_shutdown

_LOGGER = logging.getLogger("isaaclab.eval")


# =============================================================================
# Metadata
# =============================================================================


@dataclass
class ModelMetadata:
    """Model metadata for validation configuration.

    Metadata is provided via CLI arguments which are populated from Azure ML
    model tags by the submission script. The 'auto' sentinel value indicates
    the field was not provided and should use defaults.
    """

    task: str = ""
    framework: str = "skrl"
    success_threshold: float = 0.7


def load_metadata(
    task: str = "",
    framework: str = "",
    success_threshold: float = -1.0,
) -> ModelMetadata:
    """Create metadata from CLI arguments.

    The submission script (submit-azureml-validation.sh) fetches model tags
    from Azure ML and passes them as CLI arguments. The 'auto' sentinel value
    indicates the field should use defaults.

    Args:
        task: Task ID from CLI (or 'auto' to use default)
        framework: Framework from CLI (or 'auto' to use default)
        success_threshold: Threshold from CLI (or negative to use default)

    Returns:
        ModelMetadata with resolved values
    """
    meta = ModelMetadata()

    # Handle 'auto' sentinel - means not specified, use default
    if task and task != "auto":
        meta.task = task
    if framework and framework != "auto":
        meta.framework = framework
    if success_threshold >= 0:
        meta.success_threshold = success_threshold

    return meta


# =============================================================================
# Agent Loading
# =============================================================================


def load_agent(
    checkpoint_path: str,
    framework: str,
    task_id: str,
    env: Any,
    device: str = "cuda",
) -> Any:
    """Load agent based on framework.

    Args:
        checkpoint_path: Path to checkpoint file
        framework: Framework type (skrl, rsl_rl)
        task_id: IsaacLab task identifier
        env: Wrapped environment instance (needed for SKRL Runner)
        device: Torch device string

    Returns:
        Loaded agent instance

    Raises:
        ValueError: If framework is not supported
    """
    _LOGGER.info("Loading %s agent from %s", framework, checkpoint_path)

    if framework == "skrl":
        return _load_skrl(checkpoint_path, task_id, env, device)
    elif framework == "rsl_rl":
        return _load_rsl_rl(checkpoint_path, device)
    else:
        raise ValueError(f"Unsupported framework: {framework}")


def _load_skrl(
    checkpoint_path: str,
    task_id: str,
    env: Any,
    device: str,
) -> Any:
    """Load SKRL agent using SKRL Runner for proper model instantiation.

    The SKRL Runner handles model creation based on the agent configuration,
    then we load the checkpoint weights into those models.

    Note: The hydra_task_config decorator parses sys.argv, so we must
    temporarily clear it to avoid conflicts with our CLI arguments.
    """
    from isaaclab_tasks.utils.hydra import hydra_task_config
    from skrl.utils.runner.torch import Runner

    agent_cfg = None

    @hydra_task_config(task_id, "skrl_cfg_entry_point")
    def get_cfg(env_cfg: Any, cfg: Any) -> None:
        nonlocal agent_cfg
        agent_cfg = cfg

    # Temporarily clear sys.argv to prevent Hydra from parsing our CLI args
    original_argv = sys.argv
    sys.argv = [sys.argv[0]]  # Keep only the script name
    try:
        get_cfg()
    finally:
        sys.argv = original_argv

    if agent_cfg is None:
        raise ValueError(f"Could not load agent configuration for task {task_id}")

    # Normalize agent config to dict if needed
    if hasattr(agent_cfg, "to_dict"):
        agent_dict = agent_cfg.to_dict()
    elif isinstance(agent_cfg, dict):
        agent_dict = agent_cfg
    else:
        raise ValueError(f"Unexpected agent config type: {type(agent_cfg)}")

    _LOGGER.info("Creating SKRL Runner with agent config")

    # Create Runner which instantiates models based on config
    runner = Runner(env, agent_dict)

    # Load checkpoint into the runner's agent
    runner.agent.load(checkpoint_path)
    runner.agent.set_running_mode("eval")

    _LOGGER.info("SKRL agent loaded and set to eval mode")
    return runner.agent


def _load_rsl_rl(checkpoint_path: str, device: str) -> Any:
    """Load RSL-RL agent from checkpoint."""
    checkpoint = torch.load(checkpoint_path, map_location=device, weights_only=False)
    from rsl_rl.modules import ActorCritic

    policy = ActorCritic(**checkpoint.get("model_cfg", {}))
    policy.load_state_dict(checkpoint["model_state_dict"])
    policy.eval()
    return policy.to(device)


# =============================================================================
# Evaluation
# =============================================================================


@dataclass
class Metrics:
    """Episode metrics collector."""

    rewards: list[float] = field(default_factory=list)
    lengths: list[int] = field(default_factory=list)
    successes: int = 0

    def add(self, reward: float, length: int, success: bool) -> None:
        """Add metrics for a completed episode.

        Args:
            reward: Total episode reward
            length: Episode length in steps
            success: Whether episode was successful
        """
        self.rewards.append(reward)
        self.lengths.append(length)
        if success:
            self.successes += 1

    @property
    def count(self) -> int:
        """Return number of completed episodes."""
        return len(self.rewards)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for JSON serialization.

        Returns:
            Dictionary with aggregated metrics
        """
        if not self.rewards:
            return {"error": "No episodes completed"}
        return {
            "eval_episodes": self.count,
            "mean_reward": float(np.mean(self.rewards)),
            "std_reward": float(np.std(self.rewards)),
            "mean_length": float(np.mean(self.lengths)),
            "success_rate": self.successes / self.count,
        }


def evaluate(env: Any, agent: Any, num_episodes: int, framework: str) -> Metrics:
    """Run evaluation episodes and collect metrics.

    Args:
        env: IsaacLab environment
        agent: Loaded agent instance
        num_episodes: Number of episodes to evaluate
        framework: Framework type for action selection

    Returns:
        Metrics instance with evaluation results
    """
    metrics = Metrics()
    num_envs = env.num_envs
    ep_rewards = torch.zeros(num_envs, device=env.device)
    ep_lengths = torch.zeros(num_envs, dtype=torch.int32, device=env.device)

    obs, _ = env.reset()
    step = 0

    _LOGGER.info("Starting evaluation: %d episodes, %d parallel envs", num_episodes, num_envs)

    while metrics.count < num_episodes:
        with torch.inference_mode():
            actions = agent.act(obs, timestep=step, timesteps=0)[0] if framework == "skrl" else agent.act_inference(obs)

        obs, rewards, terminated, truncated, info = env.step(actions)
        ep_rewards += rewards.squeeze()
        ep_lengths += 1
        step += 1

        dones = (terminated | truncated).squeeze()
        done_indices = torch.where(dones)[0]

        for idx in done_indices:
            if metrics.count >= num_episodes:
                break

            success = info.get("success", terminated)[idx].item()
            metrics.add(
                float(ep_rewards[idx]),
                int(ep_lengths[idx]),
                bool(success) and not truncated[idx].item(),
            )
            ep_rewards[idx] = 0
            ep_lengths[idx] = 0

            if metrics.count % 20 == 0:
                _LOGGER.info("Progress: %d/%d episodes", metrics.count, num_episodes)

    _LOGGER.info("Evaluation loop completed with %d episodes", metrics.count)
    return metrics


# =============================================================================
# Main
# =============================================================================


def find_checkpoint(model_path: str) -> str:
    """Find checkpoint file from model path.

    Handles both cases:
    - model_path is a directory: search for checkpoint files inside
    - model_path is a file: return the file directly (Azure ML single-file model)

    Args:
        model_path: Path to model directory or checkpoint file

    Returns:
        Path to checkpoint file

    Raises:
        FileNotFoundError: If no checkpoint found
    """
    model_path_obj = Path(model_path)

    # Case 1: model_path is already a checkpoint file
    if model_path_obj.is_file():
        if model_path_obj.suffix in (".pt", ".pth"):
            _LOGGER.info("Model path is a checkpoint file: %s", model_path)
            return str(model_path_obj)
        raise FileNotFoundError(f"Model path is a file but not a checkpoint: {model_path}")

    # Case 2: model_path is a directory - search for checkpoints
    if model_path_obj.is_dir():
        for pattern in ["best_agent.pt", "checkpoints/*.pt", "*.pt", "*.pth"]:
            matches = list(model_path_obj.glob(pattern))
            if matches:
                checkpoint = str(max(matches, key=lambda p: p.stat().st_mtime))
                _LOGGER.info("Found checkpoint in directory: %s", checkpoint)
                return checkpoint

    raise FileNotFoundError(f"No checkpoint found at {model_path}")


def _build_parser() -> argparse.ArgumentParser:
    """Build argument parser for policy evaluation."""
    parser = argparse.ArgumentParser(
        description="Generic IsaacLab policy evaluation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--model-path",
        required=True,
        help="Path to mounted model directory",
    )
    parser.add_argument(
        "--task",
        default="",
        help="Override task ID (empty = use metadata)",
    )
    parser.add_argument(
        "--framework",
        default="",
        help="Override framework (empty = auto-detect)",
    )
    parser.add_argument(
        "--eval-episodes",
        type=int,
        default=100,
        help="Number of evaluation episodes",
    )
    parser.add_argument(
        "--num-envs",
        type=int,
        default=64,
        help="Number of parallel environments",
    )
    parser.add_argument(
        "--success-threshold",
        type=float,
        default=-1,
        help="Override threshold (negative = use metadata)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without rendering",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    return parser


def main() -> int:
    """Main entry point for policy evaluation.

    Returns:
        Exit code: 0 for success, 1 for failure
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = _build_parser().parse_args()

    # Load metadata from CLI arguments (populated from Azure ML model tags)
    meta = load_metadata(
        task=args.task,
        framework=args.framework,
        success_threshold=args.success_threshold,
    )
    if not meta.task:
        _LOGGER.error(
            "Task not specified. Provide --task argument or ensure the submission script fetched it from model tags."
        )
        return 1

    threshold = meta.success_threshold
    _LOGGER.info(
        "Task: %s, Framework: %s, Threshold: %.2f",
        meta.task,
        meta.framework,
        threshold,
    )

    # Launch simulation
    from isaaclab.app import AppLauncher

    AppLauncher(argparse.Namespace(headless=args.headless, enable_cameras=False))

    exit_code = 1
    try:
        import gymnasium as gym
        import isaaclab_tasks  # noqa: F401 - Required for task registration
        from isaaclab_rl.skrl import SkrlVecEnvWrapper
        from isaaclab_tasks.utils.parse_cfg import parse_env_cfg

        # Create environment
        env_cfg = parse_env_cfg(meta.task, "cuda:0", args.num_envs, use_fabric=True)
        env_cfg.seed = args.seed
        env = gym.make(meta.task, cfg=env_cfg, render_mode=None)
        if meta.framework == "skrl":
            env = SkrlVecEnvWrapper(env, ml_framework="torch")

        # Load agent and evaluate
        checkpoint = find_checkpoint(args.model_path)
        agent = load_agent(
            checkpoint,
            meta.framework,
            meta.task,
            env,
            "cuda",
        )
        metrics = evaluate(env, agent, args.eval_episodes, meta.framework)
        result = metrics.to_dict()

        # Output results with explicit flush for containerized environments
        print("\n" + "=" * 60, flush=True)
        print(json.dumps(result, indent=2), flush=True)
        print("=" * 60, flush=True)

        success_rate = result.get("success_rate", 0)
        if success_rate >= threshold:
            print(f"\n✅ PASSED: {success_rate:.2f} >= {threshold}", flush=True)
            exit_code = 0
        else:
            print(f"\n❌ FAILED: {success_rate:.2f} < {threshold}", flush=True)
            exit_code = 1

        # Explicit environment cleanup before exit
        prepare_for_shutdown()
        env.close()
        return exit_code

    except Exception as e:
        _LOGGER.exception("Evaluation failed: %s", e)
        return 1
    finally:
        os._exit(exit_code)


if __name__ == "__main__":
    sys.exit(main())
