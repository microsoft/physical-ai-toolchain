"""Tests for metrics/plot-lerobot-trajectories.py."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "metrics" / "plot-lerobot-trajectories.py"

_inference_pkg = types.ModuleType("inference")
_inference_pkg.__path__ = []  # type: ignore[attr-defined]
_plotting_mod = types.ModuleType("inference.plotting")
for _name in (
    "plot_action_deltas",
    "plot_cumulative_positions",
    "plot_error_heatmap",
    "plot_summary_panel",
):
    setattr(_plotting_mod, _name, lambda *a, **kw: None)
sys.modules.setdefault("inference", _inference_pkg)
sys.modules["inference.plotting"] = _plotting_mod

_spec = importlib.util.spec_from_file_location("plot_lerobot_trajectories", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _stub_plotting(monkeypatch) -> MagicMock:
    mock_fig = MagicMock()
    for name in (
        "plot_action_deltas",
        "plot_cumulative_positions",
        "plot_error_heatmap",
        "plot_summary_panel",
    ):
        monkeypatch.setattr(_mod, name, lambda *a, _f=mock_fig, **kw: _f)
    monkeypatch.setattr(_mod.plt, "close", lambda *a: None)
    return mock_fig


def _write_npz(path: Path) -> None:
    np.savez(
        path,
        predicted=np.array([[1.0, 2.0], [3.0, 4.0]]),
        ground_truth=np.array([[1.1, 2.1], [3.1, 4.1]]),
        inference_times=np.array([0.01, 0.02]),
    )


class TestMain:
    def test_missing_file_exits(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plot-lerobot-trajectories",
                str(tmp_path / "nope.npz"),
            ],
        )
        with pytest.raises(SystemExit) as exc_info:
            _mod.main()
        assert exc_info.value.code == 1

    def test_default_output_dir_creates_sibling(self, tmp_path: Path, monkeypatch) -> None:
        _stub_plotting(monkeypatch)
        npz_path = tmp_path / "predictions.npz"
        _write_npz(npz_path)

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plot-lerobot-trajectories",
                str(npz_path),
            ],
        )
        _mod.main()
        assert (tmp_path / "trajectory_plots").is_dir()

    def test_custom_output_dir_used(self, tmp_path: Path, monkeypatch) -> None:
        mock_fig = _stub_plotting(monkeypatch)
        npz_path = tmp_path / "predictions.npz"
        _write_npz(npz_path)
        out_dir = tmp_path / "custom_out"

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "plot-lerobot-trajectories",
                str(npz_path),
                "--output-dir",
                str(out_dir),
                "--episode",
                "5",
                "--fps",
                "60.0",
                "--dpi",
                "200",
            ],
        )
        _mod.main()
        assert out_dir.is_dir()
        assert mock_fig.savefig.call_count == 4
