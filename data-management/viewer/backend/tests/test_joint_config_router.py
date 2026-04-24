"""Tests for the joint configuration router endpoints."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers import joint_config


@pytest.fixture
def client():
    """TestClient with HMI_DATA_PATH pointing to an isolated temp directory."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HMI_DATA_PATH"] = tmp

        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

        with TestClient(app) as c:
            c.tmp_path = tmp  # type: ignore[attr-defined]
            yield c

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None


class TestDatasetJointConfig:
    """Per-dataset joint configuration endpoints."""

    def test_get_creates_from_hardcoded_defaults_when_missing(self, client):
        response = client.get("/api/datasets/ds-one/joint-config")
        assert response.status_code == 200

        body = response.json()
        assert body["dataset_id"] == "ds-one"
        # Hardcoded defaults: 16 labels and 6 groups.
        assert len(body["labels"]) == 16
        assert body["labels"]["0"] == "Right X"
        assert len(body["groups"]) == 6

        # File should now be persisted on disk.
        config_file = Path(client.tmp_path) / "ds-one" / "meta" / "joint_config.json"
        assert config_file.exists()
        on_disk = json.loads(config_file.read_text(encoding="utf-8"))
        assert on_disk["dataset_id"] == "ds-one"

    def test_get_returns_persisted_config(self, client):
        config_file = Path(client.tmp_path) / "ds-two" / "meta" / "joint_config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dataset_id": "ds-two",
            "labels": {"0": "Custom"},
            "groups": [{"id": "g1", "label": "Group", "indices": [0, 1]}],
        }
        config_file.write_text(json.dumps(payload), encoding="utf-8")

        response = client.get("/api/datasets/ds-two/joint-config")
        assert response.status_code == 200
        body = response.json()
        assert body["labels"] == {"0": "Custom"}
        assert body["groups"][0]["id"] == "g1"

    def test_get_uses_global_defaults_when_present(self, client):
        defaults_file = Path(client.tmp_path) / "joint_config_defaults.json"
        defaults_file.write_text(
            json.dumps(
                {
                    "dataset_id": "_defaults",
                    "labels": {"0": "Override"},
                    "groups": [{"id": "g0", "label": "Override", "indices": [0]}],
                }
            ),
            encoding="utf-8",
        )

        response = client.get("/api/datasets/ds-three/joint-config")
        assert response.status_code == 200
        body = response.json()
        assert body["labels"] == {"0": "Override"}
        assert body["groups"][0]["id"] == "g0"

    def test_put_persists_new_config(self, client):
        new_config = {
            "labels": {"0": "Joint A", "1": "Joint B"},
            "groups": [{"id": "arm", "label": "Arm", "indices": [0, 1]}],
        }
        response = client.put("/api/datasets/ds-write/joint-config", json=new_config)
        assert response.status_code == 200
        body = response.json()
        assert body["dataset_id"] == "ds-write"
        assert body["labels"] == new_config["labels"]
        assert body["groups"][0]["indices"] == [0, 1]

        # Round-trip through GET.
        get_response = client.get("/api/datasets/ds-write/joint-config")
        assert get_response.status_code == 200
        assert get_response.json()["labels"] == new_config["labels"]

    def test_put_creates_meta_directory_if_missing(self, client):
        response = client.put(
            "/api/datasets/ds-mkdir/joint-config",
            json={"labels": {}, "groups": []},
        )
        assert response.status_code == 200
        meta_dir = Path(client.tmp_path) / "ds-mkdir" / "meta"
        assert meta_dir.is_dir()
        assert (meta_dir / "joint_config.json").exists()

    def test_get_rejects_path_traversal_dataset_id(self, client):
        # SAFE_DATASET_ID_PATTERN forbids "../" in the id.
        response = client.get("/api/datasets/..%2Fevil/joint-config")
        # Either 404 (no route match after URL decoding) or 400 from validation.
        assert response.status_code in {400, 404, 422}

    def test_get_rejects_invalid_dataset_id(self, client):
        # Leading dot violates SAFE_DATASET_ID_PATTERN.
        response = client.get("/api/datasets/.hidden/joint-config")
        assert response.status_code == 400


class TestGlobalDefaults:
    """Global joint configuration defaults endpoints."""

    def test_get_returns_hardcoded_defaults_when_missing(self, client):
        response = client.get("/api/joint-config/defaults")
        assert response.status_code == 200
        body = response.json()
        assert body["dataset_id"] == "_defaults"
        assert len(body["labels"]) == 16
        assert len(body["groups"]) == 6

    def test_get_returns_persisted_defaults(self, client):
        defaults_file = Path(client.tmp_path) / "joint_config_defaults.json"
        defaults_file.write_text(
            json.dumps(
                {
                    "dataset_id": "_defaults",
                    "labels": {"0": "Persisted"},
                    "groups": [],
                }
            ),
            encoding="utf-8",
        )

        response = client.get("/api/joint-config/defaults")
        assert response.status_code == 200
        body = response.json()
        assert body["labels"] == {"0": "Persisted"}
        assert body["groups"] == []

    def test_put_writes_global_defaults(self, client):
        payload = {
            "labels": {"0": "Updated"},
            "groups": [{"id": "g", "label": "G", "indices": [0]}],
        }
        response = client.put("/api/joint-config/defaults", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["dataset_id"] == "_defaults"
        assert body["labels"] == {"0": "Updated"}

        defaults_file = Path(client.tmp_path) / "joint_config_defaults.json"
        assert defaults_file.exists()
        on_disk = json.loads(defaults_file.read_text(encoding="utf-8"))
        assert on_disk["labels"] == {"0": "Updated"}


class TestModuleHelpers:
    """Direct unit tests for module-level helpers and constants."""

    def test_hardcoded_defaults_shape(self):
        config = joint_config._hardcoded_defaults()
        assert config.dataset_id == "_defaults"
        assert len(config.labels) == 16
        assert len(config.groups) == 6
        # Returns a fresh dict each call to avoid shared mutable state.
        config.labels["0"] = "mutated"
        assert joint_config._hardcoded_defaults().labels["0"] == "Right X"

    def test_get_base_path_default(self, monkeypatch):
        monkeypatch.delenv("HMI_DATA_PATH", raising=False)
        assert joint_config._get_base_path() == "./data"

    def test_get_base_path_from_env(self, monkeypatch):
        monkeypatch.setenv("HMI_DATA_PATH", "/some/path")
        assert joint_config._get_base_path() == "/some/path"
