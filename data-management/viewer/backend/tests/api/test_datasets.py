"""Integration tests for dataset API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.models.datasources import DatasetInfo, FeatureSchema, TaskInfo
from src.api.services.dataset_service import DatasetService, get_dataset_service


@pytest.fixture
def sample_dataset():
    """Create a sample dataset for testing."""
    return DatasetInfo(
        id="test-dataset",
        name="Test Dataset",
        total_episodes=100,
        fps=30.0,
        features={
            "observation.images.top": FeatureSchema(dtype="video", shape=[480, 640, 3]),
            "action": FeatureSchema(dtype="float32", shape=[7]),
        },
        tasks=[
            TaskInfo(task_index=0, description="Pick up object"),
            TaskInfo(task_index=1, description="Place object"),
        ],
    )


@pytest.fixture
async def registered_dataset(client, sample_dataset):
    """Register a sample dataset before tests."""
    import src.api.services.dataset_service as ds_mod

    service = ds_mod.get_dataset_service()
    await service.register_dataset(sample_dataset)
    return sample_dataset


class TestDatasetEndpoints:
    """Tests for dataset API endpoints."""

    def test_list_datasets_empty(self, client):
        """Test listing datasets when none are registered."""
        response = client.get("/api/datasets")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_datasets_with_data(self, client, registered_dataset):
        """Test listing datasets returns registered datasets."""
        response = client.get("/api/datasets")
        assert response.status_code == 200

        datasets = response.json()
        assert len(datasets) == 1
        assert datasets[0]["id"] == "test-dataset"
        assert datasets[0]["name"] == "Test Dataset"
        assert datasets[0]["total_episodes"] == 100

    def test_get_dataset(self, client, registered_dataset):
        """Test getting a specific dataset."""
        response = client.get("/api/datasets/test-dataset")
        assert response.status_code == 200

        dataset = response.json()
        assert dataset["id"] == "test-dataset"
        assert dataset["fps"] == 30.0
        assert len(dataset["tasks"]) == 2

    def test_get_dataset_not_found(self, client):
        """Test getting a non-existent dataset returns 404."""
        response = client.get("/api/datasets/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_list_episodes(self, client, registered_dataset):
        """Test listing episodes for a dataset."""
        response = client.get("/api/datasets/test-dataset/episodes")
        assert response.status_code == 200

        episodes = response.json()
        assert len(episodes) <= 100  # Limited by default

    def test_list_episodes_with_pagination(self, client, registered_dataset):
        """Test episode listing with pagination."""
        response = client.get("/api/datasets/test-dataset/episodes?offset=10&limit=5")
        assert response.status_code == 200

        episodes = response.json()
        assert len(episodes) == 5
        assert episodes[0]["index"] == 10

    def test_list_episodes_dataset_not_found(self, client):
        """Test listing episodes for non-existent dataset."""
        response = client.get("/api/datasets/nonexistent/episodes")
        assert response.status_code == 404

    @pytest.mark.parametrize("episode_index", [5, 999])
    def test_given_metadata_without_source_when_reading_episode_then_conflicts(
        self, client: TestClient, registered_dataset: DatasetInfo, episode_index: int
    ) -> None:
        response = client.get(f"/api/datasets/test-dataset/episodes/{episode_index}")
        assert response.status_code == 409
        assert response.json()["detail"] == "Episode source changed or is unavailable. Reload before saving."

    @pytest.mark.asyncio
    async def test_given_physical_source_when_reading_episode_then_returns_source_identity(
        self, client: TestClient, accessibility_dataset_path: Path
    ) -> None:
        service = DatasetService(base_path=str(accessibility_dataset_path))
        await service.list_datasets()
        client.app.dependency_overrides[get_dataset_service] = lambda: service
        try:
            response = client.get("/api/datasets/a11y-synthetic/episodes/1")
            assert response.status_code == 200
            episode = response.json()
            assert episode["meta"]["index"] == 1
            assert episode["meta"]["length"] == 18
            assert (episode["source_id"], episode["source_revision"]) == await service.get_source_revision(
                "a11y-synthetic", 1
            )
        finally:
            client.app.dependency_overrides.pop(get_dataset_service, None)

    def test_get_episode_dataset_not_found(self, client):
        """Test getting episode from non-existent dataset."""
        response = client.get("/api/datasets/nonexistent/episodes/0")
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
