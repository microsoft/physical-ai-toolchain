"""Hypothesis property-based tests for inference plotting functions."""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from evaluation.metrics.plotting import (
    plot_action_deltas,
    plot_aggregate_summary,
    plot_cumulative_positions,
    plot_error_heatmap,
    plot_summary_panel,
)

matplotlib.use("Agg")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_N_STEPS = st.integers(min_value=2, max_value=50)
_N_JOINTS = st.integers(min_value=2, max_value=6)
_FPS = st.floats(min_value=1.0, max_value=120.0, allow_nan=False, allow_infinity=False)
_EPISODE = st.integers(min_value=0, max_value=9999)
_FINITE_ELEMENT = st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False)


@st.composite
def paired_arrays(draw):
    """Draw two float arrays of identical (N, J) shape."""
    n = draw(_N_STEPS)
    j = draw(_N_JOINTS)
    shape = (n, j)
    pred = draw(arrays(dtype=np.float64, shape=shape, elements=_FINITE_ELEMENT))
    gt = draw(arrays(dtype=np.float64, shape=shape, elements=_FINITE_ELEMENT))
    names = [f"j{i}" for i in range(j)]
    return pred, gt, names


@st.composite
def summary_inputs(draw):
    """Draw paired arrays plus a matching inference_times vector."""
    pred, gt, names = draw(paired_arrays())
    n = pred.shape[0]
    times = draw(
        arrays(
            dtype=np.float64,
            shape=(n,),
            elements=st.floats(min_value=1e-4, max_value=1.0, allow_nan=False, allow_infinity=False),
        )
    )
    return pred, gt, times, names


@st.composite
def episode_metrics_list(draw):
    """Draw a list of episode metric dicts with consistent joint count."""
    n_ep = draw(st.integers(min_value=1, max_value=5))
    n_j = draw(_N_JOINTS)
    names = [f"j{i}" for i in range(n_j)]
    metrics = []
    _unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    for ep in range(n_ep):
        per_joint = draw(arrays(dtype=np.float64, shape=(n_j,), elements=_unit))
        metrics.append(
            {
                "episode": ep,
                "mse": float(draw(_unit)),
                "mae": float(draw(_unit)),
                "throughput_hz": float(
                    draw(st.floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False))
                ),
                "avg_inference_ms": float(
                    draw(st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False))
                ),
                "per_joint_mae": per_joint.tolist(),
            }
        )
    return metrics, names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
# Hypothesis deadlines are disabled across this module: matplotlib/numerical
# paths exhibit high latency variance on CI runners (Windows GHA in particular)
# and regularly exceed the default 200ms deadline. Disabling removes a known
# source of cross-platform flake; perf regressions are caught by dedicated
# benchmarks, not Hypothesis timing.


@given(data=paired_arrays(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_plot_action_deltas_returns_figure(data, episode, fps):
    """plot_action_deltas returns a Figure for any valid (N, J) inputs."""
    pred, gt, names = data
    fig = plot_action_deltas(pred, gt, episode, fps, joint_names=names)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=paired_arrays(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_plot_cumulative_positions_returns_figure(data, episode, fps):
    """plot_cumulative_positions returns a Figure for any valid (N, J) inputs."""
    pred, gt, names = data
    fig = plot_cumulative_positions(pred, gt, episode, fps, joint_names=names)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=paired_arrays(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_plot_error_heatmap_returns_figure(data, episode, fps):
    """plot_error_heatmap returns a Figure for any valid (N, J) inputs."""
    pred, gt, names = data
    fig = plot_error_heatmap(pred, gt, episode, fps, joint_names=names)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=summary_inputs(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_plot_summary_panel_returns_figure(data, episode, fps):
    """plot_summary_panel returns a Figure for any valid inputs."""
    pred, gt, times, names = data
    fig = plot_summary_panel(pred, gt, times, episode, fps, joint_names=names)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=episode_metrics_list())
@settings(deadline=None, max_examples=15)
def test_plot_aggregate_summary_returns_figure(data):
    """plot_aggregate_summary returns a Figure for any valid episode metrics."""
    metrics, names = data
    fig = plot_aggregate_summary(metrics, joint_names=names)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=paired_arrays(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_action_deltas_uses_default_joint_names(data, episode, fps):
    """Passing joint_names=None falls back to JOINT_NAMES without error."""
    pred, gt, _ = data
    n_joints = pred.shape[1]
    pred6 = np.zeros((pred.shape[0], 6))
    gt6 = np.zeros((gt.shape[0], 6))
    pred6[:, :n_joints] = pred
    gt6[:, :n_joints] = gt
    fig = plot_action_deltas(pred6, gt6, episode, fps, joint_names=None)
    try:
        assert isinstance(fig, plt.Figure)
    finally:
        plt.close(fig)


@given(data=paired_arrays(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_error_heatmap_shape_consistency(data, episode, fps):
    """Heatmap image data has shape (J, N) — transposed from input."""
    pred, gt, names = data
    fig = plot_error_heatmap(pred, gt, episode, fps, joint_names=names)
    try:
        ax = fig.axes[0]
        images = ax.get_images()
        assert len(images) == 1
        img_data = images[0].get_array()
        assert img_data.shape == (pred.shape[1], pred.shape[0])
    finally:
        plt.close(fig)


@given(data=summary_inputs(), episode=_EPISODE, fps=_FPS)
@settings(deadline=None)
def test_summary_panel_has_four_subplots(data, episode, fps):
    """Summary panel always creates a 2x2 grid (4 axes)."""
    pred, gt, times, names = data
    fig = plot_summary_panel(pred, gt, times, episode, fps, joint_names=names)
    try:
        subplot_axes = [ax for ax in fig.axes if not hasattr(ax, "colorbar")]
        assert len(subplot_axes) == 4
    finally:
        plt.close(fig)


@given(data=episode_metrics_list())
@settings(deadline=None, max_examples=15)
def test_aggregate_summary_has_four_subplots(data):
    """Aggregate summary always creates a 2x2 grid (4+ axes including colorbar)."""
    metrics, names = data
    fig = plot_aggregate_summary(metrics, joint_names=names)
    try:
        assert len(fig.axes) >= 4
    finally:
        plt.close(fig)
