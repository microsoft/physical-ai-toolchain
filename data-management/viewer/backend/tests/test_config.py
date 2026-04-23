"""Unit tests for application configuration loader."""

from __future__ import annotations

import pytest

from src.api import config as config_mod
from src.api.config import (
    AppConfig,
    create_annotation_storage,
    create_blob_dataset_provider,
    get_app_config,
    load_config,
)
from src.api.storage import LocalStorageAdapter


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    config_mod._app_config = None
    yield
    config_mod._app_config = None


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "HMI_STORAGE_BACKEND",
        "HMI_DATA_PATH",
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


class TestLoadConfig:
    def test_defaults_when_no_env(self):
        cfg = load_config()
        assert cfg.storage_backend == "local"
        assert cfg.data_path == "./data"
        assert cfg.azure_account_name is None
        assert cfg.backend_host == "127.0.0.1"
        assert cfg.backend_port == 8000
        assert cfg.episode_cache_capacity == 32
        assert cfg.episode_cache_max_mb == 100
        assert "http://localhost:5173" in cfg.cors_origins

    def test_storage_backend_lowercased(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HMI_STORAGE_BACKEND", "AZURE")
        cfg = load_config()
        assert cfg.storage_backend == "azure"

    def test_cors_origins_split_and_trimmed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("CORS_ORIGINS", "http://a.test , http://b.test ,, ")
        cfg = load_config()
        assert cfg.cors_origins == ["http://a.test", "http://b.test"]

    def test_int_env_coercion(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BACKEND_PORT", "9090")
        monkeypatch.setenv("EPISODE_CACHE_CAPACITY", "8")
        monkeypatch.setenv("EPISODE_CACHE_MAX_MB", "0")
        cfg = load_config()
        assert cfg.backend_port == 9090
        assert cfg.episode_cache_capacity == 8
        assert cfg.episode_cache_max_mb == 0

    def test_azure_env_populated(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HMI_STORAGE_BACKEND", "azure")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
        monkeypatch.setenv("AZURE_STORAGE_DATASET_CONTAINER", "datasets")
        monkeypatch.setenv("AZURE_STORAGE_ANNOTATION_CONTAINER", "ann")
        monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=token")
        cfg = load_config()
        assert cfg.azure_account_name == "acct"
        assert cfg.azure_dataset_container == "datasets"
        assert cfg.azure_annotation_container == "ann"
        assert cfg.azure_sas_token == "sv=token"


class TestGetAppConfigSingleton:
    def test_caches_first_load(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("BACKEND_PORT", "9001")
        first = get_app_config()
        monkeypatch.setenv("BACKEND_PORT", "9002")
        second = get_app_config()
        assert first is second
        assert second.backend_port == 9001


class TestCreateAnnotationStorage:
    def test_local_returns_local_adapter(self, tmp_path):
        cfg = AppConfig(
            storage_backend="local",
            data_path=str(tmp_path),
            azure_account_name=None,
            azure_dataset_container=None,
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
        )
        adapter = create_annotation_storage(cfg)
        assert isinstance(adapter, LocalStorageAdapter)

    def test_azure_missing_account_raises(self):
        cfg = AppConfig(
            storage_backend="azure",
            data_path="./data",
            azure_account_name=None,
            azure_dataset_container="ds",
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
        )
        with pytest.raises(ValueError, match="AZURE_STORAGE_ACCOUNT_NAME"):
            create_annotation_storage(cfg)

    def test_azure_missing_container_raises(self):
        cfg = AppConfig(
            storage_backend="azure",
            data_path="./data",
            azure_account_name="acct",
            azure_dataset_container=None,
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
        )
        with pytest.raises(ValueError, match="CONTAINER"):
            create_annotation_storage(cfg)


class TestCreateBlobDatasetProvider:
    def test_returns_none_for_local_backend(self):
        cfg = AppConfig(
            storage_backend="local",
            data_path="./data",
            azure_account_name=None,
            azure_dataset_container=None,
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
        )
        assert create_blob_dataset_provider(cfg) is None

    def test_returns_none_when_account_missing(self):
        cfg = AppConfig(
            storage_backend="azure",
            data_path="./data",
            azure_account_name=None,
            azure_dataset_container="ds",
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
        )
        assert create_blob_dataset_provider(cfg) is None
