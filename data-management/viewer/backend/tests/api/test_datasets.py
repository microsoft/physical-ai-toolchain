"""
Integration tests for dataset API endpoints.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.models.datasources import DatasetInfo, FeatureSchema, TaskInfo


@pytest.fixture
def client():
    """Create test client with isolated singletons and empty temp data path."""
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HMI_DATA_PATH"] = tmp

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
    yield sample_dataset
    service._datasets.clear()


class TestDatasetEndpoints:
    """Tests for dataset API endpoints."""

    def test_list_datasets_empty(self, client):
        """Test listing datasets when none are registered."""
        response = client.get("/api/datasets")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_datasets_with_data(self, client, registered_dataset):
        """Test listing datasets returns registered datasets."""
        response = client.get("/api/datasets")
        assert response.status_code == 200

        datasets = response.json()
        assert len(datasets) == 1
        assert datasets[0]["id"] == "test-dataset"
        assert datasets[0]["name"] == "Test Dataset"
        assert datasets[0]["total_episodes"] == 100

    @pytest.mark.asyncio
    async def test_get_dataset(self, client, registered_dataset):
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

    @pytest.mark.asyncio
    async def test_list_episodes(self, client, registered_dataset):
        """Test listing episodes for a dataset."""
        response = client.get("/api/datasets/test-dataset/episodes")
        assert response.status_code == 200

        episodes = response.json()
        assert len(episodes) <= 100  # Limited by default

    @pytest.mark.asyncio
    async def test_list_episodes_with_pagination(self, client, registered_dataset):
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

    @pytest.mark.asyncio
    async def test_get_episode(self, client, registered_dataset):
        """Test getting a specific episode."""
        response = client.get("/api/datasets/test-dataset/episodes/5")
        assert response.status_code == 200

        episode = response.json()
        assert episode["meta"]["index"] == 5

    def test_get_episode_dataset_not_found(self, client):
        """Test getting episode from non-existent dataset."""
        response = client.get("/api/datasets/nonexistent/episodes/0")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_episode_not_found(self, client, registered_dataset):
        """Test getting a non-existent episode returns 404."""
        response = client.get("/api/datasets/test-dataset/episodes/999")
        assert response.status_code == 404


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
