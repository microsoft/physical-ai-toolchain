"""Unit tests for ``sil.scripts.download_aml_model`` module-level script."""

from __future__ import annotations

import runpy
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = str(_EVAL_ROOT / "sil" / "scripts" / "download_aml_model.py")
_CONFIG_PATH = Path("/tmp/aml_model_path.env")
_DOWNLOAD_DIR = Path("/tmp/aml-model")


class TestDownloadAmlModel:
    """Execute the download script via runpy with mocked Azure SDK."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_azure_ml: tuple[MagicMock, MagicMock],
    ) -> None:
        mock_ml, _ = mock_azure_ml
        self.mock_client = mock_ml.MLClient.return_value

        monkeypatch.setenv("AML_MODEL_NAME", "test-model")
        monkeypatch.setenv("AML_MODEL_VERSION", "3")

        shutil.rmtree(_DOWNLOAD_DIR, ignore_errors=True)
        _CONFIG_PATH.unlink(missing_ok=True)
        yield
        shutil.rmtree(_DOWNLOAD_DIR, ignore_errors=True)
        _CONFIG_PATH.unlink(missing_ok=True)

    def test_calls_download_with_model_info(self) -> None:
        model_dir = _DOWNLOAD_DIR / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "weights.safetensors").write_bytes(b"\x00" * 64)

        runpy.run_path(_SCRIPT_PATH)

        self.mock_client.models.download.assert_called_once_with(
            name="test-model",
            version="3",
            download_path=str(_DOWNLOAD_DIR),
        )

    def test_writes_config_env(self) -> None:
        model_dir = _DOWNLOAD_DIR / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "weights.safetensors").write_bytes(b"\x00" * 64)

        runpy.run_path(_SCRIPT_PATH)

        content = _CONFIG_PATH.read_text()
        assert content.startswith("AML_MODEL_PATH=")

    def test_finds_safetensors_directory(self) -> None:
        model_dir = _DOWNLOAD_DIR / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        sub = model_dir / "checkpoint"
        sub.mkdir()
        (sub / "model.safetensors").write_bytes(b"\x00" * 64)

        runpy.run_path(_SCRIPT_PATH)

        content = _CONFIG_PATH.read_text()
        assert "checkpoint" in content

    def test_finds_bin_directory(self) -> None:
        model_dir = _DOWNLOAD_DIR / "test-model"
        model_dir.mkdir(parents=True, exist_ok=True)
        sub = model_dir / "ckpt"
        sub.mkdir()
        (sub / "pytorch_model.bin").write_bytes(b"\x00" * 64)

        runpy.run_path(_SCRIPT_PATH)

        content = _CONFIG_PATH.read_text()
        assert "ckpt" in content

    def test_falls_back_to_download_dir_when_no_model_name_dir(self) -> None:
        _DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (_DOWNLOAD_DIR / "random_file.txt").write_bytes(b"\x00" * 64)

        runpy.run_path(_SCRIPT_PATH)

        content = _CONFIG_PATH.read_text()
        assert f"AML_MODEL_PATH={_DOWNLOAD_DIR}" in content
