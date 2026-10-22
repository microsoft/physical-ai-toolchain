# cspell:ignore nextafter
"""Unit tests for the anomaly detection service."""

from __future__ import annotations

import math

import numpy as np
import pytest

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
        assert len(spikes) == 1
        assert spikes[0].frame_range == (49, 51)
        assert spikes[0].severity == AnomalySeverity.HIGH
        assert spikes[0].confidence == 1.0


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
    @pytest.mark.parametrize(
        ("n", "expected_severity"),
        [
            (14, AnomalySeverity.LOW),
            (22, AnomalySeverity.MEDIUM),
            (38, AnomalySeverity.HIGH),
        ],
    )
    def test_force_spike_severity_reflects_outlier_strength(self, n, expected_severity):
        positions = _linear_positions(n)
        forces = np.zeros((n, 1))
        forces[n // 2] = 1.0
        detector = AnomalyDetector()

        out = detector.detect(positions, _ts(n), forces=forces)
        force_anoms = [a for a in out if a.type == AnomalyType.FORCE_SPIKE]

        assert len(force_anoms) == 1
        assert force_anoms[0].severity == expected_severity
        assert force_anoms[0].frame_range == (n // 2, n // 2 + 1)

    def test_separated_force_spikes_produce_distinct_anomalies(self):
        n = 80
        forces = np.zeros((n, 1))
        forces[[10, 30]] = 1.0

        out = AnomalyDetector().detect(_linear_positions(n), _ts(n), forces=forces)
        force_anoms = [a for a in out if a.type == AnomalyType.FORCE_SPIKE]

        assert [anomaly.frame_range for anomaly in force_anoms] == [(10, 11), (30, 31)]

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


class TestExtremeFiniteArithmetic:
    def test_overflowed_time_difference_retains_velocity_ratio(self) -> None:
        timestamps = np.concatenate([np.full(50, -1e308), np.full(51, 1e308)])
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(timestamps[:, None], timestamps)
        spikes = [a for a in anomalies if a.type == AnomalyType.VELOCITY_SPIKE]
        assert [a.frame_range for a in spikes] == [(49, 50)]
        assert spikes[0].severity == AnomalySeverity.HIGH

    def test_force_variance_overflow_retains_group_and_severity(self) -> None:
        timestamps = np.arange(100, dtype=float)
        forces = np.zeros((100, 1))
        forces[40:46] = 1e154
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(timestamps[:, None], timestamps, forces=forces)
        spikes = [a for a in anomalies if a.type == AnomalyType.FORCE_SPIKE]
        assert [a.frame_range for a in spikes] == [(40, 46)]
        assert spikes[0].severity == AnomalySeverity.LOW
        assert spikes[0].confidence == pytest.approx(math.sqrt(94 / 6) / 5)

    def test_nearly_constant_extreme_forces_retain_representable_spike(self) -> None:
        timestamps = np.arange(40, dtype=float)
        forces = np.full((40, 1), 1e200)
        forces[20] = np.nextafter(1e200, math.inf)
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(timestamps[:, None], timestamps, forces=forces)
        spikes = [a for a in anomalies if a.type == AnomalyType.FORCE_SPIKE]
        assert [a.frame_range for a in spikes] == [(20, 21)]
        assert spikes[0].severity == AnomalySeverity.HIGH

    def test_deviation_floor_precedes_configured_zscore_threshold(self) -> None:
        positions = np.zeros((40, 1))
        forces = np.zeros((40, 1))
        forces[20] = 1e-12
        anomalies = AnomalyDetector(velocity_zscore_threshold=-1).detect(
            positions, np.arange(40, dtype=float), forces=forces
        )
        assert not [a for a in anomalies if a.type in {AnomalyType.VELOCITY_SPIKE, AnomalyType.FORCE_SPIKE}]

    @pytest.mark.parametrize(
        ("amplitude", "interval", "offset"),
        [
            (1.0, 1.0, 0.0),
            (1e155, 1.0, 0.0),
            (1e200, 1.0, 0.0),
            (1.0, 1e-309, 0.0),
            (1e308, 1.0, -1e308),
        ],
    )
    def test_velocity_impulse_retains_spike(self, amplitude: float, interval: float, offset: float) -> None:
        positions = np.full((101, 1), offset)
        positions[50] = amplitude
        timestamps = np.arange(101, dtype=float) * interval

        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(positions, timestamps)

        spikes = [a for a in anomalies if a.type == AnomalyType.VELOCITY_SPIKE]
        assert len(spikes) == 1
        assert spikes[0].frame_range == (49, 51)
        assert spikes[0].severity == AnomalySeverity.HIGH
        assert spikes[0].confidence == 1.0

    @pytest.mark.parametrize("amplitude", [1.0, 1e155, 1e200])
    @pytest.mark.parametrize("count", [9, 11, 16, 18, 25, 27, 40])
    def test_force_spike_respects_existing_zscore_boundaries(self, amplitude: float, count: int) -> None:
        forces = np.zeros((count, 1))
        forces[count // 2] = amplitude
        timestamps = np.arange(count, dtype=float)

        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(timestamps[:, None], timestamps, forces=forces)

        spikes = [a for a in anomalies if a.type == AnomalyType.FORCE_SPIKE]
        zscore = math.sqrt(count - 1)
        if zscore <= 3:
            assert spikes == []
        else:
            assert len(spikes) == 1
            assert spikes[0].frame_range == (count // 2, count // 2 + 1)
            expected_severity = (
                AnomalySeverity.HIGH if zscore > 5 else AnomalySeverity.MEDIUM if zscore > 4 else AnomalySeverity.LOW
            )
            assert spikes[0].severity == expected_severity
            assert spikes[0].confidence == pytest.approx(min(1.0, zscore / 5))

    @pytest.mark.parametrize("amplitude", [1e-12, 1e-8])
    @pytest.mark.parametrize("force_signal", [False, True])
    def test_standard_deviation_floor_retains_original_units(self, amplitude: float, force_signal: bool) -> None:
        timestamps = np.arange(101, dtype=float)
        impulse = np.zeros((101, 1))
        impulse[50] = amplitude
        positions = timestamps[:, None] if force_signal else impulse
        forces = impulse if force_signal else None

        anomalies = AnomalyDetector().detect(positions, timestamps, forces=forces)

        kind = AnomalyType.FORCE_SPIKE if force_signal else AnomalyType.VELOCITY_SPIKE
        spikes = [a for a in anomalies if a.type == kind]
        assert len(spikes) == (0 if amplitude == 1e-12 else 1)

    @pytest.mark.parametrize("amplitude", [0.0, 1e200, 1e308])
    def test_constant_extreme_signals_have_no_anomalies(self, amplitude: float) -> None:
        timestamps = np.arange(101, dtype=float)
        values = np.full((101, 3), amplitude)

        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(values, timestamps, forces=values)

        assert anomalies == []

    @pytest.mark.parametrize("speed", [0.005, 0.02])
    def test_stop_threshold_is_not_rescaled_by_extreme_motion(self, speed: float) -> None:
        increments = np.concatenate([np.ones(10), np.full(20, speed), np.ones(10)])
        positions = np.concatenate([[0.0], np.cumsum(increments)])[:, None]
        positions[-1] = 1e200

        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(positions, np.arange(len(positions), dtype=float))

        stops = [a for a in anomalies if a.type == AnomalyType.UNEXPECTED_STOP]
        assert [a.frame_range for a in stops] == ([(10, 30)] if speed < 0.01 else [])

    @pytest.mark.parametrize("interval", [0.0, -1.0, 1.0])
    def test_constant_offset_does_not_erase_small_motion(self, interval: float) -> None:
        positions = np.column_stack([np.full(101, 1e200), np.zeros(101)])
        positions[50, 1] = 1.0

        anomalies = AnomalyDetector().detect(positions, np.arange(101, dtype=float) * interval)

        spikes = [a for a in anomalies if a.type == AnomalyType.VELOCITY_SPIKE]
        assert [a.frame_range for a in spikes] == [(49, 51)]
        assert spikes[0].severity == AnomalySeverity.HIGH

    def test_extreme_oscillations_and_gripper_mismatch_keep_their_groups(self) -> None:
        positions = np.where(np.arange(60) % 2, -1e308, 1e308)[:, None]
        states = np.full(60, 1e308)
        commands = states.copy()
        commands[20:30] = -1e308

        with np.errstate(over="raise", invalid="raise", divide="raise"):
            anomalies = AnomalyDetector().detect(
                positions, np.arange(60, dtype=float), gripper_states=states, gripper_commands=commands
            )

        assert [a.frame_range for a in anomalies if a.type == AnomalyType.OSCILLATION] == [(0, 20), (21, 41), (42, 58)]
        assert [a.frame_range for a in anomalies if a.type == AnomalyType.GRIPPER_FAILURE] == [(20, 30)]

    def test_ordinary_seeded_velocity_spikes_match_independent_statistics(self) -> None:
        random = np.random.default_rng(1846)
        for _ in range(20):
            positions = np.cumsum(random.normal(size=(60, 3)), axis=0)
            timestamps = np.cumsum(random.uniform(0.01, 0.1, size=60))
            magnitudes = [
                math.hypot(*((right - left) / (end - start)))
                for left, right, start, end in zip(positions, positions[1:], timestamps, timestamps[1:], strict=False)
            ]
            mean = math.fsum(magnitudes) / len(magnitudes)
            std = math.sqrt(math.fsum((value - mean) ** 2 for value in magnitudes) / len(magnitudes))
            expected = [i for i, value in enumerate(magnitudes) if (value - mean) / std > 3]

            anomalies = AnomalyDetector().detect(positions, timestamps)

            actual = [
                index for a in anomalies if a.type == AnomalyType.VELOCITY_SPIKE for index in range(*a.frame_range)
            ]
            assert actual == expected
