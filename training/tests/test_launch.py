from __future__ import annotations

import sys
from contextlib import contextmanager
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
    def test_optional_int_none_inputs(self) -> None:
        assert _MOD._optional_int(None) is None
        assert _MOD._optional_int("") is None

    def test_optional_int_value(self) -> None:
        assert _MOD._optional_int("42") == 42

    def test_optional_str_none_inputs(self) -> None:
        assert _MOD._optional_str(None) is None
        assert _MOD._optional_str("") is None
        assert _MOD._optional_str("none") is None
        assert _MOD._optional_str("NONE") is None

    def test_optional_str_value(self) -> None:
        assert _MOD._optional_str("Walk") == "Walk"


class TestParseArgs:
    def test_defaults(self) -> None:
        args, remaining = _MOD._parse_args([])
        assert args.mode == "train"
        assert args.task is None
        assert args.num_envs is None
        assert args.max_iterations is None
        assert args.headless is False
        assert args.experiment_name is None
        assert args.disable_mlflow is False
        assert args.checkpoint_uri is None
        assert args.checkpoint_mode == "from-scratch"
        assert args.register_checkpoint is None
        assert remaining == []

    def test_full_args_with_hydra_extras(self) -> None:
        argv = [
            "--mode",
            "train",
            "--task",
            "Walk",
            "--num_envs",
            "8",
            "--max_iterations",
            "100",
            "--headless",
            "--experiment-name",
            "exp",
            "--checkpoint-uri",
            "azureml://artifact",
            "--checkpoint-mode",
            "warm-start",
            "--register-checkpoint",
            "model-name",
            "agent.lr=0.001",
            "env.seed=42",
        ]
        args, remaining = _MOD._parse_args(argv)
        assert args.task == "Walk"
        assert args.num_envs == 8
        assert args.max_iterations == 100
        assert args.headless is True
        assert args.experiment_name == "exp"
        assert args.checkpoint_uri == "azureml://artifact"
        assert args.checkpoint_mode == "warm-start"
        assert args.register_checkpoint == "model-name"
        assert remaining == ["agent.lr=0.001", "env.seed=42"]

    def test_smoke_test_mode(self) -> None:
        args, _ = _MOD._parse_args(["--mode", "smoke-test"])
        assert args.mode == "smoke-test"


class TestEnsureDependencies:
    def test_all_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_MOD.importlib.util, "find_spec", lambda name: object())
        _MOD._ensure_dependencies()

    def test_missing_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_MOD.importlib.util, "find_spec", lambda name: None)
        with pytest.raises(SystemExit) as exc_info:
            _MOD._ensure_dependencies()
        assert "Missing required Python packages" in str(exc_info.value)


class TestNormalizeCheckpointMode:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("fresh", "from-scratch"),
            ("from-scratch", "from-scratch"),
            ("warm-start", "warm-start"),
            ("resume", "resume"),
            ("WARM-START", "warm-start"),
            ("", "from-scratch"),
            (None, "from-scratch"),
        ],
    )
    def test_valid_values(self, value: str | None, expected: str) -> None:
        assert _MOD._normalize_checkpoint_mode(value) == expected

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _MOD._normalize_checkpoint_mode("bogus")
        assert "Unsupported checkpoint mode: bogus" in str(exc_info.value)


class TestMaterializedCheckpoint:
    def test_empty_uri_yields_none(self) -> None:
        with _MOD._materialized_checkpoint(None) as path:
            assert path is None
        with _MOD._materialized_checkpoint("") as path:
            assert path is None

    def test_mlflow_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "mlflow", None)
        with pytest.raises(SystemExit) as exc_info:
            with _MOD._materialized_checkpoint("azureml://artifact"):
                pass
        assert "mlflow is required" in str(exc_info.value)

    def test_success_downloads_and_cleans_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        download_mock = MagicMock(return_value="/tmp/skrl-ckpt-xyz/checkpoint.pt")
        fake_mlflow = ModuleType("mlflow")
        fake_mlflow.artifacts = SimpleNamespace(download_artifacts=download_mock)
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

        mkdtemp_mock = MagicMock(return_value="/tmp/skrl-ckpt-xyz")
        rmtree_mock = MagicMock()
        monkeypatch.setattr(_MOD.tempfile, "mkdtemp", mkdtemp_mock)
        monkeypatch.setattr(_MOD.shutil, "rmtree", rmtree_mock)

        with _MOD._materialized_checkpoint("azureml://artifact") as path:
            assert path == "/tmp/skrl-ckpt-xyz/checkpoint.pt"

        mkdtemp_mock.assert_called_once_with(prefix="skrl-ckpt-")
        download_mock.assert_called_once_with(
            artifact_uri="azureml://artifact", dst_path="/tmp/skrl-ckpt-xyz"
        )
        rmtree_mock.assert_called_with("/tmp/skrl-ckpt-xyz", ignore_errors=True)

    def test_download_failure_cleans_up_and_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        download_mock = MagicMock(side_effect=RuntimeError("boom"))
        fake_mlflow = ModuleType("mlflow")
        fake_mlflow.artifacts = SimpleNamespace(download_artifacts=download_mock)
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

        monkeypatch.setattr(_MOD.tempfile, "mkdtemp", MagicMock(return_value="/tmp/skrl-ckpt-fail"))
        rmtree_mock = MagicMock()
        monkeypatch.setattr(_MOD.shutil, "rmtree", rmtree_mock)

        with pytest.raises(SystemExit) as exc_info:
            with _MOD._materialized_checkpoint("azureml://artifact"):
                pass
        assert "Failed to download checkpoint from azureml://artifact" in str(exc_info.value)
        rmtree_mock.assert_called_with("/tmp/skrl-ckpt-fail", ignore_errors=True)


class TestInitializeMlflowContext:
    def test_disabled_returns_none(self) -> None:
        args = SimpleNamespace(disable_mlflow=True, experiment_name=None, task=None)
        context, name = _MOD._initialize_mlflow_context(args)
        assert context is None
        assert name is None

    def test_explicit_experiment_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bootstrap_mock = MagicMock(return_value=_AzureMLContext("uri-1"))
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", bootstrap_mock)
        args = SimpleNamespace(disable_mlflow=False, experiment_name="exp", task="Walk")
        context, name = _MOD._initialize_mlflow_context(args)
        assert name == "exp"
        assert context.tracking_uri == "uri-1"
        bootstrap_mock.assert_called_once_with(experiment_name="exp")

    def test_default_with_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bootstrap_mock = MagicMock(return_value=_AzureMLContext())
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", bootstrap_mock)
        args = SimpleNamespace(disable_mlflow=False, experiment_name=None, task="Walk")
        _, name = _MOD._initialize_mlflow_context(args)
        assert name == "isaaclab-Walk"

    def test_default_without_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bootstrap_mock = MagicMock(return_value=_AzureMLContext())
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", bootstrap_mock)
        args = SimpleNamespace(disable_mlflow=False, experiment_name=None, task=None)
        _, name = _MOD._initialize_mlflow_context(args)
        assert name == "isaaclab-training"


def _seed_training_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    for pkg in ("training", "training.rl", "training.rl.scripts"):
        if pkg not in sys.modules:
            monkeypatch.setitem(sys.modules, pkg, ModuleType(pkg))


class TestRunTraining:
    def test_calls_skrl_training(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_training_packages(monkeypatch)
        run_mock = MagicMock()
        fake_skrl = ModuleType("training.rl.scripts.skrl_training")
        fake_skrl.run_training = run_mock
        monkeypatch.setitem(sys.modules, "training.rl.scripts.skrl_training", fake_skrl)

        args = SimpleNamespace(task="Walk")
        hydra = ["agent.lr=0.001"]
        ctx = _AzureMLContext()
        _MOD._run_training(args=args, hydra_args=hydra, context=ctx)
        run_mock.assert_called_once_with(args=args, hydra_args=hydra, context=ctx)

    def test_import_error_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "training.rl.scripts" and "skrl_training" in (kwargs.get("fromlist") or args[2] if len(args) > 2 else ()):
                raise ImportError("forced")
            if name == "training.rl.scripts.skrl_training":
                raise ImportError("forced")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        with pytest.raises(SystemExit) as exc_info:
            _MOD._run_training(args=SimpleNamespace(), hydra_args=[], context=None)
        assert "skrl_training module is unavailable" in str(exc_info.value)


class TestRunSmokeTest:
    def test_invokes_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_training_packages(monkeypatch)
        main_mock = MagicMock()
        fake_smoke = ModuleType("training.rl.scripts.smoke_test_azure")
        fake_smoke.main = main_mock
        monkeypatch.setitem(sys.modules, "training.rl.scripts.smoke_test_azure", fake_smoke)

        _MOD._run_smoke_test()
        main_mock.assert_called_once_with([])


class TestValidateMlflowFlags:
    def test_no_disable_passes(self) -> None:
        args = SimpleNamespace(disable_mlflow=False, checkpoint_uri="x", register_checkpoint="y")
        _MOD._validate_mlflow_flags(args)

    def test_disable_without_extras_passes(self) -> None:
        args = SimpleNamespace(disable_mlflow=True, checkpoint_uri=None, register_checkpoint=None)
        _MOD._validate_mlflow_flags(args)

    def test_checkpoint_uri_with_disable_raises(self) -> None:
        args = SimpleNamespace(
            disable_mlflow=True, checkpoint_uri="azureml://x", register_checkpoint=None
        )
        with pytest.raises(SystemExit) as exc_info:
            _MOD._validate_mlflow_flags(args)
        assert "--checkpoint-uri requires MLflow" in str(exc_info.value)

    def test_register_checkpoint_with_disable_raises(self) -> None:
        args = SimpleNamespace(
            disable_mlflow=True, checkpoint_uri=None, register_checkpoint="model-name"
        )
        with pytest.raises(SystemExit) as exc_info:
            _MOD._validate_mlflow_flags(args)
        assert "--register-checkpoint requires MLflow" in str(exc_info.value)


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_MOD, "_ensure_dependencies", lambda: None)


@contextmanager
def _fake_ckpt(path: str | None):
    yield path


class TestMain:
    def test_smoke_test_returns_early(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dependencies(monkeypatch)
        smoke_mock = MagicMock()
        train_mock = MagicMock()
        monkeypatch.setattr(_MOD, "_run_smoke_test", smoke_mock)
        monkeypatch.setattr(_MOD, "_run_training", train_mock)
        _MOD.main(["--mode", "smoke-test"])
        smoke_mock.assert_called_once_with()
        train_mock.assert_not_called()

    def test_train_mode_no_checkpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dependencies(monkeypatch)
        train_mock = MagicMock()
        monkeypatch.setattr(_MOD, "_run_training", train_mock)
        monkeypatch.setattr(
            _MOD, "_initialize_mlflow_context", lambda args: (_AzureMLContext(), "exp")
        )
        monkeypatch.setattr(_MOD, "_materialized_checkpoint", _fake_ckpt)
        _MOD.main(["--task", "Walk"])
        train_mock.assert_called_once()
        kwargs = train_mock.call_args.kwargs
        assert kwargs["args"].checkpoint is None
        assert kwargs["args"].checkpoint_mode == "from-scratch"

    def test_train_mode_with_checkpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dependencies(monkeypatch)
        train_mock = MagicMock()
        monkeypatch.setattr(_MOD, "_run_training", train_mock)
        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", lambda args: (None, None))

        @contextmanager
        def fake_ckpt(uri: str | None):
            assert uri == "azureml://artifact"
            yield "/tmp/ckpt/file.pt"

        monkeypatch.setattr(_MOD, "_materialized_checkpoint", fake_ckpt)
        _MOD.main(
            [
                "--task",
                "Walk",
                "--checkpoint-uri",
                "azureml://artifact",
                "--checkpoint-mode",
                "warm-start",
            ]
        )
        train_mock.assert_called_once()
        assert train_mock.call_args.kwargs["args"].checkpoint == "/tmp/ckpt/file.pt"

    def test_warm_start_without_checkpoint_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _patch_dependencies(monkeypatch)
        monkeypatch.setattr(_MOD, "_run_training", MagicMock())
        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", lambda args: (None, None))
        monkeypatch.setattr(_MOD, "_materialized_checkpoint", _fake_ckpt)
        with caplog.at_level("INFO", logger="isaaclab.launch"):
            _MOD.main(["--task", "Walk", "--checkpoint-mode", "warm-start"])
        assert any("No checkpoint provided" in rec.message for rec in caplog.records)

    def test_azure_config_error_raises_system_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dependencies(monkeypatch)

        def _raise(args):
            raise _AzureConfigError("auth failure")

        monkeypatch.setattr(_MOD, "_initialize_mlflow_context", _raise)
        with pytest.raises(SystemExit) as exc_info:
            _MOD.main(["--task", "Walk"])
        assert "auth failure" in str(exc_info.value)

    def test_uses_sys_argv_when_argv_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_dependencies(monkeypatch)
        smoke_mock = MagicMock()
        monkeypatch.setattr(_MOD, "_run_smoke_test", smoke_mock)
        monkeypatch.setattr(_MOD.sys, "argv", ["launch.py", "--mode", "smoke-test"])
        _MOD.main()
        smoke_mock.assert_called_once_with()
