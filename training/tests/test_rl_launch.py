"""Tests for training/rl/scripts/launch.py."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import load_training_module


class _AzureConfigError(Exception):
    pass


class _AzureMLContext:
    def __init__(self, tracking_uri: str = "azureml://tracking") -> None:
        self.tracking_uri = tracking_uri


def _bootstrap_azure_ml(experiment_name: str | None = None, **_: object) -> _AzureMLContext:
    return _AzureMLContext()


_fake_utils = ModuleType("training.utils")
_fake_utils.AzureConfigError = _AzureConfigError
_fake_utils.AzureMLContext = _AzureMLContext
_fake_utils.bootstrap_azure_ml = _bootstrap_azure_ml
sys.modules.setdefault("training.utils", _fake_utils)

_MOD = load_training_module("training_rl_scripts_launch", "training/rl/scripts/launch.py")


class TestOptionalParsers:
    def test_optional_int_none(self):
        assert _MOD._optional_int(None) is None
        assert _MOD._optional_int("") is None

    def test_optional_int_value(self):
        assert _MOD._optional_int("42") == 42

    def test_optional_str_none(self):
        assert _MOD._optional_str(None) is None
        assert _MOD._optional_str("") is None
        assert _MOD._optional_str("none") is None
        assert _MOD._optional_str("NONE") is None

    def test_optional_str_value(self):
        assert _MOD._optional_str("foo") == "foo"


class TestParseArgs:
    def test_defaults(self):
        args, remaining = _MOD._parse_args([])
        assert args.mode == "train"
        assert args.task is None
        assert args.num_envs is None
        assert args.headless is False
        assert args.disable_mlflow is False
        assert args.checkpoint_mode == "from-scratch"
        assert remaining == []

    def test_full_args(self):
        args, remaining = _MOD._parse_args(
            [
                "--mode",
                "smoke-test",
                "--task",
                "Walk",
                "--num_envs",
                "8",
                "--max_iterations",
                "100",
                "--headless",
                "--experiment-name",
                "exp",
                "--disable-mlflow",
                "--checkpoint-uri",
                "azureml://ckpt",
                "--checkpoint-mode",
                "warm-start",
                "--register-checkpoint",
                "model",
                "extra",
                "--hydra-arg",
            ]
        )
        assert args.mode == "smoke-test"
        assert args.task == "Walk"
        assert args.num_envs == 8
        assert args.max_iterations == 100
        assert args.headless is True
        assert args.experiment_name == "exp"
        assert args.disable_mlflow is True
        assert args.checkpoint_uri == "azureml://ckpt"
        assert args.checkpoint_mode == "warm-start"
        assert args.register_checkpoint == "model"
        assert remaining == ["extra", "--hydra-arg"]


class TestEnsureDependencies:
    def test_all_present(self, monkeypatch):
        monkeypatch.setattr(_MOD.importlib.util, "find_spec", lambda name: object())
        _MOD._ensure_dependencies()

    def test_missing_raises(self, monkeypatch):
        monkeypatch.setattr(_MOD.importlib.util, "find_spec", lambda name: None)
        with pytest.raises(SystemExit) as exc:
            _MOD._ensure_dependencies()
        assert "Missing required Python packages" in str(exc.value)


class TestNormalizeCheckpointMode:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, "from-scratch"),
            ("", "from-scratch"),
            ("fresh", "from-scratch"),
            ("from-scratch", "from-scratch"),
            ("warm-start", "warm-start"),
            ("WARM-START", "warm-start"),
            ("resume", "resume"),
        ],
    )
    def test_valid(self, value, expected):
        assert _MOD._normalize_checkpoint_mode(value) == expected

    def test_invalid(self):
        with pytest.raises(SystemExit) as exc:
            _MOD._normalize_checkpoint_mode("bogus")
        assert "Unsupported checkpoint mode" in str(exc.value)


class TestMaterializedCheckpoint:
    def test_no_uri_yields_none(self):
        with _MOD._materialized_checkpoint(None) as path:
            assert path is None
        with _MOD._materialized_checkpoint("") as path:
            assert path is None

    def test_mlflow_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "mlflow", None)
        with pytest.raises(SystemExit) as exc, _MOD._materialized_checkpoint("azureml://ckpt"):
            pass
        assert "mlflow is required" in str(exc.value)

    def test_download_success(self, monkeypatch, tmp_path):
        fake_mlflow = ModuleType("mlflow")
        fake_artifacts = SimpleNamespace(download_artifacts=MagicMock(return_value=str(tmp_path / "ckpt")))
        fake_mlflow.artifacts = fake_artifacts
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

        rmtree_mock = MagicMock()
        monkeypatch.setattr(_MOD.shutil, "rmtree", rmtree_mock)
        monkeypatch.setattr(_MOD.tempfile, "mkdtemp", lambda prefix=None: str(tmp_path / "dl"))

        with _MOD._materialized_checkpoint("azureml://ckpt") as path:
            assert path == str(tmp_path / "ckpt")
        rmtree_mock.assert_called_once()

    def test_download_failure_cleans_up(self, monkeypatch, tmp_path):
        fake_mlflow = ModuleType("mlflow")
        fake_mlflow.artifacts = SimpleNamespace(download_artifacts=MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
        rmtree_mock = MagicMock()
        monkeypatch.setattr(_MOD.shutil, "rmtree", rmtree_mock)
        monkeypatch.setattr(_MOD.tempfile, "mkdtemp", lambda prefix=None: str(tmp_path / "dl"))

        with pytest.raises(SystemExit) as exc, _MOD._materialized_checkpoint("azureml://ckpt"):
            pass
        assert "Failed to download checkpoint" in str(exc.value)
        rmtree_mock.assert_called_once()


class TestInitializeMlflowContext:
    def test_disabled(self):
        args = SimpleNamespace(disable_mlflow=True, experiment_name=None, task=None)
        ctx, name = _MOD._initialize_mlflow_context(args)
        assert ctx is None
        assert name is None

    def test_with_explicit_experiment(self, monkeypatch):
        captured = {}

        def fake_bootstrap(experiment_name):
            captured["exp"] = experiment_name
            return _AzureMLContext("uri")

        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", fake_bootstrap)
        args = SimpleNamespace(disable_mlflow=False, experiment_name="my-exp", task="Walk")
        ctx, name = _MOD._initialize_mlflow_context(args)
        assert captured["exp"] == "my-exp"
        assert name == "my-exp"
        assert ctx.tracking_uri == "uri"

    def test_with_task_default(self, monkeypatch):
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", lambda experiment_name: _AzureMLContext())
        args = SimpleNamespace(disable_mlflow=False, experiment_name=None, task="Run")
        _ctx, name = _MOD._initialize_mlflow_context(args)
        assert name == "isaaclab-Run"

    def test_with_no_task(self, monkeypatch):
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", lambda experiment_name: _AzureMLContext())
        args = SimpleNamespace(disable_mlflow=False, experiment_name=None, task=None)
        _ctx, name = _MOD._initialize_mlflow_context(args)
        assert name == "isaaclab-training"


class TestRunTraining:
    def test_module_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "training.rl.scripts.skrl_training", None)
        with pytest.raises(SystemExit) as exc:
            _MOD._run_training(args=SimpleNamespace(), hydra_args=[], context=None)
        assert "skrl_training module is unavailable" in str(exc.value)

    def test_invokes_training(self, monkeypatch):
        fake_module = ModuleType("training.rl.scripts.skrl_training")
        fake_module.run_training = MagicMock()
        monkeypatch.setitem(sys.modules, "training.rl.scripts.skrl_training", fake_module)
        # Also need parent packages
        monkeypatch.setitem(sys.modules, "training", sys.modules.get("training", ModuleType("training")))
        monkeypatch.setitem(sys.modules, "training.rl", sys.modules.get("training.rl", ModuleType("training.rl")))
        monkeypatch.setitem(
            sys.modules,
            "training.rl.scripts",
            sys.modules.get("training.rl.scripts", ModuleType("training.rl.scripts")),
        )

        args = SimpleNamespace(task="Walk")
        _MOD._run_training(args=args, hydra_args=["a"], context=None)
        fake_module.run_training.assert_called_once_with(args=args, hydra_args=["a"], context=None)


class TestRunSmokeTest:
    def test_invokes_smoke(self, monkeypatch):
        fake_module = ModuleType("training.rl.scripts.smoke_test_azure")
        fake_module.main = MagicMock()
        monkeypatch.setitem(sys.modules, "training.rl.scripts.smoke_test_azure", fake_module)
        monkeypatch.setitem(sys.modules, "training", sys.modules.get("training", ModuleType("training")))
        monkeypatch.setitem(sys.modules, "training.rl", sys.modules.get("training.rl", ModuleType("training.rl")))
        monkeypatch.setitem(
            sys.modules,
            "training.rl.scripts",
            sys.modules.get("training.rl.scripts", ModuleType("training.rl.scripts")),
        )

        _MOD._run_smoke_test()
        fake_module.main.assert_called_once_with([])


class TestValidateMlflowFlags:
    def test_ok_when_mlflow_enabled(self):
        args = SimpleNamespace(disable_mlflow=False, checkpoint_uri="x", register_checkpoint="y")
        _MOD._validate_mlflow_flags(args)

    def test_ok_when_no_flags(self):
        args = SimpleNamespace(disable_mlflow=True, checkpoint_uri=None, register_checkpoint=None)
        _MOD._validate_mlflow_flags(args)

    def test_checkpoint_uri_requires_mlflow(self):
        args = SimpleNamespace(disable_mlflow=True, checkpoint_uri="x", register_checkpoint=None)
        with pytest.raises(SystemExit) as exc:
            _MOD._validate_mlflow_flags(args)
        assert "--checkpoint-uri requires MLflow" in str(exc.value)

    def test_register_requires_mlflow(self):
        args = SimpleNamespace(disable_mlflow=True, checkpoint_uri=None, register_checkpoint="m")
        with pytest.raises(SystemExit) as exc:
            _MOD._validate_mlflow_flags(args)
        assert "--register-checkpoint requires MLflow" in str(exc.value)


class TestMain:
    def _patch_dependencies(self, monkeypatch):
        monkeypatch.setattr(_MOD, "_ensure_dependencies", lambda: None)

    def test_smoke_mode(self, monkeypatch):
        self._patch_dependencies(monkeypatch)
        smoke = MagicMock()
        monkeypatch.setattr(_MOD, "_run_smoke_test", smoke)
        _MOD.main(["--mode", "smoke-test"])
        smoke.assert_called_once()

    def test_train_mode_no_checkpoint(self, monkeypatch):
        self._patch_dependencies(monkeypatch)
        run_training = MagicMock()
        monkeypatch.setattr(_MOD, "_run_training", run_training)
        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", lambda args: (None, None))
        _MOD.main(["--mode", "train", "--disable-mlflow"])
        run_training.assert_called_once()

    def test_train_mode_with_checkpoint(self, monkeypatch, tmp_path):
        self._patch_dependencies(monkeypatch)
        run_training = MagicMock()
        monkeypatch.setattr(_MOD, "_run_training", run_training)
        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", lambda args: (None, None))

        from contextlib import contextmanager

        @contextmanager
        def fake_ckpt(uri):
            yield str(tmp_path / "ckpt")

        monkeypatch.setattr(_MOD, "_materialized_checkpoint", fake_ckpt)
        _MOD.main(["--mode", "train", "--checkpoint-uri", "azureml://ckpt"])
        run_training.assert_called_once()
        called_args = run_training.call_args.kwargs["args"]
        assert called_args.checkpoint == str(tmp_path / "ckpt")

    def test_train_mode_warm_start_no_checkpoint(self, monkeypatch):
        self._patch_dependencies(monkeypatch)
        run_training = MagicMock()
        monkeypatch.setattr(_MOD, "_run_training", run_training)
        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", lambda args: (None, None))
        _MOD.main(["--mode", "train", "--checkpoint-mode", "warm-start", "--disable-mlflow"])
        run_training.assert_called_once()

    def test_azure_config_error(self, monkeypatch):
        self._patch_dependencies(monkeypatch)

        def boom(args):
            raise _AzureConfigError("bad creds")

        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", boom)
        # _AzureConfigError must match the one launch.py imported; rebind it
        monkeypatch.setattr(_MOD, "AzureConfigError", _AzureConfigError)
        with pytest.raises(SystemExit) as exc:
            _MOD.main(["--mode", "train"])
        assert "bad creds" in str(exc.value)

    def test_uses_sys_argv(self, monkeypatch):
        self._patch_dependencies(monkeypatch)
        smoke = MagicMock()
        monkeypatch.setattr(_MOD, "_run_smoke_test", smoke)
        monkeypatch.setattr(_MOD.sys, "argv", ["launch.py", "--mode", "smoke-test"])
        _MOD.main(None)
        smoke.assert_called_once()
