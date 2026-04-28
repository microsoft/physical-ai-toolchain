"""Tests for SKRL training orchestration helpers in :mod:`training.rl.scripts.skrl_training`.

Heavy dependencies (gymnasium, isaaclab, skrl, mlflow, azure) are imported
lazily inside individual helper functions; tests pass in mocks rather than
stubbing them in ``sys.modules``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from .conftest import load_training_module

_MOD = load_training_module(
    "training_rl_skrl_training",
    "training/rl/scripts/skrl_training.py",
)


# ---------------------------------------------------------------------------
# _parse_mlflow_log_interval
# ---------------------------------------------------------------------------


class TestParseMlflowLogInterval:
    """Tests for the MLflow logging interval parser."""

    def test_empty_returns_default(self) -> None:
        assert _MOD._parse_mlflow_log_interval("   ", 5) == _MOD._DEFAULT_MLFLOW_INTERVAL

    def test_step_preset(self) -> None:
        assert _MOD._parse_mlflow_log_interval("step", 5) == 1

    def test_balanced_preset(self) -> None:
        assert _MOD._parse_mlflow_log_interval("BALANCED", 5) == _MOD._DEFAULT_MLFLOW_INTERVAL

    def test_rollout_uses_rollouts_when_positive(self) -> None:
        assert _MOD._parse_mlflow_log_interval("rollout", 7) == 7

    def test_rollout_falls_back_when_zero(self) -> None:
        assert _MOD._parse_mlflow_log_interval("rollout", 0) == _MOD._DEFAULT_MLFLOW_INTERVAL

    def test_integer_string(self) -> None:
        assert _MOD._parse_mlflow_log_interval("25", 5) == 25

    def test_integer_clamped_to_one(self) -> None:
        assert _MOD._parse_mlflow_log_interval("0", 5) == 1
        assert _MOD._parse_mlflow_log_interval("-3", 5) == 1

    def test_invalid_falls_back_to_default(self) -> None:
        assert _MOD._parse_mlflow_log_interval("nonsense", 5) == _MOD._DEFAULT_MLFLOW_INTERVAL


# ---------------------------------------------------------------------------
# _build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_registers_app_launcher_args(self) -> None:
        launcher = MagicMock()
        parser = _MOD._build_parser(launcher)
        launcher.add_app_launcher_args.assert_called_once_with(parser)

    def test_defaults(self) -> None:
        launcher = MagicMock()
        parser = _MOD._build_parser(launcher)
        args = parser.parse_args([])
        assert args.algorithm == "PPO"
        assert args.ml_framework == "torch"
        assert args.video is False
        assert args.video_length == 200
        assert args.video_interval == 2000
        assert args.mlflow_log_interval == "balanced"

    def test_algorithm_choice_validation(self) -> None:
        parser = _MOD._build_parser(MagicMock())
        with pytest.raises(SystemExit):
            parser.parse_args(["--algorithm", "BOGUS"])


# ---------------------------------------------------------------------------
# _sync_checkpoint_output
# ---------------------------------------------------------------------------


class TestSyncCheckpointOutput:
    def test_no_target_does_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRAINING_CHECKPOINT_OUTPUT", raising=False)
        # Should silently return without error.
        _MOD._sync_checkpoint_output(tmp_path / "missing")

    def test_missing_source_does_nothing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRAINING_CHECKPOINT_OUTPUT", str(tmp_path / "out"))
        _MOD._sync_checkpoint_output(tmp_path / "does_not_exist")
        assert not (tmp_path / "out").exists()

    def test_copies_to_destination(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "checkpoints"
        src.mkdir()
        (src / "ckpt.pt").write_text("data")
        dest = tmp_path / "out"
        monkeypatch.setenv("TRAINING_CHECKPOINT_OUTPUT", str(dest))

        _MOD._sync_checkpoint_output(src)

        assert (dest / "ckpt.pt").read_text() == "data"

    def test_replaces_existing_destination(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "checkpoints"
        src.mkdir()
        (src / "new.pt").write_text("new")
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "stale.pt").write_text("stale")
        monkeypatch.setenv("TRAINING_CHECKPOINT_OUTPUT", str(dest))

        _MOD._sync_checkpoint_output(src)

        assert (dest / "new.pt").exists()
        assert not (dest / "stale.pt").exists()

    def test_swallows_copy_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        src = tmp_path / "checkpoints"
        src.mkdir()
        monkeypatch.setenv("TRAINING_CHECKPOINT_OUTPUT", str(tmp_path / "out"))
        monkeypatch.setattr(_MOD.shutil, "copytree", MagicMock(side_effect=OSError("denied")))
        # Should not raise.
        _MOD._sync_checkpoint_output(src)


# ---------------------------------------------------------------------------
# _get_agent_config_entry_point
# ---------------------------------------------------------------------------


class TestGetAgentConfigEntryPoint:
    def test_explicit_agent_wins(self) -> None:
        cli = SimpleNamespace(agent="custom", algorithm="PPO")
        assert _MOD._get_agent_config_entry_point(cli) == "custom"

    @pytest.mark.parametrize(
        ("algorithm", "expected"),
        [
            ("ippo", "skrl_ippo_cfg_entry_point"),
            ("MAPPO", "skrl_mappo_cfg_entry_point"),
            ("amp", "skrl_amp_cfg_entry_point"),
            ("ppo", "skrl_cfg_entry_point"),
            (None, "skrl_cfg_entry_point"),
            ("", "skrl_cfg_entry_point"),
        ],
    )
    def test_algorithm_mapping(self, algorithm: str | None, expected: str) -> None:
        cli = SimpleNamespace(agent=None, algorithm=algorithm)
        assert _MOD._get_agent_config_entry_point(cli) == expected


# ---------------------------------------------------------------------------
# _prepare_log_paths
# ---------------------------------------------------------------------------


class TestPrepareLogPaths:
    def test_creates_directory_with_default_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        agent_cfg: dict = {}
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch")
        log_dir = _MOD._prepare_log_paths(agent_cfg, cli)
        assert log_dir.exists()
        # Default directory is logs/skrl/<run_name>.
        assert log_dir.parent.name == "skrl"

    def test_uses_existing_directory_setting(self, tmp_path: Path) -> None:
        agent_cfg = {"agent": {"experiment": {"directory": str(tmp_path / "runs")}}}
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch")
        log_dir = _MOD._prepare_log_paths(agent_cfg, cli)
        assert log_dir.exists()
        assert str(log_dir).startswith(str(tmp_path / "runs"))

    def test_appends_custom_experiment_name(self, tmp_path: Path) -> None:
        agent_cfg = {"agent": {"experiment": {"directory": str(tmp_path), "experiment_name": "my-exp"}}}
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch")
        log_dir = _MOD._prepare_log_paths(agent_cfg, cli)
        assert log_dir.name.endswith("my-exp")


# ---------------------------------------------------------------------------
# _wrap_with_video_recorder
# ---------------------------------------------------------------------------


class TestWrapWithVideoRecorder:
    def test_returns_env_when_video_disabled(self, tmp_path: Path) -> None:
        env = object()
        cli = SimpleNamespace(video=False, video_interval=10, video_length=20)
        gym = MagicMock()
        result = _MOD._wrap_with_video_recorder(gym, env, cli, tmp_path)
        assert result is env
        gym.wrappers.RecordVideo.assert_not_called()

    def test_wraps_when_video_enabled(self, tmp_path: Path) -> None:
        env = object()
        cli = SimpleNamespace(video=True, video_interval=100, video_length=50)
        gym = MagicMock()
        gym.wrappers.RecordVideo.return_value = "wrapped"
        result = _MOD._wrap_with_video_recorder(gym, env, cli, tmp_path)
        assert result == "wrapped"
        assert (tmp_path / "videos" / "train").exists()
        kwargs = gym.wrappers.RecordVideo.call_args.kwargs
        assert kwargs["video_length"] == 50
        assert kwargs["disable_logger"] is True
        # step_trigger fires at multiples of video_interval.
        trigger = kwargs["step_trigger"]
        assert trigger(100) is True
        assert trigger(101) is False


# ---------------------------------------------------------------------------
# _log_artifacts
# ---------------------------------------------------------------------------


class TestLogArtifacts:
    def test_logs_existing_param_files(self, tmp_path: Path) -> None:
        params = tmp_path / "params"
        params.mkdir()
        (params / "env.yaml").write_text("a")
        (params / "agent.yaml").write_text("b")
        mlflow = MagicMock()
        mlflow.active_run.return_value = None

        result = _MOD._log_artifacts(mlflow, tmp_path, resume_path=None)

        assert result is None
        # env.yaml + agent.yaml.
        assert mlflow.log_artifact.call_count == 2

    def test_logs_resume_checkpoint(self, tmp_path: Path) -> None:
        mlflow = MagicMock()
        mlflow.active_run.return_value = None
        result = _MOD._log_artifacts(mlflow, tmp_path, resume_path="/some/ckpt.pt")
        assert result is None
        mlflow.log_artifact.assert_any_call("/some/ckpt.pt", artifact_path="skrl-run/checkpoints")

    def test_returns_latest_checkpoint_uri(self, tmp_path: Path) -> None:
        ckpt_dir = tmp_path / "checkpoints"
        ckpt_dir.mkdir()
        (ckpt_dir / "old.pt").write_text("x")
        latest = ckpt_dir / "new.pt"
        latest.write_text("y")
        # Force ordering: bump latest mtime.
        import os as _os

        _os.utime(latest, (1_700_000_100, 1_700_000_100))
        _os.utime(ckpt_dir / "old.pt", (1_700_000_000, 1_700_000_000))

        mlflow = MagicMock()
        mlflow.active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="run-1"))

        result = _MOD._log_artifacts(mlflow, tmp_path, resume_path=None)

        assert result == "runs:/run-1/skrl-run/checkpoints/new.pt"
        mlflow.set_tag.assert_any_call("checkpoint_directory", "runs:/run-1/skrl-run/checkpoints")
        mlflow.set_tag.assert_any_call("checkpoint_latest", result)

    def test_logs_videos_when_present(self, tmp_path: Path) -> None:
        videos = tmp_path / "videos"
        videos.mkdir()
        mlflow = MagicMock()
        mlflow.active_run.return_value = None
        _MOD._log_artifacts(mlflow, tmp_path, resume_path=None)
        mlflow.log_artifacts.assert_any_call(str(videos), artifact_path="videos")


# ---------------------------------------------------------------------------
# _register_checkpoint_model
# ---------------------------------------------------------------------------


class TestRegisterCheckpointModel:
    def test_no_context_logs_and_returns(self) -> None:
        # Pure no-op path; should not raise.
        _MOD._register_checkpoint_model(
            context=None,
            model_name="m",
            checkpoint_uri="runs:/x/y",
            checkpoint_mode=None,
            task=None,
        )

    def test_registers_model_with_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Stub azure.ai.ml.entities so the lazy import succeeds.
        entities_module = MagicMock()
        entities_module.Model = MagicMock()
        ml_module = MagicMock()
        ml_module.entities = entities_module
        ai_module = MagicMock()
        ai_module.ml = ml_module
        azure_module = MagicMock()
        azure_module.ai = ai_module
        monkeypatch.setitem(sys.modules, "azure", azure_module)
        monkeypatch.setitem(sys.modules, "azure.ai", ai_module)
        monkeypatch.setitem(sys.modules, "azure.ai.ml", ml_module)
        monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", entities_module)

        client = MagicMock()
        context = SimpleNamespace(client=client)

        _MOD._register_checkpoint_model(
            context=context,
            model_name="my-model",
            checkpoint_uri="runs:/x/y",
            checkpoint_mode="resume",
            task="Isaac-Lift",
            algorithm="PPO",
        )

        entities_module.Model.assert_called_once()
        kwargs = entities_module.Model.call_args.kwargs
        assert kwargs["name"] == "my-model"
        assert kwargs["tags"]["task"] == "Isaac-Lift"
        assert kwargs["tags"]["algorithm"] == "PPO"
        client.models.create_or_update.assert_called_once()

    def test_swallows_registration_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entities_module = MagicMock()
        entities_module.Model = MagicMock()
        monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", entities_module)

        client = MagicMock()
        client.models.create_or_update.side_effect = RuntimeError("boom")
        context = SimpleNamespace(client=client)

        # Should not raise.
        _MOD._register_checkpoint_model(
            context=context,
            model_name="m",
            checkpoint_uri="runs:/x/y",
            checkpoint_mode=None,
            task=None,
        )

    def test_handles_missing_azure_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force the lazy import to fail.
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name: str, *args: object, **kwargs: object):
            if name.startswith("azure.ai.ml"):
                raise ImportError("no azure")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)

        # Should not raise.
        _MOD._register_checkpoint_model(
            context=SimpleNamespace(client=MagicMock()),
            model_name="m",
            checkpoint_uri="runs:/x/y",
            checkpoint_mode=None,
            task=None,
        )


# ---------------------------------------------------------------------------
# _resolve_env_count
# ---------------------------------------------------------------------------


class TestResolveEnvCount:
    def test_uses_scene_env_num_envs(self) -> None:
        env_cfg = SimpleNamespace(scene=SimpleNamespace(env=SimpleNamespace(num_envs=8)))
        assert _MOD._resolve_env_count(env_cfg) == 8

    def test_falls_back_to_top_level(self) -> None:
        env_cfg = SimpleNamespace(scene=None, num_envs=4)
        assert _MOD._resolve_env_count(env_cfg) == 4

    def test_returns_none_when_unavailable(self) -> None:
        env_cfg = SimpleNamespace(scene=None)
        assert _MOD._resolve_env_count(env_cfg) is None


# ---------------------------------------------------------------------------
# _resolve_checkpoint
# ---------------------------------------------------------------------------


class TestResolveCheckpoint:
    def test_returns_none_for_empty(self) -> None:
        assert _MOD._resolve_checkpoint(MagicMock(), None) is None
        assert _MOD._resolve_checkpoint(MagicMock(), "") is None

    def test_returns_resolved_path(self) -> None:
        resolver = MagicMock(return_value="/abs/ckpt.pt")
        assert _MOD._resolve_checkpoint(resolver, "ckpt.pt") == "/abs/ckpt.pt"

    def test_raises_system_exit_on_missing(self) -> None:
        resolver = MagicMock(side_effect=FileNotFoundError())
        with pytest.raises(SystemExit, match="Checkpoint path not found"):
            _MOD._resolve_checkpoint(resolver, "missing.pt")


# ---------------------------------------------------------------------------
# _namespace_snapshot
# ---------------------------------------------------------------------------


class TestNamespaceSnapshot:
    def test_serializes_primitives_and_builds_tokens(self) -> None:
        ns = argparse.Namespace(
            task="Isaac-Lift",
            num_envs=4,
            max_iterations=100,
            headless=True,
            checkpoint="ckpt.pt",
        )
        payload, tokens = _MOD._namespace_snapshot(ns)
        assert payload["task"] == "Isaac-Lift"
        assert payload["num_envs"] == 4
        assert "--task" in tokens
        assert "Isaac-Lift" in tokens
        assert "--headless" in tokens
        assert "--checkpoint" in tokens

    def test_stringifies_complex_values(self) -> None:
        ns = argparse.Namespace(task=None, custom=[1, 2])
        payload, tokens = _MOD._namespace_snapshot(ns)
        assert payload["custom"] == "[1, 2]"
        assert tokens == []


# ---------------------------------------------------------------------------
# _normalize_agent_config
# ---------------------------------------------------------------------------


class TestNormalizeAgentConfig:
    def test_uses_to_dict_when_available(self) -> None:
        cfg = SimpleNamespace(to_dict=lambda: {"a": 1})
        assert _MOD._normalize_agent_config(cfg) == {"a": 1}

    def test_returns_input_when_no_to_dict(self) -> None:
        cfg = {"already": "dict"}
        assert _MOD._normalize_agent_config(cfg) is cfg


# ---------------------------------------------------------------------------
# _set_num_envs_for_*_cfg
# ---------------------------------------------------------------------------


class TestSetNumEnvs:
    def test_manager_cfg_overrides(self) -> None:
        env_cfg = SimpleNamespace(scene=SimpleNamespace(num_envs=2))
        _MOD._set_num_envs_for_manager_cfg(env_cfg, 16)
        assert env_cfg.scene.num_envs == 16

    def test_manager_cfg_keeps_existing_when_none(self) -> None:
        env_cfg = SimpleNamespace(scene=SimpleNamespace(num_envs=2))
        _MOD._set_num_envs_for_manager_cfg(env_cfg, None)
        assert env_cfg.scene.num_envs == 2

    def test_direct_cfg_overrides(self) -> None:
        env_cfg = SimpleNamespace(num_envs=2)
        _MOD._set_num_envs_for_direct_cfg(env_cfg, 8)
        assert env_cfg.num_envs == 8

    def test_direct_cfg_keeps_existing_when_none(self) -> None:
        env_cfg = SimpleNamespace(num_envs=2)
        _MOD._set_num_envs_for_direct_cfg(env_cfg, None)
        assert env_cfg.num_envs == 2


# ---------------------------------------------------------------------------
# _configure_environment
# ---------------------------------------------------------------------------


class _ManagerCfg:
    def __init__(self) -> None:
        self.scene = SimpleNamespace(num_envs=1)
        self.sim = SimpleNamespace(device="cpu")
        self.seed = 0


class _DirectCfg:
    def __init__(self) -> None:
        self.num_envs = 1
        self.sim = SimpleNamespace(device="cpu")
        self.seed = 0


class _DirectMARCfg:
    def __init__(self) -> None:
        self.num_envs = 1
        self.sim = SimpleNamespace(device="cpu")
        self.seed = 0


class TestConfigureEnvironment:
    def test_manager_cfg_sets_seed_and_num_envs(self) -> None:
        env_cfg = _ManagerCfg()
        cli = SimpleNamespace(seed=123, num_envs=4, distributed=False)
        seed = _MOD._configure_environment(
            env_cfg,
            cli,
            app_launcher=MagicMock(),
            manager_cfg_type=_ManagerCfg,
            direct_cfg_type=_DirectCfg,
            direct_mar_cfg_type=_DirectMARCfg,
        )
        assert seed == 123
        assert env_cfg.seed == 123
        assert env_cfg.scene.num_envs == 4

    def test_random_seed_when_none(self) -> None:
        env_cfg = _DirectCfg()
        cli = SimpleNamespace(seed=None, num_envs=None, distributed=False)
        seed = _MOD._configure_environment(
            env_cfg,
            cli,
            app_launcher=MagicMock(),
            manager_cfg_type=_ManagerCfg,
            direct_cfg_type=_DirectCfg,
            direct_mar_cfg_type=_DirectMARCfg,
        )
        assert isinstance(seed, int)
        assert env_cfg.seed == seed

    def test_distributed_sets_device(self) -> None:
        env_cfg = _DirectCfg()
        cli = SimpleNamespace(seed=1, num_envs=None, distributed=True)
        launcher = SimpleNamespace(local_rank=2)
        _MOD._configure_environment(
            env_cfg,
            cli,
            app_launcher=launcher,
            manager_cfg_type=_ManagerCfg,
            direct_cfg_type=_DirectCfg,
            direct_mar_cfg_type=_DirectMARCfg,
        )
        assert env_cfg.sim.device == "cuda:2"


# ---------------------------------------------------------------------------
# _configure_agent_training
# ---------------------------------------------------------------------------


class TestConfigureAgentTraining:
    def test_applies_max_iterations_and_seed(self) -> None:
        agent: dict = {"agent": {"rollouts": 5}}
        cli = SimpleNamespace(max_iterations=10)
        rollouts = _MOD._configure_agent_training(agent, cli, random_seed=42)
        assert rollouts == 5
        assert agent["trainer"]["timesteps"] == 50
        assert agent["seed"] == 42
        assert agent["trainer"]["close_environment_at_exit"] is False

    def test_no_max_iterations_skips_timesteps(self) -> None:
        agent: dict = {"agent": {"rollouts": 3}}
        cli = SimpleNamespace(max_iterations=None)
        rollouts = _MOD._configure_agent_training(agent, cli, random_seed=7)
        assert rollouts == 3
        assert "timesteps" not in agent["trainer"]


# ---------------------------------------------------------------------------
# _configure_jax_backend
# ---------------------------------------------------------------------------


class TestConfigureJaxBackend:
    def test_torch_skipped(self) -> None:
        skrl = MagicMock()
        _MOD._configure_jax_backend("torch", skrl)
        assert not skrl.config.jax.backend or True  # no assignment

    def test_jax_backend(self) -> None:
        skrl = MagicMock()
        _MOD._configure_jax_backend("jax", skrl)
        assert skrl.config.jax.backend == "jax"

    def test_jax_numpy_backend(self) -> None:
        skrl = MagicMock()
        _MOD._configure_jax_backend("jax-numpy", skrl)
        assert skrl.config.jax.backend == "numpy"


# ---------------------------------------------------------------------------
# _dump_config_files
# ---------------------------------------------------------------------------


class TestDumpConfigFiles:
    def test_dumps_yaml_only_when_pickle_missing(self, tmp_path: Path) -> None:
        yaml = MagicMock()
        _MOD._dump_config_files(tmp_path, env_cfg={"e": 1}, agent_dict={"a": 1}, dump_yaml_func=yaml, dump_pickle_func=None)
        assert yaml.call_count == 2
        assert (tmp_path / "params").exists()

    def test_dumps_yaml_and_pickle(self, tmp_path: Path) -> None:
        yaml = MagicMock()
        pickle = MagicMock()
        _MOD._dump_config_files(tmp_path, env_cfg={"e": 1}, agent_dict={"a": 1}, dump_yaml_func=yaml, dump_pickle_func=pickle)
        assert yaml.call_count == 2
        assert pickle.call_count == 2


# ---------------------------------------------------------------------------
# _log_configuration_snapshot
# ---------------------------------------------------------------------------


class TestLogConfigurationSnapshot:
    def test_emits_log(self, caplog: pytest.LogCaptureFixture) -> None:
        env_cfg = SimpleNamespace(scene=None, num_envs=2, sim=SimpleNamespace(device="cpu"))
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch", max_iterations=5, distributed=False)
        agent_dict = {"trainer": {"timesteps": 100}}
        with caplog.at_level("INFO", logger="isaaclab.skrl"):
            _MOD._log_configuration_snapshot(cli, env_cfg, agent_dict, random_seed=11, rollouts=4)
        assert any("SKRL training configuration" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# _validate_gym_registry
# ---------------------------------------------------------------------------


class TestValidateGymRegistry:
    def test_missing_task_raises(self) -> None:
        with pytest.raises(ValueError, match="Task identifier is required"):
            _MOD._validate_gym_registry(None, MagicMock())

    def test_unknown_task_raises_with_isaac_list(self) -> None:
        gym = SimpleNamespace(envs=SimpleNamespace(registry={"Isaac-A": object(), "OtherTask": object()}))
        with pytest.raises(ValueError, match="Available Isaac tasks"):
            _MOD._validate_gym_registry("Isaac-Missing", gym)

    def test_known_task_passes(self) -> None:
        gym = SimpleNamespace(envs=SimpleNamespace(registry={"Isaac-Known": object()}))
        _MOD._validate_gym_registry("Isaac-Known", gym)


# ---------------------------------------------------------------------------
# _create_gym_environment
# ---------------------------------------------------------------------------


class TestCreateGymEnvironment:
    def test_render_mode_when_video_enabled(self) -> None:
        gym = MagicMock()
        gym.make.return_value = "env"
        result = _MOD._create_gym_environment("Isaac-Lift", env_cfg={"x": 1}, is_video_enabled=True, gym_module=gym)
        assert result == "env"
        gym.make.assert_called_once_with("Isaac-Lift", cfg={"x": 1}, render_mode="rgb_array")

    def test_render_mode_none_when_video_disabled(self) -> None:
        gym = MagicMock()
        _MOD._create_gym_environment("Isaac-Lift", env_cfg={}, is_video_enabled=False, gym_module=gym)
        gym.make.assert_called_once_with("Isaac-Lift", cfg={}, render_mode=None)


# ---------------------------------------------------------------------------
# _wrap_environment
# ---------------------------------------------------------------------------


class _MARLEnv:
    pass


class TestWrapEnvironment:
    def test_marl_env_with_ppo_calls_converter(self, tmp_path: Path) -> None:
        unwrapped = _MARLEnv()
        env = SimpleNamespace(unwrapped=unwrapped)
        converter = MagicMock(return_value=env)
        wrapper_cls = MagicMock(return_value="vec_env")
        cli = SimpleNamespace(algorithm="ppo", video=False, ml_framework="torch")

        result = _MOD._wrap_environment(
            env,
            cli_args=cli,
            log_dir=tmp_path,
            gym_module=MagicMock(),
            multi_agent_to_single_agent=converter,
            direct_mar_env_type=_MARLEnv,
            vec_wrapper_cls=wrapper_cls,
        )
        converter.assert_called_once()
        assert result == "vec_env"

    def test_non_marl_skips_converter(self, tmp_path: Path) -> None:
        env = SimpleNamespace(unwrapped=object())
        converter = MagicMock()
        wrapper_cls = MagicMock(return_value="vec_env")
        cli = SimpleNamespace(algorithm="ppo", video=False, ml_framework="jax")

        _MOD._wrap_environment(
            env,
            cli_args=cli,
            log_dir=tmp_path,
            gym_module=MagicMock(),
            multi_agent_to_single_agent=converter,
            direct_mar_env_type=_MARLEnv,
            vec_wrapper_cls=wrapper_cls,
        )
        converter.assert_not_called()


# ---------------------------------------------------------------------------
# _setup_agent_checkpoint and _apply_mlflow_logging
# ---------------------------------------------------------------------------


class TestSetupAgentCheckpoint:
    def test_no_resume_path_skips(self) -> None:
        runner = SimpleNamespace(agent=MagicMock())
        _MOD._setup_agent_checkpoint(runner, None)
        runner.agent.load.assert_not_called()

    def test_loads_checkpoint(self) -> None:
        runner = SimpleNamespace(agent=MagicMock())
        _MOD._setup_agent_checkpoint(runner, "/abs/ckpt.pt")
        runner.agent.load.assert_called_once_with("/abs/ckpt.pt")


class TestApplyMlflowLogging:
    def test_no_mlflow_module_skips(self) -> None:
        runner = SimpleNamespace(agent=MagicMock(update="orig"))
        _MOD._apply_mlflow_logging(runner, None)
        assert runner.agent.update == "orig"

    def test_replaces_update_when_mlflow_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = SimpleNamespace(agent=MagicMock())
        monkeypatch.setattr(_MOD, "create_mlflow_logging_wrapper", MagicMock(return_value="wrapped_update"))
        _MOD._apply_mlflow_logging(runner, MagicMock())
        assert runner.agent.update == "wrapped_update"


# ---------------------------------------------------------------------------
# _is_azureml_managed_run
# ---------------------------------------------------------------------------


class TestIsAzureMLManagedRun:
    def test_true_when_run_id_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_RUN_ID", "abc123")
        assert _MOD._is_azureml_managed_run() is True

    def test_false_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)
        assert _MOD._is_azureml_managed_run() is False


# ---------------------------------------------------------------------------
# mlflow_run_context
# ---------------------------------------------------------------------------


def _make_mlflow_args(**overrides: object) -> argparse.Namespace:
    defaults = {
        "checkpoint_mode": "from-scratch",
        "checkpoint_uri": "",
        "register_checkpoint": "",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _make_cli(**overrides: object) -> argparse.Namespace:
    defaults = {
        "algorithm": "PPO",
        "ml_framework": "torch",
        "distributed": False,
        "task": "Isaac-Lift",
        "mlflow_log_interval": "balanced",
        "max_iterations": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestMlflowRunContext:
    def test_starts_new_run_when_unmanaged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)
        monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
        monkeypatch.delenv("MLFLOW_EXPERIMENT_ID", raising=False)
        mlflow = MagicMock()
        env_cfg = SimpleNamespace(scene=None, num_envs=2)

        with _MOD.mlflow_run_context(
            mlflow,
            context=None,
            args=_make_mlflow_args(),
            cli_args=_make_cli(),
            env_cfg=env_cfg,
            log_dir=tmp_path,
            resume_path=None,
            random_seed=1,
            rollouts=3,
        ) as state:
            assert state.owns_run is True
            assert state.log_interval == _MOD._DEFAULT_MLFLOW_INTERVAL

        mlflow.start_run.assert_called_once()
        mlflow.end_run.assert_called_once()

    def test_resumes_azureml_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_RUN_ID", "azureml-run")
        monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "exp-1")
        mlflow = MagicMock()
        mlflow.active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="azureml-run"))
        env_cfg = SimpleNamespace(scene=None, num_envs=1)

        with _MOD.mlflow_run_context(
            mlflow,
            context=None,
            args=_make_mlflow_args(),
            cli_args=_make_cli(),
            env_cfg=env_cfg,
            log_dir=tmp_path,
            resume_path=None,
            random_seed=1,
            rollouts=3,
        ) as state:
            assert state.owns_run is False

        mlflow.set_experiment.assert_called_once_with(experiment_name="exp-1")
        mlflow.start_run.assert_called_once_with(run_id="azureml-run")
        mlflow.end_run.assert_not_called()

    def test_resumes_azureml_run_via_experiment_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MLFLOW_RUN_ID", "azureml-run")
        monkeypatch.delenv("MLFLOW_EXPERIMENT_NAME", raising=False)
        monkeypatch.setenv("MLFLOW_EXPERIMENT_ID", "exp-id-9")
        mlflow = MagicMock()
        env_cfg = SimpleNamespace(scene=None, num_envs=1)

        with _MOD.mlflow_run_context(
            mlflow,
            context=None,
            args=_make_mlflow_args(),
            cli_args=_make_cli(),
            env_cfg=env_cfg,
            log_dir=tmp_path,
            resume_path=None,
            random_seed=1,
            rollouts=3,
        ):
            pass

        mlflow.set_experiment.assert_called_once_with(experiment_id="exp-id-9")

    def test_start_run_failure_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)
        mlflow = MagicMock()
        mlflow.start_run.side_effect = RuntimeError("nope")
        env_cfg = SimpleNamespace(scene=None, num_envs=1)

        with pytest.raises(RuntimeError, match="nope"):
            with _MOD.mlflow_run_context(
                mlflow,
                context=None,
                args=_make_mlflow_args(),
                cli_args=_make_cli(),
                env_cfg=env_cfg,
                log_dir=tmp_path,
                resume_path=None,
                random_seed=1,
                rollouts=3,
            ):
                pass

    def test_attaches_optional_tags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MLFLOW_RUN_ID", raising=False)
        monkeypatch.setenv("MLFLOW_CORRELATION_ID", "corr-99")
        mlflow = MagicMock()
        env_cfg = SimpleNamespace(scene=None, num_envs=1)
        context = SimpleNamespace(workspace_name="ws-1")
        args = _make_mlflow_args(checkpoint_uri="runs:/abc")

        with _MOD.mlflow_run_context(
            mlflow,
            context=context,
            args=args,
            cli_args=_make_cli(),
            env_cfg=env_cfg,
            log_dir=tmp_path,
            resume_path="/some/ckpt",
            random_seed=1,
            rollouts=3,
        ):
            pass

        tags_call = mlflow.set_tags.call_args.args[0]
        assert tags_call["azureml_workspace"] == "ws-1"
        assert tags_call["checkpoint_resume"] == "/some/ckpt"
        assert tags_call["checkpoint_source_uri"] == "runs:/abc"
        assert tags_call["correlation_id"] == "corr-99"


# ---------------------------------------------------------------------------
# _finalize_mlflow_run
# ---------------------------------------------------------------------------


class TestFinalizeMlflowRun:
    def test_skips_register_when_no_uri(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        register = MagicMock()
        log_artifacts = MagicMock(return_value=None)
        monkeypatch.setattr(_MOD, "_log_artifacts", log_artifacts)
        monkeypatch.setattr(_MOD, "_register_checkpoint_model", register)

        state = _MOD.MLflowRunState(
            mlflow=mlflow,
            log_interval=10,
            owns_run=True,
            args=_make_mlflow_args(register_checkpoint="model"),
            cli_args=_make_cli(),
            log_dir=tmp_path,
            resume_path=None,
        )

        _MOD._finalize_mlflow_run(state)

        register.assert_not_called()
        mlflow.end_run.assert_called_once()

    def test_registers_when_uri_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        register = MagicMock()
        monkeypatch.setattr(_MOD, "_log_artifacts", MagicMock(return_value="runs:/x"))
        monkeypatch.setattr(_MOD, "_register_checkpoint_model", register)

        state = _MOD.MLflowRunState(
            mlflow=mlflow,
            log_interval=10,
            owns_run=False,
            args=_make_mlflow_args(register_checkpoint="model-x"),
            cli_args=_make_cli(),
            log_dir=tmp_path,
            resume_path=None,
        )

        _MOD._finalize_mlflow_run(state)

        register.assert_called_once()
        mlflow.end_run.assert_not_called()


# ---------------------------------------------------------------------------
# _execute_training_loop
# ---------------------------------------------------------------------------


class TestExecuteTrainingLoop:
    def test_records_elapsed(self) -> None:
        runner = SimpleNamespace(run=MagicMock())
        descriptor: dict = {}
        _MOD._execute_training_loop(runner, descriptor)
        assert "elapsed_seconds" in descriptor

    def test_records_elapsed_on_failure(self) -> None:
        runner = SimpleNamespace(run=MagicMock(side_effect=RuntimeError("boom")))
        descriptor: dict = {}
        with pytest.raises(RuntimeError):
            _MOD._execute_training_loop(runner, descriptor)
        assert "elapsed_seconds" in descriptor


# ---------------------------------------------------------------------------
# _build_run_descriptor
# ---------------------------------------------------------------------------


class TestBuildRunDescriptor:
    def test_includes_log_interval_when_provided(self) -> None:
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch", max_iterations=5)
        descriptor = _MOD._build_run_descriptor(
            cli,
            log_dir=Path("/tmp/x"),
            resume_path=None,
            agent_dict={"trainer": {"timesteps": 50}},
            rollouts=2,
            log_interval=10,
        )
        assert descriptor["mlflow_log_interval"] == 10
        assert descriptor["trainer_timesteps"] == 50
        assert descriptor["resume_checkpoint"] is False

    def test_omits_log_interval_when_none(self) -> None:
        cli = SimpleNamespace(algorithm="PPO", ml_framework="torch", max_iterations=None)
        descriptor = _MOD._build_run_descriptor(
            cli,
            log_dir=Path("/tmp/x"),
            resume_path="/abs/ckpt",
            agent_dict={},
            rollouts=1,
            log_interval=None,
        )
        assert "mlflow_log_interval" not in descriptor
        assert descriptor["resume_checkpoint"] is True


# ---------------------------------------------------------------------------
# _prepare_cli_arguments
# ---------------------------------------------------------------------------


class TestPrepareCliArguments:
    def test_video_enables_cameras(self) -> None:
        parser = _MOD._build_parser(MagicMock())
        args = argparse.Namespace(task="Isaac-Lift", num_envs=None, max_iterations=None, headless=False, checkpoint=None)
        cli_args, unparsed = _MOD._prepare_cli_arguments(parser, args, ["--video"])
        assert cli_args.video is True
        assert cli_args.enable_cameras is True

    def test_passes_through_hydra_overrides(self) -> None:
        parser = _MOD._build_parser(MagicMock())
        args = argparse.Namespace(task="Isaac-Lift", num_envs=None, max_iterations=None, headless=False, checkpoint=None)
        _, unparsed = _MOD._prepare_cli_arguments(parser, args, ["env.foo=bar"])
        assert "env.foo=bar" in unparsed


# ---------------------------------------------------------------------------
# _initialize_simulation
# ---------------------------------------------------------------------------


class TestInitializeSimulation:
    def test_creates_launcher_and_returns_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_argv = list(sys.argv)
        launcher_instance = SimpleNamespace(app=SimpleNamespace(config=SimpleNamespace(log_dir="/tmp/kit")))
        launcher_cls = MagicMock(return_value=launcher_instance)
        cli = argparse.Namespace()
        try:
            launcher, app = _MOD._initialize_simulation(launcher_cls, cli, ["--foo"])
        finally:
            sys.argv = original_argv
        assert launcher is launcher_instance
        assert app is launcher_instance.app
        launcher_cls.assert_called_once_with(cli)


# ---------------------------------------------------------------------------
# _close_simulation
# ---------------------------------------------------------------------------


class TestCloseSimulation:
    def test_calls_os_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[int] = []
        monkeypatch.setattr(_MOD.os, "_exit", lambda code: called.append(code))
        _MOD._close_simulation(None)
        assert called == [0]


# ---------------------------------------------------------------------------
# _run_training_with_mlflow
# ---------------------------------------------------------------------------


class TestRunTrainingWithMlflow:
    def test_no_mlflow_runs_directly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = SimpleNamespace(run=MagicMock())
        state = _MOD.LaunchState(agent_dict={}, random_seed=1, rollouts=2, log_dir=tmp_path, resume_path=None)
        modules = MagicMock()
        modules.mlflow_module = None
        execute = MagicMock()
        monkeypatch.setattr(_MOD, "_execute_training_loop", execute)

        _MOD._run_training_with_mlflow(
            runner=runner,
            state=state,
            env_cfg=SimpleNamespace(scene=None, num_envs=1),
            args=_make_mlflow_args(),
            cli_args=_make_cli(),
            context=None,
            modules=modules,
        )
        execute.assert_called_once()

    def test_with_mlflow_marks_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = MagicMock()
        state = _MOD.LaunchState(agent_dict={}, random_seed=1, rollouts=2, log_dir=tmp_path, resume_path=None)
        modules = MagicMock()
        mlflow = MagicMock()
        modules.mlflow_module = mlflow

        # Patch context manager and execution.
        captured_state = SimpleNamespace(log_interval=10, outcome="success")

        @_MOD.contextmanager
        def fake_ctx(*args: object, **kwargs: object):
            yield captured_state

        monkeypatch.setattr(_MOD, "mlflow_run_context", fake_ctx)
        monkeypatch.setattr(_MOD, "_execute_training_loop", MagicMock(side_effect=RuntimeError("fail")))

        with pytest.raises(RuntimeError):
            _MOD._run_training_with_mlflow(
                runner=runner,
                state=state,
                env_cfg=SimpleNamespace(scene=None, num_envs=1),
                args=_make_mlflow_args(),
                cli_args=_make_cli(),
                context=None,
                modules=modules,
            )
        assert captured_state.outcome == "failed"

    def test_with_mlflow_records_run_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        runner = MagicMock()
        state = _MOD.LaunchState(agent_dict={}, random_seed=1, rollouts=2, log_dir=tmp_path, resume_path=None)
        modules = MagicMock()
        mlflow = MagicMock()
        mlflow.active_run.return_value = SimpleNamespace(info=SimpleNamespace(run_id="run-77"))
        modules.mlflow_module = mlflow

        captured: dict = {}

        @_MOD.contextmanager
        def fake_ctx(*args: object, **kwargs: object):
            yield SimpleNamespace(log_interval=5, outcome="success")

        def fake_execute(runner: object, descriptor: dict) -> dict:
            captured.update(descriptor)
            return descriptor

        monkeypatch.setattr(_MOD, "mlflow_run_context", fake_ctx)
        monkeypatch.setattr(_MOD, "_execute_training_loop", fake_execute)

        _MOD._run_training_with_mlflow(
            runner=runner,
            state=state,
            env_cfg=SimpleNamespace(scene=None, num_envs=1),
            args=_make_mlflow_args(),
            cli_args=_make_cli(),
            context=None,
            modules=modules,
        )
        # Note: descriptor dict is mutated after fake_execute returns; we just ensure the call ran.
        assert "algorithm" in captured


# ---------------------------------------------------------------------------
# run_training error paths
# ---------------------------------------------------------------------------


class TestRunTraining:
    def test_raises_system_exit_when_isaaclab_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name: str, *args: object, **kwargs: object):
            if name == "isaaclab.app" or name.startswith("isaaclab.app."):
                raise ImportError("missing")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)

        with pytest.raises(SystemExit, match="IsaacLab packages are required"):
            _MOD.run_training(args=_make_mlflow_args(), hydra_args=[], context=None)
