"""Script to monitor checkpoint directories and evaluate new policies as they are saved."""

"""Launch Isaac Sim Simulator first."""

import argparse
import os
import sys
from pathlib import Path

from isaaclab.app import AppLauncher

from training.rl import cli_args  # isort: skip

# add argparse arguments
parser = argparse.ArgumentParser(description="Monitor checkpoints and evaluate new policies with RSL-RL.")
parser.add_argument(
    "--checkpoint_dir",
    type=str,
    required=True,
    help="Directory to monitor for new checkpoints.",
)
parser.add_argument(
    "--monitor_interval",
    type=int,
    default=30,
    help="Interval in seconds to check for new checkpoints.",
)
parser.add_argument(
    "--eval_episodes",
    type=int,
    default=10,
    help="Number of episodes to run for each policy evaluation.",
)
parser.add_argument(
    "--max_episode_length",
    type=int,
    default=1000,
    help="Maximum number of steps per episode.",
)
parser.add_argument(
    "--metrics_file",
    type=str,
    default="checkpoint_metrics.json",
    help="File to save evaluation metrics.",
)
parser.add_argument(
    "--disable_fabric",
    action="store_true",
    default=False,
    help="Disable fabric and use USD I/O operations.",
)
parser.add_argument("--num_envs", type=int, default=1, help="Number of environments to simulate.")
parser.add_argument("--task", type=str, default=None, help="Name of the task.")
parser.add_argument(
    "--agent",
    type=str,
    default="rsl_rl_cfg_entry_point",
    help="Name of the RL agent configuration entry point.",
)
parser.add_argument("--seed", type=int, default=42, help="Seed used for the environment")

# append RSL-RL cli arguments
cli_args.add_rsl_rl_args(parser)
# append AppLauncher cli args
AppLauncher.add_app_launcher_args(parser)
# parse the arguments
args_cli, hydra_args = parser.parse_known_args()

# clear out sys.argv for Hydra
sys.argv = [sys.argv[0], *hydra_args]

# launch omniverse app
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

"""Rest everything follows."""

import glob
import json
import time
from datetime import datetime

import gymnasium as gym
import isaaclab_tasks  # noqa: F401
import numpy as np
import torch
from isaaclab.envs import (
    DirectMARLEnv,
    DirectMARLEnvCfg,
    DirectRLEnvCfg,
    ManagerBasedRLEnvCfg,
    multi_agent_to_single_agent,
)
from isaaclab_rl.rsl_rl import (
    RslRlBaseRunnerCfg,
    RslRlVecEnvWrapper,
)
from isaaclab_tasks.utils.hydra import hydra_task_config
from rsl_rl.runners import DistillationRunner, OnPolicyRunner


class CheckpointMonitor:
    """Monitor checkpoint directory and evaluate new policies."""

    def __init__(
        self,
        checkpoint_dir: str,
        env_cfg: ManagerBasedRLEnvCfg | DirectRLEnvCfg | DirectMARLEnvCfg,
        agent_cfg: RslRlBaseRunnerCfg,
        eval_episodes: int = 10,
        max_episode_length: int = 1000,
        metrics_file: str = "checkpoint_metrics.json",
    ):
        """Initialize the checkpoint monitor.

        Args:
            checkpoint_dir: Directory to monitor for checkpoints
            env_cfg: Environment configuration
            agent_cfg: Agent configuration
            eval_episodes: Number of episodes to evaluate each policy
            max_episode_length: Maximum steps per episode
            metrics_file: File to save metrics
        """
        self.checkpoint_dir = Path(checkpoint_dir)
        self.env_cfg = env_cfg
        self.agent_cfg = agent_cfg
        self.eval_episodes = eval_episodes
        self.max_episode_length = max_episode_length
        self.metrics_file = metrics_file

        # Track processed checkpoints
        self.processed_checkpoints: set[int] = set()

        # Initialize environment
        self._init_environment()

        # Load existing metrics
        self.metrics_history = self._load_metrics_history()

    def _init_environment(self):
        """Initialize the Isaac Lab environment."""
        # create isaac environment
        self.env = gym.make(args_cli.task, cfg=self.env_cfg, render_mode=None)

        # convert to single-agent instance if required by the RL algorithm
        if isinstance(self.env.unwrapped, DirectMARLEnv):
            self.env = multi_agent_to_single_agent(self.env)

        # wrap around environment for rsl-rl
        self.env = RslRlVecEnvWrapper(self.env, clip_actions=self.agent_cfg.clip_actions)

    def _load_metrics_history(self) -> dict:
        """Load existing metrics history from file."""
        if os.path.exists(self.metrics_file):
            try:
                with open(self.metrics_file) as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                pass
        return {}

    def _save_metrics_history(self):
        """Save metrics history to file."""
        with open(self.metrics_file, "w") as f:
            json.dump(self.metrics_history, f, indent=2)

    def _get_checkpoint_files(self) -> list[str]:
        """Get all checkpoint files in the monitored directory."""
        pattern = os.path.join(self.checkpoint_dir, "model_*.pt")
        return sorted(glob.glob(pattern))

    def _extract_checkpoint_number(self, checkpoint_path: str) -> int:
        """Extract checkpoint number from filename."""
        filename = os.path.basename(checkpoint_path)
        # Extract number from model_X.pt
        number_str = filename.replace("model_", "").replace(".pt", "")
        return int(number_str)

    def _load_policy(self, checkpoint_path: str):
        """Load policy from checkpoint."""
        print(f"[INFO] Loading checkpoint: {checkpoint_path}")

        # Initialize runner
        if self.agent_cfg.class_name == "OnPolicyRunner":
            runner = OnPolicyRunner(
                self.env,
                self.agent_cfg.to_dict(),
                log_dir=None,
                device=self.agent_cfg.device,
            )
        elif self.agent_cfg.class_name == "DistillationRunner":
            runner = DistillationRunner(
                self.env,
                self.agent_cfg.to_dict(),
                log_dir=None,
                device=self.agent_cfg.device,
            )
        else:
            raise ValueError(f"Unsupported runner class: {self.agent_cfg.class_name}")

        # Load checkpoint
        runner.load(checkpoint_path)

        # Get inference policy
        policy = runner.get_inference_policy(device=self.env.unwrapped.device)

        return policy

    def _evaluate_policy(self, policy, checkpoint_number: int) -> dict:
        """Evaluate a policy and return metrics."""
        print(f"[INFO] Evaluating checkpoint {checkpoint_number} for {self.eval_episodes} episodes...")

        episode_rewards = []
        episode_lengths = []
        success_count = 0

        for episode in range(self.eval_episodes):
            obs = self.env.reset()
            episode_reward = 0.0
            episode_length = 0
            done = False

            while not done and episode_length < self.max_episode_length:
                with torch.inference_mode():
                    actions = policy(obs)
                    obs, rewards, terminated, truncated, infos = self.env.step(actions)

                    # Handle different reward formats
                    if isinstance(rewards, torch.Tensor):
                        reward = float(rewards[0]) if rewards.numel() > 0 else 0.0
                    else:
                        reward = float(rewards)

                    episode_reward += reward
                    episode_length += 1

                    # Check if episode is done
                    if isinstance(terminated, torch.Tensor):
                        done = bool(terminated[0]) if terminated.numel() > 0 else False
                    else:
                        done = bool(terminated)

                    if isinstance(truncated, torch.Tensor):
                        done = done or bool(truncated[0]) if truncated.numel() > 0 else done
                    else:
                        done = done or bool(truncated)

                    # Check for success in info
                    if infos and isinstance(infos, dict) and "success" in infos and infos["success"]:
                        success_count += 1

            episode_rewards.append(episode_reward)
            episode_lengths.append(episode_length)

            if (episode + 1) % 5 == 0:
                print(f"  Episode {episode + 1}/{self.eval_episodes} - Reward: {episode_reward:.2f}")

        # Calculate metrics
        metrics = {
            "checkpoint_number": checkpoint_number,
            "timestamp": datetime.now().isoformat(),
            "episodes_evaluated": self.eval_episodes,
            "mean_reward": float(np.mean(episode_rewards)),
            "std_reward": float(np.std(episode_rewards)),
            "min_reward": float(np.min(episode_rewards)),
            "max_reward": float(np.max(episode_rewards)),
            "mean_episode_length": float(np.mean(episode_lengths)),
            "std_episode_length": float(np.std(episode_lengths)),
            "success_rate": success_count / self.eval_episodes,
            "episode_rewards": episode_rewards,
            "episode_lengths": episode_lengths,
        }

        return metrics

    def _print_metrics(self, metrics: dict):
        """Print metrics in a readable format."""
        print("\n" + "=" * 60)
        print(f"CHECKPOINT {metrics['checkpoint_number']} EVALUATION RESULTS")
        print("=" * 60)
        print(f"Timestamp: {metrics['timestamp']}")
        print(f"Episodes: {metrics['episodes_evaluated']}")
        print(f"Mean Reward: {metrics['mean_reward']:.3f} ± {metrics['std_reward']:.3f}")
        print(f"Reward Range: [{metrics['min_reward']:.3f}, {metrics['max_reward']:.3f}]")
        print(f"Mean Episode Length: {metrics['mean_episode_length']:.1f} ± {metrics['std_episode_length']:.1f}")
        print(f"Success Rate: {metrics['success_rate']:.1%}")
        print("=" * 60 + "\n")

    def _compare_with_previous(self, current_metrics: dict):
        """Compare current metrics with previous checkpoint."""
        if not self.metrics_history:
            print("[INFO] No previous metrics to compare with.")
            return

        # Find the most recent previous checkpoint
        previous_checkpoint = None
        for checkpoint_num in sorted(self.metrics_history.keys(), key=int, reverse=True):
            if int(checkpoint_num) < current_metrics["checkpoint_number"]:
                previous_checkpoint = self.metrics_history[checkpoint_num]
                break

        if previous_checkpoint is None:
            print("[INFO] No previous checkpoint found for comparison.")
            return

        print("\n" + "-" * 40)
        print("COMPARISON WITH PREVIOUS CHECKPOINT")
        print("-" * 40)

        prev_reward = previous_checkpoint["mean_reward"]
        curr_reward = current_metrics["mean_reward"]
        reward_diff = curr_reward - prev_reward
        reward_percent_change = (reward_diff / abs(prev_reward)) * 100 if prev_reward != 0 else 0

        prev_success = previous_checkpoint["success_rate"]
        curr_success = current_metrics["success_rate"]
        success_diff = curr_success - prev_success

        print(f"Reward Change: {reward_diff:+.3f} ({reward_percent_change:+.1f}%)")
        print(f"Success Rate Change: {success_diff:+.3f} ({success_diff * 100:+.1f} percentage points)")

        # Determine if improvement
        if reward_diff > 0 and success_diff >= 0:
            print("✅ IMPROVEMENT: Better reward and equal/better success rate")
        elif reward_diff > 0:
            print("⚠️  MIXED: Better reward but lower success rate")
        elif success_diff > 0:
            print("⚠️  MIXED: Lower reward but better success rate")
        else:
            print("❌ REGRESSION: Lower reward and equal/worse success rate")
        print("-" * 40 + "\n")

    def monitor(self):
        """Main monitoring loop."""
        print("[INFO] Starting checkpoint monitoring...")
        print(f"[INFO] Monitoring directory: {self.checkpoint_dir}")
        print(f"[INFO] Check interval: {args_cli.monitor_interval} seconds")
        print(f"[INFO] Evaluation episodes: {self.eval_episodes}")
        print(f"[INFO] Metrics file: {self.metrics_file}")

        # Check for existing checkpoints first
        existing_checkpoints = self._get_checkpoint_files()
        for checkpoint_path in existing_checkpoints:
            checkpoint_num = self._extract_checkpoint_number(checkpoint_path)
            self.processed_checkpoints.add(checkpoint_num)

        print(f"[INFO] Found {len(existing_checkpoints)} existing checkpoints: {sorted(self.processed_checkpoints)}")

        try:
            while simulation_app.is_running():
                # Check for new checkpoints
                current_checkpoints = self._get_checkpoint_files()

                for checkpoint_path in current_checkpoints:
                    checkpoint_num = self._extract_checkpoint_number(checkpoint_path)

                    if checkpoint_num not in self.processed_checkpoints:
                        print(f"\n[INFO] New checkpoint detected: model_{checkpoint_num}.pt")

                        try:
                            # Load and evaluate the new policy
                            policy = self._load_policy(checkpoint_path)
                            metrics = self._evaluate_policy(policy, checkpoint_num)

                            # Print results
                            self._print_metrics(metrics)
                            self._compare_with_previous(metrics)

                            # Save metrics
                            self.metrics_history[str(checkpoint_num)] = metrics
                            self._save_metrics_history()

                            # Mark as processed
                            self.processed_checkpoints.add(checkpoint_num)

                        except Exception as e:
                            print(f"[ERROR] Failed to evaluate checkpoint {checkpoint_num}: {e}")
                            continue

                # Wait before next check
                time.sleep(args_cli.monitor_interval)

        except KeyboardInterrupt:
            print("\n[INFO] Monitoring stopped by user.")
        finally:
            self.env.close()


@hydra_task_config(args_cli.task, args_cli.agent)
def main(
    env_cfg: ManagerBasedRLEnvCfg | DirectRLEnvCfg | DirectMARLEnvCfg,
    agent_cfg: RslRlBaseRunnerCfg,
):
    """Main function to set up monitoring."""

    # Validate checkpoint directory
    if not os.path.exists(args_cli.checkpoint_dir):
        print(f"[ERROR] Checkpoint directory does not exist: {args_cli.checkpoint_dir}")
        return

    # Override configurations with CLI arguments
    agent_cfg = cli_args.update_rsl_rl_cfg(agent_cfg, args_cli)
    env_cfg.scene.num_envs = args_cli.num_envs

    # Set environment seed
    env_cfg.seed = agent_cfg.seed
    env_cfg.sim.device = args_cli.device if args_cli.device is not None else env_cfg.sim.device

    # Set log directory
    env_cfg.log_dir = args_cli.checkpoint_dir

    # Create and start monitor
    monitor = CheckpointMonitor(
        checkpoint_dir=args_cli.checkpoint_dir,
        env_cfg=env_cfg,
        agent_cfg=agent_cfg,
        eval_episodes=args_cli.eval_episodes,
        max_episode_length=args_cli.max_episode_length,
        metrics_file=args_cli.metrics_file,
    )

    monitor.monitor()


if __name__ == "__main__":
    main()
    os._exit(0)
