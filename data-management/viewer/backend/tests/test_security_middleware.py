"""Tests for security middleware, exception handlers, and hardened endpoints."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def security_client(tmp_path):
    """Test client with a valid HMI_DATA_PATH for security tests."""
    import src.api.config as config_mod
    import src.api.services.annotation_service as ann_mod
    import src.api.services.dataset_service as ds_mod

    os.environ["HMI_DATA_PATH"] = str(tmp_path)
    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None

    from src.api.main import app

    with TestClient(app) as c:
        yield c

    config_mod._app_config = None
    ds_mod._dataset_service = None
    ann_mod._annotation_service = None


# ============================================================================
# Security Headers Middleware
# ============================================================================


class TestSecurityHeaders:
    """Verify OWASP security headers are present on every response."""

    def test_x_content_type_options(self, security_client):
        resp = security_client.get("/health")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_x_frame_options(self, security_client):
        resp = security_client.get("/health")
        assert resp.headers["x-frame-options"] == "DENY"

    def test_referrer_policy(self, security_client):
        resp = security_client.get("/health")
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy(self, security_client):
        resp = security_client.get("/health")
        assert resp.headers["permissions-policy"] == "geolocation=(), microphone=(), camera=()"

    def test_cross_origin_opener_policy(self, security_client):
        resp = security_client.get("/health")
        assert resp.headers["cross-origin-opener-policy"] == "same-origin"

    def test_csp_not_on_api_responses(self, security_client):
        """CSP is only on non-API paths to avoid breaking proxied frontends."""
        resp = security_client.get("/health")
        assert "content-security-policy" not in resp.headers

    def test_csp_not_on_api_endpoints(self, security_client):
        resp = security_client.get("/api/datasets/nonexistent")
        assert "content-security-policy" not in resp.headers

    def test_headers_present_on_error_responses(self, security_client):
        resp = security_client.get("/api/datasets/nonexistent")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"

    def test_no_hsts_header(self, security_client):
        """HSTS is handled at the ingress layer, not by the app."""
        resp = security_client.get("/health")
        assert "strict-transport-security" not in resp.headers


# ============================================================================
# Content Size Limit Middleware
# ============================================================================


class TestContentSizeLimit:
    """Verify request body size enforcement."""

    def test_small_body_accepted(self, security_client):
        resp = security_client.post(
            "/api/datasets/test/episodes/0/annotations/auto",
            json={"data": "small"},
        )
        # Not 413; the request passes the size check (may fail on auth/routing)
        assert resp.status_code != 413

    def test_large_content_length_rejected(self, security_client):
        resp = security_client.post(
            "/api/datasets/test/episodes/0/detect",
            content=b"x",
            headers={"Content-Length": str(20 * 1024 * 1024), "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == "Request body too large"


# ============================================================================
# Exception Handlers
# ============================================================================


class TestExceptionHandlers:
    """Verify custom exception handlers prevent information leakage."""

    def test_500_returns_generic_message(self, security_client):
        """Internal errors should not leak stack traces."""
        resp = security_client.get("/health")
        if resp.status_code == 500:
            assert resp.json()["detail"] == "Internal server error"

    def test_404_does_not_leak_paths(self, security_client):
        resp = security_client.get("/api/datasets/../../etc/passwd")
        assert resp.status_code in (400, 404, 422)


# ============================================================================
# CSRF Cookie Path
# ============================================================================


class TestCsrfCookiePath:
    """Verify CSRF cookie includes Path=/ attribute."""

    def test_csrf_cookie_has_root_path(self, security_client):
        resp = security_client.get("/api/csrf-token")
        assert resp.status_code == 200
        cookie_header = resp.headers.get("set-cookie", "")
        assert "Path=/" in cookie_header


# ============================================================================
# CORS Hardening
# ============================================================================


class TestCorsHardening:
    """Verify CORS uses explicit method and header lists."""

    def test_cors_preflight_allows_explicit_methods(self, security_client):
        resp = security_client.options(
            "/api/datasets",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        allowed = resp.headers.get("access-control-allow-methods", "")
        assert "*" not in allowed

    def test_cors_preflight_allows_explicit_headers(self, security_client):
        resp = security_client.options(
            "/api/datasets",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        allowed = resp.headers.get("access-control-allow-headers", "")
        assert "*" not in allowed


# ============================================================================
# Health Check Enhanced
# ============================================================================


class TestEnhancedHealthCheck:
    """Verify health check includes storage probe."""

    def test_healthy_with_valid_storage(self, security_client):
        resp = security_client.get("/health")
        data = resp.json()
        assert data["status"] in ("healthy", "degraded")
        assert "api" in data["checks"]
        assert "storage" in data["checks"]

    def test_degraded_returns_503(self, tmp_path):
        """Health returns 503 when storage path does not exist."""
        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        nonexistent = str(tmp_path / "does_not_exist")
        os.environ["HMI_DATA_PATH"] = nonexistent
        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

        from src.api.main import app

        with TestClient(app) as c:
            resp = c.get("/health")
            data = resp.json()
            assert data["checks"]["storage"] == "unhealthy"
            assert data["status"] == "degraded"
            assert resp.status_code == 503

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None


# ============================================================================
# Detection Router Rate Limiting & CSRF
# ============================================================================


class TestDetectionSecurity:
    """Verify rate limiting decorators and CSRF on DELETE."""

    def test_delete_detections_requires_csrf_when_auth_enabled(self, tmp_path):
        """DELETE clear_detections requires CSRF token."""
        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod
        from src.api import auth as auth_mod

        os.environ["HMI_DATA_PATH"] = str(tmp_path)
        os.environ.pop("DATAVIEWER_AUTH_DISABLED", None)
        os.environ["DATAVIEWER_AUTH_PROVIDER"] = "apikey"
        os.environ["DATAVIEWER_API_KEY"] = "test-key"
        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None
        auth_mod.reset_auth_provider()

        from src.api.main import app

        with TestClient(app) as c:
            resp = c.delete(
                "/api/datasets/test/episodes/0/detections",
                headers={"X-API-Key": "test-key"},
            )
            assert resp.status_code == 403

        os.environ["DATAVIEWER_AUTH_DISABLED"] = "true"
        os.environ.pop("DATAVIEWER_AUTH_PROVIDER", None)
        os.environ.pop("DATAVIEWER_API_KEY", None)
        auth_mod.reset_auth_provider()
        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

    def test_detection_error_does_not_leak_details(self, security_client):
        """POST detection failure should return generic message."""
        resp = security_client.post(
            "/api/datasets/nonexistent/episodes/0/detect",
            json={},
        )
        if resp.status_code == 500:
            detail = resp.json().get("detail", "")
            assert "Traceback" not in detail
            assert "File " not in detail


# ============================================================================
# Middleware Unit Tests
# ============================================================================


class TestSecurityHeadersMiddleware:
    """Unit tests for SecurityHeadersMiddleware ASGI class."""

    @pytest.mark.asyncio
    async def test_adds_headers_to_http_response(self):
        from src.api.middleware import SecurityHeadersMiddleware

        captured_headers = []

        async def dummy_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = SecurityHeadersMiddleware(dummy_app)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        async def mock_send(message):
            if message["type"] == "http.response.start":
                captured_headers.extend(message["headers"])

        await mw({"type": "http", "path": "/other", "headers": []}, mock_receive, mock_send)

        header_names = [h[0] for h in captured_headers]
        assert b"x-content-type-options" in header_names
        assert b"x-frame-options" in header_names
        assert b"content-security-policy" in header_names

    @pytest.mark.asyncio
    async def test_no_csp_on_api_paths(self):
        from src.api.middleware import SecurityHeadersMiddleware

        captured_headers = []

        async def dummy_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})

        mw = SecurityHeadersMiddleware(dummy_app)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        async def mock_send(message):
            if message["type"] == "http.response.start":
                captured_headers.extend(message["headers"])

        await mw({"type": "http", "path": "/api/datasets", "headers": []}, mock_receive, mock_send)

        header_names = [h[0] for h in captured_headers]
        assert b"x-content-type-options" in header_names
        assert b"content-security-policy" not in header_names

    @pytest.mark.asyncio
    async def test_skips_non_http_scopes(self):
        from src.api.middleware import SecurityHeadersMiddleware

        calls = []

        async def dummy_app(scope, receive, send):
            calls.append(scope["type"])

        mw = SecurityHeadersMiddleware(dummy_app)
        await mw({"type": "websocket"}, None, None)
        assert calls == ["websocket"]

    @pytest.mark.asyncio
    async def test_skips_docs_paths(self):
        from src.api.middleware import SecurityHeadersMiddleware

        captured_headers = []

        async def dummy_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})

        mw = SecurityHeadersMiddleware(dummy_app)

        async def mock_send(message):
            if message["type"] == "http.response.start":
                captured_headers.extend(message["headers"])

        for path in ("/docs", "/redoc", "/openapi.json"):
            captured_headers.clear()
            await mw({"type": "http", "path": path, "headers": []}, None, mock_send)
            header_names = [h[0] for h in captured_headers]
            assert b"content-security-policy" not in header_names, f"CSP should not be on {path}"


class TestContentSizeLimitMiddleware:
    """Unit tests for ContentSizeLimitMiddleware ASGI class."""

    @pytest.mark.asyncio
    async def test_rejects_large_content_length(self):
        from src.api.middleware import ContentSizeLimitMiddleware

        captured = []

        async def dummy_app(scope, receive, send):
            pass

        mw = ContentSizeLimitMiddleware(dummy_app, max_content_length=100)

        async def mock_receive():
            return {"type": "http.request", "body": b""}

        async def mock_send(message):
            captured.append(message)

        scope = {"type": "http", "headers": [(b"content-length", b"200")]}
        await mw(scope, mock_receive, mock_send)

        status = next(m for m in captured if m["type"] == "http.response.start")
        assert status["status"] == 413

    @pytest.mark.asyncio
    async def test_allows_small_body(self):
        from src.api.middleware import ContentSizeLimitMiddleware

        app_called = []

        async def dummy_app(scope, receive, send):
            app_called.append(True)

        mw = ContentSizeLimitMiddleware(dummy_app, max_content_length=1000)

        async def mock_receive():
            return {"type": "http.request", "body": b"small"}

        scope = {"type": "http", "headers": [(b"content-length", b"5")]}
        await mw(scope, mock_receive, lambda m: None)
        assert app_called

    @pytest.mark.asyncio
    async def test_rejects_streaming_body_exceeding_limit(self):
        from src.api.middleware import ContentSizeLimitMiddleware

        captured = []
        chunk_count = 0

        async def dummy_app(scope, receive, send):
            nonlocal chunk_count
            while True:
                msg = await receive()
                chunk_count += 1
                if not msg.get("more_body", False):
                    break

        mw = ContentSizeLimitMiddleware(dummy_app, max_content_length=10)

        chunks = [b"x" * 6, b"x" * 6]
        chunk_iter = iter(chunks)

        async def mock_receive():
            try:
                body = next(chunk_iter)
                return {"type": "http.request", "body": body, "more_body": True}
            except StopIteration:
                return {"type": "http.request", "body": b"", "more_body": False}

        async def mock_send(message):
            captured.append(message)

        scope = {"type": "http", "headers": []}
        await mw(scope, mock_receive, mock_send)

        status = next(m for m in captured if m["type"] == "http.response.start")
        assert status["status"] == 413

    @pytest.mark.asyncio
    async def test_skips_non_http_scopes(self):
        from src.api.middleware import ContentSizeLimitMiddleware

        calls = []

        async def dummy_app(scope, receive, send):
            calls.append(scope["type"])

        mw = ContentSizeLimitMiddleware(dummy_app)
        await mw({"type": "websocket"}, None, None)
        assert calls == ["websocket"]
