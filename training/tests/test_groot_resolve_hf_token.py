# cspell:ignore clientid
from __future__ import annotations

import io
import json
import urllib.parse
import urllib.request

import pytest
from conftest import load_training_module

_MOD = load_training_module("groot_resolve_hf_token", "training/vla/scripts/groot/resolve_hf_token.py")

_SECRET_URL = "https://vault.example.net/secrets/hf-token"


class _FakeOpener:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        return io.BytesIO(json.dumps(self.payload).encode("utf-8"))


def _query(request: urllib.request.Request) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))


def test_aml_compute_request_sends_secret_header_and_client_id() -> None:
    request = _MOD.build_identity_request(
        {"MSI_ENDPOINT": "http://127.0.0.1:1/token", "MSI_SECRET": "s3cr3t", "AZURE_CLIENT_ID": "client-1"}
    )

    assert _query(request) == {
        "api-version": "2017-09-01",
        "resource": "https://vault.azure.net",
        "clientid": "client-1",
    }
    assert request.get_header("Secret") == "s3cr3t"


def test_kubernetes_compute_request_omits_secret_header() -> None:
    request = _MOD.build_identity_request({"MSI_ENDPOINT": "http://127.0.0.1:1/token"})

    assert "clientid" not in _query(request)
    assert request.get_header("Secret") is None


def test_default_identity_client_id_is_used_when_azure_client_id_is_unset() -> None:
    request = _MOD.build_identity_request(
        {"MSI_ENDPOINT": "http://127.0.0.1:1/token?existing=1", "DEFAULT_IDENTITY_CLIENT_ID": "default-1"}
    )

    assert _query(request)["clientid"] == "default-1"
    assert _query(request)["existing"] == "1"


def test_missing_msi_endpoint_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="MSI_ENDPOINT is required"):
        _MOD.build_identity_request({"MSI_SECRET": "s3cr3t"})


def test_resolve_token_reads_secret_with_identity_token() -> None:
    identity = _FakeOpener({"access_token": "vault-access"})
    key_vault = _FakeOpener({"value": "hf_example"})

    token = _MOD.resolve_token(
        {"MSI_ENDPOINT": "http://127.0.0.1:1/token", "HF_TOKEN_SECRET_URL": _SECRET_URL},
        identity_opener=identity,
        key_vault_opener=key_vault,
    )

    assert token == "hf_example"
    secret_request = key_vault.requests[0]
    assert secret_request.full_url == f"{_SECRET_URL}?api-version=7.4"
    assert secret_request.get_header("Authorization") == "Bearer vault-access"


@pytest.mark.parametrize(
    ("identity_payload", "secret_payload", "message"),
    [
        ({"access_token": ""}, {"value": "hf_example"}, "returned no access token"),
        ({"access_token": "vault-access"}, {"value": ""}, "secret is empty"),
        ({"access_token": "vault-access"}, ["not", "an", "object"], "unexpected response"),
    ],
)
def test_resolve_token_rejects_empty_or_malformed_responses(
    identity_payload: object, secret_payload: object, message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _MOD.resolve_token(
            {"MSI_ENDPOINT": "http://127.0.0.1:1/token", "HF_TOKEN_SECRET_URL": _SECRET_URL},
            identity_opener=_FakeOpener(identity_payload),
            key_vault_opener=_FakeOpener(secret_payload),
        )


def test_resolve_token_requires_secret_url() -> None:
    with pytest.raises(RuntimeError, match="HF_TOKEN_SECRET_URL is required"):
        _MOD.resolve_token({"MSI_ENDPOINT": "http://127.0.0.1:1/token"})


class _RecordingExec:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, file: str, args: list[str], env: dict[str, str]) -> None:
        self.calls.append((file, args, env))


def test_main_execs_command_with_token_only_in_its_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(_MOD, "resolve_token", lambda environ: "hf_example")
    monkeypatch.setenv("KEEP_ME", "1")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    execute = _RecordingExec()

    assert _MOD.main(["--", "bash", "train.sh", "--flag"], execute=execute) == 0

    file, args, env = execute.calls[0]
    assert (file, args) == ("bash", ["bash", "train.sh", "--flag"])
    assert env["HF_TOKEN"] == "hf_example"
    assert env["KEEP_ME"] == "1"
    captured = capsys.readouterr()
    assert "hf_example" not in captured.out + captured.err
    assert "loaded Hugging Face token from Key Vault" in captured.out
    assert "HF_TOKEN" not in _MOD.os.environ


def test_main_reports_errors_without_running_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MSI_ENDPOINT", raising=False)
    monkeypatch.setenv("HF_TOKEN_SECRET_URL", _SECRET_URL)
    execute = _RecordingExec()

    assert _MOD.main(["--", "bash", "train.sh"], execute=execute) == 1
    assert execute.calls == []
    assert "MSI_ENDPOINT is required" in capsys.readouterr().err


def test_main_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        _MOD.main(["--"])
