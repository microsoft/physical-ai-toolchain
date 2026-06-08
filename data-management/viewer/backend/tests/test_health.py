"""Health check tests."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


class TestHealthCheck:
    def test_health_check_returns_200(self, client):
        """Test health endpoint returns structured response with checks."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["checks"]["api"] == "healthy"
        assert data["checks"]["storage"] == "healthy"

    def test_health_check_includes_storage_probe(self, client):
        """Verify storage check is present in health response."""
        response = client.get("/health")
        assert "storage" in response.json()["checks"]


class TestHealthCheckAzureBranch:
    """The azure-mode health branch reports based on blob_provider presence."""

    async def test_azure_with_blob_provider_returns_healthy(self, monkeypatch):
        import src.api.main as main_mod

        monkeypatch.setattr(main_mod, "_config", SimpleNamespace(storage_backend="azure"), raising=False)
        service = MagicMock()
        service._blob_provider = object()
        monkeypatch.setattr(
            "src.api.services.dataset_service.get_dataset_service",
            lambda: service,
        )

        response = await main_mod.health_check()
        body = json.loads(bytes(response.body).decode("utf-8"))
        assert response.status_code == 200
        assert body["status"] == "healthy"
        assert body["checks"]["storage"] == "healthy"

    async def test_azure_without_blob_provider_returns_unhealthy(self, monkeypatch):
        import src.api.main as main_mod

        monkeypatch.setattr(main_mod, "_config", SimpleNamespace(storage_backend="azure"), raising=False)
        service = MagicMock()
        service._blob_provider = None
        monkeypatch.setattr(
            "src.api.services.dataset_service.get_dataset_service",
            lambda: service,
        )

        response = await main_mod.health_check()
        body = json.loads(bytes(response.body).decode("utf-8"))
        assert response.status_code == 503
        assert body["status"] == "degraded"
        assert body["checks"]["storage"] == "unhealthy"
