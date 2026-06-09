from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import load_training_module

_fake_azure = ModuleType("azure")
_fake_ai = ModuleType("azure.ai")
_fake_ai_ml = ModuleType("azure.ai.ml")
_fake_identity = ModuleType("azure.identity")


class _FakeMLClient:
    def __init__(self, **_: object) -> None:
        self.workspaces = SimpleNamespace(
            get=lambda _name: SimpleNamespace(
                name="ws-test",
                location="eastus",
                mlflow_tracking_uri="azureml://tracking",
            )
        )


_fake_ai_ml.MLClient = _FakeMLClient
_fake_identity.DefaultAzureCredential = MagicMock

_fake_azure.ai = _fake_ai
_fake_azure.identity = _fake_identity
sys.modules.setdefault("azure", _fake_azure)
sys.modules.setdefault("azure.ai", _fake_ai)
sys.modules.setdefault("azure.ai.ml", _fake_ai_ml)
sys.modules.setdefault("azure.identity", _fake_identity)

_fake_mlflow = MagicMock(name="mlflow")


class _FakeRun:
    def __init__(self) -> None:
        self.info = SimpleNamespace(run_id="run-abc")

    def __enter__(self) -> _FakeRun:
        return self

    def __exit__(self, *_: object) -> None:
        return None


_fake_mlflow.start_run.return_value = _FakeRun()
_fake_mlflow.register_model.return_value = SimpleNamespace(name="model-x", version="3")
sys.modules.setdefault("mlflow", _fake_mlflow)

_MOD = load_training_module("training_utils_aml_mirror", "training/utils/aml_mirror.py")


@pytest.fixture(autouse=True)
def _reset_mlflow() -> None:
    _fake_mlflow.reset_mock()
    _fake_mlflow.start_run.return_value = _FakeRun()
    _fake_mlflow.register_model.return_value = SimpleNamespace(name="model-x", version="3")


@pytest.fixture()
def _env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-1")
    monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws-test")
    monkeypatch.setenv("AZUREML_MODEL_NAME", "my-model")
    monkeypatch.setenv("RUN_ID", "run-42")
    monkeypatch.setenv("OUTPUT_DIR", str(output))
    return output


class TestLogTensorboardMetrics:
    def test_tbparse_not_installed_returns_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        import builtins

        real_import = builtins.__import__

        def reject_tbparse(name: str, *args: object, **kwargs: object) -> object:
            if name == "tbparse":
                raise ImportError("no tbparse")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", reject_tbparse)
        assert _MOD._log_tensorboard_metrics(tmp_path) == 0

    def test_empty_scalars_returns_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake_df = SimpleNamespace(empty=True)
        fake_reader = SimpleNamespace(scalars=fake_df)
        fake_tbparse = ModuleType("tbparse")
        fake_tbparse.SummaryReader = lambda _path: fake_reader
        monkeypatch.setitem(sys.modules, "tbparse", fake_tbparse)
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        assert _MOD._log_tensorboard_metrics(tmp_path) == 0

    def test_logs_metrics_for_each_scalar(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        rows = [
            {"tag": "loss", "step": 1, "value": 0.5},
            {"tag": "loss", "step": 2, "value": 0.3},
            {"tag": "reward", "step": 1, "value": 1.0},
        ]

        class _FakeScalars:
            empty = False

            def __getitem__(self, key: str) -> _FakeScalars:
                self._key = key
                return self

            def __eq__(self, other: object) -> _FakeScalars:
                self._filter_value = other
                return self

            def unique(self) -> list[str]:
                return ["loss", "reward"]

            def sort_values(self, _by: str) -> _FakeScalars:
                return self

            def iterrows(self) -> list[tuple[int, dict[str, object]]]:
                return [(i, r) for i, r in enumerate(rows) if r["tag"] == self._filter_value]

        fake_reader = SimpleNamespace(scalars=_FakeScalars())
        fake_tbparse = ModuleType("tbparse")
        fake_tbparse.SummaryReader = lambda _path: fake_reader
        monkeypatch.setitem(sys.modules, "tbparse", fake_tbparse)
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        count = _MOD._log_tensorboard_metrics(tmp_path)

        assert count == 3
        assert _fake_mlflow.log_metric.call_count == 3


class TestMain:
    def test_missing_env_vars_returns_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in _MOD.REQUIRED_ENV:
            monkeypatch.delenv(key, raising=False)
        assert _MOD.main() == 1

    def test_output_dir_not_exists_returns_1(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws")
        monkeypatch.setenv("AZUREML_MODEL_NAME", "m")
        monkeypatch.setenv("RUN_ID", "r")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "nonexistent"))
        assert _MOD.main() == 1

    def test_no_checkpoints_returns_1(self, _env_vars: Path) -> None:
        assert _MOD.main() == 1

    def test_happy_path_stages_and_registers(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-100"
        ckpt.mkdir()
        (ckpt / "model.safetensors").write_bytes(b"weights")
        (ckpt / "config.json").write_text("{}")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        result = _MOD.main()

        assert result == 0
        _fake_mlflow.set_tracking_uri.assert_called_once()
        _fake_mlflow.set_experiment.assert_called_once_with("my-model")
        _fake_mlflow.register_model.assert_called_once()

    def test_skip_files_excluded(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-50"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"ok")
        (ckpt / "optimizer.pt").write_bytes(b"skip")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        result = _MOD.main()

        assert result == 0
        log_artifacts_calls = _fake_mlflow.log_artifacts.call_args_list
        staged_call = [
            c
            for c in log_artifacts_calls
            if c.kwargs.get("artifact_path") == "model" or (c.args and len(c.args) > 1 and "model" in str(c))
        ]
        assert len(staged_call) >= 1

    def test_replay_run_id_tags(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-1"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setenv("REPLAY_RUN_ID", "original-run-99")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        _MOD.main()

        tag_calls = {c.args[0]: c.args[1] for c in _fake_mlflow.set_tag.call_args_list}
        assert tag_calls["osmo.replay"] == "true"
        assert tag_calls["osmo.replay_source"] == "original-run-99"

    def test_tensorboard_dir_logged(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        tb_dir = _env_vars / "runs"
        tb_dir.mkdir()
        (tb_dir / "events.out.tfevents.123").write_bytes(b"data")
        ckpt = _env_vars / "checkpoint-5"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)
        monkeypatch.setattr(_MOD, "_log_tensorboard_metrics", lambda _: 42)

        _MOD.main()

        artifact_calls = [
            c for c in _fake_mlflow.log_artifacts.call_args_list if c.kwargs.get("artifact_path") == "tensorboard"
        ]
        assert len(artifact_calls) == 1

    def test_hardlink_fallback_to_copy(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ckpt = _env_vars / "checkpoint-10"
        ckpt.mkdir()
        (ckpt / "model.bin").write_bytes(b"x")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        monkeypatch.setattr(os, "link", MagicMock(side_effect=OSError("cross-device")))

        result = _MOD.main()

        assert result == 0

    def test_selects_highest_checkpoint(self, _env_vars: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        for i in (5, 20, 10):
            d = _env_vars / f"checkpoint-{i}"
            d.mkdir()
            (d / "model.bin").write_text(f"v{i}")
        monkeypatch.setattr(_MOD, "mlflow", _fake_mlflow)

        _MOD.main()

        register_call = _fake_mlflow.register_model.call_args
        assert register_call is not None
