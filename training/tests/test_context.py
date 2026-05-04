from __future__ import annotations

import importlib
import os
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


def test_upload_files_batch_truncates_failure_summary_above_five(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage_context = context_module.AzureStorageContext(blob_client=Mock(), container_name="container-a")

    def mock_upload_file(self, *, local_path: str, blob_name: str) -> str:
        raise RuntimeError(f"failure for {local_path}")

    monkeypatch.setattr(context_module.AzureStorageContext, "upload_file", mock_upload_file)

    files = [(f"/tmp/file-{i}.ckpt", f"checkpoints/file-{i}.ckpt") for i in range(7)]

    uploaded = storage_context.upload_files_batch(files)

    assert uploaded == []
    output = capsys.readouterr().out
    assert "Failed to upload 7 files" in output
    assert "... and 2 more" in output


def test_upload_files_batch_empty_returns_empty() -> None:
    storage_context = context_module.AzureStorageContext(blob_client=Mock(), container_name="container-a")
    assert storage_context.upload_files_batch([]) == []


def test_upload_files_batch_all_success_skips_failure_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    storage_context = context_module.AzureStorageContext(blob_client=Mock(), container_name="container-a")

    def mock_upload_file(self, *, local_path: str, blob_name: str) -> str:
        return blob_name

    monkeypatch.setattr(context_module.AzureStorageContext, "upload_file", mock_upload_file)

    files = [(f"/tmp/file-{i}.ckpt", f"checkpoints/file-{i}.ckpt") for i in range(3)]
    uploaded = storage_context.upload_files_batch(files)

    assert set(uploaded) == {"checkpoints/file-0.ckpt", "checkpoints/file-1.ckpt", "checkpoints/file-2.ckpt"}
    assert "Failed to upload" not in capsys.readouterr().out


def _install_storage_blob_modules(
    *,
    blob_service_client: object,
    azure_error_cls: type[Exception],
    resource_exists_cls: type[Exception],
) -> dict[str, types.ModuleType | None]:
    azure_storage_module = types.ModuleType("azure.storage")
    azure_storage_blob_module = types.ModuleType("azure.storage.blob")
    azure_core_module = types.ModuleType("azure.core")
    azure_core_exceptions_module = types.ModuleType("azure.core.exceptions")

    azure_storage_blob_module.BlobServiceClient = blob_service_client
    azure_core_exceptions_module.AzureError = azure_error_cls
    azure_core_exceptions_module.ResourceExistsError = resource_exists_cls

    modules = {
        "azure.storage": azure_storage_module,
        "azure.storage.blob": azure_storage_blob_module,
        "azure.core": azure_core_module,
        "azure.core.exceptions": azure_core_exceptions_module,
    }
    previous = {name: sys.modules.get(name) for name in modules}
    for name, module in modules.items():
        sys.modules[name] = module
    return previous


def _restore_modules(previous: dict[str, types.ModuleType | None]) -> None:
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def test_build_storage_context_returns_none_when_account_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    assert context_module._build_storage_context(credential=object()) is None


def test_build_storage_context_creates_container_and_returns_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct1")
    monkeypatch.delenv("AZURE_STORAGE_CONTAINER_NAME", raising=False)

    container_client = Mock()
    blob_client_instance = Mock()
    blob_client_instance.get_container_client.return_value = container_client
    blob_service_client = Mock(return_value=blob_client_instance)

    class _ResourceExistsError(Exception):
        pass

    class _AzureError(Exception):
        pass

    previous = _install_storage_blob_modules(
        blob_service_client=blob_service_client,
        azure_error_cls=_AzureError,
        resource_exists_cls=_ResourceExistsError,
    )
    try:
        result = context_module._build_storage_context(credential="cred")
    finally:
        _restore_modules(previous)

    assert isinstance(result, context_module.AzureStorageContext)
    assert result.container_name == "isaaclab-training-logs"
    blob_service_client.assert_called_once_with(
        account_url="https://acct1.blob.core.windows.net/",
        credential="cred",
    )
    container_client.create_container.assert_called_once_with()


def test_build_storage_context_swallows_resource_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct1")
    monkeypatch.setenv("AZURE_STORAGE_CONTAINER_NAME", "custom-container")

    class _ResourceExistsError(Exception):
        pass

    class _AzureError(Exception):
        pass

    container_client = Mock()
    container_client.create_container.side_effect = _ResourceExistsError("already exists")
    blob_client_instance = Mock()
    blob_client_instance.get_container_client.return_value = container_client
    blob_service_client = Mock(return_value=blob_client_instance)

    previous = _install_storage_blob_modules(
        blob_service_client=blob_service_client,
        azure_error_cls=_AzureError,
        resource_exists_cls=_ResourceExistsError,
    )
    try:
        result = context_module._build_storage_context(credential="cred")
    finally:
        _restore_modules(previous)

    assert result is not None
    assert result.container_name == "custom-container"


def test_build_storage_context_raises_azure_config_error_on_azure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "acct1")

    class _ResourceExistsError(Exception):
        pass

    class _AzureError(Exception):
        pass

    blob_service_client = Mock(side_effect=_AzureError("boom"))

    previous = _install_storage_blob_modules(
        blob_service_client=blob_service_client,
        azure_error_cls=_AzureError,
        resource_exists_cls=_ResourceExistsError,
    )
    try:
        with pytest.raises(context_module.AzureConfigError, match="Failed to initialize Azure Storage container"):
            context_module._build_storage_context(credential="cred")
    finally:
        _restore_modules(previous)


def test_build_credential_uses_default_identity_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.setenv("DEFAULT_IDENTITY_CLIENT_ID", "fallback-client-id")
    monkeypatch.delenv("AZURE_AUTHORITY_HOST", raising=False)
    monkeypatch.setenv("AZURE_EXCLUDE_MANAGED_IDENTITY", "false")

    captured: dict[str, object] = {}

    class _StubCredential:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(context_module, "DefaultAzureCredential", _StubCredential)

    credential = context_module._build_credential()

    assert isinstance(credential, _StubCredential)
    assert captured["managed_identity_client_id"] == "fallback-client-id"
    assert captured["exclude_managed_identity_credential"] is False
    assert captured["authority"] is None
    assert os.environ["AZURE_CLIENT_ID"] == "fallback-client-id"


def test_build_credential_honors_exclude_flag_and_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_CLIENT_ID", "explicit-id")
    monkeypatch.delenv("DEFAULT_IDENTITY_CLIENT_ID", raising=False)
    monkeypatch.setenv("AZURE_AUTHORITY_HOST", "https://login.example/")
    monkeypatch.setenv("AZURE_EXCLUDE_MANAGED_IDENTITY", "TRUE")

    captured: dict[str, object] = {}

    class _StubCredential:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(context_module, "DefaultAzureCredential", _StubCredential)

    context_module._build_credential()

    assert captured["managed_identity_client_id"] == "explicit-id"
    assert captured["authority"] == "https://login.example/"
    assert captured["exclude_managed_identity_credential"] is True


def test_bootstrap_azure_ml_workspace_get_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "require_env", lambda name, error_type=RuntimeError: "value")
    monkeypatch.setattr(context_module, "set_env_defaults", Mock())
    monkeypatch.setattr(context_module, "_build_credential", Mock(return_value=object()))

    ml_client_mock = Mock()
    ml_client_mock.workspaces.get.side_effect = RuntimeError("workspace unreachable")
    monkeypatch.setattr(context_module, "MLClient", Mock(return_value=ml_client_mock))

    with pytest.raises(context_module.AzureConfigError, match="Failed to access workspace"):
        context_module.bootstrap_azure_ml(experiment_name="exp-name")


def test_bootstrap_azure_ml_missing_tracking_uri_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "require_env", lambda name, error_type=RuntimeError: "value")
    monkeypatch.setattr(context_module, "set_env_defaults", Mock())
    monkeypatch.setattr(context_module, "_build_credential", Mock(return_value=object()))

    workspace = types.SimpleNamespace(mlflow_tracking_uri=None)
    ml_client_mock = Mock()
    ml_client_mock.workspaces.get.return_value = workspace
    monkeypatch.setattr(context_module, "MLClient", Mock(return_value=ml_client_mock))

    with pytest.raises(context_module.AzureConfigError, match="does not expose an MLflow tracking URI"):
        context_module.bootstrap_azure_ml(experiment_name="exp-name")


def test_bootstrap_azure_ml_skips_set_experiment_when_name_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "require_env", lambda name, error_type=RuntimeError: "value")
    monkeypatch.setattr(context_module, "set_env_defaults", Mock())
    monkeypatch.setattr(context_module, "_build_credential", Mock(return_value=object()))

    workspace = types.SimpleNamespace(mlflow_tracking_uri="https://mlflow.example")
    ml_client_mock = Mock()
    ml_client_mock.workspaces.get.return_value = workspace
    monkeypatch.setattr(context_module, "MLClient", Mock(return_value=ml_client_mock))

    set_tracking_uri_mock = Mock()
    set_experiment_mock = Mock()
    monkeypatch.setattr(context_module.mlflow, "set_tracking_uri", set_tracking_uri_mock)
    monkeypatch.setattr(context_module.mlflow, "set_experiment", set_experiment_mock)
    monkeypatch.setattr(context_module, "_build_storage_context", Mock(return_value=None))

    result = context_module.bootstrap_azure_ml(experiment_name="")

    assert result.tracking_uri == "https://mlflow.example"
    set_tracking_uri_mock.assert_called_once_with("https://mlflow.example")
    set_experiment_mock.assert_not_called()
