"""
Integration tests for annotation API endpoints.
"""

import asyncio
import os
import tempfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.models.annotations import (
    AnomalyAnnotation,
    ConfidenceLevel,
    DataQualityAnnotation,
    DataQualityLevel,
    EpisodeAnnotation,
    InstructionSource,
    LanguageInstructionAnnotation,
    QualityScore,
    TaskCompletenessAnnotation,
    TaskCompletenessRating,
    TrajectoryQualityAnnotation,
    TrajectoryQualityMetrics,
)
from src.api.models.datasources import DatasetInfo, FeatureSchema


@pytest.fixture
def client():
    """Create test client with isolated singletons and empty temp data path."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATA_DIR"] = tmp

        import src.api.config as config_mod
        import src.api.services.annotation_service as ann_mod
        import src.api.services.dataset_service as ds_mod

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None

        with TestClient(app) as c:
            yield c

        config_mod._app_config = None
        ds_mod._dataset_service = None
        ann_mod._annotation_service = None


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
def registered_dataset(client, sample_dataset):
    """Register a sample dataset before tests."""
    import src.api.services.dataset_service as ds_mod

    service = ds_mod.get_dataset_service()
    asyncio.run(service.register_dataset(sample_dataset))
    yield sample_dataset
    service._datasets.clear()


@pytest.fixture
def sample_annotation():
    """Create a sample annotation for testing."""
    return EpisodeAnnotation(
        annotator_id="test-user",
        timestamp=datetime.now(UTC),
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

    def test_get_annotations_empty(self, client, registered_dataset):
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

    def test_save_annotation(self, client, registered_dataset, sample_annotation):
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

    def test_save_annotation_updates_existing(self, client, registered_dataset, sample_annotation):
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

    def test_save_annotation_multiple_annotators(self, client, registered_dataset, sample_annotation):
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

    def test_delete_annotations_all(self, client, registered_dataset, sample_annotation):
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

    def test_delete_annotations_specific_annotator(self, client, registered_dataset, sample_annotation):
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

    def test_get_summary_empty(self, client, registered_dataset):
        """Test getting summary when no annotations exist."""
        response = client.get("/api/datasets/test-dataset/annotations/summary")
        assert response.status_code == 200

        data = response.json()
        assert data["dataset_id"] == "test-dataset"
        assert data["total_episodes"] == 100
        assert data["annotated_episodes"] == 0

    def test_get_summary_with_annotations(self, client, registered_dataset, sample_annotation):
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

    def test_trigger_auto_analysis(self, client, registered_dataset):
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


class TestLanguageInstructionRoundTrip:
    """Persist and retrieve annotations carrying a language instruction payload."""

    def test_save_and_load_language_instruction(self, client, registered_dataset, sample_annotation):
        sample_annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="pick the red block",
            source=InstructionSource.HUMAN,
            paraphrases=["grab the red cube", "lift the red block"],
            subtask_instructions=["approach", "grasp", "lift"],
        )

        save = client.put(
            "/api/datasets/test-dataset/episodes/3/annotations",
            json=sample_annotation.model_dump(mode="json"),
        )
        assert save.status_code == 200

        get = client.get("/api/datasets/test-dataset/episodes/3/annotations")
        assert get.status_code == 200

        annotations = get.json()["annotations"]
        assert len(annotations) == 1
        language = annotations[0]["language_instruction"]
        assert language is not None
        assert language["instruction"] == "pick the red block"
        assert language["source"] == "human"
        assert language["paraphrases"] == ["grab the red cube", "lift the red block"]
        assert language["subtask_instructions"] == ["approach", "grasp", "lift"]

    def test_rejects_oversized_paraphrases_list(self, client, registered_dataset, sample_annotation):
        """Excessively long paraphrase lists must fail validation at the API."""
        oversized = ["paraphrase"] * 100
        sample_annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="pick",
            source=InstructionSource.HUMAN,
            paraphrases=["seed"],
        )
        payload = sample_annotation.model_dump(mode="json")
        payload["language_instruction"]["paraphrases"] = oversized

        response = client.put(
            "/api/datasets/test-dataset/episodes/4/annotations",
            json=payload,
        )
        assert response.status_code == 422

    def test_rejects_oversized_paraphrase_item(self, client, registered_dataset, sample_annotation):
        """Per-item length cap mirrors the primary instruction bound."""
        sample_annotation.language_instruction = LanguageInstructionAnnotation(
            instruction="pick",
            source=InstructionSource.HUMAN,
            paraphrases=["seed"],
        )
        payload = sample_annotation.model_dump(mode="json")
        payload["language_instruction"]["paraphrases"] = ["x" * 1001]

        response = client.put(
            "/api/datasets/test-dataset/episodes/4/annotations",
            json=payload,
        )
        assert response.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
