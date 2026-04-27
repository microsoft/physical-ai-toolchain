"""Unit tests for ``sil/scripts/batch-lerobot-eval.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# The script imports ``inference.plotting`` at module level.  Provide a
# lightweight stub so the module can be loaded without the full
# inference package.
_inference = types.ModuleType("inference")
_inference_plotting = types.ModuleType("inference.plotting")
for _name in (
    "plot_action_deltas",
    "plot_aggregate_summary",
    "plot_cumulative_positions",
    "plot_error_heatmap",
    "plot_summary_panel",
):
    setattr(_inference_plotting, _name, lambda *a, **kw: None)
_inference.plotting = _inference_plotting  # type: ignore[attr-defined]
sys.modules.setdefault("inference", _inference)
sys.modules.setdefault("inference.plotting", _inference_plotting)

_SCRIPT = Path(__file__).resolve().parents[1] / "sil" / "scripts" / "batch-lerobot-eval.py"
_spec = importlib.util.spec_from_file_location("batch_lerobot_eval", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

parse_episode_range = _mod.parse_episode_range
run_inference = _mod.run_inference
plot_episode = _mod.plot_episode


class TestParseEpisodeRange:
    def test_single_number(self) -> None:
        assert parse_episode_range("5") == [5]

    def test_comma_separated(self) -> None:
        assert parse_episode_range("0,2,5") == [0, 2, 5]

    def test_range(self) -> None:
        assert parse_episode_range("1-3") == [1, 2, 3]

    def test_mixed_range_and_numbers(self) -> None:
        assert parse_episode_range("0,2,5-7") == [0, 2, 5, 6, 7]

    def test_deduplication(self) -> None:
        assert parse_episode_range("1,1,2") == [1, 2]

    def test_overlapping_range_and_number(self) -> None:
        assert parse_episode_range("3,1-5") == [1, 2, 3, 4, 5]

    def test_single_element_range(self) -> None:
        assert parse_episode_range("4-4") == [4]

    def test_result_is_sorted(self) -> None:
        assert parse_episode_range("9,1,5") == [1, 5, 9]

    def test_whitespace_handling(self) -> None:
        assert parse_episode_range(" 1 , 3 , 5 ") == [1, 3, 5]

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_episode_range("abc")


class TestRunInference:
    def test_cached_predictions_returned(self, tmp_path: Path) -> None:
        out_path = tmp_path / "ep001_predictions.npz"
        out_path.write_bytes(b"cached")
        result = run_inference(1, "repo", "dataset", "cpu", tmp_path)
        assert result == out_path

    def test_successful_inference_returns_path(self, tmp_path: Path, monkeypatch) -> None:
        mock_result = MagicMock(returncode=0, stdout="MSE: 0.01\n", stderr="")
        monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **kw: mock_result)
        result = run_inference(1, "repo", "dataset", "cpu", tmp_path)
        assert result == tmp_path / "ep001_predictions.npz"

    def test_failed_inference_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error occurred")
        monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **kw: mock_result)
        result = run_inference(1, "repo", "dataset", "cpu", tmp_path)
        assert result is None

    def test_metric_lines_printed(self, tmp_path: Path, monkeypatch, capsys) -> None:
        mock_result = MagicMock(returncode=0, stdout="Loading model...\nMSE: 0.01\nMAE: 0.05\nDone.", stderr="")
        monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **kw: mock_result)
        run_inference(1, "repo", "dataset", "cpu", tmp_path)
        captured = capsys.readouterr()
        assert "MSE: 0.01" in captured.out
        assert "MAE: 0.05" in captured.out


class TestPlotEpisode:
    @staticmethod
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

    def test_returns_metrics_dict(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        pred = np.array([[1.0, 2.0], [3.0, 4.0]])
        gt = np.array([[1.1, 2.1], [3.1, 4.1]])
        inf_times = np.array([0.01, 0.02])
        npz_path = tmp_path / "predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        metrics = plot_episode(1, npz_path, tmp_path, fps=30.0, dpi=150)

        assert metrics is not None
        assert metrics["episode"] == 1
        assert metrics["steps"] == 2
        assert "mse" in metrics
        assert "mae" in metrics
        assert "per_joint_mae" in metrics
        assert "avg_inference_ms" in metrics
        assert "throughput_hz" in metrics

    def test_creates_episode_directory(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        pred = np.array([[1.0]])
        gt = np.array([[1.1]])
        inf_times = np.array([0.01])
        npz_path = tmp_path / "predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        plot_episode(5, npz_path, tmp_path, fps=30.0, dpi=150)
        assert (tmp_path / "episode_005").is_dir()

    def test_metric_values(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        pred = np.array([[0.0, 0.0]])
        gt = np.array([[1.0, 0.0]])
        inf_times = np.array([0.01])
        npz_path = tmp_path / "predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        metrics = plot_episode(1, npz_path, tmp_path, fps=30.0, dpi=150)

        assert metrics["mse"] == pytest.approx(0.5)
        assert metrics["mae"] == pytest.approx(0.5)
        assert metrics["avg_inference_ms"] == pytest.approx(10.0)
        assert metrics["throughput_hz"] == pytest.approx(100.0)

    def test_zero_inference_time_throughput(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        pred = np.array([[1.0]])
        gt = np.array([[1.0]])
        inf_times = np.array([0.0])
        npz_path = tmp_path / "predictions.npz"
        np.savez(npz_path, predicted=pred, ground_truth=gt, inference_times=inf_times)

        metrics = plot_episode(1, npz_path, tmp_path, fps=30.0, dpi=150)
        assert metrics["throughput_hz"] == 0.0


class TestMain:
    @staticmethod
    def _stub_plotting(monkeypatch) -> MagicMock:
        mock_fig = MagicMock()
        for name in (
            "plot_action_deltas",
            "plot_cumulative_positions",
            "plot_error_heatmap",
            "plot_summary_panel",
            "plot_aggregate_summary",
        ):
            monkeypatch.setattr(_mod, name, lambda *a, _f=mock_fig, **kw: _f)
        monkeypatch.setattr(_mod.plt, "close", lambda *a: None)
        return mock_fig

    def test_plot_only_generates_metrics_json(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        pred = np.array([[1.0, 2.0]])
        gt = np.array([[1.1, 2.1]])
        inf_times = np.array([0.01])
        for ep in (1, 2):
            np.savez(
                tmp_path / f"ep{ep:03d}_predictions.npz",
                predicted=pred,
                ground_truth=gt,
                inference_times=inf_times,
            )

        out_dir = tmp_path / "output"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "batch-lerobot-eval",
                "--plot-only",
                "--npz-dir",
                str(tmp_path),
                "--episodes",
                "1-2",
                "--output-dir",
                str(out_dir),
            ],
        )
        _mod.main()

        metrics_path = out_dir / "eval_metrics.json"
        assert metrics_path.exists()
        metrics = json.loads(metrics_path.read_text())
        assert len(metrics) == 2

    def test_missing_npz_skipped_in_plot_only(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        out_dir = tmp_path / "output"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "batch-lerobot-eval",
                "--plot-only",
                "--npz-dir",
                str(tmp_path),
                "--episodes",
                "1",
                "--output-dir",
                str(out_dir),
            ],
        )
        _mod.main()
        assert not (out_dir / "eval_metrics.json").exists()

    def test_requires_policy_and_dataset_without_plot_only(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "batch-lerobot-eval",
                "--episodes",
                "1",
                "--output-dir",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit):
            _mod.main()

    def test_plot_episode_returning_none_continues_loop(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        monkeypatch.setattr(_mod, "plot_episode", lambda *a, **kw: None)
        pred = np.array([[1.0, 2.0]])
        gt = np.array([[1.1, 2.1]])
        inf_times = np.array([0.01])
        for ep in (1, 2):
            np.savez(
                tmp_path / f"ep{ep:03d}_predictions.npz",
                predicted=pred,
                ground_truth=gt,
                inference_times=inf_times,
            )

        out_dir = tmp_path / "output"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "batch-lerobot-eval",
                "--plot-only",
                "--npz-dir",
                str(tmp_path),
                "--episodes",
                "1-2",
                "--output-dir",
                str(out_dir),
            ],
        )
        _mod.main()
        assert not (out_dir / "eval_metrics.json").exists()

    def test_inference_failure_skips_plotting(self, tmp_path: Path, monkeypatch) -> None:
        self._stub_plotting(monkeypatch)
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error")
        monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **kw: mock_result)

        out_dir = tmp_path / "output"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "batch-lerobot-eval",
                "--policy-repo",
                "repo",
                "--dataset-dir",
                str(tmp_path),
                "--episodes",
                "1-2",
                "--output-dir",
                str(out_dir),
            ],
        )
        _mod.main()
        assert not (out_dir / "eval_metrics.json").exists()
