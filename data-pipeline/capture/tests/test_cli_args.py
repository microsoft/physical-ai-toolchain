"""Example-based tests for RSL-RL CLI argument parsing utilities."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from common.cli_args import add_rsl_rl_args, update_rsl_rl_cfg


class TestAddRslRlArgs:
    """Tests for add_rsl_rl_args argument registration."""

    def test_adds_expected_arguments(self) -> None:
        """All RSL-RL arguments are present after registration."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        args = parser.parse_args([])
        for name in ("experiment_name", "run_name", "resume", "load_run", "checkpoint", "logger", "log_project_name"):
            assert hasattr(args, name), f"Missing argument: {name}"

    def test_default_values(self) -> None:
        """Default values match expected RSL-RL conventions."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        args = parser.parse_args([])
        assert args.experiment_name is None
        assert args.run_name is None
        assert args.resume is False
        assert args.load_run is None
        assert args.checkpoint is None
        assert args.logger is None
        assert args.log_project_name is None

    def test_parse_string_arguments(self) -> None:
        """String arguments are parsed from command-line tokens."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        args = parser.parse_args([
            "--experiment_name", "my-exp",
            "--run_name", "run-42",
            "--load_run", "2026-03-14_12-00-00",
            "--checkpoint", "model_5000.pt",
            "--log_project_name", "my-project",
        ])
        assert args.experiment_name == "my-exp"
        assert args.run_name == "run-42"
        assert args.load_run == "2026-03-14_12-00-00"
        assert args.checkpoint == "model_5000.pt"
        assert args.log_project_name == "my-project"

    def test_resume_flag(self) -> None:
        """--resume is a store_true action."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        args = parser.parse_args(["--resume"])
        assert args.resume is True

    @pytest.mark.parametrize("logger", ["wandb", "tensorboard", "neptune"])
    def test_logger_valid_choices(self, logger: str) -> None:
        """Logger argument accepts each valid choice."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        args = parser.parse_args(["--logger", logger])
        assert args.logger == logger

    def test_logger_rejects_invalid_choice(self) -> None:
        """Logger argument rejects values outside the allowed set."""
        parser = argparse.ArgumentParser()
        add_rsl_rl_args(parser)
        with pytest.raises(SystemExit):
            parser.parse_args(["--logger", "invalid"])


class TestUpdateRslRlCfg:
    """Tests for update_rsl_rl_cfg config overrides."""

    @staticmethod
    def _make_cfg(**overrides) -> SimpleNamespace:
        defaults = {
            "seed": 42,
            "resume": False,
            "load_run": None,
            "load_checkpoint": None,
            "run_name": None,
            "logger": "tensorboard",
            "wandb_project": None,
            "neptune_project": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @staticmethod
    def _make_args(**overrides) -> SimpleNamespace:
        defaults = {
            "seed": None,
            "resume": None,
            "load_run": None,
            "checkpoint": None,
            "run_name": None,
            "logger": None,
            "log_project_name": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_none_args_leave_config_unchanged(self) -> None:
        """When all CLI args are None, config values are preserved."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args())
        assert result.seed == 42
        assert result.resume is False
        assert result.load_run is None
        assert result.load_checkpoint is None

    def test_returns_same_config_object(self) -> None:
        """Config is mutated in-place and returned."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(resume=True))
        assert result is cfg

    def test_override_resume(self) -> None:
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(resume=True))
        assert result.resume is True

    def test_override_load_run(self) -> None:
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(load_run="2026-03-14_12-00-00"))
        assert result.load_run == "2026-03-14_12-00-00"

    def test_override_checkpoint_maps_to_load_checkpoint(self) -> None:
        """CLI --checkpoint maps to config load_checkpoint field."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(checkpoint="model_5000.pt"))
        assert result.load_checkpoint == "model_5000.pt"

    def test_override_run_name(self) -> None:
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(run_name="custom-run"))
        assert result.run_name == "custom-run"

    def test_override_seed(self) -> None:
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(seed=123))
        assert result.seed == 123

    def test_seed_minus_one_generates_random(self) -> None:
        """seed=-1 produces a random seed in [0, 10000]."""
        cfg = self._make_cfg()
        args = self._make_args(seed=-1)
        result = update_rsl_rl_cfg(cfg, args)
        assert 0 <= result.seed <= 10000
        assert args.seed == result.seed

    def test_wandb_logger_sets_project_names(self) -> None:
        """wandb logger with log_project_name sets both project fields."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(logger="wandb", log_project_name="my-project"))
        assert result.logger == "wandb"
        assert result.wandb_project == "my-project"
        assert result.neptune_project == "my-project"

    def test_neptune_logger_sets_project_names(self) -> None:
        """neptune logger with log_project_name sets both project fields."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(logger="neptune", log_project_name="my-project"))
        assert result.logger == "neptune"
        assert result.neptune_project == "my-project"
        assert result.wandb_project == "my-project"

    def test_tensorboard_logger_ignores_project(self) -> None:
        """Tensorboard logger does not set wandb/neptune project names."""
        cfg = self._make_cfg()
        result = update_rsl_rl_cfg(cfg, self._make_args(logger="tensorboard", log_project_name="my-project"))
        assert result.logger == "tensorboard"
        assert result.wandb_project is None
        assert result.neptune_project is None
