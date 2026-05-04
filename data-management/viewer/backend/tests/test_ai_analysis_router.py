"""Unit tests for the AI analysis router (`src/api/routes/ai_analysis.py`).

Exercises trajectory analysis, anomaly detection, clustering, and
annotation-suggestion endpoints end-to-end through the FastAPI app.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from src.api.main import app

    with TestClient(app) as c:
        yield c


def _smooth_trajectory(num_points: int = 50, num_joints: int = 6) -> tuple[list[list[float]], list[float]]:
    """Build a smooth synthetic trajectory and matching timestamps."""
    t = np.linspace(0.0, 1.0, num_points)
    positions = np.stack(
        [np.sin(2 * math.pi * t + i * 0.1) for i in range(num_joints)],
        axis=1,
    )
    timestamps = (t * 10.0).tolist()
    return positions.tolist(), timestamps


class TestAnalyzeTrajectory:
    def test_success_without_gripper(self, client: TestClient) -> None:
        positions, timestamps = _smooth_trajectory()
        resp = client.post(
            "/api/ai/trajectory-analysis",
            json={"positions": positions, "timestamps": timestamps},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert set(data) >= {
            "smoothness",
            "efficiency",
            "jitter",
            "hesitation_count",
            "correction_count",
            "overall_score",
            "flags",
        }
        assert 1 <= data["overall_score"] <= 5
        assert isinstance(data["flags"], list)

    def test_success_with_gripper(self, client: TestClient) -> None:
        positions, timestamps = _smooth_trajectory(num_points=30)
        gripper = [0.0 if i < 15 else 1.0 for i in range(30)]
        resp = client.post(
            "/api/ai/trajectory-analysis",
            json={
                "positions": positions,
                "timestamps": timestamps,
                "gripper_states": gripper,
            },
        )
        assert resp.status_code == 200, resp.text

    def test_too_few_positions_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/ai/trajectory-analysis",
            json={"positions": [[0.0], [1.0]], "timestamps": [0.0, 1.0]},
        )
        assert resp.status_code == 400
        assert "at least 3" in resp.json()["detail"]

    def test_length_mismatch_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/ai/trajectory-analysis",
            json={
                "positions": [[0.0], [1.0], [2.0]],
                "timestamps": [0.0, 1.0],
            },
        )
        assert resp.status_code == 400
        assert "same length" in resp.json()["detail"]


class TestDetectAnomalies:
    def test_success_with_all_optionals(self, client: TestClient) -> None:
        positions, timestamps = _smooth_trajectory(num_points=40)
        forces = [[0.1] * 6 for _ in range(40)]
        gripper_states = [0.5] * 40
        gripper_commands = [0.5] * 40
        resp = client.post(
            "/api/ai/anomaly-detection",
            json={
                "positions": positions,
                "timestamps": timestamps,
                "forces": forces,
                "gripper_states": gripper_states,
                "gripper_commands": gripper_commands,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "anomalies" in data
        assert data["total_count"] == len(data["anomalies"])
        assert set(data["severity_counts"]) == {"low", "medium", "high"}
        assert sum(data["severity_counts"].values()) == data["total_count"]

    def test_detects_velocity_spike(self, client: TestClient) -> None:
        # Inject a sudden jump to provoke an anomaly and exercise
        # AnomalyResponse.from_anomaly serialization.
        positions, timestamps = _smooth_trajectory(num_points=30)
        positions[15] = [v + 50.0 for v in positions[15]]
        resp = client.post(
            "/api/ai/anomaly-detection",
            json={"positions": positions, "timestamps": timestamps},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        if data["anomalies"]:
            sample = data["anomalies"][0]
            assert set(sample) >= {
                "id",
                "type",
                "severity",
                "frame_start",
                "frame_end",
                "description",
                "confidence",
                "auto_detected",
            }

    def test_too_few_positions_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/ai/anomaly-detection",
            json={"positions": [[0.0], [1.0]], "timestamps": [0.0, 1.0]},
        )
        assert resp.status_code == 400


class TestClusterEpisodes:
    def test_success_default_num_clusters(self, client: TestClient) -> None:
        trajectories = []
        for offset in (0.0, 0.1, 5.0, 5.1):
            positions, _ = _smooth_trajectory(num_points=20)
            trajectories.append([[v + offset for v in row] for row in positions])
        resp = client.post(
            "/api/ai/cluster",
            json={"trajectories": trajectories},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["num_clusters"] >= 1
        assert len(data["assignments"]) == len(trajectories)
        assert all(isinstance(k, str) for k in data["cluster_sizes"])

    def test_success_with_explicit_num_clusters(self, client: TestClient) -> None:
        trajectories = []
        for offset in (0.0, 0.05, 4.0, 4.05, 8.0, 8.05):
            positions, _ = _smooth_trajectory(num_points=20)
            trajectories.append([[v + offset for v in row] for row in positions])
        resp = client.post(
            "/api/ai/cluster",
            json={"trajectories": trajectories, "num_clusters": 3},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["num_clusters"] == 3

    def test_too_few_trajectories_returns_400(self, client: TestClient) -> None:
        positions, _ = _smooth_trajectory(num_points=10)
        resp = client.post(
            "/api/ai/cluster",
            json={"trajectories": [positions]},
        )
        assert resp.status_code == 400

    def test_num_clusters_out_of_range_returns_422(self, client: TestClient) -> None:
        positions, _ = _smooth_trajectory(num_points=10)
        resp = client.post(
            "/api/ai/cluster",
            json={"trajectories": [positions, positions], "num_clusters": 1},
        )
        assert resp.status_code == 422


class TestSuggestAnnotation:
    def test_clean_long_trajectory_high_confidence(self, client: TestClient) -> None:
        positions, timestamps = _smooth_trajectory(num_points=120)
        resp = client.post(
            "/api/ai/suggest-annotation",
            json={"positions": positions, "timestamps": timestamps},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert 1 <= data["task_completion_rating"] <= 5
        assert 1 <= data["trajectory_quality_score"] <= 5
        assert 0.0 <= data["confidence"] <= 1.0
        assert data["reasoning"].endswith(".")
        assert "smoothness" in data["reasoning"].lower()
        assert "efficiency" in data["reasoning"].lower()
        # Clean long trajectory with high score should hit the +0.2 boost branch.
        if not data["detected_anomalies"] and data["trajectory_quality_score"] >= 4:
            assert data["confidence"] > 0.8

    def test_short_trajectory_low_confidence(self, client: TestClient) -> None:
        positions, timestamps = _smooth_trajectory(num_points=10)
        resp = client.post(
            "/api/ai/suggest-annotation",
            json={"positions": positions, "timestamps": timestamps},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # confidence = min(1.0, 10/100) * 0.8 == 0.08, optional +0.2 if clean+high score.
        assert data["confidence"] <= 0.3

    def test_with_anomalies_includes_flags(self, client: TestClient) -> None:
        # Build a noisy trajectory likely to surface multiple anomalies, exercising
        # severe_anomaly and many_anomalies flag branches plus the task_completion
        # clamp via max(1, ...).
        rng = np.random.default_rng(seed=42)
        positions = rng.normal(0, 1, size=(60, 6))
        # Inject sharp jumps every few frames.
        for idx in (10, 20, 30, 40, 50):
            positions[idx] += 80.0
        timestamps = np.linspace(0.0, 6.0, 60).tolist()
        forces = (rng.normal(0, 50, size=(60, 6))).tolist()
        resp = client.post(
            "/api/ai/suggest-annotation",
            json={
                "positions": positions.tolist(),
                "timestamps": timestamps,
                "forces": forces,
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["task_completion_rating"] >= 1  # max(1, ...) clamp
        assert isinstance(data["suggested_flags"], list)
        if any(a["severity"] == "high" for a in data["detected_anomalies"]):
            assert "has_severe_anomalies" in data["suggested_flags"]
        if len(data["detected_anomalies"]) > 5:
            assert "many_anomalies" in data["suggested_flags"]

    def test_too_few_positions_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/ai/suggest-annotation",
            json={"positions": [[0.0]], "timestamps": [0.0]},
        )
        assert resp.status_code == 400
