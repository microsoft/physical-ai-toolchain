"""Unit tests for ``sil.scripts.download_aml_model`` module-level script."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _EVAL_ROOT / "sil" / "scripts" / "download_aml_model.py"


class TestDownloadAmlModel:
    """Execute the download script via importlib with mocked Azure SDK."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_azure_ml: tuple[MagicMock, MagicMock],
        tmp_path: Path,
    ) -> None:
        mock_ml, _ = mock_azure_ml
        self.mock_client = mock_ml.MLClient.return_value

        monkeypatch.setenv("AML_MODEL_NAME", "test-model")
        monkeypatch.setenv("AML_MODEL_VERSION", "3")

        self.download_dir = tmp_path / "aml-model"
        self.config_path = tmp_path / "aml_model_path.env"
        monkeypatch.setenv("AML_DOWNLOAD_DIR", str(self.download_dir))
        monkeypatch.setenv("AML_CONFIG_PATH", str(self.config_path))

    def _load_script(self) -> None:
        spec = importlib.util.spec_from_file_location("download_aml_model", _SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    def test_calls_download_with_model_info(self) -> None:
        model_dir = self.download_dir / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "weights.safetensors").write_bytes(b"\x00" * 64)

        self._load_script()

        self.mock_client.models.download.assert_called_once_with(
            name="test-model",
            version="3",
            download_path=str(self.download_dir),
        )

    def test_writes_config_env(self) -> None:
        model_dir = self.download_dir / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "weights.safetensors").write_bytes(b"\x00" * 64)

        self._load_script()

        content = self.config_path.read_text()
        assert content.startswith("AML_MODEL_PATH=")

    def test_finds_safetensors_directory(self) -> None:
        model_dir = self.download_dir / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        sub = model_dir / "checkpoint"
        sub.mkdir()
        (sub / "model.safetensors").write_bytes(b"\x00" * 64)

        self._load_script()

        content = self.config_path.read_text()
        assert "checkpoint" in content

    def test_finds_bin_directory(self) -> None:
        model_dir = self.download_dir / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        sub = model_dir / "ckpt"
        sub.mkdir()
        (sub / "pytorch_model.bin").write_bytes(b"\x00" * 64)

        self._load_script()

        content = self.config_path.read_text()
        assert "ckpt" in content

    def test_falls_back_to_download_dir_when_no_model_name_dir(self) -> None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        (self.download_dir / "random_file.txt").write_bytes(b"\x00" * 64)

        self._load_script()

        content = self.config_path.read_text()
        assert f"AML_MODEL_PATH={self.download_dir}" in content
