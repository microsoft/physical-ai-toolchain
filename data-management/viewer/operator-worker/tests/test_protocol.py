from __future__ import annotations

import pytest
from pydantic import ValidationError

from operator_worker.protocol import InitializeCommand, RunCommand, WorkerHello


def test_hello_reports_runtime_contract() -> None:
    hello = WorkerHello(
        session_id="session-1",
        worker_version="0.1.0",
        python_version="3.12.3",
        lerobot_version="0.6.1",
        pid=42,
        supported_modes=["teleoperate"],
    )

    assert hello.protocol_version == 2
    assert hello.type == "hello"


def test_hello_advertises_guarded_policy_mode() -> None:
    hello = WorkerHello(
        session_id="session-1",
        worker_version="0.1.0",
        python_version="3.12.3",
        lerobot_version="0.6.1",
        pid=42,
        supported_modes=["teleoperate", "record", "policy"],
    )

    assert hello.supported_modes[-1] == "policy"


def test_initialize_requires_matching_nonce_and_profile() -> None:
    with pytest.raises(ValidationError):
        InitializeCommand.model_validate(
            {
                "protocol_version": 2,
                "type": "initialize",
                "service_instance_id": "service-1",
                "session_id": "session-1",
                "sequence": 1,
                "startup_nonce": "",
                "profile": {},
                "profile_fingerprint": "profile",
                "resource_fingerprint": "resource",
            }
        )


def test_run_command_is_session_bound() -> None:
    command = RunCommand(
        service_instance_id="service-1",
        session_id="session-1",
        sequence=2,
    )
    assert command.type == "run"
