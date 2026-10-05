"""Behavior tests for trajectory analysis services and API endpoints."""

from __future__ import annotations

import numpy as np
import pytest

from src.api.services.anomaly_detection import AnomalyDetector
from src.api.services.trajectory_analysis import TrajectoryAnalyzer


@pytest.fixture
def smooth_trajectory() -> tuple[np.ndarray, np.ndarray]:
    timestamps = np.linspace(0.0, 1.0, 11)
    positions = np.column_stack((timestamps, timestamps * 2.0, timestamps * 3.0))
    return positions, timestamps


class TestTrajectoryAnalyzer:
    def test_linear_trajectory_returns_exact_contract(self, smooth_trajectory):
        positions, timestamps = smooth_trajectory

        metrics = TrajectoryAnalyzer().analyze(positions, timestamps)

        assert metrics.smoothness == pytest.approx(1.0)
        assert metrics.normalized_smoothness == pytest.approx(1.0)
        assert metrics.efficiency == pytest.approx(1.0)
        assert metrics.jitter == pytest.approx(0.0)
        assert metrics.hesitation_count == 0
        assert metrics.correction_count == 0
        assert metrics.overall_score == 5
        assert metrics.flags == []

    def test_short_trajectory_uses_neutral_score(self):
        metrics = TrajectoryAnalyzer().analyze(
            np.array([[0.0, 0.0], [1.0, 1.0]]),
            np.array([0.0, 0.033]),
        )

        assert metrics.smoothness == 1.0
        assert metrics.overall_score == 3

    def test_smoothness_modes_discriminate_degree_scale_trajectory(self):
        timestamps = np.linspace(0.0, 0.25, 8)
        steps = np.arange(8, dtype=np.float64)
        positions = np.column_stack([steps**3, (steps**3) * 0.5])

        log_metrics = TrajectoryAnalyzer(smoothness_mode="log-scaled").analyze(positions, timestamps)
        radian_metrics = TrajectoryAnalyzer(smoothness_mode="radian-based").analyze(positions, timestamps)

        assert log_metrics.smoothness < 0.01
        assert log_metrics.normalized_smoothness > log_metrics.smoothness
        assert radian_metrics.normalized_smoothness > radian_metrics.smoothness
        assert log_metrics.normalized_smoothness != pytest.approx(radian_metrics.normalized_smoothness)

    def test_invalid_smoothness_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="smoothness_mode must be one of"):
            TrajectoryAnalyzer(smoothness_mode="invalid")


class TestAnomalyDetector:
    def test_linear_trajectory_has_no_anomalies(self, smooth_trajectory):
        positions, timestamps = smooth_trajectory

        assert AnomalyDetector().detect(positions, timestamps) == []

    def test_velocity_spike_has_public_anomaly_contract(self):
        timestamps = np.linspace(0.0, 3.0, 100)
        positions = np.column_stack([np.linspace(0.0, 1.0, 100)] * 6)
        positions[50] += 100.0

        anomalies = AnomalyDetector().detect(positions, timestamps)
        velocity_spikes = [anomaly for anomaly in anomalies if anomaly.type.value == "velocity_spike"]

        assert velocity_spikes
        anomaly = velocity_spikes[0]
        assert anomaly.frame_range[0] <= 50 <= anomaly.frame_range[1]
        assert anomaly.severity.value in {"medium", "high"}
        assert 0.0 <= anomaly.confidence <= 1.0
        assert anomaly.auto_detected is True


class TestAIAnalysisEndpoints:
    def test_trajectory_analysis_returns_complete_response(self, client, smooth_trajectory):
        positions, timestamps = smooth_trajectory

        response = client.post(
            "/api/ai/trajectory-analysis",
            json={"positions": positions.tolist(), "timestamps": timestamps.tolist()},
        )

        assert response.status_code == 200
        assert response.json() == {
            "smoothness": pytest.approx(1.0),
            "normalized_smoothness": pytest.approx(1.0),
            "efficiency": pytest.approx(1.0),
            "jitter": pytest.approx(0.0),
            "hesitation_count": 0,
            "correction_count": 0,
            "overall_score": 5,
            "flags": [],
        }

    @pytest.mark.parametrize("mode", ["log-scaled", "radian-based"])
    def test_trajectory_analysis_honors_smoothness_mode(self, client, mode):
        timestamps = np.linspace(0.0, 0.25, 8)
        steps = np.arange(8, dtype=np.float64)
        positions = np.column_stack([steps**3, (steps**3) * 0.5])

        response = client.post(
            "/api/ai/trajectory-analysis",
            json={
                "positions": positions.tolist(),
                "timestamps": timestamps.tolist(),
                "smoothness_mode": mode,
            },
        )

        expected = TrajectoryAnalyzer(smoothness_mode=mode).analyze(positions, timestamps)
        assert response.status_code == 200
        assert response.json()["normalized_smoothness"] == pytest.approx(expected.normalized_smoothness)

    def test_trajectory_analysis_rejects_invalid_mode(self, client):
        response = client.post(
            "/api/ai/trajectory-analysis",
            json={
                "positions": [[0.0], [1.0], [8.0], [27.0]],
                "timestamps": [0.0, 0.1, 0.2, 0.3],
                "smoothness_mode": "invalid",
            },
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "smoothness_mode"]

    def test_trajectory_analysis_rejects_short_trajectory(self, client):
        response = client.post(
            "/api/ai/trajectory-analysis",
            json={"positions": [[0, 0], [1, 1]], "timestamps": [0.0, 0.033]},
        )

        assert response.status_code == 400
        assert response.json() == {"detail": "Trajectory must have at least 3 positions"}

    def test_anomaly_detection_returns_severity_totals(self, client, smooth_trajectory):
        positions, timestamps = smooth_trajectory

        response = client.post(
            "/api/ai/anomaly-detection",
            json={"positions": positions.tolist(), "timestamps": timestamps.tolist()},
        )

        assert response.status_code == 200
        assert response.json() == {
            "anomalies": [],
            "total_count": 0,
            "severity_counts": {"low": 0, "medium": 0, "high": 0},
        }

    def test_suggest_annotation_returns_complete_contract(self, client, smooth_trajectory):
        positions, timestamps = smooth_trajectory

        response = client.post(
            "/api/ai/suggest-annotation",
            json={"positions": positions.tolist(), "timestamps": timestamps.tolist()},
        )

        assert response.status_code == 200
        data = response.json()
        assert set(data) == {
            "task_completion_rating",
            "trajectory_quality_score",
            "suggested_flags",
            "detected_anomalies",
            "confidence",
            "reasoning",
        }
        assert data["task_completion_rating"] == 5
        assert data["trajectory_quality_score"] == 5
        assert data["suggested_flags"] == []
        assert data["detected_anomalies"] == []
        assert data["confidence"] == pytest.approx(0.288)
        assert data["reasoning"] == "Trajectory smoothness: 1.00. Path efficiency: 1.00."
