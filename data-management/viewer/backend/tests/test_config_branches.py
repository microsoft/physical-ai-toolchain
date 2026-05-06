"""Branch coverage tests for src/api/config.py."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from src.api import config as config_mod
from src.api.config import (
    AppConfig,
    create_annotation_storage,
    create_blob_dataset_provider,
    load_config,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "STORAGE_BACKEND",
        "DATA_DIR",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_STORAGE_DATASET_CONTAINER",
        "AZURE_STORAGE_ANNOTATION_CONTAINER",
        "AZURE_STORAGE_SAS_TOKEN",
        "BACKEND_HOST",
        "BACKEND_PORT",
        "CORS_ORIGINS",
        "EPISODE_CACHE_CAPACITY",
        "EPISODE_CACHE_MAX_MB",
    ):
        monkeypatch.delenv(var, raising=False)


def _azure_config(**overrides) -> AppConfig:
    defaults = dict(
        storage_backend="azure",
        data_path="./data",
        azure_account_name="acct",
        azure_dataset_container="datasets",
        azure_annotation_container="annotations",
        azure_sas_token=None,
        backend_host="127.0.0.1",
        backend_port=8000,
        cors_origins=[],
        episode_cache_capacity=32,
        episode_cache_max_mb=100,
    )
    defaults.update(overrides)
    return AppConfig(**defaults)


class TestLoadConfigEnvPath:
    def test_load_config_invokes_dotenv_when_env_path_provided(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[Path] = []
        fake_dotenv = types.ModuleType("dotenv")

        def _load_dotenv(path):
            calls.append(path)

        fake_dotenv.load_dotenv = _load_dotenv  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

        env_file = tmp_path / ".env"
        env_file.write_text("X=1\n")

        cfg = load_config(env_path=env_file)

        assert calls == [env_file]
        assert cfg.storage_backend == "local"


class TestCreateAnnotationStorageAzure:
    def test_returns_azure_adapter_with_sas_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        class _FakeAzureAdapter:
            def __init__(self, *, account_name, container_name, sas_token, use_managed_identity):
                captured["account_name"] = account_name
                captured["container_name"] = container_name
                captured["sas_token"] = sas_token
                captured["use_managed_identity"] = use_managed_identity

        fake_module = types.ModuleType("src.api.storage.azure")
        fake_module.AzureBlobStorageAdapter = _FakeAzureAdapter  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "src.api.storage.azure", fake_module)

        cfg = _azure_config(azure_sas_token="sas-value", azure_annotation_container=None)
        adapter = create_annotation_storage(cfg)

        assert isinstance(adapter, _FakeAzureAdapter)
        # Falls back to dataset container when annotation container missing
        assert captured["container_name"] == "datasets"
        assert captured["account_name"] == "acct"
        assert captured["sas_token"] == "sas-value"
        assert captured["use_managed_identity"] is False

    def test_returns_azure_adapter_with_managed_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        class _FakeAzureAdapter:
            def __init__(self, *, account_name, container_name, sas_token, use_managed_identity):
                captured["use_managed_identity"] = use_managed_identity
                captured["sas_token"] = sas_token

        fake_module = types.ModuleType("src.api.storage.azure")
        fake_module.AzureBlobStorageAdapter = _FakeAzureAdapter  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "src.api.storage.azure", fake_module)

        cfg = _azure_config()
        create_annotation_storage(cfg)

        assert captured["sas_token"] is None
        assert captured["use_managed_identity"] is True


class TestCreateBlobDatasetProvider:
    def test_returns_provider_when_azure_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        class _FakeBlobProvider:
            def __init__(self, *, account_name, container_name, sas_token):
                captured["account_name"] = account_name
                captured["container_name"] = container_name
                captured["sas_token"] = sas_token

        fake_module = types.ModuleType("src.api.storage.blob_dataset")
        fake_module.BlobDatasetProvider = _FakeBlobProvider  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "src.api.storage.blob_dataset", fake_module)

        cfg = _azure_config(azure_sas_token="sas")
        provider = create_blob_dataset_provider(cfg)

        assert isinstance(provider, _FakeBlobProvider)
        assert captured == {
            "account_name": "acct",
            "container_name": "datasets",
            "sas_token": "sas",
        }

    def test_returns_none_when_blob_dataset_import_fails(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Force the import inside the function to raise ImportError.
        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if (
                name.endswith("storage.blob_dataset")
                or (level > 0 and "blob_dataset" in (fromlist or ()))
                or name == "src.api.storage.blob_dataset"
            ):
                raise ImportError("simulated missing azure extras")
            if level > 0 and name == "storage.blob_dataset":
                raise ImportError("simulated missing azure extras")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", _fake_import)
        # Ensure cached module does not satisfy the import.
        monkeypatch.setitem(sys.modules, "src.api.storage.blob_dataset", None)

        cfg = _azure_config()
        with caplog.at_level("WARNING", logger=config_mod.logger.name):
            provider = create_blob_dataset_provider(cfg)

        assert provider is None
        assert any("BlobDatasetProvider unavailable" in rec.message for rec in caplog.records)
