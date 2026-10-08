"""Integration and unit tests for label API endpoints."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import src.api.routers.labels as labels_mod
from src.api.models.contributions import MachineOrigin
from src.api.services.dataset_service import get_dataset_service


class LabelBlobProvider:
    container_name = "datasets"

    def __init__(self, content: bytes | None, etag: str = '"revision"') -> None:
        self.content = content
        self.etag = etag
        self.requested_paths = []

    async def _get_client(self) -> LabelBlobProvider:
        return self

    def get_container_client(self, name: str) -> LabelBlobProvider:
        assert name == self.container_name
        return self

    def get_blob_client(self, path: str) -> LabelBlobProvider:
        self.requested_paths.append(path)
        return self

    async def download_blob(self) -> SimpleNamespace:
        if self.content is None:
            raise labels_mod.ResourceNotFoundError("Missing blob")
        return SimpleNamespace(readall=self.readall, properties=SimpleNamespace(etag=self.etag))

    async def readall(self) -> bytes:
        assert self.content is not None
        return self.content

    async def upload_blob(self, content: bytes, **kwargs: object) -> dict[str, str]:
        self.content = content
        self.etag = '"written"'
        return {"etag": self.etag}


@pytest.fixture
def dataset_service(client: TestClient) -> Iterator[MagicMock]:
    """Override the dataset service at the FastAPI dependency boundary."""
    service = MagicMock()
    client.app.dependency_overrides[get_dataset_service] = lambda: service
    yield service
    client.app.dependency_overrides.pop(get_dataset_service, None)


def _revision_headers(client: TestClient, dataset_id: str) -> dict[str, str]:
    response = client.get(f"/api/datasets/{dataset_id}/labels")
    etag = response.headers.get("etag")
    return {"If-Match": etag} if etag else {"If-None-Match": "*"}


async def test_given_equal_label_owners_when_machine_withdrawn_then_human_label_persists(tmp_path: Path) -> None:
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    document = labels_mod.DatasetLabelsFile(dataset_id="ownership")
    document.apply_labels(0, ["SUCCESS"], author_id="alice")
    origin = MachineOrigin(
        run_id="run",
        result_id="result",
        run_order=1,
        source_revision="source",
        input_revision="input",
        config_revision="config",
    )
    document.apply_labels(0, ["SUCCESS"], author_id="alice", machine_origin=origin)
    await storage.save("ownership", document, if_none_match=True)

    loaded = await storage.load_versioned("ownership")
    ledger = loaded.value.provenance["0"]
    assert {item.origin for item in ledger.contributions if item.field == "labels/SUCCESS"} >= {"human", "machine"}
    ledger.withdraw([item.id for item in ledger.contributions if item.origin == "machine"])
    loaded.value.materialize_episode(0, author_id="alice")
    await storage.save("ownership", loaded.value, if_match=loaded.etag)

    assert (await storage.load("ownership")).episodes["0"] == ["SUCCESS"]


async def test_given_overlapping_analysis_when_partial_human_save_then_origins_remain_independent(
    tmp_path: Path,
) -> None:
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    document = labels_mod.DatasetLabelsFile(dataset_id="ownership")
    for order, notes in ((2, "new result"), (1, "old result")):
        document.apply_analysis(
            0,
            labels_mod.EpisodeAnalysisRecord(notes=notes),
            author_id="alice",
            machine_origin=MachineOrigin(
                run_id=f"run-{order}",
                result_id=f"result-{order}",
                run_order=order,
                source_revision="source",
                input_revision="input",
                config_revision="config",
            ),
        )
    document.apply_analysis(0, labels_mod.EpisodeAnalysisRecord(motion_score=85), author_id="alice")
    assert document.analysis["0"].notes == "new result"
    assert document.provenance["0"].resolve("analysis/notes").origin == "machine"
    await storage.save("ownership", document, if_none_match=True)

    loaded = await storage.load("ownership")
    loaded.provenance["0"].withdraw(
        [item.id for item in loaded.provenance["0"].contributions if item.origin == "machine"]
    )
    loaded.materialize_episode(0, author_id="alice")

    assert loaded.analysis["0"].notes is None
    assert loaded.analysis["0"].motion_score == 85


async def test_given_provenance_timestamps_when_blob_roundtrip_then_identity_survives() -> None:
    provider = LabelBlobProvider(None)
    storage = labels_mod.BlobLabelStorage(provider)
    document = labels_mod.DatasetLabelsFile(dataset_id="ownership")
    document.apply_labels(0, ["SUCCESS"], author_id="alice")

    etag = await storage.save("ownership", document, if_none_match=True)
    loaded = await storage.load_versioned("ownership")

    assert loaded.etag == etag
    assert loaded.value.provenance == document.provenance


@pytest.mark.parametrize("intent,origin", [("human-edit", "human"), ("legacy-unknown", "legacy-unknown")])
def test_given_label_intent_when_saved_then_only_explicit_human_edit_claims_authorship(
    client: TestClient, dataset_service: MagicMock, intent: str, origin: str
) -> None:
    response = client.put(
        "/api/datasets/ownership/episodes/0/labels",
        headers=_revision_headers(client, "ownership"),
        json={"labels": ["SUCCESS"], "intent": intent, "author_id": "forged"},
    )
    assert response.status_code == 200

    saved = client.get("/api/datasets/ownership/labels").json()
    contribution = saved["provenance"]["0"]["contributions"][-1]
    assert contribution["origin"] == origin
    assert contribution["author_id"] != "forged"
    assert bool(contribution["author_id"]) == (origin == "human")


@pytest.mark.parametrize("intent,origin", [("human-edit", "human"), ("legacy-unknown", "legacy-unknown")])
def test_given_analysis_source_text_when_saved_then_intent_controls_authorship(
    client: TestClient, dataset_service: MagicMock, intent: str, origin: str
) -> None:
    response = client.put(
        "/api/datasets/ownership/episodes/0/analysis",
        headers=_revision_headers(client, "ownership") | {"X-Curation-Intent": intent},
        json={"notes": "observed", "source": "human"},
    )
    assert response.status_code == 200

    saved = client.get("/api/datasets/ownership/labels").json()
    contributions = saved["provenance"]["0"]["contributions"]
    notes = [item for item in contributions if item["field"] == "analysis/notes"][-1]
    assert notes["origin"] == origin
    assert bool(notes["author_id"]) == (origin == "human")


async def test_given_machine_analysis_when_promoted_then_lineage_and_human_labels_survive(
    client: TestClient, dataset_service: MagicMock, tmp_path: Path
) -> None:
    created = client.put(
        "/api/datasets/ownership/episodes/0/labels",
        headers={"If-None-Match": "*"},
        json={"labels": ["OBJECT: RED"], "intent": "human-edit"},
    )
    assert created.status_code == 200
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    current = await storage.load_versioned("ownership")
    ledger = current.value.provenance["0"]
    author = next(item.author_id for item in ledger.contributions if item.origin == "human")
    origin = MachineOrigin(
        run_id="run",
        result_id="result",
        run_order=1,
        source_revision="source",
        input_revision="input",
        config_revision="config",
    )
    current.value.apply_analysis(
        0, labels_mod.EpisodeAnalysisRecord(object="blue"), author_id=author, machine_origin=origin
    )
    source_id = ledger.resolve("analysis/object", human_author_id=author).contribution_ids[0]
    await storage.save("ownership", current.value, if_match=current.etag)

    response = client.post(
        "/api/datasets/ownership/labels/import-from-analysis",
        headers=_revision_headers(client, "ownership"),
        json={"field": "object", "overwrite": True},
    )

    assert response.status_code == 200
    assert set(response.json()["episodes"]["0"]) == {"OBJECT: RED", "OBJECT: BLUE"}
    loaded = await storage.load("ownership")
    ledger = loaded.provenance["0"]
    contribution = next(item for item in ledger.contributions if item.field == "labels/OBJECT: BLUE")
    assert contribution.origin == "machine" and contribution.machine == origin
    assert contribution.derived_from == [source_id]
    assert ledger.acceptances[contribution.id] == [author]

    repeated = client.post(
        "/api/datasets/ownership/labels/import-from-analysis",
        headers={"If-Match": response.headers["ETag"]},
        json={"field": "object", "overwrite": True},
    )
    assert repeated.status_code == 200
    assert repeated.headers["ETag"] == response.headers["ETag"]

    ledger.withdraw([source_id])
    loaded.materialize_episode(0, author_id=author)
    assert loaded.episodes["0"] == ["OBJECT: RED"]
    assert contribution.id in ledger.withdrawn


def test_get_dataset_labels_returns_defaults(client: TestClient) -> None:
    """GET /labels returns default available_labels for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels")
    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "new-dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
        "provenance": {},
    }


def test_get_label_options_returns_defaults(client: TestClient) -> None:
    """GET /labels/options returns default options for an unknown dataset."""
    response = client.get("/api/datasets/new-dataset/labels/options")
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]


def test_add_label_option_normalizes_and_dedupes(client: TestClient) -> None:
    """POST /labels/options normalizes input and ignores duplicates."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": " review "},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]

    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "review"},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "REVIEW"]


def test_add_label_option_rejects_empty(client: TestClient) -> None:
    """POST /labels/options with whitespace-only label returns 400."""
    response = client.post(
        "/api/datasets/test/labels/options",
        json={"label": "   "},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Label cannot be empty"}


def test_get_episode_labels_unknown_returns_empty(client: TestClient) -> None:
    """GET episode labels returns empty list when episode has no labels."""
    response = client.get("/api/datasets/test/episodes/7/labels")
    assert response.status_code == 200
    assert response.json() == {"episode_index": 7, "labels": []}


def test_set_episode_labels_auto_adds_and_invalidates_cache(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """PUT episode labels auto-adds new labels and invalidates dataset cache."""
    response = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": [" custom ", "success"]},
        headers={"If-None-Match": "*"},
    )
    assert response.status_code == 200
    assert response.json() == {"episode_index": 3, "labels": ["CUSTOM", "SUCCESS"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 3)
    assert response.headers["etag"]

    options_response = client.get("/api/datasets/test/labels/options")
    assert options_response.status_code == 200
    assert options_response.json() == ["SUCCESS", "FAILURE", "PARTIAL", "CUSTOM"]


def test_set_episode_labels_rejects_stale_revision_without_modifying_labels(client: TestClient) -> None:
    created = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": ["SUCCESS"]},
        headers={"If-None-Match": "*"},
    )

    stale = client.put(
        "/api/datasets/test/episodes/3/labels",
        json={"labels": ["FAILURE"]},
        headers={"If-Match": '"stale-revision"'},
    )

    assert created.status_code == 200
    assert stale.status_code == 412
    current = client.get("/api/datasets/test/labels")
    assert current.headers["etag"] == created.headers["etag"]
    assert current.json()["episodes"]["3"] == ["SUCCESS"]


def test_save_all_labels_roundtrip(client: TestClient, dataset_service: MagicMock) -> None:
    """POST /labels/save persists current state and returns full file."""
    update_response = client.put(
        "/api/datasets/test/episodes/1/labels",
        json={"labels": ["SUCCESS"]},
        headers=_revision_headers(client, "test"),
    )
    response = client.post(
        "/api/datasets/test/labels/save",
        headers=_revision_headers(client, "test"),
    )
    assert update_response.status_code == 200
    assert update_response.json() == {"episode_index": 1, "labels": ["SUCCESS"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 1)
    assert response.status_code == 200
    projection = response.json()
    provenance = projection.pop("provenance")
    effective = labels_mod.ContributionLedger.model_validate(provenance["1"]).resolve("labels/SUCCESS")
    assert effective.value is True and effective.origin == "legacy-unknown"
    assert projection == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {"1": ["SUCCESS"]},
        "analysis": {},
    }


def test_delete_label_option_removes_assignments(client: TestClient, dataset_service: MagicMock) -> None:
    """Deleting a label option should also remove it from episode assignments."""
    first_update = client.put(
        "/api/datasets/test-dataset/episodes/1/labels",
        json={"labels": ["SUCCESS", "REVIEW"]},
        headers=_revision_headers(client, "test-dataset"),
    )
    second_update = client.put(
        "/api/datasets/test-dataset/episodes/2/labels",
        json={"labels": ["REVIEW"]},
        headers=_revision_headers(client, "test-dataset"),
    )
    assert first_update.status_code == 200
    assert first_update.json() == {"episode_index": 1, "labels": ["SUCCESS", "REVIEW"]}
    assert second_update.status_code == 200
    assert second_update.json() == {"episode_index": 2, "labels": ["REVIEW"]}
    assert dataset_service.invalidate_episode_cache.call_args_list == [
        call("test-dataset", 1),
        call("test-dataset", 2),
    ]

    response = client.delete(
        "/api/datasets/test-dataset/labels/options/review",
        headers=_revision_headers(client, "test-dataset"),
    )

    assert response.status_code == 200
    assert response.json() == ["SUCCESS", "FAILURE", "PARTIAL"]

    labels_response = client.get("/api/datasets/test-dataset/labels")
    assert labels_response.status_code == 200
    projection = labels_response.json()
    provenance = projection.pop("provenance")
    for ledger_data in provenance.values():
        ledger = labels_mod.ContributionLedger.model_validate(ledger_data)
        assert all(item.id in ledger.withdrawn for item in ledger.contributions if item.field == "labels/REVIEW")
    assert projection == {
        "dataset_id": "test-dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {"1": ["SUCCESS"], "2": []},
        "analysis": {},
    }


def test_delete_default_label_option_rejected(client: TestClient) -> None:
    """Built-in labels should not be deletable."""
    response = client.delete(
        "/api/datasets/test-dataset/labels/options/success",
        headers=_revision_headers(client, "test-dataset"),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Built-in labels cannot be deleted"}


def test_delete_label_option_rejects_empty(client: TestClient) -> None:
    """Whitespace-only label name returns 400."""
    response = client.delete(
        "/api/datasets/test/labels/options/%20",
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Label cannot be empty"}


def test_get_episode_analysis_unknown_returns_null(client: TestClient) -> None:
    """GET episode analysis returns null when no record exists."""
    response = client.get("/api/datasets/test/episodes/4/analysis")
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.parametrize("patch", [{"notes": "reviewed"}, {"notes": None}, {"motion_flags": []}, {}])
def test_given_saved_analysis_when_partial_update_then_preserves_omitted_fields(
    client: TestClient, patch: dict[str, object]
) -> None:
    created = client.put(
        "/api/datasets/test/episodes/5/analysis",
        json={"notes": "original", "motion_score": 3, "grasp_success": True, "motion_flags": ["jittery"]},
        headers={"If-None-Match": "*"},
    )

    updated = client.put(
        "/api/datasets/test/episodes/5/analysis", json=patch, headers={"If-Match": created.headers["etag"]}
    )

    assert updated.status_code == 200
    expected = created.json() | patch
    assert updated.json() == expected
    assert client.get("/api/datasets/test/episodes/5/analysis").json() == expected


def test_set_and_get_episode_analysis_roundtrip(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """PUT analysis persists a structured record, invalidates cache, and rides along /labels."""
    record = {
        "pick_from": "front",
        "object": "black cloth",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach then a missed release.",
        "notes": "Gripper opened early.",
        "normalized_smoothness": 0.2,
        "motion_score": 2,
        "motion_flags": ["jittery"],
        "source": "qwen3-vl",
    }
    expected_record = {
        "pick_from": "front",
        "object": "black cloth",
        "grasp_success": True,
        "place_success": False,
        "movement_quality": "Smooth approach then a missed release.",
        "notes": "Gripper opened early.",
        "instruction": None,
        "duration_s": None,
        "smoothness": None,
        "normalized_smoothness": 0.2,
        "efficiency": None,
        "jitter": None,
        "hesitation_count": None,
        "correction_count": None,
        "motion_score": 2,
        "motion_flags": ["jittery"],
        "source": "qwen3-vl",
    }

    put_resp = client.put(
        "/api/datasets/test/episodes/5/analysis",
        json=record,
        headers=_revision_headers(client, "test"),
    )
    assert put_resp.status_code == 200
    assert put_resp.json() == expected_record
    dataset_service.invalidate_episode_cache.assert_called_once_with("test", 5)

    get_resp = client.get("/api/datasets/test/episodes/5/analysis")
    assert get_resp.status_code == 200
    assert get_resp.json() == expected_record

    labels_response = client.get("/api/datasets/test/labels")
    assert labels_response.status_code == 200
    projection = labels_response.json()
    provenance = projection.pop("provenance")
    ledger = labels_mod.ContributionLedger.model_validate(provenance["5"])
    assert ledger.resolve("analysis/notes").origin == "legacy-unknown"
    assert projection == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {"5": expected_record},
    }


def test_import_analysis_labels_handles_scalar_boolean_and_list_values(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """Analysis imports normalize supported value types and invalidate the dataset cache."""
    client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"object": "Black Cloth", "grasp_success": True, "motion_flags": ["jittery", "hesitation"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/analysis",
        json={"object": "Black Cloth", "grasp_success": False, "motion_flags": []},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    object_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "prefix": "item"},
        headers=_revision_headers(client, "test"),
    )
    assert object_response.status_code == 200
    assert object_response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "ITEM: BLACK CLOTH"],
        "episodes": {"0": ["ITEM: BLACK CLOTH"], "1": ["ITEM: BLACK CLOTH"]},
        "field": "object",
        "prefix": "ITEM",
        "labels_added": ["ITEM: BLACK CLOTH"],
        "episodes_updated": 2,
    }

    grasp_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "grasp_success"},
        headers=_revision_headers(client, "test"),
    )
    assert grasp_response.status_code == 200
    assert grasp_response.json() == {
        "dataset_id": "test",
        "available_labels": [
            "SUCCESS",
            "FAILURE",
            "PARTIAL",
            "ITEM: BLACK CLOTH",
            "GRASP: YES",
            "GRASP: NO",
        ],
        "episodes": {
            "0": ["ITEM: BLACK CLOTH", "GRASP: YES"],
            "1": ["ITEM: BLACK CLOTH", "GRASP: NO"],
        },
        "field": "grasp_success",
        "prefix": "GRASP",
        "labels_added": ["GRASP: YES", "GRASP: NO"],
        "episodes_updated": 2,
    }

    flags_response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "motion_flags"},
        headers=_revision_headers(client, "test"),
    )
    assert flags_response.status_code == 200
    assert flags_response.json() == {
        "dataset_id": "test",
        "available_labels": [
            "SUCCESS",
            "FAILURE",
            "PARTIAL",
            "ITEM: BLACK CLOTH",
            "GRASP: YES",
            "GRASP: NO",
            "FLAG: JITTERY",
            "FLAG: HESITATION",
        ],
        "episodes": {
            "0": ["ITEM: BLACK CLOTH", "GRASP: YES", "FLAG: JITTERY", "FLAG: HESITATION"],
            "1": ["ITEM: BLACK CLOTH", "GRASP: NO"],
        },
        "field": "motion_flags",
        "prefix": "FLAG",
        "labels_added": ["FLAG: JITTERY", "FLAG: HESITATION"],
        "episodes_updated": 1,
    }
    assert dataset_service.invalidate_episode_cache.call_args_list == [
        call("test"),
        call("test"),
        call("test"),
    ]


def test_import_analysis_labels_overwrites_stale_namespace_and_is_idempotent(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    """Overwrite removes stale namespace values and repeated imports make no changes."""
    client.put(
        "/api/datasets/test/episodes/2/labels",
        json={"labels": ["SUCCESS", "OBJECT: OLD"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/2/analysis",
        json={"object": "new"},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 200
    expected_payload = {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "OBJECT: NEW"],
        "episodes": {"2": ["SUCCESS", "OBJECT: NEW"]},
        "field": "object",
        "prefix": "OBJECT",
        "labels_added": ["OBJECT: NEW"],
        "episodes_updated": 1,
    }
    assert response.json() == expected_payload
    dataset_service.invalidate_episode_cache.assert_called_once_with("test")

    dataset_service.invalidate_episode_cache.reset_mock()
    repeated = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )
    assert repeated.status_code == 200
    assert repeated.json() == {
        **expected_payload,
        "labels_added": [],
        "episodes_updated": 0,
    }
    dataset_service.invalidate_episode_cache.assert_not_called()


def test_import_analysis_labels_overwrite_removes_stale_values_without_current_analysis(
    client: TestClient,
    dataset_service: MagicMock,
) -> None:
    client.put(
        "/api/datasets/test/episodes/0/labels",
        json={"labels": ["SUCCESS", "OBJECT: OLD"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/labels",
        json={"labels": ["OBJECT: STALE"]},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"object": "new"},
        headers=_revision_headers(client, "test"),
    )
    client.put(
        "/api/datasets/test/episodes/1/analysis",
        json={"notes": "No object value"},
        headers=_revision_headers(client, "test"),
    )
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "object", "overwrite": True},
        headers=_revision_headers(client, "test"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "OBJECT: NEW"],
        "episodes": {"0": ["SUCCESS", "OBJECT: NEW"], "1": []},
        "field": "object",
        "prefix": "OBJECT",
        "labels_added": ["OBJECT: NEW"],
        "episodes_updated": 2,
    }
    dataset_service.invalidate_episode_cache.assert_called_once_with("test")


def test_import_analysis_labels_rejects_unsupported_field(client: TestClient) -> None:
    """Free-text and unknown analysis fields cannot become dataset labels."""
    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "movement_quality"},
        headers=_revision_headers(client, "test"),
    )
    assert response.status_code == 400
    assert response.json() == {
        "detail": (
            "Field 'movement_quality' is not importable. Allowed: grasp_success, motion_flags, "
            "motion_score, object, pick_from, place_success, source"
        )
    }


def test_import_analysis_labels_without_values_does_not_persist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    dataset_service: MagicMock,
) -> None:
    """Absent and blank analysis values do not generate labels or persist changes."""
    analysis_response = client.put(
        "/api/datasets/test/episodes/0/analysis",
        json={"source": " "},
        headers=_revision_headers(client, "test"),
    )
    assert analysis_response.status_code == 200

    save = AsyncMock()
    monkeypatch.setattr(labels_mod.LocalLabelStorage, "save", save)
    dataset_service.invalidate_episode_cache.reset_mock()

    response = client.post(
        "/api/datasets/test/labels/import-from-analysis",
        json={"field": "source"},
        headers=_revision_headers(client, "test"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "test",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "field": "source",
        "prefix": "SOURCE",
        "labels_added": [],
        "episodes_updated": 0,
    }
    save.assert_not_awaited()
    dataset_service.invalidate_episode_cache.assert_not_called()


async def test_local_storage_save_then_load_roundtrip(tmp_path: Path) -> None:
    """LocalLabelStorage persists and reloads a labels file."""
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    original = labels_mod.DatasetLabelsFile(
        dataset_id="ds",
        available_labels=["A", "B"],
        episodes={"1": ["A"]},
    )

    await storage.save("ds", original)
    loaded = await storage.load("ds")

    assert loaded == original
    path = tmp_path / "ds" / "meta" / "episode_labels.json"
    assert json.loads(path.read_text(encoding="utf-8")) == original.model_dump()


def test_label_update_resolves_nested_dataset_id(
    client: TestClient,
    dataset_service: MagicMock,
    tmp_path: Path,
) -> None:
    """The labels API persists nested dataset IDs beneath the configured root."""
    response = client.put(
        "/api/datasets/owner--dataset/episodes/2/labels",
        json={"labels": [" inspect "]},
        headers=_revision_headers(client, "owner--dataset"),
    )

    assert response.status_code == 200
    assert response.json() == {"episode_index": 2, "labels": ["INSPECT"]}
    dataset_service.invalidate_episode_cache.assert_called_once_with("owner--dataset", 2)

    labels_path = tmp_path / "owner" / "dataset" / "meta" / "episode_labels.json"
    assert labels_path.relative_to(tmp_path) == Path("owner/dataset/meta/episode_labels.json")
    assert list(tmp_path.rglob("episode_labels.json")) == [labels_path]
    persisted = json.loads(labels_path.read_text(encoding="utf-8"))
    provenance = persisted.pop("provenance")
    assert labels_mod.ContributionLedger.model_validate(provenance["2"]).resolve("labels/INSPECT").value is True
    assert persisted == {
        "dataset_id": "owner--dataset",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL", "INSPECT"],
        "episodes": {"2": ["INSPECT"]},
        "analysis": {},
    }


def test_azure_configuration_loads_labels_from_blob_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Azure configuration selects blob-backed labels through the API boundary."""

    provider = LabelBlobProvider(
        json.dumps(
            {
                "dataset_id": "owner--dataset",
                "available_labels": ["SUCCESS", "REVIEWED"],
                "episodes": {"4": ["REVIEWED"]},
                "analysis": {},
            }
        ).encode()
    )
    config = SimpleNamespace(storage_backend="azure")
    provider_factory = MagicMock(return_value=provider)
    monkeypatch.setattr("src.api.config.get_app_config", lambda: config)
    monkeypatch.setattr("src.api.config.create_blob_dataset_provider", provider_factory)

    response = client.get("/api/datasets/owner--dataset/labels")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "owner--dataset",
        "available_labels": ["SUCCESS", "REVIEWED"],
        "episodes": {"4": ["REVIEWED"]},
        "analysis": {},
        "provenance": {},
    }
    provider_factory.assert_called_once_with(config)
    assert provider.requested_paths == ["owner/dataset/meta/episode_labels.json"]


async def test_local_storage_load_missing_returns_defaults(tmp_path: Path) -> None:
    """LocalLabelStorage.load returns defaults when no file exists."""
    storage = labels_mod.LocalLabelStorage(str(tmp_path))
    loaded = await storage.load("missing")
    assert loaded.model_dump() == {
        "dataset_id": "missing",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
        "provenance": {},
    }


async def test_blob_label_storage_rejects_invalid_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=labels_mod.__name__)
    provider = LabelBlobProvider(b"private-annotation-content")
    storage = labels_mod.BlobLabelStorage(provider)

    with pytest.raises(HTTPException, match="Invalid labels data"):
        await storage.load("data\r\nset")

    messages = [record.getMessage() for record in caplog.records if record.name == labels_mod.__name__]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    assert any(record.levelno == logging.DEBUG for record in caplog.records)
    assert any("dataset" in message for message in messages)
    assert all("\r" not in message and "\n" not in message for message in messages)
    assert "private-annotation-content" not in caplog.text


async def test_blob_label_storage_load_missing_returns_defaults() -> None:
    """BlobLabelStorage.load returns defaults when blob is absent."""
    provider = LabelBlobProvider(None)
    storage = labels_mod.BlobLabelStorage(provider)

    result = await storage.load("ds")
    assert provider.requested_paths == ["ds/meta/episode_labels.json"]
    assert result.model_dump() == {
        "dataset_id": "ds",
        "available_labels": ["SUCCESS", "FAILURE", "PARTIAL"],
        "episodes": {},
        "analysis": {},
        "provenance": {},
    }


async def test_blob_label_storage_load_uses_provider_etag() -> None:
    """The validator belongs to the downloaded body, not a later blob version."""
    content = json.dumps(labels_mod.DatasetLabelsFile(dataset_id="ds").model_dump()).encode()
    download = SimpleNamespace(
        readall=AsyncMock(return_value=content), properties=SimpleNamespace(etag='"body-revision"')
    )
    blob_client = SimpleNamespace(
        download_blob=AsyncMock(return_value=download),
        get_blob_properties=AsyncMock(return_value=SimpleNamespace(etag='"newer-revision"')),
    )
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(
        _read_blob_bytes=AsyncMock(
            return_value=json.dumps(labels_mod.DatasetLabelsFile(dataset_id="ds").model_dump()).encode()
        ),
        _get_client=AsyncMock(return_value=client),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    result = await storage.load_versioned("ds")

    assert result.etag == '"body-revision"'
    assert result.value is not None
    assert result.value.dataset_id == "ds"


@pytest.mark.parametrize("content", [b"not-json", b'{"dataset_id":"ds","episodes":[]}'])
async def test_given_corrupt_blob_when_loading_labels_then_fails_visibly(content: bytes) -> None:
    download = SimpleNamespace(readall=AsyncMock(return_value=content), properties=SimpleNamespace(etag='"revision"'))
    blob_client = SimpleNamespace(download_blob=AsyncMock(return_value=download))
    container = SimpleNamespace(get_blob_client=lambda path: blob_client)
    client = SimpleNamespace(get_container_client=lambda name: container)
    provider = SimpleNamespace(
        _read_blob_bytes=AsyncMock(return_value=content),
        _get_client=AsyncMock(return_value=client),
        container_name="datasets",
    )

    with pytest.raises(HTTPException) as error:
        await labels_mod.BlobLabelStorage(provider).load_versioned("ds")

    assert error.value.status_code == 500


async def test_blob_label_storage_load_fails_when_provider_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    """An unavailable authoritative store is not an empty record or synthetic revision."""
    caplog.set_level(logging.DEBUG, logger=labels_mod.__name__)
    provider = SimpleNamespace(
        _get_client=AsyncMock(side_effect=RuntimeError("metadata unavailable")),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    with pytest.raises(HTTPException, match="Failed to load labels"):
        await storage.load_versioned("ds")

    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert any(record.levelno == logging.DEBUG for record in caplog.records)


async def test_given_existing_blob_when_create_only_then_returns_revision_conflict() -> None:
    error = labels_mod.HttpResponseError(message="Already exists")
    error.status_code = 409
    error.error_code = "BlobAlreadyExists"
    blob = SimpleNamespace(upload_blob=AsyncMock(side_effect=error))
    container = SimpleNamespace(get_blob_client=lambda path: blob)
    client = SimpleNamespace(get_container_client=lambda name: container)
    provider = SimpleNamespace(_get_client=AsyncMock(return_value=client), container_name="datasets")

    with pytest.raises(labels_mod.RevisionConflictError):
        await labels_mod.BlobLabelStorage(provider).save(
            "ds", labels_mod.DatasetLabelsFile(dataset_id="ds"), if_none_match=True
        )


async def test_blob_label_storage_save_uploads_json() -> None:
    """BlobLabelStorage.save uploads serialized JSON via the blob client."""
    blob_client = SimpleNamespace(upload_blob=AsyncMock())
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container

    provider = SimpleNamespace(
        _get_client=AsyncMock(return_value=client),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)
    labels_file = labels_mod.DatasetLabelsFile(dataset_id="ds")

    await storage.save("ds", labels_file)

    provider._get_client.assert_awaited_once_with()
    client.get_container_client.assert_called_once_with("datasets")
    container.get_blob_client.assert_called_once_with("ds/meta/episode_labels.json")
    blob_client.upload_blob.assert_awaited_once()
    args, kwargs = blob_client.upload_blob.await_args
    assert len(args) == 1
    assert json.loads(args[0]) == labels_file.model_dump()
    assert kwargs["overwrite"] is True
    assert set(kwargs) == {"overwrite", "content_settings"}
    if labels_mod.ContentSettings is None:
        assert kwargs["content_settings"] is None
    else:
        assert kwargs["content_settings"].content_type == "application/json"


async def test_blob_label_storage_save_uses_matching_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blob label updates use Azure native ETag matching."""
    match_conditions = SimpleNamespace(IfNotModified="if-not-modified")
    monkeypatch.setattr(labels_mod, "MatchConditions", match_conditions)
    blob_client = SimpleNamespace(upload_blob=AsyncMock(return_value={"etag": '"revision-two"'}))
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(_get_client=AsyncMock(return_value=client), container_name="datasets")
    storage = labels_mod.BlobLabelStorage(provider)

    etag = await storage.save(
        "ds",
        labels_mod.DatasetLabelsFile(dataset_id="ds"),
        if_match='"revision-one"',
    )

    assert etag == '"revision-two"'
    kwargs = blob_client.upload_blob.await_args.kwargs
    assert kwargs["etag"] == '"revision-one"'
    assert kwargs["match_condition"] == "if-not-modified"


async def test_blob_label_storage_save_uses_create_only_precondition() -> None:
    """Blob label creation sends Azure's create-only precondition."""
    blob_client = SimpleNamespace(upload_blob=AsyncMock(return_value={}))
    container = MagicMock()
    container.get_blob_client.return_value = blob_client
    client = MagicMock()
    client.get_container_client.return_value = container
    provider = SimpleNamespace(_get_client=AsyncMock(return_value=client), container_name="datasets")
    storage = labels_mod.BlobLabelStorage(provider)

    etag = await storage.save(
        "ds",
        labels_mod.DatasetLabelsFile(dataset_id="ds"),
        if_none_match=True,
    )

    kwargs = blob_client.upload_blob.await_args.kwargs
    assert kwargs["overwrite"] is False
    assert kwargs["if_none_match"] == "*"
    assert etag.startswith('"') and etag.endswith('"')


async def test_blob_label_storage_save_failure_raises_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """BlobLabelStorage.save logs and raises HTTPException(500) on errors."""
    logged: list[tuple[object, ...]] = []
    error = RuntimeError("boom")
    provider = SimpleNamespace(
        _get_client=AsyncMock(side_effect=error),
        container_name="datasets",
    )
    storage = labels_mod.BlobLabelStorage(provider)

    monkeypatch.setattr(
        "src.api.routers.labels.logger.error",
        lambda message, *args: logged.append((message, *args)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await storage.save("ds\r\nx", labels_mod.DatasetLabelsFile(dataset_id="ds"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save labels"
    provider._get_client.assert_awaited_once_with()
    assert logged == [("Failed to save labels blob for %s: %s", "dsx", error)]


@pytest.mark.parametrize(
    ("if_match", "if_none_match", "expected_status"),
    [
        (None, None, 428),
        ('"revision"', "*", 400),
        (None, '"revision"', 400),
    ],
)
def test_revision_precondition_rejects_invalid_header_combinations(if_match, if_none_match, expected_status):
    """Revision preconditions reject missing, conflicting, and non-wildcard headers."""
    with pytest.raises(HTTPException) as exc_info:
        labels_mod.require_revision_precondition(if_match, if_none_match)

    assert exc_info.value.status_code == expected_status


# ---------------------------------------------------------------------------
# Factory + singleton wiring
# ---------------------------------------------------------------------------


def test_labels_path_resolves_nested_dataset_id(monkeypatch, tmp_path):
    """Nested dataset IDs resolve to the expected labels metadata path."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    path = labels_mod._labels_path("owner--dataset")

    assert path == tmp_path / "owner" / "dataset" / "meta" / "episode_labels.json"


def test_create_label_storage_returns_local_when_no_provider():
    """Default backend yields LocalLabelStorage."""
    storage = labels_mod._create_label_storage("local", None)
    assert isinstance(storage, labels_mod.LocalLabelStorage)


@pytest.mark.asyncio
async def test_given_corrupt_local_labels_when_reading_then_fails_with_safe_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "ds" / "meta" / "episode_labels.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"dataset_id":"ds","analysis":"private-invalid-input"}', encoding="utf-8")

    with pytest.raises(HTTPException) as error:
        await labels_mod.LocalLabelStorage(str(tmp_path)).load_versioned("ds")

    assert error.value.status_code == 500
    assert "Invalid local labels" in caplog.text
    assert "private-invalid-input" not in caplog.text


def test_create_label_storage_returns_blob_for_azure():
    """azure backend with a provider yields BlobLabelStorage."""
    provider = SimpleNamespace()
    storage = labels_mod._create_label_storage("azure", provider)
    assert isinstance(storage, labels_mod.BlobLabelStorage)


def test_given_azure_without_provider_when_creating_label_storage_then_refuses_local_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(HTTPException) as error:
        labels_mod._create_label_storage("azure", None)

    assert error.value.status_code == 503
    assert "refusing local fallback" in caplog.text


def test_get_label_storage_singleton(monkeypatch):
    """_get_label_storage caches the storage instance and uses app config."""
    monkeypatch.setattr(labels_mod, "_label_storage", None)
    fake_config = SimpleNamespace(storage_backend="local")
    monkeypatch.setattr(
        "src.api.config.get_app_config",
        lambda: fake_config,
    )

    first = labels_mod._get_label_storage()
    second = labels_mod._get_label_storage()
    assert first is second
    assert isinstance(first, labels_mod.LocalLabelStorage)

    monkeypatch.setattr(labels_mod, "_label_storage", None)


def test_get_label_storage_creates_azure_provider_once(monkeypatch):
    """Azure label storage creates one provider and caches the resulting adapter."""
    monkeypatch.setattr(labels_mod, "_label_storage", None)
    fake_config = SimpleNamespace(storage_backend="azure")
    provider = SimpleNamespace()
    create_provider = MagicMock(return_value=provider)
    monkeypatch.setattr("src.api.config.get_app_config", lambda: fake_config)
    monkeypatch.setattr("src.api.config.create_blob_dataset_provider", create_provider)

    first = labels_mod._get_label_storage()
    second = labels_mod._get_label_storage()

    assert first is second
    assert isinstance(first, labels_mod.BlobLabelStorage)
    assert first._provider is provider
    create_provider.assert_called_once_with(fake_config)

    monkeypatch.setattr(labels_mod, "_label_storage", None)
