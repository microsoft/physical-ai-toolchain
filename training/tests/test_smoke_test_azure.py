from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
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
_fake_utils.__path__ = [str(Path(__file__).resolve().parents[1] / "utils")]
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
    for var in (
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_FEDERATED_TOKEN_FILE",
        *_MOD._FORBIDDEN_STATIC_SECRET_VARS,
    ):
        monkeypatch.delenv(var, raising=False)


class TestCheckIdentityEnvVar:
    def test_records_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CLIENT_ID", "abc")
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_CLIENT_ID", "client_id", info)
        assert info == {"client_id": "abc"}

    def test_token_file_missing_path_warns(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        missing = tmp_path / "nope.txt"
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(missing))
        info: dict[str, str] = {}
        _MOD._check_identity_env_var("AZURE_FEDERATED_TOKEN_FILE", "token_file", info)
        assert info["token_file"] == str(missing)

    def test_token_file_existing_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
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

    def test_all_set_with_existing_token_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        token = tmp_path / "tok"
        token.write_text("x")
        monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
        monkeypatch.setenv("AZURE_TENANT_ID", "tid")
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(token))
        info = _MOD._validate_workload_identity()
        assert info["client_id"] == "cid"
        assert info["tenant_id"] == "tid"
        assert info["token_file"] == str(token)

    def test_token_file_missing_branch(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(tmp_path / "missing"))
        info = _MOD._validate_workload_identity()
        assert "token_file" in info


class TestCredentialAcquisition:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cred = MagicMock()
        cred.get_token.return_value = SimpleNamespace(token="x")
        cred_cls = MagicMock(return_value=cred)
        fake_identity = SimpleNamespace(DefaultAzureCredential=cred_cls)
        monkeypatch.setattr(_MOD.importlib, "import_module", lambda name: fake_identity)
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
        ctx = _AzureMLContext(workspace_name="ws", storage=_AzureStorageContext("ckpts"))
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

    def test_log_dict_failure_still_returns_run_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        mlflow.start_run.return_value = _RunCtx("run-ok")
        mlflow.log_dict.side_effect = Exception("artifact upload failed")
        monkeypatch.setattr(_MOD, "_load_mlflow", lambda: mlflow)
        ctx = _AzureMLContext(workspace_name="ws", storage=_AzureStorageContext("ckpts"))
        args = _MOD._parse_args([])
        run_id = _MOD._start_run(ctx, args, {}, {})
        assert run_id == "run-ok"
        mlflow.log_metric.assert_called_once()


class TestMain:
    def _patch_common(self, monkeypatch: pytest.MonkeyPatch, **overrides) -> MagicMock:
        client = MagicMock()
        client.jobs.list.return_value = iter([])
        storage = overrides.get("storage", MagicMock(container_name="ckpts"))
        if storage is not None and not hasattr(storage, "upload_checkpoint"):
            storage.upload_checkpoint = MagicMock(return_value="blob")
        ctx = _AzureMLContext(workspace_name="ws", client=client, storage=storage)
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", lambda **_: ctx)
        monkeypatch.setattr(
            _MOD,
            "_test_credential_acquisition",
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

    def test_bootstrap_config_error_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**_: object) -> _AzureMLContext:
            raise _MOD.AzureConfigError("bad config")

        monkeypatch.setattr(_MOD, "_test_credential_acquisition", lambda **_: True)
        monkeypatch.setattr(_MOD, "bootstrap_azure_ml", boom)
        with pytest.raises(SystemExit, match="bad config"):
            _MOD.main([])

    def test_forbid_static_secrets_exits_when_secret_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_common(monkeypatch)
        monkeypatch.setenv("AZURE_CLIENT_SECRET", "do-not-print")
        with pytest.raises(SystemExit, match="AZURE_CLIENT_SECRET") as exc_info:
            _MOD.main(["--forbid-static-secrets"])
        assert "do-not-print" not in str(exc_info.value)

    def test_require_workload_identity_checks_nonempty_token_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_common(monkeypatch)
        with pytest.raises(SystemExit, match="Workload identity required"):
            _MOD.main(["--require-workload-identity"])

        empty_tok = tmp_path / "empty-token"
        empty_tok.write_text("")
        monkeypatch.setenv("AZURE_CLIENT_ID", "cid")
        monkeypatch.setenv("AZURE_TENANT_ID", "tid")
        monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(empty_tok))
        with pytest.raises(SystemExit, match="empty"):
            _MOD.main(["--require-workload-identity"])

        empty_tok.write_text("projected-jwt")
        _MOD.main(["--require-workload-identity"])

    def test_require_artifact_upload_raises_on_log_dict_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mlflow = MagicMock()
        mlflow.start_run.return_value = _RunCtx("run-fail")
        mlflow.log_dict.side_effect = RuntimeError("blob 403")
        monkeypatch.setattr(_MOD, "_load_mlflow", lambda: mlflow)
        ctx = _AzureMLContext(workspace_name="ws", storage=None)
        args = _MOD._parse_args(["--require-artifact-upload"])
        with pytest.raises(RuntimeError, match="blob 403"):
            _MOD._start_run(ctx, args, {}, {})

    def test_verify_all_scopes_and_jwt_claims_never_leak_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = {"tid": "tenant-1", "oid": "obj-1", "appid": "app-1", "exp": 1893456000}
        b64_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"eyJhbGciOiJSUzI1NiJ9.{b64_payload}.secret-signature"

        cred = MagicMock()
        cred.get_token.return_value = SimpleNamespace(token=fake_jwt)
        fake_identity = SimpleNamespace(DefaultAzureCredential=MagicMock(return_value=cred))
        monkeypatch.setattr(_MOD.importlib, "import_module", lambda name: fake_identity)

        diag: dict[str, object] = {}
        assert _MOD._test_credential_acquisition(verify_all_scopes=True, token_diagnostics=diag) is True
        assert cred.get_token.call_count == 2
        assert diag["oid"] == "obj-1"
        assert diag["tid"] == "tenant-1"
        assert "secret-signature" not in json.dumps(diag)

    def test_network_truth_table_enforces_private_link_on_data_plane(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "smoke01")

        def _fake_probe(role: str, host: str, *, timeout_s: float = 10.0) -> dict[str, object]:
            is_priv = host.endswith((".api.azureml.ms", ".blob.core.windows.net"))
            return {
                "role": role,
                "host": host,
                "resolved_ips": ["10.240.1.15"] if is_priv else ["20.190.159.0"],
                "private_link_ip": is_priv,
                "via_proxy": False,
                "tls_reachable": True,
                "tls_ms": 4.2,
                "error": None,
            }

        monkeypatch.setattr(_MOD, "_probe_single_endpoint", _fake_probe)
        routes = _MOD._probe_network_truth_table(
            tracking_uri="azureml://eastus.api.azureml.ms/mlflow/v1.0/subscriptions/s",
            dataset_blob_url="https://datasets01.blob.core.windows.net/datasets/so101/v1",
            require_private_link=True,
        )
        assert len(routes) == 5

        def _public_leak_probe(role: str, host: str, *, timeout_s: float = 10.0) -> dict[str, object]:
            return {
                "role": role,
                "host": host,
                "resolved_ips": ["52.239.1.10"],
                "private_link_ip": False,
                "via_proxy": False,
                "tls_reachable": True,
                "tls_ms": 18.0,
                "error": None,
            }

        monkeypatch.setattr(_MOD, "_probe_single_endpoint", _public_leak_probe)
        with pytest.raises(SystemExit, match="Private Link required"):
            _MOD._probe_network_truth_table(
                tracking_uri="azureml://eastus.api.azureml.ms/mlflow/v1.0/subscriptions/s",
                require_private_link=True,
            )

    def test_dataset_hydration_preflight_resolves_asset_and_blocks_sas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_client = SimpleNamespace(
            data=SimpleNamespace(
                get=lambda name, version: SimpleNamespace(
                    path="https://datasets01.blob.core.windows.net/datasets/releases/so101/v1",
                    properties={"manifest_sha256": "abc123"},
                )
            )
        )
        with pytest.raises(SystemExit, match="SAS query string forbidden"):
            _MOD._probe_dataset_hydration_source(
                client=fake_client,
                dataset_asset=None,
                dataset_blob_url="https://datasets01.blob.core.windows.net/datasets/so101?sv=2024-01-01&sig=secret",
                forbid_static_secrets=True,
            )

        fake_container = MagicMock()
        fake_container.list_blobs.return_value = [
            SimpleNamespace(name="releases/so101/v1/meta/info.json"),
            SimpleNamespace(name="releases/so101/v1/.published.json"),
            SimpleNamespace(name="releases/so101/v1/checksums.sha256"),
        ]
        fake_identity = SimpleNamespace(DefaultAzureCredential=MagicMock(return_value=MagicMock()))
        fake_blob_mod = SimpleNamespace(ContainerClient=MagicMock(return_value=fake_container))
        monkeypatch.setattr(
            _MOD.importlib,
            "import_module",
            lambda name: fake_identity if name == "azure.identity" else fake_blob_mod,
        )

        receipt = _MOD._probe_dataset_hydration_source(
            client=fake_client,
            dataset_asset="azureml:so101_pick_place:2026.11.17",
            dataset_blob_url=None,
            forbid_static_secrets=True,
        )
        assert receipt["dataset_asset"]["manifest_sha256"] == "abc123"
        assert receipt["dataset_blob"]["has_meta_info_json"] is True
        assert receipt["dataset_blob"]["has_published_marker"] is True

    def test_storage_upload_verifies_sha256_readback_and_detects_tamper(self) -> None:
        blob_client = MagicMock()
        blob_client.download_blob.return_value.readall.return_value = b"azure storage smoke test payload"
        blob_service = MagicMock()
        blob_service.get_blob_client.return_value = blob_client
        storage = SimpleNamespace(
            container_name="checkpoints",
            upload_checkpoint=MagicMock(return_value="smoke-test/blob.chkpt"),
            blob_client=blob_service,
        )

        res = _MOD._test_storage_upload(storage)
        assert res["readback_verified"] is True
        blob_client.delete_blob.assert_called_once()

        blob_client.download_blob.return_value.readall.return_value = b"corrupted-bytes"
        with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
            _MOD._test_storage_upload(storage)

    def test_probe_single_endpoint_proxy_and_asset_ref_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="immutable version"):
            _MOD._parse_dataset_asset_ref("azureml:ds@latest")
        assert _MOD._parse_dataset_asset_ref(
            "azureml://subscriptions/s/resourceGroups/r/providers/Microsoft.MachineLearningServices/"
            "workspaces/w/data/my_ds/versions/7"
        ) == ("my_ds", "7")

        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:8080")
        monkeypatch.setenv("NO_PROXY", "localhost,.internal")
        assert _MOD._proxy_applies("service.internal") is False
        assert _MOD._proxy_applies("datasets01.blob.core.windows.net") is True

        monkeypatch.setattr(
            _MOD.socket,
            "getaddrinfo",
            lambda *_a, **_kw: [(0, 0, 0, "", ("10.10.1.5", 443))],
        )
        proxied = _MOD._probe_single_endpoint("blob", "datasets01.blob.core.windows.net")
        assert proxied["private_link_ip"] is True
        assert proxied["via_proxy"] is True
        assert proxied["tls_reachable"] is True

        monkeypatch.delenv("HTTPS_PROXY")
        fake_sock = MagicMock()
        fake_sock.__enter__.return_value = fake_sock
        fake_ssl_ctx = MagicMock()
        fake_ssl_ctx.wrap_socket.return_value.__enter__.return_value = MagicMock()
        monkeypatch.setattr(_MOD.socket, "create_connection", lambda *_a, **_kw: fake_sock)
        monkeypatch.setattr(_MOD.ssl, "create_default_context", lambda: fake_ssl_ctx)
        direct = _MOD._probe_single_endpoint("blob", "datasets01.blob.core.windows.net")
        assert direct["tls_reachable"] is True
        assert direct["tls_ms"] is not None
