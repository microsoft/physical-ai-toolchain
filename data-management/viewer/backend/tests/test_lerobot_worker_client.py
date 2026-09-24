"""Behavior tests for protocol-v2 worker command identity."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.api.operator.lerobot_worker_client import LerobotWorkerClient
from src.api.operator.models import OperatorAction, OperatorMode
from src.api.operator.protocol import (
    HardwareWorkerCommandAcknowledgement,
    HardwareWorkerHello,
    HardwareWorkerInitialized,
    HardwareWorkerRunning,
)


async def test_given_valid_hello_when_handshake_runs_then_all_commands_bind_child_identity(monkeypatch) -> None:
    # Arrange
    client = LerobotWorkerClient(
        command=["worker"],
        timeout_s=1.0,
        service_instance_id="service-1",
        profile={"fingerprint": "profile-1"},
        profile_fingerprint="profile-1",
        resource_fingerprint="resource-1",
        settings={"mode": "teleoperate", "execution_mode": "physical", "control_fps": 30},
    )
    client._session_id = "session-1"
    client._startup_nonce = "0123456789abcdef"
    client._process = SimpleNamespace(pid=4242)
    loop = asyncio.get_running_loop()
    client._hello = loop.create_future()
    client._hello.set_result(
        HardwareWorkerHello(
            session_id="session-1",
            worker_version="0.1.0",
            python_version="3.12.10",
            lerobot_version="0.6.1",
            pid=4242,
            supported_modes=["teleoperate", "record", "policy"],
        )
    )
    client._initialized = loop.create_future()
    client._initialized.set_result(
        HardwareWorkerInitialized(
            service_instance_id="service-1",
            session_id="session-1",
            sequence=1,
            startup_nonce="0123456789abcdef",
            resources={},
        )
    )
    client._running = loop.create_future()
    client._running.set_result(
        HardwareWorkerRunning(
            service_instance_id="service-1",
            session_id="session-1",
            sequence=2,
            torque_enabled=True,
        )
    )
    sent: list[str] = []

    async def capture(payload: str) -> None:
        sent.append(payload)

    monkeypatch.setattr(client, "_send", capture)

    # Act
    await client._handshake()

    # Assert
    commands = [json.loads(payload) for payload in sent]
    assert all(command["worker_pid"] == 4242 for command in commands)
    assert all(command["command_id"] and command["correlation_id"] for command in commands)


async def test_given_mismatched_action_acknowledgement_when_received_then_protocol_is_rejected() -> None:
    # Arrange
    client = LerobotWorkerClient(
        command=["worker"],
        timeout_s=1.0,
        service_instance_id="service-1",
        profile={},
        profile_fingerprint="profile-1",
        resource_fingerprint="resource-1",
        settings={"mode": "record", "execution_mode": "physical", "control_fps": 30},
    )
    client._session_id = "session-1"
    client._mode = OperatorMode.RECORD
    client._process = SimpleNamespace(pid=4242)
    loop = asyncio.get_running_loop()
    pending = loop.create_future()
    client._command_acks["command-1"] = pending
    client._expected_command_acks["command-1"] = ("correlation-1", OperatorAction.PAUSE)
    event = HardwareWorkerCommandAcknowledgement(
        service_instance_id="service-1",
        session_id="session-1",
        sequence=3,
        command_id="command-1",
        correlation_id="wrong-correlation",
        action="resume",
        dataset_id="dataset-1",
        episode_index=0,
        phase="recording",
        finalized=False,
    )

    # Act
    client._handle_command_acknowledgement(event)

    # Assert
    with pytest.raises(RuntimeError, match="command identity mismatch"):
        await pending
