"""OWASP security headers and request body size enforcement middleware."""

from __future__ import annotations

from typing import ClassVar

from starlette.responses import JSONResponse as _StarletteJSONResponse


class SecurityHeadersMiddleware:
    """Inject OWASP security headers on all HTTP responses.

    CSP is only applied to non-API paths to avoid interfering with
    frontend dev servers that proxy API responses and inherit headers.
    """

    HEADERS: ClassVar[list[tuple[bytes, bytes]]] = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
        (b"cross-origin-opener-policy", b"same-origin"),
    ]

    CSP_HEADER: ClassVar[tuple[bytes, bytes]] = (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        b"img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'",
    )

    _SKIP_PATHS: ClassVar[set[str]] = {"/docs", "/redoc", "/openapi.json"}

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self._SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        is_api = path.startswith("/api") or path == "/health"
        extra_headers = self.HEADERS if is_api else [*self.HEADERS, self.CSP_HEADER]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(extra_headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ContentSizeLimitMiddleware:
    """Reject requests exceeding the configured body size limit.

    Checks Content-Length header upfront and tracks actual bytes
    received to catch chunked transfer-encoding requests.
    """

    def __init__(self, app, max_content_length: int = 10 * 1024 * 1024):
        self.app = app
        self.max_content_length = max_content_length

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")

        if content_length is not None:
            try:
                if int(content_length) > self.max_content_length:
                    response = _StarletteJSONResponse(
                        {"detail": "Request body too large"},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        bytes_received = 0
        limit = self.max_content_length

        async def receive_with_limit():
            nonlocal bytes_received
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                bytes_received += len(body)
                if bytes_received > limit:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, receive_with_limit, send)
        except _BodyTooLargeError:
            response = _StarletteJSONResponse(
                {"detail": "Request body too large"},
                status_code=413,
            )
            await response(scope, receive, send)


class _BodyTooLargeError(Exception):
    """Internal signal for body size limit exceeded."""
