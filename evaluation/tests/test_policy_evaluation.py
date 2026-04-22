"""Unit tests for ``sil.policy_evaluation``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

torch = pytest.importorskip("torch")

from sil import policy_evaluation  # noqa: E402
from sil.policy_evaluation import (  # noqa: E402
    Metrics,
    ModelMetadata,
    _build_parser,
    _load_rsl_rl,
    _load_skrl,
    evaluate,
    find_checkpoint,
    load_agent,
    load_metadata,
    main,
)


class TestModelMetadata:
    def test_defaults(self) -> None:
        meta = ModelMetadata()
        assert meta.task == ""
        assert meta.framework == "skrl"
        assert meta.success_threshold == 0.7

    def test_custom_values(self) -> None:
        meta = ModelMetadata(task="Lift-v0", framework="rsl_rl", success_threshold=0.9)
        assert meta.task == "Lift-v0"
        assert meta.framework == "rsl_rl"
        assert meta.success_threshold == 0.9


class TestLoadMetadata:
    def test_auto_task_becomes_empty(self) -> None:
        meta = load_metadata(task="auto", framework="skrl", success_threshold=0.7)
        assert meta.task == ""

    def test_empty_task_stays_empty(self) -> None:
        meta = load_metadata(task="", framework="skrl", success_threshold=0.7)
        assert meta.task == ""

    def test_explicit_task_preserved(self) -> None:
        meta = load_metadata(task="Reach-v0", framework="rsl_rl", success_threshold=0.5)
        assert meta.task == "Reach-v0"
        assert meta.framework == "rsl_rl"
        assert meta.success_threshold == 0.5

    def test_negative_threshold_uses_default(self) -> None:
        meta = load_metadata(task="Lift-v0", framework="skrl", success_threshold=-1.0)
        assert meta.success_threshold == 0.7

    def test_zero_threshold_kept(self) -> None:
        meta = load_metadata(task="Lift-v0", framework="skrl", success_threshold=0.0)
        assert meta.success_threshold == 0.0

    def test_auto_framework_becomes_default(self) -> None:
        meta = load_metadata(task="Lift-v0", framework="auto", success_threshold=0.5)
        assert meta.framework == "skrl"

    def test_empty_framework_becomes_default(self) -> None:
        meta = load_metadata(task="Lift-v0", framework="", success_threshold=0.5)
        assert meta.framework == "skrl"


class TestMetrics:
    def test_empty_to_dict_returns_error(self) -> None:
        m = Metrics()
        result = m.to_dict()
        assert "error" in result
        assert result["error"] == "No episodes completed"

    def test_count_starts_at_zero(self) -> None:
        m = Metrics()
        assert m.count == 0

    def test_add_increments_count(self) -> None:
        m = Metrics()
        m.add(reward=10.0, length=50, success=True)
        assert m.count == 1
        m.add(reward=20.0, length=60, success=False)
        assert m.count == 2

    def test_to_dict_single_episode(self) -> None:
        m = Metrics()
        m.add(reward=10.0, length=50, success=True)
        result = m.to_dict()
        assert result["eval_episodes"] == 1
        assert result["mean_reward"] == 10.0
        assert result["std_reward"] == 0.0
        assert result["mean_length"] == 50.0
        assert result["success_rate"] == 1.0

    def test_to_dict_multiple_episodes(self) -> None:
        m = Metrics()
        m.add(reward=10.0, length=50, success=True)
        m.add(reward=20.0, length=100, success=False)
        result = m.to_dict()
        assert result["eval_episodes"] == 2
        assert result["mean_reward"] == pytest.approx(15.0)
        assert result["mean_length"] == pytest.approx(75.0)
        assert result["success_rate"] == pytest.approx(0.5)

    def test_rewards_and_lengths_tracked(self) -> None:
        m = Metrics()
        m.add(reward=5.0, length=10, success=False)
        m.add(reward=15.0, length=30, success=True)
        assert m.rewards == [5.0, 15.0]
        assert m.lengths == [10, 30]
        assert m.successes == 1


class TestFindCheckpoint:
    def test_file_with_pt_extension(self, tmp_path: Path) -> None:
        ckpt = tmp_path / "model.pt"
        ckpt.write_bytes(b"fake")
        assert find_checkpoint(str(ckpt)) == str(ckpt)

    def test_file_with_pth_extension(self, tmp_path: Path) -> None:
        ckpt = tmp_path / "model.pth"
        ckpt.write_bytes(b"fake")
        assert find_checkpoint(str(ckpt)) == str(ckpt)

    def test_bad_extension_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "model.onnx"
        bad.write_bytes(b"fake")
        with pytest.raises(FileNotFoundError):
            find_checkpoint(str(bad))

    def test_nonexistent_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            find_checkpoint("/tmp/nonexistent_checkpoint.pt")

    def test_directory_finds_best_agent(self, tmp_path: Path) -> None:
        best = tmp_path / "best_agent.pt"
        best.write_bytes(b"best")
        assert find_checkpoint(str(tmp_path)) == str(best)

    def test_directory_finds_checkpoint_subdir(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / "checkpoints"
        ckpt_dir.mkdir()
        ckpt = ckpt_dir / "step_1000.pt"
        ckpt.write_bytes(b"data")
        assert find_checkpoint(str(tmp_path)) == str(ckpt)

    def test_directory_prefers_best_agent_over_glob(self, tmp_path: Path) -> None:
        best = tmp_path / "best_agent.pt"
        best.write_bytes(b"best")
        other = tmp_path / "other.pt"
        other.write_bytes(b"other")
        assert find_checkpoint(str(tmp_path)) == str(best)

    def test_directory_selects_newest_by_mtime(self, tmp_path: Path) -> None:
        import time

        old = tmp_path / "old.pt"
        old.write_bytes(b"old")
        time.sleep(0.05)
        new = tmp_path / "new.pt"
        new.write_bytes(b"new")
        result = find_checkpoint(str(tmp_path))
        assert result == str(new)

    def test_empty_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            find_checkpoint(str(tmp_path))


class TestBuildParser:
    def test_returns_parser(self) -> None:
        parser = _build_parser()
        assert isinstance(parser, argparse.ArgumentParser)

    def test_model_path_required(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["--model-path", "/tmp/model"])
        assert args.model_path == "/tmp/model"
        assert args.task == ""
        assert args.framework == ""
        assert args.eval_episodes == 100
        assert args.num_envs == 64
        assert args.success_threshold == -1
        assert args.headless is False
        assert args.seed == 42

    def test_all_flags(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "--model-path",
                "/m",
                "--task",
                "Reach-v0",
                "--framework",
                "rsl_rl",
                "--eval-episodes",
                "50",
                "--num-envs",
                "32",
                "--success-threshold",
                "0.8",
                "--headless",
                "--seed",
                "99",
            ]
        )
        assert args.task == "Reach-v0"
        assert args.framework == "rsl_rl"
        assert args.eval_episodes == 50
        assert args.num_envs == 32
        assert args.success_threshold == 0.8
        assert args.headless is True
        assert args.seed == 99


class TestLoadAgent:
    def test_unsupported_framework_raises(self) -> None:
        with pytest.raises(ValueError, match="Unsupported framework"):
            load_agent("/tmp/ckpt.pt", "tensorflow", "Reach-v0", MagicMock(), "cuda")

    def test_dispatches_to_skrl_loader(self) -> None:
        sentinel = object()
        with patch("sil.policy_evaluation._load_skrl", return_value=sentinel) as mock:
            result = load_agent("/tmp/ckpt.pt", "skrl", "Reach-v0", MagicMock(), "cuda")
        assert result is sentinel
        mock.assert_called_once()

    def test_dispatches_to_rsl_rl_loader(self) -> None:
        sentinel = object()
        with patch("sil.policy_evaluation._load_rsl_rl", return_value=sentinel) as mock:
            result = load_agent("/tmp/ckpt.pt", "rsl_rl", "Reach-v0", MagicMock(), "cpu")
        assert result is sentinel
        mock.assert_called_once_with("/tmp/ckpt.pt", "cpu")


class TestLoadRslRl:
    def test_loads_actor_critic_and_returns_eval_policy(self) -> None:
        rsl_rl_modules = MagicMock()
        policy = MagicMock()
        policy.to.return_value = policy
        rsl_rl_modules.ActorCritic.return_value = policy
        checkpoint = {"model_cfg": {"a": 1}, "model_state_dict": {"w": 0}}

        with (
            patch.dict(sys.modules, {"rsl_rl": MagicMock(), "rsl_rl.modules": rsl_rl_modules}),
            patch("sil.policy_evaluation.torch.load", return_value=checkpoint) as mock_load,
        ):
            result = _load_rsl_rl("/tmp/ckpt.pt", "cpu")

        mock_load.assert_called_once_with("/tmp/ckpt.pt", map_location="cpu", weights_only=False)
        rsl_rl_modules.ActorCritic.assert_called_once_with(a=1)
        policy.load_state_dict.assert_called_once_with({"w": 0})
        policy.eval.assert_called_once()
        policy.to.assert_called_once_with("cpu")
        assert result is policy


class _StubEnv:
    """Minimal env stub backed by torch tensors for evaluate() loop."""

    def __init__(self, num_envs: int, episode_len: int, success: bool = True) -> None:
        self.num_envs = num_envs
        self.device = "cpu"
        self._episode_len = episode_len
        self._step_count = 0
        self._success = success

    def reset(self) -> tuple[torch.Tensor, dict]:
        self._step_count = 0
        return torch.zeros(self.num_envs, 4), {}

    def step(self, actions):
        self._step_count += 1
        rewards = torch.ones(self.num_envs, 1)
        done = self._step_count >= self._episode_len
        terminated = torch.full((self.num_envs, 1), done, dtype=torch.bool)
        truncated = torch.zeros(self.num_envs, 1, dtype=torch.bool)
        info = {"success": torch.full((self.num_envs,), self._success, dtype=torch.bool)}
        if done:
            self._step_count = 0
        return torch.zeros(self.num_envs, 4), rewards, terminated, truncated, info


class TestEvaluate:
    def test_skrl_path_collects_metrics(self) -> None:
        env = _StubEnv(num_envs=2, episode_len=3, success=True)
        agent = MagicMock()
        agent.act.return_value = (torch.zeros(2, 1),)

        metrics = evaluate(env, agent, num_episodes=2, framework="skrl")

        assert metrics.count == 2
        assert metrics.successes == 2
        assert all(r == pytest.approx(3.0) for r in metrics.rewards)
        assert metrics.lengths == [3, 3]
        agent.act.assert_called()

    def test_rsl_rl_path_uses_act_inference(self) -> None:
        env = _StubEnv(num_envs=2, episode_len=2, success=False)
        agent = MagicMock()
        agent.act_inference.return_value = torch.zeros(2, 1)

        metrics = evaluate(env, agent, num_episodes=2, framework="rsl_rl")

        assert metrics.count == 2
        assert metrics.successes == 0
        agent.act_inference.assert_called()
        agent.act.assert_not_called()

    def test_progress_logging_at_multiples_of_twenty(self) -> None:
        env = _StubEnv(num_envs=20, episode_len=1, success=True)
        agent = MagicMock()
        agent.act.return_value = (torch.zeros(20, 1),)

        metrics = evaluate(env, agent, num_episodes=20, framework="skrl")
        assert metrics.count == 20
        assert metrics.successes == 20

    def test_break_when_count_reaches_num_episodes_mid_step(self) -> None:
        # num_envs (3) > num_episodes (2) so the third done index in a single
        # step trips the early-exit guard inside the done_indices loop.
        env = _StubEnv(num_envs=3, episode_len=1, success=True)
        agent = MagicMock()
        agent.act.return_value = (torch.zeros(3, 1),)
        metrics = evaluate(env, agent, num_episodes=2, framework="skrl")
        assert metrics.count == 2

    def test_truncated_episode_not_counted_as_success(self) -> None:
        class TruncEnv(_StubEnv):
            def step(self, actions):
                self._step_count += 1
                rewards = torch.ones(self.num_envs, 1)
                terminated = torch.zeros(self.num_envs, 1, dtype=torch.bool)
                truncated = torch.ones(self.num_envs, 1, dtype=torch.bool)
                info = {"success": torch.ones(self.num_envs, dtype=torch.bool)}
                return torch.zeros(self.num_envs, 4), rewards, terminated, truncated, info

        env = TruncEnv(num_envs=2, episode_len=1)
        agent = MagicMock()
        agent.act.return_value = (torch.zeros(2, 1),)

        metrics = evaluate(env, agent, num_episodes=2, framework="skrl")
        assert metrics.count == 2
        assert metrics.successes == 0


def _skrl_module_stubs(decorator_calls_inner: bool = True, cfg: object | None = None):
    """Build sys.modules patch dict for _load_skrl tests."""
    hydra_mod = MagicMock()
    if decorator_calls_inner:
        hydra_mod.hydra_task_config = lambda task, entry: lambda fn: lambda: fn(None, cfg)
    else:
        hydra_mod.hydra_task_config = lambda task, entry: lambda fn: lambda: None
    runner_mod = MagicMock()
    runner_mod.Runner = MagicMock()
    return {
        "isaaclab_tasks": MagicMock(),
        "isaaclab_tasks.utils": MagicMock(),
        "isaaclab_tasks.utils.hydra": hydra_mod,
        "skrl": MagicMock(),
        "skrl.utils": MagicMock(),
        "skrl.utils.runner": MagicMock(),
        "skrl.utils.runner.torch": runner_mod,
    }, runner_mod


class TestLoadSkrl:
    def test_to_dict_cfg_creates_runner_and_loads_checkpoint(self) -> None:
        cfg = MagicMock()
        cfg.to_dict.return_value = {"a": 1}
        stubs, runner_mod = _skrl_module_stubs(cfg=cfg)
        env = MagicMock()

        with patch.dict(sys.modules, stubs):
            agent = _load_skrl("/tmp/ckpt.pt", "Reach-v0", env, "cuda")

        runner_mod.Runner.assert_called_once_with(env, {"a": 1})
        runner_instance = runner_mod.Runner.return_value
        runner_instance.agent.load.assert_called_once_with("/tmp/ckpt.pt")
        runner_instance.agent.enable_training_mode.assert_called_once_with(enabled=False, apply_to_models=True)
        assert agent is runner_instance.agent

    def test_dict_cfg_used_directly(self) -> None:
        cfg = {"b": 2}
        stubs, runner_mod = _skrl_module_stubs(cfg=cfg)

        with patch.dict(sys.modules, stubs):
            _load_skrl("/tmp/ckpt.pt", "Reach-v0", MagicMock(), "cuda")

        runner_mod.Runner.assert_called_once()
        assert runner_mod.Runner.call_args.args[1] == {"b": 2}

    def test_unsupported_cfg_type_raises(self) -> None:
        cfg = object()  # no to_dict, not a dict
        stubs, _ = _skrl_module_stubs(cfg=cfg)

        with patch.dict(sys.modules, stubs), pytest.raises(ValueError, match="Unexpected agent config type"):
            _load_skrl("/tmp/ckpt.pt", "Reach-v0", MagicMock(), "cuda")

    def test_missing_cfg_raises(self) -> None:
        stubs, _ = _skrl_module_stubs(decorator_calls_inner=False)

        with patch.dict(sys.modules, stubs), pytest.raises(ValueError, match="Could not load agent configuration"):
            _load_skrl("/tmp/ckpt.pt", "Reach-v0", MagicMock(), "cuda")

    def test_restores_sys_argv_after_call(self) -> None:
        cfg = {"a": 1}
        stubs, _ = _skrl_module_stubs(cfg=cfg)
        sentinel_argv = ["prog", "--keep", "me"]

        with patch.dict(sys.modules, stubs), patch.object(sys, "argv", sentinel_argv):
            _load_skrl("/tmp/ckpt.pt", "Reach-v0", MagicMock(), "cuda")
            assert sys.argv == sentinel_argv


def _main_module_stubs():
    """Build sys.modules patch dict for main() tests."""
    isaaclab_app = MagicMock()
    gym_mod = MagicMock()
    parse_cfg_mod = MagicMock()
    skrl_rl_mod = MagicMock()
    return {
        "isaaclab": MagicMock(),
        "isaaclab.app": isaaclab_app,
        "isaaclab_tasks": MagicMock(),
        "isaaclab_tasks.utils": MagicMock(),
        "isaaclab_tasks.utils.parse_cfg": parse_cfg_mod,
        "isaaclab_rl": MagicMock(),
        "isaaclab_rl.skrl": skrl_rl_mod,
        "gymnasium": gym_mod,
    }


class TestMain:
    def test_missing_task_returns_one(self) -> None:
        argv = ["prog", "--model-path", "/tmp/m"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(policy_evaluation.os, "_exit") as mock_exit,
        ):
            rc = main()
        assert rc == 1
        # Early return path: os._exit is only invoked from the try/finally
        # that wraps successful evaluation, not from the missing-task guard.
        mock_exit.assert_not_called()

    def test_success_path_returns_zero(self) -> None:
        argv = ["prog", "--model-path", "/tmp/m", "--task", "Lift-v0", "--success-threshold", "0.5"]
        metrics = MagicMock()
        metrics.to_dict.return_value = {"success_rate": 0.9}
        with (
            patch.object(sys, "argv", argv),
            patch.dict(sys.modules, _main_module_stubs()),
            patch("sil.policy_evaluation.find_checkpoint", return_value="/tmp/ckpt.pt"),
            patch("sil.policy_evaluation.load_agent", return_value=MagicMock()),
            patch("sil.policy_evaluation.evaluate", return_value=metrics),
            patch("sil.policy_evaluation.prepare_for_shutdown"),
            patch.object(policy_evaluation.os, "_exit") as mock_exit,
        ):
            rc = main()
        assert rc == 0
        mock_exit.assert_called_once_with(0)

    def test_below_threshold_returns_one(self) -> None:
        argv = ["prog", "--model-path", "/tmp/m", "--task", "Lift-v0", "--success-threshold", "0.9"]
        metrics = MagicMock()
        metrics.to_dict.return_value = {"success_rate": 0.1}
        with (
            patch.object(sys, "argv", argv),
            patch.dict(sys.modules, _main_module_stubs()),
            patch("sil.policy_evaluation.find_checkpoint", return_value="/tmp/ckpt.pt"),
            patch("sil.policy_evaluation.load_agent", return_value=MagicMock()),
            patch("sil.policy_evaluation.evaluate", return_value=metrics),
            patch("sil.policy_evaluation.prepare_for_shutdown"),
            patch.object(policy_evaluation.os, "_exit") as mock_exit,
        ):
            rc = main()
        assert rc == 1
        mock_exit.assert_called_once_with(1)

    def test_rsl_rl_framework_skips_skrl_wrapper(self) -> None:
        argv = [
            "prog",
            "--model-path",
            "/tmp/m",
            "--task",
            "Lift-v0",
            "--framework",
            "rsl_rl",
            "--success-threshold",
            "0.0",
        ]
        metrics = MagicMock()
        metrics.to_dict.return_value = {"success_rate": 1.0}
        stubs = _main_module_stubs()
        with (
            patch.object(sys, "argv", argv),
            patch.dict(sys.modules, stubs),
            patch("sil.policy_evaluation.find_checkpoint", return_value="/tmp/ckpt.pt"),
            patch("sil.policy_evaluation.load_agent", return_value=MagicMock()),
            patch("sil.policy_evaluation.evaluate", return_value=metrics),
            patch("sil.policy_evaluation.prepare_for_shutdown"),
            patch.object(policy_evaluation.os, "_exit"),
        ):
            rc = main()
        assert rc == 0
        stubs["isaaclab_rl.skrl"].SkrlVecEnvWrapper.assert_not_called()

    def test_exception_in_try_returns_one(self) -> None:
        argv = ["prog", "--model-path", "/tmp/m", "--task", "Lift-v0"]
        with (
            patch.object(sys, "argv", argv),
            patch.dict(sys.modules, _main_module_stubs()),
            patch("sil.policy_evaluation.find_checkpoint", side_effect=RuntimeError("boom")),
            patch.object(policy_evaluation.os, "_exit") as mock_exit,
        ):
            rc = main()
        assert rc == 1
        mock_exit.assert_called_once_with(1)
