"""Unit tests for ``metrics.bootstrap_mlflow`` module-level script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _EVAL_ROOT / "metrics" / "bootstrap_mlflow.py"


class TestBootstrapMlflow:
    """Execute the bootstrap script via importlib with mocked Azure and MLflow."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_azure_ml: tuple[MagicMock, MagicMock],
        tmp_path: Path,
    ) -> None:
        mock_ml, _ = mock_azure_ml
        self.mock_mlflow = MagicMock()
        monkeypatch.setitem(sys.modules, "mlflow", self.mock_mlflow)

        self.mock_workspace = MagicMock()
        self.mock_workspace.mlflow_tracking_uri = "azureml://test-tracking"
        mock_ml.MLClient.return_value.workspaces.get.return_value = self.mock_workspace

        self.config_path = tmp_path / "mlflow_config.env"
        monkeypatch.setenv("MLFLOW_CONFIG_PATH", str(self.config_path))

    def _load_script(self) -> None:
        spec = importlib.util.spec_from_file_location("bootstrap_mlflow", _SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    def test_writes_config_with_tracking_uri(self) -> None:
        self._load_script()
        content = self.config_path.read_text()
        assert "MLFLOW_TRACKING_URI=azureml://test-tracking\n" in content

    def test_default_experiment_name_uses_policy_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POLICY_TYPE", "diffusion")
        self._load_script()
        content = self.config_path.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-diffusion-inference\n" in content

    def test_custom_experiment_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "my-experiment")
        self._load_script()
        content = self.config_path.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=my-experiment\n" in content

    def test_none_experiment_falls_back_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "none")
        self._load_script()
        content = self.config_path.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-act-inference\n" in content

    def test_missing_tracking_uri_exits(self) -> None:
        self.mock_workspace.mlflow_tracking_uri = None
        with pytest.raises(SystemExit) as exc_info:
            self._load_script()
        assert exc_info.value.code == 1
