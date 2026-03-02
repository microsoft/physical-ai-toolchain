"""
Integration tests for dataset API endpoints against a sample LeRobot dataset.

Tests the full HTTP round-trip through FastAPI routes, verifying response
schemas, status codes, pagination, and data integrity.
"""

from .conftest import TEST_DATASET_ID

DATASET_ID = TEST_DATASET_ID


class TestHealthEndpoint:
    """Verify the health check still works with the real environment."""

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestListDatasets:
    """GET /api/datasets"""

    def test_returns_dataset(self, client):
        resp = client.get("/api/datasets")
        assert resp.status_code == 200
        datasets = resp.json()
        ids = [d["id"] for d in datasets]
        assert DATASET_ID in ids

    def test_dataset_schema(self, client):
        resp = client.get("/api/datasets")
        ds = next(d for d in resp.json() if d["id"] == DATASET_ID)
        assert ds["total_episodes"] == 64
        assert ds["fps"] == 30.0
        assert "features" in ds
        assert "observation.state" in ds["features"]


class TestGetDataset:
    """GET /api/datasets/{dataset_id}"""

    def test_found(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == DATASET_ID
        assert data["total_episodes"] == 64

    def test_not_found(self, client):
        resp = client.get("/api/datasets/nonexistent")
        assert resp.status_code == 404


class TestCapabilities:
    """GET /api/datasets/{dataset_id}/capabilities"""

    def test_dataset_capabilities(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/capabilities")
        assert resp.status_code == 200
        caps = resp.json()
        assert caps["is_lerobot_dataset"] is True
        assert caps["has_hdf5_files"] is False
        assert caps["lerobot_support"] is True
        assert caps["episode_count"] == 64


class TestListEpisodes:
    """GET /api/datasets/{dataset_id}/episodes"""

    def test_default(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes")
        assert resp.status_code == 200
        episodes = resp.json()
        assert len(episodes) == 64

    def test_pagination(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes?offset=10&limit=5")
        assert resp.status_code == 200
        episodes = resp.json()
        assert len(episodes) == 5
        assert episodes[0]["index"] == 10
        assert episodes[4]["index"] == 14

    def test_limit_1(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes?limit=1")
        assert resp.status_code == 200
        episodes = resp.json()
        assert len(episodes) == 1

    def test_offset_beyond_range(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes?offset=100")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_episode_meta_schema(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes?limit=1")
        ep = resp.json()[0]
        assert "index" in ep
        assert "length" in ep
        assert "task_index" in ep
        assert "has_annotations" in ep
        assert ep["length"] > 0

    def test_filter_task_index(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes?task_index=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 64

    def test_dataset_not_found(self, client):
        resp = client.get("/api/datasets/nonexistent/episodes")
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetEpisode:
    """GET /api/datasets/{dataset_id}/episodes/{episode_idx}"""

    def test_first_episode(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["index"] == 0
        assert data["meta"]["length"] > 0
        assert len(data["trajectory_data"]) > 0

    def test_last_episode(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/63")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["index"] == 63

    def test_trajectory_point_schema(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0")
        pt = resp.json()["trajectory_data"][0]
        assert "timestamp" in pt
        assert "frame" in pt
        assert "joint_positions" in pt
        assert "joint_velocities" in pt
        assert "end_effector_pose" in pt
        assert "gripper_state" in pt
        assert len(pt["joint_positions"]) == 16
        assert len(pt["joint_velocities"]) == 16

    def test_video_urls_present(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0")
        urls = resp.json()["video_urls"]
        assert "observation.images.il-camera" in urls

    def test_episode_out_of_range(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/9999")
        # The service returns 200 with empty trajectory when LeRobot loader
        # fails for an out-of-range episode (it catches the error and falls through)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert data["trajectory_data"] == []


class TestGetTrajectory:
    """GET /api/datasets/{dataset_id}/episodes/{episode_idx}/trajectory"""

    def test_trajectory_data(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/trajectory")
        assert resp.status_code == 200
        traj = resp.json()
        assert len(traj) > 0

    def test_trajectory_timestamps_ordered(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/trajectory")
        traj = resp.json()
        timestamps = [pt["timestamp"] for pt in traj]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


class TestGetCameras:
    """GET /api/datasets/{dataset_id}/episodes/{episode_idx}/cameras"""

    def test_cameras(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/cameras")
        assert resp.status_code == 200
        cameras = resp.json()
        assert "observation.images.il-camera" in cameras


class TestGetVideo:
    """GET /api/datasets/{dataset_id}/episodes/{episode_idx}/video/{camera}"""

    def test_video_stream(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/video/observation.images.il-camera")
        assert resp.status_code == 200
        assert "video" in resp.headers.get("content-type", "")

    def test_video_nonexistent_camera(self, client):
        resp = client.get(f"/api/datasets/{DATASET_ID}/episodes/0/video/fake_camera")
        assert resp.status_code == 404
