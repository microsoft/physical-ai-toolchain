"""Unit tests for ``metrics.plotting``."""

from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pytest
from metrics.plotting import (
    JOINT_NAMES,
    plot_action_deltas,
    plot_aggregate_summary,
    plot_cumulative_positions,
    plot_error_heatmap,
    plot_summary_panel,
)
from sil.robot_types import NUM_JOINTS


@pytest.fixture(autouse=True)
def _close_figures():
    """Close all matplotlib figures after each test to prevent memory leaks."""
    yield
    plt.close("all")


class TestPlotActionDeltas:
    def test_returns_figure(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_action_deltas(predicted, ground_truth, episode=1, fps=30.0)
        assert isinstance(fig, plt.Figure)

    def test_subplot_count(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_action_deltas(predicted, ground_truth, episode=1, fps=30.0)
        assert len(fig.axes) == NUM_JOINTS

    def test_custom_joint_names(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        names = [f"j{i}" for i in range(NUM_JOINTS)]
        fig = plot_action_deltas(predicted, ground_truth, episode=1, fps=30.0, joint_names=names)
        assert isinstance(fig, plt.Figure)


class TestPlotCumulativePositions:
    def test_returns_figure(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_cumulative_positions(predicted, ground_truth, episode=2, fps=30.0)
        assert isinstance(fig, plt.Figure)

    def test_subplot_count(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_cumulative_positions(predicted, ground_truth, episode=2, fps=30.0)
        assert len(fig.axes) == NUM_JOINTS


class TestPlotErrorHeatmap:
    def test_returns_figure(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_error_heatmap(predicted, ground_truth, episode=3, fps=30.0)
        assert isinstance(fig, plt.Figure)

    def test_single_axis(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_error_heatmap(predicted, ground_truth, episode=3, fps=30.0)
        # One heatmap axes + one colorbar axes.
        assert len(fig.axes) == 2


class TestPlotSummaryPanel:
    def test_returns_figure(
        self,
        action_arrays: tuple[np.ndarray, np.ndarray],
        inference_times: np.ndarray,
    ) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_summary_panel(predicted, ground_truth, inference_times, episode=4, fps=30.0)
        assert isinstance(fig, plt.Figure)

    def test_2x2_layout(
        self,
        action_arrays: tuple[np.ndarray, np.ndarray],
        inference_times: np.ndarray,
    ) -> None:
        predicted, ground_truth = action_arrays
        fig = plot_summary_panel(predicted, ground_truth, inference_times, episode=4, fps=30.0)
        assert len(fig.axes) == 4

    def test_requires_inference_times(self, action_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        predicted, ground_truth = action_arrays
        with pytest.raises(TypeError):
            plot_summary_panel(predicted, ground_truth, episode=4, fps=30.0)  # type: ignore[call-arg]


def test_joint_names_default_length() -> None:
    assert len(JOINT_NAMES) == NUM_JOINTS


class TestPlotAggregateSummary:
    @pytest.fixture()
    def episode_metrics(self) -> list[dict]:
        return [
            {
                "episode": 1,
                "mse": 0.01,
                "mae": 0.05,
                "throughput_hz": 120.0,
                "avg_inference_ms": 8.3,
                "per_joint_mae": [0.04, 0.05, 0.06, 0.03, 0.07, 0.02],
            },
            {
                "episode": 2,
                "mse": 0.02,
                "mae": 0.08,
                "throughput_hz": 110.0,
                "avg_inference_ms": 9.1,
                "per_joint_mae": [0.05, 0.06, 0.07, 0.04, 0.08, 0.03],
            },
            {
                "episode": 3,
                "mse": 0.005,
                "mae": 0.03,
                "throughput_hz": 130.0,
                "avg_inference_ms": 7.7,
                "per_joint_mae": [0.03, 0.04, 0.05, 0.02, 0.06, 0.01],
            },
        ]

    def test_returns_figure(self, episode_metrics) -> None:
        fig = plot_aggregate_summary(episode_metrics)
        assert isinstance(fig, plt.Figure)

    def test_2x2_layout(self, episode_metrics) -> None:
        fig = plot_aggregate_summary(episode_metrics)
        assert len(fig.axes) == 4

    def test_custom_joint_names(self, episode_metrics) -> None:
        names = ["j1", "j2", "j3", "j4", "j5", "j6"]
        fig = plot_aggregate_summary(episode_metrics, joint_names=names)
        ax_top_right = fig.axes[1]
        tick_labels = [t.get_text() for t in ax_top_right.get_xticklabels()]
        assert tick_labels == names

    def test_uses_default_joint_names(self, episode_metrics) -> None:
        fig = plot_aggregate_summary(episode_metrics)
        ax_top_right = fig.axes[1]
        tick_labels = [t.get_text() for t in ax_top_right.get_xticklabels()]
        assert tick_labels == list(JOINT_NAMES)
