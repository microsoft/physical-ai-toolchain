"""Tests for SKRL MLflow agent wrapper utilities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from .conftest import load_training_module

_MOD = load_training_module(
    "training_rl_skrl_mlflow_agent",
    "training/rl/scripts/skrl_mlflow_agent.py",
)
_extract_metrics_from_agent = _MOD._extract_metrics_from_agent
_has_tracking_data = _MOD._has_tracking_data
create_mlflow_logging_wrapper = _MOD.create_mlflow_logging_wrapper


class TestHasTrackingData:
    def test_true_when_dict(self) -> None:
        agent = SimpleNamespace(tracking_data={"a": 1})
        assert _has_tracking_data(agent) is True

    def test_false_when_missing(self) -> None:
        assert _has_tracking_data(SimpleNamespace()) is False

    def test_false_when_not_dict(self) -> None:
        assert _has_tracking_data(SimpleNamespace(tracking_data="nope")) is False


class TestExtractMetrics:
    def test_extracts_tracking_data(self) -> None:
        agent = SimpleNamespace(tracking_data={"loss": 0.5})
        metrics = _extract_metrics_from_agent(agent)
        assert "loss" in metrics

    def test_metric_filter_drops_others(self) -> None:
        agent = SimpleNamespace(tracking_data={"loss": 0.5, "reward": 1.0})
        metrics = _extract_metrics_from_agent(agent, metric_filter={"loss"})
        assert "loss" in metrics
        assert "reward" not in metrics

    def test_extracts_standard_attributes(self) -> None:
        # learning_rate is a standard attribute
        agent = SimpleNamespace(tracking_data={}, learning_rate=0.001)
        metrics = _extract_metrics_from_agent(agent)
        assert metrics.get("learning_rate") == pytest.approx(0.001)

    def test_no_tracking_data_still_returns(self) -> None:
        agent = SimpleNamespace()
        metrics = _extract_metrics_from_agent(agent)
        assert metrics == {}


class TestCreateMlflowLoggingWrapper:
    def test_raises_when_agent_missing_tracking_data(self) -> None:
        agent = SimpleNamespace()
        with pytest.raises(AttributeError, match="tracking_data"):
            create_mlflow_logging_wrapper(agent, mlflow_module=MagicMock())

    def test_wraps_update_and_logs_metrics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        update_calls: list[dict[str, int]] = []

        def fake_update(*, timestep: int, timesteps: int) -> str:
            update_calls.append({"timestep": timestep, "timesteps": timesteps})
            return "ok"

        agent = SimpleNamespace(tracking_data={"loss": 0.5}, update=fake_update)
        mlflow = MagicMock()

        # Avoid heavy psutil/pynvml work — return empty system metrics
        monkeypatch.setattr(
            _MOD.SystemMetricsCollector,
            "collect_metrics",
            lambda self: {"system/cpu": 1.0},
        )

        wrapper = create_mlflow_logging_wrapper(agent, mlflow_module=mlflow)
        result = wrapper(timestep=10, timesteps=100)

        assert result == "ok"
        assert update_calls == [{"timestep": 10, "timesteps": 100}]
        mlflow.log_metrics.assert_called_once()
        kwargs = mlflow.log_metrics.call_args.kwargs
        assert kwargs["step"] == 10
        assert kwargs["synchronous"] is False
        logged = mlflow.log_metrics.call_args.args[0]
        assert "loss" in logged
        assert "system/cpu" in logged

    def test_system_metrics_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = SimpleNamespace(
            tracking_data={"loss": 0.5},
            update=lambda *, timestep, timesteps: None,
        )
        mlflow = MagicMock()

        def boom(self) -> dict[str, float]:
            raise RuntimeError("nope")

        monkeypatch.setattr(_MOD.SystemMetricsCollector, "collect_metrics", boom)

        wrapper = create_mlflow_logging_wrapper(agent, mlflow_module=mlflow)
        wrapper(timestep=1, timesteps=10)

        mlflow.log_metrics.assert_called_once()
        logged = mlflow.log_metrics.call_args.args[0]
        assert "loss" in logged

    def test_no_metrics_skips_log(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = SimpleNamespace(
            tracking_data={},
            update=lambda *, timestep, timesteps: None,
        )
        mlflow = MagicMock()
        monkeypatch.setattr(_MOD.SystemMetricsCollector, "collect_metrics", lambda self: {})

        wrapper = create_mlflow_logging_wrapper(agent, mlflow_module=mlflow)
        wrapper(timestep=2, timesteps=10)

        mlflow.log_metrics.assert_not_called()

    def test_outer_exception_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        agent = SimpleNamespace(
            tracking_data={"loss": 0.5},
            update=lambda *, timestep, timesteps: None,
        )
        mlflow = MagicMock()
        mlflow.log_metrics.side_effect = RuntimeError("boom")
        monkeypatch.setattr(_MOD.SystemMetricsCollector, "collect_metrics", lambda self: {})

        wrapper = create_mlflow_logging_wrapper(agent, mlflow_module=mlflow)
        wrapper(timestep=3, timesteps=10)  # should not raise
