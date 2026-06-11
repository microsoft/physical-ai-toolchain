"""Unit tests for the anomaly detection service."""

from __future__ import annotations

import numpy as np

from src.api.services.anomaly_detection import (
    AnomalyDetector,
    AnomalySeverity,
    AnomalyType,
)


def _linear_positions(n: int, joints: int = 6) -> np.ndarray:
    return np.column_stack([np.linspace(0.0, 1.0, n)] * joints)


def _ts(n: int, dt: float = 0.033) -> np.ndarray:
    return np.linspace(0.0, dt * (n - 1), n)


class TestDetectShortAndConstant:
    def test_returns_empty_for_short_input(self):
        detector = AnomalyDetector()
        positions = np.array([[0.0, 0.0], [1.0, 1.0]])
        timestamps = np.array([0.0, 0.033])
        assert detector.detect(positions, timestamps) == []

    def test_constant_velocity_no_velocity_spikes(self):
        # Std velocity is ~0 -> early return inside _detect_velocity_spikes
        n = 50
        positions = _linear_positions(n)
        timestamps = _ts(n)
        detector = AnomalyDetector()
        out = detector.detect(positions, timestamps)
        assert all(a.type != AnomalyType.VELOCITY_SPIKE for a in out)


class TestVelocitySpikes:
    def test_velocity_spike_detected(self):
        n = 100
        positions = _linear_positions(n)
        positions[50] += 100.0
        detector = AnomalyDetector()
        out = detector.detect(positions, _ts(n))
        spikes = [a for a in out if a.type == AnomalyType.VELOCITY_SPIKE]
        assert spikes
        assert all(0.0 <= a.confidence <= 1.0 for a in spikes)


class TestUnexpectedStops:
    def test_stop_in_middle_detected(self):
        # 10 moving frames, 35 stopped frames, 10 moving frames
        moving_a = np.linspace(0.0, 1.0, 10).reshape(-1, 1)
        stopped = np.full((35, 1), 1.0)
        moving_b = np.linspace(1.0, 2.0, 10).reshape(-1, 1)
        positions = np.vstack([moving_a, stopped, moving_b])
        positions = np.hstack([positions] * 3)
        timestamps = _ts(len(positions))
        detector = AnomalyDetector(stop_min_frames=10)
        out = detector.detect(positions, timestamps)
        stops = [a for a in out if a.type == AnomalyType.UNEXPECTED_STOP]
        assert stops
        # 35 stopped frames > 30 -> HIGH severity branch
        assert any(a.severity == AnomalySeverity.HIGH for a in stops)

    def test_medium_severity_stop(self):
        moving_a = np.linspace(0.0, 1.0, 10).reshape(-1, 1)
        stopped = np.full((20, 1), 1.0)  # 15 < dur <= 30
        moving_b = np.linspace(1.0, 2.0, 10).reshape(-1, 1)
        positions = np.hstack([np.vstack([moving_a, stopped, moving_b])] * 3)
        detector = AnomalyDetector(stop_min_frames=10)
        out = detector.detect(positions, _ts(len(positions)))
        stops = [a for a in out if a.type == AnomalyType.UNEXPECTED_STOP]
        assert any(a.severity == AnomalySeverity.MEDIUM for a in stops)

    def test_low_severity_stop(self):
        moving_a = np.linspace(0.0, 1.0, 10).reshape(-1, 1)
        stopped = np.full((12, 1), 1.0)  # <=15
        moving_b = np.linspace(1.0, 2.0, 10).reshape(-1, 1)
        positions = np.hstack([np.vstack([moving_a, stopped, moving_b])] * 3)
        detector = AnomalyDetector(stop_min_frames=10)
        out = detector.detect(positions, _ts(len(positions)))
        stops = [a for a in out if a.type == AnomalyType.UNEXPECTED_STOP]
        assert any(a.severity == AnomalySeverity.LOW for a in stops)

    def test_stop_at_start_excluded(self):
        # Stop occurs immediately -> should be excluded by group[0] < 5 guard
        stopped = np.full((30, 1), 0.0)
        moving = np.linspace(0.0, 1.0, 30).reshape(-1, 1)
        positions = np.hstack([np.vstack([stopped, moving])] * 3)
        detector = AnomalyDetector(stop_min_frames=10)
        out = detector.detect(positions, _ts(len(positions)))
        assert not [a for a in out if a.type == AnomalyType.UNEXPECTED_STOP]


class TestOscillations:
    def test_short_returns_no_oscillation(self):
        # < 20 frames -> early return
        positions = np.column_stack([np.linspace(0, 1, 15)] * 6)
        detector = AnomalyDetector()
        out = detector.detect(positions, _ts(15))
        assert all(a.type != AnomalyType.OSCILLATION for a in out)

    def test_oscillation_detected(self):
        # Build a trajectory with rapid sign changes in joint 0
        n = 60
        joint0 = np.array([(i % 2) for i in range(n)], dtype=float)
        joints_rest = np.zeros((n, 5))
        positions = np.hstack([joint0.reshape(-1, 1), joints_rest])
        detector = AnomalyDetector(oscillation_min_cycles=3)
        out = detector.detect(positions, _ts(n))
        assert any(a.type == AnomalyType.OSCILLATION for a in out)


class TestForceSpikes:
    def test_force_spike_high_severity(self):
        n = 60
        positions = _linear_positions(n)
        forces = np.full((n, 3), 0.1)
        forces[30] = [100.0, 100.0, 100.0]  # huge spike -> z > 5
        detector = AnomalyDetector()
        out = detector.detect(positions, _ts(n), forces=forces)
        force_anoms = [a for a in out if a.type == AnomalyType.FORCE_SPIKE]
        assert force_anoms
        assert any(a.severity == AnomalySeverity.HIGH for a in force_anoms)

    def test_constant_forces_no_spike(self):
        n = 60
        forces = np.full((n, 3), 1.0)
        detector = AnomalyDetector()
        out = detector.detect(_linear_positions(n), _ts(n), forces=forces)
        assert not [a for a in out if a.type == AnomalyType.FORCE_SPIKE]


class TestGripperFailures:
    def test_mismatch_detected(self):
        n = 60
        states = np.zeros(n)
        commands = np.zeros(n)
        commands[20:30] = 1.0  # 10-frame mismatch > 0.3
        detector = AnomalyDetector()
        out = detector.detect(
            _linear_positions(n),
            _ts(n),
            gripper_states=states,
            gripper_commands=commands,
        )
        assert any(a.type == AnomalyType.GRIPPER_FAILURE for a in out)

    def test_short_mismatch_ignored(self):
        n = 60
        states = np.zeros(n)
        commands = np.zeros(n)
        commands[20:23] = 1.0  # only 3 frames < min duration of 5
        detector = AnomalyDetector()
        out = detector.detect(
            _linear_positions(n),
            _ts(n),
            gripper_states=states,
            gripper_commands=commands,
        )
        assert not [a for a in out if a.type == AnomalyType.GRIPPER_FAILURE]


class TestJointLimits:
    def test_near_upper_limit_detected(self):
        n = 30
        positions = np.full((n, 2), 0.5)
        positions[10:20, 0] = 0.99  # near upper of [0, 1]
        lower = np.array([0.0, 0.0])
        upper = np.array([1.0, 1.0])
        detector = AnomalyDetector()
        out = detector.detect(positions, _ts(n), joint_limits=(lower, upper))
        assert any(a.type == AnomalyType.JOINT_LIMIT for a in out)

    def test_near_lower_limit_detected(self):
        n = 30
        positions = np.full((n, 1), 0.5)
        positions[10:20, 0] = 0.01
        detector = AnomalyDetector()
        out = detector.detect(
            positions,
            _ts(n),
            joint_limits=(np.array([0.0]), np.array([1.0])),
        )
        assert any(a.type == AnomalyType.JOINT_LIMIT for a in out)


class TestZScoreSeverity:
    def test_high(self):
        d = AnomalyDetector()
        assert d._zscore_to_severity(6.0) == AnomalySeverity.HIGH

    def test_medium(self):
        d = AnomalyDetector()
        assert d._zscore_to_severity(4.5) == AnomalySeverity.MEDIUM

    def test_low(self):
        d = AnomalyDetector()
        assert d._zscore_to_severity(3.5) == AnomalySeverity.LOW


class TestGroupConsecutive:
    def test_empty(self):
        d = AnomalyDetector()
        assert d._group_consecutive(np.array([], dtype=np.int64)) == []

    def test_groups_split_correctly(self):
        d = AnomalyDetector()
        out = d._group_consecutive(np.array([1, 2, 3, 7, 8, 12], dtype=np.int64))
        assert len(out) == 3
        assert list(out[0]) == [1, 2, 3]
        assert list(out[1]) == [7, 8]
        assert list(out[2]) == [12]
