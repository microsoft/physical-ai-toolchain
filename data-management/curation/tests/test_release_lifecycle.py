"""Lease monitoring tolerates transient failures but never revives lost authority."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from physical_ai_curation.release.jobs import AuthorityError, CancellationError
from physical_ai_curation.release.runner import _LifecycleMonitor


def _monitor() -> tuple[_LifecycleMonitor, Mock]:
    store = Mock()
    store.settings.lease_seconds = 60
    store.now.return_value = 0
    return _LifecycleMonitor(store, "a" * 32, 1), store


def test_lifecycle_monitor_tolerates_transient_heartbeat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monitor, store = _monitor()
    store.heartbeat.side_effect = [OSError("temporary failure"), False]
    monkeypatch.setattr(monitor._stop, "wait", Mock(side_effect=[False, False, True]))

    monitor._run()
    monitor.check()

    assert store.heartbeat.call_count == 2


@pytest.mark.parametrize("failure", [OSError("lease deadline reached"), AuthorityError("fenced")])
def test_lifecycle_monitor_surfaces_authority_or_expired_heartbeat_failure(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    monitor, store = _monitor()
    store.now.return_value = 30
    store.heartbeat.side_effect = failure
    monkeypatch.setattr(monitor._stop, "wait", Mock(return_value=False))

    monitor._run()

    with pytest.raises(type(failure)) as raised:
        monitor.check()
    assert raised.value is failure
    store.heartbeat.assert_called_once_with("a" * 32, 1)


def test_lifecycle_monitor_observes_cancellation_without_replacing_first_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monitor, store = _monitor()
    store.heartbeat.return_value = True
    monkeypatch.setattr(monitor._stop, "wait", Mock(side_effect=[False, True]))

    monitor._run()
    monitor.request_stop(AuthorityError("later authority failure"))

    with pytest.raises(CancellationError, match="cancellation requested"):
        monitor.check()
