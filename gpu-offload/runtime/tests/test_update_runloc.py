from __future__ import annotations

import threading
import uuid

from remoter import remoter


def _runtime_with_task(
    loc: str,
    *,
    rmtport: int | None,
) -> tuple[remoter.Remoter, uuid.UUID, list[uuid.UUID]]:
    runtime = remoter.Remoter.__new__(remoter.Remoter)
    runtime.runloc = {}
    runtime.loclock = threading.Lock()
    runtime.fnlock = threading.Lock()
    runtime.rmtport = rmtport
    uid = uuid.uuid4()
    runtime.tasks = {uid: {"loc": loc, "func_name": "f", "args": ()}}
    runtime.tasksByName = {"task": {uid}}
    cancelled: list[uuid.UUID] = []
    runtime.cancelRemotedFunction = cancelled.append
    return runtime, uid, cancelled


def test_reload_with_same_location_keeps_in_flight_call() -> None:
    runtime, _, cancelled = _runtime_with_task("tcp://10.0.0.1:30001", rmtport=30001)
    runtime.updateRunLoc({"task": {"locations": {"10.0.0.1:30001": 1.0}}})
    runtime.updateRunLoc({"task": {"locations": {"10.0.0.1:30001": 1.0}}})
    assert cancelled == []


def test_rewritten_port_still_matches_task_location() -> None:
    runtime, _, cancelled = _runtime_with_task("tcp://10.0.0.1:40000", rmtport=40000)
    runtime.updateRunLoc({"task": {"locations": {"10.0.0.1:30001": 1.0}}})
    assert cancelled == []


def test_removed_location_cancels_in_flight_call() -> None:
    runtime, uid, cancelled = _runtime_with_task("tcp://10.0.0.1:30001", rmtport=30001)
    runtime.updateRunLoc({"task": {"locations": {"10.0.0.2:30001": 1.0}}})
    assert cancelled == [uid]


def test_zero_weight_location_cancels_in_flight_call() -> None:
    runtime, uid, cancelled = _runtime_with_task("tcp://10.0.0.1:30001", rmtport=30001)
    runtime.updateRunLoc({"task": {"locations": {"10.0.0.1:30001": 0.0, "10.0.0.2:30001": 1.0}}})
    assert cancelled == [uid]
