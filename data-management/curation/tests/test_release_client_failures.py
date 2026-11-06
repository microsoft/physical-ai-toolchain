"""Headless API transport failures expose only bounded, sanitized diagnostics."""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from physical_ai_curation.release import client as client_module
from physical_ai_curation.release.client import (
    EXIT_REMOTE,
    EXIT_TRANSPORT,
    EXIT_USAGE,
    ReleaseApiClient,
    ReleaseClientError,
    wait_for_job,
)


@pytest.fixture
def client() -> ReleaseApiClient:
    return ReleaseApiClient("https://example.invalid", credentials=None)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (urllib.error.URLError(TimeoutError("private")), "request_timeout"),
        (urllib.error.URLError("private"), "transport_failed"),
        (TimeoutError("private"), "request_timeout"),
        (OSError("private"), "transport_failed"),
        (http.client.IncompleteRead(b"private"), "transport_failed"),
    ],
)
def test_transport_errors_do_not_expose_provider_details(
    client: ReleaseApiClient, failure: Exception, code: str
) -> None:
    client._opener = Mock()
    client._opener.open.side_effect = failure

    with pytest.raises(ReleaseClientError) as raised:
        client.capabilities()

    assert raised.value.code == code
    assert raised.value.exit_code == EXIT_TRANSPORT
    assert "private" not in repr(raised.value)


def test_invalid_json_response_closes_http_stream(client: ReleaseApiClient) -> None:
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"not-json"
    client._opener = Mock()
    client._opener.open.return_value = response

    with pytest.raises(ReleaseClientError, match="invalid_response"):
        client.capabilities()

    response.__exit__.assert_called_once()


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"detail":"private"}',
        b'{"detail":{"schema":"unknown","code":"private"}}',
        b'{"detail":{"schema":"curated-release-api/v1","code":"Private secret!"}}',
        b'{"detail":{"schema":"curated-release-api/v1","code":17}}',
    ],
)
def test_remote_error_rejects_untrusted_diagnostic_schema(client: ReleaseApiClient, body: bytes) -> None:
    error = urllib.error.HTTPError("https://example.invalid", 503, "private", {}, io.BytesIO(body))

    failure = client._http_failure(error)

    assert (failure.code, failure.status, failure.exit_code) == ("http_503", 503, EXIT_REMOTE)
    assert error.fp.closed


def test_unreadable_http_error_body_is_sanitized_and_closed(client: ReleaseApiClient) -> None:
    body = Mock()
    body.read.side_effect = OSError("private")
    error = urllib.error.HTTPError("https://example.invalid", 503, "private", {}, body)

    failure = client._http_failure(error)

    assert failure.code == "http_503"
    body.close.assert_called_once()


@pytest.mark.parametrize("payload", [None, [], {}, {"csrf_token": 17}, {"csrf_token": "invalid"}])
def test_csrf_bootstrap_refuses_invalid_token_before_post(client: ReleaseApiClient, payload: object) -> None:
    client._send = Mock(return_value=payload)

    with pytest.raises(ReleaseClientError, match="csrf_bootstrap_failed"):
        client.post("/operation", body={})

    client._send.assert_called_once_with("GET", client_module.CSRF_PATH)


def test_csrf_bootstrap_requires_matching_cookie(client: ReleaseApiClient) -> None:
    client._send = Mock(return_value={"csrf_token": "a" * 64})
    client._cookie_value = Mock(return_value="b" * 64)

    with pytest.raises(ReleaseClientError, match="csrf_bootstrap_failed"):
        client.post("/operation", body={})

    client._send.assert_called_once_with("GET", client_module.CSRF_PATH)


@pytest.mark.parametrize("body", [b"[]", b"{", b'{"episode_indices":[NaN]}'])
def test_request_file_rejects_invalid_json_without_transmission(tmp_path: Path, body: bytes) -> None:
    request = tmp_path / "request.json"
    request.write_bytes(body)

    with pytest.raises(ReleaseClientError, match="request_file_invalid"):
        client_module._load_request(request)


def test_missing_request_file_reports_only_fixed_error(tmp_path: Path) -> None:
    with pytest.raises(ReleaseClientError, match="request_file_unreadable") as raised:
        client_module._load_request(tmp_path / "missing.json")

    assert raised.value.exit_code == EXIT_USAGE
    assert str(tmp_path) not in str(raised.value)


def test_oversized_request_file_is_refused_before_decode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = tmp_path / "request.json"
    request.write_bytes(b'{"episode_indices":[0,1]}')
    monkeypatch.setattr(client_module, "MAX_REQUEST_FILE_BYTES", 8)

    with pytest.raises(ReleaseClientError, match="request_file_invalid"):
        client_module._load_request(request)


@pytest.mark.parametrize("status", [None, [], "committed"])
def test_wait_refuses_non_object_status(client: ReleaseApiClient, status: object) -> None:
    client.status = Mock(return_value=status)

    with pytest.raises(ReleaseClientError, match="invalid_response"):
        wait_for_job(client, "a" * 32, timeout=1, interval=1)


@pytest.mark.parametrize("value", [-1, 2**31, "0"])
def test_media_episode_index_is_validated_without_transport(client: ReleaseApiClient, value: object) -> None:
    client._transmit = Mock()

    with pytest.raises(ReleaseClientError, match="episode_index_invalid"):
        client.source_episode_media("dataset", "a" * 64, episode_index=value, camera="front")

    client._transmit.assert_not_called()


@pytest.mark.parametrize("url", ["", "http://localhost:invalid", "http://localhost:65536", "http://"])
def test_base_url_missing_or_invalid_port_has_fixed_diagnostic(url: str) -> None:
    with pytest.raises(ReleaseClientError, match=r"base_url_missing|url_refused"):
        client_module.parse_base_url(url, credentialed=False)


@pytest.mark.parametrize("value", [-1, 101])
def test_pagination_option_bounds_are_enforced(value: int) -> None:
    with pytest.raises(ReleaseClientError, match="option_out_of_range"):
        client_module._bounded_int(value, 0, 100)


@pytest.mark.parametrize("value", [-1.0, float("nan"), float("inf")])
def test_timeout_option_bounds_are_enforced(value: float) -> None:
    with pytest.raises(ReleaseClientError, match="option_out_of_range"):
        client_module._bounded_float(value, 0.1, 60)


def test_text_output_for_list_response_is_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    client_module._emit_success("list", [{"job_id": "a" * 32}], "text")

    output = capsys.readouterr().out
    assert output.startswith("command: list\nresult: ")
    assert json.loads(output.split("result: ", 1)[1]) == [{"job_id": "a" * 32}]
