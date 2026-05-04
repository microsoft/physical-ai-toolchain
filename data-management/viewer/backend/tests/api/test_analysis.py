"""Tests for analysis router stub endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.analysis import router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_analyze_trajectory_quality_returns_not_implemented():
    response = _client().post("/trajectory-quality")
    assert response.status_code == 200
    assert response.json() == {"status": "not_implemented"}


def test_detect_anomalies_returns_not_implemented():
    response = _client().post("/anomaly-detection")
    assert response.status_code == 200
    assert response.json() == {"status": "not_implemented"}
