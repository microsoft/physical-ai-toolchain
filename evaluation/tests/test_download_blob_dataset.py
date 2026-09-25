"""Unit tests for ``sil.scripts.download_blob_dataset`` module-level script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

_EVAL_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _EVAL_ROOT / "sil" / "scripts" / "download_blob_dataset.py"


class TestDownloadBlobDataset:
    """Execute the download script with mocked Azure SDK and redirected output paths."""

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Mock azure.identity and azure.storage.blob via sys.modules.
        mock_identity = MagicMock()
        mock_blob = MagicMock()

        blob_a = MagicMock()
        blob_a.name = "myprefix/sub/file_a.bin"
        blob_a.size = len(b"data-bytes")
        blob_b = MagicMock()
        blob_b.name = "myprefix/file_b.txt"
        blob_b.size = len(b"data-bytes")
        # Empty rel-path entry should be skipped.
        blob_skip = MagicMock()
        blob_skip.name = "myprefix/"

        self.client = MagicMock()
        self.client.list_blobs.return_value = [blob_a, blob_b, blob_skip]
        download_stream = MagicMock()
        download_stream.readinto.side_effect = lambda handle: handle.write(b"data-bytes")
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
        monkeypatch.setenv("DATA_ROOT", str(self.data_root))
        monkeypatch.setenv("DATASET_CONFIG_PATH", str(self.config_path))

    def _load(self) -> ModuleType:
        spec = importlib.util.spec_from_file_location("download_blob_dataset", _SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run(self) -> None:
        module = self._load()
        module.download_dataset()

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

    def test_verified_release_is_checked_before_publication(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_TRUST", "verified")
        module = self._load()

        def verify(staged: Path, *, expected_target_format: tuple[str, str]) -> None:
            assert staged == self.local_root.with_name(f".{self.local_root.name}.new")
            assert staged.is_dir()
            assert not self.local_root.exists()
            assert expected_target_format == ("lerobot", "3.0")

        monkeypatch.setattr(module, "verify_release", verify)
        module.download_dataset()

        assert self.local_root.is_dir()

    def test_verification_failure_leaves_no_published_dataset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATASET_TRUST", "verified")
        module = self._load()
        monkeypatch.setattr(module, "verify_release", MagicMock(side_effect=ValueError("tampered")))

        with pytest.raises(ValueError, match="tampered"):
            module.download_dataset()

        assert not self.local_root.exists()
        assert not self.local_root.with_name(f".{self.local_root.name}.new").exists()

    def test_rejects_blob_path_traversal(self) -> None:
        blob = MagicMock(name="blob")
        blob.name = "myprefix/../escape"
        self.client.list_blobs.return_value = [blob]

        with pytest.raises(ValueError, match="Unsafe blob path"):
            self._run()

        assert not self.local_root.exists()

    def test_rejects_short_download(self) -> None:
        self.client.list_blobs.return_value[0].size += 1

        with pytest.raises(RuntimeError, match="Short read"):
            self._run()

        assert not self.local_root.exists()

    def test_refuses_to_overwrite_existing_dataset(self) -> None:
        self.local_root.mkdir(parents=True)

        with pytest.raises(FileExistsError, match="refusing to overwrite"):
            self._run()
