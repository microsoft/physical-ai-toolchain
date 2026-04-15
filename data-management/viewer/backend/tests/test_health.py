"""Health check tests."""


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
