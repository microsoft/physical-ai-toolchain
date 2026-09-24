from __future__ import annotations

import signal

import pytest

from operator_worker import parent_watch


class FakeLibC:
    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[int, signal.Signals]] = []

    def prctl(self, option: int, death_signal: signal.Signals) -> int:
        self.calls.append((option, death_signal))
        return self.result


def test_given_live_expected_parent_when_armed_then_sigterm_is_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libc = FakeLibC()
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(parent_watch.ctypes, "CDLL", lambda *_args, **_kwargs: libc)
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: 41)
    monkeypatch.setattr(parent_watch.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    parent_watch.arm_parent_death_signal(41)

    assert libc.calls == [(1, signal.SIGTERM)]
    assert killed == []


def test_given_parent_changes_during_registration_when_armed_then_process_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, signal.Signals]] = []
    monkeypatch.setattr(parent_watch.ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibC())
    monkeypatch.setattr(parent_watch.os, "getppid", lambda: 99)
    monkeypatch.setattr(parent_watch.os, "getpid", lambda: 42)
    monkeypatch.setattr(parent_watch.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    parent_watch.arm_parent_death_signal(41)

    assert killed == [(42, signal.SIGTERM)]
