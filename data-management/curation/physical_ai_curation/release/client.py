"""Bounded headless client and CLI for the curated release HTTP API.

Stdlib only. Credentials come from the environment and are sent only as request
headers. Plaintext HTTP to a non-loopback host is refused when credentials are
configured. Redirects are never followed. Failures are reduced to a fixed error
code and HTTP status, so server detail text and secrets are never echoed.
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import http.client
import http.cookiejar
import ipaddress
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

CLIENT_SCHEMA = "curated-release-client/v1"
API_PREFIX = "/api/releases/v1"
CSRF_PATH = "/api/csrf-token"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
API_KEY_HEADER = "X-API-Key"
AUTHORIZATION_HEADER = "Authorization"
IDEMPOTENCY_HEADER = "Idempotency-Key"

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_FILE_BYTES = 1024 * 1024
MAX_DESCRIPTION_CHARS = 2000
MAX_CREDENTIAL_CHARS = 4096
DEFAULT_WAIT_TIMEOUT_SECONDS = 600.0
MAX_WAIT_TIMEOUT_SECONDS = 86_400.0
DEFAULT_WAIT_INTERVAL_SECONDS = 5.0
MAX_WAIT_INTERVAL_SECONDS = 300.0
MEDIA_ACCEPT = "video/mp4"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3
EXIT_REMOTE = 4
EXIT_TRANSPORT = 5
EXIT_VERIFY = 6
EXIT_WAIT_TIMEOUT = 7
EXIT_JOB_NOT_COMMITTED = 8

SUCCESS_JOB_STATE = "committed"
TERMINAL_JOB_STATES = frozenset({"committed", "cancelled"})
REQUEST_FIELDS = frozenset(
    {
        "destination",
        "package_id",
        "attempt_id",
        "dataset_name",
        "dataset_id",
        "description",
        "episode_indices",
        "resources",
        "finalized",
    }
)

_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_CSRF_TOKEN = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ARTIFACT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_DIGEST = re.compile(r"^[A-Za-z0-9:_-]{1,64}$")
_HANDLE = re.compile(r"^[0-9a-f]{64}$")
_CAMERA = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_NEVER_SET = threading.Event()


class ReleaseClientError(Exception):
    """Sanitized client failure carrying only a fixed code and optional HTTP status or state."""

    def __init__(
        self,
        code: str,
        exit_code: int,
        status: int | None = None,
        state: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code
        self.status = status
        self.state = state


@dataclass(frozen=True)
class Credentials:
    """Header credential. The value is excluded from ``repr`` to keep it out of logs."""

    header_name: str
    header_value: str = field(repr=False)


def load_credentials(environ: Mapping[str, str]) -> Credentials | None:
    """Read ``RELEASE_API_KEY`` or ``RELEASE_BEARER_TOKEN``. Setting both is a usage error."""
    api_key = environ.get("RELEASE_API_KEY", "")
    bearer = environ.get("RELEASE_BEARER_TOKEN", "")
    if api_key and bearer:
        raise ReleaseClientError("credentials_ambiguous", EXIT_USAGE)
    if api_key:
        return Credentials(API_KEY_HEADER, _checked_secret(api_key))
    if bearer:
        return Credentials(AUTHORIZATION_HEADER, "Bearer " + _checked_secret(bearer))
    return None


def _checked_secret(value: str) -> str:
    if len(value) > MAX_CREDENTIAL_CHARS or value != value.strip() or any(ch in value for ch in "\r\n"):
        raise ReleaseClientError("credential_invalid", EXIT_USAGE)
    return value


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_base_url(raw: str, *, credentialed: bool) -> str:
    """Return the origin of the release API, or raise a refusal.

    Only an origin is accepted: no userinfo, query, fragment, or path.
    """
    if not raw:
        raise ReleaseClientError("base_url_missing", EXIT_USAGE)
    if any(ch.isspace() or not ch.isprintable() for ch in raw) or "?" in raw or "#" in raw:
        raise ReleaseClientError("url_refused", EXIT_REFUSED)
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ReleaseClientError("url_scheme_refused", EXIT_REFUSED)
    if "@" in parts.netloc or parts.path not in ("", "/"):
        raise ReleaseClientError("url_refused", EXIT_REFUSED)
    try:
        host = parts.hostname
        parts.port  # noqa: B018 - validates the port
    except ValueError:
        raise ReleaseClientError("url_refused", EXIT_REFUSED) from None
    if not host:
        raise ReleaseClientError("url_refused", EXIT_REFUSED)
    if parts.scheme == "http" and credentialed and not _is_loopback(host):
        raise ReleaseClientError("plaintext_refused", EXIT_REFUSED)
    return f"{parts.scheme}://{parts.netloc}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return no follow-up request so 3xx responses surface as errors."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _remote_code(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, dict) or detail.get("schema") != "curated-release-api/v1":
        return None
    code = detail.get("code")
    if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
        return code
    return None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _job_path(job_id: str) -> str:
    if not _JOB_ID.fullmatch(job_id):
        raise ReleaseClientError("job_id_invalid", EXIT_USAGE)
    return job_id


def _dataset_id(value: str) -> str:
    if not value or len(value) > 255 or value in {".", ".."} or any(not ch.isprintable() for ch in value):
        raise ReleaseClientError("dataset_id_invalid", EXIT_USAGE)
    return value


def _dataset_segment(dataset_id: str) -> str:
    return urllib.parse.quote(_dataset_id(dataset_id), safe="")


def _handle_segment(handle: str) -> str:
    if not _HANDLE.fullmatch(handle):
        raise ReleaseClientError("handle_invalid", EXIT_USAGE)
    return handle


class ReleaseApiClient:
    """Minimal bounded client for the release API."""

    def __init__(
        self,
        base_url: str,
        *,
        credentials: Credentials | None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._base = base_url
        self._credentials = credentials
        self._timeout = timeout
        self._max_bytes = max_response_bytes
        self._jar = http.cookiejar.CookieJar()
        # ProxyHandler({}) disables environment proxies so credentials never reach a proxy.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self._jar),
            _NoRedirect(),
        )

    def _transmit(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        accept: str = "application/json",
    ) -> bytes:
        url = self._base + path
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request_headers = {"Accept": accept}
        if self._credentials is not None:
            request_headers[self._credentials.header_name] = self._credentials.header_value
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(self._max_bytes + 1)
        except urllib.error.HTTPError as error:
            raise self._http_failure(error) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise ReleaseClientError("request_timeout", EXIT_TRANSPORT) from None
            raise ReleaseClientError("transport_failed", EXIT_TRANSPORT) from None
        except TimeoutError:
            raise ReleaseClientError("request_timeout", EXIT_TRANSPORT) from None
        except (OSError, http.client.HTTPException):
            raise ReleaseClientError("transport_failed", EXIT_TRANSPORT) from None
        if len(raw) > self._max_bytes:
            raise ReleaseClientError("response_too_large", EXIT_TRANSPORT)
        return raw

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        raw = self._transmit(method, path, query=query, body=body, headers=headers)
        try:
            return json.loads(raw)
        except ValueError:
            raise ReleaseClientError("invalid_response", EXIT_TRANSPORT) from None

    def _http_failure(self, error: urllib.error.HTTPError) -> ReleaseClientError:
        status = error.code
        try:
            if 300 <= status < 400:
                return ReleaseClientError("redirect_refused", EXIT_REFUSED, status)
            try:
                raw = error.read(self._max_bytes)
            except OSError:
                raw = b""
            code = _remote_code(raw) or f"http_{status}"
            return ReleaseClientError(code, EXIT_REMOTE, status)
        finally:
            error.close()

    def _cookie_value(self) -> str | None:
        for cookie in self._jar:
            if cookie.name == CSRF_COOKIE:
                return cookie.value
        return None

    def _bootstrap_csrf(self) -> str:
        """Fetch a CSRF token and confirm the cookie jar holds the matching cookie."""
        payload = self._send("GET", CSRF_PATH)
        token = payload.get("csrf_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not _CSRF_TOKEN.fullmatch(token):
            raise ReleaseClientError("csrf_bootstrap_failed", EXIT_TRANSPORT)
        cookie = self._cookie_value()
        if cookie is None or not hmac.compare_digest(cookie, token):
            raise ReleaseClientError("csrf_bootstrap_failed", EXIT_TRANSPORT)
        return token

    def get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        return self._send("GET", path, query=query)

    def post(
        self, path: str, *, body: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> Any:
        token = self._bootstrap_csrf()
        data = None if body is None else _canonical_json(body).encode("utf-8")
        request_headers = dict(headers or {})
        request_headers[CSRF_HEADER] = token
        return self._send("POST", path, body=data, headers=request_headers)

    def capabilities(self) -> Any:
        return self.get(f"{API_PREFIX}/capabilities")

    def destinations(self) -> Any:
        return self.get(f"{API_PREFIX}/destinations")

    def jobs(self, *, cursor: int, limit: int, dataset_id: str | None = None) -> Any:
        query = {"cursor": str(cursor), "limit": str(limit)}
        if dataset_id is not None:
            query["dataset_id"] = _dataset_id(dataset_id)
        return self.get(f"{API_PREFIX}/jobs", query)

    def accepted_request(self, idempotency_key: str) -> Any:
        return self._send(
            "GET",
            f"{API_PREFIX}/requests",
            headers={IDEMPOTENCY_HEADER: _idempotency_key(idempotency_key)},
        )

    def status(self, job_id: str) -> Any:
        return self.get(f"{API_PREFIX}/jobs/{_job_path(job_id)}")

    def preview(self, body: Mapping[str, Any]) -> Any:
        return self.post(f"{API_PREFIX}/preview", body=body)

    def preview_query(self, selection: str) -> Any:
        return self.get(f"{API_PREFIX}/preview", {"selection": selection})

    def submit(self, body: Mapping[str, Any], idempotency_key: str) -> Any:
        return self.post(f"{API_PREFIX}/jobs", body=body, headers={IDEMPOTENCY_HEADER: idempotency_key})

    def cancel(self, job_id: str) -> Any:
        return self.post(f"{API_PREFIX}/jobs/{_job_path(job_id)}/cancel")

    def recover(self, job_id: str) -> Any:
        return self.post(f"{API_PREFIX}/jobs/{_job_path(job_id)}/recover")

    def result(self, job_id: str) -> Any:
        return self.get(f"{API_PREFIX}/jobs/{_job_path(job_id)}/result")

    def artifact(self, job_id: str, alias: str) -> Any:
        if not _ARTIFACT.fullmatch(alias):
            raise ReleaseClientError("artifact_alias_invalid", EXIT_USAGE)
        return self.get(f"{API_PREFIX}/jobs/{_job_path(job_id)}/artifacts/{alias}")

    def inventory(self, dataset_id: str, *, cursor: int, limit: int) -> Any:
        return self.get(
            f"{API_PREFIX}/inventory/{_dataset_segment(dataset_id)}",
            {"cursor": str(cursor), "limit": str(limit)},
        )

    def source_releases(self, dataset_id: str) -> Any:
        return self.get(f"{API_PREFIX}/sources/{_dataset_segment(dataset_id)}/releases")

    def source_release(self, dataset_id: str, handle: str) -> Any:
        path = f"{API_PREFIX}/sources/{_dataset_segment(dataset_id)}/releases/{_handle_segment(handle)}"
        return self.get(path)

    def source_episodes(self, dataset_id: str, handle: str, *, cursor: int, limit: int) -> Any:
        path = f"{API_PREFIX}/sources/{_dataset_segment(dataset_id)}/releases/{_handle_segment(handle)}/episodes"
        return self.get(path, {"cursor": str(cursor), "limit": str(limit)})

    def source_episode_media(self, dataset_id: str, handle: str, *, episode_index: int, camera: str) -> bytes:
        if not isinstance(episode_index, int) or not 0 <= episode_index <= 2**31 - 1:
            raise ReleaseClientError("episode_index_invalid", EXIT_USAGE)
        if camera in {".", ".."} or not _CAMERA.fullmatch(camera):
            raise ReleaseClientError("camera_invalid", EXIT_USAGE)
        path = (
            f"{API_PREFIX}/sources/{_dataset_segment(dataset_id)}/releases/{_handle_segment(handle)}"
            f"/episodes/{episode_index}/media/{camera}"
        )
        return self._transmit("GET", path, accept=MEDIA_ACCEPT)


def wait_for_job(
    client: ReleaseApiClient,
    job_id: str,
    *,
    timeout: float,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], object] = _NEVER_SET.wait,
) -> dict[str, Any]:
    """Poll until the job reaches a terminal state. Returns status only for ``committed``."""
    deadline = clock() + timeout
    while True:
        status = client.status(job_id)
        if not isinstance(status, dict):
            raise ReleaseClientError("invalid_response", EXIT_TRANSPORT)
        state = status.get("state")
        if isinstance(state, str) and state in TERMINAL_JOB_STATES:
            if state != SUCCESS_JOB_STATE:
                raise ReleaseClientError("job_not_committed", EXIT_JOB_NOT_COMMITTED, state=state)
            return status
        remaining = deadline - clock()
        if remaining <= 0:
            raise ReleaseClientError("wait_deadline_exceeded", EXIT_WAIT_TIMEOUT)
        pause(min(interval, remaining))


def _bounded_float(value: float, low: float, high: float) -> float:
    if not (low <= value <= high):
        raise ReleaseClientError("option_out_of_range", EXIT_USAGE)
    return value


def _bounded_int(value: int, low: int, high: int) -> int:
    if not (low <= value <= high):
        raise ReleaseClientError("option_out_of_range", EXIT_USAGE)
    return value


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(name)


def _load_request(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_REQUEST_FILE_BYTES + 1)
    except OSError:
        raise ReleaseClientError("request_file_unreadable", EXIT_USAGE) from None
    if len(raw) > MAX_REQUEST_FILE_BYTES:
        raise ReleaseClientError("request_file_invalid", EXIT_USAGE)
    try:
        payload = json.loads(raw, parse_constant=_reject_constant)
    except ValueError:
        raise ReleaseClientError("request_file_invalid", EXIT_USAGE) from None
    if not isinstance(payload, dict):
        raise ReleaseClientError("request_file_invalid", EXIT_USAGE)
    if set(payload) - REQUEST_FIELDS:
        # Covers server-owned fields (digests, local_disk_acknowledged) that must come from flags.
        raise ReleaseClientError("request_field_refused", EXIT_USAGE)
    if "description" in payload:
        description = payload["description"]
        if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
            raise ReleaseClientError("description_invalid", EXIT_USAGE)
    return payload


def _idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise ReleaseClientError("idempotency_key_invalid", EXIT_USAGE)
    return value


def _digest(value: str) -> str:
    if not _DIGEST.fullmatch(value):
        raise ReleaseClientError("digest_invalid", EXIT_USAGE)
    return value


def _verify(path: str, expected_manifest: str) -> dict[str, Any]:
    from physical_ai_curation.release.store import LocalReleaseStore
    from physical_ai_curation.release.verification import verify_published

    try:
        result = verify_published(LocalReleaseStore(Path(path)), expected_manifest_sha256=expected_manifest)
    except (OSError, ValueError):
        raise ReleaseClientError("verification_failed", EXIT_VERIFY) from None
    return result.model_dump(mode="json")


def _build_client(args: argparse.Namespace, environ: Mapping[str, str]) -> ReleaseApiClient:
    credentials = load_credentials(environ)
    raw_url = args.base_url if args.base_url is not None else environ.get("RELEASE_API_URL", "")
    base_url = parse_base_url(raw_url, credentialed=credentials is not None)
    timeout = _bounded_float(args.timeout_seconds, 1.0, MAX_TIMEOUT_SECONDS)
    return ReleaseApiClient(base_url, credentials=credentials, timeout=timeout)


def _run(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    clock: Callable[[], float],
    pause: Callable[[float], object],
) -> Any:
    command = args.command
    if command == "verify":
        return _verify(args.path, args.expected_manifest)
    if command == "submit" and not (args.confirm and args.ack_local_disk):
        raise ReleaseClientError("submit_not_confirmed", EXIT_REFUSED)
    if command == "submit":
        spec = _load_request(args.request)
        key = _idempotency_key(args.idempotency_key)
        body = {
            **spec,
            "preview_digest": _digest(args.preview_digest),
            "processing_digest": _digest(args.processing_digest),
            "local_disk_acknowledged": True,
        }
        return _build_client(args, environ).submit(body, key)

    client = _build_client(args, environ)
    if command == "preview":
        return client.preview(_load_request(args.request))
    if command == "status":
        return client.status(args.job_id)
    if command == "list":
        return client.jobs(
            cursor=_bounded_int(args.cursor, 0, 2**31 - 1),
            limit=_bounded_int(args.limit, 1, 100),
            dataset_id=args.dataset_id,
        )
    if command == "accepted-request":
        return client.accepted_request(args.idempotency_key)
    if command == "wait":
        return wait_for_job(
            client,
            args.job_id,
            timeout=_bounded_float(args.timeout, 1.0, MAX_WAIT_TIMEOUT_SECONDS),
            interval=_bounded_float(args.interval, 0.001, MAX_WAIT_INTERVAL_SECONDS),
            clock=clock,
            pause=pause,
        )
    if command == "cancel":
        return client.cancel(args.job_id)
    if command == "recover":
        return client.recover(args.job_id)
    if command == "result":
        return client.result(args.job_id)
    if command == "artifact":
        return client.artifact(args.job_id, args.alias)
    if command == "inventory":
        return client.inventory(
            args.dataset_id,
            cursor=_bounded_int(args.cursor, 0, 2**31 - 1),
            limit=_bounded_int(args.limit, 1, 100),
        )
    if command == "source-releases":
        return client.source_releases(args.dataset_id)
    if command == "source-release":
        return client.source_release(args.dataset_id, args.handle)
    if command == "source-episodes":
        return client.source_episodes(
            args.dataset_id,
            args.handle,
            cursor=_bounded_int(args.cursor, 0, 2**31 - 1),
            limit=_bounded_int(args.limit, 1, 100),
        )
    if command == "source-media":
        return _save_media(client, args)
    raise ReleaseClientError("command_unknown", EXIT_USAGE)


def _save_media(client: ReleaseApiClient, args: argparse.Namespace) -> dict[str, Any]:
    content = client.source_episode_media(
        args.dataset_id,
        args.handle,
        episode_index=args.episode_index,
        camera=args.camera,
    )
    try:
        with args.output.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        raise ReleaseClientError("output_exists", EXIT_USAGE) from None
    except OSError:
        with contextlib.suppress(OSError):
            args.output.unlink()
        raise ReleaseClientError("output_unwritable", EXIT_USAGE) from None
    return {"bytes": len(content), "path": str(args.output)}


def _emit_success(command: str, payload: Any, output_format: str) -> None:
    if output_format == "json":
        document = {"schema": CLIENT_SCHEMA, "command": command, "ok": True, "result": payload}
        print(json.dumps(document, sort_keys=True))
        return
    lines = [f"command: {command}"]
    if isinstance(payload, dict):
        for key in sorted(payload):
            lines.append(f"{json.dumps(str(key))}: {json.dumps(payload[key], sort_keys=True)}")
    else:
        lines.append(f"result: {json.dumps(payload, sort_keys=True)}")
    print("\n".join(lines))


def _emit_error(error: ReleaseClientError) -> None:
    detail: dict[str, Any] = {"code": error.code}
    if error.status is not None:
        detail["status"] = error.status
    if error.state is not None:
        detail["state"] = error.state
    document = {"schema": CLIENT_SCHEMA, "ok": False, "error": detail}
    print(json.dumps(document, sort_keys=True), file=sys.stderr)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m physical_ai_curation.release.client",
        description=(
            "Headless client for the curated release API. Credentials: RELEASE_API_KEY or RELEASE_BEARER_TOKEN."
        ),
    )
    parser.add_argument("--base-url", default=None, help="Origin of the release API. Defaults to RELEASE_API_URL.")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview", help="Describe a release selection without reserving output.")
    preview.add_argument("--request", type=Path, required=True)

    submit = commands.add_parser("submit", help="Submit a release job. Requires --confirm and --ack-local-disk.")
    submit.add_argument("--request", type=Path, required=True)
    submit.add_argument("--preview-digest", required=True)
    submit.add_argument("--processing-digest", required=True)
    submit.add_argument("--idempotency-key", required=True)
    submit.add_argument("--ack-local-disk", action="store_true")
    submit.add_argument("--confirm", action="store_true")

    status = commands.add_parser("status", help="Show one job.")
    status.add_argument("job_id")

    jobs = commands.add_parser("list", help="List caller-owned jobs.")
    jobs.add_argument("--cursor", type=int, default=0)
    jobs.add_argument("--limit", type=int, default=50)
    jobs.add_argument("--dataset-id", default=None)

    accepted = commands.add_parser("accepted-request", help="Look up an accepted request by idempotency key.")
    accepted.add_argument("--idempotency-key", required=True)

    wait = commands.add_parser("wait", help="Poll a job until it is terminal.")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=float, default=DEFAULT_WAIT_TIMEOUT_SECONDS)
    wait.add_argument("--interval", type=float, default=DEFAULT_WAIT_INTERVAL_SECONDS)

    for name, help_text in (
        ("cancel", "Request cancellation of a job."),
        ("recover", "Request recovery of a job."),
        ("result", "Show the result of a committed job."),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("job_id")

    artifact = commands.add_parser("artifact", help="Fetch one artifact of a committed job.")
    artifact.add_argument("job_id")
    artifact.add_argument("alias")

    inventory = commands.add_parser("inventory", help="Show the inventory of a dataset.")
    inventory.add_argument("dataset_id")
    inventory.add_argument("--cursor", type=int, default=0)
    inventory.add_argument("--limit", type=int, default=50)

    source_releases = commands.add_parser("source-releases", help="List verified releases of a source dataset.")
    source_releases.add_argument("dataset_id")

    source_release = commands.add_parser("source-release", help="Show one verified source release.")
    source_release.add_argument("dataset_id")
    source_release.add_argument("handle", help="64-character lowercase hex release handle.")

    source_episodes = commands.add_parser("source-episodes", help="Page episode metadata of a verified release.")
    source_episodes.add_argument("dataset_id")
    source_episodes.add_argument("handle")
    source_episodes.add_argument("--cursor", type=int, default=0)
    source_episodes.add_argument("--limit", type=int, default=50)

    source_media = commands.add_parser("source-media", help="Write one episode camera MP4 to a new file.")
    source_media.add_argument("dataset_id")
    source_media.add_argument("handle")
    source_media.add_argument("episode_index", type=int)
    source_media.add_argument("camera")
    source_media.add_argument("--output", type=Path, required=True, help="New file path. Existing files are refused.")

    verify = commands.add_parser("verify", help="Verify a published release directory offline.")
    verify.add_argument("path")
    verify.add_argument("--expected-manifest", required=True, help="Expected manifest SHA-256.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], object] = _NEVER_SET.wait,
) -> int:
    args = _build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        payload = _run(args, env, clock, pause)
    except ReleaseClientError as error:
        _emit_error(error)
        return error.exit_code
    _emit_success(args.command, payload, args.format)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
