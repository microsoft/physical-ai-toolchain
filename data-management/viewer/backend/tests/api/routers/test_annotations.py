"""Unit tests for the annotations router (`src/api/routers/annotations.py`).

Covers GET/PUT/DELETE/auto-analysis/summary endpoints with the dataset
and annotation services mocked out.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.auth import PrincipalContext, require_principal_context
from src.api.models.annotations import (
    AnnotationSummary,
    AnomalyAnnotation,
    AutoQualityAnalysis,
    ComputedQualityMetrics,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    EpisodeAnnotationFile,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)
from src.api.models.datasources import DatasetInfo, EpisodeData, EpisodeMeta
from src.api.storage import LocalStorageAdapter, RevisionConflictError, StorageError, VersionedValue


def _make_dataset(dataset_id: str = "ds-1", total_episodes: int = 10) -> DatasetInfo:
    return DatasetInfo(
        id=dataset_id,
        name=dataset_id,
        total_episodes=total_episodes,
        fps=30.0,
    )


def _make_annotation() -> EpisodeAnnotation:
    return EpisodeAnnotation(
        annotator_id="user-1",
        timestamp="2025-01-01T00:00:00Z",
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FIVE,
        ),
        trajectory_quality=TrajectoryQualityAnnotation(
            overall_score=QualityScore.FOUR,
            metrics=TrajectoryQualityMetrics(
                smoothness=QualityScore.FOUR,
                efficiency=QualityScore.FOUR,
                safety=QualityScore.FIVE,
                precision=QualityScore.FOUR,
            ),
            flags=[],
        ),
        data_quality=DataQualityAnnotation(
            overall_quality=DataQualityLevel.GOOD,
        ),
        anomalies=AnomalyAnnotation(anomalies=[]),
    )


@pytest.fixture
def override_services():
    from src.api.main import app
    from src.api.services.annotation_service import get_annotation_service
    from src.api.services.dataset_service import get_dataset_service

    dataset_service = MagicMock()
    dataset_service.get_dataset = AsyncMock(return_value=None)
    dataset_service.get_episode = AsyncMock(return_value=None)
    dataset_service.invalidate_episode_cache = MagicMock()

    annotation_service = MagicMock()
    annotation_service.get_annotation = AsyncMock(return_value=None)
    annotation_service.get_annotation_versioned = AsyncMock(return_value=VersionedValue(value=None, etag=None))
    annotation_service.save_annotation = AsyncMock()
    annotation_service.delete_annotation = AsyncMock(return_value=True)
    annotation_service.run_auto_analysis = AsyncMock()
    annotation_service.get_summary = AsyncMock()

    app.dependency_overrides[get_dataset_service] = lambda: dataset_service
    app.dependency_overrides[get_annotation_service] = lambda: annotation_service
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
        scope_id="principal-scope",
        auth_mode="azure_ad",
    )
    try:
        yield dataset_service, annotation_service
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)
        app.dependency_overrides.pop(get_annotation_service, None)
        app.dependency_overrides.pop(require_principal_context, None)


@pytest.fixture
def real_edit_services(accessibility_dataset_path: Path, tmp_path: Path) -> Iterator[dict[str, str]]:
    from src.api.main import app
    from src.api.services.annotation_service import AnnotationService, get_annotation_service
    from src.api.services.dataset_service import DatasetService, get_dataset_service

    datasets = DatasetService(base_path=str(accessibility_dataset_path), episode_cache_capacity=0)
    annotations = AnnotationService(base_path=str(tmp_path))
    principal = {"scope_id": "author-a"}
    app.dependency_overrides[get_dataset_service] = lambda: datasets
    app.dependency_overrides[get_annotation_service] = lambda: annotations
    app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
        scope_id=principal["scope_id"],
        auth_mode="azure_ad",
    )
    try:
        yield principal
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)
        app.dependency_overrides.pop(get_annotation_service, None)
        app.dependency_overrides.pop(require_principal_context, None)


def test_given_cached_episode_when_source_replaced_then_edit_validation_uses_current_metadata(
    client: TestClient,
    real_edit_services: dict[str, str],
    accessibility_dataset_path: Path,
    tmp_path: Path,
) -> None:
    import shutil

    from src.api.main import app
    from src.api.services.dataset_service import DatasetService, get_dataset_service

    h5py = pytest.importorskip("h5py")
    root = tmp_path / "source"
    shutil.copytree(accessibility_dataset_path, root)
    datasets = DatasetService(base_path=str(root))
    app.dependency_overrides[get_dataset_service] = lambda: datasets
    url = "/api/datasets/a11y-synthetic/episodes/0/edits"
    initial = client.get(url)
    assert initial.status_code == 200
    episode = root / "a11y-synthetic" / "episode_0.hdf5"
    shutil.copyfile(root / "a11y-synthetic" / "episode_1.hdf5", episode)
    with h5py.File(episode, "r+") as source:
        del source["observations/images/wrist"]
    fresh = client.get(url)
    assert fresh.status_code == 200
    assert fresh.json()["source_revision"] != initial.json()["source_revision"]
    payload = {
        "source_id": fresh.json()["source_id"],
        "source_revision": fresh.json()["source_revision"],
        "operations": {
            "datasetId": "a11y-synthetic",
            "episodeIndex": 0,
            "cameraTransforms": {"wrist": {"resize": {"width": 16, "height": 16}}},
        },
    }
    assert client.put(url, json=payload, headers={"If-None-Match": "*"}).status_code == 422
    payload["operations"] = {"datasetId": "a11y-synthetic", "episodeIndex": 0, "removedFrames": [15]}
    assert client.put(url, json=payload, headers={"If-None-Match": "*"}).status_code == 200


def test_given_real_episode_when_saving_edits_then_conditional_author_scoped_state_survives(
    client: TestClient,
    real_edit_services: dict[str, str],
) -> None:
    url = "/api/datasets/a11y-synthetic/episodes/0/edits"
    initial = client.get(url)
    assert initial.status_code == 200
    assert initial.json()["saved"] is None
    assert "etag" not in initial.headers
    payload = {
        "source_id": initial.json()["source_id"],
        "source_revision": initial.json()["source_revision"],
        "operations": {"datasetId": "a11y-synthetic", "episodeIndex": 0, "removedFrames": [2]},
    }
    assert client.put(url, json=payload).status_code == 428
    saved = client.put(url, json=payload, headers={"If-None-Match": "*"})
    assert saved.status_code == 200
    assert saved.json()["saved"]["author_id"] == "author-a"
    assert client.get(url).json() == saved.json()
    assert client.put(url, json=payload, headers={"If-None-Match": "*"}).status_code == 412
    assert (
        client.put(
            url,
            json={**payload, "source_revision": "obsolete"},
            headers={"If-Match": saved.headers["etag"]},
        ).status_code
        == 409
    )
    assert (
        client.put(
            url,
            json={**payload, "author_id": "forged"},
            headers={"If-Match": saved.headers["etag"]},
        ).status_code
        == 422
    )

    real_edit_services["scope_id"] = "author-b"
    other = client.get(url)
    assert other.status_code == 200
    assert other.json()["saved"] is None
    assert "etag" not in other.headers


# ----------------------------------------------------------------------------
# GET /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid",
    [
        {"removedFrames": [12]},
        {"removedFrames": [-1]},
        {"cameraTransforms": {"missing": {}}},
        {"insertedFrames": [{"afterFrameIndex": 11, "interpolationFactor": 0.5}]},
        {"trajectoryAdjustments": [{"frameIndex": 12}]},
        {"globalTransform": {"resize": {"width": 0, "height": 20}}},
        {"globalTransform": {"unsupported": True}},
        {"episodeIndex": 1},
    ],
)
def test_given_invalid_edit_when_saving_then_no_descriptor_is_created(
    client: TestClient,
    real_edit_services: dict[str, str],
    invalid: dict,
) -> None:
    url = "/api/datasets/a11y-synthetic/episodes/0/edits"
    initial = client.get(url).json()
    response = client.put(
        url,
        headers={"If-None-Match": "*"},
        json={
            "source_id": initial["source_id"],
            "source_revision": initial["source_revision"],
            "operations": {"datasetId": "a11y-synthetic", "episodeIndex": 0, **invalid},
        },
    )
    assert response.status_code == 422
    assert client.get(url).json()["saved"] is None


def test_given_failed_edit_write_when_saving_then_error_is_visible_without_payloads(
    client: TestClient,
    real_edit_services: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail_write(*_args: object, **_kwargs: object) -> str:
        raise StorageError("private-edit-payload")

    monkeypatch.setattr(LocalStorageAdapter, "save_annotation", fail_write)
    url = "/api/datasets/a11y-synthetic/episodes/0/edits"
    initial = client.get(url).json()
    response = client.put(
        url,
        headers={"If-None-Match": "*"},
        json={
            "source_id": initial["source_id"],
            "source_revision": initial["source_revision"],
            "operations": {"datasetId": "a11y-synthetic", "episodeIndex": 0, "removedFrames": [2]},
        },
    )
    assert response.status_code == 500
    assert "persistence unavailable" in caplog.text
    assert "private-edit-payload" not in caplog.text + response.text
    assert client.get(url).json()["saved"] is None


def test_given_saved_edits_when_backend_process_restarts_then_state_survives(
    client: TestClient,
    real_edit_services: dict[str, str],
    accessibility_dataset_path: Path,
    tmp_path: Path,
) -> None:
    url = "/api/datasets/a11y-synthetic/episodes/0/edits"
    initial = client.get(url).json()
    saved = client.put(
        url,
        headers={"If-None-Match": "*"},
        json={
            "source_id": initial["source_id"],
            "source_revision": initial["source_revision"],
            "operations": {"datasetId": "a11y-synthetic", "episodeIndex": 0, "removedFrames": [2]},
        },
    )
    assert saved.status_code == 200
    result_path = tmp_path / "restarted-response.json"
    program = """
import json
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from src.api.main import app
from src.api.auth import PrincipalContext, require_principal_context
from src.api.services.annotation_service import AnnotationService, get_annotation_service
from src.api.services.dataset_service import DatasetService, get_dataset_service
app.dependency_overrides[get_dataset_service] = lambda: DatasetService(base_path=sys.argv[1], episode_cache_capacity=0)
app.dependency_overrides[get_annotation_service] = lambda: AnnotationService(base_path=sys.argv[2])
app.dependency_overrides[require_principal_context] = lambda: PrincipalContext(
    scope_id='author-a', auth_mode='azure_ad'
)
with TestClient(app) as client:
    response = client.get('/api/datasets/a11y-synthetic/episodes/0/edits')
    assert response.status_code == 200
    Path(sys.argv[3]).write_text(json.dumps({'body': response.json(), 'etag': response.headers['etag']}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, str(accessibility_dataset_path), str(tmp_path), str(result_path)],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "STORAGE_BACKEND": "local", "DATA_DIR": str(accessibility_dataset_path)},
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    restored = json.loads(result_path.read_text())
    assert restored["body"] == saved.json()
    assert restored["etag"] == saved.headers["etag"]


def test_get_annotations_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.get("/api/datasets/ds-1/episodes/0/annotations")

    assert response.status_code == 404
    assert "ds-1" in response.json()["detail"]


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        ("dataset", 404),
        ("revision", 503),
        ("episode-read", 503),
        ("episode", 404),
        ("empty", 404),
        ("recheck", 503),
        ("changed", 409),
    ],
)
def test_given_unavailable_edit_source_when_reading_then_reports_specific_safe_failure(
    client: TestClient,
    override_services: tuple,
    failure: str,
    status: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    datasets, _ = override_services
    datasets.get_dataset.return_value = None if failure == "dataset" else _make_dataset()
    datasets.get_source_revision = AsyncMock(return_value=("source", "revision"))
    datasets.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=0 if failure == "empty" else 10, task_index=0),
    )
    if failure == "revision":
        datasets.get_source_revision.side_effect = ValueError("private-source-detail")
    elif failure == "episode-read":
        datasets.get_episode.side_effect = ValueError("private-source-detail")
    elif failure == "episode":
        datasets.get_episode.return_value = None
    elif failure == "recheck":
        datasets.get_source_revision.side_effect = [("source", "revision"), ValueError("private-source-detail")]
    elif failure == "changed":
        datasets.get_source_revision.side_effect = [("source", "revision"), ("source", "replacement")]

    response = client.get("/api/datasets/ds-1/episodes/0/edits")

    assert response.status_code == status
    assert "private-source-detail" not in caplog.text + response.text


@pytest.mark.parametrize("has_etag", [False, True])
def test_given_stale_saved_edit_when_reading_then_returns_source_conflict(
    client: TestClient,
    override_services: tuple,
    has_etag: bool,
) -> None:
    from src.api.services.annotation_service import EditSourceChangedError

    datasets, annotations = override_services
    datasets.get_dataset.return_value = _make_dataset()
    datasets.get_source_revision = AsyncMock(return_value=("source", "revision"))
    datasets.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=10, task_index=0),
    )
    annotations.get_saved_edits = AsyncMock(side_effect=EditSourceChangedError('"saved-v1"' if has_etag else None))

    response = client.get("/api/datasets/ds-1/episodes/0/edits")

    assert response.status_code == 409
    assert response.json()["detail"]["source_revision"] == "revision"
    assert response.headers.get("etag") == ('"saved-v1"' if has_etag else None)


@pytest.mark.parametrize("failure", ["read", "acknowledgment"])
def test_given_edit_storage_failure_when_accessing_then_no_success_acknowledgment(
    client: TestClient,
    override_services: tuple,
    failure: str,
) -> None:
    datasets, annotations = override_services
    datasets.get_dataset.return_value = _make_dataset()
    datasets.get_source_revision = AsyncMock(return_value=("source", "revision"))
    datasets.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=0, length=10, task_index=0),
    )
    if failure == "read":
        annotations.get_saved_edits = AsyncMock(side_effect=StorageError("private-storage-detail"))
        response = client.get("/api/datasets/ds-1/episodes/0/edits")
    else:
        annotations.save_edits = AsyncMock(return_value=VersionedValue(value=None, etag=None))
        response = client.put(
            "/api/datasets/ds-1/episodes/0/edits",
            headers={"If-None-Match": "*"},
            json={
                "source_id": "source",
                "source_revision": "revision",
                "operations": {"datasetId": "ds-1", "episodeIndex": 0},
            },
        )

    assert response.status_code == 500
    assert "private-storage-detail" not in response.text


def test_get_annotations_returns_empty_when_none_exist(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.get_annotation.return_value = None

    response = client.get("/api/datasets/ds-1/episodes/3/annotations")

    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 3
    assert body["dataset_id"] == "ds-1"
    assert body["annotations"] == []


def test_get_annotations_returns_existing_file(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_file = EpisodeAnnotationFile(
        episode_index=2,
        dataset_id="ds-1",
        annotations=[_make_annotation()],
    )
    annotation_service.get_annotation_versioned.return_value = VersionedValue(
        value=annotation_file,
        etag='"revision-one"',
    )

    response = client.get("/api/datasets/ds-1/episodes/2/annotations")

    assert response.status_code == 200
    assert response.headers["etag"] == '"revision-one"'
    body = response.json()
    assert body["episode_index"] == 2
    assert len(body["annotations"]) == 1


# ----------------------------------------------------------------------------
# PUT /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


def test_save_annotations_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None
    payload = _make_annotation().model_dump(mode="json")

    response = client.put("/api/datasets/ds-1/episodes/0/annotations", json=payload)

    assert response.status_code == 404


def test_save_annotations_episode_out_of_range_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=5)
    payload = _make_annotation().model_dump(mode="json")

    response = client.put("/api/datasets/ds-1/episodes/99/annotations", json=payload)

    assert response.status_code == 404
    assert "Episode 99" in response.json()["detail"]


def test_save_annotations_uses_authenticated_owner_and_invalidates_cache(
    client: TestClient,
    override_services,
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=10)
    saved = EpisodeAnnotationFile(
        episode_index=4,
        dataset_id="ds-1",
        annotations=[_make_annotation()],
    )
    annotation_service.save_annotation.return_value = VersionedValue(value=saved, etag='"saved-revision"')
    payload = _make_annotation().model_dump(mode="json")

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=payload,
        headers={"If-None-Match": "*"},
    )

    assert response.status_code == 200
    assert response.json()["episode_index"] == 4
    saved_annotation = annotation_service.save_annotation.await_args.args[2]
    assert saved_annotation.annotator_id == "principal-scope"
    dataset_service.invalidate_episode_cache.assert_called_once_with("ds-1", 4)


def test_save_annotations_requires_revision_precondition(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=_make_annotation().model_dump(mode="json"),
    )

    assert response.status_code == 428


def test_save_annotations_rejects_multiple_revision_preconditions(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=_make_annotation().model_dump(mode="json"),
        headers={"If-Match": '"revision"', "If-None-Match": "*"},
    )

    assert response.status_code == 400


def test_save_annotations_rejects_non_wildcard_create_precondition(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=_make_annotation().model_dump(mode="json"),
        headers={"If-None-Match": '"revision"'},
    )

    assert response.status_code == 400


def test_save_annotations_rejects_missing_saved_value(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.save_annotation.return_value = VersionedValue(value=None, etag='"revision"')

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=_make_annotation().model_dump(mode="json"),
        headers={"If-None-Match": "*"},
    )

    assert response.status_code == 500


def test_save_annotations_returns_412_with_current_revision(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.save_annotation.side_effect = RevisionConflictError('"current-revision"')

    response = client.put(
        "/api/datasets/ds-1/episodes/4/annotations",
        json=_make_annotation().model_dump(mode="json"),
        headers={"If-Match": '"stale-revision"'},
    )

    assert response.status_code == 412
    assert response.json()["details"]["currentEtag"] == '"current-revision"'


# ----------------------------------------------------------------------------
# DELETE /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


def test_delete_annotations_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.delete("/api/datasets/ds-1/episodes/0/annotations")

    assert response.status_code == 404


def test_delete_annotations_requires_current_revision(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()

    response = client.delete("/api/datasets/ds-1/episodes/0/annotations")

    assert response.status_code == 428


def test_delete_annotations_ignores_client_owner_and_uses_authenticated_owner(
    client: TestClient,
    override_services,
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.delete_annotation.return_value = True

    response = client.delete(
        "/api/datasets/ds-1/episodes/2/annotations",
        params={"annotator_id": "other-user"},
        headers={"If-Match": '"revision-one"'},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "episode_index": 2}
    annotation_service.delete_annotation.assert_awaited_once_with(
        "ds-1",
        2,
        "principal-scope",
        if_match='"revision-one"',
    )
    dataset_service.invalidate_episode_cache.assert_called_once_with("ds-1", 2)


# ----------------------------------------------------------------------------
# POST /datasets/{id}/episodes/{idx}/annotations/auto
# ----------------------------------------------------------------------------


def test_trigger_auto_analysis_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.post("/api/datasets/ds-1/episodes/0/annotations/auto")

    assert response.status_code == 404


def test_trigger_auto_analysis_episode_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    dataset_service.get_episode.return_value = None

    response = client.post("/api/datasets/ds-1/episodes/0/annotations/auto")

    assert response.status_code == 404
    assert "Episode 0" in response.json()["detail"]


def test_trigger_auto_analysis_success_returns_analysis(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    dataset_service.get_episode.return_value = EpisodeData(
        meta=EpisodeMeta(index=1, length=5, task_index=0, has_annotations=False),
        video_urls={},
        cameras=[],
        trajectory_data=[],
    )
    annotation_service.run_auto_analysis.return_value = AutoQualityAnalysis(
        episode_index=1,
        computed=ComputedQualityMetrics(
            smoothness_score=0.9,
            efficiency_score=0.8,
            jitter_metric=0.1,
            hesitation_count=0,
            correction_count=0,
        ),
        suggested_rating=4,
        confidence=0.85,
        flags=[],
    )

    response = client.post("/api/datasets/ds-1/episodes/1/annotations/auto")

    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 1
    assert body["suggested_rating"] == 4
    annotation_service.run_auto_analysis.assert_awaited_once()


# ----------------------------------------------------------------------------
# GET /datasets/{id}/annotations/summary
# ----------------------------------------------------------------------------


def test_get_annotation_summary_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.get("/api/datasets/ds-1/annotations/summary")

    assert response.status_code == 404


def test_get_annotation_summary_returns_payload(client: TestClient, override_services) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=42)
    annotation_service.get_summary.return_value = AnnotationSummary(
        dataset_id="ds-1",
        total_episodes=42,
        annotated_episodes=10,
    )

    response = client.get("/api/datasets/ds-1/annotations/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_episodes"] == 42
    assert body["annotated_episodes"] == 10
    annotation_service.get_summary.assert_awaited_once_with("ds-1", 42)


def test_get_grounding_overview_returns_annotated_and_usable(client: TestClient, override_services) -> None:
    from src.api.models.annotations import GroundingEpisode, GroundingOverview

    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=42)
    annotation_service.get_grounding_overview = AsyncMock(
        return_value=GroundingOverview(
            dataset_id="ds-1",
            annotated=[1, 4],
            usable=[GroundingEpisode(episode_index=4, annotator_id="alice")],
        )
    )

    response = client.get("/api/datasets/ds-1/annotations/grounding")

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "ds-1",
        "annotated": [1, 4],
        "usable": [{"episode_index": 4, "annotator_id": "alice", "labels_revision": None}],
    }


def test_given_saved_labels_and_dataset_task_when_grounding_then_no_annotation_is_required(
    client: TestClient,
    override_services,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from physical_ai_curation.models import DatasetLabelsFile
    from physical_ai_curation.saved_input import label_baseline
    from physical_ai_dataset_io.lerobot import EpisodeRecord

    from src.api.models.annotations import GroundingOverview
    from src.api.routers import labels as label_router

    saved = DatasetLabelsFile(dataset_id="ds-1", episodes={"0": ["SUCCESS"], "1": ["FAILURE", "PARTIAL"]})
    path = tmp_path / "ds-1" / "meta" / "episode_labels.json"
    path.parent.mkdir(parents=True)
    path.write_text(saved.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(label_router, "_label_storage", None)
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    dataset_service.get_episode_media_record = AsyncMock(
        return_value=EpisodeRecord(
            episode_id="ds-1/episode_000000",
            episode_index=0,
            instruction="Transfer the cube",
            fps=30,
            length=10,
            video_paths={},
            from_timestamp=None,
            to_timestamp=None,
        )
    )
    annotation_service.get_grounding_overview = AsyncMock(return_value=GroundingOverview(dataset_id="ds-1"))

    response = client.get("/api/datasets/ds-1/annotations/grounding")

    assert response.status_code == 200
    assert response.json()["usable"] == [
        {
            "episode_index": 0,
            "annotator_id": None,
            "labels_revision": label_baseline(saved, 0)["revision"],
        }
    ]


def test_get_grounding_overview_hides_storage_failure_details(
    client: TestClient, override_services, caplog: pytest.LogCaptureFixture
) -> None:
    from src.api.storage import StorageError

    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=42)
    annotation_service.get_grounding_overview = AsyncMock(side_effect=StorageError("https://account/blob?sig=secret"))

    response = client.get("/api/datasets/ds-1/annotations/grounding")

    assert response.status_code == 503
    assert "secret" not in response.text
    assert "secret" not in caplog.text
    assert "Grounding overview failed" in caplog.text


def test_get_grounding_overview_dataset_not_found_returns_404(client: TestClient, override_services) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    assert client.get("/api/datasets/ds-1/annotations/grounding").status_code == 404
