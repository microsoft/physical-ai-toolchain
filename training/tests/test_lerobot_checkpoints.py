"""Tests for training/il/scripts/lerobot/checkpoints.py."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import load_training_module

_MOD = load_training_module(
    "training_il_scripts_lerobot_checkpoints",
    "training/il/scripts/lerobot/checkpoints.py",
)


@pytest.fixture
def azure_env(monkeypatch):
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", "sub-1")
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg-1")
    monkeypatch.setenv("AZUREML_WORKSPACE_NAME", "ws-1")
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_AUTHORITY_HOST", raising=False)


@pytest.fixture
def fake_azure_modules(monkeypatch):
    """Stub azure.ai.ml + azure.identity + mlflow."""
    mlflow = ModuleType("mlflow")
    mlflow.log_artifacts = MagicMock()
    mlflow.set_tag = MagicMock()

    azure_pkg = ModuleType("azure")
    azure_ai = ModuleType("azure.ai")
    azure_ai_ml = ModuleType("azure.ai.ml")
    azure_constants = ModuleType("azure.ai.ml.constants")
    azure_entities = ModuleType("azure.ai.ml.entities")
    azure_identity = ModuleType("azure.identity")

    registered = SimpleNamespace(name="model-x", version="3")
    models_attr = SimpleNamespace(create_or_update=MagicMock(return_value=registered))
    client_instance = SimpleNamespace(models=models_attr)
    ml_client_cls = MagicMock(return_value=client_instance)
    credential_cls = MagicMock(return_value="cred")

    azure_ai_ml.MLClient = ml_client_cls
    azure_constants.AssetTypes = SimpleNamespace(CUSTOM_MODEL="custom_model")
    model_cls = MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw))
    azure_entities.Model = model_cls
    azure_identity.DefaultAzureCredential = credential_cls

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.ai", azure_ai)
    monkeypatch.setitem(sys.modules, "azure.ai.ml", azure_ai_ml)
    monkeypatch.setitem(sys.modules, "azure.ai.ml.constants", azure_constants)
    monkeypatch.setitem(sys.modules, "azure.ai.ml.entities", azure_entities)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)

    return SimpleNamespace(
        mlflow=mlflow,
        ml_client_cls=ml_client_cls,
        credential_cls=credential_cls,
        models=models_attr,
        registered=registered,
        model_cls=model_cls,
    )


class TestGetAmlClient:
    def test_returns_none_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
        monkeypatch.delenv("AZUREML_WORKSPACE_NAME", raising=False)
        assert _MOD._get_aml_client() is None

    def test_creates_client(self, azure_env, fake_azure_modules):
        client = _MOD._get_aml_client()
        assert client is not None
        fake_azure_modules.ml_client_cls.assert_called_once()
        kwargs = fake_azure_modules.ml_client_cls.call_args.kwargs
        assert kwargs["subscription_id"] == "sub-1"
        assert kwargs["resource_group_name"] == "rg-1"
        assert kwargs["workspace_name"] == "ws-1"

    def test_handles_exception(self, azure_env, fake_azure_modules):
        fake_azure_modules.ml_client_cls.side_effect = RuntimeError("boom")
        assert _MOD._get_aml_client() is None


class TestRegisterModelViaAml:
    def test_returns_false_when_client_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        result = _MOD._register_model_via_aml(tmp_path, "ckpt-001")
        assert result is False

    def test_registers_successfully(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        monkeypatch.setenv("JOB_NAME", "job-a")
        monkeypatch.setenv("POLICY_TYPE", "act")
        monkeypatch.setenv("REGISTER_CHECKPOINT", "my_model_name")
        result = _MOD._register_model_via_aml(tmp_path, "ckpt-001", source="osmo")
        assert result is True
        kwargs = fake_azure_modules.model_cls.call_args.kwargs
        assert kwargs["name"] == "my-model-name"
        assert kwargs["tags"]["source"] == "osmo"
        assert kwargs["tags"]["checkpoint"] == "ckpt-001"

    def test_falls_back_to_job_name_when_no_register_env(self, azure_env, fake_azure_modules, monkeypatch, tmp_path):
        monkeypatch.setenv("JOB_NAME", "fallback_job")
        monkeypatch.delenv("REGISTER_CHECKPOINT", raising=False)
        _MOD._register_model_via_aml(tmp_path, "ckpt-002")
        kwargs = fake_azure_modules.model_cls.call_args.kwargs
        assert kwargs["name"] == "fallback-job"

    def test_handles_registration_exception(self, azure_env, fake_azure_modules, tmp_path):
        fake_azure_modules.models.create_or_update.side_effect = RuntimeError("api fail")
        assert _MOD._register_model_via_aml(tmp_path, "ckpt-003") is False


class TestUploadNewCheckpoints:
    def test_returns_when_dir_missing(self, fake_azure_modules, tmp_path):
        uploaded: set[str] = set()
        _MOD.upload_new_checkpoints(MagicMock(), tmp_path, uploaded)
        assert uploaded == set()

    def test_uploads_new_checkpoint(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        ckpt = tmp_path / "checkpoints" / "005000" / "pretrained_model"
        ckpt.mkdir(parents=True)
        (ckpt / "model.safetensors").write_bytes(b"x")
        uploaded: set[str] = set()
        _MOD.upload_new_checkpoints(MagicMock(), tmp_path, uploaded, source="src")
        assert "005000" in uploaded
        fake_azure_modules.mlflow.log_artifacts.assert_called_once()
        fake_azure_modules.mlflow.set_tag.assert_called_once()
        fake_azure_modules.models.create_or_update.assert_called_once()

    def test_skips_already_uploaded(self, azure_env, fake_azure_modules, tmp_path):
        ckpt = tmp_path / "checkpoints" / "005000" / "pretrained_model"
        ckpt.mkdir(parents=True)
        (ckpt / "model.safetensors").write_bytes(b"x")
        uploaded = {"005000"}
        _MOD.upload_new_checkpoints(MagicMock(), tmp_path, uploaded)
        fake_azure_modules.mlflow.log_artifacts.assert_not_called()

    def test_skips_when_safetensors_missing(self, azure_env, fake_azure_modules, tmp_path):
        ckpt = tmp_path / "checkpoints" / "005000" / "pretrained_model"
        ckpt.mkdir(parents=True)
        uploaded: set[str] = set()
        _MOD.upload_new_checkpoints(MagicMock(), tmp_path, uploaded)
        assert uploaded == set()
        fake_azure_modules.mlflow.log_artifacts.assert_not_called()

    def test_handles_log_artifacts_exception(self, azure_env, fake_azure_modules, tmp_path):
        ckpt = tmp_path / "checkpoints" / "005000" / "pretrained_model"
        ckpt.mkdir(parents=True)
        (ckpt / "model.safetensors").write_bytes(b"x")
        fake_azure_modules.mlflow.log_artifacts.side_effect = RuntimeError("nope")
        uploaded: set[str] = set()
        _MOD.upload_new_checkpoints(MagicMock(), tmp_path, uploaded)
        # Still adds to uploaded and attempts AML registration
        assert "005000" in uploaded
        fake_azure_modules.models.create_or_update.assert_called_once()


class TestRegisterFinalCheckpoint:
    def test_no_register_env_returns_success(self, monkeypatch):
        monkeypatch.delenv("REGISTER_CHECKPOINT", raising=False)
        assert _MOD.register_final_checkpoint() == _MOD.EXIT_SUCCESS

    def test_no_checkpoints_no_pretrained_returns_success(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("REGISTER_CHECKPOINT", "my-model")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        assert _MOD.register_final_checkpoint() == _MOD.EXIT_SUCCESS
        fake_azure_modules.models.create_or_update.assert_not_called()

    def test_uses_pretrained_when_no_checkpoints(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("REGISTER_CHECKPOINT", "my-model")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        (tmp_path / "pretrained_model").mkdir()
        assert _MOD.register_final_checkpoint() == _MOD.EXIT_SUCCESS
        fake_azure_modules.models.create_or_update.assert_called_once()

    def test_uses_latest_checkpoint(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("REGISTER_CHECKPOINT", "my-model")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        ck1 = tmp_path / "checkpoints" / "001000" / "pretrained_model"
        ck1.mkdir(parents=True)
        ck2 = tmp_path / "checkpoints" / "002000" / "pretrained_model"
        ck2.mkdir(parents=True)
        # Make ck2 newer
        import os
        os.utime(ck2.parent, (2000, 2000))
        os.utime(ck1.parent, (1000, 1000))
        assert _MOD.register_final_checkpoint() == _MOD.EXIT_SUCCESS
        fake_azure_modules.models.create_or_update.assert_called_once()

    def test_falls_back_when_pretrained_missing(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("REGISTER_CHECKPOINT", "my-model")
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        (tmp_path / "checkpoints" / "001000").mkdir(parents=True)
        assert _MOD.register_final_checkpoint() == _MOD.EXIT_SUCCESS
        fake_azure_modules.models.create_or_update.assert_called_once()


class TestUploadCheckpointsToAzureMl:
    def test_no_checkpoints_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        assert _MOD.upload_checkpoints_to_azure_ml() == _MOD.EXIT_SUCCESS

    def test_uploads_valid_checkpoints(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        # Valid: pretrained_model with model.safetensors
        ck1 = tmp_path / "checkpoints" / "001000" / "pretrained_model"
        ck1.mkdir(parents=True)
        (ck1 / "model.safetensors").write_bytes(b"x")
        # Valid: model.safetensors directly in ckpt dir (no pretrained subdir)
        ck2 = tmp_path / "checkpoints" / "002000"
        ck2.mkdir(parents=True)
        (ck2 / "model.safetensors").write_bytes(b"x")
        # Invalid: no model.safetensors
        ck3 = tmp_path / "checkpoints" / "003000"
        ck3.mkdir(parents=True)
        # Non-dir entry
        (tmp_path / "checkpoints" / "stray.txt").write_text("hi")

        assert _MOD.upload_checkpoints_to_azure_ml() == _MOD.EXIT_SUCCESS
        assert fake_azure_modules.models.create_or_update.call_count == 2

    def test_no_valid_checkpoints(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path))
        (tmp_path / "checkpoints" / "001000").mkdir(parents=True)  # no safetensors
        assert _MOD.upload_checkpoints_to_azure_ml() == _MOD.EXIT_SUCCESS
        fake_azure_modules.models.create_or_update.assert_not_called()
