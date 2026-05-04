"""Tests for the Isaac Sim shutdown workaround helpers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from .conftest import load_training_module


@pytest.fixture
def isaaclab_stub(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Inject a minimal isaaclab.sim module exposing SimulationContext.instance."""
    sim_instance = SimpleNamespace()
    sim_module = ModuleType("isaaclab.sim")
    sim_module.SimulationContext = MagicMock()  # type: ignore[attr-defined]
    sim_module.SimulationContext.instance.return_value = sim_instance  # type: ignore[attr-defined]

    parent = ModuleType("isaaclab")
    parent.sim = sim_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "isaaclab", parent)
    monkeypatch.setitem(sys.modules, "isaaclab.sim", sim_module)
    return SimpleNamespace(sim=sim_instance, sim_module=sim_module)


@pytest.fixture
def shutdown_module(monkeypatch: pytest.MonkeyPatch):
    """Load simulation_shutdown with os.fork patched to avoid real forks on Windows."""
    module = load_training_module(
        "training_rl_simulation_shutdown",
        "training/rl/simulation_shutdown.py",
    )
    return module


class TestPrepareForShutdown:
    def test_happy_path_disables_handle_unsubscribes_and_forks(
        self,
        shutdown_module,
        isaaclab_stub: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        handle = MagicMock()
        isaaclab_stub.sim._app_control_on_stop_handle = handle
        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 1234, raising=False)
        monkeypatch.setattr(shutdown_module.os, "getpid", lambda: 99)

        shutdown_module.prepare_for_shutdown(timeout=5)

        assert isaaclab_stub.sim._disable_app_control_on_stop_handle is True
        handle.unsubscribe.assert_called_once()
        assert isaaclab_stub.sim._app_control_on_stop_handle is None

    def test_handles_missing_sim_instance(
        self, shutdown_module, isaaclab_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        isaaclab_stub.sim_module.SimulationContext.instance.return_value = None
        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 1, raising=False)

        shutdown_module.prepare_for_shutdown(timeout=1)  # should not raise

    def test_handles_handle_already_none(
        self, shutdown_module, isaaclab_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        isaaclab_stub.sim._app_control_on_stop_handle = None
        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 1, raising=False)

        shutdown_module.prepare_for_shutdown(timeout=1)

    def test_disable_handler_swallows_exceptions(self, shutdown_module, monkeypatch: pytest.MonkeyPatch) -> None:
        broken = ModuleType("isaaclab.sim")

        class _Boom:
            @staticmethod
            def instance() -> None:
                raise RuntimeError("nope")

        broken.SimulationContext = _Boom  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "isaaclab.sim", broken)
        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 1, raising=False)

        shutdown_module.prepare_for_shutdown(timeout=1)  # logs warning, no raise

    def test_unsubscribe_swallows_exceptions(
        self, shutdown_module, isaaclab_stub: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_handle = MagicMock()
        bad_handle.unsubscribe.side_effect = RuntimeError("boom")
        isaaclab_stub.sim._app_control_on_stop_handle = bad_handle
        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 1, raising=False)

        shutdown_module.prepare_for_shutdown(timeout=1)


class TestWatchdog:
    def test_child_branch_kills_parent(
        self,
        shutdown_module,
        isaaclab_stub: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        kill_calls: list[tuple[int, int]] = []
        exit_calls: list[int] = []

        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 0, raising=False)
        monkeypatch.setattr(shutdown_module.os, "getpid", lambda: 42)
        monkeypatch.setattr(shutdown_module.os, "kill", lambda pid, sig: kill_calls.append((pid, sig)), raising=False)
        monkeypatch.setattr(shutdown_module.os, "_exit", lambda code: exit_calls.append(code), raising=False)
        monkeypatch.setattr(shutdown_module.signal, "SIGKILL", 9, raising=False)

        time_module = ModuleType("time")
        time_module.sleep = lambda _seconds: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "time", time_module)

        shutdown_module._start_shutdown_watchdog(timeout=0)

        assert kill_calls == [(42, 9)]
        assert exit_calls == [0]

    def test_child_branch_handles_parent_already_gone(self, shutdown_module, monkeypatch: pytest.MonkeyPatch) -> None:
        exit_calls: list[int] = []

        monkeypatch.setattr(shutdown_module.os, "fork", lambda: 0, raising=False)
        monkeypatch.setattr(shutdown_module.os, "getpid", lambda: 7)

        def _raise(_pid: int, _sig: int) -> None:
            raise ProcessLookupError

        monkeypatch.setattr(shutdown_module.os, "kill", _raise, raising=False)
        monkeypatch.setattr(shutdown_module.os, "_exit", lambda code: exit_calls.append(code), raising=False)
        monkeypatch.setattr(shutdown_module.signal, "SIGKILL", 9, raising=False)

        time_module = ModuleType("time")
        time_module.sleep = lambda _seconds: None  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "time", time_module)

        shutdown_module._start_shutdown_watchdog(timeout=0)

        assert exit_calls == [0]
