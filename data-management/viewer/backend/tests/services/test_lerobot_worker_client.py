"""Subprocess integration tests for the protocol v2 hardware worker client."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.api.operator.lerobot_worker_client import LerobotWorkerClient
from src.api.operator.models import OperatorMode
from src.api.operator.protocol import HardwareWorkerCleanup

_FAKE_WORKER = Path(__file__).parents[1] / "scripts/fake_operator_worker.py"


def _client(*, behavior: str = "normal", on_preview=None) -> LerobotWorkerClient:
    lease_fd = os.open("/dev/null", os.O_RDONLY)
    return LerobotWorkerClient(
        command=[sys.executable, str(_FAKE_WORKER)],
        timeout_s=2.0,
        service_instance_id="service-1",
        profile={"name": "so101"},
        profile_fingerprint="profile",
        resource_fingerprint="resource",
        environment={"FAKE_WORKER_BEHAVIOR": behavior},
        lease_fd=lease_fd,
        on_preview=on_preview,
    )


async def test_initialize_run_and_cleanup_sequence() -> None:
    previews = []
    client = _client(on_preview=previews.append)
    try:
        await client.launch("session-1", OperatorMode.TELEOPERATE)
        await client.wait_ready()
        acknowledgement = await client.command("session-1", "stop-1", "cancel")

        assert acknowledgement.cleanup_complete is True
        assert client.torque_verified_off is True
        assert "fake stderr" in client.stderr_text
        assert previews[0].camera == "wrist"
        assert previews[0].jpeg_base64 == "anBlZw=="
        assert await client.wait() == 0
    finally:
        os.close(client.lease_fd)


async def test_record_commands_report_progress_and_finalize() -> None:
    client = _client()
    client.settings = {
        **client.settings,
        "mode": "record",
        "control_fps": 30,
        "dataset_root": "/tmp/demo",
        "dataset_id": "demo",
    }
    try:
        await client.launch("session-1", OperatorMode.RECORD)
        await client.wait_ready()

        paused = await client.command("session-1", "pause-1", "pause")
        resumed = await client.command("session-1", "resume-1", "resume")
        saved = await client.command("session-1", "save-1", "save")
        discarded = await client.command("session-1", "rerecord-1", "rerecord")
        finished = await client.command("session-1", "finish-1", "finish")

        assert paused.recording_phase == "paused"
        assert resumed.recording_phase == "recording"
        assert saved.episode_index == 1
        assert saved.recording_phase == "recording"
        assert discarded.episode_index == 1
        assert finished.recording_phase == "finalized"
        assert finished.cleanup_complete is True
        assert await client.wait() == 0
    finally:
        os.close(client.lease_fd)


async def test_record_cancel_uses_extended_cleanup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    client._session_id = "session-1"
    client._mode = OperatorMode.RECORD
    send_stop = AsyncMock(
        return_value=HardwareWorkerCleanup(
            service_instance_id="service-1",
            session_id="session-1",
            sequence=1,
            command_id="discard-1",
            cleanup_complete=True,
            torque_verified_off=True,
            released=["follower", "leader", "dataset"],
            errors=[],
        )
    )
    monkeypatch.setattr(client, "_send_stop", send_stop)
    try:
        acknowledgement = await client.command(
            "session-1", "discard-1", "cancel"
        )

        send_stop.assert_awaited_once_with(
            "session-1", "discard-1", 3, timeout_s=120.0
        )
        assert acknowledgement.cleanup_complete is True
    finally:
        os.close(client.lease_fd)


async def test_bad_protocol_version_fails_closed() -> None:
    client = _client(behavior="bad_version")
    try:
        await client.launch("session-1", OperatorMode.TELEOPERATE)
        with pytest.raises(RuntimeError, match="protocol"):
            await client.wait_ready()
        await client.terminate()
    finally:
        os.close(client.lease_fd)


async def test_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOULD_NOT_REACH_WORKER", "secret")
    client = _client()

    environment = client.build_environment("session-1")

    assert environment["OPERATOR_SESSION_ID"] == "session-1"
    assert environment["OPERATOR_PARENT_PID"] == str(os.getpid())
    assert "SHOULD_NOT_REACH_WORKER" not in environment
    assert environment["OPERATOR_HOST_LEASE_FD"] == str(client.lease_fd)
    assert client.pass_fds == (client.lease_fd,)
    os.close(client.lease_fd)


async def test_recovery_worker_confirms_torque_off() -> None:
    client = _client()
    try:
        assert await client.recover() is True
    finally:
        os.close(client.lease_fd)


async def test_startup_failure_accepts_unsolicited_confirmed_cleanup() -> None:
    client = _client(behavior="startup_cleanup")
    try:
        await client.launch("session-1", OperatorMode.RECORD)

        with pytest.raises(RuntimeError, match="startup"):
            await client.wait_ready()

        assert await client.wait() == 1
        assert client.torque_verified_off is True
        assert await client.terminate() is True
    finally:
        os.close(client.lease_fd)
