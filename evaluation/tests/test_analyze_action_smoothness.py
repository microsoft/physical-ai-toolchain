"""Unit tests for the pure metric helpers in ``metrics.analyze_action_smoothness``."""

from __future__ import annotations

import numpy as np
from metrics.analyze_action_smoothness import (
    _aggregate,
    _direction_reversals,
    _episode_metrics,
    _sparc,
)


class TestSparc:
    def test_returns_nan_for_short_sequences(self):
        assert np.isnan(_sparc(np.zeros(4), 30.0))

    def test_returns_nan_for_flat_signal(self):
        assert np.isnan(_sparc(np.zeros(16), 30.0))

    def test_returns_negative_finite_for_real_signal(self):
        triangle = np.concatenate([np.linspace(0.0, 1.0, 32), np.linspace(1.0, 0.0, 32)])
        value = _sparc(triangle, 30.0)
        assert np.isfinite(value)
        assert value < 0.0


class TestDirectionReversals:
    def test_zero_for_single_sample(self):
        assert _direction_reversals(np.zeros((1, 3))) == 0

    def test_counts_sign_flips(self):
        signal = np.array([[1.0], [-1.0], [1.0]])
        assert _direction_reversals(signal) == 2

    def test_zero_velocity_carries_previous_sign(self):
        signal = np.array([[1.0], [0.0], [-1.0]])
        assert _direction_reversals(signal) == 1


class TestEpisodeMetrics:
    def test_marks_short_episodes_skipped(self):
        result = _episode_metrics(np.zeros((1, 6)), None, 30.0, arm_dims=6, gripper_dim=None)
        assert result == {"length": 1, "skipped_too_short": True}

    def test_computes_metrics_for_full_episode(self):
        rng = np.random.default_rng(0)
        actions = rng.normal(size=(20, 7))
        gripper = np.array([i % 2 for i in range(20)], dtype=np.int8)
        result = _episode_metrics(actions, gripper, 30.0, arm_dims=6, gripper_dim=6)
        assert result["length"] == 20
        assert len(result["joint_range"]) == 6
        for key in ("delta_rms", "path_length", "direction_reversals", "sparc_velocity_norm"):
            assert key in result
        assert result["gripper_action_range"] >= 0.0
        assert result["gripper_flips"] == 19


class TestAggregate:
    def test_summarizes_numeric_columns(self):
        rows = [
            {"length": 10, "delta_rms": 1.0, "path_length": 5.0, "direction_reversals": 2},
            {"length": 20, "delta_rms": 3.0, "path_length": 7.0, "direction_reversals": 4},
        ]
        summary = _aggregate(rows)
        assert summary["episode_count"] == 2
        assert summary["delta_rms"]["mean"] == 2.0
        assert summary["length"]["min"] == 10.0
        assert summary["length"]["max"] == 20.0
