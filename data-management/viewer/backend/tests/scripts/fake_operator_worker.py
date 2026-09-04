"""Protocol v2 fake worker used by backend subprocess tests."""

from __future__ import annotations

import json
import os
import sys


def emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def main() -> int:
    if "--deenergize" in sys.argv:
        print(
            json.dumps(
                {
                    "cleanup_complete": os.environ.get("FAKE_RECOVERY", "true")
                    == "true",
                    "torque_verified_off": os.environ.get("FAKE_RECOVERY", "true")
                    == "true",
                    "released": ["follower", "leader"],
                    "errors": [],
                }
            ),
            flush=True,
        )
        return 0
    session_id = os.environ["OPERATOR_SESSION_ID"]
    behavior = os.environ.get("FAKE_WORKER_BEHAVIOR", "normal")
    emit(
        {
            "protocol_version": 999 if behavior == "bad_version" else 2,
            "type": "hello",
            "session_id": "other-session" if behavior == "bad_session" else session_id,
            "worker_version": "0.0.0" if behavior == "bad_runtime" else "0.1.0",
            "python_version": "3.12.3",
            "lerobot_version": "0.6.1",
            "pid": os.getpid(),
            "supported_modes": ["teleoperate", "record", "policy"],
        }
    )
    print("fake stderr", file=sys.stderr, flush=True)
    initialize = json.loads(sys.stdin.readline())
    if behavior == "startup_cleanup":
        emit(
            {
                "protocol_version": 2,
                "type": "cleanup",
                "service_instance_id": initialize["service_instance_id"],
                "session_id": session_id,
                "sequence": 1,
                "command_id": "worker-exit",
                "cleanup_complete": True,
                "torque_verified_off": True,
                "released": ["follower", "leader", "front", "wrist"],
                "errors": [],
            }
        )
        return 1
    emit(
        {
            "protocol_version": 2,
            "type": "initialized",
            "service_instance_id": initialize["service_instance_id"],
            "session_id": session_id,
            "sequence": 1,
            "startup_nonce": (
                "wrong-nonce"
                if behavior == "bad_nonce"
                else initialize["startup_nonce"]
            ),
            "resources": {"follower": "acquired_torque_off"},
        }
    )
    run = json.loads(sys.stdin.readline())
    emit(
        {
            "protocol_version": 2,
            "type": "running",
            "service_instance_id": run["service_instance_id"],
            "session_id": session_id,
            "sequence": 2,
            "torque_enabled": behavior != "torque_off",
        }
    )
    emit(
        {
            "protocol_version": 2,
            "type": "preview",
            "service_instance_id": run["service_instance_id"],
            "session_id": session_id,
            "sequence": 3,
            "camera": "wrist",
            "captured_at_s": 1.25,
            "jpeg_base64": "anBlZw==",
        }
    )
    emit(
        {
            "protocol_version": 2,
            "type": "rate",
            "service_instance_id": run["service_instance_id"],
            "session_id": session_id,
            "sequence": 4,
            "target_hz": 30.0,
            "actual_hz": 29.5,
            "loop_p50_ms": 1.0,
            "loop_p95_ms": 2.0,
            "loop_max_ms": 3.0,
            "overruns": 1,
        }
    )
    emit(
        {
            "protocol_version": 2,
            "type": "telemetry",
            "service_instance_id": run["service_instance_id"],
            "session_id": session_id,
            "sequence": 5,
            "elapsed_s": 1.0,
            "leader": {"joint": 1.0},
            "follower": {"joint": 2.0},
            "commanded": {"joint": 3.0},
        }
    )
    worker_sequence = 5
    episode_index = 0
    while command_line := sys.stdin.readline():
        command = json.loads(command_line)
        if command["type"] == "action":
            if command["action"] in {"save", "finish"}:
                episode_index += 1
            worker_sequence += 1
            emit(
                {
                    "protocol_version": 2,
                    "type": "command_ack",
                    "service_instance_id": command["service_instance_id"],
                    "session_id": session_id,
                    "sequence": worker_sequence,
                    "command_id": command["command_id"],
                    "action": command["action"],
                    "dataset_id": initialize.get("settings", {}).get(
                        "dataset_id", "demo"
                    ),
                    "episode_index": episode_index,
                    "phase": (
                        "finalized"
                        if command["action"] == "finish"
                        else ("paused" if command["action"] == "pause" else "recording")
                    ),
                    "finalized": command["action"] == "finish",
                }
            )
            if command["action"] != "finish":
                continue
        worker_sequence += 1
        emit(
            {
                "protocol_version": 2,
                "type": "cleanup",
                "service_instance_id": command["service_instance_id"],
                "session_id": session_id,
                "sequence": worker_sequence,
                "command_id": command["command_id"],
                "cleanup_complete": True,
                "torque_verified_off": True,
                "released": ["follower", "leader", "front", "wrist"],
                "errors": [],
            }
        )
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
