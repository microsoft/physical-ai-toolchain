"""Unit tests for application configuration loader."""

from __future__ import annotations

import pytest

from src.api import config as config_mod
from src.api.config import (
    AppConfig,
    create_annotation_storage,
    create_blob_dataset_provider,
    create_review_repository,
    get_app_config,
    load_config,
)
from src.api.storage import LocalStorageAdapter
from src.api.storage.review_azure import AzureReviewRepository
from src.api.storage.review_local import LocalReviewRepository


@pytest.fixture(autouse=True)
def _reset_config_singleton():
    config_mod._app_config = None
    yield
    config_mod._app_config = None


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "STORAGE_BACKEND",
        "DATA_DIR",
        "DATA_PATH",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "AZURE_STORAGE_DATASET_CONTAINER",
        "AZURE_STORAGE_ANNOTATION_CONTAINER",
        "AZURE_STORAGE_DATASET_EXPORT_PREFIX",
        "AZURE_STORAGE_SAS_TOKEN",
        "DATAVIEWER_RELEASE_ROOT",
        "BACKEND_HOST",
        "BACKEND_PORT",
        "CORS_ORIGINS",
        "EPISODE_CACHE_CAPACITY",
        "EPISODE_CACHE_MAX_MB",
        "DETECTION_MODELS_DIR",
        "DETECTION_MODEL_DIGESTS",
        "DETECTION_CACHE_MAX_SIZE",
        "DETECTION_CACHE_TTL_SECONDS",
        "DETECTION_CONFIDENCE_THRESHOLD",
        "OPERATOR_POLICY_PYTHON",
        "OPERATOR_POLICY_CHECKPOINT",
        "OPERATOR_POLICY_CUDA_VISIBLE_DEVICES",
        "DATAVIEWER_AZUREML_REGISTRATION_ENABLED",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "AZUREML_WORKSPACE_NAME",
    ):
        monkeypatch.delenv(var, raising=False)


class TestLoadConfig:
    def test_defaults_when_no_env(self):
        cfg = load_config()
        assert cfg.storage_backend == "local"
        assert cfg.data_path == "./data"
        assert cfg.azure_account_name is None
        assert cfg.dataviewer_release_root == "./data-exports"
        assert cfg.azure_dataset_export_prefix == "exports"
        assert cfg.backend_host == "127.0.0.1"
        assert cfg.backend_port == 8000
        assert cfg.episode_cache_capacity == 32
        assert cfg.episode_cache_max_mb == 100
        assert cfg.detection_models_dir == "./models"
        assert cfg.detection_model_digests == {}
        assert cfg.detection_cache_max_size == 100
        assert cfg.detection_cache_ttl_seconds == 3600
        assert cfg.detection_confidence_threshold == 0.1
        assert cfg.azureml_registration_enabled is False
        assert "http://localhost:5173" in cfg.cors_origins

    def test_storage_backend_lowercased(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STORAGE_BACKEND", "AZURE")
        cfg = load_config()
        assert cfg.storage_backend == "azure"

    def test_data_path_does_not_configure_local_dataset_directory(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DATA_PATH", "/srv/datasets")

        cfg = load_config()

        assert cfg.data_path == "./data"

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

    def test_detection_env_configuration(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DETECTION_MODELS_DIR", "/srv/models")
        monkeypatch.setenv("DETECTION_MODEL_DIGESTS", '{"yolo11n":"' + "a" * 64 + '"}')
        monkeypatch.setenv("DETECTION_CACHE_MAX_SIZE", "12")
        monkeypatch.setenv("DETECTION_CACHE_TTL_SECONDS", "45")
        monkeypatch.setenv("DETECTION_CONFIDENCE_THRESHOLD", "0.35")

        cfg = load_config()

        assert cfg.detection_models_dir == "/srv/models"
        assert cfg.detection_model_digests == {"yolo11n": "a" * 64}
        assert cfg.detection_cache_max_size == 12
        assert cfg.detection_cache_ttl_seconds == 45
        assert cfg.detection_confidence_threshold == 0.35

    def test_operator_policy_configuration_is_loaded_from_trusted_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("OPERATOR_POLICY_PYTHON", "/opt/operator/python")
        monkeypatch.setenv("OPERATOR_POLICY_CHECKPOINT", "/opt/operator/checkpoint")
        monkeypatch.setenv("OPERATOR_POLICY_CUDA_VISIBLE_DEVICES", "2")

        cfg = load_config()

        assert cfg.operator_policy_python == "/opt/operator/python"
        assert cfg.operator_policy_checkpoint == "/opt/operator/checkpoint"
        assert cfg.operator_policy_cuda_visible_devices == "2"

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("DETECTION_CACHE_MAX_SIZE", "0"),
            ("DETECTION_CACHE_TTL_SECONDS", "0"),
            ("DETECTION_CONFIDENCE_THRESHOLD", "-0.1"),
            ("DETECTION_CONFIDENCE_THRESHOLD", "1.1"),
            ("DETECTION_MODEL_DIGESTS", '{"unknown":"' + "a" * 64 + '"}'),
            ("DETECTION_MODEL_DIGESTS", '{"yolo11n":"invalid"}'),
        ],
    )
    def test_invalid_detection_configuration_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        value: str,
    ):
        monkeypatch.setenv(name, value)

        with pytest.raises(ValueError, match=name):
            load_config()

    def test_azure_env_populated(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STORAGE_BACKEND", "azure")
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct")
        monkeypatch.setenv("AZURE_STORAGE_DATASET_CONTAINER", "datasets")
        monkeypatch.setenv("AZURE_STORAGE_ANNOTATION_CONTAINER", "ann")
        monkeypatch.setenv("AZURE_STORAGE_DATASET_EXPORT_PREFIX", "curated/releases")
        monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=token")
        monkeypatch.setenv("DATAVIEWER_RELEASE_ROOT", "/srv/dataviewer-exports")
        cfg = load_config()
        assert cfg.azure_account_name == "acct"
        assert cfg.azure_dataset_container == "datasets"
        assert cfg.azure_annotation_container == "ann"
        assert cfg.azure_dataset_export_prefix == "curated/releases"
        assert cfg.azure_sas_token == "sv=token"
        assert cfg.dataviewer_release_root == "/srv/dataviewer-exports"

    def test_azureml_registration_configuration_is_loaded(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STORAGE_BACKEND", "azure")
        monkeypatch.setenv("DATAVIEWER_AZUREML_REGISTRATION_ENABLED", "true")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "resource-group")
        monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "workspace")

        cfg = load_config()

        assert cfg.azureml_registration_enabled is True
        assert cfg.azure_subscription_id == "subscription"
        assert cfg.azure_resource_group == "resource-group"
        assert cfg.azureml_workspace_name == "workspace"

    @pytest.mark.parametrize(
        "missing_name",
        ["AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "AZUREML_WORKSPACE_NAME"],
    )
    def test_azureml_registration_missing_configuration_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        missing_name: str,
    ):
        monkeypatch.setenv("STORAGE_BACKEND", "azure")
        monkeypatch.setenv("DATAVIEWER_AZUREML_REGISTRATION_ENABLED", "true")
        monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "subscription")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "resource-group")
        monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "workspace")
        monkeypatch.delenv(missing_name)

        with pytest.raises(ValueError, match=missing_name):
            load_config()


class TestCreateReviewRepository:
    def test_local_uses_configured_release_root(self, tmp_path):
        cfg = AppConfig(
            storage_backend="local",
            data_path=str(tmp_path / "datasets"),
            azure_account_name=None,
            azure_dataset_container=None,
            azure_annotation_container=None,
            azure_sas_token=None,
            backend_host="127.0.0.1",
            backend_port=8000,
            dataviewer_release_root=str(tmp_path / "exports"),
        )

        repository = create_review_repository(cfg)

        assert isinstance(repository, LocalReviewRepository)
        assert repository.release_root == (tmp_path / "exports").resolve()

    def test_azure_uses_dataset_container_and_export_prefix(self):
        cfg = AppConfig(
            storage_backend="azure",
            data_path="./data",
            azure_account_name="acct",
            azure_dataset_container="datasets",
            azure_annotation_container=None,
            azure_sas_token="sv=token",
            backend_host="127.0.0.1",
            backend_port=8000,
            azure_dataset_export_prefix="curated/releases",
        )

        repository = create_review_repository(cfg)

        assert isinstance(repository, AzureReviewRepository)


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
