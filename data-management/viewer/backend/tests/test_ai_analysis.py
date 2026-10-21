"""Behavior tests for trajectory analysis services and API endpoints."""

from __future__ import annotations

import numpy as np
import pytest

from src.api.services import trajectory_analysis, trajectory_numerics
from src.api.services.anomaly_detection import AnomalyDetector
from src.api.services.trajectory_analysis import TrajectoryAnalyzer


@pytest.fixture
def smooth_trajectory() -> tuple[np.ndarray, np.ndarray]:
    timestamps = np.linspace(0.0, 1.0, 11)
    positions = np.column_stack((timestamps, timestamps * 2.0, timestamps * 3.0))
    return positions, timestamps


SMOOTHNESS_MODES = ("log-scaled", "radian-based")
DECIMAL_HELPERS = ("decimal_array", "decimal_row_norms")


def _hesitation_reversal_case() -> tuple[np.ndarray, np.ndarray]:
    timestamps = np.arange(120, dtype=np.float64) / 30.0
    positions = np.column_stack(
        [
            40.0 * np.sin(2.0 * np.pi * 0.8 * timestamps),
            120.0 + 10.0 * np.cos(2.0 * np.pi * 0.5 * timestamps),
        ]
    )
    positions[30:50] = positions[30]
    positions[80:86] = positions[80]
    return positions, timestamps


def _threshold_boundary_case() -> tuple[np.ndarray, np.ndarray]:
    steps = np.concatenate(
        [np.full(6, 0.0105), np.full(6, 0.0095), np.full(6, 0.0105), np.full(6, 0.0095), np.full(5, 0.0105)]
    )
    base = np.concatenate([[0.0], np.cumsum(steps)])
    return np.column_stack([base, 0.3 * base]), np.arange(base.size, dtype=np.float64)


def _zero_motion_case() -> tuple[np.ndarray, np.ndarray]:
    return np.full((50, 3), 7.5), np.linspace(0.0, 1.0, 50)


def _large_offset_case() -> tuple[np.ndarray, np.ndarray]:
    timestamps = np.linspace(0.0, 2.0, 60)
    return np.column_stack([np.full(60, 1e200), 2.0 * np.sin(np.pi * timestamps)]), timestamps


def _degree_scale_case() -> tuple[np.ndarray, np.ndarray]:
    timestamps = np.linspace(0.0, 0.25, 8)
    steps = np.arange(8, dtype=np.float64)
    return np.column_stack([steps**3, (steps**3) * 0.5]), timestamps


def _path_length_overflow_case() -> tuple[np.ndarray, np.ndarray]:
    positions = np.array([[8e307 * (index % 3 - 1)] for index in range(12)])
    return positions, np.arange(12, dtype=np.float64)


def _jerk_overflow_case() -> tuple[np.ndarray, np.ndarray]:
    steps = np.array([1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 1.0, -1.0, 2.0, -2.0, 3.0])
    positions = np.concatenate([[0.0], np.cumsum(steps)])[:, None]
    return positions, np.arange(positions.shape[0], dtype=np.float64) * 1e-300


ORDINARY_CASES = {
    "hesitation_and_reversal": _hesitation_reversal_case,
    "threshold_boundary": _threshold_boundary_case,
    "zero_motion": _zero_motion_case,
    "large_offset": _large_offset_case,
    "degree_scale": _degree_scale_case,
}
UNSAFE_CASES = {
    "path_length_overflow": _path_length_overflow_case,
    "jerk_overflow": _jerk_overflow_case,
}


@pytest.fixture
def decimal_helper_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record array-wide Decimal helper calls while delegating to the real helpers."""
    calls: list[str] = []
    for module in (trajectory_numerics, trajectory_analysis):
        for name in DECIMAL_HELPERS:
            original = getattr(module, name, None)
            if original is None:
                continue

            def spy(*args, _name=name, _original=original, **kwargs):
                calls.append(_name)
                return _original(*args, **kwargs)

            monkeypatch.setattr(module, name, spy)
    return calls


def _assert_metrics_match(metrics, expected: dict) -> None:
    for name, value in expected.items():
        actual = getattr(metrics, name)
        if isinstance(value, float):
            assert actual == pytest.approx(value, rel=1e-9, abs=1e-12), name
        else:
            assert actual == value, name


ORDINARY_EXPECTED: dict[tuple[str, str], dict] = {
    ("hesitation_and_reversal", "log-scaled"): {
        "smoothness": 3.017358753977871e-06,
        "normalized_smoothness": 0.15336545811768496,
        "efficiency": 0.06918700939039778,
        "jitter": 0.16918911090573277,
        "hesitation_count": 2,
        "correction_count": 3,
        "overall_score": 2,
        "flags": ["jittery"],
    },
    ("hesitation_and_reversal", "radian-based"): {
        "smoothness": 3.017358753977871e-06,
        "normalized_smoothness": 0.000172852560355135,
        "efficiency": 0.06918700939039778,
        "jitter": 0.16918911090573277,
        "hesitation_count": 2,
        "correction_count": 3,
        "overall_score": 2,
        "flags": ["jittery"],
    },
    ("threshold_boundary", "log-scaled"): {
        "smoothness": 0.9995983138317271,
        "normalized_smoothness": 0.9998255453069337,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 2,
        "correction_count": 0,
        "overall_score": 5,
        "flags": [],
    },
    ("threshold_boundary", "radian-based"): {
        "smoothness": 0.9995983138317271,
        "normalized_smoothness": 0.9999929864857422,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 2,
        "correction_count": 0,
        "overall_score": 5,
        "flags": [],
    },
    ("zero_motion", "log-scaled"): {
        "smoothness": 1.0,
        "normalized_smoothness": 1.0,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 1,
        "correction_count": 0,
        "overall_score": 5,
        "flags": [],
    },
    ("zero_motion", "radian-based"): {
        "smoothness": 1.0,
        "normalized_smoothness": 1.0,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 1,
        "correction_count": 0,
        "overall_score": 5,
        "flags": [],
    },
    ("large_offset", "log-scaled"): {
        "smoothness": 0.03182946423406638,
        "normalized_smoothness": 0.40045320546184304,
        "efficiency": 6.125404771051978e-17,
        "jitter": 1.0057799788080455e-05,
        "hesitation_count": 0,
        "correction_count": 2,
        "overall_score": 2,
        "flags": ["jittery"],
    },
    ("large_offset", "radian-based"): {
        "smoothness": 0.03182946423406638,
        "normalized_smoothness": 0.6532172189665783,
        "efficiency": 6.125404771051978e-17,
        "jitter": 1.0057799788080455e-05,
        "hesitation_count": 0,
        "correction_count": 2,
        "overall_score": 2,
        "flags": ["jittery"],
    },
    ("degree_scale", "log-scaled"): {
        "smoothness": 9.603520685038452e-06,
        "normalized_smoothness": 0.16618004927657404,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 0,
        "correction_count": 0,
        "overall_score": 4,
        "flags": ["jittery"],
    },
    ("degree_scale", "radian-based"): {
        "smoothness": 9.603520685038452e-06,
        "normalized_smoothness": 0.0005499438833324047,
        "efficiency": 1.0,
        "jitter": 0.0,
        "hesitation_count": 0,
        "correction_count": 0,
        "overall_score": 4,
        "flags": ["jittery"],
    },
}
UNSAFE_EXPECTED: dict[str, dict] = {
    "path_length_overflow": {
        "smoothness": 2.94627825494395e-309,
        "normalized_smoothness": 0.003230697036604073,
        "efficiency": 0.14285714285714285,
        "jitter": 0.0,
        "hesitation_count": 0,
        "correction_count": 6,
        "overall_score": 2,
        "flags": ["jittery", "excessive_corrections"],
    },
    "jerk_overflow": {
        "smoothness": 0.0,
        "normalized_smoothness": 0.0011087707790106976,
        "efficiency": 0.14285714285714285,
        "jitter": 0.07709214919581286,
        "hesitation_count": 0,
        "correction_count": 10,
        "overall_score": 2,
        "flags": ["jittery", "excessive_corrections"],
    },
}


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

    @pytest.mark.parametrize("smoothness_mode", SMOOTHNESS_MODES)
    @pytest.mark.parametrize("case", list(ORDINARY_CASES))
    def test_ordinary_float_trajectories_skip_decimal_work(self, case, smoothness_mode, decimal_helper_calls):
        positions, timestamps = ORDINARY_CASES[case]()

        metrics = TrajectoryAnalyzer(smoothness_mode=smoothness_mode).analyze(positions, timestamps)

        assert decimal_helper_calls == []
        _assert_metrics_match(metrics, ORDINARY_EXPECTED[(case, smoothness_mode)])

    @pytest.mark.parametrize("case", list(UNSAFE_CASES))
    def test_unsafe_float_trajectories_keep_decimal_fallback(self, case, decimal_helper_calls):
        positions, timestamps = UNSAFE_CASES[case]()

        metrics = TrajectoryAnalyzer().analyze(positions, timestamps)

        assert decimal_helper_calls
        for bounded in (metrics.smoothness, metrics.normalized_smoothness, metrics.efficiency, metrics.jitter):
            assert 0.0 <= bounded <= 1.0
        _assert_metrics_match(metrics, UNSAFE_EXPECTED[case])


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
