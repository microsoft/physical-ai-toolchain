"""Tests for the release API client and CLI against a local fake release API."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from physical_ai_curation.release.client import (
    API_PREFIX,
    EXIT_JOB_NOT_COMMITTED,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_REMOTE,
    EXIT_TRANSPORT,
    EXIT_USAGE,
    EXIT_VERIFY,
    EXIT_WAIT_TIMEOUT,
    ReleaseApiClient,
    ReleaseClientError,
    load_credentials,
    main,
    parse_base_url,
)
from test_release_contracts import MiniaturePackage, _finalize, miniature  # noqa: F401 - pytest fixture

JOB_ID = "a" * 32
KEY = "test-release-key-0001"
CSRF_TOKEN = "ab" * 32
HANDLE = "c" * 64
SOURCES_PREFIX = f"{API_PREFIX}/sources/"
MEDIA_BYTES = b"\x00\x00\x00\x18ftypmp42MEDIA"
SPEC: dict[str, Any] = {
    "destination": "lab",
    "package_id": "pkg-001",
    "attempt_id": "attempt-001",
    "dataset_id": "ds-001",
    "episode_indices": [0, 1],
}


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    target: str
    headers: dict[str, str]
    body: bytes


def _cookie_value(raw: str) -> str | None:
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "csrf_token":
            return value
    return None


class _Handler(BaseHTTPRequestHandler):
    server: FakeReleaseApi

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        self._handle(b"")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self._handle(self.rfile.read(length) if length else b"")

    def _reply(self, status: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _reply_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _source(self, rest: str, query: dict[str, list[str]]) -> None:
        segments = rest.split("/")
        if len(segments) < 2 or segments[1] != "releases":
            self._reply(404, {"detail": "Not Found"})
        elif len(segments) == 2:
            self._reply(200, {"releases": []})
        elif len(segments) == 3:
            self._reply(200, {"handle": segments[2]})
        elif len(segments) == 4 and segments[3] == "episodes":
            cursor = query.get("cursor", [""])[0]
            limit = query.get("limit", [""])[0]
            self._reply(200, {"episodes": [], "cursor": cursor, "limit": limit})
        elif len(segments) == 7 and segments[3] == "episodes" and segments[5] == "media":
            self._reply_bytes(200, MEDIA_BYTES, "video/mp4")
        else:
            self._reply(404, {"detail": "Not Found"})

    def _handle(self, body: bytes) -> None:
        fake = self.server
        headers = {name.lower(): value for name, value in self.headers.items()}
        fake.requests.append(RecordedRequest(self.command, self.path, headers, body))
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)

        if path == "/redirect":
            self._reply(302, {}, {"Location": f"{API_PREFIX}/capabilities"})
            return
        if fake.expected_api_key is not None and headers.get("x-api-key") != fake.expected_api_key:
            self._reply(401, {"detail": "Invalid API key"})
            return
        if path == "/api/csrf-token":
            extra = {} if fake.omit_csrf_cookie else {"Set-Cookie": f"csrf_token={CSRF_TOKEN}; Path=/; SameSite=Strict"}
            self._reply(200, {"csrf_token": CSRF_TOKEN}, extra)
            return
        if self.command == "POST":
            token = headers.get("x-csrf-token")
            if token is None or token != _cookie_value(headers.get("cookie", "")):
                self._reply(403, {"detail": "CSRF token missing or invalid"})
                return

        route = (self.command, path)
        if route == ("POST", f"{API_PREFIX}/preview"):
            self._reply(200, {"echo": json.loads(body)})
        elif path.startswith(SOURCES_PREFIX):
            self._source(path[len(SOURCES_PREFIX) :], query)
        elif route == ("GET", f"{API_PREFIX}/capabilities"):
            self._reply(200, {"schema": "curated-release-api/v1", "operations": ["status"]})
        elif route == ("GET", f"{API_PREFIX}/jobs/{JOB_ID}"):
            self._reply(200, {"job_id": JOB_ID, "state": fake.next_state()})
        elif route == ("POST", f"{API_PREFIX}/jobs"):
            if "idempotency-key" not in headers:
                self._reply(400, {"detail": "Idempotency-Key header is required"})
                return
            if fake.submit_conflict:
                detail = {"schema": "curated-release-api/v1", "code": "release_idempotency_conflict"}
                self._reply(409, {"detail": detail})
                return
            self._reply(202, {"job_id": JOB_ID, "replayed": False}, {"Location": f"{API_PREFIX}/jobs/{JOB_ID}"})
        elif route == ("GET", f"{API_PREFIX}/requests"):
            if "idempotency-key" not in headers:
                self._reply(400, {"detail": "Idempotency-Key header is required"})
            else:
                self._reply(200, {"job_id": JOB_ID, "replayed": True})
        elif route == ("GET", f"{API_PREFIX}/jobs"):
            self._reply(200, {"jobs": []})
        elif route == ("GET", f"{API_PREFIX}/preview"):
            self._reply(200, {"selection": query.get("selection", [""])[0]})
        elif route == ("GET", f"{API_PREFIX}/jobs/{JOB_ID}/result"):
            detail = {
                "schema": "curated-release-api/v1",
                "code": "release_job_state_conflict",
                "message": "LEAK-MARKER",
            }
            self._reply(409, {"detail": detail})
        elif route == ("POST", f"{API_PREFIX}/jobs/{JOB_ID}/cancel"):
            self._reply(202, {"job_id": JOB_ID, "state": "cancelled"})
        else:
            self._reply(404, {"detail": "Not Found"})


class FakeReleaseApi(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[RecordedRequest] = []
        self.expected_api_key: str | None = KEY
        self.omit_csrf_cookie = False
        self.submit_conflict = False
        self.states: list[str] = ["committed"]

    def next_state(self) -> str:
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.pauses: list[float] = []

    def __call__(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


@pytest.fixture
def api() -> Iterator[FakeReleaseApi]:
    server = FakeReleaseApi()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _env(base_url: str, **extra: str) -> dict[str, str]:
    return {"RELEASE_API_URL": base_url, "RELEASE_API_KEY": KEY, **extra}


def _write_spec(tmp_path: Path, **changes: Any) -> Path:
    path = tmp_path / "request.json"
    path.write_text(json.dumps({**SPEC, **changes}), encoding="utf-8")
    return path


def _submit_args(request: Path, *flags: str) -> list[str]:
    return [
        "submit",
        "--request",
        str(request),
        "--preview-digest",
        "pd-001",
        "--processing-digest",
        "pd-002",
        "--idempotency-key",
        "idem-key-0001",
        *flags,
    ]


def _stderr_error(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().err)["error"]


def test_given_status_when_main_then_key_sent_and_no_csrf_traffic(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    environ = _env(api.base_url)

    # Act
    code = main(["status", JOB_ID], environ=environ)

    # Assert
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"]["state"] == "committed"
    assert all(request.method == "GET" for request in api.requests)
    last = api.requests[-1]
    assert last.headers["x-api-key"] == KEY
    assert "x-csrf-token" not in last.headers


def test_given_bearer_token_when_status_then_authorization_header_sent(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.expected_api_key = None
    environ = {"RELEASE_API_URL": api.base_url, "RELEASE_BEARER_TOKEN": "tok-0001"}

    # Act
    code = main(["status", JOB_ID], environ=environ)

    # Assert
    assert code == EXIT_OK
    assert api.requests[-1].headers["authorization"] == "Bearer tok-0001"


def test_given_confirmed_submit_when_csrf_bootstrapped_then_cookie_echoed_and_idempotency_sent(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    request = _write_spec(tmp_path)

    # Act
    code = main(_submit_args(request, "--ack-local-disk", "--confirm"), environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"] == {"job_id": JOB_ID, "replayed": False}
    post = api.requests[-1]
    assert post.method == "POST"
    assert post.target == f"{API_PREFIX}/jobs"
    assert post.headers["x-csrf-token"] == CSRF_TOKEN
    assert f"csrf_token={CSRF_TOKEN}" in post.headers["cookie"]
    assert post.headers["idempotency-key"] == "idem-key-0001"
    body = json.loads(post.body)
    assert body["local_disk_acknowledged"] is True
    assert body["preview_digest"] == "pd-001"
    assert body["processing_digest"] == "pd-002"
    assert body["episode_indices"] == [0, 1]


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(("--ack-local-disk",), id="ack-only"),
        pytest.param(("--confirm",), id="confirm-only"),
        pytest.param((), id="neither"),
    ],
)
def test_given_unconfirmed_submit_when_main_then_refused_without_contacting_api(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: tuple[str, ...]
) -> None:
    # Arrange
    request = _write_spec(tmp_path)

    # Act
    code = main(_submit_args(request, *flags), environ=_env(api.base_url))

    # Assert
    assert code == EXIT_REFUSED
    assert _stderr_error(capsys)["code"] == "submit_not_confirmed"
    assert api.requests == []


def test_given_request_file_with_server_owned_field_when_preview_then_refused(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    request = _write_spec(tmp_path, local_disk_acknowledged=True)

    # Act
    code = main(["preview", "--request", str(request)], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "request_field_refused"
    assert api.requests == []


def test_given_request_when_preview_then_post_body_is_canonical_json_with_csrf(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    request = _write_spec(tmp_path)

    # Act
    code = main(["preview", "--request", str(request)], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    post = api.requests[-1]
    assert post.method == "POST"
    assert post.target == f"{API_PREFIX}/preview"
    assert post.headers["x-csrf-token"] == CSRF_TOKEN
    assert post.body == json.dumps(SPEC, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert json.loads(capsys.readouterr().out)["result"] == {"echo": SPEC}


def test_given_selection_when_preview_query_then_get_retained_without_csrf(api: FakeReleaseApi) -> None:
    # Arrange
    client = ReleaseApiClient(api.base_url, credentials=load_credentials({"RELEASE_API_KEY": KEY}))

    # Act
    payload = client.preview_query('{"a":1}')

    # Assert
    request = api.requests[-1]
    assert request.method == "GET"
    assert "x-csrf-token" not in request.headers
    assert payload == {"selection": '{"a":1}'}


def test_given_csrf_cookie_withheld_when_submit_then_bootstrap_fails_before_post(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.omit_csrf_cookie = True
    request = _write_spec(tmp_path)

    # Act
    code = main(_submit_args(request, "--ack-local-disk", "--confirm"), environ=_env(api.base_url))

    # Assert
    assert code == EXIT_TRANSPORT
    assert _stderr_error(capsys)["code"] == "csrf_bootstrap_failed"
    assert all(request_.method == "GET" for request_ in api.requests)


def test_given_wrong_api_key_when_status_then_sanitized_remote_error(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    environ = {"RELEASE_API_URL": api.base_url, "RELEASE_API_KEY": "wrong-key-0001"}

    # Act
    code = main(["status", JOB_ID], environ=environ)

    # Assert
    captured = capsys.readouterr()
    assert code == EXIT_REMOTE
    assert json.loads(captured.err)["error"] == {"code": "http_401", "status": 401}
    assert captured.out == ""
    assert "wrong-key-0001" not in captured.err


def test_given_api_error_with_detail_when_result_then_detail_and_secret_not_echoed(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    environ = _env(api.base_url)

    # Act
    code = main(["result", JOB_ID], environ=environ)

    # Assert
    captured = capsys.readouterr()
    assert code == EXIT_REMOTE
    assert json.loads(captured.err)["error"] == {"code": "release_job_state_conflict", "status": 409}
    assert "LEAK-MARKER" not in captured.err
    assert KEY not in captured.err
    assert captured.out == ""


def test_given_both_credentials_when_main_then_usage_error(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    environ = _env(api.base_url, RELEASE_BEARER_TOKEN="tok-0001")

    # Act
    code = main(["status", JOB_ID], environ=environ)

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "credentials_ambiguous"
    assert api.requests == []


def test_given_credentials_when_repr_then_secret_hidden() -> None:
    # Arrange
    credentials = load_credentials({"RELEASE_API_KEY": KEY})

    # Act
    rendered = repr(credentials)

    # Assert
    assert credentials is not None
    assert KEY not in rendered


@pytest.mark.parametrize("raw", ["abc\n", " padded"])
def test_given_control_or_padded_credential_when_loaded_then_rejected(raw: str) -> None:
    with pytest.raises(ReleaseClientError) as caught:
        load_credentials({"RELEASE_API_KEY": raw})

    assert caught.value.code == "credential_invalid"
    assert caught.value.exit_code == EXIT_USAGE


@pytest.mark.parametrize(
    ("raw", "credentialed", "code"),
    [
        pytest.param("http://user:secret@127.0.0.1:8080", False, "url_refused", id="userinfo"),
        pytest.param("http://127.0.0.1:8080/?x=1", False, "url_refused", id="query"),
        pytest.param("http://127.0.0.1:8080/#frag", False, "url_refused", id="fragment"),
        pytest.param("http://127.0.0.1:8080/api", False, "url_refused", id="path"),
        pytest.param("ftp://127.0.0.1", False, "url_scheme_refused", id="scheme"),
        pytest.param("http://release.example.com", True, "plaintext_refused", id="remote-plaintext-credentialed"),
    ],
)
def test_given_unsafe_base_url_when_parsed_then_refused(raw: str, credentialed: bool, code: str) -> None:
    with pytest.raises(ReleaseClientError) as caught:
        parse_base_url(raw, credentialed=credentialed)

    assert caught.value.code == code
    assert caught.value.exit_code == EXIT_REFUSED


def test_given_loopback_or_tls_base_url_when_parsed_then_origin_returned() -> None:
    assert parse_base_url("http://localhost:8080/", credentialed=True) == "http://localhost:8080"
    assert parse_base_url("https://release.example.com", credentialed=True) == "https://release.example.com"


def test_given_remote_plaintext_without_credentials_when_parsed_then_accepted() -> None:
    assert parse_base_url("http://release.example.com", credentialed=False) == "http://release.example.com"


def test_given_redirect_when_get_then_refused_and_target_not_followed(api: FakeReleaseApi) -> None:
    # Arrange
    client = ReleaseApiClient(api.base_url, credentials=None)

    # Act
    with pytest.raises(ReleaseClientError) as caught:
        client.get("/redirect")

    # Assert
    assert caught.value.code == "redirect_refused"
    assert caught.value.status == 302
    assert [request.target for request in api.requests] == ["/redirect"]


def test_given_oversized_response_when_get_then_rejected(api: FakeReleaseApi) -> None:
    # Arrange
    api.expected_api_key = None
    client = ReleaseApiClient(api.base_url, credentials=None, max_response_bytes=8)

    # Act
    with pytest.raises(ReleaseClientError) as caught:
        client.get(f"{API_PREFIX}/capabilities")

    # Assert
    assert caught.value.code == "response_too_large"
    assert caught.value.exit_code == EXIT_TRANSPORT


def test_given_committed_job_when_wait_then_polls_until_terminal(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.states = ["running", "running", "committed"]
    clock = FakeClock()

    # Act
    code = main(
        ["wait", JOB_ID, "--timeout", "30", "--interval", "1"],
        environ=_env(api.base_url),
        clock=clock,
        pause=clock.pause,
    )

    # Assert
    assert code == EXIT_OK
    assert clock.pauses == [1.0, 1.0]
    assert json.loads(capsys.readouterr().out)["result"]["state"] == "committed"


def test_given_cancelled_job_when_wait_then_not_committed(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.states = ["cancelled"]
    clock = FakeClock()

    # Act
    code = main(
        ["wait", JOB_ID, "--timeout", "30", "--interval", "1"],
        environ=_env(api.base_url),
        clock=clock,
        pause=clock.pause,
    )

    # Assert
    assert code == EXIT_JOB_NOT_COMMITTED
    error = _stderr_error(capsys)
    assert error == {"code": "job_not_committed", "state": "cancelled"}
    assert clock.pauses == []


def test_given_running_job_when_wait_past_deadline_then_timeout_exit(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.states = ["running"]
    clock = FakeClock()

    # Act
    code = main(
        ["wait", JOB_ID, "--timeout", "2", "--interval", "1"],
        environ=_env(api.base_url),
        clock=clock,
        pause=clock.pause,
    )

    # Assert
    assert code == EXIT_WAIT_TIMEOUT
    assert _stderr_error(capsys)["code"] == "wait_deadline_exceeded"
    assert clock.pauses == [1.0, 1.0]


def test_given_text_format_when_status_then_key_value_lines(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["--format", "text", "status", JOB_ID], environ=_env(api.base_url))

    # Assert
    lines = capsys.readouterr().out.splitlines()
    assert code == EXIT_OK
    assert lines[0] == "command: status"
    assert '"state": "committed"' in lines[1:]


def test_given_published_package_when_verify_then_offline_success(
    miniature: MiniaturePackage,  # noqa: F811 - pytest fixture
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    _finalize(miniature.root)

    # Act
    code = main(["verify", str(miniature.root), "--expected-manifest", miniature.manifest_sha256], environ={})

    # Assert
    assert code == EXIT_OK
    assert "published" in capsys.readouterr().out


def test_given_tampered_package_when_verify_then_rejected(
    miniature: MiniaturePackage,  # noqa: F811 - pytest fixture
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    _finalize(miniature.root)
    (miniature.root / "data" / "episode-000.jsonl").write_bytes(b"tampered")

    # Act
    code = main(["verify", str(miniature.root), "--expected-manifest", miniature.manifest_sha256], environ={})

    # Assert
    assert code == EXIT_VERIFY
    assert _stderr_error(capsys)["code"] == "verification_failed"


def test_given_wrong_expected_manifest_when_verify_then_rejected(
    miniature: MiniaturePackage,  # noqa: F811 - pytest fixture
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    _finalize(miniature.root)

    # Act
    code = main(["verify", str(miniature.root), "--expected-manifest", "0" * 64], environ={})

    # Assert
    assert code == EXIT_VERIFY
    assert _stderr_error(capsys)["code"] == "verification_failed"


def test_given_same_body_and_key_when_resubmitted_then_identical_post_bytes(
    api: FakeReleaseApi, tmp_path: Path
) -> None:
    # Arrange
    request = _write_spec(tmp_path)
    args = _submit_args(request, "--ack-local-disk", "--confirm")

    # Act
    first = main(args, environ=_env(api.base_url))
    second = main(args, environ=_env(api.base_url))

    # Assert
    posts = [item for item in api.requests if item.method == "POST" and item.target == f"{API_PREFIX}/jobs"]
    assert first == second == EXIT_OK
    assert len(posts) == 2
    assert posts[0].body == posts[1].body
    assert posts[0].headers["idempotency-key"] == posts[1].headers["idempotency-key"] == "idem-key-0001"


def test_given_key_reused_with_changed_body_when_submit_then_remote_conflict_reported(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    api.submit_conflict = True
    request = _write_spec(tmp_path)

    # Act
    code = main(_submit_args(request, "--ack-local-disk", "--confirm"), environ=_env(api.base_url))

    # Assert
    assert code == EXIT_REMOTE
    assert _stderr_error(capsys) == {"code": "release_idempotency_conflict", "status": 409}


def test_given_dataset_with_separator_when_source_releases_then_segment_escaped(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["source-releases", "ds/one two"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    last = api.requests[-1]
    assert last.method == "GET"
    assert last.target == f"{API_PREFIX}/sources/ds%2Fone%20two/releases"
    assert "x-csrf-token" not in last.headers


def test_given_cursor_and_limit_when_source_episodes_then_query_sent(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(
        ["source-episodes", "ds-001", HANDLE, "--cursor", "5", "--limit", "10"],
        environ=_env(api.base_url),
    )

    # Assert
    assert code == EXIT_OK
    target = urlsplit(api.requests[-1].target)
    assert target.path == f"{API_PREFIX}/sources/ds-001/releases/{HANDLE}/episodes"
    assert parse_qs(target.query) == {"cursor": ["5"], "limit": ["10"]}


def test_given_camera_when_source_media_then_bytes_written_to_new_file(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    output = tmp_path / "front.mp4"

    # Act
    code = main(
        ["source-media", "ds-001", HANDLE, "3", "front.rgb", "--output", str(output)],
        environ=_env(api.base_url),
    )

    # Assert
    assert code == EXIT_OK
    assert output.read_bytes() == MEDIA_BYTES
    assert json.loads(capsys.readouterr().out)["result"] == {"bytes": len(MEDIA_BYTES), "path": str(output)}
    assert api.requests[-1].target == f"{API_PREFIX}/sources/ds-001/releases/{HANDLE}/episodes/3/media/front.rgb"


def test_given_existing_output_when_source_media_then_refused_and_file_unchanged(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    output = tmp_path / "front.mp4"
    output.write_bytes(b"keep")

    # Act
    code = main(
        ["source-media", "ds-001", HANDLE, "3", "front.rgb", "--output", str(output)],
        environ=_env(api.base_url),
    )

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "output_exists"
    assert output.read_bytes() == b"keep"


def test_given_malformed_handle_when_source_release_then_refused_without_request(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["source-release", "ds-001", "../../etc"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "handle_invalid"
    assert api.requests == []


def test_given_traversal_camera_when_source_media_then_refused_without_request_or_file(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    output = tmp_path / "out.mp4"

    # Act
    code = main(
        ["source-media", "ds-001", HANDLE, "0", "..", "--output", str(output)],
        environ=_env(api.base_url),
    )

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "camera_invalid"
    assert api.requests == []
    assert not output.exists()


def test_given_description_when_preview_then_forwarded_in_body(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    request = _write_spec(tmp_path, description="Pick-and-place batch")

    # Act
    code = main(["preview", "--request", str(request)], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    assert json.loads(api.requests[-1].body)["description"] == "Pick-and-place batch"


@pytest.mark.parametrize(
    "description",
    [pytest.param("x" * 2001, id="too-long"), pytest.param(5, id="not-string")],
)
def test_given_invalid_description_when_preview_then_refused_without_request(
    api: FakeReleaseApi, tmp_path: Path, capsys: pytest.CaptureFixture[str], description: object
) -> None:
    # Arrange
    request = _write_spec(tmp_path, description=description)

    # Act
    code = main(["preview", "--request", str(request)], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "description_invalid"
    assert api.requests == []


def test_given_idempotency_key_when_accepted_request_then_get_with_header_and_no_csrf(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["accepted-request", "--idempotency-key", "idem-key-0001"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    assert json.loads(capsys.readouterr().out)["result"] == {"job_id": JOB_ID, "replayed": True}
    assert [(item.method, item.target) for item in api.requests] == [("GET", f"{API_PREFIX}/requests")]
    assert api.requests[-1].headers["idempotency-key"] == "idem-key-0001"
    assert "x-csrf-token" not in api.requests[-1].headers


def test_given_malformed_idempotency_key_when_accepted_request_then_refused_without_request(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["accepted-request", "--idempotency-key", "bad key"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "idempotency_key_invalid"
    assert api.requests == []


def test_given_dataset_filter_when_list_then_dataset_id_query_sent(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["list", "--dataset-id", "ds/one two"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    target = urlsplit(api.requests[-1].target)
    assert target.path == f"{API_PREFIX}/jobs"
    assert parse_qs(target.query) == {"cursor": ["0"], "limit": ["50"], "dataset_id": ["ds/one two"]}


def test_given_no_dataset_filter_when_list_then_query_unchanged(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["list"], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_OK
    assert parse_qs(urlsplit(api.requests[-1].target).query) == {"cursor": ["0"], "limit": ["50"]}


def test_given_empty_dataset_filter_when_list_then_refused_without_request(
    api: FakeReleaseApi, capsys: pytest.CaptureFixture[str]
) -> None:
    # Act
    code = main(["list", "--dataset-id", ""], environ=_env(api.base_url))

    # Assert
    assert code == EXIT_USAGE
    assert _stderr_error(capsys)["code"] == "dataset_id_invalid"
    assert api.requests == []
