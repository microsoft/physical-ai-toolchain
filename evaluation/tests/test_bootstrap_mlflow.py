"""Unit tests for ``metrics.bootstrap_mlflow``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _EVAL_ROOT / "metrics" / "bootstrap_mlflow.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bootstrap_mlflow", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "mlflow_config.env"
    monkeypatch.setenv("MLFLOW_CONFIG_PATH", str(path))
    return path


@pytest.fixture
def mock_mlflow(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mlflow = MagicMock()
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    return mlflow


class TestAzureMLBackend:
    """The default backend reads the tracking URI from the Azure ML workspace."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_azure_ml: tuple[MagicMock, MagicMock],
        mock_mlflow: MagicMock,
    ) -> None:
        monkeypatch.delenv("TRACKING_BACKEND", raising=False)
        mock_ml, _ = mock_azure_ml
        self.mock_mlflow = mock_mlflow
        self.mock_workspace = MagicMock()
        self.mock_workspace.mlflow_tracking_uri = "azureml://test-tracking"
        mock_ml.MLClient.return_value.workspaces.get.return_value = self.mock_workspace

    def test_writes_config_with_tracking_uri(self, config_path: Path) -> None:
        assert _load_script().main() == 0
        assert "MLFLOW_TRACKING_URI=azureml://test-tracking\n" in config_path.read_text()
        self.mock_mlflow.set_tracking_uri.assert_called_once_with("azureml://test-tracking")

    def test_default_experiment_name_uses_policy_type(self, monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
        monkeypatch.setenv("POLICY_TYPE", "diffusion")
        _load_script().main()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-diffusion-inference\n" in config_path.read_text()

    def test_custom_experiment_name(self, monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "my-experiment")
        _load_script().main()
        assert "MLFLOW_EXPERIMENT_NAME=my-experiment\n" in config_path.read_text()
        self.mock_mlflow.set_experiment.assert_called_once_with("my-experiment")

    def test_none_experiment_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "none")
        _load_script().main()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-act-inference\n" in config_path.read_text()

    def test_missing_tracking_uri_fails(self, config_path: Path) -> None:
        self.mock_workspace.mlflow_tracking_uri = None
        assert _load_script().main() == 1
        assert not config_path.exists()


class TestSelfHostedBackends:
    def test_mlflow_backend_uses_tracking_uri(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, mock_mlflow: MagicMock
    ) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "mlflow")
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.local:5000")
        monkeypatch.delenv("EXPERIMENT_NAME", raising=False)
        monkeypatch.delenv("POLICY_TYPE", raising=False)
        monkeypatch.setitem(sys.modules, "azure.ai.ml", None)

        assert _load_script().main() == 0

        assert config_path.read_text() == (
            "MLFLOW_TRACKING_URI=http://mlflow.local:5000\nMLFLOW_EXPERIMENT_NAME=lerobot-act-inference\n"
        )
        mock_mlflow.set_tracking_uri.assert_called_once_with("http://mlflow.local:5000")
        mock_mlflow.set_registry_uri.assert_called_once_with("http://mlflow.local:5000")
        mock_mlflow.set_experiment.assert_called_once_with("lerobot-act-inference")

    def test_mlflow_backend_requires_tracking_uri(
        self, monkeypatch: pytest.MonkeyPatch, config_path: Path, mock_mlflow: MagicMock
    ) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "mlflow")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        assert _load_script().main() == 1
        assert not config_path.exists()

    def test_none_backend_configures_nothing(self, monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "none")
        monkeypatch.setitem(sys.modules, "mlflow", None)
        assert _load_script().main() == 0
        assert not config_path.exists()

    def test_unknown_backend_fails(self, monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
        monkeypatch.setenv("TRACKING_BACKEND", "wandb")
        assert _load_script().main() == 1
        assert not config_path.exists()
