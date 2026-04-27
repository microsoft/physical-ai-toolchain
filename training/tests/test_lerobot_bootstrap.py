"""Tests for training/il/scripts/lerobot/bootstrap.py."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import load_training_module

_MOD = load_training_module(
    "training_il_scripts_lerobot_bootstrap",
    "training/il/scripts/lerobot/bootstrap.py",
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
    """Inject mlflow, azure.ai.ml, and azure.identity as fake modules."""
    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = MagicMock()
    mlflow.set_experiment = MagicMock()
    mlflow.autolog = MagicMock()

    azure_pkg = ModuleType("azure")
    azure_ai = ModuleType("azure.ai")
    azure_ai_ml = ModuleType("azure.ai.ml")
    azure_identity = ModuleType("azure.identity")

    workspace_obj = SimpleNamespace(mlflow_tracking_uri="azureml://tracking")
    workspaces_attr = SimpleNamespace(get=MagicMock(return_value=workspace_obj))
    client_instance = SimpleNamespace(workspaces=workspaces_attr)
    ml_client_cls = MagicMock(return_value=client_instance)
    credential_cls = MagicMock(return_value="cred")

    azure_ai_ml.MLClient = ml_client_cls
    azure_identity.DefaultAzureCredential = credential_cls

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "azure", azure_pkg)
    monkeypatch.setitem(sys.modules, "azure.ai", azure_ai)
    monkeypatch.setitem(sys.modules, "azure.ai.ml", azure_ai_ml)
    monkeypatch.setitem(sys.modules, "azure.identity", azure_identity)

    return SimpleNamespace(
        mlflow=mlflow,
        ml_client_cls=ml_client_cls,
        credential_cls=credential_cls,
        workspaces=workspaces_attr,
        workspace=workspace_obj,
    )


class TestBootstrapMlflow:
    def test_success_default_experiment_name(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        config_path = tmp_path / "mlflow_config.env"
        monkeypatch.setattr(_MOD, "Path", lambda *_a, **_k: config_path)

        result = _MOD.bootstrap_mlflow(policy_type="diffusion", job_name="job42")

        assert result.tracking_uri == "azureml://tracking"
        assert result.experiment_name == "lerobot-diffusion-job42"
        fake_azure_modules.mlflow.set_tracking_uri.assert_called_once_with("azureml://tracking")
        fake_azure_modules.mlflow.set_experiment.assert_called_once_with("lerobot-diffusion-job42")
        fake_azure_modules.mlflow.autolog.assert_called_once()
        assert "MLFLOW_TRACKING_URI=azureml://tracking" in config_path.read_text()
        assert "MLFLOW_EXPERIMENT_NAME=lerobot-diffusion-job42" in config_path.read_text()

    def test_success_explicit_experiment_name(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setattr(_MOD, "Path", lambda *_a, **_k: tmp_path / "cfg.env")

        result = _MOD.bootstrap_mlflow(experiment_name="custom-exp")

        assert result.experiment_name == "custom-exp"
        fake_azure_modules.mlflow.set_experiment.assert_called_once_with("custom-exp")

    def test_import_error_exits(self, azure_env, monkeypatch):
        # Ensure import of mlflow fails
        monkeypatch.setitem(sys.modules, "mlflow", None)
        with pytest.raises(SystemExit) as exc_info:
            _MOD.bootstrap_mlflow()
        assert exc_info.value.code == 1

    def test_missing_env_vars_exits(self, fake_azure_modules, monkeypatch):
        monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
        monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
        monkeypatch.delenv("AZUREML_WORKSPACE_NAME", raising=False)
        with pytest.raises(SystemExit) as exc_info:
            _MOD.bootstrap_mlflow()
        assert exc_info.value.code == 1

    def test_missing_tracking_uri_exits(self, azure_env, fake_azure_modules):
        fake_azure_modules.workspace.mlflow_tracking_uri = ""
        with pytest.raises(SystemExit) as exc_info:
            _MOD.bootstrap_mlflow()
        assert exc_info.value.code == 1

    def test_azure_failure_exits(self, azure_env, fake_azure_modules):
        fake_azure_modules.workspaces.get.side_effect = RuntimeError("boom")
        with pytest.raises(SystemExit) as exc_info:
            _MOD.bootstrap_mlflow()
        assert exc_info.value.code == 1

    def test_uses_optional_credential_env(self, azure_env, fake_azure_modules, tmp_path, monkeypatch):
        monkeypatch.setenv("AZURE_CLIENT_ID", "client-xyz")
        monkeypatch.setenv("AZURE_AUTHORITY_HOST", "https://login.example")
        monkeypatch.setattr(_MOD, "Path", lambda *_a, **_k: tmp_path / "cfg.env")

        _MOD.bootstrap_mlflow()

        fake_azure_modules.credential_cls.assert_called_once_with(
            managed_identity_client_id="client-xyz",
            authority="https://login.example",
        )


class TestAuthenticateHuggingface:
    def test_no_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        assert _MOD.authenticate_huggingface() is None

    def test_success_returns_username(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf-secret")
        hf_module = ModuleType("huggingface_hub")
        login_mock = MagicMock()
        whoami_mock = MagicMock(return_value={"name": "alice"})
        hf_module.login = login_mock
        hf_module.whoami = whoami_mock
        monkeypatch.setitem(sys.modules, "huggingface_hub", hf_module)

        result = _MOD.authenticate_huggingface()

        assert result == "alice"
        login_mock.assert_called_once_with(token="hf-secret", add_to_git_credential=False)
        whoami_mock.assert_called_once()

    def test_failure_returns_none(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf-secret")
        hf_module = ModuleType("huggingface_hub")
        hf_module.login = MagicMock(side_effect=RuntimeError("nope"))
        hf_module.whoami = MagicMock()
        monkeypatch.setitem(sys.modules, "huggingface_hub", hf_module)

        assert _MOD.authenticate_huggingface() is None

    def test_username_missing_in_response(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf-secret")
        hf_module = ModuleType("huggingface_hub")
        hf_module.login = MagicMock()
        hf_module.whoami = MagicMock(return_value={})
        monkeypatch.setitem(sys.modules, "huggingface_hub", hf_module)

        assert _MOD.authenticate_huggingface() == ""
