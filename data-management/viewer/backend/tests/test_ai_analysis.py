"""
Integration tests for AI analysis endpoints using real LeRobot data.

Tests trajectory analysis, anomaly detection, and annotation suggestion
endpoints with trajectory data extracted from the real dataset.
"""

import numpy as np
import pytest

from src.api.services.anomaly_detection import AnomalyDetector
from src.api.services.lerobot_loader import LeRobotLoader
from src.api.services.trajectory_analysis import TrajectoryAnalyzer


@pytest.fixture(scope="module")
def loader(test_dataset_path, test_dataset_id):
    import os

    return LeRobotLoader(os.path.join(test_dataset_path, test_dataset_id))


@pytest.fixture(scope="module")
def episode_data(loader):
    return loader.load_episode(0)


class TestTrajectoryAnalyzer:
    """Unit tests for TrajectoryAnalyzer with real data."""

    def test_analyze_returns_metrics(self, episode_data):
        analyzer = TrajectoryAnalyzer()
        metrics = analyzer.analyze(episode_data.joint_positions, episode_data.timestamps)
        assert 0 <= metrics.smoothness <= 1
        assert 0 <= metrics.efficiency <= 1
        assert 0 <= metrics.jitter <= 1
        assert metrics.hesitation_count >= 0
        assert metrics.correction_count >= 0
        assert 1 <= metrics.overall_score <= 5
        assert isinstance(metrics.flags, list)

    def test_smoothness_nonzero(self, episode_data):
        analyzer = TrajectoryAnalyzer()
        metrics = analyzer.analyze(episode_data.joint_positions, episode_data.timestamps)
        assert metrics.smoothness > 0

    def test_short_trajectory(self):
        analyzer = TrajectoryAnalyzer()
        positions = np.array([[0, 0], [1, 1]])
        timestamps = np.array([0.0, 0.033])
        metrics = analyzer.analyze(positions, timestamps)
        assert metrics.smoothness == 1.0
        assert metrics.overall_score == 3

    def test_multiple_episodes_produce_valid_scores(self, loader):
        analyzer = TrajectoryAnalyzer()
        for idx in [0, 15, 30, 63]:
            ep = loader.load_episode(idx)
            metrics = analyzer.analyze(ep.joint_positions, ep.timestamps)
            assert 1 <= metrics.overall_score <= 5, f"Episode {idx} score out of range: {metrics.overall_score}"


class TestAnomalyDetector:
    """Unit tests for AnomalyDetector with real data."""

    def test_detect_returns_list(self, episode_data):
        detector = AnomalyDetector()
        anomalies = detector.detect(episode_data.joint_positions, episode_data.timestamps)
        assert isinstance(anomalies, list)

    def test_anomaly_fields(self, episode_data):
        detector = AnomalyDetector()
        anomalies = detector.detect(episode_data.joint_positions, episode_data.timestamps)
        for a in anomalies:
            assert a.frame_range[0] <= a.frame_range[1]
            assert 0 <= a.confidence <= 1
            assert a.auto_detected is True

    def test_synthetic_spike(self):
        """Injecting a velocity spike should be detected."""
        n = 100
        timestamps = np.linspace(0, 3.0, n)
        positions = np.column_stack([np.linspace(0, 1, n)] * 6)
        positions[50] += 100  # massive spike

        detector = AnomalyDetector()
        anomalies = detector.detect(positions, timestamps)
        types = [a.type.value for a in anomalies]
        assert "velocity_spike" in types


class TestAIAnalysisEndpoints:
    """Integration tests for the /api/ai/* endpoints using real data."""

    def test_trajectory_analysis_endpoint(self, client, episode_data):
        payload = {
            "positions": episode_data.joint_positions.tolist(),
            "timestamps": episode_data.timestamps.tolist(),
        }
        resp = client.post("/api/ai/trajectory-analysis", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "smoothness" in data
        assert "efficiency" in data
        assert "overall_score" in data
        assert 1 <= data["overall_score"] <= 5

    def test_trajectory_analysis_too_short(self, client):
        payload = {
            "positions": [[0, 0], [1, 1]],
            "timestamps": [0.0, 0.033],
        }
        resp = client.post("/api/ai/trajectory-analysis", json=payload)
        assert resp.status_code == 400

    def test_anomaly_detection_endpoint(self, client, episode_data):
        payload = {
            "positions": episode_data.joint_positions.tolist(),
            "timestamps": episode_data.timestamps.tolist(),
        }
        resp = client.post("/api/ai/anomaly-detection", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "anomalies" in data
        assert "total_count" in data
        assert "severity_counts" in data
        assert data["total_count"] == len(data["anomalies"])

    def test_suggest_annotation_endpoint(self, client, episode_data):
        payload = {
            "positions": episode_data.joint_positions.tolist(),
            "timestamps": episode_data.timestamps.tolist(),
        }
        resp = client.post("/api/ai/suggest-annotation", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert 1 <= data["task_completion_rating"] <= 5
        assert 1 <= data["trajectory_quality_score"] <= 5
        assert "suggested_flags" in data
        assert "reasoning" in data
        assert 0 <= data["confidence"] <= 1
