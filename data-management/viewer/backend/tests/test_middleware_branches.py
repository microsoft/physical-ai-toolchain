"""Branch coverage for middleware skip paths and body size enforcement."""

from __future__ import annotations

import asyncio

import pytest

from src.api.middleware import ContentSizeLimitMiddleware, SecurityHeadersMiddleware


def _run(coro):
    return asyncio.run(coro)


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _streaming_app(scope, receive, send):
    while True:
        msg = await receive()
        if not msg.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(path: str = "/api/test", headers: list[tuple[bytes, bytes]] | None = None):
    return {"type": "http", "path": path, "headers": headers or []}


class _Sender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message):
        self.messages.append(message)


class TestSecurityHeadersSkipPaths:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_skip_paths_bypass_header_injection(self, path):
        sender = _Sender()
        middleware = SecurityHeadersMiddleware(_ok_app)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        _run(middleware(_http_scope(path=path), receive, sender))

        start = next(m for m in sender.messages if m["type"] == "http.response.start")
        assert start["headers"] == []


class TestContentSizeLimitBranches:
    def test_invalid_content_length_header_falls_through(self):
        sender = _Sender()
        middleware = ContentSizeLimitMiddleware(_ok_app, max_content_length=1024)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = _http_scope(headers=[(b"content-length", b"not-a-number")])
        _run(middleware(scope, receive, sender))

        start = next(m for m in sender.messages if m["type"] == "http.response.start")
        assert start["status"] == 200

    def test_streaming_body_over_limit_returns_413(self):
        sender = _Sender()
        middleware = ContentSizeLimitMiddleware(_streaming_app, max_content_length=8)

        chunks = [
            {"type": "http.request", "body": b"x" * 5, "more_body": True},
            {"type": "http.request", "body": b"y" * 10, "more_body": False},
        ]
        iterator = iter(chunks)

        async def receive():
            return next(iterator)

        _run(middleware(_http_scope(), receive, sender))

        start = next(m for m in sender.messages if m["type"] == "http.response.start")
        assert start["status"] == 413
        body = b"".join(m.get("body", b"") for m in sender.messages if m["type"] == "http.response.body")
        assert b"too large" in body.lower()
