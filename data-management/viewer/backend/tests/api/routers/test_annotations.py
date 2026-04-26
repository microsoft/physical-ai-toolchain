"""Unit tests for the annotations router (`src/api/routers/annotations.py`).

Covers GET/PUT/DELETE/auto-analysis/summary endpoints with the dataset
and annotation services mocked out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

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
def client() -> TestClient:
    from src.api.main import app

    with TestClient(app) as c:
        yield c


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
    annotation_service.save_annotation = AsyncMock()
    annotation_service.delete_annotation = AsyncMock(return_value=True)
    annotation_service.run_auto_analysis = AsyncMock()
    annotation_service.get_summary = AsyncMock()

    app.dependency_overrides[get_dataset_service] = lambda: dataset_service
    app.dependency_overrides[get_annotation_service] = lambda: annotation_service
    try:
        yield dataset_service, annotation_service
    finally:
        app.dependency_overrides.pop(get_dataset_service, None)
        app.dependency_overrides.pop(get_annotation_service, None)


# ----------------------------------------------------------------------------
# GET /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


def test_get_annotations_dataset_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.get("/api/datasets/ds-1/episodes/0/annotations")

    assert response.status_code == 404
    assert "ds-1" in response.json()["detail"]


def test_get_annotations_returns_empty_when_none_exist(
    client: TestClient, override_services
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.get_annotation.return_value = None

    response = client.get("/api/datasets/ds-1/episodes/3/annotations")

    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 3
    assert body["dataset_id"] == "ds-1"
    assert body["annotations"] == []


def test_get_annotations_returns_existing_file(
    client: TestClient, override_services
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.get_annotation.return_value = EpisodeAnnotationFile(
        episode_index=2,
        dataset_id="ds-1",
        annotations=[_make_annotation()],
    )

    response = client.get("/api/datasets/ds-1/episodes/2/annotations")

    assert response.status_code == 200
    body = response.json()
    assert body["episode_index"] == 2
    assert len(body["annotations"]) == 1


# ----------------------------------------------------------------------------
# PUT /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


def test_save_annotations_dataset_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None
    payload = _make_annotation().model_dump(mode="json")

    response = client.put("/api/datasets/ds-1/episodes/0/annotations", json=payload)

    assert response.status_code == 404


def test_save_annotations_episode_out_of_range_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=5)
    payload = _make_annotation().model_dump(mode="json")

    response = client.put("/api/datasets/ds-1/episodes/99/annotations", json=payload)

    assert response.status_code == 404
    assert "Episode 99" in response.json()["detail"]


def test_save_annotations_success_invalidates_cache(
    client: TestClient, override_services
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset(total_episodes=10)
    saved = EpisodeAnnotationFile(
        episode_index=4,
        dataset_id="ds-1",
        annotations=[_make_annotation()],
    )
    annotation_service.save_annotation.return_value = saved
    payload = _make_annotation().model_dump(mode="json")

    response = client.put("/api/datasets/ds-1/episodes/4/annotations", json=payload)

    assert response.status_code == 200
    assert response.json()["episode_index"] == 4
    annotation_service.save_annotation.assert_awaited_once()
    dataset_service.invalidate_episode_cache.assert_called_once_with("ds-1", 4)


# ----------------------------------------------------------------------------
# DELETE /datasets/{id}/episodes/{idx}/annotations
# ----------------------------------------------------------------------------


def test_delete_annotations_dataset_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.delete("/api/datasets/ds-1/episodes/0/annotations")

    assert response.status_code == 404


def test_delete_annotations_with_annotator_id(
    client: TestClient, override_services
) -> None:
    dataset_service, annotation_service = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    annotation_service.delete_annotation.return_value = True

    response = client.delete(
        "/api/datasets/ds-1/episodes/2/annotations",
        params={"annotator_id": "user-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "episode_index": 2}
    annotation_service.delete_annotation.assert_awaited_once_with("ds-1", 2, "user-1")
    dataset_service.invalidate_episode_cache.assert_called_once_with("ds-1", 2)


# ----------------------------------------------------------------------------
# POST /datasets/{id}/episodes/{idx}/annotations/auto
# ----------------------------------------------------------------------------


def test_trigger_auto_analysis_dataset_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.post("/api/datasets/ds-1/episodes/0/annotations/auto")

    assert response.status_code == 404


def test_trigger_auto_analysis_episode_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = _make_dataset()
    dataset_service.get_episode.return_value = None

    response = client.post("/api/datasets/ds-1/episodes/0/annotations/auto")

    assert response.status_code == 404
    assert "Episode 0" in response.json()["detail"]


def test_trigger_auto_analysis_success_returns_analysis(
    client: TestClient, override_services
) -> None:
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


def test_get_annotation_summary_dataset_not_found_returns_404(
    client: TestClient, override_services
) -> None:
    dataset_service, _ = override_services
    dataset_service.get_dataset.return_value = None

    response = client.get("/api/datasets/ds-1/annotations/summary")

    assert response.status_code == 404


def test_get_annotation_summary_returns_payload(
    client: TestClient, override_services
) -> None:
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
