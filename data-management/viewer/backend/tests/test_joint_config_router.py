"""Tests for the joint configuration router endpoints."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.api.routers.joint_config as joint_config
from src.api.storage.local_revision import content_etag


class SettingsBlobProvider:
    container_name = "datasets"

    def __init__(self) -> None:
        self.objects = {}
        self.path = ""

    async def _get_client(self) -> SettingsBlobProvider:
        return self

    def get_container_client(self, name: str) -> SettingsBlobProvider:
        assert name == self.container_name
        return self

    def get_blob_client(self, path: str) -> SettingsBlobProvider:
        self.path = path
        return self

    @staticmethod
    def etag(content: bytes) -> str:
        return f'"azure-{content_etag(content).strip(chr(34))}"'

    async def download_blob(self) -> SimpleNamespace:
        from azure.core.exceptions import ResourceNotFoundError

        if self.path not in self.objects:
            error = ResourceNotFoundError("Missing settings")
            error.error_code = "BlobNotFound"
            raise error
        content = self.objects[self.path]

        async def readall() -> bytes:
            return content

        return SimpleNamespace(readall=readall, properties=SimpleNamespace(etag=self.etag(content)))

    async def upload_blob(self, content: bytes, **conditions: object) -> dict[str, str]:
        from azure.core.exceptions import HttpResponseError

        current = self.objects.get(self.path)
        if (not conditions.get("overwrite", True) and current is not None) or (
            conditions.get("etag") is not None
            and conditions["etag"] != (self.etag(current) if current is not None else None)
        ):
            error = HttpResponseError("Revision mismatch")
            error.status_code = 412
            raise error
        self.objects[self.path] = content
        return {"etag": self.etag(content)}


@pytest.fixture(params=["local", "azure"])
def settings_backend(
    request: pytest.FixtureRequest, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> SettingsBlobProvider | None:
    provider = SettingsBlobProvider() if request.param == "azure" else None
    monkeypatch.setattr(joint_config, "_get_blob_provider", lambda: provider, raising=False)
    return provider


@pytest.mark.parametrize("url", ["/api/datasets/ds-one/joint-config", "/api/joint-config/defaults"])
def test_given_revision_when_updating_settings_then_preserves_omissions_and_rejects_stale_writes(
    client: TestClient,
    settings_backend: SettingsBlobProvider | None,
    url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=joint_config.__name__)
    payload = {"labels": {"0": "Private label"}, "groups": [{"id": "arm", "label": "Arm", "indices": [0]}]}
    created = client.put(url, json=payload, headers={"If-None-Match": "*"})
    assert created.status_code == 200
    original_etag = created.headers["etag"]
    assert client.get(url).headers["etag"] == original_etag

    updated = client.put(url, json={"labels": {}}, headers={"If-Match": original_etag})
    assert updated.status_code == 200
    assert updated.json()["groups"] == payload["groups"]
    assert updated.json()["labels"] == {}
    assert client.put(url, json=payload, headers={"If-Match": original_etag}).status_code == 412
    assert client.put(url, json=payload, headers={"If-None-Match": "*"}).status_code == 412
    assert client.get(url).json() == updated.json()
    assert "save conflict" in caplog.text
    assert "Saved joint configuration" in caplog.text
    assert "Private label" not in caplog.text
    if settings_backend is not None:
        blob_path = "joint_config_defaults.json" if url.endswith("defaults") else "ds-one/meta/joint_config.json"
        assert json.loads(settings_backend.objects[blob_path])["groups"] == payload["groups"]
        assert not list(tmp_path.rglob("joint_config*.json"))


def test_given_azure_without_provider_when_accessing_settings_then_refuses_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(joint_config, "get_app_config", lambda: SimpleNamespace(storage_backend="azure"))
    monkeypatch.setattr(joint_config, "get_dataset_service", lambda: SimpleNamespace(_blob_provider=None))

    with pytest.raises(HTTPException) as error:
        joint_config._get_blob_provider()

    assert error.value.status_code == 503
    assert "refusing local fallback" in caplog.text


@pytest.mark.parametrize("error_code", ["ContainerNotFound", None])
def test_given_unavailable_blob_container_when_reading_settings_then_fails_visibly(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error_code: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from azure.core.exceptions import ResourceNotFoundError

    provider = SettingsBlobProvider()

    async def unavailable() -> None:
        error = ResourceNotFoundError("Private backend failure")
        error.error_code = error_code
        raise error

    monkeypatch.setattr(provider, "download_blob", unavailable)
    monkeypatch.setattr(joint_config, "_get_blob_provider", lambda: provider)
    response = client.get("/api/datasets/ds-one/joint-config")

    assert response.status_code == 500
    assert "Failed to read joint configuration" in caplog.text
    assert "Private backend failure" not in caplog.text
    assert not list(tmp_path.rglob("joint_config*.json"))


@pytest.mark.parametrize("content", [b'{"labels": "private-invalid-input"}', b'{"dataset_id": "wrong-scope"}'])
def test_given_corrupt_settings_when_reading_then_fails_without_defaults_or_payload_logs(
    client: TestClient,
    tmp_path: Path,
    settings_backend: SettingsBlobProvider | None,
    caplog: pytest.LogCaptureFixture,
    content: bytes,
) -> None:
    if settings_backend is not None:
        settings_backend.objects["ds-one/meta/joint_config.json"] = content
    else:
        path = tmp_path / "ds-one" / "meta" / "joint_config.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)

    response = client.get("/api/datasets/ds-one/joint-config")

    assert response.status_code == 500
    assert "Invalid joint configuration" in caplog.text
    assert "private-invalid-input" not in caplog.text


@pytest.mark.parametrize("url", ["/api/datasets/ds-one/joint-config", "/api/joint-config/defaults"])
def test_given_no_revision_when_saving_joint_config_then_requires_precondition(client: TestClient, url: str) -> None:
    response = client.put(url, json={"labels": {}, "groups": []})

    assert response.status_code == 428


class TestDatasetJointConfig:
    """Per-dataset joint configuration endpoints."""

    def test_given_nested_dataset_when_reading_and_saving_then_preserves_state_names_and_dataset_path(
        self,
        client: TestClient,
        tmp_path: Path,
        settings_backend: SettingsBlobProvider | None,
    ) -> None:
        root = tmp_path / "robot" / "lerobot"
        (root / "meta").mkdir(parents=True)
        (root / "data").mkdir()
        (root / "meta" / "info.json").write_text(
            json.dumps(
                {
                    "codebase_version": "v2.1",
                    "fps": 30,
                    "total_episodes": 1,
                    "chunks_size": 1000,
                    "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
                    "features": {"observation.state": {"dtype": "float32", "shape": [2], "names": ["arm", "gripper"]}},
                }
            ),
            encoding="utf-8",
        )
        (root / "meta" / "tasks.jsonl").write_text("", encoding="utf-8")

        initial = client.get("/api/datasets/robot--lerobot/joint-config")
        assert initial.status_code == 200
        assert initial.json()["labels"] == {"0": "arm", "1": "gripper"}
        assert not (root / "meta" / "joint_config.json").exists()
        assert not (tmp_path / "robot--lerobot").exists()

        saved = client.put(
            "/api/datasets/robot--lerobot/joint-config",
            json={"labels": {"0": "Custom"}},
            headers={"If-None-Match": "*"},
        )
        assert saved.status_code == 200
        assert client.get("/api/datasets/robot--lerobot/joint-config").json()["labels"] == {"0": "Custom"}
        if settings_backend is None:
            assert (root / "meta" / "joint_config.json").exists()
        else:
            assert "robot/lerobot/meta/joint_config.json" in settings_backend.objects
        assert not (tmp_path / "robot--lerobot").exists()

    def test_get_returns_defaults_without_writing(self, client: TestClient, tmp_path: Path) -> None:
        response = client.get("/api/datasets/ds-one/joint-config")
        assert response.status_code == 200

        body = response.json()
        assert body["dataset_id"] == "ds-one"
        # Hardcoded defaults: 16 labels and 6 groups.
        assert len(body["labels"]) == 16
        assert body["labels"]["0"] == "Right X"
        assert len(body["groups"]) == 6

        assert not (tmp_path / "ds-one").exists()
        assert "etag" not in response.headers

    def test_get_returns_persisted_config(self, client, tmp_path):
        config_file = tmp_path / "ds-two" / "meta" / "joint_config.json"
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

    def test_get_uses_global_defaults_when_present(self, client, tmp_path):
        defaults_file = tmp_path / "joint_config_defaults.json"
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
        response = client.put("/api/datasets/ds-write/joint-config", json=new_config, headers={"If-None-Match": "*"})
        assert response.status_code == 200
        body = response.json()
        assert body["dataset_id"] == "ds-write"
        assert body["labels"] == new_config["labels"]
        assert body["groups"][0]["indices"] == [0, 1]

        # Round-trip through GET.
        get_response = client.get("/api/datasets/ds-write/joint-config")
        assert get_response.status_code == 200
        assert get_response.json()["labels"] == new_config["labels"]

    def test_put_creates_meta_directory_if_missing(self, client, tmp_path):
        response = client.put(
            "/api/datasets/ds-mkdir/joint-config",
            json={"labels": {}, "groups": []},
            headers={"If-None-Match": "*"},
        )
        assert response.status_code == 200
        meta_dir = tmp_path / "ds-mkdir" / "meta"
        assert meta_dir.is_dir()
        assert (meta_dir / "joint_config.json").exists()

    def test_get_rejects_path_traversal_dataset_id(self, client):
        response = client.get("/api/datasets/..%2Fevil/joint-config")
        assert response.status_code == 404

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

    def test_get_returns_persisted_defaults(self, client, tmp_path):
        defaults_file = tmp_path / "joint_config_defaults.json"
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

    def test_put_writes_global_defaults(self, client, tmp_path):
        payload = {
            "labels": {"0": "Updated"},
            "groups": [{"id": "g", "label": "G", "indices": [0]}],
        }
        response = client.put("/api/joint-config/defaults", json=payload, headers={"If-None-Match": "*"})
        assert response.status_code == 200
        body = response.json()
        assert body["dataset_id"] == "_defaults"
        assert body["labels"] == {"0": "Updated"}

        defaults_file = tmp_path / "joint_config_defaults.json"
        assert defaults_file.exists()
        on_disk = json.loads(defaults_file.read_text(encoding="utf-8"))
        assert on_disk["labels"] == {"0": "Updated"}
