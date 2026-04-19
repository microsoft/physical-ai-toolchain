"""Unit tests for ``metrics.bootstrap_mlflow`` module-level script."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = str(_EVAL_ROOT / "metrics" / "bootstrap_mlflow.py")
_CONFIG_PATH = Path("/tmp/mlflow_config.env")


class TestBootstrapMlflow:
    """Execute the bootstrap script via runpy with mocked Azure and MLflow."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_azure_ml: tuple[MagicMock, MagicMock],
    ) -> None:
        mock_ml, _ = mock_azure_ml
        self.mock_mlflow = MagicMock()
        monkeypatch.setitem(sys.modules, "mlflow", self.mock_mlflow)

        self.mock_workspace = MagicMock()
        self.mock_workspace.mlflow_tracking_uri = "azureml://test-tracking"
        mock_ml.MLClient.return_value.workspaces.get.return_value = self.mock_workspace

        _CONFIG_PATH.unlink(missing_ok=True)
        yield
        _CONFIG_PATH.unlink(missing_ok=True)

    def test_writes_config_with_tracking_uri(self) -> None:
        runpy.run_path(_SCRIPT_PATH)
        content = _CONFIG_PATH.read_text()
        assert "MLFLOW_TRACKING_URI=azureml://test-tracking\n" in content

    def test_default_experiment_name_uses_policy_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("POLICY_TYPE", "diffusion")
        runpy.run_path(_SCRIPT_PATH)
        content = _CONFIG_PATH.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-diffusion-inference\n" in content

    def test_custom_experiment_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "my-experiment")
        runpy.run_path(_SCRIPT_PATH)
        content = _CONFIG_PATH.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=my-experiment\n" in content

    def test_none_experiment_falls_back_to_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("EXPERIMENT_NAME", "none")
        runpy.run_path(_SCRIPT_PATH)
        content = _CONFIG_PATH.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-act-inference\n" in content

    def test_missing_tracking_uri_exits(self) -> None:
        self.mock_workspace.mlflow_tracking_uri = None
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(_SCRIPT_PATH)
        assert exc_info.value.code == 1
