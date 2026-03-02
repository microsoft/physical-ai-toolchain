"""
Integration tests for annotation API endpoints.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)
from src.api.models.datasources import DatasetInfo, FeatureSchema
from src.api.services.dataset_service import get_dataset_service


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_dataset():
    """Create a sample dataset for testing."""
    return DatasetInfo(
        id="test-dataset",
        name="Test Dataset",
        total_episodes=100,
        fps=30.0,
        features={
            "action": FeatureSchema(dtype="float32", shape=[7]),
        },
        tasks=[],
    )


@pytest.fixture
async def registered_dataset(sample_dataset):
    """Register a sample dataset before tests."""
    service = get_dataset_service()
    await service.register_dataset(sample_dataset)
    yield sample_dataset
    # Cleanup
    service._datasets.clear()


@pytest.fixture
def sample_annotation():
    """Create a sample annotation for testing."""
    return EpisodeAnnotation(
        annotator_id="test-user",
        timestamp=datetime.utcnow(),
        task_completeness=TaskCompletenessAnnotation(
            rating=TaskCompletenessRating.SUCCESS,
            confidence=ConfidenceLevel.FOUR,
            completion_percentage=100,
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
            issues=[],
        ),
        anomalies=AnomalyAnnotation(anomalies=[]),
        notes="Test annotation",
    )


class TestAnnotationEndpoints:
    """Tests for annotation API endpoints."""

    @pytest.mark.asyncio
    async def test_get_annotations_empty(self, client, registered_dataset):
        """Test getting annotations when none exist."""
        response = client.get("/api/datasets/test-dataset/episodes/0/annotations")
        assert response.status_code == 200

        data = response.json()
        assert data["episode_index"] == 0
        assert data["dataset_id"] == "test-dataset"
        assert data["annotations"] == []

    def test_get_annotations_dataset_not_found(self, client):
        """Test getting annotations for non-existent dataset."""
        response = client.get("/api/datasets/nonexistent/episodes/0/annotations")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_save_annotation(self, client, registered_dataset, sample_annotation):
        """Test saving an annotation."""
        response = client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        assert response.status_code == 200

        data = response.json()
        assert data["episode_index"] == 5
        assert len(data["annotations"]) == 1
        assert data["annotations"][0]["annotator_id"] == "test-user"

    @pytest.mark.asyncio
    async def test_save_annotation_updates_existing(self, client, registered_dataset, sample_annotation):
        """Test that saving updates existing annotation from same user."""
        # Save initial annotation
        client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )

        # Update annotation
        sample_annotation.notes = "Updated notes"
        response = client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["annotations"]) == 1  # Still only one annotation
        assert data["annotations"][0]["notes"] == "Updated notes"

    @pytest.mark.asyncio
    async def test_save_annotation_multiple_annotators(self, client, registered_dataset, sample_annotation):
        """Test multiple annotators can annotate same episode."""
        # Save first annotation
        client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )

        # Save second annotation from different user
        sample_annotation.annotator_id = "other-user"
        response = client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data["annotations"]) == 2

    def test_save_annotation_dataset_not_found(self, client, sample_annotation):
        """Test saving annotation to non-existent dataset."""
        response = client.put(
            "/api/datasets/nonexistent/episodes/0/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_annotations_all(self, client, registered_dataset, sample_annotation):
        """Test deleting all annotations for an episode."""
        # Save annotation
        client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )

        # Delete all annotations
        response = client.delete("/api/datasets/test-dataset/episodes/5/annotations")
        assert response.status_code == 200
        assert response.json()["deleted"] is True

        # Verify deleted
        get_response = client.get("/api/datasets/test-dataset/episodes/5/annotations")
        assert get_response.json()["annotations"] == []

    @pytest.mark.asyncio
    async def test_delete_annotations_specific_annotator(self, client, registered_dataset, sample_annotation):
        """Test deleting annotations from specific annotator."""
        # Save annotations from two users
        client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        sample_annotation.annotator_id = "other-user"
        client.put(
            "/api/datasets/test-dataset/episodes/5/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )

        # Delete only test-user's annotation
        response = client.delete("/api/datasets/test-dataset/episodes/5/annotations?annotator_id=test-user")
        assert response.status_code == 200

        # Verify only other-user remains
        get_response = client.get("/api/datasets/test-dataset/episodes/5/annotations")
        annotations = get_response.json()["annotations"]
        assert len(annotations) == 1
        assert annotations[0]["annotator_id"] == "other-user"


class TestAnnotationSummaryEndpoint:
    """Tests for annotation summary endpoint."""

    @pytest.mark.asyncio
    async def test_get_summary_empty(self, client, registered_dataset):
        """Test getting summary when no annotations exist."""
        response = client.get("/api/datasets/test-dataset/annotations/summary")
        assert response.status_code == 200

        data = response.json()
        assert data["dataset_id"] == "test-dataset"
        assert data["total_episodes"] == 100
        assert data["annotated_episodes"] == 0

    @pytest.mark.asyncio
    async def test_get_summary_with_annotations(self, client, registered_dataset, sample_annotation):
        """Test summary aggregates annotation metrics."""
        # Save some annotations
        for idx in [0, 5, 10]:
            client.put(
                f"/api/datasets/test-dataset/episodes/{idx}/annotations",
                json=sample_annotation.model_dump(mode="json"),
            )

        response = client.get("/api/datasets/test-dataset/annotations/summary")
        assert response.status_code == 200

        data = response.json()
        assert data["annotated_episodes"] == 3
        assert "success" in data["task_completeness_distribution"]

    def test_get_summary_dataset_not_found(self, client):
        """Test getting summary for non-existent dataset."""
        response = client.get("/api/datasets/nonexistent/annotations/summary")
        assert response.status_code == 404


class TestAutoAnalysisEndpoint:
    """Tests for auto-analysis endpoint."""

    @pytest.mark.asyncio
    async def test_trigger_auto_analysis(self, client, registered_dataset):
        """Test triggering auto-analysis."""
        response = client.post("/api/datasets/test-dataset/episodes/5/annotations/auto")
        assert response.status_code == 200

        data = response.json()
        assert data["episode_index"] == 5
        assert "computed" in data
        assert "suggested_rating" in data
        assert 1 <= data["suggested_rating"] <= 5

    def test_auto_analysis_dataset_not_found(self, client):
        """Test auto-analysis for non-existent dataset."""
        response = client.post("/api/datasets/nonexistent/episodes/0/annotations/auto")
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
