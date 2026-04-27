from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import load_training_module


class _AzureConfigError(Exception):
    pass


class _AzureMLContext:
    def __init__(
        self,
        workspace_name: str = "ws-smoke",
        tracking_uri: str = "azureml://tracking",
        client: object | None = None,
        storage: object | None = None,
    ) -> None:
        self.workspace_name = workspace_name
        self.tracking_uri = tracking_uri
        self.client = client
        self.storage = storage


class _AzureStorageContext:
    def __init__(self, container_name: str = "ckpts") -> None:
        self.container_name = container_name

    def upload_checkpoint(self, local_path: str, model_name: str) -> str:
        return f"{model_name}/blob.chkpt"


def _bootstrap_azure_ml(experiment_name: str | None = None, **_: object) -> _AzureMLContext:
    return _AzureMLContext()


_fake_utils = ModuleType("training.utils")
_fake_utils.AzureConfigError = _AzureConfigError
_fake_utils.AzureMLContext = _AzureMLContext
_fake_utils.bootstrap_azure_ml = _bootstrap_azure_ml
sys.modules.setdefault("training.utils", _fake_utils)

_fake_utils_context = ModuleType("training.utils.context")
_fake_utils_context.AzureStorageContext = _AzureStorageContext
sys.modules.setdefault("training.utils.context", _fake_utils_context)

_fake_launch = ModuleType("training.rl.scripts.launch")
_fake_launch._ensure_dependencies = MagicMock()
sys.modules.setdefault("training.rl.scripts.launch", _fake_launch)

_fake_azure = ModuleType("azure")
_fake_azure_identity = ModuleType("azure.identity")


class _DefaultAzureCredential:
    def get_token(self, scope: str) -> SimpleNamespace:
        return SimpleNamespace(token="tok")


_fake_azure_identity.DefaultAzureCredential = _DefaultAzureCredential
_fake_azure.identity = _fake_azure_identity
sys.modules.setdefault("azure", _fake_azure)
sys.modules.setdefault("azure.identity", _fake_azure_identity)

_fake_mlflow = MagicMock(name="mlflow")


class _RunCtx:
    def __init__(self, run_id: str = "run-123") -> None:
        self.info = SimpleNamespace(run_id=run_id)

    def __enter__(self) -> _RunCtx:
        return self

    def __exit__(self, *_: object) -> None:
        return None


_fake_mlflow.start_run = MagicMock(return_value=_RunCtx())
sys.modules.setdefault("mlflow", _fake_mlflow)


_MOD = load_training_module(
    "training_rl_scripts_smoke_test_azure",
    "training/rl/scripts/smoke_test_azure.py",
)


@pytest.fixture(autouse=True)
def _clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_FEDERATED_TOKEN_FILE"):
        monkeypatch.delenv(var, raising=False)


class TestCheckIdentityEnvVar:
    def test_records_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CLIENT_ID", "abc")
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_CLIENT_ID", "client_id", info)
        assert info == {"client_id": "abc"}

    def test_token_file_missing_path_warns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        missing = tmp_path / "nope.txt"
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(missing))
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_FEDERATED_TOKEN_FILE", "token_file", info)
        assert info["token_file"] == str(missing)

    def test_token_file_existing_path(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        existing = tmp_path / "tok.txt"
        existing.write_text("x")
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(existing))
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_FEDERATED_TOKEN_FILE", "token_file", info)
        assert info["token_file"] == str(existing)

    def test_unset_does_not_record(self) -> None:
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_CLIENT_ID", "client_id", info)
        assert info == {}


class TestValidateWorkloadIdentity:
    def test_all_unset_returns_empty(self) -> None:
        assert _MOD._validate_workload_identity() == {}

    def test_all_set_with_existing_token_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        token = tmp_path / "tok"
        token.write_text("x")
        monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
        monkeypatch.setenv("AZURE_TENANT_ID", "tid")
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(token))
        info = _MOD._validate_workload_identity()
        assert info["client_id"] == "cid"
        assert info["tenant_id"] == "tid"
        assert info["token_file"] == str(token)

    def test_token_file_missing_branch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(tmp_path / "missing"))
        info = _MOD._validate_workload_identity()
        assert "token_file" in info


class TestCredentialAcquisition:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cred = MagicMock()
        cred.get_token.return_value = SimpleNamespace(token="x")
        cred_cls = MagicMock(return_value=cred)
        fake_identity = SimpleNamespace(DefaultAzureCredential=cred_cls)
        monkeypatch.setattr(
            _MOD.importlib, "import_module", lambda name: fake_identity
        )
        assert _MOD._test_credential_acquisition() is True
        cred.get_token.assert_called_once()

    def test_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(_name: str) -> object:
            raise RuntimeError("no module")

        monkeypatch.setattr(_MOD.importlib, "import_module", boom)
        assert _MOD._test_credential_acquisition() is False


class TestWorkspacePermissions:
    def test_success(self) -> None:
        client = MagicMock()
        client.jobs.list.return_value = iter([])
        _MOD._test_workspace_permissions(client, "ws")
        client.workspaces.get.assert_called_once_with("ws")

    def test_failure_raises(self) -> None:
        client = MagicMock()
        client.workspaces.get.side_effect = RuntimeError("denied")
        with pytest.raises(RuntimeError):
            _MOD._test_workspace_permissions(client, "ws")


class TestStorageUpload:
    def test_success(self) -> None:
        storage = MagicMock()
        storage.container_name = "ckpts"
        storage.upload_checkpoint.return_value = "blob"
        _MOD._test_storage_upload(storage)
        storage.upload_checkpoint.assert_called_once()

    def test_upload_failure_raises_and_cleans_up(self) -> None:
        storage = MagicMock()
        storage.container_name = "ckpts"
        storage.upload_checkpoint.side_effect = RuntimeError("nope")
        with pytest.raises(RuntimeError):
            _MOD._test_storage_upload(storage)


class TestParseSingleTag:
    def test_valid(self) -> None:
        assert _MOD._parse_single_tag("k=v") == ("k", "v")

    def test_strips_whitespace(self) -> None:
        assert _MOD._parse_single_tag(" k = v ") == ("k", "v")

    def test_value_with_equals(self) -> None:
        assert _MOD._parse_single_tag("k=a=b") == ("k", "a=b")

    def test_missing_equals_raises(self) -> None:
        with pytest.raises(ValueError, match="KEY=VALUE"):
            _MOD._parse_single_tag("bare")

    def test_empty_key_raises(self) -> None:
        with pytest.raises(ValueError, match="key cannot be empty"):
            _MOD._parse_single_tag("=v")


class TestParseTags:
    def test_multiple(self) -> None:
        assert _MOD._parse_tags(["a=1", "b=2"]) == {"a": "1", "b": "2"}

    def test_empty(self) -> None:
        assert _MOD._parse_tags([]) == {}


class TestParseArgs:
    def test_defaults(self) -> None:
        ns = _MOD._parse_args([])
        assert ns.experiment_name == _MOD._DEFAULT_EXPERIMENT
        assert ns.run_name == _MOD._DEFAULT_RUN_NAME
        assert ns.metric_name == _MOD._DEFAULT_METRIC
        assert ns.tag == []
        assert "successfully" in ns.summary_message

    def test_overrides(self) -> None:
        ns = _MOD._parse_args(
            [
                "--experiment-name",
                "exp",
                "--run-name",
                "rn",
                "--metric-name",
                "ok",
                "--tag",
                "k=v",
                "--tag",
                "j=w",
                "--summary-message",
                "msg",
            ]
        )
        assert ns.experiment_name == "exp"
        assert ns.run_name == "rn"
        assert ns.metric_name == "ok"
        assert ns.tag == ["k=v", "j=w"]
        assert ns.summary_message == "msg"


class TestLoadMlflow:
    def test_returns_imported_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        monkeypatch.setattr(_MOD.importlib, "import_module", lambda name: sentinel)
        assert _MOD._load_mlflow() is sentinel


class TestStartRun:
    def test_records_run_id_and_logs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        mlflow.start_run.return_value = _RunCtx("run-xyz")
        monkeypatch.setattr(_MOD, "_load_mlflow", lambda: mlflow)
        ctx = _AzureMLContext(
            workspace_name="ws", storage=_AzureStorageContext("ckpts")
        )
        args = _MOD._parse_args([])
        run_id = _MOD._start_run(ctx, args, {"u": "v"}, {"client_id": "cid"})
        assert run_id == "run-xyz"
        mlflow.set_tags.assert_called_once()
        mlflow.log_metric.assert_called_once_with(args.metric_name, 1.0)
        mlflow.log_dict.assert_called_once()
        tags = mlflow.set_tags.call_args.args[0]
        assert tags["u"] == "v"
        assert tags["workspace_name"] == "ws"

    def test_no_storage_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        mlflow.start_run.return_value = _RunCtx("r")
        monkeypatch.setattr(_MOD, "_load_mlflow", lambda: mlflow)
        ctx = _AzureMLContext(workspace_name="ws", storage=None)
        args = _MOD._parse_args([])
        _MOD._start_run(ctx, args, {}, {})
        params = {c.args[0]: c.args[1] for c in mlflow.log_param.call_args_list}
        assert params["storage_container"] == "not-configured"


class TestMain:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch, **overrides) -> MagicMock:
        client = MagicMock()
        client.jobs.list.return_value = iter([])
        storage = overrides.get(
            "storage", MagicMock(container_name="ckpts")
        )
        if storage is not None and not hasattr(storage, "upload_checkpoint"):
            storage.upload_checkpoint = MagicMock(return_value="blob")
        ctx = _AzureMLContext(
            workspace_name="ws", client=client, storage=storage
        )
        monkeypatch.setattr(
            _MOD, "bootstrap_azure_ml", lambda **_: ctx
        )
        monkeypatch.setattr(
            _MOD, "_test_credential_acquisition",
            overrides.get("cred_ok", lambda: True),
        )
        monkeypatch.setattr(_MOD, "_start_run", lambda *a, **k: "run-1")
        _fake_launch._ensure_dependencies.reset_mock()
        return ctx

    def test_happy_path_with_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_common(monkeypatch)
        _MOD.main([])
        _fake_launch._ensure_dependencies.assert_called_once()

    def test_happy_path_without_storage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_common(monkeypatch, storage=None)
        _MOD.main([])

    def test_invalid_tag_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_common(monkeypatch)
        with pytest.raises(SystemExit):
            _MOD.main(["--tag", "bare"])

    def test_credential_failure_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_common(monkeypatch, cred_ok=lambda: False)
        with pytest.raises(SystemExit, match="credentials"):
            _MOD.main([])

    def test_bootstrap_config_error_exits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(**_: object) -> _AzureMLContext:
            raise _MOD.AzureConfigError("bad config")

        monkeypatch.setattr(_MOD, "_test_credential_acquisition", lambda: True)
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", boom)
        with pytest.raises(SystemExit, match="bad config"):
            _MOD.main([])
