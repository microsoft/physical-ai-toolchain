"""Unit tests for ``sil.scripts.download_blob_dataset`` module-level script."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _EVAL_ROOT / "sil" / "scripts" / "download_blob_dataset.py"


def _exec_script(local_data_root: Path, config_path: Path) -> None:
    """Execute the script with ``/workspace/data`` and ``/tmp/dataset_path.env`` redirected."""
    source = _SCRIPT_PATH.read_text()
    source = source.replace('"/workspace/data"', repr(str(local_data_root)))
    source = source.replace('"/tmp/dataset_path.env"', repr(str(config_path)))
    exec(compile(source, str(_SCRIPT_PATH), "exec"), {"__name__": "__main__"})


class TestDownloadBlobDataset:
    """Execute the download script with mocked Azure SDK and redirected output paths."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Mock azure.identity and azure.storage.blob via sys.modules.
        mock_identity = MagicMock()
        mock_blob = MagicMock()

        blob_a = MagicMock()
        blob_a.name = "myprefix/sub/file_a.bin"
        blob_b = MagicMock()
        blob_b.name = "myprefix/file_b.txt"
        # Empty rel-path entry should be skipped.
        blob_skip = MagicMock()
        blob_skip.name = "myprefix/"

        self.client = MagicMock()
        self.client.list_blobs.return_value = [blob_a, blob_b, blob_skip]
        download_stream = MagicMock()
        download_stream.readall.return_value = b"data-bytes"
        self.client.download_blob.return_value = download_stream

        mock_blob.ContainerClient.from_container_url.return_value = self.client
        self.mock_blob = mock_blob
        self.mock_identity = mock_identity

        monkeypatch.setitem(sys.modules, "azure", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.identity", mock_identity)
        monkeypatch.setitem(sys.modules, "azure.storage", MagicMock())
        monkeypatch.setitem(sys.modules, "azure.storage.blob", mock_blob)

        monkeypatch.setenv("BLOB_STORAGE_ACCOUNT", "myacct")
        monkeypatch.setenv("BLOB_PREFIX", "myprefix")
        monkeypatch.delenv("BLOB_STORAGE_CONTAINER", raising=False)

        self.data_root = tmp_path / "workspace_data"
        self.config_path = tmp_path / "dataset_path.env"
        self.local_root = self.data_root / "myprefix"
        shutil.rmtree(self.data_root, ignore_errors=True)
        yield
        shutil.rmtree(self.data_root, ignore_errors=True)

    def _run(self) -> None:
        _exec_script(self.data_root, self.config_path)

    def test_default_container_used(self) -> None:
        self._run()
        url = self.mock_blob.ContainerClient.from_container_url.call_args[0][0]
        assert url == "https://myacct.blob.core.windows.net/datasets"

    def test_custom_container_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BLOB_STORAGE_CONTAINER", "custom-ctr")
        self._run()
        url = self.mock_blob.ContainerClient.from_container_url.call_args[0][0]
        assert url.endswith("/custom-ctr")

    def test_writes_files_and_skips_empty_rel(self) -> None:
        self._run()
        assert (self.local_root / "sub" / "file_a.bin").read_bytes() == b"data-bytes"
        assert (self.local_root / "file_b.txt").read_bytes() == b"data-bytes"
        downloaded = [c.args[0] for c in self.client.download_blob.call_args_list]
        assert "myprefix/" not in downloaded

    def test_writes_config_env(self) -> None:
        self._run()
        content = self.config_path.read_text()
        assert content == f"DATASET_DIR={self.local_root}\n"

    def test_uses_default_credential(self) -> None:
        self._run()
        self.mock_identity.DefaultAzureCredential.assert_called_once()
