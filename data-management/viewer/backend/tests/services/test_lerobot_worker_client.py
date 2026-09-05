"""Subprocess integration tests for the protocol v2 hardware worker client."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.api.operator.lerobot_worker_client import LerobotWorkerClient
from src.api.operator.models import OperatorMode
from src.api.operator.protocol import HardwareWorkerCleanup

_FAKE_WORKER = Path(__file__).parents[1] / "scripts/fake_operator_worker.py"


@contextmanager
def _client(
    *,
    behavior: str = "normal",
    on_log=None,
    on_preview=None,
    on_rate=None,
    on_telemetry=None,
) -> Iterator[LerobotWorkerClient]:
    lease_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        yield LerobotWorkerClient(
            command=[sys.executable, str(_FAKE_WORKER)],
            timeout_s=2.0,
            service_instance_id="service-1",
            profile={"name": "so101"},
            profile_fingerprint="profile",
            resource_fingerprint="resource",
            environment={"FAKE_WORKER_BEHAVIOR": behavior},
            lease_fd=lease_fd,
            on_log=on_log,
            on_preview=on_preview,
            on_rate=on_rate,
            on_telemetry=on_telemetry,
        )
    finally:
        os.close(lease_fd)


async def test_initialize_run_and_cleanup_sequence() -> None:
    logs = []
    previews = []
    rates = []
    telemetry = []
    with _client(
        on_log=logs.append,
        on_preview=previews.append,
        on_rate=rates.append,
        on_telemetry=telemetry.append,
    ) as client:
        await client.launch("session-1", OperatorMode.TELEOPERATE)
        await client.wait_ready()
        for _ in range(50):
            if rates and telemetry:
                break
            await asyncio.sleep(0.01)
        acknowledgement = await client.command("session-1", "stop-1", "cancel")

        assert acknowledgement.cleanup_complete is True
        assert client.torque_verified_off is True
        assert "fake stderr" in client.stderr_text
        assert logs == ["fake stderr"]
        assert previews[0].camera == "wrist"
        assert previews[0].jpeg_base64 == "anBlZw=="
        assert rates[0].actual_hz == 29.5
        assert telemetry[0].commanded == {"joint": 3.0}
        assert await client.wait() == 0


async def test_record_commands_report_progress_and_finalize() -> None:
    with _client() as client:
        client.settings = {
            **client.settings,
            "mode": "record",
            "control_fps": 30,
            "dataset_root": "/tmp/demo",
            "dataset_id": "demo",
        }
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


async def test_record_cancel_uses_extended_cleanup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _client() as client:
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
        acknowledgement = await client.command("session-1", "discard-1", "cancel")

        send_stop.assert_awaited_once_with("session-1", "discard-1", 3, timeout_s=120.0)
        assert acknowledgement.cleanup_complete is True


async def test_bad_protocol_version_fails_closed() -> None:
    with _client(behavior="bad_version") as client:
        await client.launch("session-1", OperatorMode.TELEOPERATE)
        with pytest.raises(RuntimeError, match="protocol"):
            await client.wait_ready()
        await client.terminate()


@pytest.mark.parametrize(
    ("behavior", "message"),
    [
        ("bad_session", "session mismatch"),
        ("bad_runtime", "runtime contract"),
        ("bad_nonce", "nonce mismatch"),
        ("torque_off", "confirm follower torque"),
    ],
)
async def test_handshake_contract_failures_are_rejected(behavior: str, message: str) -> None:
    with _client(behavior=behavior) as client:
        await client.launch("session-1", OperatorMode.TELEOPERATE)

        with pytest.raises(RuntimeError, match=message):
            await client.wait_ready()

        await client.terminate()


async def test_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOULD_NOT_REACH_WORKER", "secret")
    with _client() as client:
        environment = client.build_environment("session-1")

        assert environment["OPERATOR_SESSION_ID"] == "session-1"
        assert environment["OPERATOR_PARENT_PID"] == str(os.getpid())
        assert "SHOULD_NOT_REACH_WORKER" not in environment
        assert environment["OPERATOR_HOST_LEASE_FD"] == str(client.lease_fd)
        assert client.pass_fds == (client.lease_fd,)


async def test_recovery_worker_confirms_torque_off() -> None:
    with _client() as client:
        assert await client.recover() is True


async def test_recovery_worker_rejects_unconfirmed_torque_off() -> None:
    with _client() as client:
        client.environment["FAKE_RECOVERY"] = "false"

        assert await client.recover() is False
        assert client.torque_verified_off is False


async def test_startup_failure_accepts_unsolicited_confirmed_cleanup() -> None:
    with _client(behavior="startup_cleanup") as client:
        await client.launch("session-1", OperatorMode.RECORD)

        with pytest.raises(RuntimeError, match="startup"):
            await client.wait_ready()

        assert await client.wait() == 1
        assert client.torque_verified_off is True
        assert await client.terminate() is True


async def test_unlaunched_client_rejects_lifecycle_operations() -> None:
    with _client() as client:
        assert client.pid is None
        with pytest.raises(RuntimeError, match="not launched"):
            await client.wait_ready()
        with pytest.raises(RuntimeError, match="not launched"):
            await client.wait()
        with pytest.raises(RuntimeError, match="Invalid"):
            await client.command("wrong-session", "save-1", "save")

        client._session_id = "session-1"
        with pytest.raises(RuntimeError, match="record mode"):
            await client.command("session-1", "save-1", "save")
        with pytest.raises(RuntimeError, match="cleanup channel"):
            await client._send_stop("session-1", "stop-1", 1)
        with pytest.raises(RuntimeError, match="cleanup channel"):
            await client._await_cleanup()
        with pytest.raises(RuntimeError, match="input"):
            await client._send("payload")
        with pytest.raises(RuntimeError, match="hello channel"):
            await client._await_future(None, "hello")
        assert await client.terminate() is False


async def test_recovery_output_reader_enforces_limit() -> None:
    assert await LerobotWorkerClient._read_limited(None) == b""

    stream = asyncio.StreamReader()
    stream.feed_data(b"x" * 5)
    stream.feed_eof()
    assert await LerobotWorkerClient._read_limited(stream, limit=5) == b"x" * 5

    oversized = asyncio.StreamReader()
    oversized.feed_data(b"x" * 6)
    oversized.feed_eof()
    with pytest.raises(RuntimeError, match="exceeded"):
        await LerobotWorkerClient._read_limited(oversized, limit=5)
