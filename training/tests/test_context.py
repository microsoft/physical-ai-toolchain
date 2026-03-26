from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

azure_module = types.ModuleType("azure")
azure_ai_module = types.ModuleType("azure.ai")
azure_ai_ml_module = types.ModuleType("azure.ai.ml")
azure_identity_module = types.ModuleType("azure.identity")
mlflow_module = types.ModuleType("mlflow")


class _PlaceholderMLClient:
    pass


class _PlaceholderDefaultAzureCredential:
    pass


azure_ai_ml_module.MLClient = _PlaceholderMLClient
azure_identity_module.DefaultAzureCredential = _PlaceholderDefaultAzureCredential
mlflow_module.set_tracking_uri = lambda *_args, **_kwargs: None
mlflow_module.set_experiment = lambda *_args, **_kwargs: None

azure_module.ai = azure_ai_module
azure_module.identity = azure_identity_module
azure_ai_module.ml = azure_ai_ml_module


def _import_context_module_with_mocked_dependencies() -> types.ModuleType:
    dependency_modules = {
        "azure": azure_module,
        "azure.ai": azure_ai_module,
        "azure.ai.ml": azure_ai_ml_module,
        "azure.identity": azure_identity_module,
        "mlflow": mlflow_module,
    }
    previous_dependencies = {name: sys.modules.get(name) for name in dependency_modules}
    previous_context_module = sys.modules.pop("training.utils.context", None)

    try:
        for name, module in dependency_modules.items():
            sys.modules[name] = module
        return importlib.import_module("training.utils.context")
    finally:
        for name, previous_module in previous_dependencies.items():
            if previous_module is None:
                sys.modules.pop(name, None)
                continue
            sys.modules[name] = previous_module

        if previous_context_module is None:
            sys.modules.pop("training.utils.context", None)
        else:
            sys.modules["training.utils.context"] = previous_context_module


context_module = _import_context_module_with_mocked_dependencies()


def test_bootstrap_azure_ml_success_returns_context(monkeypatch: pytest.MonkeyPatch) -> None:
    credential = object()
    storage = object()
    mlflow_tracking_uri = "https://mlflow.example"
    workspace = types.SimpleNamespace(mlflow_tracking_uri=mlflow_tracking_uri)

    require_env_values = {
        "AZURE_SUBSCRIPTION_ID": "sub-id",
        "AZURE_RESOURCE_GROUP": "rg-name",
        "AZUREML_WORKSPACE_NAME": "ws-name",
    }

    def mock_require_env(name: str, *, error_type: type[Exception] = RuntimeError) -> str:
        assert error_type is context_module.AzureConfigError
        return require_env_values[name]

    set_defaults_mock = Mock()
    build_credential_mock = Mock(return_value=credential)
    set_tracking_uri_mock = Mock()
    set_experiment_mock = Mock()
    build_storage_context_mock = Mock(return_value=storage)

    ml_client_mock = Mock()
    ml_client_mock.workspaces.get.return_value = workspace
    ml_client_constructor = Mock(return_value=ml_client_mock)

    monkeypatch.setattr(context_module, "require_env", mock_require_env)
    monkeypatch.setattr(context_module, "set_env_defaults", set_defaults_mock)
    monkeypatch.setattr(context_module, "_build_credential", build_credential_mock)
    monkeypatch.setattr(context_module, "MLClient", ml_client_constructor)
    monkeypatch.setattr(context_module.mlflow, "set_tracking_uri", set_tracking_uri_mock)
    monkeypatch.setattr(context_module.mlflow, "set_experiment", set_experiment_mock)
    monkeypatch.setattr(
        context_module,
        "_build_storage_context",
        build_storage_context_mock,
    )

    result = context_module.bootstrap_azure_ml(experiment_name="exp-name")

    ml_client_constructor.assert_called_once_with(
        credential=credential,
        subscription_id="sub-id",
        resource_group_name="rg-name",
        workspace_name="ws-name",
    )
    set_defaults_mock.assert_called_once_with(
        {
            "MLFLOW_TRACKING_TOKEN_REFRESH_RETRIES": "3",
            "MLFLOW_HTTP_REQUEST_TIMEOUT": "60",
        }
    )
    set_tracking_uri_mock.assert_called_once_with(mlflow_tracking_uri)
    set_experiment_mock.assert_called_once_with("exp-name")
    build_storage_context_mock.assert_called_once_with(credential)

    assert result.client is ml_client_mock
    assert result.workspace_name == "ws-name"
    assert result.tracking_uri == mlflow_tracking_uri
    assert result.storage is storage


@pytest.mark.parametrize(
    ("setup_patch", "message_fragment"),
    [
        (
            lambda monkeypatch: monkeypatch.setattr(
                context_module,
                "MLClient",
                Mock(side_effect=RuntimeError("dependency unavailable")),
            ),
            "Failed to create Azure ML client",
        ),
        (
            lambda monkeypatch: monkeypatch.setattr(
                context_module.mlflow,
                "set_tracking_uri",
                Mock(side_effect=RuntimeError("mlflow setup failed")),
            ),
            "Failed to configure MLflow tracking",
        ),
    ],
)
def test_bootstrap_azure_ml_setup_failures_raise_azure_config_error(
    monkeypatch: pytest.MonkeyPatch,
    setup_patch,
    message_fragment: str,
) -> None:
    monkeypatch.setattr(context_module, "require_env", lambda name, error_type=RuntimeError: "value")
    monkeypatch.setattr(context_module, "set_env_defaults", Mock())
    monkeypatch.setattr(context_module, "_build_credential", Mock(return_value=object()))

    workspace = types.SimpleNamespace(mlflow_tracking_uri="https://mlflow.example")
    ml_client_mock = Mock()
    ml_client_mock.workspaces.get.return_value = workspace
    monkeypatch.setattr(context_module, "MLClient", Mock(return_value=ml_client_mock))
    monkeypatch.setattr(context_module.mlflow, "set_tracking_uri", Mock())
    monkeypatch.setattr(context_module.mlflow, "set_experiment", Mock())
    monkeypatch.setattr(context_module, "_build_storage_context", Mock(return_value=None))

    setup_patch(monkeypatch)

    with pytest.raises(context_module.AzureConfigError, match=message_fragment):
        context_module.bootstrap_azure_ml(experiment_name="exp-name")


def test_azure_storage_context_upload_file_happy_path(tmp_path: Path) -> None:
    local_file = tmp_path / "artifact.bin"
    local_file.write_bytes(b"data")

    uploaded_payload: dict[str, object] = {}

    def capture_upload(data_stream, *, overwrite: bool) -> None:
        uploaded_payload["content"] = data_stream.read()
        uploaded_payload["overwrite"] = overwrite

    blob_mock = Mock()
    blob_mock.upload_blob.side_effect = capture_upload
    blob_client_mock = Mock()
    blob_client_mock.get_blob_client.return_value = blob_mock
    storage_context = context_module.AzureStorageContext(
        blob_client=blob_client_mock,
        container_name="container-a",
    )

    uploaded_blob_name = storage_context.upload_file(
        local_path=str(local_file),
        blob_name="artifacts/artifact.bin",
    )

    assert uploaded_blob_name == "artifacts/artifact.bin"
    blob_client_mock.get_blob_client.assert_called_once_with(
        container="container-a",
        blob="artifacts/artifact.bin",
    )
    blob_mock.upload_blob.assert_called_once()
    assert uploaded_payload == {"content": b"data", "overwrite": True}


def test_azure_storage_context_upload_file_missing_file_raises(tmp_path: Path) -> None:
    storage_context = context_module.AzureStorageContext(
        blob_client=Mock(),
        container_name="container-a",
    )
    missing_file = tmp_path / "missing.pt"

    with pytest.raises(FileNotFoundError, match=r"destination: container-a/checkpoints/missing\.pt"):
        storage_context.upload_file(
            local_path=str(missing_file),
            blob_name="checkpoints/missing.pt",
        )


def test_upload_files_batch_continues_on_error_and_aggregates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage_context = context_module.AzureStorageContext(blob_client=Mock(), container_name="container-a")

    def mock_upload_file(self, *, local_path: str, blob_name: str) -> str:
        if local_path.endswith("bad.ckpt"):
            raise RuntimeError("upload failed")
        return blob_name

    monkeypatch.setattr(context_module.AzureStorageContext, "upload_file", mock_upload_file)

    files = [
        ("/tmp/good-a.ckpt", "checkpoints/good-a.ckpt"),
        ("/tmp/bad.ckpt", "checkpoints/bad.ckpt"),
        ("/tmp/good-b.ckpt", "checkpoints/good-b.ckpt"),
    ]

    uploaded = storage_context.upload_files_batch(files)

    assert set(uploaded) == {"checkpoints/good-a.ckpt", "checkpoints/good-b.ckpt"}
    output = capsys.readouterr().out
    assert "Failed to upload 1 files" in output
    assert "/tmp/bad.ckpt: upload failed" in output


def test_upload_checkpoint_wires_blob_name_and_propagates_upload_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_context = context_module.AzureStorageContext(blob_client=Mock(), container_name="container-a")
    upload_file_mock = Mock(return_value="checkpoints/model-a/20260219_101112_step_42.pt")

    def patched_upload_file(self, *, local_path: str, blob_name: str) -> str:
        return upload_file_mock(local_path=local_path, blob_name=blob_name)

    monkeypatch.setattr(context_module.AzureStorageContext, "upload_file", patched_upload_file)

    fake_now = Mock()
    fake_now.strftime.return_value = "20260219_101112"
    fake_datetime = Mock()
    fake_datetime.utcnow.return_value = fake_now
    monkeypatch.setattr(context_module, "datetime", fake_datetime)

    uploaded_blob_name = storage_context.upload_checkpoint(
        local_path="/tmp/model.pt",
        model_name="model-a",
        step=42,
    )

    assert uploaded_blob_name == "checkpoints/model-a/20260219_101112_step_42.pt"
    upload_file_mock.assert_called_once_with(
        local_path="/tmp/model.pt",
        blob_name="checkpoints/model-a/20260219_101112_step_42.pt",
    )

    upload_file_mock.side_effect = RuntimeError("blob upload failed")
    with pytest.raises(RuntimeError, match="blob upload failed"):
        storage_context.upload_checkpoint(
            local_path="/tmp/model.pt",
            model_name="model-a",
            step=42,
        )
